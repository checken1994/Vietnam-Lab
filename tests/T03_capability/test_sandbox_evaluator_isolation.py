"""Tests for SCP-01: Sandbox Evaluator OS-level isolation and fail-closed security."""
import os
import sys
import pytest
from scp.sandbox_evaluator.evaluator import _sanitized_env, evaluate
from scp.security.os_sandbox import build_bwrap_argv


def test_sanitized_env_sets_dead_proxy():
    """SCP-01: Subprocess environment must set dead-proxy to block network egress."""
    env = _sanitized_env()
    assert env["HTTP_PROXY"] == "http://127.0.0.1:0"
    assert env["HTTPS_PROXY"] == "http://127.0.0.1:0"
    assert env["ALL_PROXY"] == "http://127.0.0.1:0"
    assert env["NO_PROXY"] == ""
    assert env["PYTHONIOENCODING"] == "utf-8"


def test_bwrap_argv_builder_isolation_parameters():
    """SCP-01: build_bwrap_argv generates thorough isolation flags."""
    cmd = ["python", "-m", "pytest"]
    argv = build_bwrap_argv(cmd, workspace="/tmp/test_workspace", clearenv=True)
    assert argv[0] == "bwrap"
    assert "--unshare-all" in argv
    assert "--die-with-parent" in argv
    assert "--clearenv" in argv
    assert "--ro-bind" in argv and argv[argv.index("--ro-bind") + 1] == "/"
    assert "--tmpfs" in argv and argv[argv.index("--tmpfs") + 1] == "/tmp"
    assert "--bind" in argv
    bind_idx = argv.index("--bind")
    assert argv[bind_idx + 1] == "/tmp/test_workspace"
    assert argv[bind_idx + 2] == "/tmp/test_workspace"
    assert argv[-len(cmd):] == cmd


def test_evaluator_strict_mode_refuses_unisolated_subprocess(monkeypatch):
    """SCP-01: In strict mode without bwrap or container, evaluate fails closed."""
    monkeypatch.setenv("SCP_STRICT_ISOLATION", "1")
    monkeypatch.delenv("SCP_CAPABILITY_TOKEN", raising=False)

    # Force platform and environment flags
    monkeypatch.setattr("platform.system", lambda: "Windows")
    monkeypatch.setattr("shutil.which", lambda prog: None)
    monkeypatch.setattr("os.path.exists", lambda path: False)

    patch_target = {
        "files": {"sample.py": "def add(a, b): return a + b\n"},
        "test_files": {"test_sample.py": "from sample import add\ndef test_add(): assert add(1, 2) == 3\n"},
    }

    result = evaluate(patch_target)
    assert result.verdict == "FAIL"
    assert "UNISOLATED_SUBPROCESS_REFUSED" in result.reason


def test_evaluator_strict_mode_allows_with_capability_token(monkeypatch):
    """SCP-01: In strict mode, an explicit capability token allows execution."""
    monkeypatch.setenv("SCP_STRICT_ISOLATION", "1")

    # Force platform without bwrap/container
    monkeypatch.setattr("platform.system", lambda: "Windows")
    monkeypatch.setattr("shutil.which", lambda prog: None)
    monkeypatch.setattr("os.path.exists", lambda path: False)

    patch_target = {
        "files": {"sample.py": "def add(a, b): return a + b\n"},
        "test_files": {"test_sample.py": "from sample import add\ndef test_add(): assert add(1, 2) == 3\n"},
        "capability_token": "valid-token-32-chars-long-test",
    }

    result = evaluate(patch_target)
    assert result.verdict == "PASS"
    assert result.returncode == 0
