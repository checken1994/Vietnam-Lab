import pytest
from scp.autofix.engine import get_autofix_engine
from scp.autofix.classifier import BugReport, BugTier


def test_autofix_protected_path_blocked():
    """Protected-path short-circuit fires BEFORE the WHY gate is consulted.

    ``AutoFixEngine._auto_fix_gates`` calls ``_is_protected_path`` at the
    top of ``process_bug`` — the gate is never reached. The test now calls
    the real engine directly with no mock, pinning the actual production
    ordering: a bug on ``scp/autofix/policy_gate.py`` is blocked by the
    protected-path guard without consulting WHY."""
    engine = get_autofix_engine()
    bug = BugReport(
        file='scp/autofix/policy_gate.py',
        line=1,
        bug_type='test_bug',
        description='Test bug',
        suggested_fix='<<<<<<< SEARCH\nfoo\n=======\nbar\n>>>>>>> REPLACE',
        tier=BugTier.TIER_1_AUTO_FIX
    )
    result = engine.process_bug(bug)

    assert result['action'] == 'protected_path_blocked'
