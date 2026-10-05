"""[W8-e1 2026-10-05] q08 stale-fact guard tại boundary /ask (GA.md B1b).

W7-battery run C: "Joe Biden là tổng thống Mỹ hiện tại, nhậm chức 20/1/2021"
được uphold PASS-thuần (deliver nguyên văn) trên question time-signal mà
KHÔNG có evidence nào từ web/data. Root-cause: judge LLM + crosscheck cùng
training cutoff stale (system prompt W8-e1 chỉ là lớp mềm).

Hợp đồng guard (deterministic, tại _ask_impl sau judge, trước boundary):
  * question có time-signal (hiện tại/hiện nay/hôm nay/bây giờ/currently/
    latest) + verdict PASS + KHÔNG có evidence web/data (không web fallback,
    không contexts, không autonomous retrieval) → KHÔNG được PASS-thuần:
      - lane CHATBOT (benign) → ABSTAIN → deliver kèm nhãn '[unverified —
        abstain]' (đường e6, Option A);
      - lane khác (factual) → FAIL + governance ESCALATE → withheld (W3-e1).
  * CÓ evidence (contexts/web fallback/retrieval) → guard tắt, PASS giữ nguyên.
  * Verdict không-PASS (ABSTAIN/FAIL/UNKNOWN) không bị đụng — carve-out W7-e6
    và các pin W3 giữ nguyên.

Không gọi LLM: judge mock, web fallback tắt qua env (pattern
test_ask_w7_abstain_delivery). Anti-placebo: các test (1)-(2) chạy trên code
TRƯỚC W8-e1 sẽ FAIL (PASS-thuần được deliver).
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from scp.api_server_parts._ask_impl import _ask_impl
from scp.api_server_parts.helpers import AskRequest
from scp.runtime.question_router import LANE_CHATBOT, LANE_FACTUAL, RouteDecision

_ABSTAIN_LABEL = "[unverified — abstain]"
_ESCALATE_WITHHOLD = (
    "[SCP: Answer withheld — không xác minh được câu trả lời (governance: ESCALATE)]"
)

_Q08_QUESTION = "Ai là tổng thống Mỹ hiện tại?"
_Q08_STALE_ANSWER = (
    "Joe Biden là tổng thống Mỹ hiện tại, nhậm chức ngày 20/1/2021."
)
_CHATBOT_TIME_Q = "Bạn đang ở đâu bây giờ?"
_CHATBOT_ANSWER = "Mình đang chạy trên máy chủ của bạn đây!"


def _force_lane(monkeypatch: pytest.MonkeyPatch, lane: str, reason: str = "test") -> None:
    monkeypatch.setattr(
        "scp.runtime.question_router.route_question",
        lambda q: RouteDecision(
            intent="LOOKUP" if lane == LANE_FACTUAL else "REASONING",
            domain="general", confidence=0.9,
            via="test", lane=lane, reason=reason,
        ),
    )


def _mock_request() -> MagicMock:
    mock_request = MagicMock()
    mock_request.client.host = "127.0.0.1"
    mock_request.state = MagicMock()
    mock_request.state.scp_run = None
    mock_request.state.trace_id = "test-w8-e1"
    return mock_request


def _judge(verdict: str, governance: str, final_answer: str = ""):
    class FakeJudge:
        async def judge_with_react_fallback(self, **kwargs):
            return {
                "verdict": verdict,
                "confidence": 0.99 if verdict == "PASS" else 0.0,
                "reasoning": "mocked judge",
                "failures": [],
                "evidence": {"governance_decision": governance},
                "slm_responses": [],
                "final_answer": final_answer,
            }

    return FakeJudge()


async def _noop_fact_check(*_a, **_k) -> None:
    return None


def _w8_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCP_MULTI_LLM_CROSSCHECK", "0")
    monkeypatch.setenv("SCP_WEB_FALLBACK", "0")
    monkeypatch.setattr(
        "scp.api_server_parts._ask_impl._async_fact_check", _noop_fact_check
    )


@pytest.mark.asyncio
async def test_q08_factual_time_signal_pass_is_downgraded_and_withheld(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Old-fails/new-passes (q08 shape): factual + time-signal + judge PASS +
    KHÔNG web/data evidence → KHÔNG PASS-thuần: FAIL/ESCALATE, answer stale
    bị withheld. Trước W8-e1: PASS + UPHOLD + 'Joe Biden...' deliver nguyên."""
    _force_lane(monkeypatch, LANE_FACTUAL, reason="lookup_signal:interrogative_vi")
    _w8_env(monkeypatch)
    monkeypatch.setattr(
        "scp.api_server_parts._ask_impl.get_judge",
        lambda: _judge("PASS", "UPHOLD", _Q08_STALE_ANSWER),
    )

    req = AskRequest(question=_Q08_QUESTION, ai_answer=_Q08_STALE_ANSWER)
    response = await _ask_impl(req, _mock_request())

    assert response.verdict == "FAIL", (
        "stale PASS on time-signal question without evidence must not deliver (W8-e1)"
    )
    assert response.governance_decision == "ESCALATE"
    assert str(response.final_answer) == _ESCALATE_WITHHOLD
    assert "Joe Biden" not in str(response.final_answer)
    assert _ABSTAIN_LABEL not in str(response.final_answer)


