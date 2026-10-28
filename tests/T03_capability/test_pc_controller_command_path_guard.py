"""Regression tests for the PCController command path guard.

[AUDIT-FIX 2026-09-24] External audit probe PROVED that READ_ONLY_PATTERNS
allowed `evaluate('type .env', capability_level=0)` and that
`execute('type .env')` returned the file content: the sensitive-path policy
(_sensitive/_inside_root) was only applied to read_file/write_file, never to
command evaluation. A PCController token holder could read .env and any
absolute path through the read-only command allowlist.

Fix contract: for file-read verbs (type|cat|get-content) the referenced path
arguments must pass the SAME sensitive-path / workspace-root validation as
read_file. A violation denies with CapabilityScopeMismatchError semantics,
regardless of capability level. FA-09: these tests are the exploit scenarios
proving both the old failure and the new fail-closed behavior.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from scp.pc_control.pc_controller import PCController
from scp.security.capability_epoch import CapabilityAuthority


@pytest.fixture()
def controller(tmp_path: Path) -> tuple[PCController, Path]:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "notes.txt").write_text("benign content", encoding="utf-8")
    (workspace / ".env").write_text("SECRET_VALUE=1", encoding="utf-8")
    (workspace / "sub").mkdir(exist_ok=True)
    (workspace / "sub" / "report.txt").write_text("report", encoding="utf-8")
    authority = CapabilityAuthority(tmp_path / "capability_state.json")
    controller = PCController(working_dir=workspace, capability_authority=authority)
    return controller, workspace


def test_type_dotenv_denied_at_read_only_level(controller):
    """`type .env` must be denied even at capability_level 0 (read-only)."""
    pc, _ws = controller
    decision = pc.evaluate("type .env", 0)
    assert decision.allowed is False
    assert "CapabilityScopeMismatchError" in decision.reason
    assert "sensitive" in decision.reason


def test_type_dotenv_denied_even_when_approved_high_level(controller):
    """No capability level or approval makes a sensitive read acceptable."""
    pc, _ws = controller
    for level in (0, 2, 3, 5):
        decision = pc.evaluate("type .env", level, approved=True)
        assert decision.allowed is False, f"level={level}"


def test_type_workspace_file_allowed(controller):
    """Legitimate read-only reads inside the workspace keep working."""
    pc, _ws = controller
    for cmd in ("type notes.txt", "cat notes.txt", "get-content notes.txt", "type sub/report.txt"):
        decision = pc.evaluate(cmd, 0)
        assert decision.allowed is True, f"{cmd}: {decision.reason}"


def test_get_content_absolute_sensitive_path_denied(controller):
    """`get-content <abs>/.env` (absolute path) must be denied."""
    pc, ws = controller
    decision = pc.evaluate(f"get-content {ws.parent / 'outside' / '.env'}", 0)
    assert decision.allowed is False
    assert "CapabilityScopeMismatchError" in decision.reason


def test_get_content_absolute_outside_root_denied(controller):
    """Absolute non-sensitive paths outside the workspace root are denied too."""
    pc, _ws = controller
    decision = pc.evaluate("get-content C:/Windows/win.ini", 0)
    assert decision.allowed is False
    assert "outside" in decision.reason


def test_type_parent_traversal_denied(controller):
    pc, _ws = controller
    decision = pc.evaluate("type ..\\.env", 0)
    assert decision.allowed is False


def test_type_quoted_absolute_path_denied(controller):
    """Quoted absolute paths (spaces) must not slip past tokenized extraction."""
    pc, _ws = controller
    decision = pc.evaluate('type "C:/Program Files/secret file.txt"', 0)
    assert decision.allowed is False


def test_non_path_read_only_commands_unchanged(controller):
    """Read-only commands without file-read verbs keep the old behavior."""
    pc, _ws = controller
    for cmd in ("git status", "whoami", "hostname", "tasklist"):
        decision = pc.evaluate(cmd, 0)
        assert decision.allowed is True, f"{cmd}: {decision.reason}"


def test_execute_blocks_sensitive_read_command(controller):
    """execute() fail-closes on `type .env` — the command must never run."""
    pc, _ws = controller
    with pytest.raises(PermissionError):
        asyncio.run(pc.execute("type .env", capability_token=None))


# ---------------------------------------------------------------------------
# [R9 fix 2026-10-28] Runtime-resolved path arguments and sensitive material.
#
# REAL probe evidence (pre-fix, verified against the live guard): all ten
# commands in R9_PROBE_COMMANDS were ALLOWED by evaluate() — '$HOME' /
# '$env:USERPROFILE' tokens survive as literal directory names under the
# workspace after resolution ('Get-Content $HOME/.ssh/id_rsa' ALLOWED),
# wildcard characters survive as literal filename chars ('Get-Content *.env'
# masked .env from SENSITIVE_PARTS matching; 'Get-Content *' read the whole
# workspace root), and the literal sensitive reads slipped because
# SENSITIVE_PARTS lacked .ssh/.git/id_rsa/... Fail-closed fix:
# (a) any path token carrying '$' or a glob wildcard (* ? [) is denied with
#     'Path variables/wildcards not permitted; pass literal path';
# (b) SENSITIVE_PARTS now covers runtime credential/config material;
# (c) outside-root is checked before sensitive (same order as read_file PEP),
#     so '~' keeps its expanduser → outside-root deny path.
# FA-09: ADD-ONLY block — no assertion above this line was changed.
# ---------------------------------------------------------------------------

R9_PROBE_COMMANDS = (
    "Get-Content $HOME/.ssh/id_rsa",
    "cat $env:USERPROFILE\\.ssh\\id_rsa",
    "Get-Content *.env",
    "Get-Content *",
    "type $HOME/.aws/credentials",
    "get-content $env:USERPROFILE\\.git\\config",
    "Get-Content ?.env",
    "Get-Content [a-z].env",
    "Get-Content -Path:$HOME/.ssh/id_rsa",
    'Get-Content "$HOME/.ssh/id_rsa"',
)


@pytest.mark.parametrize("command", R9_PROBE_COMMANDS)
def test_r9_probe_commands_denied(controller, command):
    """All ten REAL probe commands that slipped through evaluate() are denied."""
    pc, _ws = controller
    for level in (0, 3, 5):
        decision = pc.evaluate(command, level, approved=True)
        assert decision.allowed is False, f"{command!r} level={level}: {decision.reason}"


def test_r9_variable_or_wildcard_deny_reason(controller):
    """The deny reason names the R9 rule: literal path required."""
    pc, _ws = controller
    for command in ("Get-Content $HOME/.ssh/id_rsa", "Get-Content *"):
        decision = pc.evaluate(command, 0)
        assert decision.allowed is False
        assert "Path variables/wildcards not permitted; pass literal path" in decision.reason


def test_r9_literal_sensitive_parts_denied(controller):
    """Literal in-workspace reads of runtime credential material are denied."""
    pc, _ws = controller
    for rel in (
        ".ssh/id_rsa", ".git/config", ".aws/credentials", ".gnupg/secring.gpg",
        ".kube/config", ".azure/accessTokens.json", ".docker/config.json",
        "id_ed25519", "known_hosts", "authorized_keys", "_netrc", ".npmrc", ".pypirc",
    ):
        decision = pc.evaluate(f"type {rel}", 0)
        assert decision.allowed is False, f"{rel}: {decision.reason}"
        assert "CapabilityScopeMismatchError" in decision.reason


def test_r9_positive_controls_unchanged(controller):
    """Positive controls: benign literal read stays allowed; `~` stays on the
    expanduser → outside-root deny path (NOT the variable/wildcard deny)."""
    pc, _ws = controller
    decision = pc.evaluate("type notes.txt", 0)
    assert decision.allowed is True, decision.reason
    decision = pc.evaluate("type ~/.ssh/id_rsa", 0)
    assert decision.allowed is False
    assert "outside" in decision.reason
    assert "wildcards" not in decision.reason


@pytest.mark.parametrize(
    "command",
    (
        "type $HOME/.ssh/id_rsa",
        "cat $env:USERPROFILE\\.ssh\\id_rsa",
        "get-content *.env",
        "type ?.txt",
        "type [abc].txt",
        'type "$HOME/.ssh/id_rsa"',
        "type sub/*.txt",
    ),
)
def test_r9_property_flagged_candidate_forces_deny(controller, command):
    """Property: whenever _command_read_paths yields a candidate whose text
    contains '$', '*', '?' or '[', evaluate() must deny that command."""
    pc, _ws = controller
    candidates = pc._command_read_paths(command)
    assert candidates, f"extraction regression: no candidates for {command!r}"
    flagged = [
        str(candidate) for candidate in candidates
        if "$" in str(candidate) or any(ch in str(candidate) for ch in "*?[")
    ]
    assert flagged, f"property setup: expected a flagged candidate for {command!r}"
    decision = pc.evaluate(command, 0)
    assert decision.allowed is False, f"{command!r}: {decision.reason}"
