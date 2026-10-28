"""Issuer-signed approval tokens for TaskKernel ``WAITING_APPROVAL -> READY`` (R5-B).

Before R5 an ``approval:grant`` token was an HMAC under SCP_CAPABILITY_SECRET, so any code
holding that key (the autonomous checker gate included) could approve its own work. Approval
is now an Ed25519 signature: the PRIVATE key (SCP_APPROVAL_ISSUER_PRIVATE_KEY / _FILE) lives
only with the issuer (an operator or a separate issuer service); the kernel verifies with the
PUBLIC key (SCP_APPROVAL_ISSUER_PUBLIC_KEY / _FILE) and cannot mint.

Token format: ``apv1.<base64url(payload)>.<base64url(signature)>`` where the signature covers
``scp.approval.v1\\x00 + payload`` and payload is canonical JSON bound to exactly one task.
"""
from __future__ import annotations

import base64
import json
import math
import secrets
import time
from typing import Any

from scp.core.capability_token import InvalidTokenSignatureError
from scp.security.secret_registry import resolve

APPROVAL_TOKEN_PREFIX = "apv1."
APPROVAL_SIGNING_DOMAIN = b"scp.approval.v1\x00"
APPROVAL_TOKEN_TYPE = "approval_ed25519"
DEFAULT_TTL_SECONDS = 300.0
MAX_TTL_SECONDS = 3600.0
MAX_CLOCK_SKEW_SECONDS = 60.0


class ApprovalTokenExpired(InvalidTokenSignatureError):
    """The approval token is outside its validity window."""


class ApprovalTokenScopeMismatch(InvalidTokenSignatureError):
    """The approval token is bound to a different task or scope."""


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _canonical(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _load_key(name: str) -> bytes:
    value = resolve(name)
    if not value:
        raise InvalidTokenSignatureError(f"{name} is required for issuer-signed approval tokens (R5-B)")
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as exc:
        raise InvalidTokenSignatureError(f"{name} is not valid base64") from exc
    if len(raw) != 32:
        raise InvalidTokenSignatureError(f"{name} must be a raw 32-byte Ed25519 key")
    return raw


def generate_issuer_keypair() -> tuple[str, str]:
    """Return a new (private_key_b64, public_key_b64) pair of raw Ed25519 keys."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    private = Ed25519PrivateKey.generate()
    raw_private = private.private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()
    )
    raw_public = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(raw_private).decode("ascii"), base64.b64encode(raw_public).decode("ascii")


def issue_approval_token(
    task_id: str,
    issuer: str,
    ttl_seconds: float = DEFAULT_TTL_SECONDS,
    *,
    now: float | None = None,
) -> str:
    """Sign an approval for exactly ``task_id``. Requires the issuer PRIVATE key."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    task_id = str(task_id or "")
    issuer = str(issuer or "").strip()
    if not task_id.strip() or not issuer:
        raise InvalidTokenSignatureError("task_id and issuer are required to issue an approval token")
    if task_id != task_id.strip():
        # The token binds the EXACT task id; normalising here would let one approval cover
        # several distinct kernel tasks ("t", "t ", " t").
        raise InvalidTokenSignatureError("task_id must not carry leading/trailing whitespace")
    ttl = float(ttl_seconds)
    if not math.isfinite(ttl) or ttl <= 0 or ttl > MAX_TTL_SECONDS:
        raise InvalidTokenSignatureError(f"approval token ttl must be in (0, {MAX_TTL_SECONDS:.0f}] seconds")
    issued_at = time.time() if now is None else float(now)
    payload = {
        "v": 1,
        "typ": "scp.approval",
        "task_id": task_id,
        "scope": f"approval:grant:{task_id}",
        "iss": issuer,
        "iat": issued_at,
        "exp": issued_at + ttl,
        "jti": secrets.token_hex(12),
    }
    body = _canonical(payload)
    private = Ed25519PrivateKey.from_private_bytes(_load_key("SCP_APPROVAL_ISSUER_PRIVATE_KEY"))
    signature = private.sign(APPROVAL_SIGNING_DOMAIN + body)
    return f"{APPROVAL_TOKEN_PREFIX}{_b64url_encode(body)}.{_b64url_encode(signature)}"


def verify_approval_token(token: Any, task_id: str, *, now: float | None = None) -> dict[str, Any]:
    """Verify an issuer-signed approval for ``task_id`` with the issuer PUBLIC key, fail-closed.

    Returns verification metadata (token_type, token_id, actor, scope, signature).
    """
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    if not isinstance(token, str) or not token.startswith(APPROVAL_TOKEN_PREFIX):
        raise InvalidTokenSignatureError(
            "approval:grant requires an issuer-signed Ed25519 approval token (R5-B); "
            "HMAC capability tokens and operator HMAC signatures are not accepted"
        )
    parts = token[len(APPROVAL_TOKEN_PREFIX):].split(".")
    if len(parts) != 2:
        raise InvalidTokenSignatureError("malformed approval token")
    try:
        body = _b64url_decode(parts[0])
        signature = _b64url_decode(parts[1])
    except (ValueError, TypeError) as exc:
        raise InvalidTokenSignatureError("malformed approval token encoding") from exc

    public = Ed25519PublicKey.from_public_bytes(_load_key("SCP_APPROVAL_ISSUER_PUBLIC_KEY"))
    try:
        public.verify(signature, APPROVAL_SIGNING_DOMAIN + body)
    except InvalidSignature as exc:
        raise InvalidTokenSignatureError("approval token signature verification failed") from exc

    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise InvalidTokenSignatureError("approval token payload is not JSON") from exc
    if not isinstance(payload, dict) or payload.get("v") != 1 or payload.get("typ") != "scp.approval":
        raise InvalidTokenSignatureError("unsupported approval token payload")

    expected_task = str(task_id or "")  # exact match: no normalisation of the kernel task id
    if payload.get("task_id") != expected_task or payload.get("scope") != f"approval:grant:{expected_task}":
        raise ApprovalTokenScopeMismatch(
            f"approval token is bound to task '{payload.get('task_id')}', not '{expected_task}'"
        )

    try:
        issued_at = float(payload["iat"])
        expires_at = float(payload["exp"])
    except (KeyError, TypeError, ValueError) as exc:
        raise InvalidTokenSignatureError("approval token timestamps are missing or invalid") from exc
    if not (math.isfinite(issued_at) and math.isfinite(expires_at)):
        raise InvalidTokenSignatureError("approval token timestamps must be finite")
    if expires_at <= issued_at or expires_at - issued_at > MAX_TTL_SECONDS:
        raise InvalidTokenSignatureError("approval token validity window is invalid")
    current = time.time() if now is None else float(now)
    if issued_at > current + MAX_CLOCK_SKEW_SECONDS:
        raise InvalidTokenSignatureError("approval token issued_at is in the future")
    if current > expires_at:
        raise ApprovalTokenExpired("approval token has expired")

    issuer = str(payload.get("iss") or "").strip()
    if not issuer:
        raise InvalidTokenSignatureError("approval token has no issuer")
    return {
        "token_type": APPROVAL_TOKEN_TYPE,
        "token_id": str(payload.get("jti") or ""),
        "actor": issuer,
        "scope": payload["scope"],
        "signature": parts[1],
    }


__all__ = [
    "APPROVAL_TOKEN_PREFIX",
    "APPROVAL_TOKEN_TYPE",
    "ApprovalTokenExpired",
    "ApprovalTokenScopeMismatch",
    "generate_issuer_keypair",
    "issue_approval_token",
    "verify_approval_token",
]
