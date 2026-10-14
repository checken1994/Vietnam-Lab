# Wave 13 CHECKER VERDICT — WeatherDataSource (Open-Meteo) wire vào lookup fork

- Checker (độc lập với implementer): agent_a2183d31-e05f-4eaf-90f5-dc045cc80ec2
- Dispatch record: `reports/scp_acceptance_ci/wave13/dispatch_record.txt`
  (implementer agent_9607874a-…; separation: checker không tham gia implement,
  dispatch SAU khi implementer báo xong; checker verify code-path, không gọi
  mạng thật theo kế hoạch — xem Ghi nhận quy trình cuối).
- Scope: base `bc3b1bb6` → head `2fefe3ea` (fix/wave13), 3 commit:
  151dbb58 (w13a wire), abc64ae5 (w13b egress), 2fefe3ea (w13c 29 test).
- **HEAD DI CHUYỂN GIỮA PHIÊN CHECK:** khi checker đang chạy, fix/wave13 nhận
  commit thứ 4 `eb62b224` (zcode-agent, 20:13:31 +0700) — "weather tier
  event-loop + ordering + seam fix (CI ×4 platforms)". Verdict dưới chạy trên
  `2fefe3ea`, sau đó delta của `eb62b224` được re-verify độc lập; tip cuối
  khi kết thúc = `eb62b224`. Chi tiết ở mục "Re-verify commit 4".
- Ngày kiểm: 2026-10-06. Runner: Python 3.12.10, pytest 9.1.1, ruff 0.16.1.

## Kết quả 7 mục check

| # | Check | Verdict | Evidence |
|---|---|---|---|
| 1 | kc-A per-commit replay ×3 (worktree riêng) + re-run tại tip | **PASS** | `kc_A_per_commit_replay.txt` — w13a: 104 passed/exit 0; w13b: 9 passed/exit 0 (replay test w13c theo ghi chú trailer, code egress thuộc w13b); w13c: 29 passed/exit 0. Tại tip mới eb62b224: 29/29 + SSRF-S1 81 + vùng kề tools.py 29, exit 0. |
| 2 | kc-B anti-placebo @ bc3b1bb6 | **PASS** | `kc_B_anti_placebo_base.txt` — test mới của head trên code cũ: EXIT=2, 3 collection-ERROR `ImportError: cannot import name 'OPEN_METEO_EGRESS_HOST'` (traceback trỏ weather.py của worktree base). Behavioral old-fail đo trực tiếp: `resolve_lookup_data(q07, domain=weather)` → None tại base. Không thể pass-trống. |
| 3 | kc-C T00 wave-delta + ruff E9,F821 | **PASS** | `kc_C_t00_ruff.txt` — T00 EXIT 0 "0 new regressions"; delta non-vacuous (29 NodeID W13 trong candidate; 3 file test KHÔNG tồn tại tại base — `git cat-file -e` fatal); ruff E9,F821 trên 5 file .py: All checks passed, exit 0. |
| 4 | kc-D census | **PASS** | `kc_D_census.txt` — skip/xfail thêm mới = 0 (1 match là docstring "Không skip/xfail"); noqa mới = 0; secrets = 0; test xoá = 0; scp/tests untouched. 5 `except` thêm mới được enumerate từng cái: 3 narrow-typed (WMO/lat-lon/echo → ""/None fail-closed), 2 broad (host-gate → False fail-CLOSED; lookup → None fallback) — không có fail-open, không swallow-continue. |
| 5 | kc-E SSRF sweep + egress scope | **PASS** | `kc_E_ssrf_egress_sweep.txt` — chi tiết ở mục 7 dưới. |
| 6 | kc-F deliver-accuracy + W8-e1 | **PASS** (1 flag minor) | `kc_F_deliver_accuracy_w8e1.txt` — compose 100% từ payload (fixture-pin từng giá trị); WMO map chuẩn WMO 4677, code lạ → "" không bịa; fail-closed API-down/unparseable/mismatch → None → LLM fallback → withhold đúng thiết kế. FLAG: thiếu pin end-to-end "politics + time-signal không bị weather evidence tắt guard" (cấu trúc đã chặn 3 lớp). |
| 7 | SSRF/EGRESS tổng hợp | **PASS** | Xem dưới. |

