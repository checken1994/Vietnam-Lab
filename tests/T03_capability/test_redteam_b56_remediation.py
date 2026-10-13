"""Tests for GA B56 Remediation: Red-Team L3 & Review Round 25 Findings.

Verifies:
- PC-01: Linux/POSIX command dispatcher platform branching.
- GAP-01: Journal anchor HMAC detecting events deletion fail-closed.
- GAP-02: compute_journal_event_hash requires SCP_CAPABILITY_SECRET (no unkeyed fallback).
- GAP-03: Sandbox evaluator strict isolation refuses unisolated subprocess even inside container.
- E-02: Production guard TLS enforcement when binding non-loopback host.
"""

from __future__ import annotations

import os
import platform
import shutil
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from scp.pc_control.pc_controller import PCController
from scp.task_kernel_parts.definitions import KernelError, compute_journal_event_hash
from scp.task_kernel_parts.lease_manager import verify_journal_impl
from scp.task_kernel_parts.taskkernel import TaskKernel
from scp.sandbox_evaluator.evaluator import evaluate
from scp.security.production_guard import enforce_production_safety
from scp.core.safe_process import _validate_executable, _WHITELISTED_TOOLS


def test_pc01_command_dispatcher_platform_branching(tmp_path: Path):
    """PC-01: Command dispatcher must branch between Windows powershell.exe and POSIX /bin/sh or pwsh."""
    controller = PCController(working_dir=tmp_path)

    # 1. On Windows, verify command targets powershell.exe
    with patch("platform.system", return_value="Windows"), \
         patch("scp.pc_control.pc_controller.safe_run") as mock_safe_run:
        mock_completed = MagicMock(returncode=0, stdout="ok", stderr="")
        mock_safe_run.return_value = mock_completed
        res = controller._run_sync("echo hello", timeout=5)
        assert res["success"] is True
        mock_safe_run.assert_called_once()
        cmd_args = mock_safe_run.call_args[0][0]
        assert cmd_args[0] == "powershell.exe"
        assert cmd_args[-1] == "echo hello"

    # 2. On Linux/POSIX without pwsh, verify command targets /bin/sh
    with patch("platform.system", return_value="Linux"), \
         patch("shutil.which", return_value=None), \
         patch("scp.pc_control.pc_controller.safe_run") as mock_safe_run:
        mock_completed = MagicMock(returncode=0, stdout="ok", stderr="")
        mock_safe_run.return_value = mock_completed
        res = controller._run_sync("echo hello", timeout=5)
        assert res["success"] is True
        mock_safe_run.assert_called_once()
        cmd_args = mock_safe_run.call_args[0][0]
        assert cmd_args[0] == "/bin/sh"
        assert cmd_args[1] == "-c"
        assert cmd_args[2] == "echo hello"

    # 3. On Linux/POSIX with pwsh installed, verify command targets pwsh
    with patch("platform.system", return_value="Linux"), \
         patch("shutil.which", return_value="/usr/bin/pwsh"), \
         patch("scp.pc_control.pc_controller.safe_run") as mock_safe_run:
        mock_completed = MagicMock(returncode=0, stdout="ok", stderr="")
        mock_safe_run.return_value = mock_completed
        res = controller._run_sync("echo hello", timeout=5)
        assert res["success"] is True
        mock_safe_run.assert_called_once()
        cmd_args = mock_safe_run.call_args[0][0]
        assert cmd_args[0] == "pwsh"
        assert cmd_args[-1] == "echo hello"

    # 4. Verify /bin/sh and sh are in whitelisted tools for safe_run
    assert "/bin/sh" in _WHITELISTED_TOOLS
    assert "sh" in _WHITELISTED_TOOLS
    _validate_executable("/bin/sh")
    _validate_executable("sh")


def test_gap01_journal_anchor_detects_events_deletion(monkeypatch: pytest.MonkeyPatch):
    """GAP-01: Deleting events table must be caught fail-closed via Journal Anchor in control table."""
    monkeypatch.setenv("SCP_CAPABILITY_SECRET", "test-capability-secret-for-b56-remediation-key")
    kernel = TaskKernel(":memory:")
    task_id = "task_b56_anchor_test_001"

    # Create task and append events
    kernel.create_task(task_id=task_id, owner="operator", goal="test journal anchor")
    journal_before = kernel.verify_journal(task_id)
    assert journal_before["hash_chain_valid"] is True
    assert journal_before["event_count"] >= 1

    # Check anchor recorded in control table
    ctrl = kernel._control()
    assert ctrl["event_count"] >= 1
    assert ctrl["journal_head_hash"] is not None

    # Simulate adversarial attacker running DELETE FROM events
    kernel.conn.execute("DELETE FROM events WHERE task_id = ?", (task_id,))
    events = kernel.get_events(task_id)
    assert len(events) == 0

    # Verification must fail-closed catching deletion via anchor
    journal_after = kernel.verify_journal(task_id)
    assert journal_after["hash_chain_valid"] is False
    assert journal_after["event_count"] == 0
    assert any("journal_deleted:events_table_empty_but_anchor_exists" in err for err in journal_after["errors"])


