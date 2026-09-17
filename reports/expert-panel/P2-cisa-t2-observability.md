# P2 — CISA KEV, T2 Auth Policy và KPI Observability

## Phạm vi / snapshot

- Worker: Slot B.
- Scope sửa: `scp/security/cisa_kev.py`, `scp/security/predictor.py`, `scp/runtime/question_router.py`, `scp/ask_kernel_adapter.py`, và targeted tests trong `tests/T07_learning/`.
- HEAD lúc reconcile: `8428114073571e169f8ab9e74a7dcc13d740ed88`.
- Working tree có nhiều mutation/artefact ngoài scope; không reset hoặc sửa chúng.
- Không commit.

## Vì sao chain

1. `cisa_kev_match_recent()` trước đây tạo `CisaKevFeed` và gọi `refresh_feed()` cho mỗi lookup. Mỗi verdict có thể đọc cache và đủ điều kiện network I/O; nếu được gọi từ async request thì transport blocking có thể chiếm event loop.
2. `AttackPredictor.predict_cyber_attack()` gọi KEV helper hai lần trong cùng forecast: một lần để boost confidence, một lần để gắn evidence. Điều này nhân đôi cơ hội refresh/latency và làm khó quan sát số lookup thực tế.
3. `question_router._catalog_candidates()` từng thử `auth=None` sau nhánh `auth='No'`. Kết quả API cần credential có thể lọt tới generic fetcher, dù router không có cơ chế acquire credential được owner cho phép.
4. `llm_calls_count` là counter legacy của lựa chọn fallback, không phải số provider call. Generation, classifier và verifier cần tách; tổng outbound LLM không được suy ra khi chưa instrument đủ provider/crosscheck.

## Thay đổi

### CISA KEV

- Thêm process-scoped `get_cisa_kev_feed()` singleton và lock refresh; feed cache được nạp một lần, refresh chỉ xảy ra khi TTL hết.
- Thêm bounded transport timeout `SCP_CISA_KEV_TIMEOUT_SECONDS` (default 5s, max 30s) và failure backoff (default 60s, max 300s) để slow/error feed không bị stampede.
- `CisaKevFeed.refresh()` bảo toàn cache cũ khi refresh lỗi; kết quả `failed` là neutral, không tạo positive match/confidence.
- Predictor có bounded LRU-like positive/negative CVE result cache (max 1024), TTL không vượt feed TTL; failure/timeout neutral result có TTL ngắn để tránh retry từng verdict.
- Async callers có `cisa_kev_match_recent_async()`, dùng `asyncio.to_thread()` + `wait_for()`; event loop không thực hiện blocking feed transport.
- CVE input được normalize/validate trước feed lookup.
- Trong một forecast, `predict_cyber_attack()` resolve KEV một lần rồi tái sử dụng cho boost và evidence.
- Egress/URL allowlist vẫn nằm trong `_open_cisa_feed()` và `safe_urlopen()`.

### T2 catalog auth policy

- Catalog search chỉ gọi với `auth="No"`; bỏ fallback `auth=None`.
- Kết quả được kiểm tra lại: chỉ entry có `auth` đúng bằng `No` mới được generic fetcher xử lý. Missing/unknown auth bị loại.
- Nếu catalog implementation/test double bỏ qua filter và trả entry credential-required, router ghi `auth_required_catalog_entry_skipped`, không fetch entry và để fallback Wikipedia/LLM theo flow hiện hữu.
- URL scheme/host validation và egress choke không thay đổi.

### KPI

- Giữ `llm_calls_count` vì compatibility: đây là fallback-selection counter, không tuyên bố provider call.
- `generation_calls_count` và alias `generation_llm_calls` ghi tại adapter boundary ngay trước generation handler.
- `classifier_llm_calls`/`classifier_llm_failures` giữ riêng cho L2 classifier.
- Thêm `verifier_calls`/`verifier_failures`, ghi tại `AskKernelAdapter.verify_response()` quanh `RealityJudge.judge_async()`.
- Không thêm trường `total_outbound_llm_calls`; cross-provider outbound/crosscheck chưa được instrument đầy đủ nên không claim tổng.
- Counter Prometheus mới chỉ chứa các tên/giá trị không có secret.

