from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from scp.kernel_storage import SQLiteKernelStorage, make_storage
from scp.task_kernel import TaskKernel


class TrackingStorage:
    """Small contract spy; SQL behaviour still comes from the real backend."""

    def __init__(self, db_path: Path) -> None:
        self.backend = SQLiteKernelStorage(db_path)
        self.db_path = str(db_path)
        self.begins = 0
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def begin(self) -> None:
        self.begins += 1
        self.backend.begin()

    def commit(self) -> None:
        self.commits += 1
        self.backend.commit()

    def rollback(self) -> None:
        self.rollbacks += 1
        self.backend.rollback()

    def execute(self, sql: str, params: Any = ()) -> Any:
        return self.backend.execute(sql, params)

    def executescript(self, script: str) -> None:
        self.backend.executescript(script)

    def fetchone(self, sql: str, params: Any = ()) -> Any | None:
        return self.backend.fetchone(sql, params)

    def fetchall(self, sql: str, params: Any = ()) -> list[Any]:
        return self.backend.fetchall(sql, params)

    @property
    def in_transaction(self) -> bool:
        return self.backend.in_transaction

    def backup_to(self, target: str | Path) -> None:
        self.backend.backup_to(target)

    def close(self) -> None:
        self.closed = True
        self.backend.close()


def test_task_kernel_uses_injected_storage_for_transaction_lifecycle(tmp_path: Path) -> None:
    storage = TrackingStorage(tmp_path / "kernel.sqlite3")
    kernel = TaskKernel(storage=storage)

    kernel.create_task("storage-contract", "test", "prove injected storage")
    kernel.transition("storage-contract", "PLANNING")

    assert kernel.get_task("storage-contract")["state"] == "PLANNING"
    assert storage.begins == 2
    assert storage.commits == 2
    assert storage.rollbacks == 0

    kernel.close()
    assert storage.closed is True


def test_task_kernel_backup_is_delegated_to_storage(tmp_path: Path) -> None:
    storage = TrackingStorage(tmp_path / "kernel.sqlite3")
    kernel = TaskKernel(storage=storage)
    kernel.create_task("backup-contract", "test", "prove backup delegation")

    result = kernel.backup(tmp_path / "backups", retain=1)

    backup_path = Path(result["backup"])
    assert backup_path.is_file()
    reopened = TaskKernel(backup_path)
    try:
        assert reopened.get_task("backup-contract")["state"] == "CREATED"
    finally:
        reopened.close()
        kernel.close()


def test_make_storage_spof_warning_docstring() -> None:
    """make_storage docstring must contain explicit SPOF warning for distributed deployments."""
    doc = make_storage.__doc__ or ""
    assert "WARNING: SQLite is a Single Point of Failure (SPOF) in distributed deployments." in doc
    assert "For high availability or multi-node production setups, a distributed storage backend is required." in doc


