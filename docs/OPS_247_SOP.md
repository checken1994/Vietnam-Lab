# SOP — Vận hành SCP 24/7 kèm Theo dõi — Cảnh báo — Đánh giá

> Phiên bản: 1.0 · Ngày: 2026-09-28 · Workspace: `D:\scp`
> Đối tượng: người tiếp quản chưa tham gia xây dựng. Làm theo thứ tự là chạy được.

> **Hiện trạng deployment (cập nhật 2026-10-01 — audit A7 MED-06):**
> - `compose.yml` hiện hành mặc định **chỉ khởi động `scp-api`** (port 8000,
>   expose loopback-only `127.0.0.1:8000`). Scheduler/loop là profile opt-in
>   (`docker compose --profile loop up`), KHÔNG phải default 4 dịch vụ.
> - Topology 4 dịch vụ dưới đây là chế độ **native/Windows** khi vận hành đủ
>   stack theo mục 2 — vẫn đúng cho chế độ đó, nhưng không phải default của compose.
> - Monitor hiện trạng: `scripts/ops/scp_hourly_monitor.py` từ commit `7de459d1`
>   là **notify-only mặc định** — một finding KHÔNG còn tự động dừng stack;
>   dừng stack là opt-in (`--stop` hoặc env `SCP_MONITOR_STOP_ON_ERROR=1`), và
>   `_kill_port_listeners` không bao giờ kill process thuộc Docker backend (fail-closed).

---

## 1. Kiến trúc quy trình (sơ đồ logic)

```
┌────────────────────────── 24/7 RUNTIME ──────────────────────────┐
│  4 dịch vụ SCP:                                                  │
│   8000 API (python -m scp)   8081 LLM Bridge (bun index.ts)      │
│   3030 Scheduler (bun)       3000 Dashboard (bun run dev)        │
└──────────────┬───────────────────────────────────────────────────┘
               │ probe mỗi 60s
┌──────────────▼───────────────────────────────────────────────────┐
│  MONITOR  scripts/ops/scp_ops_monitor.py                         │
│   • thu thập: up/health/latency/pid/uptime/ERROR mới             │
│   • ghi: ops_ledger.jsonl + ops_metrics.sqlite (append-only)     │
│   • đánh giá ngưỡng cảnh báo → data/ops/alerts/pending.jsonl     │
└──────┬──────────────────────────────┬────────────────────────────┘
       │                              │
┌──────▼───────────┐        ┌─────────▼──────────────────────┐
│ DASHBOARD        │        │ ALERT CHANNEL                  │
│ data/ops/        │        │ (a) AutoClaw patrol cron 30'   │
│ dashboard.html   │        │ (b) pending.jsonl đọc thủ công │
│ (tự refresh 30s) │        │ (c) email/Telegram: tương lai  │
└──────┬───────────┘        └─────────┬──────────────────────┘
       │ tổng hợp ngày/tuần           │
┌──────▼──────────────────────────────▼───────────────────────────┐
│ EVALUATION REPORTS  data/ops/reports/ops-report-{daily|weekly}-* │
│ → verdict OK / ATTENTION_NEEDED + uptime từng dịch vụ            │
└──────────────────────────────────────────────────────────────────┘
```

**Vai trò:** (1) *Runtime* — 4 dịch vụ SCP; (2) *Monitor* — quan sát, ghi ledger,
không tự sửa; (3) *Alert* — đánh giá ngưỡng, ghi cảnh báo; (4) *Operator (bạn)* —
nhận cảnh báo, quyết định khởi động lại; (5) *Evaluator* — sinh báo cáo định kỳ.

**Điểm kiểm tra / theo dõi / đánh giá trong quy trình:**
- Điểm theo dõi: mỗi 60s (monitor cycle) — 4 dịch vụ.
- Điểm kiểm tra: ngưỡng cảnh báo (mục 4) đánh giá trên dữ liệu mỗi chu kỳ.
- Điểm đánh giá: báo cáo daily (mỗi 24h) + weekly (mỗi 7 ngày).

## 2. Khởi động (theo thứ tự)

> **Interpreter Python (path-agnostic):** KHÔNG hardcode path Python cá nhân.
> Dùng Windows launcher `py -3.12` (có sẵn với bản cài python.org). Nếu máy
> không có `py`, xác định interpreter bằng `where.exe python` rồi thay đường
> dẫn trả về vào lệnh `Start-Process` tương ứng.

