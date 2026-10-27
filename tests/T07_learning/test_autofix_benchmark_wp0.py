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
import tempfile
import pytest

from tools.autofix_benchmark_wp0 import (
    ROOT_DIR,
    verify_code_claims,
    audit_fix_ledgers,
    run_seeded_benchmark,
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
    """Verify ledger audit parses records from real data directory."""
    data_dir = ROOT_DIR / "data"
    res = audit_fix_ledgers(data_dir)
    assert "failure_ledger" in res
    assert "tier3_auto_audit" in res
    assert "regression_watch" in res
    assert "policy_blocks" in res

    # Real data directory checks
    if data_dir.exists():
        assert res["failure_ledger"].get("exists") is True
        assert res["tier3_auto_audit"].get("exists") is True
        assert res["regression_watch"].get("exists") is True
        assert res["policy_blocks"].get("exists") is True


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
