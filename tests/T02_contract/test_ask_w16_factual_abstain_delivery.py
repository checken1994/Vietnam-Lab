# SCP CIRCUIT: W16-f4 — factual-ABSTAIN-delivery (Option-A scope expansion).
"""T02/W16 — mở deliver-with-label sang lane FACTUAL khi answer honest-abstain.

Owner duyệt scope expansion Wave 16 ("1 và 2"): factual lane + verdict
ABSTAIN + answer KHÔNG chứa factual claim có thể refuted (detector
`is_honest_abstain_answer` — refusal/conv marker + claim-cue guard BC-1/W8,
URL/digit bị chặn) → deliver 200 kèm nhãn '[unverified — abstain]' +
governance 'ABSTAIN' + marker audit, thay vì withhold im lặng.

Ranh giới anti-lộng NGHIÊM NGẶT (các pin bắt buộc trong file này):
  * (1) answer CHỨA factual claim + chỉ thiếu opinion độc lập (crosscheck
    missing/timeout — shape verdict ABSTAIN + ESCALATE có abstain_reasons
    'crosscheck_consensus_missing') → VẪN withhold fail-closed; claim được
    verify phải đi đường crosscheck agree-PASS → PASS-thuần;
  * (a) crosscheck agree-FAIL + claim → verdict FAIL/ESCALATE → withheld
    NGUYÊN (answer bị refuted ≠ unverified);
  * (b) KILL path nguyên vẹn — factual + governance KILL → withheld nhãn
    'Governance KILL', không deliver, không nhãn abstain;
  * (c) label bắt buộc trên MỌI ABSTAIN-delivered — shape ABSTAIN không đủ
    điều kiện deliver (claim / governance lạ) bị withhold fail-closed,
    KHÔNG được rơi qua boundary với answer gốc không nhãn;
  * (d) BC-1 claim-wrapper probes (refusal marker bọc claim assertion) →
    detector False → không deliver;
  * (4) W8-e1 time-signal guard giữ nguyên — factual + time-signal + không
    evidence tươi + judge PASS → FAIL/withheld (f4 không mở lại lỗ stale).

Old-fails/new-passes: (1),(2) ở dưới FAIL trên code TRƯỚC f4 (factual
honest-abstain bị withheld); các pin anti-lộng PASS trên cả trước lẫn sau
f4 (chúng pin ranh giới KHÔNG ĐỔI).

Không gọi LLM/mạng: judge mock, crosscheck + web fallback tắt qua env,
lane ép tường minh qua monkeypatch route_question (pattern
test_ask_w7_abstain_delivery.py).
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from scp.api_server_parts._ask_impl import _ask_impl
from scp.api_server_parts.helpers import AskRequest
from scp.runtime.question_router import LANE_FACTUAL, RouteDecision

_ABSTAIN_LABEL = "[unverified — abstain]"
_ESCALATE_WITHHOLD = (
    "[SCP: Answer withheld — không xác minh được câu trả lời (governance: ESCALATE)]"
)

# Lane FACTUAL thường (không phải realtime weather/finance) — đúng shape
# trước đây bị pin là withheld, nay mở cho honest-abstain theo f4.
_FACTUAL_REASON = "lookup_signal:interrogative_vi"
_FACTUAL_Q = "Ai là người sáng lập đại học Harvard?"

_HONEST_REFUSAL = (
    "Tôi chưa được cập nhật dữ liệu thời gian thực để trả lời câu hỏi này."
)
_HONEST_CONV = "Xin chào! Mình khỏe, cảm ơn bạn đã hỏi."
_CLAIM_ANSWER = "Donald Trump là tổng thống Mỹ."
# BC-1 claim-wrapper probe: refusal marker theo sau bởi assertion copula.
_CLAIM_WRAPPED = "Tôi không có dữ liệu, nhưng thủ đô Pháp là Paris."


def _force_factual(monkeypatch: pytest.MonkeyPatch, reason: str = _FACTUAL_REASON) -> None:
    monkeypatch.setattr(
        "scp.runtime.question_router.route_question",
        lambda q: RouteDecision(
            intent="LOOKUP",
            domain="general",
            confidence=0.9,
            via="test",
            lane=LANE_FACTUAL,
            reason=reason,
        ),
    )


def _mock_request() -> MagicMock:
    mock_request = MagicMock()
    mock_request.client.host = "127.0.0.1"
    mock_request.state = MagicMock()
    mock_request.state.scp_run = None
    mock_request.state.trace_id = "test-w16-f4"
    return mock_request


def _judge(verdict: str, governance: str, final_answer: str = "", failures: list | None = None):
    class FakeJudge:
        async def judge_with_react_fallback(self, **kwargs):
            return {
                "verdict": verdict,
                "confidence": 0.0,
                "reasoning": "mocked judge",
                "failures": failures or [],
                "evidence": {"governance_decision": governance},
                "slm_responses": [],
                "final_answer": final_answer,
            }

    return FakeJudge()


async def _noop_fact_check(*_a, **_k) -> None:
    return None


def _w16_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCP_MULTI_LLM_CROSSCHECK", "0")
    monkeypatch.setenv("SCP_WEB_FALLBACK", "0")
    monkeypatch.setattr(
        "scp.api_server_parts._ask_impl._async_fact_check", _noop_fact_check
    )


@pytest.mark.asyncio
async def test_factual_honest_refusal_abstain_delivers_with_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Old-fails/new-passes (f4 acceptance): factual lane thường + verdict
    ABSTAIN + answer refusal trung thực (không claim) → deliver 200 kèm nhãn
    + governance ABSTAIN. Trước f4: withheld-ESCALATE (old-fails)."""
    _force_factual(monkeypatch)
    _w16_env(monkeypatch)
    monkeypatch.setattr(
        "scp.api_server_parts._ask_impl.get_judge",
        lambda: _judge("ABSTAIN", "ESCALATE", _HONEST_REFUSAL),
    )

    req = AskRequest(question=_FACTUAL_Q, ai_answer=_HONEST_REFUSAL)
    response = await _ask_impl(req, _mock_request())

    assert response.verdict == "ABSTAIN"
    assert response.governance_decision == "ABSTAIN"
    assert str(response.final_answer).startswith(_ABSTAIN_LABEL)
    assert "chưa được cập nhật" in str(response.final_answer), (
        "original honest-abstain answer must be delivered with the label, not replaced"
    )
    assert "[SCP: Answer withheld" not in str(response.final_answer)