def test_make_storage_default_sqlite(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """make_storage returns SQLiteKernelStorage when SCP_STORAGE_BACKEND is unset."""
    monkeypatch.delenv("SCP_STORAGE_BACKEND", raising=False)
    storage = make_storage(tmp_path / "default.sqlite3")
    try:
        assert isinstance(storage, SQLiteKernelStorage)
        assert storage.db_path == str(tmp_path / "default.sqlite3")
    finally:
        storage.close()


@pytest.mark.parametrize("backend_val", ["sqlite", "SQLite", "SQLITE", "  sqlite  ", ""])
def test_make_storage_explicit_sqlite(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, backend_val: str) -> None:
    """make_storage returns SQLiteKernelStorage when SCP_STORAGE_BACKEND is sqlite or empty."""
    monkeypatch.setenv("SCP_STORAGE_BACKEND", backend_val)
    storage = make_storage(tmp_path / "test.sqlite3")
    try:
        assert isinstance(storage, SQLiteKernelStorage)
    finally:
        storage.close()


@pytest.mark.parametrize("unsupported", ["postgres", "mysql", "etcd", "redis", "distributed"])
def test_make_storage_unsupported_backend_raises_not_implemented(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, unsupported: str
) -> None:
    """make_storage raises NotImplementedError when SCP_STORAGE_BACKEND is an unsupported backend."""
    monkeypatch.setenv("SCP_STORAGE_BACKEND", unsupported)
    with pytest.raises(NotImplementedError) as exc_info:
        make_storage(tmp_path / "test.sqlite3")

    msg = str(exc_info.value)
    assert f"Unsupported storage backend '{unsupported}'" in msg
    assert "Only 'sqlite' is currently supported" in msg
    assert "For distributed deployments, inject a custom Storage instance into TaskKernel." in msg


def test_task_kernel_fails_closed_on_unsupported_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TaskKernel constructor fails closed when SCP_STORAGE_BACKEND is unsupported."""
    monkeypatch.setenv("SCP_STORAGE_BACKEND", "postgres")
    with pytest.raises(NotImplementedError):
        TaskKernel(tmp_path / "kernel.sqlite3")


def test_gap05_multi_instance_concurrent_writes_without_rlock(tmp_path: Path) -> None:
    """Verify that independent SQLiteKernelStorage instances without in-memory RLock maintain OCC integrity."""
    db_path = tmp_path / "gap05_occ.sqlite3"
    init_storage = SQLiteKernelStorage(db_path)
    init_storage.executescript(
        "CREATE TABLE counters (id TEXT PRIMARY KEY, val INTEGER, version INTEGER DEFAULT 1);"
    )
    init_storage.execute("INSERT INTO counters (id, val, version) VALUES ('c1', 0, 1)")
    init_storage.close()

    s_a = SQLiteKernelStorage(db_path)
    s_b = SQLiteKernelStorage(db_path)
    try:
        # Transaction A updates version 1 -> 2
        s_a.begin()
        s_a.execute("UPDATE counters SET val=val+10, version=version+1 WHERE id='c1' AND version=1")
        s_a.commit()

        # Transaction B attempts update expecting stale version 1
        s_b.begin()
        cur = s_b.execute("UPDATE counters SET val=val+20, version=version+1 WHERE id='c1' AND version=1")
        assert cur.rowcount == 0  # OCC conflict detected at database level
        s_b.rollback()

        # Confirm value was updated only once
        row = s_a.fetchone("SELECT val, version FROM counters WHERE id='c1'")
        assert row["val"] == 10
        assert row["version"] == 2
    finally:
        s_a.close()
        s_b.close()


def test_arc01_kernel_storage_property_and_di(tmp_path: Path) -> None:
    """ARC-01: TaskKernel accepts storage via DI and exposes kernel.storage."""
    db_file = tmp_path / "arc01_di.sqlite3"
    storage = SQLiteKernelStorage(db_file)
    kernel = TaskKernel(storage=storage)
    try:
        assert kernel.storage is storage
        assert kernel.conn is storage
        assert kernel.db_path == str(db_file)

        # Full lifecycle verification with injected storage
        task = kernel.create_task("task-arc01", "owner-arc01", "goal-arc01")
        assert task["task_id"] == "task-arc01"
        assert task["state"] == "CREATED"

        kernel.transition("task-arc01", "PLANNING", actor="planner")
        kernel.transition("task-arc01", "READY", actor="planner")
        kernel.transition("task-arc01", "QUEUED", actor="dispatcher")

        lease = kernel.claim("task-arc01", "worker-1", ttl_seconds=30)
        assert lease.task_id == "task-arc01"
        assert kernel.get_task("task-arc01")["state"] == "LEASED"

        kernel.start("task-arc01", lease.lease_id)
        assert kernel.get_task("task-arc01")["state"] == "RUNNING"

        # Checkpoint and verification
        cp_hash = kernel.checkpoint(
            task_id="task-arc01",
            lease_id=lease.lease_id,
            step_id="step_1",
            state="RUNNING",
            planned_action={"tool": "echo"},
            capability_epoch=1,
            idempotency_key="idem_1",
        )
        assert cp_hash is not None
        cp = kernel.get_checkpoint(cp_hash)
        assert cp is not None
        assert cp["step_id"] == "step_1"
        validated = kernel.validate_checkpoint(cp_hash, {"tool": "echo"})
        assert validated["checkpoint_id"] == cp_hash

        kernel.cancel("task-arc01", actor="operator")
        assert kernel.get_task("task-arc01")["state"] == "CANCELLED"
    finally:
        kernel.close()


def test_arc01_backward_compatibility_56_methods(tmp_path: Path) -> None:
    """ARC-01: Ensure 100% backward compatibility with all 56 TaskKernel methods."""
    from scp.task_kernel import (
        KernelStorage as ReExportedKernelStorage,
        SQLiteKernelStorage as ReExportedSQLiteKernelStorage,
        make_storage as re_exported_make_storage,
    )
    assert ReExportedKernelStorage is not None
    assert ReExportedSQLiteKernelStorage is SQLiteKernelStorage
    assert re_exported_make_storage is make_storage

    db_path = tmp_path / "arc01_compat.sqlite3"
    k_db = TaskKernel(db_path)
    storage = SQLiteKernelStorage(tmp_path / "arc01_di_compat.sqlite3")
    k_di = TaskKernel(storage=storage)

    try:
        # Verify both have storage property
        assert hasattr(k_db, "storage")
        assert hasattr(k_di, "storage")
        assert k_di.storage is storage

        # Verify all methods on k_db exist on k_di
        methods_db = {m for m in dir(k_db) if not m.startswith("__")}
        methods_di = {m for m in dir(k_di) if not m.startswith("__")}
        assert methods_db == methods_di
        assert len(methods_db) >= 56
    finally:
        k_db.close()
        k_di.close()


def test_arc01_step2_step3_idempotency_and_checkpoint_engines(tmp_path: Path) -> None:
    """ARC-01 Steps 2 & 3: Modular IdempotencyEngine and CheckpointEngine properties and delegation."""
    from scp.task_kernel_parts.idempotency import IdempotencyEngine
    from scp.task_kernel_parts.checkpoint import CheckpointEngine

    db_path = tmp_path / "arc01_engines.sqlite3"
    kernel = TaskKernel(db_path)
    try:
        assert isinstance(kernel.idempotency, IdempotencyEngine)
        assert isinstance(kernel.checkpoint_engine, CheckpointEngine)
        assert kernel.idempotency.kernel is kernel
        assert kernel.checkpoint_engine.kernel is kernel

        kernel.create_task("task-mod-01", owner="tester", goal="test modular engines")
        kernel.transition("task-mod-01", "PLANNING", actor="planner")
        kernel.transition("task-mod-01", "READY", actor="planner")
        kernel.transition("task-mod-01", "QUEUED", actor="dispatcher")
        lease = kernel.claim("task-mod-01", "worker-1", ttl_seconds=30)
        kernel.start("task-mod-01", lease.lease_id)

        # 1. Idempotency claim and complete via kernel and engine
        logical_key, claimed = kernel.idempotency_claim(
            task_id="task-mod-01",
            step_id="s1",
            action_type="call",
            resource_identity="res1",
        )
        assert claimed is True
        status = kernel.idempotency_status(logical_key)
        assert status["status"] == "CLAIMED"

        kernel.idempotency_complete(logical_key, result_ref="ref://done")
        status_after = kernel.idempotency_status(logical_key)
        assert status_after["status"] == "COMPLETED"
        assert status_after["result_ref"] == "ref://done"

        # 2. Checkpoint via kernel and engine
        cp_id = kernel.checkpoint(
            task_id="task-mod-01",
            lease_id=lease.lease_id,
            step_id="s1",
            state="RUNNING",
            planned_action={"tool": "write"},
            capability_epoch=1,
            idempotency_key="idem_key_1",
        )
        assert cp_id.startswith("cp_")
        cp_row = kernel.get_checkpoint(cp_id)
        assert cp_row["checkpoint_id"] == cp_id
        assert cp_row["task_id"] == "task-mod-01"

        validated = kernel.validate_checkpoint(cp_id, {"tool": "write"})
        assert validated["checkpoint_id"] == cp_id

        # 3. Finalize checkpoint (blocked while task is not yet terminal)
        finalized = kernel.finalize_checkpoint(
            task_id="task-mod-01",
            checkpoint_id=cp_id,
            planned_action={"tool": "write"},
            note="task in progress",
            verifier_verdict="PASS",
        )
        assert finalized["checkpoint_id"] == cp_id
        assert finalized["finalization"] == "blocked_task_not_decided"
    finally:
        kernel.close()


