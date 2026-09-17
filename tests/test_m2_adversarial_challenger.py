"""
tests/test_m2_adversarial_challenger.py
=======================================
Adversarial Empirical Stress Suite for M2 Architecture Backlog:
1. Adversarial SSE Token Streaming:
   - Malformed body (non-JSON, non-dict, missing messages, non-dict messages)
   - Missing model, null model, invalid model type
   - Invalid temperature (strings, out-of-range negative/extreme values)
   - Stream aborted midway (client disconnect simulation)
   - Empty stream, empty deltas, immediate [DONE]
   - Multi-token chunk bursts
2. Adversarial Structlog:
   - Complex non-serializable objects (sets, circular dicts, exceptions, locks)
   - Verify log formatting does not crash stdlib logging handlers
"""
from __future__ import annotations

import asyncio
import http.server
import json
import logging
import threading
import time
from typing import AsyncIterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from scp.api.routes import openai_compat
from scp.api.routes.openai_compat import router as openai_router
from scp.core.logging_config import configure_logging, get_logger
from scp.security.jwt_guard import get_current_user


def _inject_verified_canonical(monkeypatch, answer: str = "Verified canonical answer") -> None:
    """Drive the /v1/chat/completions boundary with a canonical, non-held result.

    Why the stream tests must do this: after commit 5e54de3 the OpenAI-compat
    stream path is no longer a thin proxy over the LLM gateway provider. Every
    valid request is rerouted through the canonical run_rag/kernel boundary
    (`openai_compat._run_canonical_ask`), which is deliberately fail-closed: a
    missing judge (`judge_ready` False) or a policy/UNKNOWN/kernel-gate hold
    returns 503 before any SSE is built. The bare TestClient in this suite never
    runs the api_server lifespan, so the provider-chain monkeypatch alone can no
    longer produce a streaming 200 -- reality is 503.

    These adversarial tests target openai_compat's OWN boundary contract (model
    defaulting, temperature tolerance, canonical-answer -> SSE translation,
    client-abort lifecycle), not the judge kernel, which is out of scope. So the
    out-of-scope canonical boundary is stubbed at the same seam the T02 contract
    suite uses (`_inject_canonical`); the assertions below are UNCHANGED. A
    non-withheld verdict (PASS / UPHOLD) is what the current contract requires
    for the route to emit a 200 text/event-stream.
    """
    async def _injected(*_args, **_kwargs):
        return {
            "verdict": "PASS",
            "governance_decision": "UPHOLD",
            "final_answer": answer,
            "confidence": 0.9,
            "falsification_status": "PASSED",
            "run_status": "SUCCESS",
            "ledger_status": "OK",
        }

    monkeypatch.setattr(openai_compat, "_run_canonical_ask", _injected)


# =============================================================================
# Physical HTTP loopback server simulating adversarial SSE behavior
# =============================================================================
class _AdversarialSSEHandler(http.server.BaseHTTPRequestHandler):
    """Physical HTTP loopback server simulating adversarial SSE behavior."""
    scenario = "normal"

    def do_POST(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        if self.scenario == "empty_stream":
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        elif self.scenario == "empty_deltas":
            self.wfile.write(b'data: {"choices": [{"delta": {"role": "assistant"}}]}\n\n')
            self.wfile.write(b'data: {"choices": [{"delta": {"content": ""}}]}\n\n')
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        elif self.scenario == "multi_token_burst":
            chunks = [
                'data: {"choices": [{"delta": {"content": "Token1 Token2 Token3"}}]}\n\n',
                'data: {"choices": [{"delta": {"content": " Token4 Token5"}}]}\n\n',
                'data: {"choices": [{"delta": {"content": " Token6 Token7 Token8 Token9 Token10"}}]}\n\n',
                'data: [DONE]\n\n',
            ]
            for c in chunks:
                self.wfile.write(c.encode("utf-8"))
                self.wfile.flush()
        else:
            chunks = [
                'data: {"choices": [{"delta": {"content": "Hello"}}]}\n\n',
                'data: {"choices": [{"delta": {"content": " world"}}]}\n\n',
                'data: [DONE]\n\n',
            ]
            for c in chunks:
                self.wfile.write(c.encode("utf-8"))
                self.wfile.flush()

    def log_message(self, *args):
        pass


@pytest.fixture
def loopback_server():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _AdversarialSSEHandler)
    port = server.server_port
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    yield server, port
    server.shutdown()


