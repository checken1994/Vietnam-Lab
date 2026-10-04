"""[W3-e3] root-5: identity leak — system prompt phải pin định danh SCP.

Thực tế runtime (GA.md B1b): q12 "Bạn được huấn luyện từ dữ liệu gì?" →
answer "Tôi là SCP... được phát triển bởi NVIDIA" — provider persona lộ ra
client vì system prompt (_ask_impl.py) không pin identity.

Hợp đồng sau e3: system prompt (cả vi + en) sinh answer cho /ask PHẢI chứa
câu định danh: SCP do dự án SCP phát triển; khi hỏi ai phát triển/từ đâu ra,
trả lời trung lập theo identity pin, KHÔNG nhận danh nhà cung cấp model nền
(NVIDIA, OpenAI, Anthropic, Google, Meta...).

Old-fails/new-passes: trước e3, captured system prompt không chứa pin → test
FAIL. Không gọi LLM thật — gateway bị thay bằng stub ghi lại system_prompt.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from scp.api_server_parts._ask_impl import _ask_impl
from scp.api_server_parts.helpers import AskRequest


_PIN_HINTS_VI = (
    "dự án SCP phát triển",
    "không tự nhận",
    "NVIDIA",
)
_PIN_HINTS_EN = (
    "SCP project",
    "never claim",
    "NVIDIA",
)


class _CapturingGateway:
    """Gateway stub: ghi lại system_prompt được gửi cho provider, trả answer
    tĩnh — không network, không LLM thật."""

    def __init__(self) -> None:
        self.captured_system_prompts: list[str] = []

    async def chat(self, question, context="", system_prompt="", task="default"):
        self.captured_system_prompts.append(str(system_prompt))
        return "Tôi là SCP, một trợ lý AI. Tôi được huấn luyện từ dữ liệu đa dạng.", "fake-provider"


def _pass_judge():
    class FakeJudge:
        async def judge_with_react_fallback(self, **kwargs):
            return {
                "verdict": "PASS",
                "confidence": 0.9,
                "reasoning": "ok",
                "failures": [],
                "evidence": {"governance_decision": "ALLOW"},
                "slm_responses": [],
                "final_answer": "",
            }

    return FakeJudge()


def _mock_request() -> MagicMock:
    mock_request = MagicMock()
    mock_request.client.host = "127.0.0.1"
    mock_request.state = MagicMock()
    mock_request.state.scp_run = None
    mock_request.state.trace_id = "test-w3-e3"
    return mock_request


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCP_MULTI_LLM_CROSSCHECK", "0")
    monkeypatch.setenv("SCP_WEB_FALLBACK", "0")
    monkeypatch.setattr(
        "scp.api_server_parts._ask_impl.get_judge", lambda: _pass_judge()
    )
    monkeypatch.setattr(
        "scp.api_server_parts._ask_impl._async_fact_check", lambda *a, **k: _noop()
    )


async def _noop() -> None:
    return None


@pytest.mark.asyncio
async def test_ask_impl_vi_system_prompt_pins_scp_identity(monkeypatch) -> None:
    """q12 (tiếng Việt): system prompt phải chứa identity pin — SCP do dự án
    SCP phát triển, không nhận danh vendor nền."""
    gateway = _CapturingGateway()
    monkeypatch.setattr("scp.llm_gateway.get_gateway", lambda: gateway)

    req = AskRequest(question="Bạn được huấn luyện từ dữ liệu gì?", ai_answer="")
    await _ask_impl(req, _mock_request())

    assert gateway.captured_system_prompts, "generation path must call the gateway"
    prompt = gateway.captured_system_prompts[-1]
    for hint in _PIN_HINTS_VI:
        assert hint in prompt, f"vi system prompt must pin identity (missing: {hint})"


@pytest.mark.asyncio
async def test_ask_impl_en_system_prompt_pins_scp_identity(monkeypatch) -> None:
    """Bản tiếng Anh của system prompt phải pin cùng định danh."""
    gateway = _CapturingGateway()
    monkeypatch.setattr("scp.llm_gateway.get_gateway", lambda: gateway)

    req = AskRequest(question="Who developed you?", ai_answer="")
    await _ask_impl(req, _mock_request())

    assert gateway.captured_system_prompts, "generation path must call the gateway"
    prompt = gateway.captured_system_prompts[-1]
    for hint in _PIN_HINTS_EN:
        assert hint in prompt, f"en system prompt must pin identity (missing: {hint})"
