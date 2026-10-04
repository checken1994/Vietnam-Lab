"""[Agent3-GatewaySentinel R2] Regression: gateway-level hedge cap must apply
when a provider chain contains providers with NO API keys configured.

Contract under test (scp/llm_gateway/client.py, LLMGateway.chat):
  * the rotation pool is built from ENABLED providers only
    (`enabled = [p for p in chain if p.enabled]`);
  * with a single enabled provider, a slow-but-successful primary must still
    return its answer (hedge off / single-provider sequential semantics);
  * an all-disabled chain fails closed with (None, "none").

This pins the pool-construction invariant that the hedge race depends on:
providers without credentials must never enter the race (old-code risk was
rotating into a disabled provider whose enabled=False guard returns
(None, "none") immediately, wasting the hedge fire on a no-op).

Env hygiene: Phase 1.1 rule 5. Network: none (chain fully monkeypatched).
"""
from __future__ import annotations

import asyncio
import time

from scp.llm_gateway.client import EnvCompatProvider, LLMGateway


class _SlowFakeProvider:
    PROVIDER_NAME = "slowfake"

    def __init__(self, delay: float, answer: str):
        self._delay = delay
        self._answer = answer
        self.enabled = True
        self._breaker = type("_B", (), {"is_open": lambda self: False})()

    async def chat(self, question, context="", system_prompt="", prioritize_free=False):
        await asyncio.sleep(self._delay)
        return self._answer, f"{self.PROVIDER_NAME}:model"


class _DeadProvider:
    """A provider with no keys configured: enabled=False by contract."""

    PROVIDER_NAME = "deadfake"

    def __init__(self):
        self.enabled = False
        self._breaker = type("_B", (), {"is_open": lambda self: False})()

    async def chat(self, *args, **kwargs):  # pragma: no cover — must never run
        raise AssertionError("disabled provider must never be called")


def _gateway_with(monkeypatch, providers) -> LLMGateway:
    for name in ("SCP_LLM_HEDGE", "SCP_LLM_ATTEMPT_TIMEOUT_SECONDS", "SCP_LLM_HEDGE_MAX_SECONDS", "SCP_LLM_SEQ_MAX_SECONDS"):
        monkeypatch.delenv(name, raising=False)
    gateway = LLMGateway()
    monkeypatch.setattr(gateway, "_provider_chain", lambda _task: providers, raising=False)
    return gateway


def test_disabled_providers_excluded_from_rotation_pool(monkeypatch):
    """Chain [dead, slow-ok] → rotation must use only the enabled provider."""
    dead = _DeadProvider()
    slow = _SlowFakeProvider(delay=0.05, answer="ok")
    gateway = _gateway_with(monkeypatch, [dead, slow])

    t0 = time.monotonic()
    answer, label = asyncio.run(gateway.chat("q", task="chat"))
    elapsed = time.monotonic() - t0

    assert answer == "ok"
    assert label == "slowfake:model"
    assert elapsed < 5.0


def test_all_disabled_chain_fails_closed(monkeypatch):
    gateway = _gateway_with(monkeypatch, [_DeadProvider(), _DeadProvider()])
    answer, label = asyncio.run(gateway.chat("q", task="chat"))
    assert answer is None
    assert label == "none"


def test_env_compat_provider_without_key_is_disabled():
    """EnvCompatProvider contract: no key → enabled False (pool invariant)."""
    provider = EnvCompatProvider(
        "envtest", "default", "AGENT3_TEST_KEY_ENV", "AGENT3_TEST_BASE_ENV", "AGENT3_TEST_MODEL_ENV",
        default_base_url="https://openrouter.ai/api/v1",
    )
    assert provider.enabled is False
    assert provider._key_count() == 0


# ---------------------------------------------------------------------------
# [W1-c5 2026-10-02] Sequential failover must have a TOTAL time cap. Before
# c5, _chat_sequential waited unbounded: each provider.chat retries transient
# errors 3x under a 60s httpx timeout (~182s per provider), so a chain could
# hold one /ask for many minutes — the missing-lease window behind q05 122.4s
# / q11 89.4s (lifecycle_authority_lost under the old 60s TTL). The cap
# (SCP_LLM_SEQ_MAX_SECONDS, default 90 = hedge cap) fail-closes when
# exhausted; a mid-flight provider killed by the cap is NOT breaker-penalized
# (slow != dead), and fast-failover behaviour is unchanged.
# ---------------------------------------------------------------------------


class _QuotaProvider:
    """Provider that immediately answers quota-style None (failover trigger)."""

    PROVIDER_NAME = "quotafake"

    def __init__(self):
        self.enabled = True
        self._breaker = type("_B", (), {"is_open": lambda self: False})()

    async def chat(self, question, context="", system_prompt="", prioritize_free=False):
        return None, "rate_limited:429"


def test_sequential_failover_total_cap_fails_closed(monkeypatch):
    """Old-fail/new-pass: single slow provider (5s) under a 0.5s total cap.
    OLD _chat_sequential (no cap): slept through and returned 'late'. NEW:
    the cap kills the attempt and fails closed (None, 'none') fast."""
    slow = _SlowFakeProvider(delay=5.0, answer="late")
    gateway = _gateway_with(monkeypatch, [slow])
    monkeypatch.setenv("SCP_LLM_SEQ_MAX_SECONDS", "0.5")

    t0 = time.monotonic()
    answer, label = asyncio.run(gateway.chat("q", task="chat"))
    elapsed = time.monotonic() - t0

    assert answer is None, f"total cap must fail-closed, got answer={answer!r}"
    assert label == "none"
    assert elapsed < 3.0, f"cap must bound the wait, took {elapsed:.2f}s"


def test_sequential_midflight_cap_timeout_not_breaker_penalized(monkeypatch):
    """A provider killed by the cap mid-flight loses the slot but must NOT be
    recorded as a breaker failure (slow != dead — same semantics as losing
    the hedge race)."""
    slow = _SlowFakeProvider(delay=5.0, answer="late")
    gateway = _gateway_with(monkeypatch, [slow])
    monkeypatch.setenv("SCP_LLM_SEQ_MAX_SECONDS", "0.5")

    recorded: list[str] = []
    monkeypatch.setattr(
        slow._breaker, "record_failure", lambda *a, **k: recorded.append("failure"), raising=False
    )
    monkeypatch.setattr(
        slow._breaker, "record_success", lambda *a, **k: recorded.append("success"), raising=False
    )

    answer, label = asyncio.run(gateway.chat("q", task="chat"))

    assert answer is None and label == "none"
    assert recorded == [], f"cap timeout must not touch the breaker, got {recorded}"


def test_sequential_fast_failover_within_cap_unchanged(monkeypatch):
    """The cap must not change healthy failover: quota-None on provider 1
    still rotates to provider 2 which answers within the budget."""
    quota = _QuotaProvider()
    fast = _SlowFakeProvider(delay=0.05, answer="fast")
    gateway = _gateway_with(monkeypatch, [quota, fast])
    monkeypatch.setenv("SCP_LLM_HEDGE", "off")  # force the sequential path
    monkeypatch.setenv("SCP_LLM_SEQ_MAX_SECONDS", "5")

    answer, label = asyncio.run(gateway.chat("q", task="chat"))

    assert answer == "fast"
    assert label == "slowfake:model"