# Setup test app
@pytest.fixture
def api_client(loopback_server, monkeypatch):
    server, port = loopback_server
    _AdversarialSSEHandler.scenario = "normal"
    monkeypatch.setenv("TEST_SSE_KEY", "valid-key")
    monkeypatch.setenv("TEST_SSE_BASE_URL", f"http://127.0.0.1:{port}")
    monkeypatch.setenv("TEST_SSE_MODEL", "test-model")

    from scp.llm_gateway.client import get_gateway, EnvCompatProvider
    gw = get_gateway()
    test_provider = EnvCompatProvider(
        name="loopback_api_client",
        task="chat",
        key_env="TEST_SSE_KEY",
        base_url_env="TEST_SSE_BASE_URL",
        model_env="TEST_SSE_MODEL",
        default_model="test-model",
        default_base_url=f"http://127.0.0.1:{port}",
    )
    monkeypatch.setattr(gw, "_provider_chain", lambda task: [test_provider])

    app = FastAPI()
    app.include_router(openai_router)
    app.dependency_overrides[get_current_user] = lambda: "test_challenger"
    return TestClient(app)



# =============================================================================
# 1. Adversarial Malformed Body & Input Boundary Tests
# =============================================================================
def test_streaming_malformed_json_syntax(api_client):
    """Verify non-JSON syntax returns 400 with OpenAI-compatible error format."""
    resp = api_client.post(
        "/v1/chat/completions",
        content=b"not a valid json {",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 400
    data = resp.json()
    assert "error" in data
    assert data["error"]["type"] == "invalid_request"


def test_streaming_json_array_top_level(api_client):
    """Verify top-level JSON array is rejected fail-closed without crashing (500)."""
    resp = api_client.post(
        "/v1/chat/completions",
        content=b"[{\"role\": \"user\", \"content\": \"hi\"}]",
        headers={"Content-Type": "application/json"},
    )
    # If body is a list, body.get() would crash with AttributeError if not validated
    assert resp.status_code in (400, 422)
    assert resp.status_code != 500


@pytest.mark.parametrize(
    "payload_bytes,description",
    [
        (b"[]", "empty JSON array"),
        (b"[[{\"role\": \"user\", \"content\": \"hi\"}]]", "nested JSON array"),
        (b'"just a string"', "primitive string"),
        (b"12345", "primitive integer"),
        (b"3.1415", "primitive float"),
        (b"true", "primitive boolean true"),
        (b"false", "primitive boolean false"),
        (b"null", "primitive null"),
    ],
)
def test_adversarial_fuzz_extreme_inputs_chat_completions(api_client, payload_bytes, description):
    """Verify extreme malformed root payloads yield HTTP 400 with OpenAI error envelope, NEVER 500."""
    resp = api_client.post(
        "/v1/chat/completions",
        content=payload_bytes,
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 400, f"Payload {description} returned status {resp.status_code}, expected 400. Body: {resp.text}"
    assert resp.status_code != 500
    data = resp.json()
    assert "error" in data, f"Missing 'error' in response for {description}: {data}"
    assert data["error"]["type"] == "invalid_request", f"Wrong error type for {description}: {data}"
    assert data["error"]["message"] == "Invalid JSON body: expected root object", f"Wrong error message for {description}: {data}"


@pytest.mark.parametrize(
    "payload_bytes,description",
    [
        (b"[]", "empty JSON array"),
        (b"[[{\"role\": \"user\", \"content\": \"hi\"}]]", "nested JSON array"),
        (b'"just a string"', "primitive string"),
        (b"12345", "primitive integer"),
        (b"3.1415", "primitive float"),
        (b"true", "primitive boolean true"),
        (b"false", "primitive boolean false"),
        (b"null", "primitive null"),
    ],
)
def test_adversarial_fuzz_extreme_inputs_v1_completions(api_client, payload_bytes, description):
    """Verify /v1/completions with malformed payloads returns 404 (unmapped route) and NEVER crashes with 500."""
    resp = api_client.post(
        "/v1/completions",
        content=payload_bytes,
        headers={"Content-Type": "application/json"},
    )
    # /v1/completions is not a registered route in openai_compat router; must yield 404, never 500
    assert resp.status_code == 404, f"/v1/completions with {description} returned {resp.status_code}, expected 404."
    assert resp.status_code != 500


def test_adversarial_high_concurrency_rapid_burst_malformed(api_client):
    """High-concurrency rapid burst of 100 malformed requests to verify zero 500s and zero unhandled crashes."""
    import concurrent.futures
    import random

    payloads = [
        b"[]",
        b"[[{\"role\": \"user\", \"content\": \"hi\"}]]",
        b'"just a string"',
        b"12345",
        b"3.1415",
        b"true",
        b"false",
        b"null",
        b"not a valid json {",
        b"",
    ]

    def _send_probe(i: int):
        payload = random.choice(payloads)
        resp = api_client.post(
            "/v1/chat/completions",
            content=payload,
            headers={"Content-Type": "application/json"},
        )
        return resp.status_code, resp.json()

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(_send_probe, i) for i in range(100)]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]

    assert len(results) == 100
    for code, body in results:
        assert code == 400, f"Rapid burst yielded unexpected status code {code}: {body}"
        assert code != 500
        assert "error" in body
        assert body["error"]["type"] == "invalid_request"


