# [HC-31 · 2026-10-24] Self-certification transparency contract.
#
# Problem (HC-31): khi gateway chỉ có 1 provider ENABLED, CÙNG model vừa sinh
# answer vừa chấm verdict (self-certify) — nhưng evidence không nói ra điều đó
# nên PASS trông như đã đối chứng chéo. Contract sau fix:
#   - 1 provider enabled  → evidence["provider_count"] == 1, self_certified=True
#   - ≥2 provider enabled → evidence["provider_count"] >= 2, self_certified=False
#   - custom judge / atomic-deterministic / gateway chưa tồn tại → KHÔNG stamp
#   - response_verifier surface cờ này lên kết quả verify (nếu verdict có)
#
# Anti-placebo: nhánh 2-provider PHẢI cho self_certified=False tường minh
# (không chấp nhận "không có key" — trông xanh nhưng vô nghĩa).
from __future__ import annotations

import itertools
from typing import Any

import pytest
from starlette.requests import Request as StarletteRequest

from scp.api_server_parts.helpers import AskRequest
from scp.api_server_parts.pipeline.context import AskPipelineContext
from scp.api_server_parts.pipeline.verification_stage import VerificationStage
from scp.ask_adapter_parts.response_verifier import verify_ask_response
from scp.llm_gateway.client import LLMGateway, peek_gateway, OpenRouterProvider as ClientOpenRouterProvider
from scp.llm_gateway.providers import OpenRouterProvider as ProvidersOpenRouterProvider
from scp.runtime.judge import RealityJudge


def _make_starlette_request() -> StarletteRequest:
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/ask",
        "headers": [],
        "query_string": b"",
        "client": ("127.0.0.1", 54321),
        "server": ("testserver", 80),
        "scheme": "http",
    }
    return StarletteRequest(scope)


@pytest.fixture(autouse=True)
def _hermetic_gateway_state(monkeypatch):
    """[Full-suite isolation] Reset _gateway singleton + provider class-state
    trước mỗi test để provider_count deterministic. Sử dụng try/finally
    thay vì monkeypatch setattr trên class (monkeypatch restore giá trị
    gốc có thể xung đột với composition root test chạy sau)."""
    import scp.llm_gateway.client as _gw_client
    import scp.llm_gateway.providers as _gw_providers

    _orig_gateway = _gw_client._gateway
    _orig_prov_keys = _gw_providers.OpenRouterProvider._API_KEYS
    _orig_prov_cycle = _gw_providers.OpenRouterProvider._key_cycle
    _orig_prov_loaded = _gw_providers.OpenRouterProvider._dynamic_models_loaded

    _subclass_had_keys = "_API_KEYS" in _gw_client.OpenRouterProvider.__dict__
    _orig_client_keys = _gw_client.OpenRouterProvider.__dict__.get("_API_KEYS")
    _subclass_had_cycle = "_key_cycle" in _gw_client.OpenRouterProvider.__dict__
    _orig_client_cycle = _gw_client.OpenRouterProvider.__dict__.get("_key_cycle")
    _subclass_had_loaded = "_dynamic_models_loaded" in _gw_client.OpenRouterProvider.__dict__
    _orig_client_loaded = _gw_client.OpenRouterProvider.__dict__.get("_dynamic_models_loaded")

    _gw_client._gateway = None

    _gw_providers.OpenRouterProvider._API_KEYS = []
    _gw_providers.OpenRouterProvider._key_cycle = None
    _gw_providers.OpenRouterProvider._dynamic_models_loaded = True

    if "_API_KEYS" in _gw_client.OpenRouterProvider.__dict__:
        delattr(_gw_client.OpenRouterProvider, "_API_KEYS")
    if "_key_cycle" in _gw_client.OpenRouterProvider.__dict__:
        delattr(_gw_client.OpenRouterProvider, "_key_cycle")
    if "_dynamic_models_loaded" in _gw_client.OpenRouterProvider.__dict__:
        delattr(_gw_client.OpenRouterProvider, "_dynamic_models_loaded")

    _gw_client.OpenRouterProvider._API_KEYS = []
    _gw_client.OpenRouterProvider._key_cycle = None
    _gw_client.OpenRouterProvider._dynamic_models_loaded = True

    yield

    _gw_client._gateway = _orig_gateway

    if "_API_KEYS" in _gw_client.OpenRouterProvider.__dict__:
        delattr(_gw_client.OpenRouterProvider, "_API_KEYS")
    if "_key_cycle" in _gw_client.OpenRouterProvider.__dict__:
        delattr(_gw_client.OpenRouterProvider, "_key_cycle")
    if "_dynamic_models_loaded" in _gw_client.OpenRouterProvider.__dict__:
        delattr(_gw_client.OpenRouterProvider, "_dynamic_models_loaded")

    if _subclass_had_keys:
        _gw_client.OpenRouterProvider._API_KEYS = _orig_client_keys
    if _subclass_had_cycle:
        _gw_client.OpenRouterProvider._key_cycle = _orig_client_cycle
    if _subclass_had_loaded:
        _gw_client.OpenRouterProvider._dynamic_models_loaded = _orig_client_loaded

    _gw_providers.OpenRouterProvider._API_KEYS = _orig_prov_keys
    _gw_providers.OpenRouterProvider._key_cycle = _orig_prov_cycle
    _gw_providers.OpenRouterProvider._dynamic_models_loaded = _orig_prov_loaded


