"""Test SEC-R2-02: Fail-closed enforcement on missing governance and removal of bypass_verdict_pass."""
from __future__ import annotations

import pytest
from fastapi import HTTPException
from starlette.requests import Request as StarletteRequest

from scp.api_server_parts._ask_impl import _ask_impl
from scp.api_server_parts.helpers import AskRequest
from scp.runtime.question_router import LANE_CHATBOT, RouteDecision


def _mock_request() -> StarletteRequest:
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/ask",
        "headers": [(b"host", b"127.0.0.1:8000")],
        "query_string": b"",
        "client": ("127.0.0.1", 54321),
        "server": ("testserver", 80),
        "scheme": "http",
    }
    req = StarletteRequest(scope)
    req.state.scp_run = None
    req.state.trace_id = "test-sec-r2-02"
    return req


@pytest.mark.asyncio
async def test_ask_impl_fails_closed_when_governance_missing(monkeypatch):
    """SEC-R2-02: When governance decision is empty or missing, _ask_impl must raise 403."""
    req = AskRequest(question="Xin chào bạn", ai_answer="Chào bạn!")
    mock_request = _mock_request()

    # Mock judge returning empty governance decision
    class FakeJudge:
        async def judge_with_react_fallback(self, **kwargs):
            return {
                "verdict": "PASS",
                "confidence": 0.85,
                "evidence": {"governance_decision": ""},  # MISSING
                "slm_responses": [],
                "final_answer": "Chào bạn!",
            }

    monkeypatch.setattr("scp.api_server_parts._ask_impl.get_judge", lambda: FakeJudge())

    with pytest.raises(HTTPException) as exc_info:
        await _ask_impl(req, mock_request)

    assert exc_info.value.status_code == 403
    assert "Governance clearance missing — fail-closed" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_ask_impl_fails_closed_when_governance_unknown(monkeypatch):
    """SEC-R2-02: When governance decision is UNKNOWN, _ask_impl must raise 403."""
    req = AskRequest(question="Xin chào bạn", ai_answer="Chào bạn!")
    mock_request = _mock_request()

    class FakeJudge:
        async def judge_with_react_fallback(self, **kwargs):
            return {
                "verdict": "UNKNOWN",
                "confidence": 0.40,
                "evidence": {"governance_decision": "UNKNOWN"},
                "slm_responses": [],
                "final_answer": "Chào bạn!",
            }

    monkeypatch.setattr("scp.api_server_parts._ask_impl.get_judge", lambda: FakeJudge())

    with pytest.raises(HTTPException) as exc_info:
        await _ask_impl(req, mock_request)

    assert exc_info.value.status_code == 403
    assert "Governance clearance missing — fail-closed" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_ask_impl_fails_closed_when_governance_none(monkeypatch):
    """SEC-R2-02: When governance decision is None in evidence, _ask_impl must raise 403."""
    req = AskRequest(question="Xin chào bạn", ai_answer="Chào bạn!")
    mock_request = _mock_request()

    class FakeJudge:
        async def judge_with_react_fallback(self, **kwargs):
            return {
                "verdict": "PASS",
                "confidence": 0.85,
                "evidence": {"governance_decision": None},
                "slm_responses": [],
                "final_answer": "Chào bạn!",
            }

    monkeypatch.setattr("scp.api_server_parts._ask_impl.get_judge", lambda: FakeJudge())

    with pytest.raises(HTTPException) as exc_info:
        await _ask_impl(req, mock_request)

    assert exc_info.value.status_code == 403
    assert "Governance clearance missing — fail-closed" in str(exc_info.value.detail)


def _force_chatbot_lane(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "scp.runtime.question_router.route_question",
        lambda q: RouteDecision(
            intent="REASONING", domain="general", confidence=0.9,
            via="test", lane=LANE_CHATBOT, reason="test",
        ),
    )


def _pass_judge(governance: str):
    class FakeJudge:
        async def judge_with_react_fallback(self, **kwargs):
            return {
                "verdict": "PASS",
                "confidence": 0.9,
                "evidence": {"governance_decision": governance},
                "slm_responses": [],
                "final_answer": "2+2=4",
            }

    return FakeJudge()


