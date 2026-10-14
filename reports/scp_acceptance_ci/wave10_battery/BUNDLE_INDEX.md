# W10-BATTERY BUNDLE INDEX — battery khép campaign SCP qua STATISTICAL GATE mới (W9)

- **SHA under test:** `dfda847548177df1c9f4d3f3cdea972f44ab58c1` (main tip, PR #60 —
  "test(w9): statistical battery gate + flake determinism ×2 + dashboard audit 6-high fix";
  main chứa W1–W9: statistical gate + Option A + family-2)
- **Ngày chạy:** 2026-10-06, window 05:42Z–06:06Z (3 boot độc lập cùng SHA, mỗi run 1 boot)
- **Phương pháp:** STABILITY trên cùng SHA — 3 run, chấm bằng gate statistical W9
  `tools/w6_ask_battery_gate.py --statistical` (2/3 run ≥14 theo Option-A counting,
  no-KILL benign, stable-set, withheld phải có reason). Đếm theo **Option A** (kế thừa
  W7, owner duyệt 2026-10-05): PASS-thuần = PASS + run_status SUCCESS + HTTP 200;
  ABSTAIN-delivered = ABSTAIN + gov ABSTAIN + nhãn `[unverified — abstain]` + HTTP 200;
  answer-rate = (PASS-thuần + ABSTAIN-delivered)/16.
- **Verdict tổng: GATE FAIL (EXIT=1, báo as-is)** — chi tiết bên dưới. Không sửa
  product/harness.

## Kết quả chính (answer-rate theo Option A)

| Run | File | Boot PID | PASS-thuần | ABSTAIN-delivered | Answer-rate | non-answer probes (withheld-ESCALATE) |
|---|---|---|---|---|---|---|
| A | `battery/run_a.json` | 2900 | 11 | 1 (q16) | **12/16** | q03, q05, q07, q08 |
| B | `battery/run_b.json` | 18916 | 12 | 1 (q16) | **13/16** | q07, q08, q12 |
| C | `battery/run_c.json` | 26136 | 12 | 2 (q11, q16) | **14/16** | q07, q08 |

48/48 probe HTTP 200. Battery exit code A/B/C = 0/0/0. **Probe-FAIL theo định nghĩa
Option A: 0** — cả 9 withheld-ESCALATE đều có lý do verification rõ (fail-closed trung
thực: llm_reasoning "Suy đoán mô hình chưa đủ chứng cứ đối chiếu (độ tin cậy: 10%)"
hoặc crosscheck primary/secondary FAIL + "withholding unverified" trong log window).
Không KILL-class.

## STATISTICAL GATE (W9) — `battery/gate_check_output.txt`

```
STAT[run1]: answer-rate 12/16
STAT[run2]: answer-rate 13/16
STAT[run3]: answer-rate 14/16
STAT[stable-set]: 14 probe(s) answered in >=2 runs: [q01..q16 trừ q07,q08]
{"mode": "statistical", "verdict": "FAIL", "answer_rates": {"run1": 12, "run2": 13, "run3": 14},
 "violations": ["(a) only 1/3 runs have answer-rate >= 14/16"]}
EXIT=1
```

| Check gate | Kết quả |
|---|---|
| (a) 2/3 run answer-rate ≥ 14/16 | **FAIL** — chỉ RUN C (14) đạt; A=12, B=13 |
| (b) 0 KILL-class trên benign | **PASS** — 0 trong 48 response + 3 server log |
| (c) stable-set + 0 hard-FAIL trần 3 run | **PASS** — stable-set 14 probe |
| (d) withheld-ESCALATE có reason | **PASS** — 9/9 có reason (không violation) |

## Chi tiết per-run (`battery/option_a_counts.txt`, script `w10_check_option_a.py`)

- RUN A 12/16: q03+q05 withheld (crosscheck FAIL), q07+q08 withheld; q16 ABSTAIN-delivered.
- RUN B 13/16: q07+q08+q12 withheld; q16 ABSTAIN-delivered.
- RUN C 14/16: q07+q08 withheld; q11+q16 ABSTAIN-delivered (deliver 200 kèm nhãn).
- Flip giữa boot cùng SHA (drift trôi câu — đúng dạng inherent variance W6-RE/W7):
  q03 (A withheld → B,C PASS), q05 (A withheld → B,C PASS), q11 (A,B PASS → C
  ABSTAIN-delivered), q12 (A,C PASS → B withheld).
- **Mới so W7:** q07 (realtime weather) + q08 (time-sensitive) withheld-ESCALATE
  **đủ 3/3 run** — nhất quán, hết flip (W7: q08 flip A/B FAIL → C PASS-thuần với nội
  dung stale "Joe Biden"; nay fail-closed ổn định sau W8 stale-fact verifier).

## KC-16 LLM-call census

- `battery/llm_call_compare.csv` (16 × A/B/C) — nguồn đếm + quy tắc gán:
  `battery/call_source_documentation.md` (script đếm: `w10_check_option_a.py` hàm `census()`).