## Mục 7 — SSRF / EGRESS (tổng hợp kc-E)

**(a) URL construction:** lat/lon chỉ đến từ bảng `_cities` HARDCODE; question
chỉ match key (diacritic-fold + word-boundary). PoC injection — question chứa
`&param=evil=1`, path traversal `/../../admin`, CRLF `%0d%0aX-Injected: 1`,
`&callback=evil` — KHÔNG cái nào vào URL; URL luôn
`https://api.open-meteo.com/v1/forecast?latitude=<số bảng>&longitude=<số bảng>&...`
với query value được urlencode; path là hằng. `build_open_meteo_url` chặn
nan/inf/string/None bằng ValueError trước fetch. Host không bao giờ ghép từ
question (dry-check URL và fallback URL đều là hằng số chuỗi).

**(b) Egress scope:** `_weather_host_allowed` = exact hostname match
`api.open-meteo.com` rồi mới `enforce_egress_policy` với extra grant
`frozenset({"api.open-meteo.com"})` — entry không có `*.` nên chỉ exact-match
(không wildcard). PoC 9/9: suffix spoof, `geocoding-api.open-meteo.com`
(subdomain khác), userinfo trick `api.open-meteo.com@evil.example.com`,
`evil.com?…api.open-meteo.com`, `[::1]`, metadata 169.254.169.254, string rác
— đều False. DENY mode thắng CẢ ở dry-check (pin test) LẪN ở điểm fetch thật
(live probe: `safe_urlopen(..., extra_allowed_hosts=...)` dưới
SCP_EGRESS_MODE=deny → EgressDeniedError trước connect — Invariant 3 trước 4,
đọc code egress.py dòng 330-352). Metadata/loopback invariants nguyên
(Invariant 1/2 đứng trước mọi mode). SSRF-S1 pin tại head: 81 passed exit 0.

**(c) Scope nới (residual, có chủ đích):** `.env.example` default
`SCP_EGRESS_ALLOWLIST=api.open-meteo.com` mở host này cho GENERIC path ngoài
weather tier. Là widening 1 host exact, được owner duyệt và ghi rõ trong
commit w13b ("owner approval W13"). Không phải violation; liệt kê để human
review giữ vệt approval.

**(d) SSRF/injection-candidate còn sống: KHÔNG tìm thấy.** Không có đường từ
question text vào URL (host/path/query đều hằng hoặc số từ bảng); không có
wildcard; không có fail-open trong exception paths; DENY thắng; metadata/
loopback chặn độc lập với allowlist.

## Re-verify commit 4 (eb62b224 — head di chuyển giữa phiên)

Diff `2fefe3ea..eb62b224` (3 file, +47/-1):
- 2 file test W13: CHỈ thêm `monkeypatch.setenv("SCP_EGRESS_MODE","allowlist")`
  + docstring/comment. KHÔNG assertion bị sửa, KHÔNG skip/xfail, file pin
  egress T05 (test_w13_weather_egress.py) KHÔNG bị đụng → kỷ luật test giữ
  nguyên (census lại: 0 skip/noqa, 1 except mới).
- `scp/capabilities/tools.py`: reap process bounded 5s sau kill trong
  SafeCommandRunnerTool — fail-safe logging-only, semantics timeout không đổi,
  không liên quan weather path.
- Root-cause claim verify độc lập: `.github/workflows/ci.yml:20` (và 4
  workflow gate khác) thực đặt `SCP_EGRESS_MODE: deny` — CI-deny chặn weather
  tier trước fetch qua dry-check (đúng hợp đồng Invariant 3 w13b đã pin);
  local pass vì .env ambient allowlist. Seam fix là đúng điểm lỗi, không nới
  gate: deny-contract vẫn pin riêng ở T05 và deny probe chạy lại OK (dưới).

