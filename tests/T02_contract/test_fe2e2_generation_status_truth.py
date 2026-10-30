"""[F-E2E-2 · 2026-10-30] /ask phải nói ĐÚNG SỰ THẬT khi LLM không sinh được đáp án.

Root cause (inbox e2e-falsepositive-analysis.md RC-1/RC-2, đọc lại trên main):
- ``LLMGateway.chat`` trả ``(None, "none")`` khi pool provider rỗng — KHÔNG raise.
- ``GenerationStage`` lặng lẽ để ``ai_answer`` rỗng, không log/không trạng thái.
- Judge chấm đáp án rỗng ⇒ FAIL/ESCALATE ⇒ DeliveryRuleEngine dán nhãn
  "không xác minh được câu trả lời" — người đọc tưởng hệ thống ĐÃ kiểm và thấy
  sai, trong khi thực tế KHÔNG có đáp án nào để kiểm.
- ``except Exception`` của generation nuốt cả lỗi lập trình (TypeError…) như thể
  "LLM lỗi" rồi chạy web fallback.

Hợp đồng pin ở đây (hermetic: fake gateway, fake judge, web search bị cấm):
1. Trạng thái generation tường minh trên ctx: ``llm`` | ``provided`` |
   ``llm_unavailable`` | ``gateway_error`` | ``internal_error``.
2. (a) không-có-đáp-án ≠ (b) có-đáp-án-nhưng-không-xác-minh-được: (a) mang code
   ``ERR_GENERATION_UNAVAILABLE`` + nhãn riêng; (b) giữ nguyên nhãn cũ.
   Không câu nào chuyển từ withheld sang delivered.
3. Lỗi lập trình trong generation ⇒ ``internal_error``, log ERROR, KHÔNG web
   fallback, KHÔNG dùng context/snippet làm đáp án ⇒ withheld fail-closed
   (``ERR_GENERATION_INTERNAL``). Lỗi gateway/provider giữ đường fallback cũ.
"""
from __future__ import annotations

import logging
from typing import Any

import pytest
from starlette.requests import Request as StarletteRequest

import scp.api_server_parts._ask_impl as ask_impl_mod
from scp.api_server_parts._ask_impl import _ask_impl
from scp.api_server_parts.helpers import AskRequest
from scp.api_server_parts.pipeline.context import AskPipelineContext
from scp.api_server_parts.pipeline.delivery_rules import DeliveryRuleEngine
from scp.api_server_parts.pipeline.generation_stage import GenerationStage
from scp.runtime.question_router import LANE_CHATBOT, LANE_FACTUAL, RouteDecision

# Hằng số được viết cứng (KHÔNG import từ code sản phẩm) — để test đỏ trên base
# vì đúng lý do hành vi, không vì ImportError.
UNGENERATED_LABEL = "[SCP: Answer withheld — chưa sinh được câu trả lời (LLM không khả dụng)]"
INTERNAL_LABEL = "[SCP: Answer withheld — lỗi nội bộ khi sinh câu trả lời (fail-closed)]"
UNVERIFIED_LABEL = "[SCP: Answer withheld — không xác minh được câu trả lời (governance: ESCALATE)]"

BENIGN_E2E = [
    "Viết 2 câu thơ về con mèo",  # Q6
    "Bạn tên là gì?",  # Q7
    "Giải thích cách hoạt động của khóa API",  # Q8
    "Giúp tôi hướng dẫn cấu hình khóa quản trị an toàn cho máy chủ của tôi",  # Q9
]


# ---------------------------------------------------------------------------
# Test doubles (no LLM, no network)
# ---------------------------------------------------------------------------
class _NoneGateway:
    """Mô phỏng đúng ``client.py``: pool rỗng ⇒ ``(None, "none")``, không raise."""

    def __init__(self) -> None:
        self.calls = 0

    async def chat(self, *_a: Any, **_k: Any) -> tuple[str | None, str]:
        self.calls += 1
        return None, "none"


