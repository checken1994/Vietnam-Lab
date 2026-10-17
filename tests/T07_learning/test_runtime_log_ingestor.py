"""Tests for Dynamic Bug Ingestors (RuntimeLogIngestor, TestFailureIngestor, KernelCrashIngestor).

Strict FA-04 Zero-Mock policy: all tests use real file I/O, temporary directories,
and concrete SQLite test databases.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from scp.autofix.ingestors.kernel_crash_ingestor import KernelCrashIngestor
from scp.autofix.ingestors.runtime_log_ingestor import RuntimeLogIngestor
from scp.autofix.ingestors.test_failure_ingestor import TestFailureIngestor

SAMPLE_TRACEBACK = """
2026-10-18 04:00:01,123 ERROR [uvicorn.error] Exception in ASGI application
Traceback (most recent call last):
  File "/usr/local/lib/python3.12/site-packages/starlette/routing.py", line 68, in app
    response = await func(request)
  File "scp/api/routes/v105_routes.py", line 42, in get_status
    rate = count / denominator
ZeroDivisionError: division by zero
"""

SAMPLE_PYTEST_OUTPUT = """
=================================== FAILURES ===================================
___________________________ test_auth_token_expiry ___________________________

tests/T03_capability/test_auth.py:88: in test_auth_token_expiry
    assert token.is_valid() is False
E   AssertionError: assert True is False
E    +  where True = <Token expired=True>.is_valid()
"""


def test_runtime_log_ingestor_parsing(tmp_path: Path):
    """Verify parsing real Python tracebacks into BugReport objects."""
    log_file = tmp_path / "backend-error.log"
    log_file.write_text(SAMPLE_TRACEBACK, encoding="utf-8")

    ingestor = RuntimeLogIngestor(log_path=log_file)
    bugs = ingestor.collect_bugs()

    assert len(bugs) == 1
    b = bugs[0]
    assert b.file == "scp/api/routes/v105_routes.py"
    assert b.line == 42
    assert b.bug_type == "Runtime_ZeroDivisionError"
    assert "division by zero" in b.description
    assert b.tier == 1


def test_runtime_log_ingestor_auto_cleanup(tmp_path: Path):
    """Verify auto-cleanup of temporary simulation logs (User Directive #3 & Rule #4)."""
    temp_sim_log = tmp_path / "simulated_probe_run.log"
    temp_sim_log.write_text(SAMPLE_TRACEBACK, encoding="utf-8")
    assert temp_sim_log.exists()

    with RuntimeLogIngestor(log_path=temp_sim_log) as ingestor:
        ingestor.register_temp_file(temp_sim_log)
        bugs = ingestor.collect_bugs()
        assert len(bugs) == 1

    # After context manager exits, the temporary simulation log must be automatically wiped!
    assert not temp_sim_log.exists()


def test_test_failure_ingestor_parsing(tmp_path: Path):
    """Verify parsing pytest failure output into BugReport."""
    pytest_report = tmp_path / "pytest_output.txt"
    pytest_report.write_text(SAMPLE_PYTEST_OUTPUT, encoding="utf-8")

    ingestor = TestFailureIngestor(report_path=pytest_report)
    bugs = ingestor.collect_bugs()

    assert len(bugs) == 1
    b = bugs[0]
    assert b.file == "tests/T03_capability/test_auth.py"
    assert b.line == 88
    assert b.bug_type == "Test_Failure"
    assert "test_auth_token_expiry" in b.description
    assert "assert True is False" in b.description


def test_kernel_crash_ingestor(tmp_path: Path):
    """Verify ingesting failed/timed-out tasks from a concrete SQLite Task Kernel."""
    db_file = tmp_path / "ask_task_kernel.sqlite3"
    conn = sqlite3.connect(str(db_file))
    conn.execute(
        """
        CREATE TABLE tasks (
            task_id TEXT PRIMARY KEY,
            state TEXT NOT NULL,
            error TEXT
        );
        """
    )
    conn.execute(
        """
        INSERT INTO tasks (task_id, state, error)
        VALUES ('task-101', 'FAILED', 'Worker lease expired after 120s');
        """
    )
    conn.commit()
    conn.close()

    ingestor = KernelCrashIngestor(db_paths=[db_file])
    bugs = ingestor.collect_bugs()

    assert len(bugs) == 1
    b = bugs[0]
    assert b.bug_type == "Kernel_FAILED"
    assert "task-101" in b.description
    assert "Worker lease expired" in b.description
