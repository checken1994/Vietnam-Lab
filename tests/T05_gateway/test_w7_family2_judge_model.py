"""[W7-e7] family-2 judge model — Option C (GA.md B1b, owner duyệt).

Root-cause W6-RE: crosscheck 2 opinion CÙNG weights nemotron (openai_compat
chạy OPENAI_MODEL=nvidia/nemotron-3-super-120b-a12b:free trên
https://openrouter.ai/api/v1; openrouter_judge chạy default
anthropic/claude-3-5-sonnet → 404 pre-flight → rơi free-fallback nemotron
cùng weights) = consensus không độc lập (residual DNA-#5 đã ghi trong
`_family_key` — key pre-flight theo primary model không nhìn thấy fallback).

Fix e7 (config, không đụng code hedge/deadline W1): set env
`OPENROUTER_MODEL_JUDGE_PRIMARY=deepseek/deepseek-v4-flash-0731:free`
(env var ĐÃ TỒN TẠI — scp/llm_gateway/client.py OpenRouterProvider task
'judge'; model free có rate-limit — hedge/deadline W1-c6 giữ nguyên).

Hợp đồng pin ở đây:
  1. env knob điều khiển đúng OpenRouterProvider(task='judge').model;
  2. default khi unset = 'anthropic/claude-3-5-sonnet' (giá trị ROLLBACK
     được ghi trong .env comment — pin để nhận biết hồi quy config);
  3. family-key (base_url|model) của judge provider KHÁC openai_compat khi
     cả 2 sống → cross_verify đạt consensus 'agree' với 2 family thật
     (unit với mock provider — không gọi mạng);
  4. old-shape (2 provider cùng nemotron) vẫn 1 family fail-closed — đã pin
     trong test_multi_llm_crosscheck.py
     (test_cross_verify_same_base_url_and_model_is_one_family_regardless_of_name).
"""
from __future__ import annotations

import asyncio

from scp.llm_gateway.client import EnvCompatProvider, OpenRouterProvider
from scp.runtime.multi_llm_crosscheck import _family_key, cross_verify

_DEEPSEEK = "deepseek/deepseek-v4-flash-0731:free"
_NEMOTRON = "nvidia/nemotron-3-super-120b-a12b:free"
_OPENROUTER_BASE = "https://openrouter.ai/api/v1"


def test_judge_primary_model_env_knob_selects_deepseek(monkeypatch) -> None:
    """e7 acceptance: đọc cấu hình judge → model = deepseek-... (khác
    nemotron). Env knob hiện có điều khiển đúng primary model của provider
    judge; task khác vẫn dùng OPENROUTER_MODEL (không bị knob đụng)."""
    monkeypatch.setenv("OPENROUTER_MODEL_JUDGE_PRIMARY", _DEEPSEEK)
    monkeypatch.setenv("OPENROUTER_MODEL", "openrouter/free")

    judge = OpenRouterProvider(task="judge")
    other = OpenRouterProvider(task="default")

    assert judge.model == _DEEPSEEK
    assert judge.model != _NEMOTRON
    assert other.model == "openrouter/free"  # knob chỉ áp cho task='judge'


def test_judge_primary_model_unset_default_is_rollback_value(monkeypatch) -> None:
    """Rollback pin: unset → default cũ 'anthropic/claude-3-5-sonnet' (chính
    là giá trị được ghi trong comment rollback của .env sau e7). Default này
    404 pre-flight → rơi fallback nemotron trùng weights openai_compat —
    nguyên nhân consensus không độc lập trước e7."""
    monkeypatch.delenv("OPENROUTER_MODEL_JUDGE_PRIMARY", raising=False)

    assert OpenRouterProvider(task="judge").model == "anthropic/claude-3-5-sonnet"


def test_gateway_judge_chain_family_keys_distinct_under_e7_env(monkeypatch) -> None:
    """Cấu hình production-shape sau e7 (đọc từ env, cùng pattern gateway
    dựng provider): openai_compat (OPENAI_MODEL=nemotron) + openrouter_judge
    (knob=deepseek) trên CÙNG base_url → 2 family-key khác nhau."""
    monkeypatch.setenv("OPENROUTER_MODEL_JUDGE_PRIMARY", _DEEPSEEK)
    monkeypatch.setenv("OPENAI_MODEL", _NEMOTRON)
    monkeypatch.setenv("OPENAI_BASE_URL", _OPENROUTER_BASE)
    monkeypatch.setenv("OPENROUTER_BASE_URL", _OPENROUTER_BASE)

    compat = EnvCompatProvider(
        "openai_compat", "judge", "OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL"
    )
    judge = OpenRouterProvider(task="judge")

    assert judge.model == _DEEPSEEK
    assert compat.model == _NEMOTRON
    assert _family_key(judge) == f"{_OPENROUTER_BASE}|{_DEEPSEEK}"
    assert _family_key(compat) == f"{_OPENROUTER_BASE}|{_NEMOTRON}"
    assert _family_key(judge) != _family_key(compat)


class _FamilyProvider:
    """Provider stub cùng shape test_multi_llm_crosscheck.FakeProvider —
    family = (base_url, model); không gọi mạng."""

    def __init__(self, name: str, base_url: str, model: str, answer: str = "PASS"):
        self.PROVIDER_NAME = name
        self.base_url = base_url
        self.model = model
        self.answer = answer
        self.calls = 0
        self.enabled = True

    async def chat(self, question: str, context: str = "", system_prompt: str = "", prioritize_free: bool = False):
        self.calls += 1
        return self.answer, f"{self.PROVIDER_NAME}:{self.model}"


class _FamilyGateway:
    def __init__(self, providers):
        self.providers = list(providers)

    def provider_candidates(self, task: str):
        assert task == "judge"
        return list(self.providers)


def test_cross_verify_reaches_consensus_with_two_weight_families() -> None:
    """e7 acceptance: crosscheck family-key 2 opinion KHÁC nhau khi cả 2
    sống (unit với mock) — cùng base_url openrouter nhưng 2 model khác
    weights thật (nemotron vs deepseek) → consensus 'agree' + audit trail
    ghi 2 family-key riêng biệt. OLD-shape (cùng nemotron) →
    missing_distinct_providers (pin W3-e2 sẵn, không lặp lại ở đây)."""
    compat = _FamilyProvider("openai_compat", _OPENROUTER_BASE, _NEMOTRON)
    judge = _FamilyProvider("openrouter", _OPENROUTER_BASE, _DEEPSEEK)

    result = asyncio.run(
        cross_verify(
            "Is the supplied answer supported?",
            "Yes.",
            context="The supplied evidence says yes.",
            gateway=_FamilyGateway([compat, judge]),
        )
    )

    assert result["consensus"] == "agree"
    assert result["final"] == "PASS"
    assert compat.calls == 1
    assert judge.calls == 1
    assert result["distinct_families_attempted"] == [
        f"{_OPENROUTER_BASE}|{_NEMOTRON}",
        f"{_OPENROUTER_BASE}|{_DEEPSEEK}",
    ]
