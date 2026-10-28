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

[R16 2026-10-28] Storage hit() lỗi (BẤT KỲ exception) → DEGRADED-MODE có
ceiling cứng: fallback bộ đếm in-process theo giá trị tier cấu hình (không
fail-open cho-qua tự do, không propagate 500) — xem _hit_with_degraded_fallback.
"""
from __future__ import annotations

import hashlib
import logging
import threading
from collections.abc import Callable
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse
from limits import parse
from slowapi import Limiter
from slowapi import _rate_limit_exceeded_handler as _base_rate_limit_exceeded_handler

def _rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> Any:
    """Rate limit exceeded handler ensuring Retry-After: 60 header is present."""
    resp = _base_rate_limit_exceeded_handler(request, exc)
    if "Retry-After" not in resp.headers:
        resp.headers["Retry-After"] = "60"
    return resp
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

# [HC-03 / B3-W1 2026-10-23] Điểm đọc env chuẩn (settings.get — live, strip,
# không cache); mọi giá trị tier vẫn parse qua limits.parse như cũ.
from scp.core.settings import get

# [R16] Bộ đếm in-process dùng chung cho degraded-mode — tái dùng InMemoryRateLimiter
# của adapter H3-W1 (fixed-window đúng ngữ nghĩa INCR+EXPIRE, thread-safe) thay vì
# viết bộ đếm thứ hai; đồng thời rate_limit_redis.py có thêm 1 caller thật.
from scp.api.rate_limit_redis import InMemoryRateLimiter

logger = logging.getLogger("scp.rate_limiting")

# Default tier rates
_DEFAULT_RATE_HEAVY = "10/minute"
_DEFAULT_RATE_AUTH = "120/minute"
_DEFAULT_RATE_PUBLIC = "30/minute"
_DEFAULT_RATE_ASK = "60/minute"
_DEFAULT_RATE_AUTH_ROUTE = "5/minute"

RATE_HEAVY = parse(get("SCP_RATE_LIMIT_HEAVY", _DEFAULT_RATE_HEAVY))
RATE_AUTH = parse(get("SCP_RATE_LIMIT_AUTH", _DEFAULT_RATE_AUTH))
RATE_PUBLIC = parse(get("SCP_RATE_LIMIT_PUBLIC", _DEFAULT_RATE_PUBLIC))
RATE_ASK = parse(get("SCP_RATE_LIMIT_ASK", _DEFAULT_RATE_ASK))
RATE_AUTH_TOKEN = parse(get("SCP_RATE_LIMIT_AUTH_TOKEN", _DEFAULT_RATE_AUTH_ROUTE))

# [R16] Storage URI cho slowapi Limiter (package `limits` có sẵn qua slowapi —
# KHÔNG thêm dependency). Đọc LIVE qua settings.get; rỗng/không đặt → "memory://"
# đúng hành vi cũ (slowapi default storage_uri=None → storage_from_string("memory://")).
STORAGE_URI_ENV = "SCP_RATE_LIMIT_STORAGE_URI"


def _resolve_storage_uri() -> str:
    val = get(STORAGE_URI_ENV)
    if val is None or not val.strip():
        return "memory://"
    return val.strip()


limiter = Limiter(key_func=get_remote_address, storage_uri=_resolve_storage_uri())


# ── [R16] DEGRADED-MODE: storage lỗi → bộ đếm in-process, ceiling cứng ────────
#
# Probe đã verify REAL hành vi cũ: (a) 4 loại exception trong tuple cũ
# (KeyError/ValueError/RuntimeError/TypeError) → except set hit_allowed=True →
# FAIL-OPEN cho qua toàn bộ; (b) exception NGOÀI tuple (ConnectionError/OSError)
# → propagate → 500 mọi request. Fix theo mẫu _downgrade_to_memory của
# scp/api/rate_limit_redis.py:154-162: downgrade 1 lần (sticky), log ERROR
# (không WARNING), enforcement KHÔNG bao giờ mở (ceiling cứng = giá trị tier
# cấu hình hiện hành, không ∞).

_degraded_lock = threading.Lock()
_degraded_counter: InMemoryRateLimiter | None = None  # None = storage còn khỏe
_degraded_hits = 0


def is_degraded_mode_active() -> bool:
    """Dấu hiệu phát hiện: True khi storage đã lỗi và mọi hit đang qua bộ đếm in-process."""
    with _degraded_lock:
        return _degraded_counter is not None


def degraded_hit_count() -> int:
    """Tổng số hit đã qua bộ đếm degraded kể từ lần downgrade (observability)."""
    with _degraded_lock:
        return _degraded_hits


def _reset_degraded_mode() -> None:
    """Reset trạng thái degraded (test/ops: cho phép thử lại storage sau khi sửa)."""
    global _degraded_counter, _degraded_hits
    with _degraded_lock:
        _degraded_counter = None
        _degraded_hits = 0


def _ceiling_of(rate: Any) -> tuple[int, int] | None:
    """(limit, window_seconds) từ parsed rate item — ceiling cứng theo tier config.

    Trả None nếu không suy ra được amount/window hợp lệ (đối tượng lạ) → caller
    phải deny (fail-closed), tuyệt đối không cấp quota vô hạn.
    """
    try:
        amount = int(getattr(rate, "amount"))
        window = int(rate.get_expiry())
    except Exception:
        return None
    if amount <= 0 or window <= 0:
        return None
    return amount, window


def _degraded_allow(rate: Any, key: str) -> bool:
    """Kiểm đếm 1 hit bằng bộ đếm in-process với ceiling cứng của tier hiện hành."""
    global _degraded_hits
    with _degraded_lock:
        counter = _degraded_counter
    ceiling = _ceiling_of(rate)
    if counter is None or ceiling is None:
        logger.error(
            "[RateLimit][DEGRADED] không kiểm đếm được key=%s (rate=%s) — deny (fail-closed)",
            key,
            rate,
        )
        return False
    limit, window_seconds = ceiling
    allowed = counter.allow(key, limit=limit, window_seconds=window_seconds)
    with _degraded_lock:
        _degraded_hits += 1
    return allowed


def _hit_with_degraded_fallback(rate: Any, key: str) -> bool:
    """hit() qua slowapi storage; BẤT KỲ exception nào → degraded-mode (không 500).

    - Không fail-open: fallback bộ đếm in-process, ceiling cứng = giá trị cấu
      hình tier hiện hành (burst vượt ceiling → 429 như thường).
    - Bắt rộng `Exception`: cả exception ngoài tuple cũ (ConnectionError/OSError/...)
      vào degraded-mode thay vì propagate thành 500; semantics fail-closed của
      các tầng xác thực khác giữ nguyên vẹn (middleware không đổi ngoài hit).
    - Downgrade sticky đúng mẫu _downgrade_to_memory: log ERROR đúng 1 lần khi
      chuyển trạng thái, các hit sau không còn chạm storage.
    """
    global _degraded_counter
    with _degraded_lock:
        counter = _degraded_counter
    if counter is not None:
        return _degraded_allow(rate, key)
    try:
        return bool(limiter._limiter.hit(rate, key))
    except Exception as exc:
        with _degraded_lock:
            if _degraded_counter is None:
                logger.error(
                    "[RateLimit][DEGRADED] storage hit() lỗi key=%s (%s: %s) — "
                    "downgrade vĩnh viễn sang bộ đếm in-process, ceiling cứng = tier "
                    "config (fail-closed, KHÔNG fail-open)",
                    key,
                    type(exc).__name__,
                    exc,
                )
                _degraded_counter = InMemoryRateLimiter()
            counter = _degraded_counter
        return _degraded_allow(rate, key)


def is_rate_limiting_enabled() -> bool:
    """Check if global rate limiting is active."""
    val = get("SCP_RATE_LIMIT_ENABLED", "1").strip().lower()
    return val not in ("0", "false", "off", "no")


def get_rate_heavy() -> Any:
    return parse(get("SCP_RATE_LIMIT_HEAVY", _DEFAULT_RATE_HEAVY))


def get_rate_auth() -> Any:
    return parse(get("SCP_RATE_LIMIT_AUTH", _DEFAULT_RATE_AUTH))


def get_rate_public() -> Any:
    return parse(get("SCP_RATE_LIMIT_PUBLIC", _DEFAULT_RATE_PUBLIC))


def get_rate_ask() -> Any:
    # [B3-W1] Semantics giữ nguyên so với subscript cũ: key CÓ trong env (kể cả
    # rỗng) → parse giá trị đó; không có → fallback SCP_RATE_LIMIT_PUBLIC rồi default.
    val = get("SCP_RATE_LIMIT_ASK")
    if val is not None:
        return parse(val)
    val = get("SCP_RATE_LIMIT_PUBLIC")
    if val is not None:
        return parse(val)
    return parse(_DEFAULT_RATE_ASK)


def get_rate_auth_token() -> Any:
    return parse(get("SCP_RATE_LIMIT_AUTH_TOKEN", _DEFAULT_RATE_AUTH_ROUTE))


def determine_rate_limit_tier(request: Request) -> tuple[Any, str]:
    """Determine the rate limit tier and parsed limit for an incoming request."""
    path = request.url.path
    method = request.method.upper()
    has_auth = bool(
        request.headers.get("authorization")
        or request.headers.get("x-scp-capability-token")
        or request.headers.get("x-scp-pc-token")
        or request.headers.get("x-scp-scheduler-token")
        or request.headers.get("x-scp-admin-token")
        or request.headers.get("x-admin-key")
        or request.headers.get("x-api-key")
    )

    if path.startswith("/auth/token"):
        return get_rate_auth_token(), "auth_token"

    if path in ("/ask", "/v1/chat/completions", "/v1/models"):
        return get_rate_ask(), "ask"

    # Read-only GET requests to status/health/info endpoints MUST NOT be classified as heavy.
    # Dashboard and scheduler polling (e.g. /v3/pc/status, /v3/hands/status, /v3/web/status,
    # /v3/tasks/stats, /v3/tasks, /health, /ready, /metrics) must not be throttled at heavy tier.
    is_status_probe = (
        method == "GET"
        and (
            any(path.endswith(s) for s in ("/status", "/health", "/ready", "/tags", "/stats", "/info", "/live", "/metrics"))
            or path in ("/v3/tasks", "/v3/pc", "/v3/hands", "/v3/web", "/health", "/ready")
        )
    )
    if is_status_probe:
        if has_auth:
            return get_rate_auth(), "auth"
        return get_rate_public(), "public"

    # Only mutating requests or explicit heavy action keywords are classified as heavy
    heavy_keywords = (
        "/execute",
        "/kill",
        "/run-audit",
        "/run-cycle",
        "/plan",
        "/rollback",
        "/reconcile",
    )
    is_action_heavy = any(kw in path for kw in heavy_keywords)
    is_mutating = method in ("POST", "PUT", "DELETE", "PATCH")
    is_heavy_prefix = any(path.startswith(p) for p in (
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

    is_heavy = is_action_heavy or (is_mutating and is_heavy_prefix)

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
        or request.headers.get("x-scp-scheduler-token")
        or request.headers.get("x-scp-admin-token")
        or request.headers.get("x-admin-key")
        or request.headers.get("x-api-key")
    )
    if auth_header:
        token_hash = hashlib.sha256(auth_header.encode("utf-8")).hexdigest()[:16]
        client_id = f"tok:{token_hash}"
    else:
        client_id = f"ip:{client_ip}"

    # Global rate limit per tier applies across endpoints for this client
    rate_key = f"global_tier:{client_id}:{tier_name}"
    ip_rate_key = f"global_ip:{client_ip}:{tier_name}"

    # [R16] hit() lỗi (BẤT KỲ exception — kể cả ngoài tuple cũ như
    # ConnectionError/OSError) → degraded-mode có ceiling cứng: KHÔNG cho-qua
    # tự do (fail-open cũ), KHÔNG propagate thành 500. Xem
    # _hit_with_degraded_fallback.
    hit_allowed = _hit_with_degraded_fallback(rate, rate_key)
    # Also enforce per-IP bucket on ask/auth_token tiers to stop token-rotation floods
    if hit_allowed and tier_name in ("ask", "auth_token") and auth_header:
        hit_allowed = _hit_with_degraded_fallback(rate, ip_rate_key)

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
    "is_degraded_mode_active",
    "degraded_hit_count",
    "STORAGE_URI_ENV",
    "RATE_HEAVY",
    "RATE_AUTH",
    "RATE_PUBLIC",
    "RATE_ASK",
    "RATE_AUTH_TOKEN",
]
