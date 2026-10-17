"""SCP Failure Ledger & Epistemic Immunity Memory (WAL-mode SQLite).

Records and indexes all negative feedback, WHY-Gate rejections, DriftGuard
critiques, and runtime exceptions. Inoculates the LLM against repeating known
failed patterns (Prompt Inoculation & Negative Knowledge).

Invariants:
  - FA-04 Zero-Mock: Pure empirical record, no mock or simulated PASS.
  - Fail-Closed: DB corruption or lock falls back to safe read-only / empty list.
  - WAL mode: High concurrency, zero blocking on reading.
"""
from __future__ import annotations

import hashlib
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("scp.epistemic.failure_ledger")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "failure_ledger.sqlite3"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_module_path(module_path: str | Path) -> str:
    p = Path(module_path)
    try:
        if p.is_absolute():
            rel = p.relative_to(PROJECT_ROOT)
            return rel.as_posix()
    except ValueError:
        pass
    return p.as_posix()


class FailureLedger:
    """Epistemic memory of rejected patches, runtime crashes, and anti-patterns."""

    def __init__(self, db_path: Path | str | None = None) -> None:
        self.db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        self._lock = threading.Lock()
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA busy_timeout = 5000;")
        return conn

    def _init_db(self) -> None:
        with self._lock:
            try:
                with self._get_connection() as conn:
                    conn.executescript(
                        """
                        CREATE TABLE IF NOT EXISTS failure_records (
                            failure_id TEXT PRIMARY KEY,
                            failure_type TEXT NOT NULL,
                            target_module TEXT NOT NULL,
                            target_symbol TEXT,
                            verbatim_critique TEXT NOT NULL,
                            falsified_patch_diff TEXT,
                            anti_pattern_rule TEXT,
                            occurrence_count INTEGER DEFAULT 1,
                            first_observed TEXT NOT NULL,
                            last_observed TEXT NOT NULL,
                            status TEXT DEFAULT 'ACTIVE_IMMUNITY'
                        );
                        CREATE INDEX IF NOT EXISTS idx_fl_module ON failure_records(target_module);
                        CREATE INDEX IF NOT EXISTS idx_fl_type ON failure_records(failure_type);
                        CREATE INDEX IF NOT EXISTS idx_fl_status ON failure_records(status);
                        """
                    )
            except Exception as e:
                logger.error("[FailureLedger] Failed to initialize database: %s", e, exc_info=True)

    def record_failure(
        self,
        failure_type: str,
        target_module: str | Path,
        target_symbol: str = "",
        verbatim_critique: str = "",
        falsified_patch_diff: str = "",
        anti_pattern_rule: str = "",
    ) -> str:
        """Record a failure or increment occurrence count if identical."""
        norm_mod = _normalize_module_path(target_module)
        critique_sample = (verbatim_critique or "").strip()[:200]
        fp_raw = f"{failure_type}:{norm_mod}:{critique_sample}"
        failure_id = hashlib.sha256(fp_raw.encode("utf-8")).hexdigest()[:16]
        now = _now_iso()

        if not anti_pattern_rule:
            anti_pattern_rule = f"Avoid pattern leading to: {critique_sample[:80]}"

        with self._lock:
            try:
                with self._get_connection() as conn:
                    row = conn.execute(
                        "SELECT occurrence_count FROM failure_records WHERE failure_id = ?",
                        (failure_id,),
                    ).fetchone()

                    if row:
                        conn.execute(
                            """
                            UPDATE failure_records
                            SET occurrence_count = occurrence_count + 1,
                                last_observed = ?,
                                status = 'ACTIVE_IMMUNITY'
                            WHERE failure_id = ?
                            """,
                            (now, failure_id),
                        )
                    else:
                        conn.execute(
                            """
                            INSERT INTO failure_records (
                                failure_id, failure_type, target_module, target_symbol,
                                verbatim_critique, falsified_patch_diff, anti_pattern_rule,
                                occurrence_count, first_observed, last_observed, status
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, 'ACTIVE_IMMUNITY')
                            """,
                            (
                                failure_id,
                                failure_type,
                                norm_mod,
                                target_symbol,
                                verbatim_critique,
                                falsified_patch_diff,
                                anti_pattern_rule,
                                now,
                                now,
                            ),
                        )
                logger.info(
                    "[FailureLedger] Recorded failure %s for %s (%s)",
                    failure_id,
                    norm_mod,
                    failure_type,
                )
                return failure_id
            except Exception as e:
                logger.error("[FailureLedger] Failed to record failure: %s", e, exc_info=True)
                return failure_id

    def get_anti_patterns_for_module(
        self,
        target_module: str | Path,
        status: str = "ACTIVE_IMMUNITY",
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """Retrieve active anti-patterns and previous failure critiques for a module."""
        norm_mod = _normalize_module_path(target_module)
        with self._lock:
            try:
                with self._get_connection() as conn:
                    cursor = conn.execute(
                        """
                        SELECT failure_id, failure_type, target_module, target_symbol,
                               verbatim_critique, anti_pattern_rule, occurrence_count, last_observed
                        FROM failure_records
                        WHERE (target_module = ? OR target_module LIKE ?)
                          AND status = ?
                        ORDER BY occurrence_count DESC, last_observed DESC
                        LIMIT ?
                        """,
                        (norm_mod, f"%{Path(norm_mod).name}", status, limit),
                    )
                    return [dict(row) for row in cursor.fetchall()]
            except Exception as e:  # noqa: BLE001
                logger.debug("[FailureLedger] get_anti_patterns_for_module error: %s", e)
                return []

    def resolve_failure(self, failure_id: str) -> bool:
        """Mark a failure pattern as resolved after a verified fix."""
        with self._lock:
            try:
                with self._get_connection() as conn:
                    cur = conn.execute(
                        "UPDATE failure_records SET status = 'RESOLVED' WHERE failure_id = ?",
                        (failure_id,),
                    )
                    return cur.rowcount > 0
            except Exception as e:  # noqa: BLE001
                logger.debug("[FailureLedger] resolve_failure error: %s", e)
                return False

    def count_failures(self, status: str | None = None) -> int:
        """Count total failures recorded."""
        with self._lock:
            try:
                with self._get_connection() as conn:
                    if status:
                        row = conn.execute(
                            "SELECT COUNT(*) as c FROM failure_records WHERE status = ?",
                            (status,),
                        ).fetchone()
                    else:
                        row = conn.execute("SELECT COUNT(*) as c FROM failure_records").fetchone()
                    return int(row["c"]) if row else 0
            except Exception:  # noqa: BLE001
                return 0


_global_ledger: FailureLedger | None = None
_ledger_lock = threading.Lock()


def get_failure_ledger(db_path: Path | str | None = None) -> FailureLedger:
    """Get or create singleton FailureLedger instance."""
    global _global_ledger
    if db_path is not None:
        return FailureLedger(db_path=db_path)
    if _global_ledger is None:
        with _ledger_lock:
            if _global_ledger is None:
                _global_ledger = FailureLedger()
    return _global_ledger
