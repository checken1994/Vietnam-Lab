"""[W7-e5] Judge 3-state {PASS, FAIL, ABSTAIN} — Option A (GA.md B1b, owner duyệt).

Root-cause W6-RE: judge nhị phân không có class ABSTAIN → câu benign không
verify được (chào hỏi q06, thời tiết realtime q07) bị chấm FAIL → withheld;
ESCALATE-flip q03/q05 = inherent variance của đường verify.

Hợp đồng sau e5 (scp/runtime/judge.py):
  * answer là lời từ chối/abstain trung thực (không chứa factual claim cần
    verify) → verdict ABSTAIN + governance ESCALATE (audit giữ nguyên),
    KHÔNG bị chấm FAIL;
  * crosscheck consensus missing trên câu benign → ABSTAIN + ESCALATE
    (thiếu opinion độc lập ≠ FAIL — không ai adjudicated answer);
  * answer CHỨA factual claim → giữ nguyên đường PASS/FAIL. CHỐNG LỘNG:
    ABSTAIN không được phép thay thế FAIL cho answer có claim (test riêng).
  * KILL (REJECT_INTERNAL_MARKER), DEGRADED, disagree→UNKNOWN, REJECT_EMPTY
    → FAIL: toàn bộ giữ nguyên hành vi W3-e1.

Anti-placebo: các test (1)-(3) chạy trên code TRƯỚC e5 sẽ FAIL (verdict
FAIL/UNKNOWN thay vì ABSTAIN). Không gọi LLM — crosscheck inject qua seam.
"""
from __future__ import annotations

import pytest

from scp.runtime.judge import (
    RealityJudge,
    is_honest_abstain_answer,
    is_refusal_abstain_answer,
)

_QUESTION = "Thời tiết Hà Nội hôm nay thế nào?"
_CLAIM_ANSWER = "Theo dữ liệu được cung cấp, câu trả lời là Donald Trump."
_CLAIM_Q = "Ai là tổng thống Mỹ hiện tại?"
_REFUSAL_ANSWER = (
    "Tôi không thể xác minh thời tiết hiện tại vì không có dữ liệu thời gian thực."
)
_GREETING_ANSWER = "Chào bạn! Mình khỏe, cảm ơn bạn đã hỏi. Bạn khỏe không?"


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch: pytest.MonkeyPatch) -> None:
    """Không chạm mạng: không expert injection, không KB consult."""
    monkeypatch.setattr(
        "scp.data_sources.domain_classifier.classify_top1", lambda _q: "__no_domain__"
    )
    monkeypatch.setattr(
        RealityJudge, "_consult_knowledge", lambda self, question, limit=3: []
    )
    monkeypatch.setenv("SCP_MULTI_LLM_CROSSCHECK", "1")


def _agree_fail_crosscheck() -> dict:
    """Crosscheck consensus 'agree' trên FAIL — benign không verify được."""
    return {"consensus": "agree", "final": "FAIL", "primary": {}, "secondary": {}}


def test_sync_judge_honest_refusal_answer_verdict_abstain_not_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Old-fails/new-passes: refusal benign + semantic agree-FAIL.

    OLD (nhị phân): verdict FAIL → withheld. NEW: verdict ABSTAIN +
    governance ESCALATE (audit giữ nguyên), KHÔNG bị chấm FAIL."""
    import scp.runtime.judge as judge_module

    monkeypatch.setattr(
        judge_module, "_run_crosscheck_sync", lambda *a, **k: _agree_fail_crosscheck()
    )

    verdict = RealityJudge().judge(
        question=_QUESTION, ai_answer=_REFUSAL_ANSWER, context=""
    )

    assert verdict["verdict"] == "ABSTAIN", (
        "honest refusal must be ABSTAIN, not FAIL (old behavior)"
    )
    assert verdict["evidence"]["governance_decision"] == "ESCALATE"
    assert "abstain_reasons" in verdict["evidence"]
    assert "answer_without_verifiable_claim" in verdict["evidence"]["abstain_reasons"]
    assert verdict["final_answer"] == _REFUSAL_ANSWER


@pytest.mark.asyncio
async def test_async_judge_honest_refusal_answer_verdict_abstain_not_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Đường async (judge_async — đường /ask thật qua judge_with_react_fallback)
    phải cùng hợp đồng: refusal benign → ABSTAIN, không FAIL."""
    import scp.runtime.multi_llm_crosscheck as crosscheck_module

    async def _agree_fail(*_a, **_k):
        return _agree_fail_crosscheck()

    monkeypatch.setattr(crosscheck_module, "cross_verify", _agree_fail)

    verdict = await RealityJudge().judge_async(
        question=_QUESTION, ai_answer=_REFUSAL_ANSWER, context=""
    )

    assert verdict["verdict"] == "ABSTAIN"
    assert verdict["evidence"]["governance_decision"] == "ESCALATE"
    assert "answer_without_verifiable_claim" in verdict["evidence"]["abstain_reasons"]


