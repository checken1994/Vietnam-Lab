# SCP CIRCUIT: M02 — Q07 controlled Reflection / self-correction (hermetic).
"""T07/Q07 — wire Reflection (self-critique + 1 retry) vào đường /ask.

Bối cảnh đã đo: benchmark F_self_correction = 0/8 vì class Reflection tồn tại
trong scp/ai_patterns.py nhưng CHƯA từng được gọi; khi canonical verify trả
FAIL/UNKNOWN, finalize chỉ withhold.

Q07 nối Reflection CÓ KIỂM SOÁT:
  * critique->regenerate chạy TỐI ĐA 1 vòng, có budget + timeout;
  * answer round-2 chỉ được nhận nếu qua LẠI đúng canonical verify_response;
  * vẫn fail -> withhold như cũ (fail-closed, KHÔNG nới gate);
  * Reflection KHÔNG tự approve — verifier độc lập mới là gate;
  * không thêm LLM call vô hạn (không handler -> không reflection).

Không mạng: RealityJudge + handler + gateway đều stub. Không skip/xfail.
"""
from __future__ import annotations

import asyncio

import pytest

from scp.ask_kernel_adapter import AskKernelAdapter

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _no_crosscheck(monkeypatch):
    monkeypatch.setenv("SCP_MULTI_LLM_CROSSCHECK", "0")


class Req:
    """SimpleNamespace-like request (không có model_copy -> generic clone path)."""

    def __init__(self, question, ai_answer="", contexts=None, session_id=None):
        self.question = question
        self.ai_answer = ai_answer
        self.contexts = contexts or []
        self.retrieved_context = ""
        self.session_id = session_id or "q07-session"
        self.conversation_history: list[dict[str, str]] = []
        self.source = "benchmark_v2"
        self.domain = "general"
        self.domain_override = ""


class PydanticLikeReq(Req):
    """Request có model_copy -> pydantic AskRequest clone path."""

    def model_copy(self, *, update=None):
        clone = Req(**{k: getattr(self, k) for k in (
            "question", "ai_answer", "contexts", "session_id",
        )})
        clone.retrieved_context = self.retrieved_context
        clone.conversation_history = list(self.conversation_history)
        for key, value in (update or {}).items():
            setattr(clone, key, value)
        return clone


def _withheld_response():
    return {
        "verdict": "FAIL",
        "final_answer": "[SCP: Answer withheld — verdict: FAIL]",
        "confidence": 0.0,
        "governance_decision": "KILL",
        "v98_classification": {"provenance": "input_context_only"},
        "run_id": "run-r1", "trace_id": "trace-r1",
    }


def _good_response(answer="4"):
    return {
        "verdict": "PASS",
        "final_answer": answer,
        "confidence": 0.85,
        "governance_decision": "UPHOLD",
        "v98_classification": {"provenance": "input_context_only"},
        "run_id": "run-r2", "trace_id": "trace-r2",
    }


@pytest.fixture()
def judge_that_only_passes(monkeypatch):
    """Canonical verifier stub: judge PASS chỉ khi answer nằm trong set.

    Patch TRÊN scp.runtime.judge.RealityJudge (nơi verify_response import lúc
    gọi) -> điều khiển đúng biến judge_pass, trong khi verdict_pass/
    governance_uphold/web_fallback/provenance/grounding vẫn là logic canonical
    THẬT trong verify_response (gate không bị thay thế)."""
    state = {"pass_set": {"4"}, "calls": 0}

    class _FakeJudge:
        async def judge_async(self, question, ai_answer="", context="", **kw):
            state["calls"] += 1
            passed = ai_answer in state["pass_set"]
            return {"verdict": "PASS" if passed else "UNKNOWN", "final_answer": ai_answer}

    monkeypatch.setattr("scp.runtime.judge.RealityJudge", _FakeJudge)
    return state


def _stats_guard():
    from scp.runtime.question_router import route_stats_snapshot

    return route_stats_snapshot()


