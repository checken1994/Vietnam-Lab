"""Tests for GA B56 Remediation: Red-Team L3 & Review Round 25 Findings.

Verifies:
- PC-01: Linux/POSIX command dispatcher platform branching and safe_process POSIX shell hardening.
- GAP-01: Journal anchor HMAC detecting events deletion fail-closed (both verify_journal and verify_integrity).
- GAP-02: compute_journal_event_hash requires SCP_CAPABILITY_SECRET (no unkeyed fallback).
- GAP-03: Sandbox evaluator strict isolation refuses unisolated subprocess even inside container.
- E-02: Production guard TLS enforcement when binding non-loopback host.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess

import pytest

from scp.core.safe_process import _WHITELISTED_TOOLS, _validate_executable, safe_run
from scp.pc_control.pc_controller import PCController
from scp.sandbox_evaluator.evaluator import evaluate
from scp.security.production_guard import enforce_production_safety
from scp.task_kernel_parts.definitions import KernelError, compute_journal_event_hash
from scp.task_kernel_parts.taskkernel import TaskKernel


def test_pc01_command_dispatcher_platform_branching(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """PC-01: Command dispatcher must branch between Windows powershell.exe and POSIX /bin/sh or pwsh."""
    controller = PCController(working_dir=tmp_path)
    mock_completed = subprocess.CompletedProcess(args=["echo", "hello"], returncode=0, stdout="ok", stderr="")

    # 1. On Windows, verify command targets powershell.exe
    safe_run_calls = []

    def _fake_safe_run(cmd, *args, **kwargs):
        safe_run_calls.append(cmd)
        return mock_completed

    monkeypatch.setattr("platform.system", lambda: "Windows")
    monkeypatch.setattr("scp.pc_control.pc_controller.safe_run", _fake_safe_run)

    res = controller._run_sync("echo hello", timeout=5)
    assert res["success"] is True
    assert len(safe_run_calls) == 1
    cmd_args = safe_run_calls[0]
    assert cmd_args[0] == "powershell.exe"
    assert cmd_args[-1] == "echo hello"

    # 2. On Linux/POSIX without pwsh, verify command targets /bin/sh
    safe_run_calls.clear()
    monkeypatch.setattr("platform.system", lambda: "Linux")
    monkeypatch.setattr("shutil.which", lambda prog: None)

    res = controller._run_sync("echo hello", timeout=5)
    assert res["success"] is True
    assert len(safe_run_calls) == 1
    cmd_args = safe_run_calls[0]
    assert cmd_args[0] == "/bin/sh"
    assert cmd_args[1] == "-c"
    assert cmd_args[2] == "echo hello"

    # 3. On Linux/POSIX with pwsh installed, verify command targets pwsh
    safe_run_calls.clear()
    monkeypatch.setattr("shutil.which", lambda prog: "/usr/bin/pwsh" if prog == "pwsh" else None)

    res = controller._run_sync("echo hello", timeout=5)
    assert res["success"] is True
    assert len(safe_run_calls) == 1
    cmd_args = safe_run_calls[0]
    assert cmd_args[0] == "pwsh"
    assert cmd_args[-1] == "echo hello"

    # 4. Verify /bin/sh and sh are in whitelisted tools for safe_run
    assert "/bin/sh" in _WHITELISTED_TOOLS
    assert "sh" in _WHITELISTED_TOOLS
    _validate_executable("/bin/sh")
    _validate_executable("sh")

    # 5. Verify POSIX shell command hardening in safe_process (SEC-05/07/08 parity)
    with pytest.raises(PermissionError, match="safe_process: POSIX shell reading sensitive file/path"):
        safe_run(["/bin/sh", "-c", "cat .env"])

    with pytest.raises(PermissionError, match="Chaining/piping/interpolation operators forbidden"):
        safe_run(["/bin/sh", "-c", "echo 1; cat /etc/passwd"])

    with pytest.raises(PermissionError, match="catastrophic command blocked"):
        safe_run(["/bin/sh", "-c", "rm -rf /"], token=True)


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

    # Verify integrity before deletion is completely green
    integrity_before = kernel.verify_integrity()
    assert integrity_before["quick_check"] == "ok"
    assert integrity_before["invalid_chains"] == []

    # Simulate adversarial attacker running DELETE FROM events
    kernel.conn.execute("DELETE FROM events WHERE task_id = ?", (task_id,))
    events = kernel.get_events(task_id)
    assert len(events) == 0

    # Verification of single task must fail-closed catching deletion via anchor
    journal_after = kernel.verify_journal(task_id)
    assert journal_after["hash_chain_valid"] is False
    assert journal_after["event_count"] == 0
    assert any("journal_deleted:events_table_empty_but_anchor_exists" in err for err in journal_after["errors"])

    # Full database verify_integrity must ALSO fail-closed catching deletion via anchor
    integrity_after = kernel.verify_integrity()
    assert len(integrity_after["invalid_chains"]) >= 1
    assert any(
        chain["task_id"] in ("__global_journal__", task_id)
        for chain in integrity_after["invalid_chains"]
    )


def test_gap01_journal_anchor_detects_anchor_undercount_fail_closed(monkeypatch: pytest.MonkeyPatch):
    """GAP-01 (TK-01 · AUD-S2 2026-10-24): chèn/restore event lén không cập nhật
    anchor (actual > anchor) cũng PHẢI bị bắt fail-closed — trước đây check chỉ
    bắt chiều xóa (actual < anchor), cửa sổ mù một chiều đã tồn tại thật trên
    ask_task_kernel.sqlite3 production (Δ=3.986 event)."""
    monkeypatch.setenv("SCP_CAPABILITY_SECRET", "test-capability-secret-for-b56-remediation-key")
    kernel = TaskKernel(":memory:")
    task_id = "task_b56_anchor_undercount_001"

    kernel.create_task(task_id=task_id, owner="operator", goal="test anchor undercount")
    integrity_before = kernel.verify_integrity()
    assert integrity_before["quick_check"] == "ok"
    assert integrity_before["invalid_chains"] == []

    # Kẻ tấn công INSERT thêm event thẳng vào DB (không qua _append_event)
    # ⇒ events thật > anchor; anchor cũ không được cập nhật.
    kernel.conn.execute(
        "INSERT INTO events (event_id, task_id, seq, type, from_state, to_state, actor, "
        "reason, payload_json, prev_event_hash, event_hash, created_at) "
        "SELECT 'evt_forge_undercount', task_id, seq + 1, 'TASK_UPDATED', from_state, "
        "to_state, actor, reason, payload_json, prev_event_hash, event_hash, created_at "
        "FROM events WHERE task_id = ?",
        (task_id,),
    )
    # kernel_storage chạy isolation_level=None (autocommit) — KHÔNG commit tay.

    integrity_after = kernel.verify_integrity()
    flagged = [
        chain
        for chain in integrity_after["invalid_chains"]
        if chain["task_id"] == "__global_journal__"
    ]
    assert flagged, (
        "verify_integrity KHÔNG phát hiện actual > anchor — cửa sổ mù một chiều "
        "của TK-01 vẫn mở (anti-placebo: test này phải văng)"
    )
    assert any(
        "greater_than_anchor" in err
        for chain in flagged
        for err in chain["errors"]
    )


def test_gap02_journal_event_hash_requires_capability_secret_fail_closed(monkeypatch: pytest.MonkeyPatch):
    """GAP-02: compute_journal_event_hash must fail closed when its key is missing.

    [R5] The journal key is K_journal (SCP_JOURNAL_SECRET), not SCP_CAPABILITY_SECRET; the node
    id is kept for FA-02 continuity. A capability key alone must not be enough to journal.
    """
    test_body = {
        "event_id": "evt_test_123",
        "task_id": "task_123",
        "seq": 1,
        "type": "TASK_CREATED",
    }
    monkeypatch.setenv("SCP_CAPABILITY_SECRET", "valid-capability-key-32-bytes-long")
    monkeypatch.delenv("SCP_JOURNAL_SECRET_FILE", raising=False)

    # Missing journal key -> must raise KernelError, even with a capability key present
    monkeypatch.delenv("SCP_JOURNAL_SECRET", raising=False)
    with pytest.raises(KernelError, match="SCP_JOURNAL_SECRET .* is required to compute HMAC journal hash"):
        compute_journal_event_hash(test_body)

    # Empty journal key -> must raise KernelError
    monkeypatch.setenv("SCP_JOURNAL_SECRET", "")
    with pytest.raises(KernelError, match="SCP_JOURNAL_SECRET .* is required to compute HMAC journal hash"):
        compute_journal_event_hash(test_body)

    # Valid journal key -> returns HMAC SHA256 hex string
    monkeypatch.setenv("SCP_JOURNAL_SECRET", "valid-journal-key-32-bytes-long-now")
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

    # Truthy values like "true" must also be accepted
    monkeypatch.setenv("SCP_FORCE_HTTPS", "true")
    enforce_production_safety()  # Should not raise
