import logging
import os
import sqlite3

logger = logging.getLogger(__name__)


def _get_path_conn(db_path: str) -> sqlite3.Connection:
    """Get or create a cached connection for a specific db_path (WAL concurrency tuned)."""
    conn = _path_conns.get(db_path)  # noqa: F821  # [hygiene-keep] _path_conns injected by db_manager.py rebind/wire
    if conn is None:
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        conn = sqlite3.connect(db_path, timeout=60.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=60000")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA synchronous=NORMAL")
        try:
            cur = conn.execute("PRAGMA journal_mode")
            mode = (cur.fetchone() or [""])[0].lower()
            if mode != "wal":
                conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError as _wal_err:
            logger.warning("Could not set PRAGMA journal_mode=WAL on %s: %s", db_path, _wal_err)
        try:
            conn.execute("CREATE TABLE IF NOT EXISTS _scp_vfs_probe (id INTEGER PRIMARY KEY)")
            conn.execute("INSERT OR IGNORE INTO _scp_vfs_probe (id) VALUES (1)")
            conn.commit()
        except sqlite3.OperationalError as _probe_err:
            logger.debug("VFS probe write skipped (non-fatal): %s", _probe_err)
        _path_conns[db_path] = conn  # noqa: F821  # [hygiene-keep] _path_conns injected by db_manager.py rebind/wire
    return conn