def test_gap02_journal_event_hash_requires_capability_secret_fail_closed(monkeypatch: pytest.MonkeyPatch):
    """GAP-02: compute_journal_event_hash must fail closed when SCP_CAPABILITY_SECRET is missing."""
    test_body = {
        "event_id": "evt_test_123",
        "task_id": "task_123",
        "seq": 1,
        "type": "TASK_CREATED",
    }

    # Missing secret -> must raise KernelError
    monkeypatch.delenv("SCP_CAPABILITY_SECRET", raising=False)
    with pytest.raises(KernelError, match="SCP_CAPABILITY_SECRET is required to compute HMAC journal hash"):
        compute_journal_event_hash(test_body)

    # Empty secret -> must raise KernelError
    monkeypatch.setenv("SCP_CAPABILITY_SECRET", "")
    with pytest.raises(KernelError, match="SCP_CAPABILITY_SECRET is required to compute HMAC journal hash"):
        compute_journal_event_hash(test_body)

    # Valid secret -> returns HMAC SHA256 hex string
    monkeypatch.setenv("SCP_CAPABILITY_SECRET", "valid-secret-key-32-bytes-long-now")
    h = compute_journal_event_hash(test_body)
    assert isinstance(h, str)
    assert len(h) == 64


def test_gap03_evaluator_strict_isolation_refuses_even_in_container(monkeypatch: pytest.MonkeyPatch):
    """GAP-03: Strict isolation in evaluator must refuse execution when bwrap is absent even inside container."""
    monkeypatch.setenv("SCP_STRICT_ISOLATION", "1")
    monkeypatch.delenv("SCP_CAPABILITY_TOKEN", raising=False)

    # Simulate running inside Docker container without bwrap
    monkeypatch.setattr("platform.system", lambda: "Linux")
    monkeypatch.setattr("shutil.which", lambda prog: None)
    # Simulate /.dockerenv exists
    orig_exists = os.path.exists
    monkeypatch.setattr(
        "os.path.exists",
        lambda path: True if path in ("/.dockerenv", "/run/.containerenv") else orig_exists(path),
    )

    patch_target = {
        "files": {"sample.py": "def add(a, b): return a + b\n"},
        "test_files": {"test_sample.py": "from sample import add\ndef test_add(): assert add(1, 2) == 3\n"},
    }

    result = evaluate(patch_target)
    assert result.verdict == "FAIL"
    assert "UNISOLATED_SUBPROCESS_REFUSED:missing_bwrap_or_token" in result.reason


def test_e02_production_guard_tls_enforcement_non_loopback(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """E-02: In production mode, binding non-loopback host without SCP_FORCE_HTTPS=1 must fail closed."""
    pw_file = tmp_path / "auth_pw.txt"
    pw_file.write_text("strong_production_password_16_chars_min", encoding="utf-8")

    monkeypatch.setenv("SCP_PRODUCTION_MODE", "1")
    monkeypatch.setenv("SCP_EGRESS_MODE", "deny")
    monkeypatch.setenv("SCP_AUTH_PASSWORD_FILE", str(pw_file))

    # Binding to 0.0.0.0 without SCP_FORCE_HTTPS -> Refuse startup
    monkeypatch.setenv("SCP_HOST", "0.0.0.0")
    monkeypatch.setenv("SCP_FORCE_HTTPS", "0")
    with pytest.raises(RuntimeError, match="SCP_FORCE_HTTPS=1 is required when SCP_HOST is not loopback"):
        enforce_production_safety()

    # Binding to public IP without SCP_FORCE_HTTPS -> Refuse startup
    monkeypatch.setenv("SCP_HOST", "192.168.1.100")
    monkeypatch.setenv("SCP_FORCE_HTTPS", "0")
    with pytest.raises(RuntimeError, match="SCP_FORCE_HTTPS=1 is required when SCP_HOST is not loopback"):
        enforce_production_safety()

    # Binding to 0.0.0.0 WITH SCP_FORCE_HTTPS=1 -> Passes TLS check
    monkeypatch.setenv("SCP_FORCE_HTTPS", "1")
    enforce_production_safety()  # Should not raise
