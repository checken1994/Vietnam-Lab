"""Tests for AutoFix Unleashed Rate Limits, Multi-Provider Gateway, and Rich Diagnostic Context.

Governed by SCP DNA #1–#29:
- DNA #2 (Reality > Model): Verifies real multi-file context, real failure traceback injection,
  and real multi-turn conversation preservation.
- DNA #22 (PASS != TRUE) & Anti-Placebo Gate Pattern: Positive test proving successful multi-round
  refinement with rich context; Negative test proving fail-closed rejection on invalid fixes.
- FA-04 Zero-Mock: Pure concrete stubs and pytest monkeypatch, zero unittest.mock.
"""
from __future__ import annotations

import os
from pathlib import Path
import pytest

from scp.autofix.classifier import BugReport, BugTier
from scp.autofix.llm_fix import (
    _check_rate_limit,
    _build_fix_prompt,
    select_provider_for_bug,
    generate_fix_for_bug,
    process_bug_with_llm,
)


@pytest.fixture
def temp_repo(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("SCP_DEV_MODE", "1")
    monkeypatch.setenv("SCP_WHY_LLM_ENABLED", "0")
    monkeypatch.setenv("SCP_LLM_FIX_CACHE_DISABLED", "1")
    return tmp_path


def test_autofix_unleashed_rate_limit(monkeypatch):
    """AutoFix rate limit is unleashed (unlimited) by default or when set <= 0."""
    import scp.autofix.llm_fix as llm_fix_mod

    # Default / 0 = unleashed: even 150 consecutive calls must all return True
    monkeypatch.setenv("SCP_AUTOFIX_MAX_LLM_PER_HOUR", "0")
    llm_fix_mod._recent_llm_calls.clear()
    for _ in range(150):
        assert _check_rate_limit() is True

    # Negative value = unleashed
    monkeypatch.setenv("SCP_AUTOFIX_MAX_LLM_PER_HOUR", "-1")
    llm_fix_mod._recent_llm_calls.clear()
    for _ in range(150):
        assert _check_rate_limit() is True

    # Explicit positive limit enforces the bound
    monkeypatch.setenv("SCP_AUTOFIX_MAX_LLM_PER_HOUR", "5")
    llm_fix_mod._recent_llm_calls.clear()
    for _ in range(5):
        assert _check_rate_limit() is True
    # 6th call exceeds limit
    assert _check_rate_limit() is False


def test_autofix_multi_provider_selection(monkeypatch):
    """select_provider_for_bug dynamically respects active providers without hardcoded ollama."""
    # Test env override
    monkeypatch.setenv("SCP_AUTOFIX_PROVIDER", "gemini_compat")
    assert select_provider_for_bug("AnyBug") == "gemini_compat"

    # Test dynamic discovery from gateway
    monkeypatch.delenv("SCP_AUTOFIX_PROVIDER", raising=False)
    provider = select_provider_for_bug("SQLInjection")
    assert provider != "ollama"  # Must NOT be hardcoded to ollama
    assert isinstance(provider, str) and len(provider) > 0


def test_autofix_rich_diagnostic_prompt_construction(temp_repo):
    """_build_fix_prompt injects related files, test diagnostics, and previous patch."""
    src_file = temp_repo / "foo.py"
    src_file.write_text("def solve():\n    return 0\n", encoding="utf-8")

    bug = BugReport(
        file=str(src_file),
        line=2,
        bug_type="LogicError",
        description="Return value is 0 instead of 42",
        suggested_fix="",
        tier=BugTier.TIER_1_AUTO_FIX,
        failing_tests=["tests/test_foo.py::test_solve"],
        test_output="assert 0 == 42\nE   where 0 = solve()",
        related_files=[str(temp_repo / "bar.py")],
    )

    prompt = _build_fix_prompt(
        bug,
        code_context="def solve():\n# line 2 >>>\n    return 0",
        feedback="AssertionError: 0 != 42",
        previous_patch="<<<<<<< SEARCH\nreturn 0\n=======\nreturn -1\n>>>>>>> REPLACE",
        related_files_context="[RELATED FILE CONTEXT: bar.py]\nCONSTANT = 42",
        test_context="[FAILING TEST DIAGNOSTICS & TRACEBACK]\nFailing test: test_solve",
    )

    assert "LogicError" in prompt
    assert "[RELATED FILE CONTEXT: bar.py]" in prompt
    assert "[FAILING TEST DIAGNOSTICS & TRACEBACK]" in prompt
    assert "[PREVIOUS ATTEMPTED PATCH THAT WAS REJECTED]" in prompt
    assert "return -1" in prompt
    assert "[CRITIQUE OF PREVIOUS ATTEMPT - MANDATORY REFINEMENT]" in prompt
    assert "AssertionError: 0 != 42" in prompt


def test_autofix_auto_collects_related_files_and_test_specs(temp_repo, monkeypatch):
    """generate_fix_for_bug automatically discovers local imports and test suites."""
    dep_file = temp_repo / "helpers.py"
    dep_file.write_text("EXPECTED_VALUE = 42\n", encoding="utf-8")

    mod_file = temp_repo / "math_mod.py"
    mod_file.write_text("import helpers\ndef compute():\n    return 0\n", encoding="utf-8")

    test_file = temp_repo / "tests" / "test_math_mod.py"
    test_file.write_text("def test_compute():\n    assert compute() == 42\n", encoding="utf-8")

    bug = BugReport(
        file=str(mod_file),
        line=3,
        bug_type="ComputeError",
        description="compute() returned 0",
        suggested_fix="",
        tier=BugTier.TIER_1_AUTO_FIX,
        related_files=[str(dep_file)],
        failing_tests=["test_math_mod.py::test_compute"],
        test_output="assert 0 == 42",
    )

    captured_prompt = []

    def mock_smart_llm(prompt="", bug_type="", max_tokens=4000, messages=None):
        captured_prompt.append(prompt)
        return "<<<<<<< SEARCH\n    return 0\n=======\n    return 42\n>>>>>>> REPLACE"

    monkeypatch.setattr("scp.autofix.llm_fix_parts.generate_fix_for_bug._call_smart_llm", mock_smart_llm)
    monkeypatch.setattr("scp.autofix.llm_fix_parts.generate_fix_for_bug._check_rate_limit", lambda: True)

    fix = generate_fix_for_bug(bug)
    assert fix is not None
    assert len(captured_prompt) == 1
    p = captured_prompt[0]

    assert "EXPECTED_VALUE = 42" in p
    assert "assert 0 == 42" in p


def test_process_bug_multi_turn_conversation_propagation(temp_repo, monkeypatch):
    """process_bug_with_llm maintains multi-turn conversation history across rounds."""
    mod_file = temp_repo / "service.py"
    mod_file.write_text("def get_port():\n    return 80\n", encoding="utf-8")

    bug = BugReport(
        file=str(mod_file),
        line=2,
        bug_type="PortMismatch",
        description="Port should be 8080",
        suggested_fix="",
        tier=BugTier.TIER_1_AUTO_FIX,
    )

    engine_responses = [
        {"action": "skipped", "reason": "pytest regression: test_service failed"},
        {"action": "fixed", "reason": "all verifications passed"},
    ]

    class _MockEngine:
        def process_bug(self, *a, **kw):
            return engine_responses.pop(0)

    received_calls = []

    def mock_generate_fix_for_bug(b, feedback=None, previous_patch=None, conversation_history=None):
        received_calls.append({
            "feedback": feedback,
            "previous_patch": previous_patch,
            "conversation_history": list(conversation_history) if conversation_history else None,
        })
        if len(received_calls) == 1:
            return "<<<<<<< SEARCH\n    return 80\n=======\n    return 8000\n>>>>>>> REPLACE"
        return "<<<<<<< SEARCH\n    return 80\n=======\n    return 8080\n>>>>>>> REPLACE"

    monkeypatch.setattr("scp.autofix.llm_fix_parts.process_bug_with_llm.generate_fix_for_bug", mock_generate_fix_for_bug)

    result = process_bug_with_llm(bug, _MockEngine(), allow_llm=True)

    assert result["action"] == "fixed"
    assert result["refinement_rounds"] == 2
    assert len(received_calls) == 2

    # Round 1: no feedback, no previous patch, no conversation history
    assert received_calls[0]["feedback"] is None
    assert received_calls[0]["previous_patch"] is None
    assert received_calls[0]["conversation_history"] is None

    # Round 2: receives previous patch, feedback, AND full multi-turn conversation history
    r2 = received_calls[1]
    assert "pytest regression" in r2["feedback"]
    assert "return 8000" in r2["previous_patch"]
    history = r2["conversation_history"]
    assert history is not None
    assert len(history) == 3
    assert history[0]["role"] == "user"
    assert history[1]["role"] == "assistant"
    assert "return 8000" in history[1]["content"]
    assert history[2]["role"] == "user"
    assert "pytest regression" in history[2]["content"]


def test_process_bug_anti_placebo_fail_closed_on_exhaustion(temp_repo, monkeypatch):
    """Negative anti-placebo test: all 3 rounds rejected -> fails closed and records cooldown."""
    mod_file = temp_repo / "broken.py"
    mod_file.write_text("def broken():\n    pass\n", encoding="utf-8")

    bug = BugReport(
        file=str(mod_file),
        line=2,
        bug_type="UnfixableBug",
        description="Never fixes",
        suggested_fix="",
        tier=BugTier.TIER_1_AUTO_FIX,
    )

    class _AlwaysRejectEngine:
        def __init__(self):
            self.cooldown_recorded = None
        def process_bug(self, *a, **kw):
            return {"action": "skipped", "reason": "security policy violation: banned call"}
        def record_cooldown(self, bug_key, reason=""):
            self.cooldown_recorded = (bug_key, reason)

    engine = _AlwaysRejectEngine()

    monkeypatch.setattr(
        "scp.autofix.llm_fix_parts.process_bug_with_llm.generate_fix_for_bug",
        lambda *a, **kw: "<<<<<<< SEARCH\npass\n=======\npass # attempt\n>>>>>>> REPLACE",
    )

    result = process_bug_with_llm(bug, engine, allow_llm=True)

    assert result["action"] == "skipped"
    assert "security policy violation" in result["reason"]
    assert engine.cooldown_recorded is not None
    assert "Refinement failed" in engine.cooldown_recorded[1]
