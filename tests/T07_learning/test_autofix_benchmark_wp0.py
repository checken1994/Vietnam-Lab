"""Tests for WP0 Autofix Benchmark Runner.

Verifies:
- 5/5 core code invariant claims
- Fix ledger audit functionality
- Seeded flaw benchmark execution across all 5 tracks
- Anti-placebo test: claim checker fails-closed on damaged root or missing file
- ARC-01 compliance: tools/autofix_benchmark_wp0.py <= 800 LOC
"""
from __future__ import annotations

from pathlib import Path
import json
import sqlite3
import tempfile
import pytest

from tools.autofix_benchmark_wp0 import (
    ROOT_DIR,
    verify_code_claims,
    audit_fix_ledgers,
    run_seeded_benchmark,
    is_placebo_patch,
)


def test_code_claims_all_verified():
    """Verify that all 5 code claims are verified on real repo."""
    res = verify_code_claims(ROOT_DIR)
    assert res["all_claims_verified"] is True
    assert res["verified_count"] == 5
    assert res["total_claims"] == 5

    claims = res["claims"]
    assert claims["claim_1_cap_test_targets"]["verified"] is True
    assert claims["claim_2_brain_model_fallback"]["verified"] is True
    assert claims["claim_3_rollback_rate_locations"]["verified"] is True
    assert claims["claim_4_repro_generator_assert_false"]["verified"] is True
    assert claims["claim_5_pytest_baseline_vs_post_patch"]["verified"] is True


def test_code_claims_anti_placebo_fails_on_missing_files(tmp_path):
    """Anti-placebo: verify_code_claims must fail closed when files are missing."""
    empty_root = tmp_path / "empty_repo"
    empty_root.mkdir()
    res = verify_code_claims(empty_root)
    assert res["all_claims_verified"] is False
    assert res["verified_count"] == 0


def test_fix_ledger_audit_structure():
    """Verify ledger audit parses records from real data directory hermetically.

    Handles both dirty workspace (untracked local files present) and clean clone
    (where untracked runtime files do not exist).
    """
    data_dir = ROOT_DIR / "data"
    res = audit_fix_ledgers(data_dir)
    assert "failure_ledger" in res
    assert "tier3_auto_audit" in res
    assert "regression_watch" in res
    assert "policy_blocks" in res

    ledger_files = (
        ("failure_ledger", "failure_ledger.sqlite3"),
        ("tier3_auto_audit", "tier3_auto_audit.jsonl"),
        ("regression_watch", "regression_watch.jsonl"),
        ("policy_blocks", "policy_blocks.jsonl"),
    )
    for key, filename in ledger_files:
        fpath = data_dir / filename
        if fpath.exists():
            assert res[key].get("exists") is True
            assert "error" not in res[key]
        else:
            assert res[key].get("exists") is False


