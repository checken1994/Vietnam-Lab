"""Checker-gate method implementations for TaskKernel.

[SEC-FIX 2026-10-27] ARC-01 tách file (move-only): taskkernel.py đạt 821 LOC
vượt trần 800 (tripwire test_arc01_taskkernel_loc_ceiling). Hai method surface
checker-gate/autonomous chuyển VERBATIM thành module-level function (tham số
đầu tiên là kernel instance/self) và được TaskKernel gán vào class attribute
theo đúng pattern sẵn có của repo: ``_RENEWABLE_LEASE_STATES`` trong
taskkernel.py và ``start_fast_learning_thread`` (scp/core/fast_learning_engine.py).
Không dùng kế thừa mixin (ARC-02: zero mixin inheritance trong scp/). Không đổi
logic, signature, default hay docstring; import path công khai không đổi.
"""
from __future__ import annotations

from typing import Any


def autonomous_review_and_approve(
    self,
    task_id: str,
    candidate_patch: str,
    target_file: str,
    actor: str = "autonomous_checker",
    expected_version: int | None = None,
    max_blast_radius_lines: int | None = None,
    **kwargs: Any,
) -> Any:
    """Evaluate candidate patch fail-closed and atomically approve task from WAITING_APPROVAL -> READY."""
    return self.checker_gate.evaluate_and_approve(
        task_id=task_id,
        candidate_patch=candidate_patch,
        target_file=target_file,
        actor=actor,
        expected_version=expected_version,
        max_blast_radius_lines=max_blast_radius_lines,
        **kwargs,
    )


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
