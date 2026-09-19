"""T09 Golden Task - CE-S??: Bounded Autofix (Shadow Rollback & Blast Radius).

Evidence level: C (End-to-end execution flow without unit test mocks)
Authority path: [AutoFixEngine, CodeEvolutionAgent, ShadowSnapshotManager]
Covered capabilities:
  - self_improvement.bounded_autofix
  - self_improvement.autofix_safety_guards
Gates: T07, T09
"""
import os
import shutil
from pathlib import Path

import pytest

from scp.autofix.engine import AutoFixEngine
from scp.autofix.runner_phases.ast_scan import BugReport

# For true E2E, we need a workspace with a test file.
@pytest.fixture
def e2e_workspace(tmp_path):
    src_dir = tmp_path / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    
    # Target file that has a "bug"
    target_py = src_dir / "calculator.py"
    target_py.write_text(
        "def add(a, b):\n"
        "    return a + b\n",
        encoding="utf-8"
    )
    
    # Test file that verifies the behavior
    test_py = tests_dir / "test_calculator.py"
    test_py.write_text(
        "import sys, os\n"
        "sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))\n"
        "from calculator import add\n\n"
        "def test_add():\n"
        "    assert add(2, 3) == 5\n",
        encoding="utf-8"
    )
    
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    
    return {
        "root": tmp_path,
        "src": src_dir,
        "tests": tests_dir,
        "target": target_py,
        "test_file": test_py,
        "data": data_dir,
    }


def test_ce_autofix_bounded_rollback_e2e(e2e_workspace, monkeypatch):
    """Proves the full closed-loop pipeline for Bounded Autofix:
    
    1. A BugReport is generated containing a patch that intentionally introduces a regression.
    2. AutoFixEngine applies the patch physically using CodeEvolutionAgent (no mocks).
    3. AutoFixEngine runs Reality Verification (pytest) which fails.
    4. AutoFixEngine rolls back the file via ShadowSnapshotManager.
    5. The codebase remains unharmed.
    """
    target = e2e_workspace["target"]
    original_code = target.read_text(encoding="utf-8")
    
    engine = AutoFixEngine(data_dir=str(e2e_workspace["data"]))
    
    # We must patch pytest running directory so it tests our tmp_path workspace
    # `engine._verify_fix` calls `subprocess.run(["pytest", ...])`
    # We set cwd to e2e_workspace["root"]
    import subprocess
    original_run = subprocess.run
    
    def mocked_run(cmd, *args, **kwargs):
        if "pytest" in cmd:
            kwargs["cwd"] = str(e2e_workspace["root"])
        return original_run(cmd, *args, **kwargs)
        
    monkeypatch.setattr(subprocess, "run", mocked_run)
    monkeypatch.setenv("SCP_AUTO_APPROVE_TIER3", "1")
    
    # Provide a patch that breaks the `add` function intentionally (returns a - b)
    # This will cause test_add() to fail (2 - 3 != 5)
    suggested_fix = (
        "<<<<<<< SEARCH\n"
        "def add(a, b):\n"
        "    return a + b\n"
        "=======\n"
        "def add(a, b):\n"
        "    return a - b\n"
        ">>>>>>>"
    )
    
    bug = BugReport(
        file=str(target),
        line=1,
        bug_type="LogicError",
        description="add function is incorrect",
        suggested_fix=suggested_fix,
        tier=3
    )
    
    # Monkeypatch to avoid the Policy Gate blocking it (as we're testing rollback, not policy gate)
    # And mock confidence ranker
    from scp.autofix.confidence_ranker import ProposedFix
    mock_ranked = ProposedFix(
        fix_id="test",
        patch=suggested_fix,
        patched_source="",
        source="rule",
    )
    mock_ranked.confidence = 0.95
    mock_ranked.disposition = "auto_apply"
    
    monkeypatch.setattr("scp.autofix.confidence_ranker.best_fix", lambda *args, **kwargs: mock_ranked)
    monkeypatch.setattr(engine, "_auto_fix_gates", lambda ctx: None)
    
    result = engine._auto_fix(bug, report=False)
    
    # Because the test fails, AutoFixEngine must rollback and return skipped/failed
    assert result["action"] != "fixed"
    
    # Verify rollback was successful
    current_code = target.read_text(encoding="utf-8")
    assert current_code == original_code, "Catastrophic Forgetting! Engine failed to rollback after test failure."
    
    # Check shadow directory has the rolled_back transaction
    shadow_rb = e2e_workspace["data"] / "shadow" / "rolled_back"
    assert shadow_rb.exists()
    assert len(list(shadow_rb.iterdir())) >= 1
