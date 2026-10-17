"""Tests for Subsystem Independent Reviewer, AutoFix Autonomy & Queue Pruning.

Governed by SCP DNA #1–#29:
- Distributed Autonomy (Non-Negotiable Rule #7): Autonomous review replaces human bottleneck.
- Anti-Placebo Gate Pattern (Rule #11, FA-01): Comprehensive negative tests proving fail-closed
  rejection on malicious, invalid, drifting, or test-weakening patches.
- Transactional Safety & Reversibility (DNA #7, #9, #26): Changes verified via shadow test and
  postflight invariants before committing.
"""
from __future__ import annotations

import ast
import os
import shutil
import time
from pathlib import Path

import pytest

from scp.autofix.runner_phases.shadow_canary import CanaryResult, CanarySuite, CanaryTestResult
from scp.autofix.subsystem_reviewer import (
    ReviewResult,
    ReviewSummary,
    SubsystemIndependentReviewer,
    apply_patch_to_text,
    is_duplicate_pending_fix,
    parse_pending_fix_file,
    prune_pending_fixes,
)
from scp.core.code_evolution_agent import CodeEvolutionAgent
from scp.governance import DriftDecision, DriftGuard
from scp.meta.capability_levels import CapabilityLevel, get_capability_manager
from scp.meta.why_gate import WhyDecision, WhyResult


@pytest.fixture
def isolated_repo(tmp_path, monkeypatch):
    """Set up an isolated repo environment with spec, scp, data, and pending_fixes directories."""
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)

    spec_dir = repo / "spec"
    spec_dir.mkdir(parents=True)
    (spec_dir / "protected_invariants.yaml").write_text(
        """schema_version: 1
protected:
  - id: security_guards
    description: Core security and capability boundaries
    path_patterns:
      - "scp/security/**"
      - "scp/meta/**"
""",
        encoding="utf-8",
    )

    scp_dir = repo / "scp"
    scp_dir.mkdir(parents=True)
    tests_dir = repo / "tests"
    tests_dir.mkdir(parents=True)
    data_dir = repo / "data"
    data_dir.mkdir(parents=True)
    pending_dir = repo / "pending_fixes"
    pending_dir.mkdir(parents=True)

    monkeypatch.setenv("SCP_CAPABILITY_LEVEL", "FULL_PRODUCTION")
    monkeypatch.setenv("SCP_DEV_MODE", "1")
    monkeypatch.setenv("SCP_WHY_LLM_ENABLED", "0")
    monkeypatch.setenv("SCP_CAPABILITY_SECRET", "test-secret-key-32-bytes-long-123456")

    return repo


def test_subsystem_reviewer_auto_merges_safe_patch_under_full_production(isolated_repo):
    """A safe patch is verified via shadow canary, DriftGuard, and WHY-Gate, and auto-merged."""
    sample_file = isolated_repo / "scp" / "worker_logic.py"
    sample_file.write_text("def compute(x):\n    # TODO: optimize\n    return x + 1\n", encoding="utf-8")

    pending_file = isolated_repo / "pending_fixes" / "worker_logic_pending_01.py"
    patch_text = "<<<<<<< SEARCH\n    # TODO: optimize\n=======\n    # Optimized calculation\n>>>>>>> REPLACE"
    pending_file.write_text(
        f"# Pending fix for worker_logic.py — HUMAN review required.\n# Suggested fix:\n# "
        + patch_text.replace("\n", "\n# ")
        + "\n",
        encoding="utf-8",
    )

    reviewer = SubsystemIndependentReviewer(repo_root=isolated_repo)
    result = reviewer.review_fix(
        target_path=sample_file,
        patch_text=patch_text,
        pending_file=pending_file,
        capability_level=CapabilityLevel.FULL_PRODUCTION,
    )

    assert result.approved is True
    assert result.action == "AUTO_MERGED_BY_SUBSYSTEM_REVIEWER"
    assert "Optimized calculation" in sample_file.read_text(encoding="utf-8")
    assert not pending_file.exists(), "Merged pending fix file must be deleted"


