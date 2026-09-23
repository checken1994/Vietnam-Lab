# SCP CIRCUIT: M04 — STATUS: CLOSED_WITH_KNOWN_GAP (closure: docs/evidence-summary/M04-closure.json)
"""SCP V3.1 PC Controller.

This module is intentionally conservative: the model proposes actions, while
this controller enforces capability levels, workspace boundaries, audit logs,
backups and a kill switch before touching the Windows host.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass
from enum import IntEnum
from pathlib import Path
from typing import Any

from scp.core.capability_token import InvalidTokenSignatureError
from scp.security.capability_epoch import (
    CapabilityAuthority,
    CapabilityRevokedError,
    CapabilityToken,
    parse_capability_token,
)

logger = logging.getLogger("scp.pc_controller")


class CapabilityLevel(IntEnum):
    READ_ONLY = 0
    DRY_RUN = 1
    SANDBOX = 2
    WORKSPACE = 3
    REAL = 4
    PRIVILEGED = 5


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    reason: str
    risk: str
    requires_approval: bool
    capability_level: int


class PCController:
    """Controlled local-PC executor for SCP's observe-plan-act-verify loop.

    The controller does not grant the LLM a shell. Commands are classified by
    an allowlist and executed through PowerShell without ``shell=True``. High
    capability levels require explicit approval and every action is appended to
    an audit JSONL ledger.
    """

    BLOCKED_PATTERNS = (
        r"\bformat\s+[a-z]:",
        r"\bshutdown\b",
        r"\breg\s+delete\b",
        r"\bnet\s+user\b.*\b(delete|/delete)\b",
        r"\b(remove|del|erase)\b.*\s(-recurse|-force|/s|/q)\b",
        r"\brm\s+-rf\b",
        r"\bcredential(s)?\b.*\b(export|dump|steal)\b",
        r"\b(invoke-webrequest|invoke-restmethod|irm|curl|wget)\b.*(\b(iex|invoke-expression)\b|https?://)",
        r"\b(iex|invoke-expression)\b",
        r"\b(encodedcommand|enc)\b",
        r"\bset-executionpolicy\b",
    )
    READ_ONLY_PATTERNS = (
        r"^\s*(dir|ls|get-childitem)(\s|$)",
        r"^\s*(type|cat|get-content)(\s|$)",
        r"^\s*git\s+(status|diff|log|show|branch)(\s|$)",
        r"^\s*(where|whoami|hostname|tasklist|netstat)(\s|$)",
        r"^\s*(get-service|sc(\.exe)?\s+query)(\s|$)",
        r"^\s*(python|python3|bun|node)\s+(-{0,2}(version|help))(\s|$)",
        r"^\s*git\s+diff\s+--check(\s|$)",
    )
    WORKSPACE_PATTERNS = (
        r"^\s*(bun|npm)\s+run\s+(build|lint|test)(\s|$)",
        r"^\s*(python|python3)\s+-m\s+(pytest|compileall)(\s|$)",
        r"^\s*pytest(\s|$)",
        r"^\s*git\s+diff\s+--check(\s|$)",
    )
    SENSITIVE_PARTS = {".env", ".private-secrets", "credentials", "secrets"}

    def __init__(
        self,
        working_dir: str | Path | None = None,
        capability_authority: CapabilityAuthority | None = None,
    ) -> None:
        project_root = Path(__file__).resolve().parents[2]
        configured = working_dir or os.environ.get("SCP_PC_WORKING_DIR")
        self.working_dir = self._resolve_path(configured or project_root)
        self.data_dir = self.working_dir / "data" / "pc_controller"
        self.audit_path = self.data_dir / "audit.jsonl"
        self.backup_dir = self.data_dir / "backups"
        self.kill_switch_path = self.data_dir / "KILL_SWITCH"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self.capability_authority = capability_authority or CapabilityAuthority(
            self.data_dir / "capability_state.json"
        )

    def _verify_token(self, token: Any, required_action: str = "pc.execute") -> CapabilityToken:
        """Strict Zero-Trust Policy Enforcement Point (PEP) for PCController.

        Validates HMAC-SHA256 signature, epoch, and subject scope fail-closed.
        Raises InvalidTokenSignatureError or PermissionError immediately if invalid.
        """
        if token is None or token == "":
            self._audit("TOKEN_REJECTED", {"action": required_action, "reason": "missing_token"})
            raise PermissionError(
                f"CapabilityRequiredError: Action '{required_action}' requires an authorized capability token (FA-05)"
            )

        parsed = parse_capability_token(token)
        if parsed is None:
            self._audit("TOKEN_REJECTED", {"action": required_action, "reason": "invalid_format"})
            raise InvalidTokenSignatureError(
                "InvalidCapabilityTokenError: Capability token format is invalid (FA-04)"
            )

        # Cryptographic signature check (raises InvalidTokenSignatureError if tampered/unsigned)
        try:
            is_valid = self.capability_authority.validate(parsed, required_subject=None)
        except InvalidTokenSignatureError as exc:
            self._audit(
                "TOKEN_REJECTED",
                {
                    "action": required_action,
                    "reason": "signature_verification_failed",
                    "token_id": getattr(parsed, "token_id", ""),
                },
            )
            raise exc

        if not is_valid:
            self._audit(
                "TOKEN_REJECTED",
                {
                    "action": required_action,
                    "reason": "revoked_or_stale_epoch",
                    "token_id": getattr(parsed, "token_id", ""),
                    "epoch": getattr(parsed, "epoch", -1),
                },
            )
            raise PermissionError("CapabilityRevokedError: Capability token is revoked or epoch is stale")

        subject = str(getattr(parsed, "subject", "")).strip()
        allowed_scopes: dict[str, set[str]] = {
            "pc.execute": {"pc.execute", "pc:execute", "hands:execute", "hands:pc.execute"},
            "pc.write_file": {"pc.write_file", "pc:write_file", "hands:pc.write_file"},
            "pc.read_file": {"pc.read_file", "pc:read_file", "hands:pc.read_file"},
            "pc.rollback": {"pc.rollback", "pc:rollback", "hands:rollback", "hands:pc.rollback"},
            "pc.clear_kill_switch": {"pc.clear_kill_switch", "pc:clear_kill_switch", "hands:admin", "hands:pc.clear_kill_switch"},
        }

        is_scope_valid = False
        valid_set = allowed_scopes.get(required_action, {required_action})
        if subject in valid_set:
            is_scope_valid = True
        elif required_action == "pc.execute" and subject.startswith("hands:pc."):
            is_scope_valid = True

        if not is_scope_valid:
            self._audit("TOKEN_REJECTED", {"action": required_action, "reason": "scope_mismatch", "subject": subject})
            raise PermissionError(
                f"CapabilityScopeMismatchError: Token subject '{subject}' does not permit action '{required_action}' (INV-AUTH-02)"
            )

        return parsed

    def _resolve_path(self, value: str | Path) -> Path:
        path = Path(value).expanduser().resolve()
        return path

    def _inside_root(self, path: Path) -> bool:
        try:
            path.relative_to(self.working_dir)
            return True
        except ValueError:
            logger.debug('PCController._inside_root: ValueError ignored', exc_info=True)
            return False

    def _sensitive(self, path: Path) -> bool:
        return any(part.lower() in self.SENSITIVE_PARTS for part in path.parts)

    # P2-FIX-AUDIT: return a durable result; callers must fail closed.
    def _audit(self, event: str, payload: dict[str, Any]) -> bool:
        record = {
            "timestamp": time.time(),
            "iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "event": event,
            **payload,
        }
        try:
            with self.audit_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            return True
        except OSError:
            logger.exception("Unable to append PC controller audit entry")
            return False

    def kill_switch_engaged(self) -> bool:
        return self.kill_switch_path.exists()

    def status(self) -> dict[str, Any]:
        audit_count = 0
        if self.audit_path.exists():
            try:
                audit_count = sum(1 for _ in self.audit_path.open("r", encoding="utf-8"))
            except OSError:
                logger.debug('PCController.status: OSError ignored', exc_info=True)
                audit_count = -1
        return {
            "controller": "online",
            "version": "3.1",
            "workingDir": str(self.working_dir),
            "killSwitch": self.kill_switch_engaged(),
            "auditEntries": audit_count,
            "capabilityLevels": {level.name: int(level) for level in CapabilityLevel},
            "policy": "allowlist + explicit approval + audit + backup",
        }

    def evaluate(self, command: str, capability_level: int, approved: bool = False) -> PolicyDecision:
        if "\n" in command or "\r" in command:
            return PolicyDecision(False, "Multiline commands are not allowed", "critical", False, capability_level)
        command = command.strip()
        if not command:
            return PolicyDecision(False, "Empty command", "low", False, capability_level)
        if self.kill_switch_engaged():
            return PolicyDecision(False, "Kill switch is engaged", "critical", False, capability_level)
        # A read-only prefix must describe the whole command. Reject shell
        # separators/substitution before applying the prefix allowlist so a
        # payload such as `whoami ; Start-Process ...` cannot smuggle a second
        # command through the read-only regex.
        if re.search(r"(?:;|&&|\|\||\||`|\$\(|\$\{)", command):
            return PolicyDecision(False, "Command chaining is not allowed", "critical", False, capability_level)
        try:
            level = CapabilityLevel(max(0, min(5, int(capability_level))))
        except (TypeError, ValueError):
            logger.debug('PCController.evaluate: TypeError, ValueError ignored', exc_info=True)
            return PolicyDecision(False, "Invalid capability level", "high", False, capability_level)
        if any(re.search(pattern, command, re.IGNORECASE) for pattern in self.BLOCKED_PATTERNS):
            return PolicyDecision(False, "Command matches a blocked safety pattern", "critical", False, int(level))
        if any(re.search(pattern, command, re.IGNORECASE) for pattern in self.READ_ONLY_PATTERNS):
            return PolicyDecision(True, "Read-only allowlist", "low", False, int(level))
        if level < CapabilityLevel.WORKSPACE:
            return PolicyDecision(False, "Command is not read-only at this capability level", "medium", False, int(level))
        if not any(re.search(pattern, command, re.IGNORECASE) for pattern in self.WORKSPACE_PATTERNS):
            return PolicyDecision(False, "Command is outside the workspace allowlist", "high", True, int(level))
        if not approved:
            return PolicyDecision(False, "Explicit approval required for workspace execution", "medium", True, int(level))
        return PolicyDecision(True, "Workspace allowlist + approval", "medium", True, int(level))

    def plan(self, command: str, capability_level: int = 0, approved: bool = False) -> dict[str, Any]:
        decision = self.evaluate(command, capability_level, approved)
        result = {"command": command, "decision": asdict(decision), "workingDir": str(self.working_dir)}
        self._audit("PLAN", result)
        return result

    def _run_sync(self, command: str, timeout: int) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            completed = subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", command],
                cwd=str(self.working_dir),
                capture_output=True,
                text=True,
                timeout=max(1, min(timeout, 300)),
                shell=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return {
                "success": completed.returncode == 0,
                "returnCode": completed.returncode,
                "stdout": completed.stdout[-10000:],
                "stderr": completed.stderr[-5000:],
                "durationMs": round((time.perf_counter() - started) * 1000),
            }
        except subprocess.TimeoutExpired as exc:
            return {"success": False, "returnCode": None, "stdout": str(exc.stdout or "")[-5000:], "stderr": "timeout", "durationMs": round((time.perf_counter() - started) * 1000)}
        except OSError as exc:
            return {"success": False, "returnCode": None, "stdout": "", "stderr": str(exc), "durationMs": round((time.perf_counter() - started) * 1000)}

    async def execute(
        self,
        command: str,
        capability_token: CapabilityToken | str | dict[str, Any] | int | None = None,
        capability_level: int = 0,
        approved: bool = False,
        timeout: int = 120,
    ) -> dict[str, Any]:
        if isinstance(capability_token, (int, CapabilityLevel)):
            capability_level = int(capability_token)
            capability_token = None

        token_obj = self._verify_token(capability_token, "pc.execute")

        decision = self.evaluate(command, capability_level, approved)
        base = {
            "command": command,
            "decision": asdict(decision),
            "workingDir": str(self.working_dir),
            "tokenId": token_obj.token_id,
            "epoch": token_obj.epoch,
        }
        if not decision.allowed:
            self._audit("BLOCK", base)
            return {**base, "success": False, "output": "", "error": decision.reason}
        if not self._audit("EXECUTE_INTENT", {**base, "timeout": timeout}):
            return {**base, "success": False, "output": "", "error": "Audit storage unavailable; action blocked", "auditStatus": "DB_WRITE_FAILED"}
        result = await asyncio.to_thread(self._run_sync, command, timeout)
        post_audit_ok = self._audit("EXECUTE", {**base, **result})
        if not post_audit_ok:
            return {**base, **result, "executed": True, "auditStatus": "DB_WRITE_FAILED", "error": "Audit write failed after execution"}
        return {**base, **result, "auditStatus": "OK"}

    async def read_file(
        self,
        path: str,
        max_bytes: int = 200_000,
        capability_token: CapabilityToken | str | dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not isinstance(max_bytes, int) and capability_token is None:
            capability_token = max_bytes
            max_bytes = 200_000

        token_obj = self._verify_token(capability_token, "pc.read_file")

        target = self._resolve_path(path)
        if not self._inside_root(target):
            return {"success": False, "error": "Path is outside SCP workspace"}
        if self._sensitive(target):
            return {"success": False, "error": "Sensitive path is not readable by this endpoint"}
        try:
            content = target.read_text(encoding="utf-8")[:max_bytes]
            result = {
                "success": True,
                "path": str(target),
                "content": content,
                "truncated": target.stat().st_size > max_bytes,
                "tokenId": token_obj.token_id,
            }
        except OSError as exc:
            result = {"success": False, "path": str(target), "error": str(exc), "tokenId": token_obj.token_id}
        self._audit("READ_FILE", {key: value for key, value in result.items() if key != "content"})
        return result

    async def write_file(
        self,
        path: str,
        content: str,
        capability_token: CapabilityToken | str | dict[str, Any] | int | None = None,
        capability_level: int = 0,
        approved: bool = False,
    ) -> dict[str, Any]:
        if isinstance(capability_token, (int, CapabilityLevel)):
            capability_level = int(capability_token)
            capability_token = None

        token_obj = self._verify_token(capability_token, "pc.write_file")

        target = self._resolve_path(path)
        if not self._inside_root(target):
            return {"success": False, "error": "Path is outside SCP workspace"}
        if self._sensitive(target):
            return {"success": False, "error": "Sensitive path cannot be written by this endpoint"}
        if self.kill_switch_engaged() or capability_level < CapabilityLevel.WORKSPACE or not approved:
            return {"success": False, "error": "Write requires capability >= 3 and explicit approval"}
        content_hash = hashlib.sha256(content.encode("utf-8", "replace")).hexdigest()
        intent = {
            "path": str(target),
            "bytes": len(content.encode("utf-8")),
            "content_sha256": content_hash,
            "capability_level": int(capability_level),
            "tokenId": token_obj.token_id,
            "epoch": token_obj.epoch,
        }
        if not self._audit("WRITE_FILE_INTENT", intent):
            return {"success": False, "error": "Audit storage unavailable; write blocked", "auditStatus": "DB_WRITE_FAILED"}
        target.parent.mkdir(parents=True, exist_ok=True)
        backup_id = uuid.uuid4().hex
        backup_path = self.backup_dir / f"{backup_id}.bak"
        existed = target.exists()
        if existed:
            shutil.copy2(target, backup_path)
        fd, temp_name = tempfile.mkstemp(prefix="scp-write-", suffix=".tmp", dir=str(target.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, target)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
        result = {
            "success": True,
            "path": str(target),
            "backupId": backup_id if existed else None,
            "bytes": len(content.encode("utf-8")),
            "content_sha256": content_hash,
            "tokenId": token_obj.token_id,
        }
        if not self._audit("WRITE_FILE", result):
            try:
                if existed:
                    shutil.copy2(backup_path, target)
                else:
                    target.unlink(missing_ok=True)
            except OSError:
                logger.exception("Write rollback failed after audit failure")
            return {"success": False, "path": str(target), "error": "Audit write failed; write rolled back", "auditStatus": "DB_WRITE_FAILED"}
        return {**result, "auditStatus": "OK"}

    async def rollback(
        self,
        backup_id: str,
        approved: bool = False,
        capability_level: int = 3,
        capability_token: CapabilityToken | str | dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        token_obj = self._verify_token(capability_token, "pc.rollback")
        if not approved or capability_level < CapabilityLevel.WORKSPACE:
            return {"success": False, "error": "Rollback requires explicit approval and capability >= 3"}
        backup = self.backup_dir / f"{backup_id}.bak"
        if not backup.exists():
            return {"success": False, "error": "Backup not found"}
        return {"success": False, "error": "Rollback target metadata is not available in this backup format; use audit entry to select a target"}

    def engage_kill_switch(self, reason: str = "user requested") -> dict[str, Any]:
        reason_hash = hashlib.sha256(reason.encode("utf-8", "replace")).hexdigest()
        if not self._audit("KILL_SWITCH_INTENT", {"reason_sha256": reason_hash}):
            return {"success": False, "killSwitch": self.kill_switch_engaged(), "error": "Audit storage unavailable; kill-switch action blocked", "auditStatus": "DB_WRITE_FAILED"}
        self.kill_switch_path.write_text(json.dumps({"reason": reason, "timestamp": time.time()}, ensure_ascii=False), encoding="utf-8")
        if not self._audit("KILL_SWITCH_ENGAGED", {"reason_sha256": reason_hash}):
            return {"success": True, "killSwitch": True, "reason": reason, "auditStatus": "DB_WRITE_FAILED"}
        return {"success": True, "killSwitch": True, "reason": reason, "auditStatus": "OK"}

    def clear_kill_switch(
        self,
        approved: bool = False,
        capability_token: CapabilityToken | str | dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        token_obj = self._verify_token(capability_token, "pc.clear_kill_switch")
        if not approved:
            return {"success": False, "error": "Clearing kill switch requires explicit approval"}
        if not self._audit("KILL_SWITCH_CLEAR_INTENT", {"tokenId": token_obj.token_id}):
            return {"success": False, "killSwitch": True, "error": "Audit storage unavailable; clear blocked", "auditStatus": "DB_WRITE_FAILED"}
        self.kill_switch_path.unlink(missing_ok=True)
        if not self._audit("KILL_SWITCH_CLEARED", {"tokenId": token_obj.token_id}):
            self.kill_switch_path.write_text(json.dumps({"reason": "audit failure fail-closed", "timestamp": time.time()}, ensure_ascii=False), encoding="utf-8")
            return {"success": False, "killSwitch": True, "error": "Audit write failed; kill switch re-engaged", "auditStatus": "DB_WRITE_FAILED"}
        return {"success": True, "killSwitch": False, "auditStatus": "OK"}
