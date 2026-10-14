# W14-BATTERY BUNDLE INDEX — battery khép toàn bộ campaign SCP (W1-W14)

- **SHA under test:** `3880f8382204d46c2f7799da4bee78e9506e85f0` (main — W14
  chain đã merge: c3835989 "fix(ask-w14): geocoding fallback cho weather tier —
  mở rộng ngoài 16-city table (owner duyệt egress)" + 653814a4 "fix(ask-w14):
  gen-with-evidence — time-signal question được web evidence trước generation" +
  8f9fe6ba "fix(ask-w14): egress seam (EE-G1) cho geocode contract tests + T00
  worktree stderr diagnostics" + 3880f838 "fix(ops-w15): untrack runtime audit
  artifacts + gitignore". Git status D:\scp tại lúc chạy: HEAD khớp 3880f838,
  chỉ có file untracked/dashboard-local không thuộc product.)
- **Ngày chạy:** 2026-10-07, window 03:01–03:29 local (UTC+7)
  (3 boot độc lập cùng SHA, mỗi run 1 boot)
- **Phương pháp:** giống W10/W11/W12/W13 — STABILITY trên cùng SHA, 3 run, chấm
  bằng statistical gate W9 `tools/w6_ask_battery_gate.py --statistical` (2/3 run
  ≥14 theo Option-A counting, no-KILL benign, stable-set, withheld phải có
  reason). Đếm theo **Option A**: PASS-thuần = PASS + run_status SUCCESS +
  HTTP 200; ABSTAIN-delivered = ABSTAIN + gov ABSTAIN + nhãn
  `[unverified — abstain]` + HTTP 200; answer-rate = (PASS-thuần +
  ABSTAIN-delivered)/16.
- **Verdict tổng: GATE PASS (EXIT=0, verdict PASS_WITHIN_SCOPE)** — answer-rate
  **15/15/16**, **3/3 run ≥14/16 (mạnh nhất chuỗi W9→W14)**; RUN C = 16/16 sạch
  — lần đầu trong chuỗi battery; 0 KILL; 2/2 withheld có reason; 0 probe-FAIL
  Option A. W13 weather focus giữ vững: **q07 PASS-thuần 3/3 Open-Meteo thật +
  provenance "Nguồn dữ liệu: Open-Meteo"**. W14 focus: đường gen-with-evidence
  q08 trigger 3/3 (web_fallback_used=True), q08 PASS-thuần LẦN ĐẦU (RUN C).
  Scope: 16 probe × 3 boot, 1 host — không claim production-ready.

## Kết quả chính (answer-rate theo Option A)

| Run | File | Boot PID | PASS-thuần | ABSTAIN-delivered | Answer-rate | non-answer probes (withheld-ESCALATE) |
|---|---|---|---|---|---|---|
| A | `battery/run_a.json` | 31240 | 14 | 1 (q16) | **15/16** | q08 |
| B | `battery/run_b.json` | 10604 | 12 | 3 (q05, q06, q16) | **15/16** | q08 |
| C | `battery/run_c.json` | 23180 | 15 | 1 (q16) | **16/16** | (none) |

48/48 probe HTTP 200, 0 client-retry (mọi attempts length=1). Battery exit code
A/B/C = 0/0/0. **Probe-FAIL theo định nghĩa Option A: 0** — cả 2 withheld-ESCALATE
(q08×A, q08×B) đều có lý do verification rõ: llm_reasoning "Suy đoán mô hình
chưa đủ chứng cứ đối chiếu (độ tin cậy: 10%)" — stale-fact guard W8 fail-closed
giữ nguyên. Không KILL-class.

## STATISTICAL GATE (W9) — `battery/gate_check_output.txt`

```
STAT[run1]: answer-rate 15/16
STAT[run2]: answer-rate 15/16
STAT[run3]: answer-rate 16/16
STAT[stable-set]: 15 probe(s) answered in >=2 runs: ['q01'..'q07','q09'..'q16']
{"mode": "statistical", "verdict": "PASS", "answer_rates": {"run1": 15, "run2": 15, "run3": 16},
 "stable_set": [...], "violations": []}
EXIT=0
```