```powershell
# 2.1 API backend (trước tiên)
Start-Process py -ArgumentList '-3.12','-m','scp','8000' `
  -WorkingDirectory 'D:\scp' -WindowStyle Hidden
# chờ http://127.0.0.1:8000/health trả 200

# 2.2 LLM Bridge — LƯU Ý: phải set port, mặc định của bridge là 11434!
$env:SCP_LLM_BRIDGE_PORT = '8081'
Start-Process bun -ArgumentList 'index.ts' `
  -WorkingDirectory 'D:\scp\mini-services\llm-bridge' -WindowStyle Hidden

# 2.3 Scheduler — cần 3 biến môi trường, thiếu là refuse-to-start (fail-closed)
$env:SCP_LLM_BRIDGE_PORT = '8081'
$env:SCP_BASE_URL        = 'http://127.0.0.1:8000'
$env:LLM_BRIDGE_URL      = 'http://127.0.0.1:8081'
Start-Process bun -ArgumentList 'run','start' `
  -WorkingDirectory 'D:\scp\mini-services\loop-scheduler' -WindowStyle Hidden

# 2.4 Dashboard
Start-Process bun -ArgumentList 'run','dev' `
  -WorkingDirectory 'D:\scp\dashboard' -WindowStyle Hidden

# 2.5 Monitor 24/7 (chạy nền liên tục, ghi ledger mỗi 60s)
Set-Location D:\scp
Start-Process py -ArgumentList '-3.12','scripts\ops\scp_ops_monitor.py','--interval','60' `
  -WindowStyle Hidden
```

Kiểm tra khởi động xong: mở `data\ops\dashboard.html` — cả 4 service phải UP.

## 3. Dừng hệ thống

```powershell
# Dừng monitor trước (để không ghi alert nhầm khi shutdown có chủ đích)
Get-Process python | Where-Object { $_.MainWindowTitle -eq '' } | Out-Null
# (xác định pid monitor: Get-CimInstance Win32_Process | Where CommandLine -match 'scp_ops_monitor')

# Dừng từng dịch vụ theo pid thực tế:
Get-NetTCPConnection -LocalPort 8000,8081,3030,3000 -State Listen -ErrorAction SilentlyContinue |
  Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object { Stop-Process -Id $_ -Force }
