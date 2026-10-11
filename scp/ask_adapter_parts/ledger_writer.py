"""Trace ledger appending and checkpoint finalization for AskKernelAdapter."""
from __future__ import annotations

import datetime as _dt
import logging
import uuid
from pathlib import Path
from typing import Any

from scp.core.runtime_paths import runtime_data_dir

from .response_verifier import build_safe_response, dump_data

logger = logging.getLogger(__name__)


def effective_run_identity(response_data: dict[str, Any], request: Any) -> tuple[str, Any]:
    scp_run = getattr(getattr(request, "state", None), "scp_run", None)
    effective_trace_id = (
        response_data.get("trace_id") or getattr(scp_run, "trace_id", None) or f"trace_{uuid.uuid4().hex}"
    )
    effective_run_id = response_data.get("run_id") or getattr(scp_run, "run_id", None)
    return effective_trace_id, effective_run_id


def unified_ledger_path() -> Path:
    return runtime_data_dir() / "trace_ledger.jsonl"


def build_unified_ledger_fields(
    *,
    req: Any,
    request: Any,
    response: Any,
    verification: dict[str, Any],
    final_task: dict[str, Any],
    response_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build unified trace-ledger fields; final_answer is pre-safe provenance."""
    if response_data is None:
        response_data = dump_data(response)
    effective_trace_id, effective_run_id = effective_run_identity(response_data, request)
    safe_data = dump_data(build_safe_response(response, verification))
    return {
        "trace_id": effective_trace_id,
        "run_id": effective_run_id,
        "session_id": getattr(req, "session_id", "") or response_data.get("session_id", ""),
        "question": str(getattr(req, "question", "") or ""),
        "final_answer": str(response_data.get("final_answer", "") or ""),
        "verdict": response_data.get("verdict", verification.get("verdict", "UNKNOWN")),
        "confidence": float(response_data.get("confidence", 0.0) or 0.0),
        "domain": str(response_data.get("domain", "") or getattr(req, "domain", "") or "general"),
        "lane": (
            response_data.get("lane")
            or getattr(req, "lane", None)
            or ("LANE_CHATBOT" if verification.get("is_chatbot_lane") else "LANE_FACTUAL")
        ),
        "routing": response_data.get("routing", {}),
        "governance_decision": response_data.get("governance_decision") or "UNKNOWN",
        "why_gate": response_data.get("why_gate") or {},
        "slm_trace": response_data.get("slm_trace") or [],
        "web_fallback": response_data.get("web_fallback") or None,
        "elapsed_ms": round(float(response_data.get("elapsed_ms") or 0.0), 1),
        "final_verdict": str(safe_data.get("verdict") or "UNKNOWN"),
        "final_governance": str(safe_data.get("governance_decision") or "UNKNOWN"),
        "final_outcome": str(final_task.get("state") or "UNKNOWN"),
    }


def append_unified_ledger(fields: dict[str, Any]) -> None:
    from scp.trace_ledger import TraceLedger

    try:
        if not fields.get("timestamp"):
            fields = {**fields, "timestamp": _dt.datetime.now(_dt.timezone.utc).isoformat()}
        TraceLedger(unified_ledger_path()).append(**fields)
    except Exception as exc:
        logger.debug("append_unified_ledger ignored: %s", exc, exc_info=True)
        logger.warning(
            "[ask-kernel] TraceLedger append failed: %s: %s (fields=%s)",
            type(exc).__name__,
            exc,
            sorted(fields.keys()),
        )


def finalize_task_checkpoint(
    kernel: Any,
    task: dict[str, Any],
    verification: dict[str, Any],
    note: str | None = None,
) -> None:
    checkpoint_id = task.get("checkpoint_id")
    planned_action = task.get("checkpoint_planned_action")
    if not checkpoint_id or planned_action is None:
        return
    try:
        final_state = str(kernel.get_task(task["task_id"])["state"])
        tool_result = {
            "final_task_state": final_state,
            "verifier_verdict": verification.get("verdict"),
            "verifier_id": verification.get("verifier_id"),
            "evidence_ref": verification.get("evidence_ref"),
            "failures": list(verification.get("failures") or []),
        }
        result = kernel.finalize_checkpoint(
            task["task_id"],
            checkpoint_id,
            planned_action=planned_action,
            verifier_verdict=str(verification["verdict"]) if verification.get("verdict") else None,
            post_observation_ref=str(verification["evidence_ref"]) if verification.get("evidence_ref") else None,
            tool_result=tool_result,
            note=note,
        )
        logger.info(
            "[ask-kernel] checkpoint %s finalized for %s: %s (state=%s)",
            checkpoint_id,
            task["task_id"],
            result.get("finalization"),
            result.get("state"),
        )
    except Exception as exc:
        logger.debug("finalize_task_checkpoint ignored: %s", exc, exc_info=True)
        logger.warning(
            "[ask-kernel] checkpoint finalization failed for %s (non-fatal): %s: %s",
            task.get("task_id"),
            type(exc).__name__,
            exc,
        )


async def run_lease_heartbeat(
    kernel: Any,
    task: dict[str, Any],
    stop: Any,
    *,
    default_ttl: float = 120.0,
    max_consecutive_infra_failures: int = 3,
    infra_backoff_s: float = 0.25,
    c3_logger: Any = None,
) -> None:
    """Renew the ask lease every ttl/3 while the in-process attempt holds it."""
    import asyncio

    ttl = float(task.get("lease_ttl_seconds") or default_ttl)
    interval = max(ttl / 3.0, 0.05)
    task_id, lease_id = task["task_id"], task["lease_id"]
    infra_failures = 0
    while True:
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
            return
        except asyncio.TimeoutError:
            pass
        try:
            renewed = kernel.renew_lease(
                task_id, lease_id, task["fencing_token"], ttl_seconds=ttl
            )
        except Exception as exc:
            infra_failures += 1
            if infra_failures >= max_consecutive_infra_failures:
                logger.warning(
                    "[ask-kernel] lease heartbeat error for %s (lease %s): %s — "
                    "stopping after %d consecutive infra failures; finalize will "
                    "route by current state (fail-closed)",
                    task_id, lease_id, type(exc).__name__, infra_failures,
                )
                return
            logger.debug("_lease_heartbeat ignored: %s", exc, exc_info=True)
            logger.warning(
                "[ask-kernel] lease heartbeat infra fault for %s (lease %s): %s — "
                "retrying (%d/%d)",
                task_id, lease_id, type(exc).__name__,
                infra_failures, max_consecutive_infra_failures,
            )
            try:
                await asyncio.wait_for(stop.wait(), timeout=infra_backoff_s)
                return
            except asyncio.TimeoutError:
                continue
        infra_failures = 0
        if not renewed:
            (c3_logger or logger).warning(
                "[S20] lease renew refused for %s (lease %s, attempt %s) — "
                "heartbeat stopped; finalize will route by current state (fail-closed)",
                task_id, lease_id, task.get("attempt_id"),
            )
            return


def record_task_failure(
    adapter: Any,
    task: dict[str, Any],
    reason: str,
    failure_classification: str = "FATAL",
    indictment_ref: str | None = None,
    details: dict[str, Any] | None = None,
    *,
    terminal_states: set[str] | frozenset[str] = frozenset({"COMPLETED", "FAILED", "CANCELLED"}),
    trace_lock: Any = None,
) -> None:
    """Record failure disposition in kernel, checkpoint, trace, and unified ledger."""
    try:
        current = adapter.kernel.get_task(task["task_id"])
        if current["state"] not in terminal_states:
            lease_id = task.get("lease_id") or current.get("active_lease_id")
            if lease_id:
                adapter.kernel.commit_failed(
                    task_id=task["task_id"],
                    lease_id=lease_id,
                    actor=task.get("worker_id") or "ask-route-worker",
                    failure_classification=failure_classification,
                    indictment_ref=indictment_ref or f"ask://{task['task_id']}/failure/{reason}",
                    details=details or {"reason": reason},
                )
            else:
                adapter.kernel.set_task_kill(task["task_id"], actor="ask-kernel-adapter")
        adapter._finalize_task_checkpoint(
            task,
            {
                "verdict": None,
                "verifier_id": None,
                "evidence_ref": indictment_ref or f"ask://{task['task_id']}/failure/{reason}",
                "failures": [reason],
            },
            note=reason,
        )
        if trace_lock is not None:
            with trace_lock:
                adapter.trace.append(
                    task_id=task["task_id"],
                    attempt_id=task.get("attempt_id"),
                    lease_id=task.get("lease_id"),
                    checkpoint_id=task.get("checkpoint_id"),
                    outcome="FAILED",
                    reason=reason,
                )
        else:
            adapter.trace.append(
                task_id=task["task_id"],
                attempt_id=task.get("attempt_id"),
                lease_id=task.get("lease_id"),
                checkpoint_id=task.get("checkpoint_id"),
                outcome="FAILED",
                reason=reason,
            )
        try:
            terminal_state = str(adapter.kernel.get_task(task["task_id"])["state"])
        except Exception:
            logger.debug("fail ignored", exc_info=True)
            terminal_state = "FAILED"
        adapter._append_unified_ledger(
            {
                "task_id": task["task_id"],
                "attempt_id": task.get("attempt_id"),
                "lease_id": task.get("lease_id"),
                "checkpoint_id": task.get("checkpoint_id"),
                "input_hash": task.get("input_hash"),
                "outcome": terminal_state,
                "reason": reason,
                "failure_classification": failure_classification,
                "final_verdict": "FAIL",
                "final_governance": "KILL",
                "final_outcome": terminal_state,
            }
        )
    except Exception as exc:
        logger.warning("AskKernelAdapter.fail: Exception not handled: %s", exc, exc_info=True)
        try:
            from scp.core.exception_policy import observe_nonfatal

            observe_nonfatal(component="scp/ask_kernel_adapter.py:fail", exception_type=type(exc).__name__)
        except Exception:
            logger.warning("AskKernelAdapter.fail: Exception not handled", exc_info=True)
            return

