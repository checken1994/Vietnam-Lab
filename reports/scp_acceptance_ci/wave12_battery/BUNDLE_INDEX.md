# W12-BATTERY BUNDLE INDEX — battery xác nhận sau Wave 12 (ConversionDataSource
# wire + domain taxonomy repair)

- **SHA under test:** `e62eeed3ff8474b7292a2c036c6cdb343850e4fb` (main, PR #63 squash —
  "fix(ask-w12): wire ConversionDataSource (11/11 conversion offline) + domain
  taxonomy repair"; main chứa W1–W12. Nội dung W12 = branch fix/wave12 tip
  034cd70d; `git diff e62eeed3 034cd70d -- scp/ tools/` = EMPTY — cây product
  đồng nhất)
- **Ngày chạy:** 2026-10-06, window 18:01–18:25 local (UTC+7)
  (3 boot độc lập cùng SHA, mỗi run 1 boot)
- **Phương pháp:** giống W10/W11 — STABILITY trên cùng SHA, 3 run, chấm bằng
  statistical gate W9 `tools/w6_ask_battery_gate.py --statistical` (2/3 run ≥14
  theo Option-A counting, no-KILL benign, stable-set, withheld phải có reason).
  Đếm theo **Option A**: PASS-thuần = PASS + run_status SUCCESS + HTTP 200;
  ABSTAIN-delivered = ABSTAIN + gov ABSTAIN + nhãn `[unverified — abstain]` +
  HTTP 200; answer-rate = (PASS-thuần + ABSTAIN-delivered)/16.
- **Verdict tổng: GATE PASS (EXIT=0, verdict PASS_WITHIN_SCOPE)** — answer-rate
  14/12/14, 2/3 run ≥14/16 (lần đầu gate (a) PASS kể từ khi W9 giới thiệu gate);
  0 KILL; 8/8 withheld có reason; 0 probe-FAIL Option A. Chi tiết bên dưới.
  Scope: 16 probe × 3 boot, 1 host — không claim production-ready.

## Kết quả chính (answer-rate theo Option A)

| Run | File | Boot PID | PASS-thuần | ABSTAIN-delivered | Answer-rate | non-answer probes (withheld-ESCALATE) |
|---|---|---|---|---|---|---|
| A | `battery/run_a.json` | 13644 | 12 | 2 (q07, q16) | **14/16** | q08, q09 |
| B | `battery/run_b.json` | 22304 | 10 | 2 (q06, q07) | **12/16** | q08, q09, q15, q16 |
| C | `battery/run_c.json` | 23212 | 11 | 3 (q06, q07, q16) | **14/16** | q08, q09 |

48/48 probe HTTP 200. Battery exit code A/B/C = 0/0/0. **Probe-FAIL theo định nghĩa
Option A: 0** — cả 8 withheld-ESCALATE đều có lý do verification rõ (llm_reasoning
"chưa đủ chứng cứ đối chiếu" cho q08/q15; crosscheck deadline 15s fail-closed +
consensus=missing_distinct_providers / judge FAIL–agree cho q09/q16). Không
KILL-class.

## STATISTICAL GATE (W9) — `battery/gate_check_output.txt`

```
STAT[run1]: answer-rate 14/16
STAT[run2]: answer-rate 12/16
STAT[run3]: answer-rate 14/16
STAT[stable-set]: 14 probe(s) answered in >=2 runs: [q01..q05,q06,q07,q10..q16]
{"mode": "statistical", "verdict": "PASS", "answer_rates": {"run1": 14, "run2": 12, "run3": 14},
 "stable_set": [...], "violations": []}
EXIT=0
```

| Check gate | Kết quả |
|---|---|
| (a) 2/3 run answer-rate ≥ 14/16 | **PASS** — RUN A (14) + RUN C (14) |
| (b) 0 KILL-class trên benign | **PASS** — 0 trong 48 response + 3 server log |
| (c) stable-set + 0 hard-FAIL trần 3 run | **PASS** — stable-set 14 probe |
| (d) withheld-ESCALATE có reason | **PASS** — 8/8 có reason (không violation) |

## CONVERSION FOCUS (mục đích chính W12 — chi tiết `battery/w12_verification.txt` §2)

- **Battery 16 probe KHÔNG chứa conversion probe** (quét cơ học 0 unit-token;
  bộ câu factual/math/translation/summarization/smalltalk/realtime/creative/
  nonsense/meta/opinion). Marker conversion tier ('ConversionDataSource (local)'
  / 'local:scp/data_sources/conversion.py') trong 16×3 response: 0 hit — đường
  local conversion không được chạm (đúng thiết kế, tier chỉ trigger câu quy đổi).
- → **Verify conversion qua unit test đã có**: `tests/T07_learning/
  test_w12_conversion_lookup.py` — pytest --collect-only @ worktree e62eeed3:
  17 test cases / 7 test functions (11 case data-driven bám conversion goldset
  11/11). Cả 2 file W12 (conversion + domain hint repair) = 41 test cases / 11
  test functions. Ghi chú as-is: task nhắc "20 test", đếm thực tế 41 cases —
  báo số thực.