def _correction_delta(before):
    from scp.runtime.question_router import route_stats_snapshot

    after = route_stats_snapshot()
    return {
        "attempts": after["correction_attempts"] - before["correction_attempts"],
        "success": after["correction_success"] - before["correction_success"],
        "fail": after["correction_fail"] - before["correction_fail"],
        "timeout": after["correction_timeout"] - before["correction_timeout"],
    }


# ---------------------------------------------------------------------------
# (1) Round-2 accept: corrupted answer được self-refine thành answer đúng
# ---------------------------------------------------------------------------
async def test_reflection_accepts_corrected_answer_only_after_canonical_verify(
    tmp_path, judge_that_only_passes
):
    adapter = AskKernelAdapter(db_path=":memory:", trace_path=str(tmp_path / "t.jsonl"))
    before = _stats_guard()
    calls = {"n": 0}

    async def handler(req, request):
        calls["n"] += 1
        # Round 1: client gửi ai_answer corrupted -> primary pipeline withhold.
        if req.ai_answer:
            return _withheld_response()
        # Round 2 (reflection): ai_answer được xóa -> pipeline sinh answer đúng.
        return _good_response("4")

    req = Req("2 + 2 = ?", ai_answer="5")
    safe = await adapter.run_rag(req, request=None, handler=handler)

    assert calls["n"] == 2, "phải chạy đúng 1 vòng regenerate"
    assert safe["final_answer"] == "4", "answer round-2 đã qua verify phải thoát ra"
    assert safe["verdict"] == "PASS"
    assert _correction_delta(before) == {"attempts": 1, "success": 1, "fail": 0, "timeout": 0}
    adapter.kernel.close()


# ---------------------------------------------------------------------------
# (2) Still-fail -> withhold như cũ (fail-closed)
# ---------------------------------------------------------------------------
async def test_reflection_still_failing_withholds_like_before(tmp_path, judge_that_only_passes):
    adapter = AskKernelAdapter(db_path=":memory:", trace_path=str(tmp_path / "t.jsonl"))
    before = _stats_guard()
    calls = {"n": 0}

    async def handler(req, request):
        calls["n"] += 1
        return _withheld_response()  # không bao giờ sửa được

    req = Req("unanswerable?", ai_answer="wrong")
    safe = await adapter.run_rag(req, request=None, handler=handler)

    assert safe["final_answer"].startswith("[SCP: Answer withheld")
    assert safe["verdict"] == "FAIL"
    assert _correction_delta(before) == {"attempts": 1, "success": 0, "fail": 1, "timeout": 0}
    adapter.kernel.close()


# ---------------------------------------------------------------------------
# (3) Reflection KHÔNG tự approve: refined answer tự nhận PASS nhưng canonical
#     judge vẫn FAIL -> withhold. Đây là phát biểu cốt lõi của invariant.
# ---------------------------------------------------------------------------
async def test_refined_answer_self_claiming_pass_is_still_rejected_by_judge(
    tmp_path, judge_that_only_passes
):
    adapter = AskKernelAdapter(db_path=":memory:", trace_path=str(tmp_path / "t.jsonl"))
    before = _stats_guard()
    judge_that_only_passes["pass_set"] = {"999"}  # không answer nào handler trả qua

    async def handler(req, request):
        if req.ai_answer:
            return _withheld_response()
        # Refined answer TỰ GÁN verdict=PASS/governance=UPHOLD nhưng verifier
        # độc lập không đồng ý -> canonical verify_response phải CONTRADICTED.
        return _good_response("7")

    req = Req("2 + 2 = ?", ai_answer="5")
    safe = await adapter.run_rag(req, request=None, handler=handler)

    assert safe["final_answer"].startswith("[SCP: Answer withheld")
    assert _correction_delta(before) == {"attempts": 1, "success": 0, "fail": 1, "timeout": 0}
    adapter.kernel.close()


