"""State machine engine for durable TaskKernel (ARC-01 Step 5)."""
from __future__ import annotations

import hashlib
import hmac
import logging
import time
from typing import Any

from scp.core.capability_token import (
    InvalidTokenSignatureError,
    get_capability_secret,
)
from scp.security.capability_epoch import parse_capability_token
from scp.task_kernel_parts.definitions import (
    ALLOWED_TRANSITIONS,
    CONTROLLED_STATES,
    DISPATCH_TARGETS,
    SAFE_UNDER_KILL,
    checkpointed_from_controlled,
    STATES,
    TERMINAL,
    InvalidTransition,
    KernelError,
    KillSwitchActive,
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
    from scp.core.capability_token import InvalidTokenSignatureError

    if token is None or token == "":
        raise InvalidTokenSignatureError("Approval token is missing or empty (GAP-13/FA-04)")
    if isinstance(token, str) and not token.strip():
        raise InvalidTokenSignatureError("Approval token is missing or empty (GAP-13/FA-04)")

    # Branch A: Compact string token (mint_token format "payload_b64.sig")
    if isinstance(token, str) and "." in token and not token.strip().startswith("{"):
        from scp.core.capability_token import verify_token

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
        from scp.core.capability_token import verify_token_signature

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


class StateMachineEngine:
    """Encapsulates the 18-state lifecycle matrix (incl. WAITING_APPROVAL, HC-44), validations, transitions, and authority gates."""

    def __init__(self, kernel: Any = None) -> None:
        self.kernel = kernel

    def _assert_bound_kernel(self) -> None:
        if self.kernel is None:
            raise RuntimeError("StateMachineEngine has no bound TaskKernel instance")

    @property
    def conn(self):
        self._assert_bound_kernel()
        return self.kernel.conn

    @property
    def states(self) -> frozenset[str]:
        return frozenset(STATES)

    @property
    def terminal_states(self) -> frozenset[str]:
        return frozenset(TERMINAL)

    @property
    def allowed_transitions(self) -> dict[str, set[str]]:
        return ALLOWED_TRANSITIONS

    def get_valid_next_states(self, from_state: str) -> set[str]:
        """Return the set of valid next states reachable from `from_state`."""
        return set(ALLOWED_TRANSITIONS.get(from_state, set()))

    def _can_transition(self, from_state: str, to_state: str) -> bool:
        """Predicate checking if `from_state -> to_state` transition is allowed."""
        if from_state in TERMINAL:
            return False
        if from_state == "WAITING_APPROVAL" and to_state == "READY":
            return False
        return to_state in ALLOWED_TRANSITIONS.get(from_state, set())

    def _validate_transition(self, from_state: str, to_state: str) -> None:
        """Enforce transition rules and invariants (fail-closed)."""
        # [K1-W2 2026-10-23] HC-44: carve-out `and to_state != "WAITING_APPROVAL"`
        # removed — WAITING_APPROVAL is now a real member of STATES.
        if to_state not in STATES:
            raise InvalidTransition(f"unknown target state {to_state}")
        if to_state in ("COMPLETED", "FAILED"):
            raise InvalidTransition(
                f"direct transition to {to_state} is forbidden; use commit_{to_state.lower()}() with valid evidence"
            )
        if from_state == "WAITING_APPROVAL" and to_state == "READY":
            raise InvalidTransition(
                "direct transition from WAITING_APPROVAL to READY is forbidden; use commit_approval() with valid capability token"
            )
        if to_state not in ALLOWED_TRANSITIONS.get(from_state, set()):
            raise InvalidTransition(f"{from_state}->{to_state}")
        if from_state in TERMINAL:
            raise InvalidTransition("terminal task is immutable")

    def _checkpointed_from_controlled(self, task_id: str) -> bool:
        """[R7-2] Contained-CHECKPOINTED check (single implementation in definitions)."""
        return checkpointed_from_controlled(self.conn, task_id)

    def legal_or_nearest(self, from_state: str, target: str, note: str) -> tuple[str, str]:
        """Determine legal state transition or nearest reroute (transition-map authority)."""
        if target in ALLOWED_TRANSITIONS.get(from_state, set()):
            return target, note
        if target == "CANCELLED" and "FAILED" in ALLOWED_TRANSITIONS.get(from_state, set()):
            return "FAILED", f"{note}:kill_reroute:{from_state}->FAILED"
        if target == "UNKNOWN" and "RECOVERING" in ALLOWED_TRANSITIONS.get(from_state, set()):
            return "RECOVERING", f"{note}:transition_reroute:{from_state}->RECOVERING"
        if target == "RETRY_SCHEDULED" and "FAILED" in ALLOWED_TRANSITIONS.get(from_state, set()):
            return "FAILED", f"{note}:transition_reroute:{from_state}->FAILED(planned_retry={target})"
        raise InvalidTransition(f"{from_state}->{target}")

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
        """Atomic state transition with OCC, Lease Authority Gate, and Why Gate."""
        self._assert_bound_kernel()
        if to_state not in STATES:
            raise InvalidTransition(f"unknown target state {to_state}")
        if to_state in ("COMPLETED", "FAILED"):
            raise InvalidTransition(
                f"direct transition to {to_state} is forbidden; use commit_{to_state.lower()}() with valid evidence"
            )
        self.kernel._begin()
        try:
            if event_id:
                existing = self.conn.execute(
                    "SELECT * FROM events WHERE event_id=?", (event_id,)
                ).fetchone()
                if existing:
                    if existing["task_id"] != task_id or existing["to_state"] != to_state:
                        raise InvalidTransition("event_id reused for a different transition")
                    self.kernel._commit()
                    return self.kernel.get_task(task_id)
            task = self.kernel._task(task_id)
            if expected_version is not None and int(task["version"]) != expected_version:
                raise OptimisticLockError(
                    f"concurrency conflict on task {task_id}: expected version {expected_version}, found {task['version']}",
                    table="tasks",
                    entity_id=task_id,
                    expected_version=expected_version,
                )
            cur_version = int(task["version"])
            old = task["state"]

            if old == "HUMAN_REVIEW" and to_state == "HUMAN_REVIEW":
                self.kernel._rollback()
                return self.kernel.get_task(task_id)

            self._validate_transition(old, to_state)

            # [R7-1 2026-10-28] Kill-switch THEO LOẠI CẠNH (tư vấn 3 phía:
            # không fail-closed tuyệt đối — cạnh về trạng thái an toàn vẫn
            # chạy để xả lease/dọn dẹp). Chỉ cho phép target thuộc
            # SAFE_UNDER_KILL; mọi cạnh resume (RESUME_TARGETS) bị chặn.
            # Check nằm TRONG cùng transaction với OCC version-check phía
            # trên (control + task đọc sau _begin()) nên không có race
            # giữa kiểm kill và ghi state.
            if to_state not in SAFE_UNDER_KILL:
                control_row = self.kernel._control()
                if control_row["global_kill"]:
                    raise KillSwitchActive(
                        f"global kill switch active: edge {old}->{to_state} resumes work; "
                        f"only containment edges to {sorted(SAFE_UNDER_KILL)} are allowed"
                    )

            # [R7-2 2026-10-28] Edge-class authority gate (chặn theo LỚP cạnh,
            # không liệt kê cặp): cạnh đưa task từ trạng thái dừng/kiểm-soát
            # về trạng thái dispatch phải qua authority gate chính thức
            # (commit_approval / reconcile_unknown / auto_resolve_human_review),
            # không qua transition() generic. Hai check này nằm TRƯỚC nhánh
            # system-authority bypass bên dưới: _system_authority chỉ được miễn
            # YÊU CẦU LEASE, không được miễn kill-check và edge-class gate.
            if to_state in DISPATCH_TARGETS:
                if old in CONTROLLED_STATES:
                    raise InvalidTransition(
                        f"{old}->{to_state} is a controlled-state dispatch edge; it requires an "
                        "authority gate (commit_approval / reconcile_unknown / auto_resolve_human_review), "
                        "not the generic transition()"
                    )
                if old == "CHECKPOINTED" and self._checkpointed_from_controlled(task_id):
                    raise InvalidTransition(
                        f"CHECKPOINTED->{to_state} dispatch edge on task {task_id} entered CHECKPOINTED "
                        "from a controlled state; the task stays contained until an authority gate "
                        "releases it (anti-bypass for RECONCILING->CHECKPOINTED->QUEUED style detours)"
                    )

            if to_state in ("COMPLETED", "FAILED", "RUNNING", "CHECKPOINTED", "VERIFYING"):
                try:
                    from scp.meta.why_gate import WhyDecision, get_why_gate

                    why_res = get_why_gate().gate(
                        action_type="kernel_transition",
                        action_desc=f"Transition {task_id} from {old} to {to_state} by {actor}",
                        context=reason,
                        llm_enabled=False,
                    )
                    if why_res.decision == WhyDecision.REJECT:
                        raise InvalidTransition(
                            f"WHY Gate REJECTED this kernel transition: {why_res.falsification_reason}"
                        )
                except InvalidTransition:
                    raise
                except Exception as why_err:
                    logger.debug(
                        "WHY Gate crashed during transition %s -> %s (fail-closed): %s",
                        task_id,
                        to_state,
                        why_err,
                        exc_info=True,
                    )
                    raise InvalidTransition(f"WHY Gate crashed, fail-closed: {why_err}")

            # Lease Authority Gate (INV-01)
            # [R7-4 2026-10-28] _system_authority chỉ được miễn YÊU CẦU LEASE
            # cho cạnh boot-recovery; kill-check (R7-1) và edge-class gate
            # (R7-2) đã chạy TRƯỚC nhánh này nên system authority không thể
            # vòng qua chúng.
            caller_lease = lease_id or getattr(self.kernel, "_bound_leases", {}).get(task_id)
            is_system = getattr(self.kernel, "_system_authority", False)

            if is_system:
                new_lease_id = None
                new_fencing_token = 0
            else:
                is_leased_state = old in {"LEASED", "RUNNING", "WAITING_TOOL", "VERIFYING", "CHECKPOINTED", "UNKNOWN"}
                has_active_lease = bool(task["active_lease_id"])
                if is_leased_state or has_active_lease:
                    bound = getattr(self.kernel, "_bound_leases", {}).get(task_id)
                    if not caller_lease:
                        raise StaleLease(f"transition from {old} requires active lease authority")
                    if bound and caller_lease != bound:
                        raise StaleLease(f"caller lease {caller_lease} does not match bound instance lease {bound}")
                    if not bound:
                        raise StaleLease(f"kernel instance does not possess active lease authority for task {task_id}")
                    if task["active_lease_id"] and caller_lease != task["active_lease_id"]:
                        raise StaleLease(
                            f"caller lease {caller_lease} does not match active task lease {task['active_lease_id']}"
                        )
                    lease_row = self.kernel._assert_lease(caller_lease, task_id)
                    token = int(lease_row["fencing_token"])
                elif caller_lease:
                    lease_row = self.kernel._assert_lease(caller_lease, task_id)
                    token = int(lease_row["fencing_token"])
                else:
                    token = 0

                if to_state in {"RUNNING", "WAITING_TOOL", "VERIFYING", "CHECKPOINTED"}:
                    new_lease_id = caller_lease
                    new_fencing_token = token
                else:
                    new_lease_id = None
                    new_fencing_token = 0
                    if caller_lease:
                        self.conn.execute(
                            "UPDATE leases SET released=1,version=version+1 WHERE lease_id=? AND released=0",
                            (caller_lease,),
                        )
                        self.conn.execute(
                            "UPDATE queue_accounts SET active=CASE WHEN active>0 THEN active-1 ELSE 0 END,version=version+1 WHERE owner=?",
                            (task["owner"],),
                        )
                        if hasattr(self.kernel, "_bound_leases"):
                            self.kernel._bound_leases.pop(task_id, None)

            cur = self.conn.execute(
                "UPDATE tasks SET state=?,version=version+1,active_lease_id=?,active_fencing_token=?,updated_at=? WHERE task_id=? AND version=?",
                (to_state, new_lease_id, new_fencing_token, now_iso(), task_id, cur_version),
            )
            if cur.rowcount != 1:
                raise OptimisticLockError(
                    f"concurrency conflict transitioning task {task_id}: expected version {cur_version}",
                    table="tasks",
                    entity_id=task_id,
                    expected_version=cur_version,
                )

            self.kernel._append_event(
                task_id,
                "STATE_TRANSITION",
                old,
                to_state,
                actor,
                reason or f"{old}->{to_state}",
                payload,
                event_id=event_id,
            )
            self.kernel._commit()
        except Exception:
            self.kernel._rollback()
            raise
        return self.kernel.get_task(task_id)

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
            # [R7-3 2026-10-28] HUMAN_REVIEW->READY là cạnh controlled->dispatch:
            # gate chính thức này (autonomous_mode) là đường duy nhất ngoài
            # transition() generic (đã bị R7-2 chặn). Kill-check nằm trong
            # transaction — READY không thuộc SAFE_UNDER_KILL nên kill active
            # thì fail-closed tại đây.
            self.kernel._assert_not_killed()
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
