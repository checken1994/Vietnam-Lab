"""[W7-e6] benign-ABSTAIN-delivery — Option A (GA.md B1b, owner duyệt).

Root-cause W6-RE: câu benign không verify được (chào hỏi q06, thời tiết
realtime q07) bị judge chấm FAIL (pre-e5) → withheld hoàn toàn.

Hợp đồng sau e5+e6:
  * judge phát verdict ABSTAIN (answer không chứa factual claim / consensus
    missing) — governance ESCALATE ở judge (audit giữ nguyên);
  * boundary (_ask_impl): lane CHATBOT (hoặc realtime-no-tool: factual
    weather/finance KHÔNG có tool/data nào trả dữ liệu + answer là lời từ
    chối tường minh) + ABSTAIN → deliver 200 với final_answer = answer gốc +
    nhãn đầu dòng '[unverified — abstain]' + governance ghi 'ABSTAIN';
  * Lane FACTUAL thường + mọi verdict không PASS → giữ nguyên fail-closed
    (withheld) — CHỐNG LỘN LANE: factual-ABSTAIN vẫn withheld, KHÔNG deliver;
  * KILL path nguyên vẹn (governance KILL → 403, nhãn security không đổi);
  * kernel adapter chỉ chấp nhận clearance ABSTAIN khi ĐỦ shape:
    verdict ABSTAIN + governance ABSTAIN + answer mang nhãn '[unverified —
    abstain]' (marker audit) — thiếu nhãn → fail-closed như trước.

Không gọi LLM: judge bị mock, gateway/web fallback tắt qua env, lane ép tường
minh qua monkeypatch route_question (pattern test_ask_w3_escalate_withhold).
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

_CHATBOT_Q = "hi"
_FACTUAL_Q = "Ai là tổng thống Mỹ hiện tại?"
_REALTIME_Q = "Thời tiết Hà Nội hôm nay thế nào?"
_REFUSAL_ANSWER = (
    "Tôi không thể xác minh thời tiết hiện tại vì không có dữ liệu thời gian thực."
)


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
    mock_request.state.trace_id = "test-w7-e6"
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


def _w7_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCP_MULTI_LLM_CROSSCHECK", "0")
    monkeypatch.setenv("SCP_WEB_FALLBACK", "0")
    monkeypatch.setattr(
        "scp.api_server_parts._ask_impl._async_fact_check", _noop_fact_check
    )


@pytest.mark.asyncio
async def test_chatbot_abstain_delivers_200_with_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """e6 acceptance (1): chatbot + ABSTAIN → 200 + nhãn '[unverified —
    abstain]' đầu dòng + governance ghi 'ABSTAIN'. Trước e6: verdict ABSTAIN
    + governance ESCALATE rơi vào nhánh withhold → final_answer bị thay bằng
    '[SCP: Answer withheld — ...]' (old-fails)."""
    _force_lane(monkeypatch, LANE_CHATBOT)
    _w7_env(monkeypatch)
    monkeypatch.setattr(
        "scp.api_server_parts._ask_impl.get_judge",
        lambda: _judge("ABSTAIN", "ESCALATE", "Chào bạn! Mình khỏe, cảm ơn bạn đã hỏi."),
    )

    req = AskRequest(question=_CHATBOT_Q, ai_answer="Chào bạn! Mình khỏe, cảm ơn bạn đã hỏi.")
    response = await _ask_impl(req, _mock_request())

    assert response.verdict == "ABSTAIN"
    assert response.governance_decision == "ABSTAIN"
    assert str(response.final_answer).startswith(_ABSTAIN_LABEL)
    assert "Chào bạn! Mình khỏe" in str(response.final_answer), (
        "original answer must be delivered (with the abstain label), not replaced"
    )
    # Marker audit: withheld-message class KHÔNG được xuất hiện trên đường deliver.
    assert "[SCP: Answer withheld" not in str(response.final_answer)


@pytest.mark.asyncio
async def test_factual_abstain_stays_withheld_no_delivery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CHỐNG LỘN LANE (e6 acceptance 2): factual lane (không phải realtime
    no-tool) + verdict ABSTAIN → vẫn withheld (fail-closed), KHÔNG deliver,
    governance không bị đổi thành ABSTAIN."""
    _force_lane(monkeypatch, LANE_FACTUAL, reason="lookup_signal:interrogative_vi")
    _w7_env(monkeypatch)
    monkeypatch.setattr(
        "scp.api_server_parts._ask_impl.get_judge",
        lambda: _judge("ABSTAIN", "ESCALATE", "Donald Trump là tổng thống."),
    )

    req = AskRequest(question=_FACTUAL_Q, ai_answer="Donald Trump là tổng thống.")
    response = await _ask_impl(req, _mock_request())

    assert response.verdict == "ABSTAIN"  # judge-level verdict giữ nguyên
    assert response.governance_decision == "ESCALATE"
    assert str(response.final_answer) == _ESCALATE_WITHHOLD
    assert _ABSTAIN_LABEL not in str(response.final_answer)
    assert "Donald Trump" not in str(response.final_answer)