@pytest.mark.asyncio
async def test_chatbot_time_signal_pass_downgraded_to_labeled_abstain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Đường benign (chatbot lane): time-signal + PASS + không evidence →
    ABSTAIN-delivered kèm nhãn (đường e6), KHÔNG PASS-thuần."""
    _force_lane(monkeypatch, LANE_CHATBOT)
    _w8_env(monkeypatch)
    monkeypatch.setattr(
        "scp.api_server_parts._ask_impl.get_judge",
        lambda: _judge("PASS", "UPHOLD", _CHATBOT_ANSWER),
    )

    req = AskRequest(question=_CHATBOT_TIME_Q, ai_answer=_CHATBOT_ANSWER)
    response = await _ask_impl(req, _mock_request())

    assert response.verdict == "ABSTAIN"
    assert response.governance_decision == "ABSTAIN"
    assert str(response.final_answer).startswith(_ABSTAIN_LABEL)
    assert "Mình đang chạy" in str(response.final_answer), (
        "original answer must be delivered with the abstain label, not replaced"
    )
    assert "[SCP: Answer withheld" not in str(response.final_answer)


@pytest.mark.asyncio
async def test_time_signal_pass_with_provided_evidence_keeps_pass(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Guard CHỈ chặn khi KHÔNG có evidence: contexts được cung cấp (web/data
    evidence) → PASS giữ nguyên, answer deliver bình thường (không hạ chuẩn
    đường có evidence). SCP_DATA_DIR=tmp vì hook PASS ghi ledger."""
    _force_lane(monkeypatch, LANE_FACTUAL, reason="lookup_signal:interrogative_vi")
    _w8_env(monkeypatch)
    monkeypatch.setenv("SCP_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        "scp.api_server_parts._ask_impl.get_judge",
        lambda: _judge("PASS", "UPHOLD", _Q08_STALE_ANSWER),
    )

    req = AskRequest(
        question=_Q08_QUESTION,
        ai_answer=_Q08_STALE_ANSWER,
        contexts=["[web evidence] Certification: president took office 2021-01-20 (retrieved today)"],
    )
    response = await _ask_impl(req, _mock_request())

    assert response.verdict == "PASS"
    assert response.governance_decision == "UPHOLD"
    assert "Joe Biden" in str(response.final_answer)
    assert _ABSTAIN_LABEL not in str(response.final_answer)



@pytest.mark.asyncio
async def test_time_signal_web_fallback_evidence_keeps_pass(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Evidence tu web fallback (web_fallback_used=True) cung tat guard —
    khong chan duong da co du lieu tuoi. Hermetic: LLM generation fail-fast
    (gateway raise) -> public-web fallback stub tra ket qua; autonomous
    retriever stub (khong mang)."""
    from types import SimpleNamespace

    _force_lane(monkeypatch, LANE_FACTUAL, reason="lookup_signal:interrogative_vi")
    monkeypatch.setenv("SCP_MULTI_LLM_CROSSCHECK", "0")
    monkeypatch.setenv("SCP_WEB_FALLBACK", "1")
    monkeypatch.setenv("SCP_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        "scp.api_server_parts._ask_impl._async_fact_check", _noop_fact_check
    )
    monkeypatch.setattr(
        "scp.api_server_parts._ask_impl.get_judge",
        lambda: _judge("PASS", "UPHOLD", _Q08_STALE_ANSWER),
    )

    def _broken_gateway():
        raise RuntimeError("gateway unavailable (fault injection)")

    monkeypatch.setattr("scp.llm_gateway.get_gateway", _broken_gateway)

    async def _fake_web_search(_q, max_results=6):
        return {
            "success": True,
            "results": [
                {"title": "t", "snippet": "president took office 2021", "url": "https://example.com/x"}
            ],
        }

    monkeypatch.setattr(
        "scp.api_server_parts._ask_impl.InternetSearch",
        lambda timeout=None: SimpleNamespace(search=_fake_web_search),
    )

    class _StubRetriever:
        async def retrieve(self, **_kwargs):
            return {"retrieval_triggered": False}

    monkeypatch.setattr(
        "scp.knowledge.domain_knowledge.AutonomousEvidenceRetriever", _StubRetriever
    )

    req = AskRequest(question=_Q08_QUESTION, ai_answer="")
    response = await _ask_impl(req, _mock_request())

    assert response.verdict == "PASS", (
        "PASS backed by web-fallback evidence must not be downgraded (W8-e1)"
    )
    assert response.governance_decision == "UPHOLD"
    assert response.web_fallback_used is True
    assert "Joe Biden" in str(response.final_answer)
