# W16-BATTERY BUNDLE INDEX — battery hoàn thiện W16 (f4/f5/g1) @ d64e8a17

- **SHA under test:** `d64e8a17017da9b233f5a14267e50ac77048b5e6` (main — W16
  chain đã merge: a85a77e1 "fix(ask-w16): f4 factual-ABSTAIN-delivery — mở
  deliver-with-label sang lane FACTUAL khi answer honest-abstain (owner duyệt
  1+2)" + 8d24e9c7 "fix(ask-w16): f5 carve-out un-starve — nới domain-based
  (weather/finance) cho realtime-no-tool carve-out + pin W11-f1" + d64e8a17
  "fix(judge-w16): g1 ABSTAIN-detector inversion — conv self-report whitelist +
  refusal safe-segment grammar (BC-E1/E2/E3)"; trước đó 5a0ed49d ops-w15 KILL
  label + sharp CVE bump. Git status D:\scp tại lúc chạy: HEAD khớp d64e8a17,
  chỉ có file untracked/dashboard-local không thuộc product.)
- **Ngày chạy:** 2026-10-07, RUN A 16:31–16:41 + RUN B 16:42–16:48 (agent
  trước — froze sau run B, evidence salvaged), RUN C 18:47–18:53 local (UTC+7,
  session W16-C) — 3 boot độc lập cùng SHA, mỗi run 1 boot
- **Phương pháp:** giống W10–W14 — STABILITY trên cùng SHA, 3 run, chấm bằng
  statistical gate W9 `tools/w6_ask_battery_gate.py --statistical` (2/3 run
  ≥14 theo Option-A counting, no-KILL benign, stable-set, withheld phải có
  reason). Đếm theo **Option A**: PASS-thuần = PASS + run_status SUCCESS +
  HTTP 200; ABSTAIN-delivered = ABSTAIN + gov ABSTAIN + nhãn
  `[unverified — abstain]` + HTTP 200; answer-rate = (PASS-thuần +
  ABSTAIN-delivered)/16. Runner hash 050f8979… đồng nhất W10→W16.
- **Verdict tổng: GATE PASS_WITHIN_SCOPE (EXIT=0, verdict PASS)** — answer-rate
  **15/15/13**, **2/3 run ≥14/16**; 0 KILL; 5/5 withheld có reason; 0
  probe-FAIL Option A; stable-set 15. RUN C 13/16 dưới ngưỡng — root-cause
  provider crosscheck noise (MULTI-LLM FAIL 2 bên trên free-tier nemotron chỉ
  ở window 18:47–18:53; cùng model pair cho PASS 14/14 ở 16:3x), báo as-is.
  Weather focus giữ vững: **q07 PASS-thuần 3/3 Open-Meteo thật + provenance**
  (28.2/28.0/25.2°C — live fetch giữa boot). q08 stale-fact guard: withheld
  ổn định 3/3, web_fallback_used=True 3/3. q06 f4: 2/3 không withheld-trần
  (A/B PASS; C flip do cùng root-cause provider — KHÔNG phải hồi quy f4/g1).
  Scope: 16 probe × 3 boot, 1 host — không claim production-ready.

## Kết quả chính (answer-rate theo Option A)

| Run | File | Boot PID | PASS-thuần | ABSTAIN-delivered | Answer-rate | non-answer probes (withheld-ESCALATE) |
|---|---|---|---|---|---|---|
| A | `battery/run_a.json` | 41828 | 13 | 2 (q05, q16) | **15/16** | q08 |
| B | `battery/run_b.json` | 42324 | 14 | 1 (q16) | **15/16** | q08 |
| C | `battery/run_c.json` | 6472 | 12 | 1 (q16) | **13/16** | q03, q06, q08 |

