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

# [K1-W2 2026-10-23] HC-44: WAITING_APPROVAL added as the 18th lifecycle state.
# It was already a key in ALLOWED_TRANSITIONS (PLANNING -> WAITING_APPROVAL ->
# READY/CANCELLED) and enforced via a carve-out hack in state_machine.py; the
# schema now agrees with the transition map (no more split-brain).
STATES = {
    "CREATED", "PLANNING", "WAITING_APPROVAL", "READY", "QUEUED", "LEASED",
    "RUNNING", "WAITING_TOOL", "VERIFYING", "CHECKPOINTED", "UNKNOWN",
    "RECOVERING", "RECONCILING", "HUMAN_REVIEW", "RETRY_SCHEDULED",
    "COMPLETED", "FAILED", "CANCELLED",
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
    "VERIFYING": {"VERIFYING", "RUNNING", "COMPLETED", "HUMAN_REVIEW", "RETRY_SCHEDULED", "FAILED"},
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


# [R5 · K_journal] Journal + checkpoint HMAC dùng khóa RIÊNG (SCP_JOURNAL_SECRET),
# không còn dùng chung SCP_CAPABILITY_SECRET: trước R5 ai giữ khóa capability vừa
# mint được approval token, vừa ký receipt, vừa viết lại journal hợp lệ.
# Mỗi miền có tiền tố riêng để một chữ ký checkpoint (planned_action do caller
# cung cấp) không bao giờ dùng được làm event_hash của journal.
KEYED_HASH_DOMAINS: dict[str, bytes] = {
    "journal": b"scp.journal.v1\x00",
    "checkpoint": b"scp.checkpoint.v1\x00",
}


def _split_secret_list(raw: str) -> list[str]:
    return [s.strip() for s in raw.split(",") if s.strip()]


def journal_secret() -> bytes:
    """K_journal hiện hành, đọc qua secret_registry (env hoặc SCP_JOURNAL_SECRET_FILE).

    Fail-closed khi thiếu, hoặc khi giá trị trùng SCP_CAPABILITY_SECRET (tách khóa cả giá trị).
    """
    from scp.security.secret_registry import resolve_distinct

    try:
        value = resolve_distinct("SCP_JOURNAL_SECRET", "SCP_CAPABILITY_SECRET")
    except ValueError as exc:
        raise KernelError(str(exc)) from exc
    if not value:
        raise KernelError("SCP_JOURNAL_SECRET (K_journal) is required to compute HMAC journal hash")
    return value.encode()


def previous_journal_secrets() -> list[bytes]:
    """K_journal đã nghỉ (rotation) khai báo qua SCP_PREVIOUS_JOURNAL_SECRETS (comma-separated)."""
    return [s.encode() for s in _split_secret_list(os.environ.get("SCP_PREVIOUS_JOURNAL_SECRETS", ""))]


def legacy_capability_journal_keys(kernel: Any) -> list[bytes]:
    """Khóa journal trước R5 (SCP_CAPABILITY_SECRET + SCP_PREVIOUS_CAPABILITY_SECRETS, định dạng
    không tiền tố miền) — CHỈ trong cửa sổ dual-accept, tức khi control.journal_key_migrated == 0.

    Sau khi reanchor_all_journals ký lại sạch toàn bộ sang K_journal, cờ chuyển 1 và các khóa
    này không còn được chấp nhận (cutover). Không đọc được cờ -> coi như đã cutover (fail-closed).
    """
    try:
        ctrl = kernel._control()
        if int(ctrl["journal_key_migrated"] or 0) != 0:
            return []
    except (AttributeError, KeyError, IndexError, TypeError, ValueError):
        return []
    raw = [os.environ.get("SCP_CAPABILITY_SECRET", "")]
    raw += _split_secret_list(os.environ.get("SCP_PREVIOUS_CAPABILITY_SECRETS", ""))
    return [s.strip().encode() for s in raw if s.strip()]


def _hmac_hex(key: bytes, message: bytes) -> str:
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def compute_keyed_hash(value: Any, *, domain: str) -> str:
    """[R5] HMAC-SHA256 dưới K_journal hiện hành, tách miền theo ``domain``."""
    return _hmac_hex(journal_secret(), KEYED_HASH_DOMAINS[domain] + json.dumps(value, sort_keys=True).encode())


def matches_previous_secret_hmac(
    value: Any, stored_hash: str, *, domain: str, legacy_keys: Any = ()
) -> bool:
    """stored_hash có khớp một K_journal đã nghỉ (định dạng có miền), hoặc một khóa legacy
    của cửa sổ dual-accept (định dạng trước R5, không miền)?"""
    body_bytes = json.dumps(value, sort_keys=True).encode()
    prefixed = KEYED_HASH_DOMAINS[domain] + body_bytes
    if any(hmac.compare_digest(stored_hash, _hmac_hex(k, prefixed)) for k in previous_journal_secrets()):
        return True
    return any(hmac.compare_digest(stored_hash, _hmac_hex(k, body_bytes)) for k in legacy_keys)


def compute_journal_event_hash(body: dict[str, Any]) -> str:
    return compute_keyed_hash(body, domain="journal")


def classify_journal_hash(body: dict[str, Any], stored_hash: str, legacy_keys: Any = ()) -> tuple[bool, bool]:
    """(is_keyed, via_legacy_capability_key) của một event_hash: khớp K_journal hiện hành,
    K_journal đã nghỉ, hoặc khóa capability trước R5 trong cửa sổ dual-accept (``legacy_keys``)."""
    try:
        current = compute_journal_event_hash(body)
    except KernelError:
        current = None  # thiếu K_journal -> chỉ còn các khóa đã nghỉ
    if current is not None and stored_hash == current:
        return True, False
    if matches_previous_secret_hmac(body, stored_hash, domain="journal"):
        return True, False
    if legacy_keys and matches_previous_secret_hmac(body, stored_hash, domain="journal", legacy_keys=legacy_keys):
        return True, True
    return False, False


def journal_event_hash_with_key(body: dict[str, Any], key: bytes) -> str:
    """event_hash của ``body`` dưới một K_journal cho trước (dùng bởi runtime audit độc lập)."""
    return _hmac_hex(key, KEYED_HASH_DOMAINS["journal"] + json.dumps(body, sort_keys=True).encode())


def journal_event_body(e: Any, payload: Any) -> dict[str, Any]:
    """Canonical body của một journal event dùng cho event_hash (moved từ lease_manager, R6)."""
    return {
        'event_id': e['event_id'], 'task_id': e['task_id'], 'seq': e['seq'], 'type': e['type'],
        'from_state': e['from_state'], 'to_state': e['to_state'], 'actor': e['actor'], 'reason': e['reason'],
        'payload': payload, 'policy_hash': e['policy_hash'], 'prev_event_hash': e['prev_event_hash'],
    }


def all_events_provable_under_previous_secrets(kernel: Any, task_id: str) -> bool:
    """[R6/R5] Mọi event của task phải khớp K_journal hiện hành, một K_journal đã nghỉ, hoặc
    khóa legacy của cửa sổ dual-accept thì reanchor (rotation / migration) mới được cấp."""
    legacy_keys = legacy_capability_journal_keys(kernel)
    if not previous_journal_secrets() and not legacy_keys:
        return False
    for e in kernel.get_events(task_id):
        try:
            payload = json.loads(e['payload_json'])
        except (TypeError, ValueError):
            return False
        if not classify_journal_hash(journal_event_body(e, payload), e['event_hash'], legacy_keys)[0]:
            return False
    return True


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

