"""Task Kernel Crash Ingestor for SCP AutoFix.

Scans data/ask_task_kernel.sqlite3 and data/hands/task_kernel.sqlite3 for tasks
in FAILED or TIMED_OUT states and converts their failure reasons into BugReports.

Invariants:
  - FA-04 Zero-Mock: Reads physical SQLite rows directly.
  - Fail-Closed: SQLite locks or missing tables return empty list safely.
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from scp.autofix.classifier import BugReport, BugTier

logger = logging.getLogger("scp.autofix.kernel_crash_ingestor")

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_KERNEL_DBS = [
    PROJECT_ROOT / "data" / "ask_task_kernel.sqlite3",
    PROJECT_ROOT / "data" / "hands" / "task_kernel.sqlite3",
]


class KernelCrashIngestor:
    """Ingests task crashes and timeouts from Task Kernel databases."""

    def __init__(self, db_paths: list[Path] | None = None) -> None:
        self.db_paths = db_paths or DEFAULT_KERNEL_DBS

    def collect_bugs(self, max_bugs: int = 10) -> list[BugReport]:
        """Collect crashed tasks from kernel databases."""
        bugs: list[BugReport] = []

        for db_p in self.db_paths:
            if not db_p.exists():
                continue

            try:
                conn = sqlite3.connect(str(db_p), timeout=3.0)
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()

                # Check if tasks table exists
                tbl_check = cursor.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='tasks'"
                ).fetchone()
                if not tbl_check:
                    conn.close()
                    continue

                rows = cursor.execute(
                    """
                    SELECT task_id, state, error
                    FROM tasks
                    WHERE state IN ('FAILED', 'TIMED_OUT')
                      AND COALESCE(error, '') NOT LIKE '%insufficient_evidence_audited_and_contained%'
                      AND COALESCE(error, '') NOT LIKE 'evidence_resolution:%'
                    LIMIT ?
                    """,
                    (max_bugs,),
                ).fetchall()

                import re as _re
                from scp.autofix.runner_phases.ast_scan import _is_protected_path

                _file_line_pat = _re.compile(
                    r'(?:File\s+["\'](?P<f1>[^"\']+\.py)["\'],\s*line\s+(?P<l1>\d+)|(?P<f2>scp/[A-Za-z0-9_./\\-]+\.py):(?P<l2>\d+))'
                )

                for row in rows:
                    task_id = row["task_id"]
                    state = row["state"]
                    err = row["error"] or "Task execution failure without error detail"
                    # [esc-auto slice 2] evidence outcomes are resolver verdicts, not crashes.
                    if "insufficient_evidence_audited_and_contained" in err or err.startswith("evidence_resolution:"):
                        continue

                    target_file = "scp/core/autonomous_cycle.py"
                    target_line = 1
                    for m in _file_line_pat.finditer(err):
                        cand_file = (m.group("f1") or m.group("f2") or "").replace("\\", "/")
                        cand_line = int(m.group("l1") or m.group("l2") or 1)
                        try:
                            p = Path(cand_file)
                            if p.is_absolute() and p.is_relative_to(PROJECT_ROOT):
                                cand_file = p.relative_to(PROJECT_ROOT).as_posix()
                        except (ValueError, OSError) as _path_err:
                            logger.debug('cand_file relative_to failed: %s', _path_err)
                        if cand_file.startswith("scp/") and not _is_protected_path(cand_file):
                            target_file = cand_file
                            target_line = cand_line

                    bugs.append(
                        BugReport(
                            file=target_file,
                            line=target_line,
                            bug_type=f"Kernel_{state}",
                            description=f"Task {task_id} {state}: {err[:150]}",
                            suggested_fix=f"# Investigate task {task_id} failure in {target_file}",
                            tier=BugTier.TIER_2_AUTO_FIX_LOG,
                            is_restraint=False,
                            is_reversible=True,
                            affects_logic=True,
                        )
                    )
                conn.close()
            except (sqlite3.Error, OSError) as e:
                logger.debug("[KernelCrashIngestor] Error querying %s: %s", db_p, e)

        return bugs[:max_bugs]
