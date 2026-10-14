# W7-BATTERY BUNDLE INDEX — battery hành vi cuối sau Option A + family-2 (semantic Option A, owner duyệt 2026-10-05)

- **SHA under test:** `4cec89034dfe0da7ca3a2772f93614ce1c689d38` (main tip, PR #58 —
  "fix(ask-w7): Option A judge 3-state ABSTAIN + family-2 judge model deepseek")
- **Ngày chạy:** 2026-10-05, window 18:22Z–18:47Z (3 boot độc lập cùng SHA, mỗi run 1 boot)
- **Phương pháp:** STABILITY trên cùng SHA — baseline_ref = RUN A của chính series này.
  Đếm theo **semantic Option A** (khác W6-RE dùng Option B pin): PASS-thuần = PASS +
  run_status SUCCESS; ABSTAIN-delivered = ABSTAIN + gov ABSTAIN + nhãn
  `[unverified — abstain]` + HTTP 200; answer-rate = (PASS-thuần + ABSTAIN-delivered)/16.
- **Verdict tổng: GATE FAIL (báo as-is)** — chi tiết bên dưới. Không sửa product/harness.

## Kết quả chính (answer-rate theo Option A)

| Run | File | Boot PID | PASS-thuần | ABSTAIN-delivered | Answer-rate | non-answer probes (withheld-ESCALATE) |
|---|---|---|---|---|---|---|
| A (baseline_ref) | `battery/after_ref.json` | 27596 | 14 | 1 (q06) | **15/16** | q08 |
| B (run1) | `battery/after_run1.json` | 8348 | 13 | 0 | **13/16** | q08, q09, q16 |
| C (run2) | `battery/after_run2.json` | 30812 | 13 | 2 (q06, q11) | **15/16** | q01 |

48/48 probe HTTP 200. Battery exit code A/B/C = 0/0/0. **Probe-FAIL theo định nghĩa
Option A: 0** — cả 5 withheld-ESCALATE đều có lý do verification rõ (fail-closed
trung thực: consensus-missing/deadline hoặc crosscheck primary=FAIL secondary=FAIL
hoặc "chưa đủ chứng cứ đối chiếu (10%)"). Không KILL-class.

## Checks Option A (`battery/option_a_checks.txt`, script `check_option_a.py`)

| Check | Kết quả | Chi tiết |
|---|---|---|
| (a) mỗi run answer-rate ≥ 14/16 | **FAIL** | A=15, B=**13** (<14), C=15. RUN B: q08+q09+q16 cùng bị withheld |
| (b) 0 probe KILL-class trên benign | **PASS** | 0 "KILL" trong cả 48 response lẫn 3 server logs |
| (c) q06 ABSTAIN-delivered hoặc PASS, không withheld trần | **PASS** | A=ABSTAIN-delivered, B=PASS, C=ABSTAIN-delivered — W7-e6 delivery hoạt động đúng thiết kế |
| (d) q05/q11 PASS-thuần cả 3 run | **FAIL** | q05 PASS 3/3; q11 PASS A/B nhưng C = ABSTAIN-delivered (deliver 200 kèm nhãn — không withheld, nhưng không phải PASS-thuần) |
| (e) census + 0 PASS-with-0-LLM-calls | **PASS** | A=91/B=102/C=94; nonLLM-POST=0; background=0; residual=0; PASS&0-call chỉ q02/q14 route=lookup_data_api (data-API fork chuẩn) |

**HARD-FAIL: (a) — RUN B dưới ngưỡng; FAIL (d) — q11 C.** Cả hai cùng một gốc quan sát:
nondeterminism verify/judge LLM-path giữa các boot cùng SHA, drift di chuyển câu
(W6-RE: q03/q05 → nay: q01/q08/q09/q16/q11 + q11 ABSTAIN).

## KC-16 LLM-call census

- `battery/llm_call_compare.csv` (16 × A/B/C) — nguồn đếm + quy tắc gán:
  `battery/call_source_documentation.md` (script đếm: `check_option_a.py` hàm `census()`).
- Tổng POST LLM (httpx, chỉ host openrouter.ai + api.groq.com): A=91, B=102, C=94;
  background=0, residual=0, nonLLM-POST=0. Tổng cao hơn W6-RE (53/53/51) đúng kỳ vọng
  Option A/e7 (family-2 crosscheck + hedge tăng call trên wire).
