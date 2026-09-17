"""SCP canonical admin-auth verifier.

[Fix 4-a-006 / Phase 3-A — DNA #5, #14, #19, #25]
TẠI SAO this module exists:
  Before this fix, TWO divergent `verify_admin` functions lived in the codebase:
    1. `scp.api_server_parts.helpers.verify_admin` — historical HTTPBearer-based
       implementation with no rate limiting. It is now only a re-export.
    2. `scp.api._shared.verify_admin` — historical header-based implementation.
       It is now only a re-export. All route modules resolve the canonical
       implementation below.

  DNA #5 (ảo giác đồng thuận): same name `verify_admin` ≠ same auth posture.
  DNA #14 (đồng thuận ≠ đúng): both passed basic tests; only the differing
  failure semantics mattered under attack. DNA #19: scanner didn't cross-check
  the two definitions. DNA #25 (missing piece): no comment in
  either file pointing to the other as canonical.

  Fix: ONE canonical `verify_admin` lives HERE in `scp/security/auth.py`
  (security is the right home for auth). It has:
    - Rate limiting (max 5 failed attempts / 60s per IP → 429 Too Many Requests)
    - 401 Unauthorized when no credential source is configured
    - 503 Service Unavailable when a configured credential source is unreadable or conflicts
    - 401 Unauthorized on missing/invalid token
    - Timing-safe `secrets.compare_digest` (no early short-circuit)
    - NO dev-mode bypass (deny by default if no token configured)

  `scp/api/_shared.py` and `scp/api_server_parts/helpers.py` BOTH re-export
  this function — so existing `from scp.api._shared import verify_admin`
  imports keep working, but there is now ONE implementation. If you need to
  change admin auth behavior, edit THIS file only.
"""
from __future__ import annotations

import logging
import os
import secrets
import time
from typing import Any

logger = logging.getLogger("scp.security.auth")
from scp.security.auth_config import AuthConfigError, load_auth_config

# [G5-FIX] Header + Request imports — both are FastAPI dependency markers.
try:
    from fastapi import Header, Request
except ImportError:  # pragma: no cover — fastapi is a hard dep of the API server
    logger.debug('<module>: ImportError ignored', exc_info=True)
    Header = None  # type: ignore
    Request = Any  # type: ignore[misc,assignment]

# [AUTOFIX-T2-SEC] Rate limiting for auth endpoints — prevents brute-force.
# Simple in-memory sliding-window limiter: max 5 failed attempts per IP per 60s.
# DNA SCP #12: "Không tăng quyền chỉ vì lập luận tăng" — rate limit là capability
# control, không phải logic change. Fail-closed (block) on threshold.
_RATE_LIMIT_WINDOW = 60  # seconds
_RATE_LIMIT_MAX_FAILURES = 5
_auth_failures: dict[str, list[float]] = {}

# [FIX-A P0-3] Log-once flag: warn when admin token is empty (deny-by-default).
_admin_no_token_warned: bool = False


def _check_rate_limit(ip: str) -> bool:
    """Return True if IP is within rate limit, False if blocked."""
    now = time.time()
    failures = _auth_failures.get(ip, [])
    # Prune old entries
    failures = [t for t in failures if now - t < _RATE_LIMIT_WINDOW]
    _auth_failures[ip] = failures
    return len(failures) < _RATE_LIMIT_MAX_FAILURES


def _record_auth_failure(ip: str) -> None:
    """Record a failed auth attempt for rate limiting."""
    _auth_failures.setdefault(ip, []).append(time.time())


