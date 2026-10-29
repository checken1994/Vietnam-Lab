"""Empirical Stress & Resilience Verification for Gemini Native Provider & CircuitBreaker.

Validates M1 requirements under adversarial fault-injection:
1. GEMINI_API_KEY missing, placeholder, or invalid handling.
2. Endpoint 500 & timeout fault injection triggering CircuitBreaker OPEN after 3 failures.
3. Fast-fail behavior during OPEN state with 0 network calls.
4. Transition to HALF-OPEN after cooldown, probe concurrency locking, and probe pass/fail transitions.
"""
from __future__ import annotations

import asyncio
import itertools
import time

import httpx
import pytest

from scp.llm_gateway.circuit_breaker import CircuitBreaker
from scp.llm_gateway.client import EnvCompatProvider, LLMGateway, OpenRouterProvider


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
    """Mock HTTP client tracking call count, recording payloads, and popping scripted responses."""

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
    """Ensure baseline egress allowlist permits Gemini endpoint and OpenRouter."""
    from scp.llm_gateway.circuit_breaker import reset_dead_model_cache
    reset_dead_model_cache()
    monkeypatch.setenv("SCP_EGRESS_MODE", "allowlist")
    monkeypatch.setenv(
        "SCP_LLM_EGRESS_ALLOWLIST",
        "generativelanguage.googleapis.com,openrouter.ai,api.openai.com",
    )
    # Isolate environment: prevent local .env keys and model overrides from leaking into tests
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    monkeypatch.delenv("GEMINI_BASE_URL", raising=False)
    monkeypatch.delenv("SCP_LLM_FALLBACK_PROVIDERS", raising=False)
    # Disable backoff sleep during fast stress testing without unittest.mock
    async def _noop_sleep(*args, **kwargs):
        pass

    monkeypatch.setattr(asyncio, "sleep", _noop_sleep)
    yield
    reset_dead_model_cache()



# ===========================================================================
# Suite 1: Missing, Placeholder & Invalid GEMINI_API_KEY
# ===========================================================================

