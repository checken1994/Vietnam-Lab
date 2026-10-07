# SCP CIRCUIT: W16-f5 — carve-out un-starve (W11-f1 verify + domain-based nới).
"""T02/W16 — carve-out realtime-no-tool (W7-e6) hoạt động với route reason
mới sau reorder W11-f1, và không còn starve các đường route domain
weather/finance không mang tag trong reason.

Bằng chứng routing (deterministic, local — chạy TRỰC TIẾP route_question,
không force lane):
  * 'Thời tiết Hà Nội hôm nay thế nào?' → lookup_signal:weather_fact
    (W11-f1 đưa weather_fact/finance_fact đứng trước interrogative_vi —
    trước W11-f1 reason là lookup_signal:interrogative_vi → carve-out
    không bao giờ mở, root-cause W10 q07 withheld đủ 3 run);
  * 'Hà Nội hôm nay có mưa không?' → domain_hint:weather (l0-domain —
    weather keyword miss, domain từ classify_top1) — reason KHÔNG chứa tag
    → carve-out starve trên shape này;
  * 'Cổ phiếu FPT hôm nay thế nào?' → lookup_signal:interrogative_vi +
    domain 'finance' (l0-keyword match rule generic) — shape starve thứ hai.

Hợp đồng W16-f5 (owner duyệt): condition carve-out được nới ĐÚNG mức
domain-based — chấp nhận domain thuộc allowlist ĐÓNG {'weather', 'finance'}
bên cạnh route-reason tag; KHÔNG string-match trên text câu hỏi. Mọi điều
kiện khác giữ nguyên fail-closed: không web fallback, không contexts, không
retrieval triggered, answer là lời TỪ CHỐI tường minh (refusal marker —
claim answer vẫn KHÔNG deliver).

Anti-delivery pins: weather/finance domain + CLAIM answer → withheld
(refusal-only carve-out + detector f4 cùng chặn).

Old-fails/new-passes: e2e deliver (4)-(5) FAIL trên main base (pre-W16:
carve-out starve trên shape domain-hint/keyword-miss + chưa có f4 — xác
nhận bằng worktree main); pin router (1)-(2) và q07 canonical (3) pass cả
trước lẫn sau (chúng pin điều ĐÃ un-starve bởi W11-f1 cho shape canonical —
chống regression); anti-delivery (6) pass cả hai (pin ranh giới KHÔNG ĐỔI).

Không gọi LLM/mạng: judge mock, crosscheck + web fallback tắt qua env
(pattern test_ask_w7_abstain_delivery.py). Router chạy THẬT — đây là điểm
của f5: verify carve-out với route reason/domain THỰC, không phải lane ép.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from scp.api_server_parts._ask_impl import _ask_impl
from scp.api_server_parts.helpers import AskRequest
from scp.runtime.question_router import LANE_FACTUAL, route_question

_ABSTAIN_LABEL = "[unverified — abstain]"
_ESCALATE_WITHHOLD = (
    "[SCP: Answer withheld — không xác minh được câu trả lời (governance: ESCALATE)]"
)

_Q07 = "Thời tiết Hà Nội hôm nay thế nào?"
_Q07_DOMAIN_HINT_SHAPE = "Hà Nội hôm nay có mưa không?"
_FINANCE_KEYWORD_MISS_SHAPE = "Cổ phiếu FPT hôm nay thế nào?"
_FINANCE_CANONICAL = "Giá bitcoin hôm nay bao nhiêu?"

_HONEST_REFUSAL = (
    "Tôi chưa được cập nhật dữ liệu thời gian thực để trả lời câu hỏi này."
)
_CLAIM_ANSWER = "Hà Nội hôm nay nắng 30 độ."


def _mock_request() -> MagicMock:
    mock_request = MagicMock()
    mock_request.client.host = "127.0.0.1"
    mock_request.state = MagicMock()
    mock_request.state.scp_run = None
    mock_request.state.trace_id = "test-w16-f5"
    return mock_request


def _judge(verdict: str, governance: str, final_answer: str = ""):
    class FakeJudge:
        async def judge_with_react_fallback(self, **kwargs):
            return {
                "verdict": verdict,
                "confidence": 0.0,
                "reasoning": "mocked judge",
                "failures": [],
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


# ---------------------------------------------------------------------------
# (1)-(2) Router pins — tiền đề un-starve của carve-out (W11-f1).
# ---------------------------------------------------------------------------
def test_real_router_q07_reason_carries_weather_fact() -> None:
    """W11-f1 verify: q07 canonical route THẬT → LANE_FACTUAL + reason mang
    tag 'weather_fact' — điều kiện reason của carve-out thỏa (substring
    match trên 'lookup_signal:weather_fact'). Trước W11-f1 reason là
    'lookup_signal:interrogative_vi' → carve-out starve (root-cause W10)."""
    decision = route_question(_Q07)
    assert decision.lane == LANE_FACTUAL
    assert "weather_fact" in str(decision.reason)


def test_real_router_finance_reason_carries_finance_fact() -> None:
    """Tương tự cho finance canonical — tag 'finance_fact' trong reason."""
    decision = route_question(_FINANCE_CANONICAL)
    assert decision.lane == LANE_FACTUAL
    assert "finance_fact" in str(decision.reason)


# ---------------------------------------------------------------------------
# (3)-(5) E2E un-starve pins — route THẬT, deliver kèm nhãn.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_q07_real_router_refusal_abstain_delivers_with_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """q07 (W10 withheld đủ 3 run) qua route THẬT + refusal + ABSTAIN →
    deliver 200 kèm nhãn. Trước W16: withheld-ESCALATE (old-fails)."""
    _w16_env(monkeypatch)
    monkeypatch.setattr(
        "scp.api_server_parts._ask_impl.get_judge",
        lambda: _judge("ABSTAIN", "ESCALATE", _HONEST_REFUSAL),
    )

    req = AskRequest(question=_Q07, ai_answer=_HONEST_REFUSAL)
    response = await _ask_impl(req, _mock_request())

    assert response.verdict == "ABSTAIN"
    assert response.governance_decision == "ABSTAIN"
    assert str(response.final_answer).startswith(_ABSTAIN_LABEL)
    assert "chưa được cập nhật" in str(response.final_answer)
    assert "[SCP: Answer withheld" not in str(response.final_answer)


@pytest.mark.asyncio
async def test_domain_hint_weather_shape_delivers_with_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Shape starve (a) — reason 'domain_hint:weather' không mang tag:
    route THẬT 'Hà Nội hôm nay có mưa không?' + refusal + ABSTAIN → deliver
    kèm nhãn. Carve-out mở qua domain-based acceptance (domain 'weather')."""
    decision = route_question(_Q07_DOMAIN_HINT_SHAPE)
    assert decision.lane == LANE_FACTUAL
    assert "weather_fact" not in str(decision.reason), (
        "premise: shape này KHÔNG mang tag trong reason — nếu router đổi, "
        "test này chuyển thành pin reason-tag, không còn phủ đường domain"
    )
    assert str(decision.domain) == "weather"

    _w16_env(monkeypatch)
    monkeypatch.setattr(
        "scp.api_server_parts._ask_impl.get_judge",
        lambda: _judge("ABSTAIN", "ESCALATE", _HONEST_REFUSAL),
    )

    req = AskRequest(question=_Q07_DOMAIN_HINT_SHAPE, ai_answer=_HONEST_REFUSAL)
    response = await _ask_impl(req, _mock_request())

    assert response.verdict == "ABSTAIN"
    assert response.governance_decision == "ABSTAIN"
    assert str(response.final_answer).startswith(_ABSTAIN_LABEL)


