"""[F-05 regression 2026-09-25] Per-process dead-model circuit breaker.

Runtime audit RUNTIME-AUDIT-20260925-0411 finding F-05 (LOW): every LLM-lane
ask burned 3x HTTP 404 on stale OpenRouter model names in the fallback chain
before a live model answered (known debt GA.md B11 — the owner's .env lists
dead model names). .env belongs to the owner, so the PRODUCT fix is a
per-process dead-model cache inside the gateway client:

  (a) a 404 classified as model-not-found marks the candidate dead; the next
      call skips ONLY that candidate and the chain still succeeds;
  (b) any other 404 (different body semantics) is a normal failure and is
      NEVER breaker-cached;
  (c) kill-switch SCP_LLM_DEAD_MODEL_BREAKER=off → no caching, the candidate
      is attempted again.

Env hygiene (Phase 1.1 rule 5): env changes go through monkeypatch only.
"""
from __future__ import annotations

import asyncio

import pytest

from scp.llm_gateway import client as gw_client
from scp.llm_gateway.client import OpenRouterProvider


@pytest.fixture(autouse=True)
def _clean_dead_model_registry():
    """Process-level registry must be empty at the start and end of each test."""
    gw_client.reset_dead_model_cache()
    yield
    gw_client.reset_dead_model_cache()


class _FakeResponse:
    def __init__(self, status_code: int, text: str = ""):
        self.status_code = status_code
        self.text = text

    def json(self):
        import json

        return json.loads(self.text)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"Client error '{self.status_code}' for url chat/completions")


class _FakeClient:
    def __init__(self, resp: _FakeResponse):
        self._resp = resp
        self.posts = 0

    async def post(self, url, json=None, headers=None):
        self.posts += 1
        return self._resp


def _make_provider(monkeypatch, primary: str = "stale/dead-model-test", fallback: str = "openrouter/free") -> OpenRouterProvider:
    monkeypatch.setenv("OPENROUTER_MODEL", primary)
    monkeypatch.setenv("OPENROUTER_MODEL_DEFAULT", fallback)
    monkeypatch.delenv("SCP_BUDGET_ROUTING", raising=False)
    provider = OpenRouterProvider(task="default")
    provider._API_KEYS = ["test-key"]
    provider._next_key = lambda: "test-key"  # type: ignore[method-assign]
    return provider


def _allow_egress(monkeypatch):
    """Isolate the 404 classifier from the egress policy (egress has its own
    suite: test_llm_egress_policy.py)."""
    monkeypatch.setattr(gw_client, "_llm_egress_allowed", lambda base_url: True)
    monkeypatch.setattr(gw_client, "enforce_egress_policy", lambda *a, **k: None)


def test_model_not_found_404_is_cached_and_skipped_on_next_call(monkeypatch):
    """(a) 404 model-not-found → the dead candidate is attempted exactly ONCE
    across two asks; the chain still succeeds both times via the fallback."""
    provider = _make_provider(monkeypatch)
    calls: list[str] = []

    async def fake_call_model_once(model: str, messages: list[dict], api_key: str):
        calls.append(model)
        if model == provider.model:
            return None, f"model_not_found: HTTP 404 for model '{model}' on openrouter"
        return "live model answer", None

    monkeypatch.setattr(provider, "_call_model_once", fake_call_model_once)

    async def scenario():
        first = await provider.chat("q1")
        second = await provider.chat("q2")
        return first, second

    first, second = asyncio.run(scenario())

    # Chain still succeeds BOTH times (breaker only skips the dead candidate)
    assert first[0] == "live model answer"
    assert second[0] == "live model answer"
    assert first[1] == f"openrouter:{provider.free_fallback}"
    # The dead primary was attempted exactly once — no re-burn on ask #2
    assert calls.count(provider.model) == 1
    assert calls.count(provider.free_fallback) == 2
    # And the candidate is registered as dead for the process
    assert gw_client._is_dead_model(provider.PROVIDER_NAME, provider.base_url, provider.model)