| Check gate | Kết quả |
|---|---|
| (a) 2/3 run answer-rate ≥ 14/16 | **PASS** — 3/3 run (15 + 15 + 16) |
| (b) 0 KILL-class trên benign | **PASS** — 0 trong 48 response + 3 server log |
| (c) stable-set + 0 hard-FAIL trần 3 run | **PASS** — stable-set 15 probe (chỉ q08 ngoài) |
| (d) withheld-ESCALATE có reason | **PASS** — 2/2 có reason (không violation) |

## WEATHER FOCUS (mục đích W13 — hồi quy qua W14; chi tiết `battery/w14_verification.txt` §3a)

- 3/3 run: PASS/SUCCESS/UPHOLD, route='lookup_data_api', api_name='Open-Meteo',
  latency ~0.95s (0.95/0.94/0.95), **0 LLM POST trong window** (fork no-LLM).
- Answer compose từ payload Open-Meteo: A/B = 20.7°C/77%/5.6 km/h; C = 20.6°C/
  77%/5.8 km/h (live fetch giữa boot — khác hẳn giá trị W13 22.8–22.9°C ngày
  2026-10-06, loại trừ fixture/cache); provenance nhãn "(Nguồn dữ liệu:
  Open-Meteo — <api_url>)" khớp field v98_classification.api_url từng run.
- Server-log: marker "[S24] /ask forked to data-API (no LLM generation):
  via=l0-keyword domain=weather api=Open-Meteo" trong window q07 cả 3 boot;
  grep 'geocoding' = 0 hit — geocode fallback W14a KHÔNG trigger (Hà Nội trong
  16-city table), đường W13 giữ nguyên đúng thiết kế.
- Trajectory: W10/W11 withheld 3/3 → W12 ABSTAIN-delivered 3/3 → W13 PASS-thuần
  3/3 → **W14 PASS-thuần 3/3 (giữ vững, không hồi quy)**.

## W14 FOCUS — q08 gen-with-evidence (653814a4; chi tiết `battery/w14_verification.txt` §3b)

- response.web_fallback_used=True 3/3 — bằng chứng cơ học đường pre-gen evidence
  W14d trigger; server-log window q08 có 2 cặp GET web-search (duckduckgo + bing)
  TRƯỚC dòng generation — fetch trước generation đúng thiết kế.
- Verdict as-is: A/B = WITHHELD_ESCALATE (reason 10% — fail-closed giữ nguyên);
  **C = PASS-thuần 'Donald John Trump.'** — q08 PASS LẦN ĐẦU trong chuỗi battery
  (W10→W13: withheld 12/12).
- Đọc trung thực: 1/3 PASS là flip có hướng tốt nhưng KHÔNG đủ claim 'q08 đã
  fix'; claim đúng: đường W14d chạy 3/3, q08 đạt PASS lần đầu, fail-closed
  không bị phá. Geocode fallback W14a (city ngoài bảng) KHÔNG được battery phủ —
  chỉ có 18 test hermetic T02/T05 ở CI level.

## So với W12-battery (@ e62eeed3, 14/12/14) và W10 (@ af57ba3b, 12/13/14) — as-is

- Answer-rate: W14 = 15/15/16 (tổng 46, mean 15.33) vs W13 43/14.33, W12 40/13.33,
  W11 39/13.00, W10 39/13.00. Gate (a): W14 **3/3 run ≥14** — mạnh nhất chuỗi
  (W13 3/3; W12 2/3; W10/W11 1/3 FAIL).
- Đếm trung thực: +6 điểm so với W12 gồm (i) q08 PASS-thuần ×1 — phần attributable
  trực tiếp tới W14d (đường mới + flag + log); (ii) flip q05/q06 ABSTAIN-delivered
  (B) và q16 ABSTAIN-delivered stable 3/3 — LLM-sampling variance, W14 không chạm
  lane summarization/smalltalk/opinion, KHÔNG claim hiệu ứng fix.
- q07 giữ PASS-thuần 3/3 (W13 không hồi quy). KILL-semantics giữ vững: 0/0/0.
- Census: 88/90/90 vs W13 89/88/89, W12 76/71/93, W11 104/102/92, W10 93/93/105;
  q08 census 6/6/6 — không tăng so với W13 (khớp thiết kế W14d: tái dùng pre-gen
  evidence, không double egress).

## KC-16 LLM-call census

- `battery/llm_call_compare.csv` (16 × A/B/C) — nguồn đếm + quy tắc gán:
  `battery/call_source_documentation.md` (script đếm: `w14_check_option_a.py`
  hàm `census()`).
