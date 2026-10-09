# SCP CIRCUIT: M02 — Pipeline Pattern Refactor Verification Tests
from __future__ import annotations

import asyncio
import base64
from typing import Any
from unittest.mock import MagicMock

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

    mock_detector = MagicMock()
    mock_result = MagicMock()
    mock_result.jailbreak_detected = True
    mock_result.method = "ocr"
    mock_detector.detect.return_value = mock_result

    ctx = AskPipelineContext(req=req, request=request, namespace={"_image_detector": mock_detector})
    runner = AskPipelineRunner()
    resp = await runner.run(req, request, namespace={"_image_detector": mock_detector})

    assert mock_detector.detect.called
    assert resp.verdict == "FAIL"
    assert "multimodal jailbreak detected" in resp.final_answer


@pytest.mark.asyncio
async def test_judge_instance_persisted_on_context_across_stages():
    """Verify that ctx.judge holds the single instance across stages and finally block."""
    request = _make_starlette_request()
    req = AskRequest(question="Test singleton judge")

    class MockJudge:
        def __init__(self):
            self.slots = 0
            self.dos_protection = MagicMock()
            self.dos_protection.check_request.return_value = None  # slot taken
            self.dos_protection.record_verdict = MagicMock()
            self.dos_protection.release_slot = MagicMock()

        async def judge_with_react_fallback(self, **kwargs):
            return {
                "verdict": "PASS",
                "confidence": 0.9,
                "final_answer": "ok",
                "evidence": {"governance_decision": "ALLOW"},
                "slm_responses": [],
            }

    instances_created = []

    def judge_factory():
        j = MockJudge()
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


@pytest.mark.asyncio
async def test_verification_and_ledger_stages_handle_none_route_decision():
    """Verify defensive handling when ctx.route_decision is None."""
    request = _make_starlette_request()
    req = AskRequest(question="Hello")
    ctx = AskPipelineContext(req=req, request=request)
    ctx.route_decision = None
    ctx.judge_verdict = MagicMock(verdict="PASS", confidence=0.9, domain="general", reasoning="", final_answer="Hi", slm_responses=[], evidence={})

    # Should not raise AttributeError: 'NoneType' object has no attribute 'lane'
    v_stage = VerificationStage()
    # Mock judge on ctx to avoid network
    ctx.judge = MagicMock()
    async def fake_judge_call(**kwargs):
        return {
            "verdict": "PASS",
            "confidence": 0.9,
            "final_answer": "Hi",
            "evidence": {"governance_decision": "ALLOW"},
            "slm_responses": [],
        }
    ctx.judge.judge_with_react_fallback = fake_judge_call
    await v_stage.execute(ctx)
    assert ctx.gov_decision == "ALLOW"

    # Ledger stage should complete without error
    l_stage = LedgerStage()
    await l_stage.execute(ctx)
    assert ctx.final_response is not None
    assert ctx.final_response.lane == "LANE_CHATBOT"
