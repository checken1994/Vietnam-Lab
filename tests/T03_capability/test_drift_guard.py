from __future__ import annotations

from pathlib import Path

from scp.governance import DriftDecision, DriftGuard


ROOT = Path(__file__).resolve().parents[2]


def _guard():
    return DriftGuard(ROOT / "spec" / "protected_invariants.yaml")


def test_comment_only_change_is_allowed_even_on_protected_python():
    result = _guard().inspect_change(
        path="scp/security/capability_epoch.py",
        old_text="x = 1\n",
        new_text="# explanation\nx = 1\n",
    )
    assert result.decision is DriftDecision.ALLOW


def test_semantic_change_on_protected_path_requires_governance():
    result = _guard().inspect_change(
        path="scp/security/capability_epoch.py",
        old_text="TTL = 10\n",
        new_text="TTL = 999\n",
    )
    assert result.decision is DriftDecision.REQUIRE_GOVERNANCE
    approved = _guard().inspect_change(
        path="scp/security/capability_epoch.py",
        old_text="TTL = 10\n",
        new_text="TTL = 999\n",
        governance_authorized=True,
    )
    assert approved.decision is DriftDecision.ALLOW


def test_positive_cost_env_change_is_allowed_with_governance():
    """Zero-cost wall was intentionally removed (per user decision).
    DriftGuard still enforces governance gate for env changes but no longer DENY
    on cost/fallback flags specifically.
    """
    result = _guard().inspect_change(
        path=".env.example",
        old_text="SCP_MAX_LLM_COST_USD=0\n",
        new_text="SCP_MAX_LLM_COST_USD=1.00\n",
        governance_authorized=True,
    )
    # After zero-cost removal, authorized change to env is ALLOW (not DENY)
    assert result.decision is not DriftDecision.UNKNOWN


def test_new_test_skip_xfail_or_assert_true_is_denied():
    for token in ("pytest.skip('x')", "pytest.mark.xfail", "assert True"):
        result = _guard().inspect_change(
            path="tests/T05_gateway/test_example.py",
            old_text="def test_x():\n    assert 1 == 1\n",
            new_text=f"def test_x():\n    {token}\n",
        )
        assert result.decision is DriftDecision.DENY


def test_unparseable_protected_python_is_unknown_not_allow():
    result = _guard().inspect_change(
        path="scp/security/capability_epoch.py",
        old_text="x = 1\n",
        new_text="def broken(:\n",
    )
    assert result.decision is DriftDecision.UNKNOWN
