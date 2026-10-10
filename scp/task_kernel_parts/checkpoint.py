"""Checkpoint engine for durable TaskKernel."""
from __future__ import annotations

import json
import secrets
from typing import Any

from scp.task_kernel_parts.definitions import (
    STATES,
    TERMINAL,
    CheckpointCorrupt,
    InvalidTransition,
    KernelError,
    NotFound,
    StaleLease,
    _assert_checkpoint_safe,
    now_iso,
    stable_hash,
)


class CheckpointEngine:
    def __init__(self, kernel: Any) -> None:
        self.kernel = kernel

    @property
    def conn(self):
        return self.kernel.conn

    def checkpoint(
        self,
        task_id: str,
        lease_id: str,
        step_id: str,
        state: str,
        planned_action: Any,
        capability_epoch: int,
        idempotency_key: str,
        pre_observation_ref: str | None = None,
        post_observation_ref: str | None = None,
        tool_result: Any | None = None,
        verifier_verdict: str | None = None,
    ) -> str:
        if state not in STATES:
            raise CheckpointCorrupt("invalid checkpoint state")
        _assert_checkpoint_safe({
            "planned_action": planned_action,
            "pre_observation_ref": pre_observation_ref,
            "post_observation_ref": post_observation_ref,
            "tool_result": tool_result,
        })
        self.kernel._begin()
        try:
            lease = self.kernel._assert_lease(lease_id, task_id)
            task = self.kernel._task(task_id)
            if task["state"] not in {"RUNNING", "WAITING_TOOL", "VERIFYING", "CHECKPOINTED"}:
                raise InvalidTransition(f"cannot checkpoint task in state {task['state']}")
            if task["active_lease_id"] != lease_id:
                raise StaleLease(f"checkpoint lease {lease_id} does not match active task lease {task['active_lease_id']}")
            is_system = getattr(self.kernel, "_system_authority", False)
            bound = getattr(self.kernel, "_bound_leases", {}).get(task_id)
            if not is_system:
                if not bound:
                    raise StaleLease(f"kernel instance does not possess active lease authority for task {task_id}")
                if bound != lease_id:
                    raise StaleLease(f"caller lease {lease_id} does not match bound instance lease {bound}")
            payload = {
                "task_id": task_id,
                "attempt_id": lease["attempt_id"],
                "step_id": step_id,
                "state": state,
                "planned_action": planned_action,
                "capability_epoch": capability_epoch,
                "idempotency_key": idempotency_key,
                "pre_observation_ref": pre_observation_ref,
                "post_observation_ref": post_observation_ref,
                "tool_result": tool_result,
                "verifier_verdict": verifier_verdict,
            }
            cp_id = "cp_" + secrets.token_hex(10)
            payload_hash = stable_hash(payload)
            self.conn.execute(
                "INSERT INTO checkpoints(checkpoint_id,task_id,attempt_id,step_id,state,planned_action_hash,capability_epoch,idempotency_key,pre_observation_ref,post_observation_ref,tool_result_json,verifier_verdict,payload_hash,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    cp_id,
                    task_id,
                    lease["attempt_id"],
                    step_id,
                    state,
                    stable_hash(planned_action),
                    capability_epoch,
                    idempotency_key,
                    pre_observation_ref,
                    post_observation_ref,
                    json.dumps(tool_result, ensure_ascii=False, sort_keys=True) if tool_result is not None else None,
                    verifier_verdict,
                    payload_hash,
                    now_iso(),
                ),
            )
            # [P1 FIX 2026-09-05] A checkpoint is a snapshot, not a state transition
            self.kernel._append_event(
                task_id,
                "CHECKPOINT_WRITTEN",
                None,
                None,
                "kernel",
                "checkpoint_written",
                {"checkpoint_id": cp_id, "payload_hash": payload_hash, "idempotency_key": idempotency_key},
            )
            self.kernel._commit()
            return cp_id
        except Exception:
            self.kernel._rollback()
            raise

    @staticmethod
    def _rebuild_checkpoint_payload(
        row: Any,
        state: str,
        planned_action: Any,
        post_observation_ref: str | None,
        tool_result: Any,
        verifier_verdict: str | None,
    ) -> dict[str, Any]:
        """Rebuild the stable_hash payload of a checkpoint row with the EXACT
        shape used by checkpoint() / validate_checkpoint(), so a row updated by
        finalize_checkpoint keeps a self-verifying payload_hash."""
        return {
            "task_id": row["task_id"],
            "attempt_id": row["attempt_id"],
            "step_id": row["step_id"],
            "state": state,
            "planned_action": planned_action,
            "capability_epoch": row["capability_epoch"],
            "idempotency_key": row["idempotency_key"],
            "pre_observation_ref": row["pre_observation_ref"],
            "post_observation_ref": post_observation_ref,
            "tool_result": tool_result,
            "verifier_verdict": verifier_verdict,
        }

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
        if note is None and verifier_verdict is None and post_observation_ref is None and tool_result is None:
            raise KernelError("finalize_checkpoint requires evidence fields or a note (no fabricated data)")
        _assert_checkpoint_safe({
            "post_observation_ref": post_observation_ref,
            "tool_result": tool_result,
            "verifier_verdict": verifier_verdict,
            "note": note,
        })
        self.kernel._begin()
        try:
            row = self.conn.execute("SELECT * FROM checkpoints WHERE checkpoint_id=?", (checkpoint_id,)).fetchone()
            if not row:
                raise CheckpointCorrupt(checkpoint_id)
            if row["task_id"] != task_id:
                raise KernelError("checkpoint task mismatch")
            if row["planned_action_hash"] != stable_hash(planned_action):
                raise CheckpointCorrupt("planned action hash mismatch")
            if row["state"] in TERMINAL or row["state"] == "HUMAN_REVIEW":
                self.kernel._commit()
                result = dict(row)
                result["finalization"] = "already_final"
                return result
            if row["state"] == "UNKNOWN":
                self.kernel._commit()
                result = dict(row)
                result["finalization"] = "owned_by_reconcile"
                return result
            task = self.kernel._task(task_id)
            if task["state"] not in TERMINAL and task["state"] != "HUMAN_REVIEW":
                blocked = dict(tool_result) if isinstance(tool_result, dict) else {}
                blocked["finalization"] = "BLOCKED"
                blocked["finalization_reason"] = f"task_state={task['state']}" + (f"; note={note}" if note else "")
                payload = self._rebuild_checkpoint_payload(
                    row, row["state"], planned_action, row["post_observation_ref"], blocked, row["verifier_verdict"]
                )
                self.conn.execute(
                    "UPDATE checkpoints SET tool_result_json=?, payload_hash=? WHERE checkpoint_id=?",
                    (json.dumps(blocked, ensure_ascii=False, sort_keys=True), stable_hash(payload), checkpoint_id),
                )
                self.kernel._append_event(
                    task_id,
                    "CHECKPOINT_FINALIZED",
                    None,
                    None,
                    "kernel",
                    "checkpoint_finalization_blocked",
                    {"checkpoint_id": checkpoint_id, "task_state": task["state"], "note": note},
                )
                self.kernel._commit()
                result = self.get_checkpoint(checkpoint_id)
                result["finalization"] = "blocked_task_not_decided"
                return result
            from_state = row["state"]
            new_post = post_observation_ref if post_observation_ref is not None else row["post_observation_ref"]
            new_verdict = verifier_verdict if verifier_verdict is not None else row["verifier_verdict"]
            if tool_result is not None:
                new_tool = tool_result
            else:
                new_tool = json.loads(row["tool_result_json"]) if row["tool_result_json"] else None
            payload = self._rebuild_checkpoint_payload(
                row, task["state"], planned_action, new_post, new_tool, new_verdict
            )
            self.conn.execute(
                "UPDATE checkpoints SET state=?, post_observation_ref=?, verifier_verdict=?, tool_result_json=?, payload_hash=? WHERE checkpoint_id=?",
                (
                    task["state"],
                    new_post,
                    new_verdict,
                    json.dumps(new_tool, ensure_ascii=False, sort_keys=True) if new_tool is not None else None,
                    stable_hash(payload),
                    checkpoint_id,
                ),
            )
            self.kernel._append_event(
                task_id,
                "CHECKPOINT_FINALIZED",
                None,
                None,
                "kernel",
                "checkpoint_finalized",
                {
                    "checkpoint_id": checkpoint_id,
                    "from_state": from_state,
                    "to_state": task["state"],
                    "verifier_verdict": new_verdict,
                    "note": note,
                },
            )
            self.kernel._commit()
            result = self.get_checkpoint(checkpoint_id)
            result["finalization"] = "finalized"
            return result
        except Exception:
            self.kernel._rollback()
            raise

    def validate_checkpoint(self, checkpoint_id: str, planned_action: Any) -> dict[str, Any]:
        row = self.conn.execute("SELECT * FROM checkpoints WHERE checkpoint_id=?", (checkpoint_id,)).fetchone()
        if not row:
            raise CheckpointCorrupt(checkpoint_id)
        if row["planned_action_hash"] != stable_hash(planned_action):
            raise CheckpointCorrupt("planned action hash mismatch")
        try:
            tool_result = json.loads(row["tool_result_json"]) if row["tool_result_json"] is not None else None
        except (TypeError, ValueError) as exc:
            raise CheckpointCorrupt("checkpoint tool result is not valid JSON") from exc
        payload = {
            "task_id": row["task_id"],
            "attempt_id": row["attempt_id"],
            "step_id": row["step_id"],
            "state": row["state"],
            "planned_action": planned_action,
            "capability_epoch": row["capability_epoch"],
            "idempotency_key": row["idempotency_key"],
            "pre_observation_ref": row["pre_observation_ref"],
            "post_observation_ref": row["post_observation_ref"],
            "tool_result": tool_result,
            "verifier_verdict": row["verifier_verdict"],
        }
        if row["payload_hash"] != stable_hash(payload):
            raise CheckpointCorrupt("checkpoint payload hash mismatch")
        return dict(row)

    def get_checkpoint(self, checkpoint_id: str) -> dict[str, Any]:
        row = self.conn.execute("SELECT * FROM checkpoints WHERE checkpoint_id=?", (checkpoint_id,)).fetchone()
        if not row:
            raise CheckpointCorrupt(checkpoint_id)
        return dict(row)
