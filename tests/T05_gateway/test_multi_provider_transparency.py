"""
Tests for Multi-Vendor LLM Gateway Transparency and Provider Neutrality.

Verifies:
1. Operational mode:
   - "local_verified" when no provider API keys are configured (Local Verified Mode).
   - "connected_llm" when at least one provider is configured (Connected LLM Mode).
2. First-class status of individual AI providers without OpenRouter:
   - Google Gemini (GEMINI_API_KEY)
   - DeepSeek (DEEPSEEK_API_KEY)
   - Groq (GROQ_API_KEY)
   - Mistral (MISTRAL_API_KEY)
   - OpenAI (OPENAI_API_KEY)
3. Multi-vendor semantic consensus / independent lineage across distinct providers.
4. Transparency in GET /health (operational_mode and active_llm_providers).
"""
from __future__ import annotations

import asyncio
import itertools

import pytest
from starlette.testclient import TestClient

from scp.api_server import app
from scp.llm_gateway.client import LLMGateway, OpenRouterProvider
import scp.llm_gateway.providers as _gw_providers


@pytest.fixture(autouse=True)
def clean_llm_env(monkeypatch):
    """Ensure a clean slate for LLM provider environment variables."""
    for key in (
        "OPENROUTER_API_KEY",
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "OPENAI_MODEL",
        "GEMINI_API_KEY",
        "GEMINI_BASE_URL",
        "GEMINI_MODEL",
        "DEEPSEEK_API_KEY",
        "DEEPSEEK_BASE_URL",
        "DEEPSEEK_MODEL",
        "GROQ_API_KEY",
        "GROQ_BASE_URL",
        "GROQ_MODEL",
        "MISTRAL_API_KEY",
        "MISTRAL_BASE_URL",
        "MISTRAL_MODEL",
        "SCP_LLM_FALLBACK_PROVIDERS",
    ):
        monkeypatch.delenv(key, raising=False)

    # Disable OpenRouter keys and prevent _init_keys from reloading
    monkeypatch.setattr(OpenRouterProvider, "_API_KEYS", [])
    monkeypatch.setattr(OpenRouterProvider, "_key_cycle", None)
    monkeypatch.setattr(OpenRouterProvider, "_init_keys", lambda *args: None)
    monkeypatch.setattr(_gw_providers.OpenRouterProvider, "_API_KEYS", [])
    monkeypatch.setattr(_gw_providers.OpenRouterProvider, "_key_cycle", None)
    monkeypatch.setattr(_gw_providers.OpenRouterProvider, "_init_keys", lambda *args: None)


def test_operational_mode_local_verified_when_no_keys(monkeypatch):
    """When no LLM keys are provided, system must be in Local Verified Mode."""
    gw = LLMGateway()
    assert gw.has_active_providers() is False
    assert gw.operational_mode() == "local_verified"
    assert gw.get_active_provider_names() == []
    assert gw._provider_chain("chat") == []

    # Chat call returns (None, "none") without exceptions
    ans, prov = asyncio.run(gw.chat("What is 2+2?", task="chat"))
    assert ans is None
    assert prov == "none"


def test_first_class_gemini_without_openrouter(monkeypatch):
    """Google Gemini operates directly as primary provider when GEMINI_API_KEY is set."""
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaSyTestGeminiKey123")
    gw = LLMGateway()

    assert gw.has_active_providers() is True
    assert gw.operational_mode() == "connected_llm"
    assert "gemini_compat" in gw.get_active_provider_names()

    chain = gw._provider_chain("chat")
    names = [p.PROVIDER_NAME for p in chain if getattr(p, "enabled", False)]
    assert names == ["gemini_compat"]
    assert chain[0].base_url == "https://generativelanguage.googleapis.com/v1beta/openai"
    assert chain[0].model == "gemini-2.0-flash"


def test_first_class_deepseek_without_openrouter(monkeypatch):
    """DeepSeek operates directly as primary provider when DEEPSEEK_API_KEY is set."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-ds-test-key-456")
    gw = LLMGateway()

    assert gw.has_active_providers() is True
    assert gw.operational_mode() == "connected_llm"
    assert "deepseek" in gw.get_active_provider_names()

    chain = gw._provider_chain("chat")
    names = [p.PROVIDER_NAME for p in chain if getattr(p, "enabled", False)]
    assert names == ["deepseek"]
    assert chain[0].base_url == "https://api.deepseek.com/v1"
    assert chain[0].model == "deepseek-chat"


def test_first_class_groq_without_openrouter(monkeypatch):
    """Groq operates directly as primary provider when GROQ_API_KEY is set."""
    monkeypatch.setenv("GROQ_API_KEY", "gsk_testGroqKey789")
    gw = LLMGateway()

    assert gw.has_active_providers() is True
    assert gw.operational_mode() == "connected_llm"
    assert "groq" in gw.get_active_provider_names()

    chain = gw._provider_chain("chat")
    names = [p.PROVIDER_NAME for p in chain if getattr(p, "enabled", False)]
    assert names == ["groq"]
    assert chain[0].base_url == "https://api.groq.com/openai/v1"
    assert chain[0].model == "llama-3.3-70b-versatile"


def test_first_class_mistral_without_openrouter(monkeypatch):
    """Mistral operates directly as primary provider when MISTRAL_API_KEY is set."""
    monkeypatch.setenv("MISTRAL_API_KEY", "mis_testMistralKey321")
    gw = LLMGateway()

    assert gw.has_active_providers() is True
    assert gw.operational_mode() == "connected_llm"
    assert "mistral" in gw.get_active_provider_names()

    chain = gw._provider_chain("chat")
    names = [p.PROVIDER_NAME for p in chain if getattr(p, "enabled", False)]
    assert names == ["mistral"]
    assert chain[0].base_url == "https://api.mistral.ai/v1"
    assert chain[0].model == "mistral-small-latest"


def test_multi_vendor_independent_lineage(monkeypatch):
    """Multiple distinct providers produce independent lineage for consensus."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-deepseek-key")
    gw = LLMGateway()

    assert gw.has_active_providers() is True
    assert gw.enabled_provider_count() == 2
    active = set(gw.get_active_provider_names())
    assert {"gemini_compat", "deepseek"}.issubset(active)


def test_health_endpoint_transparency(monkeypatch):
    """GET /health must expose operational_mode and active_llm_providers.

    [BUYER-05 2026-10-30] Transparency này giờ dành cho operator (Bearer admin
    qua verify_admin canonical); caller không auth KHÔNG được thấy provider
    list (fingerprint buyer báo). Pin cũ gọi không auth — đổi sang Bearer
    admin + thêm assert vắng mặt ở bản unauth (siết, không nới).
    """
    admin_key = "buyer05-transparency-throwaway-admin-key-32b"
    monkeypatch.setenv("SCP_ADMIN_KEY", admin_key)
    monkeypatch.delenv("SCP_ADMIN_KEY_FILE", raising=False)
    client = TestClient(app)
    unauth = client.get("/health")
    assert unauth.status_code == 200
    assert "active_llm_providers" not in unauth.json()
    assert "operational_mode" not in unauth.json()
    resp = client.get("/health", headers={"Authorization": f"Bearer {admin_key}"})
    assert resp.status_code == 200
    data = resp.json()

    assert "operational_mode" in data
    assert data["operational_mode"] in ("local_verified", "connected_llm")
    assert "active_llm_providers" in data
    assert isinstance(data["active_llm_providers"], list)