def test_subsystem_reviewer_skips_when_not_full_production(isolated_repo):
    """Under ANALYSIS_ONLY or SANDBOX, reviewer does not auto-merge and preserves pending file."""
    sample_file = isolated_repo / "scp" / "worker_logic.py"
    original_content = "def compute(x):\n    return x + 1\n"
    sample_file.write_text(original_content, encoding="utf-8")

    pending_file = isolated_repo / "pending_fixes" / "worker_logic_pending_02.py"
    patch_text = "<<<<<<< SEARCH\n    return x + 1\n=======\n    return x + 2\n>>>>>>> REPLACE"
    pending_file.write_text(
        f"# Pending fix for worker_logic.py — HUMAN review required.\n# Suggested fix:\n# "
        + patch_text.replace("\n", "\n# "),
        encoding="utf-8",
    )

    reviewer = SubsystemIndependentReviewer(repo_root=isolated_repo)
    result = reviewer.review_fix(
        target_path=sample_file,
        patch_text=patch_text,
        pending_file=pending_file,
        capability_level=CapabilityLevel.ANALYSIS_ONLY,
    )

    assert result.approved is False
    assert result.action == "SKIPPED_NOT_FULL_PRODUCTION"
    assert sample_file.read_text(encoding="utf-8") == original_content, "Source must not be mutated"
    assert pending_file.exists(), "Pending fix must remain intact for non-production capability"


def test_subsystem_reviewer_iterative_refinement_on_drift_critique(isolated_repo):
    """Round 1 is rejected with critique; refiner callback generates safe patch in Round 2 which auto-merges."""
    sample_file = isolated_repo / "scp" / "guarded_service.py"
    sample_file.write_text("def run_service():\n    return 'base'\n", encoding="utf-8")

    # Round 1 introduces syntax error
    round1_patch = "<<<<<<< SEARCH\n    return 'base'\n=======\n    return def bad syntax\n>>>>>>> REPLACE"
    # Round 2 is clean
    round2_patch = "<<<<<<< SEARCH\n    return 'base'\n=======\n    return 'refined_service'\n>>>>>>> REPLACE"

    calls = []

    def mock_refiner(target_path, current_patch, critique):
        calls.append((target_path, current_patch, critique))
        assert "SYNTAX_ERROR" in critique
        return round2_patch

    reviewer = SubsystemIndependentReviewer(repo_root=isolated_repo)
    result = reviewer.review_fix(
        target_path=sample_file,
        patch_text=round1_patch,
        capability_level=CapabilityLevel.FULL_PRODUCTION,
        refiner_callback=mock_refiner,
        max_refinement_rounds=3,
    )

    assert result.approved is True
    assert result.action == "AUTO_MERGED_BY_SUBSYSTEM_REVIEWER"
    assert result.refinement_rounds == 2
    assert len(calls) == 1
    assert "refined_service" in sample_file.read_text(encoding="utf-8")


def test_subsystem_reviewer_refinement_exhaustion_quarantines_failclosed(isolated_repo):
    """When all 3 rounds are rejected, system fails closed and marks as QUARANTINED."""
    sample_file = isolated_repo / "scp" / "unfixable.py"
    original = "def stubborn():\n    return 0\n"
    sample_file.write_text(original, encoding="utf-8")

    round_attempts = 0

    def mock_always_bad_refiner(target_path, current_patch, critique):
        nonlocal round_attempts
        round_attempts += 1
        return f"<<<<<<< SEARCH\n    return 0\n=======\n    return def syntax_{round_attempts}\n>>>>>>> REPLACE"

    initial_patch = "<<<<<<< SEARCH\n    return 0\n=======\n    return def initial_syntax\n>>>>>>> REPLACE"

    reviewer = SubsystemIndependentReviewer(repo_root=isolated_repo)
    result = reviewer.review_fix(
        target_path=sample_file,
        patch_text=initial_patch,
        capability_level=CapabilityLevel.FULL_PRODUCTION,
        refiner_callback=mock_always_bad_refiner,
        max_refinement_rounds=3,
    )

    assert result.approved is False
    assert result.action == "QUARANTINED"
    assert round_attempts == 2  # round 1 was initial, then round 2 and 3 through callback
    assert result.refinement_rounds == 3
    assert sample_file.read_text(encoding="utf-8") == original


