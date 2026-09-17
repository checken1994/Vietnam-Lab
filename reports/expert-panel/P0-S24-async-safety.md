# P0-S24 — Async Safety / Lookup Fork Review

## Phạm vi và snapshot

- Worker scope: `scp/runtime/question_router.py`, `scp/ask_kernel_adapter.py`,
  `tests/T07_learning/test_question_router_cascade.py`,
  `tests/T07_learning/test_ask_lookup_fork.py`.
- HEAD lúc reconcile: `a2edec2c32d0be12901b268d797f018c15ce1fdc`.
- Working tree có mutation ngoài scope của worker khác; không sửa hoặc reset các
  file đó.
- Không commit theo ủy quyền.

## Why chain / root cause

1. `classify_l2()` là hàm sync nhưng production-shaped `LLMGateway.chat()` là
   coroutine; gọi `chat()` trực tiếp tạo awaitable chưa được await và không phải
   sync response.
2. `attempt_lookup_fork()` là async nhưng gọi `resolve_lookup_data()` sync trực
   tiếp; catalog, DNS/HTTP và Wikipedia client có thể block event loop.
3. Khi event loop bị block, S20 `_lease_heartbeat()` không chạy được; lease nhỏ
   có thể hết hạn trước khi handler/finalize tiếp tục.
4. `llm_calls_count` trước đây trộn lựa chọn fallback với generation thật, còn
   fetch counters đếm transport entry; dashboard không phân biệt được lookup
   success/fail với generation invocation.

## Thay đổi đã thực hiện

### 1. Sync/async contract

- `classify_l2()` giờ yêu cầu callable `gateway.chat_sync()` và fail-safe về
  `REASONING/l2-failsafe` nếu gateway chỉ có async `chat`.
- Awaitable trả nhầm từ `chat_sync()` được đóng nếu có thể rồi fail-safe, tránh
  coroutine leak.
- `classify_l2_async()` vẫn gọi và await `gateway.chat()` cho production path.
- Test dùng gateway có cả async `chat` và sync `chat_sync`, đồng thời có test
  async-only gateway không được gọi nhầm từ sync path.

### 2. Bounded non-blocking lookup

- Thêm `lookup_timeout_seconds()` với env `SCP_LOOKUP_TIMEOUT_SECONDS`.
- Default tổng budget là 8 giây; giá trị không hợp lệ/non-finite/non-positive hoặc
  lớn hơn 30 giây quay về default.
- `attempt_lookup_fork()` chạy blocking `resolve_lookup_data()` qua
  `asyncio.to_thread()` và bọc toàn bộ operation bằng `asyncio.wait_for()`.
- Timeout hoặc exception ghi nhận lookup failure, fallback reason và chuyển về
  generation handler; không tự tạo answer và không bypass `safe_urlopen`,
  `_host_allowed`, scheme HTTP/HTTPS hoặc egress checks.
- Generic catalog URL giờ parse bằng `urlsplit`, chỉ nhận `http`/`https` có host;
  malformed URL fail-closed.

### 3. S20 heartbeat

- Không tăng lease TTL.
- Vì lookup không còn chiếm event loop, heartbeat task có thể renew lease trong
  lúc lookup/handler chậm. Test có TTL 1 giây, lookup blocking 300ms, timeout 50ms
  và handler async 1.2 giây; test quan sát renewal thật trên kernel.

### 4. KPI

- Giữ `llm_calls_count` để tương thích như counter fallback-path cũ.
- Thêm `generation_calls_count`, tăng ngay trước `handler(req, request)` trong
  `AskKernelAdapter.run_rag()`.
- Thêm `lookup_attempts`, `lookup_success`, `lookup_fail` và
  `lookup_timeout_count`.
- `RouteStats` đã có lock vì lookup chạy worker thread và metrics được đọc từ
  request/admin path.
- Chưa sửa vùng judge; report không claim verifier-call KPI đã được đo.

## Causal coverage matrix

