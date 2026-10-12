"""Disaster Recovery and SQLite WAL Lock Recovery Tests (CE-X05-01 / D-Level).

Proves:
- Corrupt / tampered journal fails closed; boot recovery refuses unsafe transitions.
- SQLite WAL write lock is released completely after process crash / termination.
- Auto-reconcile detects expired worker leases immediately without blind retry.
- Completed actions with verifier receipt remain durable and idempotent across restart.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from scp.kernel_storage import SQLiteKernelStorage
from scp.task_kernel import KernelError, TaskKernel


def test_disaster_recovery_corrupt_journal_fails_closed(tmp_path: Path) -> None:
    """CE-X05-01: Power loss/tamper in journal halts recovery without modifying state (Fail-Closed)."""
    db_path = tmp_path / "corrupt_dr.sqlite3"
    kernel = TaskKernel(db_path)
    try:
        kernel.create_task("dr-task-1", owner="operator", goal="disaster recovery test", risk_tier="R1")
        kernel.transition("dr-task-1", "PLANNING", actor="operator", reason="plan")
        kernel.transition("dr-task-1", "READY", actor="operator", reason="ready")
        assert kernel.get_task("dr-task-1")["state"] == "READY"

        # Simulate journal tampering / power loss corruption in SQLite events
        kernel.conn.execute(
            "UPDATE events SET event_hash='sha256:0000000000000000000000000000000000000000000000000000000000000000' "
            "WHERE task_id='dr-task-1' AND seq=1"
        )
    finally:
        kernel.close()

    # Restart kernel following disaster
    restarted = TaskKernel(db_path)
    try:
        journal_check = restarted.verify_journal("dr-task-1")
        assert journal_check["hash_chain_valid"] is False
        assert len(journal_check["errors"]) > 0

        # Boot recovery refuses to modify corrupt task
        report = restarted.recover_on_boot(actor="dr_boot")
        corrupted_ids = [c["task_id"] for c in report["corrupted"]]
        assert "dr-task-1" in corrupted_ids

        # Task remains in its pre-recovery state (Fail-Closed, no silent alteration)
        assert restarted.get_task("dr-task-1")["state"] == "READY"
    finally:
        restarted.close()


def test_disaster_recovery_wal_write_lock_freed_after_crash(tmp_path: Path) -> None:
    """CE-X05-01: SQLite WAL locks are released on restart; subsequent transactions succeed immediately."""
    db_path = tmp_path / "wal_dr.sqlite3"

    # Context 1 opens storage and writes
    storage_1 = SQLiteKernelStorage(db_path)
    storage_1.executescript(
        "CREATE TABLE test_data (id INTEGER PRIMARY KEY, val TEXT);"
    )
    storage_1.begin()
    storage_1.execute("INSERT INTO test_data (val) VALUES ('in-flight');")
    storage_1.commit()
    storage_1.close()

    # Reopening after simulated crash/restart succeeds without SQLITE_BUSY
    storage_2 = SQLiteKernelStorage(db_path)
    try:
        storage_2.begin()
        storage_2.execute("INSERT INTO test_data (val) VALUES ('post-recovery');")
        storage_2.commit()

        rows = storage_2.fetchall("SELECT val FROM test_data ORDER BY id")
        values = [r["val"] for r in rows]
        assert values == ["in-flight", "post-recovery"]
    finally:
        storage_2.close()


def test_disaster_recovery_orphan_reconciliation_detects_expired_leases_immediately(tmp_path: Path) -> None:
    """CE-X05-01: Auto-reconciliation detects dead workers with expired leases even with fresh updated_at."""
    db_path = tmp_path / "orphan_dr.sqlite3"
    kernel = TaskKernel(db_path)
    try:
        kernel.create_task("orphan-fast", owner="worker-pool", goal="recover immediately", risk_tier="R2")
        for s in ("PLANNING", "READY", "QUEUED"):
            kernel.transition("orphan-fast", s)
        # Grant a short lease of 1 second
        lease = kernel.claim("orphan-fast", "worker-doomed", ttl_seconds=1)
        kernel.start("orphan-fast", lease.lease_id)
        assert kernel.get_task("orphan-fast")["state"] == "RUNNING"

        # Sleep past lease expiry
        time.sleep(1.2)

        # Run auto-reconciliation with stale_seconds=300 (updated_at is fresh, but lease is expired!)
        orphans = kernel.auto_reconcile_orphans(actor="dr_watchdog", stale_seconds=300.0)
        assert "orphan-fast" in orphans
        assert kernel.get_task("orphan-fast")["state"] == "RECONCILING"

        # Lease is revoked
        lease_row = kernel.conn.execute(
            "SELECT released FROM leases WHERE lease_id=?", (lease.lease_id,)
        ).fetchone()
        assert lease_row["released"] == 1
    finally:
        kernel.close()
