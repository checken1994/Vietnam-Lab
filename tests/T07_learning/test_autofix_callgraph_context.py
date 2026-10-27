"""Tests for AutoFix Multi-File and Callgraph Context Probing.

Governed by SCP DNA #1–#29:
- DNA #2 (Reality > Model): Verifies real multi-file context discovery from AST imports
  and explicit related_files, proving cross-file alignment in the prompt.
- DNA #9 (No Harm / Blast Radius Containment): Verifies strict LOC limits (max 80 lines
  per file) and file limits (max 2 files) to prevent prompt bloat and runaway blast radius.
- DNA #22 (PASS != TRUE) & Anti-Placebo: Positive verification that related files appear
  in prompt; Negative verification that non-existent files, self-references, and syntax
  errors fail-closed without corrupting the prompt or crashing.
- FA-04 Zero-Mock: Uses concrete files and monkeypatch, zero unittest.mock.
"""
from __future__ import annotations

import os
from pathlib import Path
import pytest

from scp.autofix.classifier import BugReport, BugTier
from scp.autofix.llm_fix import (
    _build_fix_prompt,
    generate_fix_for_bug,
)
from scp.autofix.llm_fix_parts.generate_fix_for_bug import _collect_related_files_context


@pytest.fixture
def autofix_workspace(tmp_path, monkeypatch):
    """Hermetic test workspace isolating environment and disabling persistent cache."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("SCP_DEV_MODE", "1")
    monkeypatch.setenv("SCP_WHY_LLM_ENABLED", "0")
    monkeypatch.setenv("SCP_LLM_FIX_CACHE_DISABLED", "1")
    return tmp_path


def test_collect_related_files_explicit_discovery(autofix_workspace):
    """1. Explicit bug.related_files are discovered, read, and properly formatted."""
    dep_file = autofix_workspace / "shared_models.py"
    dep_file.write_text(
        "class Order:\n    id: int\n    status: str = 'pending'\n",
        encoding="utf-8",
    )

    src_file = autofix_workspace / "order_service.py"
    src_file.write_text(
        "def process_order():\n    return False\n",
        encoding="utf-8",
    )

    bug = BugReport(
        file=str(src_file),
        line=2,
        bug_type="BusinessLogic",
        description="Order not transitioning",
        suggested_fix="",
        tier=BugTier.TIER_1_AUTO_FIX,
        related_files=[str(dep_file)],
    )

    context = _collect_related_files_context(bug)

    assert f"[RELATED DEPENDENCY MODULE: {dep_file.name} ({dep_file})]" in context
    assert "# Context definitions for cross-file alignment:" in context
    assert "class Order:" in context
    assert "status: str = 'pending'" in context


def test_collect_related_files_local_import_discovery(autofix_workspace, monkeypatch):
    """2. Local scp.* imports are discovered via AST when explicit related_files is empty."""
    # Create a dummy scp package inside the workspace to simulate repo root imports
    scp_dir = autofix_workspace / "scp" / "workflow"
    scp_dir.mkdir(parents=True, exist_ok=True)
    dep_file = scp_dir / "state_types.py"
    dep_file.write_text(
        "STATE_INIT = 'INIT'\nSTATE_ACTIVE = 'ACTIVE'\nSTATE_DONE = 'DONE'\n",
        encoding="utf-8",
    )

    src_file = scp_dir / "worker.py"
    src_file.write_text(
        "from scp.workflow.state_types import STATE_INIT\n\ndef run():\n    return STATE_INIT\n",
        encoding="utf-8",
    )

    # Change working directory so relative Path('scp/workflow/state_types.py') exists
    monkeypatch.chdir(autofix_workspace)

    bug = BugReport(
        file="scp/workflow/worker.py",
        line=3,
        bug_type="StateTransitionError",
        description="Worker in wrong state",
        suggested_fix="",
        tier=BugTier.TIER_1_AUTO_FIX,
        related_files=[],
    )

    context = _collect_related_files_context(bug)

    assert "[RELATED DEPENDENCY MODULE: state_types.py (scp/workflow/state_types.py)]" in context
    assert "STATE_INIT = 'INIT'" in context
    assert "STATE_DONE = 'DONE'" in context


def test_loc_truncation_strictly_top_80_lines(autofix_workspace):
    """3. Files with > 80 lines are strictly truncated to the top 80 lines."""
    dep_file = autofix_workspace / "large_module.py"
    lines = [f"LINE_{i:03d} = {i}" for i in range(1, 151)]  # 150 lines
    dep_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    src_file = autofix_workspace / "consumer.py"
    src_file.write_text("import large_module\n", encoding="utf-8")

    bug = BugReport(
        file=str(src_file),
        line=1,
        bug_type="ConfigError",
        description="Missing config",
        suggested_fix="",
        tier=BugTier.TIER_1_AUTO_FIX,
        related_files=[str(dep_file)],
    )

    context = _collect_related_files_context(bug)

    # Lines 1 to 80 must be present
    assert "LINE_001 = 1" in context
    assert "LINE_080 = 80" in context

    # Lines 81 through 150 must NOT be present
    assert "LINE_081 = 81" not in context
    assert "LINE_150 = 150" not in context


def test_max_files_cap_at_two(autofix_workspace):
    """4. At most 2 related files are processed, even if more are provided."""
    dep1 = autofix_workspace / "dep1.py"
    dep1.write_text("DEP1_DATA = 100\n", encoding="utf-8")
    dep2 = autofix_workspace / "dep2.py"
    dep2.write_text("DEP2_DATA = 200\n", encoding="utf-8")
    dep3 = autofix_workspace / "dep3.py"
    dep3.write_text("DEP3_DATA = 300\n", encoding="utf-8")
    dep4 = autofix_workspace / "dep4.py"
    dep4.write_text("DEP4_DATA = 400\n", encoding="utf-8")

    src_file = autofix_workspace / "app.py"
    src_file.write_text("pass\n", encoding="utf-8")

    bug = BugReport(
        file=str(src_file),
        line=1,
        bug_type="ScopeLeak",
        description="Too many deps",
        suggested_fix="",
        tier=BugTier.TIER_1_AUTO_FIX,
        related_files=[str(dep1), str(dep2), str(dep3), str(dep4)],
    )

    context = _collect_related_files_context(bug)

    assert "DEP1_DATA = 100" in context
    assert "DEP2_DATA = 200" in context
    # dep3 and dep4 must be excluded by the max_files=2 cap
    assert "DEP3_DATA = 300" not in context
    assert "DEP4_DATA = 400" not in context
    assert context.count("[RELATED DEPENDENCY MODULE:") == 2


def test_duplicate_explicit_files_deduplicated(autofix_workspace):
    """4b. Duplicate files in related_files are deduplicated without consuming file slots."""
    dep1 = autofix_workspace / "dep1.py"
    dep1.write_text("DEP1_DATA = 100\n", encoding="utf-8")
    dep2 = autofix_workspace / "dep2.py"
    dep2.write_text("DEP2_DATA = 200\n", encoding="utf-8")

    src_file = autofix_workspace / "app.py"
    src_file.write_text("pass\n", encoding="utf-8")

    bug = BugReport(
        file=str(src_file),
        line=1,
        bug_type="ScopeLeak",
        description="Duplicate deps",
        suggested_fix="",
        tier=BugTier.TIER_1_AUTO_FIX,
        # dep1 repeated twice, then dep2
        related_files=[str(dep1), str(dep1), str(dep2)],
    )

    context = _collect_related_files_context(bug)

    # Both dep1 and dep2 must be included because dep1 is deduplicated
    assert "DEP1_DATA = 100" in context
    assert "DEP2_DATA = 200" in context
    assert context.count("[RELATED DEPENDENCY MODULE:") == 2


def test_self_reference_and_missing_files_excluded(autofix_workspace):
    """5. Self-referencing file is excluded and non-existent files are safely ignored."""
    src_file = autofix_workspace / "main.py"
    src_file.write_text("def run():\n    pass\n", encoding="utf-8")

    non_existent = autofix_workspace / "phantom_dep.py"

    valid_dep = autofix_workspace / "valid_dep.py"
    valid_dep.write_text("VALID_TOKEN = 'secret_abc'\n", encoding="utf-8")

    bug = BugReport(
        file=str(src_file),
        line=1,
        bug_type="DependencyError",
        description="Check filtering",
        suggested_fix="",
        tier=BugTier.TIER_1_AUTO_FIX,
        related_files=[
            str(src_file),       # Self-reference: must be filtered
            str(non_existent),   # Non-existent: must be safely skipped
            str(valid_dep),      # Valid: must be included
        ],
    )

    context = _collect_related_files_context(bug)

    assert "VALID_TOKEN = 'secret_abc'" in context
    assert str(src_file.name) not in context
    assert "phantom_dep" not in context
    assert context.count("[RELATED DEPENDENCY MODULE:") == 1


def test_anti_placebo_syntax_error_in_source_graceful_fail_closed(autofix_workspace):
    """6. Negative test: SyntaxError in source file does not crash import discovery."""
    broken_src = autofix_workspace / "broken_syntax.py"
    broken_src.write_text("def invalid syntax :::\n", encoding="utf-8")

    bug = BugReport(
        file=str(broken_src),
        line=1,
        bug_type="SyntaxErrorBug",
        description="Syntax error in source",
        suggested_fix="",
        tier=BugTier.TIER_1_AUTO_FIX,
        related_files=[],
    )

    # Must return empty string without raising SyntaxError
    context = _collect_related_files_context(bug)
    assert context == ""


def test_build_fix_prompt_injects_related_files_context(autofix_workspace):
    """7. _build_fix_prompt formats and injects related files context."""
    src_file = autofix_workspace / "service.py"
    src_file.write_text("def handle():\n    return False\n", encoding="utf-8")

    bug = BugReport(
        file=str(src_file),
        line=2,
        bug_type="ServiceError",
        description="Handler returning False",
        suggested_fix="",
        tier=BugTier.TIER_1_AUTO_FIX,
    )

    related_ctx = (
        "[RELATED DEPENDENCY MODULE: config.py (config.py)]\n"
        "# Context definitions for cross-file alignment:\n"
        "DEBUG_MODE = True"
    )

    prompt = _build_fix_prompt(
        bug,
        code_context="def handle():\n# line 2 >>>\n    return False",
        related_files_context=related_ctx,
    )

    assert "[RELATED DEPENDENCY MODULE: config.py (config.py)]" in prompt
    assert "# Context definitions for cross-file alignment:" in prompt
    assert "DEBUG_MODE = True" in prompt
    assert "ServiceError" in prompt


def test_live_multi_file_scenario_in_generate_fix_for_bug(autofix_workspace, monkeypatch):
    """8. End-to-end multi-file dependency scenario: prompt passed to LLM includes related files."""
    schema_file = autofix_workspace / "schema.py"
    schema_file.write_text(
        "class UserAccount:\n    username: str\n    is_verified: bool = False\n",
        encoding="utf-8",
    )

    handler_file = autofix_workspace / "account_handler.py"
    handler_file.write_text(
        "import schema\n\ndef activate_user(user):\n    user.is_verified = False\n    return user\n",
        encoding="utf-8",
    )

    bug = BugReport(
        file=str(handler_file),
        line=4,
        bug_type="LogicError",
        description="activate_user sets is_verified to False instead of True",
        suggested_fix="user.is_verified = True",
        tier=BugTier.TIER_1_AUTO_FIX,
        related_files=[str(schema_file)],
    )

    captured_prompts = []

    def mock_smart_llm(prompt="", bug_type="", max_tokens=4000, messages=None):
        captured_prompts.append(prompt)
        return (
            "<<<<<<< SEARCH\n"
            "    user.is_verified = False\n"
            "=======\n"
            "    user.is_verified = True\n"
            ">>>>>>> REPLACE"
        )

    monkeypatch.setattr(
        "scp.autofix.llm_fix_parts.generate_fix_for_bug._call_smart_llm",
        mock_smart_llm,
    )
    monkeypatch.setattr(
        "scp.autofix.llm_fix_parts.generate_fix_for_bug._check_rate_limit",
        lambda: True,
    )

    fix = generate_fix_for_bug(bug)

    assert fix is not None
    assert "user.is_verified = True" in fix
    assert len(captured_prompts) == 1

    prompt = captured_prompts[0]
    # Verify related file snippet was injected into the prompt
    assert f"[RELATED DEPENDENCY MODULE: {schema_file.name} ({schema_file})]" in prompt
    assert "class UserAccount:" in prompt
    assert "is_verified: bool = False" in prompt
    # Verify target file context is present
    assert "def activate_user(user):" in prompt
