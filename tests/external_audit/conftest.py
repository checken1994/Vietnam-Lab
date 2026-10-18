"""Pytest configuration for external audit tests.

Independent test harness that does NOT import SCP modules directly.
External audit tests shell out to external tools (bandit, ruff, python subshell)
and assert on security invariants via subprocess and AST/regex parsing.
"""
from __future__ import annotations

import os
import shutil
import sys

import pytest

# Ensure venv Scripts directory is available in PATH for tool discovery (bandit, ruff, etc.)
_SCRIPTS_DIR = os.path.join(sys.prefix, "Scripts")
if os.path.isdir(_SCRIPTS_DIR) and _SCRIPTS_DIR not in os.environ.get("PATH", ""):
    os.environ["PATH"] = _SCRIPTS_DIR + os.pathsep + os.environ.get("PATH", "")


def pytest_collection_modifyitems(config, items):
    """Mark tests that need external tools — skip if tool missing."""
    for item in items:
        if "via_ruff" in item.nodeid and shutil.which("ruff") is None:
            item.add_marker(pytest.mark.skip(reason="ruff not installed"))
        if "via_bandit" in item.nodeid and shutil.which("bandit") is None:
            item.add_marker(pytest.mark.skip(reason="bandit not installed"))
        if "via_grep" in item.nodeid and shutil.which("grep") is None:
            item.add_marker(pytest.mark.skip(reason="grep not installed"))