def test_streaming_missing_messages(api_client):
    """Verify missing messages field returns 400."""
    resp = api_client.post(
        "/v1/chat/completions",
        json={"model": "gpt-4", "stream": True},
    )
    assert resp.status_code == 400
    assert "error" in resp.json()


def test_streaming_malformed_messages_structure(api_client):
    """Verify invalid messages types (str, list of non-dicts) return 400."""
    # messages is a string
    resp1 = api_client.post(
        "/v1/chat/completions",
        json={"messages": "not-a-list", "stream": True},
    )
    assert resp1.status_code == 400

    # messages contains non-dicts
    resp2 = api_client.post(
        "/v1/chat/completions",
        json={"messages": ["invalid", 123], "stream": True},
    )
    assert resp2.status_code == 400

    # messages contains empty content or no user message
    resp3 = api_client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "system", "content": "only system"}], "stream": True},
    )
    assert resp3.status_code == 400


def test_streaming_missing_or_null_model(api_client, monkeypatch):
    """Verify streaming works when model is missing or null (defaults to canonical model)."""
    _inject_verified_canonical(monkeypatch)
    resp_missing = api_client.post(
        "/v1/chat/completions",
        json={
            "messages": [{"role": "user", "content": "ping"}],
            "stream": True,
        },
    )
    assert resp_missing.status_code == 200
    assert "text/event-stream" in resp_missing.headers.get("content-type", "")

    resp_null = api_client.post(
        "/v1/chat/completions",
        json={
            "model": None,
            "messages": [{"role": "user", "content": "ping"}],
            "stream": True,
        },
    )
    assert resp_null.status_code == 200
    assert "text/event-stream" in resp_null.headers.get("content-type", "")


def test_streaming_invalid_temperature(api_client, monkeypatch):
    """Verify behavior when temperature is invalid (strings, negative, extreme values)."""
    _inject_verified_canonical(monkeypatch)
    # Non-numeric string temperature
    resp_str = api_client.post(
        "/v1/chat/completions",
        json={
            "messages": [{"role": "user", "content": "ping"}],
            "temperature": "super_hot",
            "stream": True,
        },
    )
    # Must either reject (400/422) or ignore gracefully without 500
    assert resp_str.status_code in (200, 400, 422)
    assert resp_str.status_code != 500

    # Negative temperature
    resp_neg = api_client.post(
        "/v1/chat/completions",
        json={
            "messages": [{"role": "user", "content": "ping"}],
            "temperature": -2.5,
            "stream": True,
        },
    )
    assert resp_neg.status_code in (200, 400, 422)
    assert resp_neg.status_code != 500


