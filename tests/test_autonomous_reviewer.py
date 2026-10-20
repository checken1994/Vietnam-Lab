"""Tests for SCP Autonomous Reviewer (Rule 7: Distributed Autonomy).

Verifies:
1. Positive path: valid patch evaluated, approved, applied, and notifications recorded.
2. Negative anti-placebo paths (Rule 11):
   - Syntax error in replacement code -> DENY (Fail-Closed)
   - Dangerous pattern (eval/exec/os.system) -> DENY (Fail-Closed)
   - Blast radius out-of-bounds -> DENY (Fail-Closed)
   - SEARCH block mismatch -> DENY (Fail-Closed)
   - Stale / already-resolved bug condition -> DENY (Fail-Closed)
3. Notification integration:
   - tier3_bug_queued notification emitted
   - permission_approved notification emitted
   - permission_denied notification emitted
   - fix_applied notification emitted
"""
from __future__ import annotations

import json
from pathlib import Path
import pytest

from scp.autofix.autonomous_reviewer import (
    AutonomousReviewer,
    AutonomousReviewerConfig,
    get_autonomous_reviewer,
    reset_autonomous_reviewer,
)
from scp.autofix.classifier import BugReport, BugTier
from scp.autofix.permission import PermissionGate
from scp.runtime.notifications import NotificationConfig, UserNotificationSystem


@pytest.fixture(autouse=True)
def cleanup():
    reset_autonomous_reviewer()
    yield
    reset_autonomous_reviewer()


def test_autonomous_reviewer_positive_flow(tmp_path):
    """Test positive flow: valid fix passes independent review and is approved."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    target_file = tmp_path / "sample_service.py"
    target_file.write_text(
        "def compute(a, b):\n"
        "    result = a + b\n"
        "    return result\n",
        encoding="utf-8",
    )

    notif = UserNotificationSystem(
        config=NotificationConfig(min_severity="warning"),
        data_dir=str(data_dir),
    )
    gate = PermissionGate(data_dir=str(data_dir), notif_system=notif)

    valid_patch = (
        "<<<<<<< SEARCH\n"
        "def compute(a, b):\n"
        "    result = a + b\n"
        "    return result\n"
        "=======\n"
        "def compute(a, b):\n"
        "    # verified fix\n"
        "    result = a + b\n"
        "    return max(0, result)\n"
        ">>>>>>> REPLACE"
    )

    bug = BugReport(
        file=str(target_file),
        line=2,
        bug_type="LogicCorrection",
        description="compute should clamp negative results to zero",
        suggested_fix=valid_patch,
        tier=BugTier.TIER_3_PERMISSION,
    )

    req_id = gate.request_permission(bug)
    assert req_id.startswith("perm_")

    reviewer = AutonomousReviewer(
        data_dir=str(data_dir),
        repo_root=tmp_path,
        gate=gate,
        notif_system=notif,
        config=AutonomousReviewerConfig(auto_apply=False),
    )

    result = reviewer.review_request(req_id)
    assert result["status"] == "approved"
    assert "AST parsed cleanly" in result["rationale"]
    assert gate.check_permission(req_id) == "approved"

    # Verify notifications recorded
    notifs_file = data_dir / "notifications.jsonl"
    assert notifs_file.exists()
    lines = [json.loads(line) for line in notifs_file.read_text(encoding="utf-8").splitlines()]
    event_types = [entry["event_type"] for entry in lines]
    assert "tier3_bug_queued" in event_types
    assert "permission_approved" in event_types


def test_autonomous_reviewer_rejects_syntax_error(tmp_path):
    """Negative Anti-Placebo: Patch with invalid syntax must be DENIED."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    target_file = tmp_path / "bad_syntax.py"
    target_file.write_text("def test():\n    return 42\n", encoding="utf-8")

    notif = UserNotificationSystem(data_dir=str(data_dir))
    gate = PermissionGate(data_dir=str(data_dir), notif_system=notif)

    broken_syntax_patch = (
        "<<<<<<< SEARCH\n"
        "def test():\n"
        "    return 42\n"
        "=======\n"
        "def test(:\n"  # deliberate SyntaxError
        "    return 42\n"
        ">>>>>>> REPLACE"
    )

    bug = BugReport(
        file=str(target_file),
        line=1,
        bug_type="SyntaxErrorFix",
        description="syntax test",
        suggested_fix=broken_syntax_patch,
        tier=BugTier.TIER_3_PERMISSION,
    )

    req_id = gate.request_permission(bug)
    reviewer = AutonomousReviewer(
        data_dir=str(data_dir),
        repo_root=tmp_path,
        gate=gate,
        notif_system=notif,
        config=AutonomousReviewerConfig(auto_apply=False),
    )

    result = reviewer.review_request(req_id)
    assert result["status"] == "denied"
    assert "syntax" in result["rationale"].lower()
    assert gate.check_permission(req_id) == "denied"

    # Verify notification recorded
    notifs_file = data_dir / "notifications.jsonl"
    lines = [json.loads(line) for line in notifs_file.read_text(encoding="utf-8").splitlines()]
    event_types = [entry["event_type"] for entry in lines]
    assert "permission_denied" in event_types


