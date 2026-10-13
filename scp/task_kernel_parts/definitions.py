"""Shared state-machine definitions, exceptions, and helpers for TaskKernel."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

STATES = {
    "CREATED", "PLANNING", "READY", "QUEUED", "LEASED", "RUNNING",
    "WAITING_TOOL", "VERIFYING", "CHECKPOINTED", "UNKNOWN", "RECOVERING",
    "RECONCILING", "HUMAN_REVIEW", "RETRY_SCHEDULED", "COMPLETED",
    "FAILED", "CANCELLED",
}
TERMINAL = {"COMPLETED", "FAILED", "CANCELLED"}
ALLOWED_TRANSITIONS = {
    "CREATED": {"PLANNING", "CANCELLED"},
    "PLANNING": {"READY", "WAITING_APPROVAL", "FAILED", "CANCELLED"},
    "WAITING_APPROVAL": {"READY", "CANCELLED"},
    "READY": {"QUEUED", "CANCELLED"},
    "QUEUED": {"LEASED", "CANCELLED"},
    "LEASED": {"RUNNING", "RECOVERING", "CANCELLED"},
    # [Agent2 2026-09-29] RETRY_SCHEDULED added to execution states: the
    # retry branch of commit_failed() (pinned by b5/autonomous/gap13 tests)
    # commits it from RUNNING/WAITING_TOOL/VERIFYING; the map now agrees.
    "RUNNING": {"WAITING_TOOL", "VERIFYING", "CHECKPOINTED", "RECOVERING", "HUMAN_REVIEW", "RETRY_SCHEDULED", "FAILED", "CANCELLED"},
    "WAITING_TOOL": {"VERIFYING", "UNKNOWN", "RECOVERING", "RETRY_SCHEDULED", "FAILED", "CANCELLED"},
    "VERIFYING": {"RUNNING", "COMPLETED", "HUMAN_REVIEW", "RETRY_SCHEDULED", "FAILED"},
    "CHECKPOINTED": {"RUNNING", "QUEUED", "CANCELLED"},
    "UNKNOWN": {"RECONCILING", "HUMAN_REVIEW", "RECOVERING", "FAILED", "CANCELLED"},
    "HUMAN_REVIEW": {"READY", "CANCELLED", "FAILED"},
    "RECOVERING": {"RECONCILING", "CHECKPOINTED", "QUEUED", "HUMAN_REVIEW", "FAILED"},
    "RECONCILING": {"RECOVERING", "CHECKPOINTED", "QUEUED", "HUMAN_REVIEW", "FAILED", "CANCELLED"},
    "RETRY_SCHEDULED": {"QUEUED", "FAILED", "CANCELLED"},
    "COMPLETED": set(),
    "FAILED": set(),
    "CANCELLED": set(),
}


class KernelError(RuntimeError):
    pass


class InvalidTransition(KernelError):
    pass


class StaleLease(KernelError):
    pass


class OptimisticLockError(StaleLease):
    """Raised when an atomic OCC version check fails (0 rows updated due to version mismatch)."""

    def __init__(
        self,
        message: str = "",
        *,
        table: str | None = None,
        entity_id: str | None = None,
        expected_version: int | None = None,
    ) -> None:
        self.table = table
        self.entity_id = entity_id
        self.expected_version = expected_version
        if not message:
            message = (
                f"Optimistic lock conflict on table '{table}' for entity '{entity_id}'"
                f" (expected version {expected_version})"
            )
        elif entity_id and entity_id not in message:
            message = f"{message} (table={table}, entity_id={entity_id}, expected_version={expected_version})"
        super().__init__(message)


class KillSwitchActive(KernelError):
    pass


class CheckpointCorrupt(KernelError):
    pass


class NotFound(KernelError):
    pass


@dataclass(frozen=True)
class Lease:
    lease_id: str
    task_id: str
    attempt_id: str
    worker_id: str
    expires_at: float
    fencing_token: int
    global_kill_epoch: int


@dataclass(frozen=True)
class RecoveryDecision:
    decision: str
    reason: str
    safe_to_retry: bool
    required_evidence: tuple[str, ...]
    next_state: str
    escalation: str


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_hash(value: Any) -> str:
    if isinstance(value, bytes):
        raw = value
    else:
        raw = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def compute_journal_event_hash(body: dict[str, Any]) -> str:
    key = os.environ.get("SCP_CAPABILITY_SECRET", "").encode() or b"scp-internal-journal-seed"
    return hmac.new(key, json.dumps(body, sort_keys=True).encode(), hashlib.sha256).hexdigest()


_SENSITIVE_KEY_MARKERS = (
    "password", "passwd", "secret", "token", "api_key", "apikey",
    "authorization", "cookie", "private_key",
)
_SENSITIVE_VALUE_PATTERNS = (
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"(?i)(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret)=\S+"),
)


def _checkpoint_contains_secret(value: Any, path: str = "checkpoint") -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key).lower().replace("-", "_")
            if any(marker in key_text for marker in _SENSITIVE_KEY_MARKERS):
                return True
            if _checkpoint_contains_secret(child, f"{path}.{key}"):
                return True
        return False
    if isinstance(value, (list, tuple, set)):
        return any(_checkpoint_contains_secret(child, f"{path}[]") for child in value)
    if isinstance(value, str):
        return any(pattern.search(value) for pattern in _SENSITIVE_VALUE_PATTERNS)
    return False


def _assert_checkpoint_safe(value: Any) -> None:
    if _checkpoint_contains_secret(value):
        raise KernelError("checkpoint contains secret material")


def as_json(value: Any) -> str:
    payload = asdict(value) if hasattr(value, "__dataclass_fields__") else value
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def verify_approval_authority(
    token: Any,
    task_id: str,
    secret: bytes,
    max_skew_seconds: float = 300.0,
) -> dict[str, Any]:
    """Verify an approval token or operator signature fail-closed (GAP-13)."""
    import hmac
    import time

    from scp.core.capability_token import (
        InvalidTokenSignatureError,
        verify_token,
        verify_token_signature,
    )
    from scp.security.capability_epoch import parse_capability_token

    if token is None or token == "":
        raise InvalidTokenSignatureError("Approval token is missing or empty (GAP-13/FA-04)")
    if isinstance(token, str) and not token.strip():
        raise InvalidTokenSignatureError("Approval token is missing or empty (GAP-13/FA-04)")

    if isinstance(token, str) and "." in token and not token.strip().startswith("{"):
        res = verify_token(token.strip(), required_scope="*")
        if not res.get("valid"):
            err_msg = res.get("error", "Invalid capability token")
            if "expired" in err_msg.lower():
                raise InvalidTransition(f"Capability token has expired: {err_msg}")
            raise InvalidTokenSignatureError(f"Invalid capability token: {err_msg}")
        payload = res.get("payload", {})
        scope = payload.get("scope", "")
        if scope not in {"approval:grant", f"approval:grant:{task_id}", "*"}:
            raise InvalidTransition(
                f"Capability token scope '{scope}' does not authorize 'approval:grant' for task '{task_id}'"
            )
        now_ts = time.time()
        iat = float(payload.get("iat", 0.0))
        if iat > now_ts + 60.0:
            raise InvalidTokenSignatureError("Token issue time is in the future")
        return {
            "token_type": "compact_mint_token",
            "token_id": str(payload.get("iat", "")),
            "actor": payload.get("iss", "unknown"),
            "scope": scope,
            "signature": token.strip().split(".", 1)[1],
        }

    cap_token = parse_capability_token(token)
    if cap_token is not None:
        if not cap_token.signature or not cap_token.signature.strip():
            raise InvalidTokenSignatureError("Capability token is unsigned (GAP-08/FA-04)")

        verify_token_signature(
            secret=secret,
            subject=cap_token.subject,
            epoch=cap_token.epoch,
            token_id=cap_token.token_id,
            issued_at=cap_token.issued_at,
            signature=cap_token.signature,
        )
        if cap_token.subject not in {"approval:grant", f"approval:grant:{task_id}", "*"}:
            raise InvalidTransition(
                f"CapabilityToken subject '{cap_token.subject}' does not authorize 'approval:grant' for task '{task_id}'"
            )
        now_ts = time.time()
        if cap_token.issued_at > now_ts + 60.0:
            raise InvalidTokenSignatureError("Capability token issued_at is in the future")
        return {
            "token_type": "capability_token_epoch",
            "token_id": cap_token.token_id,
            "actor": "capability_authority",
            "scope": cap_token.subject,
            "signature": cap_token.signature,
        }

    if isinstance(token, dict) and "signature" in token:
        actor = str(token.get("actor") or token.get("operator", "")).strip()
        sig = str(token.get("signature", "")).strip()
        ts_val = token.get("timestamp")
        task_in_token = token.get("task_id")
        if task_in_token and str(task_in_token).strip() != task_id:
            raise InvalidTransition(
                f"Operator signature task_id '{task_in_token}' does not match '{task_id}'"
            )
        if not actor or not sig or ts_val is None:
            raise InvalidTokenSignatureError("Malformed operator signature structure")
        try:
            timestamp = float(ts_val)
        except (TypeError, ValueError):
            raise InvalidTokenSignatureError("Invalid timestamp in operator signature")
        now_ts = time.time()
        if now_ts - timestamp > max_skew_seconds:
            raise InvalidTransition("Operator approval signature has expired")
        if timestamp > now_ts + 60.0:
            raise InvalidTokenSignatureError("Operator approval timestamp is in the future")

        canonical = f"operator_approval:{task_id}:{actor}:{timestamp:.6f}".encode()
        expected_sig = hmac.new(secret, canonical, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected_sig):
            raise InvalidTokenSignatureError("Operator approval signature verification failed")
        return {
            "token_type": "operator_signature",
            "token_id": f"op_{actor}_{int(timestamp)}",
            "actor": actor,
            "scope": "approval:grant",
            "signature": sig,
        }

    raise InvalidTokenSignatureError("Unsupported or malformed approval token format")