class _TextGateway:
    def __init__(self, text: str) -> None:
        self.text = text

    async def chat(self, *_a: Any, **_k: Any) -> tuple[str | None, str]:
        return self.text, "fake-provider"


class _RaisingGateway:
    def __init__(self, exc: BaseException) -> None:
        self.exc = exc

    async def chat(self, *_a: Any, **_k: Any) -> tuple[str | None, str]:
        raise self.exc


class _ForbiddenSearch:
    """Web search bị cấm: nếu bị gọi ⇒ ghi nhận + lỗi (không mạng)."""

    instances = 0

    def __init__(self, *_a: Any, **_k: Any) -> None:
        type(self).instances += 1

    async def search(self, *_a: Any, **_k: Any) -> dict[str, Any]:
        raise RuntimeError("hermetic test: web search forbidden")


class _EmptyAwareJudge:
    """Judge giả bám hợp đồng judge thật trên đáp án rỗng (tier1 REJECT_EMPTY ⇒
    FAIL/ESCALATE). Đáp án KHÁC rỗng ⇒ FAIL/ESCALATE luôn (judge không xác minh
    được) — để mọi nhánh withhold đều được đi qua mà không cần LLM thật."""

    def __init__(self) -> None:
        self.seen_answers: list[str] = []

    async def judge_with_react_fallback(self, **kwargs: Any) -> dict[str, Any]:
        answer = str(kwargs.get("ai_answer") or "")
        self.seen_answers.append(answer)
        return {
            "verdict": "FAIL",
            "confidence": 0.0,
            "reasoning": "Verification failed on a benign request",
            "failures": ["REJECT_EMPTY"] if not answer.strip() else ["semantic_judge_fail"],
            "evidence": {"governance_decision": "ESCALATE"},
            "slm_responses": [],
            "final_answer": answer,
        }


class _PassingJudge:
    """Judge giả cho PASS/ALLOW mọi đáp án khác rỗng — dùng để chứng minh
    fail-closed của internal_error KHÔNG phụ thuộc vào việc judge chặn."""

    async def judge_with_react_fallback(self, **kwargs: Any) -> dict[str, Any]:
        answer = str(kwargs.get("ai_answer") or "")
        if not answer.strip():
            return {
                "verdict": "FAIL", "confidence": 0.0, "reasoning": "empty",
                "failures": ["REJECT_EMPTY"], "evidence": {"governance_decision": "ESCALATE"},
                "slm_responses": [], "final_answer": "",
            }
        return {
            "verdict": "PASS", "confidence": 0.95, "reasoning": "ok", "failures": [],
            "evidence": {"governance_decision": "ALLOW"}, "slm_responses": [],
            "final_answer": answer,
        }


async def _noop_fact_check(*_a: Any, **_k: Any) -> None:
    return None


def _mock_request() -> StarletteRequest:
    scope = {
        "type": "http", "method": "POST", "path": "/ask",
        "headers": [(b"host", b"127.0.0.1:8000")], "query_string": b"",
        "client": ("127.0.0.1", 54321), "server": ("testserver", 80), "scheme": "http",
    }
    req = StarletteRequest(scope)
    req.state.scp_run = None
    req.state.trace_id = "test-fe2e2"
    return req


@pytest.fixture
def hermetic(monkeypatch: pytest.MonkeyPatch):
    """Cấu hình chung: không crosscheck, không web, không brain shadow, search bị cấm."""
    monkeypatch.setenv("SCP_MULTI_LLM_CROSSCHECK", "0")
    monkeypatch.setenv("SCP_WEB_FALLBACK", "0")
    monkeypatch.delenv("SCP_BRAIN_MODE", raising=False)
    monkeypatch.setattr(ask_impl_mod, "_async_fact_check", _noop_fact_check)
    monkeypatch.setattr(ask_impl_mod, "InternetSearch", _ForbiddenSearch)
    _ForbiddenSearch.instances = 0

    decisions: list[Any] = []
    orig_eval = DeliveryRuleEngine.evaluate

    def _recording_eval(*a: Any, **k: Any):
        d = orig_eval(*a, **k)
        decisions.append(d)
        return d

    monkeypatch.setattr(DeliveryRuleEngine, "evaluate", staticmethod(_recording_eval))

    def _install(gateway: Any, judge: Any) -> list[Any]:
        monkeypatch.setattr(ask_impl_mod, "get_gateway", lambda: gateway, raising=False)
        monkeypatch.setattr(ask_impl_mod, "get_judge", lambda: judge)
        return decisions

    return _install


