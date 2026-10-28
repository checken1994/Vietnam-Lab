"""R22.1 · error paths report the real task state or an explicit UNKNOWN, never a guessed FAILED.

Follow-up to R22 (r22-done.md §6):
- ``ledger_writer.record_task_failure``: an unreadable task was written to the unified ledger as
  FAILED, even when the kernel had just moved it to RETRY_SCHEDULED.
- Hands bridge outer ``except``: with no lease the kernel view was hard-coded ``state: FAILED``
  (under a global kill the task really sits in CREATED); with a lease the view re-read the task and
  a failing read escaped ``execute`` itself, after the driver had already run.
- Hands bridge replay branch: ``_public_kernel`` re-read the row it had just read, so a purge in
  between turned a clean replay into "failed before dispatch: NotFound" with a fake FAILED.

Zero-mock: real AskKernelAdapter / TaskKernelHandsBridge on SQLite files under tmp_path; a SECOND
real TaskKernel calls public APIs (``set_global_kill``, ``purge_tasks``) at precise points
scheduled from the watched connection's SQLite trace callback (same technique as
test_r22_handler_read_masking / test_security_sweep_s4).
"""
from __future__ import annotations

import asyncio
import json
import re
import uuid

import pytest

from scp.ask_kernel_adapter import AskKernelAdapter
from scp.task_kernel import TaskKernel

BEGIN = r"BEGIN"
SELECT_TASK = r"SELECT \* FROM tasks WHERE task_id"
SELECT_CHECKPOINT = r"SELECT \* FROM checkpoints"


class Choreography:
    """Run ``steps`` (pattern, label, action[, when]) in order; a step fires on the first statement
    matching its regex that the watched connection starts outside a transaction (and ``when()``)."""

    def __init__(self, conn, steps):
        self.conn, self.steps, self.done, self.errors = conn, steps, [], []
        conn.set_trace_callback(self)

    def __call__(self, sql: str) -> None:
        if len(self.done) >= len(self.steps) or self.conn.in_transaction:
            return
        pattern, label, action, *when = self.steps[len(self.done)]
        if re.match(pattern, sql.strip(), re.I) and (not when or when[0]()):
            self.done.append(label)
            try:
                action()
            except Exception as exc:  # surfaced through self.errors
                self.errors.append(f"{label}: {type(exc).__name__}: {exc}")

    def stop(self) -> None:
        self.conn.set_trace_callback(None)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("SCP_CAPABILITY_SECRET", "r22-1-capability-secret-0123456789abcd")
    monkeypatch.setenv("SCP_JOURNAL_SECRET", "r22-1-journal-secret-distinct-012345678")
    monkeypatch.setenv("SCP_MULTI_LLM_CROSSCHECK", "0")
    monkeypatch.setenv("SCP_DATA_DIR", str(tmp_path / "data"))
    return tmp_path


# --- ledger_writer.record_task_failure -------------------------------------------------------

