# W6-RE BUNDLE INDEX — re-battery /ask với EXPECTED-STATE ĐÃ PIN theo semantic mới (Option B, GA.md B1b)

- **SHA under test:** `bf8a8fbaf3930d943e035ee9236c148165faea8f` (main tip, docs handoff GA.md B1b)
- **Ngày chạy:** 2026-10-05, window 12:39Z–12:58Z (3 boot độc lập cùng SHA, mỗi run 1 boot)
- **Phương pháp:** STABILITY trên cùng SHA — baseline_ref = RUN A của chính series này.
  Khác W6-FINAL: expected-state ĐÃ PIN trước khi chạy (Option B — pin theo semantic
  W3: q06/q07 withheld-ESCALATE = ĐẠT; q08 PASS hoặc withheld-ESCALATE đều ĐẠT,
  cấm KILL-class), KHÔNG so binary PASS-vs-baseline may mắn.
- **Verdict tổng: GATE FAIL (báo as-is)** — chi tiết bên dưới. Không sửa product/harness.

## Kết quả chính

| Run | File | Boot PID | Pass count | non-PASS probes | Ghi chú |
|---|---|---|---|---|---|
| A (baseline_ref) | `battery/after_ref.json` | 17620 | **13/16** | q03, q05, q07 (ESCALATE) | q03 + q05 thuộc stable-PASS set → **HARD-FAIL** |
| B (run1) | `battery/after_run1.json` | 13596 | **15/16** | q07 (ESCALATE) | stable set đầy đủ |
| C (run2) | `battery/after_run2.json` | 7056 | **15/16** | q05 (ESCALATE) | q05 thuộc stable set → **HARD-FAIL** |

48/48 probe HTTP 200. Battery exit code A/B/C = 0/0/0.

## Expected-state checks (`battery/stability_checks.txt`, script `check_expected_state.py`)

| Check | Kết quả | Chi tiết |
|---|---|---|
| (a) stable-PASS 13 probe PASS cả A/B/C | **FAIL** | q03: ESCALATE ở A (PASS B/C); q05: ESCALATE ở A và C (PASS B) — withheld-ESCALATE-class, KHÔNG KILL |
| (b) q06/q07 withheld-ESCALATE cả A/B/C | FAIL theo nghĩa đen pin (không hard-fail) | q06: PASS cả 3 (trước đây FAIL đều 3 run ở W6-FINAL — đã hồi phục); q07: ESCALATE ở A/B, PASS-as-abstain ở C ("Xin lỗi, hiện tại tôi không có dữ liệu thời tiết cập nhật.") |
| (c) q08 không KILL-class | **PASS** | PASS cả 3 (không còn flip PASS↔ESCALATE như W6-FINAL) |
| (d) mỗi run ≥ 13/16 | **PASS** | 13/15/15 |
| (e) census + PASS-with-0-calls | **PASS** | A=53/B=53/C=51; background=0; residual=0; PASS&0-call chỉ q02/q14 (data-API fork chuẩn) |

**HARD-FAIL duy nhất: (a) — q03, q05.** Cả hai là withheld-ESCALATE
(`[SCP: Answer withheld — không xác minh được câu trả lời (governance: ESCALATE)]`),
KHÔNG phải KILL-class. grep "KILL" trên 3 server log = 0/0/0.

## KC-16 LLM-call census

- `battery/llm_call_compare.csv` (16 × A/B/C) — nguồn đếm + quy tắc gán:
  `battery/call_source_documentation.md` (script đếm: `check_expected_state.py`)
- Tổng POST LLM (httpx, chỉ host openrouter.ai + api.groq.com): A=53, B=53, C=51;
  background=0, residual=0, overlap=none; nonLLM-POST=0.
- Drift per-probe ≤ ±1 (q01/q03/q04/q09/q11/q13) — sai số quy tắc gán theo giây +
  async hedge/crosscheck sát ranh giới window.
- **Không có đột biến kiểu "PASS mà mất toàn bộ LLM-call".**

## So với W6-FINAL (`reports/scp_acceptance_ci/wave6/`) — cùng semantic W3

- **q06 (greeting): hồi phục** — W6-FINAL FAIL đều 3 run (regression mở); nay PASS
  3/3. Đề nghị owner xem như root-cause tự giải (không rõ nguyên nhân — chưa claim fix).
- **q08: hết flip** — W6-FINAL PASS→ESCALATE→PASS; nay PASS 3/3 (chỉ 1 run).
- **q03, q05: vi phạm pin stable-PASS MỚI** — W6-FINAL PASS 3/3; nay q03 ESCALATE 1/3,
  q05 ESCALATE 2/3. Đây là nondeterminism verifier/judge trên cùng SHA, di chuyển
  sang câu khác (W6-FINAL: q08; nay: q03/q05). Cần root-cause ở session sau.
- KILL-semantics giữ vững: 0 "KILL" trong cả response lẫn server log (6/6 boots tính cả W6-FINAL).

## Bằng chứng chính (đã hash trong `bundle_hashes.txt`)

| File | Nội dung |
|---|---|
| `battery/after_ref.json` | RUN A — 16 probe full response |
| `battery/after_run1.json` | RUN B — 16 probe full response |
| `battery/after_run2.json` | RUN C — 16 probe full response |
| `battery/stability_checks.txt` | 5 expected-state checks + verdict |
| `check_expected_state.py` | Checker + census (lưu trong evidence, auditable) |
| `battery/llm_call_compare.csv` | Census 16 × 3 |
| `battery/call_source_documentation.md` | Định nghĩa nguồn đếm + limitation |
| `battery/server_log_run{A,B,C}.log` | 3 boot log (scan secret pattern: 0 secret thật; match duy nhất là task-ID `ask-<hex>` false-positive) |
| `final_provenance.txt` | Lineage, identity, limitations |

## Lineage

`1aef24a8` (baseline KC-23) → `a79b6148` (W1) → `bc055371` (W2) → `2b37e7ee` (W3)
→ `e6e0c295` (W4) → `e2e735a8` (W5 squash) → `bf8a8fba` (GA.md B1b docs, main tip)
→ **battery tại bf8a8fba** (worktree detach, /health commit khớp từng boot).
Lưu ý: checkout chính `D:\scp` đang ở `b8b4c1c1` (fix/wave4b, silent-except sweep
chưa merge); battery chạy trên worktree `bf8a8fba`, runner hash = pre-wave4b `aab949d8`.

## Verdict & scope

- **GATE FAIL as-is theo pin Option B:** q03 (A) và q05 (A, C) thuộc stable-PASS
  set bị ESCALATE-withheld giữa các boot cùng SHA = nondeterminism verifier/judge
  còn lại trên đường /ask. Không sửa product/harness (ràng buộc session).
- Phần ĐẠT: (c) q08 không KILL + PASS ổn định 3/3; (d) 13/15/15 ≥ ngưỡng;
  (e) census sạch, 0 vi phạm PASS-with-0-calls; KILL-semantics giữ vững;
  q06 hồi phục PASS 3/3.
- PASS ở các check (c)(d)(e) chỉ có nghĩa "không thấy vi phạm trong phạm vi
  16 probe × 3 boot, 1 host, window đã ghi". Không claim production-ready.