@pytest.mark.asyncio
async def test_factual_honest_conv_abstain_delivers_with_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Shape honest-abstain thứ hai (conv marker — không claim cue): factual
    lane + ABSTAIN → deliver kèm nhãn. Trước f4: withheld (old-fails)."""
    _force_factual(monkeypatch)
    _w16_env(monkeypatch)
    monkeypatch.setattr(
        "scp.api_server_parts._ask_impl.get_judge",
        lambda: _judge("ABSTAIN", "ESCALATE", _HONEST_CONV),
    )

    req = AskRequest(question=_FACTUAL_Q, ai_answer=_HONEST_CONV)
    response = await _ask_impl(req, _mock_request())

    assert response.verdict == "ABSTAIN"
    assert response.governance_decision == "ABSTAIN"
    assert str(response.final_answer).startswith(_ABSTAIN_LABEL)
    assert "Mình khỏe" in str(response.final_answer)
    assert "[SCP: Answer withheld" not in str(response.final_answer)


@pytest.mark.asyncio
async def test_factual_claim_consensus_missing_stays_withheld(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hợp đồng (1): answer CHỨA factual claim + chỉ thiếu opinion độc lập
    (shape thật của crosscheck-missing: verdict ABSTAIN + ESCALATE + marker
    'crosscheck_consensus_missing') → VẪN withhold fail-closed. Claim chưa
    verify KHÔNG được deliver kể cả với verdict ABSTAIN."""
    _force_factual(monkeypatch)
    _w16_env(monkeypatch)
    monkeypatch.setattr(
        "scp.api_server_parts._ask_impl.get_judge",
        lambda: _judge(
            "ABSTAIN",
            "ESCALATE",
            _CLAIM_ANSWER,
            failures=["semantic_judge_unavailable"],
        ),
    )

    req = AskRequest(question=_FACTUAL_Q, ai_answer=_CLAIM_ANSWER)
    response = await _ask_impl(req, _mock_request())

    assert response.verdict == "ABSTAIN"  # judge-level verdict giữ nguyên
    assert response.governance_decision == "ESCALATE"
    assert str(response.final_answer) == _ESCALATE_WITHHOLD
    assert _ABSTAIN_LABEL not in str(response.final_answer)
    assert "Donald Trump" not in str(response.final_answer)


