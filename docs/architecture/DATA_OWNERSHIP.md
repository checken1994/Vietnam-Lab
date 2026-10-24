# DATA OWNERSHIP MAP (HC-17 / HC-18)

Trạng thái: trả nợ HC-17 (v13.db đa chủ) + HC-18 (ownership map từng file).
Commit khảo sát: `feat/runtime-audit-brain-console` @ `e79bafd5` (2026-10-23).
Phạm vi: các store chính dưới `data/`. Bảng là **bằng chứng tĩnh tại 1 commit**,
không phải cam kết runtime — thay đổi schema/file phải cập nhật bảng này.

## Quy tắc ownership (bắt buộc)

1. **Một store = một owner module** (module duy nhất được ghi bằng SQL thô / open file
   read-modify-write). Owner phải expose accessor (hàm/API) cho phần còn lại của hệ thống.
2. **Module ngoài owner KHÔNG được chạy SQL thô** (`sqlite3.connect` trực tiếp, mở JSONL
   để ghi) vào store của owner khác. Chỉ đọc qua accessor của owner.
3. **Ngoại lệ được cấp phép (out-of-process, read-only, phải ghi reason trong docstring):**
   - `scp/runtime_audit/probes/*` — probe runtime đọc `data/runtime_audit.sqlite3`
     out-of-process, read-only (reason nằm trong `scp/runtime_audit/probes/_shared.py`).
   - `scp/autofix/ingestors/kernel_crash_ingestor.py` — ingest crash, đọc
     `data/ask_task_kernel.sqlite3` read-only (không ghi trực tiếp DB kernel).
4. Store mới phải thêm vào bảng này trong cùng PR tạo ra nó (fail-closed review).

## Bảng ownership

