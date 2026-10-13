"""Test M-10: Production Guard blocks SCP_AUTONOMOUS_MODE in production."""
import os
import pytest
from scp.security.production_guard import enforce_production_safety


def test_production_guard_refuses_autonomous_mode(monkeypatch):
    """M-10: When SCP_PRODUCTION_MODE=1 and SCP_AUTONOMOUS_MODE=1, startup is refused fail-closed."""
    monkeypatch.setenv("SCP_PRODUCTION_MODE", "1")
    monkeypatch.setenv("SCP_AUTONOMOUS_MODE", "1")
    monkeypatch.setenv("SCP_EGRESS_MODE", "deny")
    monkeypatch.setenv("SCP_HOST", "127.0.0.1")

    with pytest.raises(RuntimeError, match="unsafe bypass flags active.*SCP_AUTONOMOUS_MODE"):
        enforce_production_safety()


def test_production_guard_allows_non_production_autonomous(monkeypatch):
    """When SCP_PRODUCTION_MODE=0, SCP_AUTONOMOUS_MODE does not trigger production guard exception."""
    monkeypatch.setenv("SCP_PRODUCTION_MODE", "0")
    monkeypatch.setenv("SCP_AUTONOMOUS_MODE", "1")
    # Should return cleanly without error
    enforce_production_safety()
