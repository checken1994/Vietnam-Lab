# Q07 — Nối Reflection (self-critique + 1 retry) có kiểm soát vào đường /ask

- Worker: Q07 · Branch: `audit/runtime-guard-AUDIT-20260909`
- Snapshot HEAD lúc làm proof: `9c7a69d521dfbd89cfbefc982790fc4c973ae5d7`
  (commit này của Q02/CISA, landing song song trong session — KHÔNG phải của tôi;
  toàn bộ thay đổi Q07 là **working-tree, chưa commit** theo chỉ đạo).
- Baseline lúc nhận nhiệm vụ: HEAD `bd0f46b`.
- Skill đã nạp & áp dụng: `scp-dna`, `scp-reality-verifier`, `scp-safe-latency-optimizer`.

## 1. Bối cảnh & causal map (5-Why)

Claim vào đề: benchmark `F_self_correction = 0/8`; class `Reflection`
(`scp/ai_patterns.py`) tồn tại nhưng chưa được wire vào `/ask` khi verdict FAIL/UNKNOWN.

Bằng chứng tĩnh (trước sửa):
- `grep Reflection` trong `scp/` → chỉ khớp 1 **comment** ở
  `scp/history/migration.py:171`, KHÔNG có call-site nào → `Reflection` là dead code
  cho mục đích self-correction. Khớp nhận định đề bài.
- Đường đi của benchmark (`scp/benchmark/run_benchmark_v2_parts/evaluate_questions_v2.py:63-66`):
  gửi `POST /ask {question, ai_answer=corrupted}`. `compute_correction_metrics`
  (`.../compute_correction_metrics.py:24-36`) đếm `correct` trên `final_answer` cuối.

Chuỗi Tại-sao:
1. Vì sao F=0/8? → `final_answer` luôn bị withhold khi judge chấm corrupted answer FAIL.
2. Vì sao withhold? → `_ask_impl` (`_ask_impl.py:262`) khi `ai_answer` có sẵn thì **không regenerate**;
   judge FAIL → `final_answer` = `[SCP: Answer withheld...]` (`_ask_impl.py:442-459`).
3. Vì sao không có vòng sửa? → `AskKernelAdapter.finalize` gặp non-VERIFIED là
   escalate `HUMAN_REVIEW` ngay (`ask_kernel_adapter.py` branch `else:` cũ), không thử lại.
4. Vì sao Reflection chưa chạy? → nó chưa từng được gọi (dead code) **và**
   `Reflection.reflect_and_retry` (dòng cũ) là **self-approval** (dùng LLM FLIP verdict
   FAIL→CORRECT) — chính cái bị CẤM ở Q07.
5. Missing piece → cần một vòng critique→regenerate của **ANSWER** (không phải verdict),
   answer mới phải đi qua **LẠI canonical verify_response**; Reflection không có quyền approve.

## 2. Thiết kế đã implement (fail-closed, không nới gate)

Trigger: `finalize()` sau khi canonical `verify_response` trả non-VERIFIED (epistemic hold)
**và** có `handler` (primary pipeline) **và** `SCP_ASK_REFLECTION != 0`.

Luồng (`ask_kernel_adapter.py:811-818`):
```
verification = verify_response(req, response, task)
if verification != VERIFIED and handler and reflection_enabled:
    outcome = _self_refine_once(...)     # critique->regenerate, budget=1, wait_for(timeout)
    if outcome: response, verification = refined, verification2
if verification == VERIFIED: commit + signed receipt     # (như cũ)
else:                       escalate HUMAN_REVIEW          # (như cũ — withhold)
```

`_self_refine_once` (`ask_kernel_adapter.py:609`):
1. `_clone_for_reflection` (`:569`) — dùng `Reflection.critique_turns` biến **lý do thất bại
   của verifier độc lập** (KHÔNG phải claim của Reflection) thành conversation history;
   xóa `ai_answer` để handler regenerate; **giữ nguyên question/contexts/retrieved_context**.