def _gen_ctx(question: str, gateway: Any, *, lane: str = LANE_FACTUAL, **req_kw: Any) -> AskPipelineContext:
    ns = {
        "get_gateway": lambda: gateway,
        "InternetSearch": _ForbiddenSearch,
        "_brain_shadow_generate": _noop_fact_check,
    }
    ctx = AskPipelineContext(req=AskRequest(question=question, **req_kw), request=_mock_request(), namespace=ns)
    ctx.route_decision = RouteDecision(
        intent="LOOKUP" if lane == LANE_FACTUAL else "REASONING", domain="general",
        confidence=0.8, via="test", lane=lane,
    )
    ctx.is_chatbot_lane = lane == LANE_CHATBOT
    return ctx


# ---------------------------------------------------------------------------
# 1. Trạng thái generation tường minh
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_none_from_gateway_sets_llm_unavailable_and_warns(monkeypatch, caplog) -> None:
    monkeypatch.setenv("SCP_WEB_FALLBACK", "0")
    gw = _NoneGateway()
    ctx = _gen_ctx("Viết 2 câu thơ về con mèo", gw, lane=LANE_CHATBOT)
    with caplog.at_level(logging.WARNING, logger="scp.api_server_parts.pipeline.generation_stage"):
        await GenerationStage().execute(ctx)
    assert gw.calls == 1
    assert ctx.ai_answer == ""
    assert ctx.generation_status == "llm_unavailable"
    assert ctx.v98_context.get("generation_status") == "llm_unavailable"
    warned = [r for r in caplog.records if r.levelno >= logging.WARNING and "llm_unavailable" in r.getMessage()]
    assert warned, "LLM trả rỗng phải để lại WARNING có trạng thái llm_unavailable"


@pytest.mark.asyncio
async def test_llm_text_sets_llm_status() -> None:
    ctx = _gen_ctx("Viết 2 câu thơ về con mèo", _TextGateway("Mèo nằm sưởi nắng hiên nhà"), lane=LANE_CHATBOT)
    await GenerationStage().execute(ctx)
    assert ctx.ai_answer == "Mèo nằm sưởi nắng hiên nhà"
    assert ctx.generation_status == "llm"


@pytest.mark.asyncio
async def test_provided_answer_sets_provided_status() -> None:
    gw = _NoneGateway()
    ctx = _gen_ctx("hi", gw, lane=LANE_CHATBOT, ai_answer="Xin chào bạn!")
    await GenerationStage().execute(ctx)
    assert gw.calls == 0
    assert ctx.generation_status == "provided"


# ---------------------------------------------------------------------------
# 2. Nhãn trung thực ở biên /ask (pipeline thật, router thật)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
@pytest.mark.parametrize("question", BENIGN_E2E)
async def test_ungenerated_answer_is_not_labelled_unverified(hermetic, question: str) -> None:
    judge = _EmptyAwareJudge()
    decisions = hermetic(_NoneGateway(), judge)
    response = await _ask_impl(AskRequest(question=question), _mock_request())

    assert judge.seen_answers == [""], "điều kiện tiên quyết: không có bản nháp nào"
    # Vẫn withheld (không giao nội dung chưa sinh/chưa xác minh).
    assert response.final_answer == UNGENERATED_LABEL
    assert "không xác minh được" not in str(response.final_answer)
    assert response.governance_decision == "ESCALATE"
    assert response.verdict != "PASS"
    assert decisions and decisions[-1].code == "ERR_GENERATION_UNAVAILABLE"
    assert decisions[-1].should_clear_fields is True
    assert _ForbiddenSearch.instances == 0


