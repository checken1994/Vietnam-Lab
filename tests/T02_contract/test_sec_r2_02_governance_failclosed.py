"""Test SEC-R2-02: Fail-closed enforcement on missing governance and removal of bypass_verdict_pass."""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from scp.api_server_parts._ask_impl import _ask_impl
from scp.api_server_parts.helpers import AskRequest
from scp.runtime.question_router import LANE_CHATBOT, RouteDecision


@pytest.mark.asyncio
async def test_ask_impl_fails_closed_when_governance_missing(monkeypatch):
    """SEC-R2-02: When governance decision is empty or missing, _ask_impl must raise 403."""
    req = AskRequest(question="Xin chào bạn", ai_answer="Chào bạn!")
    mock_request = MagicMock()
    mock_request.client.host = "127.0.0.1"
    mock_request.state = MagicMock()
    mock_request.state.scp_run = None
    mock_request.state.trace_id = "test-sec-r2-02"

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
    mock_request = MagicMock()
    mock_request.client.host = "127.0.0.1"
    mock_request.state = MagicMock()
    mock_request.state.scp_run = None
    mock_request.state.trace_id = "test-sec-r2-02"

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


def test_route_decision_bypass_verdict_pass_not_bypassing_in_chat():
    """SEC-R2-02: Chatbot lane in chat.py must not bypass governance with bypass_verdict_pass."""
    route = RouteDecision(
        lane=LANE_CHATBOT,
        intent="conversational",
        domain="general",
        confidence=0.9,
        via="keyword",
        bypass_verdict_pass=True,
    )
    assert route.lane == LANE_CHATBOT
