# Auto-extracted from db_manager.py
import os
import sqlite3


def get_db():
    """Persistent connection + thread lock.  SQLite PRAGMA tuned for speed."""
    global _persistent_conn
    _db_lock.acquire()  # noqa: F821  # [hygiene-keep] _db_lock injected by db_manager.py rebind/wire
    try:
        if _persistent_conn is None:
            os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)  # noqa: F821
            _preflight_integrity_check()  # noqa: F821  # [hygiene-keep] _preflight_integrity_check injected by db_manager.py rebind/wire
            _c = None
            try:
                _c = sqlite3.connect(DB_PATH, timeout=30.0, check_same_thread=False)  # noqa: F821
                _c.row_factory = sqlite3.Row
                _c.execute("PRAGMA journal_mode=WAL")
                _c.execute("PRAGMA synchronous=NORMAL")
                _c.execute("PRAGMA cache_size=-128000")
                _c.execute("PRAGMA temp_store=MEMORY")
                _c.execute("PRAGMA busy_timeout=30000")
                _c.execute("PRAGMA mmap_size=268435456")
                _c.execute("PRAGMA wal_autocheckpoint=5000")
                _c.execute("PRAGMA page_size=4096")
                _c.execute("PRAGMA foreign_keys = ON")
                _c.execute("CREATE TABLE IF NOT EXISTS _scp_vfs_probe (id INTEGER PRIMARY KEY)")
                _c.execute("INSERT OR REPLACE INTO _scp_vfs_probe (id) VALUES (1)")
                _c.commit()
                _persistent_conn = _c
            except sqlite3.OperationalError:
                if _c is not None:
                    try:
                        _c.close()
                    except (sqlite3.Error, OSError) as _close_err:
                        _ = _close_err
                    _c = None
                _posix_p = os.path.abspath(DB_PATH).replace("\\", "/")  # noqa: F821
                try:
                    _c = sqlite3.connect(
                        f"file:{_posix_p}?vfs=unix-none",
                        uri=True,
                        timeout=30.0,
                        check_same_thread=False,
                    )
                except sqlite3.OperationalError:
                    _c = sqlite3.connect(DB_PATH, timeout=30.0, check_same_thread=False)  # noqa: F821
                _c.row_factory = sqlite3.Row
                _c.execute("PRAGMA synchronous=OFF")
                _c.execute("PRAGMA journal_mode=MEMORY")
                _c.execute("PRAGMA cache_size=-128000")
                _c.execute("PRAGMA temp_store=MEMORY")
                _c.execute("PRAGMA busy_timeout=30000")
                _c.execute("PRAGMA mmap_size=0")
                _c.execute("PRAGMA foreign_keys = ON")
                _persistent_conn = _c
        return _persistent_conn
    finally:
        _db_lock.release()  # noqa: F821  # [hygiene-keep] _db_lock injected by db_manager.py rebind/wire
