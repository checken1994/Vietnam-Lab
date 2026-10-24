# [HC-39 · 2026-10-24] BackgroundJobRegistry migration contract cho
# doubt_cron + InboxDaemon (trước đây là thread daemon NGOÀI registry).
#
# Contract:
#   - DoubtCron.run_once() là một vòng doubt hoàn chỉnh, có thể bọc vào
#     BackgroundJob mà không đổi logic.
#   - start_inbox_daemon(autostart=False) tạo daemon KHÔNG mở thread riêng
#     ("InboxDaemonWorker"); run_once()+cleanup_archive() bọc được vào job.
#   - Registry thấy job trong status(); stop_all() dừng sạch (không rò thread).
#   - Mọi dữ liệu ghi vào tmp_path — KHÔNG đụng data/ thật.
from __future__ import annotations

import threading
import time

from scp.api.background_jobs import BackgroundJob, BackgroundJobRegistry
from scp.core.doubt_cron import DoubtCron
from scp.knowledge.inbox_daemon import InboxDaemon


def _registry_with(fn, *, name: str, interval: float = 3600.0) -> tuple[BackgroundJobRegistry, BackgroundJob]:
    reg = BackgroundJobRegistry()  # registry CỤC BỘ test — không ôm registry global
    job = BackgroundJob(
        name=name, fn=fn, interval_seconds=interval, required=False, initial_delay_seconds=0.0,
    )
    reg.add(job)
    return reg, job


def test_doubt_cron_run_once_wraps_into_registry(tmp_path) -> None:
    """run_once() chạy qua BackgroundJobRegistry và registry thấy job."""
    cron = DoubtCron(data_dir=str(tmp_path), interval_seconds=3600.0)
    calls = {"n": 0}
    real_run_once = cron.run_once

    def counting_run_once():
        calls["n"] += 1
        return real_run_once()

    reg, job = _registry_with(counting_run_once, name="doubt_cron_test")
    reg.start_all()
    try:
        assert job._started is True
        # initial_delay=0 → vòng đầu chạy ngay trong thread job
        deadline = time.monotonic() + 10.0
        while calls["n"] == 0 and time.monotonic() < deadline:
            time.sleep(0.05)
        assert calls["n"] >= 1, "BackgroundJob phải thực sự chạy run_once (không chỉ schedule)"
        status = reg.status()
        assert status["doubt_cron_test"]["started"] is True
        assert status["doubt_cron_test"]["error_count"] == 0
    finally:
        reg.stop_all(timeout=5.0)
    assert job._started is False
    assert not job._thread.is_alive(), "stop_all phải dừng thread registry job sạch"


def test_inbox_daemon_autostart_false_opens_no_worker_thread(tmp_path) -> None:
    """autostart=False → không có thread 'InboxDaemonWorker' tự quản."""
    daemon = InboxDaemon(inbox_dir=tmp_path / "inbox", db_path=tmp_path / "inbox.sqlite3")
    worker_before = [t for t in threading.enumerate() if t.name == "InboxDaemonWorker"]
    assert daemon._thread is None
    assert not daemon._is_running
    assert worker_before == []
    # tick qua registry seam: run_once + cleanup chạy trực tiếp, không thread
    daemon.run_once()
    daemon.cleanup_archive(daemon.retention_days)
    status = daemon.get_status()
    assert status["error_count"] >= 0


def test_inbox_daemon_tick_wraps_into_registry_and_stops_clean(tmp_path) -> None:
    daemon = InboxDaemon(inbox_dir=tmp_path / "inbox", db_path=tmp_path / "inbox.sqlite3")
    calls = {"n": 0}

    def tick():
        calls["n"] += 1
        daemon.run_once()
        daemon.cleanup_archive(daemon.retention_days)

    reg, job = _registry_with(tick, name="inbox_daemon_test")
    reg.start_all()
    try:
        deadline = time.monotonic() + 10.0
        while calls["n"] == 0 and time.monotonic() < deadline:
            time.sleep(0.05)
        assert calls["n"] >= 1
        assert reg.status()["inbox_daemon_test"]["started"] is True
    finally:
        reg.stop_all(timeout=5.0)
    assert not job._thread.is_alive()