```

Đóng gói an toàn dùng sẵn: `powershell scripts\ops\scp_247_control.ps1 -Action status|stop|kill`
(kill-switch file `.private-secrets\release-audit\scp-247\KILL`; xóa file đó bằng
`-Action clear-kill` để chạy lại).

## 4. Quy tắc cảnh báo theo ngưỡng + kịch bản xử lý

| Quy tắc | Ngưỡng | Mức | Xử lý第一步 | Leo thang |
|---|---|---|---|---|
| `service_down` | port không LISTEN hoặc health != 200 | critical | Kiểm tra log `data\service-logs\<svc>-error.log`, khởi động lại theo mục 2 | 2 lần DOWN liên tiếp → dừng monitor, chạy scp_247_control kill, liên hệ owner |
| `health_latency_ms` | probe > 2000ms | warning | Quan sát 2 chu kỳ; nếu kéo dài → restart dịch vụ đó | Kéo dài > 10 phút → xem tài nguyên máy |
| `error_burst` | ≥ 10 dòng ERROR mới trong 1 chu kỳ | warning | Đọc log, đối chiếu commit mới nhất | Lặp lại ≥ 3 chu kỳ → rollback commit gần nhất |
| `heartbeat_gap` | ledger ngắt quãng > 300s | critical | Monitor chết → khởi động lại monitor (mục 2.5) | Nếu tiếp tục chết → kiểm tra ổ cứng/quyền ghi |

Kênh cảnh báo **hiện tại**: (a) AutoClaw patrol cron mỗi 30 phút đọc pending.jsonl
và báo trong panel「Định kỳ」; (b) mở trực tiếp `data\ops\alerts\pending.jsonl`.
**Chưa bật cảnh báo push (email/Telegram)** — sẽ bật sau khi bạn xác nhận kênh.

## 5. Nơi lưu dữ liệu (nguồn sự thật)

| Dữ liệu | Đường dẫn | Định dạng |
|---|---|---|
| Ledger mọi sự kiện | `data\ops\ops_ledger.jsonl` | JSONL append-only, ts UTC + session |
| Chỉ số có cấu trúc | `data\ops\ops_metrics.sqlite` | SQLite: bảng metrics, alerts |
| Cảnh báo pending | `data\ops\alerts\pending.jsonl` | JSONL |
| Báo cáo đánh giá | `data\ops\reports\ops-report-*.{json,html}` | JSON + HTML |
| Dashboard | `data\ops\dashboard.html` | HTML tự refresh 30s |
| Log dịch vụ | `data\service-logs\*.log` | text |

Không có dữ liệu cá nhân/secret trong ledger (chỉ pid, port, ms, số dòng ERROR).

## 6. Xử lý sự cố thường gặp

| Triệu chứng | Nguyên nhân đã gặp thật | Cách xử lý |
|---|---|---|
| Bridge không lên, EADDRINUSE | Process cũ còn giữ port 11434/8081 | Tìm pid: `Get-NetTCPConnection -LocalPort 8081`, kill, start lại với `$env:SCP_LLM_BRIDGE_PORT='8081'` |
| Scheduler refuse-to-start | Thiếu `SCP_BASE_URL` / `LLM_BRIDGE_URL` (fail-closed chủ đích) | Set đủ 3 env ở mục 2.3 rồi start |
| WS chat chết `UnboundLocalError: asyncio` | Đã fix (e7803e58) — nếu tái phát: kiểm tra không ai thêm `import asyncio` cục bộ trong hàm scp_chat | — |
| API lên nhưng WS/chat lỗi | Bridge chưa chạy hoặc sai port | Kiểm tra 8081 trước |
| Monitor báo DOWN nhưng service sống | Monitor chạy thiếu quyền network probe | Chạy monitor cùng user với dịch vụ |

## 7. Đánh giá định kỳ

```powershell
# Báo cáo hằng ngày (chạy lúc sáng, hoặc theo lịch của bạn)
python scripts\ops\scp_ops_monitor.py --report daily
python scripts\ops\scp_ops_monitor.py --report weekly
# Kiểm tra tính liên tục heartbeat (gap > 300s = FAIL)
python scripts\ops\scp_ops_monitor.py --verify
# Render lại dashboard từ dữ liệu mới nhất
python scripts\ops\scp_render_dashboard.py
```

Báo cáo gồm: heartbeats, alerts, service_down_events, uptime từng dịch vụ,
verdict. Mỗi con số truy ngược được về ledger theo timestamp + session id.

## 8. Checklist bàn giao (người mới tự làm được)

- [ ] Đọc mục 1–2, khởi động đủ 4 dịch vụ + monitor
- [ ] Mở `data\ops\dashboard.html` thấy 4/4 UP
- [ ] Chạy `--verify` → `violations=0`
- [ ] Chạy `--report daily` → file json+html sinh trong `data\ops\reports\`
- [ ] Test cảnh báo: tắt 1 dịch vụ → trong 1 chu kỳ monitor xuất hiện alert
      `service_down` trong pending.jsonl → khởi động lại dịch vụ
- [ ] Biết đường dẫn ledger + cách dừng toàn hệ thống (mục 3)

## 9. Ghi chú kiểm chứng đã thực hiện (2026-09-28)

- Fault-injection test: kill scheduler thật → monitor chu kỳ kế phát hiện và
  ghi alert `service_down` critical vào ledger + pending.jsonl → khởi động lại
  → chu kỳ kế xác nhận UP trở lại (uptime 4/4).
- Cross-check dashboard↔ledger↔DB tại 4 thời điểm: ALL_MATCH (4/4 dịch vụ).
- Daily report mẫu sinh từ dữ liệu thật: `ops-report-daily-20260928-0556.json/.html`
  (ghi đúng sự cố scheduler: uptime 50% ở cửa sổ test, verdict ATTENTION_NEEDED).
- Giới hạn đã khai báo: cửa sổ 24h liên tục chưa trọn — heartbeat đang chạy,
  verify sẽ bằng chứng khi đủ 24h (chạy `--verify` bất kỳ lúc nào).