@pytest.mark.asyncio
async def test_bypass_verdict_pass_rejected_fail_closed(monkeypatch):
    """R8-P01: bypass_verdict_pass is completely removed; missing governance raises 403."""
    _force_chatbot_lane(monkeypatch)
    monkeypatch.setenv("SCP_MULTI_LLM_CROSSCHECK", "0")
    monkeypatch.setenv("SCP_WEB_FALLBACK", "0")
    monkeypatch.setattr(
        "scp.api_server_parts._ask_impl.get_judge", lambda: _pass_judge("")
    )

    req = AskRequest(question="2+2 bằng mấy?", ai_answer="4")

    # With empty governance, fail-closed 403 must be raised
    with pytest.raises(HTTPException) as exc_info:
        await _ask_impl(req, _mock_request())

    assert exc_info.value.status_code == 403
    assert "Governance clearance missing — fail-closed" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_valid_governance_clearance_passes(monkeypatch):
    """R8-P01: Valid governance decision (ALLOW) delivers the response."""
    _force_chatbot_lane(monkeypatch)
    monkeypatch.setenv("SCP_MULTI_LLM_CROSSCHECK", "0")
    monkeypatch.setenv("SCP_WEB_FALLBACK", "0")
    monkeypatch.setattr(
        "scp.api_server_parts._ask_impl.get_judge", lambda: _pass_judge("ALLOW")
    )

    req = AskRequest(question="2+2 bằng mấy?", ai_answer="4")
    response = await _ask_impl(req, _mock_request())

    assert response.verdict == "PASS"
    assert response.governance_decision == "ALLOW"
    assert "4" in str(response.final_answer)


@pytest.mark.asyncio
async def test_uphold_governance_clearance_passes(monkeypatch):
    """R8-P01: Valid governance decision (UPHOLD) delivers the response."""
    _force_chatbot_lane(monkeypatch)
    monkeypatch.setenv("SCP_MULTI_LLM_CROSSCHECK", "0")
    monkeypatch.setenv("SCP_WEB_FALLBACK", "0")
    monkeypatch.setattr(
        "scp.api_server_parts._ask_impl.get_judge", lambda: _pass_judge("UPHOLD")
    )

    req = AskRequest(question="2+2 bằng mấy?", ai_answer="4")
    response = await _ask_impl(req, _mock_request())

    assert response.verdict == "PASS"
    assert response.governance_decision == "UPHOLD"
    assert "4" in str(response.final_answer)


def _force_factual_lane(monkeypatch):
    """Pin the router to the factual (non-chatbot) lane for hermetic runs."""
    from scp.runtime.question_router import LANE_FACTUAL, RouteDecision

    monkeypatch.setattr(
        "scp.runtime.question_router.route_question",
        lambda q: RouteDecision(
            intent="LOOKUP", domain="general", confidence=0.9,
            via="test", lane=LANE_FACTUAL,
        ),
    )


@pytest.mark.asyncio
async def test_ask_impl_non_chatbot_pass_with_missing_governance_fails_closed(monkeypatch):
    """SEC-R2-02 (second-pass): non-chatbot lane + verdict PASS + governance
    MISSING must escalate 403. Pre-fix the answer was delivered (200) because
    the fail-closed check only ran in the chatbot branch."""
    _force_factual_lane(monkeypatch)
    monkeypatch.setenv("SCP_MULTI_LLM_CROSSCHECK", "0")
    monkeypatch.setenv("SCP_WEB_FALLBACK", "0")
    monkeypatch.setattr("scp.api_server_parts._ask_impl.get_judge", lambda: _pass_judge(""))

    req = AskRequest(question="What is 2+2?", ai_answer="2+2=4", contexts=["2+2=4"])
    with pytest.raises(HTTPException) as exc_info:
        await _ask_impl(req, _mock_request())

    assert exc_info.value.status_code == 403
    assert "Governance clearance missing — fail-closed" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_ask_impl_non_chatbot_pass_with_governance_allow_still_delivers(monkeypatch, tmp_path):
    """Present governance is unchanged: non-chatbot PASS + governance ALLOW is
    still delivered (the hoisted check must not over-block cleared answers)."""
    _force_factual_lane(monkeypatch)
    monkeypatch.setenv("SCP_MULTI_LLM_CROSSCHECK", "0")
    monkeypatch.setenv("SCP_WEB_FALLBACK", "0")
    monkeypatch.setenv("SCP_DATA_DIR", str(tmp_path / "secdata"))
    monkeypatch.setattr("scp.api_server_parts._ask_impl.get_judge", lambda: _pass_judge("ALLOW"))

    req = AskRequest(question="What is 2+2?", ai_answer="2+2=4", contexts=["2+2=4"])
    response = await _ask_impl(req, _mock_request())

    assert response.verdict == "PASS"
    assert "2+2=4" in str(response.final_answer)


def test_route_decision_bypass_verdict_pass_not_bypassing_in_chat():
    """SEC-R2-02: Chatbot lane in chat.py must not bypass governance with bypass_verdict_pass.

    [F-E2E-2] Field đã XÓA: không thể dựng RouteDecision mang cờ bypass nữa
    (constructor từ chối) — chặt hơn bản cũ (chỉ dựng được rồi kiểm lane).
    """
    with pytest.raises(TypeError):
        RouteDecision(  # type: ignore[call-arg]
            lane=LANE_CHATBOT,
            intent="conversational",
            domain="general",
            confidence=0.9,
            via="keyword",
            bypass_verdict_pass=True,
        )
    route = RouteDecision(
        lane=LANE_CHATBOT,
        intent="conversational",
        domain="general",
        confidence=0.9,
        via="keyword",
    )
    assert route.lane == LANE_CHATBOT
    assert not hasattr(route, "bypass_verdict_pass")

