# SECRETS.md — Bảng registry 11 secret của SCP (HC-07/HC-08)

> Trả nợ **HC-07** (11 loại token/secret riêng biệt, không ai khai báo vai trò)
> và **HC-08** (secret từ env hoặc `*_FILE`) — K2-W2, 2026-10-23.
>
> Nguồn sự thật machine-readable: `scp/security/secret_registry.py`
> (`SECRET_PURPOSES`, `resolve()`, `audit_missing()`).
> **Tài liệu này KHÔNG BAO GIỜ chứa giá trị secret** — chỉ tên, vai trò, consumer.

## Precedence đã pin (HC-08)

1. `{name}_FILE` được set (khác rỗng) → **file thắng**. Lý do: `*_FILE` là kênh
   cấp phát do orchestrator kiểm soát (docker secrets / K8s mount); khi nó được
   mount, đó là ý định hiện tại của operator, env cùng lúc chỉ là residue từ
   shell/bootstrap (vd. default của `compose.test.yml`).
2. Fail-closed: `{name}_FILE` set nhưng file thiếu/không đọc được/rỗng →
   `resolve()` trả `None` + log ERROR (chỉ tên var + loại lỗi, không giá trị),
   **không fallback về env** — fallback âm thầm là fail-open.
3. Không cache giá trị; mỗi lần gọi đọc lại env/file (secret có thể rotate).
4. Tên secret không có trong registry → `ValueError` (phải khai báo trước).
5. Lưu ý: `scp/security/auth_config.py` giữ precedence riêng NGHIÊM hơn
   (env + file khác giá trị → `_FILE_CONFLICT`); không thay trong đợt này.

## Bảng registry (11 secret)

| Secret | Purpose | Consumer chính | `_FILE` | Min length |
|---|---|---|---|---|
| `SCP_ADMIN_KEY` | Admin key cấp admin token + gate mint JWT admin | `scp/security/auth_config.py`, `scp/api_server.py`, `scp/runtime_audit/runner.py`, `scp/api/routes/hands_routes.py` | có (`SCP_ADMIN_KEY_FILE`, auth_config) | 8 (config_contract) |
| `SCP_AUTH_TOKEN_SECRET` | Shared secret xác thực admin/benchmark bearer token | `scp/security/auth_config.py`, `scp/security/auth.py`, `scp/benchmark/run_benchmark*.py` | có (`SCP_AUTH_TOKEN_SECRET_FILE`, auth_config) | — |
| `SCP_BRAIN_ADMIN_TOKEN` | Admin token brain-console mini-service (Node.js); compose map từ `SCP_ADMIN_KEY` | `mini-services/brain-console/index.js` | không | — |
| `SCP_CAPABILITY_SECRET` | HMAC capability token + TaskKernel journal hash chain (GAP-09, fail-closed) | `scp/core/capability_token.py`, `scp/core/verifier_receipt.py`, `scp/core/autonomous_cycle.py`, `scp/task_kernel_parts/definitions.py`, `scp/data_sources/auto_web_ingestor.py`, `scp/core/doubt_cron.py` | không | — |
| `SCP_DASHBOARD_PROXY_SECRET` | Shared secret reverse proxy ↔ Next.js dashboard middleware | `dashboard/src/middleware.ts`, `dashboard/src/lib/scp-service-auth.ts`, `dashboard/gateway.js` | không | — |
| `SCP_ENCRYPTION_KEY` | Khóa AES-256 mã hóa state nhạy cảm at-rest | `scp/security/bypass_encrypt.py` | không | — |
| `SCP_INTERNAL_SECRET` | HMAC giữa các dịch vụ nội bộ | `scp/api/routes/agent_routes.py` | không | — |
| `SCP_JWT_SECRET` | Khóa ký JWT API (HS256), required tại boot | `scp/security/jwt_guard.py`, `scp/core/config_contract.py` | không | 32 (config_contract) |
| `SCP_PC_CONTROLLER_TOKEN` | Bearer PC Controller / web control / MCP transport (deny-by-default) | `scp/api/routes/{pc_controller,web_control,hands,call}_routes.py`, `scp/api/routes/agent_routes.py`, `scp/middleware/runtime_skill_guard.py`, `scp/mcp_server/server.py` | không | — |
| `SCP_SCHEDULER_ADMIN_TOKEN` | Admin token scheduler loop; compose fail-closed khi thiếu (`:?`) | `scp/security/auth_config.py`, `scp/runtime_audit/runner.py` | có (`SCP_SCHEDULER_ADMIN_TOKEN_FILE`, auth_config) | — |
| `SCP_VERIFIER_SECRET` | Secret ký verifier receipt (FA-04 / GAP-03); fallback `SCP_CAPABILITY_SECRET` | `scp/core/verifier_receipt.py` | không | — |

## Điểm đã dùng `resolve()` (K2-W2, mẫu — migrate hàng loạt ở wave sau)

- `scp/security/jwt_guard.py::_get_jwt_secret` — `SCP_JWT_SECRET`.
- `scp/core/capability_token.py::get_capability_secret` — `SCP_CAPABILITY_SECRET`.

## Chưa làm (UNKNOWN / còn mở)

- `scp/core/config_contract.validate_boot_config` vẫn đọc env trực tiếp: nếu
  secret chỉ cấp qua `*_FILE`, boot contract sẽ báo MISSING dù `resolve()` tìm
  thấy → cần nối `audit_missing`/resolve vào boot gate (wave K2 sau).
- `SCP_BRAIN_ADMIN_TOKEN`, `SCP_DASHBOARD_PROXY_SECRET` nằm ở tiến trình
  Node/TS ngoài package Python; registry Python chỉ khai báo metadata, không
  resolve được cho tiến trình đó.
- `min_length` trong registry hiện là metadata; enforcement thật vẫn ở
  `config_contract.MIN_LENGTH` (chỉ 2 secret).