## Call graph / execution trace

```text
AskKernelAdapter.run_rag()
  -> attempt_lookup_fork()
     -> route_question_async()
        -> classify_l0() | classify_l2_async()
     -> to_thread(resolve_lookup_data)
        -> _catalog_candidates(auth="No")
        -> _generic_entry_lookup()
           -> _host_allowed() -> enforce_egress_policy()
           -> _fetch_url_text() -> safe_urlopen()
        -> _wiki_lookup() -> canonical wikipedia_client
  -> generation handler (_ask_impl) [record_generation_call at boundary]
  -> finalize()
     -> verify_response()
        -> RealityJudge.judge_async() [record_verifier_call]

AttackPredictor.predict_cyber_attack()
  -> cisa_kev_match_recent()
     -> bounded result cache
     -> get_cisa_kev_feed() singleton
        -> CisaKevFeed.refresh_feed()
           -> TTL/backoff check
           -> _open_cisa_feed() -> safe_urlopen()
  -> _boost_confidence_with_intel(cisa_kev_match=resolved)
  -> evidence reuse (no second feed lookup)
```

## Causal coverage matrix

| Nhánh | Test/evidence | Trạng thái |
|---|---|---|
| Catalog auth-required không được fetch | `test_resolve_lookup_data_skips_auth_required_entry` | COVERED |
| Entry auth=No vẫn fetch qua egress path | `test_resolve_lookup_data_uses_catalog_entry` | COVERED |
| URL host bị block | `test_resolve_lookup_data_blocked_host_falls_back`, T03 egress suite | COVERED trong scope |
| Lookup chạy ngoài event loop + bounded timeout | existing `test_fork_slow_lookup_does_not_block_heartbeat_event_loop`, `test_fork_lookup_timeout_is_bounded_and_fails_closed` | COVERED |
| Generation/classifier/verifier counters tách | `test_kpi_counters_separate_generation_classifier_and_verifier`, verifier integration test | COVERED |
| Singleton + cache/TTL | `tests/T03_capability/test_cisa_kev_cache.py::test_cisa_feed_singleton_refreshes_once_within_ttl` | COVERED |
| Slow CISA transport timeout | `tests/T03_capability/test_cisa_kev_cache.py::test_cisa_async_slow_transport_is_bounded_and_neutral` | COVERED |
| No-egress neutral CISA behavior | `tests/T03_capability/test_cisa_kev_cache.py::test_cisa_no_egress_is_neutral_and_backed_off` + T03 egress denial | COVERED for stale/unavailable feed; legacy challenger conflict remains |
| Live external CISA feed | không chạy trong targeted hermetic tests | UNPROVEN |
| Tổng outbound LLM | chưa instrument cross-provider/crosscheck đầy đủ | NOT CLAIMED |

## Evidence đã quan sát