- Tổng POST LLM (httpx, chỉ host openrouter.ai + api.groq.com): A=93, B=93, C=105;
  background=0, residual=0, nonLLM-POST=0. Cùng mức W7 (91/102/94) — đúng kỳ vọng
  (W9 chỉ thêm gate, không đổi đường /ask runtime).
- Ngoại lệ documented: q05 RUN C=17 do client-retry (attempt 1 conn-error → attempt 2
  200) phát call của cả 2 attempt trong cùng window; q02/q14 count=0 là data-API fork
  chuẩn `route=lookup_data_api`.
- **Hard rule PASS-with-0-LLM-calls: 0 vi phạm ngoài data-API fork chuẩn** (5 case
  q02/q14 × A/B/C — nếu đếm nghĩa đen 0 tuyệt đối, cần owner ghi nhận; đã ghi rõ trong
  `option_a_counts.txt`).

## So với W7-battery (@ 4cec8903, 15/13/15) — quan sát as-is

- Answer-rate THẤP HƠN W7 (12/13/14 so với 15/13/15). Driver chính, xác minh từ
  response JSON W7: **q07 chuyển PASS-thuần 3/3 → withheld-ESCALATE 3/3** (W7 deliver
  PASS với câu "không có dữ liệu thời tiết hiện tại"; nay fail-closed withhold — mất
  3 answer-rate points); cộng flips q03/q05 (A withheld; W7 PASS 3/3) và q12 (B
  withheld; W7 PASS 3/3), và q08-C không còn PASS-thuần stale (xem dưới).
- q08 stale-fact (sau W8 stale-fact verifier): hết tình trạng PASS-thuần với nội dung
  stale "Joe Biden..." (W7 RUN C) — nay withheld-ESCALATE 3/3 (an toàn hơn, trả lời
  ít hơn).
- KILL-semantics giữ vững: 0/0/0 (12 boots liên tiếp tính từ W6-FINAL).
- 0 probe-FAIL theo định nghĩa Option A (mọi withheld đều có lý do verification rõ) —
  giữ nguyên tính chất W7.

## Bằng chứng chính (đã hash trong `bundle_hashes.txt`)

| File | Nội dung |
|---|---|
| `battery/run_a.json` | RUN A — 16 probe full response |
| `battery/run_b.json` | RUN B — 16 probe full response |
| `battery/run_c.json` | RUN C — 16 probe full response |
| `battery/gate_check_output.txt` | Statistical gate (W9) — verdict FAIL, EXIT=1 |
| `battery/option_a_counts.txt` | Đếm Option A per-run + withheld adjudication + KILL + census |
| `w10_check_option_a.py` | Checker + census (evidence dir, auditable, read-only) |
| `battery/llm_call_compare.csv` | Census 16 × 3 |
| `battery/call_source_documentation.md` | Định nghĩa nguồn đếm + limitation |
| `battery/server_log_run{A,B,C}.log` | 3 boot log (grep KILL = 0/0/0) |
| `bundle_hashes.txt` | SHA256 runner/wrapper/gate + mọi evidence file |
| `final_provenance.txt` | Lineage, identity, counting semantic, limitations |

## Lineage

`1aef24a8` (baseline KC-23) → `a79b6148` (W1) → `bc055371` (W2) → `2b37e7ee` (W3)
→ `e6e0c295` (W4) → `e2e735a8` (W5 squash) → `bf8a8fba` (GA.md B1b docs — SHA
W6-FINAL/W6-RE battery) → `323c234f` (W4b sweep 183→0, PR #57) → `feba681e`
(GA.md B1b docs handoff W6-RE) → `4cec8903` (W7, PR #58) → `38bac514` (W8, PR #59)
→ `dfda8475` (**W9, PR #60, main tip**)
→ **battery tại dfda8475** (worktree detach, /health commit khớp từng boot).
Lưu ý: checkout chính `D:\scp` đang ở `701bc2d2` (fix(ops-w9) dashboard npm audit
chưa merge); battery chạy trên worktree `dfda8475` = main sau W9, TRƯỚC commit đó.

## Verdict & scope

- **GATE FAIL as-is theo statistical gate W9:** (a) chỉ 1/3 run đạt answer-rate ≥14/16
  (12/13/14). Không sửa product/harness (ràng buộc session).
- Phần ĐẠT: (b) 0 KILL-class; (c) stable-set 14 probe, 0 hard-FAIL trần; (d) 9/9
  withheld có reason; 0 probe-FAIL theo định nghĩa Option A; census sạch, hard rule
  PASS-with-0-calls không vi phạm (ngoại trừ data-API fork chuẩn đã documented).
- FAIL ở check (a) chỉ có nghĩa statistical gate không đạt trong phạm vi 16 probe ×
  3 boot, 1 host, window 05:42–06:06Z 2026-10-06. Không claim production-ready.
