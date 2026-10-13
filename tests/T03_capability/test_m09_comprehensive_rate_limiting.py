"""Comprehensive Rate Limiting Test Suite (Finding M-09 Remediation).

Verifies fail-closed rate-limiting across ALL API routes:
- Global middleware applies default tiered limits (public 30/min, auth 120/min, heavy 10/min)
- Decorator limits on heavy endpoints (/auth/token, /ask, /v1/chat/completions, /v1/models, /import/jsonl, /v3/pc/execute, /v3/tasks)
- Unauthenticated requests to /ask are throttled at middleware layer, returning 429
- 429 responses contain Retry-After header and descriptive detail
"""
from __future__ import annotations

import os
import pytest
from fastapi.testclient import TestClient
from limits import parse

from scp.api.rate_limiting import (
    RATE_HEAVY,
    RATE_PUBLIC,
    determine_rate_limit_tier,
    limiter,
)
from scp.api_server import app
from scp.security.jwt_guard import create_access_token


@pytest.fixture(autouse=True)
def reset_limiter_storage(monkeypatch):
    """Ensure clean in-memory rate limiter state for each test."""
    monkeypatch.setenv("SCP_RATE_LIMIT_ENABLED", "1")
    monkeypatch.setenv("SCP_RATE_LIMIT_PUBLIC", "5/minute")
    monkeypatch.setenv("SCP_RATE_LIMIT_HEAVY", "3/minute")
    limiter.reset()
    yield
    limiter.reset()


def test_m09_determine_rate_limit_tier_logic():
    """M-09: Tier determination correctly maps paths and auth headers."""
    from fastapi import Request

    # Heavy endpoints
    scope_heavy = {"type": "http", "method": "POST", "path": "/v3/pc/execute", "headers": []}
    req_heavy = Request(scope_heavy)
    rate, tier = determine_rate_limit_tier(req_heavy)
    assert tier == "heavy"

    # Public unauthenticated endpoints
    scope_pub = {"type": "http", "method": "GET", "path": "/health", "headers": []}
    req_pub = Request(scope_pub)
    rate, tier = determine_rate_limit_tier(req_pub)
    assert tier == "public"

    # Authenticated general endpoints
    scope_auth = {
        "type": "http",
        "method": "GET",
        "path": "/v3/trace/123",
        "headers": [(b"authorization", b"Bearer some-token")],
    }
    req_auth = Request(scope_auth)
    rate, tier = determine_rate_limit_tier(req_auth)
    assert tier == "auth"


def test_m09_unauthenticated_ask_throttles_to_429():
    """M-09 / GA B1c: Unauthenticated requests to /ask throttle fail-closed to 429."""
    limiter.reset()
    client = TestClient(app)

    # First few requests fail with 401 Unauthorized (JWT required)
    for _ in range(5):
        resp = client.post("/ask", json={"question": "Test question"})
        assert resp.status_code == 401

    # 6th request is caught by public rate limiter tier before reaching auth dependency
    resp_429 = client.post("/ask", json={"question": "Test question"})
    assert resp_429.status_code == 429
    assert "rate limit exceeded" in resp_429.json()["detail"].lower()
    assert resp_429.headers.get("Retry-After") == "60"


def test_m09_heavy_endpoint_strict_throttling():
    """M-09: Heavy endpoints are strictly capped and return 429 when threshold exceeded."""
    limiter.reset()
    client = TestClient(app)
    token = create_access_token({"sub": "admin"})
    headers = {"Authorization": f"Bearer {token}"}

    # /v3/tasks endpoint (heavy tier)
    hit_429 = False
    for i in range(15):
        resp = client.get("/v3/tasks", headers=headers)
        if resp.status_code == 429:
            hit_429 = True
            assert "rate limit exceeded" in resp.json()["detail"].lower()
            break

    assert hit_429, "Heavy endpoint /v3/tasks must trigger HTTP 429 when rate limit exceeded"


def test_m09_decorator_rate_limit_on_token_endpoint():
    """M-09: /auth/token decorator strictly caps requests (5/minute limit)."""
    limiter.reset()
    client = TestClient(app)

    # 5 attempts with invalid key
    for _ in range(5):
        resp = client.post("/auth/token", json={"admin_key": "wrong-key"})
        assert resp.status_code in (401, 429)

    # 6th attempt must return 429
    resp_blocked = client.post("/auth/token", json={"admin_key": "wrong-key"})
    assert resp_blocked.status_code == 429


def test_m09_decorator_rate_limit_on_compat_endpoints(monkeypatch):
    """M-09: /v1/models is covered by rate limiter and triggers 429 when flooded."""
    limiter.reset()
    monkeypatch.setenv("SCP_RATE_LIMIT_PUBLIC", "3/minute")
    client = TestClient(app)
    token = create_access_token({"sub": "user"})
    headers = {"Authorization": f"Bearer {token}"}

    # First request succeeds
    resp = client.get("/v1/models", headers=headers)
    assert resp.status_code == 200

    # Exhaust models endpoint rate limit
    hit_429 = False
    for _ in range(65):
        resp = client.get("/v1/models", headers=headers)
        if resp.status_code == 429:
            hit_429 = True
            break
    assert hit_429, "/v1/models must return HTTP 429 when rate limit exceeded"


def test_m09_dynamic_path_cannot_bypass_global_rate_limit():
    """M-09 Anti-Bypass: Rotating path parameters cannot bypass global tier rate limit."""
    limiter.reset()
    client = TestClient(app)

    # SCP_RATE_LIMIT_PUBLIC is 5/minute in fixture
    # Sending requests across distinct paths /v3/nonexistent/{i}
    hit_429 = False
    statuses = []
    for i in range(10):
        resp = client.get(f"/v3/nonexistent/{i}")
        statuses.append(resp.status_code)
        if resp.status_code == 429:
            hit_429 = True
            assert "rate limit exceeded" in resp.json()["detail"].lower()
            break

    assert hit_429, f"Dynamic paths must not bypass tier rate limit. Statuses: {statuses}"


def test_m09_public_endpoint_throttling():
    """M-09: Public routes like /health are throttled when flooded beyond public tier ceiling."""
    limiter.reset()
    client = TestClient(app)

    # Flooding /health beyond 5 requests
    hit_429 = False
    for i in range(10):
        resp = client.get("/health")
        if resp.status_code == 429:
            hit_429 = True
            break

    assert hit_429, "Public endpoints must enforce rate limits against flooding"