def verify_admin(
    authorization: str = Header("", alias="Authorization"),  # noqa: B008 — FastAPI dependency injection idiom
    token: str | None = None,
    *,
    request: Request,
):
    """Verify admin token — timing-safe, NO dev-mode bypass, RAISES on failure.

    [Fix 4-a-006 / Phase 3-A] This is the CANONICAL `verify_admin` for the
    SCP system. Previously, TWO divergent impls existed (helpers.py + _shared.py)
    with different signatures, different rate-limit behavior, and different
    failure semantics. Now both re-export THIS function — ONE source of truth.
    Do NOT add a second impl.

    Behavior:
      - 429 Too Many Requests if IP exceeds 5 failed attempts / 60s (rate limit)
      - 401 Unauthorized if no credential source is configured or a credential
        source is malformed
      - 401 Unauthorized on missing or invalid token (timing-safe compare)
      - Returns True on success
    """
    from fastapi import HTTPException

    # Extract client IP for rate limiting. The Request annotation is essential:
    # FastAPI injects the actual connection only for a Request-typed parameter.
    client_ip = "unknown"
    if request is not None and hasattr(request, "client") and request.client:
        client_ip = request.client.host or "unknown"

    # Rate limit check BEFORE auth (don't leak whether token is valid)
    if not _check_rate_limit(client_ip):
        logger.warning(f"[RATE-LIMIT] IP {client_ip} blocked — too many auth failures")
        raise HTTPException(
            status_code=429,
            detail="Too many auth attempts — try again later",
        )

    try:
        auth_config = load_auth_config()
    except AuthConfigError as exc:
        logger.error("verify_admin: invalid auth configuration (%s)", exc.code)
        # A configured but unreadable/conflicting credential source is a
        # server configuration failure. Keep it distinct from route absence
        # (404) and caller credential rejection (401).
        raise HTTPException(status_code=503, detail="Authentication configuration unavailable") from exc
    auth_password = auth_config.password
    auth_token = auth_config.token
    jwt_secret = os.environ.get("SCP_JWT_SECRET", "").strip()
    # Deny by default if NEITHER is configured (no dev-mode bypass).
    # [Fix 4-a-006] 503 → 401: "Auth not configured" is an authorization
    # failure (caller's credentials don't match the empty config), NOT a
    # service-availability issue. Per RFC 7235, 401 is correct.
    if not auth_password and not auth_token:
        if not jwt_secret:
            global _admin_no_token_warned
            if not _admin_no_token_warned:
                logger.warning(
                    "verify_admin: SCP_AUTH_PASSWORD/SCP_AUTH_TOKEN_SECRET not set — "
                    "denying admin request (configure a real token in .env; "
                    "SCP_DEV_MODE bypass was removed in RC-2 fix)"
                )
                _admin_no_token_warned = True
            raise HTTPException(
                status_code=401,
                detail="Unauthorized — set SCP_AUTH_PASSWORD / SCP_AUTH_TOKEN_SECRET in .env",
            )

    provided = token or ""
    # [G5-FIX] Extract from Authorization header (FastAPI populates `authorization` param)
    # [REAUDIT-FIX] When called directly (not via Depends), authorization may be Header object
    if not provided and isinstance(authorization, str) and authorization:
        if authorization.lower().startswith("bearer "):
            provided = authorization[7:].strip()
        else:
            provided = authorization.strip()
    if not provided:
        _record_auth_failure(client_ip)
        raise HTTPException(status_code=401, detail="Missing auth token")

    # Mode 1: Attempt JWT verification if SCP_JWT_SECRET is configured
    if jwt_secret and provided:
        try:
            import jwt
            payload = jwt.decode(provided, jwt_secret, algorithms=["HS256"])
            sub = str(payload.get("sub", "")).strip()
            role = str(payload.get("role", "")).strip()
            if sub == "admin" or role == "admin":
                return True
        except Exception as exc:
            logger.debug(f"verify_admin: JWT decode fallback to static secrets: {exc}")

    # Timing-safe comparison — no short-circuit on first differing byte.
    ok = False
    if auth_token:
        try:
            ok = ok or secrets.compare_digest(provided, auth_token)
        except (TypeError, ValueError) as e:
            logger.warning(f"Silent except: {e}")
    if auth_password and not ok:
        try:
            ok = ok or secrets.compare_digest(provided, auth_password)
        except (TypeError, ValueError) as e:
            logger.warning(f"Silent except: {e}")
    if not ok:
        _record_auth_failure(client_ip)
        raise HTTPException(status_code=401, detail="Invalid auth token")
    return True


__all__ = [
    "verify_admin",
    "_check_rate_limit",
    "_record_auth_failure",
    "_auth_failures",
    "_RATE_LIMIT_WINDOW",
    "_RATE_LIMIT_MAX_FAILURES",
    "_admin_no_token_warned",
]
