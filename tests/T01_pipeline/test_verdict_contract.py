"""[HC-52 / HC-46 / HC-53 · 2026-10-24 · K5-W1] Verdict shape + context key contract.

Pin test (không bịa tập giá trị — mỗi member được xác minh bằng rg trên code + tests):
1. HC-52: VERDICTS / GOVERNANCE đúng tập đã xác minh; validate_verdict_shape bắt
   lỗi shape (verdict lạ, evidence sai kiểu) mà KHÔNG raise (phase 1 observe).
2. HC-46: PipelineContextKeys khớp giá trị magic-string thật trong pipeline —
   chống drift key khi refactor.
3. HC-53: reasoning-source (<think>) bị cắt ở biên judge output — suy luận nội
   bộ KHÔNG được quyết định verdict và KHÔNG lọt vào output visible.
"""
from __future__ import annotations

import inspect
import logging

import pytest

import scp.api_server_parts.pipeline.verification_stage as verification_stage_mod
from scp.api_server_parts.pipeline.verdict_contract import (
    ALL_VERDICTS,
    GOVERNANCE,
    VERDICTS,
    VERDICTS_EXTENDED,
    PipelineContextKeys,
    VerdictEvidenceKeys,
    validate_verdict_shape,
)
from scp.runtime.judge_llm import _parse_verdict
from scp.verifier import strip_reasoning_tags


# ---------------------------------------------------------------------------
# HC-52: value sets — pin đúng tập đã xác minh bằng rg (2026-10-24)
# ---------------------------------------------------------------------------

def test_verdicts_set_matches_verified_reality() -> None:
    # Bằng chứng rg: verification_stage.py:116,177,263,271,330; ledger_stage.py:67,103;
    # delivery_rules.py:61,77,109,162,174; security_stage.py:252; judge.py:342,359,524,541.
    assert VERDICTS == frozenset(
        {"PASS", "FAIL", "UNKNOWN", "PARTIAL", "ABSTAIN", "FLAGGED", "ESCALATE"}
    )
    assert VERDICTS_EXTENDED == frozenset({"DEGRADED", "BLOCKED"})
    assert ALL_VERDICTS == VERDICTS | VERDICTS_EXTENDED


def test_governance_set_matches_verified_reality() -> None:
    # Bằng chứng rg: verification_stage.py:132,196,273; server_helpers.py:76,93;
    # delivery_rules.py:60,126,142,241,256; response_verifier.py:162; drift_guard.py:26.
    assert GOVERNANCE == frozenset(
        {"UPHOLD", "ALLOW", "ESCALATE", "KILL", "ABSTAIN", "DENY", "REJECT", "DEGRADED", "UNKNOWN"}
    )


def test_validate_accepts_atomic_shortcircuit_shape() -> None:
    # Shape thật của nhánh atomic short-circuit (verification_stage.py:115-150).
    v = {
        "verdict": "PASS",
        "confidence": 0.98,
        "final_answer": "answer",
        "reasoning": "ok",
        "evidence": {
            VerdictEvidenceKeys.GOVERNANCE_DECISION: "UPHOLD",
            VerdictEvidenceKeys.JUDGE_EVALUATED: False,
            VerdictEvidenceKeys.DETERMINISTIC_VERIFIED: True,
            VerdictEvidenceKeys.ADJUDICATED_BY: "atomic_deterministic_local",
            VerdictEvidenceKeys.PROVENANCE: "atomic_deterministic_local",
        },
    }
    assert validate_verdict_shape(v) == []


def test_validate_flags_unknown_verdict_value() -> None:
    errors = validate_verdict_shape({"verdict": "CERTAINLY_TRUE", "evidence": {}})
    assert any("not in ALL_VERDICTS" in e for e in errors)


def test_validate_flags_wrong_evidence_field_types() -> None:
    v = {
        "verdict": "PASS",
        "confidence": "high",  # sai kiểu
        "evidence": {
            VerdictEvidenceKeys.JUDGE_EVALUATED: "yes",  # sai kiểu: phải bool
            VerdictEvidenceKeys.EVIDENCE_REFS: "ref-1",  # sai kiểu: phải list/tuple
            VerdictEvidenceKeys.ADJUDICATED_BY: 123,  # sai kiểu: phải str
        },
    }
    errors = validate_verdict_shape(v)
    assert any("confidence" in e for e in errors)
    assert any(f"evidence.{VerdictEvidenceKeys.JUDGE_EVALUATED}" in e for e in errors)
    assert any(f"evidence.{VerdictEvidenceKeys.EVIDENCE_REFS}" in e for e in errors)
    assert any(f"evidence.{VerdictEvidenceKeys.ADJUDICATED_BY}" in e for e in errors)


