"""[W3-e4] root-5: identity post-guard — vendor self-attribution trong final
answer phải được thay bằng câu identity-pin trung lập.

Thực tế runtime (GA.md B1b): q12 answer "Tôi là SCP... được phát triển bởi
NVIDIA" được DELIVER nguyên văn cho client. e3 pin system prompt (pre-guard),
nhưng model vẫn có thể vượt prompt (persona leakage). Hợp đồng sau e4: trước
khi trả response, mọi câu trong final_answer tự-nhận nhà phát triển ngoài
("phát triển bởi NVIDIA", "developed by OpenAI", "powered by Google", ...)
được THAY THẾ bằng câu identity-pin trung lập — giữ phần còn lại của answer —
và log WARNING quan sát được.

Không gọi LLM thật: judge + gateway + fact-check đều mock/hermetic.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from scp.api_server_parts._ask_impl import _ask_impl, _strip_vendor_identity_claims
from scp.api_server_parts.helpers import AskRequest


_PIN_VI = "Tôi là SCP — trợ lý AI do dự án SCP phát triển."
_PIN_EN = "I am SCP — an AI assistant developed by the SCP project."


def _pass_judge(final_answer: str):
    class FakeJudge:
        async def judge_with_react_fallback(self, **kwargs):
            return {
                "verdict": "PASS",
                "confidence": 0.9,
                "reasoning": "ok",
                "failures": [],
                "evidence": {"governance_decision": "ALLOW"},
                "slm_responses": [],
                "final_answer": final_answer,
            }

    return FakeJudge()


class _NoopGateway:
    """Gateway stub — KHÔNG network. BẮT BUỘC: test integration này để
    ai_answer rỗng nên _ask_impl chạy generation path; nếu dùng gateway thật,
    test sẽ gọi LLM thật và tiêu rate-limit trong state dùng chung của
    process (dead-model breaker) → làm FAIL các test thật-network chạy sau
    (bài học hermetic: mọi /ask test phải mock gateway)."""

    async def chat(self, question, context="", system_prompt="", task="default"):
        return "Tôi là SCP, một trợ lý AI.", "noop-provider"


def _mock_request() -> MagicMock:
    mock_request = MagicMock()
    mock_request.client.host = "127.0.0.1"
    mock_request.state = MagicMock()
    mock_request.state.scp_run = None
    mock_request.state.trace_id = "test-w3-e4"
    return mock_request


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCP_MULTI_LLM_CROSSCHECK", "0")
    monkeypatch.setenv("SCP_WEB_FALLBACK", "0")
    monkeypatch.setattr("scp.llm_gateway.get_gateway", lambda: _NoopGateway())
    monkeypatch.setattr(
        "scp.api_server_parts._ask_impl._async_fact_check", lambda *a, **k: _noop()
    )


async def _noop() -> None:
    return None


# ---------------------------------------------------------------------------
# Unit: helper thuần (pin pattern ở test — đúng yêu cầu W3-e4)
# ---------------------------------------------------------------------------


def test_postguard_replaces_vi_vendor_claim_sentence_keeps_rest():
    answer = "Tôi là SCP — trợ lý AI được phát triển bởi NVIDIA. Tôi có thể giúp gì cho bạn?"
    sanitized, changed = _strip_vendor_identity_claims(answer)

    assert changed is True
    assert "NVIDIA" not in sanitized
    assert _PIN_VI in sanitized
    assert "Tôi có thể giúp gì cho bạn?" in sanitized


def test_postguard_replaces_en_vendor_claim_sentence():
    sanitized, changed = _strip_vendor_identity_claims(
        "I was developed by OpenAI. How can I help you today?"
    )

    assert changed is True
    assert "OpenAI" not in sanitized
    assert _PIN_EN in sanitized
    assert "How can I help you today?" in sanitized


def test_postguard_matches_powered_by_and_bare_by_patterns():
    assert _strip_vendor_identity_claims("Tôi được vận hành trên nền tảng by NVIDIA.")[0] != (
        "Tôi được vận hành trên nền tảng by NVIDIA."
    )
    sanitized, changed = _strip_vendor_identity_claims("Our assistant is powered by Anthropic.")
    assert changed is True
    assert "Anthropic" not in sanitized


def test_postguard_leaves_clean_answer_untouched():
    answer = "Hôm nay trời đẹp. Tôi có thể giúp gì cho bạn?"
    sanitized, changed = _strip_vendor_identity_claims(answer)

    assert changed is False
    assert sanitized == answer


def test_postguard_never_touches_identity_pin_sentence_itself():
    answer = f"{_PIN_VI} Tôi có thể giúp gì cho bạn?"
    sanitized, changed = _strip_vendor_identity_claims(answer)

    assert changed is False
    assert sanitized == answer


def test_postguard_skips_boundary_withheld_artifacts():
    answer = "[SCP: Answer withheld — Governance KILL]"
    sanitized, changed = _strip_vendor_identity_claims(answer)

    assert changed is False
    assert sanitized == answer


# ---------------------------------------------------------------------------
# Integration: /ask với judge trả answer lộ persona
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ask_impl_delivered_answer_has_no_vendor_persona(monkeypatch, caplog) -> None:
    """Old-fails/new-passes: trước e4, answer '...được phát triển bởi NVIDIA'
    được deliver nguyên văn. Sau e4: câu đó bị thay bằng pin trung lập, phần
    còn lại giữ nguyên, WARNING được log."""
    monkeypatch.setattr(
        "scp.api_server_parts._ask_impl.get_judge",
        lambda: _pass_judge(
            "Tôi là SCP — trợ lý AI được phát triển bởi NVIDIA. Tôi có thể giúp gì cho bạn?"
        ),
    )

    req = AskRequest(question="Bạn được huấn luyện từ dữ liệu gì?", ai_answer="")
    response = await _ask_impl(req, _mock_request())

    assert response.verdict == "PASS"
    assert "NVIDIA" not in str(response.final_answer)
    assert _PIN_VI in str(response.final_answer)
    assert "Tôi có thể giúp gì cho bạn?" in str(response.final_answer)