# =============================================================================
# 2. Adversarial Empty Stream & Multi-Token Burst Tests (Physical Network Loopback)
# =============================================================================


@pytest.mark.asyncio
async def test_empty_stream_graceful_handling(loopback_server, monkeypatch):
    """Verify that an empty upstream stream (immediate [DONE]) is handled gracefully."""
    server, port = loopback_server
    _AdversarialSSEHandler.scenario = "empty_stream"

    monkeypatch.setenv("TEST_SSE_KEY", "valid-key")
    monkeypatch.setenv("TEST_SSE_BASE_URL", f"http://127.0.0.1:{port}")
    monkeypatch.setenv("TEST_SSE_MODEL", "test-model")

    from scp.llm_gateway.client import EnvCompatProvider
    provider = EnvCompatProvider(
        name="loopback_empty_sse",
        task="chat",
        key_env="TEST_SSE_KEY",
        base_url_env="TEST_SSE_BASE_URL",
        model_env="TEST_SSE_MODEL",
        default_model="test-model",
        default_base_url=f"http://127.0.0.1:{port}",
    )

    tokens = []
    async for token in provider.chat_stream("test empty"):
        tokens.append(token)

    # Empty stream should yield 0 tokens and terminate normally
    assert tokens == []


@pytest.mark.asyncio
async def test_empty_deltas_ignored(loopback_server, monkeypatch):
    """Verify deltas without content or empty string content do not yield empty tokens."""
    server, port = loopback_server
    _AdversarialSSEHandler.scenario = "empty_deltas"

    monkeypatch.setenv("TEST_SSE_KEY", "valid-key")
    monkeypatch.setenv("TEST_SSE_BASE_URL", f"http://127.0.0.1:{port}")
    monkeypatch.setenv("TEST_SSE_MODEL", "test-model")

    from scp.llm_gateway.client import EnvCompatProvider
    provider = EnvCompatProvider(
        name="loopback_empty_deltas",
        task="chat",
        key_env="TEST_SSE_KEY",
        base_url_env="TEST_SSE_BASE_URL",
        model_env="TEST_SSE_MODEL",
        default_model="test-model",
        default_base_url=f"http://127.0.0.1:{port}",
    )

    tokens = []
    async for token in provider.chat_stream("test empty deltas"):
        tokens.append(token)

    assert tokens == []


@pytest.mark.asyncio
async def test_multi_token_burst_intact(loopback_server, monkeypatch):
    """Verify multi-token bursts in chunks are received intact without corruption."""
    server, port = loopback_server
    _AdversarialSSEHandler.scenario = "multi_token_burst"

    monkeypatch.setenv("TEST_SSE_KEY", "valid-key")
    monkeypatch.setenv("TEST_SSE_BASE_URL", f"http://127.0.0.1:{port}")
    monkeypatch.setenv("TEST_SSE_MODEL", "test-model")

    from scp.llm_gateway.client import EnvCompatProvider
    provider = EnvCompatProvider(
        name="loopback_burst_sse",
        task="chat",
        key_env="TEST_SSE_KEY",
        base_url_env="TEST_SSE_BASE_URL",
        model_env="TEST_SSE_MODEL",
        default_model="test-model",
        default_base_url=f"http://127.0.0.1:{port}",
    )

    tokens = []
    async for token in provider.chat_stream("test burst"):
        tokens.append(token)

    full_text = "".join(tokens)
    assert full_text == "Token1 Token2 Token3 Token4 Token5 Token6 Token7 Token8 Token9 Token10"


