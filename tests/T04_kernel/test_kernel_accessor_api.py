"""[HC-21 2026-10-23 / K3-W1] Kernel accessor API tests.

4 accessor chuẩn trên TaskKernel thay SQL thô ngoài kernel:
  - list_recent(limit)            — /v3/tasks (api_server.py)
  - query_by_state(state)         — retry-policy worker (lifespan.py)
  - pending_review_breakdown()    — doubt_cron escalation backlog
  - purge_tasks(pattern)          — deduplication_maintenance (journal-first)

Anti-placebo yêu cầu: purge pattern không match phải xóa 0 dòng và KHÔNG ghi
journal; purge match phải ghi tombstone event và trả đúng count.
"""
from __future__ import annotations

import pytest

import scp.task_kernel_parts.taskkernel as tk_module
from scp.task_kernel import TaskKernel
from scp.task_kernel_parts.definitions import KernelError, NotFound


EXECUTION_PATH = ("PLANNING", "READY", "QUEUED", "LEASED", "RUNNING")


@pytest.fixture()
def kernel(tmp_path):
    k = TaskKernel(str(tmp_path / "kernel_accessor.sqlite3"))
    yield k
    k.close()


def _drive(kernel: TaskKernel, task_id: str, *states: str) -> None:
    """Đưa task qua chuỗi transition hợp lệ (system authority, không cần lease)."""
    kernel._system_authority = True
    try:
        for state in states:
            kernel.transition(task_id, state, actor="test", reason="accessor_test")
    finally:
        kernel._system_authority = False


class TestListRecent:
    def test_orders_newest_first_then_task_id_desc(self, kernel, monkeypatch):
        # now_iso() được gọi 2 lần/task (tasks.created_at + events.created_at);
        # counter đơn điệu đảm bảo mọi lần gọi đều muộn hơn lần trước.
        counter = iter(range(1000))
        monkeypatch.setattr(
            tk_module, "now_iso", lambda: f"2026-10-23T00:00:{next(counter):02d}Z"
        )
        kernel.create_task("aaa", "tester", "first")
        kernel.create_task("bbb", "tester", "second")
        kernel.create_task("ccc", "tester", "third")

        rows = kernel.list_recent()
        assert [r["task_id"] for r in rows] == ["ccc", "bbb", "aaa"]

    def test_tie_break_by_task_id_desc(self, kernel, monkeypatch):
        monkeypatch.setattr(tk_module, "now_iso", lambda: "2026-10-23T00:00:00Z")
        kernel.create_task("aaa", "tester", "same second")
        kernel.create_task("zzz", "tester", "same second")

        rows = kernel.list_recent()
        assert [r["task_id"] for r in rows] == ["zzz", "aaa"]

    def test_shape_is_safe_metadata_only(self, kernel):
        kernel.create_task("t1", "secret-owner", "goal-with-user-content")
        rows = kernel.list_recent()
        assert len(rows) == 1
        assert set(rows[0].keys()) == {"task_id", "state", "created_at"}
        assert rows[0]["state"] == "CREATED"
        assert "owner" not in rows[0] and "goal" not in rows[0]

    def test_limit_and_default_20(self, kernel):
        for i in range(23):
            kernel.create_task(f"task-{i:02d}", "tester", "bulk")
        assert len(kernel.list_recent()) == 20
        assert len(kernel.list_recent(2)) == 2
        assert kernel.list_recent(0) == []
        # limit nhỏ lấy đúng bản mới nhất (task_id DESC trong cùng batch created_at)
        assert [r["task_id"] for r in kernel.list_recent(1)] == ["task-22"]

    def test_empty_db_returns_empty_list(self, kernel):
        assert kernel.list_recent() == []


class TestQueryByState:
    def test_filters_by_state_exact(self, kernel):
        kernel.create_task("keep-created", "tester", "untouched")
        kernel.create_task("to-retry", "tester", "will retry")
        kernel.create_task("also-created", "tester", "untouched 2")
        _drive(kernel, "to-retry", *EXECUTION_PATH, "RETRY_SCHEDULED")

        rows = kernel.query_by_state("RETRY_SCHEDULED")
        assert [r["task_id"] for r in rows] == ["to-retry"]
        assert rows[0]["state"] == "RETRY_SCHEDULED"
        assert set(rows[0].keys()) == {"task_id", "state"}
        assert {r["task_id"] for r in kernel.query_by_state("CREATED")} == {
            "keep-created", "also-created",
        }

    def test_unknown_state_returns_empty(self, kernel):
        kernel.create_task("t1", "tester", "x")
        assert kernel.query_by_state("NEVER_SEEN_STATE") == []

    def test_shape_no_owner_no_goal(self, kernel):
        kernel.create_task("t1", "secret-owner", "goal-content")
        rows = kernel.query_by_state("CREATED")
        assert set(rows[0].keys()) == {"task_id", "state"}