48/48 probe HTTP 200 (client-retry: 1 — q11×A attempt 1 HTTP 0 → retry).
Battery exit code A/B/C = 0/0/0. **Probe-FAIL theo định nghĩa Option A: 0** —
cả 5 withheld-ESCALATE (q08×A/B/C + q03×C + q06×C) đều có lý do verification
rõ (mechanical check từ llm_reasoning + server-log window). Không KILL-class.

## STATISTICAL GATE (W9) — `battery/gate_check_output.txt`

```
STAT[run1]: answer-rate 15/16
STAT[run2]: answer-rate 15/16
STAT[run3]: answer-rate 13/16
STAT[stable-set]: 15 probe(s) answered in >=2 runs: ['q01'..'q07','q09'..'q16']
{"mode": "statistical", "verdict": "PASS", "answer_rates": {"run1": 15, "run2": 15, "run3": 13},
 "stable_set": [...], "violations": []}
EXIT=0
```

| Check gate | Kết quả |
|---|---|
| (a) 2/3 run answer-rate ≥ 14/16 | **PASS** — 2/3 run (15 + 15; C=13 dưới ngưỡng) |
| (b) 0 KILL-class trên benign | **PASS** — 0 trong 48 response + 3 server log |
| (c) stable-set + 0 hard-FAIL trần 3 run | **PASS** — stable-set 15 probe (chỉ q08 ngoài) |
| (d) withheld-ESCALATE có reason | **PASS** — 5/5 có reason (không violation) |

## WEATHER FOCUS q07 (W13 wire + f5 carve-out un-starve; chi tiết `battery/w16_verification.txt` §2)

- 3/3 run: PASS/SUCCESS/UPHOLD, route='lookup_data_api', api_name='Open-Meteo',
  latency ~1.0s (1.03/1.09/1.00), **0 LLM POST trong window** (fork no-LLM).
- Answer compose từ payload Open-Meteo: A = 28.2°C/52%/2.3 km/h; B = 28.0°C/
  55%/1.0 km/h; C = 25.2°C/65%/2.2 km/h (live fetch giữa boot — giá trị khác
  nhau loại trừ fixture/cache; khác hẳn W14 20.x°C cùng ngày 03:0x);
  provenance "(Nguồn dữ liệu: Open-Meteo — <api_url>)" khớp field
  v98_classification.api_url từng run.
- Server-log: marker "[S24] /ask forked to data-API (no LLM generation):
  via=l0-keyword domain=weather api=Open-Meteo" trong window q07 cả 3 boot;
  grep 'geocoding' = 0 hit — geocode fallback W14a KHÔNG trigger (Hà Nội trong
  16-city table). Trajectory: W10/W11 withheld 3/3 → W12 ABSTAIN 3/3 → W13
  PASS 3/3 → W14 PASS 3/3 → **W16 PASS 3/3 (giữ vững, không hồi quy)**.

## W16 FOCUS — q06 f4 (a85a77e1) + q08 stale-fact guard (chi tiết `battery/w16_verification.txt` §3–§4)

- **q06** ('Chào bạn, dạo này bạn thế nào?'): A/B = PASS-thuần ('Chào bạn! Mình
  rất vui…' / 'Tôi ổn, cảm ơn bạn!'), C = WITHHELD_ESCALATE (flip). Root-cause
  C: MULTI-LLM crosscheck primary=FAIL secondary=FAIL consensus=agree
  final=FAIL → [W3-e1] Chatbot lane withholding — fail-closed đúng thiết kế,
  KHÔNG phải hồi quy f4/g1 (f4 chỉ mở deliver khi honest-abstain; C không tạo
  honest-abstain). Kết luận: **2/3 run không withheld-trần** (W10: 0/3).
  Lưu ý trung thực: f4 path (deliver-with-label cho honest-abstain lane
  FACTUAL) KHÔNG được battery trigger trực tiếp ở W16.
- **q08** ('Ai là tổng thống Mỹ hiện tại?'): WITHHELD_ESCALATE ổn định 3/3 —
  llm_reasoning 'Suy đoán mô hình chưa đủ chứng cứ đối chiếu (độ tin cậy:
  10%)'; web_fallback_used=True 3/3 (đường W14d gen-with-evidence giữ nguyên,
  GET web-search trong window trước generation). Battery QUAN SÁT, KHÔNG
  adjudicate nội dung. q08 PASS 1/3 của W14 không tái hiện (0/3) — variance
  judge, không claim hồi quy/fix.

