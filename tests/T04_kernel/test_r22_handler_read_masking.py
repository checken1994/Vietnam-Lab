"""R22 · a task read inside an error handler must not replace the error being handled.

Sites (r20-r21-design.md §3.2): finalize_flow 143 / 169 / 192, AskKernelAdapter
``_escalate_to_human_review`` and ``_fail_closed_autonomous`` (documented "never raise"), and the
Hands bridge duplicate-request branch. Before R22 a ``get_task`` in those handlers raised
``NotFound`` when the row had vanished, hiding InvalidTransition / OptimisticLockError / the
auto-resolve failure, breaking the never-raise contract, and (Hands) letting the pre-dispatch
handler report a non-existent task as FAILED.

Zero-mock: real AskKernelAdapter / TaskKernelHandsBridge on SQLite files under tmp_path. The race
is produced by a SECOND real TaskKernel on the same file calling public kernel APIs
(``expire_leases`` with a logical clock, ``set_task_kill``, ``purge_tasks``) at precise points,
scheduled from the adapter connection's SQLite trace callback (the same instrumentation as
test_security_sweep_s4). The callback only acts while the adapter connection holds no
transaction, so the second writer never waits on it.
"""
from __future__ import annotations

import asyncio
import re
import time
import uuid

import pytest

from scp.ask_kernel_adapter import AskKernelAdapter
from scp.task_kernel import InvalidTransition, NotFound, StaleLease, TaskKernel

BEGIN = r"BEGIN"
SELECT_TASK = r"SELECT \* FROM tasks WHERE task_id"
QUESTION, CONTEXTS = "what color is the sky?", ["sky is blue"]
# Chatbot-lane response shape whose judge provenance the real verifier accepts (VERIFIED).
JUDGED = {
    "final_answer": "The sky is blue",
    "verdict": "PASS",
    "governance_decision": "UPHOLD",
    "confidence": 0.9,
    "lane": "LANE_CHATBOT",
    "judge_evaluated": True,
    "evidence": {"judge_evaluated": True, "governance_decision": "UPHOLD"},
    "elapsed_ms": 1.0,
    "trace_id": "trace-r22",
}


class _Req:
    question = QUESTION
    contexts = CONTEXTS
    retrieved_context = ""
    session_id = "r22"


class Choreography:
    """Run ``steps`` in order; step i fires on the first statement matching its regex that the
    watched connection starts while it holds no transaction."""

    def __init__(self, conn, steps):
        self.conn, self.steps, self.done, self.errors = conn, steps, [], []
        conn.set_trace_callback(self)

    def __call__(self, sql: str) -> None:
        if len(self.done) >= len(self.steps) or self.conn.in_transaction:
            return
        pattern, label, action = self.steps[len(self.done)]
        if re.match(pattern, sql.strip(), re.I):
            self.done.append(label)
            try:
                action()
            except Exception as exc:  # surfaced by assert_all_ran
                self.errors.append(f"{label}: {type(exc).__name__}: {exc}")

    def stop(self) -> None:
        self.conn.set_trace_callback(None)

    def assert_all_ran(self) -> None:
        assert self.done == [label for _, label, _ in self.steps], self.done
        assert not self.errors, self.errors


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("SCP_CAPABILITY_SECRET", "r22-capability-secret-0123456789abcdef")
    monkeypatch.setenv("SCP_JOURNAL_SECRET", "r22-journal-secret-distinct-0123456789")
    monkeypatch.setenv("SCP_MULTI_LLM_CROSSCHECK", "0")
    return tmp_path


def _pair(tmp_path, *, autonomous: bool):
    db = str(tmp_path / "kernel.sqlite3")
    adapter = AskKernelAdapter(db, str(tmp_path / "trace.jsonl"), autonomous_mode=autonomous)
    return adapter, TaskKernel(db)


def _finalize_with(adapter, task, steps):
    async def go():
        chor = Choreography(adapter.kernel._storage._get_conn(), steps)
        try:
            return await adapter.finalize(task, JUDGED, _Req())
        finally:
            chor.stop()
            chor.assert_all_ran()

    return asyncio.run(go())


def _expire_all_leases(other: TaskKernel) -> None:
    other.expire_leases(now=time.time() + 100_000)  # watchdog sweep with a logical clock


# --- finalize_flow ---------------------------------------------------------------------------

