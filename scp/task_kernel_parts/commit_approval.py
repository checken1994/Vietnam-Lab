"""Commit and Approval engine for durable TaskKernel (ARC-01 GA B44).

Encapsulates:
- Task approval verification and commit (WAITING_APPROVAL -> READY)
- Autonomous human review resolution (HUMAN_REVIEW -> READY)
- Autonomous fail-closed task termination (* -> FAILED)
- Verifier receipt commitment (VERIFYING -> COMPLETED)
- Verified task completion commit (VERIFYING -> COMPLETED)
- Verified task failure & retry routing commit (* -> FAILED/RETRY_SCHEDULED/UNKNOWN)
- Task cancellation / kill & lease releasing (* -> CANCELLED/FAILED)
- Global kill epoch control
- Lease renewal, expiration sweep, and cooperative release transitions
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from typing import Any

from scp.core.capability_token import (
    InvalidTokenSignatureError,
    get_capability_secret,
    verify_token,
    verify_token_signature,
)
from scp.security.capability_epoch import parse_capability_token
from scp.task_kernel_parts.definitions import (
    ALLOWED_TRANSITIONS,
    TERMINAL,
    InvalidTransition,
    KernelError,
    OptimisticLockError,
    StaleLease,
    now_iso,
)

logger = logging.getLogger(__name__)


def verify_approval_authority(
    token: Any,
    task_id: str,
    secret: bytes,
    max_skew_seconds: float = 300.0,
) -> dict[str, Any]:
    """Verify an approval token or operator signature fail-closed (GAP-13).

    Returns a dict with verification metadata:
        {"token_type": str, "token_id": str, "actor": str, "scope": str, "signature": str}
    Raises InvalidTokenSignatureError, InvalidTransition, or PermissionError on any failure.
    """
    if token is None or token == "":
        raise InvalidTokenSignatureError("Approval token is missing or empty (GAP-13/FA-04)")
    if isinstance(token, str) and not token.strip():
        raise InvalidTokenSignatureError("Approval token is missing or empty (GAP-13/FA-04)")

    # Branch A: Compact string token (mint_token format "payload_b64.sig")
    if isinstance(token, str) and "." in token and not token.strip().startswith("{"):
        res = verify_token(token.strip(), required_scope="*")
        if not res.get("valid"):
            err_msg = res.get("error", "Invalid capability token")
            if "expired" in err_msg.lower():
                raise InvalidTransition(f"Capability token has expired: {err_msg}")
            raise InvalidTokenSignatureError(f"Invalid capability token: {err_msg}")
        payload = res.get("payload", {})
        scope = payload.get("scope", "")
        if scope not in {"approval:grant", f"approval:grant:{task_id}", "*"}:
            raise InvalidTransition(
                f"Capability token scope '{scope}' does not authorize 'approval:grant' for task '{task_id}'"
            )
        now_ts = time.time()
        iat = float(payload.get("iat", 0.0))
        if iat > now_ts + 60.0:
            raise InvalidTokenSignatureError("Token issue time is in the future")
        return {
            "token_type": "compact_mint_token",
            "token_id": str(payload.get("iat", "")),
            "actor": payload.get("iss", "unknown"),
            "scope": scope,
            "signature": token.strip().split(".", 1)[1],
        }

    # Branch B: CapabilityToken instance, dict, or JSON string with epoch
    cap_token = parse_capability_token(token)
    if cap_token is not None:
        if not cap_token.signature or not cap_token.signature.strip():
            raise InvalidTokenSignatureError("Capability token is unsigned (GAP-08/FA-04)")

        verify_token_signature(
            secret=secret,
            subject=cap_token.subject,
            epoch=cap_token.epoch,
            token_id=cap_token.token_id,
            issued_at=cap_token.issued_at,
            signature=cap_token.signature,
        )
        if cap_token.subject not in {"approval:grant", f"approval:grant:{task_id}", "*"}:
            raise InvalidTransition(
                f"CapabilityToken subject '{cap_token.subject}' does not authorize 'approval:grant' for task '{task_id}'"
            )
        now_ts = time.time()
        if cap_token.issued_at > now_ts + 60.0:
            raise InvalidTokenSignatureError("Capability token issued_at is in the future")
        return {
            "token_type": "capability_token_epoch",
            "token_id": cap_token.token_id,
            "actor": "capability_authority",
            "scope": cap_token.subject,
            "signature": cap_token.signature,
        }

    # Branch C: Operator Signature Dictionary
    if isinstance(token, dict) and "signature" in token:
        actor = str(token.get("actor") or token.get("operator", "")).strip()
        sig = str(token.get("signature", "")).strip()
        ts_val = token.get("timestamp")
        task_in_token = token.get("task_id")
        if task_in_token and str(task_in_token).strip() != task_id:
            raise InvalidTransition(
                f"Operator signature task_id '{task_in_token}' does not match '{task_id}'"
            )
        if not actor or not sig or ts_val is None:
            raise InvalidTokenSignatureError("Malformed operator signature structure")
        try:
            timestamp = float(ts_val)
        except (TypeError, ValueError):
            raise InvalidTokenSignatureError("Invalid timestamp in operator signature")
        now_ts = time.time()
        if now_ts - timestamp > max_skew_seconds:
            raise InvalidTransition("Operator approval signature has expired")
        if timestamp > now_ts + 60.0:
            raise InvalidTokenSignatureError("Operator approval timestamp is in the future")

        canonical = f"operator_approval:{task_id}:{actor}:{timestamp:.6f}".encode()
        expected_sig = hmac.new(secret, canonical, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected_sig):
            raise InvalidTokenSignatureError("Operator approval signature verification failed")
        return {
            "token_type": "operator_signature",
            "token_id": f"op_{actor}_{int(timestamp)}",
            "actor": actor,
            "scope": "approval:grant",
            "signature": sig,
        }

    raise InvalidTokenSignatureError("Unsupported or malformed approval token format")


class CommitApprovalEngine:
    """Commit & Approval engine for durable TaskKernel (ARC-01 GA B44).

    Encapsulates all task commit logic, approval authority gating, lease renewal,
    lease expiration sweeps, cooperative release, and terminal cancellation.
    """

    _RENEWABLE_LEASE_STATES = frozenset({"LEASED", "RUNNING", "WAITING_TOOL", "VERIFYING"})

    def __init__(self, kernel: Any = None) -> None:
        self.kernel = kernel

    def _assert_bound_kernel(self) -> None:
        if self.kernel is None:
            raise RuntimeError("CommitApprovalEngine has no bound TaskKernel instance")

    @property
    def conn(self) -> Any:
        self._assert_bound_kernel()
        return self.kernel.conn

    def commit_verification_result(
        self,
        task_id: str,
        lease_id: str,
        verification_result: Any,
    ) -> dict[str, Any]:
        """Commit an independently signed verifier receipt fail-closed."""
        from scp.core.verifier_receipt import (
            InvalidReceiptSignatureError,
            VerifierReceipt,
            verify_verifier_receipt,
        )

        self._assert_bound_kernel()
        if not task_id or not str(task_id).strip():
            raise KernelError("task_id is required")
        if not lease_id or not str(lease_id).strip():
            raise KernelError("lease_id is required")

        self.kernel._assert_lease(lease_id, task_id)
        is_system = getattr(self.kernel, "_system_authority", False)
        bound = getattr(self.kernel, "_bound_leases", {}).get(task_id)
        if not is_system:
            if not bound:
                raise StaleLease(f"kernel instance does not possess active lease authority to complete task {task_id}")
            if bound != lease_id:
                raise StaleLease(f"caller lease {lease_id} does not match bound instance lease {bound}")

        task = self.kernel._task(task_id)
        if task["state"] != "VERIFYING":
            raise InvalidTransition(f"{task['state']}->COMPLETED")

        if not isinstance(verification_result, (VerifierReceipt, dict)):
            raise InvalidReceiptSignatureError("verification_result must be a VerifierReceipt or dict (R3/FA-04)")

        verify_verifier_receipt(verification_result, task_id=task_id)
        return self.commit_completed(task_id, lease_id, receipt=verification_result)

    def commit_completed(
        self,
        task_id: str,
        lease_id: str,
        verifier_verdict: Any = "VERIFIED",
        evidence_ref: str | None = None,
        *,
        receipt: Any = None,
    ) -> dict[str, Any]:
        """Atomically transition a task from VERIFYING to COMPLETED with verified receipt."""
        from scp.core.verifier_receipt import (
            VerifierReceipt,
            verify_verifier_receipt,
        )

        self._assert_bound_kernel()
        if receipt is None and isinstance(verifier_verdict, (VerifierReceipt, dict)):
            receipt = verifier_verdict

        if receipt is not None:
            verify_verifier_receipt(receipt, task_id=task_id)
            if hasattr(receipt, "verifier_id"):
                actual_verifier_id = receipt.verifier_id
                actual_evidence_ref = receipt.evidence_ref
                actual_verdict = receipt.verdict
                sig = receipt.signature
                issued_at = receipt.issued_at
            else:
                actual_verifier_id = str(receipt.get("verifier_id", "")).strip()
                actual_evidence_ref = str(receipt.get("evidence_ref", "")).strip()
                actual_verdict = str(receipt.get("verdict", "")).strip()
                sig = str(receipt.get("signature", "")).strip()
                issued_at = float(receipt.get("issued_at", 0.0))
            signature_digest = f"sha256:{sig[:16]}..." if sig else None
        else:
            if verifier_verdict != "VERIFIED" or not evidence_ref:
                raise KernelError("completion requires independent VERIFIED verdict and evidence")
            actual_verifier_id = "verifier"
            actual_evidence_ref = str(evidence_ref).strip()
            actual_verdict = "VERIFIED"
            signature_digest = None
            issued_at = None

        self.kernel._begin()
        try:
            self.kernel._assert_lease(lease_id, task_id)
            task = self.kernel._task(task_id)
            if task["state"] != "VERIFYING":
                raise InvalidTransition(f"{task['state']}->COMPLETED")
            if task["active_lease_id"] != lease_id:
                raise StaleLease(f"completion lease {lease_id} does not match active task lease {task['active_lease_id']}")
            is_system = getattr(self.kernel, "_system_authority", False)
            bound = getattr(self.kernel, "_bound_leases", {}).get(task_id)
            if not is_system:
                if not bound:
                    raise StaleLease(f"kernel instance does not possess active lease authority to complete task {task_id}")
                if bound != lease_id:
                    raise StaleLease(f"caller lease {lease_id} does not match bound instance lease {bound}")
            old = task["state"]
            cur = self.conn.execute(
                "UPDATE tasks SET state='COMPLETED',version=version+1,active_lease_id=NULL,active_fencing_token=0,updated_at=? WHERE task_id=? AND version=?",
                (now_iso(), task_id, task["version"]),
            )
            if cur.rowcount != 1:
                raise StaleLease(f"concurrency conflict completing task {task_id}")

            event_payload = {
                "evidence_ref": actual_evidence_ref,
                "verifier_verdict": actual_verdict,
                "lease_id": lease_id,
                "verifier_id": actual_verifier_id,
            }
            if signature_digest:
                event_payload["signature_digest"] = signature_digest
            if issued_at is not None:
                event_payload["issued_at"] = issued_at

            self.kernel._append_event(
                task_id,
                "TASK_COMPLETED",
                old,
                "COMPLETED",
                actual_verifier_id,
                "postcondition_verified",
                event_payload,
            )
            self.conn.execute(
                "UPDATE leases SET released=1,version=version+1 WHERE lease_id=? AND released=0",
                (lease_id,),
            )
            self.conn.execute(
                "UPDATE queue_accounts SET active=CASE WHEN active>0 THEN active-1 ELSE 0 END,version=version+1 WHERE owner=?",
                (task["owner"],),
            )
            if hasattr(self.kernel, "_bound_leases"):
                self.kernel._bound_leases.pop(task_id, None)
            self.kernel._commit()
            return self.kernel.get_task(task_id)
        except Exception:
            self.kernel._rollback()
            raise

    def commit_approval(
        self,
        task_id: str,
        approval_token: Any,
        actor: str = "operator",
        details: dict[str, Any] | None = None,
        max_skew_seconds: float = 300.0,
        expected_version: int | None = None,
    ) -> dict[str, Any]:
        """Atomically commit an authenticated approval to transition WAITING_APPROVAL -> READY."""
        self._assert_bound_kernel()
        if not task_id or not str(task_id).strip():
            raise KernelError("task_id is required")
        if approval_token is None or approval_token == "":
            raise InvalidTokenSignatureError("approval_token is required")
        if isinstance(approval_token, str) and not approval_token.strip():
            raise InvalidTokenSignatureError("approval_token is required")
        if not actor or not str(actor).strip():
            raise KernelError("actor is required")

        self.kernel._begin()
        try:
            self.kernel._assert_not_killed()
            task = self.kernel._task(task_id)
            current_state = task["state"]

            if current_state in TERMINAL:
                raise InvalidTransition("terminal task is immutable")
            if current_state != "WAITING_APPROVAL":
                raise InvalidTransition(
                    f"task {task_id} in state '{current_state}' cannot be approved; task must be in WAITING_APPROVAL"
                )

            cur_version = int(task["version"])
            if expected_version is not None and cur_version != expected_version:
                raise OptimisticLockError(
                    f"concurrency conflict approving task {task_id}: expected version {expected_version}, found {cur_version}",
                    table="tasks",
                    entity_id=task_id,
                    expected_version=expected_version,
                )

            secret = get_capability_secret()
            verification_meta = verify_approval_authority(
                approval_token, task_id, secret, max_skew_seconds=max_skew_seconds
            )

            now_str = now_iso()
            cur = self.conn.execute(
                "UPDATE tasks SET state='READY', version=version+1, updated_at=? WHERE task_id=? AND version=? AND state='WAITING_APPROVAL'",
                (now_str, task_id, cur_version),
            )
            if cur.rowcount != 1:
                raise OptimisticLockError(
                    f"concurrency conflict committing approval on task {task_id}: expected version {cur_version}",
                    table="tasks",
                    entity_id=task_id,
                    expected_version=cur_version,
                )

            event_payload = {
                "token_type": verification_meta["token_type"],
                "token_id": verification_meta["token_id"],
                "scope": verification_meta["scope"],
                "signature_digest": (
                    verification_meta["signature"][:16] + "..."
                    if verification_meta["signature"]
                    else ""
                ),
                "actor": actor,
                "details": details or {},
            }
            self.kernel._append_event(
                task_id,
                "TASK_APPROVED",
                "WAITING_APPROVAL",
                "READY",
                actor,
                "approval_granted",
                event_payload,
            )

            self.kernel._commit()
            return self.kernel.get_task(task_id)
        except Exception:
            self.kernel._rollback()
            raise

    def auto_resolve_human_review(
        self,
        task_id: str,
        reason: str = "autonomous_verification_passed",
    ) -> dict[str, Any]:
        """Atomically transition a task from HUMAN_REVIEW to READY in autonomous mode."""
        self._assert_bound_kernel()
        if not getattr(self.kernel, "autonomous_mode", False):
            raise InvalidTransition("auto_resolve_human_review can only be called when autonomous_mode is enabled")
        if not task_id or not str(task_id).strip():
            raise KernelError("task_id is required")
        self.kernel._begin()
        try:
            task = self.kernel._task(task_id)
            if task["state"] != "HUMAN_REVIEW":
                raise InvalidTransition(
                    f"cannot auto-resolve task {task_id} in state {task['state']}; expected HUMAN_REVIEW"
                )
            cur_version = int(task["version"])
            cur = self.conn.execute(
                "UPDATE tasks SET state='READY',version=version+1,active_lease_id=NULL,active_fencing_token=0,updated_at=? WHERE task_id=? AND version=?",
                (now_iso(), task_id, cur_version),
            )
            if cur.rowcount != 1:
                raise OptimisticLockError(
                    f"concurrency conflict auto-resolving human review on task {task_id}: expected version {cur_version}",
                    table="tasks",
                    entity_id=task_id,
                    expected_version=cur_version,
                )
            self.kernel._append_event(
                task_id,
                "AUTONOMOUS_HUMAN_REVIEW_RESOLVED",
                "HUMAN_REVIEW",
                "READY",
                "kernel:autonomous",
                reason,
                {"task_id": task_id, "reason": reason, "resolved_at": now_iso()},
            )
            self.kernel._commit()
            return self.kernel.get_task(task_id)
        except Exception:
            self.kernel._rollback()
            raise

    def fail_task_fail_closed(
        self,
        task_id: str,
        reason: str = "autonomous_fail_closed",
        error_payload: dict[str, Any] | None = None,
        actor: str = "kernel:autonomous",
    ) -> dict[str, Any]:
        """Atomically transition a non-terminal task to FAILED in autonomous mode (fail-closed)."""
        self._assert_bound_kernel()
        if not task_id or not str(task_id).strip():
            raise KernelError("task_id is required")
        self.kernel._begin()
        try:
            task = self.kernel._task(task_id)
            old_state = task["state"]
            if old_state in TERMINAL:
                self.kernel._rollback()
                return task
            if "FAILED" not in ALLOWED_TRANSITIONS.get(old_state, set()):
                raise InvalidTransition(f"cannot fail-closed from {old_state}->FAILED")
            cur_version = int(task["version"])
            cur = self.conn.execute(
                "UPDATE tasks SET state='FAILED',error=?,version=version+1,active_lease_id=NULL,active_fencing_token=0,updated_at=? WHERE task_id=? AND version=?",
                (reason, now_iso(), task_id, cur_version),
            )
            if cur.rowcount != 1:
                raise OptimisticLockError(
                    f"concurrency conflict failing task {task_id}",
                    table="tasks",
                    entity_id=task_id,
                    expected_version=cur_version,
                )
            active_leases = self.conn.execute(
                "SELECT lease_id FROM leases WHERE task_id=? AND released=0",
                (task_id,),
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
                "TASK_FAILED",
                old_state,
                "FAILED",
                actor,
                reason,
                error_payload or {"error": reason},
            )
            self.kernel._commit()
            return self.kernel.get_task(task_id)
        except Exception:
            self.kernel._rollback()
            raise

    def commit_failed(
        self,
        task_id: str,
        lease_id: str,
        actor: str,
        failure_classification: str,
        indictment_ref: str,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Commit an authenticated, verified failure or route to retry/recovery."""
        self._assert_bound_kernel()
        if not task_id or not str(task_id).strip():
            raise KernelError("task_id is required")
        if not lease_id or not str(lease_id).strip():
            raise StaleLease("lease_id is required")
        if not actor or not str(actor).strip():
            raise KernelError("actor is required")
        if not failure_classification or not str(failure_classification).strip():
            raise KernelError("failure_classification is required")
        if not indictment_ref or not str(indictment_ref).strip():
            raise KernelError("indictment_ref is required; failure commitment requires verifiable failure evidence")

        self.kernel._begin()
        try:
            task = self.kernel._task(task_id)
            _lease = self.kernel._assert_lease(lease_id, task_id, actor=actor)  # noqa: F841

            old_state = task["state"]
            if old_state in TERMINAL:
                raise InvalidTransition("terminal task is immutable")
            if old_state not in {"RUNNING", "WAITING_TOOL", "VERIFYING", "LEASED", "CHECKPOINTED", "UNKNOWN"}:
                raise InvalidTransition(f"{old_state}->commit_failed")
            if task["active_lease_id"] != lease_id:
                raise StaleLease(f"failure lease {lease_id} does not match active task lease {task['active_lease_id']}")

            is_system = getattr(self.kernel, "_system_authority", False)
            bound = getattr(self.kernel, "_bound_leases", {}).get(task_id)
            if not is_system:
                if not bound:
                    raise StaleLease(f"kernel instance does not possess active lease authority to fail task {task_id}")
                if bound != lease_id:
                    raise StaleLease(f"caller lease {lease_id} does not match bound instance lease {bound}")

            cur_version = int(task["version"])
            current_attempts = int(task["attempts"]) if ("attempts" in task.keys() and task["attempts"] is not None) else 0
            max_attempts = int(task["max_attempts"]) if ("max_attempts" in task.keys() and task["max_attempts"] is not None) else 3
            new_attempts = current_attempts + 1

            classification_upper = failure_classification.strip().upper()
            retryable_classes = {"RETRYABLE", "TRANSIENT", "TIMEOUT", "NETWORK_ERROR", "TEMPORARY"}
            uncertain_classes = {"UNKNOWN", "UNCERTAIN", "LOST_RESPONSE", "CRASH_AFTER_SUBMIT"}

            if classification_upper in uncertain_classes:
                target_state = "UNKNOWN"
                event_type = "TASK_UNKNOWN_STATE"
                reason = f"uncertain_state:{classification_upper.lower()}"
            elif (classification_upper in retryable_classes) and (new_attempts < max_attempts):
                target_state = "RETRY_SCHEDULED"
                event_type = "TASK_RETRY_SCHEDULED"
                reason = f"retryable_failure:{classification_upper.lower()}"
            else:
                target_state = "FAILED"
                event_type = "TASK_FAILED"
                reason = f"terminal_failure:{classification_upper.lower()}"

            planned_target = target_state
            target_state, reason = self.kernel._legal_or_nearest(old_state, target_state, reason)
            if target_state == "FAILED" and event_type != "TASK_FAILED":
                event_type = "TASK_FAILED"

            error_payload = {
                "classification": classification_upper,
                "indictment_ref": indictment_ref,
                "details": details or {},
                "attempts": new_attempts,
                "max_attempts": max_attempts,
                "planned_retry": planned_target if planned_target in {"RETRY_SCHEDULED", "UNKNOWN"} else None,
            }
            error_json = json.dumps(error_payload, ensure_ascii=False, sort_keys=True)

            cur = self.conn.execute(
                "UPDATE tasks SET state=?,attempts=?,error=?,version=version+1,active_lease_id=NULL,active_fencing_token=0,updated_at=? WHERE task_id=? AND version=?",
                (target_state, new_attempts, error_json, now_iso(), task_id, cur_version),
            )
            if cur.rowcount != 1:
                raise OptimisticLockError(
                    f"concurrency conflict failing task {task_id}: expected version {cur_version}",
                    table="tasks",
                    entity_id=task_id,
                    expected_version=cur_version,
                )

            event_payload = {
                "lease_id": lease_id,
                "actor": actor,
                "failure_classification": classification_upper,
                "indictment_ref": indictment_ref,
                "details": details or {},
                "attempts": new_attempts,
                "max_attempts": max_attempts,
                "planned_retry": planned_target if planned_target in {"RETRY_SCHEDULED", "UNKNOWN"} else None,
            }
            self.kernel._append_event(
                task_id,
                event_type,
                old_state,
                target_state,
                actor,
                reason,
                payload=event_payload,
            )

            self.conn.execute(
                "UPDATE leases SET released=1,version=version+1 WHERE lease_id=? AND released=0",
                (lease_id,),
            )
            self.conn.execute(
                "UPDATE queue_accounts SET active=CASE WHEN active>0 THEN active-1 ELSE 0 END,version=version+1 WHERE owner=?",
                (task["owner"],),
            )
            if hasattr(self.kernel, "_bound_leases"):
                self.kernel._bound_leases.pop(task_id, None)

            self.kernel._commit()
            return self.kernel.get_task(task_id)
        except Exception:
            self.kernel._rollback()
            raise

    def set_task_kill(self, task_id: str, actor: str = "operator") -> dict[str, Any]:
        """Cancel a task with lease release and queue decrement (fail-closed)."""
        self._assert_bound_kernel()
        self.kernel._begin()
        try:
            task = self.kernel._task(task_id)
            if task["state"] in TERMINAL:
                raise InvalidTransition("terminal task is immutable")
            active_leases = self.conn.execute(
                "SELECT lease_id FROM leases WHERE task_id=? AND released=0",
                (task_id,),
            ).fetchall()
            kill_target, kill_reason = self.kernel._legal_or_nearest(
                task["state"], "CANCELLED", "task_kill"
            )
            cur = self.conn.execute(
                "UPDATE tasks SET state=?,version=version+1,active_lease_id=NULL,active_fencing_token=0,updated_at=? WHERE task_id=? AND version=?",
                (kill_target, now_iso(), task_id, task["version"]),
            )
            if cur.rowcount != 1:
                raise StaleLease(f"concurrency conflict cancelling task {task_id}")
            self.conn.execute(
                "UPDATE leases SET released=1,version=version+1 WHERE task_id=? AND released=0",
                (task_id,),
            )
            for _ in active_leases:
                self.conn.execute(
                    "UPDATE queue_accounts SET active=CASE WHEN active>0 THEN active-1 ELSE 0 END,version=version+1 WHERE owner=?",
                    (task["owner"],),
                )
            self.kernel._append_event(
                task_id,
                "TASK_KILLED",
                task["state"],
                kill_target,
                actor,
                kill_reason,
                {},
            )
            if hasattr(self.kernel, "_bound_leases"):
                self.kernel._bound_leases.pop(task_id, None)
            self.kernel._commit()
            return self.kernel.get_task(task_id)
        except Exception:
            self.kernel._rollback()
            raise

    def cancel(self, task_id: str, actor: str = "operator") -> dict[str, Any]:
        """Cancel a task atomically with lease release and queue decrement (alias of set_task_kill)."""
        return self.set_task_kill(task_id, actor=actor)

    def set_global_kill(self, active: bool, actor: str = "operator") -> int:
        """Toggle global kill switch and bump global kill epoch."""
        self._assert_bound_kernel()
        self.kernel._begin()
        try:
            c = self.kernel._control()
            epoch = int(c["global_kill_epoch"]) + (1 if active else 0)
            self.conn.execute(
                "UPDATE control SET global_kill=?,global_kill_epoch=? WHERE id=1",
                (1 if active else 0, epoch),
            )
            self.kernel._append_event(
                "__global__",
                "GLOBAL_KILL_ON" if active else "GLOBAL_KILL_OFF",
                None,
                None,
                actor,
                "operator_toggle",
                {"epoch": epoch},
            )
            self.kernel._commit()
            return epoch
        except Exception:
            self.kernel._rollback()
            raise

    def renew_lease(
        self,
        task_id: str,
        lease_id: str,
        fencing_token: int,
        ttl_seconds: float | None = None,
    ) -> bool:
        """Expiry-only lease renewal for long-running holders."""
        self._assert_bound_kernel()
        if not task_id or not lease_id:
            return False
        try:
            presented_token = int(fencing_token)
        except (TypeError, ValueError):
            return False
        if ttl_seconds is not None:
            try:
                presented_ttl = float(ttl_seconds)
            except (TypeError, ValueError):
                return False
            if presented_ttl <= 0:
                return False
        else:
            presented_ttl = None
        self.kernel._begin()
        try:
            lease = self.conn.execute("SELECT * FROM leases WHERE lease_id=?", (lease_id,)).fetchone()
            if lease is None or str(lease["task_id"]) != str(task_id):
                self.kernel._commit()
                return False
            now = time.time()
            if int(lease["released"]) != 0 or float(lease["expires_at"]) <= now:
                self.kernel._commit()
                return False
            if int(lease["fencing_token"]) != presented_token:
                self.kernel._commit()
                return False
            latest = self.conn.execute(
                "SELECT COALESCE(MAX(fencing_token), 0) AS n FROM leases WHERE task_id=?",
                (task_id,),
            ).fetchone()["n"]
            if presented_token != int(latest):
                self.kernel._commit()
                return False
            control = self.kernel._control()
            if int(control["global_kill"]) != 0 or int(lease["global_kill_epoch"]) != int(control["global_kill_epoch"]):
                self.kernel._commit()
                return False
            task = self.conn.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            if task is None:
                self.kernel._commit()
                return False
            if (
                (task["active_lease_id"] is None or str(task["active_lease_id"]) != str(lease_id))
                or str(task["state"]) not in self._RENEWABLE_LEASE_STATES
            ):
                self.kernel._commit()
                return False
            extend = (
                presented_ttl
                if presented_ttl is not None
                else (float(lease["expires_at"]) - float(lease["issued_at"]))
            )
            if extend <= 0:
                self.kernel._commit()
                return False
            expires = now + extend
            lease_version = int(lease["version"]) if "version" in lease.keys() else 1
            cur = self.conn.execute(
                "UPDATE leases SET heartbeat_at=?,expires_at=?,version=version+1 WHERE lease_id=? AND released=0 AND version=?",
                (now, expires, lease_id, lease_version),
            )
            if cur.rowcount != 1:
                self.kernel._commit()
                return False
            self.kernel._commit()
            return True
        except Exception:
            self.kernel._rollback()
            raise

    def expire_leases(self, now: float | None = None) -> list[str]:
        """Sweep expired leases and transition tasks into recovery/reclaim."""
        self._assert_bound_kernel()
        now = now or time.time()
        expired: list[str] = []
        candidates = self.conn.execute(
            "SELECT * FROM leases WHERE released=0 AND expires_at<=?", (now,)
        ).fetchall()

        for lease in candidates:
            self.kernel._begin()
            try:
                task = self.kernel._task(lease["task_id"])
                cur_state = task["state"]
                is_active_lease = (task["active_lease_id"] == lease["lease_id"])
                if is_active_lease:
                    if cur_state in {"LEASED", "RUNNING", "WAITING_TOOL"}:
                        old = cur_state
                        cur = self.conn.execute(
                            "UPDATE tasks SET state='RECOVERING',version=version+1,active_lease_id=NULL,active_fencing_token=0,updated_at=? WHERE task_id=? AND version=?",
                            (now_iso(), task["task_id"], task["version"]),
                        )
                        if cur.rowcount == 0:
                            raise OptimisticLockError(
                                f"Task {task['task_id']} modified concurrently",
                                table="tasks",
                                entity_id=task["task_id"],
                            )
                        self.kernel._append_event(
                            task["task_id"],
                            "LEASE_EXPIRED",
                            old,
                            "RECOVERING",
                            "kernel",
                            "heartbeat_expired",
                            {"lease_id": lease["lease_id"]},
                        )
                    elif cur_state == "VERIFYING":
                        if getattr(self.kernel, "autonomous_mode", False):
                            cur = self.conn.execute(
                                "UPDATE tasks SET state='FAILED',error=?,version=version+1,active_lease_id=NULL,active_fencing_token=0,updated_at=? WHERE task_id=? AND version=?",
                                ("verification_lease_expired", now_iso(), task["task_id"], task["version"]),
                            )
                            if cur.rowcount == 0:
                                raise OptimisticLockError(
                                    f"Task {task['task_id']} modified concurrently",
                                    table="tasks",
                                    entity_id=task["task_id"],
                                )
                            self.kernel._append_event(
                                task["task_id"],
                                "LEASE_EXPIRED",
                                cur_state,
                                "FAILED",
                                "kernel:autonomous",
                                "heartbeat_expired_autonomous",
                                {"lease_id": lease["lease_id"], "autonomous": True},
                            )
                        else:
                            cur = self.conn.execute(
                                "UPDATE tasks SET state='HUMAN_REVIEW',version=version+1,active_lease_id=NULL,active_fencing_token=0,updated_at=? WHERE task_id=? AND version=?",
                                (now_iso(), task["task_id"], task["version"]),
                            )
                            if cur.rowcount == 0:
                                raise OptimisticLockError(
                                    f"Task {task['task_id']} modified concurrently",
                                    table="tasks",
                                    entity_id=task["task_id"],
                                )
                            self.kernel._append_event(
                                task["task_id"],
                                "LEASE_EXPIRED",
                                cur_state,
                                "HUMAN_REVIEW",
                                "kernel",
                                "heartbeat_expired",
                                {"lease_id": lease["lease_id"]},
                            )
                    elif cur_state == "CHECKPOINTED":
                        cur = self.conn.execute(
                            "UPDATE tasks SET state='QUEUED',version=version+1,active_lease_id=NULL,active_fencing_token=0,updated_at=? WHERE task_id=? AND version=?",
                            (now_iso(), task["task_id"], task["version"]),
                        )
                        if cur.rowcount == 0:
                            raise OptimisticLockError(
                                f"Task {task['task_id']} modified concurrently",
                                table="tasks",
                                entity_id=task["task_id"],
                            )
                        self.kernel._append_event(
                            task["task_id"],
                            "LEASE_EXPIRED",
                            cur_state,
                            "QUEUED",
                            "kernel",
                            "heartbeat_expired",
                            {"lease_id": lease["lease_id"]},
                        )
                    else:
                        cur = self.conn.execute(
                            "UPDATE tasks SET active_lease_id=NULL,active_fencing_token=0,version=version+1,updated_at=? WHERE task_id=? AND version=?",
                            (now_iso(), task["task_id"], task["version"]),
                        )
                        if cur.rowcount == 0:
                            raise OptimisticLockError(
                                f"Task {task['task_id']} modified concurrently",
                                table="tasks",
                                entity_id=task["task_id"],
                            )
                else:
                    logger.info(
                        "expire_leases: lease %s expired for task %s, but active_lease_id is %s; preserving active task",
                        lease["lease_id"],
                        task["task_id"],
                        task["active_lease_id"],
                    )

                cur_lease = self.conn.execute(
                    "UPDATE leases SET released=1,version=version+1 WHERE lease_id=? AND released=0",
                    (lease["lease_id"],),
                )
                if cur_lease.rowcount == 0:
                    raise OptimisticLockError(
                        f"Lease {lease['lease_id']} modified concurrently or already released",
                        table="leases",
                        entity_id=lease["lease_id"],
                    )

                owner = self.kernel._task(lease["task_id"])["owner"]
                cur_acc = self.conn.execute(
                    "UPDATE queue_accounts SET active=CASE WHEN active>0 THEN active-1 ELSE 0 END,version=version+1 WHERE owner=?",
                    (owner,),
                )
                if cur_acc.rowcount == 0:
                    raise OptimisticLockError(
                        f"Queue account for owner {owner} modified concurrently or not found",
                        table="queue_accounts",
                        entity_id=owner,
                    )

                if is_active_lease and hasattr(self.kernel, "_bound_leases"):
                    self.kernel._bound_leases.pop(lease["task_id"], None)

                self.kernel._commit()
                expired.append(lease["lease_id"])
            except OptimisticLockError as ole:
                self.kernel._rollback()
                logger.warning(
                    "OCC conflict during lease expiration for lease %s: %s",
                    lease["lease_id"],
                    ole,
                )
            except Exception:
                self.kernel._rollback()
                raise

        return expired

    def release(
        self,
        task_id: str,
        lease_id: str,
        expected_version: int | None = None,
    ) -> None:
        """Cooperative lease release by active worker."""
        self._assert_bound_kernel()
        self.kernel._begin()
        try:
            self.kernel._assert_lease(lease_id, task_id)
            task = self.kernel._task(task_id)
            if task["active_lease_id"] != lease_id:
                raise StaleLease(f"release lease {lease_id} does not match active task lease {task['active_lease_id']}")
            is_system = getattr(self.kernel, "_system_authority", False)
            bound = getattr(self.kernel, "_bound_leases", {}).get(task_id)
            if not is_system:
                if not bound:
                    raise StaleLease(f"kernel instance does not possess active lease authority for task {task_id}")
                if bound != lease_id:
                    raise StaleLease(f"caller lease {lease_id} does not match bound instance lease {bound}")
            lease = self.kernel._lease(lease_id)
            current_lease_version = int(lease["version"]) if "version" in lease.keys() else 1
            if expected_version is not None and current_lease_version != expected_version:
                raise OptimisticLockError(
                    f"concurrency conflict releasing lease {lease_id}: expected version {expected_version}, found {current_lease_version}",
                    table="leases",
                    entity_id=lease_id,
                    expected_version=expected_version,
                )
            target_version = expected_version if expected_version is not None else current_lease_version
            cur = self.conn.execute(
                "UPDATE leases SET released=1,version=version+1 WHERE lease_id=? AND released=0 AND version=?",
                (lease_id, target_version),
            )
            if cur.rowcount != 1:
                raise OptimisticLockError(
                    f"concurrency conflict releasing lease {lease_id}",
                    table="leases",
                    entity_id=lease_id,
                    expected_version=target_version,
                )
            cur = self.conn.execute(
                "UPDATE tasks SET active_lease_id=NULL,active_fencing_token=0,version=version+1,updated_at=? WHERE task_id=? AND version=?",
                (now_iso(), task_id, task["version"]),
            )
            if cur.rowcount != 1:
                raise StaleLease(f"concurrency conflict releasing lease on task {task_id}")
            self.conn.execute(
                "UPDATE queue_accounts SET active=CASE WHEN active>0 THEN active-1 ELSE 0 END,version=version+1 WHERE owner=?",
                (task["owner"],),
            )
            self.kernel._append_event(task_id, "LEASE_RELEASED", None, None, "kernel", "worker_release", {"lease_id": lease_id})
            if hasattr(self.kernel, "_bound_leases"):
                self.kernel._bound_leases.pop(task_id, None)
            self.kernel._commit()
        except Exception:
            self.kernel._rollback()
            raise


# Alias for backward-compat / naming flexibility
ApprovalManager = CommitApprovalEngine

__all__ = [
    "CommitApprovalEngine",
    "ApprovalManager",
    "verify_approval_authority",
]