## So với W14-battery (@ 3880f838, 15/15/16) và W10 (@ af57ba3b, 12/13/14) — as-is

- Answer-rate: W16 = 15/15/13 (tổng 43, mean 14.33) vs W14 46/15.33, W13 43,
  W12 40, W11/W10 39. Gate (a): W16 **2/3 run ≥14** (W14/W13 3/3; W12 2/3;
  W10/W11 1/3 FAIL).
- Đếm trung thực delta W16 vs W14 (−3, toàn bộ RUN C): q03 + q06 flip PASS →
  withheld (provider crosscheck noise — cùng model pair PASS 14/14 ở 16:3x) và
  q08-C (W14-C PASS lần đầu không tái hiện). Không có điểm nào attributable
  âm hay dương trực tiếp tới f4/f5/g1 từ battery; f4/f5 giữ nguyên behavior
  chuẩn (q07 3/3, q06 2/3 non-withheld).
- KILL-semantics giữ vững: 0/0/0 (ops-w15 KILL label chỉ lane security — 16
  probe benign không trigger, đúng thiết kế).
- Census: 98/85/89 vs W14 88/90/90, W13 89/88/89, W10 93/93/105; q07 census
  0/0/0 giữ nguyên; q08 5/2/6 (variance hedge, không double egress).

## KC-16 LLM-call census

- `battery/llm_call_compare.csv` (16 × A/B/C) — nguồn đếm + quy tắc gán:
  `battery/call_source_documentation.md` (script đếm: `w16_check_option_a.py`
  hàm `census()`).
- Tổng POST LLM (httpx, chỉ host openrouter.ai + api.groq.com): A=98, B=85,
  C=89; background=0, residual=0, nonLLM-POST=0.
- **Hard rule PASS-with-0-LLM-calls: PASS** — 6 case count=0 (q07×A/B/C,
  q02×B, q02×C, q14×C) đều là data-API fork chuẩn `v98_classification.route=
  'lookup_data_api'`.

## INCIDENT (session W16-C — disclosure)

- Battery song song ngoài ý muốn (lệnh `--help` gọi nhầm lên runner không có
  argparse) tại boot đầu 18:34–18:53 → reconcile: kill cả 2 battery + server
  boot đầu (toàn bộ process do session này boot, xác minh CommandLine trước
  khi kill), reboot sạch (PID 6472), RUN C chạy lại đúng 1 lần.
- Log boot nhiễm bẩn: `battery/incident_server_log_duplicate_run.log` (525
  dòng) — lưu làm evidence minh bạch, KHÔNG dùng cho census/gate. Không
  artifact của 2 battery bỏ dở lọt vào số liệu nào trong bundle.

## Bằng chứng chính (đã hash trong `bundle_hashes.txt`)

