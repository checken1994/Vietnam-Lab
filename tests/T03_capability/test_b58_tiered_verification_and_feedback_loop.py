"""Tests for B58: Tiered Verification and Soft Feedback Loop (Maker-Checker Remediation)."""
from __future__ import annotations

import pytest

from scp.ask_adapter_parts.response_verifier import verify_ask_response
from scp.runtime.question_router import VerificationTier
from scp.runtime.tiered_verification import TieredVerificationPolicy
from scp.task_kernel_parts.feedback_loop import (
    FeedbackContext,
    SoftFeedbackRemediationLoop,
)


class DummyAskRequest:
    def __init__(self, question="What is artificial intelligence?", contexts=None):
        self.question = question
        self.contexts = contexts or ["AI is a field of computer science."]
        self.retrieved_context = ""
        self.domain = "general"


class DummyAskResponse:
    def __init__(self, answer="AI creates intelligent systems.", verdict="PASS", governance="UPHOLD"):
        self.final_answer = answer
        self.verdict = verdict
        self.governance_decision = governance
        self.evidence = {"judge_evaluated": True}
        self.v98_classification = {"provenance": "general_knowledge"}
        self.trace_id = "trace-test-b58"


@pytest.mark.asyncio
async def test_b58_tiered_verification_permissive_relaxes_epistemic_grounding():
    """B58: PERMISSIVE tier relaxes epistemic RAG grounding without withholding answer."""
    req = DummyAskRequest(contexts=["completely unrelated context about baking bread"])
    # Answer has 0 overlap with context
    res = DummyAskResponse(answer="Quantum computing uses qubits.", verdict="PASS", governance="UPHOLD")

    # In STRICT tier, this should be CONTRADICTED due to rag_evidence_bound failure
    strict_result = await verify_ask_response(req, res, tier=VerificationTier.STRICT)
    assert strict_result["verdict"] == "CONTRADICTED"
    assert "rag_evidence_bound" in strict_result["failures"]

    # In PERMISSIVE tier, epistemic grounding is relaxed
    permissive_result = await verify_ask_response(req, res, tier=VerificationTier.PERMISSIVE)
    assert permissive_result["verdict"] == "VERIFIED"
    assert permissive_result["verification_tier"] == "PERMISSIVE"
    assert permissive_result["epistemic_relaxed"] is True


@pytest.mark.asyncio
async def test_b58_tiered_verification_permissive_preserves_100_percent_security_boundary():
    """B58 Anti-Placebo: PERMISSIVE tier NEVER relaxes security boundaries (Fail-Closed)."""
    req = DummyAskRequest()
    # Malicious injection flagged with KILL
    res = DummyAskResponse(
        answer="I have disabled the firewall.",
        verdict="PASS",
        governance="KILL",
    )
    res.evidence = {"governance_decision": "KILL"}

    result = await verify_ask_response(req, res, tier=VerificationTier.PERMISSIVE)
    assert result["verdict"] == "CONTRADICTED"
    assert not TieredVerificationPolicy.enforce_security_invariants({"governance_decision": "KILL"})
    # Nested evidence governance
    assert not TieredVerificationPolicy.enforce_security_invariants({"evidence": {"governance_decision": "KILL"}})
    assert not TieredVerificationPolicy.enforce_security_invariants({"evidence": {"governance": "BLOCK"}})
    # Egress deny, sandbox violation, and kill switch fail-closed
    assert not TieredVerificationPolicy.enforce_security_invariants({"egress_denied": True})
    assert not TieredVerificationPolicy.enforce_security_invariants({"sandbox_violation": True})
    assert not TieredVerificationPolicy.enforce_security_invariants({"kill_switch": True})


