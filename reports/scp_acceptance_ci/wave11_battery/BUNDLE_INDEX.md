# W11-BATTERY BUNDLE INDEX — battery xác nhận sau 3 code-fix (f1 carve-out
# starvation + f2 marker gap + f3 async BC-3)

- **SHA under test:** `b13fba9a5bdf3d5735187df2ec3743548789b032` (main, PR #61 squash —
  "fix(ask-w11): carve-out starvation + marker gap + async BC-3 parity — 3 code-fix
  từ chuỗi nhân quả W10"; main chứa W1–W10. Nội dung 3 fix = branch fix/wave11
  051107da; `git diff 051107da b13fba9a -- scp/ tools/` = EMPTY — cây product đồng nhất)
- **Ngày chạy:** 2026-10-06, window 14:21–14:53 local (07:21–07:53Z, UTC+7)
  (3 boot độc lập cùng SHA, mỗi run 1 boot)
- **Phương pháp:** giống W10 — STABILITY trên cùng SHA, 3 run, chấm bằng statistical
  gate W9 `tools/w6_ask_battery_gate.py --statistical` (2/3 run ≥14 theo Option-A
  counting, no-KILL benign, stable-set, withheld phải có reason). Đếm theo
  **Option A**: PASS-thuần = PASS + run_status SUCCESS + HTTP 200; ABSTAIN-delivered
  = ABSTAIN + gov ABSTAIN + nhãn `[unverified — abstain]` + HTTP 200; answer-rate =
  (PASS-thuần + ABSTAIN-delivered)/16.
- **Verdict tổng: GATE FAIL (EXIT=1, báo as-is)** — chi tiết bên dưới. Không sửa
  product/harness, không push.

## Kết quả chính (answer-rate theo Option A)

| Run | File | Boot PID | PASS-thuần | ABSTAIN-delivered | Answer-rate | non-answer probes (withheld-ESCALATE) |
|---|---|---|---|---|---|---|
| A | `battery/run_a.json` | 6880 | 13 | 1 (q16) | **14/16** | q07, q08 |
| B | `battery/run_b.json` | 22212 | 10 | 2 (q05, q06) | **12/16** | q03, q07, q08, q16 |
| C | `battery/run_c.json` | 30204 | 12 | 1 (q16) | **13/16** | q07, q08, q11 |

48/48 probe HTTP 200. Battery exit code A/B/C = 0/0/0. **Probe-FAIL theo định nghĩa
Option A: 0** — cả 9 withheld-ESCALATE đều có lý do verification rõ (llm_reasoning
"chưa đủ chứng cứ đối chiếu" / crosscheck FAIL–disagreement / deadline 1-opinion
fail-closed / W8-e1 time-signal downgrade trong log window). Không KILL-class.

## STATISTICAL GATE (W9) — `battery/gate_check_output.txt`

```
STAT[run1]: answer-rate 14/16
STAT[run2]: answer-rate 12/16
STAT[run3]: answer-rate 13/16
STAT[stable-set]: 14 probe(s) answered in >=2 runs
{"mode": "statistical", "verdict": "FAIL", "answer_rates": {"run1": 14, "run2": 12, "run3": 13},
 "violations": ["(a) only 1/3 runs have answer-rate >= 14/16"]}
EXIT=1
```

| Check gate | Kết quả |
|---|---|
| (a) 2/3 run answer-rate ≥ 14/16 | **FAIL** — chỉ RUN A (14) đạt; B=12, C=13 |
| (b) 0 KILL-class trên benign | **PASS** — 0 trong 48 response + 3 server log |
| (c) stable-set + 0 hard-FAIL trần 3 run | **PASS** — stable-set 14 probe |
| (d) withheld-ESCALATE có reason | **PASS** — 9/9 có reason (không violation) |

## Q07 FOCUS (mục đích chính fix f1 — chi tiết `battery/w11_verification.txt` §2)

- **f1 hoạt động ở tầng router:** `routing.reason='lookup_signal:weather_fact'`
  ĐỦ 3/3 run (W10: `'interrogative_vi'` 3/3 — starvation router-level). LOOKUP
  fork miss → LLM fallback đúng đường (weather data source vẫn chưa wire — gap
  feature đã ghi W10).
- Model tự refusal-abstain 3/3 ("chưa có dữ liệu thời tiết" A/B — marker f2;
  "không có dữ liệu" C).
- **Verdict: FAIL/ESCALATE 3/3, KHÔNG deliver** — RUN A: crosscheck consensus PASS
  nhưng `[W8-e1] time-signal guard` HẠ PASS→FAIL (factual, không evidence tươi);
  RUN B/C: crosscheck disagreement (primary PASS, secondary FAIL) → UNKNOWN-withheld
  (f3 giữ UNKNOWN đúng contract W3-e1). Kỳ vọng "deliver-with-label hoặc PASS"
  CHƯA ĐẠT trong battery này.