def test_anti_placebo_syntax_error_rejected(isolated_repo):
    """Negative test (Anti-Placebo): Malformed syntax must be rejected fail-closed."""
    sample_file = isolated_repo / "scp" / "module_a.py"
    sample_file.write_text("x = 10\n", encoding="utf-8")

    bad_patch = "<<<<<<< SEARCH\nx = 10\n=======\nx = (\n>>>>>>> REPLACE"
    reviewer = SubsystemIndependentReviewer(repo_root=isolated_repo)
    result = reviewer.review_fix(
        target_path=sample_file,
        patch_text=bad_patch,
        capability_level=CapabilityLevel.FULL_PRODUCTION,
    )

    assert result.approved is False
    assert result.action == "QUARANTINED"
    assert "SYNTAX_ERROR" in result.critique
    assert sample_file.read_text(encoding="utf-8") == "x = 10\n"


def test_anti_placebo_driftguard_test_weakening_rejected(isolated_repo):
    """Negative test (Anti-Placebo): Patch introducing pytest.mark.skip in tests/ is rejected."""
    test_file = isolated_repo / "tests" / "test_dummy.py"
    test_file.write_text("def test_dummy():\n    assert 1 == 1\n", encoding="utf-8")

    weakening_patch = "<<<<<<< SEARCH\ndef test_dummy():\n=======\nimport pytest\n@pytest.mark.skip\ndef test_dummy():\n>>>>>>> REPLACE"
    reviewer = SubsystemIndependentReviewer(repo_root=isolated_repo)
    result = reviewer.review_fix(
        target_path=test_file,
        patch_text=weakening_patch,
        capability_level=CapabilityLevel.FULL_PRODUCTION,
    )

    assert result.approved is False
    assert result.action == "QUARANTINED"
    assert "DRIFT_GUARD_BLOCKED" in result.critique
    assert "pytest.mark.skip" in result.critique
    assert "@pytest.mark.skip" not in test_file.read_text(encoding="utf-8")


def test_anti_placebo_why_gate_rejection(isolated_repo):
    """Negative test (Anti-Placebo): Patch rejected by WHY-Gate is rejected fail-closed."""
    sample_file = isolated_repo / "scp" / "security_module.py"
    sample_file.write_text("def check():\n    return True\n", encoding="utf-8")

    patch_text = "<<<<<<< SEARCH\n    return True\n=======\n    return False\n>>>>>>> REPLACE"

    class _MockWhyGate:
        def gate(self, *args, **kwargs):
            return WhyResult(
                decision=WhyDecision.REJECT,
                falsification_reason="Unauthorized mutation of security check",
            )

    mock_why_gate = _MockWhyGate()

    reviewer = SubsystemIndependentReviewer(repo_root=isolated_repo, why_gate=mock_why_gate)
    result = reviewer.review_fix(
        target_path=sample_file,
        patch_text=patch_text,
        capability_level=CapabilityLevel.FULL_PRODUCTION,
    )

    assert result.approved is False
    assert result.action == "QUARANTINED"
    assert "WHY_GATE_BLOCKED" in result.critique
    assert "Unauthorized mutation of security check" in result.critique
    assert sample_file.read_text(encoding="utf-8") == "def check():\n    return True\n"


def test_anti_placebo_mismatched_search_block_rejected(isolated_repo):
    """Negative test (Anti-Placebo): Patch with mismatched SEARCH block fails closed."""
    sample_file = isolated_repo / "scp" / "sample.py"
    sample_file.write_text("alpha = 1\n", encoding="utf-8")

    mismatched_patch = "<<<<<<< SEARCH\nbeta = 2\n=======\nbeta = 3\n>>>>>>> REPLACE"
    reviewer = SubsystemIndependentReviewer(repo_root=isolated_repo)
    result = reviewer.review_fix(
        target_path=sample_file,
        patch_text=mismatched_patch,
        capability_level=CapabilityLevel.FULL_PRODUCTION,
    )

    assert result.approved is False
    assert result.action == "QUARANTINED"
    assert "SEARCH block not found" in result.critique


