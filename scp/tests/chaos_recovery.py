# -*- coding: utf-8 -*-
"""P1: Chaos Recovery Test (FIXED - FA-04 compliance).
Creates a real TaskKernel, drives a task to a non-terminal state, then verifies
recover_on_boot() recovers it to a safe state per the ALLOWED_TRANSITIONS map.

Contract (from scp/task_kernel_parts/taskkernel.py):
- TaskKernel(db) -> kernel
- kernel.create_task(task_id, owner, goal, risk_tier)
- kernel.transition(task_id, new_state, actor, reason)
- kernel.recover_on_boot() -> {'recovered': [...], 'corrupted': [...]}
- RUNNING/VERIFYING -> HUMAN_REVIEW, LEASED/WAITING_TOOL -> RECOVERING
"""
from pathlib import Path

from scp.task_kernel import TaskKernel


def test_chaos_recovery(tmp_path: Path) -> None:
    """Simulate crash during RUNNING and verify reconcile recovers to HUMAN_REVIEW."""
    db = tmp_path / "chaos_recovery.sqlite3"
    kernel = TaskKernel(str(db))

    # 1. Create + drive to RUNNING (non-terminal, in-flight state)
    kernel.create_task("chaos-1", "chaos-worker", "write report to disk", "R2")
    kernel.transition("chaos-1", "PLANNING", actor="chaos", reason="plan")
    kernel.transition("chaos-1", "READY", actor="chaos", reason="ready")
    kernel.transition("chaos-1", "QUEUED", actor="chaos", reason="queue")
    lease = kernel.claim("chaos-1", "chaos-worker", ttl_seconds=300)
    kernel.start("chaos-1", lease.lease_id)

    # Verify we are actually RUNNING before simulated crash
    assert kernel.get_task("chaos-1")["state"] == "RUNNING"

    # 2. Simulate crash + boot recovery (replay journal)
    report = kernel.recover_on_boot()

    # 3. Contract assertions (no weakening)
    assert not report["corrupted"], f"journal corrupted: {report['corrupted']}"
    assert any(
        r["task_id"] == "chaos-1" and r["to"] == "HUMAN_REVIEW"
        for r in report["recovered"]
    ), f"chaos-1 not recovered to HUMAN_REVIEW: {report['recovered']}"
    assert kernel.get_task("chaos-1")["state"] == "HUMAN_REVIEW"

    # 4. Journal hash-chain must survive recovery (evidence non-repudiation)
    verification = kernel.verify_journal("chaos-1")
    assert verification["hash_chain_valid"] is True

    kernel.close()
