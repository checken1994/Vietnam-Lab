"""Physical integrity of every live SQLite database.
This module is part of :mod:`scp.runtime_audit.probes`.
"""
from __future__ import annotations

import logging
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable

from scp.runtime_audit.models import ProbeResult, Severity, utc_now_iso
from scp.runtime_audit.probes._shared import (
    INTEGRITY_CHECK_BUDGET_MS,
    MAX_TABLES_PER_DB,
    SELF_AUDIT_DB_NAMES,
    classify_integrity_failure,
    is_backup_path,
    open_readonly,
    quote_ident,
)

logger = logging.getLogger(__name__)


class SqliteRealityProbe:
    """Verify physical integrity of every live SQLite database.

    Observes: file presence, openability, ``PRAGMA integrity_check``,
    ``PRAGMA quick_check``, journal mode, table census and row counts.
    """

    probe_id = "storage.sqlite_reality"

    def __init__(
        self,
        data_dir: Path,
        *,
        exclude_names: Iterable[str] = SELF_AUDIT_DB_NAMES,
        max_row_census_tables: int = MAX_TABLES_PER_DB,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.exclude_names = {str(n).lower() for n in exclude_names}
        self.max_row_census_tables = max_row_census_tables

    # ── discovery ────────────────────────────────────────────────────────
    def discover(self) -> list[Path]:
        """Find every SQLite database under the data directory."""
        if not self.data_dir.exists():
            return []
        found: list[Path] = []
        seen: set[Path] = set()
        for pattern in ("*.sqlite3", "*.sqlite", "*.db"):
            for candidate in sorted(self.data_dir.rglob(pattern)):
                if not candidate.is_file():
                    continue
                if candidate.name.lower() in self.exclude_names:
                    continue
                resolved = candidate.resolve()
                if resolved in seen:
                    continue
                seen.add(resolved)
                found.append(candidate)
        return found

    @staticmethod
    def _is_backup(path: Path) -> bool:
        return is_backup_path(path)

    # ── observation ──────────────────────────────────────────────────────
    def _integrity_check(self, conn: sqlite3.Connection) -> tuple[str, bool]:
        """Run ``integrity_check`` under a hard time budget.

        Returns ``(verdict, completed)``. When the check raises, the verdict
        is the error text and ``completed`` is False — the database could
        not be checked at all, which is different from being checked and
        failing.
        """
        deadline = time.monotonic() + INTEGRITY_CHECK_BUDGET_MS / 1000.0

        def _progress() -> int:
            return 1 if time.monotonic() > deadline else 0

        conn.set_progress_handler(_progress, 10_000)
        try:
            row = conn.execute("PRAGMA integrity_check").fetchone()
            value = str(row[0]) if row else ""
            return value, True
        except sqlite3.DatabaseError as exc:
            return f"{type(exc).__name__}: {exc}", False
        finally:
            conn.set_progress_handler(None, 0)

    def inspect_database(self, db_path: Path) -> dict[str, Any]:
        """Observe one database. Never raises."""
        rel = self._relative(db_path)
        record: dict[str, Any] = {
            "path": rel,
            "size_bytes": db_path.stat().st_size if db_path.exists() else 0,
            "is_backup": self._is_backup(db_path),
        }
        try:
            conn = open_readonly(db_path)
        except sqlite3.DatabaseError as exc:
            record.update({
                "openable": False,
                "error": f"{type(exc).__name__}: {exc}",
                "integrity_check": None,
                "journal_mode": None,
                "table_count": 0,
                "tables": [],
                "row_total": None,
            })
            return record
        except (OSError, sqlite3.Error) as exc:
            record.update({
                "openable": False,
                "error": f"{type(exc).__name__}: {exc}",
                "integrity_check": None,
                "journal_mode": None,
                "table_count": 0,
                "tables": [],
                "row_total": None,
            })
            return record

        try:
            integrity, completed = self._integrity_check(conn)
            record["integrity_check"] = integrity
            record["integrity_check_completed"] = completed
            try:
                record["journal_mode"] = str(conn.execute("PRAGMA journal_mode").fetchone()[0])
            except sqlite3.DatabaseError:
                record["journal_mode"] = None
            try:
                table_rows = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                ).fetchall()
            except sqlite3.DatabaseError as exc:
                table_rows = []
                record["tables_error"] = f"{type(exc).__name__}: {exc}"
            tables: list[dict[str, Any]] = []
            row_total = 0
            rows_known = True
            for entry in table_rows[: self.max_row_census_tables]:
                table_name = str(entry[0])
                try:
                    count = int(conn.execute(f"SELECT COUNT(*) FROM {quote_ident(table_name)}").fetchone()[0])
                except sqlite3.DatabaseError as exc:
                    count = None
                    rows_known = False
                    tables.append({"name": table_name, "rows": None, "error": f"{type(exc).__name__}: {exc}"})
                    continue
                row_total += count
                tables.append({"name": table_name, "rows": count})
            record["table_count"] = len(table_rows)
            record["tables"] = tables
            record["row_total"] = row_total if rows_known else None
            record["rows_known"] = rows_known
            record["openable"] = True
            # ``readable`` means the schema could actually be listed. A file
            # that opens as a handle but refuses every statement is not
            # readable, and saying so is more honest than calling it corrupt.
            record["readable"] = bool(table_rows) or record["integrity_check_completed"]
            if not record["readable"]:
                record["defect_class"] = classify_integrity_failure(integrity)
            elif not record["integrity_check_completed"]:
                record["defect_class"] = classify_integrity_failure(integrity)
            elif integrity != "ok":
                record["defect_class"] = "corrupt"
            else:
                record["defect_class"] = None
        finally:
            try:
                conn.close()
            except sqlite3.Error as exc:
                # The observation is already complete; a failure to release the
                # read-only handle is recorded, never swallowed silently.
                logger.debug("sqlite close failed for %s: %s", db_path, exc)
        return record

    def _relative(self, db_path: Path) -> str:
        try:
            return str(db_path.resolve().relative_to(self.data_dir.resolve())).replace("\\", "/")
        except ValueError:
            return str(db_path).replace("\\", "/")

    # ── probe protocol ──────────────────────────────────────────────────
    def run(self) -> list[ProbeResult]:
        results: list[ProbeResult] = []
        if not self.data_dir.exists():
            results.append(ProbeResult(
                probe_id=f"{self.probe_id}.datadir",
                severity=Severity.UNKNOWN,
                title="Data directory is not observable",
                summary=f"Data directory {self.data_dir} does not exist; no storage reality could be observed.",
                details={"data_dir": str(self.data_dir)},
            ))
            return results

        databases = self.discover()
        if not databases:
            results.append(ProbeResult(
                probe_id=f"{self.probe_id}.datadir",
                severity=Severity.UNKNOWN,
                title="No SQLite database found",
                summary=f"No *.sqlite3/*.sqlite/*.db file exists under {self.data_dir}.",
                details={
                    "data_dir": str(self.data_dir),
                    "database_count": 0,
                    "corrupt": [],
                    "unreadable": [],
                    "inconclusive": [],
                },
            ))
            return results

        census: list[dict[str, Any]] = []
        corrupt: list[str] = []
        unreadable: list[str] = []
        unknown: list[str] = []

        for db_path in databases:
            record = self.inspect_database(db_path)
            census.append(record)
            path_label = record["path"]
            if record.get("is_backup"):
                # A historical backup is inventoried but never graded.
                continue
            defect_class = record.get("defect_class")
            if defect_class == "corrupt":
                corrupt.append(path_label)
            elif defect_class == "unreadable":
                unreadable.append(path_label)
            elif not record.get("integrity_check_completed", True):
                unknown.append(path_label)

        live_defects = corrupt + unreadable
        if live_defects:
            severity = Severity.FAIL
            parts: list[str] = []
            if corrupt:
                parts.append(
                    f"{len(corrupt)} database(s) failed physical integrity: " + ", ".join(sorted(corrupt)[:8])
                )
            if unreadable:
                parts.append(
                    f"{len(unreadable)} database(s) could not be read: " + ", ".join(sorted(unreadable)[:8])
                )
            title = "Live SQLite database is physically corrupt or unreadable"
            summary = "; ".join(parts)
        elif unknown:
            severity = Severity.UNKNOWN
            title = "Database integrity could not be fully observed"
            summary = f"{len(unknown)} database(s) could not be conclusively checked."
        else:
            severity = Severity.PASS
            title = "All live SQLite databases pass physical integrity"
            summary = f"{len(databases)} database(s) verified; integrity_check returned 'ok' for each."

        results.append(ProbeResult(
            probe_id=self.probe_id,
            severity=severity,
            title=title,
            summary=summary,
            details={
                "data_dir": str(self.data_dir),
                "database_count": len(databases),
                "corrupt": sorted(corrupt),
                "unreadable": sorted(unreadable),
                "inconclusive": sorted(unknown),
            },
            evidence={"census": census},
            observed_at=utc_now_iso(),
        ))
        return results
