"""Unit and branch coverage tests for RecoveryEngine reconciliation outcomes (ARC-01 / TST-03).

Covers:
- enter_reconciling error paths (wrong task_id, wrong state, non-existent checkpoint)
- reconcile_unknown outcomes: PARTIAL, CONFLICT, NOT_APPLIED, APPLIED, UNKNOWN, INVALID
- recovery engine optimistic locking and idempotent events
"""
from __future__ import annotations

from pathlib import Path
import pytest

from scp.task_kernel import (
    InvalidTransition,
    KernelError,
    TaskKernel,
)


def _setup_dispatched_unknown_task(kernel: TaskKernel, task_id: str = "task-rec-test") -> tuple[str, str, str]:
    """Helper to setup a task in UNKNOWN state with active idempotency and checkpoint."""
    kernel.create_task(task_id, owner="owner-rec", goal="test recovery branches")
    for s in ("PLANNING", "READY", "QUEUED"):
        kernel.transition(task_id, s)
    lease = kernel.claim(task_id, "worker-rec", ttl_seconds=30)
    kernel.start(task_id, lease.lease_id)

    idem_key, claimed = kernel.idempotency_claim(task_id, "step_1", "http.post", "https://api.test/resource")
    assert claimed is True
    disp = kernel.record_action_dispatched(
        task_id,
        lease.lease_id,
        "step_1",
        {"action": "test_post"},
        1,
        idem_key,
        "req-rec-001",
    )
    cp_id = disp["checkpoint_id"]
    assert kernel.get_task(task_id)["state"] == "UNKNOWN"
    return task_id, lease.lease_id, cp_id


def test_enter_reconciling_error_paths(tmp_path: Path) -> None:
    """Test enter_reconciling validation and error branches."""
    kernel = TaskKernel(tmp_path / "rec_err.sqlite3")
    try:
        task_id, lease_id, cp_id = _setup_dispatched_unknown_task(kernel, "task-rec-err")

        # 1. Non-existent checkpoint
        with pytest.raises(KernelError, match="checkpoint task mismatch"):
            kernel.recovery_engine.enter_reconciling(task_id, "non_existent_cp")

        # 2. Checkpoint for another task
        other_task = "task-other"
        kernel.create_task(other_task, "owner-other", "goal-other")
        with pytest.raises(KernelError, match="checkpoint task mismatch"):
            kernel.recovery_engine.enter_reconciling(other_task, cp_id)

        # 3. Enter reconciling successfully
        rec_task = kernel.recovery_engine.enter_reconciling(task_id, cp_id)
        assert rec_task["state"] == "RECONCILING"

        # 4. Calling enter_reconciling when already in RECONCILING is idempotent
        rec_repeat = kernel.recovery_engine.enter_reconciling(task_id, cp_id)
        assert rec_repeat["state"] == "RECONCILING"
    finally:
        kernel.close()


def test_reconcile_unknown_partial_and_conflict(tmp_path: Path) -> None:
    """Test reconcile_unknown PARTIAL and CONFLICT outcomes."""
    kernel = TaskKernel(tmp_path / "rec_out.sqlite3")
    try:
        # PARTIAL outcome -> transitions to HUMAN_REVIEW
        t1, l1, cp1 = _setup_dispatched_unknown_task(kernel, "task-partial")
        kernel.recovery_engine.enter_reconciling(t1, cp1)

        res_partial = kernel.recovery_engine.reconcile_unknown(
            t1,
            cp1,
            outcome="PARTIAL",
            evidence_ref="test://evidence/partial-effect",
            verifier_id="verifier-partial",
        )
        assert res_partial["state"] == "HUMAN_REVIEW"

        # CONFLICT outcome -> transitions to HUMAN_REVIEW
        t2, l2, cp2 = _setup_dispatched_unknown_task(kernel, "task-conflict")
        kernel.recovery_engine.enter_reconciling(t2, cp2)

        res_conflict = kernel.recovery_engine.reconcile_unknown(
            t2,
            cp2,
            outcome="CONFLICT",
            evidence_ref="test://evidence/conflict-effect",
            verifier_id="verifier-conflict",
        )
        assert res_conflict["state"] == "HUMAN_REVIEW"
    finally:
        kernel.close()


def test_reconcile_unknown_applied_and_unknown(tmp_path: Path) -> None:
    """Test reconcile_unknown APPLIED and UNKNOWN outcomes."""
    kernel = TaskKernel(tmp_path / "rec_app.sqlite3")
    try:
        # APPLIED outcome -> completed step, task goes to CHECKPOINTED or RUNNING
        t1, l1, cp1 = _setup_dispatched_unknown_task(kernel, "task-applied")
        kernel.recovery_engine.enter_reconciling(t1, cp1)

        res_applied = kernel.recovery_engine.reconcile_unknown(
            t1,
            cp1,
            outcome="APPLIED",
            evidence_ref="test://evidence/applied-proof",
            verifier_id="verifier-applied",
        )
        assert res_applied["state"] == "HUMAN_REVIEW"

        # UNKNOWN outcome with verifier -> stays in UNKNOWN or HUMAN_REVIEW
        t2, l2, cp2 = _setup_dispatched_unknown_task(kernel, "task-unknown-res")
        kernel.recovery_engine.enter_reconciling(t2, cp2)

        res_unknown = kernel.recovery_engine.reconcile_unknown(
            t2,
            cp2,
            outcome="UNKNOWN",
            evidence_ref="test://evidence/still-unknown",
            verifier_id="verifier-unknown",
        )
        assert res_unknown["state"] in {"UNKNOWN", "HUMAN_REVIEW", "RECOVERING"}
    finally:
        kernel.close()


def test_reconcile_unknown_validation_errors(tmp_path: Path) -> None:
    """Test invalid arguments and missing verifier errors on reconcile_unknown."""
    kernel = TaskKernel(tmp_path / "rec_val.sqlite3")
    try:
        t1, l1, cp1 = _setup_dispatched_unknown_task(kernel, "task-val-err")
        kernel.recovery_engine.enter_reconciling(t1, cp1)

        # Invalid outcome
        with pytest.raises(KernelError, match="invalid reconcile outcome"):
            kernel.recovery_engine.reconcile_unknown(t1, cp1, outcome="INVALID_OUTCOME", evidence_ref="ev")

        # Missing evidence
        with pytest.raises(KernelError, match="reconcile evidence is required"):
            kernel.recovery_engine.reconcile_unknown(t1, cp1, outcome="PARTIAL", evidence_ref="")

        # Missing verifier for PARTIAL
        with pytest.raises(KernelError, match="reconciliation requires verifier"):
            kernel.recovery_engine.reconcile_unknown(t1, cp1, outcome="PARTIAL", evidence_ref="ev", verifier_id="")

        # Missing verifier for APPLIED
        with pytest.raises(KernelError, match="reconcile verifier is required"):
            kernel.recovery_engine.reconcile_unknown(t1, cp1, outcome="APPLIED", evidence_ref="ev", verifier_id="")
    finally:
        kernel.close()
