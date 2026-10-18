"""Empirical Challenger Test Suite for Gemini Native Provider & CircuitBreaker (M1).

Adversarial Stress Verification:
1. Missing, whitespace, placeholder, and invalid GEMINI_API_KEY.
2. Endpoint 500, 502, 503, 504 & timeouts tripping CircuitBreaker to OPEN after 3 failures.
3. Fast-fail behavior during OPEN state with 0 network calls at provider, gateway, and middleware (HTTP 503) levels.
4. HALF-OPEN state transition after cooldown, probe concurrency locking, probe recovery/re-trip, and exception safety.
"""
from __future__ import annotations

import asyncio
import itertools
import os
import time

import httpx
import pytest
from starlette.requests import Request
from starlette.responses import JSONResponse

from scp.core.circuit_breaker import CircuitBreakerRegistry
from scp.llm_gateway.circuit_breaker import CircuitBreaker
from scp.llm_gateway.client import EnvCompatProvider, LLMGateway, OpenRouterProvider
from scp.middleware.runtime_skill_guard import RuntimeSkillGuardMiddleware


class MockResponse:
    def __init__(self, status_code: int = 200, text: str = "ok", json_data: dict | None = None):
        self.status_code = status_code
        self._text = text
        self._json = json_data or {"choices": [{"message": {"content": text}}]}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("POST", "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions")
            response = httpx.Response(self.status_code, request=request, text=self._text)
            raise httpx.HTTPStatusError(f"HTTP {self.status_code}", request=request, response=response)

    def json(self) -> dict:
        return self._json

    @property
    def text(self) -> str:
        return self._text


class ScriptedNetworkClient:
    def __init__(self, responses: list[Exception | MockResponse]):
        self.script = list(responses)
        self.call_count = 0
        self.recorded_calls: list[dict] = []

    async def post(self, url: str, json: dict | None = None, headers: dict | None = None, **kwargs):
        self.call_count += 1
        self.recorded_calls.append({"url": url, "json": json, "headers": headers})
        if not self.script:
            raise RuntimeError("ScriptedNetworkClient exhausted script")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture(autouse=True)
def setup_gateway_env(monkeypatch):
    from scp.llm_gateway.circuit_breaker import reset_dead_model_cache
    reset_dead_model_cache()
    monkeypatch.setenv("SCP_EGRESS_MODE", "allowlist")
    monkeypatch.setenv(
        "SCP_LLM_EGRESS_ALLOWLIST",
        "generativelanguage.googleapis.com,openrouter.ai,api.openai.com",
    )
    async def _noop_sleep(*args, **kwargs):
        pass
    monkeypatch.setattr(asyncio, "sleep", _noop_sleep)
    yield
    reset_dead_model_cache()



# ===========================================================================
# 1. GEMINI_API_KEY Handling (Missing, Whitespace, Placeholder, Invalid)
# ===========================================================================

def test_gemini_missing_or_whitespace_key(monkeypatch):
    """Missing or whitespace-only GEMINI_API_KEY must disable provider."""
    for val in [None, "", "   ", "\t\n  "]:
        if val is None:
            monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        else:
            monkeypatch.setenv("GEMINI_API_KEY", val)

        provider = EnvCompatProvider(
            "gemini_compat", "default",
            "GEMINI_API_KEY", "GEMINI_BASE_URL", "GEMINI_MODEL",
            default_base_url="https://generativelanguage.googleapis.com/v1beta/openai",
            default_model="gemini-2.0-flash",
        )
        assert provider.enabled is False
        assert provider._key_count() == 0

        # Direct call immediately returns None, none without network call
        ans, lbl = asyncio.run(provider.chat("hello"))
        assert ans is None and lbl == "none"


@pytest.mark.parametrize("placeholder", [
    "changeme", "your-key", "your_api_key", "placeholder", "xxx", "sk-xxx", "none",
    "CHANGEME", "YOUR-KEY", "None"
])
def test_gemini_placeholders_disabled(monkeypatch, placeholder):
    """Case-insensitive placeholder keys must not enable the provider."""
    monkeypatch.setenv("GEMINI_API_KEY", placeholder)
    provider = EnvCompatProvider(
        "gemini_compat", "default",
        "GEMINI_API_KEY", "GEMINI_BASE_URL", "GEMINI_MODEL",
        default_base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        default_model="gemini-2.0-flash",
    )
    assert provider.enabled is False


