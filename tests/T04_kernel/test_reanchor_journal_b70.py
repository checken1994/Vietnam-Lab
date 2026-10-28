"""Unit tests for TaskKernel journal re-anchoring and backward-compatible verification (B70).

Zero-mock policy: exercises real TaskKernel against temporary SQLite database.
Tests cover:
- kernel.reanchor_journal happy path and NO_EVENTS path.
- kernel.reanchor_all_journals across multiple tasks.
- HMAC journal verification with stable_hash fallback.
- Tamper detection (fail-closed) on corrupted event chains.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scp.task_kernel import TaskKernel
from scp.task_kernel_parts.definitions import KernelError, stable_hash


def test_reanchor_single_task_journal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SCP_CAPABILITY_SECRET", "old-secret-123456789012345678901234")
    db_file = tmp_path / "kernel_reanchor.sqlite3"
    kernel = TaskKernel(db_file)
    try:
        task_id = "task-b70-reanchor-1"
        kernel.create_task(task_id, "test-user", "test reanchor", "R1")
        kernel.transition(task_id, "PLANNING", actor="setup")
        kernel.transition(task_id, "READY", actor="setup")
        kernel.transition(task_id, "QUEUED", actor="setup")

        v1 = kernel.verify_journal(task_id)
        assert v1["hash_chain_valid"] is True
        assert v1["event_count"] == 4

        # Change secret to simulate key rotation
        monkeypatch.setenv("SCP_CAPABILITY_SECRET", "new-secret-987654321098765432109876")
        # R6: rotation phải được KHAI BÁO qua SCP_PREVIOUS_CAPABILITY_SECRETS thì
        # chuỗi cũ mới chứng minh được nguồn gốc; reanchor không khai báo bị từ chối.
        monkeypatch.setenv("SCP_PREVIOUS_CAPABILITY_SECRETS", "old-secret-123456789012345678901234")

        # Now re-anchor with the new secret
        res = kernel.reanchor_journal(task_id)
        assert res["status"] == "REANCHORED"
        assert res["reanchored"] == 4
        assert res["task_id"] == task_id

        # Verify with new secret
        v2 = kernel.verify_journal(task_id)
        assert v2["hash_chain_valid"] is True
        assert len(v2["errors"]) == 0
        assert v2["legacy_unkeyed"] is False
        # R6: reanchor thành công phải ghi audit event REANCHORED bền trong journal
        audit = kernel.get_events(task_id)[-1]
        assert audit["type"] == "REANCHORED"
    finally:
        kernel.close()


def test_reanchor_noop_for_nonexistent_task(tmp_path: Path):
    db_file = tmp_path / "kernel_noop.sqlite3"
    kernel = TaskKernel(db_file)
    try:
        res = kernel.reanchor_journal("task-does-not-exist")
        assert res["status"] == "NO_EVENTS"
        assert res["reanchored"] == 0
    finally:
        kernel.close()


def test_reanchor_all_journals(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SCP_CAPABILITY_SECRET", "initial-secret-12345678901234567890")
    db_file = tmp_path / "kernel_multi.sqlite3"
    kernel = TaskKernel(db_file)
    try:
        # Create multiple tasks
        for i in range(3):
            tid = f"multi-task-{i}"
            kernel.create_task(tid, "user", f"goal {i}", "R1")
            kernel.transition(tid, "PLANNING", actor="planner")

        # Rotate secret (R6: khai báo secret cũ để rotation chứng minh được nguồn gốc)
        monkeypatch.setenv("SCP_CAPABILITY_SECRET", "rotated-secret-abcdef0123456789")
        monkeypatch.setenv("SCP_PREVIOUS_CAPABILITY_SECRETS", "initial-secret-12345678901234567890")

        # Re-anchor all
        all_res = kernel.reanchor_all_journals()
        assert all_res["status"] == "COMPLETED"
        assert all_res["total_tasks"] == 3
        assert all_res["reanchored_tasks"] == 3

        # Verify all
        for i in range(3):
            tid = f"multi-task-{i}"
            v = kernel.verify_journal(tid)
            assert v["hash_chain_valid"] is True
    finally:
        kernel.close()


def test_reanchor_empty_database(tmp_path: Path):
    db_file = tmp_path / "kernel_empty.sqlite3"
    kernel = TaskKernel(db_file)
    try:
        res = kernel.reanchor_all_journals()
        assert res["status"] == "COMPLETED"
        assert res["total_tasks"] == 0
        assert res["reanchored_tasks"] == 0
    finally:
        kernel.close()


def test_stable_hash_backward_compatibility(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SCP_CAPABILITY_SECRET", "some-fixed-secret-key-123456789012")
    db_file = tmp_path / "kernel_compat.sqlite3"
    kernel = TaskKernel(db_file)
    try:
        task_id = "compat-task"
        kernel.create_task(task_id, "user", "goal", "R1")

        # Manually overwrite event_hash in DB with stable_hash to simulate legacy record
        events = kernel.get_events(task_id)
        kernel._begin()
        for e in events:
            body = {
                "event_id": e["event_id"],
                "task_id": e["task_id"],
                "seq": e["seq"],
                "type": e["type"],
                "from_state": e["from_state"],
                "to_state": e["to_state"],
                "actor": e["actor"],
                "reason": e["reason"],
                "payload": json.loads(e["payload_json"]),
                "policy_hash": e["policy_hash"],
                "prev_event_hash": e["prev_event_hash"],
            }
            h = stable_hash(body)
            kernel.conn.execute("UPDATE events SET event_hash = ? WHERE event_id = ?", (h, e["event_id"]))
        # R6: emulate DB pre-B55 THẬT — chuỗi legacy sinh ra trước khi cơ chế
        # anchor tồn tại, nên control không giữ anchor HMAC (cột migrate = NULL/0).
        # Nếu giữ anchor HMAC sống, chuỗi stable_hash bị coi là whitewash chuỗi
        # HMAC đã ký (xem test_r6_journal_whitewash.py).
        kernel.conn.execute("UPDATE control SET journal_head_hash = NULL, event_count = 0 WHERE id = 1")
        kernel._commit()

        # verify_journal should accept the stable_hash fallback (chuỗi legacy thuần)
        v = kernel.verify_journal(task_id)
        assert v["hash_chain_valid"] is True
        assert v["legacy_unkeyed"] is True

        # Tampering with payload should fail verification
        kernel._begin()
        kernel.conn.execute(
            "UPDATE events SET payload_json = ? WHERE task_id = ?",
            ('{"tampered": true}', task_id),
        )
        kernel._commit()
        v_tampered = kernel.verify_journal(task_id)
        assert v_tampered["hash_chain_valid"] is False
    finally:
        kernel.close()


def test_reanchor_tampered_prev_hash_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SCP_CAPABILITY_SECRET", "test-secret-key-12345678901234567890")
    db_file = tmp_path / "kernel_tamper_prev.sqlite3"
    kernel = TaskKernel(db_file)
    try:
        task_id = "task-tamper-prev"
        kernel.create_task(task_id, "user", "goal", "R1")
        kernel.transition(task_id, "PLANNING", actor="setup")
        kernel.transition(task_id, "READY", actor="setup")

        # Break hash chain pointer on event 2
        kernel._begin()
        kernel.conn.execute("UPDATE events SET prev_event_hash = 'corrupt_prev' WHERE seq = 2 AND task_id = ?", (task_id,))
        kernel._commit()

        # Re-anchoring must fail-closed with KernelError (cannot bleach broken pointer)
        with pytest.raises(KernelError, match="Cannot reanchor tampered journal"):
            kernel.reanchor_journal(task_id)
    finally:
        kernel.close()


def test_reanchor_partial_tampering_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SCP_CAPABILITY_SECRET", "test-secret-key-12345678901234567890")
    db_file = tmp_path / "kernel_partial_tamper.sqlite3"
    kernel = TaskKernel(db_file)
    try:
        task_id = "task-partial-tamper"
        kernel.create_task(task_id, "user", "goal", "R1")
        kernel.transition(task_id, "PLANNING", actor="setup")
        kernel.transition(task_id, "READY", actor="setup")

        # Tamper with single event's payload in an active task (selective modification)
        kernel._begin()
        kernel.conn.execute("UPDATE events SET payload_json = '{\"forged\": 1}' WHERE seq = 2 AND task_id = ?", (task_id,))
        kernel._commit()

        # Re-anchoring must fail-closed (cannot bleach selective tampering)
        with pytest.raises(KernelError, match="Cannot reanchor partially tampered journal"):
            kernel.reanchor_journal(task_id)
    finally:
        kernel.close()


def test_reanchor_with_previous_secrets_chain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    v1_key = "test-only-placeholder_secret-epoch-1"
    v2_key = "test-only-placeholder_secret-epoch-2"
    monkeypatch.setenv("SCP_CAPABILITY_SECRET", v1_key)
    db_file = tmp_path / "kernel_multi_epoch.sqlite3"
    kernel = TaskKernel(db_file)
    try:
        task_id = "task-epoch-1"
        kernel.create_task(task_id, "user", "goal", "R1")
        kernel.transition(task_id, "PLANNING", actor="setup")

        # Rotate secret to v2, keeping v1 in previous secrets list
        monkeypatch.setenv("SCP_CAPABILITY_SECRET", v2_key)
        monkeypatch.setenv("SCP_PREVIOUS_CAPABILITY_SECRETS", f"old_key_other,{v1_key}")

        # Verification passes under previous secret
        v = kernel.verify_journal(task_id)
        assert v["hash_chain_valid"] is True

        # Re-anchor upgrades to v2
        res = kernel.reanchor_journal(task_id)
        assert res["status"] == "REANCHORED"

        # Now unset previous secrets; verification must still pass under v2
        monkeypatch.delenv("SCP_PREVIOUS_CAPABILITY_SECRETS", raising=False)
        v_post = kernel.verify_journal(task_id)
        assert v_post["hash_chain_valid"] is True
    finally:
        kernel.close()
