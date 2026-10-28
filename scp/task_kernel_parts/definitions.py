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

# [R7 2026-10-28] Edge-class security constants (kill-switch theo loại cạnh +
# authority gate theo loại cạnh). Phân loại THEO LỚP trạng thái, không liệt kê
# cặp from->to (OWNER MANDATE @ bfb40026: cấm phân loại bằng danh sách mẫu).
# Khi global kill active, CHỈ cạnh về SAFE_UNDER_KILL được phép (nhả lease /
# contain, không tiếp tục công việc); mọi cạnh "khởi động lại" có target trong
# RESUME_TARGETS bị chặn. Cạnh đưa task từ CONTROLLED_STATES về
# DISPATCH_TARGETS phải qua authority gate chính thức (commit_approval /
# reconcile_unknown / auto_resolve_human_review), không qua transition()
# generic — kể cả qua trung gian (xem state_machine._checkpointed_from_controlled).
SAFE_UNDER_KILL = frozenset({
    "FAILED", "CANCELLED", "HUMAN_REVIEW", "CHECKPOINTED", "UNKNOWN",
    "RECONCILING", "RECOVERING", "RETRY_SCHEDULED",
})
RESUME_TARGETS = frozenset({
    "READY", "QUEUED", "LEASED", "RUNNING", "WAITING_TOOL", "VERIFYING", "PLANNING",
})
CONTROLLED_STATES = frozenset({
    "WAITING_APPROVAL", "HUMAN_REVIEW", "RECONCILING", "UNKNOWN", "RECOVERING",
})
DISPATCH_TARGETS = frozenset({"READY", "QUEUED", "LEASED", "RUNNING"})


def checkpointed_from_controlled(conn: Any, task_id: str) -> bool:
    """[R7-2] Did the task enter its current CHECKPOINTED stay from a CONTROLLED_STATES state?

    The journal is the authority: read ``from_state`` of the latest state-changing event
    (``from_state != to_state`` skips audit self-loops such as LEASE_EXPIRED containment).
    Every exit from CHECKPOINTED changes state, so this is the edge that entered it. Shared by
    the state machine and the lease watchdog so neither can requeue a contained task.
    """
    row = conn.execute(
        "SELECT from_state FROM events "
        "WHERE task_id=? AND from_state IS NOT NULL AND to_state IS NOT NULL "
        "AND from_state != to_state "
        "ORDER BY seq DESC LIMIT 1",
        (task_id,),
    ).fetchone()
    return bool(row and row["from_state"] in CONTROLLED_STATES)

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
    """K_journal đã nghỉ (rotation) khai báo qua SCP_PREVIOUS_JOURNAL_SECRETS (comma-separated).

    Fail-closed nếu danh sách chứa giá trị SCP_CAPABILITY_SECRET: một "khóa đã nghỉ" như vậy cho
    người giữ khóa capability ký journal hợp lệ ở định dạng hiện hành (có tiền tố miền).
    """
    keys = _split_secret_list(os.environ.get("SCP_PREVIOUS_JOURNAL_SECRETS", ""))
    capability = os.environ.get("SCP_CAPABILITY_SECRET", "").strip()
    if capability and capability in keys:
        raise KernelError(
            "SCP_PREVIOUS_JOURNAL_SECRETS must not reuse the value of SCP_CAPABILITY_SECRET (R5 key separation)"
        )
    return [s.encode() for s in keys]


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


def live_checkpointed_tasks(conn: Any) -> list[str]:
    """[R5] Non-terminal tasks that own checkpoints. Their pre-cutover checkpoint hashes cannot be
    re-signed (planned_action is not stored), so the K_journal cutover waits until they finish."""
    placeholders = ",".join("?" for _ in TERMINAL)
    rows = conn.execute(
        "SELECT DISTINCT c.task_id FROM checkpoints c JOIN tasks t ON t.task_id = c.task_id "
        f"WHERE t.state NOT IN ({placeholders}) ORDER BY c.task_id",
        tuple(sorted(TERMINAL)),
    ).fetchall()
    return [str(r[0]) for r in rows]


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
    max_skew_seconds: float = 300.0,
) -> dict[str, Any]:
    """Verify an issuer-signed approval token for ``task_id`` fail-closed (GAP-13, R5-B).

    [R5-B] Only Ed25519 tokens signed with the issuer PRIVATE key are accepted, verified with
    SCP_APPROVAL_ISSUER_PUBLIC_KEY. HMAC capability tokens (compact mint / CapabilityToken) and
    operator HMAC signature dicts are rejected: whoever held SCP_CAPABILITY_SECRET could mint
    them, the autonomous checker gate included. Validity is bounded by the token's own exp
    (max 1h); ``max_skew_seconds`` is kept for call compatibility.
    """
    from scp.core.capability_token import InvalidTokenSignatureError
    from scp.security.approval_issuer import (
        ApprovalTokenExpired,
        ApprovalTokenScopeMismatch,
        verify_approval_token,
    )

    if token is None or (isinstance(token, str) and not token.strip()):
        raise InvalidTokenSignatureError("Approval token is missing or empty (GAP-13/FA-04)")
    try:
        return verify_approval_token(token.strip() if isinstance(token, str) else token, task_id)
    except ApprovalTokenExpired as exc:
        raise InvalidTransition(f"Approval token has expired: {exc}") from exc
    except ApprovalTokenScopeMismatch as exc:
        raise InvalidTransition(f"Approval token does not authorize 'approval:grant' for task '{task_id}': {exc}") from exc

