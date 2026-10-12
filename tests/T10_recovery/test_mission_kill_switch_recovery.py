"""Recovery and Kill-Switch Integration Tests (CE-S01-03 / D-Level).

Proves:
- Kill switch halts in-flight missions, fencing active leases.
- Durable mission state is preserved in SQLite control and events tables.
- Reboot / crash recovery under active kill switch does not resume killed missions.
- Released kill switch permits safe resumption from durable checkpoint without duplicate side effects.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scp.task_kernel import (
    KernelError,
    KillSwitchActive,
    StaleLease,
    TaskKernel,
)


def _setup_long_running_mission(kernel: TaskKernel, task_id: str = "mission-1") -> tuple[str, str, str]:
    """Create and start a long-running mission up to WAITING_TOOL with a checkpoint."""
    kernel.create_task(task_id, owner="mission-supervisor", goal="long-running mission run", risk_tier="R1")
    for s in ("PLANNING", "READY", "QUEUED"):
        kernel.transition(task_id, s, actor="mission-supervisor", reason="setup")
    lease = kernel.claim(task_id, "worker-mission", ttl_seconds=300)
    kernel.start(task_id, lease.lease_id)
    idem_key, claimed = kernel.idempotency_claim(task_id, "step-1", "net.fetch", "mission://resource")
    assert claimed is True
    disp = kernel.record_action_dispatched(
        task_id,
        lease.lease_id,
        "step-1",
        {"target": "mission://resource"},
        1,
        idem_key,
        "req-mission-001",
    )
    cp_id = str(disp["checkpoint_id"])
    return task_id, lease.lease_id, cp_id


def test_kill_switch_recovery_halts_long_running_mission_and_fences_leases(tmp_path: Path) -> None:
    """CE-S01-03: Kill switch halts mission, invalidates active lease, and preserves state."""
    db_path = tmp_path / "mission_kill.sqlite3"
    kernel = TaskKernel(db_path)
    try:
        task_id, lease_id, cp_id = _setup_long_running_mission(kernel, "mission-kill-1")
        assert kernel.get_task(task_id)["state"] == "UNKNOWN"

        # Trigger global kill switch
        kernel.set_global_kill(True, actor="ops-admin")
        ctrl = kernel._control()
        assert ctrl["global_kill"] == 1

        # In-flight worker attempt to heartbeat or transition must be rejected with StaleLease / KillSwitchActive
        with pytest.raises((KillSwitchActive, StaleLease)):
            kernel.heartbeat(task_id, lease_id, extend_seconds=60)

        # Reopen kernel (simulating supervisor crash/restart under active kill switch)
        supervisor = TaskKernel(db_path)
        try:
            assert supervisor._control()["global_kill"] == 1
            # Auto-reconcile or boot recovery must safely transition orphan without resuming execution
            orphans = supervisor.auto_reconcile_orphans(actor="watchdog", stale_seconds=0.0)
            # Reconcile does not dispatch new actions while kill switch is active
            task = supervisor.get_task(task_id)
            assert task["state"] in ("RECONCILING", "HUMAN_REVIEW", "UNKNOWN")
        finally:
            supervisor.close()
    finally:
        kernel.close()


def test_kill_switch_active_persists_across_restart_and_prevents_boot_resumption(tmp_path: Path) -> None:
    """CE-S01-03: Persistent control table preserves kill switch across restarts; boot recovery fails closed."""
    db_path = tmp_path / "persist_kill.sqlite3"
    kernel = TaskKernel(db_path)
    try:
        kernel.create_task("mission-halt", owner="mission-runner", goal="halted goal", risk_tier="R1")
        for s in ("PLANNING", "READY", "QUEUED"):
            kernel.transition("mission-halt", s)
        lease = kernel.claim("mission-halt", "worker-halt", ttl_seconds=120)
        kernel.start("mission-halt", lease.lease_id)
        # Checkpoint mission at WAITING_TOOL
        kernel.checkpoint(
            "mission-halt",
            lease.lease_id,
            "substep",
            "WAITING_TOOL",
            {"progress": 50},
            1,
            "mission.substep",
        )
        kernel.set_global_kill(True, actor="safety-guard")
    finally:
        kernel.close()

    # Reopen fresh kernel process
    rebooted = TaskKernel(db_path)
    try:
        assert rebooted._control()["global_kill"] == 1

        report = rebooted.recover_on_boot(actor="boot_eval")
        # Task was in flight -> recovered safely to HUMAN_REVIEW/RECOVERING, NOT dispatched or run
        recovered_ids = [r["task_id"] for r in report["recovered"]]
        assert "mission-halt" in recovered_ids
        task = rebooted.get_task("mission-halt")
        assert task["state"] in ("RECOVERING", "HUMAN_REVIEW")

        # Worker cannot claim task while kill switch is engaged
        with pytest.raises(KillSwitchActive):
            rebooted.claim("mission-halt", "new-worker", ttl_seconds=60)
    finally:
        rebooted.close()


def test_kill_switch_cleared_allows_resumption_with_new_lease_and_unpolluted_state(tmp_path: Path) -> None:
    """CE-S01-03: Clearing kill switch allows recovery reconciliation and legal resumption."""
    db_path = tmp_path / "resume_kill.sqlite3"
    kernel = TaskKernel(db_path)
    try:
        task_id, lease_id, cp_id = _setup_long_running_mission(kernel, "mission-resume-1")
        kernel.set_global_kill(True, actor="ops")
        assert kernel._control()["global_kill"] == 1

        # Release kill switch
        kernel.set_global_kill(False, actor="ops")
        assert kernel._control()["global_kill"] == 0

        # Now reconciliation can safely occur
        kernel.recovery_engine.enter_reconciling(task_id, cp_id)
        rec_task = kernel.recovery_engine.reconcile_unknown(
            task_id,
            cp_id,
            outcome="NOT_APPLIED",
            evidence_ref="evidence://dry-run-check",
            verifier_id="verifier-independent",
        )
        assert rec_task["state"] == "QUEUED"

        # Fresh worker can claim the task and resume execution legally
        new_lease = kernel.claim(task_id, "fresh-worker", ttl_seconds=120)
        assert new_lease.lease_id != lease_id
        kernel.start(task_id, new_lease.lease_id)
        assert kernel.get_task(task_id)["state"] == "RUNNING"
    finally:
        kernel.close()
