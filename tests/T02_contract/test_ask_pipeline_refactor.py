# SCP CIRCUIT: M02 — Pipeline Pattern Refactor Verification Tests
from __future__ import annotations

import asyncio
import base64
from typing import Any

import pytest
from starlette.requests import Request as StarletteRequest

from scp.api_server_parts._ask_impl import _ask_impl
from scp.api_server_parts.helpers import AskRequest, AskResponse
from scp.api_server_parts.pipeline import (
    AskPipelineContext,
    AskPipelineRunner,
    GenerationStage,
    LedgerStage,
    LookupStage,
    PipelineStage,
    SecurityStage,
    VerificationStage,
)
from scp.runtime.question_router import LANE_FACTUAL, RouteDecision


def _make_starlette_request() -> StarletteRequest:
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/ask",
        "headers": [],
        "query_string": b"",
        "client": ("127.0.0.1", 54321),
        "server": ("testserver", 80),
        "scheme": "http",
    }
    return StarletteRequest(scope)


@pytest.mark.asyncio
async def test_image_data_base64_jailbreak_detected_and_withheld():
    """Verify that base64 image_data triggers ImageJailbreakDetector and withholds response."""
    request = _make_starlette_request()
    raw_img = b"fake_png_header_and_data"
    req = AskRequest(
        question="Analyze this image",
        image_data=base64.b64encode(raw_img).decode(),
    )

    class ConcreteJailbreakResult:
        jailbreak_detected = True
        method = "ocr"

    class ConcreteImageDetector:
        def __init__(self):
            self.called = False

        def detect(self, *args: Any, **kwargs: Any) -> ConcreteJailbreakResult:
            self.called = True
            return ConcreteJailbreakResult()

    detector = ConcreteImageDetector()
    ctx = AskPipelineContext(req=req, request=request, namespace={"_image_detector": detector})
    runner = AskPipelineRunner()
    resp = await runner.run(req, request, namespace={"_image_detector": detector})

    assert detector.called is True
    assert resp.verdict == "FAIL"
    assert "multimodal jailbreak detected" in resp.final_answer


@pytest.mark.asyncio
async def test_judge_instance_persisted_on_context_across_stages():
    """Verify that ctx.judge holds the single instance across stages and finally block."""
    request = _make_starlette_request()
    req = AskRequest(question="Test singleton judge")

    class ConcreteDosProtection:
        def __init__(self):
            self.slots_checked = 0
            self.slots_released = 0
            self.recorded_verdicts: list[tuple[Any, ...]] = []

        def check_request(self, *args: Any, **kwargs: Any) -> None:
            self.slots_checked += 1
            return None  # slot granted

        def record_verdict(self, *args: Any, **kwargs: Any) -> None:
            self.recorded_verdicts.append(args)

        def release_slot(self, *args: Any, **kwargs: Any) -> None:
            self.slots_released += 1

    class ConcreteJudge:
        def __init__(self):
            self.slots = 0
            self.dos_protection = ConcreteDosProtection()

        async def judge_with_react_fallback(self, **kwargs: Any) -> dict[str, Any]:
            return {
                "verdict": "PASS",
                "confidence": 0.9,
                "final_answer": "ok",
                "evidence": {"governance_decision": "ALLOW"},
                "slm_responses": [],
            }

    instances_created = []

    def judge_factory():
        j = ConcreteJudge()
        instances_created.append(j)
        return j

    ctx = AskPipelineContext(req=req, request=request, namespace={"get_judge": judge_factory})
    runner = AskPipelineRunner()

    # Run only SecurityStage to verify slot taken on ctx.judge
    await SecurityStage().execute(ctx)
    assert ctx.dos_slot_taken is True
    assert ctx.judge is not None
    # Ensure no duplicate creation
    first_judge = ctx.judge
    await VerificationStage().execute(ctx)
    assert ctx.judge is first_judge
    assert len(instances_created) == 1


@pytest.mark.asyncio
async def test_lookup_stage_does_not_override_ai_answer_prematurely(monkeypatch):
    """Verify that LookupStage populates ctx.retrieval_res without overwriting ctx.ai_answer."""
    request = _make_starlette_request()
    req = AskRequest(question="Thủ đô của Úc là gì?")
    ctx = AskPipelineContext(req=req, request=request)

    class StubRetriever:
        async def retrieve(self, **kwargs):
            return {
                "retrieval_triggered": True,
                "clean_evidence_snippets": ["Canberra là thủ đô của Úc."],
                "web_search_hits": [],
            }

    monkeypatch.setattr(
        "scp.runtime.question_router.route_question",
        lambda q: RouteDecision(intent="LOOKUP", domain="geography", confidence=0.5, via="test", lane=LANE_FACTUAL, reason="test"),
    )
    monkeypatch.setattr("scp.knowledge.domain_knowledge.AutonomousEvidenceRetriever", StubRetriever)

    lookup = LookupStage()
    await lookup.execute(ctx)

    assert ctx.retrieval_res.get("retrieval_triggered") is True
    # ctx.ai_answer must NOT be populated by lookup stage so GenerationStage can invoke LLM
    assert ctx.ai_answer == ""


