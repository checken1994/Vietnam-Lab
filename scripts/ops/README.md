# Operational harnesses

Host-level scripts that require an explicit change window and elevated PowerShell.

## scp_hourly_monitor.py — notify-only mặc định (M-03, 2026-10-01)

- Một finding KHÔNG còn tự động dừng toàn bộ stack. Hành vi cũ
  `stop_on_error=True` (kill services khi 1 endpoint offline tạm thời) đã đổi
  thành notify-only: ghi incident report + return code 1, KHÔNG stop.
- Dừng stack giờ là OPT-IN: env `SCP_MONITOR_STOP_ON_ERROR=1` hoặc flag `--stop`.
  `--no-stop` vẫn giữ để ép không dừng.
- `_kill_port_listeners` KHÔNG BAO GIỜ kill process thuộc Docker backend
  (com.docker.backend, vpnkit, docker-proxy, Docker Desktop) — kill nhầm PID
  đó sẽ tắt Docker Desktop (FA-11, đã xảy ra thật 2 lần trong audit wave 1
  khi deployment 24/7 dạng docker compose giữ port 8000). PID không xác định
  được identity → bỏ qua (fail-closed).
- Bridge probe dùng port 8081 (`/api/tags`, khớp supervisor); port 11434 đã
  retire (đó là default port của Ollama — không được kill nhầm service ngoài).