def test_sync_judge_greeting_smalltalk_no_claim_verdict_abstain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """q06 shape: answer hội thoại xã giao (không claim bên ngoài) + semantic
    agree-FAIL → ABSTAIN. OLD: FAIL (nguyên nhân q06 bị withhold)."""
    import scp.runtime.judge as judge_module

    monkeypatch.setattr(
        judge_module, "_run_crosscheck_sync", lambda *a, **k: _agree_fail_crosscheck()
    )

    verdict = RealityJudge().judge(
        question="Chào bạn, dạo này bạn thế nào?", ai_answer=_GREETING_ANSWER, context=""
    )

    assert verdict["verdict"] == "ABSTAIN"
    assert verdict["evidence"]["governance_decision"] == "ESCALATE"


def test_sync_judge_factual_claim_answer_keeps_fail_not_abstain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CHỐNG LỘNG (anti-lộng): answer chứa factual claim + semantic agree-FAIL
    → verdict PHẢI vẫn FAIL (không lọt ABSTAIN). ABSTAIN không được phép thay
    thế FAIL cho answer có khẳng định kiểm chứng được."""
    import scp.runtime.judge as judge_module

    monkeypatch.setattr(
        judge_module, "_run_crosscheck_sync", lambda *a, **k: _agree_fail_crosscheck()
    )

    verdict = RealityJudge().judge(
        question=_CLAIM_Q, ai_answer=_CLAIM_ANSWER, context=""
    )

    assert verdict["verdict"] == "FAIL", (
        "ABSTAIN must never replace FAIL for an answer carrying a factual claim"
    )
    assert verdict["evidence"]["governance_decision"] == "ESCALATE"
    assert "semantic_judge_fail" in verdict["failures"]


def test_sync_judge_factual_claim_answer_pass_keeps_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Claim answer + crosscheck agree PASS → PASS giữ nguyên (e5 không đụng
    đường PASS, không hạ PASS thành ABSTAIN)."""
    import scp.runtime.judge as judge_module

    monkeypatch.setattr(
        judge_module,
        "_run_crosscheck_sync",
        lambda *a, **k: {"consensus": "agree", "final": "PASS", "primary": {}, "secondary": {}},
    )

    verdict = RealityJudge().judge(
        question=_CLAIM_Q, ai_answer=_CLAIM_ANSWER, context=""
    )

    assert verdict["verdict"] == "PASS"
    assert verdict["evidence"]["governance_decision"] == "UPHOLD"


def test_sync_judge_consensus_missing_benign_verdict_abstain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Crosscheck consensus missing trên câu benign → ABSTAIN (thay UNKNOWN):
    thiếu opinion độc lập không phải FAIL cũng không phải "model bất đồng" —
    là abstain có lý do. Governance vẫn ESCALATE (fail-closed giữ nguyên ở
    boundary)."""
    import scp.runtime.judge as judge_module

    monkeypatch.setattr(
        judge_module,
        "_run_crosscheck_sync",
        lambda *a, **k: {
            "consensus": "missing_distinct_providers",
            "final": None,
            "primary": {},
            "secondary": {},
        },
    )

    verdict = RealityJudge().judge(
        question=_CLAIM_Q, ai_answer=_CLAIM_ANSWER, context=""
    )

    assert verdict["verdict"] == "ABSTAIN"
    assert verdict["evidence"]["governance_decision"] == "ESCALATE"
    assert "crosscheck_consensus_missing" in verdict["evidence"]["abstain_reasons"]


def test_sync_judge_disagreement_still_unknown_not_abstain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Consensus DISAGREE + claim answer giữ nguyên UNKNOWN (pin W3-e1/T05
    không bị ABSTAIN nuốt mất) — e5 chỉ đổi nhánh missing, không đổi disagree."""
    import scp.runtime.judge as judge_module

    monkeypatch.setattr(
        judge_module,
        "_run_crosscheck_sync",
        lambda *a, **k: {"consensus": "disagree", "final": None, "primary": {}, "secondary": {}},
    )

    verdict = RealityJudge().judge(
        question=_CLAIM_Q, ai_answer=_CLAIM_ANSWER, context=""
    )

    assert verdict["verdict"] == "UNKNOWN"
    assert verdict["evidence"]["governance_decision"] == "ESCALATE"
    assert "multi_llm_disagreement" in verdict["failures"]