def _real_gateway(monkeypatch, *, extra_openai: bool) -> LLMGateway:
    """Gateway THẬT với env kiểm soát — không mock subsystem.

    OPENROUTER_API_KEY luôn set (synthetic) → openrouter enabled.
    extra_openai=True → thêm openai_compat enabled (2 provider).
    """
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-hc31")
    monkeypatch.delenv("SCP_LLM_FALLBACK_PROVIDERS", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    if extra_openai:
        monkeypatch.setenv("OPENAI_API_KEY", "test-key-hc31")
    else:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    return LLMGateway()


@pytest.fixture
def openrouter_keys(monkeypatch):
    import scp.llm_gateway.client as _gw_client
    import scp.llm_gateway.providers as _gw_providers

    monkeypatch.setattr(_gw_providers.OpenRouterProvider, "_API_KEYS", ["test-key-hc31"])
    monkeypatch.setattr(
        _gw_providers.OpenRouterProvider,
        "_key_cycle",
        itertools.cycle(["test-key-hc31"]),
    )
    monkeypatch.setattr(_gw_client.OpenRouterProvider, "_API_KEYS", ["test-key-hc31"])
    monkeypatch.setattr(
        _gw_client.OpenRouterProvider,
        "_key_cycle",
        itertools.cycle(["test-key-hc31"]),
    )


# ---------------------------------------------------------------------------
# 1. Gateway đếm provider enabled (unit — subsystem thật)
# ---------------------------------------------------------------------------

def test_gateway_provider_count_one_when_only_openrouter(openrouter_keys, monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("SCP_LLM_FALLBACK_PROVIDERS", raising=False)
    gw = LLMGateway()
    assert gw.enabled_provider_count() == 1


def test_gateway_provider_count_two_with_openai_compat(openrouter_keys, monkeypatch) -> None:
    # openai_compat chỉ enabled khi CÓ key VÀ model (EnvCompatProvider.enabled
    # = keys AND model AND base_url — default model rỗng).
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-hc31")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o-mini")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("SCP_LLM_FALLBACK_PROVIDERS", raising=False)
    gw = LLMGateway()
    assert gw.enabled_provider_count() == 2


def test_peek_gateway_does_not_instantiate(monkeypatch) -> None:
    """peek_gateway KHÔNG tự tạo singleton (zero side effect)."""
    monkeypatch.setattr("scp.llm_gateway.client._gateway", None)
    assert peek_gateway() is None


# ---------------------------------------------------------------------------
# 2. VerificationStage stamp anti-placebo (judge path, gateway thật)
# ---------------------------------------------------------------------------

def _patched_reality_judge(monkeypatch) -> RealityJudge:
    """RealityJudge THẬT với LLM boundary stub ở method instance (không mock
    class/subsystem — chỉ thay cuộc gọi LLM ngoài)."""
    judge = RealityJudge()

    async def canned_judge(**kwargs: Any) -> dict[str, Any]:
        return {
            "verdict": "PASS",
            "confidence": 0.9,
            "final_answer": "Hi",
            "evidence": {"governance_decision": "ALLOW"},
            "slm_responses": [],
        }

    judge.judge_with_react_fallback = canned_judge  # type: ignore[method-assign]
    return judge


def _fake_get_judge(judge: RealityJudge):
    def get_judge() -> RealityJudge:  # tên PHẢI là get_judge để không bị coi custom
        return judge
    return get_judge


async def _run_verification_stage(monkeypatch, gateway: LLMGateway) -> AskPipelineContext:
    monkeypatch.setattr("scp.llm_gateway.client._gateway", gateway)
    monkeypatch.setenv("SCP_ATOMIC_VERIFIER", "0")  # ép judge path (KHÔNG atomic short-circuit)
    monkeypatch.setattr(
        "scp.api_server_parts.helpers.get_judge", _fake_get_judge(_patched_reality_judge(monkeypatch))
    )
    req = AskRequest(question="Hello")
    ctx = AskPipelineContext(req=req, request=_make_starlette_request())
    ctx.route_decision = None
    await VerificationStage().execute(ctx)
    return ctx


@pytest.mark.asyncio
async def test_stage_stamps_self_certified_true_with_single_provider(
    openrouter_keys, monkeypatch
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("SCP_LLM_FALLBACK_PROVIDERS", raising=False)
    gw = LLMGateway()
    ctx = await _run_verification_stage(monkeypatch, gw)
    ev = ctx.judge_verdict.evidence
    assert ev["provider_count"] == 1
    assert ev["self_certified"] is True


@pytest.mark.asyncio
async def test_stage_anti_placebo_two_providers_self_certified_false(
    openrouter_keys, monkeypatch
) -> None:
    """ANTI-PLACEBO: 2 provider enabled → self_certified phải False tường minh."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-hc31")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o-mini")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("SCP_LLM_FALLBACK_PROVIDERS", raising=False)
    gw = LLMGateway()
    assert gw.enabled_provider_count() == 2
    ctx = await _run_verification_stage(monkeypatch, gw)
    ev = ctx.judge_verdict.evidence
    assert ev["provider_count"] == 2
    assert ev["self_certified"] is False


@pytest.mark.asyncio
async def test_stage_no_stamp_when_gateway_absent(openrouter_keys, monkeypatch) -> None:
    """Custom judge path / gateway chưa tồn tại → không dán nhãn (UNKNOWN)."""
    monkeypatch.setattr("scp.llm_gateway.client._gateway", None)
    monkeypatch.setenv("SCP_ATOMIC_VERIFIER", "0")
    monkeypatch.setattr(
        "scp.api_server_parts.helpers.get_judge", _fake_get_judge(_patched_reality_judge(monkeypatch))
    )
    req = AskRequest(question="Hello")
    ctx = AskPipelineContext(req=req, request=_make_starlette_request())
    ctx.route_decision = None
    await VerificationStage().execute(ctx)
    ev = ctx.judge_verdict.evidence
    assert "self_certified" not in ev
    assert "provider_count" not in ev


# ---------------------------------------------------------------------------
# 3. response_verifier surface
# ---------------------------------------------------------------------------

def _base_response_data() -> dict[str, Any]:
    return {
        "verdict": "PASS",
        "final_answer": "Hi",
        "governance_decision": "UPHOLD",
        "confidence": 0.9,
        "evidence": {"governance_decision": "UPHOLD", "judge_evaluated": True},
        "v98_classification": {"provenance": "general_knowledge", "judge_evaluated": True},
        "trace_id": "trace-hc31",
    }


@pytest.mark.asyncio
async def test_response_verifier_surfaces_self_certified_true() -> None:
    data = _base_response_data()
    data["evidence"]["self_certified"] = True
    data["evidence"]["provider_count"] = 1
    req = AskRequest(question="Hello")
    res = await verify_ask_response(req, data)
    assert res["self_certified"] is True
    assert res["provider_count"] == 1


@pytest.mark.asyncio
async def test_response_verifier_anti_placebo_surfaces_false() -> None:
    data = _base_response_data()
    data["evidence"]["self_certified"] = False
    data["evidence"]["provider_count"] = 3
    req = AskRequest(question="Hello")
    res = await verify_ask_response(req, data)
    assert res["self_certified"] is False  # phải là False tường minh, không bị nuốt
    assert res["provider_count"] == 3


@pytest.mark.asyncio
async def test_response_verifier_no_flags_no_keys() -> None:
    """Verdict không có cờ → không thêm key (UNKNOWN, không tự bịa)."""
    req = AskRequest(question="Hello")
    res = await verify_ask_response(req, _base_response_data())
    assert "self_certified" not in res
    assert "provider_count" not in res
