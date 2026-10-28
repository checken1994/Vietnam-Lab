"""R6 negative tests: journal whitewash qua 2 đường đã verify REAL bằng probe.

Zero-mock policy: real TaskKernel trên SQLite tmp_path, không mock hash/conn.

Đường 1 (verify_journal): fallback legacy stable_hash từng-event cho phép chuỗi
HMAC bị viết lại 100% (payload mới + hash stable tự tính, không cần secret)
vẫn PASS. Fix: gate legacy theo CHUỖI + đối chiếu control anchor sống.

Đường 2 (reanchor_journal_impl): nhánh cấp quyền khi len(hash_errors)==event_count
ký lại HMAC cho chính chuỗi bị viết lại (whitewash) và không audit. Fix: chỉ
cấp quyền khi verify PASS (re-key/legacy migration) hoặc rotation khai báo;
mọi reanchor thành công phải ghi audit event REANCHORED.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scp.task_kernel import CheckpointCorrupt, KernelError, TaskKernel
from scp.task_kernel_parts.definitions import stable_hash


def _rewrite_chain_with_stable_hash(kernel: TaskKernel, task_id: str, payload_override: dict | None = None) -> str:
    """Giả lập kẻ tấn công có DB-write: viết lại TOÀN chuỗi bằng stable_hash tự tính.

    payload_override != None -> UPDATE payload 100% event (tẩy trắng nội dung),
    chuỗi prev_event_hash được tính lại nhất quán để qua kiểm tra linkage.
    Trả về head mới của chuỗi (không cần secret để tính).
    """
    prev = None
    kernel._begin()
    for e in kernel.get_events(task_id):
        payload = payload_override if payload_override is not None else json.loads(e["payload_json"])
        kernel.conn.execute(
            "UPDATE events SET payload_json = ? WHERE event_id = ?",
            (json.dumps(payload, ensure_ascii=False, sort_keys=True), e["event_id"]),
        )
        body = {
            "event_id": e["event_id"], "task_id": e["task_id"], "seq": e["seq"], "type": e["type"],
            "from_state": e["from_state"], "to_state": e["to_state"], "actor": e["actor"], "reason": e["reason"],
            "payload": payload, "policy_hash": e["policy_hash"], "prev_event_hash": prev,
        }
        h = stable_hash(body)
        kernel.conn.execute(
            "UPDATE events SET prev_event_hash = ?, event_hash = ? WHERE event_id = ?",
            (prev, h, e["event_id"]),
        )
        prev = h
    kernel._commit()
    return prev


def test_r6_whitewash_full_rewrite_rejected_by_verify_and_reanchor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """(a) Chuỗi HMAC bị UPDATE payload 100% event + hash stable tự tính:
    verify_journal phải TỪ CHỐI (mixed chain-vs-anchor bị bắt) VÀ reanchor từ chối."""
    monkeypatch.setenv("SCP_CAPABILITY_SECRET", "r6-secret-key-0123456789abcdef")
    monkeypatch.delenv("SCP_PREVIOUS_CAPABILITY_SECRETS", raising=False)
    kernel = TaskKernel(tmp_path / "r6_whitewash.sqlite3")
    try:
        tid = "r6-whitewash-1"
        kernel.create_task(tid, "user", "goal", "R1")
        kernel.transition(tid, "PLANNING", actor="setup")
        kernel.transition(tid, "READY", actor="setup")

        anchor_head = kernel._control()["journal_head_hash"]
        assert anchor_head, "anchor HMAC phải đang sống trước tấn công"
        assert not anchor_head.startswith("sha256:"), "anchor của kernel hiện hành là HMAC trần"

        new_head = _rewrite_chain_with_stable_hash(kernel, tid, payload_override={"forged": "whitewash"})
        assert new_head != anchor_head, "chuỗi đã bị viết lại toàn bộ"

        v = kernel.verify_journal(tid)
        assert v["hash_chain_valid"] is False, "chuỗi viết lại 100% phải bị từ chối"
        assert any(err.startswith("legacy_whitewash:") for err in v["errors"]), v["errors"]
        assert "event_hash:1" in v["errors"], "event legacy giả phải bị đánh invalid"
        assert v["legacy_unkeyed"] is False

        with pytest.raises(KernelError):
            kernel.reanchor_journal(tid)

        # Từ chối tuyệt đối -> journal không bị ký lại (không whitewash HMAC).
        still = kernel.verify_journal(tid)
        assert still["hash_chain_valid"] is False
    finally:
        kernel.close()


def test_r6_pure_legacy_chain_verifies_and_reanchor_only_on_pass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """(b) Chuỗi thuần legacy (toàn stable_hash từ đầu, DB pre-B55 không anchor):
    verify vẫn PASS (giữ tương thích cũ, đánh dấu legacy_unkeyed); reanchor chỉ
    chạy nhờ verify PASS (legacy migration) và phải ghi audit event REANCHORED."""
    monkeypatch.setenv("SCP_CAPABILITY_SECRET", "r6-secret-key-0123456789abcdef")
    monkeypatch.delenv("SCP_PREVIOUS_CAPABILITY_SECRETS", raising=False)
    kernel = TaskKernel(tmp_path / "r6_legacy.sqlite3")
    try:
        tid = "r6-legacy-1"
        kernel.create_task(tid, "user", "goal", "R1")
        kernel.transition(tid, "PLANNING", actor="setup")

        # Emulate DB pre-B55 thuần legacy: hash stable từ đầu + control chưa có
        # anchor (cột migrate thêm mặc định NULL/0).
        _rewrite_chain_with_stable_hash(kernel, tid)
        kernel._begin()
        kernel.conn.execute("UPDATE control SET journal_head_hash = NULL, event_count = 0 WHERE id = 1")
        kernel._commit()

        v = kernel.verify_journal(tid)
        assert v["hash_chain_valid"] is True, "chuỗi legacy thuần phải vẫn PASS (tương thích cũ)"
        assert v["legacy_unkeyed"] is True, "kết quả legacy phải được đánh dấu tách biệt"

        res = kernel.reanchor_journal(tid)
        assert res["status"] == "REANCHORED"
        assert res["reason"] == "legacy_migration"

        audit = kernel.get_events(tid)[-1]
        assert audit["type"] == "REANCHORED"
        audit_payload = json.loads(audit["payload_json"])
        assert audit_payload["task_id"] == tid
        assert audit_payload["old_head_hash"]
        assert audit_payload["new_head_hash"]
        assert audit_payload["old_head_hash"] != audit_payload["new_head_hash"]

        v_post = kernel.verify_journal(tid)
        assert v_post["hash_chain_valid"] is True
        assert v_post["legacy_unkeyed"] is False, "sau re-key chuỗi phải là HMAC thuần"
    finally:
        kernel.close()


def test_r6_mixed_chain_keyed_plus_forged_legacy_event_invalid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """(1) Chuỗi TRỘN: event đầu còn hash HMAC đúng, event sau bị viết lại thành
    stable_hash tự tính -> gate theo chuỗi phải coi event legacy giả là invalid."""
    monkeypatch.setenv("SCP_CAPABILITY_SECRET", "r6-secret-key-0123456789abcdef")
    monkeypatch.delenv("SCP_PREVIOUS_CAPABILITY_SECRETS", raising=False)
    kernel = TaskKernel(tmp_path / "r6_mixed.sqlite3")
    try:
        tid = "r6-mixed-1"
        kernel.create_task(tid, "user", "goal", "R1")
        kernel.transition(tid, "PLANNING", actor="setup")
        kernel.transition(tid, "READY", actor="setup")

        # Chỉ viết lại event CUỐI thành stable_hash (payload giả), giữ nguyên event HMAC đầu.
        events = kernel.get_events(tid)
        target = events[-1]
        payload = {"forged": "partial"}
        kernel._begin()
        kernel.conn.execute(
            "UPDATE events SET payload_json = ? WHERE event_id = ?",
            (json.dumps(payload, ensure_ascii=False, sort_keys=True), target["event_id"]),
        )
        body = {
            "event_id": target["event_id"], "task_id": target["task_id"], "seq": target["seq"],
            "type": target["type"], "from_state": target["from_state"], "to_state": target["to_state"],
            "actor": target["actor"], "reason": target["reason"], "payload": payload,
            "policy_hash": target["policy_hash"], "prev_event_hash": target["prev_event_hash"],
        }
        kernel.conn.execute(
            "UPDATE events SET event_hash = ? WHERE event_id = ?",
            (stable_hash(body), target["event_id"]),
        )
        kernel._commit()

        v = kernel.verify_journal(tid)
        assert v["hash_chain_valid"] is False, "chuỗi trộn keyed+legacy phải bị từ chối"
        assert f"event_hash:{target['seq']}" in v["errors"], v["errors"]
        assert v["legacy_unkeyed"] is False, "event legacy giả không được coi là legacy hợp lệ"

        with pytest.raises(KernelError):
            kernel.reanchor_journal(tid)
    finally:
        kernel.close()


def test_r6_one_way_migration_flag_kills_all_legacy_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """(5) Cờ một chiều legacy_migration_done: sau khi một pass reanchor_all
    hoàn tất sạch, MỌI hash legacy — kể cả chuỗi thuần legacy không anchor —
    đều bị verify coi là SAI; reanchor cũng từ chối (không còn đường rửa)."""
    monkeypatch.setenv("SCP_CAPABILITY_SECRET", "r6-secret-key-0123456789abcdef")
    monkeypatch.delenv("SCP_PREVIOUS_CAPABILITY_SECRETS", raising=False)
    kernel = TaskKernel(tmp_path / "r6_migrate.sqlite3")
    try:
        tid = "r6-migrate-1"
        kernel.create_task(tid, "user", "goal", "R1")
        kernel.transition(tid, "PLANNING", actor="setup")
        kernel.transition(tid, "READY", actor="setup")

        res = kernel.reanchor_all_journals()
        assert res["status"] == "COMPLETED"
        assert res["legacy_migration_done"] is True, "pass migration sạch phải bật cờ một chiều"
        assert kernel._control()["legacy_migration_done"] == 1

        # Kẻ tấn công DB-write giả lập task "mồ côi" thuần legacy (không anchor
        # riêng, chuỗi stable_hash tự tính từ đầu — không cần secret).
        tid2 = "r6-migrate-forged"
        kernel.create_task(tid2, "user", "forged", "R1")
        kernel.transition(tid2, "PLANNING", actor="setup")
        _rewrite_chain_with_stable_hash(kernel, tid2)

        v = kernel.verify_journal(tid2)
        assert v["hash_chain_valid"] is False, "post-migration: chuỗi thuần legacy phải SAI"
        assert any(err.startswith("legacy_hash:post_migration:") for err in v["errors"]), v["errors"]
        assert "event_hash:1" in v["errors"]
        assert v["legacy_unkeyed"] is False

        with pytest.raises(KernelError):
            kernel.reanchor_journal(tid2)

        # Chuỗi HMAC hợp lệ vẫn PASS — cờ không phá đường chính.
        assert kernel.verify_journal(tid)["hash_chain_valid"] is True
    finally:
        kernel.close()


def test_r6_checkpoint_tamper_with_selfcomputed_hash_detected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """(6) Checkpoint: kẻ có DB-write sửa payload rồi TỰ TÍNH LẠI stable_hash
    (không cần secret) — validate_checkpoint phải bắt (mixed row / post-migration).
    Zero-mock: TaskKernel thật trên SQLite tmp_path, tấn công bằng SQL thật."""
    monkeypatch.setenv("SCP_CAPABILITY_SECRET", "r6-secret-key-0123456789abcdef")
    monkeypatch.delenv("SCP_PREVIOUS_CAPABILITY_SECRETS", raising=False)
    kernel = TaskKernel(tmp_path / "r6_checkpoint.sqlite3")
    try:
        tid = "r6-cp-1"
        kernel.create_task(tid, "user", "goal", "R1")
        for s in ("PLANNING", "READY", "QUEUED"):
            kernel.transition(tid, s, actor="setup")
        lease = kernel.claim(tid, "worker-1", ttl_seconds=300)
        kernel.start(tid, lease.lease_id)
        planned_action = {"action": "sum", "args": [1, 2]}
        cp_id = kernel.checkpoint(tid, lease.lease_id, "step-1", "RUNNING", planned_action, 1, "r6-idem-1")

        row = kernel.get_checkpoint(cp_id)
        assert not row["payload_hash"].startswith("sha256:"), "kernel hiện hành phải ghi HMAC keyed"
        assert not row["planned_action_hash"].startswith("sha256:")

        # Positive: row do kernel ghi validate PASS (keyed thuần).
        kernel.validate_checkpoint(cp_id, planned_action)

        def _forged_payload(tool_result):
            return {
                "task_id": row["task_id"], "attempt_id": row["attempt_id"], "step_id": row["step_id"],
                "state": row["state"], "planned_action": planned_action,
                "capability_epoch": row["capability_epoch"], "idempotency_key": row["idempotency_key"],
                "pre_observation_ref": row["pre_observation_ref"], "post_observation_ref": row["post_observation_ref"],
                "tool_result": tool_result, "verifier_verdict": row["verifier_verdict"],
            }

        # Tấn công 1: sửa tool_result + tự tính payload_hash = stable_hash (không secret).
        forged_tool = {"forged": "whitewash"}
        kernel._begin()
        kernel.conn.execute(
            "UPDATE checkpoints SET tool_result_json=?, payload_hash=? WHERE checkpoint_id=?",
            (json.dumps(forged_tool, ensure_ascii=False, sort_keys=True), stable_hash(_forged_payload(forged_tool)), cp_id),
        )
        kernel._commit()
        # Row bị mixed (planned keyed + payload legacy giả) -> phải bị bắt.
        with pytest.raises(CheckpointCorrupt, match="payload hash mismatch"):
            kernel.validate_checkpoint(cp_id, planned_action)

        # Tấn công 2: đè cả planned_action_hash về stable_hash -> row "thuần legacy".
        # Pre-migration (flag=0) vẫn phải chấp để giữ tương thích DB cũ.
        kernel._begin()
        kernel.conn.execute(
            "UPDATE checkpoints SET planned_action_hash=? WHERE checkpoint_id=?",
            (stable_hash(planned_action), cp_id),
        )
        kernel._commit()
        kernel.validate_checkpoint(cp_id, planned_action)

        # Post-migration (cờ một chiều bật bằng pass reanchor_all sạch): row
        # thuần legacy cũng bị coi là SAI — không còn đường rửa qua legacy.
        kernel.reanchor_all_journals()
        assert kernel._control()["legacy_migration_done"] == 1
        with pytest.raises(CheckpointCorrupt, match="planned action hash mismatch"):
            kernel.validate_checkpoint(cp_id, planned_action)
    finally:
        kernel.close()
