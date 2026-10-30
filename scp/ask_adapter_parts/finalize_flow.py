"""Finalize flow for AskKernelAdapter (extracted for < 780 LOC modularity)."""
from __future__ import annotations

import logging
import time
from typing import Any

from scp.ask_adapter_parts.response_verifier import dump_data, verification_fail_reason
from scp.ask_adapter_parts.task_reads import refresh_task_or_reraise
from scp.core.verifier_receipt import VerifierReceipt, sign_verifier_receipt

logger = logging.getLogger("scp.ask_kernel_adapter")

try:
    from scp.task_kernel import InvalidTransition, KernelError
except ImportError:
    from task_kernel import InvalidTransition, KernelError


async def finalize_ask_task(
    adapter: Any,
    task: dict[str, Any],
    response: Any,
    req: Any,
    request: Any = None,
    *,
    terminal_states: set[str],
    intact_states: set[str],
    trace_lock: Any,
    sign_receipt_fn: Any = sign_verifier_receipt,
) -> dict[str, Any]:
    task_id, lease_id = task["task_id"], task["lease_id"]

    def _terminal_result(current_task: dict[str, Any]) -> dict[str, Any]:
        verification = {
            "verdict": "INSUFFICIENT",
            "verifier_id": "scp-ask-kernel-terminal-v1",
            "evidence_ref": f"ask://{task_id}/terminal/{str(current_task['state']).lower()}",
            "failures": ["task_terminal_before_verification"],
            "checked": {"kernel_task_non_terminal": False},
        }
        adapter._finalize_task_checkpoint(task, verification, note="task_terminal_before_verification")
        response_data = dump_data(response)
        with trace_lock:
            adapter.trace.append(
                task_id=task_id,
                attempt_id=task.get("attempt_id"),
                step_id="rag-read",
                lease_id=lease_id,
                checkpoint_id=task.get("checkpoint_id"),
                verifier_id=verification["verifier_id"],
                evidence_ref=verification["evidence_ref"],
                run_id=response_data.get("run_id"),
                trace_id=response_data.get("trace_id"),
                outcome=current_task["state"],
                verdict=verification["verdict"],
                reason="task_terminal_before_verification",
                response_elapsed_ms=response_data.get("elapsed_ms"),
            )
        adapter._append_unified_ledger(
            adapter._unified_ledger_fields(
                req=req,
                request=request,
                response=response,
                verification=verification,
                final_task=current_task,
                response_data=response_data,
            )
        )
        return {
            "task": current_task,
            "verification": verification,
            "safe_response": adapter._safe_response(response, verification),
        }

    current_task = adapter.kernel.get_task(task_id)
    if current_task["state"] in terminal_states:
        return _terminal_result(current_task)

    def _stale_lifecycle_result(stale_task: dict[str, Any], reason: str) -> dict[str, Any]:
        verification = {
            "verdict": "INSUFFICIENT",
            "verifier_id": "scp-ask-kernel-lifecycle-race-v1",
            "evidence_ref": f"ask://{task_id}/lifecycle/{str(stale_task['state']).lower()}",
            "failures": [reason],
            "checked": {"kernel_execution_authority": False},
        }
        response_data = dump_data(response)
        if adapter.autonomous_mode:
            final_task = adapter._fail_closed_autonomous(
                task_id,
                reason=reason,
                payload={"verification": verification},
            )
        else:
            final_task = adapter._escalate_to_human_review(
                task_id,
                reason=reason,
                payload={"verification": verification},
            )
        adapter._finalize_task_checkpoint(task, verification, note=reason)
        with trace_lock:
            adapter.trace.append(
                task_id=task_id,
                attempt_id=task.get("attempt_id"),
                step_id="rag-read",
                lease_id=lease_id,
                checkpoint_id=task.get("checkpoint_id"),
                verifier_id=verification["verifier_id"],
                evidence_ref=verification["evidence_ref"],
                run_id=response_data.get("run_id"),
                trace_id=response_data.get("trace_id"),
                outcome=final_task["state"],
                verdict=verification["verdict"],
                reason=reason,
                response_elapsed_ms=response_data.get("elapsed_ms"),
            )
        adapter._append_unified_ledger(
            adapter._unified_ledger_fields(
                req=req,
                request=request,
                response=response,
                verification=verification,
                final_task=final_task,
                response_data=response_data,
            )
        )
        return {
            "task": final_task,
            "verification": verification,
            "safe_response": adapter._safe_response(response, verification),
        }

    if current_task["state"] not in intact_states:
        return _stale_lifecycle_result(
            current_task,
            f"lifecycle_authority_lost:{current_task['state']}",
        )

    try:
        if current_task["state"] == "RUNNING":
            adapter.kernel.transition(task_id, "VERIFYING", actor="ask-kernel-adapter", reason="ask_response_observed")
    except InvalidTransition as race_exc:
        # [R22] A failed re-read must not replace the InvalidTransition (phantom NotFound).
        current_task = refresh_task_or_reraise(adapter.kernel, task_id, race_exc)
        if current_task["state"] in terminal_states:
            return _terminal_result(current_task)
        return _stale_lifecycle_result(
            current_task,
            f"verifying_transition_raced:{current_task['state']}",
        )

    verification = await adapter.verify_response(req, response, task)
    if verification["verdict"] == "VERIFIED":
        receipt = sign_receipt_fn(
            VerifierReceipt(
                task_id=task_id,
                verifier_id=str(verification.get("verifier_id", "scp-ask-rag-verifier-v2")),
                verdict="VERIFIED",
                evidence_ref=str(verification.get("evidence_ref", "")),
                issued_at=time.time(),
                attempt_id=task.get("attempt_id"),
            )
        )
        verification["task_id"] = task_id
        verification["issued_at"] = receipt.issued_at
        verification["signature"] = receipt.signature
        try:
            final_task = adapter.kernel.commit_verification_result(task_id, lease_id, receipt)
        except KernelError as commit_exc:
            current_task = refresh_task_or_reraise(adapter.kernel, task_id, commit_exc)  # [R22]
            if current_task["state"] in terminal_states:
                return _terminal_result(current_task)
            if adapter.autonomous_mode and current_task["state"] == "HUMAN_REVIEW":
                try:
                    adapter.kernel.auto_resolve_human_review(task_id, reason="autonomous_verification_passed")
                    adapter.kernel.transition(task_id, "QUEUED", actor="ask-kernel-adapter", reason="autonomous_auto_advance")
                    new_lease = adapter.kernel.claim(task_id, worker_id=task.get("worker_id") or "ask-route-worker")
                    adapter.kernel.transition(task_id, "RUNNING", actor="ask-kernel-adapter", lease_id=new_lease.lease_id, reason="autonomous_auto_advance")
                    adapter.kernel.transition(task_id, "VERIFYING", actor="ask-kernel-adapter", lease_id=new_lease.lease_id, reason="autonomous_auto_advance")
                    fresh_receipt = sign_receipt_fn(
                        VerifierReceipt(
                            task_id=task_id,
                            verifier_id=str(verification.get("verifier_id", "scp-ask-rag-verifier-v2")),
                            verdict="VERIFIED",
                            evidence_ref=str(verification.get("evidence_ref", "")),
                            issued_at=time.time(),
                            attempt_id=new_lease.attempt_id,
                        )
                    )
                    final_task = adapter.kernel.commit_verification_result(task_id, new_lease.lease_id, fresh_receipt)
                except Exception as auto_err:
                    logger.warning("[ask-kernel] Failed to auto-resolve and complete from HUMAN_REVIEW: %s", auto_err, exc_info=True)
                    # [R22] Keep auto_err as the reported cause even if the re-read fails.
                    refreshed = refresh_task_or_reraise(adapter.kernel, task_id, auto_err)
                    return _stale_lifecycle_result(refreshed, f"auto_resolve_failed:{auto_err}")
            else:
                return _stale_lifecycle_result(current_task, "commit_raced_lease_or_state")
    else:
        _fail_data = dump_data(response)
        _fail_reason = verification_fail_reason(_fail_data)
        if adapter.autonomous_mode:
            try:
                attempts = int(task.get("attempts", 0))
                max_attempts = int(task.get("max_attempts", 3))
                is_retryable = (attempts + 1 < max_attempts)
                classification = "RETRYABLE" if is_retryable else "VERIFICATION_FAILED"
                indictment = str(verification.get("evidence_ref") or f"ask://{task_id}/verification_failed")
                actor = task.get("worker_id") or "ask-route-worker"
                final_task = adapter.kernel.commit_failed(
                    task_id,
                    lease_id,
                    actor=actor,
                    failure_classification=classification,
                    indictment_ref=indictment,
                    details={"reason": _fail_reason, "verification": verification},
                )
            except KernelError as fail_err:
                logger.warning("[ask-kernel] Autonomous commit_failed failed (%s); failing closed: %s", fail_err, task_id)
                final_task = adapter._fail_closed_autonomous(
                    task_id,
                    reason="ask_verification_failed_unleased",
                    payload={"verification": verification, "commit_error": str(fail_err)},
                )
        else:
            final_task = adapter._escalate_to_human_review(
                task_id,
                reason=_fail_reason,
                payload={"verification": verification},
            )
        # [esc-auto slice 2] Evidence-insufficient ESCALATE ⇒ SCP's own evidence
        # ticket (no human wait). The resolver closes the task with the true
        # outcome; the AI draft stays withheld either way.
        from scp.core.evidence_resolution import on_ask_escalation

        on_ask_escalation(req, request, task_id, getattr(adapter.kernel, "db_path", None), _fail_reason)
    adapter._finalize_task_checkpoint(task, verification)
    response_data = dump_data(response)
    safe_resp = adapter._safe_response(response, verification)

    ledger_fields = adapter._unified_ledger_fields(
        req=req,
        request=request,
        response=response,
        verification=verification,
        final_task=final_task,
        response_data=response_data,
    )
    effective_trace_id = ledger_fields["trace_id"]
    effective_run_id = ledger_fields["run_id"]
    adapter._append_unified_ledger(ledger_fields)

    with trace_lock:
        adapter.trace.append(
            task_id=task_id,
            attempt_id=task.get("attempt_id"),
            step_id="rag-read",
            lease_id=lease_id,
            checkpoint_id=task.get("checkpoint_id"),
            verifier_id=verification.get("verifier_id"),
            evidence_ref=verification.get("evidence_ref"),
            run_id=effective_run_id,
            trace_id=effective_trace_id,
            outcome=final_task.get("state"),
            verdict=verification.get("verdict"),
            grounded_ratio=verification.get("grounded_ratio"),
            response_elapsed_ms=response_data.get("elapsed_ms"),
        )
    if hasattr(safe_resp, "model_copy"):
        safe_resp = safe_resp.model_copy(update={"trace_id": effective_trace_id, "run_id": effective_run_id})
    elif isinstance(safe_resp, dict):
        safe_resp["trace_id"] = effective_trace_id
        safe_resp["run_id"] = effective_run_id
    return {
        "task": final_task,
        "verification": verification,
        "safe_response": safe_resp,
    }