@pytest.mark.asyncio
async def test_finance_keyword_miss_domain_finance_delivers_with_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Shape starve (b) — reason generic 'interrogative_vi' nhưng domain
    'finance' (classify_top1): route THẬT 'Cổ phiếu FPT hôm nay thế nào?' +
    refusal + ABSTAIN → deliver kèm nhãn (domain-based acceptance)."""
    decision = route_question(_FINANCE_KEYWORD_MISS_SHAPE)
    assert decision.lane == LANE_FACTUAL
    assert "finance_fact" not in str(decision.reason), (
        "premise: shape này KHÔNG mang tag trong reason"
    )
    assert str(decision.domain) == "finance"

    _w16_env(monkeypatch)
    monkeypatch.setattr(
        "scp.api_server_parts._ask_impl.get_judge",
        lambda: _judge("ABSTAIN", "ESCALATE", _HONEST_REFUSAL),
    )

    req = AskRequest(question=_FINANCE_KEYWORD_MISS_SHAPE, ai_answer=_HONEST_REFUSAL)
    response = await _ask_impl(req, _mock_request())

    assert response.verdict == "ABSTAIN"
    assert response.governance_decision == "ABSTAIN"
    assert str(response.final_answer).startswith(_ABSTAIN_LABEL)


# ---------------------------------------------------------------------------
# Anti-delivery pins — carve-out refusal-only, claim vẫn withheld.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_realtime_claim_answer_still_withheld_real_router(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Anti-delivery: q07 route THẬT + answer CHỨA claim (kèm số liệu) +
    verdict ABSTAIN → KHÔNG deliver — carve-out đòi refusal tường minh,
    detector f4 cũng chặn claim (digit). Fail-closed giữ nguyên."""
    _w16_env(monkeypatch)
    monkeypatch.setattr(
        "scp.api_server_parts._ask_impl.get_judge",
        lambda: _judge("ABSTAIN", "ESCALATE", _CLAIM_ANSWER),
    )

    req = AskRequest(question=_Q07, ai_answer=_CLAIM_ANSWER)
    response = await _ask_impl(req, _mock_request())

    assert response.governance_decision == "ESCALATE"
    assert str(response.final_answer) == _ESCALATE_WITHHOLD
    assert _ABSTAIN_LABEL not in str(response.final_answer)
    assert "nắng 30 độ" not in str(response.final_answer)