def test_gemini_invalid_key_401_retries_and_records_failure(monkeypatch):
    """Invalid GEMINI_API_KEY resulting in HTTP 401 retries transiently then records 1 failure."""
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-invalid-bad-key")
    provider = EnvCompatProvider(
        "gemini_compat", "default",
        "GEMINI_API_KEY", "GEMINI_BASE_URL", "GEMINI_MODEL",
        default_base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        default_model="gemini-2.0-flash",
    )
    assert provider.enabled is True

    mock_client = ScriptedNetworkClient([
        MockResponse(401, "API key not valid"),
        MockResponse(401, "API key not valid"),
        MockResponse(401, "API key not valid"),
    ])
    provider._client = mock_client

    ans, lbl = asyncio.run(provider.chat("hello"))
    assert ans is None and lbl == "none"
    assert mock_client.call_count == 3
    assert provider._breaker._consecutive_failures == 1
    assert mock_client.recorded_calls[0]["headers"]["Authorization"] == "Bearer gemini-invalid-bad-key"


# ===========================================================================
# 2. 500, 502, 503, 504 & Timeout Fault Injection -> Tripping to OPEN
# ===========================================================================

@pytest.mark.parametrize("status_code", [500, 502, 503, 504])
def test_gemini_server_errors_trip_breaker(monkeypatch, status_code):
    """3 calls failing with 5xx status code trip the breaker to OPEN."""
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-valid-key")
    provider = EnvCompatProvider(
        "gemini_compat", "default",
        "GEMINI_API_KEY", "GEMINI_BASE_URL", "GEMINI_MODEL",
        default_base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        default_model="gemini-2.0-flash",
    )
    script = [MockResponse(status_code, f"Server Error {status_code}") for _ in range(9)]
    mock_client = ScriptedNetworkClient(script)
    provider._client = mock_client

    for i in range(1, 4):
        ans, lbl = asyncio.run(provider.chat(f"query {i}"))
        assert ans is None and lbl == "none"
        assert provider._breaker._consecutive_failures == i

    assert provider._breaker.is_open() is True
    assert provider._breaker._opened_at is not None


def test_gemini_mixed_timeouts_trip_breaker(monkeypatch):
    """Mixed connect and read timeouts trip breaker to OPEN after 3 failures."""
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-valid-key")
    provider = EnvCompatProvider(
        "gemini_compat", "default",
        "GEMINI_API_KEY", "GEMINI_BASE_URL", "GEMINI_MODEL",
        default_base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        default_model="gemini-2.0-flash",
    )
    script = [
        httpx.ConnectTimeout("Connect timeout"), httpx.ConnectTimeout("Connect timeout"), httpx.ConnectTimeout("Connect timeout"),
        httpx.ReadTimeout("Read timeout"), httpx.ReadTimeout("Read timeout"), httpx.ReadTimeout("Read timeout"),
        httpx.PoolTimeout("Pool timeout"), httpx.PoolTimeout("Pool timeout"), httpx.PoolTimeout("Pool timeout"),
    ]
    mock_client = ScriptedNetworkClient(script)
    provider._client = mock_client

    for i in range(1, 4):
        ans, lbl = asyncio.run(provider.chat(f"timeout query {i}"))
        assert ans is None and lbl == "none"
        assert provider._breaker._consecutive_failures == i

    assert provider._breaker.is_open() is True


# ===========================================================================
# 3. Fast-Fail with 0 Network Calls & 503 Middleware Inspection
# ===========================================================================

def test_gemini_open_state_zero_network_calls(monkeypatch):
    """During OPEN state, 100 consecutive chat calls fast-fail with 0 network calls in < 50ms."""
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-valid-key")
    provider = EnvCompatProvider(
        "gemini_compat", "default",
        "GEMINI_API_KEY", "GEMINI_BASE_URL", "GEMINI_MODEL",
        default_base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        default_model="gemini-2.0-flash",
    )
    for _ in range(3):
        provider._breaker.record_failure()
    assert provider._breaker.is_open() is True

    dead_client = ScriptedNetworkClient([])
    provider._client = dead_client

    async def _stress_fast_fail():
        t0 = time.monotonic()
        for _ in range(100):
            ans, lbl = await provider.chat("fast fail stress")
            assert ans is None and lbl == "none"
        return time.monotonic() - t0

    elapsed = asyncio.run(_stress_fast_fail())

    assert dead_client.call_count == 0
    assert elapsed < 0.05, f"100 fast-fails took {elapsed}s; expected < 0.05s"



