"""Recovery and reconciliation engine for durable TaskKernel (ARC-01)."""
from __future__ import annotations

import json
import time
from typing import Any

from scp.task_kernel_parts.definitions import (
    ALLOWED_TRANSITIONS,
    TERMINAL,
    CheckpointCorrupt,
    InvalidTransition,
    KernelError,
    NotFound,
    OptimisticLockError,
    RecoveryDecision,
    StaleLease,
    _assert_checkpoint_safe,
    now_iso,
)


class RecoveryEngine:
    """Encapsulates crash recovery, orphan reconciliation, and uncertain side-effect handling."""

    def __init__(self, kernel: Any = None) -> None:
        self.kernel = kernel

    @property
    def conn(self):
        return self.kernel.conn

    def _load_reconcile_checkpoint(self, task_id: str, checkpoint_id: str) -> dict[str, Any]:
        checkpoint = self.conn.execute(
            "SELECT * FROM checkpoints WHERE checkpoint_id=?", (checkpoint_id,)
        ).fetchone()
        if not checkpoint or checkpoint["task_id"] != task_id:
            raise KernelError("checkpoint task mismatch")
        if checkpoint["state"] != "UNKNOWN":
            raise KernelError("reconcile requires UNKNOWN checkpoint")
        try:
            tool_result = json.loads(checkpoint["tool_result_json"] or "{}")
        except (TypeError, ValueError) as exc:
            raise KernelError("checkpoint integrity: invalid tool result JSON") from exc
        if "planned_action" not in tool_result:
            raise KernelError("checkpoint integrity: planned action unavailable")
        try:
            self.kernel.validate_checkpoint(checkpoint_id, tool_result["planned_action"])
        except CheckpointCorrupt as exc:
            raise KernelError(f"checkpoint integrity: {exc}") from exc
        return dict(checkpoint)

    def enter_reconciling(
        self, task_id: str, checkpoint_id: str, reason: str = "reconcile_required"
    ) -> dict[str, Any]:
        self.kernel._begin()
        try:
            task = self.kernel._task(task_id)
            self._load_reconcile_checkpoint(task_id, checkpoint_id)
            if task["state"] == "RECONCILING":
                self.kernel._commit()
                return dict(task)
            if task["state"] != "UNKNOWN":
                raise InvalidTransition(f"{task['state']}->RECONCILING")
            cur = self.conn.execute(
                "UPDATE tasks SET state='RECONCILING',version=version+1,active_lease_id=NULL,active_fencing_token=0,updated_at=? WHERE task_id=? AND version=?",
                (now_iso(), task_id, task["version"]),
            )
            if cur.rowcount != 1:
                raise StaleLease(f"concurrency conflict entering reconciling task {task_id}")
            active_leases = self.conn.execute(
                "SELECT lease_id FROM leases WHERE task_id=? AND released=0", (task_id,)
            ).fetchall()
            if active_leases:
                self.conn.execute(
                    "UPDATE leases SET released=1,version=version+1 WHERE task_id=? AND released=0",
                    (task_id,),
                )
                for _ in active_leases:
                    self.conn.execute(
                        "UPDATE queue_accounts SET active=CASE WHEN active>0 THEN active-1 ELSE 0 END,version=version+1 WHERE owner=?",
                        (task["owner"],),
                    )
            if hasattr(self.kernel, "_bound_leases"):
                self.kernel._bound_leases.pop(task_id, None)
            self.kernel._append_event(
                task_id,
                "RECONCILE_STARTED",
                "UNKNOWN",
                "RECONCILING",
                "kernel",
                reason or "reconcile_required",
                {"checkpoint_id": checkpoint_id},
            )
            self.kernel._commit()
            return self.kernel.get_task(task_id)
        except Exception:
            self.kernel._rollback()
            raise

    def reconcile_unknown(
        self,
        task_id: str,
        checkpoint_id: str,
        outcome: str,
        evidence_ref: str,
        verifier_id: str | None = None,
    ) -> dict[str, Any]:
        """Reconcile an uncertain side effect without auto-completing the task."""
        normalized = str(outcome or "").strip().upper()
        if normalized in {"PARTIAL", "CONFLICT"}:
            if not evidence_ref or not str(evidence_ref).strip():
                raise KernelError("reconcile evidence is required")
            if not verifier_id or not str(verifier_id).strip():
                raise KernelError(f"{normalized} reconciliation requires verifier")
            _assert_checkpoint_safe({"evidence_ref": evidence_ref, "verifier_id": verifier_id})
            self.kernel._begin()
            try:
                task = self.kernel._task(task_id)
                checkpoint = self._load_reconcile_checkpoint(task_id, checkpoint_id)
                if task["state"] != "RECONCILING":
                    raise InvalidTransition(f"{task['state']}->reconcile_outcome")
                idem = self.conn.execute(
                    "SELECT * FROM idempotency WHERE logical_key=?",
                    (checkpoint["idempotency_key"],),
                ).fetchone()
                if not idem:
                    raise KernelError("reconcile idempotency key not found")
                if idem["status"] != "CLAIMED":
                    raise KernelError("reconcile idempotency status is not CLAIMED")

                status = f"RECONCILED_{normalized}"
                event_type = f"RECONCILE_{normalized}"
                cur_idem_version = int(idem["version"]) if "version" in idem.keys() else 1
                cur_idem = self.conn.execute(
                    "UPDATE idempotency SET status=?,result_ref=?,version=version+1 WHERE logical_key=? AND status='CLAIMED' AND version=?",
                    (status, evidence_ref, checkpoint["idempotency_key"], cur_idem_version),
                )
                if cur_idem.rowcount != 1:
                    raise OptimisticLockError(
                        f"concurrency conflict reconciling idempotency key {checkpoint['idempotency_key']}",
                        table="idempotency",
                        entity_id=checkpoint["idempotency_key"],
                        expected_version=cur_idem_version,
                    )
                cur = self.conn.execute(
                    "UPDATE tasks SET state='HUMAN_REVIEW',version=version+1,active_lease_id=NULL,active_fencing_token=0,updated_at=? WHERE task_id=? AND version=?",
                    (now_iso(), task_id, task["version"]),
                )
                if cur.rowcount != 1:
                    raise StaleLease(f"concurrency conflict reconciling task {task_id}")
                active_leases = self.conn.execute(
                    "SELECT lease_id FROM leases WHERE task_id=? AND released=0", (task_id,)
                ).fetchall()
                if active_leases:
                    self.conn.execute(
                        "UPDATE leases SET released=1,version=version+1 WHERE task_id=? AND released=0",
                        (task_id,),
                    )
                    for _ in active_leases:
                        self.conn.execute(
                            "UPDATE queue_accounts SET active=CASE WHEN active>0 THEN active-1 ELSE 0 END,version=version+1 WHERE owner=?",
                            (task["owner"],),
                        )
                if hasattr(self.kernel, "_bound_leases"):
                    self.kernel._bound_leases.pop(task_id, None)
                self.kernel._append_event(
                    task_id,
                    event_type,
                    task["state"],
                    "HUMAN_REVIEW",
                    str(verifier_id),
                    "reconcile_outcome_recorded",
                    {
                        "checkpoint_id": checkpoint_id,
                        "outcome": normalized,
                        "evidence_ref": evidence_ref,
                        "verifier_id": verifier_id,
                        "safe_to_retry": False,
                    },
                )
                self.kernel._commit()
                return self.kernel.get_task(task_id)
            except Exception:
                self.kernel._rollback()
                raise

        if outcome not in {"NOT_APPLIED", "APPLIED", "UNKNOWN"}:
            raise KernelError("invalid reconcile outcome")
        if not evidence_ref or not str(evidence_ref).strip():
            raise KernelError("reconcile evidence is required")
        if outcome in {"APPLIED", "UNKNOWN"} and (not verifier_id):
            raise KernelError("reconcile verifier is required")
        _assert_checkpoint_safe({"evidence_ref": evidence_ref, "verifier_id": verifier_id})
        self.kernel._begin()
        try:
            task = self.kernel._task(task_id)
            checkpoint = self._load_reconcile_checkpoint(task_id, checkpoint_id)
            if task["state"] != "RECONCILING":
                raise InvalidTransition(f"{task['state']}->reconcile_outcome")
            idem = self.conn.execute(
                "SELECT * FROM idempotency WHERE logical_key=?",
                (checkpoint["idempotency_key"],),
            ).fetchone()
            if not idem:
                raise KernelError("reconcile idempotency key not found")
            old_state = task["state"]
            idem_version = int(idem["version"]) if "version" in idem.keys() else 1
            if outcome == "NOT_APPLIED":
                if idem["status"] != "CLAIMED":
                    raise KernelError("reconcile idempotency status is not CLAIMED")
                cur_idem = self.conn.execute(
                    "UPDATE idempotency SET status='RETRYABLE',result_ref=?,version=version+1 WHERE logical_key=? AND status='CLAIMED' AND version=?",
                    (evidence_ref, checkpoint["idempotency_key"], idem_version),
                )
                if cur_idem.rowcount != 1:
                    raise OptimisticLockError(
                        f"concurrency conflict reconciling idempotency key {checkpoint['idempotency_key']}",
                        table="idempotency",
                        entity_id=checkpoint["idempotency_key"],
                        expected_version=idem_version,
                    )
                next_state = "QUEUED"
                event_type = "RECONCILE_NOT_APPLIED"
            elif outcome == "APPLIED":
                cur_idem = self.conn.execute(
                    "UPDATE idempotency SET status='RECONCILED_APPLIED',result_ref=?,version=version+1 WHERE logical_key=? AND status='CLAIMED' AND version=?",
                    (evidence_ref, checkpoint["idempotency_key"], idem_version),
                )
                if cur_idem.rowcount != 1:
                    raise OptimisticLockError(
                        f"concurrency conflict reconciling idempotency key {checkpoint['idempotency_key']}",
                        table="idempotency",
                        entity_id=checkpoint["idempotency_key"],
                        expected_version=idem_version,
                    )
                if getattr(self.kernel, "autonomous_mode", False) and evidence_ref:
                    next_state = "QUEUED"
                    event_type = "RECONCILE_APPLIED_AUTONOMOUS"
                else:
                    next_state = "HUMAN_REVIEW"
                    event_type = "RECONCILE_APPLIED"
            else:
                cur_idem = self.conn.execute(
                    "UPDATE idempotency SET status='RECONCILED_UNKNOWN',result_ref=?,version=version+1 WHERE logical_key=? AND status='CLAIMED' AND version=?",
                    (evidence_ref, checkpoint["idempotency_key"], idem_version),
                )
                if cur_idem.rowcount != 1:
                    raise OptimisticLockError(
                        f"concurrency conflict reconciling idempotency key {checkpoint['idempotency_key']}",
                        table="idempotency",
                        entity_id=checkpoint["idempotency_key"],
                        expected_version=idem_version,
                    )
                next_state = "HUMAN_REVIEW"
                event_type = "RECONCILE_UNKNOWN"
            cur = self.conn.execute(
                "UPDATE tasks SET state=?,version=version+1,active_lease_id=NULL,active_fencing_token=0,updated_at=? WHERE task_id=? AND version=?",
                (next_state, now_iso(), task_id, task["version"]),
            )
            if cur.rowcount != 1:
                raise StaleLease(f"concurrency conflict reconciling task {task_id}")
            active_leases = self.conn.execute(
                "SELECT lease_id FROM leases WHERE task_id=? AND released=0", (task_id,)
            ).fetchall()
            if active_leases:
                self.conn.execute(
                    "UPDATE leases SET released=1,version=version+1 WHERE task_id=? AND released=0",
                    (task_id,),
                )
                for _ in active_leases:
                    self.conn.execute(
                        "UPDATE queue_accounts SET active=CASE WHEN active>0 THEN active-1 ELSE 0 END,version=version+1 WHERE owner=?",
                        (task["owner"],),
                    )
            if hasattr(self.kernel, "_bound_leases"):
                self.kernel._bound_leases.pop(task_id, None)
            self.kernel._append_event(
                task_id,
                event_type,
                old_state,
                next_state,
                verifier_id or "provider-state-reader",
                "reconcile_outcome_recorded",
                {
                    "checkpoint_id": checkpoint_id,
                    "outcome": outcome,
                    "evidence_ref": evidence_ref,
                    "verifier_id": verifier_id,
                },
            )
            self.kernel._commit()
            return self.kernel.get_task(task_id)
        except Exception:
            self.kernel._rollback()
            raise

    def auto_reconcile_orphans(
        self, actor: str = "kernel_watchdog", stale_seconds: float = 60.0, now: float | None = None
    ) -> list[str]:
        """Tự động rà soát các task bị mồ côi (chết do crash, mất kết nối) và đưa vào RECONCILING."""
        orphans: list[str] = []
        now_ts = time.time() if now is None else float(now)
        cutoff = int(now_ts - float(stale_seconds))
        try:
            self.kernel._begin()
            rows = self.conn.execute(
                "SELECT task_id, state FROM tasks "
                "WHERE state IN ('LEASED', 'RUNNING', 'WAITING_TOOL') "
                "AND CAST(strftime('%s', updated_at) AS INTEGER) < ?",
                (cutoff,),
            ).fetchall()
            for r in rows:
                tid = r["task_id"]
                lease = self.conn.execute(
                    "SELECT * FROM leases WHERE task_id=? AND released=0 ORDER BY fencing_token DESC LIMIT 1",
                    (tid,),
                ).fetchone()
                if lease is not None and float(lease["expires_at"]) > now_ts:
                    continue
                task = self.kernel._task(tid)
                decision = self.recovery_decision("LOST_RESPONSE", True, "UNKNOWN")
                plan = [("RECOVERING", "ORPHAN_TIMEOUT")]
                if decision.next_state == "RECONCILING":
                    plan.append(("RECONCILING", "AUTO_RECONCILE_INITIATED"))
                payload = {
                    "lease_id": lease["lease_id"] if lease is not None else None,
                    "heartbeat_at": float(lease["heartbeat_at"]) if lease is not None else None,
                    "expires_at": float(lease["expires_at"]) if lease is not None else None,
                    "watchdog_now": now_ts,
                    "stale_seconds": float(stale_seconds),
                }
                current = task["state"]
                cur_version = task["version"]
                moved = False
                for target, reason in plan:
                    if target not in ALLOWED_TRANSITIONS.get(current, set()):
                        break
                    cur = self.conn.execute(
                        "UPDATE tasks SET state=?,version=version+1,active_lease_id=NULL,active_fencing_token=0,updated_at=? WHERE task_id=? AND version=?",
                        (target, now_iso(), tid, cur_version),
                    )
                    if cur.rowcount != 1:
                        break
                    cur_version += 1
                    self.kernel._append_event(
                        tid, "STATE_TRANSITION", current, target, actor, reason, dict(payload)
                    )
                    current = target
                    moved = True
                if not moved:
                    continue
                active_leases = self.conn.execute(
                    "SELECT lease_id FROM leases WHERE task_id=? AND released=0", (tid,)
                ).fetchall()
                if active_leases:
                    self.conn.execute(
                        "UPDATE leases SET released=1,version=version+1 WHERE task_id=? AND released=0",
                        (tid,),
                    )
                    for _ in active_leases:
                        self.conn.execute(
                            "UPDATE queue_accounts SET active=CASE WHEN active>0 THEN active-1 ELSE 0 END,version=version+1 WHERE owner=?",
                            (task["owner"],),
                        )
                if hasattr(self.kernel, "_bound_leases"):
                    self.kernel._bound_leases.pop(tid, None)
                orphans.append(tid)
            self.kernel._commit()
        except Exception:
            self.kernel._rollback()
            raise
        return orphans

    def recover_on_boot(self, actor: str = "boot_recovery") -> dict[str, Any]:
        """[Cổng F — Event-Sourcing Crash Recovery] Máy tự replay journal."""
        report: dict[str, Any] = {"recovered": [], "corrupted": [], "left_as_is": []}
        self.kernel._system_authority = True
        try:
            rows = self.conn.execute("SELECT task_id, state FROM tasks").fetchall()
            for row in rows:
                task_id, state = (row["task_id"], row["state"])
                journal = self.kernel.verify_journal(task_id)
                if not journal["hash_chain_valid"]:
                    report["corrupted"].append({"task_id": task_id, "errors": journal["errors"][:5]})
                    continue
                if state in TERMINAL:
                    continue
                self.kernel.rebuild_projection(task_id)
                task_row = self.kernel._task(task_id)
                current = task_row["state"]
                active_leases = self.conn.execute(
                    "SELECT lease_id FROM leases WHERE task_id=? AND released=0", (task_id,)
                ).fetchall()
                if active_leases:
                    self.conn.execute(
                        "UPDATE leases SET released=1,version=version+1 WHERE task_id=? AND released=0",
                        (task_id,),
                    )
                    for _ in active_leases:
                        self.conn.execute(
                            "UPDATE queue_accounts SET active=CASE WHEN active>0 THEN active-1 ELSE 0 END,version=version+1 WHERE owner=?",
                            (task_row["owner"],),
                        )
                if current == "RUNNING" or current == "VERIFYING":
                    self.kernel.transition(task_id, "HUMAN_REVIEW", actor=actor, reason="boot_recovery_in_flight")
                    report["recovered"].append({"task_id": task_id, "from": current, "to": "HUMAN_REVIEW"})
                elif current in {"LEASED", "WAITING_TOOL"}:
                    self.kernel.transition(task_id, "RECOVERING", actor=actor, reason="boot_recovery_in_flight")
                    report["recovered"].append({"task_id": task_id, "from": current, "to": "RECOVERING"})
                elif current == "CHECKPOINTED":
                    self.kernel.transition(task_id, "QUEUED", actor=actor, reason="boot_recovery_resume")
                    report["recovered"].append({"task_id": task_id, "from": current, "to": "QUEUED"})
                else:
                    report["left_as_is"].append({"task_id": task_id, "state": current})
            return report
        finally:
            self.kernel._system_authority = False

    @staticmethod
    def recovery_decision(
        reason: str, action_dispatched: bool = False, side_effect_risk: str = "R0"
    ) -> RecoveryDecision:
        if action_dispatched or reason in {
            "TOOL_UNKNOWN_STATE",
            "LOST_RESPONSE",
            "WORKER_CRASH_AFTER_SUBMIT",
        }:
            return RecoveryDecision(
                "RECONCILE",
                "ACTION_DISPATCHED_WITHOUT_RESULT",
                False,
                ("provider_request_status", "read_only_state"),
                "RECONCILING",
                "human_review_if_unknown",
            )
        if (
            reason in {"PROVIDER_TIMEOUT", "DEPENDENCY_NOT_READY", "TRANSIENT_NETWORK"}
            and side_effect_risk in {"R0", "R1"}
        ):
            return RecoveryDecision(
                "RETRY", "TRANSIENT_FAILURE", True, (), "QUEUED", "bounded_backoff"
            )
        if reason in {"POLICY_DENIED", "INVALID_CAPABILITY", "CHECKPOINT_CORRUPT"}:
            return RecoveryDecision(
                "STOP", "NON_RETRYABLE_POLICY_OR_INTEGRITY_FAILURE", False, (), "FAILED", "none"
            )
        return RecoveryDecision(
            "REVIEW",
            "INSUFFICIENT_STATE_EVIDENCE",
            False,
            ("last_checkpoint", "event_journal"),
            "HUMAN_REVIEW",
            "human_required",
        )