def test_anti_placebo_shadow_canary_failure_rejected(isolated_repo):
    """Negative test (Anti-Placebo): Patch causing shadow canary smoke call to fail is rejected."""
    sample_file = isolated_repo / "scp" / "canary_target.py"
    sample_file.write_text("def safe_func():\n    return 1\n", encoding="utf-8")

    # Patch that introduces regression detected by canary test
    broken_patch = "<<<<<<< SEARCH\ndef safe_func():\n    return 1\n=======\ndef safe_func():\n    raise RuntimeError('shadow canary runtime regression')\n>>>>>>> REPLACE"

    suite = CanarySuite()
    # Add a canary test that passes on original but detects regression on shadow
    suite.add_test(
        "regression_probe",
        lambda ctx: CanaryTestResult(
            "regression_probe",
            passed=("RuntimeError" not in ctx.get("source", "")),
            reason="RuntimeError introduced in shadow patch",
        ),
    )

    reviewer = SubsystemIndependentReviewer(repo_root=isolated_repo)
    result = reviewer.review_fix(
        target_path=sample_file,
        patch_text=broken_patch,
        capability_level=CapabilityLevel.FULL_PRODUCTION,
        canary_suite=suite,
    )

    assert result.approved is False
    assert result.action == "QUARANTINED"
    assert "SHADOW_CANARY_FAILED" in result.critique


def test_queue_deduplication_prevents_bloat(isolated_repo, monkeypatch):
    """Queueing identical pending fixes for the same file does not produce duplicate files."""
    test_file = isolated_repo / "scp" / "duplicate_target.py"
    test_file.write_text("x = 1\n", encoding="utf-8")

    pending_dir = isolated_repo / "pending_fixes"
    # Unparseable / rejected patch that fails auto-merge and gets queued
    patch_text = "Suggested change cannot be represented as a safe automatic patch."

    monkeypatch.setattr("scp.core.code_evolution_agent.SCP_ROOT", isolated_repo)
    # First call queues the file
    queued1 = CodeEvolutionAgent._queue_pending_fix(test_file, patch_text)
    assert queued1 is False  # Queued for review, not auto-merged
    files_after_first = list(pending_dir.glob("*.py"))
    assert len(files_after_first) == 1

    # Second call with identical target and diff must be deduplicated
    queued2 = CodeEvolutionAgent._queue_pending_fix(test_file, patch_text)
    assert queued2 is False
    files_after_second = list(pending_dir.glob("*.py"))
    assert len(files_after_second) == 1, "Deduplication must prevent generating a duplicate pending fix file"


def test_queue_pruning_removes_duplicate_files(isolated_repo):
    """prune_pending_fixes collapses duplicate files down to 1 per unique content group."""
    pending_dir = isolated_repo / "pending_fixes"

    # Create 5 duplicate files for module A
    content_a = "# Pending fix for mod_a.py — HUMAN review required.\n# Suggested fix:\n# <<<<<<< SEARCH\n# a = 1\n# =======\n# a = 2\n# >>>>>>> REPLACE\n"
    for i in range(5):
        (pending_dir / f"mod_a_pending_{100 + i}_hash.py").write_text(content_a, encoding="utf-8")

    # Create 3 duplicate files for module B
    content_b = "# Pending fix for mod_b.py — HUMAN review required.\n# Suggested fix:\n# <<<<<<< SEARCH\n# b = 1\n# =======\n# b = 2\n# >>>>>>> REPLACE\n"
    for i in range(3):
        (pending_dir / f"mod_b_pending_{200 + i}_hash.py").write_text(content_b, encoding="utf-8")

    # Create 1 unique file for module C
    content_c = "# Pending fix for mod_c.py — HUMAN review required.\n# Suggested fix:\n# <<<<<<< SEARCH\n# c = 1\n# =======\n# c = 2\n# >>>>>>> REPLACE\n"
    (pending_dir / "mod_c_pending_300_hash.py").write_text(content_c, encoding="utf-8")

    assert len(list(pending_dir.glob("*.py"))) == 9

    pruned = prune_pending_fixes(pending_dir)
    assert pruned == 6  # 4 from A, 2 from B
    remaining = list(pending_dir.glob("*.py"))
    assert len(remaining) == 3, f"Expected 3 unique candidate files, found {len(remaining)}"


