"""Comprehensive Rate Limiting Architecture for SCP API Server (Finding M-09 + B1c Gap-2 Fix).

Enforces tiered rate limits across ALL endpoints via ASGI HTTP middleware and SlowAPI:
- Heavy endpoints (write / execute / admin): 10 requests / minute
- Route-specific limits enforced at middleware boundary BEFORE auth dependencies:
  * /auth/token: 5/minute
  * /ask: 60/minute
  * /v1/chat/completions: 60/minute
  * /v1/models: 60/minute
  * /import/jsonl: 10/minute
  * /v3/pc/execute: 10/minute
  * /v3/tasks: 30/minute
- Authenticated endpoints (Bearer / Capability token): 120 requests / minute
- Public endpoints / unauthenticated requests: 30 requests / minute
"""
from __future__ import annotations

import hashlib
import logging
import os
from collections.abc import Callable
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse
from limits import parse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

logger = logging.getLogger("scp.rate_limiting")

# Default tier rates
_DEFAULT_RATE_HEAVY = "10/minute"
_DEFAULT_RATE_AUTH = "120/minute"
_DEFAULT_RATE_PUBLIC = "30/minute"
_DEFAULT_RATE_ASK = "60/minute"
_DEFAULT_RATE_AUTH_ROUTE = "5/minute"

RATE_HEAVY = parse(os.environ.get("SCP_RATE_LIMIT_HEAVY", _DEFAULT_RATE_HEAVY))
RATE_AUTH = parse(os.environ.get("SCP_RATE_LIMIT_AUTH", _DEFAULT_RATE_AUTH))
RATE_PUBLIC = parse(os.environ.get("SCP_RATE_LIMIT_PUBLIC", _DEFAULT_RATE_PUBLIC))
RATE_ASK = parse(os.environ.get("SCP_RATE_LIMIT_ASK", _DEFAULT_RATE_ASK))
RATE_AUTH_TOKEN = parse(os.environ.get("SCP_RATE_LIMIT_AUTH_TOKEN", _DEFAULT_RATE_AUTH_ROUTE))

limiter = Limiter(key_func=get_remote_address)


def is_rate_limiting_enabled() -> bool:
    """Check if global rate limiting is active."""
    val = os.environ.get("SCP_RATE_LIMIT_ENABLED", "1").strip().lower()
    return val not in ("0", "false", "off", "no")


def get_rate_heavy() -> Any:
    return parse(os.environ.get("SCP_RATE_LIMIT_HEAVY", _DEFAULT_RATE_HEAVY))


def get_rate_auth() -> Any:
    return parse(os.environ.get("SCP_RATE_LIMIT_AUTH", _DEFAULT_RATE_AUTH))


def get_rate_public() -> Any:
    return parse(os.environ.get("SCP_RATE_LIMIT_PUBLIC", _DEFAULT_RATE_PUBLIC))


def get_rate_ask() -> Any:
    if "SCP_RATE_LIMIT_ASK" in os.environ:
        return parse(os.environ["SCP_RATE_LIMIT_ASK"])
    if "SCP_RATE_LIMIT_PUBLIC" in os.environ:
        return parse(os.environ["SCP_RATE_LIMIT_PUBLIC"])
    return parse(_DEFAULT_RATE_ASK)


def get_rate_auth_token() -> Any:
    return parse(os.environ.get("SCP_RATE_LIMIT_AUTH_TOKEN", _DEFAULT_RATE_AUTH_ROUTE))


def determine_rate_limit_tier(request: Request) -> tuple[Any, str]:
    """Determine the rate limit tier and parsed limit for an incoming request."""
    path = request.url.path
    method = request.method.upper()
    has_auth = bool(
        request.headers.get("authorization")
        or request.headers.get("x-scp-capability-token")
        or request.headers.get("x-scp-pc-token")
    )

    if path.startswith("/auth/token"):
        return get_rate_auth_token(), "auth_token"

    if path in ("/ask", "/v1/chat/completions", "/v1/models"):
        return get_rate_ask(), "ask"

    is_heavy = (
        any(path.startswith(p) for p in (
            "/admin",
            "/v98",
            "/v100",
            "/v102",
            "/v103",
            "/v104",
            "/v105",
            "/v106",
            "/v3/pc",
            "/v3/hands",
            "/v3/tasks",
            "/import",
        ))
        or "/execute" in path
        or "/kill" in path
        or (method in ("POST", "PUT", "DELETE", "PATCH") and any(sub in path for sub in ("/plan", "/rollback", "/reconcile")))
    )

    if is_heavy:
        return get_rate_heavy(), "heavy"
    if has_auth:
        return get_rate_auth(), "auth"
    return get_rate_public(), "public"


async def rate_limit_middleware(request: Request, call_next: Callable[..., Any]) -> Any:
    """Global ASGI HTTP middleware applying tiered rate limiting across ALL endpoints.

    [B1c GAP-2 FIX]: Enforces route-level /ask quota (60/min) AND per-IP ceiling
    before FastAPI dependency injection (`Depends(verify_jwt_token)`), preventing
    unauthenticated or rotating bad-JWT floods from bypassing the limiter with 401s.
    """
    if not is_rate_limiting_enabled():
        return await call_next(request)

    path = request.url.path

    # Exempt internal OpenAPI and metrics docs endpoints from artificial throttle
    if path in ("/docs", "/redoc", "/openapi.json", "/metrics", "/favicon.ico"):
        return await call_next(request)

    rate, tier_name = determine_rate_limit_tier(request)
    client_ip = get_remote_address(request) or "127.0.0.1"
    auth_header = (
        request.headers.get("authorization")
        or request.headers.get("x-scp-capability-token")
        or request.headers.get("x-scp-pc-token")
    )
    if auth_header:
        token_hash = hashlib.sha256(auth_header.encode("utf-8")).hexdigest()[:16]
        client_id = f"tok:{token_hash}"
    else:
        client_id = f"ip:{client_ip}"

    # Global rate limit per tier applies across endpoints for this client
    rate_key = f"global_tier:{client_id}:{tier_name}"
    ip_rate_key = f"global_ip:{client_ip}:{tier_name}"

    try:
        hit_allowed = limiter._limiter.hit(rate, rate_key)
        # Also enforce per-IP bucket on ask/auth_token tiers to stop token-rotation floods
        if hit_allowed and tier_name in ("ask", "auth_token") and auth_header:
            hit_allowed = limiter._limiter.hit(rate, ip_rate_key)
    except (KeyError, ValueError, RuntimeError, TypeError) as exc:
        logger.warning("[RateLimit] Error evaluating storage hit: %s", exc)
        hit_allowed = True

    if not hit_allowed:
        logger.warning(
            "[RateLimit 429] Limit exceeded: tier=%s limit=%s key=%s client=%s",
            tier_name, rate, rate_key, client_id,
        )
        return JSONResponse(
            status_code=429,
            content={"detail": f"Rate limit exceeded: {rate} ({tier_name} tier)"},
            headers={"Retry-After": "60"},
        )

    return await call_next(request)


__all__ = [
    "limiter",
    "rate_limit_middleware",
    "determine_rate_limit_tier",
    "RateLimitExceeded",
    "_rate_limit_exceeded_handler",
    "is_rate_limiting_enabled",
    "RATE_HEAVY",
    "RATE_AUTH",
    "RATE_PUBLIC",
    "RATE_ASK",
    "RATE_AUTH_TOKEN",
]
