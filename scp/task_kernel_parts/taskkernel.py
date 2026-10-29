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
    _system_authority_var,
    compute_journal_event_hash,
    current_system_authority,
    migrate_control_columns,
    now_iso,
    stable_hash,
    verified_control_row,
)
from scp.task_kernel_parts.idempotency import IdempotencyEngine
from scp.task_kernel_parts.recovery import RecoveryEngine
from scp.task_kernel_parts.state_machine import StateMachineEngine
import scp.task_kernel_parts.taskkernel_checker_gate as _checker_gate_impl  # [SEC-FIX 2026-10-27] ARC-01 tách file (move-only)
import scp.task_kernel_parts.taskkernel_queries as _queries_impl  # [2026-10-29] ARC-01 tách file (move-only, HC-21 accessors)

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
        checker_gate: Any | None = None,
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
        # [§15 2026-10-29] _system_authority KHÔNG còn là instance attribute
        # (property dưới đây đọc/ghi ContextVar theo caller). Không gán gì ở
        # đây: giá trị mặc định của ContextVar là False (fail-closed).
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
        if checker_gate is not None:
            if getattr(checker_gate, "kernel", None) is None:
                checker_gate.kernel = self
            self._checker_gate = checker_gate
        else:
            self._checker_gate = None

    @property
    def _system_authority(self) -> bool:
        """[§15 2026-10-29] System-authority waive là trạng thái CALLER (ContextVar),
        không còn là instance attribute: thread khác gọi chung kernel object
        không thể nhận miễn lease của thread đang recover (R7 CHECKER finding,
        probe 2026-10-29 trước fix: leak waive qua thread)."""
        return current_system_authority()

    @_system_authority.setter
    def _system_authority(self, value: bool) -> None:
        # Tương thích call-site cũ (recover_on_boot / tests gán trực tiếp):
        # giá trị ghi vào ContextVar của CHÍNH thread này — thread khác không
        # nhìn thấy. Muốn waive có kiểm soát, dùng system_authority_scope().
        _system_authority_var.set(bool(value))

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

    @property
    def checker_gate(self) -> Any:
        """Direct access to the autonomous checker subsystem gate (ARC-01 GA B44)."""
        if self._checker_gate is None:
            from scp.task_kernel_parts.autonomous_checker_gate import AutonomousCheckerGate
            self._checker_gate = AutonomousCheckerGate(self)
        return self._checker_gate

    def close(self) -> None:
        if hasattr(self, "_bound_leases"):
            self._bound_leases.clear()
        self._storage.close()

    def _schema(self) -> None:
        self.conn.executescript('''
            CREATE TABLE IF NOT EXISTS control (
                id INTEGER PRIMARY KEY CHECK (id=1),
                global_kill INTEGER NOT NULL DEFAULT 0,
                global_kill_epoch INTEGER NOT NULL DEFAULT 0,
                journal_head_hash TEXT,
                event_count INTEGER NOT NULL DEFAULT 0,
                legacy_migration_done INTEGER NOT NULL DEFAULT 0,
                journal_key_migrated INTEGER NOT NULL DEFAULT 1,
                journal_key_window_opened_at TEXT,
                receipt_mode_floor TEXT NOT NULL DEFAULT 'audit'
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

        migrate_control_columns(self.conn)  # [R6/R5/R5.1] one-way flags + K_journal window mark

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
        """[§15] Verified read: cờ control thiếu/sai chữ ký HMAC (K_journal) bị thay bằng
        giá trị an toàn mặc định (kill=ON fail-closed, migration flags=0, mark=None)."""
        return verified_control_row(self.conn.execute('SELECT * FROM control WHERE id=1').fetchone())

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
        event_hash = compute_journal_event_hash(body)
        row = {'event_id': event_id, 'task_id': task_id, 'seq': seq, 'type': event_type, 'from_state': from_state, 'to_state': to_state, 'actor': actor, 'reason': reason, 'payload_json': json.dumps(payload, ensure_ascii=False, sort_keys=True), 'policy_hash': policy_hash, 'prev_event_hash': prev_hash, 'event_hash': event_hash, 'created_at': now_iso()}
        try:
            self.conn.execute('INSERT INTO events(event_id,task_id,seq,type,from_state,to_state,actor,reason,payload_json,policy_hash,prev_event_hash,event_hash,created_at) VALUES (:event_id,:task_id,:seq,:type,:from_state,:to_state,:actor,:reason,:payload_json,:policy_hash,:prev_event_hash,:event_hash,:created_at)', row)
            self.conn.execute(
                'UPDATE control SET journal_head_hash = ?, event_count = event_count + 1 WHERE id = 1',
                (event_hash,),
            )
        except StorageIntegrityError:
            existing = self.conn.execute('SELECT * FROM events WHERE event_id=?', (event_id,)).fetchone()
            if existing:
                return dict(existing)
            raise
        if os.environ.get("SCP_EVENT_BUS_ENABLED", "0").strip().lower() in {"1", "true", "yes"}:
            try:
                from scp.event_bus_pg import make_event_bus
                _bus = make_event_bus()
                if _bus is not None:
                    _bus.publish("kernel.events", {"event_id": event_id, "task_id": task_id, "type": event_type, "from_state": from_state, "to_state": to_state, "event_hash": event_hash})
            except (RuntimeError, ValueError, OSError, ImportError, AttributeError, KeyError) as _bus_exc:
                logger.warning("[Track-C2] Kernel event bus publish failed (non-blocking): %s", _bus_exc)
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
        from scp.task_kernel_parts.lease_manager import claim_impl

        return claim_impl(self, task_id, worker_id, attempt_id=attempt_id, ttl_seconds=ttl_seconds)

    def claim_next(self, worker_id: str, max_active_per_owner: int=1, ttl_seconds: float=30.0, now: float | None=None) -> Lease | None:
        """Claim one queued task using priority + owner fair-share + deadline guards."""
        from scp.task_kernel_parts.lease_manager import claim_next_impl

        return claim_next_impl(self, worker_id, max_active_per_owner=max_active_per_owner, ttl_seconds=ttl_seconds, now=now)

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
        from scp.task_kernel_parts.lease_manager import assert_lease_impl

        return assert_lease_impl(self, lease_id, task_id, actor=actor)

    def start(self, task_id: str, lease_id: str) -> dict[str, Any]:
        from scp.task_kernel_parts.lease_manager import start_impl

        return start_impl(self, task_id, lease_id)

    def heartbeat(self, task_id: str, lease_id: str, extend_seconds: float=30.0, expected_version: int | None=None) -> Lease:
        from scp.task_kernel_parts.lease_manager import heartbeat_impl

        return heartbeat_impl(self, task_id, lease_id, extend_seconds=extend_seconds, expected_version=expected_version)

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
        from scp.task_kernel_parts.lease_manager import record_action_dispatched_impl

        return record_action_dispatched_impl(
            self, task_id, lease_id, step_id, planned_action, capability_epoch,
            idempotency_key, provider_request_id, pre_observation_ref=pre_observation_ref,
        )

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

    # [SEC-FIX 2026-10-27] ARC-01 tách file (move-only): thân method chuyển sang
    # taskkernel_checker_gate.py, gán vào class như _RENEWABLE_LEASE_STATES ở trên.
    autonomous_review_and_approve = _checker_gate_impl.autonomous_review_and_approve

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

    # [SEC-FIX 2026-10-27] ARC-01 tách file (move-only) — xem ghi chú trên.
    fail_task_fail_closed = _checker_gate_impl.fail_task_fail_closed

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
        """Transition-map authority: reroute to nearest legal edge (NEVER invents a new edge); if no nearest legal edge exists, RAISES InvalidTransition."""
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
        """[C3 — Gemini indictment: SQLite SPOF] Kiểm tra sức khoẻ DB."""
        from scp.task_kernel_parts.lease_manager import verify_integrity_impl

        return verify_integrity_impl(self)

    def backup(self, backup_dir: str | Path, retain: int=7) -> dict[str, Any]:
        """Online backup qua sqlite3 backup API (an toàn khi đang chạy WAL)."""
        from scp.task_kernel_parts.lease_manager import backup_impl

        return backup_impl(self, backup_dir, retain=retain)

    def recover_on_boot(self, actor: str = 'boot_recovery') -> dict[str, Any]:
        """[Cổng F — Event-Sourcing Crash Recovery] Máy tự replay journal."""
        return self._recovery.recover_on_boot(actor=actor)


    def in_flight_count(self) -> int:
        """[CHAIN-AUDIT: backpressure] Số task chưa tới quyết định cuối."""
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

    # [HC-21 2026-10-23] Bốn accessor chuẩn dưới đây thay thế SQL thô rải rác
    # ngoài kernel (api_server /v3/tasks, lifespan retry worker, doubt_cron
    # escalation backlog, deduplication_maintenance purge). Nguyên tắc: caller
    # ngoài không tự viết SQL trên projection `tasks`; mọi truy cập đi qua
    # method có docstring + contract rõ ràng ở một chỗ. Read-only methods
    # không mở transaction; purge là method duy nhất ghi và journal-first.
    #
    # [2026-10-29] ARC-01 tách file (move-only): thân 4 accessor chuyển
    # VERBATIM sang taskkernel_queries.py, gán vào class attribute theo đúng
    # pattern taskkernel_checker_gate.py ở trên (ARC-02: zero mixin). Cặp
    # counter admission (in_flight_count/pending_review_count ở trên) giữ
    # nguyên tại file này vì test admission semantics 77d44816 đọc source
    # taskkernel.py tìm docstring in_flight_count.
    list_recent = _queries_impl.list_recent
    query_by_state = _queries_impl.query_by_state
    pending_review_breakdown = _queries_impl.pending_review_breakdown
    purge_tasks = _queries_impl.purge_tasks

    def get_task(self, task_id: str) -> dict[str, Any]:
        return dict(self._task(task_id))

    def get_events(self, task_id: str) -> list[dict[str, Any]]:
        return [dict(x) for x in self.conn.execute('SELECT * FROM events WHERE task_id=? ORDER BY seq', (task_id,)).fetchall()]

    def get_checkpoint(self, checkpoint_id: str) -> dict[str, Any]:
        return self._checkpoint.get_checkpoint(checkpoint_id)

    def verify_journal(self, task_id: str) -> dict[str, Any]:
        from scp.task_kernel_parts.lease_manager import verify_journal_impl

        return verify_journal_impl(self, task_id)

    def reanchor_journal(self, task_id: str) -> dict[str, Any]:
        from scp.task_kernel_parts.lease_manager import reanchor_journal_impl

        return reanchor_journal_impl(self, task_id)

    def reanchor_all_journals(self) -> dict[str, Any]:
        from scp.task_kernel_parts.lease_manager import reanchor_all_journals_impl

        return reanchor_all_journals_impl(self)

    def rebuild_projection(self, task_id: str, expected_version: int | None = None) -> dict[str, Any]:
        from scp.task_kernel_parts.lease_manager import rebuild_projection_impl

        return rebuild_projection_impl(self, task_id, expected_version=expected_version)

    @staticmethod
    def recovery_decision(
        reason: str, action_dispatched: bool = False, side_effect_risk: str = 'R0'
    ) -> RecoveryDecision:
        return RecoveryEngine.recovery_decision(
            reason, action_dispatched=action_dispatched, side_effect_risk=side_effect_risk
        )