def test_review_pending_fixes_batch_integration(isolated_repo):
    """review_pending_fixes handles a mix of safe and unsafe pending fix files."""
    target_safe = isolated_repo / "scp" / "safe_mod.py"
    target_safe.write_text("def hello():\n    return 'old'\n", encoding="utf-8")

    target_unsafe = isolated_repo / "scp" / "unsafe_mod.py"
    target_unsafe.write_text("def check():\n    return 1\n", encoding="utf-8")

    pending_dir = isolated_repo / "pending_fixes"

    # 1. Safe fix: changes 'old' to 'new'
    safe_patch = "<<<<<<< SEARCH\n    return 'old'\n=======\n    return 'new'\n>>>>>>> REPLACE"
    (pending_dir / "safe_mod_pending_01.py").write_text(
        f"# Pending fix for safe_mod.py — HUMAN review required.\n# Suggested fix:\n# "
        + safe_patch.replace("\n", "\n# "),
        encoding="utf-8",
    )

    # 2. Unsafe fix: syntax error
    unsafe_patch = "<<<<<<< SEARCH\n    return 1\n=======\n    return def error\n>>>>>>> REPLACE"
    (pending_dir / "unsafe_mod_pending_02.py").write_text(
        f"# Pending fix for unsafe_mod.py — HUMAN review required.\n# Suggested fix:\n# "
        + unsafe_patch.replace("\n", "\n# "),
        encoding="utf-8",
    )

    reviewer = SubsystemIndependentReviewer(repo_root=isolated_repo)
    summary = reviewer.review_pending_fixes(
        pending_dir=pending_dir,
        capability_level=CapabilityLevel.FULL_PRODUCTION,
        auto_prune=True,
    )

    assert summary.total_inspected == 2
    assert summary.approved_count == 1
    assert summary.quarantined_count == 1

    # Verify safe target file was patched and pending file removed
    assert "return 'new'" in target_safe.read_text(encoding="utf-8")
    assert not (pending_dir / "safe_mod_pending_01.py").exists()

    # Verify unsafe target file was not patched and pending file was quarantined
    assert "return 1" in target_unsafe.read_text(encoding="utf-8")
    assert (pending_dir / "unsafe_mod_pending_02.py").exists()


def test_anti_placebo_path_confinement_rejects_external_file(isolated_repo, tmp_path):
    """Anti-Placebo: Target paths strictly outside repo_root must be rejected fail-closed."""
    from scp.autofix.subsystem_reviewer import _resolve_target_path

    # Case 1: External file outside repo_root
    outside_dir = tmp_path / "outside_jail"
    outside_dir.mkdir(parents=True, exist_ok=True)
    outside_secret = outside_dir / "external_keys.py"
    outside_content = "API_KEY = 'secret-original'\n"
    outside_secret.write_text(outside_content, encoding="utf-8")

    malicious_patch = "<<<<<<< SEARCH\nAPI_KEY = 'secret-original'\n=======\nAPI_KEY = 'leaked'\n>>>>>>> REPLACE"

    reviewer = SubsystemIndependentReviewer(repo_root=isolated_repo)
    result = reviewer.review_fix(
        target_path=outside_secret,
        patch_text=malicious_patch,
        capability_level=CapabilityLevel.FULL_PRODUCTION,
    )

    assert result.approved is False
    assert result.action == "QUARANTINED"
    assert "confinement violation" in result.critique.lower()
    assert outside_secret.read_text(encoding="utf-8") == outside_content, "Outside file must not be modified"

    # Case 2: Parent directory traversal in _resolve_target_path
    traversal_res = _resolve_target_path("../outside_jail/external_keys.py", isolated_repo)
    assert traversal_res is None, "Parent directory traversal must resolve to None"

    # Case 3: Wildcard patterns in _resolve_target_path
    wildcard_res = _resolve_target_path("*.py", isolated_repo)
    assert wildcard_res is None, "Wildcard pattern in target string must resolve to None"

    # Case 4: Wildcard patterns in review_fix
    wildcard_fix_res = reviewer.review_fix("*.py", malicious_patch, capability_level=CapabilityLevel.FULL_PRODUCTION)
    assert wildcard_fix_res.approved is False
    assert wildcard_fix_res.action == "QUARANTINED"
    assert "Wildcard pattern not allowed" in wildcard_fix_res.critique


