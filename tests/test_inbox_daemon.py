"""
Unit, Contract & Integration Tests for Knowledge Inbox Daemon.
==============================================================
Kiểm chứng toàn diện scp/knowledge/inbox_daemon.py:
  1. Quản lý trạng thái: is_running, last_run_timestamp, processed_count, error_count.
  2. run_once(): Quét và nạp tri thức hợp lệ vào LearningDB, tăng processed_count.
  3. run_once(): Bắt lỗi fail-closed khi gặp tệp vi phạm/hỏng, tăng error_count.
  4. Vòng lặp nền background thread: start() và stop() dừng an toàn, không rò rỉ thread.
  5. Singleton helper functions: start_inbox_daemon(), get_inbox_daemon(), stop_inbox_daemon().
  6. Khả năng phục hồi (fault tolerance): ngoại lệ không làm sập luồng nền.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time

import pytest

from scp.knowledge.inbox_daemon import (
    InboxDaemon,
    get_inbox_daemon,
    start_inbox_daemon,
    stop_inbox_daemon,
)
from scp.knowledge.inbox_watcher import InboxWatcher
from scp.knowledge.learning_db import LearningDB


@pytest.fixture
def daemon_env(tmp_path):
    inbox_dir = tmp_path / "inbox"
    db_path = tmp_path / "learning.sqlite"
    inbox_dir.mkdir(parents=True, exist_ok=True)
    db = LearningDB(db_path)
    watcher = InboxWatcher(
        inbox_dir=inbox_dir,
        db_path=db_path,
        learning_db=db,
        archive=True,
    )
    daemon = InboxDaemon(
        inbox_dir=inbox_dir,
        db_path=db_path,
        interval_seconds=0.05,
        watcher=watcher,
    )
    yield {
        "daemon": daemon,
        "inbox_dir": inbox_dir,
        "db_path": db_path,
        "db": db,
        "watcher": watcher,
        "processed_dir": inbox_dir / "processed",
        "quarantine_dir": inbox_dir / "quarantine",
    }
    daemon.stop()
    stop_inbox_daemon()


def test_inbox_daemon_initial_state(daemon_env):
    """Trạng thái ban đầu của daemon phải đúng chuẩn."""
    daemon = daemon_env["daemon"]
    assert daemon.is_running is False
    assert daemon.last_run_timestamp is None
    assert daemon.processed_count == 0
    assert daemon.error_count == 0

    status = daemon.get_status()
    assert status["is_running"] is False
    assert status["processed_count"] == 0
    assert status["error_count"] == 0


def test_inbox_daemon_run_once_valid_knowledge(daemon_env):
    """run_once xử lý tệp tri thức sạch và lưu bền vững vào SQLite."""
    daemon = daemon_env["daemon"]
    inbox_dir = daemon_env["inbox_dir"]
    db_path = daemon_env["db_path"]

    # Tạo tệp tri thức hợp lệ
    file_path = inbox_dir / "fact_astronomy.json"
    file_path.write_text(
        json.dumps({
            "item_id": "astro_01",
            "domain": "astronomy",
            "question": "Mặt trời cách Trái Đất bao xa?",
            "text": "Khoảng cách từ Mặt trời đến Trái Đất là khoảng 149.6 triệu km (1 AU).",
        }),
        encoding="utf-8",
    )

    results = daemon.run_once()
    assert len(results) == 1
    assert results[0].status == "ACCEPTED"
    assert daemon.processed_count == 1
    assert daemon.error_count == 0
    assert daemon.last_run_timestamp is not None

    # Kiểm tra bản ghi trong LearningDB
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT hypothesis_id, question_ref, hypothesis FROM hypotheses")
        rows = cursor.fetchall()
        assert len(rows) == 1
        assert rows[0][1] == "astro_01"


def test_inbox_daemon_run_once_corrupted_file_fail_closed(daemon_env):
    """run_once xử lý tệp hỏng, chuyển quarantine và tăng error_count."""
    daemon = daemon_env["daemon"]
    inbox_dir = daemon_env["inbox_dir"]
    db_path = daemon_env["db_path"]

    # Tạo tệp JSON sai cú pháp
    bad_file = inbox_dir / "broken.json"
    bad_file.write_text("{invalid json format: missing brackets", encoding="utf-8")

    results = daemon.run_once()
    assert len(results) == 1
    assert results[0].status == "CORRUPTED"
    assert daemon.error_count == 1
    assert daemon.processed_count == 0

    # Kiểm tra open_questions đã được tạo trong SQLite
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT question_id, trigger FROM open_questions")
        rows = cursor.fetchall()
        assert len(rows) == 1


def test_inbox_daemon_background_loop_lifecycle(daemon_env):
    """Kiểm tra vòng lặp ngầm: start() -> xử lý file tự động -> stop()."""
    daemon = daemon_env["daemon"]
    inbox_dir = daemon_env["inbox_dir"]

    daemon.start()
    assert daemon.is_running is True

    # Gọi start lần 2 không gây lỗi
    daemon.start()
    assert daemon.is_running is True

    # Thêm 1 file vào thư mục inbox
    test_file = inbox_dir / "quick_fact.json"
    test_file.write_text(
        json.dumps({
            "item_id": "quick_01",
            "domain": "geography",
            "text": "Đỉnh Everest cao 8848 mét so với mực nước biển.",
        }),
        encoding="utf-8",
    )

    # Đợi vòng lặp ngầm quét xong (tối đa 2 giây)
    start_wait = time.time()
    while daemon.processed_count == 0 and time.time() - start_wait < 2.0:
        time.sleep(0.05)

    assert daemon.processed_count >= 1

    # Dừng daemon
    daemon.stop()
    assert daemon.is_running is False
    assert daemon._thread is None


def test_inbox_daemon_singleton_lifecycle(tmp_path):
    """Kiểm tra module-level singleton start_inbox_daemon / stop_inbox_daemon."""
    inbox = tmp_path / "singleton_inbox"
    db = tmp_path / "singleton_db.sqlite"
    inbox.mkdir(parents=True, exist_ok=True)

    d1 = start_inbox_daemon(interval_seconds=0.1, inbox_dir=inbox, db_path=db)
    assert d1.is_running is True
    assert get_inbox_daemon() is d1

    # Gọi lại start_inbox_daemon trả về cùng singleton
    d2 = start_inbox_daemon()
    assert d2 is d1

    stop_inbox_daemon()
    assert get_inbox_daemon() is None
    assert d1.is_running is False


def test_inbox_daemon_run_once_no_claims_tracked_as_error(daemon_env):
    """File rỗng hoặc không có khẳng định (NO_CLAIMS) phải được tính vào error_count vì bị cách ly."""
    daemon = daemon_env["daemon"]
    inbox_dir = daemon_env["inbox_dir"]

    empty_file = inbox_dir / "empty_notes.txt"
    empty_file.write_text("Chỉ là ghi chú chung chung, không có số liệu hay khẳng định nào.", encoding="utf-8")

    results = daemon.run_once()
    assert len(results) == 1
    assert results[0].status == "NO_CLAIMS"
    assert daemon.error_count == 1
    assert daemon.processed_count == 0


def test_inbox_daemon_loop_resilience_on_uncaught_exception(daemon_env, monkeypatch):
    """Nếu run_once văng ngoại lệ nghiêm trọng, finally trong _loop vẫn bảo đảm is_running được hạ xuống False."""
    daemon = daemon_env["daemon"]

    def crashing_run_once():
        daemon._stop_event.set()
        raise RuntimeError("Severe simulated crash in worker loop")

    monkeypatch.setattr(daemon, "run_once", crashing_run_once)

    daemon.start()
    daemon._thread.join(timeout=2.0)
    assert daemon.is_running is False


def test_cleanup_archive_removes_old_files_preserves_fresh(daemon_env):
    """cleanup_archive xóa tệp cũ hơn retention_days và giữ nguyên tệp mới."""
    daemon = daemon_env["daemon"]
    inbox_dir = daemon_env["inbox_dir"]

    archive_dir = inbox_dir / "archive"
    processed_dir = inbox_dir / "processed"
    archive_dir.mkdir(parents=True, exist_ok=True)
    processed_dir.mkdir(parents=True, exist_ok=True)

    now = time.time()
    day_s = 86400

    # 1. Tệp trong archive/ đã cũ (35 ngày)
    old_archive_file = archive_dir / "old_fact.json"
    old_archive_file.write_text('{"item": "old"}', encoding="utf-8")
    t_35d_ago = now - (35 * day_s)
    os.utime(old_archive_file, (t_35d_ago, t_35d_ago))

    # 2. Tệp trong archive/ còn mới (5 ngày)
    fresh_archive_file = archive_dir / "fresh_fact.json"
    fresh_archive_file.write_text('{"item": "fresh"}', encoding="utf-8")
    t_5d_ago = now - (5 * day_s)
    os.utime(fresh_archive_file, (t_5d_ago, t_5d_ago))

    # 3. Tệp trong processed/ đã cũ (40 ngày)
    old_processed_file = processed_dir / "old_processed.json"
    old_processed_file.write_text('{"item": "old_proc"}', encoding="utf-8")
    t_40d_ago = now - (40 * day_s)
    os.utime(old_processed_file, (t_40d_ago, t_40d_ago))

    # 4. Tệp ẩn .gitkeep trong archive (60 ngày) - không được xóa
    hidden_file = archive_dir / ".gitkeep"
    hidden_file.write_text("", encoding="utf-8")
    t_60d_ago = now - (60 * day_s)
    os.utime(hidden_file, (t_60d_ago, t_60d_ago))

    deleted_count = daemon.cleanup_archive(retention_days=30)
    assert deleted_count == 2
    assert not old_archive_file.exists()
    assert not old_processed_file.exists()
    assert fresh_archive_file.exists()
    assert hidden_file.exists()


def test_cleanup_archive_respects_env_retention_days(tmp_path, monkeypatch):
    """InboxDaemon nạp retention_days từ biến môi trường SCP_INBOX_RETENTION_DAYS."""
    monkeypatch.setenv("SCP_INBOX_RETENTION_DAYS", "10")
    inbox = tmp_path / "env_inbox"
    archive = inbox / "archive"
    archive.mkdir(parents=True, exist_ok=True)

    d = InboxDaemon(inbox_dir=inbox, db_path=tmp_path / "test.db")
    assert d.retention_days == 10
    assert d.get_status()["retention_days"] == 10

    # Tạo tệp 15 ngày tuổi
    target_file = archive / "expired.json"
    target_file.write_text('{"val": 1}', encoding="utf-8")
    t_15d_ago = time.time() - (15 * 86400)
    os.utime(target_file, (t_15d_ago, t_15d_ago))

    deleted = d.cleanup_archive(d.retention_days)
    assert deleted == 1
    assert not target_file.exists()


def test_cleanup_archive_nonexistent_directory_safe(daemon_env):
    """Khi thư mục archive chưa tồn tại, cleanup_archive không gây crash và trả về 0."""
    daemon = daemon_env["daemon"]
    archive_dir = daemon.inbox_dir / "archive"
    if archive_dir.exists():
        import shutil
        shutil.rmtree(archive_dir)

    res = daemon.cleanup_archive(retention_days=30)
    assert res == 0


def test_cleanup_archive_uses_instance_retention_days_by_default(tmp_path):
    """Gọi cleanup_archive() không truyền tham số phải tôn trọng self.retention_days đã cấu hình."""
    inbox = tmp_path / "custom_retention_inbox"
    archive = inbox / "archive"
    archive.mkdir(parents=True, exist_ok=True)

    # Khởi tạo với retention_days = 7 ngày
    daemon = InboxDaemon(inbox_dir=inbox, db_path=tmp_path / "test.db", retention_days=7)
    assert daemon.retention_days == 7

    now = time.time()
    day_s = 86400

    # Tệp 10 ngày tuổi (> 7 ngày) -> phải bị xóa
    old_file = archive / "expired_7d.json"
    old_file.write_text('{"v": 1}', encoding="utf-8")
    t_10d = now - (10 * day_s)
    os.utime(old_file, (t_10d, t_10d))

    # Tệp 3 ngày tuổi (< 7 ngày) -> phải được giữ lại
    fresh_file = archive / "keep_7d.json"
    fresh_file.write_text('{"v": 2}', encoding="utf-8")
    t_3d = now - (3 * day_s)
    os.utime(fresh_file, (t_3d, t_3d))

    # Gọi không truyền đối số
    deleted = daemon.cleanup_archive()
    assert deleted == 1
    assert not old_file.exists()
    assert fresh_file.exists()


def test_cleanup_archive_handles_directory_scan_exception_gracefully(tmp_path, monkeypatch):
    """Nếu iterdir văng lỗi (ví dụ PermissionError), cleanup_archive bắt lỗi an toàn và không crash."""
    inbox = tmp_path / "unreadable_inbox"
    archive = inbox / "archive"
    archive.mkdir(parents=True, exist_ok=True)

    daemon = InboxDaemon(inbox_dir=inbox, db_path=tmp_path / "test.db")

    from pathlib import Path
    orig_iterdir = Path.iterdir

    def crashing_iterdir(self_path):
        if "archive" in str(self_path):
            raise PermissionError("Access denied on simulated locked directory")
        return orig_iterdir(self_path)

    monkeypatch.setattr(Path, "iterdir", crashing_iterdir)

    # Không được ném ngoại lệ ra ngoài
    deleted = daemon.cleanup_archive(retention_days=30)
    assert deleted == 0


def test_inbox_daemon_loop_resilience_continues_after_cycle_error(tmp_path, monkeypatch):
    """Khi một chu kỳ trong _loop gặp lỗi bất ngờ, daemon ghi nhận error_count và tiếp tục chạy chu kỳ tiếp theo."""
    inbox = tmp_path / "resilient_inbox"
    inbox.mkdir(parents=True, exist_ok=True)

    daemon = InboxDaemon(inbox_dir=inbox, db_path=tmp_path / "test.db", interval_seconds=0.02)

    call_count = 0
    second_cycle_reached = False

    def flaky_run_once():
        nonlocal call_count, second_cycle_reached
        call_count += 1
        if call_count == 1:
            raise RuntimeError("Simulated transient cycle failure")
        second_cycle_reached = True
        daemon._stop_event.set()
        return []

    monkeypatch.setattr(daemon, "run_once", flaky_run_once)

    daemon.start()
    daemon._thread.join(timeout=2.0)

    # Chu kỳ thứ hai vẫn được gọi thành công và error_count được ghi nhận
    assert second_cycle_reached is True
    assert daemon.error_count >= 1
    assert daemon.is_running is False


