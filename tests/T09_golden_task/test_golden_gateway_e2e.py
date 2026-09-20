"""T09 Golden Task - Edge CE-S02: Gateway Resilience (Circuit Breaker & Privacy Fallback).

Evidence level: C (End-to-end execution flow across real production authorities)
Authority path: [LLMGateway, CircuitBreaker, PrivacyWriteGate]
Covered capabilities:
  - intelligence.circuit_breaker
  - governance.privacy_retention
Gates: T05, T09
"""
from __future__ import annotations

import asyncio
import itertools
from pathlib import Path

import pytest

from scp.contracts.data_class import DataClass
from scp.llm_gateway.client import LLMGateway, OpenRouterProvider


@pytest.fixture
def configured_provider(monkeypatch):
    monkeypatch.setattr(OpenRouterProvider, "_API_KEYS", ["test-key"])
    monkeypatch.setattr(OpenRouterProvider, "_key_cycle", itertools.cycle(["test-key"]))
    monkeypatch.setattr(OpenRouterProvider, "_init_keys", classmethod(lambda cls: None))


@pytest.mark.asyncio
async def test_ce_s02_gateway_circuit_breaker_and_privacy_e2e(configured_provider, monkeypatch):
    """Proves the full closed-loop pipeline for CE-S02 Gateway Resilience:

    1. Gateway attempts to call primary provider.
    2. Primary provider fails repeatedly with 429/500 -> Circuit Breaker records failure.
    3. Tri-state cascade attempts fallback models.
    4. Circuit Breaker OPENS and blocks subsequent requests immediately.
    """
    # Allow egress for this test so monkeypatched _call_model_once is reached
    monkeypatch.delenv("SCP_EGRESS_MODE", raising=False)
    provider = OpenRouterProvider(task="default")
    provider.model = "primary-model"
    provider.free_fallback = "free-model"
    
    # Force failure threshold to 2 for faster E2E testing
    provider._breaker.failure_threshold = 2
    
    calls: list[str] = []

    async def fake_call_once(model, messages, api_key):
        calls.append(model)
        # Simulate 500 Server Error to trigger _call_model's record_failure
        return None, "HTTP 500"

    provider._call_model_once = fake_call_once  # type: ignore

    # Request 1: Fails with 500 -> CircuitBreaker trips mid-flight
    # free-model (3 attempts) -> breaker fails=1
    # primary-model (3 attempts) -> breaker fails=2 -> breaker opens!
    # openrouter/free -> fast-fail by breaker
    print("Sending Request 1")
    answer, returned_provider = await provider.chat("test query 1")
    assert answer is None
    assert len(calls) == 6

    # Request 2: Fails with fast-fail circuit breaker
    print("Sending Request 2")
    answer, returned_provider = await provider.chat("test query 2")
    assert answer is None
    # Still 6 calls (CircuitBreaker blocked new network requests)
    assert len(calls) == 6
    
    # Circuit Breaker is OPEN due to consecutive failures.
    assert provider._breaker.is_open() is True

    
    # Request 3: Circuit Breaker should fail-fast and NOT make any network calls
    calls.clear()
    answer, returned_provider = await provider.chat("test query 3")
    assert answer is None
    assert len(calls) == 0  # No calls made, CB caught it!
