"""Tests for Maker 5: AutoFix Diagnostic Retention, Callgraph Injection, Context Caps & Gateway Preferred Provider.

Governed by SCP DNA #1–#29:
- DNA #2 (Reality > Model): Verifies real multi-round context preservation, real callgraph injection,
  real relative import discovery, and real preferred provider gateway reordering.
- DNA #22 (PASS != TRUE) & Anti-Placebo: Positive verification that code context and callgraph
  are retained across rounds; Negative verification that caps prevent runaway prompt bloat.
- FA-01 to FA-13: Zero mock of authority, fail-closed verification.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
import pytest

from scp.autofix.classifier import BugReport, BugTier
from scp.autofix.llm_fix import (
    _build_fix_prompt,
    generate_fix_for_bug,
    process_bug_with_llm,
)
from scp.autofix.llm_fix_parts.generate_fix_for_bug import (
    _collect_related_files_context,
    _collect_test_context,
)
from scp.autofix.llm_fix_parts.process_bug_with_llm import _safe_call_generate_fix_for_bug
from scp.llm_gateway.client import LLMGateway, chat_sync


@pytest.fixture
def test_env(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("SCP_DEV_MODE", "1")
    monkeypatch.setenv("SCP_WHY_LLM_ENABLED", "0")
    monkeypatch.setenv("SCP_LLM_FIX_CACHE_DISABLED", "1")
    return tmp_path


def test_round_2_and_3_conversation_history_preserves_code_context(test_env, monkeypatch):
    """Blocker 6: Round 2 and 3 conversation history must preserve full code context."""
    src_file = test_env / "calc.py"
    src_file.write_text("def compute(x):\n    # Core business logic\n    return x * 1\n", encoding="utf-8")

    bug = BugReport(
        file=str(src_file),
        line=3,
        bug_type="ComputeLogic",
        description="Return x * 2 instead of x * 1",
        suggested_fix="",
        tier=BugTier.TIER_1_AUTO_FIX,
    )

    captured_histories = []

    def mock_gen_fix(b, feedback=None, previous_patch=None, conversation_history=None):
        if conversation_history:
            captured_histories.append([dict(m) for m in conversation_history])
        rnd = len(captured_histories) + 1
        if rnd == 1:
            return "<<<<<<< SEARCH\n    return x * 1\n=======\n    return x * 0\n>>>>>>> REPLACE"
        elif rnd == 2:
            return "<<<<<<< SEARCH\n    return x * 1\n=======\n    return x * -1\n>>>>>>> REPLACE"
        else:
            return "<<<<<<< SEARCH\n    return x * 1\n=======\n    return x * 2\n>>>>>>> REPLACE"

    class _MockEngine:
        def __init__(self):
            self.calls = 0
        def process_bug(self, b):
            self.calls += 1
            if self.calls < 3:
                return {"action": "skipped", "reason": f"Gate fail on round {self.calls}"}
            return {"action": "fixed", "reason": "All verifications passed"}

    monkeypatch.setattr("scp.autofix.llm_fix_parts.process_bug_with_llm.generate_fix_for_bug", mock_gen_fix)

    result = process_bug_with_llm(bug, _MockEngine(), allow_llm=True)

    assert result["action"] == "fixed"
    assert result["refinement_rounds"] == 3
    assert len(captured_histories) == 2  # Round 2 and Round 3 calls

    # Verify Round 2 initial message contains non-empty code context
    r2_hist = captured_histories[0]
    assert r2_hist[0]["role"] == "user"
    init_content = r2_hist[0]["content"]
    assert "Code context:" in init_content
    assert "def compute(x):" in init_content
    assert "return x * 1" in init_content
    assert "# line 3 >>>" in init_content  # Verifies line marker is present!


def test_callgraph_context_wired_into_prompt_and_generate_fix(test_env, monkeypatch):
    """Blocker 7: callgraph_context is formatted and appended to prompt and passed from generate_fix."""
    src_file = test_env / "node.py"
    src_file.write_text("def run():\n    return None\n", encoding="utf-8")

    bug = BugReport(
        file=str(src_file),
        line=2,
        bug_type="CallGraphBug",
        description="Verify callgraph section",
        suggested_fix="",
        tier=BugTier.TIER_1_AUTO_FIX,
        callgraph_context="Node.run -> Service.execute -> DB.commit",
    )

    # 1. Direct prompt builder check
    prompt = _build_fix_prompt(
        bug,
        code_context="def run():\n    return None",
        callgraph_context=bug.callgraph_context,
    )
    assert "[CALLGRAPH & STRUCTURAL DEPENDENCIES]" in prompt
    assert "Node.run -> Service.execute -> DB.commit" in prompt

    # 2. generate_fix_for_bug wiring check
    captured = []
    def mock_smart_llm(prompt="", bug_type="", max_tokens=4000, messages=None):
        captured.append(prompt)
        return "<<<<<<< SEARCH\n    return None\n=======\n    return True\n>>>>>>> REPLACE"

    monkeypatch.setattr("scp.autofix.llm_fix_parts.generate_fix_for_bug._call_smart_llm", mock_smart_llm)
    monkeypatch.setattr("scp.autofix.llm_fix_parts.generate_fix_for_bug._check_rate_limit", lambda: True)

    fix = generate_fix_for_bug(bug)
    assert fix is not None
    assert len(captured) == 1
    assert "[CALLGRAPH & STRUCTURAL DEPENDENCIES]" in captured[0]
    assert "Node.run -> Service.execute -> DB.commit" in captured[0]


def test_relative_and_direct_import_discovery(test_env):
    """Blocker 7: _collect_related_files_context discovers relative and direct scp.* imports."""
    pkg_dir = test_env / "pkg"
    pkg_dir.mkdir(parents=True, exist_ok=True)

    sibling = pkg_dir / "sibling_mod.py"
    sibling.write_text("SIBLING_VALUE = 42\n", encoding="utf-8")

    src = pkg_dir / "main_mod.py"
    src.write_text("from .sibling_mod import SIBLING_VALUE\n", encoding="utf-8")

    bug = BugReport(
        file=str(src),
        line=1,
        bug_type="ImportBug",
        description="Test relative import discovery",
        suggested_fix="",
        tier=BugTier.TIER_1_AUTO_FIX,
    )

    ctx = _collect_related_files_context(bug)
    assert "sibling_mod.py" in ctx
    assert "SIBLING_VALUE = 42" in ctx

    # Also directly verify _walk_imports helper
    from scp.autofix.llm_fix import _walk_imports
    discovered = _walk_imports(src)
    assert any("sibling_mod.py" in str(d) for d in discovered)


def test_gateway_preferred_provider_ordering():
    """Blocker 8: preferred_provider places specified provider at index 0 and invokes it first without monkeypatching _provider_chain."""
    gw = LLMGateway()

    class _MockProvider:
        def __init__(self, name: str):
            self.PROVIDER_NAME = name
            self.enabled = True
            self._breaker = type("_B", (), {"is_open": lambda self: False})()
        async def chat(self, *a, **kw):
            return f"response_from_{self.PROVIDER_NAME}", f"{self.PROVIDER_NAME}:test"

    p_openrouter = _MockProvider("openrouter")
    p_gemini = _MockProvider("gemini_compat")
    p_deepseek = _MockProvider("deepseek")

    gw.openrouter_autofix = p_openrouter
    gw._extra_providers["autofix"] = [p_gemini, p_deepseek]

    # Verify real _provider_chain logic places preferred provider at index 0
    chain_ds = gw._provider_chain("autofix", preferred_provider="deepseek")
    assert chain_ds[0].PROVIDER_NAME == "deepseek"

    chain_gem = gw._provider_chain("autofix", preferred_provider="gemini")
    assert chain_gem[0].PROVIDER_NAME == "gemini_compat"

    # 1. Calling chat with preferred_provider="deepseek"
    ans, prov = asyncio.run(gw.chat("hello", task="autofix", preferred_provider="deepseek"))
    assert ans == "response_from_deepseek"
    assert prov == "deepseek:test"

    # 2. Calling chat with preferred_provider="gemini" (should resolve to gemini_compat)
    ans2, prov2 = asyncio.run(gw.chat("hello", task="autofix", preferred_provider="gemini"))
    assert ans2 == "response_from_gemini_compat"
    assert prov2 == "gemini_compat:test"


def test_token_and_character_caps_enforcement(test_env):
    """Token/Character Budget Caps: 4000 char cap on snippet/patch, 8000 char cap on test output."""
    dep_file = test_env / "giant_dep.py"
    dep_file.write_text("X = " + ("a" * 10000) + "\n", encoding="utf-8")

    bug = BugReport(
        file=str(dep_file),
        line=1,
        bug_type="CapBug",
        description="Test budget caps",
        suggested_fix="",
        tier=BugTier.TIER_1_AUTO_FIX,
        related_files=[str(dep_file)],
        test_output="E" * 15000,
    )

    # Snippet cap
    # Create consumer
    consumer = test_env / "consumer.py"
    consumer.write_text("import giant_dep\n", encoding="utf-8")
    bug.file = str(consumer)
    ctx = _collect_related_files_context(bug)
    assert "[truncated to 4000 chars]" in ctx

    # Test output cap
    t_ctx = _collect_test_context(bug)
    assert "[truncated to 8000 chars]" in t_ctx

    # Previous patch cap
    huge_patch = "P" * 10000
    prompt = _build_fix_prompt(bug, code_context="pass", previous_patch=huge_patch)
    assert "[truncated to 4000 chars]" in prompt


def test_safe_call_does_not_mask_real_provider_error():
    """Anti-Placebo: _safe_call_generate_fix_for_bug does not swallow or blindly re-call on real provider errors."""
    bug = BugReport(
        file="f.py",
        line=1,
        bug_type="B",
        description="D",
        suggested_fix="",
        tier=BugTier.TIER_1_AUTO_FIX,
    )

    calls = 0
    def failing_fn(b, feedback=None):
        nonlocal calls
        calls += 1
        raise RuntimeError("Gateway provider network error")

    with pytest.raises(RuntimeError, match="Gateway provider network error"):
        _safe_call_generate_fix_for_bug(failing_fn, bug, feedback="critique")

    # Verify it was called exactly once, not retried/re-called blindly
    assert calls == 1
