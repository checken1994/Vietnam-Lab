# BOUNDARIES (HC-42 / HC-45 / HC-23 / HC-24 / HC-37)

Trạng thái: trả nợ nhóm K4 docs. Commit: `feat/runtime-audit-brain-console` @ `e79bafd5`.
Mỗi mục ≤10 dòng; đây là **doc thiết kế/limitation**, không phải bằng chứng runtime.

## HC-42 — Rate limiter + auth-fail counter trong RAM per-process

- `scp/api/rate_limiting.py`: SlowAPI `Limiter` giữ state trong RAM của process backend.
- `scp/security/auth.py`: `_auth_failures: dict[str, list[float]]` — sliding window
  5 lần sai / IP / 60s, cũng thuần RAM (có lock + eviction theo số IP).
- **Limitation:** chạy >1 worker process (uvicorn workers/gunicorn) thì mỗi process có
  counter riêng → hạn mức thật = hạn mức × số process, auth-fail lockout bị lách bằng
  việc đổi process. Hiện tại SCP chỉ chạy **1 process backend** (compose map 1 cổng).
- **Design note khi cần multi-worker (chưa implement):** thay dict RAM bằng Redis
  adapter (INCR + EXPIRE cho auth-fail; token bucket cho rate limit), giữ nguyên
  interface `_check_rate_limit`/`_record_auth_failure` để không đổi call-site; Redis
  unreachable → fail-closed (chỉ cho phép endpoint public-read) hoặc fallback single-node
  phải được cấu hình tường minh, không lặng lẽ.

## HC-45 — Giao thức ba phía: Lease + Idempotency-key + Capability epoch

- **TaskKernel cấp lease** (`task_kernel_parts/lease_manager.py`): `lease_id`,
  `fencing_token = MAX(fencing_token)+1`, TTL, `global_kill_epoch`; task state → `LEASED`.
  Worker giữ lease bằng heartbeat; hết TTL/heartbeat → lease chết, fencing token cũ vô hiệu.
- **Idempotency-key** (`task_kernel_parts/idempotency.py`): caller đưa `logical_key`;
  state machine `CLAIMED → COMPLETED` (chỉ `RETRYABLE` được CLAIM lại, có version check).
  Kernel kiểm; known gap TK-02: row kẹt `CLAIMED` vĩnh viễn nếu worker chết giữa chừng.
- **Capability epoch** (`scp/security/capability_epoch.py` + `core/capability_token.py`):
  token HMAC mang `epoch`; verify từ chối token có `epoch < current_epoch`. Ai bump:
  kill-switch (`kernel.set_global_kill`) — xem HC-24. Ai kiểm: mọi dispatch action trước
  khi ghi journal `action_dispatched` (kèm `capability_epoch` + `idempotency_key`).
- Lifecycle: tạo token (epoch hiện tại) → dispatch (kiểm epoch + idempotency) →
  revoke/bump epoch → token cũ từ chối ngay cả khi lease còn hạn.

## HC-23 — Scheduler / Dashboard: đường HTTP vs đường file chung

- **HTTP (control/data plane):** dashboard + caller giao tiếp backend qua API
  (`/health`, `/ask`, admin routes...) — cổng 127.0.0.1:8080 → 8000; đây là đường
  duy nhất để ra lệnh/đọc trạng thái có auth.
- **File chung (state/log plane, read-only phía dashboard):** scheduler và các vòng nền
  (`core/autonomous_cycle.py`, `core/doubt_cron.py`) ghi state vào `data/`
  (vd `data/knowledge.sqlite3`, `data/hands/task_kernel.sqlite3`,
  `data/doubt_ledger.jsonl`, `service-logs/`); dashboard/kỹ sư chỉ **đọc** để chẩn đoán.
- Quy tắc: không module nền nào nhận lệnh bằng cách poll file dashboard ghi; mọi lệnh
  điều khiển phải đi HTTP có auth. Ghi file = output của scheduler, không phải input lệnh.

## HC-24 — Capability epoch nối Hands + PC Controller + TaskKernel lease

- **Khi nào bump epoch:** kill-switch phía PC Controller gọi
  `kernel.set_global_kill(True)` → `global_kill_epoch` tăng, fence mọi lease in-flight
  (`pc_controller_routes.py`: "fences every in-flight lease via the epoch bump").
- **Ai đọc:** Hands routes kiểm token bằng `parse_capability_token`
  (`scp/api/routes/hands_routes.py`) — token epoch cũ hơn epoch hiện tại → 403
  `CapabilityRevokedError`. TaskKernel ghi epoch vào lease + journal action.
- Ý nghĩa: revoke capability là **toàn cục và tức thời** cho cả 3 phía (Hands, PC, Kernel),
  không đợi TTL lease; token mới phải mang epoch mới.

## HC-37 — External deps: bắt buộc hay tùy chọn + graceful degradation

| Dependency | Dùng ở | Bắt buộc? | Khi offline |
|---|---|---|---|
| OpenRouter (LLM) | `llm_gateway/providers.py`, `client.py` | Tùy chọn (cần cho chat/generation) | Circuit breaker + fallback provider cục bộ/queue; `/health` không phụ thuộc |
| Google Fonts | **Không tìm thấy tham chiếu trong repo @ e79bafd5** (rg `fonts.googleapis|fonts.gstatic` = 0 hit trong scp/ + assets) | — | Claim "dashboard build cần fonts" từ khảo sát trước; mark UNKNOWN, cần re-check bản build JS/CSS nếu có |
| GitHub (backup/audit fetch) | `core/github_backup.py`, `core/audit_fetcher.py` | Tùy chọn | Backup/fetch fail → log + retry lần sau, không chặn boot |
| CISA KEV | `security/cisa_kev.py` | Tùy chọn (threat intel) | Cache `intel_crawl_cache.json`; stale data vẫn dùng được, fetch lại khi có mạng |
| HuggingFace | `core/ai_threat_scanner.py` (`_scan_huggingface`) | Tùy chọn (intel scan) | Scan bỏ qua nguồn đó, phần nguồn khác vẫn chạy |
| Fact-check Google | `data_sources/google_factcheck.py` | Tùy chọn | Fallback search/web evidence pipeline |

Quy tắc chung: chỉ có LLM gateway là dependency ảnh hưởng lane chính `/ask`; mọi
dependency intel/backup phải degrade về cache/empty chứ không crash process.