def test_anti_placebo_fail_closed_on_observer_only_and_unknown_capability(isolated_repo, monkeypatch):
    """Anti-Placebo: OBSERVER_ONLY and unrecognized capability levels fail closed to ANALYSIS_ONLY."""
    sample_file = isolated_repo / "scp" / "telemetry_worker.py"
    orig_content = "status = 'normal'\n"
    sample_file.write_text(orig_content, encoding="utf-8")
    patch_text = "<<<<<<< SEARCH\nstatus = 'normal'\n=======\nstatus = 'mutated'\n>>>>>>> REPLACE"

    reviewer = SubsystemIndependentReviewer(repo_root=isolated_repo)

    # 1. Environment variable set to OBSERVER_ONLY
    monkeypatch.setenv("SCP_CAPABILITY_LEVEL", "OBSERVER_ONLY")
    res_observer = reviewer.review_fix(
        target_path=sample_file,
        patch_text=patch_text,
        capability_level=None,
    )
    assert res_observer.approved is False
    assert res_observer.action == "SKIPPED_NOT_FULL_PRODUCTION"
    assert sample_file.read_text(encoding="utf-8") == orig_content

    # 2. String capability_level unrecognized
    res_unknown = reviewer.review_fix(
        target_path=sample_file,
        patch_text=patch_text,
        capability_level="RESTRICTED_REPAIR_UNKNOWN",
    )
    assert res_unknown.approved is False
    assert res_unknown.action == "SKIPPED_NOT_FULL_PRODUCTION"
    assert sample_file.read_text(encoding="utf-8") == orig_content


def test_hyphenated_and_relative_target_parsing(isolated_repo):
    """Hyphenated filenames and full relative target paths are accurately parsed."""
    reality_test_dir = isolated_repo / "tests" / "reality-tests"
    reality_test_dir.mkdir(parents=True, exist_ok=True)
    target_file = reality_test_dir / "reality_4-d-024.py"
    target_file.write_text("def test_reality():\n    return 42\n", encoding="utf-8")

    # With explicit # Target file: line
    pending_file = isolated_repo / "pending_fixes" / "reality_4-d-024_pending.py"
    pending_file.write_text(
        "# Pending fix for reality_4-d-024.py — HUMAN review required.\n"
        "# Target file: tests/reality-tests/reality_4-d-024.py\n"
        "# Suggested fix:\n"
        "# <<<<<<< SEARCH\n"
        "#     return 42\n"
        "# =======\n"
        "#     return 100\n"
        "# >>>>>>> REPLACE\n",
        encoding="utf-8",
    )

    parsed_target, parsed_patch = parse_pending_fix_file(pending_file, repo_root=isolated_repo)
    assert parsed_target == target_file.resolve()
    assert parsed_patch is not None
    assert "return 100" in parsed_patch

    # Legacy format without # Target file: line
    pending_legacy = isolated_repo / "pending_fixes" / "reality_legacy.py"
    pending_legacy.write_text(
        "# Pending fix for reality_4-d-024.py — HUMAN review required.\n"
        "# Suggested fix:\n"
        "# pass\n",
        encoding="utf-8",
    )
    legacy_target, legacy_patch = parse_pending_fix_file(pending_legacy, repo_root=isolated_repo)
    assert legacy_target == target_file.resolve(), f"Legacy hyphenated target parsing failed: {legacy_target}"
    assert legacy_patch == "pass"


def test_defensive_none_and_malformed_patch_input(isolated_repo):
    """Defensive input guards prevent TypeError crashes on None or non-string patch inputs."""
    sample_file = isolated_repo / "scp" / "guard_test.py"
    sample_file.write_text("x = 1\n", encoding="utf-8")

    # apply_patch_to_text with None
    ok1, orig1, err1 = apply_patch_to_text("x = 1\n", None)
    assert ok1 is False
    assert err1 == "Patch text must be a non-empty string"

    # apply_patch_to_text with non-string
    ok2, orig2, err2 = apply_patch_to_text("x = 1\n", 999)  # type: ignore
    assert ok2 is False
    assert err2 == "Patch text must be a non-empty string"

    # apply_patch_to_text with whitespace-only string
    ok3, orig3, err3 = apply_patch_to_text("x = 1\n", "   \n\t  ")
    assert ok3 is False
    assert err3 == "Patch text must be a non-empty string"

    # review_fix with None patch
    reviewer = SubsystemIndependentReviewer(repo_root=isolated_repo)
    res = reviewer.review_fix(sample_file, None, capability_level=CapabilityLevel.FULL_PRODUCTION)  # type: ignore
    assert res.approved is False
    assert res.action == "QUARANTINED"
    assert "non-empty string" in res.critique


