"""Tests for GA B60 L3 Remediation: L3-F2, L3-F3, L3-F4 (Zero-Mock Real Execution).

Verifies:
- L3-F3: Epoch Rollback prevention: capability_state.json and data/security paths
  are strictly classified as sensitive, protected against pc.write_file and shell reads.
- L3-F4: Self-Minting Confirmation prevention: POST /v3/hands/confirmations strictly
  requires x-scp-admin-key and cannot be self-attested with only pc token.
- L3-F2: TaskKernelHandsBridge delegates controller property to executor to avoid
  AttributeError 500 when attaching governor grants for pc.write_file.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from scp.api.routes.hands_routes import ConfirmationRecordRequest, hands_record_confirmation
from scp.core.safe_process import _validate_posix_shell_call, _validate_powershell_call
from scp.hands.hands_executor import HandsExecutor
from scp.hands.planner import HandsPlanner
from scp.hands.task_kernel_bridge import TaskKernelHandsBridge
from scp.pc_control.pc_controller import PCController


@pytest.mark.asyncio
async def test_l3_f3_capability_state_cannot_be_overwritten_via_write_file(tmp_path: Path):
    """L3-F3: Agent cannot overwrite capability_state.json via pc.write_file to rollback revocation epoch."""
    controller = PCController(working_dir=tmp_path)
    token = controller.capability_authority.issue("pc.write_file")

    # 1. Attempt writing to data/security/capability_state.json inside workspace
    res1 = await controller.write_file(
        path=str(tmp_path / "data" / "security" / "capability_state.json"),
        content='{"epoch": 1, "revoked": false}',
        capability_token=token,
        capability_level=3,
        approved=True,
        confirmation_id="cid-dummy",
    )
    assert res1["success"] is False
    assert "Sensitive path cannot be written" in res1["error"]

    # 2. Attempt writing directly to capability_state.json anywhere inside workspace
    res2 = await controller.write_file(
        path=str(tmp_path / "capability_state.json"),
        content='{"epoch": 1, "revoked": false}',
        capability_token=token,
        capability_level=3,
        approved=True,
        confirmation_id="cid-dummy",
    )
    assert res2["success"] is False
    assert "Sensitive path cannot be written" in res2["error"]

    # 3. Verify security path inside working_dir is also rejected
    res3 = await controller.write_file(
        path=str(tmp_path / "security" / "state.json"),
        content='{"hacked": true}',
        capability_token=token,
        capability_level=3,
        approved=True,
        confirmation_id="cid-dummy",
    )
    assert res3["success"] is False
    assert "Sensitive path cannot be written" in res3["error"]


def test_l3_f3_safe_process_blocks_unauthorized_security_file_access():
    """L3-F3: safe_process PowerShell and POSIX validators block reading capability_state.json."""
    # 1. PowerShell validator check
    with pytest.raises(PermissionError, match="reading sensitive file/path"):
        _validate_powershell_call(
            ["powershell.exe", "-Command", "Get-Content data/security/capability_state.json"],
            token=None,
        )

    # 2. POSIX shell validator check
    with pytest.raises(PermissionError, match="reading sensitive file/path"):
        _validate_posix_shell_call(
            ["sh", "-c", "cat data/security/capability_state.json"],
            token=None,
        )


@pytest.mark.asyncio
async def test_l3_f4_self_mint_confirmation_rejected_without_admin_key(tmp_path: Path, monkeypatch):
    """L3-F4: POST /v3/hands/confirmations rejects self-minting when caller lacks x-scp-admin-key."""
    pc_token = "test-pc-token-secret"
    admin_key = "test-admin-key-secret"
    monkeypatch.setenv("SCP_PC_CONTROLLER_TOKEN", pc_token)
    monkeypatch.setenv("SCP_ADMIN_KEY", admin_key)

    dummy_request = Request({"type": "http", "method": "POST", "headers": []})
    req_payload = ConfirmationRecordRequest(action="pc.write_file", target="workspace/main.py")

    # 1. Caller provides valid PC token but NO admin key -> FAIL-CLOSED 403
    with pytest.raises(HTTPException) as exc_info:
        await hands_record_confirmation(
            payload=req_payload,
            request=dummy_request,
            x_scp_pc_token=pc_token,
            x_scp_admin_key=None,
        )
    assert exc_info.value.status_code == 403
    assert "Operator confirmation requires admin authority" in exc_info.value.detail

    # 2. Caller provides valid PC token but WRONG admin key -> FAIL-CLOSED 403
    with pytest.raises(HTTPException) as exc_info:
        await hands_record_confirmation(
            payload=req_payload,
            request=dummy_request,
            x_scp_pc_token=pc_token,
            x_scp_admin_key="attacker-forged-key",
        )
    assert exc_info.value.status_code == 403
    assert "Operator confirmation requires admin authority" in exc_info.value.detail

    # 3. Caller provides valid PC token AND valid admin key -> 200 SUCCESS
    result = await hands_record_confirmation(
        payload=req_payload,
        request=dummy_request,
        x_scp_pc_token=pc_token,
        x_scp_admin_key=admin_key,
    )
    assert result["success"] is True
    assert result["confirmation_id"].startswith("conf-")


def test_l3_f2_hands_bridge_delegates_controller_to_executor(tmp_path: Path):
    """L3-F2: TaskKernelHandsBridge must expose controller property so governor grant attachment succeeds without 500."""
    executor = HandsExecutor(data_dir=tmp_path)
    bridge = TaskKernelHandsBridge(executor)

    # 1. Controller property existence and delegation parity
    assert hasattr(bridge, "controller")
    assert bridge.controller is executor.controller

    # 2. End-to-end execution of governor grant attachment in planner
    planner = HandsPlanner(bridge)
    step = {"action": "pc.write_file", "params": {"path": "script.py"}}
    plan = {"planId": "plan-123", "stepId": "step-001"}

    # Must NOT raise AttributeError: 'TaskKernelHandsBridge' object has no attribute 'controller'
    planner._attach_governor_grant(step, plan)

    assert "confirmationId" in step
    assert step["confirmationId"].startswith("gov-")
    assert step["params"]["confirmation_id"] == step["confirmationId"]
