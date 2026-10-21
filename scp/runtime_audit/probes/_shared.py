"""Shared helpers for the SCP runtime reality probes.

Every helper here is observation-only: nothing in this module writes to the
system under audit.
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: Databases that belong to the audit subsystem itself. They are excluded
#: from the storage census so the audit never grades its own homework.
SELF_AUDIT_DB_NAMES = frozenset({"runtime_audit.sqlite3", "runtime_audit.sqlite"})

#: Directories whose databases are historical copies. They are inventoried
#: but never fail the verdict (a corrupt old backup is not a live defect).
BACKUP_DIR_MARKERS = ("backups", "backup", "archive")

#: Bound for `PRAGMA integrity_check` so a pathological database cannot hang
#: the whole audit. Exceeding the bound is reported as UNKNOWN, not PASS.
INTEGRITY_CHECK_BUDGET_MS = 20_000

#: Upper bound on tables inspected per database (keeps row census bounded).
MAX_TABLES_PER_DB = 80


def quote_ident(name: str) -> str:
    """Quote a SQLite identifier safely for interpolation into SQL."""
    return '"' + str(name).replace('"', '""') + '"'


def is_backup_path(path: Path) -> bool:
    """True when the database lives under a backup/archive directory."""
    for part in Path(path).parts:
        lowered = part.lower()
        if any(marker in lowered for marker in BACKUP_DIR_MARKERS):
            return True
    return False


def open_readonly(db_path: Path) -> sqlite3.Connection:
    """Open a SQLite database strictly read-only.

    Raises ``sqlite3.DatabaseError`` when the file cannot be opened as a
    database (e.g. a malformed image). The caller turns that into FAIL.
    """
    uri = "file:" + str(db_path).replace("\\", "/").replace("?", "%3f").replace("#", "%23") + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=5.0)
    conn.row_factory = sqlite3.Row
    return conn


def as_int(value: Any) -> int:
    """Best-effort integer coercion. Never raises."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