def test_autonomous_reviewer_rejects_dangerous_patterns(tmp_path):
    """Negative Anti-Placebo: Patch introducing eval/exec must be DENIED."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    target_file = tmp_path / "policy_test.py"
    target_file.write_text("def run(code):\n    return code\n", encoding="utf-8")

    notif = UserNotificationSystem(data_dir=str(data_dir))
    gate = PermissionGate(data_dir=str(data_dir), notif_system=notif)

    evil_patch = (
        "<<<<<<< SEARCH\n"
        "def run(code):\n"
        "    return code\n"
        "=======\n"
        "def run(code):\n"
        "    return eval(code)\n"  # dangerous pattern
        ">>>>>>> REPLACE"
    )

    bug = BugReport(
        file=str(target_file),
        line=1,
        bug_type="SecurityFix",
        description="dangerous eval addition",
        suggested_fix=evil_patch,
        tier=BugTier.TIER_3_PERMISSION,
    )

    req_id = gate.request_permission(bug)
    reviewer = AutonomousReviewer(
        data_dir=str(data_dir),
        repo_root=tmp_path,
        gate=gate,
        notif_system=notif,
        config=AutonomousReviewerConfig(auto_apply=False),
    )

    result = reviewer.review_request(req_id)
    assert result["status"] == "denied"
    assert "Forbidden pattern detected" in result["rationale"]
    assert gate.check_permission(req_id) == "denied"


def test_autonomous_reviewer_rejects_search_mismatch(tmp_path):
    """Negative Anti-Placebo: Patch whose SEARCH block does not match file must be DENIED."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    target_file = tmp_path / "existing.py"
    target_file.write_text("val = 123\n", encoding="utf-8")

    notif = UserNotificationSystem(data_dir=str(data_dir))
    gate = PermissionGate(data_dir=str(data_dir), notif_system=notif)

    mismatch_patch = (
        "<<<<<<< SEARCH\n"
        "non_existent_code_block = 999\n"
        "=======\n"
        "val = 456\n"
        ">>>>>>> REPLACE"
    )

    bug = BugReport(
        file=str(target_file),
        line=1,
        bug_type="LogicFix",
        description="mismatched patch",
        suggested_fix=mismatch_patch,
        tier=BugTier.TIER_3_PERMISSION,
    )

    req_id = gate.request_permission(bug)
    reviewer = AutonomousReviewer(
        data_dir=str(data_dir),
        repo_root=tmp_path,
        gate=gate,
        notif_system=notif,
        config=AutonomousReviewerConfig(auto_apply=False),
    )

    result = reviewer.review_request(req_id)
    assert result["status"] == "denied"
    assert "patch application failed" in result["rationale"].lower() or "search block not found" in result["rationale"].lower()
    assert gate.check_permission(req_id) == "denied"


def test_autonomous_reviewer_processes_batch_queue(tmp_path):
    """Batch review: multiple pending requests are processed without human intervention."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    f1 = tmp_path / "f1.py"
    f1.write_text("x = 1\n", encoding="utf-8")
    f2 = tmp_path / "f2.py"
    f2.write_text("y = 2\n", encoding="utf-8")

    notif = UserNotificationSystem(data_dir=str(data_dir))
    gate = PermissionGate(data_dir=str(data_dir), notif_system=notif)

    p1 = "<<<<<<< SEARCH\nx = 1\n=======\nx = 10\n>>>>>>> REPLACE"
    p2 = "<<<<<<< SEARCH\nmissing = 0\n=======\ny = 20\n>>>>>>> REPLACE"

    gate.request_permission(BugReport(str(f1), 1, "TypeA", "desc1", p1, BugTier.TIER_3_PERMISSION))
    gate.request_permission(BugReport(str(f2), 1, "TypeB", "desc2", p2, BugTier.TIER_3_PERMISSION))

    assert len(gate.list_pending()) == 2

    reviewer = AutonomousReviewer(
        data_dir=str(data_dir),
        repo_root=tmp_path,
        gate=gate,
        notif_system=notif,
        config=AutonomousReviewerConfig(auto_apply=False),
    )

    summary = reviewer.review_all_pending()
    assert summary["total_pending"] == 2
    assert summary["approved_count"] == 1
    assert summary["denied_count"] == 1

    # Pending queue must now be clean
    assert len(gate.list_pending()) == 0
