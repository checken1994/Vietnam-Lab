"""[JOURNAL-CHAIN 2026-10-31] Phản ứng của vòng Nghi ngờ khi chuỗi journal không chứng minh được.

Sự cố thật: 2,288/4,225 vòng DOUBT_DETECTED (invalid_chains_count=9) kể từ 2026-10-27, nhưng:
  * từ vòng thứ 2 cảnh báo bị hạ xuống DEBUG (``DOUBT_REPEATED``) — nghi ngờ biến mất khỏi log;
  * không có trạng thái bền (đếm, lần đầu/lần cuối) — mỗi tiến trình khởi động lại "quên";
  * /ready vẫn trả ``ready`` — người vận hành không thấy gì;
  * không có bài học nào được ghi — SCP không học được gì từ 2,288 lần báo;
  * vòng Nghi ngờ chỉ kiểm ``<data_dir>/ask_task_kernel.sqlite3``, KHÔNG kiểm DB mà /ask thật sự
    ghi (``SCP_KERNEL_DB_PATH``).

Hợp đồng được khóa ở đây: báo động là TRẠNG THÁI BỀN theo nguyên nhân gốc (``doubt_alarm_state.json``
trong chính data_dir), mỗi vòng còn mở vẫn là WARNING, đúng 1 bài học HYPOTHESIS mỗi lần mở (qua
wire ErrorStore đã duyệt, chỉ vào kho của chính data_dir), /ready ``degraded`` trung thực, tự gỡ khi
bằng chứng (journal) chứng minh được lại; KHÔNG bao giờ bật kill switch toàn cục.

Zero-mock cho phần đang kiểm: TaskKernel + SQLite thật, ErrorStore thật, /ready qua ASGI thật.
Chỉ ``_check_fitness`` (Golden Suite, ngoài phạm vi) được thay bằng kết quả ok qua seam đặt tên của
``run_doubt_cycle``. Mọi khóa là chuỗi test giả.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import scp.core.doubt_cron as doubt_cron
from scp.task_kernel import KillSwitchActive, TaskKernel
from scp.task_kernel_parts.definitions import journal_event_body

_CAPABILITY = "journal-chain-doubt-capability-key-tests-only-01"
_JOURNAL = "journal-chain-doubt-K_journal-current-tests-only-02"
_FOREIGN = "journal-chain-doubt-K_journal-foreign-tests-only-0003"
_PREFIX = b"scp.journal.v1\x00"
_STATE = "doubt_alarm_state.json"
_DEFAULT_DB = "ask_task_kernel.sqlite3"
_KEY = f"kernel_integrity|{_DEFAULT_DB}|KEY_DOMAIN_MISMATCH"


def _fitness_ok(data_dir: str) -> dict:
    return {"check": "fitness_drift", "ok": True, "detail": {"verdict": "NOT_RUN_IN_THIS_TEST"}}


def _queued(k: TaskKernel, task_id: str, priority: int = 5) -> None:
    k.create_task(task_id, "jcd-owner-" + task_id, "goal", "R1", input_hash="sha256:" + "0" * 64, priority=priority)
    k.transition(task_id, "PLANNING", actor="planner", reason="jcd")
    k.transition(task_id, "READY", actor="planner", reason="jcd")
    k.transition(task_id, "QUEUED", actor="planner", reason="jcd")


def _rechain_foreign(db_path: Path, task_id: str, key: str = _FOREIGN) -> None:
    """Tiến trình KHÁC khóa ghi lại toàn bộ chuỗi (cấu trúc nguyên vẹn, HMAC hợp lệ dưới khóa lạ)."""
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


def _build_kernel(db_path: Path, good: list[str], foreign: list[str]) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    k = TaskKernel(db_path)
    try:
        for tid in [*good, *foreign]:
            _queued(k, tid)
    finally:
        k.close()
    for tid in foreign:
        _rechain_foreign(db_path, tid)


def _events(db_path: Path, task_id: str) -> list[tuple]:
    side = sqlite3.connect(db_path)
    try:
        return side.execute(
            "SELECT event_id, seq, type, payload_json, prev_event_hash, event_hash FROM events WHERE task_id=? ORDER BY seq",
            (task_id,),
        ).fetchall()
    finally:
        side.close()


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("SCP_CAPABILITY_SECRET", _CAPABILITY)
    monkeypatch.setenv("SCP_JOURNAL_SECRET", _JOURNAL)
    for name in (
        "SCP_PREVIOUS_CAPABILITY_SECRETS", "SCP_PREVIOUS_JOURNAL_SECRETS", "SCP_JOURNAL_SECRET_FILE",
        "SCP_KERNEL_DB_PATH",
    ):
        monkeypatch.delenv(name, raising=False)
    root = tmp_path / "rt"
    data = root / "data"
    data.mkdir(parents=True)
    monkeypatch.chdir(root)  # kho ErrorStore mặc định (cwd-relative "data/") = kho của chính data_dir này
    monkeypatch.setenv("SCP_DATA_DIR", str(data))
    monkeypatch.setattr(doubt_cron, "_check_fitness", _fitness_ok)
    monkeypatch.setattr(doubt_cron, "_LESSON_STORE_FACTORY", None, raising=False)
    db = data / _DEFAULT_DB
    _build_kernel(db, good=["t-good"], foreign=["t-bad"])
    return SimpleNamespace(root=root, data=data, db=db)


def _state(data: Path) -> dict:
    path = data / _STATE
    assert path.exists(), "doubt alarm state must be durable in data_dir (none written)"
    return json.loads(path.read_text(encoding="utf-8"))


def _check(report: dict, name: str) -> dict:
    return next(c for c in report["checks"] if c["check"] == name)


def _lessons(data: Path) -> list[dict]:
    path = data / "error_store.jsonl"
    if not path.exists():
        return []
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [r for r in rows if r.get("domain") == "kernel_integrity"]


def _ready(monkeypatch) -> dict:
    from fastapi.testclient import TestClient

    from scp.api_server import app

    monkeypatch.setattr(app.state, "judge_ready", True, raising=False)
    resp = TestClient(app).get("/ready")
    assert resp.status_code == 200  # degraded KHÔNG đổi mã HTTP (container healthcheck dùng /health)
    return resp.json()


# ---------------------------------------------------------------- báo động bền, không im lặng


def test_repeated_alarm_stays_warning_with_durable_counts(runtime, caplog):
    caplog.set_level(logging.DEBUG, logger="scp.core.doubt_cron")
    levels_per_cycle = []
    cron = doubt_cron.DoubtCron(data_dir=str(runtime.data), interval_seconds=3600)
    for _ in range(3):
        caplog.clear()
        assert cron.run_once()["verdict"] == "DOUBT_DETECTED"
        levels_per_cycle.append({r.levelno for r in caplog.records if r.name == "scp.core.doubt_cron"})
    assert all(logging.WARNING in levels for levels in levels_per_cycle), levels_per_cycle
    alarm = _state(runtime.data)["alarms"][_KEY]
    assert (alarm["status"], alarm["occurrences"], alarm["consecutive"], alarm["opened_count"]) == ("ACTIVE", 3, 3, 1)
    assert alarm["first_seen"] <= alarm["last_seen"]
    assert alarm["last_detail"]["chains"] == 1
    # trạng thái nằm ở đĩa, không ở RAM: một tiến trình/instance mới tiếp tục đếm
    doubt_cron.DoubtCron(data_dir=str(runtime.data), interval_seconds=3600).run_once()
    again = _state(runtime.data)["alarms"][_KEY]
    assert (again["occurrences"], again["first_seen"]) == (4, alarm["first_seen"])


def test_exactly_one_hypothesis_lesson_per_root_cause(runtime):
    for _ in range(3):
        doubt_cron.run_doubt_cycle(str(runtime.data))
    lessons = _lessons(runtime.data)
    assert len(lessons) == 1, f"expected exactly 1 lesson for one open root cause, got {len(lessons)}"
    lesson = lessons[0]
    assert lesson["verdict"] == "UNVERIFIED"
    assert lesson["error_type"] == "doubt_kernel_integrity_key_domain_mismatch"
    d = lesson["details"]
    assert (d["epistemic_status"], d["verified"], d["promotion"]) == ("HYPOTHESIS", False, "never_automatic")
    assert (d["classification"], d["alarm_key"]) == ("KEY_DOMAIN_MISMATCH", _KEY)
    assert d["evidence"]["chains"] == 1
    assert _state(runtime.data)["alarms"][_KEY]["lesson"] == "recorded"


def test_held_tasks_reported_and_refused_by_kernel(runtime):
    before = _events(runtime.db, "t-bad")
    detail = _check(doubt_cron.run_doubt_cycle(str(runtime.data)), "kernel_integrity")["detail"]
    assert detail["by_class"] == {"KEY_DOMAIN_MISMATCH": 1}
    assert detail["held_count"] == 1
    held = detail["targets"][0]["held_tasks"]
    assert [(h["task_id"], h["state"], h["integrity_class"]) for h in held] == [("t-bad", "QUEUED", "KEY_DOMAIN_MISMATCH")]
    k = TaskKernel(runtime.db)
    try:
        with pytest.raises(KillSwitchActive):
            k.claim("t-bad", "worker-jcd")
        lease = k.claim_next("worker-jcd", max_active_per_owner=5)
        assert lease is not None and lease.task_id == "t-good"
    finally:
        k.close()
    assert _events(runtime.db, "t-bad") == before  # không xóa/sửa bằng chứng


def test_ledger_row_carries_classification_and_alarm_summary(runtime):
    doubt_cron.run_doubt_cycle(str(runtime.data))
    rows = (runtime.data / "doubt_ledger.jsonl").read_text(encoding="utf-8").splitlines()
    row = json.loads(rows[-1])
    detail = _check(row, "kernel_integrity")["detail"]
    assert (detail["invalid_chains_count"], detail["by_class"], detail["held_count"]) == (1, {"KEY_DOMAIN_MISMATCH": 1}, 1)
    assert row["alarms"]["active"] == [_KEY] and row["alarms"]["opened"] == [_KEY]


# ---------------------------------------------------------------- đúng DB, đúng phạm vi


def test_doubt_verifies_configured_kernel_db_path(runtime, monkeypatch):
    prod = runtime.root / "prod" / "task_kernel.sqlite3"
    _build_kernel(prod, good=["p-good"], foreign=["p-bad-1", "p-bad-2"])
    monkeypatch.setenv("SCP_KERNEL_DB_PATH", str(prod))
    report = doubt_cron.run_doubt_cycle(str(runtime.data))
    detail = _check(report, "kernel_integrity")["detail"]
    assert detail["invalid_chains_count"] == 3, "the DB /ask actually writes (SCP_KERNEL_DB_PATH) must be verified"
    per_db = {t["db"]: t["invalid_chains_count"] for t in detail["targets"]}
    assert per_db == {"task_kernel.sqlite3": 2, _DEFAULT_DB: 1}
    assert set(report["alarms"]["active"]) == {_KEY, "kernel_integrity|task_kernel.sqlite3|KEY_DOMAIN_MISMATCH"}


def test_doubt_never_engages_global_kill(runtime):
    for _ in range(3):
        doubt_cron.run_doubt_cycle(str(runtime.data))
    k = TaskKernel(runtime.db)
    try:
        assert k._control()["global_kill"] == 0  # quyền bật kill toàn hệ thống là của người
    finally:
        k.close()


def test_no_lesson_written_outside_the_doubted_data_dir(runtime, monkeypatch, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)  # kho mặc định giờ trỏ vào elsewhere/data — KHÔNG phải data_dir đang bị nghi ngờ
    doubt_cron.run_doubt_cycle(str(runtime.data))
    assert not (elsewhere / "data" / "error_store.jsonl").exists()
    assert _lessons(runtime.data) == []
    assert _state(runtime.data)["alarms"][_KEY]["lesson"] == "skipped:store_outside_data_dir"


# ---------------------------------------------------------------- /ready trung thực, tự gỡ bằng bằng chứng


def test_ready_degraded_while_alarm_open_then_recovers_by_evidence(runtime, monkeypatch):
    assert _ready(monkeypatch)["status"] == "ready"
    doubt_cron.run_doubt_cycle(str(runtime.data))
    body = _ready(monkeypatch)
    assert body["status"] == "degraded"
    assert (body["checks"]["kernel_journal"], body["checks"]["doubt"]) == ("degraded", "degraded")
    assert "KEY_DOMAIN_MISMATCH" in body["checks"]["doubt_active_alarms"]
    # khóa đã nghỉ được khai báo ⇒ chính journal chứng minh được lại ⇒ báo động tự gỡ, không cần người duyệt
    monkeypatch.setenv("SCP_PREVIOUS_JOURNAL_SECRETS", _FOREIGN)
    report = doubt_cron.run_doubt_cycle(str(runtime.data))
    assert report["verdict"] == "CLEAN" and report["alarms"]["cleared"] == [_KEY]
    after = _ready(monkeypatch)
    assert (after["status"], after["checks"]["kernel_journal"], after["checks"]["doubt"]) == ("ready", "ok", "ok")
    assert _state(runtime.data)["alarms"][_KEY]["status"] == "CLEARED"


def test_stale_doubt_state_degrades_ready(runtime, monkeypatch):
    monkeypatch.setenv("SCP_PREVIOUS_JOURNAL_SECRETS", _FOREIGN)  # dữ liệu sạch: chỉ kiểm tính "còn sống"
    doubt_cron.DoubtCron(data_dir=str(runtime.data), interval_seconds=60).run_once()
    state = _state(runtime.data)
    assert state["last_verdict"] == "CLEAN"
    assert _ready(monkeypatch)["status"] == "ready"
    from datetime import datetime, timezone

    state["last_cycle_at"] = datetime.fromtimestamp(time.time() - 10 * 3600, timezone.utc).isoformat()
    (runtime.data / _STATE).write_text(json.dumps(state), encoding="utf-8")  # vòng Nghi ngờ ngừng chạy 10h
    body = _ready(monkeypatch)
    assert (body["status"], body["checks"]["doubt"]) == ("degraded", "stale")


def test_check_error_does_not_clear_an_open_alarm(runtime, monkeypatch):
    doubt_cron.run_doubt_cycle(str(runtime.data))

    def _broken(data_dir: str) -> dict:
        raise RuntimeError("kernel db unreadable in this cycle")

    monkeypatch.setattr(doubt_cron, "_check_kernel_integrity", _broken)
    doubt_cron.run_doubt_cycle(str(runtime.data))
    alarms = _state(runtime.data)["alarms"]
    # không có bằng chứng mới ≠ đã khỏi: báo động cũ giữ ACTIVE (không đếm thêm), lỗi check là báo động riêng
    assert (alarms[_KEY]["status"], alarms[_KEY]["occurrences"]) == ("ACTIVE", 1)
    assert alarms["kernel_integrity|-|CHECK_ERROR"]["status"] == "ACTIVE"
    assert _ready(monkeypatch)["status"] == "degraded"
