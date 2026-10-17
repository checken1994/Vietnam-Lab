"""Contract and unit tests for Global AST Symbol Index, Failure Cooldown, and Consolidated Auth.

[SCP DNA #1, #4, #19, #26] Reality over Model:
  Verifies that:
  1. GlobalSymbolIndex discovers cross-module symbols and runtime dynamic wiring.
  2. Provenance prompt sections instruct LLM not to delete injected safety gates.
  3. Failure Cooldown stops scanner false-positive loops.
  4. 3 Auth mechanisms (JWT/SCP_ADMIN_KEY, SCP_AUTH_TOKEN_SECRET, SCP_SCHEDULER_ADMIN_TOKEN)
     are consolidated and interoperable.
  5. Tabula Rasa is preserved (BOOTSTRAP_FACTS is empty).
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from scp.autofix.classifier import BugReport, BugTier
from scp.autofix.engine import AutoFixEngine
from scp.autofix.global_symbol_index import GlobalSymbolIndex, get_global_symbol_index
from scp.autofix.llm_fix import process_bug_with_llm
from scp.core.autonomous_cycle import BOOTSTRAP_FACTS
from scp.security.auth_config import AuthConfig, load_auth_config


def test_tabula_rasa_bootstrap_facts_empty():
    """Verify that hardcoded static bootstrap facts have been eliminated for Tabula Rasa."""
    assert len(BOOTSTRAP_FACTS) == 0, "BOOTSTRAP_FACTS must be empty for 100% Tabula Rasa"


def test_global_symbol_index_scans_codebase():
    """Verify GlobalSymbolIndex parses files and extracts functions, classes, and constants."""
    index = get_global_symbol_index()
    assert index is not None

    # Known symbols in scp/autofix/llm_fix.py
    locs = index.lookup_symbol("_validate_openrouter_base_url")
    assert len(locs) > 0, "Expected _validate_openrouter_base_url to be indexed"
    assert locs[0].file_path == "scp/autofix/llm_fix.py"
    assert locs[0].kind == "function"

    # Known constant
    simple_locs = index.lookup_symbol("_SIMPLE_BUG_TYPES")
    assert len(simple_locs) > 0
    assert simple_locs[0].file_path == "scp/autofix/llm_fix.py"
    assert simple_locs[0].kind == "constant"


def test_global_symbol_index_detects_dynamic_wiring():
    """Verify that symbols referenced in split files are correctly traced to their runtime source."""
    index = get_global_symbol_index()
    provenances = index.find_provenance_for_file("scp/autofix/llm_fix_parts/_call_openrouter.py")
    names = {p.name: p for p in provenances}

    assert "_validate_openrouter_base_url" in names, "Expected provenance for _validate_openrouter_base_url"
    prov = names["_validate_openrouter_base_url"]
    assert prov.file_path == "scp/autofix/llm_fix.py"
    assert "injected" in prov.wiring_note.lower() or "_wire_parts" in prov.wiring_note


def test_provenance_prompt_section_generation():
    """Verify that the generated prompt block contains strict preservation directives."""
    index = get_global_symbol_index()
    section = index.build_provenance_prompt_section("scp/autofix/llm_fix_parts/_call_openrouter.py")

    assert "[GLOBAL AST SYMBOL PROVENANCE — MANDATORY FIDELITY GUARD]" in section
    assert "_validate_openrouter_base_url" in section
    assert "DO NOT remove, delete, comment out, or bypass" in section


def test_failure_cooldown_stops_infinite_retry(tmp_path):
    """Verify that failure cooldown is recorded on LLM failure and skips subsequent runs."""
    engine = AutoFixEngine(str(tmp_path))
    bug = BugReport(
        file="scp/test_file.py",
        line=42,
        bug_type="PossiblyUndefinedName",
        description="F821 undefined name 'dummy_symbol'",
        suggested_fix="",
        tier=BugTier.TIER_1_AUTO_FIX,
    )

    bug_key = f"{bug.file}:{bug.line}:{bug.bug_type}"
    assert not engine.is_in_cooldown(bug_key)[0]

    # Record failure cooldown
    engine.record_cooldown(bug_key, reason="LLM fix generation failed")
    in_cd, reason = engine.is_in_cooldown(bug_key)
    assert in_cd is True
    assert "cooldown" in reason

    # Call process_bug_with_llm: must immediately return skipped without calling LLM
    result = process_bug_with_llm(bug, engine, allow_llm=True)
    assert result.get("action") == "skipped"
    assert "cooldown" in result.get("reason", "")
    assert result.get("fix_source") == "cooldown_skip"


def test_consolidated_auth_tokens(monkeypatch):
    """Verify that AuthConfig and jwt_guard recognize all 3 admin credential forms."""
    monkeypatch.setenv("SCP_ADMIN_KEY", "admin-key-test-1234567890123456")
    monkeypatch.setenv("SCP_AUTH_TOKEN_SECRET", "auth-token-test-123456789012345")
    monkeypatch.setenv("SCP_SCHEDULER_ADMIN_TOKEN", "sched-token-test-1234567890123")

    cfg = load_auth_config()
    assert cfg.configured is True
    assert "admin-key-test-1234567890123456" in cfg.valid_admin_tokens
    assert "auth-token-test-123456789012345" in cfg.valid_admin_tokens
    assert "sched-token-test-1234567890123" in cfg.valid_admin_tokens

    # Verify jwt_guard verify_api_key accepts all 3
    from scp.security.jwt_guard import verify_api_key
    res1 = verify_api_key("admin-key-test-1234567890123456")
    assert res1.get("role") == "admin"

    res2 = verify_api_key("auth-token-test-123456789012345")
    assert res2.get("role") == "admin"

    res3 = verify_api_key("sched-token-test-1234567890123")
    assert res3.get("role") == "admin"

    # Negative test (Anti-placebo): invalid key must raise 401
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc_info:
        verify_api_key("invalid-tampered-token")
    assert exc_info.value.status_code == 401


def test_global_symbol_index_robustness_external_paths_and_syntax_error(tmp_path):
    """Verify GlobalSymbolIndex gracefully handles external paths and syntax errors without crashing."""
    index = GlobalSymbolIndex(root_dir=tmp_path)

    # 1. File with syntax error
    bad_syntax = tmp_path / "bad.py"
    bad_syntax.write_text("def broken_syntax(:\n   pass\n", encoding="utf-8")
    locs = index.scan_file(bad_syntax)
    assert locs == [], "Expected empty symbols on SyntaxError"

    # 2. File completely outside self.root (e.g. parent dir or another tempdir)
    import tempfile
    with tempfile.TemporaryDirectory() as external_dir:
        external_file = Path(external_dir) / "external.py"
        external_file.write_text("def external_func():\n    pass\n", encoding="utf-8")

        # scan_file should handle path outside root without ValueError
        ext_locs = index.scan_file(external_file)
        assert len(ext_locs) == 1
        assert ext_locs[0].name == "external_func"

        # find_provenance_for_file should not raise ValueError
        prov = index.find_provenance_for_file(external_file, candidate_names=["nonexistent_xyz"])
        assert isinstance(prov, list)

        # build_provenance_prompt_section should fail-open gracefully
        section = index.build_provenance_prompt_section(external_file)
        assert isinstance(section, str)


def test_runner_skips_cooldown_before_why_gate(tmp_path, monkeypatch):
    """Verify that runner filters out cooldown bugs before calling WHY-gate and records cooldown on skip."""
    from scp.autofix.runner import run_once
    from scp.meta.why_gate import WhyDecision, WhyResult

    engine = AutoFixEngine(str(tmp_path))
    monkeypatch.setattr("scp.autofix.runner.get_autofix_engine", lambda *a, **kw: engine)

    bug1 = BugReport(
        file="scp/test_cd1.py",
        line=10,
        bug_type="PossiblyUndefinedName",
        description="F821 undefined name 'x'",
        suggested_fix="",
        tier=BugTier.TIER_1_AUTO_FIX,
    )
    bug2 = BugReport(
        file="scp/test_cd2.py",
        line=20,
        bug_type="PossiblyUndefinedName",
        description="F821 undefined name 'y'",
        suggested_fix="",
        tier=BugTier.TIER_1_AUTO_FIX,
    )

    # Pre-record cooldown for bug1
    engine.record_cooldown(bug1, reason="prior test cooldown")

    why_calls = []

    class MockWhyGate:
        def gate(self, action_type, action_desc, context="", **kwargs):
            why_calls.append(action_desc)
            if "test_cd2" in action_desc:
                # WHY gate rejects bug2 as false positive
                return WhyResult(
                    decision=WhyDecision.REJECT,
                    falsification_reason="names ARE defined dynamically",
                )
            return WhyResult(decision=WhyDecision.ALLOW)

    monkeypatch.setattr("scp.meta.why_gate.get_why_gate", lambda *a, **kw: MockWhyGate())

    # Run runner with both bugs
    summary = run_once(bugs=[bug1, bug2], max_bugs=5)

    # bug1 should NOT have been evaluated by WHY-gate because it was in cooldown!
    assert not any("test_cd1" in call for call in why_calls), "Cooldown bug should be skipped before WHY gate"

    # bug2 was evaluated and rejected by WHY-gate, so it should now be in cooldown
    in_cd, reason = engine.is_in_cooldown(bug2)
    assert in_cd is True
    assert "cooldown" in reason

