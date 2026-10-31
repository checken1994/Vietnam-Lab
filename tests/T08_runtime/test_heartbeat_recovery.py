"""[HEARTBEAT-RECOVERY 2026-10-31] Bộ giám sát chết phải tự lộ ra + tự phục hồi.

Gốc lỗi (VERIFIED-RUN, xem docs/evidence/heartbeat-recovery-done.final.md):
``data/subsystem_heartbeat.sqlite`` sống bị ghi đồng thời bởi container Linux và tiến
trình Windows trên host qua bind-mount ⇒ SQLite WAL không có khoá/-shm chung ⇒ file hỏng
(header khai 500 trang, file chỉ có 497; trang interior trỏ tới 498-500). Hệ quả base:
``SubsystemTelemetry`` raise ⇒ attack monitor + deep audit không đăng ký / vòng chết,
/ready vẫn im lặng; attack monitor đếm ``governance_kill`` không ai phát (luôn 0).

Mọi test chạy trong tmp_path; bài học đi vào store giả (LESSON_STORE_FACTORY) — không
ghi data/ sống.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import os
import sqlite3
import time
from pathlib import Path

import pytest

from scp.core import subsystem_telemetry as st
from scp.core.subsystem_telemetry import SubsystemTelemetry, heartbeat_sleep


def _optional(name: str):
    # Import per-test (not at collection) so red-before on base shows WHICH behaviour is
    # missing instead of one collection error. On the fix it is always present (asserted).
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError:
        return None


hb = _optional("scp.core.heartbeat_store")
DB_NAME = "subsystem_heartbeat.sqlite"
QUARANTINE_INFIX = ".corrupt-"
OWNER_SUFFIX = ".owner.json"


class _FakeLessonStore:
    def __init__(self) -> None:
        self.records: list[dict] = []

    def add(self, **kw):
        self.records.append(kw)
        return {"id": f"L{len(self.records)}"}


@pytest.fixture
def lessons(monkeypatch):
    store = _FakeLessonStore()
    if hb is not None:
        monkeypatch.setattr(hb, "LESSON_STORE_FACTORY", lambda: store)
        hb._lesson_seen.clear()
    for key in ("SCP_HEARTBEAT_AUTO_QUARANTINE", "SCP_HEARTBEAT_QUARANTINE_MAX_PER_DAY",
                "SCP_HEARTBEAT_KERNEL_GUARD", "SCP_HEARTBEAT_OWNER_TTL_S", "SCP_MONITOR_LIVENESS_MISSED"):
        monkeypatch.delenv(key, raising=False)
    yield store
    if hb is not None:
        hb._lesson_seen.clear()
    if hasattr(st, "_LIVENESS_REGISTRY"):
        with st._LIVENESS_LOCK:
            st._LIVENESS_REGISTRY.clear()


def test_heartbeat_store_module_present():
    assert hb is not None, "scp.core.heartbeat_store (quarantine + kernel guard) missing"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _make_production_like_corruption(data_dir: Path) -> Path:
    """Tái tạo đúng HÌNH DẠNG hỏng của file thật: header khai N trang nhưng đuôi file mất
    vài trang mà trang interior của bảng events vẫn trỏ tới (SQLITE_CORRUPT)."""
    db = data_dir / DB_NAME
    # Built with plain sqlite3 (independent of the code under test).
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE subsystem_heartbeat_events (seq INTEGER PRIMARY KEY AUTOINCREMENT, subsystem TEXT,"
                 " instance_id TEXT, event_type TEXT, status TEXT, run_id TEXT, ts_utc TEXT, payload_json TEXT)")
    conn.executemany(
        "INSERT INTO subsystem_heartbeat_events(subsystem,instance_id,event_type,status,run_id,ts_utc,payload_json)"
        " VALUES(?,?,?,?,?,?,?)",
        [("seed", "seed-1", "cycle_completed", "SUCCESS", f"r{i}", "2026-10-30T05:06:47+00:00", "x" * 300)
         for i in range(400)],
    )
    conn.commit()
    conn.close()
    raw = db.read_bytes()
    page = int.from_bytes(raw[16:18], "big")
    db.write_bytes(raw[: len(raw) - 3 * page])
    for side in ("-wal", "-shm"):
        p = db.with_name(db.name + side)
        if p.exists():
            p.unlink()
    probe = sqlite3.connect(db)
    try:
        with pytest.raises(sqlite3.DatabaseError) as exc_info:
            probe.execute("SELECT count(*) FROM sqlite_master").fetchall()
    finally:
        probe.close()  # Windows: an open handle would block the rename (by design ⇒ rename_failed)
    assert exc_info.value.sqlite_errorcode == 11  # SQLITE_CORRUPT — đúng như file thật
    return db


def _quarantined(data_dir: Path) -> list[Path]:
    return sorted(p for p in data_dir.glob(f"{DB_NAME}{QUARANTINE_INFIX}*") if p.suffix != ".json"
                  and not p.name.endswith(("-wal", "-shm")))


# --------------------------------------------------------------------------- quarantine


def test_corrupt_db_is_quarantined_with_sha256_and_monitor_keeps_running(tmp_path, lessons):
    db = _make_production_like_corruption(tmp_path)
    original_sha = _sha(db)

    t = SubsystemTelemetry("attack_monitor", tmp_path)  # base: raises DatabaseError

    moved = _quarantined(tmp_path)
    assert len(moved) == 1
    assert moved[0].name.endswith(original_sha[:12])
    assert _sha(moved[0]) == original_sha, "evidence must be kept byte-identical"
    manifest = json.loads(moved[0].with_name(moved[0].name + ".json").read_text(encoding="utf-8"))
    assert manifest["sha256"] == original_sha and manifest["quarantined"] is True
    assert manifest["error_code"] == 11 and manifest["subsystem"] == "attack_monitor"
    # Fresh DB works end-to-end.
    t.start(mode="background", config={"interval_seconds": 300})
    t.cycle_started("r1")
    t.cycle_completed("r1", "SUCCESS", asked=0, verified=1)
    snap = t.snapshot()
    assert snap["cycles_completed"] == 1 and snap["last_status"] == "SUCCESS"
    assert sqlite3.connect(db).execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert t.storage_state == "RECOVERED"
    # Not silent: a structured HYPOTHESIS lesson was recorded.
    kinds = [r["error_type"] for r in lessons.records]
    assert kinds == ["heartbeat_heartbeat_db_corrupt"]
    details = lessons.records[0]["details"]
    assert details["epistemic_status"] == "HYPOTHESIS" and details["verified"] is False
    assert details["sha256"] == original_sha


def test_recovered_storage_does_not_spam_logs(tmp_path, lessons, caplog):
    """Probe uvicorn 18801 lộ ra: sau cách ly, mỗi tick log "back to OK" (spam)."""
    _make_production_like_corruption(tmp_path)
    t = SubsystemTelemetry("deep_audit", tmp_path)
    caplog.clear()
    with caplog.at_level("WARNING", logger="scp.core.subsystem_telemetry"):
        for _ in range(5):
            t.tick(status="IDLE")
        t.snapshot()
    assert [r.getMessage() for r in caplog.records] == []
    assert t.storage_state == "RECOVERED"


def test_not_a_database_file_is_quarantined(tmp_path, lessons):
    db = tmp_path / DB_NAME
    db.write_bytes(b"\x00garbage-not-sqlite" * 400)
    sha = _sha(db)
    t = SubsystemTelemetry("deep_audit", tmp_path)
    moved = _quarantined(tmp_path)
    assert len(moved) == 1 and _sha(moved[0]) == sha
    assert json.loads(moved[0].with_name(moved[0].name + ".json").read_text(encoding="utf-8"))["error_code"] == 26
    t.tick(status="IDLE")
    assert t.snapshot()["subsystem"] == "deep_audit"


def test_quarantine_cap_is_durable_and_degrades_honestly(tmp_path, lessons, monkeypatch):
    monkeypatch.setenv("SCP_HEARTBEAT_QUARANTINE_MAX_PER_DAY", "1")
    db = _make_production_like_corruption(tmp_path)
    # A previous quarantine (e.g. before a restart) is on disk within 24h.
    (tmp_path / f"{DB_NAME}{QUARANTINE_INFIX}20261031T000000Z-deadbeef0000.json").write_text("{}", encoding="utf-8")
    sha = _sha(db)
    t = SubsystemTelemetry("attack_monitor", tmp_path)
    assert _sha(db) == sha, "cap reached: corrupt file must NOT be renamed again"
    assert _quarantined(tmp_path) == []
    assert t.storage_state == "UNAVAILABLE"
    # Monitor API never raises; state is exposed instead of silence.
    t.start()
    event = t.tick(status="IDLE")
    assert event["storage_state"] == "UNAVAILABLE"
    t.declare_cycle(300, 0)
    live = t.liveness()
    assert live["state"] == "DEGRADED" and live["reason"] == "telemetry_storage_unavailable"
    assert t.snapshot()["status"] == "TELEMETRY_UNAVAILABLE"


def test_kill_switch_disables_quarantine(tmp_path, lessons, monkeypatch):
    monkeypatch.setenv("SCP_HEARTBEAT_AUTO_QUARANTINE", "0")
    db = _make_production_like_corruption(tmp_path)
    sha = _sha(db)
    t = SubsystemTelemetry("attack_monitor", tmp_path)
    t.tick(status="IDLE")
    assert _sha(db) == sha and _quarantined(tmp_path) == []
    assert t.storage_state == "UNAVAILABLE"


def test_rename_failure_degrades_without_raising(tmp_path, lessons, monkeypatch):
    db = _make_production_like_corruption(tmp_path)
    sha = _sha(db)

    def _deny(*_a, **_k):
        raise PermissionError(32, "file in use by another process")

    monkeypatch.setattr(hb.os, "replace", _deny)
    t = SubsystemTelemetry("attack_monitor", tmp_path)
    assert t.last_quarantine["reason"] == "rename_failed:PermissionError"
    assert t.storage_state == "UNAVAILABLE" and _sha(db) == sha
    t.tick(status="IDLE")  # still no raise


def test_healthy_db_is_never_quarantined_on_misreported_error(tmp_path, lessons):
    t = SubsystemTelemetry("evolution", tmp_path)
    t.tick(status="IDLE")
    db = tmp_path / DB_NAME
    fake = sqlite3.DatabaseError("database disk image is malformed")
    fake.sqlite_errorcode = 11
    result = hb.quarantine_corrupt(db, fake, subsystem="evolution")
    assert result["quarantined"] is False and result["reason"] == "recheck_ok"
    assert db.exists() and _quarantined(tmp_path) == []


def test_corruption_mid_run_does_not_kill_heartbeat_loop(tmp_path, lessons):
    t = SubsystemTelemetry("attack_monitor", tmp_path)
    t.start()
    t.tick(status="IDLE")
    db = tmp_path / DB_NAME
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()
    db.write_bytes(b"\xff" * 8192)  # disk image destroyed while monitor sleeps
    heartbeat_sleep(t, 0.01, status="IDLE")  # base: DatabaseError escapes ⇒ monitor loop dies
    t.cycle_started("r2")
    t.cycle_completed("r2", "SUCCESS", asked=0, verified=1)
    assert len(_quarantined(tmp_path)) == 1
    assert t.snapshot()["cycles_completed"] == 1


def test_evolution_stays_fail_closed_after_heartbeat_recovery(tmp_path, lessons, monkeypatch):
    """Subsystem thứ ba chết cùng file (log container 03:46:05 "[EVOLUTION] Runtime
    initialization failed: database disk image is malformed"). Đính chính coordinator:
    khi DB được tạo lại, evolution KHÔNG được tự hồi sinh (evolution_parts chưa có guard
    protected-path) — chỉ báo liveness DEGRADED trung thực; monitor chỉ-đọc vẫn phục hồi."""
    from scp.autofix import evolution as evo

    monkeypatch.setattr(evo, "get_autofix_engine", lambda data_dir="data": object())
    monkeypatch.setenv("SCP_EVOLUTION_ENABLED", "1")
    monkeypatch.delenv("SCP_EVOLUTION_HEARTBEAT_RECOVERY_ACK", raising=False)
    db = _make_production_like_corruption(tmp_path)
    sha = _sha(db)
    # Read-only monitor recovers first (as in lifespan: deep_audit/attack_monitor boot first).
    monitor = SubsystemTelemetry("attack_monitor", tmp_path)
    assert monitor.storage_state == "RECOVERED" and len(_quarantined(tmp_path)) == 1
    with pytest.raises(hb.EvolutionBlockedError):
        evo.EvolutionEngine(data_dir=str(tmp_path))  # fresh healthy DB, still blocked
    with pytest.raises(hb.EvolutionBlockedError):
        evo.EvolutionEngine(data_dir=str(tmp_path))  # a retry / later boot does not revive it
    blocked = st.registered_monitors()["evolution"]
    live = blocked.liveness()
    assert live["state"] == "DEGRADED" and live["reason"].startswith("blocked_fail_closed:heartbeat_recovered")
    assert _last_ledger(tmp_path, "evolution")["event_type"] == "blocked"
    # A wrong/old ack does not unblock; only the human ack of THIS quarantine's sha does.
    monkeypatch.setenv("SCP_EVOLUTION_HEARTBEAT_RECOVERY_ACK", "000000000000")
    with pytest.raises(hb.EvolutionBlockedError):
        evo.EvolutionEngine(data_dir=str(tmp_path))
    monkeypatch.setenv("SCP_EVOLUTION_HEARTBEAT_RECOVERY_ACK", sha[:12])
    engine = evo.EvolutionEngine(data_dir=str(tmp_path))
    assert engine._telemetry.liveness()["state"] == "PENDING_FIRST_CYCLE"


def test_evolution_blocked_while_corrupt_file_kept_by_kill_switch(tmp_path, lessons, monkeypatch):
    from scp.autofix import evolution as evo

    monkeypatch.setattr(evo, "get_autofix_engine", lambda data_dir="data": object())
    monkeypatch.setenv("SCP_HEARTBEAT_AUTO_QUARANTINE", "0")
    _make_production_like_corruption(tmp_path)
    with pytest.raises(hb.EvolutionBlockedError, match="heartbeat_storage_unavailable"):
        evo.EvolutionEngine(data_dir=str(tmp_path))


def test_evolution_unchanged_when_no_recovery_happened(tmp_path, lessons, monkeypatch):
    from scp.autofix import evolution as evo

    monkeypatch.setattr(evo, "get_autofix_engine", lambda data_dir="data": object())
    monkeypatch.delenv("SCP_EVOLUTION_ENABLED", raising=False)
    engine = evo.EvolutionEngine(data_dir=str(tmp_path))
    assert engine._telemetry.liveness()["state"] == "DISABLED"


# --------------------------------------------------------------------------- liveness


def test_liveness_never_completed_becomes_degraded(tmp_path, lessons, monkeypatch):
    t = SubsystemTelemetry("deep_audit", tmp_path)
    t.start()
    t.declare_cycle(10, initial_delay_seconds=0)
    assert t.liveness()["state"] == "PENDING_FIRST_CYCLE"
    real = time.time
    monkeypatch.setattr(st.time, "time", lambda: real() + 31)
    live = t.liveness()
    assert live["state"] == "DEGRADED" and live["reason"] == "never_completed_a_cycle"


def test_liveness_restart_cannot_hide_never_completed(tmp_path, lessons):
    first = SubsystemTelemetry("deep_audit", tmp_path)
    first.start()
    conn = sqlite3.connect(tmp_path / DB_NAME)
    conn.execute("UPDATE subsystem_heartbeat SET started_at_utc='2026-10-25T05:17:11+00:00' WHERE subsystem='deep_audit'")
    conn.commit()
    conn.close()
    restarted = SubsystemTelemetry("deep_audit", tmp_path)  # new process after a restart
    restarted.start()
    restarted.declare_cycle(86400, initial_delay_seconds=60)
    live = restarted.liveness()
    assert live["state"] == "DEGRADED" and live["reason"] == "never_completed_a_cycle"


def test_liveness_live_then_consecutive_unknown_degrades(tmp_path, lessons):
    t = SubsystemTelemetry("attack_monitor", tmp_path)
    t.declare_cycle(300, 120)
    t.cycle_completed("a", "SUCCESS", asked=1, verified=1)
    assert t.liveness()["state"] == "LIVE"
    for i in range(3):
        t.cycle_completed(f"u{i}", "UNKNOWN")
    live = t.liveness()
    assert live["state"] == "DEGRADED" and live["reason"] == "consecutive_non_success_cycles"


def test_ready_checks_report_degraded_monitor(tmp_path, lessons, monkeypatch):
    from fastapi.testclient import TestClient

    from scp.api_server import app

    t = SubsystemTelemetry("attack_monitor", tmp_path)
    t.declare_cycle(10, 0)
    real = time.time
    monkeypatch.setattr(st.time, "time", lambda: real() + 100)
    body = TestClient(app).get("/ready").json()  # real route, lifespan not started
    assert body["checks"]["monitors"] == "degraded"
    assert body["checks"]["monitor_attack_monitor"] == "degraded:never_completed_a_cycle"


def test_subsystem_status_route_prefers_live_monitor_with_liveness(tmp_path, lessons, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from scp.api._shared import verify_admin
    from scp.api.routes.v105_routes import router

    monkeypatch.setenv("SCP_DATA_DIR", str(tmp_path))
    live = SubsystemTelemetry("attack_monitor", tmp_path)
    live.declare_cycle(300, 120)
    live.cycle_completed("r1", "SUCCESS", asked=1, verified=1)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[verify_admin] = lambda: None
    body = TestClient(app).get("/v105/runtime/subsystems").json()
    assert body["attack_monitor"]["instance_id"] == live.instance_id
    assert body["attack_monitor"]["liveness"]["state"] == "LIVE"
    assert body["deep_audit"]["liveness"]["state"] == "NOT_DECLARED"


# --------------------------------------------------------------------------- governance_kill


class _Engine:
    def __init__(self) -> None:
        self.in_attack_mode = False
        self.calls: list[bool] = []

    def set_attack_mode(self, value: bool) -> None:
        self.calls.append(value)
        self.in_attack_mode = value


def _last_ledger(tmp_path: Path, subsystem: str) -> dict:
    lines = (tmp_path / f"{subsystem}_runs.jsonl").read_text(encoding="utf-8").splitlines()
    return json.loads(lines[-1])


def test_attack_monitor_without_signal_source_is_unknown_not_zero(tmp_path, lessons, monkeypatch):
    from scp.core.attack_mode_cycle import run_attack_monitor_cycle
    from scp.runtime import notifications as nmod

    monkeypatch.setattr(nmod, "_EVENT_SOURCES", {})
    notif = nmod.UserNotificationSystem()
    t = SubsystemTelemetry("attack_monitor", tmp_path)
    engine = _Engine()
    sig = run_attack_monitor_cycle(t, "r1", notif, engine)
    assert sig["state"] == "NO_SOURCE" and sig["count"] is None
    row = _last_ledger(tmp_path, "attack_monitor")
    assert row["status"] == "UNKNOWN" and row["signal_state"] == "NO_SOURCE"
    assert "asked" not in row, "placebo count must not be recorded"
    assert engine.calls == []
    assert [r["error_type"] for r in lessons.records] == ["heartbeat_monitor_signal_no_source"]


def test_attack_monitor_counts_when_real_source_registered(tmp_path, lessons, monkeypatch):
    from scp.core.attack_mode_cycle import run_attack_monitor_cycle
    from scp.runtime import notifications as nmod

    monkeypatch.setattr(nmod, "_EVENT_SOURCES", {})
    nmod.register_event_source("governance_kill", "test.producer")
    notif = nmod.UserNotificationSystem()
    now = time.time()
    with notif._recent_lock:
        for i in range(25):
            notif._recent.append({"timestamp": now - i, "event_type": "governance_kill"})
    t = SubsystemTelemetry("attack_monitor", tmp_path)
    engine = _Engine()
    sig = run_attack_monitor_cycle(t, "r1", notif, engine)
    assert sig == {"state": "COUNTED", "count": 25, "sources": ["test.producer"]}
    assert engine.calls == [True]
    row = _last_ledger(tmp_path, "attack_monitor")
    assert row["status"] == "SUCCESS" and row["asked"] == 25


# --------------------------------------------------------------------------- kernel guard (root cause)


def _write_owner(tmp_path: Path, kernel: str, age_s: float) -> None:
    (tmp_path / f"{DB_NAME}{OWNER_SUFFIX}").write_text(
        json.dumps({"kernel": kernel, "pid": 1, "claimed_at": time.time() - age_s}), encoding="utf-8")


def test_foreign_kernel_with_fresh_claim_never_touches_shared_file(tmp_path, lessons):
    _write_owner(tmp_path, "linux:other-vm-boot-id", age_s=5)
    t = SubsystemTelemetry("fast_learning", tmp_path)
    t.start()
    t.tick(status="IDLE")
    assert not (tmp_path / DB_NAME).exists(), "shared WAL file must not be opened from a foreign kernel"
    assert t.active_db_path != tmp_path / DB_NAME and t.active_db_path.exists()
    assert t.liveness()["state"] == "NOT_DECLARED"
    assert t._guard.state == "FOREIGN_KERNEL_FALLBACK"
    assert [r["error_type"] for r in lessons.records] == ["heartbeat_foreign_kernel_fallback"]


def test_stale_foreign_claim_is_taken_over_and_same_kernel_uses_primary(tmp_path, lessons, monkeypatch):
    monkeypatch.setenv("SCP_HEARTBEAT_OWNER_TTL_S", "60")
    _write_owner(tmp_path, "linux:other-vm-boot-id", age_s=3600)
    t = SubsystemTelemetry("fast_learning", tmp_path)
    t.tick(status="IDLE")
    assert t.active_db_path == tmp_path / DB_NAME
    owner = json.loads((tmp_path / f"{DB_NAME}{OWNER_SUFFIX}").read_text(encoding="utf-8"))
    assert owner["kernel"] == hb.kernel_identity() and owner["pid"] == os.getpid()
    assert [r["error_type"] for r in lessons.records] == ["heartbeat_foreign_claim_stale_takeover"]