@pytest.mark.usefixtures("relevance_confirmed")  # [RELEVANCE] tiền điều kiện: reader độc lập đã xác nhận
@pytest.mark.asyncio
async def test_verification_and_ledger_stages_handle_none_route_decision():
    """Verify defensive handling when ctx.route_decision is None."""
    request = _make_starlette_request()
    req = AskRequest(question="Hello")
    ctx = AskPipelineContext(req=req, request=request)
    ctx.route_decision = None

    class ConcreteJudgeVerdict:
        verdict = "PASS"
        confidence = 0.9
        domain = "general"
        reasoning = ""
        final_answer = "Hi"
        slm_responses = []
        evidence = {}

    ctx.judge_verdict = ConcreteJudgeVerdict()

    # Should not raise AttributeError: 'NoneType' object has no attribute 'lane'
    v_stage = VerificationStage()

    class ConcretePipelineJudge:
        async def judge_with_react_fallback(self, **kwargs: Any) -> dict[str, Any]:
            return {
                "verdict": "PASS",
                "confidence": 0.9,
                "final_answer": "Hi",
                "evidence": {"governance_decision": "ALLOW"},
                "slm_responses": [],
            }

    ctx.judge = ConcretePipelineJudge()
    await v_stage.execute(ctx)
    assert ctx.gov_decision == "ALLOW"

    # Ledger stage should complete without error
    l_stage = LedgerStage()
    await l_stage.execute(ctx)
    assert ctx.final_response is not None
    assert ctx.final_response.lane == "LANE_CHATBOT"


@pytest.mark.asyncio
async def test_response_verifier_lookup_fork_self_certified_bypasses_judge(monkeypatch):
    """[O1 DNA #7] Lookup fork self-certified marks already_judged=True and bypasses judge."""
    from scp.ask_adapter_parts.response_verifier import verify_ask_response

    req = AskRequest(question="Thời tiết Hà Nội hôm nay?")
    response_data = {
        "final_answer": "Thời tiết Hà Nội hôm nay 25°C. (Nguồn dữ liệu: Open-Meteo)",
        "verdict": "PASS",
        "governance_decision": "UPHOLD",
        "provenance": "lookup_data_api",
        "v98_classification": {
            "route": "lookup_data_api",
            "api_name": "Open-Meteo",
        },
        "relevance_gate": {
            "checked": True,
            "terms": ["thời", "tiết"],
        },
    }

    judge_called = False

    def _mock_get_judge():
        nonlocal judge_called
        judge_called = True
        return None

    monkeypatch.setattr("scp.api_server_parts.helpers.get_judge", _mock_get_judge)

    result = await verify_ask_response(req, response_data)
    assert result["checked"]["judge_pass"] is True
    assert result["checked"]["verdict_pass"] is True
    assert result["verdict"] == "VERIFIED"
    assert judge_called is False, "Self-certified lookup fork must NOT call get_judge()!"


@pytest.mark.asyncio
async def test_response_verifier_lookup_fork_not_self_certified_calls_singleton_judge(monkeypatch):
    """[O1 DNA #7] Non self-certified lookup fork falls back to singleton helpers.get_judge()."""
    from scp.ask_adapter_parts.response_verifier import verify_ask_response

    req = AskRequest(question="Thời tiết Hà Nội hôm nay?")
    response_data = {
        "final_answer": "Thời tiết Hà Nội hôm nay 25°C.",
        "verdict": "PASS",
        "governance_decision": "UPHOLD",
        "provenance": "lookup_data_api",
        "v98_classification": {
            "route": "lookup_data_api",
        },
        "relevance_gate": {
            "checked": False,
        },
    }

    class ConcreteAsyncJudge:
        def __init__(self):
            self.called = False

        async def judge_async(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
            self.called = True
            return {"verdict": "PASS"}

    concrete_judge = ConcreteAsyncJudge()
    judge_getter_called = False

    def _mock_get_judge():
        nonlocal judge_getter_called
        judge_getter_called = True
        return concrete_judge

    monkeypatch.setattr("scp.api_server_parts.helpers.get_judge", _mock_get_judge)

    result = await verify_ask_response(req, response_data)
    assert judge_getter_called is True, "Non self-certified lookup fork must call get_judge()!"
    assert concrete_judge.called is True
    assert result["checked"]["judge_pass"] is True