- Tổng POST LLM (httpx, chỉ host openrouter.ai + api.groq.com): A=88, B=90,
  C=90; background=0, residual=0, nonLLM-POST=0.
- **Hard rule PASS-with-0-LLM-calls: PASS** — 7 case count=0 (q02×A, q02×B,
  q07×A/B/C, q14×A, q14×C) đều là data-API fork chuẩn `v98_classification.route=
  'lookup_data_api'`.

## Bằng chứng chính (đã hash trong `bundle_hashes.txt`)

| File | Nội dung |
|---|---|
| `battery/run_a.json` | RUN A — 16 probe full response |
| `battery/run_b.json` | RUN B — 16 probe full response |
| `battery/run_c.json` | RUN C — 16 probe full response |
| `battery/health_run{A,B,C}.json` + `battery/ready_run{A,B,C}.json` | Identity mỗi boot (/health commit khớp + /ready 200) |
| `battery/gate_check_output.txt` | Statistical gate (W9) — verdict PASS, EXIT=0 |
| `battery/option_a_counts.txt` | Đếm Option A per-run + weather focus + q08 focus + withheld adjudication + KILL + census |
| `battery/w14_verification.txt` | Phân tích W14: Option-A counting, gate, q07 weather focus, q08 focus, so W12/W10, census, KILL-count |
| `w14_check_option_a.py` | Checker + census (evidence dir, auditable, read-only) |
| `battery/llm_call_compare.csv` | Census 16 × 3 |
| `battery/call_source_documentation.md` | Định nghĩa nguồn đếm + limitation |
| `battery/server_log_run{A,B,C}.log` | 3 boot log (grep KILL = 0/0/0) |
| `battery/battery_run{A,B,C}_console.log` | Console output runner từng run |
| `bundle_hashes.txt` | SHA256 runner/wrapper/gate + mọi evidence file |
| `final_provenance.txt` | Lineage, identity, counting semantic, limitations |

## Lineage

`1aef24a8` (baseline KC-23) → `a79b6148` (W1) → `bc055371` (W2) → `2b37e7ee` (W3)
→ `e6e0c295` (W4) → `e2e735a8` (W5 squash) → `bf8a8fba` (GA.md B1b docs — SHA
W6-FINAL/W6-RE battery) → `323c234f` (W4b sweep 183→0, PR #57) → `feba681e`
(GA.md B1b docs handoff W6-RE) → `4cec8903` (W7, PR #58) → `38bac514` (W8, PR #59)
→ `dfda8475` (W9, PR #60) → `af57ba3b` (GA.md B1b docs — W10 battery SHA) →
`b13fba9a` (W11, PR #61) → `e62eeed3` (W12, PR #63) → `bc3b1bb6` (GA.md B1b docs
— W12 handoff) → `ede074ec` (W13, PR #64) → `44e5bf02` (GA.md B1b docs — W13
handoff, campaign đóng) → `2fefcbc5` (docs(audit) full-system audit @ 44e5bf02)
→ `8d1d6b04` (GA.md B1b docs — BC-2 verify) → `c3835989` (W14a/b/c geocode
fallback weather tier) → `653814a4` (W14d/e/f gen-with-evidence time-signal) →
`8f9fe6ba` (W14 egress seam EE-G1 tests + T00 stderr diagnostics) →
**`3880f838` (ops-w15 untrack artifacts + gitignore = MAIN TIP)** →
**battery tại 3880f838** (worktree detach, /health commit khớp từng boot).

## Verdict & scope

- **GATE PASS_WITHIN_SCOPE theo statistical gate W9:** 3/3 run ≥14/16
  (15/15/16); 0 probe-FAIL Option A; 0 KILL; 2/2 withheld có reason; census
  sạch; hard rule PASS-with-0-calls PASS; q07 PASS-thuần 3/3 dữ liệu thật;
  q08 PASS-thuần lần đầu (1/3) với đường W14d trigger 3/3.
- Phạm vi: PASS chỉ nghĩa "không thấy vi phạm trong 16 probe × 3 boot, 1 host,
  window 03:01–03:29 local 2026-10-07". Không claim production-ready.
- Geocode fallback W14a không được battery phủ (chỉ test hermetic CI level);
  flip q05/q06/q08 là variance trừ q08-C (đường mới, bằng chứng cơ học). Không
  sửa product/harness; không push.
