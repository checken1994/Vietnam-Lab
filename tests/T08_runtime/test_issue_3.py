import pytest
from scp.runtime.judge import RealityJudge

@pytest.mark.asyncio
async def test_judge_async_semantic_none_escalates(monkeypatch):
    # Mock tier1 to pass
    monkeypatch.setattr("scp.runtime.judge.tier1_check", lambda *a, **k: type("T1", (), {"passed": True, "failures": []})())
    
    # Mock the LLM judge to return None
    async def _fake_llm_judge(*a, **k):
        return None
    monkeypatch.setattr("scp.runtime.judge_llm._llm_judge_async", _fake_llm_judge)
    monkeypatch.setenv("SCP_MULTI_LLM_CROSSCHECK", "0")
    
    judge = RealityJudge()
    
    # Mock verifier to pass structurally
    def _fake_verify(*a, **k):
        return type("V", (), {"verdict": "VERIFIED", "failures": []})()
    judge.verifier.verify = _fake_verify
    
    res = await judge.judge_async("q", "a")
    assert res["verdict"] == "UNKNOWN"
    assert "semantic_judge_unavailable" in res["failures"]
    assert res["evidence"]["governance_decision"] == "ESCALATE"