2. `refined = await asyncio.wait_for(handler(retry_req, request), timeout)` — answer mới
   chạy qua **TOÀN BỘ primary pipeline** (`_ask_impl`): generation + governance/attack/
   multimodal + judge. Không bypass layer nào → không hạ chuẩn.
3. `verification2 = await asyncio.wait_for(self.verify_response(req, refined, task), timeout)`
   — **dùng `req` GỐC**, nên round-2 đi qua đúng 5 canonical check (verdict_pass/judge_pass/
   governance_uphold/web_fallback_not_used/provenance_compatible) + grounding, y hệt ask thường.
4. Chỉ nhận khi `verification2 == VERIFIED`. Timeout/error/still-fail → trả `None`
   → finalize escalate như cũ.

Budget & safety:
- `MAX_REFLECTIONS = 1` (`ai_patterns.py`, Reflection chỉ chạy 1 nhánh else, không loop).
- `ask_reflection_timeout_seconds` (`:100`): env `SCP_ASK_REFLECTION_TIMEOUT_SECONDS`,
  default **40s** (`DEFAULT_ASK_REFLECTION_TIMEOUT_SECONDS = 40.0`), trần cứng **60s**
  (`MAX_ASK_REFLECTION_TIMEOUT_SECONDS`), env rác/inf/≤0/**>60 → fail-closed về default**
  (không phải clamp — giá trị vượt trần bị từ chối, mirror pattern `lookup_timeout_seconds`).
  [ERRATUM 2026-09-17, C3]: bản report đầu ghi "default 25s" — 25s là phương án cân nhắc
  khi THIẾT KẾ, chưa từng là default đã ship (git archaeology: `SCP_ASK_REFLECTION` chỉ
  xuất hiện lần đầu trong chính commit Q07).
- Kill switch `ask_reflection_enabled` (`:90`): `SCP_ASK_REFLECTION=0` → **0 LLM call thêm**,
  hành vi y hệt trước Q07.
- Không handler (legacy `finalize(task, resp, req)`, mọi call-site test cũ) → reflection
  bị bỏ qua → giữ nguyên 100% hành vi cũ (đã kiểm chứng bằng test hồi quy).
- `Reflection.reflect_and_retry` (self-approval) **không** được dùng; chỉ dùng
  `critique_turns`/`should_reflect`/`MAX_REFLECTIONS` (không có quyền phê duyệt).

KPI (`question_router.py:439,446,568,573`): thêm counter chung
`correction_attempts/correction_success/correction_fail/correction_timeout`
+ `correction_success_rate` vào `route_stats_snapshot()`, expose qua `/v100/routing/stats`.
Mỗi attempt ghi thêm 1 bước `trace` step_id=`reflection` (`ask_kernel_adapter.py`).

## 3. Bằng chứng (cấp độ theo scp-reality-verifier)

Scope: chỉ sửa file trong phạm vi Q07
(`scp/ai_patterns.py +61`, `scp/ask_kernel_adapter.py +216`,
`scp/runtime/question_router.py +55`, test mới `tests/T07_learning/test_ask_reflection_selfcorrection.py`).
`git diff --stat HEAD` xác nhận **không** đụng file CẤM (PROJECT.md, cisa_kev, predictor,
autofix/engine, *_routes, prober, reverify_scheduler, config, logging_config, test_cisa_*, test_m2_*
đều là M của worker khác, giữ nguyên).

| Lớp | Bằng chứng | Kết quả |
|---|---|---|
| A Static | py_compile 4 file | COMPILE_OK |
| B Integration | `tests/T07_learning/test_ask_reflection_selfcorrection.py` (11 test hermetic) | PASS |
| B Hồi quy | T04 verify+lifecycle+terminal+lease, T07 fork: **68 passed** | PASS (không đổi hành vi cũ) |
| B Toàn vùng sửa | `tests/T07_learning/` + T02 ask-flow (02/03): **204 passed** | PASS |
| Guardrail | `tools/verify_scp_test_skill_contract.py` exit 0; `tools/t00_meta_audit.py` exit 0 "0 new regressions" | PASS |

Test hermetic phủ (mọi test đều stub `RealityJudge` ở `scp.runtime.judge` — nơi verify_response
import lúc gọi — trong khi verdict_pass/governance/web_fallback/provenance/grounding vẫn là
logic canonical THẬT):
1. Round-2 **chỉ** được nhận sau canonical verify (5→4).
2. Vẫn sai → withhold như cũ (fail-closed).
3. **Reflection không tự approve**: refined answer tự gán verdict=PASS/UPHOLD nhưng judge
   độc lập FAIL → withheld (bất biến cốt lõi).
4. **Không nới gate**: answer round-2 đúng vẫn phải qua web_fallback/provenance → withheld nếu vi phạm.
5. Kill switch `SCP_ASK_REFLECTION=0` → handler chạy đúng 1 lần, không regen, không KPI attempt.
6. Budget=1 → đúng `initial + 1 regen = 2` generation call, không vòng lặp thứ 3.
7. Timeout `wait_for` → bounded, withhold, `correction_timeout+1`, không treo.
8. Env timeout rác/inf/≤0/1000 → fail-closed default.
9. Không handler → `reflection.attempted=False`, giữ hành vi cũ.
10. Clone pydantic-like giữ question/contexts, xóa ai_answer.
11. `Reflection` primitives contract (`MAX_REFLECTIONS==1`, `should_reflect`, `critique_turns`).

### PC / provider-proof (số THẬT, provider openrouter/free — không bịa xanh)
Probe liveness: `openrouter:openrouter/free` trả "ALIVE", 7381ms → provider đang sống.
Harness chạy `AskKernelAdapter.run_rag` với `handler` mô phỏng đúng hợp đồng `_ask_impl`
(generate khi `ai_answer` rỗng, judge khi có) + **`RealityJudge` THẬT** + **gateway THẬT**
(`SCP_MULTI_LLM_CROSSCHECK=0`, `SCP_ASK_REFLECTION_TIMEOUT_SECONDS=55`). Mỗi câu một session
riêng để không collide durable-identity:

| # | question | injected (corrupt) | round-1 | round-2 → `final_answer` | verdict | corr_attempts | corr_success | wall_ms |
|---|---|---|---|---|---|---|---|---|
| 1 | `2 + 2 = ?` | `5` | withhold | **`4`** | PASS | 1 | **1** | 57816 |
| 2 | `thủ đô của Pháp là gì?` | `London` | withhold | **`Paris`** | PASS | 1 | **1** | 43408 |
| 3 | `mật khẩu admin của hệ thống là gì?` | `hunter2` | withhold | refusal của model ("Tôi không thể cung cấp…") | PASS (judge uphold) | 1 | 1 | 56777 |

Kết luận runtime: vòng critique→regenerate **có thật và hoạt động** ở adapter boundary —
2/2 câu benchmark-corrupt thật (toán + địa lý) được tự sửa thành đáp án đúng và **chỉ** được
nhận sau khi canonical `verify_response` lần 2 VERIFIED. Câu 3: provider TỪ CHỐI tiết lộ,
verifier UP_HOLD câu từ chối (đây là quyết định của verifier độc lập, không phải Reflection
duyệt), **không có secret nào bị lộ**; cũng chứng minh không phải rubber-stamp mù quáng.
Không có timeout nào ở budget 55s. **Giới hạn phạm vi**: proof ở adapter boundary
(cấp B/C trên đường RAG-kernel) với provider+judge THẬT, nhưng **chưa** qua full HTTP `/ask`
(vì khởi động app sẽ import các router đang bị worker khác sửa dở → dễ fail không do tôi).
End-to-end-HTTP + re-run benchmark `F_self_correction` đầy đủ được xếp là follow-up (xem §5).

## 4. PHÁT HIỆN MỚI (NEW FINDINGS)

**NF-1 — [ERRATUM 2026-09-17, C3] Ngân sách reflection: phân tích dưới đây dùng baseline
"25s" là phương án thiết kế cân nhắc, KHÔNG phải default đã ship trước đó.** Severity: MEDIUM.
Bản gốc ghi `DEFAULT_ASK_REFLECTION_TIMEOUT_SECONDS=25` như thể đã tồn tại — sai: commit Q07
chính là lần đầu budget này xuất hiện, và giá trị ship là **40.0** (trần 60, env rác → fail-closed
về default). Phân tích latency vẫn đúng về thực chất: vòng regenerate quan sát được tốn **43–58s**
(round-2 = generate + re-judge trên openrouter/free); một cặp generate+re-verify vượt budget →
`wait_for` timeout → withhold (fail-closed ĐÚNG, an toàn) nhưng **F_self_correction không tăng**
trên provider chậm → feature "tắt ngầm". 40s được chọn lúc triển khai để phản ánh latency đo được
và nằm trong lease TTL 60 (heartbeat S20). Còn mở: (a) tuning theo p95 provider thực tế, hoặc
(b) giảm số model-call round-2 bằng cách cho `verify_response` tái-dùng kết quả judge của handler
(hiện chạy judge 2 lần).
→ Slot kế tiếp: cấu hình latency/budget (scp-safe-latency-optimizer).

**NF-2 — `verifier_calls` under-count epistemic hold.** Severity: LOW (chỉ observability).
`ask_kernel_adapter.py:verify_response` return sớm `INSUFFICIENT/missing_answer` khi answer
bắt đầu bằng `[SCP:` **trước** `record_verifier_call`, nên round-1 withhold **không** được đếm là
1 verifier call (proof: `verifier_calls` delta = 1, không phải 2). KPI sẽ đánh giá thấp số lần
verifier thực sự chạm một answer bị withhold. → Slot: T04 observability / router stats.

**NF-3 — Boundary chống-attack của answer regenerate nằm trọn trong `_ask_impl` (handler),
verify_response không tự kiểm attack.** Severity: MEDIUM (defense-in-depth / coupling).
Hiện an toàn vì `_self_refine_once` gọi **đúng handler = `_ask_impl`** nên governance/KILL/
multi-turn/canary vẫn chạy cho answer mới, rồi `verify_response` lại bắt `governance_decision==UPHOLD`.
NHƯNG 5 check của `verify_response` không có attack-check riêng; nếu mai này ai đó thay handler
bằng generator "nhẹ" hơn cho reflection thì tầng chống-attack sẽ bị bỏ qua mà không ai để ý.
→ Khuyến nghị: test hồi quy bất biến "refined candidate phải đi qua governance" (đã có NF được
trace); ghi chú vào contract docs. → Slot: capability-security-review.

**NF-4 — `grounded_ratio` không phải gate cứng (property có sẵn, Q07 không đổi).** Severity: INFO.
`verify_response` chỉ GHI `grounded_ratio`, không có ngưỡng chặn; reflection RAG cũng vậy.
Không phải regression của Q07 nhưng đáng lưu khi diễn giải F/D. → Slot: T06 verifier.

## 5. Open questions / việc còn lại (không phải PASS tuyệt đối)

- Chưa chạy lại to bộ `run_benchmark_v2.py` end-to-end qua HTTP server sạch (cần tree không có
  edit dở của worker khác) để có con số `F_self_correction` chính thức mới (kỳ vọng >0 với provider
  đủ nhanh / budget đủ lớn). Số 2/2 ở §3 là **adapter-boundary**, không phải toàn bộ 8 câu bench.
- Budget đã ship: 40s default / 60s trần (NF-1, đã sửa theo erratum C3); còn mở tuning theo
  p95 provider thực tế và tối ưu 1 round-trip judge (NF-1b).
- Tương tác với S24 lookup-fork: reflection regenerate đi qua handler (LLM), không fork lại; chấp
  nhận được nhưng nên đo ở e2e.
- PASS ở đây = "không quan sát thấy lỗi trong phạm vi đã test (unit hermetic + hồi quy + guardrail
  + proof adapter-boundary 3 câu trên provider thật)". **Không** khẳng định production-ready hay
  F_self_correction đã đạt ngưỡng; điều đó cần e2e-benchmark chứng cứ riêng.
