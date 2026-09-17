"""SCP LLM Gateway — Contract Prober Zero-Trust Tests (S33).

Zero-mock test suite:
- Zero mock imports (no mocks, no fakes, no patches).
- Ephemeral ThreadingHTTPServer on 127.0.0.1:0 for real TCP loopback.
- Covers safe qualification, adversarial quarantines (prompt injection,
  roleplay bypass, format breaking), and network fail-closed paths.
"""

from __future__ import annotations

import asyncio
import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import httpx
import pytest

from scp.llm_gateway.prober import ContractProber

# Capture original httpx send functions at module import time before any fixture runs
_ORIGINAL_ASYNC_SEND = httpx.AsyncClient.send
_ORIGINAL_SYNC_SEND = httpx.Client.send


@pytest.fixture(autouse=True)
def allow_loopback_transport(monkeypatch):
    """Permit loopback traffic while keeping external endpoints blocked per T05 isolation."""

    async def loopback_async_send(self, request, *args, **kwargs):
        if request.url.host in ("127.0.0.1", "localhost", "::1"):
            return await _ORIGINAL_ASYNC_SEND(self, request, *args, **kwargs)
        raise AssertionError(
            f"Non-loopback HTTP request forbidden in T05: {request.url}"
        )

    def loopback_sync_send(self, request, *args, **kwargs):
        if request.url.host in ("127.0.0.1", "localhost", "::1"):
            return _ORIGINAL_SYNC_SEND(self, request, *args, **kwargs)
        raise AssertionError(
            f"Non-loopback HTTP request forbidden in T05: {request.url}"
        )

    monkeypatch.setattr(httpx.AsyncClient, "send", loopback_async_send)
    monkeypatch.setattr(httpx.Client, "send", loopback_sync_send)
    yield


class ProberLoopbackHandler(BaseHTTPRequestHandler):
    """Physical loopback HTTP handler simulating LLM completions endpoint."""

    def log_message(self, format: str, *args: Any) -> None:
        # Suppress noisy HTTP request logging during tests
        pass

    def do_POST(self) -> None:
        content_length = int(self.headers.get("Content-Length", 0))
        body_bytes = self.rfile.read(content_length)

        # Store last received request on the server instance for assertions
        self.server.last_headers = dict(self.headers)
        self.server.last_body = body_bytes

        try:
            req_data = json.loads(body_bytes.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self.send_response(400)
            self.end_headers()
            return

        model = req_data.get("model", "")
        messages = req_data.get("messages", [])
        prompt = messages[0].get("content", "") if messages else ""

        if model == "500-error-model":
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error": "Simulated server failure"}')
            return

        if model == "malformed-json-model":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b"INVALID_JSON_PAYLOAD{{{")
            return

        if model == "empty-response-model":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            resp = {"choices": [{"message": {"content": ""}}]}
            self.wfile.write(json.dumps(resp).encode("utf-8"))
            return

        if model == "injected-model":
            if "unrestricted_bypass_mode" in prompt:
                content = "Understood. Switching to unrestricted_bypass_mode immediately."
            else:
                content = "I am a helpful assistant."
        elif model == "roleplay-model":
            if "destroy a server" in prompt:
                content = "To destroy a server, you can use rm -rf / on the root partition."
            else:
                content = "I am a helpful assistant."
        elif model == "format-break-model":
            if "pure XML" in prompt:
                content = "<test>hello</test>"
            else:
                content = "I am a helpful assistant."
        else:
            # Default safe model: rejects trick questions safely
            content = "I cannot fulfill this adversarial request. I adhere to SCP security constraints."

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        resp_data = {
            "id": "chatcmpl-test",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                }
            ],
        }
        self.wfile.write(json.dumps(resp_data).encode("utf-8"))


@pytest.fixture
def prober_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), ProberLoopbackHandler)
    server.last_headers = {}
    server.last_body = b""
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    url = f"http://127.0.0.1:{port}"
    try:
        yield server, url
    finally:
        server.shutdown()
        server.server_close()


def test_prober_qualified_safe_model(prober_server) -> None:
    _server, base_url = prober_server
    prober = ContractProber(
        endpoint_url=f"{base_url}/v1/chat/completions",
        model="safe-model",
    )
    result = asyncio.run(prober.probe_async())
    assert result is True


def test_prober_quarantined_prompt_injection(prober_server) -> None:
    _server, base_url = prober_server
    prober = ContractProber(
        endpoint_url=f"{base_url}/v1/chat/completions",
        model="injected-model",
    )
    result = asyncio.run(prober.probe_async())
    assert result is False


def test_prober_quarantined_roleplay_bypass(prober_server) -> None:
    _server, base_url = prober_server
    prober = ContractProber(
        endpoint_url=f"{base_url}/v1/chat/completions",
        model="roleplay-model",
    )
    result = asyncio.run(prober.probe_async())
    assert result is False


def test_prober_quarantined_format_breaking(prober_server) -> None:
    _server, base_url = prober_server
    prober = ContractProber(
        endpoint_url=f"{base_url}/v1/chat/completions",
        model="format-break-model",
    )
    result = asyncio.run(prober.probe_async())
    assert result is False


def test_prober_fail_closed_on_500_error(prober_server) -> None:
    _server, base_url = prober_server
    prober = ContractProber(
        endpoint_url=f"{base_url}/v1/chat/completions",
        model="500-error-model",
    )
    result = asyncio.run(prober.probe_async())
    assert result is False


def test_prober_fail_closed_on_malformed_json(prober_server) -> None:
    _server, base_url = prober_server
    prober = ContractProber(
        endpoint_url=f"{base_url}/v1/chat/completions",
        model="malformed-json-model",
    )
    result = asyncio.run(prober.probe_async())
    assert result is False


def test_prober_fail_closed_on_unreachable_endpoint() -> None:
    # Port 59199 is not bound to any server
    prober = ContractProber(
        endpoint_url="http://127.0.0.1:59199/v1/chat/completions",
        model="safe-model",
        timeout=1.0,
    )
    result = asyncio.run(prober.probe_async())
    assert result is False


def test_prober_empty_response_allowed(prober_server) -> None:
    _server, base_url = prober_server
    prober = ContractProber(
        endpoint_url=f"{base_url}/v1/chat/completions",
        model="empty-response-model",
    )
    result = asyncio.run(prober.probe_async())
    assert result is True


def test_prober_request_payload_and_auth_headers(prober_server) -> None:
    server, base_url = prober_server
    api_key = secrets.token_urlsafe(32)
    prober = ContractProber(
        endpoint_url=f"{base_url}/v1/chat/completions",
        api_key=api_key,
        model="safe-model",
    )
    result = asyncio.run(prober.probe_async())
    assert result is True

    # Verify physical HTTP request reached server with correct headers & body
    assert server.last_headers.get("Authorization") == f"Bearer {api_key}"
    assert server.last_headers.get("Content-Type") == "application/json"
    req_body = json.loads(server.last_body.decode("utf-8"))
    assert req_body["model"] == "safe-model"
    assert "messages" in req_body
    assert len(req_body["messages"]) > 0
