# [HC-39 · 2026-10-24] Wave B2-W1 — migration còn lại của BackgroundJobRegistry.
#
# Phạm vi wave này (không đụng 4 job đã migrate bởi B1):
#   - why_verify          (trước đây thread "scp-why-verify-scheduler")
#   - retry_policy        (trước đây thread "scp-retry-policy" chạy run_background)
#   - deep_audit_heartbeat (trước đây thread "scp-deep-audit-heartbeat")
#   - attack_monitor_heartbeat (trước đây thread "scp-attack-monitor-heartbeat")
#
# Contract mỗi job migrate:
#   1. lifespan boot đăng ký job vào registry (status() thấy được);
#   2. job thread THẬT SỰ chạy ≥1 vòng khi initial_delay cho phép (bằng chứng:
#      log "first execution completed" của registry — chỉ emit SAU khi fn()
#      thành công, MACH1-FIX-1);
#   3. shutdown: registry.stop_all() dừng sạch (thread chết, không rò);
#   4. boot/shutdown TestClient không treo (gate F-14 — giới hạn 30s).
#
# F-14 (scheduler offloop stop-event) KHÔNG bị đụng — các test gate hiện có
# (test_flow_01/02, cs2) phải giữ xanh; test này chỉ thêm bằng chứng.
from __future__ import annotations

import logging
import os
import time

os.environ.setdefault(
    "SCP_JWT_SECRET", "test-only-jwt-secret-0123456789abcdef-not-for-production"
)
os.environ.setdefault("SCP_ADMIN_KEY", "test-only-admin-key-0123456789abcdef")

import pytest
from fastapi.testclient import TestClient

from scp.api.background_jobs import registry
from scp.api_server import app
from scp.policy.retry_policy import RetryPolicy

# (name, interval_seconds, initial_delay_seconds) theo cadence giữ nguyên từ
# pre-migration: why-verify R5 (warm-up 180s + 5min), retry 60s poll (đợi 1
# interval trước poll đầu), 2 heartbeat 15s tick-ngay.
EXPECTED_MIGRATED_JOBS = {
    "why_verify": (300.0, 180.0),
    "retry_policy": (60.0, 60.0),
    "deep_audit_heartbeat": (15.0, 0.0),
    "attack_monitor_heartbeat": (15.0, 0.0),
}


def test_lifespan_registers_all_b2w1_migrated_jobs():
    """Boot thật: 4 job migrate phải nằm trong registry với cadence đúng."""
    with TestClient(app) as client:
        resp = client.get("/health")
        assert resp.status_code == 200
        for name, (interval, initial_delay) in EXPECTED_MIGRATED_JOBS.items():
            job = registry._jobs.get(name)
            assert job is not None, f"{name} chưa được đăng ký vào registry lúc boot"
            assert job.interval_seconds == interval, (
                f"{name}: interval đổi sau migration ({job.interval_seconds} != {interval})"
            )
            assert job.initial_delay_seconds == initial_delay, (
                f"{name}: initial delay đổi sau migration ({job.initial_delay_seconds} != {initial_delay})"
            )
            assert job._started is True, f"{name} phải được start_all() khởi động lúc boot"
            assert job._thread is not None and job._thread.is_alive(), (
                f"{name}: thread registry job phải sống trong uptime"
            )


def test_heartbeat_jobs_really_execute_at_least_one_cycle(caplog):
    """Bằng chứng ≥1 vòng chạy THẬT cho job initial_delay=0 (2 heartbeat).

    [MACH1-FIX-1] log 'first execution completed' CHỈ được emit sau khi fn()
    chạy thành công — đây là bằng chứng execution, không phải schedule.
    """
    caplog.set_level(logging.INFO, logger="scp.api.background_jobs")
    with TestClient(app) as client:
        resp = client.get("/health")
        assert resp.status_code == 200
        deadline = time.monotonic() + 20.0
        names_seen: set[str] = set()
        while time.monotonic() < deadline and names_seen != {
            "deep_audit_heartbeat",
            "attack_monitor_heartbeat",
        }:
            for rec in caplog.records:
                msg = rec.getMessage()
                if "first execution completed" in msg:
                    for name in ("deep_audit_heartbeat", "attack_monitor_heartbeat"):
                        if name in msg:
                            names_seen.add(name)
            time.sleep(0.1)
        assert names_seen == {"deep_audit_heartbeat", "attack_monitor_heartbeat"}, (
            f"heartbeat jobs chưa chứng minh chạy ≥1 vòng: {names_seen}"
        )