@pytest.mark.asyncio
async def test_existing_answer_failing_verification_keeps_unverified_label(hermetic) -> None:
    """(b) có đáp án nhưng không xác minh được ⇒ GIỮ nhãn cũ (không bị nhãn (a) nuốt)."""
    decisions = hermetic(_TextGateway("Mèo nằm sưởi nắng hiên nhà"), _EmptyAwareJudge())
    response = await _ask_impl(AskRequest(question="Viết 2 câu thơ về con mèo"), _mock_request())
    assert response.final_answer == UNVERIFIED_LABEL
    assert decisions[-1].code == "ERR_GOVERNANCE_ESCALATE"


# ---------------------------------------------------------------------------
# 3. Lỗi lập trình ≠ lỗi gateway
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
@pytest.mark.parametrize("bug", [TypeError("bad arg"), AttributeError("no attr"), NameError("undefined")])
async def test_programming_error_is_internal_and_skips_web_fallback(monkeypatch, caplog, bug) -> None:
    monkeypatch.setenv("SCP_WEB_FALLBACK", "1")  # fallback BẬT — vẫn không được chạy
    _ForbiddenSearch.instances = 0
    ctx = _gen_ctx("Thủ đô của Pháp là gì?", _RaisingGateway(bug), lane=LANE_FACTUAL)
    with caplog.at_level(logging.ERROR, logger="scp.api_server_parts.pipeline.generation_stage"):
        await GenerationStage().execute(ctx)
    assert ctx.generation_status == "internal_error"
    assert ctx.ai_answer == ""
    assert _ForbiddenSearch.instances == 0, "lỗi lập trình không được giả làm 'LLM lỗi' để chạy web fallback"
    assert any(r.levelno >= logging.ERROR and type(bug).__name__ in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_gateway_error_keeps_gateway_status_and_fallback_path(monkeypatch) -> None:
    monkeypatch.setenv("SCP_WEB_FALLBACK", "1")
    _ForbiddenSearch.instances = 0
    ctx = _gen_ctx("Thủ đô của Pháp là gì?", _RaisingGateway(ConnectionError("provider down")), lane=LANE_FACTUAL)
    await GenerationStage().execute(ctx)
    assert ctx.generation_status == "gateway_error"
    # Đường fallback cũ cho lỗi gateway vẫn được thử (search giả bị gọi, lỗi được ghi).
    assert _ForbiddenSearch.instances == 1
    assert ctx.web_fallback.get("success") is False


@pytest.mark.asyncio
async def test_internal_error_withholds_even_with_provided_contexts(hermetic) -> None:
    """Fail-closed: lỗi lập trình ⇒ KHÔNG lấy context làm đáp án, kể cả khi judge
    sẵn sàng PASS mọi thứ — withheld với nhãn lỗi nội bộ."""
    decisions = hermetic(_RaisingGateway(TypeError("bug in our code")), _PassingJudge())
    response = await _ask_impl(
        AskRequest(question="Thủ đô của Pháp là gì?", contexts=["Paris là thủ đô của Pháp."]),
        _mock_request(),
    )
    assert response.final_answer == INTERNAL_LABEL
    assert "Paris" not in str(response.final_answer)
    assert response.verdict != "PASS"
    assert decisions[-1].code == "ERR_GENERATION_INTERNAL"


@pytest.mark.asyncio
async def test_internal_error_benign_question_is_labelled_internal(hermetic) -> None:
    decisions = hermetic(_RaisingGateway(TypeError("bug in our code")), _EmptyAwareJudge())
    response = await _ask_impl(AskRequest(question="Viết 2 câu thơ về con mèo"), _mock_request())
    assert response.final_answer == INTERNAL_LABEL
    assert "không xác minh được" not in str(response.final_answer)
    assert decisions[-1].code == "ERR_GENERATION_INTERNAL"