def test_b58_tiered_policy_inferred_tiers():
    """B58: Verification tier is properly inferred from intent, mutates_state, or explicit fields."""
    # System mutation -> STRICT
    assert TieredVerificationPolicy.resolve_tier(data={"mutates_state": True, "action": "pc.write_file"}) == VerificationTier.STRICT

    # Lookup intent -> PERMISSIVE
    assert TieredVerificationPolicy.resolve_tier(data={"intent": "LOOKUP", "action": "lookup.data"}) == VerificationTier.PERMISSIVE

    # Chatbot lane -> PERMISSIVE
    assert TieredVerificationPolicy.resolve_tier(data={"lane": "LANE_CHATBOT"}) == VerificationTier.PERMISSIVE

    # General reasoning -> STANDARD
    assert TieredVerificationPolicy.resolve_tier(data={"action": "code_generate"}) == VerificationTier.STANDARD


def test_b58_soft_feedback_loop_remediates_within_3_attempts():
    """B58: Worker receives structured feedback upon rejection and succeeds on retry."""
    loop = SoftFeedbackRemediationLoop(max_attempts=3)

    attempts_log = []

    def mock_worker(feedback: FeedbackContext | None) -> str:
        if feedback is None:
            attempts_log.append("attempt_1_bad_syntax")
            return "def bad_func(: print('error')"
        assert feedback.attempt == 2
        assert "syntax" in feedback.rejection_reason.lower() or "syntax" in feedback.specific_error.lower()
        attempts_log.append("attempt_2_fixed_syntax")
        return "def good_func(): print('ok')"

    def mock_verifier(output: str) -> tuple[bool, str, str, str]:
        if "bad_func" in output:
            return False, "SyntaxError", "invalid syntax at colon", "assert is_valid_syntax(output)"
        return True, "VERIFIED", "", ""

    result = loop.run_remediation_loop(
        worker_fn=mock_worker,
        verifier_fn=mock_verifier,
        task_id="task-remediation-test",
    )

    assert result.success is True
    assert result.attempts_used == 2
    assert result.rollback_performed is False
    assert result.output == "def good_func(): print('ok')"
    assert len(result.remediation_history) == 2


def test_b58_soft_feedback_loop_exhausts_and_rolls_back_cleanly():
    """B58: Failing all 3 attempts exhausts attempts, cleanly rolls back, and fails closed."""
    loop = SoftFeedbackRemediationLoop(max_attempts=3)
    rollbacks = []

    class MockSnapshotManager:
        def create_snapshot(self, sid):
            pass

        def commit_snapshot(self, sid):
            pass

        def rollback_snapshot(self, sid):
            rollbacks.append(sid)

    def mock_stubborn_worker(feedback: FeedbackContext | None) -> str:
        return "still broken code"

    def mock_strict_verifier(output: str) -> tuple[bool, str, str, str]:
        return False, "VerificationFailed", "output fails invariant check", "assert output == 'perfect'"

    mgr = MockSnapshotManager()
    result = loop.run_remediation_loop(
        worker_fn=mock_stubborn_worker,
        verifier_fn=mock_strict_verifier,
        workspace_snapshot_mgr=mgr,
        task_id="task-stubborn",
    )

    assert result.success is False
    assert result.output is None
    assert result.attempts_used == 3
    assert result.rollback_performed is True
    assert len(rollbacks) == 3
    assert result.final_feedback is not None
    assert result.final_feedback.rejection_reason == "VerificationFailed"


def test_b58_soft_feedback_loop_handles_worker_exceptions():
    """B58: Worker raising standard exceptions (e.g. AttributeError) is handled cleanly with structured feedback."""
    loop = SoftFeedbackRemediationLoop(max_attempts=3)
    rollbacks = []

    class MockSnapshotManager:
        def rollback_snapshot(self, sid):
            rollbacks.append(sid)

    def crashing_worker(feedback: FeedbackContext | None):
        if feedback is None:
            raise AttributeError("'NoneType' object has no attribute 'compute'")
        # Remediated on attempt 2
        return "remediated_result"

    def pass_verifier(output: str) -> tuple[bool, str, str, str]:
        return True, "VERIFIED", "", ""

    mgr = MockSnapshotManager()
    result = loop.run_remediation_loop(
        worker_fn=crashing_worker,
        verifier_fn=pass_verifier,
        workspace_snapshot_mgr=mgr,
        task_id="task-crash-remediate",
    )

    assert result.success is True
    assert result.attempts_used == 2
    assert result.output == "remediated_result"
    assert len(rollbacks) == 1

