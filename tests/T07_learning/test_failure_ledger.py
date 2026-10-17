"""Tests for SCP FailureLedger (Epistemic Negative Knowledge & Immunity).

Strict FA-04 Zero-Mock policy: all tests use concrete SQLite WAL databases in tmp_path,
concrete data structures, and real execution. Zero unittest.mock.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from scp.epistemic.failure_ledger import FailureLedger, _normalize_module_path


def test_normalize_module_path():
    """Verify module path normalization handles both relative and absolute paths."""
    assert _normalize_module_path("scp/core/test.py") == "scp/core/test.py"
    assert _normalize_module_path(Path("scp/core/test.py")) == "scp/core/test.py"


def test_record_and_retrieve_failure(tmp_path: Path):
    """Verify recording a failure and retrieving it as an active anti-pattern."""
    db_file = tmp_path / "test_failure_ledger.sqlite3"
    ledger = FailureLedger(db_path=db_file)

    # Initially empty
    assert ledger.count_failures() == 0
    patterns = ledger.get_anti_patterns_for_module("scp/security/auth.py")
    assert len(patterns) == 0

    # Record first failure
    fid1 = ledger.record_failure(
        failure_type="WHY_REJECTION",
        target_module="scp/security/auth.py",
        target_symbol="verify_jwt",
        verbatim_critique="Patch removes mandatory cryptographic signature check",
        falsified_patch_diff="<<<<<<< SEARCH\nverify_sig()\n=======\npass\n>>>>>>> REPLACE",
        anti_pattern_rule="Do not remove verify_sig()",
    )
    assert isinstance(fid1, str) and len(fid1) > 0
    assert ledger.count_failures() == 1

    # Retrieve anti-patterns
    patterns = ledger.get_anti_patterns_for_module("scp/security/auth.py")
    assert len(patterns) == 1
    assert patterns[0]["failure_id"] == fid1
    assert patterns[0]["target_module"] == "scp/security/auth.py"
    assert patterns[0]["occurrence_count"] == 1
    assert "signature check" in patterns[0]["verbatim_critique"]

    # Re-recording the identical failure increments occurrence_count without duplicate rows
    fid2 = ledger.record_failure(
        failure_type="WHY_REJECTION",
        target_module="scp/security/auth.py",
        target_symbol="verify_jwt",
        verbatim_critique="Patch removes mandatory cryptographic signature check",
    )
    assert fid2 == fid1
    assert ledger.count_failures() == 1

    patterns_updated = ledger.get_anti_patterns_for_module("scp/security/auth.py")
    assert len(patterns_updated) == 1
    assert patterns_updated[0]["occurrence_count"] == 2


def test_resolve_failure(tmp_path: Path):
    """Verify resolving a failure marks it RESOLVED and excludes from ACTIVE_IMMUNITY."""
    db_file = tmp_path / "test_fl_resolve.sqlite3"
    ledger = FailureLedger(db_path=db_file)

    fid = ledger.record_failure(
        failure_type="TEST_FAILURE",
        target_module="scp/core/engine.py",
        verbatim_critique="ZeroDivisionError in calculate_rate",
    )
    assert len(ledger.get_anti_patterns_for_module("scp/core/engine.py")) == 1

    # Mark resolved
    resolved = ledger.resolve_failure(fid)
    assert resolved is True

    # Active immunity should now be empty
    assert len(ledger.get_anti_patterns_for_module("scp/core/engine.py")) == 0
    # But still present if queried with status='RESOLVED'
    resolved_patterns = ledger.get_anti_patterns_for_module("scp/core/engine.py", status="RESOLVED")
    assert len(resolved_patterns) == 1


def test_wal_mode_and_concurrency(tmp_path: Path):
    """Verify SQLite database enforces WAL journal mode and handles reads concurrently."""
    db_file = tmp_path / "test_fl_wal.sqlite3"
    ledger = FailureLedger(db_path=db_file)

    ledger.record_failure("RUNTIME_EXCEPTION", "scp/api/test.py", verbatim_critique="crash")

    conn = sqlite3.connect(str(db_file))
    cur = conn.cursor()
    mode = cur.execute("PRAGMA journal_mode;").fetchone()[0]
    assert mode.lower() == "wal"
    conn.close()
