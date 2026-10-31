"""[JOURNAL-CHAIN 2026-10-31] Chuỗi journal không chứng minh được ⇒ task bị HOLD tại kernel.

Sự cố thật (data/ask_task_kernel.sqlite3, 2026-10-27 → 10-31): 9 chuỗi được ký bằng KHÓA TEST
công khai của tests/conftest.py (pytest ghi vào data sống), 13 chuỗi ký bằng khóa production.
Bộ xác minh giữ khóa nào thì thấy chuỗi của khóa kia là "hỏng" — nhưng kernel vẫn cho task trên
chuỗi đó claim / chạy / hoàn tất như thường; vòng Nghi ngờ chỉ ghi 1 dòng WARNING.

Hợp đồng được khóa ở đây (task-scoped kill, bằng chứng = chính journal):
  * mọi sự kiện TIẾN TRIỂN (lease, resume, checkpoint trước side effect, hoàn tất) trên chuỗi
    không chứng minh được dưới khóa của kernel ⇒ ``JournalIntegrityHold`` (lớp con của
    ``KillSwitchActive``), cả transaction rollback, journal KHÔNG bị sửa/xóa;
  * cạnh an toàn (CANCELLED / HUMAN_REVIEW / FAILED…) vẫn được phép — giữ được task lại;
  * ``claim_next`` bỏ qua task bị HOLD, KHÔNG chặn cả hàng đợi;
  * HOLD tự gỡ khi chính journal chứng minh được lại (khóa đã nghỉ được khai báo) — không cần
    người duyệt; kill switch toàn cục KHÔNG bị bật;
  * ``verify_journal`` phân loại nguyên nhân: KEY_DOMAIN_MISMATCH / CONTENT_TAMPER /
    STRUCTURAL_TAMPER (cấu trúc dữ liệu, không regex câu chữ).

Zero-mock: TaskKernel thật trên SQLite trong tmp_path; "ghi bằng khóa khác" được thực hiện bằng
một kết nối sqlite3 thứ hai đúng như một tiến trình khác khóa (hoặc kẻ ghi DB) làm.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import time

import pytest

from scp.task_kernel import KillSwitchActive, TaskKernel
from scp.task_kernel_parts.definitions import journal_event_body

_CAPABILITY = "journal-chain-capability-key-tests-only-0001"
_JOURNAL = "journal-chain-K_journal-current-tests-only-0002"
_FOREIGN = "journal-chain-K_journal-foreign-writer-tests-only-03"
_PREFIX = b"scp.journal.v1\x00"


@pytest.fixture
def kernel(tmp_path, monkeypatch):
    monkeypatch.setenv("SCP_CAPABILITY_SECRET", _CAPABILITY)
    monkeypatch.setenv("SCP_JOURNAL_SECRET", _JOURNAL)
    for name in ("SCP_PREVIOUS_CAPABILITY_SECRETS", "SCP_PREVIOUS_JOURNAL_SECRETS", "SCP_JOURNAL_SECRET_FILE"):
        monkeypatch.delenv(name, raising=False)
    k = TaskKernel(tmp_path / "jc_kernel.sqlite3")
    yield k
    k.close()


def _queued(k: TaskKernel, task_id: str, priority: int = 5) -> None:
    k.create_task(task_id, "jc-owner-" + task_id, "goal", "R1", input_hash="sha256:" + "0" * 64, priority=priority)
    k.transition(task_id, "PLANNING", actor="planner", reason="jc")
    k.transition(task_id, "READY", actor="planner", reason="jc")
    k.transition(task_id, "QUEUED", actor="planner", reason="jc")


def _rows(db_path, task_id: str) -> list[tuple]:
    side = sqlite3.connect(db_path)
    try:
        return side.execute(
            "SELECT event_id, seq, type, payload_json, prev_event_hash, event_hash FROM events WHERE task_id=? ORDER BY seq",
            (task_id,),
        ).fetchall()
    finally:
        side.close()


def _rechain_foreign(db_path, task_id: str, key: str = _FOREIGN) -> None:
    """Một tiến trình KHÁC khóa (vd pytest với khóa conftest) ghi lại toàn bộ chuỗi: cấu trúc
    nguyên vẹn, mọi event_hash là HMAC hợp lệ — nhưng dưới khóa kernel này không giữ."""
    side = sqlite3.connect(db_path)
    side.row_factory = sqlite3.Row
    try:
        prev = None
        for e in [dict(r) for r in side.execute("SELECT * FROM events WHERE task_id=? ORDER BY seq", (task_id,))]:
            e["prev_event_hash"] = prev
            body = journal_event_body(e, json.loads(e["payload_json"]))
            digest = hmac.new(key.encode(), _PREFIX + json.dumps(body, sort_keys=True).encode(), hashlib.sha256).hexdigest()
            side.execute("UPDATE events SET prev_event_hash=?, event_hash=? WHERE event_id=?", (prev, digest, e["event_id"]))
            prev = digest
        side.execute("UPDATE control SET journal_head_hash=? WHERE id=1", (prev,))
        side.commit()
    finally:
        side.close()


@pytest.fixture
def db_path(kernel):
    assert kernel.db_path
    return kernel.db_path


def _hold_type(exc: BaseException) -> str:
    return type(exc).__name__


# ---------------------------------------------------------------- phân loại nguyên nhân

def test_verify_journal_classifies_foreign_key_domain(kernel, db_path):
    _queued(kernel, "t-foreign")
    _rechain_foreign(db_path, "t-foreign")
    res = kernel.verify_journal("t-foreign")
    assert res["hash_chain_valid"] is False
    # cấu trúc nguyên vẹn, MỌI event lệch khóa đồng loạt ⇒ miền khóa khác, không phải sửa nội dung
    assert res.get("integrity_class") == "KEY_DOMAIN_MISMATCH"
    assert res.get("hash_failed") == res["event_count"] == 4


def test_verify_journal_classifies_content_tamper(kernel, db_path):
    _queued(kernel, "t-content")
    side = sqlite3.connect(db_path)
    try:
        side.execute(
            "UPDATE events SET payload_json=? WHERE task_id=? AND seq=2",
            (json.dumps({"injected": "after-signing"}), "t-content"),
        )
        side.commit()
    finally:
        side.close()
    res = kernel.verify_journal("t-content")
    assert res["hash_chain_valid"] is False
    # chỉ 1/4 event lệch, 3 event khác vẫn khớp khóa hiện hành ⇒ nội dung bị sửa sau khi ký
    assert res.get("integrity_class") == "CONTENT_TAMPER"
    assert res.get("hash_failed") == 1


def test_verify_journal_classifies_structural_tamper(kernel, db_path):
    _queued(kernel, "t-struct")
    side = sqlite3.connect(db_path)
    try:
        side.execute("DELETE FROM events WHERE task_id=? AND seq=2", ("t-struct",))
        side.commit()
    finally:
        side.close()
    res = kernel.verify_journal("t-struct")
    assert res["hash_chain_valid"] is False
    assert res.get("integrity_class") == "STRUCTURAL_TAMPER"


def test_valid_chain_has_no_integrity_class(kernel):
    _queued(kernel, "t-ok")
    res = kernel.verify_journal("t-ok")
    assert res["hash_chain_valid"] is True
    assert res.get("integrity_class") is None
    assert res.get("hash_failed") == 0


# ---------------------------------------------------------------- HOLD fail-closed

def test_claim_on_unprovable_chain_is_held_and_evidence_untouched(kernel, db_path):
    _queued(kernel, "t-held")
    _rechain_foreign(db_path, "t-held")
    before = _rows(db_path, "t-held")
    with pytest.raises(KillSwitchActive) as excinfo:
        kernel.claim("t-held", "worker-1")
    assert _hold_type(excinfo.value) == "JournalIntegrityHold"
    assert getattr(excinfo.value, "task_id", None) == "t-held"
    assert getattr(excinfo.value, "integrity_class", None) == "KEY_DOMAIN_MISMATCH"
    # fail-closed: không lease, không đổi state, journal (bằng chứng) giữ nguyên từng byte
    assert kernel.get_task("t-held")["state"] == "QUEUED"
    assert _rows(db_path, "t-held") == before
    assert kernel._control()["global_kill"] == 0  # HOLD theo phạm vi task, KHÔNG bật kill toàn hệ thống


def test_resume_edge_held_but_containment_edge_allowed(kernel, db_path):
    kernel.create_task("t-ready", "jc-owner", "goal", "R1", input_hash="sha256:" + "1" * 64)
    kernel.transition("t-ready", "PLANNING", actor="planner", reason="jc")
    kernel.transition("t-ready", "READY", actor="planner", reason="jc")
    _rechain_foreign(db_path, "t-ready")
    with pytest.raises(KillSwitchActive) as excinfo:
        kernel.transition("t-ready", "QUEUED", actor="planner", reason="resume")
    assert _hold_type(excinfo.value) == "JournalIntegrityHold"
    assert kernel.get_task("t-ready")["state"] == "READY"
    # cạnh giữ lại (containment) vẫn đi được: SCP tự chặn chứ không kẹt task
    kernel.cancel("t-ready", actor="journal-chain-test")
    assert kernel.get_task("t-ready")["state"] == "CANCELLED"


def test_autonomous_human_review_resolution_is_held(kernel, db_path):
    """Cổng thật thả backlog HUMAN_REVIEW (auto_resolve, autonomous_mode) không được đưa task trên
    chuỗi không chứng minh được về READY — đúng hình dạng 5 task HR trên chuỗi khóa lạ của DB sự cố."""
    _queued(kernel, "t-hr")
    lease = kernel.claim("t-hr", "worker-h")
    kernel.start("t-hr", lease.lease_id)
    kernel.transition("t-hr", "HUMAN_REVIEW", lease_id=lease.lease_id)
    _rechain_foreign(db_path, "t-hr")
    before = _rows(db_path, "t-hr")
    kernel.autonomous_mode = True
    with pytest.raises(KillSwitchActive) as excinfo:
        kernel.auto_resolve_human_review("t-hr", reason="autonomous_verification_passed")
    assert _hold_type(excinfo.value) == "JournalIntegrityHold"
    assert kernel.get_task("t-hr")["state"] == "HUMAN_REVIEW"
    assert _rows(db_path, "t-hr") == before


def test_claim_next_skips_held_task_and_serves_others(kernel, db_path):
    _queued(kernel, "t-bad", priority=0)   # ưu tiên cao nhất nhưng chuỗi không chứng minh được
    _queued(kernel, "t-good", priority=9)
    _rechain_foreign(db_path, "t-bad")
    lease = kernel.claim_next("worker-q", max_active_per_owner=5)
    assert lease is not None and lease.task_id == "t-good"
    assert kernel.get_task("t-bad")["state"] == "QUEUED"
    assert kernel.get_task("t-good")["state"] == "LEASED"


def test_start_and_checkpoint_on_unprovable_chain_are_held(kernel, db_path):
    _queued(kernel, "t-run")
    lease = kernel.claim("t-run", "worker-r")
    _rechain_foreign(db_path, "t-run")
    with pytest.raises(KillSwitchActive) as excinfo:
        kernel.start("t-run", lease.lease_id)
    assert _hold_type(excinfo.value) == "JournalIntegrityHold"
    assert kernel.get_task("t-run")["state"] == "LEASED"


def test_checkpoint_write_before_side_effect_is_held(kernel, db_path):
    _queued(kernel, "t-cp")
    lease = kernel.claim("t-cp", "worker-c")
    kernel.start("t-cp", lease.lease_id)
    _rechain_foreign(db_path, "t-cp")
    before = _rows(db_path, "t-cp")
    with pytest.raises(KillSwitchActive) as excinfo:
        kernel.checkpoint("t-cp", lease.lease_id, "step-1", "RUNNING", {"tool": "noop"}, 0, "jc-idem-1")
    assert _hold_type(excinfo.value) == "JournalIntegrityHold"
    assert _rows(db_path, "t-cp") == before


def test_expired_lease_on_held_checkpointed_task_is_contained_not_requeued(kernel, db_path):
    _queued(kernel, "t-exp")
    lease = kernel.claim("t-exp", "worker-e", ttl_seconds=5)
    kernel.start("t-exp", lease.lease_id)
    kernel.transition("t-exp", "CHECKPOINTED", actor="worker-e", reason="jc", lease_id=lease.lease_id)
    _rechain_foreign(db_path, "t-exp")
    expired = kernel.expire_leases(now=time.time() + 3600)
    assert "t-exp" in expired or kernel.get_task("t-exp")["active_lease_id"] is None
    # watchdog không phải lối vòng quanh HOLD: giữ CHECKPOINTED (containment), không requeue
    assert kernel.get_task("t-exp")["state"] == "CHECKPOINTED"


# ---------------------------------------------------------------- tự phục hồi từ bằng chứng của chính journal

def test_hold_lifts_when_journal_becomes_provable_again(kernel, db_path, monkeypatch):
    _queued(kernel, "t-rot")
    _rechain_foreign(db_path, "t-rot")
    with pytest.raises(KillSwitchActive):
        kernel.claim("t-rot", "worker-1")
    # bằng chứng khóa: khóa ghi chuỗi được khai báo là K_journal đã nghỉ (rotation có cấu hình)
    monkeypatch.setenv("SCP_PREVIOUS_JOURNAL_SECRETS", _FOREIGN)
    assert kernel.verify_journal("t-rot")["hash_chain_valid"] is True
    lease = kernel.claim("t-rot", "worker-1")
    assert lease.task_id == "t-rot"
    assert kernel.get_task("t-rot")["state"] == "LEASED"


def test_global_kill_still_engageable_with_held_chains(kernel, db_path):
    _queued(kernel, "t-k")
    _rechain_foreign(db_path, "t-k")
    kernel.set_global_kill(True, actor="human-operator")
    assert kernel._control()["global_kill"] == 1
    kernel.set_global_kill(False, actor="human-operator")
    assert kernel._control()["global_kill"] == 0


def test_verify_integrity_reports_class_and_state_per_invalid_chain(kernel, db_path):
    _queued(kernel, "t-a")
    _queued(kernel, "t-b")
    _rechain_foreign(db_path, "t-a")
    report = kernel.verify_integrity()
    invalid = {c["task_id"]: c for c in report["invalid_chains"]}
    assert set(invalid) == {"t-a"}
    assert invalid["t-a"].get("integrity_class") == "KEY_DOMAIN_MISMATCH"
    assert invalid["t-a"].get("state") == "QUEUED"
