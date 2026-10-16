import logging
import os
import sqlite3

logger = logging.getLogger(__name__)


def get_db():
    """Persistent connection + thread lock.  SQLite PRAGMA tuned for speed and WAL concurrency."""
    global _persistent_conn
    _db_lock.acquire()  # noqa: F821  # [hygiene-keep] _db_lock injected by db_manager.py rebind/wire
    try:
        if _persistent_conn is None:
            os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)  # noqa: F821
            _preflight_integrity_check()  # noqa: F821  # [hygiene-keep] _preflight_integrity_check injected by db_manager.py rebind/wire
            _c = sqlite3.connect(DB_PATH, timeout=60.0, check_same_thread=False)  # noqa: F821
            _c.row_factory = sqlite3.Row
            _c.execute("PRAGMA busy_timeout=60000")
            _c.execute("PRAGMA foreign_keys = ON")
            _c.execute("PRAGMA synchronous=NORMAL")
            _c.execute("PRAGMA cache_size=-128000")
            _c.execute("PRAGMA temp_store=MEMORY")
            _c.execute("PRAGMA mmap_size=268435456")
            _c.execute("PRAGMA wal_autocheckpoint=5000")
            _c.execute("PRAGMA page_size=4096")
            try:
                cur = _c.execute("PRAGMA journal_mode")
                mode = (cur.fetchone() or [""])[0].lower()
                if mode != "wal":
                    _c.execute("PRAGMA journal_mode=WAL")
            except sqlite3.OperationalError as _wal_err:
                logger.warning("Could not set PRAGMA journal_mode=WAL: %s", _wal_err)
            try:
                _c.execute("CREATE TABLE IF NOT EXISTS _scp_vfs_probe (id INTEGER PRIMARY KEY)")
                _c.execute("INSERT OR IGNORE INTO _scp_vfs_probe (id) VALUES (1)")
                _c.commit()
            except sqlite3.OperationalError as _probe_err:
                logger.debug("VFS probe write skipped (non-fatal): %s", _probe_err)
            _persistent_conn = _c
        return _persistent_conn
    finally:
        _db_lock.release()  # noqa: F821  # [hygiene-keep] _db_lock injected by db_manager.py rebind/wire

