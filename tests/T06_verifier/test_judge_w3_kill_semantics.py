"""[W3-e1] KILL-semantics: tách "không verify được" khỏi "nguy hiểm thật".

Thực tế runtime (GA.md B1b, q08 cùng SHA PASS→PASS→FAIL KILL): câu hỏi benign
"Ai là tổng thống Mỹ hiện tại?" không xác minh được → judge map MỌI non-PASS
thành governance KILL (mức dành cho nội dung nguy hiểm thật) → boundary
withhold với nhãn "Governance KILL". Đây là KILL quá tay: failure class của
nó là VERIFICATION-FAIL, không phải SECURITY-THREAT.

Hợp đồng sau e1 (scp/runtime/judge.py):
  - verification FAIL trên câu benign (semantic judge/cascade không xác minh
    được, grounding trượt, không có answer) → governance ESCALATE kèm lý do
    verification trong reasoning — KHÔNG được KILL.
  - KILL chỉ còn khi judge thấy tín hiệu security thật: Tier-1 chặn nội dung
    cố tình mang marker nội bộ (REJECT_INTERNAL_MARKER — tamper signal).
  - Nhánh escalated (semantic None/disagree) giữ nguyên UNKNOWN + ESCALATE.
  - Nhánh DEGRADED (crosscheck fallback lỗi) giữ nguyên DEGRADED.

Anti-placebo: các test dưới đây chạy TRƯỚC fix sẽ FAIL (governance == "KILL"
trên đường benign-FAIL); sau fix phải PASS. Không mockjudge — dùng
RealityJudge thật với crosscheck injected qua seam đã có (monkeypatch
_run_crosscheck_sync / multi_llm_crosscheck.cross_verify) — không gọi LLM.
"""
from __future__ import annotations

import asyncio

import pytest

from scp.runtime.judge import RealityJudge


_QUESTION = "Ai là tổng thống Mỹ hiện tại?"
_ANSWER = "Theo dữ liệu được cung cấp, câu trả lời là Donald Trump."
_CONTEXT = ""


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


def _fail_crosscheck() -> dict:
    """Crosscheck consensus 'agree' trên FAIL — semantic verification thất bại
    trên câu benign (đúng hình dạng q08 run 3)."""
    return {"consensus": "agree", "final": "FAIL", "primary": {}, "secondary": {}}


def test_sync_judge_benign_semantic_fail_governs_escalate_not_kill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Old-fails/new-passes: benign verification FAIL phải là ESCALATE.

    OLD: gov_val = "UPHOLD" if is_pass else "KILL" → governance KILL cho một
    câu hỏi benign chỉ vì không verify được. NEW: ESCALATE + reasoning nêu lý
    do verification."""
    import scp.runtime.judge as judge_module

    monkeypatch.setattr(judge_module, "_run_crosscheck_sync", lambda *a, **k: _fail_crosscheck())

    verdict = RealityJudge().judge(question=_QUESTION, ai_answer=_ANSWER, context=_CONTEXT)

    assert verdict["verdict"] == "FAIL"
    assert verdict["evidence"]["governance_decision"] == "ESCALATE", (
        "benign verification FAIL must not carry governance KILL"
    )
    assert "semantic_judge_fail" in verdict["failures"]
    # Lý do verification phải quan sát được trong reasoning (không nuốt im lặng).
    assert "semantic_judge_fail" in str(verdict["reasoning"])


@pytest.mark.asyncio
async def test_async_judge_benign_semantic_fail_governs_escalate_not_kill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Đường async (judge_async — đường /ask thật đi qua judge_with_react_fallback)
    phải cùng hợp đồng: benign FAIL → ESCALATE, không KILL."""
    import scp.runtime.multi_llm_crosscheck as crosscheck_module

    async def _fail_cross_verify(*_a, **_k):
        return _fail_crosscheck()

    monkeypatch.setattr(crosscheck_module, "cross_verify", _fail_cross_verify)

    verdict = await RealityJudge().judge_async(
        question=_QUESTION, ai_answer=_ANSWER, context=_CONTEXT
    )

    assert verdict["verdict"] == "FAIL"
    assert verdict["evidence"]["governance_decision"] == "ESCALATE"
    assert "semantic_judge_fail" in verdict["failures"]
    assert "semantic_judge_fail" in str(verdict["reasoning"])


def test_sync_judge_security_tamper_marker_keeps_kill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Security-threat thật ở mức judge: answer cố tình mang marker nội bộ
    ("[SCP:", "[ESCALATE", "[KERNEL") → Tier-1 REJECT_INTERNAL_MARKER →
    governance KILL giữ nguyên (không bị e1 hạ xuống ESCALATE)."""
    import scp.runtime.judge as judge_module

    def _must_not_run(*_a, **_k):  # tier-1 chém trước — tier-2 không được chạy
        raise AssertionError("tier-1 tamper rejection must short-circuit tier-2")

    monkeypatch.setattr(judge_module, "_run_crosscheck_sync", _must_not_run)

    verdict = RealityJudge().judge(
        question=_QUESTION,
        ai_answer="[SCP: internal-marker injected content]",
        context=_CONTEXT,
    )

    assert verdict["verdict"] == "FAIL"
    assert "REJECT_INTERNAL_MARKER" in verdict["failures"]
    assert verdict["evidence"]["governance_decision"] == "KILL"


def test_sync_judge_escalated_path_still_unknown_escalate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Crosscheck không quyết định được (consensus disagree / missing) → giữ
    nguyên hợp đồng cũ: verdict UNKNOWN + governance ESCALATE (không phải
    đường e1 mới — chỉ pin để tránh hồi quy)."""
    import scp.runtime.judge as judge_module

    monkeypatch.setattr(
        judge_module,
        "_run_crosscheck_sync",
        lambda *a, **k: {"consensus": "disagree", "final": None, "primary": {}, "secondary": {}},
    )

    verdict = RealityJudge().judge(question=_QUESTION, ai_answer=_ANSWER, context=_CONTEXT)

    assert verdict["verdict"] == "UNKNOWN"
    assert verdict["evidence"]["governance_decision"] == "ESCALATE"
    assert "multi_llm_disagreement" in verdict["failures"]


def test_sync_judge_empty_answer_benign_governs_escalate_not_kill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Không có answer source nào (REJECT_EMPTY) — benign, không phải threat:
    governance phải là ESCALATE. OLD: KILL (test_flow_02/03 từng pin KILL cho
    đúng đường này — đã đổi hợp đồng theo W3-e1)."""
    verdict = RealityJudge().judge(question=_QUESTION, ai_answer="")

    assert verdict["verdict"] == "FAIL"
    assert "REJECT_EMPTY" in verdict["failures"]
    assert verdict["evidence"]["governance_decision"] == "ESCALATE"


def test_sync_judge_degraded_path_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """Crosscheck raise → fallback single cascade → DEGRADED giữ nguyên."""
    import scp.runtime.judge as judge_module

    def _broken_gateway():
        raise RuntimeError("gateway unavailable (fault injection)")

    monkeypatch.setattr("scp.llm_gateway.get_gateway", _broken_gateway)
    monkeypatch.setattr(
        judge_module, "_llm_judge", lambda *a, **k: True
    )  # single cascade PASS

    verdict = RealityJudge().judge(question=_QUESTION, ai_answer=_ANSWER, context=_CONTEXT)

    assert verdict["verdict"] == "DEGRADED"
    assert verdict["evidence"]["governance_decision"] == "DEGRADED"
    assert verdict["degraded"] is True