class TestPendingReviewBreakdown:
    def test_counts_only_escalation_states_present(self, kernel):
        kernel.create_task("r-hr", "tester", "human review")
        kernel.create_task("r-unk", "tester", "unknown")
        kernel.create_task("r-rec", "tester", "reconciling")
        kernel.create_task("r-new", "tester", "fresh")
        _drive(kernel, "r-hr", *EXECUTION_PATH, "WAITING_TOOL", "UNKNOWN", "HUMAN_REVIEW")
        _drive(kernel, "r-unk", *EXECUTION_PATH, "WAITING_TOOL", "UNKNOWN")
        _drive(kernel, "r-rec", *EXECUTION_PATH, "WAITING_TOOL", "UNKNOWN", "RECONCILING")

        breakdown = kernel.pending_review_breakdown()
        assert breakdown == {"HUMAN_REVIEW": 1, "UNKNOWN": 1, "RECONCILING": 1}
        # CREATED task không lọt vào breakdown
        assert "CREATED" not in breakdown

    def test_absent_state_omitted_and_empty_db(self, kernel):
        assert kernel.pending_review_breakdown() == {}
        kernel.create_task("t1", "tester", "normal")
        kernel.create_task("t2", "tester", "normal2")
        assert kernel.pending_review_breakdown() == {}
        assert sum(kernel.pending_review_breakdown().values()) == 0


class TestPurgeTasks:
    def test_purges_only_matching_pattern_and_journals_tombstone(self, kernel):
        kernel.create_task("stress_1", "loadtest", "junk")
        kernel.create_task("stress_2", "loadtest", "junk")
        kernel.create_task("prod_task", "real", "keep me")

        result = kernel.purge_tasks("stress_%")

        assert result["pattern"] == "stress_%"
        assert result["deleted_count"] == 2
        assert sorted(result["task_ids"]) == ["stress_1", "stress_2"]
        with pytest.raises(NotFound):
            kernel.get_task("stress_1")
        with pytest.raises(NotFound):
            kernel.get_task("stress_2")
        # Task không match phải còn nguyên
        assert kernel.get_task("prod_task")["state"] == "CREATED"

        # Journal-first: tombstone event được ghi và GIỮ lại (anti-placebo
        # "xóa lịch sử"): events của task purged không bị xóa cùng lúc.
        events = kernel.get_events("stress_1")
        assert [e["type"] for e in events] == ["TASK_CREATED", "TASK_PURGED"]
        tombstone = events[-1]
        assert tombstone["from_state"] == "CREATED"
        assert tombstone["to_state"] is None
        assert tombstone["payload_json"]
        # Chain hash vẫn hợp lệ sau purge
        assert kernel.verify_journal("stress_1")["hash_chain_valid"] is True

    def test_no_match_purges_nothing_and_writes_no_journal(self, kernel):
        kernel.create_task("prod_task", "real", "keep me")
        events_before = kernel.get_events("prod_task")
        control_before = kernel._control()

        result = kernel.purge_tasks("nomatch_%")

        assert result == {"pattern": "nomatch_%", "deleted_count": 0, "task_ids": []}
        assert kernel.get_task("prod_task")["state"] == "CREATED"
        assert kernel.get_events("prod_task") == events_before
        assert kernel._control()["event_count"] == control_before["event_count"]

    def test_empty_pattern_fails_closed(self, kernel):
        with pytest.raises(KernelError):
            kernel.purge_tasks("")
        with pytest.raises(KernelError):
            kernel.purge_tasks("")

    def test_atomic_rollback_on_midway_failure(self, kernel, monkeypatch):
        kernel.create_task("stress_1", "loadtest", "junk")
        kernel.create_task("stress_2", "loadtest", "junk")
        real_append = kernel._append_event
        calls = {"n": 0}

        def flaky_append(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("injected journal failure")
            return real_append(*args, **kwargs)

        monkeypatch.setattr(kernel, "_append_event", flaky_append)
        with pytest.raises(RuntimeError):
            kernel.purge_tasks("stress_%")

        # Rollback: KHÔNG xóa nửa chừng — cả 2 task vẫn còn, không có tombstone
        assert kernel.get_task("stress_1")["state"] == "CREATED"
        assert kernel.get_task("stress_2")["state"] == "CREATED"
        assert [e["type"] for e in kernel.get_events("stress_1")] == ["TASK_CREATED"]

    def test_journal_integrity_after_purge(self, kernel):
        for i in range(3):
            kernel.create_task(f"stress_{i}", "loadtest", "junk")
        kernel.create_task("prod_task", "real", "keep me")

        kernel.purge_tasks("stress_%")

        integrity = kernel.verify_integrity()
        assert integrity["quick_check"] == "ok"
        assert integrity["invalid_chains"] == []