def test_retry_policy_poll_once_is_a_complete_cycle():
    """poll_once() — thân vòng lặp tách từ run_background — phải là 1 chu kỳ
    hoàn chỉnh: observe plans rồi check_and_retry với fns thật (không mock
    logic)."""
    calls = {"observe": [], "retried": []}

    def _get_plans():
        return [{"planId": "task-1", "state": "RETRY_SCHEDULED"}]

    def _retry_fn(plan_id, reason):
        calls["retried"].append((plan_id, reason))

    policy = RetryPolicy(get_plans_fn=_get_plans, retry_fn=_retry_fn)
    original_check = policy.check_and_retry

    def counting_check():
        calls["observe"].append(len(policy._waiting_since))
        return original_check()

    policy.check_and_retry = counting_check  # type: ignore[method-assign]
    # RETRY_TIMEOUT_SEC default 300s — lần poll đầu CHỈ observe, chưa retry.
    policy.poll_once()
    assert calls["observe"] == [1], "poll_once phải observe plans trước khi check"
    assert calls["retried"] == [], "plan mới observe chưa hết hạn → không retry"
    assert "task-1" in policy._waiting_since

    # Giả lập hết hạn bằng cách lùi mốc waiting_since quá RETRY_TIMEOUT_SEC.
    import scp.policy.retry_policy as rp_mod

    policy._waiting_since["task-1"] -= rp_mod.RETRY_TIMEOUT_SEC + 1.0
    policy.poll_once()
    assert calls["retried"] == [("task-1", "")], (
        "poll_once sau khi hết hạn phải gọi retry_fn (plan kernel-flat, step '')"
    )


def test_run_background_still_loops_for_standalone_callers():
    """run_background() giữ contract cũ (poll loop + stop_event) — caller
    standalone/test cũ (flow_01 causal retry test) không bị phá."""
    polls = {"n": 0}

    def _get_plans():
        polls["n"] += 1
        return []

    policy = RetryPolicy(get_plans_fn=_get_plans, retry_fn=lambda p, r: None)
    import threading as _th

    stop = _th.Event()
    worker = _th.Thread(
        target=policy.run_background,
        kwargs={"interval_sec": 0.1, "stop_event": stop},
        daemon=True,
    )
    worker.start()
    try:
        deadline = time.monotonic() + 5.0
        while polls["n"] < 2 and time.monotonic() < deadline:
            time.sleep(0.05)
        assert polls["n"] >= 2, "run_background phải vẫn poll định kỳ"
    finally:
        stop.set()
        worker.join(timeout=2.0)
    assert not worker.is_alive()


def test_boot_shutdown_completes_without_hang_and_all_migrated_jobs_stop():
    """Gate F-14 + HC-39: boot → shutdown phải hoàn tất (giới hạn 30s tổng cho
    phần lifespan) và mọi job migrate phải dừng sạch (thread chết, inner
    stop-event set)."""
    started = time.monotonic()
    with TestClient(app) as client:
        resp = client.get("/health")
        assert resp.status_code == 200
    elapsed = time.monotonic() - started
    assert elapsed < 30.0, (
        f"boot+shutdown mất {elapsed:.1f}s — khả năng treo shutdown (F-14 class)"
    )
    for name in EXPECTED_MIGRATED_JOBS:
        job = registry._jobs.get(name)
        assert job is not None
        assert job._started is False, f"{name}: _started phải False sau shutdown"
        assert job._thread is None or not job._thread.is_alive(), (
            f"{name}: thread phải chết sau registry.stop_all() — rò thread nền"
        )
    # Inner stop-handles cũ vẫn được shutdown set (cơ chế dừng bên trong fn).
    for attr in ("deep_audit_stop", "attack_monitor_stop", "retry_policy_stop", "why_verify_stop"):
        ev = getattr(app.state, attr, None)
        if ev is not None:
            assert ev.is_set(), f"app.state.{attr} phải được set lúc shutdown"
