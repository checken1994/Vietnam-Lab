"""Empirical Stress & Boundary Challenge for Question Router & LLM Gateway (M1).

Validates M1 requirements under adversarial fault-injection and empirical probes:
1. Stress-test question routing under LLM gateway failure:
   Verify _l2_failsafe catches errors and strictly returns bypass_verdict_pass=False.
2. Verify that NO network packets or socket connections are attempted to port 8081
   or localhost Ollama bridge.
3. Verify concurrency handling in async routing under provider failover and identify
   architectural failover gaps in task="route".
"""
from __future__ import annotations

import asyncio
import os
import socket
import threading
import time
from typing import Any

import httpx
import pytest
import yaml

from scp.llm_gateway.client import EnvCompatProvider, LLMGateway, get_gateway
from scp.runtime.question_router import (
    LANE_CHATBOT,
    LANE_FACTUAL,
    LOOKUP,
    REASONING,
    RouteDecision,
    classify_l2,
    classify_l2_async,
    route_question,
    route_question_async,
    route_stats_snapshot,
)
from scp.security.provider_keys import ProviderCredentialError
from scp.security.url_safety import EgressDeniedError


# ===========================================================================
# Suite 1: Stress-test Question Routing under Gateway Failure
# ===========================================================================

@pytest.mark.parametrize(
    "exc_to_raise",
    [
        httpx.ConnectError("Connection refused to remote provider"),
        httpx.ConnectTimeout("Connect timed out after 10.0s"),
        httpx.ReadTimeout("Read timed out waiting for LLM completion"),
        httpx.HTTPStatusError("500 Internal Server Error", request=httpx.Request("POST", "https://api.openai.com"), response=httpx.Response(500)),
        httpx.HTTPStatusError("429 Too Many Requests", request=httpx.Request("POST", "https://api.openai.com"), response=httpx.Response(429)),
        httpx.HTTPStatusError("401 Unauthorized", request=httpx.Request("POST", "https://api.openai.com"), response=httpx.Response(401)),
        ProviderCredentialError("OPENROUTER_API_KEY missing or invalid"),
        EgressDeniedError(target="https://api.openai.com", reason="Egress to host denied by policy"),
        RuntimeError("Unexpected gateway worker panic"),
        ValueError("Invalid prompt encoding"),
        OSError(101, "Network is unreachable"),
        MemoryError("Out of memory during tensor allocation"),
    ],
)
def test_l2_failsafe_catches_all_exceptions_sync(exc_to_raise: Exception):
    """Sync classify_l2 must catch every Exception and return bypass_verdict_pass=False."""
    class FaultyGateway:
        def chat(self, *args, **kwargs):
            raise exc_to_raise

    question = "What is the philosophical nature of reality and consciousness?"
    decision = classify_l2(question, gateway=FaultyGateway())

    assert decision.via == "l2-failsafe", f"Expected l2-failsafe, got {decision.via}"
    assert decision.reason == "llm_error_fail_closed", f"Expected llm_error_fail_closed, got {decision.reason}"
    assert decision.intent == REASONING
    assert decision.lane == LANE_CHATBOT
    assert decision.confidence == 0.5
    assert decision.bypass_verdict_pass is False, "CRITICAL: Failsafe MUST NOT allow verdict pass bypass!"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exc_to_raise",
    [
        httpx.ConnectError("Connection refused to remote provider"),
        httpx.ConnectTimeout("Connect timed out after 10.0s"),
        httpx.ReadTimeout("Read timed out waiting for LLM completion"),
        httpx.HTTPStatusError("500 Internal Server Error", request=httpx.Request("POST", "https://api.openai.com"), response=httpx.Response(500)),
        httpx.HTTPStatusError("429 Too Many Requests", request=httpx.Request("POST", "https://api.openai.com"), response=httpx.Response(429)),
        httpx.HTTPStatusError("401 Unauthorized", request=httpx.Request("POST", "https://api.openai.com"), response=httpx.Response(401)),
        ProviderCredentialError("OPENROUTER_API_KEY missing or invalid"),
        EgressDeniedError(target="https://api.openai.com", reason="Egress to host denied by policy"),
        RuntimeError("Unexpected gateway worker panic"),
        ValueError("Invalid prompt encoding"),
        OSError(101, "Network is unreachable"),
    ],
)
async def test_l2_failsafe_catches_all_exceptions_async(exc_to_raise: Exception):
    """Async classify_l2_async must catch every Exception and return bypass_verdict_pass=False."""
    class FaultyAsyncGateway:
        async def chat(self, *args, **kwargs):
            raise exc_to_raise

    question = "What is the philosophical nature of reality and consciousness?"
    decision = await classify_l2_async(question, gateway=FaultyAsyncGateway())

    assert decision.via == "l2-failsafe"
    assert decision.reason == "llm_error_fail_closed"
    assert decision.intent == REASONING
    assert decision.lane == LANE_CHATBOT
    assert decision.confidence == 0.5
    assert decision.bypass_verdict_pass is False, "CRITICAL: Failsafe MUST NOT allow verdict pass bypass!"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "malformed_return",
    [
        ("", "stub:model"),
        ("   ", "stub:model"),
        ("I cannot answer this question because it is controversial", "stub:model"),
        ("404 Not Found", "stub:model"),
        ("{'error': 'service unavailable'}", "stub:model"),
        (None, "stub:model"),
        (12345, "stub:model"),
        None,  # Unpack failure (TypeError)
        ("LOOKUP but also REASONING", "stub:model"),  # Contains both
    ],
)
async def test_l2_malformed_outputs_fail_closed(malformed_return: Any):
    """Malformed or ambiguous responses must fail closed with bypass_verdict_pass=False."""
    class MalformedGateway:
        async def chat(self, *args, **kwargs):
            return malformed_return

    question = "What is the meaning of life?"
    decision = await classify_l2_async(question, gateway=MalformedGateway())

    if malformed_return and isinstance(malformed_return, tuple) and "LOOKUP" in str(malformed_return[0])[:40]:
        # If LOOKUP is parsed first, it goes to factual lane with bypass=False
        assert decision.bypass_verdict_pass is False
    else:
        # Otherwise it MUST fail closed as failsafe
        assert decision.via == "l2-failsafe"
        assert decision.bypass_verdict_pass is False