| Nhánh nhân quả | Test/evidence | Kết quả |
|---|---|---|
| Sync gateway dùng `chat_sync` | `test_l2_parses_lookup`, `test_l2_parses_reasoning` | COVERED |
| Async production path vẫn await `chat` | `test_l2_async_uses_production_async_chat` | COVERED |
| Async-only gateway không bị gọi từ sync path | `test_l2_async_only_gateway_fails_closed_without_calling_chat` | COVERED |
| `chat_sync` exception / parse fail-safe | `test_l2_gateway_exception_is_failsafe`, `test_l2_unparseable_is_failsafe_reasoning` | COVERED |
| Blocking lookup không block event loop | `test_fork_slow_lookup_does_not_block_heartbeat_event_loop` | COVERED |
| Lookup timeout bounded + fallback | `test_fork_lookup_timeout_is_bounded_and_fails_closed` | COVERED |
| Timeout trong run_rag không làm mất heartbeat | `test_fork_timeout_fallback_in_run_rag_heartbeat_renews` | COVERED |
| Lookup success/fail KPI | success, timeout, miss tests in `test_ask_lookup_fork.py` | COVERED |
| Scheme/host validation remains enforced | existing blocked-host test + generic URL validation branch | COVERED within current test scope |
| Judge-call KPI | no change in judge area | UNPROVEN / intentionally out of scope |
| Real external API / live egress | hermetic tests only | UNPROVEN |

## Evidence observed

- `python -m pytest -q tests/T07_learning/test_question_router_cascade.py tests/T07_learning/test_ask_lookup_fork.py --tb=short`
  → **57 passed**.
- `python -m pytest -q tests/T04_kernel/test_lease_heartbeat.py tests/T07_learning/test_question_router_cascade.py tests/T07_learning/test_ask_lookup_fork.py --tb=short`
  → **80 passed**.
- Targeted adapter/lease set:
  `tests/T04_kernel/test_ask_kernel_adapter_verify.py`,
  `test_ask_kernel_lifecycle_and_identity.py`,
  `test_ask_kernel_terminal_race.py`,
  `test_lease_fencing_idempotency.py`
  → **20 passed**.
- Combined scoped regression command including all above files → **100 passed**.
- `python -m ruff check` on router and both S24 test files → **All checks passed**.
- `python tools/t00_meta_audit.py` → **exit 0; 0 new regressions**. Existing
  baseline tripwire debt and L4 warnings remain; no test node was deleted.
- `python scripts/run_reality_tests_portable.py` on the full repository → 74/76
  passed, 2 pre-existing/out-of-scope failures (`reality_4-b-002.py` references
  deleted judge mixin path; `reality_4-c-006.py` reports fallback LOC drift).
  These failures were not changed because their files are outside worker scope.

## Rollback

Revert only the uncommitted hunks in the four scoped files, preserving unrelated
working-tree mutations. No commit or reset was performed by this worker.

## Limitations / open questions

- `asyncio.wait_for()` cannot forcibly terminate arbitrary blocking Python code
  already running in a worker thread; after timeout, the thread may finish later.
  The event loop and lease heartbeat are protected, but thread-pool resource
  reclamation under repeated hostile slow lookups needs a separate bounded
  executor/capacity review.
- Default budget 8 seconds is a safe bounded default, not a measured p95/p99
  production optimum. Live latency distribution and safety metrics were not
  collected in this worker run.
- Full repository reality runner is not green for the two unrelated failures
  listed above.
- No verifier/judge instrumentation was added; `generation_calls_count` is the
  actual handler boundary, not a claim that an LLM provider call succeeded.

## PHÁT HIỆN MỚI (NEW FINDINGS)

1. **`scp/runtime/question_router.py:~804` — MEDIUM**: timeout của
   `asyncio.wait_for(to_thread(...))` dừng chờ coroutine nhưng không dừng được
   blocking function đã chạy trong worker thread. Đề xuất slot: S24 follow-up /
   runtime capacity guard; cân nhắc executor giới hạn và quota cho lookup, không
   retry side effect.
2. **`tests/reality-tests/reality_4-b-002.py:~91` — MEDIUM, ngoài scope**:
   reality test vẫn tham chiếu `scp/runtime/judge_parts/judgecore_mixin.py`, file
   đã bị loại khỏi cây hiện tại. Đề xuất slot: S26/reality-harness repair.
3. **`tests/reality-tests/reality_4-c-006.py:~155` — LOW/MEDIUM, ngoài scope**:
   fallback LOC metadata `5007` lệch wc thực tế `5003`; test fail-closed đúng.
   Đề xuất slot: release/reality evidence maintenance.
4. **`scp/ask_kernel_adapter.py:~377-386` — MEDIUM, ngoài scope hiện tại**:
   `verify_response()` gọi RealityJudge nhưng lỗi bị chuyển thành `judge_pass=False`;
   KPI verifier-call chưa có receipt/counter đo độc lập. Đề xuất slot: verifier /
   judge observability slot; không sửa trong S24 async-safety worker.

## Verdict

`PASS_WITHIN_SCOPE` cho sync/async contract, bounded non-blocking lookup, S20
heartbeat interaction và lookup/generation KPI separation theo test profile trên.
Không phải full-repository PASS, không phải production-ready claim, và không
claim judge/verifier calls đã được đo.