@pytest.mark.asyncio
async def test_factual_claim_agree_fail_stays_withheld(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pin (a): crosscheck agree-FAIL + claim → verdict FAIL/ESCALATE (shape
    thật của agree-FAIL trên claim) → withheld NGUYÊN — answer bị refuted
    không được deliver dưới bất kỳ nhãn nào."""
    _force_factual(monkeypatch)
    _w16_env(monkeypatch)
    monkeypatch.setattr(
        "scp.api_server_parts._ask_impl.get_judge",
        lambda: _judge(
            "FAIL",
            "ESCALATE",
            _CLAIM_ANSWER,
            failures=["semantic_judge_fail"],
        ),
    )

    req = AskRequest(question=_FACTUAL_Q, ai_answer=_CLAIM_ANSWER)
    response = await _ask_impl(req, _mock_request())

    assert response.verdict == "FAIL"
    assert response.governance_decision == "ESCALATE"
    # FAIL + ESCALATE trên benign rơi vào nhánh W3-e1 (message withhold phân
    # biệt được) TRƯỚC nhánh '[SCP: Answer withheld]' generic — khớp pin
    # test_ask_w3_escalate_withhold hiện có.
    assert str(response.final_answer) == _ESCALATE_WITHHOLD
    assert _ABSTAIN_LABEL not in str(response.final_answer)
    assert "Donald Trump" not in str(response.final_answer)


@pytest.mark.asyncio
async def test_factual_bc1_claim_wrapper_probe_not_delivered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pin (d): BC-1 claim-wrapper probe — refusal marker theo sau assertion
    copula ('... nhưng thủ đô Pháp là Paris.') → detector False → KHÔNG
    deliver, claim bọc wrapper không lọt qua nhãn abstain."""
    _force_factual(monkeypatch)
    _w16_env(monkeypatch)
    monkeypatch.setattr(
        "scp.api_server_parts._ask_impl.get_judge",
        lambda: _judge("ABSTAIN", "ESCALATE", _CLAIM_WRAPPED),
    )

    req = AskRequest(question=_FACTUAL_Q, ai_answer=_CLAIM_WRAPPED)
    response = await _ask_impl(req, _mock_request())

    assert response.governance_decision == "ESCALATE"
    assert str(response.final_answer) == _ESCALATE_WITHHOLD
    assert _ABSTAIN_LABEL not in str(response.final_answer)
    assert "Paris" not in str(response.final_answer)


@pytest.mark.asyncio
async def test_factual_kill_path_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pin (b): KILL path nguyên vẹn trên factual — governance KILL → rơi vào
    _is_true_security_threat → withheld nhãn 'Governance KILL', KHÔNG deliver,
    KHÔNG nhãn abstain, governance không bị đổi thành ABSTAIN."""
    _force_factual(monkeypatch)
    _w16_env(monkeypatch)
    monkeypatch.setattr(
        "scp.api_server_parts._ask_impl.get_judge",
        lambda: _judge("FAIL", "KILL", _HONEST_REFUSAL),
    )

    req = AskRequest(question=_FACTUAL_Q, ai_answer=_HONEST_REFUSAL)
    response = await _ask_impl(req, _mock_request())

    assert response.verdict == "FAIL"
    assert response.governance_decision == "KILL"
    assert str(response.final_answer) == "[SCP: Answer withheld — Governance KILL]"
    assert _ABSTAIN_LABEL not in str(response.final_answer)


@pytest.mark.asyncio
async def test_factual_abstain_without_delivery_shape_is_withheld_unlabeled_hole_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pin (c): label bắt buộc trên mọi ABSTAIN-delivered — shape ABSTAIN mà
    KHÔNG thỏa đường deliver (claim answer) với governance không phải
    ESCALATE/DEGRADED (shape lạ) KHÔNG được rơi qua boundary với answer gốc
    không nhãn. Trước f4: fall-through deliver answer gốc KHÔNG nhãn
    (old-fails); sau f4: withheld fail-closed."""
    _force_factual(monkeypatch)
    _w16_env(monkeypatch)
    monkeypatch.setattr(
        "scp.api_server_parts._ask_impl.get_judge",
        lambda: _judge("ABSTAIN", "ABSTAIN", _CLAIM_ANSWER),
    )

    req = AskRequest(question=_FACTUAL_Q, ai_answer=_CLAIM_ANSWER)
    response = await _ask_impl(req, _mock_request())

    assert response.verdict == "ABSTAIN"
    assert str(response.final_answer) == "[SCP: Answer withheld]"
    assert _ABSTAIN_LABEL not in str(response.final_answer)
    assert "Donald Trump" not in str(response.final_answer)


@pytest.mark.asyncio
async def test_factual_time_signal_pass_still_withheld_w8_guard_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hợp đồng (4): W8-e1 time-signal guard giữ nguyên — factual + câu hỏi
    time-signal + không evidence tươi + judge PASS (dù answer là honest
    abstain) → FAIL/ESCALATE → withheld. f4 KHÔNG mở lại lỗ stale-fact."""
    _force_factual(monkeypatch, reason="lookup_signal:interrogative_vi")
    _w16_env(monkeypatch)
    monkeypatch.setattr(
        "scp.api_server_parts._ask_impl.get_judge",
        lambda: _judge("PASS", "UPHOLD", _HONEST_REFUSAL),
    )

    req = AskRequest(question="Ai là tổng thống Mỹ hiện tại?", ai_answer=_HONEST_REFUSAL)
    response = await _ask_impl(req, _mock_request())

    assert response.verdict == "FAIL"
    assert response.governance_decision == "ESCALATE"
    assert str(response.final_answer) == _ESCALATE_WITHHOLD
    assert _ABSTAIN_LABEL not in str(response.final_answer)