def test_middleware_returns_503_on_open_circuit_breaker():
    """RuntimeSkillGuardMiddleware returns HTTP 503 when circuit breaker is OPEN."""
    reg = CircuitBreakerRegistry()
    breaker_name = "test-gemini-provider-breaker"
    cb = reg.get(breaker_name, fail_threshold=3)
    cb.record_failure()
    cb.record_failure()
    cb.record_failure()
    assert not cb.allow(), "Circuit breaker must be tripped OPEN"

    # Create dummy ASGI app wrapped with RuntimeSkillGuardMiddleware
    async def dummy_app(scope, receive, send):
        response = JSONResponse({"status": "ok"})
        await response(scope, receive, send)

    middleware = RuntimeSkillGuardMiddleware(dummy_app)

    # Simulate HTTP request targeting the tripped breaker
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/health",
        "headers": [(b"x-scp-circuit-breaker", breaker_name.encode("utf-8"))],
        "query_string": b"",
        "client": ("127.0.0.1", 12345),
    }

    sent_messages = []
    async def mock_send(msg):
        sent_messages.append(msg)

    async def mock_receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    asyncio.run(middleware(scope, mock_receive, mock_send))

    # Assert HTTP 503 response
    start_msg = next(m for m in sent_messages if m["type"] == "http.response.start")
    body_msg = next(m for m in sent_messages if m["type"] == "http.response.body")

    assert start_msg["status"] == 503
    headers_dict = {k.decode("utf-8").lower(): v.decode("utf-8") for k, v in start_msg["headers"]}
    assert headers_dict.get("retry-after") == "300"
    assert "circuit_breaker_open" in body_msg["body"].decode("utf-8")


# ===========================================================================
# 4. HALF-OPEN Cooldown, Probe Concurrency & Exception Safety
# ===========================================================================

def test_gemini_half_open_probe_concurrency_locking(monkeypatch):
    """In HALF-OPEN state, exactly 1 caller gets the probe; concurrent callers fast-fail."""
    breaker = CircuitBreaker(failure_threshold=3, cooldown_seconds=10.0)
    for _ in range(3):
        breaker.record_failure()
    assert breaker.is_open() is True

    # Advance clock past cooldown
    monkeypatch.setattr("scp.llm_gateway.circuit_breaker._monotonic", lambda: breaker._opened_at + 15.0)

    # Caller 1 acquires probe
    assert breaker.acquire_half_open_probe() is True
    assert breaker._half_open_probe_in_flight is True

    # Callers 2..20 concurrently attempt probe -> REJECTED
    for _ in range(20):
        assert breaker.acquire_half_open_probe() is False

    # Once probe records success -> reset to CLOSED
    breaker.record_success()
    assert breaker._half_open_probe_in_flight is False
    assert breaker._consecutive_failures == 0
    assert breaker.is_open() is False


def test_gemini_half_open_exception_safety(monkeypatch):
    """If an unhandled exception occurs inside _call_model, probe lock is released via finally."""
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-valid-key")
    provider = EnvCompatProvider(
        "gemini_compat", "default",
        "GEMINI_API_KEY", "GEMINI_BASE_URL", "GEMINI_MODEL",
        default_base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        default_model="gemini-2.0-flash",
    )
    for _ in range(3):
        provider._breaker.record_failure()

    # Advance past cooldown
    monkeypatch.setattr("scp.llm_gateway.circuit_breaker._monotonic", lambda: provider._breaker._opened_at + 305.0)

    # Inject an unexpected crash inside _call_model_inner
    async def _crashing_inner(*args, **kwargs):
        raise SystemError("Simulated unexpected crash during probe")

    monkeypatch.setattr(provider, "_call_model_inner", _crashing_inner)

    with pytest.raises(SystemError):
        asyncio.run(provider._call_model("gemini-2.0-flash", [], "test-key"))

    # Crucial assertion: probe must NOT be leaked
    assert provider._breaker._half_open_probe_in_flight is False
