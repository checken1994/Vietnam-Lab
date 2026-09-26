# Auto-extracted from db_manager.py
import gzip
import json
import logging
import os
import sqlite3
import threading
import time
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)
def _preflight_integrity_check() -> None:
    """[R16-ROOT-FIX-4] Check DB integrity before first query.

    If DB is corrupted, attempt recovery via VACUUM INTO (creates fresh DB
    from corrupted one, skipping bad pages). This prevents the cascading
    "database disk image is malformed" errors that plague production.
    """
    import os as _os
    if not _os.path.isfile(DB_PATH):
        return
    try:
        _probe = sqlite3.connect(DB_PATH, timeout=5.0)
        _probe.execute('PRAGMA quick_check')
        _probe.close()
    except sqlite3.DatabaseError as _de:
        _err_msg = str(_de).lower()
        if 'malformed' in _err_msg or 'not a database' in _err_msg:
            logger.error(f"[R16-ROOT-FIX-4] DB corrupted ('{_de}'). Attempting VACUUM INTO recovery...")
            _recovered = DB_PATH + '.recovered'
            try:
                _probe2 = sqlite3.connect(DB_PATH, timeout=30.0)
                # [SEC-S4] VACUUM INTO supports bound parameters for the target
                # filename — never interpolate the path into the SQL string.
                _probe2.execute('VACUUM INTO ?', (_recovered,))
                _probe2.close()
                import time as _time
                _backup = f'{DB_PATH}.broken.{int(_time.time())}'
                _os.rename(DB_PATH, _backup)
                _os.rename(_recovered, DB_PATH)
                logger.info(f'[R16-ROOT-FIX-4] DB recovered via VACUUM INTO. Old corrupted DB saved as {_backup}')
            except Exception as _re:
                logger.critical(f'[R16-ROOT-FIX-4] DB recovery FAILED: {_re}. Delete {DB_PATH} manually to start fresh (data will be lost).', exc_info=True)
        else:
            logger.warning(f'[R16-ROOT-FIX-4] DB warning: {_de}')