def test_fix_ledger_audit_hermetic_fixture(tmp_path):
    """Hermetic test: verify ledger schema parsing and aggregations against isolated fixtures."""
    # 1. SQLite failure ledger
    db_path = tmp_path / "failure_ledger.sqlite3"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("CREATE TABLE failure_records (failure_type TEXT, status TEXT)")
        conn.execute("INSERT INTO failure_records VALUES ('MAKER_CHECKER_REJECTION', 'ACTIVE_IMMUNITY')")
        conn.execute("INSERT INTO failure_records VALUES ('WHY_REJECTION', 'RESOLVED')")
        conn.commit()
    finally:
        conn.close()

    # 2. tier3_auto_audit.jsonl
    t3_path = tmp_path / "tier3_auto_audit.jsonl"
    t3_path.write_text(
        json.dumps({"action": "auto_approve", "bug_type": "HardcodedSecret"}) + "\n",
        encoding="utf-8",
    )

    # 3. regression_watch.jsonl
    rw_path = tmp_path / "regression_watch.jsonl"
    rw_path.write_text(
        json.dumps({"action": "register"}) + "\n" +
        json.dumps({"action": "rollback"}) + "\n",
        encoding="utf-8",
    )

    # 4. policy_blocks.jsonl
    pb_path = tmp_path / "policy_blocks.jsonl"
    pb_path.write_text(
        json.dumps({"blocked_patterns": ["verify_false_tls", "eval_call"]}) + "\n",
        encoding="utf-8",
    )

    res = audit_fix_ledgers(tmp_path)

    # Strictly assert parsed structure and calculations
    assert res["failure_ledger"]["exists"] is True
    assert res["failure_ledger"]["records"] == 2
    assert res["failure_ledger"]["active_immunity_rules"] == 1
    assert res["failure_ledger"]["breakdown"]["MAKER_CHECKER_REJECTION [ACTIVE_IMMUNITY]"] == 1
    assert res["failure_ledger"]["breakdown"]["WHY_REJECTION [RESOLVED]"] == 1

    assert res["tier3_auto_audit"]["exists"] is True
    assert res["tier3_auto_audit"]["entries"] == 1
    assert res["tier3_auto_audit"]["bug_types"]["HardcodedSecret"] == 1
    assert res["tier3_auto_audit"]["actions"]["auto_approve"] == 1

    assert res["regression_watch"]["exists"] is True
    assert res["regression_watch"]["entries"] == 2
    assert res["regression_watch"]["actions"]["register"] == 1
    assert res["regression_watch"]["actions"]["rollback"] == 1
    assert res["regression_watch"]["rollback_rate"] == 1.0

    assert res["policy_blocks"]["exists"] is True
    assert res["policy_blocks"]["entries"] == 1
    assert res["policy_blocks"]["patterns"]["verify_false_tls"] == 1
    assert res["policy_blocks"]["patterns"]["eval_call"] == 1


def test_anti_placebo_destructive_logic_rejection():
    """Verify that destructive patches and placebos are rejected, while genuine fixes are accepted."""
    destructive_patches = [
        "pass\n",
        "return None\n",
        "return 0\n",
        "return False\n",
        "return ''\n",
        "assert True\n",
        "assert 1 == 1\n",
        "assert x == x\n",
        "assert res == expected or True\n",
        "x = x\n",
        "1 + 1\n",
        "return 'MOCK_VERIFIED'\n",
        "def broken():\n    return 0\n",
        "def broken():\n    pass\n",
    ]
    for patch in destructive_patches:
        is_placebo, reason = is_placebo_patch(patch)
        assert is_placebo is True, f"Expected {patch!r} to be rejected as placebo/destructive, but was accepted: {reason}"

    genuine_patches = [
        "def divide(a, b):\n    if b == 0:\n        raise ValueError('Zero denominator')\n    return a / b\n",
        "def get_val(d, k):\n    return d[k] if d is not None and k in d else None\n",
        "def to_int(s: str) -> int:\n    return int(s.strip())\n",
    ]
    for patch in genuine_patches:
        is_placebo, reason = is_placebo_patch(patch)
        assert is_placebo is False, f"Expected genuine fix to be accepted, but was rejected: {reason}"


def test_seeded_flaw_benchmark_full_pass(tmp_path):
    """Verify seeded flaw benchmark runs 75 cases with >= 95% pass rate."""
    bench_dir = tmp_path / "bench_sandbox"
    bench_dir.mkdir()
    res = run_seeded_benchmark(bench_dir, target_cases=75)

    assert res["total_seeded_cases"] == 75
    assert res["pass_rate_pct"] >= 95.0
    assert res["failed_cases"] == 0

    tracks = res["track_summary"]
    for tname in (
        "track_1_deterministic_ast",
        "track_2_security_policy_gate",
        "track_3_clean_rollback",
        "track_4_anti_placebo",
        "track_5_complex_rescan",
    ):
        assert tname in tracks
        assert tracks[tname]["total"] == 15
        assert tracks[tname]["passed"] == 15
        assert tracks[tname]["failed"] == 0


def test_benchmark_runner_arc01_compliance():
    """ARC-01: tools/autofix_benchmark_wp0.py must be <= 800 LOC."""
    runner_path = ROOT_DIR / "tools" / "autofix_benchmark_wp0.py"
    assert runner_path.exists()
    lines = runner_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) <= 800, f"tools/autofix_benchmark_wp0.py has {len(lines)} lines, exceeding 800 LOC ceiling"