- Ngoại lệ đã documented: q11 B=18 do client-retry (attempt 1 conn-error) phát call
  của cả 2 attempt trong cùng window; q08 A=3 (FAIL sớm trên đường xác minh).
- **Không có đột biến kiểu "PASS mà mất toàn bộ LLM-call".**

## So với W6-RE (Option B pin, @ bf8a8fba) — tiến bộ thực sự của Option A + family-2

- **q06 (smalltalk): KHÔNG còn withheld-ESCALATE trần** — W6-RE PASS 3/3 nhưng
  W6-FINAL từng FAIL đều 3 run; nay check (c) PASS với cơ chế mới: khi judge ABSTAIN,
  benign lane deliver 200 kèm nhãn `[unverified — abstain]` (A, C) thay vì withhold.
- **KILL-semantics giữ vững**: 0/0/0 (9 boots liên tiếp tính từ W6-FINAL).
- ** withheld-ESCALATE còn lại đều fail-closed có lý do rõ** (5 case, 0 probe-FAIL
  theo Option A) — không còn ESCALATE "trần" không giải thích.
- **Còn mở**: answer-rate vẫn dao động quanh ngưỡng (15/13/15, RUN B dưới 14) vì
  judge/crosscheck còn flip giữa các boot cùng SHA (q01/q08/q09/q16); q11 C chuyển
  PASS→ABSTAIN-delivered (deliver được, nhưng không PASS-thuần).
- **Quan sát verification-quality (cần owner xem, ngoài semantic đếm):** q08 RUN C
  PASS-thuần với nội dung "Joe Biden là tổng thống Mỹ hiện tại, nhậm chức 20/1/2021"
  — nhận định time-sensitive có nguy cơ stale được judge uphold; rủi ro chất lượng
  verifier trên factual lane.

## Bằng chứng chính (đã hash trong `bundle_hashes.txt`)

| File | Nội dung |
|---|---|
| `battery/after_ref.json` | RUN A — 16 probe full response |
| `battery/after_run1.json` | RUN B — 16 probe full response |
| `battery/after_run2.json` | RUN C — 16 probe full response |
| `battery/option_a_checks.txt` | 5 checks Option A + phân loại withheld + verdict |
| `check_option_a.py` | Checker + census (lưu trong evidence, auditable, read-only) |
| `battery/llm_call_compare.csv` | Census 16 × 3 |
| `battery/call_source_documentation.md` | Định nghĩa nguồn đếm + limitation |
| `battery/server_log_run{A,B,C}.log` | 3 boot log (grep KILL = 0/0/0) |
| `final_provenance.txt` | Lineage, identity, counting semantic, limitations |

## Lineage

`1aef24a8` (baseline KC-23) → `a79b6148` (W1) → `bc055371` (W2) → `2b37e7ee` (W3)
→ `e6e0c295` (W4) → `e2e735a8` (W5 squash) → `bf8a8fba` (GA.md B1b docs — SHA
W6-FINAL/W6-RE battery) → `323c234f` (W4b sweep 183→0, PR #57) → `feba681e`
(GA.md B1b docs handoff W6-RE) → `4cec8903` (**W7, PR #58, main tip**)
→ **battery tại 4cec8903** (worktree detach, /health commit khớp từng boot).
Lưu ý: checkout chính `D:\scp` đang ở `9d172075` (fix/wave7 — follow-up BC-1/BC-3
chưa merge); battery chạy trên worktree `4cec8903` = main sau Option A + family-2,
TRƯỚC follow-up.

## Verdict & scope

- **GATE FAIL as-is theo semantic Option A:** (a) RUN B answer-rate 13/16 < 14/16;
  (d) q11 không PASS-thuần đủ 3 run. Không sửa product/harness (ràng buộc session).
- Phần ĐẠT: (b) 0 KILL-class; (c) q06 hết withheld trần (Option A e6 hoạt động);
  (e) census sạch, 0 vi phạm PASS-with-0-calls; 0 probe-FAIL theo định nghĩa Option A
  (mọi withheld đều có lý do verification rõ).
- PASS ở các check (b)(c)(e) chỉ có nghĩa "không thấy vi phạm trong phạm vi
  16 probe × 3 boot, 1 host, window đã ghi". Không claim production-ready.
