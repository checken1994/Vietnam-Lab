"""R5.1 · K_journal cutover: window mark, honest status, and the remaining key/test gaps.

Follow-up to the R5 internal checker (r5-final.md):
- F1: the cutover used to wait for ANY live task owning ANY checkpoint, so a busy upgraded DB
  could keep the dual-accept window (capability key still accepted) open forever. Only
  checkpoints created before the window opened are pre-R5 and unre-signable.
- F2: reanchor status / the reanchor tool must reflect the real flag.
- F3: controlled-state tasks (RECONCILING / UNKNOWN / HUMAN_REVIEW) with pre-R5 checkpoints
  must block the cutover too.
- F5: a retired capability key must not be accepted as a retired journal key.

Zero-mock: real TaskKernel on SQLite files under tmp_path; the pre-R5 upgrade is reproduced by
dropping the R5 control columns with a second sqlite3 connection, exactly the shape of a DB
written by pre-R5 code.
"""
from __future__ import annotations

import sqlite3
import time

import pytest

from scp.task_kernel import KernelError, TaskKernel

_CAPABILITY = "r5-1-capability-secret-must-not-sign-journal-32b"
_JOURNAL = "r5-1-journal-secret-K_journal-distinct-value-32b"
_R5_CONTROL_COLUMNS = ("journal_key_migrated", "journal_key_window_opened_at")


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("SCP_CAPABILITY_SECRET", _CAPABILITY)
    monkeypatch.setenv("SCP_JOURNAL_SECRET", _JOURNAL)
    for name in ("SCP_PREVIOUS_CAPABILITY_SECRETS", "SCP_PREVIOUS_JOURNAL_SECRETS", "SCP_JOURNAL_SECRET_FILE"):
        monkeypatch.delenv(name, raising=False)
    return tmp_path / "r5_1.sqlite3"


def _downgrade_to_pre_r5(db_path) -> None:
    """Drop the R5 control columns so the next TaskKernel open runs the pre-R5 upgrade path."""
    side = sqlite3.connect(db_path)
    try:
        present = {row[1] for row in side.execute('PRAGMA table_info("control")')}
        for col in _R5_CONTROL_COLUMNS:
            if col in present:
                side.execute(f"ALTER TABLE control DROP COLUMN {col}")
        side.commit()
    finally:
        side.close()


def _rechain_with(db_path, task_id: str, key: bytes) -> None:
    """Re-sign one task's journal in the current (domain-prefixed) format under ``key``."""
    import hashlib
    import hmac
    import json

    from scp.task_kernel_parts.definitions import KEYED_HASH_DOMAINS, journal_event_body

    side = sqlite3.connect(db_path)
    side.row_factory = sqlite3.Row
    try:
        prev = None
        for e in [dict(r) for r in side.execute("SELECT * FROM events WHERE task_id=? ORDER BY seq", (task_id,))]:
            e["prev_event_hash"] = prev
            body = journal_event_body(e, json.loads(e["payload_json"]))
            digest = hmac.new(key, KEYED_HASH_DOMAINS["journal"] + json.dumps(body, sort_keys=True).encode(),
                              hashlib.sha256).hexdigest()
            side.execute("UPDATE events SET prev_event_hash=?, event_hash=? WHERE event_id=?", (prev, digest, e["event_id"]))
            prev = digest
        side.commit()
    finally:
        side.close()


def _running_with_checkpoint(kernel: TaskKernel, task_id: str):
    kernel.create_task(task_id, "r5-1-owner", "goal", "R0", input_hash="sha256:" + "0" * 64)
    for state in ("PLANNING", "READY", "QUEUED"):
        kernel.transition(task_id, state, actor="r5-1", reason="setup")
    lease = kernel.claim(task_id, "r5-1-worker", ttl_seconds=300)
    kernel.start(task_id, lease.lease_id)
    kernel.checkpoint(task_id, lease.lease_id, "s1", "RUNNING", {"action": task_id},
                      capability_epoch=1, idempotency_key=f"idem-{task_id}")
    return lease


def _to_controlled(kernel: TaskKernel, task_id: str, state: str) -> None:
    lease = _running_with_checkpoint(kernel, task_id)
    if state == "HUMAN_REVIEW":
        kernel.transition(task_id, "HUMAN_REVIEW", lease_id=lease.lease_id)
        return
    key, claimed = kernel.idempotency_claim(task_id, "s2", "http.post", f"https://api.test/{task_id}")
    assert claimed
    disp = kernel.record_action_dispatched(task_id, lease.lease_id, "s2", {"a": 1}, 1, key, f"req-{task_id}")
    assert kernel.get_task(task_id)["state"] == "UNKNOWN"
    if state == "RECONCILING":
        kernel.enter_reconciling(task_id, disp["checkpoint_id"])
    assert kernel.get_task(task_id)["state"] == state


def test_window_mark_is_recorded_when_a_pre_r5_db_is_upgraded(db):
    TaskKernel(db).close()
    _downgrade_to_pre_r5(db)
    before = time.time()
    upgraded = TaskKernel(db)
    try:
        control = upgraded._control()
        assert control["journal_key_migrated"] == 0
        assert control["journal_key_window_opened_at"]
    finally:
        upgraded.close()
    assert before > 0