- `python -m pytest -q tests/T07_learning/test_question_router_cascade.py tests/T07_learning/test_ask_lookup_fork.py --tb=short` → **59 passed**.
- `python -m pytest -q tests/T07_learning/test_question_router_cascade.py tests/T07_learning/test_ask_lookup_fork.py tests/T03_capability/test_egress_enforcement.py --tb=short` → **83 passed, 2 skipped**. Hai skip là behavior có sẵn của test suite và không được thêm bởi patch này.
- Final assigned targeted command: `python -m pytest -q tests/T03_capability/test_cisa_kev_cache.py tests/T03_capability/test_egress_enforcement.py tests/T07_learning/test_question_router_cascade.py tests/T07_learning/test_ask_lookup_fork.py tests/T07_learning/test_question_router_goldset.py --tb=short` → **91 passed, 2 skipped**.
- Gateway regression: `python -m pytest -q tests/T05_gateway/test_judge_sync_crosscheck_alive.py tests/T05_gateway/test_multi_llm_crosscheck.py tests/T05_gateway/test_multi_llm_crosscheck_concurrency.py --tb=short` → **13 passed**.
- `python tools/t00_meta_audit.py` → **All integrity checks passed (0 new regressions)**; existing baseline debt/L4 warnings remain.
- `python tools/verify_scp_test_skill_contract.py` → **status PASS_WITHIN_SCOPE**, errors `[]`, commit snapshot `8428114073571e169f8ab9e74a7dcc13d740ed88`.
- Trước thay đổi test CISA, `tests/test_m2_empirical_challenger.py` cho thấy 2 failure dưới `SCP_EGRESS_MODE=deny`: known CVE bị neutral và boost không tăng. Đây là evidence rằng test/challenger cũ giả định local cache được dùng khi feed unavailable; patch giữ fail-closed neutral theo yêu cầu mới, không sửa test để manufacture green.
- `py_compile` cho các module production scope → PASS.
- `git diff --check` scoped → không có lỗi; một whitespace pre-existing ở `scp/requirements.txt:110` ngoài scope vẫn được giữ nguyên.

## PHÁT HIỆN MỚI (NEW FINDINGS)

1. **`tests/test_m2_empirical_challenger.py:122-182` — HIGH — Slot B / cần owner quyết định test contract.** Fixture đặt `SCP_EGRESS_MODE=deny` nhưng nominal CISA tests yêu cầu known CVE vẫn match từ `data/cisa_kev.json`. Yêu cầu mới “feed unavailable → neutral” mâu thuẫn trực tiếp với assertion này. Không được làm xanh bằng cách bỏ egress deny hoặc coi stale cache là fresh evidence. Cần cập nhật test contract để tách local-cache/fresh-cache case khỏi no-egress unavailable case.
2. **`scp/security/predictor.py:248-270` — MEDIUM — Slot B.** `asyncio.wait_for(to_thread(...))` bounds caller wait nhưng không hủy được blocking native/network work đã chạy trong worker thread; repeated hostile slow transports vẫn có thể giữ thread-pool capacity. Đây là residual capacity gap, không được giải bằng retry.
3. **`scp/ask_kernel_adapter.py:377-400` — MEDIUM — Slot B.** Verifier counter hiện ghi một invocation thành công khi `judge_async()` trả về, và ghi failure khi raise; nó chưa phân loại verdict `PASS/FAIL/UNKNOWN` hoặc crosscheck provider calls bên trong `RealityJudge`. Vì vậy `verifier_calls` là observed verifier boundary, không phải total outbound LLM count.
4. **`scp/runtime/question_router.py:367-480` — LOW — Slot B.** `llm_calls_count` vẫn là legacy fallback-selection counter để tương thích tests/dashboard; tên có thể gây hiểu nhầm nếu consumer coi đó là outbound calls. Snapshot đã thêm tên rõ `generation_llm_calls`, nhưng consumer nên migrate dần và không dùng legacy field làm total.
5. **`scp/security/predictor.py:226-270` — LOW — Slot B.** Sync callers vẫn có thể block tối đa transport timeout; chỉ async-safe helper offload. Hiện không tìm thấy production async caller của `AttackPredictor`, nên chưa mở rộng class API thành async forecast để tránh scope creep.

## Rollback / giới hạn

Rollback là revert các hunk uncommitted trong các file scope, không reset các mutation ngoài scope. Chưa có live-feed/e2e proof; targeted PASS chỉ có nghĩa không thấy failure trong profile đã chạy.

## Verdict

`PASS_WITHIN_SCOPE` cho T2 auth filter, CISA singleton/TTL/bounded timeout, lookup async safety regression và KPI separation trong targeted profile. Legacy challenger CISA vẫn đỏ bởi contract conflict no-egress vs stale cache. Không claim full suite, production-ready, tổng outbound LLM, hoặc live CISA availability.