def test_failure_ledger_records_unknown_not_failed_for_an_unreadable_task(env):
    adapter = AskKernelAdapter(str(env / "kernel.sqlite3"), str(env / "trace.jsonl"), autonomous_mode=False)
    other = TaskKernel(str(env / "kernel.sqlite3"))
    try:
        task = adapter.begin("what color is the sky?", ["sky is blue"], "", "r22-1-ledger")
        tid = task["task_id"]
        observed = {}

        def purge_after_noting_the_real_state():
            observed["state"] = other.get_task(tid)["state"]
            other.purge_tasks(tid)

        chor = Choreography(adapter.kernel._storage._get_conn(), [
            (SELECT_CHECKPOINT, "checkpoint finalized", lambda: None),
            (SELECT_TASK, "purge at the ledger read", purge_after_noting_the_real_state),
        ])
        try:
            adapter.fail(task, "r22-1", failure_classification="RETRYABLE")
        finally:
            chor.stop()
        assert chor.done == ["checkpoint finalized", "purge at the ledger read"] and not chor.errors
        assert observed["state"] == "RETRY_SCHEDULED"  # the guess "FAILED" would be false
        rows = [json.loads(line) for line in (env / "data" / "trace_ledger.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
        fields = [r["fields"] for r in rows if r.get("fields", {}).get("task_id") == tid]
        assert len(fields) == 1, fields
        assert fields[0]["outcome"] == fields[0]["final_outcome"] == "UNKNOWN"
        assert fields[0]["kernel_read_error"].startswith("NotFound")
    finally:
        adapter.kernel.close()
        other.close()


def test_failure_ledger_still_records_the_kernel_state_when_readable(env):
    """Positive control: no purge, the ledger carries the state the kernel reports."""
    adapter = AskKernelAdapter(str(env / "kernel.sqlite3"), str(env / "trace.jsonl"), autonomous_mode=False)
    try:
        task = adapter.begin("what color is the sky?", ["sky is blue"], "", "r22-1-ledger-ok")
        adapter.fail(task, "r22-1", failure_classification="RETRYABLE")
        rows = [json.loads(line) for line in (env / "data" / "trace_ledger.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
        fields = [r["fields"] for r in rows if r.get("fields", {}).get("task_id") == task["task_id"]]
        assert [f["outcome"] for f in fields] == ["RETRY_SCHEDULED"]
        assert "kernel_read_error" not in fields[0]
    finally:
        adapter.kernel.close()


# --- Hands bridge ----------------------------------------------------------------------------

class _Hands:
    def __init__(self, root):
        from scp.hands.hands_executor import HandsExecutor
        from scp.hands.task_kernel_bridge import TaskKernelHandsBridge
        from scp.pc_control.pc_controller import PCController
        from scp.security.capability_epoch import CapabilityAuthority

        self.workspace = root / "workspace"
        self.workspace.mkdir()
        self.cap = CapabilityAuthority(root / "capability_state.json")
        executor = HandsExecutor(controller=PCController(working_dir=self.workspace), capability_authority=self.cap,
                                 data_dir=root / "hands_data")
        self.bridge = TaskKernelHandsBridge(executor, db_path=root / "kernel.sqlite3")
        self.other = TaskKernel(str(root / "kernel.sqlite3"))

    def write(self, name: str, content: str, request_key: str, steps=None):
        target = self.workspace / name
        token = self.cap.issue("hands:pc.write_file")
        confirmation_id = self.bridge.executor.controller.human_store.record_confirmation(
            action="pc.write_file", target=str(target.resolve()), ttl_seconds=60,
        )

        async def go():
            chor = Choreography(self.bridge.kernel._storage._get_conn(), steps) if steps else None
            try:
                result = await self.bridge.execute(
                    action="pc.write_file",
                    params={"path": str(target), "content": content, "confirmation_id": confirmation_id},
                    capability_level=3, approved=True, request_key=request_key, capability_token=token,
                )
            finally:
                if chor:
                    chor.stop()
            return result, chor

        return asyncio.run(go())

    def close(self):
        self.bridge.kernel.close()
        self.other.close()


def test_bridge_pre_dispatch_failure_reports_the_real_state_not_failed(env):
    """Outer except, no lease: a global kill stops the lifecycle; the task really sits in CREATED."""
    hands = _Hands(env)
    try:
        hands.other.set_global_kill(True, actor="r22-1-operator")
        result, _ = hands.write("killed.txt", "x", f"r22-1-kill-{uuid.uuid4().hex}")
        assert result["success"] is False and "KillSwitchActive" in result["error"]
        real = hands.other.get_task(result["kernel"]["taskId"])["state"]
        assert real == "CREATED"
        assert result["kernel"]["state"] == real
        assert not (hands.workspace / "killed.txt").exists()
    finally:
        hands.close()


def test_bridge_post_dispatch_failure_with_an_unreadable_task_does_not_escape(env):
    """Outer except, after the driver ran: the task is purged, every kernel write and read fails."""
    hands = _Hands(env)
    target = hands.workspace / "dispatched.txt"
    try:
        result, chor = hands.write("dispatched.txt", "dispatched", f"r22-1-post-{uuid.uuid4().hex}", steps=[
            (BEGIN, "purge right after the driver wrote", lambda: hands.other.purge_tasks("hands-%"), target.exists),
        ])
        assert chor.done == ["purge right after the driver wrote"] and not chor.errors
        assert target.read_text(encoding="utf-8") == "dispatched"  # the side effect did happen
        assert result["success"] is False and result["requiresRecovery"] is True
        assert result["safeToRetry"] is False
        assert result["kernel"]["state"] == "UNKNOWN"
        assert result["kernel"]["readError"].startswith("NotFound")
    finally:
        hands.close()


def test_bridge_replay_uses_the_row_it_already_read(env):
    """Replay branch: a purge right after the existing-row read cannot break the replay."""
    hands = _Hands(env)
    key = f"r22-1-replay-{uuid.uuid4().hex}"
    try:
        first, _ = hands.write("replay.txt", "written-once", key)
        assert first.get("success") is True, first
        tid = first["kernel"]["taskId"]
        replay, chor = hands.write("replay.txt", "MUTATED_BY_REPLAY", key, steps=[
            (BEGIN, "create_task collides", lambda: None),
            (SELECT_TASK, "existing row read", lambda: None),
            (SELECT_TASK, "purge at a second read", lambda: hands.other.purge_tasks(tid)),
        ])
        assert chor.done == ["create_task collides", "existing row read"], chor.done  # one read only
        assert replay["replayed"] is True and replay["safeToRetry"] is False
        assert replay["kernel"]["state"] == "COMPLETED"
        assert (hands.workspace / "replay.txt").read_text(encoding="utf-8") == "written-once"
    finally:
        hands.close()