@pytest.mark.asyncio
async def test_route_question_async_e2e_gateway_down():
    """End-to-end route_question_async on indeterminate question with dead gateway."""
    class DeadGateway:
        async def chat(self, *args, **kwargs):
            raise httpx.ConnectError("All LLM providers unreachable")

    # Indeterminate questions that return None from classify_l0 to reach L2
    test_queries = [
        "indeterminate conceptual discussion alpha",
        "epistemic versus aleatoric differentiation",
        "abstract conceptual divergence xyz",
    ]

    for q in test_queries:
        decision = await route_question_async(q, gateway=DeadGateway())
        assert decision.via == "l2-failsafe"
        assert decision.reason == "llm_error_fail_closed"
        assert decision.bypass_verdict_pass is False


# ===========================================================================
# Suite 2: Verify NO network packets / socket connections to port 8081
# ===========================================================================

def test_no_socket_connections_to_port_8081(monkeypatch):
    """Empirically intercept all socket connect attempts and assert port 8081 is NEVER targeted."""
    attempted_connections: list[tuple[str, int]] = []
    orig_connect = socket.socket.connect

    def _intercepting_connect(self, address):
        if isinstance(address, tuple) and len(address) >= 2:
            host, port = address[0], address[1]
            attempted_connections.append((str(host), int(port)))
            if port == 8081:
                raise AssertionError(f"VIOLATION: Attempted socket connection to forbidden port 8081 ({address})")
        return orig_connect(self, address)

    monkeypatch.setattr(socket.socket, "connect", _intercepting_connect)

    # 1. Execute sync route
    decision1 = route_question("What is the capital of France?")
    assert decision1.intent == LOOKUP

    # 2. Execute L2 sync route with standard gateway
    decision2 = route_question("Explain the ontological foundation of mathematics.")
    assert decision2 is not None

    # Check recorded connections
    for host, port in attempted_connections:
        assert port != 8081, f"Socket connection attempted to port 8081 on {host}"


def test_no_http_traffic_to_port_8081(monkeypatch):
    """Verify that httpx and safe_urlopen never target port 8081 or llm-bridge."""
    urls_called: list[str] = []

    async def _intercept_httpx_send(self, request, *args, **kwargs):
        url_str = str(request.url)
        urls_called.append(url_str)
        assert ":8081" not in url_str, f"VIOLATION: httpx request to port 8081: {url_str}"
        assert "llm-bridge" not in url_str, f"VIOLATION: httpx request to llm-bridge: {url_str}"
        return httpx.Response(
            200,
            request=request,
            json={"choices": [{"message": {"content": "LOOKUP"}}]},
        )

    monkeypatch.setattr(httpx.AsyncClient, "send", _intercept_httpx_send)

    # Run routing across both sync and async paths
    route_question("arbitrary nonsensical phrase num 1")
    route_question("What is the capital of France?")
    route_question("2 + 2 = ?")
    route_question("Thủ đô của Việt Nam là gì?")
    assert len(urls_called) >= 1, "At least one call must have exercised HTTP client"
    for u in urls_called:
        assert ":8081" not in u


def test_llm_outbound_paths_yaml_bridge_retirement():
    """Verify spec/llm_outbound_paths.yaml formally deprecates llm-bridge."""
    yaml_path = os.path.join(os.path.dirname(__file__), "..", "..", "spec", "llm_outbound_paths.yaml")
    assert os.path.exists(yaml_path), f"Spec file missing: {yaml_path}"

    with open(yaml_path, "r", encoding="utf-8") as f:
        spec = yaml.safe_load(f)

    paths = spec.get("paths", [])
    bridge_entry = next((p for p in paths if p.get("path") == "mini-services/llm-bridge/index.ts"), None)
    assert bridge_entry is not None, "mini-services/llm-bridge/index.ts must be registered in outbound spec"
    assert bridge_entry.get("status") == "DEPRECATED_RETIRED", (
        f"Expected status DEPRECATED_RETIRED, got {bridge_entry.get('status')}"
    )

    # Verify no other path targets 8081
    for p in paths:
        path_str = str(p.get("path", ""))
        assert "8081" not in path_str, f"Found port 8081 in path: {path_str}"


