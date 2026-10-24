# AUTOFIX_TRIGGERS.md — Boundary đường kích hoạt Audit/Fix của AutoFix (HC-40)

> [HC-40 · 2026-10-23] Doc boundary ngắn: **ai** kích hoạt audit/fix, **audit log nằm ở đâu**,
> và **kill switch chặn gì**. Nguồn sự thật: code tại HEAD (feat/runtime-audit-brain-console);
> mỗi claim trong doc này map đúng 1 file:line có thể rg lại được.

## 1. Hai đường kích hoạt audit/fix (apply mode)

| # | Đường | Ai kích hoạt | Code path | Cadence / trigger |
|---|---|---|---|---|
| T1 | **Backend scheduled job** (in-process) | BackgroundJobRegistry job `deep_audit_scheduler`, đăng ký bởi lifespan | `scp/api_server_parts/lifespan.py` `_deep_audit_loop` → `scp/autofix/runner.py::run_deep_audit` → `run_once(ast_scan=True)` | 24h (initial delay 60s; boot-run mặc định SKIP qua `SCP_DEEP_AUDIT_BOOT_RUN` — F-10) |
| T2 | **API + loop-scheduler mini-service** | `mini-services/loop-scheduler` (port 3030) cron mỗi 300s hoặc operator POST `/trigger`; cũng gọi được trực tiếp bằng admin token | `POST /v105/autofix/run-audit` (`scp/api/routes/v105_routes.py::v105_run_deep_audit`) → `_v105_run_deep_audit_impl` → `run_deep_audit` (apply mode) | cron 5 phút (env `LOOP_INTERVAL_SEC`) hoặc manual |

**Điểm hội tụ:** cả T1 và T2 (apply mode) đều đi qua `run_once()` trong `scp/autofix/runner.py` —
đây là **điểm chặn chung** duy nhất của hai đường (kill-switch gate HC-40 đặt ở đây).

### Đường phụ (không apply nguồn, chỉ scan/enqueue)

| # | Đường | Ghi chú |
|---|---|---|
| T2-observe | `POST /v105/autofix/run-audit` với `mode=observe` | Chỉ AST-scan thu bằng chứng, **không** gọi `process_bug`, không ghi source |
| T2-worker | `POST /v105/autofix/run-audit` với `SCP_AUTOFIX_DETERMINISTIC_ONLY=1` + `SCP_AUTOFIX_WORKER_MODE=deterministic` | Scan + enqueue vào DeterministicWorker queue; apply/verify do worker riêng sở hữu (xem `docs/AUTOFIX_DETERMINISTIC_WORKER.md`) |
| T3 | `pre_startup_audit` (boot) | `scp/autofix/runner.py::pre_startup_audit` → cũng qua `run_once` |

## 2. Hai nơi đổi trạng thái AutoFix theo tín hiệu tấn công (attack mode)

| # | Nơi | Tín hiệu | Code |
|---|---|---|---|
| A1 | Backend job `attack_mode_monitor` (registry) | `governance_kill` notification: >20 trong 10 phút → ON; <5 → OFF | `scp/api_server_parts/lifespan.py` `_attack_mode_monitor` → `AutoFixEngine.set_attack_mode` |
| A2 | Admin API (con người) | Operator POST | `POST /v105/autofix/attack-mode/{enabled}` (`scp/api/routes/v105_routes.py`) |

Attack mode = **hạn chế** (Tier 4 restraints), không phải mở quyền — bật khi bị tấn công để
SIÊU thận trọng hơn. Kill switch KHÔNG chặn việc bật restraints.

## 3. Audit log nằm ở đâu

| Log | Vị trí | Ghi bởi |
|---|---|---|
| Kết quả từng bug của deep audit | `data/deep_audit_results.jsonl` | `_write_deep_audit_result` (`scp/autofix/runner_phases/ast_scan.py`) |
| Forensic fix entry (before_hash/after_hash/rollback_token/reality_test) | audit log schema `scp/autofix/audit_log.py::write_audit_entry` | mọi Tier 1/2/3/4 (Pydantic bắt buộc 4 trường) |
| Request-run ledger của endpoint T2 | `_V105_ROUTES_LEDGER` (`scp/core/request_run_ledger.py`) qua `@traced_request(action="v105_run_deep_audit", require_write=True)` | decorator trên chính endpoint |
| Scheduler run của loop-scheduler | `data/loop_runs.jsonl` + dashboard port 3030 | `mini-services/loop-scheduler/index.ts` |
| Telemetry subsystem | `data/` qua `SubsystemTelemetry('deep_audit'/'attack_monitor')` (`/v105/runtime/subsystems`) | heartbeat + cycle_started/completed/failed |

## 4. Kill switch chặn gì (fail-closed)

Authority DUY NHẤT: `scp/security/kill_switch.py::is_engaged()` — 3 kênh:
file flag `data/pc_controller/KILL_SWITCH`, env `SCP_KILL_SWITCH=1`, kernel `control.global_kill`.
Lỗi đọc trạng thái = tính là engaged (fail-closed).

| Điểm chặn | Phạm vi | Đặt tại |
|---|---|---|
| **run_once() gate** [HC-40 — điểm chung T1+T2+T3] | mọi apply-mode cycle: trả summary rỗng `source='kill_switch_blocked'`, không scan, không fix, không raise | `scp/autofix/runner.py::run_once` (ngay sau `ensure_runner_env`) |
| **run-audit endpoint gate** [HC-40] | TOÀN BỘ `POST /v105/autofix/run-audit` (kể cả observe/worker mode) → 503 `kill_switch_engaged` | `scp/api/routes/v105_routes.py::v105_run_deep_audit` |
| `/ask` admission (có từ trước) | không tạo task mới khi kill | `scp/api_server.py::_pc_kill_switch_engaged` |
| `/v3/pc/execute|write`, `/v3/hands/execute` (có từ trước) | hardware execution | `scp/middleware/runtime_skill_guard.py` Pillar 5 |
| TaskKernel claim/lease (có từ trước) | `KillSwitchActive` khi kernel global_kill | `taskkernel._assert_not_killed` |

**Trước HC-40:** KHÔNG đường audit/fix nào kiểm kill switch (gap — kill switch chỉ chặn
/ask + hardware, trong khi AutoFix vẫn tự scan/fix source). **Sau HC-40:** cả 2 đường T1/T2
đều bị chặn tại 2 lớp (endpoint + điểm chung run_once).

## 5. Residual scope (khai báo trung thực — DNA #26)

- DeterministicWorker apply **riêng lẻ** sau khi enqueue (T2-worker): apply happens trong
  worker process riêng; gate endpoint chặn lúc enqueue, nhưng worker đang chạy dở không bị
  kill switch dừng giữa chừng (worker có verify/rollback riêng).
- Attack-mode setters A1/A2 không bị kill switch chặn (bật restraints không phải mutation).
- `run_once(bugs=[...])` gọi trực tiếp từ code/test khác (non-AST nguồn) vẫn qua gate —
  đúng chủ đích (mọi đường fix phải tôn trọng kill switch).

Tests biên giới: `tests/internal/test_hc40_autofix_kill_switch_boundary.py`.
