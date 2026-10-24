"""Durable history for runtime audit verdicts.

The ledger is written to its OWN database file, separate from every
database it audits, so that recording an observation can never mutate the
subject of the observation. The ledger file name is registered in
:data:`scp.runtime_audit.probes.SELF_AUDIT_DB_NAMES` and is therefore
excluded from the storage census.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from scp.runtime_audit.models import RuntimeAuditVerdict, utc_now_iso

logger = logging.getLogger(__name__)

LEDGER_FILENAME = "runtime_audit.sqlite3"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_runs (
    run_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    verdict TEXT NOT NULL,
    target TEXT NOT NULL,
    data_dir TEXT NOT NULL,
    elapsed_sec REAL NOT NULL,
    finding_count INTEGER NOT NULL,
    summary_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_findings (
    row_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    probe_id TEXT NOT NULL,
    severity TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    detail_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_runs_started ON audit_runs(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_findings_run ON audit_findings(run_id);
"""


class RuntimeAuditLedger:
    """Append-only store of runtime audit verdicts."""

    def __init__(self, data_dir: Path, *, filename: str = LEDGER_FILENAME) -> None:
        self.data_dir = Path(data_dir)
        self.path = self.data_dir / filename

    def _connect(self) -> sqlite3.Connection:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.executescript(_SCHEMA)
        return conn

    def record(self, verdict: RuntimeAuditVerdict) -> str:
        """Persist one verdict. Returns the run id.

        Recording is best-effort for the *audit* but is itself fail-closed
        for the *caller*: a ledger write failure raises, because silently
        losing an audit record is exactly the failure mode this subsystem
        exists to prevent.
        """
        run_id = f"rt-{utc_now_iso()}-{uuid.uuid4().hex[:8]}"
        conn = self._connect()
        try:
            with conn:
                conn.execute(
                    "INSERT INTO audit_runs(run_id, started_at, completed_at, verdict, target, "
                    "data_dir, elapsed_sec, finding_count, summary_json) VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        run_id,
                        verdict.started_at,
                        verdict.completed_at,
                        verdict.verdict.value,
                        verdict.target,
                        verdict.data_dir,
                        verdict.elapsed_sec,
                        len(verdict.findings),
                        json.dumps(verdict.to_dict(), ensure_ascii=False, sort_keys=True),
                    ),
                )
                for finding in verdict.findings:
                    conn.execute(
                        "INSERT INTO audit_findings(run_id, probe_id, severity, title, summary, detail_json) "
                        "VALUES (?,?,?,?,?,?)",
                        (
                            run_id,
                            finding.probe_id,
                            finding.severity.value,
                            finding.title,
                            finding.summary,
                            json.dumps(finding.to_dict(), ensure_ascii=False, sort_keys=True),
                        ),
                    )
        finally:
            try:
                conn.close()
            except sqlite3.Error as exc:
                # The verdict is already persisted; a failure to release the
                # handle is recorded, never swallowed silently.
                logger.debug("sqlite close failed for %s: %s", self.path, exc)
        return run_id

    def history(self, limit: int = 20) -> list[dict[str, Any]]:
        """Return the most recent verdicts, newest first."""
        if not self.path.exists():
            return []
        try:
            conn = self._connect()
        except sqlite3.Error:
            return []
        try:
            rows = conn.execute(
                "SELECT run_id, started_at, completed_at, verdict, target, elapsed_sec, finding_count "
                "FROM audit_runs ORDER BY started_at DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        except sqlite3.Error:
            return []
        finally:
            try:
                conn.close()
            except sqlite3.Error as exc:
                # The verdict is already persisted; a failure to release the
                # handle is recorded, never swallowed silently.
                logger.debug("sqlite close failed for %s: %s", self.path, exc)
        return [dict(row) for row in rows]

    def latest(self) -> dict[str, Any] | None:
        """Return the full most recent verdict, or None."""
        rows = self.history(limit=1)
        if not rows:
            return None
        if not self.path.exists():
            return None
        try:
            conn = self._connect()
        except sqlite3.Error:
            return None
        try:
            row = conn.execute(
                "SELECT summary_json FROM audit_runs WHERE run_id = ?", (rows[0]["run_id"],)
            ).fetchone()
        except sqlite3.Error:
            return None
        finally:
            try:
                conn.close()
            except sqlite3.Error as exc:
                # The verdict is already persisted; a failure to release the
                # handle is recorded, never swallowed silently.
                logger.debug("sqlite close failed for %s: %s", self.path, exc)
        if row is None:
            return None
        try:
            return json.loads(row["summary_json"])
        except (TypeError, ValueError):
            return None