- Đọc as-is: q07 withheld sau W11 KHÔNG còn vì starvation router (bug f1 đã hết ở
  tầng router) mà vì W8-e1 + disagreement + thiếu data source — guard fail-closed
  hoạt động đúng thiết kế. Carve-out W7-e6 (đòi verdict ABSTAIN) không bao giờ
  được chạm trên 2 đường này. Câu hỏi semantics (override carve-out cho
  refusal-shape realtime-no-tool?) thuộc owner — không sửa trong session này.

## So với W10-battery (@ dfda8475, 12/13/14) — quan sát as-is

- Answer-rate KHÔNG nâng: W11 = 14/12/13 (tổng 39 = 39, mean 13.0 cả hai).
  Statistical gate (a) FAIL cả hai wave (1/3 run ≥14). Phân bố đổi chỗ nằm trong
  inherent variance LLM-sampling đã root-cause W6-RE/W7; q07 vẫn đóng góp 0 điểm
  cả hai wave.
- Bản chất nhóm withheld cùng độ lớn (9 vs 9), thành phần trôi theo variance;
  q07+q08 withheld ĐỦ 3/3 cả hai wave (nhất quán).
- KILL-semantics giữ vững: 0/0/0 (16 boots liên tiếp theo cách đếm GA.md).
- q08 stale-fact W8 giữ vững: answer stale "Joe Biden" (RUN A) bị W8-e1 chặn,
  KHÔNG deliver — không tái hiện cảnh báo W7 (PASS-thuần stale).
- Census cùng mức (104/102/92 vs 93/93/105).

## KC-16 LLM-call census

- `battery/llm_call_compare.csv` (16 × A/B/C) — nguồn đếm + quy tắc gán:
  `battery/call_source_documentation.md` (script đếm: `w11_check_option_a.py`
  hàm `census()`).
- Tổng POST LLM (httpx, chỉ host openrouter.ai + api.groq.com): A=104, B=102,
  C=92; background=0, residual=0, nonLLM-POST=0.
- **Hard rule PASS-with-0-LLM-calls: PASS** — 3 case count=0 (q02×A, q14×A,
  q02×B) đều là data-API fork chuẩn `v98_classification.route='lookup_data_api'`
  (trạng thái thiết kế đã documented từ wave1).

## Bằng chứng chính (đã hash trong `bundle_hashes.txt`)

| File | Nội dung |
|---|---|
| `battery/run_a.json` | RUN A — 16 probe full response |
| `battery/run_b.json` | RUN B — 16 probe full response |
| `battery/run_c.json` | RUN C — 16 probe full response |
| `battery/gate_check_output.txt` | Statistical gate (W9) — verdict FAIL, EXIT=1 |
| `battery/option_a_counts.txt` | Đếm Option A per-run + q07 focus + withheld adjudication + KILL + census |
| `battery/w11_verification.txt` | Phân tích W11: Option-A counting, q07 focus, KILL-count, so W10, census |
| `w11_check_option_a.py` | Checker + census (evidence dir, auditable, read-only) |
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
→ `dfda8475` (W9, PR #60) → `af57ba3b` (GA.md B1b docs — W10 battery SHA + campaign
đóng) → **`b13fba9a` (W11, PR #61 = MAIN TIP)**
→ **battery tại b13fba9a** (worktree detach, /health commit khớp từng boot).
Lưu ý: checkout chính `D:\scp` đang ở `051107da` (branch `fix/wave11` — 3 fix
per-commit; cây scp/+tools/ đồng nhất b13fba9a, đã kiểm chứng diff-empty);
battery chạy trên worktree `b13fba9a` = main sau W11.

## Verdict & scope

- **GATE FAIL as-is theo statistical gate W9:** (a) chỉ 1/3 run đạt answer-rate
  ≥14/16 (14/12/13). Không sửa product/harness (ràng buộc session).
- Phần ĐẠT: (b) 0 KILL-class; (c) stable-set 14 probe, 0 hard-FAIL trần; (d) 9/9
  withheld có reason; 0 probe-FAIL theo định nghĩa Option A; census sạch; hard
  rule PASS-with-0-calls PASS (data-API fork chuẩn); f1 loại starvation router
  (reason tag weather_fact 3/3); f3 không leak ABSTAIN trên disagreement;
  f2 pin unit level, không regression.
- Chưa ĐẠT: q07 deliver-with-label/PASS (mục tiêu end-to-end f1) — còn chặn bởi
  W8-e1 (consensus case), disagreement (RUN B/C), thiếu weather data source.
- FAIL ở check (a) chỉ có nghĩa statistical gate không đạt trong phạm vi 16 probe ×
  3 boot, 1 host, window 14:21–14:53 local 2026-10-06. Không claim production-ready.
