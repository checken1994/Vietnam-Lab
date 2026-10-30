"""Unit and Anti-Placebo Tests for Issue 1: Math Verification in Chatbot Lane.

Verifies:
1. Arithmetic rules (arithmetic, math_function, factorial, power, compute_command)
   route to LANE_FACTUAL (no bypass flag — removed F-E2E-2) and intent=REASONING.
2. Non-arithmetic conversational queries preserve LANE_CHATBOT (dead bypass flag removed F-E2E-2).
3. VerificationStage enables AtomicDeterministicVerifier for calculation verification
   even when ctx.is_chatbot_lane is True.
4. Anti-placebo negative test: Wrong math (e.g. 2+2=5, 17*23=400) is NEVER delivered
   as [unverified — abstain]; it is either deterministically repaired to the correct
   value or fail-closed withheld.
"""
from __future__ import annotations

import asyncio
import pytest

from scp.runtime.router_rules import (
    classify_l0,
    LANE_FACTUAL,
    LANE_CHATBOT,
    REASONING,
)
from scp.runtime.atomic_verifier import (
    AtomicDeterministicVerifier,
    is_atomic_verifier_enabled,
)
from scp.api_server_parts.pipeline.context import AskPipelineContext
from scp.api_server_parts.pipeline.verification_stage import VerificationStage
from scp.api_server_parts.pipeline.delivery_rules import (
    DeliveryRuleEngine,
    DecisionCode,
)
from scp.api_server_parts.helpers import AskRequest


# ---------------------------------------------------------------------------
# 1. Router Classification Tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "query,expected_tag",
    [
        ("2 + 2", "arithmetic"),
        ("17 * 23", "arithmetic"),
        ("100 / 4", "arithmetic"),
        ("100 - 25", "arithmetic"),
        ("sqrt(144)", "math_function"),
        ("sin(0)", "math_function"),
        ("5!", "factorial"),
        ("2^10", "power"),
        ("2 mũ 8", "power"),
        ("tính 15 + 30", "compute_command"),
        ("calculate 50 * 2", "compute_command"),
    ],
)
def test_arithmetic_queries_route_to_factual_lane(query: str, expected_tag: str) -> None:
    """Arithmetic rules must route to LANE_FACTUAL (no bypass flag, F-E2E-2)."""
    decision = classify_l0(query)
    assert decision is not None, f"Expected L0 decision for '{query}'"
    assert decision.intent == REASONING, f"Expected REASONING intent for '{query}'"
    assert decision.lane == LANE_FACTUAL, f"Expected LANE_FACTUAL for '{query}', got {decision.lane}"
    assert not hasattr(decision, "bypass_verdict_pass"), f"dead bypass flag must be absent (F-E2E-2) for '{query}'"
    assert any(
        tag in decision.reason
        for tag in ("arithmetic", "math_function", "factorial", "power", "compute_command")
    ), f"Expected arithmetic strong tag in '{decision.reason}'"


@pytest.mark.parametrize(
    "query",
    [
        "xin chào bạn",
        "bạn là ai",
        "who are you",
        "kể một câu chuyện vui",
        "cảm ơn bạn nhiều",
    ],
)
def test_conversational_queries_remain_in_chatbot_lane(query: str) -> None:
    """Anti-placebo negative test: conversational queries must remain in LANE_CHATBOT (no bypass flag, F-E2E-2)."""
    decision = classify_l0(query)
    assert decision is not None, f"Expected L0 decision for '{query}'"
    assert decision.intent == REASONING
    assert decision.lane == LANE_CHATBOT, f"Expected LANE_CHATBOT for '{query}', got {decision.lane}"
    assert not hasattr(decision, "bypass_verdict_pass"), f"dead bypass flag must be absent (F-E2E-2) for '{query}'"


# ---------------------------------------------------------------------------
# 2. Atomic Verifier Calculation Execution
# ---------------------------------------------------------------------------

def test_atomic_verifier_correct_arithmetic_passes() -> None:
    """Atomic verifier confirms correct arithmetic clause with VERIFIED status."""
    verifier = AtomicDeterministicVerifier()
    res = verifier.verify_and_prune(
        question="2 + 2 bằng mấy?",
        draft_answer="2 + 2 = 4.",
    )
    assert res["total_claims"] == 1
    assert res["verified_count"] == 1
    assert res["all_verified_locally"] is True
    assert res["repaired_answer"] is None


def test_atomic_verifier_wrong_arithmetic_repaired() -> None:
    """Atomic verifier detects wrong arithmetic (2+2=5) and repairs it to 4."""
    verifier = AtomicDeterministicVerifier()
    res = verifier.verify_and_prune(
        question="Tính 2 + 2",
        draft_answer="2 + 2 = 5.",
    )
    assert res["total_claims"] == 1
    assert res["all_verified_locally"] is True
    assert res["repaired_answer"] == "2 + 2 = 4."


# ---------------------------------------------------------------------------
# 3. VerificationStage Execution in Chatbot Lane Context
# ---------------------------------------------------------------------------

def test_verification_stage_in_chatbot_lane_repairs_wrong_math() -> None:
    """Even if ctx.is_chatbot_lane is True, wrong math is repaired and not passed through unverified."""
    req = AskRequest(question="Tính 17 * 23")
    ctx = AskPipelineContext(req=req, request=None, namespace={})
    ctx.is_chatbot_lane = True
    ctx.ai_answer = "17 * 23 = 400"

    stage = VerificationStage()
    asyncio.run(stage.execute(ctx))

    # The calculation must be repaired to 391
    assert "391" in ctx.ai_answer, f"Expected repaired answer containing 391, got: {ctx.ai_answer}"
    assert "400" not in ctx.ai_answer, f"Wrong answer 400 must not survive: {ctx.ai_answer}"
    assert ctx.judge_verdict.verdict == "PASS"
    assert "391" in ctx.api_final_answer


def test_wrong_math_in_factual_lane_never_delivered_as_unverified_abstain() -> None:
    """Anti-placebo test: in LANE_FACTUAL, wrong math under ABSTAIN is withheld, NOT delivered with [unverified — abstain]."""
    req = AskRequest(question="2 + 2 = 5 đúng không?")
    ctx = AskPipelineContext(req=req, request=None, namespace={})
    ctx.is_chatbot_lane = False  # Factual lane per router rule fix
    ctx.ai_answer = "2 + 2 = 5"

    class _AbstainingJudge:
        async def judge_with_react_fallback(self, **kwargs):
            return {
                "verdict": "ABSTAIN",
                "confidence": 0.5,
                "reasoning": "abstain",
                "final_answer": "2 + 2 = 5",
                "evidence": {"governance_decision": "ABSTAIN"},
            }

    ctx.judge = _AbstainingJudge()
    stage = VerificationStage()
    asyncio.run(stage.execute(ctx))

    # Delivery decision must NOT be DELIVER_UNVERIFIED_ABSTAIN
    decision_code = ctx.v98_context.get("decision_code")
    assert decision_code != DecisionCode.DELIVER_UNVERIFIED_ABSTAIN, (
        f"Wrong math in factual lane must NOT be DELIVER_UNVERIFIED_ABSTAIN: {decision_code}"
    )
    assert "[unverified — abstain]" not in ctx.api_final_answer, (
        f"Wrong math must not have [unverified — abstain] label: {ctx.api_final_answer}"
    )
    # It must be withheld fail-closed
    assert "[SCP: Answer withheld" in ctx.api_final_answer or "4" in ctx.api_final_answer