@pytest.mark.asyncio
async def test_realtime_no_tool_refusal_abstain_delivers_with_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Realtime-no-tool carve-out (q07 shape): factual weather + không có
    tool/data nào trả dữ liệu + answer là lời từ chối tường minh + ABSTAIN →
    deliver 200 kèm nhãn thay vì withhold rỗng."""
    _force_lane(monkeypatch, LANE_FACTUAL, reason="lookup_signal:weather_fact")
    _w7_env(monkeypatch)
    monkeypatch.setattr(
        "scp.api_server_parts._ask_impl.get_judge",
        lambda: _judge("ABSTAIN", "ESCALATE", _REFUSAL_ANSWER),
    )

    req = AskRequest(question=_REALTIME_Q, ai_answer=_REFUSAL_ANSWER)
    response = await _ask_impl(req, _mock_request())

    assert response.verdict == "ABSTAIN"
    assert response.governance_decision == "ABSTAIN"
    assert str(response.final_answer).startswith(_ABSTAIN_LABEL)
    assert "không thể xác minh" in str(response.final_answer)


@pytest.mark.asyncio
async def test_realtime_claim_answer_never_gets_abstain_delivery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Realtime carve-out CHỈ cho refusal: nếu 'ABSTAIN' xuất hiện trên câu
    realtime mà answer KHÔNG phải lời từ chối tường minh (claim shape — mô
    phỏng consensus-missing trên claim) → KHÔNG deliver (fail-closed)."""
    _force_lane(monkeypatch, LANE_FACTUAL, reason="lookup_signal:weather_fact")
    _w7_env(monkeypatch)
    monkeypatch.setattr(
        "scp.api_server_parts._ask_impl.get_judge",
        lambda: _judge("ABSTAIN", "ESCALATE", "Hà Nội hôm nay nắng 30 độ."),
    )

    req = AskRequest(question=_REALTIME_Q, ai_answer="Hà Nội hôm nay nắng 30 độ.")
    response = await _ask_impl(req, _mock_request())

    assert response.governance_decision == "ESCALATE"
    assert str(response.final_answer) == _ESCALATE_WITHHOLD
    assert _ABSTAIN_LABEL not in str(response.final_answer)
    assert "nắng 30 độ" not in str(response.final_answer)


