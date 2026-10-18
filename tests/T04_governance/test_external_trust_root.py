"""Tests for Issue 5: External Trust Root Restoration & Governance Invariants.

Verifies that ExternalTrustRoot passes completely with no missing files and
human-approved constitution. Contains anti-placebo negative tests demonstrating
fail-closed behavior when signatures are missing, malformed, or misplaced.
"""
from __future__ import annotations

import ast
import tempfile
from pathlib import Path

import pytest

from scp.meta.external_trust import ExternalTrustRoot


def test_external_trust_root_full_pass():
    """Verify that ExternalTrustRoot passes with missing: [] and constitution_approved: True."""
    trust = ExternalTrustRoot()
    result = trust.verify_external()

    assert result["passed"] is True, f"External trust failed: {result}"
    assert result["constitution_approved"] is True, "Constitution was not approved"
    assert result["missing"] == [], f"Missing expected files: {result['missing']}"
    assert len(result["checks"]) >= 5


def test_all_expected_files_exist_in_reality():
    """Verify that every file listed in EXPECTED_FILES exists on the filesystem."""
    trust = ExternalTrustRoot()
    for expected in trust.EXPECTED_FILES:
        resolved = trust._resolve_expected_path(expected)
        assert resolved.exists(), f"Expected trust root file does not exist: {expected} (resolved: {resolved})"


# =========================================================================
# ANTI-PLACEBO NEGATIVE TESTS (FA-01 / FA-11 / FA-13 / DNA #11)
# =========================================================================


def test_constitution_missing_line1_signature_fails_approval():
    """Negative test: Constitution without the strict line 1 marker MUST fail approval."""
    trust = ExternalTrustRoot()
    sample_content = '''"""
V4 Constitution without human signature.
"""
class Principle:
    pass
'''
    assert trust._is_constitution_human_approved(sample_content) is False


def test_constitution_invalid_date_fails_approval():
    """Negative test: Non-existent or invalid date in signature MUST fail closed."""
    trust = ExternalTrustRoot()
    # Invalid month/day
    bad_date_content = '# HUMAN_APPROVED_BY: Minh 2026-99-99\n"""doc"""'
    assert trust._is_constitution_human_approved(bad_date_content) is False

    # Non-date string
    bad_string_content = '# HUMAN_APPROVED_BY: Minh not-a-date\n"""doc"""'
    assert trust._is_constitution_human_approved(bad_string_content) is False


def test_constitution_signature_not_on_line1_fails_approval():
    """Negative test: Valid signature placed on line 5 instead of line 1 MUST fail."""
    trust = ExternalTrustRoot()
    misplaced_content = '''"""
Docstring first.
Some more docstring.
"""
# HUMAN_APPROVED_BY: Minh 2026-10-18
class Principle:
    pass
'''
    assert trust._is_constitution_human_approved(misplaced_content) is False


def test_deprecated_substring_markers_fail_closed():
    """Negative test: Legacy substring markers without line 1 marker MUST NOT grant approval."""
    trust = ExternalTrustRoot()
    legacy_content = '''"""
Copyright (c) 2026 [Author: Minh / SCP V4 Project]
# human-approved
# human_reviewed
"""
'''
    assert trust._is_constitution_human_approved(legacy_content) is False


def test_external_trust_root_detects_missing_file_in_sandbox():
    """Negative test: If any expected trust root file is missing, verify_external must fail."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_root = Path(tmp_dir)
        # Create only a partial structure
        (tmp_root / ".github" / "workflows").mkdir(parents=True)
        (tmp_root / "meta").mkdir(parents=True)
        (tmp_root / "meta" / "constitution.py").write_text(
            "# HUMAN_APPROVED_BY: Tester 2026-10-18\n", encoding="utf-8"
        )
        # tests/external_audit is missing!

        trust = ExternalTrustRoot(project_root=str(tmp_root))
        result = trust.verify_external()

        assert result["passed"] is False
        assert "tests/external_audit/test_security.py" in result["missing"]
        assert "tests/external_audit/conftest.py" in result["missing"]


def test_external_audit_decoupling_ast_invariant():
    """Negative check / Decoupling invariant:
    Ensure tests/external_audit/test_security.py and conftest.py do not import scp.
    """
    repo_root = Path(__file__).resolve().parent.parent.parent
    external_audit_dir = repo_root / "tests" / "external_audit"

    for py_file in (external_audit_dir / "test_security.py", external_audit_dir / "conftest.py"):
        assert py_file.exists(), f"Missing file: {py_file}"
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("scp"), (
                        f"Circular trust violation in {py_file.name}: imports '{alias.name}'"
                    )
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                assert not mod.startswith("scp"), (
                    f"Circular trust violation in {py_file.name}: imports from '{mod}'"
                )
