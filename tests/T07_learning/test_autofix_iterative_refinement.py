"""Tests for Autofix Multi-Turn Iterative Refinement & Autonomous Goal/Learning Schedule.

Governed by SCP DNA #1–#29:
- Anti-Placebo Gate Pattern (FA-11/FA-12): Verifies real multi-turn critique loop
  and fail-closed quarantine after 3 rounds.
- Invariant preservation: Verifies safety invariants are never dropped.
"""
from __future__ import annotations

import ast
import asyncio
import os
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import scp.autofix.llm_fix as llm_fix
from scp.autofix.classifier import BugReport, BugTier
from scp.autofix.engine import AutoFixEngine
from scp.core.autonomous_cycle import run_autonomous_cycle
from scp.core.code_evolution_agent import CodeEvolutionAgent
from scp.governance import DriftDecision, DriftGuard
from scp.meta.why_gate import WhyDecision, WhyResult


@pytest.fixture
def temp_repo(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    pending_dir = tmp_path / "pending_fixes"
    pending_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("SCP_CAPABILITY_SECRET", "test-secret-key-32-bytes-long-123456")
    monkeypatch.setenv("SCP_DEV_MODE", "1")
    monkeypatch.setenv("SCP_WHY_LLM_ENABLED", "0")
    return tmp_path


def test_autofix_iterative_refinement_driftguard_recovery(temp_repo, monkeypatch):
    """Round 1 is blocked by DriftGuard; verbatim critique is fed to Round 2 which succeeds."""
    test_file = temp_repo / "sample_module.py"
    test_file.write_text("def process(x):\n    # TODO: fix\n    return x\n", encoding="utf-8")

    agent = CodeEvolutionAgent()
    agent.scp_dir = temp_repo
    agent.tests_dir = temp_repo
    agent.log_file = temp_repo / "data" / "evolution_log.jsonl"

    bug = {
        "file": "sample_module.py",
        "line": 2,
        "pattern": "TodoComment",
        "severity": "LOW",
        "why": "Unfinished TODO",
        "fix_hint": "Implement check",
        "match": "# TODO: fix",
    }

    call_count = 0
    received_feedbacks = []

    async def mock_generate_fix(b, feedback=None):
        nonlocal call_count
        call_count += 1
        received_feedbacks.append(feedback)
        if call_count == 1:
            # Round 1: bad patch that produces syntax error
            return "<<<<<<< SEARCH\n    # TODO: fix\n=======\n    return def syntax error\n>>>>>>> REPLACE"
        # Round 2: valid, safe patch
        return "<<<<<<< SEARCH\n    # TODO: fix\n=======\n    # Verified safe fix\n>>>>>>> REPLACE"

    agent._generate_fix = mock_generate_fix
    agent._scan_bugs = MagicMock(return_value=[bug])
    agent._run_tests = MagicMock(return_value={"passed": True, "output": "OK", "failures": ""})

    with patch("scp.core.code_evolution_agent.SCP_ROOT", temp_repo):
        result = asyncio.run(agent.run_cycle())

    assert call_count == 2
    assert len(received_feedbacks) == 2
    assert received_feedbacks[0] is None
    assert "SYNTAX_ERROR" in received_feedbacks[1]
    assert result["status"] in ("fix_applied", "fix_applied_pending_commit")
    assert "Verified safe fix" in test_file.read_text(encoding="utf-8")


def test_autofix_iterative_refinement_why_gate_recovery(temp_repo, monkeypatch):
    """Round 1 is blocked by WHY-Gate; Round 2 receives critique and generates compliant patch."""
    test_file = temp_repo / "guard_module.py"
    test_file.write_text("def guarded():\n    return 42\n", encoding="utf-8")

    agent = CodeEvolutionAgent()
    agent.scp_dir = temp_repo
    agent.tests_dir = temp_repo
    agent.log_file = temp_repo / "data" / "evolution_log.jsonl"

    bug = {
        "file": "guard_module.py",
        "line": 1,
        "pattern": "MockBug",
        "severity": "MEDIUM",
        "why": "Missing invariant",
        "fix_hint": "Add invariant",
        "match": "return 42",
    }

    call_count = 0
    received_feedbacks = []

    async def mock_generate_fix(b, feedback=None):
        nonlocal call_count
        call_count += 1
        received_feedbacks.append(feedback)
        if call_count == 1:
            return "<<<<<<< SEARCH\n    return 42\n=======\n    # deleted guard\n    return 0\n>>>>>>> REPLACE"
        return "<<<<<<< SEARCH\n    return 42\n=======\n    assert True # preserved guard\n    return 42\n>>>>>>> REPLACE"

    agent._generate_fix = mock_generate_fix
    agent._scan_bugs = MagicMock(return_value=[bug])
    agent._run_tests = MagicMock(return_value={"passed": True, "output": "OK", "failures": ""})

    # Mock WHY-Gate: blocks round 1, allows round 2
    mock_why_gate = MagicMock()
    mock_why_gate.gate.side_effect = [
        WhyResult(decision=WhyDecision.REJECT, falsification_reason="Deleted critical invariant"),
        WhyResult(decision=WhyDecision.ALLOW, falsification_reason=""),
    ]

    with patch("scp.core.code_evolution_agent.SCP_ROOT", temp_repo), \
         patch("scp.meta.why_gate.get_why_gate", return_value=mock_why_gate):
        result = asyncio.run(agent.run_cycle())

    assert call_count == 2
    assert "FALSIFICATION: WHY-Gate blocked patch: Deleted critical invariant" in received_feedbacks[1]
    assert result["status"] in ("fix_applied", "fix_applied_pending_commit")


def test_autofix_refinement_exhaustion_quarantines_failclosed(temp_repo, monkeypatch):
    """When all 3 rounds are rejected, system fails closed and quarantines to pending_fixes/."""
    test_file = temp_repo / "broken_module.py"
    test_file.write_text("def broken():\n    return -1\n", encoding="utf-8")

    agent = CodeEvolutionAgent()
    agent.scp_dir = temp_repo
    agent.tests_dir = temp_repo
    agent.log_file = temp_repo / "data" / "evolution_log.jsonl"

    bug = {
        "file": "broken_module.py",
        "line": 1,
        "pattern": "StubBug",
        "severity": "HIGH",
        "why": "Persistent bug",
        "fix_hint": "Fix correctly",
        "match": "return -1",
    }

    call_count = 0

    async def mock_always_invalid_fix(b, feedback=None):
        nonlocal call_count
        call_count += 1
        # Always syntax error
        return f"<<<<<<< SEARCH\n    return -1\n=======\n    return broken syntax round {call_count}\n>>>>>>> REPLACE"

    agent._generate_fix = mock_always_invalid_fix
    agent._scan_bugs = MagicMock(return_value=[bug])

    pending_dir = temp_repo / "pending_fixes"

    with patch("scp.core.code_evolution_agent.SCP_ROOT", temp_repo):
        result = asyncio.run(agent.run_cycle())

    assert call_count == 3
    assert result["status"] == "fix_queued_for_review"
    assert result["fixes_skipped"] == 1
    # Check that pending_fixes directory received exactly one quarantined file (no duplicates)
    pending_files = list(pending_dir.glob("*.py"))
    assert len(pending_files) == 1
    # Check that original source file was NOT mutated
    assert "def broken():\n    return -1\n" == test_file.read_text(encoding="utf-8")


def test_fast_learning_dual_store_populates_knowledge_records(temp_repo):
    """FastLearningEngine._store_kb must populate data/knowledge.sqlite3 table knowledge_records."""
    from scp.core.fast_learning_engine import FastLearningEngine
    data_dir = temp_repo / "data"
    scp_db = data_dir / "v13.db"

    engine = FastLearningEngine(
        scp_db_path=str(scp_db),
        data_dir=str(data_dir),
    )

    # Store a verified fact
    stored = engine._store_kb(
        entity="Python Programming Language",
        attribute="inventor",
        value="Guido van Rossum",
        source="unit_test_verification",
        confidence=0.98,
    )
    assert stored is True

    # Verify data/knowledge.sqlite3 received the row
    kr_sqlite = data_dir / "knowledge.sqlite3"
    assert kr_sqlite.exists()

    with sqlite3.connect(kr_sqlite) as conn:
        cur = conn.cursor()
        cur.execute("SELECT question, answer, confidence FROM knowledge_records")
        rows = cur.fetchall()
        assert len(rows) == 1
        assert "Python Programming Language" in rows[0][0]
        assert "Guido van Rossum" == rows[0][1]
        assert rows[0][2] >= 0.5


def test_autonomous_cycle_dispatches_kernel_and_hands_tasks(temp_repo):
    """run_autonomous_cycle triggers learning and dispatches tasks to ask and hands kernels."""
    data_dir = temp_repo / "data"

    result = asyncio.run(run_autonomous_cycle(count=2, dispatch_tasks=True, data_dir=data_dir))

    assert result["status"] == "ok"
    assert result["knowledge_records_count"] >= 5
    assert result["ask_task"]["state"] == "COMPLETED"
    assert result["hands_task"]["state"] == "COMPLETED"

    # Verify physical records in ask_task_kernel.sqlite3
    ask_db = data_dir / "ask_task_kernel.sqlite3"
    with sqlite3.connect(ask_db) as conn:
        c = conn.cursor().execute("SELECT state FROM tasks WHERE task_id=?", (result["ask_task"]["task_id"],)).fetchone()
        assert c is not None
        assert c[0] == "COMPLETED"

    # Verify physical records in data/hands/task_kernel.sqlite3
    hands_db = data_dir / "hands" / "task_kernel.sqlite3"
    with sqlite3.connect(hands_db) as conn:
        c = conn.cursor().execute("SELECT state FROM tasks WHERE task_id=?", (result["hands_task"]["task_id"],)).fetchone()
        assert c is not None
        assert c[0] == "COMPLETED"


def test_process_bug_with_llm_iterative_refinement(temp_repo):
    """process_bug_with_llm loops up to max_rounds with critique on gate failure."""
    from scp.autofix.llm_fix_parts.process_bug_with_llm import process_bug_with_llm

    bug = BugReport(
        file=str(temp_repo / "foo.py"),
        line=1,
        bug_type="CustomBug",
        description="Test bug",
        suggested_fix="",
        tier=BugTier.TIER_1_AUTO_FIX,
    )

    mock_engine = MagicMock()
    # Round 1 fails, Round 2 succeeds
    mock_engine.process_bug.side_effect = [
        {"action": "skipped", "reason": "realtime_verifier: assertion failed"},
        {"action": "fixed", "reason": "all checks passed"},
    ]

    feedbacks_seen = []

    def mock_gen_fix(b, feedback=None):
        feedbacks_seen.append(feedback)
        return "<<<<<<< SEARCH\na = 1\n=======\na = 2\n>>>>>>> REPLACE"

    with patch("scp.autofix.llm_fix_parts.process_bug_with_llm.generate_fix_for_bug", side_effect=mock_gen_fix):
        result = process_bug_with_llm(bug, mock_engine, allow_llm=True)

    assert result["action"] == "fixed"
    assert result["refinement_rounds"] == 2
    assert len(feedbacks_seen) == 2
    assert feedbacks_seen[0] is None
    assert "realtime_verifier: assertion failed" in feedbacks_seen[1]


def test_generate_fix_for_bug_bypasses_cache_when_feedback_present(temp_repo):
    """When feedback is passed, generate_fix_for_bug must bypass the cache."""
    from scp.autofix.llm_fix_cache import get_llm_fix_cache
    from scp.autofix.llm_fix_parts.generate_fix_for_bug import generate_fix_for_bug

    get_llm_fix_cache().clear_all()

    test_file = temp_repo / "test_cache.py"
    test_file.write_text("x = 1\n", encoding="utf-8")

    bug = BugReport(
        file=str(test_file),
        line=1,
        bug_type="CacheTestBug",
        description="Testing cache bypass",
        suggested_fix="",
        tier=BugTier.TIER_1_AUTO_FIX,
    )

    calls = []
    def mock_smart_llm(prompt, bug_type, max_tokens):
        calls.append(prompt)
        return "<<<<<<< SEARCH\nx = 1\n=======\nx = 2\n>>>>>>> REPLACE"

    with patch("scp.autofix.llm_fix_parts.generate_fix_for_bug._call_smart_llm", side_effect=mock_smart_llm), \
         patch("scp.autofix.llm_fix_parts.generate_fix_for_bug._check_rate_limit", return_value=True):
        # Round 1: normal call (populates cache)
        fix1 = generate_fix_for_bug(bug, feedback=None)
        assert fix1 is not None

        # Round 2: call with feedback must invoke LLM again with critique in prompt (bypasses cache)
        fix2 = generate_fix_for_bug(bug, feedback="CRITIQUE: failed test")
        assert fix2 is not None

        # Round 3: call without feedback should hit cache and NOT invoke LLM again
        fix3 = generate_fix_for_bug(bug, feedback=None)
        assert fix3 is not None

    assert len(calls) == 2
    assert "CRITIQUE: failed test" not in calls[0]
    assert "CRITIQUE: failed test" in calls[1]


def test_autofix_engine_driftguard_verbatim_critique_propagation(temp_repo):
    """AutoFixEngine.process_bug must propagate DriftGuard verbatim critique into result['reason']."""
    test_file = temp_repo / "target_module.py"
    test_file.write_text("def target():\n    return 1\n", encoding="utf-8")

    engine = AutoFixEngine(data_dir=str(temp_repo / "data"))

    bug = BugReport(
        file=str(test_file),
        line=1,
        bug_type="TestBug",
        description="Drift guard test bug",
        suggested_fix="<<<<<<< SEARCH\n    return 1\n=======\n    return 2\n>>>>>>> REPLACE",
        tier=BugTier.TIER_1_AUTO_FIX,
    )
    # Simulate round 1 (not last round)
    setattr(bug, "_last_refinement_round", False)

    with patch.object(
        CodeEvolutionAgent,
        "_try_patch_with_feedback",
        return_value=(False, "DRIFT_GUARD_BLOCKED: unauthorized deletion of _validate_openrouter_base_url"),
    ):
        res = engine.process_bug(bug)

    assert res["action"] == "skipped"
    assert res["reason"] == "DRIFT_GUARD_BLOCKED: unauthorized deletion of _validate_openrouter_base_url"
    assert res.get("pending_fix_queued") is False


def test_process_bug_with_llm_single_quarantine_on_exhaustion(temp_repo):
    """process_bug_with_llm must quarantine exactly ONE pending fix file when round 3 is exhausted."""
    test_file = temp_repo / "exhaust_module.py"
    test_file.write_text("def broken():\n    pass\n", encoding="utf-8")

    pending_dir = temp_repo / "pending_fixes"

    bug = BugReport(
        file=str(test_file),
        line=1,
        bug_type="ExhaustBug",
        description="Persistent failure",
        suggested_fix="",
        tier=BugTier.TIER_1_AUTO_FIX,
    )

    from scp.autofix.llm_fix_parts.process_bug_with_llm import process_bug_with_llm

    mock_engine = MagicMock()
    # All 3 rounds fail in _apply_fix on the final round
    mock_engine.process_bug.side_effect = [
        {"action": "skipped", "reason": "Round 1 failed", "pending_fix_queued": False},
        {"action": "skipped", "reason": "Round 2 failed", "pending_fix_queued": False},
        # Round 3: _apply_fix already queued the file
        {"action": "skipped", "reason": "Round 3 failed", "pending_fix_queued": True},
    ]

    with patch("scp.autofix.llm_fix_parts.process_bug_with_llm.generate_fix_for_bug", return_value="<<<<<<< SEARCH\npass\n=======\nreturn 1\n>>>>>>> REPLACE"), \
         patch("scp.core.code_evolution_agent.SCP_ROOT", temp_repo):
        res = process_bug_with_llm(bug, mock_engine, allow_llm=True)

    assert res["action"] == "skipped"
    assert res["refinement_rounds"] == 3
    # pending_fix_queued was True, so process_bug_with_llm did NOT double-queue
    pending_files = list(pending_dir.glob("*.py"))
    assert len(pending_files) == 0  # mock_engine reported it already handled queueing; no extra write