# ===========================================================================
# Suite 3: Concurrency Stress-Testing & Provider Failover Gaps
# ===========================================================================

@pytest.mark.asyncio
async def test_concurrent_async_routing_under_gateway_failures():
    """Stress-test 50 concurrent async routing requests under mixed provider failure conditions."""
    call_counter = 0
    lock = asyncio.Lock()

    class FlakyConcurrentGateway:
        async def chat(self, *args, **kwargs):
            nonlocal call_counter
            async with lock:
                call_counter += 1
                c = call_counter

            # Simulate variable network latency
            await asyncio.sleep(0.01 * (c % 5))

            # Simulate varied failure modes
            if c % 4 == 0:
                raise httpx.ConnectTimeout("Simulated network timeout")
            elif c % 4 == 1:
                raise httpx.HTTPStatusError("500 Internal Error", request=httpx.Request("POST", "http://test"), response=httpx.Response(500))
            elif c % 4 == 2:
                return "unparseable response", "flaky_provider"
            else:
                return "LOOKUP", "flaky_provider"

    gateway = FlakyConcurrentGateway()
    questions = [f"Ambiguous query #{i} on computability theory" for i in range(50)]

    results = await asyncio.gather(*(route_question_async(q, gateway=gateway) for q in questions))

    assert len(results) == 50
    for r in results:
        assert isinstance(r, RouteDecision)
        if r.via == "l2-failsafe":
            assert r.bypass_verdict_pass is False, "Every failsafe decision MUST have bypass_verdict_pass=False"
        elif r.intent == LOOKUP:
            assert r.bypass_verdict_pass is False


@pytest.mark.asyncio
async def test_concurrency_stats_counters_integrity():
    """Verify RouteStats integrity when 100 concurrent requests execute."""
    before_stats = route_stats_snapshot()

    class CountingGateway:
        async def chat(self, *args, **kwargs):
            await asyncio.sleep(0.005)
            return "LOOKUP", "mock_counter"

    gateway = CountingGateway()
    questions = [f"arbitrary nonsensical phrase num {i}" for i in range(100)]

    results = await asyncio.gather(*(route_question_async(q, gateway=gateway) for q in questions))
    assert len(results) == 100

    after_stats = route_stats_snapshot()
    delta_classifier_calls = after_stats["classifier_llm_calls"] - before_stats["classifier_llm_calls"]
    assert delta_classifier_calls == 100, f"Expected 100 classifier calls recorded, got {delta_classifier_calls}"


def test_exploit_gap_task_route_omits_extra_providers_in_gateway():
    """EMPIRICAL PROOF OF GAP:
    In scp/llm_gateway/client.py, _parse_extra_providers registers extra providers
    (including gemini_compat and openai_compat) only for tasks:
      ('autofix', 'why', 'learning', 'fast_learning', 'judge', 'chat', 'default').
    It OMITS 'route'!
    Furthermore, in _provider_chain(task):
      extra = self._extra_providers.get(task, [])
    which does NOT fall back to self._extra_providers.get('default', []).

    As a result:
    When question_router calls gateway.chat(..., task='route'),
    neither gemini_compat nor openai_compat is present in the failover chain.
    If OpenRouter is unavailable or disabled, task='route' has an EMPTY chain ([]),
    making provider failover impossible for question routing!
    """
    saved_gemini = os.environ.get("GEMINI_API_KEY")
    saved_openai = os.environ.get("OPENAI_API_KEY")
    try:
        os.environ["GEMINI_API_KEY"] = "test-gemini-key"
        os.environ["OPENAI_API_KEY"] = "test-openai-key"

        gw = LLMGateway()

        # Task 'default' properly registers openai_compat and gemini_compat
        default_chain = [p.PROVIDER_NAME for p in gw._provider_chain("default")]
        assert "gemini_compat" in default_chain, "Default chain must include gemini_compat"
        assert "openai_compat" in default_chain, "Default chain must include openai_compat"

        # Task 'route' OMITS openai_compat and gemini_compat!
        route_chain = [p.PROVIDER_NAME for p in gw._provider_chain("route")]

        # PROOF OF GAP: route_chain has NO gemini_compat and NO openai_compat!
        has_gemini_in_route = "gemini_compat" in route_chain
        has_openai_in_route = "openai_compat" in route_chain

        # Documenting the architectural gap resolution:
        assert has_gemini_in_route, "RESOLVED: gemini_compat must be present in route chain"
        assert has_openai_in_route, "RESOLVED: openai_compat must be present in route chain"

    finally:
        if saved_gemini is not None:
            os.environ["GEMINI_API_KEY"] = saved_gemini
        else:
            os.environ.pop("GEMINI_API_KEY", None)

        if saved_openai is not None:
            os.environ["OPENAI_API_KEY"] = saved_openai
        else:
            os.environ.pop("OPENAI_API_KEY", None)
