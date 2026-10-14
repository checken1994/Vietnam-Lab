# W6-FINAL BUNDLE INDEX — /ask battery re-battery sau 5 wave remediation

- **SHA under test:** `e2e735a8d017974852ca4182b712bb47f318607a` (main tip, W5 squash merge PR #56)
- **Ngày chạy:** 2026-10-05, window 10:45Z–11:03Z (3 boot độc lập cùng SHA, mỗi run 1 boot)
- **Phương pháp:** STABILITY trên cùng SHA — baseline_ref = RUN A của chính series này
  (KHÔNG so với baseline 1aef24a8 cũ), vì W3 đã chủ đích đổi semantics q07/q08
  (benign-unverifiable → abstain ESCALATE ổn định, hết KILL-xổ-pháo).
- **Verdict tổng: GATE FAIL (báo as-is)** — chi tiết bên dưới. Không sửa product/harness.

## Kết quả chính

| Run | File | Boot PID | Pass count | FAIL probes | Ghi chú |
|---|---|---|---|---|---|
| A (baseline_ref) | `battery/baseline_ref.json` | 7820 | **14/16** | q06, q07 (ESCALATE) | baseline series này |
| B (run1) | `battery/run1.json` | 16684 | **13/16** | q06, q07, q08 (ESCALATE) | **q08 PASS→FAIL vs A** |
| C (run2) | `battery/run2.json` | 12808 | **14/16** | q06, q07 (ESCALATE) | q08 PASS trở lại |

48/48 probe HTTP 200. Battery exit code A/B/C = 0/0/0.

## Gate

- Tool: `tools/w6_ask_battery_gate.py` (R1-R5, fail-closed)
- Output: `battery/gate_check_output.txt` — **EXIT=1**
  - run1: FAIL — `baseline-regression@q08` (PASS→FAIL cùng SHA) + 13/16 < 14
  - run2: OK — 14/16, 0 regression
- Self-test harness: `battery/gate_selftest_output.txt` — PASS (S1/S2/S3), fail-closed hoạt động đúng

## Expected-state checks (`battery/expected_state_checks.txt`)

- **(a) q07/q08 ESCALATE-class: PASS** — mọi withheld là
  `'[SCP: Answer withheld — không xác minh được câu trả lời (governance: ESCALATE)]'`;
  grep "KILL" trên 3 server log = 0. Drift q08 là ESCALATE-abstain đúng semantics W3,
  KHÔNG phải KILL quay lại.
- **(b) q05/q11 (nạn nhân lifecycle cũ): PASS** — PASS/SUCCESS 6/6 qua A/B/C.

## KC-16 LLM-call census

- `battery/llm_call_compare.csv` (16 × A/B/C) — nguồn đếm + quy tắc gán:
  `battery/call_source_documentation.md`
- Tổng POST LLM (httpx, chỉ host openrouter.ai + api.groq.com): A=52, B=52, C=53;
  background=0, residual=0, overlap=none.
- HARD RULE PASS-with-0-calls: KHÔNG vi phạm (chỉ q02 A/B = 0 — data-API fork chuẩn;
  q14=1/1/1 vẫn data-API route).

## Drift LLM-call nổi bật

- Per-probe drift ≤ ±2 (q04: 5→6→7; q09: 3→2→4; q10: 3→4→3) — sai số quy tắc gán
  theo giây + async hedge/crosscheck sát ranh giới window; tổng thể 52→52→53 ổn định.
- **Không có đột biến kiểu "PASS mà mất toàn bộ LLM-call".**

## Bằng chứng chính (đã hash trong `bundle_hashes.txt`)

| File | Nội dung |
|---|---|
| `battery/baseline_ref.json` | RUN A — 16 probe full response |
| `battery/run1.json` | RUN B — 16 probe full response |
| `battery/run2.json` | RUN C — 16 probe full response |
| `battery/gate_check_output.txt` | Gate EXIT=1 chi tiết R5@q08 |
| `battery/gate_selftest_output.txt` | Self-test harness PASS |
| `battery/expected_state_checks.txt` | Assertion (a) PASS, (b) PASS |
| `battery/llm_call_compare.csv` | Census 16 × 3 |
| `battery/call_source_documentation.md` | Định nghĩa nguồn đếm + limitation |
| `battery/server_log_run{A,B,C}.log` | 3 boot log (đã scan sạch secret pattern) |
| `final_provenance.txt` | Lineage, identity, CI runs, limitations |

## Lineage

`1aef24a8` (baseline KC-23) → `a79b6148` (W1) → `bc055371` (W2) → `2b37e7ee` (W3)
→ `e6e0c295` (W4) → `e2e735a8` (W5 squash) → **battery tại e2e735a8** (worktree detach, /health commit khớp từng boot).

## CI runs reference (same-SHA e2e735a8, 2026-10-05T10:41Z)

| Run ID | Workflow | Kết quả |
|---|---|---|
| 37298177254 | SCP L3 Verification (T00 Meta-Audit) | success |
| 37298177268 | SCP CI Baseline (Non-Authoritative) | success |
| 37298177634 | SCP Pre-RC Verification (Non-Authoritative) | success |
| 37298177244 | SCP RC Promotion and Customer Handoff | failure (security-mutation-durability: provider_failover_timeout trong runtime-audit sandbox; lineage/provenance/handoff skip theo thiết kế main-push) |

## Verdict & scope

- **Gate FAIL as-is:** q08 dao động PASS↔ESCALATE giữa các boot trên cùng SHA —
  bằng chứng nondeterminism còn lại trên đường /ask (provider/judge variance trên câu
  time-sensitive). Root-cause chưa thực hiện trong session này (ràng buộc: không sửa
  product/harness khi gate FAIL).
- Phần ĐẠT: KILL-semantics W3 giữ vững (0 KILL); q05/q11 lifecycle cũ ổn định;
  census không phát hiện PASS-mất-LLM-call; 3 CI workflow thường xanh same-SHA.
- PASS ở đây (expected-state) chỉ có nghĩa "không thấy vi phạm trong phạm vi
  16 probe × 3 boot, 1 host, window đã ghi". Không claim production-ready.