def test_checkpoints_created_after_the_window_opened_do_not_block_the_cutover(db):
    kernel = TaskKernel(db)
    try:
        _running_with_checkpoint(kernel, "r5-1-old")
    finally:
        kernel.close()
    time.sleep(0.01)
    _downgrade_to_pre_r5(db)
    upgraded = TaskKernel(db)
    try:
        time.sleep(0.01)
        _running_with_checkpoint(upgraded, "r5-1-new")  # K_journal-signed, after the mark
        first = upgraded.reanchor_all_journals()
        assert first["journal_key_cutover_blocked_by"] == ["r5-1-old"]
        assert upgraded._control()["journal_key_migrated"] == 0
        upgraded.set_task_kill("r5-1-old")
        second = upgraded.reanchor_all_journals()
        assert second["journal_key_cutover_blocked_by"] == []
        assert upgraded._control()["journal_key_migrated"] == 1
        assert second["status"] == "COMPLETED"
        assert upgraded.get_task("r5-1-new")["state"] == "RUNNING"
    finally:
        upgraded.close()


@pytest.mark.parametrize("state", ["RECONCILING", "UNKNOWN", "HUMAN_REVIEW"])
def test_controlled_state_task_with_pre_r5_checkpoint_blocks_the_cutover(db, state):
    kernel = TaskKernel(db)
    try:
        _to_controlled(kernel, "r5-1-ctl", state)
    finally:
        kernel.close()
    time.sleep(0.01)
    _downgrade_to_pre_r5(db)
    upgraded = TaskKernel(db)
    try:
        result = upgraded.reanchor_all_journals()
        assert result["journal_key_cutover_blocked_by"] == ["r5-1-ctl"]
        assert result["status"] == "PARTIAL"
        assert upgraded._control()["journal_key_migrated"] == 0
    finally:
        upgraded.close()


def test_status_after_cutover_is_completed_even_with_live_checkpoints(db):
    kernel = TaskKernel(db)  # fresh DB: already cut over
    try:
        _running_with_checkpoint(kernel, "r5-1-live")
        result = kernel.reanchor_all_journals()
        assert kernel._control()["journal_key_migrated"] == 1
        assert result["status"] == "COMPLETED"
        assert result["journal_key_cutover_blocked_by"] == []
    finally:
        kernel.close()


def test_empty_pre_r5_db_cuts_over_in_one_pass(db):
    TaskKernel(db).close()
    _downgrade_to_pre_r5(db)
    upgraded = TaskKernel(db)
    try:
        assert upgraded._control()["journal_key_migrated"] == 0
        result = upgraded.reanchor_all_journals()
        assert upgraded._control()["journal_key_migrated"] == 1
        assert result["status"] == "COMPLETED"
    finally:
        upgraded.close()


def test_reanchor_tool_reports_partial_and_exits_non_zero_while_blocked(db, capsys):
    from tools import reanchor_task_journal as tool

    kernel = TaskKernel(db)
    try:
        _running_with_checkpoint(kernel, "r5-1-tool")
    finally:
        kernel.close()
    time.sleep(0.01)
    _downgrade_to_pre_r5(db)
    TaskKernel(db).close()  # upgrade: window opens with the live pre-R5 checkpoint
    summary = tool.reanchor_database(db, backup=False)
    assert summary["status"] == "PARTIAL"
    assert summary["journal_key_migrated"] is False
    assert summary["journal_key_cutover_blocked_by"] == ["r5-1-tool"]
    assert tool.main(["--db-path", str(db), "--no-backup"]) != 0
    assert "r5-1-tool" in capsys.readouterr().out


def test_retired_capability_keys_are_not_accepted_as_retired_journal_keys(db, monkeypatch):
    kernel = TaskKernel(db)
    try:
        kernel.create_task("r5-1-prev", "r5-1-owner", "goal", "R0", input_hash="sha256:" + "0" * 64)
        retired = "retired-capability-key-0123456789abcd"
        monkeypatch.setenv("SCP_PREVIOUS_CAPABILITY_SECRETS", retired)
        monkeypatch.setenv("SCP_PREVIOUS_JOURNAL_SECRETS", retired)
        _rechain_with(db, "r5-1-prev", retired.encode())  # forces the retired-key lookup
        with pytest.raises(KernelError, match="SCP_PREVIOUS_CAPABILITY_SECRETS"):
            kernel.verify_journal("r5-1-prev")
    finally:
        kernel.close()


def test_error_messages_name_the_current_mechanisms(db):
    from scp.task_kernel import InvalidTransition

    kernel = TaskKernel(db)
    try:
        kernel.create_task("r5-1-msg", "r5-1-owner", "goal", "R1", input_hash="sha256:" + "0" * 64)
        kernel.transition("r5-1-msg", "PLANNING")
        kernel.transition("r5-1-msg", "WAITING_APPROVAL")
        with pytest.raises(InvalidTransition, match="issuer-signed approval token"):
            kernel.transition("r5-1-msg", "READY")
    finally:
        kernel.close()
    from pathlib import Path

    import scp.task_kernel_parts.lease_manager as lm

    source = Path(lm.__file__).read_text(encoding="utf-8")
    assert "match no SCP_PREVIOUS_CAPABILITY_SECRETS entry" not in source
