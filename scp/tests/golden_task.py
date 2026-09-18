# -*- coding: utf-8 -*-
"""P1: Golden Task Runtime Evidence (FIXED - FA-04 compliance).
Drives a real TaskKernel through the full lifecycle and asserts each transition.

Contract (from scp/task_kernel_parts/taskkernel.py):
- TaskKernel(db) -> kernel
- kernel.create_task(task_id, owner, goal, risk_tier, ...)
- kernel.transition(task_id, new_state, actor, reason)
- kernel.claim(task_id, worker_id, ttl_seconds) -> Lease
- kernel.start(task_id, lease_id)
- kernel.commit_completed(task_id, lease_id, verifier_verdict='VERIFIED', evidence_ref='...')
- kernel.get_task(task_id) -> dict with 'state'
- Full path: CREATED -> PLANNING -> READY -> QUEUED -> LEASED -> RUNNING -> VERIFYING -> COMPLETED
"""
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("SCP_VERIFIER_SECRET", "test-golden-task-secret-2026")

from scp.core.verifier_receipt import VerifierReceipt, sign_verifier_receipt
from scp.task_kernel import TaskKernel


def run_golden(tmp_path: Path | None = None) -> int:
    """Run a golden task through the full TaskKernel lifecycle. Returns 0 on success."""
    import tempfile

    if tmp_path is None:
        tmp_path = Path(tempfile.mkdtemp(prefix="scp_golden_"))

    db = tmp_path / "golden.sqlite3"
    kernel = TaskKernel(str(db))

    try:
        # CREATED
        kernel.create_task("golden-1", "golden-worker", "verify full lifecycle", "R0")
        assert kernel.get_task("golden-1")["state"] == "CREATED"

        # PLANNING
        kernel.transition("golden-1", "PLANNING", actor="golden", reason="begin planning")
        assert kernel.get_task("golden-1")["state"] == "PLANNING"

        # READY
        kernel.transition("golden-1", "READY", actor="golden", reason="plan complete")
        assert kernel.get_task("golden-1")["state"] == "READY"

        # QUEUED
        kernel.transition("golden-1", "QUEUED", actor="golden", reason="queued for execution")
        assert kernel.get_task("golden-1")["state"] == "QUEUED"

        # LEASED (claim)
        lease = kernel.claim("golden-1", "golden-worker", ttl_seconds=300)
        assert kernel.get_task("golden-1")["state"] == "LEASED"

        # RUNNING (start)
        kernel.start("golden-1", lease.lease_id)
        assert kernel.get_task("golden-1")["state"] == "RUNNING"

        # Transition RUNNING -> VERIFYING (required before completion per ALLOWED_TRANSITIONS)
        kernel.transition("golden-1", "VERIFYING", actor="golden", reason="verification submitted")
        assert kernel.get_task("golden-1")["state"] == "VERIFYING"

        # COMPLETED (commit_verification_result with signed VerifierReceipt)
        # commit_verification_result requires a VerifierReceipt or dict, and calls commit_completed internally
        receipt = sign_verifier_receipt(
            VerifierReceipt(
                task_id="golden-1",
                verifier_id="golden-verifier",
                verdict="VERIFIED",
                evidence_ref="ev_golden_verified",
                issued_at=time.time(),
            )
        )
        kernel.commit_verification_result("golden-1", lease.lease_id, receipt)
        assert kernel.get_task("golden-1")["state"] == "COMPLETED"

        # Integrity check
        integrity = kernel.verify_integrity()
        assert integrity["quick_check"] == "ok"
        assert integrity["invalid_chains"] == [], f"invalid chains: {integrity['invalid_chains']}"

        return 0
    finally:
        kernel.close()


if __name__ == "__main__":
    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix="scp_golden_"))
    sys.exit(run_golden(tmp))
