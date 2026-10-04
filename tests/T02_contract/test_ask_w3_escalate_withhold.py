"""[W3-e1] /ask boundary: governance ESCALATE trên câu benign phải withhold
với thông điệp PHÂN BIỆT được — không mượn nhãn "Governance KILL".

Thực tế runtime (GA.md B1b): q08 benign không verify được → judge (trước e1)
phát governance KILL → _ask_impl rơi vào nhánh _is_true_security_threat →
"[SCP: Answer withheld — Governance KILL]" — KILL-xổ-pháo cho nội dung không
nguy hiểm. Sau e1:
  - judge benign-FAIL phát governance ESCALATE;
  - _ask_impl thêm nhánh riêng: governance ESCALATE (non-chatbot) →
    "[SCP: Answer withheld — không xác minh được câu trả lời
    (governance: ESCALATE)]" — abstain trung thực, vẫn fail-closed (không
    deliver answer chưa verify), không nhãn KILL;
  - governance DEGRADED giữ nguyên thông điệp cũ;
  - đường security-threat thật (_is_true_security_threat, ví dụ governance
    KILL từ mocked judge) giữ nguyên "[SCP: Answer withheld — Governance KILL]"
    (đã pin trong tests/integration/test_critical_chain_severed.py).

Không gọi LLM: judge bị mock, gateway/web fallback tắt qua env.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from scp.api_server_parts._ask_impl import _ask_impl
from scp.api_server_parts.helpers import AskRequest
from scp.runtime.question_router import LANE_FACTUAL, RouteDecision


def _force_factual_lane(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "scp.runtime.question_router.route_question",
        lambda q: RouteDecision(
            intent="LOOKUP", domain="general", confidence=0.9,
            via="test", lane=LANE_FACTUAL,
        ),
    )


def _mock_request() -> MagicMock:
    mock_request = MagicMock()
    mock_request.client.host = "127.0.0.1"
    mock_request.state = MagicMock()
    mock_request.state.scp_run = None
    mock_request.state.trace_id = "test-w3-e1"
    return mock_request


def _fail_judge(governance: str, verdict: str = "FAIL"):
    class FakeJudge:
        async def judge_with_react_fallback(self, **kwargs):
            return {
                "verdict": verdict,
                "confidence": 0.0,
                "reasoning": "semantic verification failed",
                "failures": ["semantic_judge_fail"],
                "evidence": {"governance_decision": governance},
                "slm_responses": [],
                "final_answer": "Ai đó là tổng thống.",
            }

    return FakeJudge()


_ESCALATE_WITHHOLD = (
    "[SCP: Answer withheld — không xác minh được câu trả lời (governance: ESCALATE)]"
)


@pytest.mark.asyncio
async def test_ask_impl_escalate_governance_withholds_distinct_message_not_kill(
    monkeypatch,
) -> None:
    """Old-fails/new-passes: trước e1, verdict FAIL + governance ESCALATE rơi
    vào nhánh S-H1 chung → "[SCP: Answer withheld — governance degraded]" (nhãn
    sai class); đường judge-cũ (FAIL→KILL) thì rơi vào nhãn "Governance KILL".
    Sau e1: thông điệp ESCALATE phân biệt được, fail-closed giữ nguyên."""
    _force_factual_lane(monkeypatch)
    monkeypatch.setenv("SCP_MULTI_LLM_CROSSCHECK", "0")
    monkeypatch.setenv("SCP_WEB_FALLBACK", "0")
    monkeypatch.setattr(
        "scp.api_server_parts._ask_impl.get_judge", lambda: _fail_judge("ESCALATE")
    )
    monkeypatch.setattr(
        "scp.api_server_parts._ask_impl._async_fact_check",
        _noop_fact_check,
    )

    req = AskRequest(question="Ai là tổng thống Mỹ hiện tại?", ai_answer="Ai đó là tổng thống.")
    response = await _ask_impl(req, _mock_request())

    assert response.verdict == "FAIL"
    assert response.governance_decision == "ESCALATE"
    assert response.final_answer == _ESCALATE_WITHHOLD
    assert "Governance KILL" not in str(response.final_answer)
    assert "governance degraded" not in str(response.final_answer)
    # Fail-closed: nội dung chưa verify không được deliver.
    assert "tổng thống" not in str(response.final_answer)


@pytest.mark.asyncio
async def test_ask_impl_degraded_governance_keeps_existing_message(monkeypatch) -> None:
    """DEGRADED giữ nguyên hợp đồng cũ "[SCP: Answer withheld — governance
    degraded]" — e1 chỉ tách ESCALATE, không đụng DEGRADED."""
    _force_factual_lane(monkeypatch)
    monkeypatch.setenv("SCP_MULTI_LLM_CROSSCHECK", "0")
    monkeypatch.setenv("SCP_WEB_FALLBACK", "0")
    monkeypatch.setattr(
        "scp.api_server_parts._ask_impl.get_judge",
        lambda: _fail_judge("DEGRADED", verdict="DEGRADED"),
    )
    monkeypatch.setattr(
        "scp.api_server_parts._ask_impl._async_fact_check",
        _noop_fact_check,
    )

    req = AskRequest(question="Ai là tổng thống Mỹ hiện tại?", ai_answer="Ai đó là tổng thống.")
    response = await _ask_impl(req, _mock_request())

    assert response.verdict == "DEGRADED"
    assert response.governance_decision == "DEGRADED"
    assert response.final_answer == "[SCP: Answer withheld — governance degraded]"


@pytest.mark.asyncio
async def test_ask_impl_chatbot_escalate_withholds_distinct_message_not_403_kill(
    monkeypatch,
) -> None:
    """[W3-e1 follow-up] Chatbot lane: benign ask không verify được (judge
    FAIL + governance ESCALATE) phải trả 200 + thông điệp withheld phân biệt
    được — KHÔNG phải 403 "Governance KILL enforced" và KHÔNG deliver answer
    chưa verify.

    Thực tế chứng minh bằng DEBUG log: trên main, đường này rơi vào nhánh
    _is_true_security_threat (gov==KILL) → 200 + "[SCP: Answer withheld]"; e1
    tách ESCALATE khỏi KILL nên nếu chatbot branch không xử lý ESCALATE thì
    rơi xuống raise 403 "Governance KILL enforced" (regression so với hợp đồng
    200-withheld của các /ask test thật — TestFlow02AskDetectorDegraded)."""
    monkeypatch.setenv("SCP_MULTI_LLM_CROSSCHECK", "0")
    monkeypatch.setenv("SCP_WEB_FALLBACK", "0")
    monkeypatch.setattr(
        "scp.api_server_parts._ask_impl.get_judge", lambda: _fail_judge("ESCALATE")
    )
    monkeypatch.setattr(
        "scp.api_server_parts._ask_impl._async_fact_check",
        _noop_fact_check,
    )

    req = AskRequest(question="hi", ai_answer="Xin chào bạn!")  # lane chatbot mặc định
    response = await _ask_impl(req, _mock_request())

    assert response.verdict == "FAIL"
    assert response.governance_decision == "ESCALATE"
    assert response.final_answer == _ESCALATE_WITHHOLD
    assert "Governance KILL" not in str(response.final_answer)
    # Fail-closed: nội dung chưa verify của judge không được deliver.
    assert "tổng thống" not in str(response.final_answer)


async def _noop_fact_check(*_a, **_k) -> None:
    return None