# ---------------------------------------------------------------------------
# (4) Gate không nới: answer round-2 đúng vẫn phải pass web_fallback/provenance
# ---------------------------------------------------------------------------
async def test_round2_answer_with_web_fallback_is_not_loosened(tmp_path, judge_that_only_passes):
    adapter = AskKernelAdapter(db_path=":memory:", trace_path=str(tmp_path / "t.jsonl"))
    before = _stats_guard()

    async def handler(req, request):
        if req.ai_answer:
            return _withheld_response()
        r = _good_response("4")
        r["web_fallback_used"] = True  # canonical check web_fallback_not_used -> False
        return r

    req = Req("2 + 2 = ?", ai_answer="5")
    safe = await adapter.run_rag(req, request=None, handler=handler)

    # Answer "4" đúng nhưng dùng web fallback -> withheld (không nới gate).
    assert safe["final_answer"].startswith("[SCP: Answer withheld")
    assert _correction_delta(before) == {"attempts": 1, "success": 0, "fail": 1, "timeout": 0}
    adapter.kernel.close()


# ---------------------------------------------------------------------------
# (5) Kill switch: tắt reflection -> handler chạy 1 lần, withhold ngay
# ---------------------------------------------------------------------------
async def test_kill_switch_disables_reflection(tmp_path, judge_that_only_passes, monkeypatch):
    monkeypatch.setenv("SCP_ASK_REFLECTION", "0")
    adapter = AskKernelAdapter(db_path=":memory:", trace_path=str(tmp_path / "t.jsonl"))
    before = _stats_guard()
    calls = {"n": 0}

    async def handler(req, request):
        calls["n"] += 1
        return _withheld_response() if req.ai_answer else _good_response("4")

    req = Req("2 + 2 = ?", ai_answer="5")
    safe = await adapter.run_rag(req, request=None, handler=handler)

    assert calls["n"] == 1, "kill switch phải chặn hẳn vòng regenerate"
    assert safe["final_answer"].startswith("[SCP: Answer withheld")
    assert _correction_delta(before) == {"attempts": 0, "success": 0, "fail": 0, "timeout": 0}
    adapter.kernel.close()


# ---------------------------------------------------------------------------
# (6) Budget = 1: round-2 sai cũng KHÔNG lặp lại vòng 3 (tối đa initial + 1 regen)
# ---------------------------------------------------------------------------
async def test_budget_is_one_round_no_infinite_llm(tmp_path, judge_that_only_passes):
    adapter = AskKernelAdapter(db_path=":memory:", trace_path=str(tmp_path / "t.jsonl"))
    calls = {"n": 0}

    async def handler(req, request):
        calls["n"] += 1
        if req.ai_answer:
            return _withheld_response()   # round 1 fail -> epistemic hold
        return _good_response("wrong")     # round 2 vẫn sai -> canonical judge FAIL

    req = Req("2 + 2 = ?", ai_answer="5")
    safe = await adapter.run_rag(req, request=None, handler=handler)

    assert calls["n"] == 2, "đúng 1 initial + 1 regen, không vòng lặp thứ 3"
    assert safe["final_answer"].startswith("[SCP: Answer withheld")
    adapter.kernel.close()


# ---------------------------------------------------------------------------
# (7) Timeout: regenerate quá budget -> withhold fail-closed, không treo
# ---------------------------------------------------------------------------
async def test_reflection_timeout_bounded_and_withholds(tmp_path, judge_that_only_passes, monkeypatch):
    monkeypatch.setenv("SCP_ASK_REFLECTION_TIMEOUT_SECONDS", "0.05")
    adapter = AskKernelAdapter(db_path=":memory:", trace_path=str(tmp_path / "t.jsonl"))
    before = _stats_guard()

    async def slow_handler(req, request):
        if req.ai_answer:
            return _withheld_response()
        await asyncio.sleep(0.3)  # vượt budget 0.05s
        return _good_response("4")

    req = Req("2 + 2 = ?", ai_answer="5")
    safe = await adapter.run_rag(req, request=None, handler=slow_handler)

    assert safe["final_answer"].startswith("[SCP: Answer withheld")
    delta = _correction_delta(before)
    assert delta["attempts"] == 1 and delta["timeout"] == 1 and delta["success"] == 0
    adapter.kernel.close()