def test_verifying_race_keeps_invalid_transition_when_the_task_vanishes(env):
    """Site 143: RUNNING->VERIFYING races an operator kill, then the task is purged."""
    adapter, other = _pair(env, autonomous=False)
    try:
        task = adapter.begin(QUESTION, CONTEXTS, "", "r22-143")
        tid = task["task_id"]
        with pytest.raises(InvalidTransition, match="CANCELLED->VERIFYING") as caught:
            _finalize_with(adapter, task, [
                (BEGIN, "operator kill", lambda: other.set_task_kill(tid, actor="r22-operator")),
                (SELECT_TASK, "purge", lambda: other.purge_tasks(tid)),
            ])
        assert not isinstance(caught.value, NotFound)
        assert any("NotFound" in note for note in getattr(caught.value, "__notes__", []))
    finally:
        adapter.kernel.close()
        other.close()


def test_verifying_race_with_a_readable_task_still_reports_the_terminal_state(env):
    """Positive control for site 143: without the purge the handler result is unchanged."""
    adapter, other = _pair(env, autonomous=False)
    try:
        task = adapter.begin(QUESTION, CONTEXTS, "", "r22-143-control")
        tid = task["task_id"]
        result = _finalize_with(adapter, task, [
            (BEGIN, "operator kill", lambda: other.set_task_kill(tid, actor="r22-operator")),
        ])
        assert result["task"]["state"] == "CANCELLED"
        assert result["verification"]["failures"] == ["task_terminal_before_verification"]
    finally:
        adapter.kernel.close()
        other.close()


def test_commit_race_keeps_the_commit_error_when_the_task_vanishes(env):
    """Site 169: the lease expires under commit_verification_result, then the task is purged."""
    adapter, other = _pair(env, autonomous=False)
    try:
        task = adapter.begin(QUESTION, CONTEXTS, "", "r22-169")
        tid = task["task_id"]
        with pytest.raises(StaleLease, match="already been released") as caught:
            _finalize_with(adapter, task, [
                (BEGIN, "RUNNING->VERIFYING proceeds", lambda: None),
                (BEGIN, "watchdog lease expiry", lambda: _expire_all_leases(other)),
                (SELECT_TASK, "purge", lambda: other.purge_tasks(tid)),
            ])
        assert not isinstance(caught.value, NotFound)
        assert any("NotFound" in note for note in getattr(caught.value, "__notes__", []))
    finally:
        adapter.kernel.close()
        other.close()


def test_auto_resolve_failure_stays_the_reported_cause_when_the_task_vanishes(env):
    """Site 192 (autonomous): HUMAN_REVIEW auto-resolve fails, then the re-read fails too."""
    adapter, other = _pair(env, autonomous=True)
    try:
        task = adapter.begin(QUESTION, CONTEXTS, "", "r22-192")
        tid = task["task_id"]
        with pytest.raises(InvalidTransition, match="cannot auto-resolve") as caught:
            _finalize_with(adapter, task, [
                (BEGIN, "RUNNING->VERIFYING proceeds", lambda: None),
                (BEGIN, "watchdog lease expiry", lambda: _expire_all_leases(other)),
                (BEGIN, "operator kill", lambda: other.set_task_kill(tid, actor="r22-operator")),
                (SELECT_TASK, "purge", lambda: other.purge_tasks(tid)),
            ])
        assert not isinstance(caught.value, NotFound)
        assert any("NotFound" in note for note in getattr(caught.value, "__notes__", []))
    finally:
        adapter.kernel.close()
        other.close()


# --- never-raise adapter helpers -------------------------------------------------------------

@pytest.mark.parametrize("method,autonomous", [("_fail_closed_autonomous", True), ("_escalate_to_human_review", False)])
def test_never_raise_helpers_report_unknown_for_a_purged_task(env, method, autonomous):
    """Sites 466/428: the entry read of a purged task returns an explicit UNKNOWN result."""
    adapter, other = _pair(env, autonomous=autonomous)
    try:
        tid = adapter.begin(QUESTION, CONTEXTS, "", f"r22-{method}")["task_id"]
        other.purge_tasks(tid)
        result = getattr(adapter, method)(tid, reason="r22")
        assert result["state"] == "UNKNOWN"
        assert result["kernel_read_error"].startswith("NotFound")
    finally:
        adapter.kernel.close()
        other.close()


def test_fail_closed_autonomous_except_branch_never_raises(env):
    """Site 479/486: fail_task_fail_closed fails (task purged at its BEGIN); no NotFound escapes."""
    adapter, other = _pair(env, autonomous=True)
    try:
        tid = adapter.begin(QUESTION, CONTEXTS, "", "r22-479")["task_id"]
        chor = Choreography(adapter.kernel._storage._get_conn(), [
            (BEGIN, "purge", lambda: other.purge_tasks(tid)),
        ])
        try:
            result = adapter._fail_closed_autonomous(tid, reason="r22")
        finally:
            chor.stop()
        chor.assert_all_ran()
        assert result["state"] == "UNKNOWN"
        assert result["kernel_error"].startswith("NotFound")  # the fail-closed error itself
        assert result["kernel_read_error"].startswith("NotFound")
    finally:
        adapter.kernel.close()
        other.close()


