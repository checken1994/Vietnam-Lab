# [esc-auto slice 1 · 2026-10-30] Dead-man switch có giới hạn — giết DoS thread/state.
#
# Trước fix (claude/c1-escalation 3a9f5b83): mỗi threat high/critical tạo MỘT
# threading.Timer (1 OS thread sống 30 phút); threat_id = hash nội dung ⇒ N payload
# khác nhau = N thread + N entry `_active` + history không giới hạn (persist mỗi lần).
#
# Pin ở đây (owner: "đã tự động thì ai duyệt cho bạn" ⇒ hết giờ = kết quả AN TOÀN):
#   * lũ threat khác nhau ⇒ số thread tăng tối đa 1 (một worker), state có trần;
#   * đầy ⇒ entry có deadline sớm nhất bị BẮN SỚM (chạy safe outcome), không vứt im lặng;
#   * lặp lại cùng threat KHÔNG đẩy lùi deadline (deadline = min);
#   * restart với state cũ phình to ⇒ khôi phục trong trần, không spawn thread theo entry.
#
# Zero-mock: EscalationManager thật, state/log file thật trên tmp_path.
from __future__ import annotations

import json
import threading
import time

import pytest

from scp.security.escalation import EscalationManager


def _wait_until(predicate, timeout: float = 5.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


@pytest.fixture
def manager_factory(tmp_path):
    created: list[EscalationManager] = []

    def _make(**kwargs) -> EscalationManager:
        manager = EscalationManager(data_dir=str(tmp_path), **kwargs)
        created.append(manager)
        return manager

    yield _make
    for manager in created:
        manager.shutdown()


def _log_rows(tmp_path) -> list[dict]:
    path = tmp_path / "escalation_log.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_flood_of_distinct_critical_threats_keeps_threads_and_state_bounded(manager_factory, tmp_path):
    manager = manager_factory()
    threads_before = threading.active_count()
    for i in range(300):
        manager.on_threat_detected({"id": f"flood-{i}", "type": "probe", "severity": "critical"})
    grown = threading.active_count() - threads_before
    assert grown <= 1, f"each threat must not spawn its own OS thread (grew by {grown})"
    status = manager.escalation_status()
    assert status["active_count"] <= manager.max_active
    assert status["timers_count"] <= manager.max_active
    persisted = json.loads((tmp_path / "escalation_state.json").read_text(encoding="utf-8"))
    assert len(persisted["active"]) <= manager.max_active
    assert len(persisted["history"]) <= manager.history_cap
    # Không vứt im lặng: 300 threat đều được đếm, phần tràn đi qua safe outcome.
    counters = status["counters"]
    assert counters["threats_seen_total"] == 300
    assert counters["evicted_total"] == 300 - manager.max_active


def test_capacity_overflow_fires_earliest_safe_outcome_never_drops(manager_factory, tmp_path):
    manager = manager_factory(max_active=4)
    for i in range(6):
        manager.on_threat_detected({"id": f"cap-{i}", "type": "probe", "severity": "critical"})
    active_ids = {entry["threat_id"] for entry in manager.get_active_escalations()}
    assert active_ids == {"cap-2", "cap-3", "cap-4", "cap-5"}
    history = manager.get_escalation_history()
    evicted = [h for h in history if h.get("cause") == "capacity_evicted"]
    assert {h["threat_id"] for h in evicted} == {"cap-0", "cap-1"}
    assert all(h["action"] == "timeout" for h in evicted), "eviction must run the SAFE outcome, not approve"
    # Safe outcome = defensive playbook ⇒ forensic log thật cho từng threat bị bắn sớm.
    forensic = [r for r in _log_rows(tmp_path) if r.get("why") == "forensic_logging"]
    assert len(forensic) >= 2
    assert manager.escalation_status()["counters"]["evicted_total"] == 2


def test_repeating_same_threat_cannot_postpone_safe_outcome(manager_factory):
    manager = manager_factory()
    threat = {"id": "repeat-1", "type": "probe", "severity": "critical"}
    manager.on_threat_detected(threat)
    first_deadline = manager.get_active_escalations()[0]["deadline_at"]
    time.sleep(0.05)
    manager.on_threat_detected(threat)
    manager.start_countdown(threat, timeout_min=manager.DEFAULT_TIMEOUT_MIN)
    entry = manager.get_active_escalations()[0]
    assert entry["deadline_at"] == first_deadline, "re-detection must not extend the dead-man deadline"
    assert entry["seen_count"] >= 2
    assert manager.escalation_status()["active_count"] == 1


def test_deadline_runs_safe_outcome_on_single_worker(manager_factory, tmp_path):
    manager = manager_factory(timeout_seconds=0.2)
    threads_before = threading.active_count()
    for i in range(20):
        manager.on_threat_detected({"id": f"due-{i}", "type": "probe", "severity": "high"})
    assert threading.active_count() - threads_before <= 1
    assert _wait_until(lambda: manager.escalation_status()["active_count"] == 0)
    history = manager.get_escalation_history()
    assert len([h for h in history if h["action"] == "timeout" and h.get("cause") == "deadline"]) == 20
    assert manager.escalation_status()["timers_count"] == 0


def test_restart_with_oversized_state_restores_within_cap_without_thread_per_entry(tmp_path):
    now = time.time()
    active = {
        f"old-{i}": {
            "threat_id": f"old-{i}",
            "threat": {"id": f"old-{i}", "severity": "critical"},
            "severity": "critical",
            "status": "armed",
            "armed_at": now,
            "deadline_at": now + 600 + i,
        }
        for i in range(80)
    }
    (tmp_path / "escalation_state.json").write_text(
        json.dumps({"schema_version": 1, "active": active, "history": [{"threat_id": "h", "action": "x"}] * 5000}),
        encoding="utf-8",
    )
    threads_before = threading.active_count()
    manager = EscalationManager(data_dir=str(tmp_path), max_active=8)
    try:
        assert threading.active_count() - threads_before <= 1
        status = manager.escalation_status()
        assert status["active_count"] <= 8
        assert status["history_count"] <= manager.history_cap
        # Phần vượt trần đi qua safe outcome (timeout), không biến mất im lặng.
        evicted = [h for h in manager.get_escalation_history() if h.get("cause") == "capacity_evicted"]
        assert len(evicted) == 80 - 8
    finally:
        manager.shutdown()


def test_scheduler_unit_contract_dedup_min_cancel_and_early_fire():
    from scp.security.deadline_scheduler import BoundedDeadlineScheduler

    fired: list[tuple[str, str]] = []
    scheduler = BoundedDeadlineScheduler(lambda key, cause: fired.append((key, cause)), max_pending=2)
    try:
        far = time.time() + 600
        assert scheduler.schedule("a", far) == far
        assert scheduler.schedule("a", far + 100) == far, "later re-schedule must keep the earlier deadline"
        assert scheduler.schedule("a", far - 100) == far - 100, "earlier re-schedule tightens the deadline"
        scheduler.schedule("b", far + 5)
        scheduler.schedule("c", far + 9)  # full ⇒ earliest ("a") fires early
        assert fired == [("a", "capacity_evicted")]
        assert scheduler.cancel("b") is True
        assert scheduler.pending_keys() == ["c"]
        scheduler.schedule("d", time.time() - 1)
        assert _wait_until(lambda: ("d", "deadline") in fired)
        stats = scheduler.stats()
        assert stats["worker_threads"] == 1
        assert stats["evicted_fired_early"] == 1
        assert stats["deduped"] == 2
    finally:
        scheduler.shutdown()
