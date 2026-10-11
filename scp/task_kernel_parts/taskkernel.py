# Auto-extracted from task_kernel.py
from __future__ import annotations

import json
import logging
import os
import secrets
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from scp.kernel_storage import KernelStorage, StorageIntegrityError, make_storage

logger = logging.getLogger(__name__)

from scp.task_kernel_parts.checkpoint import CheckpointEngine
from scp.task_kernel_parts.commit_approval import (
    ApprovalManager,
    CommitApprovalEngine,
    verify_approval_authority,
)
from scp.task_kernel_parts.definitions import (
    CheckpointCorrupt,
    InvalidTransition,
    KernelError,
    KillSwitchActive,
    Lease,
    NotFound,
    OptimisticLockError,
    RecoveryDecision,
    StaleLease,
    _assert_checkpoint_safe,
    now_iso,
    stable_hash,
)
from scp.task_kernel_parts.idempotency import IdempotencyEngine
from scp.task_kernel_parts.recovery import RecoveryEngine
from scp.task_kernel_parts.state_machine import StateMachineEngine

__all__ = [
    "TaskKernel",
    "OptimisticLockError",
    "CheckpointCorrupt",
    "verify_approval_authority",
    "CommitApprovalEngine",
    "ApprovalManager",
]

class TaskKernel:
    """Small durable kernel. The journal is authoritative; tasks is a rebuildable projection.

    [CHAIN-AUDIT FIX 2026-08-29] Per-thread connections thay vì 1 shared
    connection: thực nghiệm 40-100 luồng đồng thời cho thấy shared connection
    làm SELECT đọc "nhìn thấy" snapshot cũ (row vừa commit vẫn invisible) →
    NotFound → ~8-16% task chết dưới tải. Mỗi thread có connection riêng
    (WAL sinh tồn đa connection), transaction không còn dính chéo thread.
    """

    def __init__(
        self,
        db_path: str | Path | None = None,
        *,
        storage: KernelStorage | None = None,
        recovery: RecoveryEngine | None = None,
        state_machine: StateMachineEngine | None = None,
        approval_engine: CommitApprovalEngine | None = None,
        autonomous_mode: bool | None = None,
    ) -> None:
        """Initialise the kernel.

        Backward-compat: TaskKernel(db_path) still works.
        DI-ready: TaskKernel(storage=my_storage, recovery=my_recovery, state_machine=my_sm, approval_engine=my_appr) injects custom backends.
        Future: when TaskKernel is fully decoupled, the db_path arg will be removed.
        """
        if storage is not None:
            self._storage = storage
            self.db_path = getattr(storage, 'db_path', str(db_path or ''))
        else:
            if db_path is None:
                raise ValueError('Either db_path or storage= must be provided')
            self._storage = make_storage(db_path)
            self.db_path = str(db_path)
        self._bound_leases: dict[str, str] = {}
        self._system_authority: bool = False
        if autonomous_mode is not None:
            self.autonomous_mode = bool(autonomous_mode)
        else:
            self.autonomous_mode = os.environ.get("SCP_AUTONOMOUS_MODE", "").strip().lower() in {"1", "true", "yes"}
        self._schema()
        self._idempotency = IdempotencyEngine(self)
        self._checkpoint = CheckpointEngine(self)
        if recovery is not None:
            if getattr(recovery, "kernel", None) is None:
                recovery.kernel = self
            self._recovery = recovery
        else:
            self._recovery = RecoveryEngine(self)
        if state_machine is not None:
            if getattr(state_machine, "kernel", None) is None:
                state_machine.kernel = self
            self._state_machine = state_machine
        else:
            self._state_machine = StateMachineEngine(self)
        if approval_engine is not None:
            if getattr(approval_engine, "kernel", None) is None:
                approval_engine.kernel = self
            self._approval_engine = approval_engine
        else:
            self._approval_engine = CommitApprovalEngine(self)

    @property
    def storage(self) -> KernelStorage:
        """Direct access to the injected persistence storage engine (ARC-01)."""
        return self._storage

    @property
    def conn(self) -> KernelStorage:
        """Backward-compatible query facade backed by the injected storage."""
        return self._storage

    @property
    def idempotency(self) -> IdempotencyEngine:
        return self._idempotency

    @property
    def checkpoint_engine(self) -> CheckpointEngine:
        return self._checkpoint

    @property
    def recovery_engine(self) -> RecoveryEngine:
        """Direct access to the injected recovery engine (ARC-01)."""
        return self._recovery

    @property
    def state_machine(self) -> StateMachineEngine:
        """Direct access to the injected state machine engine (ARC-01)."""
        return self._state_machine

    @property
    def approval_engine(self) -> CommitApprovalEngine:
        """Direct access to the injected commit & approval engine (ARC-01 GA B44)."""
        return self._approval_engine

    def close(self) -> None:
        if hasattr(self, "_bound_leases"):
            self._bound_leases.clear()
        self._storage.close()

    def _schema(self) -> None:
        self.conn.executescript('''
            CREATE TABLE IF NOT EXISTS control (
                id INTEGER PRIMARY KEY CHECK (id=1),
                global_kill INTEGER NOT NULL DEFAULT 0,
                global_kill_epoch INTEGER NOT NULL DEFAULT 0
            );
            INSERT OR IGNORE INTO control(id) VALUES(1);
            CREATE TABLE IF NOT EXISTS tasks (
                task_id TEXT PRIMARY KEY,
                owner TEXT NOT NULL,
                goal TEXT NOT NULL,
                risk_tier TEXT NOT NULL,
                deadline_ms INTEGER NOT NULL,
                max_attempts INTEGER NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                input_hash TEXT NOT NULL,
                priority INTEGER NOT NULL DEFAULT 5,
                state TEXT NOT NULL,
                version INTEGER NOT NULL DEFAULT 1,
                active_lease_id TEXT,
                active_fencing_token INTEGER NOT NULL DEFAULT 0,
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events (
                event_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                seq INTEGER NOT NULL,
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
                issued_at REAL NOT NULL,
                expires_at REAL NOT NULL,
                heartbeat_at REAL NOT NULL,
                fencing_token INTEGER NOT NULL,
                global_kill_epoch INTEGER NOT NULL,
                released INTEGER NOT NULL DEFAULT 0,
                version INTEGER NOT NULL DEFAULT 1
            );
            CREATE INDEX IF NOT EXISTS idx_leases_task ON leases(task_id, fencing_token);
            CREATE TABLE IF NOT EXISTS checkpoints (
                checkpoint_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                attempt_id TEXT NOT NULL,
                step_id TEXT NOT NULL,
                state TEXT NOT NULL,
                planned_action_hash TEXT NOT NULL,
                capability_epoch INTEGER NOT NULL,
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
                version INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS queue_accounts (
                owner TEXT PRIMARY KEY,
                active INTEGER NOT NULL DEFAULT 0,
                dispatch_count INTEGER NOT NULL DEFAULT 0,
                last_dispatch_at REAL NOT NULL DEFAULT 0,
                version INTEGER NOT NULL DEFAULT 1
            );
            ''')
        task_columns = {row['name'] for row in self.conn.execute('PRAGMA table_info(tasks)').fetchall()}
        if 'priority' not in task_columns:
            self.conn.execute('ALTER TABLE tasks ADD COLUMN priority INTEGER NOT NULL DEFAULT 5')
        if 'active_lease_id' not in task_columns:
            self.conn.execute('ALTER TABLE tasks ADD COLUMN active_lease_id TEXT')
        if 'active_fencing_token' not in task_columns:
            self.conn.execute('ALTER TABLE tasks ADD COLUMN active_fencing_token INTEGER NOT NULL DEFAULT 0')
        if 'version' not in task_columns:
            self.conn.execute('ALTER TABLE tasks ADD COLUMN version INTEGER NOT NULL DEFAULT 1')
        if 'attempts' not in task_columns:
            self.conn.execute('ALTER TABLE tasks ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0')
        if 'error' not in task_columns:
            self.conn.execute('ALTER TABLE tasks ADD COLUMN error TEXT')

        # [SEC-S4] Table names are compile-time constants of the kernel schema.
        # PRAGMA/ALTER identifiers cannot be bound as SQL parameters, so each
        # migration statement is a compile-time literal per table — the loop
        # dispatches on the constant name and executes only literal SQL text
        # (defense-in-depth against future refactors).
        for table in ('leases', 'idempotency', 'queue_accounts'):
            if table == 'leases':
                cols = {row['name'] for row in self.conn.execute('PRAGMA table_info("leases")').fetchall()}
                if 'version' not in cols:
                    self.conn.execute('ALTER TABLE "leases" ADD COLUMN version INTEGER NOT NULL DEFAULT 1')
            elif table == 'idempotency':
                cols = {row['name'] for row in self.conn.execute('PRAGMA table_info("idempotency")').fetchall()}
                if 'version' not in cols:
                    self.conn.execute('ALTER TABLE "idempotency" ADD COLUMN version INTEGER NOT NULL DEFAULT 1')
            elif table == 'queue_accounts':
                cols = {row['name'] for row in self.conn.execute('PRAGMA table_info("queue_accounts")').fetchall()}
                if 'version' not in cols:
                    self.conn.execute('ALTER TABLE "queue_accounts" ADD COLUMN version INTEGER NOT NULL DEFAULT 1')
            else:
                raise KernelError('unsafe kernel table identifier')

    def _begin(self) -> None:
        """Acquire the write slot + BEGIN IMMEDIATE, với bounded retry trên
        'database is locked' (multi-connection WAL contention khi hệ thống
        đang chạy phụ trợ khác cùng lúc). Đã hết retry → raise, fail-closed."""
        self._storage.begin()

    def _commit(self) -> None:
        self._storage.commit()

    def _rollback(self) -> None:
        self._storage.rollback()

    def _control(self) -> Any:
        return self.conn.execute('SELECT * FROM control WHERE id=1').fetchone()

    def _task(self, task_id: str) -> Any:
        row = self.conn.execute('SELECT * FROM tasks WHERE task_id=?', (task_id,)).fetchone()
        if not row:
            raise NotFound(task_id)
        return row

    def _append_event(self, task_id: str, event_type: str, from_state: str | None, to_state: str | None, actor: str, reason: str, payload: dict[str, Any] | None=None, policy_hash: str | None=None, event_id: str | None=None) -> dict[str, Any]:
        event_id = event_id or 'evt_' + secrets.token_hex(12)
        payload = payload or {}
        previous = self.conn.execute('SELECT seq,event_hash FROM events WHERE task_id=? ORDER BY seq DESC LIMIT 1', (task_id,)).fetchone()
        seq = int(previous['seq'] + 1) if previous else 1
        prev_hash = previous['event_hash'] if previous else None
        body = {'event_id': event_id, 'task_id': task_id, 'seq': seq, 'type': event_type, 'from_state': from_state, 'to_state': to_state, 'actor': actor, 'reason': reason, 'payload': payload, 'policy_hash': policy_hash, 'prev_event_hash': prev_hash}
        event_hash = stable_hash(body)
        row = {'event_id': event_id, 'task_id': task_id, 'seq': seq, 'type': event_type, 'from_state': from_state, 'to_state': to_state, 'actor': actor, 'reason': reason, 'payload_json': json.dumps(payload, ensure_ascii=False, sort_keys=True), 'policy_hash': policy_hash, 'prev_event_hash': prev_hash, 'event_hash': event_hash, 'created_at': now_iso()}
        try:
            self.conn.execute('INSERT INTO events(event_id,task_id,seq,type,from_state,to_state,actor,reason,payload_json,policy_hash,prev_event_hash,event_hash,created_at) VALUES (:event_id,:task_id,:seq,:type,:from_state,:to_state,:actor,:reason,:payload_json,:policy_hash,:prev_event_hash,:event_hash,:created_at)', row)
        except StorageIntegrityError:
            existing = self.conn.execute('SELECT * FROM events WHERE event_id=?', (event_id,)).fetchone()
            if existing:
                return dict(existing)
            raise
        return row

    def create_task(self, task_id: str, owner: str, goal: str, risk_tier: str='R0', deadline_ms: int=120000, max_attempts: int=3, input_hash: str | None=None, priority: int=5) -> dict[str, Any]:
        if not task_id or not owner or (not goal) or (deadline_ms <= 0) or (max_attempts <= 0):
            raise KernelError('invalid task contract')
        if not isinstance(priority, int) or not 0 <= priority <= 100:
            raise KernelError('invalid task priority')
        if risk_tier not in {'R0', 'R1', 'R2', 'R3'}:
            raise KernelError('invalid risk tier')
        created = now_iso()
        input_hash = input_hash or stable_hash({'goal': goal})
        self._begin()
        try:
            self.conn.execute('INSERT INTO tasks(task_id,owner,goal,risk_tier,deadline_ms,max_attempts,input_hash,priority,state,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)', (task_id, owner, goal, risk_tier, deadline_ms, max_attempts, input_hash, priority, 'CREATED', created, created))
            self._append_event(task_id, 'TASK_CREATED', None, 'CREATED', 'kernel', 'task_created', {'input_hash': input_hash})
            self._commit()
        except Exception:
            self._rollback()
            raise
        return self.get_task(task_id)

    def _can_transition(self, from_state: str, to_state: str) -> bool:
        """Predicate checking if `from_state -> to_state` transition is allowed (ARC-01)."""
        return self._state_machine._can_transition(from_state, to_state)

    def _validate_transition(self, from_state: str, to_state: str) -> None:
        """Enforce transition rules and invariants via state machine (ARC-01)."""
        self._state_machine._validate_transition(from_state, to_state)

    def get_valid_next_states(self, from_state: str) -> set[str]:
        """Return the set of valid next states reachable from `from_state` (ARC-01)."""
        return self._state_machine.get_valid_next_states(from_state)

    def transition(
        self,
        task_id: str,
        to_state: str,
        actor: str = "kernel",
        reason: str = "",
        payload: dict[str, Any] | None = None,
        event_id: str | None = None,
        lease_id: str | None = None,
        expected_version: int | None = None,
    ) -> dict[str, Any]:
        """Atomic state transition delegated to StateMachineEngine (ARC-01)."""
        return self._state_machine.transition(
            task_id,
            to_state,
            actor=actor,
            reason=reason,
            payload=payload,
            event_id=event_id,
            lease_id=lease_id,
            expected_version=expected_version,
        )

    def _assert_not_killed(self) -> Any:
        control = self._control()
        if control['global_kill']:
            raise KillSwitchActive('global kill switch active')
        return control

    def claim(self, task_id: str, worker_id: str, attempt_id: str | None=None, ttl_seconds: float=30.0) -> Lease:
        if ttl_seconds <= 0 or not worker_id:
            raise KernelError('invalid lease contract')
        self._begin()
        try:
            control = self._assert_not_killed()
            task = self._task(task_id)
            created_at = datetime.fromisoformat(task['created_at']).timestamp()
            if time.time() >= created_at + int(task['deadline_ms']) / 1000.0:
                raise KernelError('task deadline exceeded')
            if task['state'] != 'QUEUED':
                raise KernelError(f"task not queueable: {task['state']}")
            attempt_id = attempt_id or 'attempt_' + secrets.token_hex(8)
            old = self.conn.execute('SELECT COALESCE(MAX(fencing_token),0) AS n FROM leases WHERE task_id=?', (task_id,)).fetchone()['n']
            token = int(old) + 1
            now = time.time()
            lease_id = 'lease_' + secrets.token_hex(12)
            cur = self.conn.execute("UPDATE tasks SET state='LEASED',version=version+1,active_lease_id=?,active_fencing_token=?,updated_at=? WHERE task_id=? AND version=?", (lease_id, token, now_iso(), task_id, task['version']))
            if cur.rowcount != 1:
                raise StaleLease(f"concurrency conflict claiming task {task_id}")
            self.conn.execute('INSERT INTO leases(lease_id,task_id,attempt_id,worker_id,issued_at,expires_at,heartbeat_at,fencing_token,global_kill_epoch) VALUES (?,?,?,?,?,?,?,?,?)', (lease_id, task_id, attempt_id, worker_id, now, now + ttl_seconds, now, token, control['global_kill_epoch']))
            self.conn.execute('INSERT INTO queue_accounts(owner,active,dispatch_count,last_dispatch_at) VALUES (?,?,?,?) ON CONFLICT(owner) DO UPDATE SET active=active+1,dispatch_count=dispatch_count+1,last_dispatch_at=excluded.last_dispatch_at', (task['owner'], 1, 1, now))
            self._bound_leases[task_id] = lease_id
            self._append_event(task_id, 'LEASE_GRANTED', 'QUEUED', 'LEASED', 'kernel', 'lease_granted', {'lease_id': lease_id, 'fencing_token': token, 'worker_id': worker_id})
            self._commit()
            return Lease(lease_id, task_id, attempt_id, worker_id, now + ttl_seconds, token, control['global_kill_epoch'])
        except Exception:
            self._rollback()
            raise

    def claim_next(self, worker_id: str, max_active_per_owner: int=1, ttl_seconds: float=30.0, now: float | None=None) -> Lease | None:
        """Claim one queued task using priority + owner fair-share + deadline guards."""
        if not worker_id or max_active_per_owner <= 0 or ttl_seconds <= 0:
            raise KernelError('invalid queue claim contract')
        now = time.time() if now is None else float(now)
        self._begin()
        try:
            control = self._assert_not_killed()
            candidates = self.conn.execute("\n                SELECT t.*, COALESCE(a.active,0) AS owner_active,\n                       COALESCE(a.dispatch_count,0) AS owner_dispatch_count,\n                       COALESCE(a.last_dispatch_at,0) AS owner_last_dispatch_at\n                FROM tasks t LEFT JOIN queue_accounts a ON a.owner=t.owner\n                WHERE t.state='QUEUED'\n                ORDER BY t.priority ASC, owner_dispatch_count ASC,\n                         owner_last_dispatch_at ASC, t.created_at ASC, t.task_id ASC\n                ").fetchall()
            for task in candidates:
                created_at = datetime.fromisoformat(task['created_at']).timestamp()
                if now >= created_at + int(task['deadline_ms']) / 1000.0:
                    cur = self.conn.execute(
                        "UPDATE tasks SET state='FAILED',version=version+1,active_lease_id=NULL,active_fencing_token=0,updated_at=? WHERE task_id=? AND version=?",
                        (now_iso(), task['task_id'], task['version']),
                    )
                    if cur.rowcount != 1:
                        continue
                    self._append_event(task['task_id'], 'DEADLINE_EXPIRED', 'QUEUED', 'FAILED', 'kernel', 'queue_deadline_guard', {'deadline_ms': task['deadline_ms']})
                    continue
                if int(task['owner_active']) >= max_active_per_owner:
                    continue
                attempt_id = 'attempt_' + secrets.token_hex(8)
                latest = self.conn.execute('SELECT COALESCE(MAX(fencing_token),0) AS n FROM leases WHERE task_id=?', (task['task_id'],)).fetchone()['n']
                fencing_token = int(latest) + 1
                lease_id = 'lease_' + secrets.token_hex(12)
                expires_at = now + ttl_seconds
                cur = self.conn.execute("UPDATE tasks SET state='LEASED',version=version+1,active_lease_id=?,active_fencing_token=?,updated_at=? WHERE task_id=? AND version=?", (lease_id, fencing_token, now_iso(), task['task_id'], task['version']))
                if cur.rowcount != 1:
                    continue
                self.conn.execute('INSERT INTO leases(lease_id,task_id,attempt_id,worker_id,issued_at,expires_at,heartbeat_at,fencing_token,global_kill_epoch) VALUES (?,?,?,?,?,?,?,?,?)', (lease_id, task['task_id'], attempt_id, worker_id, now, expires_at, now, fencing_token, control['global_kill_epoch']))
                self.conn.execute('INSERT INTO queue_accounts(owner,active,dispatch_count,last_dispatch_at) VALUES (?,?,?,?) ON CONFLICT(owner) DO UPDATE SET active=active+1,dispatch_count=dispatch_count+1,last_dispatch_at=excluded.last_dispatch_at', (task['owner'], 1, 1, now))
                self._bound_leases[task['task_id']] = lease_id
                self._append_event(task['task_id'], 'LEASE_GRANTED', 'QUEUED', 'LEASED', 'kernel', 'fair_queue_claim', {'lease_id': lease_id, 'fencing_token': fencing_token, 'worker_id': worker_id})
                self._commit()
                return Lease(lease_id, task['task_id'], attempt_id, worker_id, expires_at, fencing_token, control['global_kill_epoch'])
            self._commit()
            return None
        except Exception:
            self._rollback()
            raise

    def queue_status(self) -> dict[str, Any]:
        rows = self.conn.execute('SELECT owner,active,dispatch_count,last_dispatch_at FROM queue_accounts ORDER BY owner').fetchall()
        queued = self.conn.execute("SELECT COUNT(*) AS n FROM tasks WHERE state='QUEUED'").fetchone()['n']
        return {'queued': int(queued), 'owners': [dict(row) for row in rows]}

    def _lease(self, lease_id: str) -> Any:
        row = self.conn.execute('SELECT * FROM leases WHERE lease_id=?', (lease_id,)).fetchone()
        if not row:
            raise StaleLease(lease_id)
        return row

    def _assert_lease(self, lease_id: str, task_id: str, actor: str | None = None) -> Any:
        lease = self._lease(lease_id)
        control = self._control()
        now = time.time()
        if lease['task_id'] != task_id or lease['released'] or lease['expires_at'] <= now or (lease['global_kill_epoch'] != control['global_kill_epoch']) or control['global_kill']:
            if lease['released']:
                raise OptimisticLockError(
                    f"lease {lease_id} has already been released",
                    table="leases",
                    entity_id=lease_id,
                )
            raise StaleLease(lease_id)
        latest = self.conn.execute('SELECT COALESCE(MAX(fencing_token), 0) AS n FROM leases WHERE task_id=?', (task_id,)).fetchone()['n']
        if int(lease['fencing_token']) != int(latest):
            raise StaleLease(lease_id)
        if actor is not None and str(actor).strip():
            if lease['worker_id'] != str(actor).strip():
                raise InvalidTransition(f"actor '{actor}' does not match lease worker '{lease['worker_id']}'")
        return lease

    def start(self, task_id: str, lease_id: str) -> dict[str, Any]:
        self._begin()
        try:
            lease = self._assert_lease(lease_id, task_id)
            task = self._task(task_id)
            if task['state'] != 'LEASED':
                raise InvalidTransition(f"{task['state']}->RUNNING")
            if task['active_lease_id'] and task['active_lease_id'] != lease_id:
                raise StaleLease(f"start lease {lease_id} does not match active task lease {task['active_lease_id']}")
            is_system = getattr(self, "_system_authority", False)
            bound = getattr(self, "_bound_leases", {}).get(task_id)
            if not is_system:
                if not bound:
                    raise StaleLease(f"kernel instance does not possess active lease authority to start task {task_id}")
                if bound != lease_id:
                    raise StaleLease(f"caller lease {lease_id} does not match bound instance lease {bound}")
            cur = self.conn.execute(
                "UPDATE tasks SET state='RUNNING',version=version+1,active_lease_id=?,active_fencing_token=?,updated_at=? WHERE task_id=? AND version=? AND (active_lease_id=? OR active_lease_id IS NULL)",
                (lease_id, int(lease['fencing_token']), now_iso(), task_id, task['version'], lease_id)
            )
            if cur.rowcount != 1:
                raise StaleLease(f"concurrency conflict starting task {task_id}")
            self._bound_leases[task_id] = lease_id
            self._append_event(task_id, 'WORKER_STARTED', 'LEASED', 'RUNNING', 'worker', 'lease_valid', {'lease_id': lease_id})
            self._commit()
        except Exception:
            self._rollback()
            raise
        return self.get_task(task_id)

    def heartbeat(self, task_id: str, lease_id: str, extend_seconds: float=30.0, expected_version: int | None=None) -> Lease:
        self._begin()
        try:
            lease = self._assert_lease(lease_id, task_id)
            task = self._task(task_id)
            if task['active_lease_id'] != lease_id:
                raise StaleLease(f"heartbeat lease {lease_id} does not match active task lease {task['active_lease_id']}")
            if task['state'] not in {"LEASED", "RUNNING", "WAITING_TOOL", "VERIFYING", "CHECKPOINTED", "UNKNOWN"}:
                raise StaleLease(f"cannot heartbeat task in non-leased state {task['state']}")
            is_system = getattr(self, "_system_authority", False)
            bound = getattr(self, "_bound_leases", {}).get(task_id)
            if not is_system:
                if not bound:
                    raise StaleLease(f"kernel instance does not possess active lease authority for task {task_id}")
                if bound != lease_id:
                    raise StaleLease(f"caller lease {lease_id} does not match bound instance lease {bound}")
            current_lease_version = int(lease['version']) if 'version' in lease.keys() else 1
            if expected_version is not None and current_lease_version != expected_version:
                raise OptimisticLockError(
                    f"concurrency conflict on lease {lease_id}: expected version {expected_version}, found {current_lease_version}",
                    table="leases",
                    entity_id=lease_id,
                    expected_version=expected_version,
                )
            target_version = expected_version if expected_version is not None else current_lease_version
            now = time.time()
            expires = now + extend_seconds
            cur = self.conn.execute(
                'UPDATE leases SET heartbeat_at=?,expires_at=?,version=version+1 WHERE lease_id=? AND released=0 AND version=?',
                (now, expires, lease_id, target_version),
            )
            if cur.rowcount != 1:
                raise OptimisticLockError(
                    f"concurrency conflict heartbeating lease {lease_id} (version mismatch or lease released)",
                    table="leases",
                    entity_id=lease_id,
                    expected_version=target_version,
                )
            self._commit()
            return Lease(lease['lease_id'], lease['task_id'], lease['attempt_id'], lease['worker_id'], expires, lease['fencing_token'], lease['global_kill_epoch'])
        except Exception:
            self._rollback()
            raise

    _RENEWABLE_LEASE_STATES = CommitApprovalEngine._RENEWABLE_LEASE_STATES

    def renew_lease(self, task_id: str, lease_id: str, fencing_token: int, ttl_seconds: float | None = None) -> bool:
        """Expiry-only lease renewal delegated to CommitApprovalEngine (ARC-01 GA B44)."""
        return self._approval_engine.renew_lease(task_id, lease_id, fencing_token, ttl_seconds=ttl_seconds)

    def expire_leases(self, now: float | None = None) -> list[str]:
        """Sweep expired leases delegated to CommitApprovalEngine (ARC-01 GA B44)."""
        return self._approval_engine.expire_leases(now=now)

    def release(self, task_id: str, lease_id: str, expected_version: int | None = None) -> None:
        """Cooperative lease release delegated to CommitApprovalEngine (ARC-01 GA B44)."""
        return self._approval_engine.release(task_id, lease_id, expected_version=expected_version)

    def checkpoint(self, task_id: str, lease_id: str, step_id: str, state: str, planned_action: Any, capability_epoch: int, idempotency_key: str, pre_observation_ref: str | None=None, post_observation_ref: str | None=None, tool_result: Any | None=None, verifier_verdict: str | None=None) -> str:
        return self._checkpoint.checkpoint(
            task_id, lease_id, step_id, state, planned_action, capability_epoch, idempotency_key,
            pre_observation_ref=pre_observation_ref, post_observation_ref=post_observation_ref,
            tool_result=tool_result, verifier_verdict=verifier_verdict,
        )

    @staticmethod
    def _rebuild_checkpoint_payload(row: Any, state: str, planned_action: Any, post_observation_ref: str | None, tool_result: Any, verifier_verdict: str | None) -> dict[str, Any]:
        return CheckpointEngine._rebuild_checkpoint_payload(
            row, state, planned_action, post_observation_ref, tool_result, verifier_verdict,
        )

    def finalize_checkpoint(
        self,
        task_id: str,
        checkpoint_id: str,
        *,
        planned_action: Any,
        verifier_verdict: str | None = None,
        post_observation_ref: str | None = None,
        tool_result: Any | None = None,
        note: str | None = None,
    ) -> dict[str, Any]:
        return self._checkpoint.finalize_checkpoint(
            task_id, checkpoint_id, planned_action=planned_action,
            verifier_verdict=verifier_verdict, post_observation_ref=post_observation_ref,
            tool_result=tool_result, note=note,
        )

    def record_action_dispatched(self, task_id: str, lease_id: str, step_id: str, planned_action: Any, capability_epoch: int, idempotency_key: str, provider_request_id: str, pre_observation_ref: str | None=None) -> dict[str, Any]:
        """Persist the side-effect boundary before a provider response is trusted."""
        if not step_id or not idempotency_key or (not str(provider_request_id).strip()):
            raise KernelError('dispatch requires step, idempotency key and provider request')
        _assert_checkpoint_safe({'planned_action': planned_action, 'pre_observation_ref': pre_observation_ref, 'provider_request_id': provider_request_id})
        self._begin()
        try:
            lease = self._assert_lease(lease_id, task_id)
            task = self._task(task_id)
            if task['state'] not in {'RUNNING', 'WAITING_TOOL'}:
                raise InvalidTransition(f"{task['state']}->UNKNOWN")
            if task['active_lease_id'] != lease_id:
                raise StaleLease(f"dispatch lease {lease_id} does not match active task lease {task['active_lease_id']}")
            is_system = getattr(self, "_system_authority", False)
            bound = getattr(self, "_bound_leases", {}).get(task_id)
            if not is_system:
                if not bound:
                    raise StaleLease(f"kernel instance does not possess active lease authority for task {task_id}")
                if bound != lease_id:
                    raise StaleLease(f"caller lease {lease_id} does not match bound instance lease {bound}")
            existing = self.conn.execute("SELECT * FROM checkpoints WHERE task_id=? AND attempt_id=? AND step_id=? AND idempotency_key=? AND state='UNKNOWN' ORDER BY created_at DESC LIMIT 1", (task_id, lease['attempt_id'], step_id, idempotency_key)).fetchone()
            if existing:
                existing_result = json.loads(existing['tool_result_json'] or '{}')
                if existing_result.get('provider_request_id') != str(provider_request_id):
                    raise KernelError('idempotency key reused with different provider request')
                self._commit()
                result = dict(existing)
                result['dispatch_status'] = 'UNKNOWN'
                result['provider_request_id'] = existing_result.get('provider_request_id')
                return result
            tool_result = {'dispatch_status': 'UNKNOWN', 'provider_request_id': str(provider_request_id), 'planned_action': planned_action}
            payload = {'task_id': task_id, 'attempt_id': lease['attempt_id'], 'step_id': step_id, 'state': 'UNKNOWN', 'planned_action': planned_action, 'capability_epoch': capability_epoch, 'idempotency_key': idempotency_key, 'pre_observation_ref': pre_observation_ref, 'post_observation_ref': None, 'tool_result': tool_result, 'verifier_verdict': None}
            checkpoint_id = 'cp_' + secrets.token_hex(10)
            payload_hash = stable_hash(payload)
            self.conn.execute('INSERT INTO checkpoints(checkpoint_id,task_id,attempt_id,step_id,state,planned_action_hash,capability_epoch,idempotency_key,pre_observation_ref,post_observation_ref,tool_result_json,verifier_verdict,payload_hash,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (checkpoint_id, task_id, lease['attempt_id'], step_id, 'UNKNOWN', stable_hash(planned_action), capability_epoch, idempotency_key, pre_observation_ref, None, json.dumps(tool_result, ensure_ascii=False, sort_keys=True), None, payload_hash, now_iso()))
            old_state = task['state']
            cur = self.conn.execute("UPDATE tasks SET state='UNKNOWN',version=version+1,active_lease_id=?,active_fencing_token=?,updated_at=? WHERE task_id=? AND version=?", (lease_id, int(lease['fencing_token']), now_iso(), task_id, task['version']))
            if cur.rowcount != 1:
                raise StaleLease(f"concurrency conflict on task {task_id}")
            self._append_event(task_id, 'ACTION_DISPATCHED', old_state, 'UNKNOWN', 'worker', 'side_effect_response_unknown', {'checkpoint_id': checkpoint_id, 'step_id': step_id, 'idempotency_key': idempotency_key, 'provider_request_id': str(provider_request_id)})
            self._commit()
            result = self.get_checkpoint(checkpoint_id)
            result['dispatch_status'] = 'UNKNOWN'
            result['provider_request_id'] = str(provider_request_id)
            return result
        except Exception:
            self._rollback()
            raise

    def _load_reconcile_checkpoint(self, task_id: str, checkpoint_id: str) -> dict[str, Any]:
        return self._recovery._load_reconcile_checkpoint(task_id, checkpoint_id)

    def enter_reconciling(self, task_id: str, checkpoint_id: str, reason: str = 'reconcile_required') -> dict[str, Any]:
        return self._recovery.enter_reconciling(task_id, checkpoint_id, reason=reason)

    def reconcile_unknown(
        self,
        task_id: str,
        checkpoint_id: str,
        outcome: str,
        evidence_ref: str,
        verifier_id: str | None = None,
    ) -> dict[str, Any]:
        """Reconcile an uncertain side effect without auto-completing the task."""
        return self._recovery.reconcile_unknown(
            task_id, checkpoint_id, outcome, evidence_ref, verifier_id=verifier_id
        )


    def validate_checkpoint(self, checkpoint_id: str, planned_action: Any) -> dict[str, Any]:
        return self._checkpoint.validate_checkpoint(checkpoint_id, planned_action)

    def idempotency_claim(
        self,
        task_id: str,
        step_id: str,
        action_type: str,
        resource_identity: str,
        expected_version: int | None = None,
    ) -> tuple[str, bool]:
        return self._idempotency.claim(task_id, step_id, action_type, resource_identity, expected_version)

    def idempotency_complete(
        self,
        logical_key: str,
        result_ref: str,
        expected_version: int | None = None,
    ) -> None:
        return self._idempotency.complete(logical_key, result_ref, expected_version)

    def idempotency_status(self, logical_key: str) -> dict[str, Any]:
        return self._idempotency.status(logical_key)

    def commit_verification_result(self, task_id: str, lease_id: str, verification_result: Any) -> dict[str, Any]:
        """Commit an independently signed verifier receipt delegated to CommitApprovalEngine (ARC-01 GA B44)."""
        return self._approval_engine.commit_verification_result(task_id, lease_id, verification_result)

    def commit_completed(
        self,
        task_id: str,
        lease_id: str,
        verifier_verdict: Any = 'VERIFIED',
        evidence_ref: str | None = None,
        *,
        receipt: Any = None,
    ) -> dict[str, Any]:
        """Atomically transition a task to COMPLETED delegated to CommitApprovalEngine (ARC-01 GA B44)."""
        return self._approval_engine.commit_completed(
            task_id, lease_id, verifier_verdict=verifier_verdict, evidence_ref=evidence_ref, receipt=receipt
        )

    def commit_approval(
        self,
        task_id: str,
        approval_token: Any,
        actor: str = "operator",
        details: dict[str, Any] | None = None,
        max_skew_seconds: float = 300.0,
        expected_version: int | None = None,
    ) -> dict[str, Any]:
        """Atomically commit an authenticated approval delegated to CommitApprovalEngine (ARC-01 GA B44)."""
        return self._approval_engine.commit_approval(
            task_id,
            approval_token,
            actor=actor,
            details=details,
            max_skew_seconds=max_skew_seconds,
            expected_version=expected_version,
        )

    def auto_resolve_human_review(
        self,
        task_id: str,
        reason: str = "autonomous_verification_passed",
    ) -> dict[str, Any]:
        """Atomically transition a task from HUMAN_REVIEW to READY delegated to CommitApprovalEngine (ARC-01 GA B44)."""
        return self._approval_engine.auto_resolve_human_review(task_id, reason=reason)

    def fail_task_fail_closed(
        self,
        task_id: str,
        reason: str = "autonomous_fail_closed",
        error_payload: dict[str, Any] | None = None,
        actor: str = "kernel:autonomous",
    ) -> dict[str, Any]:
        """Atomically transition a non-terminal task to FAILED delegated to CommitApprovalEngine (ARC-01 GA B44)."""
        return self._approval_engine.fail_task_fail_closed(
            task_id, reason=reason, error_payload=error_payload, actor=actor
        )

    def commit_failed(
        self,
        task_id: str,
        lease_id: str,
        actor: str,
        failure_classification: str,
        indictment_ref: str,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Commit an authenticated failure delegated to CommitApprovalEngine (ARC-01 GA B44)."""
        return self._approval_engine.commit_failed(
            task_id, lease_id, actor, failure_classification, indictment_ref, details=details
        )

    def _legal_or_nearest(self, from_state: str, target: str, note: str) -> tuple[str, str]:
        """[Agent2 2026-09-29] Transition-map authority: raw-commit helpers
        (commit_failed / set_task_kill) must never journal an edge outside
        ALLOWED_TRANSITIONS. rebuild_projection replays the journal with no map
        validation, so one out-of-law edge poisons every projection rebuild and
        breaks the map contract (code must agree with the documented machine).

        If the requested target is illegal from from_state, reroute to the
        NEAREST LEGAL edge — but ONLY when ALLOWED_TRANSITIONS already contains
        such an edge for from_state; the reroute NEVER invents a new edge:

        - kills become FAILED where CANCELLED is illegal but FAILED is legal
          (fail-closed is the intent of a kill);
        - UNKNOWN-class failures become RECOVERING where RECOVERING is legal
          (RUNNING / LEASED / WAITING_TOOL / UNKNOWN);
        - planned RETRY_SCHEDULED is honoured by bookkeeping (attempts /
          planned_retry in the event payload) and committing FAILED now.

        When NO nearest legal edge exists, this RAISES InvalidTransition
        (fail-closed) instead of fabricating one.

        [SECOND-PASS DECISION 2026-09-30 — probe-verified reality] An
        UNKNOWN-class commit_failed from VERIFYING or CHECKPOINTED raises
        InvalidTransition here: those map entries carry no UNKNOWN/RECOVERING
        edge. This is intentional, not a bug. Caller census at 38562c26:
        adapter finalize sends only RETRYABLE/VERIFICATION_FAILED from
        VERIFYING (-> RETRY_SCHEDULED/FAILED), adapter fail() and the hands
        bridge send FATAL from RUNNING/VERIFYING (-> FAILED) — no runtime
        caller sends the raising shape. Widening the machine (e.g. adding
        VERIFYING->RECOVERING or a reroute to HUMAN_REVIEW) would change
        documented state-machine behavior WITHOUT evidence, so the docstring
        was fixed to match the code instead. If a caller that produces this
        shape ever appears, the fix is a map change through the
        transition-map-authority process (or an explicit caller-side reroute
        to a legal target such as HUMAN_REVIEW) — never a silent edge
        invention here.

        Returns (final_state, reason_note); the reroute is always recorded in
        the journal event reason so audits can see it.
        """
        return self._state_machine.legal_or_nearest(from_state, target, note)

    def set_global_kill(self, active: bool, actor: str = 'operator') -> int:
        """Toggle global kill switch delegated to CommitApprovalEngine (ARC-01 GA B44)."""
        return self._approval_engine.set_global_kill(active, actor=actor)

    def set_task_kill(self, task_id: str, actor: str = 'operator') -> dict[str, Any]:
        """Cancel a task with lease release delegated to CommitApprovalEngine (ARC-01 GA B44)."""
        return self._approval_engine.set_task_kill(task_id, actor=actor)

    def cancel(self, task_id: str, actor: str = 'operator') -> dict[str, Any]:
        """Cancel a task atomically (alias of set_task_kill)."""
        return self._approval_engine.cancel(task_id, actor=actor)

    def auto_reconcile_orphans(self, actor: str = 'kernel_watchdog', stale_seconds: float = 60.0, now: float | None = None) -> list[str]:
        """Tự động rà soát các task bị mồ côi (chết do crash, mất kết nối) và đưa vào RECONCILING."""
        return self._recovery.auto_reconcile_orphans(actor=actor, stale_seconds=stale_seconds, now=now)


    def verify_integrity(self) -> dict[str, Any]:
        """[C3 — Gemini indictment: SQLite SPOF] Kiểm tra sức khoẻ DB.

        PRAGMA quick_check + verify hash-chain toàn bộ journal. KHÔNG tự sửa
        gì — chỉ báo cáo (fail-closed với bằng chứng). Đây là durability
        single-node; HA đa node (Raft/etcd) là kiến trúc khác, không claim."""
        quick = self.conn.execute('PRAGMA quick_check').fetchone()[0]
        chains = {'checked': 0, 'invalid': []}
        for row in self.conn.execute('SELECT DISTINCT task_id FROM events').fetchall():
            chains['checked'] += 1
            result = self.verify_journal(row['task_id'])
            if not result['hash_chain_valid']:
                chains['invalid'].append({'task_id': row['task_id'], 'errors': result['errors'][:3]})
        return {'quick_check': quick, 'tasks': chains['checked'], 'invalid_chains': chains['invalid']}

    def backup(self, backup_dir: str | Path, retain: int=7) -> dict[str, Any]:
        """Online backup qua sqlite3 backup API (an toàn khi đang chạy WAL) +
        prune giữ lại `retain` bản mới nhất. Đây là giảm thiểu thiệt hại khi
        hỏng sector — KHÔNG phải High Availability đa node."""
        import time as _time
        dest = Path(backup_dir)
        dest.mkdir(parents=True, exist_ok=True)
        import uuid
        stamp = f"{_time.strftime('%Y%m%d-%H%M%S')}-{_time.time_ns() % 10 ** 9:09d}-{uuid.uuid4().hex[:6]}"
        target = dest / f'kernel-backup-{stamp}.sqlite3'
        self._storage.backup_to(target)
        backups = sorted(dest.glob('kernel-backup-*.sqlite3'))
        pruned = 0
        for old in backups[:max(0, len(backups) - int(retain))]:
            old.unlink(missing_ok=True)
            pruned += 1
        return {'backup': str(target), 'size_bytes': target.stat().st_size, 'pruned': pruned, 'retained': len(backups) - pruned}

    def recover_on_boot(self, actor: str = 'boot_recovery') -> dict[str, Any]:
        """[Cổng F — Event-Sourcing Crash Recovery] Máy tự replay journal."""
        return self._recovery.recover_on_boot(actor=actor)


    def in_flight_count(self) -> int:
        """[CHAIN-AUDIT: backpressure] Số task chưa tới quyết định cuối —
        dùng làm admission control chống ngập kernel dưới tải đồng thời.

        [FIX 2026-09-29] HUMAN_REVIEW là trạng thái ĐÃ CÓ quyết định (answer
        withheld, chờ human flow) — chính adapter tôn trọng điều đó qua
        ``_duplicate_is_reaskable`` (dedupe chỉ chặn task thật sự đang chạy),
        nhưng bộ đếm admission lại tính HUMAN_REVIEW là in-flight. 200 task
        withheld tích lũy từ 09-21 đã bóp nghẹt mọi /ask mới (observed:
        audit 20260929, ask bị chặn 'backpressure ... at cap 200' vĩnh viễn
        dù load hiện tại = 0). Admission control phải đếm như dedupe:
        loại cả TERMINAL lẫn HUMAN_REVIEW; nếu cần đếm review backlog,
        dùng pending_review_count().
        """
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM tasks "
            "WHERE state NOT IN ('COMPLETED','FAILED','CANCELLED','HUMAN_REVIEW')"
        ).fetchone()
        return int(row['n'])

    def pending_review_count(self) -> int:
        """Số task HUMAN_REVIEW chờ human flow — telemetry, không dùng để
        chặn intake (HUMAN_REVIEW đã là quyết định fail-closed recorded)."""
        row = self.conn.execute("SELECT COUNT(*) AS n FROM tasks WHERE state = 'HUMAN_REVIEW'").fetchone()
        return int(row['n'])

    def get_task(self, task_id: str) -> dict[str, Any]:
        return dict(self._task(task_id))

    def get_events(self, task_id: str) -> list[dict[str, Any]]:
        return [dict(x) for x in self.conn.execute('SELECT * FROM events WHERE task_id=? ORDER BY seq', (task_id,)).fetchall()]

    def get_checkpoint(self, checkpoint_id: str) -> dict[str, Any]:
        return self._checkpoint.get_checkpoint(checkpoint_id)

    def verify_journal(self, task_id: str) -> dict[str, Any]:
        events = self.get_events(task_id)
        prev = None
        errors = []
        for i, e in enumerate(events, 1):
            if e['seq'] != i:
                errors.append(f"sequence:{e['seq']} expected {i}")
            if e['prev_event_hash'] != prev:
                errors.append(f"prev_hash:{e['seq']}")
            try:
                payload = json.loads(e['payload_json'])
            except (TypeError, ValueError):
                errors.append(f"payload_json:{e['seq']}")
                prev = e['event_hash']
                continue
            body = {'event_id': e['event_id'], 'task_id': e['task_id'], 'seq': e['seq'], 'type': e['type'], 'from_state': e['from_state'], 'to_state': e['to_state'], 'actor': e['actor'], 'reason': e['reason'], 'payload': payload, 'policy_hash': e['policy_hash'], 'prev_event_hash': e['prev_event_hash']}
            if stable_hash(body) != e['event_hash']:
                errors.append(f"event_hash:{e['seq']}")
            prev = e['event_hash']
        return {'task_id': task_id, 'event_count': len(events), 'hash_chain_valid': not errors, 'errors': errors}

    def rebuild_projection(self, task_id: str, expected_version: int | None = None) -> dict[str, Any]:
        self._begin()
        try:
            journal = self.verify_journal(task_id)
            if not journal['hash_chain_valid']:
                details = ';'.join(journal['errors'])
                raise KernelError(f'journal integrity invalid: {details}')
            events = self.get_events(task_id)
            if not events:
                raise NotFound(task_id)
            state = events[0]['to_state']
            for event in events[1:]:
                if event['to_state']:
                    state = event['to_state']
            task = self._task(task_id)
            cur_version = int(task['version'])
            if expected_version is not None and cur_version != expected_version:
                raise OptimisticLockError(
                    f"concurrency conflict rebuilding projection for task {task_id}: expected version {expected_version}, found {cur_version}",
                    table="tasks",
                    entity_id=task_id,
                    expected_version=expected_version,
                )
            target_version = expected_version if expected_version is not None else cur_version
            if state in {"LEASED", "RUNNING", "WAITING_TOOL", "VERIFYING", "CHECKPOINTED", "UNKNOWN"}:
                lease_row = self.conn.execute(
                    'SELECT lease_id, fencing_token FROM leases WHERE task_id=? AND released=0 ORDER BY fencing_token DESC LIMIT 1',
                    (task_id,),
                ).fetchone()
                active_lease_id = lease_row['lease_id'] if lease_row else None
                active_fencing_token = int(lease_row['fencing_token']) if lease_row else 0
            else:
                active_lease_id = None
                active_fencing_token = 0
            cur = self.conn.execute(
                'UPDATE tasks SET state=?,version=version+1,active_lease_id=?,active_fencing_token=?,updated_at=? WHERE task_id=? AND version=?',
                (state, active_lease_id, active_fencing_token, now_iso(), task_id, target_version),
            )
            if cur.rowcount != 1:
                raise OptimisticLockError(
                    f"concurrency conflict rebuilding projection for task {task_id}: expected version {target_version}",
                    table="tasks",
                    entity_id=task_id,
                    expected_version=target_version,
                )
            self._commit()
        except Exception:
            self._rollback()
            raise
        return self.get_task(task_id)

    @staticmethod
    def recovery_decision(
        reason: str, action_dispatched: bool = False, side_effect_risk: str = 'R0'
    ) -> RecoveryDecision:
        return RecoveryEngine.recovery_decision(
            reason, action_dispatched=action_dispatched, side_effect_risk=side_effect_risk
        )