def test_escalation_keeps_the_transition_error_when_the_task_vanishes(env):
    """Site 450/457: the HUMAN_REVIEW transition races a kill, then the task is purged."""
    adapter, other = _pair(env, autonomous=False)
    try:
        tid = adapter.begin(QUESTION, CONTEXTS, "", "r22-450")["task_id"]
        chor = Choreography(adapter.kernel._storage._get_conn(), [
            (BEGIN, "operator kill", lambda: other.set_task_kill(tid, actor="r22-operator")),
            (SELECT_TASK, "purge", lambda: other.purge_tasks(tid)),
        ])
        try:
            result = adapter._escalate_to_human_review(tid, reason="r22")
        finally:
            chor.stop()
        chor.assert_all_ran()
        assert result["state"] == "UNKNOWN"
        assert result["kernel_error"] == "InvalidTransition: CANCELLED->HUMAN_REVIEW"
        assert result["kernel_read_error"].startswith("NotFound")
    finally:
        adapter.kernel.close()
        other.close()


def test_escalation_with_a_readable_task_returns_the_kernel_row(env):
    """Positive control for site 450: the kernel row is returned when the re-read works."""
    adapter, other = _pair(env, autonomous=False)
    try:
        tid = adapter.begin(QUESTION, CONTEXTS, "", "r22-450-control")["task_id"]
        chor = Choreography(adapter.kernel._storage._get_conn(), [
            (BEGIN, "operator kill", lambda: other.set_task_kill(tid, actor="r22-operator")),
        ])
        try:
            result = adapter._escalate_to_human_review(tid, reason="r22")
        finally:
            chor.stop()
        chor.assert_all_ran()
        assert result["state"] == "CANCELLED"
        assert "kernel_read_error" not in result
    finally:
        adapter.kernel.close()
        other.close()


# --- Hands bridge ----------------------------------------------------------------------------

def test_bridge_duplicate_with_an_unreadable_task_reports_unknown_not_failed(env):
    """Site bridge 411: the duplicate's existing row is purged before it can be read."""
    from scp.hands.hands_executor import HandsExecutor
    from scp.hands.task_kernel_bridge import TaskKernelHandsBridge
    from scp.pc_control.pc_controller import PCController
    from scp.security.capability_epoch import CapabilityAuthority

    workspace = env / "workspace"
    workspace.mkdir()
    cap_auth = CapabilityAuthority(env / "capability_state.json")
    executor = HandsExecutor(controller=PCController(working_dir=workspace), capability_authority=cap_auth,
                             data_dir=env / "hands_data")
    bridge = TaskKernelHandsBridge(executor, db_path=env / "kernel.sqlite3")
    other = TaskKernel(str(env / "kernel.sqlite3"))
    target = workspace / "r22_artifact.txt"
    request_key = f"r22-replay-{uuid.uuid4().hex}"
    token = cap_auth.issue("hands:pc.write_file")
    confirmation_id = executor.controller.human_store.record_confirmation(
        action="pc.write_file", target=str(target.resolve()), ttl_seconds=60,
    )

    async def run(content: str, steps=None):
        chor = Choreography(bridge.kernel._storage._get_conn(), steps) if steps else None
        try:
            return await bridge.execute(
                action="pc.write_file",
                params={"path": str(target), "content": content, "confirmation_id": confirmation_id},
                capability_level=3, approved=True, request_key=request_key, capability_token=token,
            )
        finally:
            if chor:
                chor.stop()
                chor.assert_all_ran()

    try:
        first = asyncio.run(run("written-once"))
        assert first.get("success") is True, first
        tid = first["kernel"]["taskId"]
        replay = asyncio.run(run("MUTATED_BY_REPLAY", [
            (BEGIN, "create_task collides", lambda: None),
            (SELECT_TASK, "purge", lambda: other.purge_tasks(tid)),
        ]))
        assert replay["success"] is False and replay["safeToRetry"] is False
        assert replay["kernel"]["state"] == "UNKNOWN"
        assert replay["kernel"]["requiresRecovery"] is True
        assert replay["kernel"]["readError"].startswith("NotFound")
        assert "StorageIntegrityError" in replay["error"]
        assert "before dispatch" not in replay["error"]
        assert target.read_text(encoding="utf-8") == "written-once"  # no second side effect
    finally:
        bridge.kernel.close()
        other.close()