# =============================================================================
# 3. Stream Aborted Midway (Client Disconnect Simulation)
# =============================================================================
def test_stream_aborted_midway(api_client, loopback_server, monkeypatch):
    """Simulate client disconnecting after reading only the first chunk."""
    server, port = loopback_server
    _AdversarialSSEHandler.scenario = "multi_token_burst"
    monkeypatch.setenv("TEST_SSE_KEY", "valid-key")
    monkeypatch.setenv("TEST_SSE_BASE_URL", f"http://127.0.0.1:{port}")
    monkeypatch.setenv("TEST_SSE_MODEL", "test-model")

    from scp.llm_gateway.client import get_gateway, EnvCompatProvider
    gw = get_gateway()
    test_provider = EnvCompatProvider(
        name="loopback_abort",
        task="chat",
        key_env="TEST_SSE_KEY",
        base_url_env="TEST_SSE_BASE_URL",
        model_env="TEST_SSE_MODEL",
        default_model="test-model",
        default_base_url=f"http://127.0.0.1:{port}",
    )
    monkeypatch.setattr(gw, "_provider_chain", lambda task: [test_provider])

    # Contract note: after 5e54de3 the route no longer proxies provider deltas;
    # the SSE body is the canonical answer translated by openai_compat itself.
    # Seed a multi-word verified canonical answer so the first SSE frame is a
    # content delta, preserving this test's abort-lifecycle intent.
    _inject_verified_canonical(
        monkeypatch, answer="Token1 Token2 Token3 Token4 Token5 Token6 Token7"
    )

    with api_client.stream(
        "POST",
        "/v1/chat/completions",
        json={
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "Tell me a very long story"}],
            "stream": True,
        },
    ) as response:
        assert response.status_code == 200
        # Read only the first line/chunk and immediately close the context (aborts stream)
        first_line = next(response.iter_lines())
        assert "data:" in first_line

    # Context closed: client disconnected. Verify subsequent requests still work cleanly
    subsequent_resp = api_client.post(
        "/v1/chat/completions",
        json={
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "ping after abort"}],
            "stream": True,
        },
    )
    assert subsequent_resp.status_code == 200



# =============================================================================
# 4. Adversarial Structlog Serialization & Resilience
# =============================================================================
def test_structlog_complex_non_serializable_objects():
    """Verify Structlog with JSONRenderer handles sets, exceptions, and non-serializables."""
    configure_logging(log_level="DEBUG", json_output=True)
    logger = get_logger("scp.test_adversarial")

    # 1. Set object
    logger.info("test_set", my_set={1, 2, 3, "apple"})

    # 2. Exception object as parameter
    logger.info("test_exception_obj", error=RuntimeError("something went wrong"))

    # 3. Custom unhashable / non-primitive class
    class CustomState:
        def __repr__(self):
            return "<CustomState active=True>"

    logger.info("test_custom_repr", state=CustomState())

    # 4. Exception formatting
    try:
        raise ZeroDivisionError("division by zero test")
    except ZeroDivisionError:
        logger.exception("caught_exception")


def test_structlog_circular_dict_resilience():
    """Verify that circular dicts do not crash application code or unhandled handler errors."""
    configure_logging(log_level="DEBUG", json_output=True)
    logger = get_logger("scp.test_circular")

    circular = {"name": "root"}
    circular["self"] = circular

    # Standard library logging handler catches formatting errors inside handleError
    # Verify calling logger.info does not crash the caller
    logger.info("circular_event", payload=circular)

    # Console renderer test
    configure_logging(log_level="INFO", json_output=False)
    logger.info("circular_console", payload=circular)


def test_stdlib_interop_does_not_crash():
    """Verify stdlib loggers formatting through structlog ProcessorFormatter do not crash."""
    configure_logging(log_level="DEBUG", json_output=True)
    std_logger = logging.getLogger("scp.stdlib_adversarial")

    # Stdlib with extra dictionary containing non-primitives
    std_logger.info("stdlib message with set", extra={"items": {"a", "b"}})

    # Stdlib with exception
    try:
        raise KeyError("missing_key_test")
    except KeyError:
        std_logger.exception("stdlib exception caught")
