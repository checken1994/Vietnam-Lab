"""
[OPT-21] InboxDaemon — Background Knowledge Ingestion Loop.
===========================================================
Định kỳ quét thư mục data/inbox_knowledge/ bằng InboxWatcher:
  - Quản lý vòng lặp chạy ngầm không chặn main thread.
  - Quản lý trạng thái: is_running, last_run_timestamp, processed_count, error_count.
  - Fail-closed: Bắt mọi ngoại lệ để vòng lặp nền không bị chết đột ngột.
  - Safe shutdown: Event-driven stop, không rò rỉ thread/task.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

from scp.contracts.time import now_utc_iso
from scp.knowledge.inbox_watcher import InboxWatcher

logger = logging.getLogger("scp.knowledge.inbox_daemon")


class InboxDaemon:
    """Daemon quản lý vòng lặp nạp tri thức định kỳ từ inbox_knowledge."""

    def __init__(
        self,
        inbox_dir: Path | str | None = None,
        db_path: Path | str | None = None,
        interval_seconds: float = 30.0,
        watcher: InboxWatcher | None = None,
        retention_days: int | None = None,
    ) -> None:
        self.interval_seconds = max(0.01, float(interval_seconds))
        if inbox_dir is not None:
            self.inbox_dir = Path(inbox_dir).resolve()
        else:
            self.inbox_dir = Path(__file__).resolve().parents[2] / "data" / "inbox_knowledge"

        if db_path is not None:
            self.db_path = Path(db_path).resolve()
        else:
            self.db_path = Path(__file__).resolve().parents[2] / "data" / "cognitive" / "learning.sqlite"

        if watcher is not None:
            self.watcher = watcher
        else:
            self.watcher = InboxWatcher(inbox_dir=self.inbox_dir, db_path=self.db_path)

        if retention_days is not None:
            self.retention_days = max(0, int(retention_days))
        else:
            try:
                self.retention_days = max(0, int(os.environ.get("SCP_INBOX_RETENTION_DAYS", "30")))
            except (ValueError, TypeError):
                self.retention_days = 30

        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._is_running = False
        self._last_run_timestamp: str | None = None
        self._processed_count: int = 0
        self._error_count: int = 0
        self._lock = threading.Lock()

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._is_running

    @property
    def last_run_timestamp(self) -> str | None:
        with self._lock:
            return self._last_run_timestamp

    @property
    def processed_count(self) -> int:
        with self._lock:
            return self._processed_count

    @property
    def error_count(self) -> int:
        with self._lock:
            return self._error_count

    def run_once(self) -> list[Any]:
        """Thực thi một vòng quét duy nhất."""
        try:
            results = self.watcher.process_directory(self.inbox_dir)
            with self._lock:
                self._last_run_timestamp = now_utc_iso()
                for res in results:
                    if res.status == "ACCEPTED":
                        self._processed_count += 1
                    elif res.status in ("CORRUPTED", "REJECTED", "NO_CLAIMS"):
                        self._error_count += 1
            return results
        except Exception as exc:
            logger.error("[InboxDaemon] Lỗi trong vòng quét inbox: %s", exc, exc_info=True)
            with self._lock:
                self._error_count += 1
                self._last_run_timestamp = now_utc_iso()
            return []

    def cleanup_archive(self, retention_days: int | None = None) -> int:
        """Dọn dẹp các tệp cũ hơn retention_days trong data/inbox_knowledge/archive/ (và processed/) để chống đầy ổ đĩa."""
        if retention_days is None:
            days = getattr(self, "retention_days", 30)
        else:
            try:
                days = max(0, int(retention_days))
            except (ValueError, TypeError):
                days = getattr(self, "retention_days", 30)

        now = time.time()
        cutoff_seconds = now - (days * 86400)
        deleted_count = 0

        # Quét đồng thời archive/ và processed/ (và thư mục archive riêng của watcher nếu có)
        candidate_dirs = [self.inbox_dir / "archive", self.inbox_dir / "processed"]
        if hasattr(self, "watcher") and hasattr(self.watcher, "archive_dir"):
            candidate_dirs.append(Path(self.watcher.archive_dir))

        target_dirs: list[Path] = []
        seen_resolved: set[Path] = set()
        for cand in candidate_dirs:
            resolved = cand.resolve()
            if resolved not in seen_resolved:
                seen_resolved.add(resolved)
                target_dirs.append(cand)

        for d in target_dirs:
            if not d.exists() or not d.is_dir():
                continue
            try:
                items = list(d.iterdir())
            except Exception as scan_err:
                logger.warning(
                    "[InboxDaemon] Không thể đọc danh sách tệp trong thư mục %s: %s",
                    d, scan_err, exc_info=True,
                )
                continue

            for item in items:
                if not item.is_file() or item.name.startswith("."):
                    continue
                try:
                    mtime = item.stat().st_mtime
                    if mtime < cutoff_seconds:
                        item.unlink(missing_ok=True)
                        deleted_count += 1
                        logger.info(
                            "[InboxDaemon] Đã xoá tệp archive hết hạn retention (%dd): %s",
                            days, item.name,
                        )
                except Exception as del_err:
                    logger.warning(
                        "[InboxDaemon] Không thể xoá tệp archive %s: %s",
                        item.name, del_err, exc_info=True,
                    )
        return deleted_count

    def _loop(self) -> None:
        """Vòng lặp nền chính."""
        logger.info("[InboxDaemon] Background loop started (interval=%.1fs)", self.interval_seconds)
        try:
            while not self._stop_event.is_set():
                try:
                    self.run_once()
                    self.cleanup_archive(self.retention_days)
                except Exception as cycle_exc:
                    logger.error("[InboxDaemon] Uncaught error in loop cycle: %s", cycle_exc, exc_info=True)
                    with self._lock:
                        self._error_count += 1
                # Dùng wait trên Event thay vì sleep để phản hồi ngay khi stop()
                self._stop_event.wait(timeout=self.interval_seconds)
        except Exception as exc:
            logger.error("[InboxDaemon] Uncaught fatal error in loop: %s", exc, exc_info=True)
        finally:
            with self._lock:
                self._is_running = False
            logger.info("[InboxDaemon] Background loop stopped cleanly.")

    def start(self) -> None:
        """Khởi động daemon nếu chưa chạy."""
        with self._lock:
            if self._is_running:
                logger.warning("[InboxDaemon] Daemon is already running.")
                return
            self._stop_event.clear()
            self._is_running = True
            self._thread = threading.Thread(target=self._loop, name="InboxDaemonWorker", daemon=True)
            self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Dừng daemon an toàn."""
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
        with self._lock:
            self._is_running = False
            self._thread = None

    def get_status(self) -> dict[str, Any]:
        """Lấy snapshot trạng thái daemon."""
        with self._lock:
            return {
                "is_running": self._is_running,
                "last_run_timestamp": self._last_run_timestamp,
                "processed_count": self._processed_count,
                "error_count": self._error_count,
                "interval_seconds": self.interval_seconds,
                "retention_days": self.retention_days,
                "inbox_dir": str(self.inbox_dir),
            }