def test_strict_classifier_real_call_model_once_path(monkeypatch):
    """The REAL _call_model_once classifies an OpenRouter-style 'No endpoints
    found' 404 body as model_not_found; _call_model caches it AND skips the
    transient retries (1 HTTP call instead of the 3x burn seen in the audit)."""
    _allow_egress(monkeypatch)
    provider = _make_provider(monkeypatch)
    body = (
        '{"error": {"message": "No endpoints found matching model '
        'stale/dead-model-test.", "code": 404}}'
    )
    client = _FakeClient(_FakeResponse(404, text=body))

    async def fake_get_client():
        return client

    monkeypatch.setattr(provider, "_get_client", fake_get_client)

    async def scenario():
        return await provider._call_model(
            provider.model, [{"role": "user", "content": "q"}], "test-key"
        )

    answer, err = asyncio.run(scenario())
    assert answer is None
    assert err is not None and err.startswith("model_not_found")
    # Permanent error: NO transient retry at the HTTP layer (audit saw 3x 404)
    assert client.posts == 1
    assert gw_client._is_dead_model(provider.PROVIDER_NAME, provider.base_url, provider.model)


def test_other_404s_are_not_breaker_cached(monkeypatch):
    """(b) A 404 that does NOT say model-not-found (permissions body) is a
    normal failure: retried as today, recorded on the provider breaker, and
    NEVER added to the dead-model registry."""
    _allow_egress(monkeypatch)
    provider = _make_provider(monkeypatch)
    body = '{"error": {"message": "Insufficient permissions to access this endpoint", "code": 404}}'
    client = _FakeClient(_FakeResponse(404, text=body))

    async def fake_get_client():
        return client

    monkeypatch.setattr(provider, "_get_client", fake_get_client)

    async def scenario():
        return await provider._call_model(
            provider.model, [{"role": "user", "content": "q"}], "test-key"
        )

    err = asyncio.run(scenario())
    assert err is not None and "404" in str(err)
    # Normal-failure semantics preserved: the HTTP layer saw the retries
    assert client.posts == 3
    # ...and the candidate was NOT cached as dead
    assert not gw_client._is_dead_model(provider.PROVIDER_NAME, provider.base_url, provider.model)
    assert gw_client._DEAD_MODEL_REGISTRY == set()


def test_kill_switch_off_disables_caching(monkeypatch):
    """(c) SCP_LLM_DEAD_MODEL_BREAKER=off → no caching at all and the dead
    candidate is attempted on every ask (old behavior)."""
    monkeypatch.setenv("SCP_LLM_DEAD_MODEL_BREAKER", "off")
    provider = _make_provider(monkeypatch)
    calls: list[str] = []

    async def fake_call_model_once(model: str, messages: list[dict], api_key: str):
        calls.append(model)
        if model == provider.model:
            return None, f"model_not_found: HTTP 404 for model '{model}' on openrouter"
        return "fallback ok", None

    monkeypatch.setattr(provider, "_call_model_once", fake_call_model_once)

    async def scenario():
        await provider.chat("q1")
        await provider.chat("q2")

    asyncio.run(scenario())
    assert gw_client._DEAD_MODEL_REGISTRY == set()
    assert calls.count(provider.model) == 2, "kill-switch off must not skip the candidate"


def test_non_404_statuses_never_cached(monkeypatch):
    """Defense in depth: 404-only classification — 5xx/429 responses never
    enter the dead-model registry even with model-like bodies."""
    _allow_egress(monkeypatch)
    provider = _make_provider(monkeypatch)
    client = _FakeClient(
        _FakeResponse(500, text='{"error": {"message": "No endpoints found matching model x."}}')
    )

    async def fake_get_client():
        return client

    monkeypatch.setattr(provider, "_get_client", fake_get_client)

    async def scenario():
        return await provider._call_model(
            provider.model, [{"role": "user", "content": "q"}], "test-key"
        )

    asyncio.run(scenario())
    assert gw_client._DEAD_MODEL_REGISTRY == set()
