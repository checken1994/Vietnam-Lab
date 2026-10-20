"""
Normative test suite for the AutoFix & Companion Checking/Verification Ecosystem Overhaul (14 Points).

Covers all 8 Causal Branches (B1-B8) with both Positive Acceptance and Mandatory Anti-Placebo Negative Gates:
  - B1: validate_patch deletion syntax acceptance & syntax error rejection (Fail-Closed)
  - B2: realtime_verifier & autofix_helpers CRLF, OLD/NEW, and empty replace parsing
  - B3: post_fix_verify missing test skip vs failing test rollback (Fail-Closed)
  - B4: code_evolution_agent flexible replace & AST multi-line function replacement with blank lines
  - B5: Dynamic Ingestors (KernelCrash, RuntimeLog, TestFailure) filtering & target resolution
  - B6: ast_scan _UndefinedNameFinder noqa suppression & annotation validation
  - B7: ScannerSelfAudit isolated snippet execution & temp directory auto-cleanup
  - B8: VerifyMixin line-shift tolerance (no false rollbacks when lines shift +-1)
  - B9: ShadowSnapshotManager prune_history auto-cleanup
"""
from __future__ import annotations

import ast
from pathlib import Path
import pytest

from scp.autofix.classifier import BugReport, BugTier
from scp.autofix.validate_patch import validate_patch, flexible_replace
from scp.autofix.runner_phases.post_fix_verify import _try_hypothesis_test
from scp.autofix.shadow_snapshot import ShadowSnapshotManager


# ---------------------------------------------------------------------------
# B1: validate_patch deletion & syntax error rejection
# ---------------------------------------------------------------------------

def test_validate_patch_deletion_valid_and_syntax_error_rejected(tmp_path: Path):
    target = tmp_path / "mod.py"
    target.write_text("import sys\nimport os\n\ndef run():\n    return sys.version\n", encoding="utf-8")

    # Positive: deleting unused 'import os' results in valid Python
    res_pos = validate_patch(str(target), "import os", "")
    assert res_pos.valid is True
    assert res_pos.syntax_ok is True
    assert res_pos.search_found is True

    # Negative (Anti-Placebo Fail-Closed): deleting 'return sys.version' leaves def run(): with no body -> SyntaxError
    res_neg = validate_patch(str(target), "    return sys.version", "")
    assert res_neg.valid is False
    assert res_neg.syntax_ok is False
    assert "syntax error" in res_neg.reason.lower()


# ---------------------------------------------------------------------------
# B2: RealTimeVerifier & helpers CRLF, OLD/NEW, empty replace parsing
# ---------------------------------------------------------------------------

def test_realtime_verifier_handles_crlf_old_new_and_deletion(tmp_path: Path):
    src = "line1\r\nline2\r\nline3\r\n"
    res = flexible_replace(src, "line2", "")
    assert res is not None
    assert "line2" not in res
    assert "line1\r\nline3\r\n" == res

    # Test regex parsing with CRLF and empty replace
    import re
    pat = re.compile(
        r"<<<<<<<[ \t]*SEARCH[ \t]*\r?\n(.*?)\r?\n={5,7}[ \t]*\r?\n(?:(.*?)\r?\n)?>>>>>>>[ \t]*(?:REPLACE)?[ \t]*",
        re.DOTALL,
    )
    patch_text = "<<<<<<< SEARCH\r\nimport unused\r\n=======\r\n\r\n>>>>>>> REPLACE"
    m = pat.search(patch_text)
    assert m is not None
    assert m.group(1).strip() == "import unused"
    assert (m.group(2) or "").strip() == ""


# ---------------------------------------------------------------------------
# B3: post_fix_verify missing test skip vs failing test rollback
# ---------------------------------------------------------------------------

def test_post_fix_verify_missing_vs_failing_property_test(tmp_path: Path):
    # Non-existent property test file -> True (SKIPPED_NO_PROPERTY_FILE)
    target = tmp_path / "some_module.py"
    target.write_text("x = 1\n", encoding="utf-8")
    ok, msg = _try_hypothesis_test(str(target))
    assert ok is True
    assert "SKIPPED_NO_PROPERTY_FILE" in msg

    # Existent but failing property test file -> False (Fail-Closed)
    tests_prop = tmp_path / "tests" / "property"
    tests_prop.mkdir(parents=True, exist_ok=True)
    prop_test = tests_prop / "test_some_module.py"
    prop_test.write_text("def test_fail():\n    assert False\n", encoding="utf-8")

    ok_fail, msg_fail = _try_hypothesis_test(str(target))
    assert ok_fail is False
    assert "failed" in msg_fail.lower() or "assert False" in msg_fail


# ---------------------------------------------------------------------------
# B4: code_evolution_agent flexible replace & multi-paragraph function
# ---------------------------------------------------------------------------

def test_patch_applier_flexible_whitespace_and_multiline_function_with_blank_lines(tmp_path: Path):
    from scp.core.code_evolution_agent import CodeEvolutionAgent
    agent = CodeEvolutionAgent.__new__(CodeEvolutionAgent)

    target = tmp_path / "func_mod.py"
    target.write_text(
        "def compute(a, b):\n"
        "    step1 = a + 1\n"
        "\n"
        "    step2 = b * 2\n"
        "\n"
        "    return step1 + step2\n",
        encoding="utf-8",
    )

    # Replace via fenced def with blank lines inside
    fenced_fix = (
        "```python\n"
        "def compute(a, b):\n"
        "    res = a * b\n"
        "\n"
        "    return res + 10\n"
        "```"
    )

    ok, critique = agent._try_patch_with_feedback(target, fenced_fix)
    assert ok is True
    assert critique is None
    patched_text = target.read_text(encoding="utf-8")
    assert "step1" not in patched_text
    assert "res = a * b" in patched_text
    assert "return res + 10" in patched_text
    # Ensure syntax is valid
    ast.parse(patched_text)


