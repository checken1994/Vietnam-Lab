-- SCP TaskKernel — canonical PostgreSQL schema (ADOPT-AND-FIX Track C1)
--
-- 1:1 mapping of the SQLite schema declared by TaskKernel._schema()
-- (scp/task_kernel_parts/taskkernel.py). Kernel semantics are unchanged:
-- only the storage backend changes. Postgres is storage + event bus;
-- state machine, lease/fencing, idempotency, journal hash-chain and
-- UNKNOWN reconcile logic stay in the kernel.
--
-- Type mapping (SQLite -> PostgreSQL), chosen for byte-level parity:
--   TEXT    -> TEXT
--   INTEGER -> BIGINT   (SQLite INTEGER is signed 64-bit)
--   REAL    -> DOUBLE PRECISION (IEEE-754 8-byte, same as SQLite REAL)
--   BLOB    -> BYTEA    (not used by the current kernel schema)
-- Timestamps stay TEXT ISO-8601 strings (now_iso() output) exactly like the
-- SQLite backend, so projection comparisons are byte-identical.
--
-- Two ways this schema is materialised:
--  1. Runtime: TaskKernel._schema() sends the SQLite DDL to
--     PgKernelStorage.executescript(), which translates it (same mapping).
--  2. Operations: scripts/migrate_kernel_sqlite_to_pg.py applies this file
--     (or verifies it) before copying rows 1:1.
--
-- This file must never drift from the translator mapping in
-- scp/kernel_storage_pg.py; parity tests (tests/T04_kernel/test_pg_storage_parity.py)
-- compare SQLite and PG projections on the same operation sequence.

CREATE TABLE IF NOT EXISTS control (
    id BIGINT PRIMARY KEY CHECK (id=1),
    global_kill BIGINT NOT NULL DEFAULT 0,
    global_kill_epoch BIGINT NOT NULL DEFAULT 0,
    journal_head_hash TEXT,
    event_count BIGINT NOT NULL DEFAULT 0,
    legacy_migration_done BIGINT NOT NULL DEFAULT 0,  -- [R6] cờ một chiều, mirror SQLite schema
    journal_key_migrated BIGINT NOT NULL DEFAULT 1,  -- [R5] cutover K_journal, mirror SQLite schema
    journal_key_window_opened_at TEXT,  -- [R5.1] dual-accept window mark (NULL on fresh DBs)
    -- [Key C L2b] floor một chiều của receipt authority (audit -> enforce); thứ tự cột = SQLite fresh
    -- (CREATE trong TaskKernel._schema rồi tới các cột chữ ký). Sig NULL đọc ra 'enforce' (fail-closed) và
    -- KHÔNG có API ký lại 'audit': DB canonical-trực-tiếp phải đi qua migration script (copy hàng đã ký).
    receipt_mode_floor TEXT NOT NULL DEFAULT 'audit',
    -- [§15 2026-10-29] chữ ký HMAC từng cờ control (K_journal) — kernel thêm các
    -- cột này qua ALTER tại open (migrate_control_columns); thứ tự = SIGNED_CONTROL_FLAGS.
    -- Seed row sinh NULL: một kernel mở DB canonical-trực-tiếp (không qua
    -- migration script) sẽ đọc fail-closed về mặc định an toàn cho tới khi API
    -- kernel ghi/ký lại (set_global_kill / reanchor_all_journals).
    global_kill_sig TEXT,
    journal_key_migrated_sig TEXT,
    legacy_migration_done_sig TEXT,
    journal_key_window_opened_at_sig TEXT,
    receipt_mode_floor_sig TEXT
);

-- Singleton row that holds the kill-switch state (SQLite: INSERT OR IGNORE).
INSERT INTO control(id) VALUES(1) ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    goal TEXT NOT NULL,
    risk_tier TEXT NOT NULL,
    deadline_ms BIGINT NOT NULL,
    max_attempts BIGINT NOT NULL,
    attempts BIGINT NOT NULL DEFAULT 0,
    input_hash TEXT NOT NULL,
    priority BIGINT NOT NULL DEFAULT 5,
    state TEXT NOT NULL,
    version BIGINT NOT NULL DEFAULT 1,
    active_lease_id TEXT,
    active_fencing_token BIGINT NOT NULL DEFAULT 0,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Append-only event journal; (task_id, seq) is the hash-chain order.
CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    seq BIGINT NOT NULL,
    type TEXT NOT NULL,
    from_state TEXT,
    to_state TEXT,
    actor TEXT NOT NULL,
    reason TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    policy_hash TEXT,
    prev_event_hash TEXT,
    event_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(task_id, seq)
);

CREATE TABLE IF NOT EXISTS leases (
    lease_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    attempt_id TEXT NOT NULL,
    worker_id TEXT NOT NULL,
    issued_at DOUBLE PRECISION NOT NULL,
    expires_at DOUBLE PRECISION NOT NULL,
    heartbeat_at DOUBLE PRECISION NOT NULL,
    fencing_token BIGINT NOT NULL,
    global_kill_epoch BIGINT NOT NULL,
    released BIGINT NOT NULL DEFAULT 0,
    version BIGINT NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_leases_task ON leases(task_id, fencing_token);

CREATE TABLE IF NOT EXISTS checkpoints (
    checkpoint_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    attempt_id TEXT NOT NULL,
    step_id TEXT NOT NULL,
    state TEXT NOT NULL,
    planned_action_hash TEXT NOT NULL,
    capability_epoch BIGINT NOT NULL,
    idempotency_key TEXT NOT NULL,
    pre_observation_ref TEXT,
    post_observation_ref TEXT,
    tool_result_json TEXT,
    verifier_verdict TEXT,
    payload_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS idempotency (
    logical_key TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    step_id TEXT NOT NULL,
    action_type TEXT NOT NULL,
    resource_identity TEXT NOT NULL,
    status TEXT NOT NULL,
    result_ref TEXT,
    created_at TEXT NOT NULL,
    version BIGINT NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS queue_accounts (
    owner TEXT PRIMARY KEY,
    active BIGINT NOT NULL DEFAULT 0,
    dispatch_count BIGINT NOT NULL DEFAULT 0,
    last_dispatch_at DOUBLE PRECISION NOT NULL DEFAULT 0,
    version BIGINT NOT NULL DEFAULT 1
);
