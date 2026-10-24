# SCP Port Registry (HC-26)

Registry chuẩn của các port SCP. Mọi service phải khai báo port ở đúng nguồn
dưới đây; không hardcode port mới ngoài registry này.

Bộ 3 nguồn bắt buộc khớp nhau (drift guard:
`tests/T00_integrity/test_ports_registry.py` — anti-placebo: sửa 1 nguồn,
thiếu 1 nguồn ⇒ test văng):

1. Bảng này (`docs/architecture/PORTS.md`)
2. `scp/core/ports.py` (constants Python)
3. `docker-compose.full.yml` (port map của các service compose publishes)

## Bảng port

| Port | Service | Nguồn khai báo | Ghi chú |
|------|---------|----------------|---------|
| 8000 | backend (FastAPI API server) | `docker-compose.full.yml` — service `backend`, `127.0.0.1:8000:8000` + env `SCP_PORT=8000` | `scp/core/ports.py: BACKEND` |
| 3000 | dashboard (Next.js web UI) | `docker-compose.full.yml` — service `dashboard`, `127.0.0.1:3000:3000` + env `PORT=3000` | `scp/core/ports.py: DASHBOARD` |
| 3030 | loop-scheduler (Node.js autonomous loop) | `docker-compose.full.yml` — service `loop-scheduler`, `127.0.0.1:3030:3030` + env `PORT=3030`, `LOOP_SCHEDULER_PORT=3030` | `scp/core/ports.py: LOOP_SCHEDULER` |
| 6080 | agent-desktop noVNC (virtual desktop HTTP) | `docker-compose.full.yml` — service `agent-desktop`, `127.0.0.1:6080:6080` + env `NOVNC_PORT=6080` | `scp/core/ports.py: AGENT_DESKTOP` |
| 9222 | agent-desktop Chromium CDP | `docker-compose.full.yml` — service `agent-desktop`, env `CHROME_DEBUG_PORT=9222` (loopback-internal, không publish) | `scp/core/ports.py: CDP` |
| 8090 | brain-console (read-only brain projection) | `docker-compose.full.yml` — service `brain-console`, `127.0.0.1:8090:8090` + env `SCP_BRAIN_CONSOLE_PORT=8090` | `scp/core/ports.py: BRAIN_CONSOLE` |
| 4317 | OTLP gRPC collector (observability) | `docker-compose.observability.yml` — `127.0.0.1:4317:4317` + `infra/otel-collector.yaml` grpc endpoint `0.0.0.0:4317` | Không constant-ize trong `ports.py` (thuộc profile observability) |
| 4318 | OTLP HTTP collector (observability) | `docker-compose.observability.yml` — `127.0.0.1:4318:4318` + `infra/otel-collector.yaml` http endpoint `0.0.0.0:4318` | Không constant-ize trong `ports.py` (thuộc profile observability) |
| 8055 | audit backend hermetic (audit runner) | `scripts/run_full_audit.py` — `AUDIT_PORT = 8055` | Port nhất thời cho phiên audit hermetic, không constant-ize |

## Quy tắc

- Thêm/đổi port service: sửa **cùng lúc** bảng này + `scp/core/ports.py` +
  nguồn khai báo gốc (compose/env/code), rồi chạy
  `pytest tests/T00_integrity/test_ports_registry.py`. Test đỏ ⇒ chưa xong.
- Port loopback publish (`127.0.0.1:X:X`) phải giữ host-port == container-port;
  lệch port giữa 2 vế cũng tính drift.
- Port mới chỉ thêm qua PR có cập nhật cả 3 nguồn; không "gắn tạm" port
  ngoài registry (DNA #22: PASS ≠ TRUE — service chạy được nhưng lệch registry
  vẫn là lỗi kiến trúc).
- **[bridge removed 2026-10-24]** Port 8081 (`LLM_BRIDGE`) và 11434 (legacy
  advertised) đã bị gỡ khỏi registry khi xóa sổ `mini-services/llm-bridge/`
  (Zero-Bun deprecation). Không tái sử dụng 2 port này cho service mới.
