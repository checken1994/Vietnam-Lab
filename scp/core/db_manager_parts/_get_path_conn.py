# Auto-extracted from db_manager.py
import os
import sqlite3


def _get_path_conn(db_path: str) -> sqlite3.Connection:
    """Get or create a cached connection for a specific db_path (9p/FUSE adaptive)."""
    conn = _path_conns.get(db_path)  # noqa: F821  # [hygiene-keep] _path_conns injected by db_manager.py rebind/wire
    if conn is None:
        try:
            conn = sqlite3.connect(db_path, timeout=30.0, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA busy_timeout=30000")
            conn.execute("CREATE TABLE IF NOT EXISTS _scp_vfs_probe (id INTEGER PRIMARY KEY)")
            conn.execute("INSERT OR REPLACE INTO _scp_vfs_probe (id) VALUES (1)")
            conn.commit()
        except sqlite3.OperationalError:
            try:
                conn.close()
            except (sqlite3.Error, OSError) as _close_err:
                _ = _close_err
            posix_path = os.path.abspath(db_path).replace("\\", "/")
            conn = sqlite3.connect(
                f"file:{posix_path}?vfs=unix-none",
                uri=True,
                timeout=30.0,
                check_same_thread=False,
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA synchronous=OFF")
            conn.execute("PRAGMA journal_mode=MEMORY")
            conn.execute("PRAGMA mmap_size=0")
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA busy_timeout=30000")
        _path_conns[db_path] = conn  # noqa: F821  # [hygiene-keep] _path_conns injected by db_manager.py rebind/wire
    return conn