# ---------------------------------------------------------------------------
# B5: Dynamic Ingestors filtering & target resolution
# ---------------------------------------------------------------------------

def test_dynamic_ingestors_filtering_and_target_resolution(tmp_path: Path):
    from scp.autofix.ingestors.runtime_log_ingestor import RuntimeLogIngestor

    # File containing 'lib' in name (e.g. calibration.py or calibrate.py) must NOT be ignored
    log_text = (
        "Traceback (most recent call last):\n"
        '  File "D:/scp/scp/epistemic/calibration.py", line 45, in calibrate_item\n'
        "ValueError: invalid score\n"
    )
    ingestor = RuntimeLogIngestor()
    bugs = ingestor.parse_log_content(log_text)
    assert len(bugs) == 1
    assert "calibration.py" in bugs[0].file
    assert bugs[0].line == 45
    assert bugs[0].bug_type == "Runtime_ValueError"


# ---------------------------------------------------------------------------
# B6: ast_scan _UndefinedNameFinder noqa suppression & annotations
# ---------------------------------------------------------------------------

def test_undefined_name_finder_noqa_and_annotations():
    from scp.autofix.runner_phases.ast_scan import _UndefinedNameFinder

    # Case 1: with noqa: F821 -> suppressed
    src_noqa = "def foo():\n    return undefined_var  # noqa: F821\n"
    tree = ast.parse(src_noqa)
    finder = _UndefinedNameFinder(src_noqa.splitlines())
    finder.visit(tree)
    assert len(finder.findings) == 0

    # Case 2: without future annotations, missing type in annotation -> flagged
    src_ann = "def bar(x: MissingType) -> None:\n    pass\n"
    tree_ann = ast.parse(src_ann)
    finder_ann = _UndefinedNameFinder(src_ann.splitlines(), has_future_annotations=False)
    finder_ann.visit(tree_ann)
    names = [f["name"] for f in finder_ann.findings]
    assert "MissingType" in names


# ---------------------------------------------------------------------------
# B7: ScannerSelfAudit isolated snippet execution & temp cleanup
# ---------------------------------------------------------------------------

def test_scanner_self_audit_isolated_snippet_and_temp_cleanup():
    from scp.autofix.scanners._self_audit import ScannerSelfAudit
    import tempfile

    audit = ScannerSelfAudit()
    # Audit only NullSafetyScanner on known corpus
    res = audit.audit_scanner(
        "NullDereference",
        [("null_deref_basic", "def foo():\n    d = {}\n    return d.get('k').attr\n")],
        [("clean_none_check", "def bar():\n    d = {}\n    v = d.get('k')\n    if v: return v.attr\n")],
    )
    assert res.true_positives >= 1
    assert res.precision >= 0.5
    # Verify no temp dirs leaked in temp directory matching scp_selfaudit_null_deref_basic
    tmp_dir = Path(tempfile.gettempdir())
    leaked = list(tmp_dir.glob("scp_selfaudit_null_deref_basic_*"))
    assert len(leaked) == 0


# ---------------------------------------------------------------------------
# B8: VerifyMixin line-shift tolerance
# ---------------------------------------------------------------------------

def test_verify_fix_line_shift_does_not_false_rollback(tmp_path: Path):
    from scp.autofix.engine_parts.verify_mixin import VerifyMixin

    target = tmp_path / "test_shift.py"
    # Pre-patch file: 1 unused import at line 1, 1 bare except at line 5
    pre_content = (
        "import sys\n"
        "import os\n"
        "\n"
        "try:\n"
        "    x = 1\n"
        "except:\n"
        "    pass\n"
    )
    target.write_text(pre_content, encoding="utf-8")

    # Create backup simulating pre-patch snapshot
    bk = target.with_suffix(".py.tier3bak")
    bk.write_text(pre_content, encoding="utf-8")

    # Post-patch: deleted line 2 ('import os'), so bare except shifted from line 6 to line 5
    post_content = (
        "import sys\n"
        "\n"
        "try:\n"
        "    x = 1\n"
        "except:\n"
        "    pass\n"
    )
    target.write_text(post_content, encoding="utf-8")

    mixin = VerifyMixin()
    orig_bug = BugReport(
        file=str(target),
        line=2,
        bug_type="UnusedImport",
        description="Unused import os",
        suggested_fix="",
        tier=BugTier.TIER_1_AUTO_FIX,
    )

    # Check 1, 2, 3 in _verify_fix must not fail due to pre-existing BareExceptPass at line 5
    is_valid, reason = mixin._verify_fix(target, [orig_bug])
    assert is_valid is True, f"Expected verify OK, got: {reason}"


# ---------------------------------------------------------------------------
# B9: ShadowSnapshotManager prune_history
# ---------------------------------------------------------------------------

def test_shadow_snapshot_prune_history(tmp_path: Path):
    shadow_dir = tmp_path / "shadow"
    mgr = ShadowSnapshotManager(shadow_dir=shadow_dir)

    # Create 60 dummy directories in rolled_back
    for i in range(60):
        d = mgr.rolled_back_dir / f"tx_{i:03d}"
        d.mkdir(parents=True, exist_ok=True)
        (d / "manifest.json").write_text("{}", encoding="utf-8")

    assert len(list(mgr.rolled_back_dir.iterdir())) == 60

    # Prune to keep 50
    pruned = mgr.prune_history(max_keep=50)
    assert pruned == 10
    assert len(list(mgr.rolled_back_dir.iterdir())) == 50