- W12-f2 taxonomy repair: 11 boost-keyword mới vs 16 câu battery = 0 hit →
  repair là no-op trên battery này. **Hiệu ứng fix W12 không đo được qua battery
  16 câu** — battery này chứng minh KHÔNG-REGRESSION + gate W9 PASS.

## So với W11-battery (@ b13fba9a, 14/12/13) và W10 (@ af57ba3b, 12/13/14) — as-is

- Answer-rate: W12 = 14/12/14 (tổng 40, mean 13.33) vs W11 14/12/13 và W10
  12/13/14 (cả hai tổng 39, mean 13.00). Gate (a): W12 PASS (2/3 run ≥14) —
  W10/W11 FAIL (1/3). +1 điểm tổng nằm trong inherent variance LLM-sampling
  (flip q06/q15/q16) — W12 không chạm probe nào của battery (mục conversion
  focus) → KHÔNG claim "fix W12 nâng answer-rate"; claim chính xác: "W12 không
  làm giảm; gate W9 PASS trong battery này".
- Q07 (focus W11): routing.reason='lookup_signal:weather_fact' 3/3 giữ vững;
  lần này ABSTAIN-delivered 3/3 (W11: withheld 3/3 do W8-e1 + disagreement).
  Kỳ vọng W11 "deliver-with-label" cho q07 ĐÃ QUAN SÁT ĐƯỢC — do refusal-shape
  model rơi đúng nhánh abstain (variance), không phải code-change W12; nhất
  quán 3/3 boot là tín hiệu đáng chú ý, việc pin/harden thuộc owner.
- q08 stale-fact W8 giữ vững: withheld 3/3, reason "chưa đủ chứng cứ đối chiếu".
- KILL-semantics giữ vững: 0/0/0 (chuỗi boots 0-KILL tiếp tục).
- Census cùng mức (76/71/93 vs W11 104/102/92, W10 93/93/105).

## KC-16 LLM-call census

- `battery/llm_call_compare.csv` (16 × A/B/C) — nguồn đếm + quy tắc gán:
  `battery/call_source_documentation.md` (script đếm: `w12_check_option_a.py`
  hàm `census()`).
- Tổng POST LLM (httpx, chỉ host openrouter.ai + api.groq.com): A=76, B=71,
  C=93; background=0, residual=0, nonLLM-POST=0.
- **Hard rule PASS-with-0-LLM-calls: PASS** — 4 case count=0 (q02×A, q14×A,
  q02×C, q14×C) đều là data-API fork chuẩn `v98_classification.route=
  'lookup_data_api'` (trạng thái thiết kế đã documented từ wave1).

## Bằng chứng chính (đã hash trong `bundle_hashes.txt`)

| File | Nội dung |
|---|---|
| `battery/run_a.json` | RUN A — 16 probe full response |
| `battery/run_b.json` | RUN B — 16 probe full response |
| `battery/run_c.json` | RUN C — 16 probe full response |
| `battery/gate_check_output.txt` | Statistical gate (W9) — verdict PASS, EXIT=0 |
| `battery/option_a_counts.txt` | Đếm Option A per-run + conversion focus + withheld adjudication + KILL + census |
| `battery/w12_verification.txt` | Phân tích W12: Option-A counting, conversion, so W10/W11, KILL-count, census |
| `w12_check_option_a.py` | Checker + census (evidence dir, auditable, read-only) |
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
→ `dfda8475` (W9, PR #60) → `af57ba3b` (GA.md B1b docs — W10 battery SHA) →
`b13fba9a` (W11, PR #61) → **`e62eeed3` (W12, PR #63 = MAIN TIP)**
→ **battery tại e62eeed3** (worktree detach, /health commit khớp từng boot).
Lưu ý: checkout chính `D:\scp` đang ở `034cd70d` (branch `fix/wave12` — 2 fix
per-commit; cây scp/+tools/ đồng nhất e62eeed3, đã kiểm chứng diff-empty);
battery chạy trên worktree `e62eeed3` = main sau W12.

## Verdict & scope

- **GATE PASS_WITHIN_SCOPE theo statistical gate W9:** (a) 2/3 run ≥14/16
  (14/12/14); 0 probe-FAIL Option A; 0 KILL; 8/8 withheld có reason; census
  sạch; hard rule PASS-with-0-calls PASS.
- Phạm vi: PASS chỉ nghĩa "không thấy vi phạm trong 16 probe × 3 boot, 1 host,
  window 18:01–18:25 local 2026-10-06". Không claim production-ready.
- Conversion W12-f1 chỉ pin unit level (battery không có conversion probe);
  taxonomy W12-f2 no-op trên battery; +1 điểm answer-rate là variance, không
  phải hiệu ứng fix. Không sửa product/harness; không push.
