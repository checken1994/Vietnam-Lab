"""T05 uses real pricing/egress guards, isolated state and synthetic transport.

Network-backed account/inference evidence is outside this contract test profile.
Any accidental HTTP request is a failure even if product code catches it.
"""

from __future__ import annotations

import itertools
import os
from pathlib import Path

import httpx
import pytest

from scp.llm_gateway import client, free_catalog

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def isolated_gateway_state(tmp_path, monkeypatch):
    data_root = tmp_path / "gateway-state"
    values = {
        "SCP_MODE": "test",
        "SCP_DATA_DIR": str(data_root),
        "SCP_LLM_COST_MODE": "free_only",
        "SCP_ALLOW_PAID_FALLBACK": "0",
        "SCP_MAX_LLM_COST_USD": "0",
        "SCP_FREE_REQUIRE_PRICE_PROOF": "1",
        "SCP_FREE_FAIL_IF_PRICE_UNKNOWN": "1",
        "SCP_EGRESS_MODE": "allowlist",
        "SCP_LLM_EGRESS_ALLOWLIST": "openrouter.ai",
        "SCP_LLM_FALLBACK_PROVIDERS": "",
        "SCP_LLM_PROVIDER_MODE": "openrouter",
        "OPENROUTER_MODEL": "unverified-primary",
        "OPENROUTER_MODEL_CHAT": "unverified-chat",
        "OPENROUTER_BASE_URL": "https://openrouter.ai/api/v1",
        "SCP_BUDGET_ROUTING": "0",
    }
    for name in list(os.environ):
        if name.endswith(("_API_KEY", "_API_KEY_2", "_API_KEY_3")):
            monkeypatch.delenv(name)
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    
    monkeypatch.setattr(free_catalog, "_FOUNDATION", data_root)
    monkeypatch.setattr(client.OpenRouterProvider, "_API_KEYS", ["test-key"])
    monkeypatch.setattr(
        client.OpenRouterProvider, "_key_cycle", itertools.cycle(["test-key"])
    )
    monkeypatch.setattr(client.OpenRouterProvider, "_dynamic_models_loaded", True)
    unexpected_http = []

    # [ARC-01-FOLLOWUP 2026-10-28] Guard chặn NHẦM cả TestClient in-process:
    # starlette TestClient kế thừa httpx.Client và mọi client.get()/post() đi
    # qua Client.send ⇒ test endpoint hợp lệ (vd test_health_endpoint_transparency,
    # ba3c37c5) bị cấm oan dù KHÔNG chạm mạng. Phân loại theo transport: chỉ
    # transport MẠNG THẬT (httpx.HTTPTransport) bị cấm; transport in-process
    # (TestClient transport tùy biến, MockTransport mà test chủ động inject)
    # được đi qua send gốc. Strictness GIỮ NGUYÊN với mục tiêu gốc — mọi nỗ
    # lực ra mạng thật vẫn bị bắt và vẫn assert ở teardown.
    _real_client_send = httpx.Client.send
    _real_async_send = httpx.AsyncClient.send

    def forbidden_sync(self_client, request, **_kwargs):
        if isinstance(getattr(self_client, "_transport", None), httpx.HTTPTransport):
            unexpected_http.append("sync")
            raise AssertionError("T05 must inject a transport before any network request")
        return _real_client_send(self_client, request, **_kwargs)

    async def forbidden_async(self_client, request, **_kwargs):
        # [ARC-01-FOLLOWUP 2026-10-28] httpx.AsyncHTTPTransport KHÔNG kế thừa httpx.HTTPTransport (MRO tách biệt) nên isinstance HTTPTransport luôn False với async client mạng thật = guard chết; pin: test_t05_guard_blocks_real_network_sync_and_async.
        if isinstance(getattr(self_client, "_transport", None), httpx.AsyncHTTPTransport):
            unexpected_http.append("async")
            raise AssertionError("T05 must inject a transport before any network request")
        return await _real_async_send(self_client, request, **_kwargs)

    monkeypatch.setattr(httpx.Client, "send", forbidden_sync)
    monkeypatch.setattr(httpx.AsyncClient, "send", forbidden_async)
    yield
    assert unexpected_http == [], "Product swallowed an accidental network attempt"

@pytest.fixture
def pricing_runtime(tmp_path, isolated_gateway_state):
    def seed(*args, **kwargs):
        pass
    yield seed