| File (data/) | Owner (ghi) | Readers KHÔNG qua owner accessor | Retention / pruning hiện có | Trust gate |
|---|---|---|---|---|
| `v13.db` | `scp/core/db_manager.py` (+`db_manager_parts/`) | 18+ module tham chiếu tên file trực tiếp: `api_server.py`, `api_server_parts/helpers.py`, `core/anchor.py`, `core/autonomous_cycle.py`, `core/data_partitioner.py`, `core/github_backup.py`, `core/fast_learning_engine*`, `core/phase0.py`, `core/partition/shard.py`, `learning/continual.py`, `knowledge/source_reputation.py`… (chưa audit từng caller đã qua accessor — đó là phase 1 của HC-17) | WAL mode; backup qua `core/github_backup.py`; chưa thấy prune tập trung | Không |
| `ask_task_kernel.sqlite3` | TaskKernel: `scp/task_kernel.py` + `scp/task_kernel_parts/*` (state_machine, lease_manager, idempotency, checkpoint, recovery) | `autofix/ingestors/kernel_crash_ingestor.py` (read-only, ngoại lệ được cấp phép #3); routes đọc qua kernel adapter | Journal/leases giữ theo task; `kernel-backups/` cho snapshot; chưa thấy TTL | Không (trạng thái nội bộ) |
| `hands/task_kernel.sqlite3` | TaskKernel instance phía Hands (cùng code `task_kernel_parts/`, DB riêng trong `data/hands/`) | `core/autonomous_cycle.py` dispatch goal vào DB này (qua API kernel, xác nhận chưa từng-by-từng) | Như `ask_task_kernel.sqlite3` | Không |
| `world_state.sqlite` | `scp/world_state/temporal_authority.py` (TemporalAuthority) | `api/routes/world_state_routes.py`, `api_server_parts/pipeline/ledger_stage.py` — khởi tạo `TemporalAuthority(db_path=...)` = đi qua owner accessor | WAL mode; chưa thấy prune | Không |
| `calibration.sqlite` | `CalibrationLedger` (writer: `api/routes/calibration_routes.py`, `api_server_parts/pipeline/ledger_stage.py` đều qua class này) | Không phát hiện reader ngoài lớp owner | WAL mode; chưa thấy prune | Không |
| `knowledge.sqlite3` | `scp/knowledge/domain_knowledge.py` (DomainKnowledge.store — choke point ghi duy nhất) | `api/routes/v105_routes.py`, `core/autonomous_cycle.py`, `core/fast_learning_engine_parts/fastlearningengine.py`, `data_sources/free_api_bridge.py` (đọc/ghi phải qua DomainKnowledge; chưa audit từng call-site) | Chưa thấy prune; backup cùng v13 | **Có** — trust gate của DomainKnowledge (nguồn không tin cậy không được ghi như fact đã kiểm chứng) |
| `bypasses.jsonl` + `dynamic_rules.jsonl` (attack memory) | `scp/security/attack_memory.py` (AttackPatternMemory — module ghi duy nhất) | `security/unified_detector.py` tiêu thụ qua `check_against_rules()` (owner accessor, in-process); `security/defense_antibody_synthesizer.py` ghi qua AttackPatternMemory API | `dynamic_rules.jsonl` capped `MAX_DYNAMIC_RULES=200`; `bypasses.jsonl` chỉ dedup-at-write (sha256), **không có size prune** — từng phình 47686 dòng/8 biến thể cross_lang | **Có** — poisoning gate: rule nguồn non-local (crawler/simulator) ở trạng thái `pending`, chỉ activate qua `promote_rule()` + FP screening (AUDIT-FIX 2026-09-24) |
| `autofix_audit.jsonl` | `scp/autofix/engine.py` + `scp/autofix/deterministic_worker.py` | Không phát hiện reader ngoài autofix | Append-only, chưa thấy prune | Không |
| `chat_memory.jsonl` | `scp/core/chat_memory_store.py` | Không phát hiện reader ngoài lớp owner | Prune: `MAX_RECORDS=5000`, `MAX_CONTENT_CHARS=800`, `_prune_if_needed()` | Không |
| `trace_ledger.jsonl` | `scp/trace_ledger.py` (hash-chained append-only) | `ask_adapter_parts/ledger_writer.py`, `api_server_parts/_trace_impl.py`, `core/autonomous_ledger.py` — ghi qua lớp ledger; không reader SQL thô | Append-only + CHAIN_RECOVERY/anchor; **không prune** (by design — ledger kiểm toán) | Không (tự bảo toàn bằng hash chain) |
| `scp_reputation.sqlite` | `scp/knowledge/source_reputation.py` | `knowledge/domain_knowledge.py` tham chiếu (qua API reputation) | Chưa thấy prune | Phụ trợ cho trust gate của knowledge |
| `subsystem_heartbeat.sqlite` | `scp/core/subsystem_telemetry.py` | Không phát hiện reader ngoài lớp owner | Chưa thấy prune | Không |
| `escalation_state.json` | `scp/security/escalation.py` | Không phát hiện reader ngoài lớp owner | File state đơn, ghi đè theo state machine | Không |
| `runtime_audit.sqlite3` | `scp/runtime_audit/ledger.py` | `scp/runtime_audit/probes/_shared.py` — probe out-of-process **read-only** (ngoại lệ được cấp phép #3) | WAL mode; retention do audit policy, chưa thấy prune trong repo | Không |
| `doubt_ledger.jsonl` | `scp/core/doubt_cron.py` | Không phát hiện reader ngoài lớp owner | Rotate 10 MB, giữ 3 backup (`_rotate_jsonl_if_needed`) | Không |
| `hypothesis_zone.db` | `scp/core/hypothesis_zone.py` | Không phát hiện reader ngoài lớp owner | WAL mode; chưa thấy prune | Không |
| `kb_evolve.sqlite` | `scp/meta/kb_evolve.py` | Không phát hiện reader ngoài lớp owner | WAL mode; chưa thấy prune | Không |
| `failure_ledger.sqlite3` | `scp/epistemic/failure_ledger.py` | Không phát hiện reader ngoài lớp owner | WAL mode; chưa thấy prune | Không |

## Giới hạn bằng chứng (không được đọc như "đã xác thực toàn bộ")

- Cột "Readers KHÔNG qua owner accessor" lập từ grep tên file tại commit `e79bafd5`;
  module chỉ import accessor của owner (không nhắc tên file) không xuất hiện ở cột này,
  nhưng grep tĩnh **không chứng minh** mọi call-site đều đi qua accessor (HC-17 phase 1
  còn lại: audit 18 caller của `v13.db`).
- `subsystem_heartbeat.sqlite` từng bị ghi nhận "malformed" trong khảo sát trước; tại
  commit này chưa tìm thấy logic tự-phục-hồi/recreate trong `subsystem_telemetry.py` —
  cần runtime audit riêng để xác nhận.
- Retention "chưa thấy prune" = không tìm thấy bằng grep tĩnh, không hứa hẹn "sẽ phình vô hạn".

## Việc còn mở

- HC-17 phase 1: chuyển 18 caller `v13.db` qua accessor `db_manager`; tách DB long-term.
- Bổ sung trust gate cho các store học từ nguồn ngoài (hiện chỉ `knowledge.sqlite3` + attack memory có).
