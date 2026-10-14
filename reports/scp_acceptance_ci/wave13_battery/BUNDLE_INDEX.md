# W13-BATTERY BUNDLE INDEX — battery khép campaign sau Wave 13 (wire
# WeatherDataSource Open-Meteo vào S24 lookup fork — q07 deliver dữ liệu thật)

- **SHA under test:** `ede074ec11bda1979da9bd8016ccb24a82699e17` (main local =
  origin/main, PR #64 squash — "fix(ask-w13): wire WeatherDataSource (Open-Meteo)
  — q07 deliver dữ liệu thời tiết thật (#64)"; main chứa W1–W13. Nội dung W13 =
  branch fix/wave13 tip eb62b224; `git diff ede074ec eb62b224` = EMPTY — toàn cây
  đồng nhất, follow-up seam-fix đã được squash vào PR #64)
- **Ngày chạy:** 2026-10-06, window 21:04–21:27 local (UTC+7)
  (3 boot độc lập cùng SHA, mỗi run 1 boot)
- **Phương pháp:** giống W10/W11/W12 — STABILITY trên cùng SHA, 3 run, chấm bằng
  statistical gate W9 `tools/w6_ask_battery_gate.py --statistical` (2/3 run ≥14
  theo Option-A counting, no-KILL benign, stable-set, withheld phải có reason).
  Đếm theo **Option A**: PASS-thuần = PASS + run_status SUCCESS + HTTP 200;
  ABSTAIN-delivered = ABSTAIN + gov ABSTAIN + nhãn `[unverified — abstain]` +
  HTTP 200; answer-rate = (PASS-thuần + ABSTAIN-delivered)/16.
- **Verdict tổng: GATE PASS (EXIT=0, verdict PASS_WITHIN_SCOPE)** — answer-rate
  **14/15/14**, **3/3 run ≥14/16 (lần đầu cả 3 run đạt ngưỡng kể từ khi W9
  giới thiệu gate)**; 0 KILL; 5/5 withheld có reason; 0 probe-FAIL Option A.
  W13 weather focus: **q07 PASS-thuần 3/3 với dữ liệu Open-Meteo thật +
  provenance "Nguồn dữ liệu: Open-Meteo"** — mục đích wave ĐẠT, đo được runtime.
  Scope: 16 probe × 3 boot, 1 host — không claim production-ready.

## Kết quả chính (answer-rate theo Option A)

| Run | File | Boot PID | PASS-thuần | ABSTAIN-delivered | Answer-rate | non-answer probes (withheld-ESCALATE) |
|---|---|---|---|---|---|---|
| A | `battery/run_a.json` | 3636 | 13 | 1 (q16) | **14/16** | q03, q08 |
| B | `battery/run_b.json` | 30304 | 13 | 2 (q06, q16) | **15/16** | q08 |
| C | `battery/run_c.json` | 23356 | 13 | 1 (q06) | **14/16** | q08, q16 |

48/48 probe HTTP 200. Battery exit code A/B/C = 0/0/0. **Probe-FAIL theo định nghĩa
Option A: 0** — cả 5 withheld-ESCALATE đều có lý do verification rõ (q08×3:
llm_reasoning "chưa đủ chứng cứ đối chiếu (độ tin cậy: 10%)" — stale-fact guard
W8; q03×A: MULTI-LLM FAIL/FAIL consensus=agree + W3-e1 withholding trong log
window; q16×C: consensus=disagree final=None + W3-e1). Không KILL-class.

## STATISTICAL GATE (W9) — `battery/gate_check_output.txt`

```
STAT[run1]: answer-rate 14/16
STAT[run2]: answer-rate 15/16
STAT[run3]: answer-rate 14/16
STAT[stable-set]: 15 probe(s) answered in >=2 runs: ['q01'..'q07','q09'..'q16']
{"mode": "statistical", "verdict": "PASS", "answer_rates": {"run1": 14, "run2": 15, "run3": 14},
 "stable_set": [...], "violations": []}
EXIT=0
```

| Check gate | Kết quả |
|---|---|
| (a) 2/3 run answer-rate ≥ 14/16 | **PASS** — 3/3 run (14 + 15 + 14) |
| (b) 0 KILL-class trên benign | **PASS** — 0 trong 48 response + 3 server log |
| (c) stable-set + 0 hard-FAIL trần 3 run | **PASS** — stable-set 15 probe (chỉ q08 ngoài) |
| (d) withheld-ESCALATE có reason | **PASS** — 5/5 có reason (không violation) |

## WEATHER FOCUS (mục đích chính W13 — chi tiết `battery/w13_verification.txt` §2)

- **q07 được verify runtime TRỰC TIẾP** (khác W12 — battery không có probe
  conversion): q07 = "Thời tiết Hà Nội hôm nay thế nào?" nằm trong bộ 16 câu.
- 3/3 run: PASS/SUCCESS/UPHOLD, route='lookup_data_api', api_name='Open-Meteo',
  latency ~1s (1.08/0.99/1.04s), **0 LLM POST trong window** (fork no-LLM).
- Answer compose 100% từ payload Open-Meteo: nhiệt độ/độ ẩm/gió/WMO (trời quang)
  + dự báo 21.7–27.6°C, giá trị khác nhau giữa boot (A: 22.9°C/69%/3.2 km/h;
  B/C: 22.8°C/68%/2.7 km/h — live fetch, không fixture/cache); provenance nhãn
  "(Nguồn dữ liệu: Open-Meteo — <api_url>)" với api_url khớp từng ký tự field
  v98_classification.api_url.
- Server-log: marker "[S24] /ask forked to data-API (no LLM generation):
  via=l0-keyword domain=weather api=Open-Meteo" trong window q07 cả 3 boot.
  Fetch qua safe_urlopen (urllib) không sinh dòng httpx — đã ghi trung thực
  trong `battery/call_source_documentation.md`.
- Trajectory: W11 withheld 3/3 → W12 ABSTAIN-delivered 3/3 → **W13 PASS-thuần
  3/3 (dữ liệu thật)** — nâng withheld → PASS-thuần attributable trực tiếp tới
  code-change (bằng chứng cơ học, không phải variance).

## So với W12-battery (@ e62eeed3, 14/12/14) và W10 (@ af57ba3b, 12/13/14) — as-is

- Answer-rate: W13 = 14/15/14 (tổng 43, mean 14.33) vs W12 40/13.33, W11 39/13.00,
  W10 39/13.00. Gate (a): W13 **3/3 run ≥14** — mạnh nhất chuỗi (W12 2/3;
  W10/W11 1/3 FAIL).
- Đếm trung thực: q07 chuyển ABSTAIN-delivered → PASS-thuần KHÔNG đổi answer-rate
  (cả hai đều tính 1 theo Option A). +3 điểm tổng đến từ flip chiều tốt của
  q09 (0/3→3/3), q15 (2/3→3/3), q16 (1/3→2/3) — LLM-sampling variance (W13 không
  chạm lane creative/strict-verify), KHÔNG claim là hiệu ứng fix W13. Claim đúng:
  (i) wire W13 hoạt động đúng thiết kế 3/3; (ii) không giảm answer-rate;
  (iii) gate PASS 3/3.
- q08 stale-fact W8 giữ vững: withheld 3/3, reason "chưa đủ chứng cứ đối chiếu".
- KILL-semantics giữ vững: 0/0/0 (chuỗi boots 0-KILL tiếp tục).
- Census: 89/88/89 vs W12 76/71/93, W11 104/102/92, W10 93/93/105; q07 census
  0/0/0 (trước đây 6/6/6) — đường no-LLM mới đúng thiết kế.

## KC-16 LLM-call census

- `battery/llm_call_compare.csv` (16 × A/B/C) — nguồn đếm + quy tắc gán:
  `battery/call_source_documentation.md` (script đếm: `w13_check_option_a.py`
  hàm `census()`).
- Tổng POST LLM (httpx, chỉ host openrouter.ai + api.groq.com): A=89, B=88,
  C=89; background=0, residual=0, nonLLM-POST=0.
- **Hard rule PASS-with-0-LLM-calls: PASS** — 7 case count=0 (q02×A, q02×B,
  q07×A/B/C, q14×A, q14×C) đều là data-API fork chuẩn `v98_classification.route=
  'lookup_data_api'`; q07 weather fork là trạng thái thiết kế MỚI từ W13.

## Bằng chứng chính (đã hash trong `bundle_hashes.txt`)

| File | Nội dung |
|---|---|
| `battery/run_a.json` | RUN A — 16 probe full response |
| `battery/run_b.json` | RUN B — 16 probe full response |
| `battery/run_c.json` | RUN C — 16 probe full response |
| `battery/gate_check_output.txt` | Statistical gate (W9) — verdict PASS, EXIT=0 |
| `battery/option_a_counts.txt` | Đếm Option A per-run + weather focus + withheld adjudication + KILL + census |
| `battery/w13_verification.txt` | Phân tích W13: Option-A counting, q07 focus, so W12/W10, KILL-count, census |
| `w13_check_option_a.py` | Checker + census (evidence dir, auditable, read-only) |
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
`b13fba9a` (W11, PR #61) → `e62eeed3` (W12, PR #63) → `bc3b1bb6` (GA.md B1b docs
— W12 handoff, campaign 2026-10-04→06 đóng) → **`ede074ec` (W13, PR #64 = MAIN
TIP)** → **battery tại ede074ec** (worktree detach, /health commit khớp từng boot).
Lưu ý: checkout chính `D:\scp` đang ở `eb62b224` (branch `fix/wave13` — 3 fix
per-commit + w13a; `git diff ede074ec eb62b224` = 0 dòng — cây toàn phần đồng
nhất, follow-up seam-fix đã squash vào PR #64); battery chạy trên worktree
`ede074ec` = main sau W13.

## Verdict & scope

- **GATE PASS_WITHIN_SCOPE theo statistical gate W9:** 3/3 run ≥14/16
  (14/15/14); 0 probe-FAIL Option A; 0 KILL; 5/5 withheld có reason; census
  sạch; hard rule PASS-with-0-calls PASS; weather focus q07 PASS-thuần 3/3 với
  dữ liệu thật.
- Phạm vi: PASS chỉ nghĩa "không thấy vi phạm trong 16 probe × 3 boot, 1 host,
  window 21:04–21:27 local 2026-10-06". Không claim production-ready.
- Weather W13 được verify runtime trực tiếp qua q07; các flip q03/q06/q16 và
  hồi phục q09/q15 là variance — không suy diễn thành hiệu ứng fix. Không sửa
  product/harness; không push.