# Module singleton
_GLOBAL_INBOX_DAEMON: InboxDaemon | None = None
_GLOBAL_DAEMON_LOCK = threading.Lock()


def get_inbox_daemon() -> InboxDaemon | None:
    global _GLOBAL_INBOX_DAEMON
    with _GLOBAL_DAEMON_LOCK:
        return _GLOBAL_INBOX_DAEMON


def start_inbox_daemon(
    interval_seconds: float = 30.0,
    inbox_dir: Path | str | None = None,
    db_path: Path | str | None = None,
    retention_days: int | None = None,
) -> InboxDaemon:
    """Khởi động singleton InboxDaemon."""
    global _GLOBAL_INBOX_DAEMON
    with _GLOBAL_DAEMON_LOCK:
        if _GLOBAL_INBOX_DAEMON is None or not _GLOBAL_INBOX_DAEMON.is_running:
            _GLOBAL_INBOX_DAEMON = InboxDaemon(
                inbox_dir=inbox_dir,
                db_path=db_path,
                interval_seconds=interval_seconds,
                retention_days=retention_days,
            )
            _GLOBAL_INBOX_DAEMON.start()
        return _GLOBAL_INBOX_DAEMON


def stop_inbox_daemon(timeout: float = 5.0) -> None:
    """Dừng singleton InboxDaemon."""
    global _GLOBAL_INBOX_DAEMON
    with _GLOBAL_DAEMON_LOCK:
        if _GLOBAL_INBOX_DAEMON is not None:
            _GLOBAL_INBOX_DAEMON.stop(timeout=timeout)
            _GLOBAL_INBOX_DAEMON = None