def test_refiner_callback_exception_fails_closed(isolated_repo):
    """Exceptions raised in refiner_callback are caught and fail closed to QUARANTINED without crash."""
    sample_file = isolated_repo / "scp" / "failing_callback.py"
    orig_code = "def f():\n    return 0\n"
    sample_file.write_text(orig_code, encoding="utf-8")

    bad_patch = "<<<<<<< SEARCH\n    return 0\n=======\n    return def bad syntax\n>>>>>>> REPLACE"

    def exploding_refiner(target_path, current_patch, critique):
        raise ConnectionError("Simulated LLM Gateway connection drop")

    reviewer = SubsystemIndependentReviewer(repo_root=isolated_repo)
    result = reviewer.review_fix(
        target_path=sample_file,
        patch_text=bad_patch,
        capability_level=CapabilityLevel.FULL_PRODUCTION,
        refiner_callback=exploding_refiner,
        max_refinement_rounds=3,
    )

    assert result.approved is False
    assert result.action == "QUARANTINED"
    assert "REFINER_CALLBACK_ERROR" in result.critique
    assert "Simulated LLM Gateway connection drop" in result.critique
    assert sample_file.read_text(encoding="utf-8") == orig_code


def test_large_patch_deduplication_and_crlf_normalization(isolated_repo, monkeypatch):
    """Large patches (>2000 chars) and CRLF line endings are preserved and correctly deduplicated."""
    import time
    from scp.autofix.subsystem_reviewer import is_duplicate_pending_fix

    target = isolated_repo / "scp" / "large_module.py"
    target.write_text("value = 1\n", encoding="utf-8")

    long_comment = "# " + ("B" * 70 + "\n# ") * 35  # ~2500 chars
    large_patch_crlf = f"<<<<<<< SEARCH\r\nvalue = 1\r\n=======\r\nvalue = 2\r\n{long_comment}\r\n>>>>>>> REPLACE"
    assert len(large_patch_crlf) > 2200

    pending_dir = isolated_repo / "pending_fixes"

    monkeypatch.setattr("scp.core.code_evolution_agent.SCP_ROOT", isolated_repo)
    monkeypatch.setattr("scp.autofix.subsystem_reviewer.SCP_ROOT", isolated_repo)
    monkeypatch.setenv("SCP_CAPABILITY_LEVEL", "SANDBOX")

    # First call: queues the patch
    CodeEvolutionAgent._queue_pending_fix(target, large_patch_crlf)
    files_1 = list(pending_dir.glob("*.py"))
    assert len(files_1) == 1

    # Read content of queued file to ensure no truncation occurred
    queued_content = files_1[0].read_text(encoding="utf-8")
    assert len(queued_content) > 2200, "Fix content must not be truncated to 2000 characters"
    assert "# Target file: scp/large_module.py" in queued_content

    time.sleep(1.1)

    # Second call: same patch with LF line endings must be detected as duplicate
    large_patch_lf = large_patch_crlf.replace("\r\n", "\n")
    CodeEvolutionAgent._queue_pending_fix(target, large_patch_lf)
    files_2 = list(pending_dir.glob("*.py"))
    assert len(files_2) == 1, "Duplicate submission with CRLF vs LF variation must not produce a second file"

    # Verify cross-directory distinct modules with same name do NOT collide
    target_b = isolated_repo / "scp" / "pkg_b" / "large_module.py"
    target_b.parent.mkdir(parents=True, exist_ok=True)
    target_b.write_text("value = 1\n", encoding="utf-8")

    CodeEvolutionAgent._queue_pending_fix(target_b, large_patch_lf)
    files_3 = list(pending_dir.glob("*.py"))
    assert len(files_3) == 2, "Distinct files in different subdirectories must not collide in deduplication"


def test_api_contract_review_patch_alias(isolated_repo):
    """SubsystemIndependentReviewer provides review_patch as an alias for review_fix."""
    assert hasattr(SubsystemIndependentReviewer, "review_patch")
    reviewer = SubsystemIndependentReviewer(repo_root=isolated_repo)
    assert hasattr(reviewer, "review_patch")
    assert reviewer.review_patch == reviewer.review_fix