| File | Nội dung |
|---|---|
| `battery/run_a.json` | RUN A — 16 probe full response |
| `battery/run_b.json` | RUN B — 16 probe full response |
| `battery/run_c.json` | RUN C — 16 probe full response |
| `battery/health_run{A,B,C}.json` + `battery/ready_run{A,B,C}.json` | Identity mỗi boot (/health commit khớp + /ready 200; PID khớp netstat listener) |
| `battery/gate_check_output.txt` | Statistical gate (W9) — verdict PASS, EXIT=0 |
| `battery/option_a_counts.txt` | Đếm Option A per-run + q07/q06/q08 focus + withheld adjudication + KILL + census + verdict tổng |
| `battery/checker_console.txt` | stdout checker (capture chạy lại được) |
| `battery/w16_verification.txt` | Phân tích W16: Option-A counting, gate, q07/q06/q08 focus, KILL-count, 0-calls, so W14/W10, incident, open questions |
| `w16_check_option_a.py` | Checker + census (evidence dir, auditable, read-only) |
| `battery/llm_call_compare.csv` | Census 16 × 3 |
| `battery/call_source_documentation.md` | Định nghĩa nguồn đếm + limitation + incident |
| `battery/server_log_run{A,B,C}.log` | 3 boot log (grep KILL = 0/0/0) |
| `battery/battery_run{A,B,C}_console.log` | Console output runner từng run |
| `battery/incident_server_log_duplicate_run.log` | Log boot nhiễm bẩn (incident, ngoài census) |
| `bundle_hashes.txt` | SHA256 runner/wrapper/gate + mọi evidence file |
| `final_provenance.txt` | Lineage, identity, counting semantic, incident, limitations |

## Lineage

`1aef24a8` (baseline KC-23) → `a79b6148` (W1) → `bc055371` (W2) → `2b37e7ee` (W3)
→ `e6e0c295` (W4) → `e2e735a8` (W5 squash) → `bf8a8fba` (GA.md B1b docs —
battery W6-FINAL/W6-RE) → `323c234f` (W4b sweep, PR #57) → `feba681e` (GA.md
B1b docs handoff W6-RE) → `4cec8903` (W7, PR #58) → `38bac514` (W8, PR #59)
→ `dfda8475` (W9, PR #60) → `af57ba3b` (GA.md B1b docs — W10 battery SHA) →
`b13fba9a` (W11, PR #61) → `e62eeed3` (W12, PR #63) → `bc3b1bb6` (GA.md B1b
docs — W12 handoff) → `ede074ec` (W13, PR #64) → `44e5bf02` (GA.md B1b docs —
W13 handoff, campaign đóng) → `2fefcbc5` (docs(audit) full-system audit) →
`8d1d6b04` (GA.md B1b docs — BC-2 verify) → `c3835989` (W14a/b/c geocode
fallback) → `653814a4` (W14d/e/f gen-with-evidence) → `8f9fe6ba` (W14 egress
seam + T00 diagnostics) → `3880f838` (ops-w15 untrack = W14 battery SHA) →
`5a0ed49d` (W15: sharp CVE bump + security-lane KILL label + kill-sites audit,
PR #66) → `a85a77e1` (W16 f4) → `8d24e9c7` (W16 f5) →
**`d64e8a17` (W16 g1 = MAIN TIP)** → **battery tại d64e8a17** (worktree
detach, /health commit khớp từng boot). Full SHA 28/28 đã re-verify bằng
git rev-parse trong session này (xem `final_provenance.txt`).

## Verdict & scope

- **GATE PASS_WITHIN_SCOPE theo statistical gate W9:** 2/3 run ≥14/16
  (15/15/13); 0 probe-FAIL Option A; 0 KILL; 5/5 withheld có reason; census
  sạch; hard rule PASS-with-0-calls PASS; q07 PASS-thuần 3/3 dữ liệu thật;
  q08 withheld ổn định 3/3 (guard giữ nguyên).
- RUN C 13/16 dưới ngưỡng — root-cause provider crosscheck noise (free-tier
  nemotron FAIL 2 bên chỉ ở window 18:47–18:53), báo as-is, KHÔNG sửa
  product/harness; không loại trừ 100% tương tác code×thời điểm (cùng SHA,
  khả năng thấp — đã ghi limitation).
- Phạm vi: PASS chỉ nghĩa "không thấy vi phạm trong 16 probe × 3 boot, 1 host,
  window đã ghi 2026-10-07". Không claim production-ready.
- f4/f5 path không được battery trigger trực tiếp (chỉ hermetic test + W14-B);
  geocode fallback W14a không được battery phủ (q07 chỉ hỏi Hà Nội). Không
  push; GA.md handoff W16 là bước owner tiếp theo.
