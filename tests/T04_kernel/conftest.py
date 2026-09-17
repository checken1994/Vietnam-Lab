"""T04 kernel test environment boundary.

The repository .env is an operator configuration and may declare production
mode. Kernel contract tests must explicitly run in the non-production profile;
individual tests that prove production behavior opt in with monkeypatch.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def kernel_test_profile(monkeypatch):
    monkeypatch.setenv("SCP_PRODUCTION_MODE", "0")
    monkeypatch.setenv("SCP_MODE", "test")
    monkeypatch.setenv("SCP_API_PROFILE", "full")
    monkeypatch.delenv("SCP_RELEASE_PROFILE", raising=False)
