"""[C1 wire-or-kill 2026-10-30] Judge/default model lifecycle + truthful degraded state.

Root cause (E2E false-positive, reason A): GROQ_API_KEY present but the DEFAULT
model ('llama-3.3-70b-versatile') returned 404 => provider counted as 'enabled'
yet unusable => gateway returned (None, 'none') silently => judge could not run
=> benign questions withheld with no truthful label.

Contract pinned here (no network - httpx.MockTransport only):
  1. no built-in default model id is on the documented decommissioned list
     (every list entry carries a public https source URL + date);
  2. env var present-but-EMPTY (docker compose passthrough ``${X:-}``) is treated
     as unset - it must not blank the default model/base_url and silently
     disable the provider;
  3. a provider whose model 404s (model-not-found) is marked UNUSABLE: it is not
     counted by enabled_provider_count()/get_active_provider_names(), the
     operational mode becomes local_verified, and llm_status() says
     'unavailable' with the reason;
  4. the judge, when no provider is usable, reports the degraded reason in its
     escalated result instead of an anonymous 'unavailable';
  5. with a working stub provider the judge runs (PASS) and nothing is degraded.
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from scp.llm_gateway import client as gw_client
from scp.llm_gateway.client import (
    _BUILTIN_COMPAT_PROVIDERS,
    EnvCompatProvider,
    LLMGateway,
    OpenRouterProvider,
)
from scp.llm_gateway.model_lifecycle import DECOMMISSIONED_MODELS, known_decommissioned

_NEW_GROQ = "openai/gpt-oss-120b"
_OLD_GROQ = "llama-3.3-70b-versatile"


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    gw_client.reset_dead_model_cache()
    # no OpenRouter key: only the provider under test may be configured
    monkeypatch.setattr(OpenRouterProvider, "_API_KEYS", [])
    monkeypatch.setattr(OpenRouterProvider, "_key_cycle", None)
    monkeypatch.setattr(gw_client, "_llm_egress_allowed", lambda base_url: True)
    monkeypatch.setattr(gw_client, "enforce_egress_policy", lambda *a, **k: None)
    monkeypatch.setattr(gw_client, "_gateway", None, raising=False)
    for name in ("GROQ_MODEL", "GROQ_BASE_URL", "GEMINI_MODEL", "OPENROUTER_MODEL",
                 "OPENROUTER_MODEL_JUDGE_PRIMARY", "OPENROUTER_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    yield
    gw_client.reset_dead_model_cache()


def _stub_transport(dead_models, answer="PASS"):
    """Provider impersonation: 404 model-not-found for dead models, 200 else."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        model = json.loads(request.content)["model"]
        calls.append(model)
        if model in dead_models:
            return httpx.Response(404, json={"error": {"message": f"The model `{model}` does not exist", "code": "model_not_found"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": answer}}]})

    return httpx.MockTransport(handler), calls


def _wire_all(gw, transport):
    for providers in gw._extra_providers.values():
        for p in providers:
            p._client = httpx.AsyncClient(transport=transport)


# ---- 1. defaults are not decommissioned ---------------------------------

def test_builtin_default_models_are_not_decommissioned():
    for name, _k, _b, _m, default_model, _url in _BUILTIN_COMPAT_PROVIDERS:
        assert known_decommissioned(name, default_model) is None, (
            f"default model {default_model!r} of {name} is decommissioned"
        )


def test_openrouter_judge_primary_default_is_not_decommissioned():
    model = OpenRouterProvider(task="judge").model
    assert known_decommissioned("openrouter", model) is None, model


def test_decommissioned_list_is_sourced_and_nonempty():
    assert len(DECOMMISSIONED_MODELS) >= 5
    for entry in DECOMMISSIONED_MODELS:
        assert entry.source_url.startswith("https://"), entry
        assert len(entry.retired_on) == 10 and entry.retired_on[4] == "-", entry
        assert entry.replacement and entry.replacement != entry.model, entry
        # the documented replacement itself must not be on the list
        assert known_decommissioned(entry.provider, entry.replacement) is None, entry
    # the exact id that 404'd in the E2E run stays recorded
    assert known_decommissioned("groq", _OLD_GROQ) is not None


def test_groq_default_is_the_supported_replacement(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    gw = LLMGateway()
    groq = next(p for p in gw._extra_providers["judge"] if p.PROVIDER_NAME == "groq")
    assert groq.model == _NEW_GROQ


# ---- 2. empty env (compose passthrough) == unset -------------------------

def test_empty_env_model_and_base_url_fall_back_to_defaults(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    monkeypatch.setenv("GROQ_MODEL", "")
    monkeypatch.setenv("GROQ_BASE_URL", "  ")
    p = EnvCompatProvider("groq", "judge", "GROQ_API_KEY", "GROQ_BASE_URL", "GROQ_MODEL",
                          default_model=_NEW_GROQ, default_base_url="https://api.groq.com/openai/v1")
    assert p.model == _NEW_GROQ
    assert p.base_url == "https://api.groq.com/openai/v1"
    assert p.enabled is True


def test_empty_openrouter_env_does_not_blank_model(monkeypatch):
    monkeypatch.setenv("OPENROUTER_MODEL", "")
    monkeypatch.setenv("OPENROUTER_BASE_URL", "")
    monkeypatch.setenv("OPENROUTER_MODEL_JUDGE_PRIMARY", "")
    monkeypatch.setenv("OPENROUTER_MODEL_CHAT", "")
    d = OpenRouterProvider(task="default")
    j = OpenRouterProvider(task="judge")
    c = OpenRouterProvider(task="chat")
    assert d.model and j.model and c.free_fallback
    assert d.base_url == "https://openrouter.ai/api/v1"


def test_task_model_env_is_read_dynamically_for_why_and_route(monkeypatch):
    """OPENROUTER_MODEL_WHY / _ROUTE HAVE a reader (dynamic name
    OPENROUTER_MODEL_{TASK}) - the earlier 'zero consumer' grep missed it."""
    monkeypatch.setenv("OPENROUTER_MODEL_WHY", "vendor/why-model")
    monkeypatch.setenv("OPENROUTER_MODEL_ROUTE", "vendor/route-model")
    assert OpenRouterProvider(task="why").free_fallback == "vendor/why-model"
    assert OpenRouterProvider(task="route").free_fallback == "vendor/route-model"


# ---- 3. 404 model => unusable, truthfully reported ------------------------

def test_provider_with_404_model_is_marked_unusable_and_reported(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    monkeypatch.setenv("GROQ_MODEL", _OLD_GROQ)  # owner env still points at the dead model
    gw = LLMGateway()
    transport, _calls = _stub_transport({_OLD_GROQ})
    _wire_all(gw, transport)

    # before any call the gateway cannot know: configured => counted, but warns
    assert gw.enabled_provider_count() == 1
    assert gw.llm_status()["state"] == "ok"
    assert gw.llm_status()["warnings"], "known-decommissioned config must warn up-front"

    answer, label = asyncio.run(gw.chat("q", task="judge"))
    assert answer is None and label == "none"

    assert gw.enabled_provider_count() == 0
    assert gw.get_active_provider_names() == []
    assert gw.has_active_providers() is False
    assert gw.operational_mode() == "local_verified"
    st = gw.llm_status()
    assert st["state"] == "unavailable"
    assert st["usable_providers"] == []
    assert st["judge"]["state"] == "unavailable"
    assert [u["provider"] for u in st["unusable_providers"]] == ["groq"]
    assert "404" in st["unusable_providers"][0]["reason"]
    assert st["unusable_providers"][0]["model"] == _OLD_GROQ


def test_unconfigured_gateway_state_is_unconfigured_not_unavailable():
    gw = LLMGateway()
    st = gw.llm_status()
    assert st["state"] == "unconfigured"
    assert st["judge"]["state"] == "unavailable"


def test_openrouter_unusable_only_when_all_candidates_dead():
    p = OpenRouterProvider(task="chat")
    p._API_KEYS = ["k"]
    assert p.enabled
    gw_client._mark_dead_model("openrouter", p.base_url, p.model)
    assert p.unusable_reason() == "", "free fallback still alive => usable"
    for m in (p.free_fallback, "openrouter/free"):
        gw_client._mark_dead_model("openrouter", p.base_url, m)
    assert "404" in p.unusable_reason()


# ---- 4/5. judge reports degraded reason; works with a live stub -----------

def test_judge_reports_unavailable_reason_when_no_usable_provider(monkeypatch):
    from scp.runtime import judge_llm
    from scp.runtime.judge import RealityJudge

    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    monkeypatch.setenv("GROQ_MODEL", _OLD_GROQ)
    gw = LLMGateway()
    transport, _ = _stub_transport({_OLD_GROQ})
    _wire_all(gw, transport)
    monkeypatch.setattr(gw_client, "_gateway", gw)

    assert asyncio.run(judge_llm._llm_judge_async("Thu do Viet Nam?", "Ha Noi", "")) is None
    reason = judge_llm.judge_unavailable_reason()
    assert reason.startswith("no_usable_llm_provider")

    res = RealityJudge._format_escalated(
        ai_answer="Ha Noi", failures=[], slm_responses_list=[], kb_refs=[],
        consensus_missing=False, answer_is_abstain=False, cycle_count=0,
        is_structurally_pass=True,
    )
    assert res["evidence"]["judge_unavailable_reason"] == reason
    assert "judge_llm_unavailable" in res["failures"]
    assert "no usable LLM provider" in res["reasoning"]


def test_judge_runs_with_working_stub_provider(monkeypatch):
    from scp.runtime import judge_llm

    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")  # default model = supported replacement
    gw = LLMGateway()
    transport, calls = _stub_transport({_OLD_GROQ}, answer="PASS")
    _wire_all(gw, transport)
    monkeypatch.setattr(gw_client, "_gateway", gw)

    assert asyncio.run(judge_llm._llm_judge_async("Thu do Viet Nam?", "Ha Noi", "")) is True
    assert calls and set(calls) == {_NEW_GROQ}
    assert judge_llm.judge_unavailable_reason() == ""
    st = gw.llm_status()
    assert st["state"] == "ok" and st["judge"]["state"] == "ok"
    assert gw.enabled_provider_count() == 1