# ---------------------------------------------------------------------------
# (8) Invalid reflection timeout -> fail-closed về default, không crash
# ---------------------------------------------------------------------------
async def test_invalid_reflection_timeout_falls_back_to_default(monkeypatch):
    from scp.ask_kernel_adapter import (
        DEFAULT_ASK_REFLECTION_TIMEOUT_SECONDS,
        ask_reflection_timeout_seconds,
    )

    for junk in ("", "abc", "0", "-3", "inf", "1000"):
        monkeypatch.setenv("SCP_ASK_REFLECTION_TIMEOUT_SECONDS", junk)
        assert ask_reflection_timeout_seconds() == DEFAULT_ASK_REFLECTION_TIMEOUT_SECONDS


# ---------------------------------------------------------------------------
# (9) Không handler (legacy finalize call-site) -> KHÔNG reflection, giữ hành vi cũ
# ---------------------------------------------------------------------------
async def test_finalize_without_handler_is_unchanged(tmp_path, judge_that_only_passes):
    adapter = AskKernelAdapter(db_path=":memory:", trace_path=str(tmp_path / "t.jsonl"))
    before = _stats_guard()
    req = Req("2 + 2 = ?", ai_answer="5")
    task = adapter.begin(req.question, list(req.contexts), req.retrieved_context, req.session_id)
    # finalize gọi thẳng (không handler) với response withhold -> escalate, không regen.
    result = await adapter.finalize(task, _withheld_response(), req)
    assert result["verification"]["verdict"] != "VERIFIED"
    assert result["reflection"]["attempted"] is False
    assert _correction_delta(before) == {"attempts": 0, "success": 0, "fail": 0, "timeout": 0}
    assert adapter.kernel.get_task(task["task_id"])["state"] == "HUMAN_REVIEW"
    adapter.kernel.close()


# ---------------------------------------------------------------------------
# (10) clone path pydantic-like giữ nguyên question/contexts, xóa ai_answer
# ---------------------------------------------------------------------------
async def test_clone_for_reflection_preserves_question_clears_answer(tmp_path):
    adapter = AskKernelAdapter(db_path=":memory:", trace_path=str(tmp_path / "t.jsonl"))
    req = PydanticLikeReq("capital of France?", ai_answer="London", contexts=["Paris is the capital"])
    clone = adapter._clone_for_reflection(req, _withheld_response(), {"failures": ["judge_pass"], "grounded_ratio": 0.2})
    assert clone is not None
    assert clone.question == "capital of France?"          # question bất biến
    assert clone.contexts == ["Paris is the capital"]      # evidence bất biến
    assert clone.ai_answer == ""                            # force regenerate
    assert any("tự phê bình" in t["content"] for t in clone.conversation_history)
    adapter.kernel.close()


# ---------------------------------------------------------------------------
# (11) ai_patterns Reflection primitives
# ---------------------------------------------------------------------------
async def test_reflection_primitives_contract():
    from scp.ai_patterns import Reflection

    assert Reflection.MAX_REFLECTIONS == 1  # budget hợp đồng
    assert Reflection.should_reflect({"verdict": "CONTRADICTED"}) is True
    assert Reflection.should_reflect({"verdict": "INSUFFICIENT"}) is True
    assert Reflection.should_reflect({"verdict": "VERIFIED"}) is False
    turns = Reflection.critique_turns("q", "wrong answer", {"failures": ["judge_pass", "missing_answer"]})
    # critique là CONTEXT (assistant/user), không chứa verdict do Reflection tự quyết.
    assert turns and all(t["role"] in {"user", "assistant"} for t in turns)
    # Withheld answer không được lặp lại nguyên văn thành claim "đã từ chối".
    assert Reflection.critique_turns("q", "[SCP: Answer withheld]", {"failures": ["missing_answer"]})[-1]["role"] == "user"