@pytest.mark.asyncio
async def test_chatbot_kill_path_unchanged_withhold_kill_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """KILL path nguyên vẹn (e6 acceptance 3): chatbot + governance KILL →
    rơi vào _is_true_security_threat (gov == 'KILL') → 200 + withheld với
    nhãn 'Governance KILL' — đúng hợp đồng W3-e1 được pin trong
    tests/integration/test_critical_chain_severed.py; e6 KHÔNG deliver,
    KHÔNG gắn nhãn abstain, KHÔNG đổi governance."""
    _force_lane(monkeypatch, LANE_CHATBOT)
    _w7_env(monkeypatch)
    monkeypatch.setattr(
        "scp.api_server_parts._ask_impl.get_judge",
        lambda: _judge("FAIL", "KILL", "Chào bạn!"),
    )

    req = AskRequest(question=_CHATBOT_Q, ai_answer="Chào bạn!")
    response = await _ask_impl(req, _mock_request())

    assert response.verdict == "FAIL"
    assert response.governance_decision == "KILL"
    assert str(response.final_answer) == "[SCP: Answer withheld — Governance KILL]"
    assert _ABSTAIN_LABEL not in str(response.final_answer)


# ---------------------------------------------------------------------------
# Kernel adapter clearance — CHỈ shape labeled-abstain đủ điều kiện.
# ---------------------------------------------------------------------------
def _adapter_stub():
    from types import SimpleNamespace

    return SimpleNamespace(_lookup_fork_self_certified=lambda data: True)


def _adapter_response(labeled: bool, lane: str, question: str) -> tuple[MagicMock, dict, dict]:
    req = MagicMock()
    req.question = question
    req.contexts = []
    req.retrieved_context = ""
    req.lane = lane
    answer = (
        f"{_ABSTAIN_LABEL} Chào bạn! Mình khỏe." if labeled else "Chào bạn! Mình khỏe."
    )
    response = {
        "final_answer": answer,
        "verdict": "ABSTAIN",
        "governance_decision": "ABSTAIN",
        "lane": lane,
        "evidence": {"judge_evaluated": True},
        "trace_id": "trace-w7-e6",
    }
    return req, response, {"task_id": "task-w7-e6"}


@pytest.mark.asyncio
async def test_adapter_accepts_labeled_abstain_delivery_chatbot_lane() -> None:
    """Chatbot lane: verdict ABSTAIN + governance ABSTAIN + nhãn → VERIFIED."""
    from scp.ask_kernel_adapter import AskKernelAdapter

    req, response, task = _adapter_response(True, "LANE_CHATBOT", "hi")
    verification = await AskKernelAdapter.verify_response(_adapter_stub(), req, response, task)
    assert verification["verdict"] == "VERIFIED"
    assert verification["failures"] == []


@pytest.mark.asyncio
async def test_adapter_rejects_unlabeled_abstain_fail_closed() -> None:
    """Anti-lộng kernel: ABSTAIN + governance ABSTAIN NHƯNG answer không mang
    nhãn '[unverified — abstain]' → CONTRADICTED (fail-closed giữ nguyên).
    Trước e6 mọi shape ABSTAIN đều bị withhold; sau e6 CHỈ shape có nhãn
    được thông qua — shape không nhãn vẫn chặn (không nới lỏng class khác)."""
    from scp.ask_kernel_adapter import AskKernelAdapter

    req, response, task = _adapter_response(False, "LANE_CHATBOT", "hi")
    verification = await AskKernelAdapter.verify_response(_adapter_stub(), req, response, task)
    assert verification["verdict"] == "CONTRADICTED"
    assert "judge_pass" in verification["failures"]
    assert "governance_uphold" in verification["failures"]


@pytest.mark.asyncio
async def test_adapter_accepts_labeled_abstain_realtime_factual_lane() -> None:
    """Factual realtime (đường e6 carve-out): labeled abstain delivery →
    VERIFIED ở kernel (verdict_pass + governance_uphold qua
    _abstain_delivery); factual thường với verdict không PASS vẫn withhold
    ở boundary nên không bao giờ tới đây với shape lạ."""
    from scp.ask_kernel_adapter import AskKernelAdapter

    req, response, task = _adapter_response(True, "LANE_FACTUAL", _REALTIME_Q)
    verification = await AskKernelAdapter.verify_response(_adapter_stub(), req, response, task)
    assert verification["verdict"] == "VERIFIED"
    assert verification["failures"] == []
