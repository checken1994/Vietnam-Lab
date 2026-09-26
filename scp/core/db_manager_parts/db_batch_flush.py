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
def db_batch_flush() -> int:
    """Flush buffered writes to DB in 1 transaction."""
    global _last_batch_flush
    with _batch_lock:
        if not _batch_buffer:
            return 0
        to_flush = _batch_buffer[:]
        _batch_buffer.clear()
        _last_batch_flush = time.time()
    _db_lock.acquire()
    try:
        conn = get_db()
        count = 0
        try:
            for sql, params in to_flush:
                conn.execute(sql, params)
                count += 1
            conn.commit()
            logger.debug(f'Batch flushed: {count} writes')
            return count
        except Exception:
            try:
                conn.rollback()
            except Exception as e:
                logger.debug(f'[V104.37] core/db_manager.py: e={e}', exc_info=True)
            raise
    finally:
        _db_lock.release()