Re-run tại tip `eb62b224`:
| Hạng mục | Kết quả |
|---|---|
| 29 test W13 (3 file) | 29 passed, exit 0 |
| SSRF-S1 pin (test_ssrf_sweep_s1) | 81 passed, exit 0 |
| Vùng kề tools.py (test_autonomous_tool_contracts + test_sec_r1_remediation) | 29 passed, exit 0 |
| Deny probe fetch-site + dry-check (deny, geocoding) | FAIL-CLOSED đúng |
| ruff E9,F821 (6 file Python incl. tools.py) | All checks passed, exit 0 |
| T00 meta-audit (delta main → tip mới) | exit 0, "0 new regressions" |
| Census commit 4 | 0 skip/xfail/noqa; assertion 0 sửa; 1 except mới fail-safe |

Ghi chú runner: 1 lượt chạy 29 test cho 9 ERROR là lỗi thao tác checker
(--basetemp trỏ vào parent chưa mkdir — pytest tmp factory không tạo nested
parent trên Windows); đã mkdir và chạy lại → 29 passed. 1 warning duy nhất
trong các lần chạy là PytestCacheWarning không tạo được D:\scp\.pytest_cache
(Windows permission — môi trường máy, không phải code/test).

## Ghi nhận quy trình (minh bạch)

Probe PoC SSRF phần (a) chạy không mock `safe_urlopen` → phát sinh 6 GET
read-only THẬT tới api.open-meteo.com (host được owner duyệt; không host nào
khác; không write). Đây là lệch nhỏ khỏi kế hoạch "không call mạng thật" của
checker; vô tình cho ra runtime evidence cấp B (integration): full fetch path
qua safe_urlopen+EgressPolicy hoạt động với host pinned, answer compose từ
API thật (Hà Nội 23.3°C lúc probe — khớp pattern live probe 23.7°C của
implementer). Không đụngscp/tests, không push/merge.

## Limitations (phạm vi bằng chứng)

- PASS là "không thấy lỗi trong phạm vi đã kiểm": 29 test hermetic + 3 replay
  per-commit + T00/ruff + PoC SSRF tĩnh/hermetic + 6 GET read-only ngẫu nhiên.
  Chưa kiểm: chất lượng routing weather_fact ở biên ngữ nghĩa (W11 đã pin ở
  wave khác), hành vi khi Open-Meteo đổi shape payload mới, latency/timeout
  thật ngoài 8s timeout config, và pin end-to-end W8-e1 nêu ở flag.
- NodeID/harness strictness: 29 NodeID mới hoàn toàn, không delete/skip/
  xfail/loosen (T00 exit 0, census 0, anti-placebo chặn pass-trống).

## Verdict tổng

**GATE PASS_WITHIN_SCOPE** (tính đến tip cuối `eb62b224` trên fix/wave13) —
Wave 13 đạt gate kiểm định checker: 7/7 mục PASS, 0 SSRF/injection-candidate,
0 fail-open, 0 test-nới-chuẩn (kể cả commit 4 giữa phiên), 1 flag minor
(thiếu 1 pin test W8-e1 cross-domain — khuyến nghị wave sau, không chặn) +
1 scope note có owner approval (api.open-meteo.com vào allowlist config
default). Không merge/push — quyết định merge thuộc orchestrator/owner.

Open questions còn lại (không chặn, DNA #23): shape payload Open-Meteo nếu
API đổi schema; pin W8-e1 cross-domain tường minh; giá trị thật của
`t2_min_confidence()` khi route q07 trên CI; hành vi egress khi SCP_EGRESS_
ALLOWLIST chứa wildcard do config tương lai (EgressPolicy hỗ trợ `*.` entry —
ngoài scope W13, cần gate riêng khi owner thêm wildcard).