def test_sync_judge_security_marker_answer_never_abstain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Anti-lộng KILL: tamper marker (REJECT_INTERNAL_MARKER) → KILL, verdict
    FAIL — security class không bao giờ bị hạ thành ABSTAIN."""
    import scp.runtime.judge as judge_module

    def _must_not_run(*_a, **_k):
        raise AssertionError("tier-1 tamper rejection must short-circuit tier-2")

    monkeypatch.setattr(judge_module, "_run_crosscheck_sync", _must_not_run)

    verdict = RealityJudge().judge(
        question=_QUESTION,
        ai_answer="[SCP: internal-marker injected content]",
        context="",
    )

    assert verdict["verdict"] == "FAIL"
    assert "REJECT_INTERNAL_MARKER" in verdict["failures"]
    assert verdict["evidence"]["governance_decision"] == "KILL"


def test_sync_judge_empty_answer_still_fail_not_abstain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """REJECT_EMPTY giữ nguyên FAIL (pin W3-e1): rỗng không phải lời từ chối
    trung thực — detector phải trả False cho empty."""
    assert is_honest_abstain_answer("") is False

    verdict = RealityJudge().judge(question=_QUESTION, ai_answer="")

    assert verdict["verdict"] == "FAIL"
    assert "REJECT_EMPTY" in verdict["failures"]
    assert verdict["evidence"]["governance_decision"] == "ESCALATE"


def test_sync_judge_degraded_path_not_reclassified_abstain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DEGRADED (crosscheck raise → fallback cascade) giữ nguyên class kể cả
    khi answer là refusal — degraded là lớp quan sát riêng (SEC-R2-01)."""
    import scp.runtime.judge as judge_module

    def _broken_gateway():
        raise RuntimeError("gateway unavailable (fault injection)")

    monkeypatch.setattr("scp.llm_gateway.get_gateway", _broken_gateway)
    monkeypatch.setattr(judge_module, "_llm_judge", lambda *a, **k: False)

    verdict = RealityJudge().judge(
        question=_QUESTION, ai_answer=_REFUSAL_ANSWER, context=""
    )

    assert verdict["verdict"] == "DEGRADED"
    assert verdict["degraded"] is True
    assert verdict["evidence"]["governance_decision"] == "DEGRADED"


# ---------------------------------------------------------------------------
# Detector unit contract (deterministic, máy đọc — không cảm tính).
# ---------------------------------------------------------------------------
def test_detector_refusal_shapes() -> None:
    assert is_refusal_abstain_answer(
        "Tôi không có thông tin realtime về thời tiết hôm nay."
    )
    assert is_refusal_abstain_answer("I don't have data on that.")
    # Refusal kèm payload số liệu/URL → KHÔNG còn là abstain thuần.
    assert is_refusal_abstain_answer("Tôi không thể xác minh (HTTP 404).") is False
    assert is_refusal_abstain_answer("Xem https://example.com — không có dữ liệu.") is False
    assert is_refusal_abstain_answer("") is False


def test_detector_conv_shapes_and_claim_guard() -> None:
    assert is_honest_abstain_answer(_GREETING_ANSWER)
    assert is_honest_abstain_answer("Hello! I'm fine, thanks for asking!")
    # Anti-lộng detector: copula-assertion / claim payload phủ định conv-tier.
    assert is_honest_abstain_answer("Cảm ơn bạn. Thủ đô Pháp là Paris.") is False
    assert is_honest_abstain_answer(_CLAIM_ANSWER) is False
    assert is_honest_abstain_answer("Được nâng cấp vào năm 2024.") is False