def test_validate_flags_missing_verdict_and_non_mapping() -> None:
    assert any("missing" in e for e in validate_verdict_shape({"confidence": 0.5}))
    assert validate_verdict_shape("PASS") != []


def test_validation_attached_to_verification_stage_observe_only() -> None:
    # Pin điểm gắn HC-52: validation phải được gọi ở cuối verification_stage và
    # CHỈ log WARNING (phase 1 observe — không raise, không mutate).
    src = inspect.getsource(verification_stage_mod.VerificationStage.execute)
    assert "_validate_verdict_shape" in src
    assert "HC-52" in src
    imports = inspect.getsource(verification_stage_mod)
    assert "validate_verdict_shape" in imports


def test_validate_never_raises_phase1_observe() -> None:
    # Đầu vào phá hoại mọi kiểu: validator phải trả lỗi, không raise.
    for bad in (None, 42, b"x", ["PASS"], {"verdict": ["PASS"]}, {"verdict": "PASS", "evidence": 7}):
        assert isinstance(validate_verdict_shape(bad), list)


# ---------------------------------------------------------------------------
# HC-46: PipelineContextKeys chống drift key
# ---------------------------------------------------------------------------

def test_pipeline_context_keys_match_real_magic_strings() -> None:
    # Giá trị phải khớp đúng key thật đang chạy trong 5 stage (rg 2026-10-24).
    assert PipelineContextKeys.SESSION_ID == "session_id"
    assert PipelineContextKeys.WEB_FALLBACK == "web_fallback"
    assert PipelineContextKeys.FACT_CHECK_HINT == "fact_check_hint"
    assert PipelineContextKeys.DECISION_CODE == "decision_code"
    assert PipelineContextKeys.SEMANTIC_FIREWALL == "semantic_firewall"
    assert PipelineContextKeys.V104_MULTI_TURN == "v104_multi_turn"
    assert PipelineContextKeys.V99_VIETNAMESE_ATTACK == "v99_vietnamese_attack"


def test_pipeline_stages_reference_key_constants_not_raw_strings() -> None:
    # 6 điểm thay magic-string: verification_stage (5) + ledger_stage (1).
    vsrc = inspect.getsource(verification_stage_mod)
    lsrc = inspect.getsource(
        __import__(
            "scp.api_server_parts.pipeline.ledger_stage", fromlist=["LedgerStage"]
        )
    )
    for token in (
        'v98_context["semantic_firewall"]',
        'v98_context["v104_multi_turn"]',
        'v98_context["decision_code"]',
        'v98_context.get("session_id"',
    ):
        assert token not in vsrc, f"magic-string vẫn còn: {token}"
        assert token not in lsrc, f"magic-string vẫn còn: {token}"
    assert "_PCK.SESSION_ID" in vsrc and "_PCK.SESSION_ID" in lsrc
    assert "_PCK.SEMANTIC_FIREWALL" in vsrc and "_PCK.V104_MULTI_TURN" in vsrc
    assert "_PCK.DECISION_CODE" in vsrc


# ---------------------------------------------------------------------------
# HC-53: <think> stripping contract ở biên judge output
# ---------------------------------------------------------------------------

def test_judge_verdict_parser_strips_think_block_reasoning_cannot_decide() -> None:
    # Reasoning-source (think block) chứa PASS nhưng đáp án là FAIL → FAIL.
    assert _parse_verdict("<think>I want to say PASS</think>the answer is FAIL") == "FAIL"
    # Ngược lại: đáp án wins.
    assert _parse_verdict("<think>looks like FAIL</think>final: PASS") == "PASS"
    # Chỉ có suy luận trong think block, không có phán quyết visible → None (fail-closed).
    assert _parse_verdict("<think>PASS PASS PASS</think>") is None


def test_judge_verdict_parser_case_insensitive_strip() -> None:
    assert _parse_verdict("<THINK>PASS</THINK>FAIL") == "FAIL"


def test_no_think_tag_survives_reasoning_strip_boundary() -> None:
    # Biên strip chung (scp/verifier.py): không lọt <think> vào text visible/final.
    assert "<think>" not in strip_reasoning_tags(
        "<think>internal reasoning must not leak</think>visible answer"
    )
    assert strip_reasoning_tags("<think>leak attempt</think>visible answer") == "visible answer"
    # Block chưa đóng: toàn bộ đuôi bị cắt (fail-closed, không leak).
    assert strip_reasoning_tags("start <think>never closed PASS PASS") == "start"
    assert "<think>" not in strip_reasoning_tags("a <reasoning>x</reasoning> b")


def test_think_block_does_not_poison_plain_pass_text() -> None:
    # Anti-placebo: "PASS" nằm CHỈ trong think, text ngoài là FAIL → không được PASS.
    assert _parse_verdict("<think>PASS</think>FAIL") != "PASS"