def test_gemini_missing_key_disabled(monkeypatch):
    """When GEMINI_API_KEY is not set or empty, provider must be disabled."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    provider = EnvCompatProvider(
        "gemini_compat", "default",
        "GEMINI_API_KEY", "GEMINI_BASE_URL", "GEMINI_MODEL",
        default_base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        default_model="gemini-2.0-flash",
    )
    assert provider.enabled is False
    assert provider._key_count() == 0

    # Gateway chain should NOT include disabled gemini_compat
    gateway = LLMGateway()
    gemini_in_gw = next(
        p for p in gateway._extra_providers["default"] if p.PROVIDER_NAME == "gemini_compat"
    )
    assert gemini_in_gw.enabled is False
    chain = gateway._provider_chain("default")
    assert not any(p.PROVIDER_NAME == "gemini_compat" for p in chain)


@pytest.mark.parametrize("placeholder", ["changeme", "your-key", "your_api_key", "placeholder", "xxx", "sk-xxx", "none"])
def test_gemini_placeholder_key_disabled(monkeypatch, placeholder):
    """Placeholder keys must NOT enable the Gemini provider."""
    monkeypatch.setenv("GEMINI_API_KEY", placeholder)
    provider = EnvCompatProvider(
        "gemini_compat", "default",
        "GEMINI_API_KEY", "GEMINI_BASE_URL", "GEMINI_MODEL",
        default_base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        default_model="gemini-2.0-flash",
    )
    assert provider.enabled is False


def test_gemini_invalid_key_401_fails_over(monkeypatch):
    """When GEMINI_API_KEY is invalid (e.g., 401 Unauthorized), provider fails over cleanly."""
    monkeypatch.setenv("GEMINI_API_KEY", "invalid-secret-key-12345")
    provider = EnvCompatProvider(
        "gemini_compat", "default",
        "GEMINI_API_KEY", "GEMINI_BASE_URL", "GEMINI_MODEL",
        default_base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        default_model="gemini-2.0-flash",
    )
    assert provider.enabled is True

    # 401 response from Gemini endpoint
    mock_client = ScriptedNetworkClient([
        MockResponse(401, "Invalid API key"),
        MockResponse(401, "Invalid API key"),
        MockResponse(401, "Invalid API key"),
    ])
    provider._client = mock_client

    answer, label = asyncio.run(provider.chat("Hello"))
    assert answer is None
    assert label == "none"
    # Verification: request was attempted with Authorization header
    assert mock_client.call_count == 3
    assert mock_client.recorded_calls[0]["headers"]["Authorization"] == "Bearer invalid-secret-key-12345"


# ===========================================================================
# Suite 2: Endpoint 500 & Timeout -> CircuitBreaker Trips to OPEN after 3 Failures
# ===========================================================================

def test_gemini_endpoint_500_trips_breaker_after_3_failures(monkeypatch):
    """3 consecutive HTTP 500 errors trip the CircuitBreaker from CLOSED to OPEN."""
    monkeypatch.setenv("GEMINI_API_KEY", "valid-gemini-key")
    provider = EnvCompatProvider(
        "gemini_compat", "default",
        "GEMINI_API_KEY", "GEMINI_BASE_URL", "GEMINI_MODEL",
        default_base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        default_model="gemini-2.0-flash",
    )
    assert provider._breaker.is_open() is False
    assert provider._breaker._consecutive_failures == 0

    # 3 calls, each retrying 3 times inside _call_model_inner before recording failure
    script = [MockResponse(500, "Internal Server Error") for _ in range(9)]
    mock_client = ScriptedNetworkClient(script)
    provider._client = mock_client

    # Call 1 -> failure 1
    ans1, lbl1 = asyncio.run(provider.chat("call 1"))
    assert ans1 is None and lbl1 == "none"
    assert provider._breaker._consecutive_failures == 1
    assert provider._breaker.is_open() is False

    # Call 2 -> failure 2
    ans2, lbl2 = asyncio.run(provider.chat("call 2"))
    assert ans2 is None and lbl2 == "none"
    assert provider._breaker._consecutive_failures == 2
    assert provider._breaker.is_open() is False

    # Call 3 -> failure 3 -> TRIPS TO OPEN
    ans3, lbl3 = asyncio.run(provider.chat("call 3"))
    assert ans3 is None and lbl3 == "none"
    assert provider._breaker._consecutive_failures == 3
    assert provider._breaker.is_open() is True
    assert provider._breaker._opened_at is not None


def test_gemini_endpoint_timeout_trips_breaker_after_3_failures(monkeypatch):
    """3 consecutive timeout exceptions trip CircuitBreaker to OPEN."""
    monkeypatch.setenv("GEMINI_API_KEY", "valid-gemini-key")
    provider = EnvCompatProvider(
        "gemini_compat", "default",
        "GEMINI_API_KEY", "GEMINI_BASE_URL", "GEMINI_MODEL",
        default_base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        default_model="gemini-2.0-flash",
    )
    assert provider._breaker.is_open() is False

    script = [httpx.ReadTimeout("Read timed out") for _ in range(9)]
    mock_client = ScriptedNetworkClient(script)
    provider._client = mock_client

    for i in range(1, 4):
        ans, lbl = asyncio.run(provider.chat(f"call {i}"))
        assert ans is None and lbl == "none"
        assert provider._breaker._consecutive_failures == i

    assert provider._breaker.is_open() is True


def test_gemini_intermittent_success_resets_breaker(monkeypatch):
    """A successful request resets consecutive failure count to 0, preventing premature trip."""
    monkeypatch.setenv("GEMINI_API_KEY", "valid-gemini-key")
    provider = EnvCompatProvider(
        "gemini_compat", "default",
        "GEMINI_API_KEY", "GEMINI_BASE_URL", "GEMINI_MODEL",
        default_base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        default_model="gemini-2.0-flash",
    )
    mock_client = ScriptedNetworkClient([
        # Call 1: 3x 500 -> 1 failure
        MockResponse(500), MockResponse(500), MockResponse(500),
        # Call 2: Success -> resets consecutive failures to 0
        MockResponse(200, "Gemini answers successfully"),
        # Call 3: 3x 500 -> 1 failure
        MockResponse(500), MockResponse(500), MockResponse(500),
        # Call 4: 3x 500 -> 2 failures (not yet tripped)
        MockResponse(500), MockResponse(500), MockResponse(500),
    ])
    provider._client = mock_client

    # Call 1
    asyncio.run(provider.chat("call 1"))
    assert provider._breaker._consecutive_failures == 1

    # Call 2 (Success)
    ans2, lbl2 = asyncio.run(provider.chat("call 2"))
    assert ans2 == "Gemini answers successfully"
    assert provider._breaker._consecutive_failures == 0
    assert provider._breaker.is_open() is False

    # Call 3
    asyncio.run(provider.chat("call 3"))
    assert provider._breaker._consecutive_failures == 1

    # Call 4
    asyncio.run(provider.chat("call 4"))
    assert provider._breaker._consecutive_failures == 2
    assert provider._breaker.is_open() is False


# ===========================================================================
# Suite 3: Fast-Fail During OPEN State Without Sending Network Requests
# ===========================================================================

def test_gemini_open_state_fast_fails_without_network_request(monkeypatch):
    """During OPEN state, provider fast-fails without burning timeout or touching network."""
    monkeypatch.setenv("GEMINI_API_KEY", "valid-gemini-key")
    provider = EnvCompatProvider(
        "gemini_compat", "default",
        "GEMINI_API_KEY", "GEMINI_BASE_URL", "GEMINI_MODEL",
        default_base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        default_model="gemini-2.0-flash",
    )
    # Trip breaker
    for _ in range(3):
        provider._breaker.record_failure()
    assert provider._breaker.is_open() is True

    # Injected client with NO scripted responses — any call would raise RuntimeError
    dead_client = ScriptedNetworkClient([])
    provider._client = dead_client

    t0 = time.monotonic()
    ans, lbl = asyncio.run(provider.chat("Are you there?"))
    elapsed = time.monotonic() - t0

    assert ans is None
    assert lbl == "none"
    assert dead_client.call_count == 0, "No network request should be made when circuit breaker is OPEN"
    assert elapsed < 0.1, f"Fast-fail must be near-instantaneous (took {elapsed}s)"


def test_gateway_partitions_open_gemini_to_degraded_pool(monkeypatch):
    """Gateway partitions an OPEN Gemini provider into degraded pool, prioritizing healthy OpenRouter."""
    monkeypatch.setenv("GEMINI_API_KEY", "valid-gemini-key")
    gateway = LLMGateway()

    # Configure OpenRouter mock
    openrouter = gateway.openrouter_default
    monkeypatch.setattr(type(openrouter), "_API_KEYS", ["test-or-key"], raising=False)
    monkeypatch.setattr(type(openrouter), "_key_cycle", itertools.cycle(["test-or-key"]), raising=False)
    openrouter._client = ScriptedNetworkClient([MockResponse(200, "OpenRouter recovered answer")])

    # Configure Gemini and trip its breaker
    gemini = next(p for p in gateway._extra_providers["default"] if p.PROVIDER_NAME == "gemini_compat")
    for _ in range(3):
        gemini._breaker.record_failure()
    assert gemini._breaker.is_open() is True

    # Gemini client should NOT be called
    gemini_client = ScriptedNetworkClient([])
    gemini._client = gemini_client

    answer, label = asyncio.run(gateway.chat("test query", task="default"))
    assert answer == "OpenRouter recovered answer"
    assert gemini_client.call_count == 0


def test_gateway_all_open_fast_fails(monkeypatch):
    """When all providers have OPEN breakers, gateway fast-fails cleanly."""
    monkeypatch.setenv("GEMINI_API_KEY", "valid-gemini-key")
    gateway = LLMGateway()

    for task_name, providers in gateway._extra_providers.items():
        for p in providers:
            for _ in range(3):
                p._breaker.record_failure()

    or_provider = gateway.openrouter_default
    monkeypatch.setattr(type(or_provider), "_API_KEYS", ["test-or-key"], raising=False)
    monkeypatch.setattr(type(or_provider), "_key_cycle", itertools.cycle(["test-or-key"]), raising=False)
    for _ in range(3):
        or_provider._breaker.record_failure()

    dead_client = ScriptedNetworkClient([])
    or_provider._client = dead_client

    answer, label = asyncio.run(gateway.chat("test query", task="default"))
    assert answer is None
    assert label == "none"
    assert dead_client.call_count == 0


# ===========================================================================
# Suite 4: Transition to HALF-OPEN after Cooldown
# ===========================================================================

def test_gemini_transitions_to_half_open_after_cooldown(monkeypatch):
    """After cooldown period elapses, CircuitBreaker allows exactly 1 HALF-OPEN probe."""
    breaker = CircuitBreaker(failure_threshold=3, cooldown_seconds=10.0)

    # Trip breaker
    breaker.record_failure()
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.is_open() is True
    assert breaker.acquire_half_open_probe() is False

    # Simulate clock advance before cooldown -> still OPEN
    monkeypatch.setattr("scp.llm_gateway.circuit_breaker._monotonic", lambda: breaker._opened_at + 5.0)
    assert breaker.is_open() is True
    assert breaker.acquire_half_open_probe() is False

    # Simulate clock advance past cooldown -> HALF-OPEN
    monkeypatch.setattr("scp.llm_gateway.circuit_breaker._monotonic", lambda: breaker._opened_at + 15.0)
    # Pure classifier: cooldown elapsed, not strictly OPEN anymore
    assert breaker.is_open() is False

    # Caller 1 acquires the probe
    assert breaker.acquire_half_open_probe() is True
    assert breaker._half_open_probe_in_flight is True
    # Consecutive failures reduced to threshold - 1 (2)
    assert breaker._consecutive_failures == 2

    # Caller 2 concurrently tries to acquire probe -> REJECTED (single probe invariant)
    assert breaker.acquire_half_open_probe() is False


def test_gemini_half_open_probe_success_resets_to_closed(monkeypatch):
    """When a HALF-OPEN probe succeeds, the breaker resets to fully CLOSED."""
    breaker = CircuitBreaker(failure_threshold=3, cooldown_seconds=10.0)
    for _ in range(3):
        breaker.record_failure()

    # Advance past cooldown
    monkeypatch.setattr("scp.llm_gateway.circuit_breaker._monotonic", lambda: breaker._opened_at + 20.0)
    acquired = breaker.acquire_half_open_probe()
    assert acquired is True

    # Probe succeeds
    breaker.record_success()
    assert breaker._consecutive_failures == 0
    assert breaker._opened_at is None
    assert breaker._half_open_probe_in_flight is False
    assert breaker.is_open() is False


def test_gemini_half_open_probe_failure_re_opens_immediately(monkeypatch):
    """When a HALF-OPEN probe fails, the breaker immediately trips back to OPEN."""
    breaker = CircuitBreaker(failure_threshold=3, cooldown_seconds=10.0)
    for _ in range(3):
        breaker.record_failure()

    probe_time = breaker._opened_at + 25.0
    monkeypatch.setattr("scp.llm_gateway.circuit_breaker._monotonic", lambda: probe_time)
    acquired = breaker.acquire_half_open_probe()
    assert acquired is True

    # Probe fails
    breaker.record_failure()
    assert breaker._consecutive_failures == 3
    assert breaker._opened_at == probe_time
    assert breaker._half_open_probe_in_flight is False
    assert breaker.is_open() is True


# ===========================================================================
# Suite 5: Quota & Rate Limit (429 / 402) Dead-Model Caching & Router Failsafe
# ===========================================================================

def test_gemini_402_hard_quota_marks_dead_model_immediately(monkeypatch):
    """HTTP 402 Payment Required immediately marks the model dead and records breaker failure."""
    from scp.llm_gateway.circuit_breaker import _is_dead_model, reset_dead_model_cache
    reset_dead_model_cache()

    monkeypatch.setenv("GEMINI_API_KEY", "valid-gemini-key")
    provider = EnvCompatProvider(
        "gemini_compat", "default",
        "GEMINI_API_KEY", "GEMINI_BASE_URL", "GEMINI_MODEL",
        default_base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        default_model="gemini-2.0-flash",
    )
    provider._client = ScriptedNetworkClient([MockResponse(402, "Payment Required")])

    answer, label = asyncio.run(provider.chat("test"))
    assert answer is None and label == "none"
    assert provider._breaker._consecutive_failures == 1
    assert _is_dead_model("gemini_compat", provider.base_url, "gemini-2.0-flash") is True


def test_gemini_question_router_fail_closed_when_all_fail():
    """When Gateway cannot get any response, question_router._l2_failsafe engages fail-closed."""
    from scp.runtime.question_router import classify_l2, classify_l2_async

    # Case A: Gateway returns (None, "none") -> parses as unparseable_fail_closed
    class EmptyGateway:
        async def chat(self, *args, **kwargs):
            return (None, "none")

        def chat_sync(self, *args, **kwargs):
            return (None, "none")

    fake_gateway_empty = EmptyGateway()

    decision = classify_l2("What is the capital of Vietnam?", gateway=fake_gateway_empty)
    assert decision.via == "l2-failsafe"
    assert decision.reason == "unparseable_fail_closed"
    assert not hasattr(decision, "bypass_verdict_pass")

    decision_async = asyncio.run(classify_l2_async("What is the capital of Vietnam?", gateway=fake_gateway_empty))
    assert decision_async.via == "l2-failsafe"
    assert decision_async.reason == "unparseable_fail_closed"
    assert not hasattr(decision_async, "bypass_verdict_pass")

    # Case B: Gateway raises Exception -> caught and returns llm_error_fail_closed
    class ErrorGateway:
        async def chat(self, *args, **kwargs):
            raise RuntimeError("All providers dead")

        def chat_sync(self, *args, **kwargs):
            raise RuntimeError("All providers dead")

    fake_gateway_err = ErrorGateway()

    decision_err = classify_l2("What is the capital of Vietnam?", gateway=fake_gateway_err)
    assert decision_err.via == "l2-failsafe"
    assert decision_err.reason == "llm_error_fail_closed"
    assert not hasattr(decision_err, "bypass_verdict_pass")


def test_gemini_route_task_resolution_and_failover(monkeypatch):
    """Verify GAP-M1-01 remediation: task='route' includes gemini_compat in failover cascade."""
    monkeypatch.setenv("GEMINI_API_KEY", "valid-gemini-key")

    gateway = LLMGateway()
    route_chain = [p.PROVIDER_NAME for p in gateway._provider_chain("route")]
    assert "gemini_compat" in route_chain, "gemini_compat must be present in route provider chain"

    # Trip OpenRouter circuit breaker to simulate OpenRouter outage cleanly without unmocked network calls
    openrouter = getattr(gateway, "openrouter_route", gateway.openrouter_default)
    for _ in range(3):
        openrouter._breaker.record_failure()
    assert openrouter._breaker.is_open() is True

    # Mock Gemini response to verify full end-to-end chat execution under task='route'
    gemini = next(p for p in gateway._extra_providers["route"] if p.PROVIDER_NAME == "gemini_compat")
    mock_client = ScriptedNetworkClient([MockResponse(200, "classification: REASONING")])
    gemini._client = mock_client

    answer, provider_name = asyncio.run(gateway.chat("Classify this query", task="route"))
    assert answer == "classification: REASONING"
    assert provider_name.startswith("gemini_compat")
    assert mock_client.call_count == 1

    # Unmapped arbitrary task must also fall back to default extra providers
    unmapped_chain = [p.PROVIDER_NAME for p in gateway._provider_chain("unmapped_custom_task")]
    assert "gemini_compat" in unmapped_chain, "Unmapped tasks must fall back to default extra providers"






