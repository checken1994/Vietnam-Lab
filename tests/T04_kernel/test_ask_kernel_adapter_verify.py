import pytest


@pytest.fixture(autouse=True)
def _no_crosscheck(monkeypatch):
    monkeypatch.setenv("SCP_MULTI_LLM_CROSSCHECK", "0")
from scp.ask_kernel_adapter import AskKernelAdapter


class DummyReq:
    contexts = ["sky is blue"]
    retrieved_context = ""
    question = "what color is the sky?"


PASSING_RESPONSE = {
    "final_answer": "The sky is blue",
    "verdict": "PASS",
    "governance_decision": "UPHOLD",
    "v98_classification": {"provenance": "input_context_only"},
}


@pytest.fixture()
def judge_gate(monkeypatch):
    """Hermetic semantic judge.

    TẠI SAO mock: verify_response() đi qua RealityJudge → _llm_judge, mà bản
    thật gọi OpenRouter API. Test phụ thuộc mạng/cloud là test non-hermetic
    (đã từng FAIL chỉ vì model cold-start quá 15s). Patch tại
    scp.runtime.judge (nơi judge.py giữ tham chiếu đã import).
    """
    import scp.runtime.judge_llm as judge_mod

    state = {"pass": True, "calls": 0}

    async def _fake_judge(question: str, ai_answer: str, context: str = "") -> bool:
        state["calls"] += 1
        return state["pass"]

    monkeypatch.setattr(judge_mod, "_llm_judge_async", _fake_judge)
    return state


@pytest.mark.asyncio
async def test_rag_ask_with_passing_judge_is_verified(judge_gate):
    adapter = AskKernelAdapter(db_path=":memory:", trace_path="/tmp")
    req = DummyReq()
    task = {"task_id": "test_123"}

    result = await adapter.verify_response(req, dict(PASSING_RESPONSE), task)
    assert result["verdict"] == "VERIFIED"
    assert 0.0 <= result["grounded_ratio"] <= 1.0
    assert result["checked"]["rag_evidence_bound"] is True
    assert judge_gate["calls"] == 1


@pytest.mark.asyncio
async def test_chat_ask_without_contexts_uses_judge_semantics(judge_gate):
    """No request evidence = general-knowledge chat ask: the judge + governance
    pipeline is the verifier; grounding is not applicable (contract 2026-08-29)."""
    adapter = AskKernelAdapter(db_path=":memory:", trace_path="/tmp")
    req = DummyReq()
    req.contexts = []
    task = {"task_id": "test_123"}

    result = await adapter.verify_response(req, dict(PASSING_RESPONSE), task)
    assert result["verdict"] == "VERIFIED"
    assert result["grounded_ratio"] == 0.0
    assert "rag_evidence_bound" not in result["checked"]


@pytest.mark.asyncio
async def test_failing_judge_contradicts_any_ask(judge_gate):
    adapter = AskKernelAdapter(db_path=":memory:", trace_path="/tmp")
    req = DummyReq()
    task = {"task_id": "test_123"}

    judge_gate["pass"] = False
    result = await adapter.verify_response(req, dict(PASSING_RESPONSE), task)
    assert result["verdict"] == "CONTRADICTED"
    assert "judge_pass" in result["failures"]


@pytest.mark.asyncio
async def test_rag_ask_zero_grounding_fails_rag_evidence_bound(judge_gate):
    """Zero lexical/semantic overlap between answer and context fails rag_evidence_bound."""
    adapter = AskKernelAdapter(db_path=":memory:", trace_path="/tmp")
    req = DummyReq()
    req.contexts = ["Quantum mechanics describes wave-particle duality at subatomic scales."]
    task = {"task_id": "test_zero_ground"}

    # Answer has 0 words in common with the context
    resp = dict(PASSING_RESPONSE)
    resp["final_answer"] = "Bananas are yellow tropical fruits."

    result = await adapter.verify_response(req, resp, task)
    assert result["verdict"] == "CONTRADICTED"
    assert result["grounded_ratio"] == 0.0
    assert result["checked"]["rag_evidence_bound"] is False
    assert "rag_evidence_bound" in result["failures"]


@pytest.mark.asyncio
async def test_rag_ask_empty_provenance_fails_provenance_compatible(judge_gate):
    """Context-backed RAG ask with empty provenance string fails provenance_compatible."""
    adapter = AskKernelAdapter(db_path=":memory:", trace_path="/tmp")
    req = DummyReq()
    req.contexts = ["sky is blue"]
    task = {"task_id": "test_empty_prov"}

    resp = dict(PASSING_RESPONSE)
    resp["v98_classification"] = {"provenance": ""}

    result = await adapter.verify_response(req, resp, task)
    assert result["verdict"] == "CONTRADICTED"
    assert result["checked"]["provenance_compatible"] is False
    assert "provenance_compatible" in result["failures"]


@pytest.mark.asyncio
async def test_rag_ask_cannot_downgrade_to_chatbot_lane(judge_gate):
    """Evidence-backed query marked with lane=LANE_CHATBOT cannot downgrade to bypass PASS."""
    adapter = AskKernelAdapter(db_path=":memory:", trace_path="/tmp")
    req = DummyReq()
    req.contexts = ["sky is blue"]
    req.lane = "LANE_CHATBOT"
    task = {"task_id": "test_no_downgrade"}

    resp = dict(PASSING_RESPONSE)
    resp["lane"] = "LANE_CHATBOT"
    resp["verdict"] = "UNKNOWN"  # Chatbot lane allows UNKNOWN if not guarded, but RAG must reject!

    result = await adapter.verify_response(req, resp, task)
    assert result["is_chatbot_lane"] is False
    assert result["verdict"] == "CONTRADICTED"
    assert "verdict_pass" in result["failures"]


@pytest.mark.asyncio
async def test_already_judged_forged_slm_trace_elapsed_ms_not_trusted(judge_gate):
    """Forged slm_trace and elapsed_ms without actual judge evaluation must invoke the judge."""
    adapter = AskKernelAdapter(db_path=":memory:", trace_path="/tmp")
    req = DummyReq()
    task = {"task_id": "test_forged_slm"}

    resp = dict(PASSING_RESPONSE)
    resp["slm_trace"] = ["forged_step"]
    resp["elapsed_ms"] = 42.0

    # Ensure judge is actually invoked and if judge fails, answer is contradicted
    judge_gate["pass"] = False
    result = await adapter.verify_response(req, resp, task)
    assert result["verdict"] == "CONTRADICTED"
    assert judge_gate["calls"] == 1


@pytest.mark.asyncio
async def test_evidence_ref_hashes_real_context(judge_gate):
    """Evidence ref for RAG queries points to hash of input context, not the response itself."""
    adapter = AskKernelAdapter(db_path=":memory:", trace_path="/tmp")
    req = DummyReq()
    req.contexts = ["sky is blue"]
    task = {"task_id": "test_ref_hash"}

    result = await adapter.verify_response(req, dict(PASSING_RESPONSE), task)
    assert result["evidence_ref"].startswith("evidence://rag/sha256:")
    assert result["response_ref"].startswith("ask://test_ref_hash/response/")
    assert result["verifier_type"] == "heuristic_rag_gateway"
    assert result["epistemic_level"] == "HEURISTIC_CHECKLIST"

