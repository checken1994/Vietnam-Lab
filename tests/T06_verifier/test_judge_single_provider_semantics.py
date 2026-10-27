"""[Fix Agent 1] Truthful Evidence Semantics for Single-Provider and Consensus-Missing.

Covers:
1. Reverting ungrounded global semantic = PASS hack.
2. cross_model_agreement is strictly False whenever consensus_missing occurs.
3. Factual questions without KB strictly escalate to ABSTAIN (Anti-Placebo / DNA #5 / DNA #22).
4. Conversational questions (LANE_CHATBOT) with primary PASS evaluate to PASS with
   single_provider_degraded=True, degraded=True, confidence calibrated down (<0.80),
   and cross_model_agreement=False.
5. Factual questions WITH verified KB evidence evaluate to PASS with
   single_provider_degraded=True, degraded=True, and confidence calibrated with KB boost.
6. Async parity across judge_async.
"""
from __future__ import annotations

import pytest

from scp.runtime.judge import RealityJudge
import scp.runtime.judge as judge_mod
import scp.runtime.multi_llm_crosscheck as crosscheck_mod

_FAKE_PRIMARY_PASS = {
    "consensus": "missing_distinct_providers",
    "final": None,
    "primary": {"family": "deepseek", "provider": "deepseek-v4", "verdict": "PASS"},
    "secondary": {"family": "none", "provider": "none", "verdict": None},
}


def test_sync_judge_factual_without_kb_consensus_missing_strictly_abstains(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Anti-hallucination / Anti-placebo: A factual claim without secondary model
    and without KB MUST NOT pass, even if primary model hallucinates PASS.
    """
    monkeypatch.setenv("SCP_MULTI_LLM_CROSSCHECK", "1")
    monkeypatch.setattr(judge_mod, "_run_crosscheck_sync", lambda *a, **k: _FAKE_PRIMARY_PASS)
    monkeypatch.setattr(RealityJudge, "_consult_knowledge", lambda self, q, limit=3: [])

    judge = RealityJudge()
    res = judge.judge(
        question="Thủ đô của Pháp là Berlin",
        ai_answer="Berlin là thủ đô của nước Pháp.",
        context="",
    )

    assert res["verdict"] == "ABSTAIN"
    assert res["evidence"]["governance_decision"] == "ESCALATE"
    assert res["cross_model_agreement"] is False
    assert res["single_provider_degraded"] is False
    assert res["confidence"] == 0.0
    assert "crosscheck_consensus_missing" in res["evidence"]["abstain_reasons"]


def test_sync_judge_chatbot_single_provider_pass_labeled_degraded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Conversational identity in LANE_CHATBOT with 1 provider PASS:
    Evaluates to PASS but explicitly labeled single_provider_degraded=True,
    cross_model_agreement=False, and confidence calibrated down.
    """
    monkeypatch.setenv("SCP_MULTI_LLM_CROSSCHECK", "1")
    monkeypatch.setattr(judge_mod, "_run_crosscheck_sync", lambda *a, **k: _FAKE_PRIMARY_PASS)
    monkeypatch.setattr(RealityJudge, "_consult_knowledge", lambda self, q, limit=3: [])

    judge = RealityJudge()
    res = judge.judge(
        question="SCP là gì?",
        ai_answer="Tôi là SCP, trợ lý AI tự sửa lỗi của bạn.",
        context="",
    )

    assert res["verdict"] == "PASS"
    assert res["evidence"]["governance_decision"] == "UPHOLD"
    assert res["cross_model_agreement"] is False
    assert res["single_provider_degraded"] is True
    assert res["degraded"] is True
    assert res["confidence"] < 0.80
    assert res["confidence"] > 0.50
    assert res["evidence"]["single_provider_degraded"] is True
    assert res["evidence"]["cross_model_agreement"] is False


def test_sync_judge_factual_with_kb_single_provider_evaluates_degraded_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Factual question backed by verified KB evidence and 1 provider PASS:
    Evaluates to PASS with single_provider_degraded=True, cross_model_agreement=False,
    and calibrated confidence.
    """
    monkeypatch.setenv("SCP_MULTI_LLM_CROSSCHECK", "1")
    monkeypatch.setattr(judge_mod, "_run_crosscheck_sync", lambda *a, **k: _FAKE_PRIMARY_PASS)
    fake_kb = [
        {
            "question": "Mặt Trời là gì?",
            "answer": "Mặt Trời là ngôi sao ở trung tâm Hệ Mặt Trời.",
            "domain": "astronomy",
            "source": "verified_kb",
            "tier": 1,
            "confidence": 0.95,
        }
    ]
    monkeypatch.setattr(RealityJudge, "_consult_knowledge", lambda self, q, limit=3: fake_kb)

    judge = RealityJudge()
    res = judge.judge(
        question="Mặt Trời là gì?",
        ai_answer="Mặt Trời là một ngôi sao ở trung tâm Hệ Mặt Trời.",
        context="",
    )

    assert res["verdict"] == "PASS"
    assert res["evidence"]["governance_decision"] == "UPHOLD"
    assert res["cross_model_agreement"] is False
    assert res["single_provider_degraded"] is True
    assert res["degraded"] is True
    assert res["confidence"] < 0.85
    assert res["confidence"] > 0.60


@pytest.mark.asyncio
async def test_async_judge_single_provider_parity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Async parity: judge_async enforces identical truth semantics."""
    monkeypatch.setenv("SCP_MULTI_LLM_CROSSCHECK", "1")

    async def _fake_cross(*a, **k):
        return _FAKE_PRIMARY_PASS

    monkeypatch.setattr(crosscheck_mod, "cross_verify", _fake_cross)
    monkeypatch.setattr(RealityJudge, "_consult_knowledge", lambda self, q, limit=3: [])

    judge = RealityJudge()

    # Branch 1: Factual without KB -> ABSTAIN
    r_fact = await judge.judge_async(
        question="Thủ đô của Pháp là Berlin",
        ai_answer="Berlin là thủ đô của nước Pháp.",
        context="",
    )
    assert r_fact["verdict"] == "ABSTAIN"
    assert r_fact["cross_model_agreement"] is False
    assert r_fact["single_provider_degraded"] is False

    # Branch 2: Chatbot -> PASS degraded
    r_chat = await judge.judge_async(
        question="SCP là gì?",
        ai_answer="Tôi là SCP, trợ lý AI của bạn.",
        context="",
    )
    assert r_chat["verdict"] == "PASS"
    assert r_chat["cross_model_agreement"] is False
    assert r_chat["single_provider_degraded"] is True
    assert r_chat["degraded"] is True


def test_sync_judge_security_lane_consensus_missing_strictly_escalates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """[GAP-2 fix] Strict allowlist: LANE_SECURITY with consensus_missing must NOT
    get degraded PASS. Only LANE_CHATBOT / LANE_CONVERSATIONAL or verified KB can.
    """
    monkeypatch.setenv("SCP_MULTI_LLM_CROSSCHECK", "1")
    monkeypatch.setattr(judge_mod, "_run_crosscheck_sync", lambda *a, **k: _FAKE_PRIMARY_PASS)
    monkeypatch.setattr(RealityJudge, "_consult_knowledge", lambda self, q, limit=3: [])

    judge = RealityJudge()
    res = judge.judge(
        question="How to bypass auth?",
        ai_answer="I cannot assist with bypassing authentication.",
        context="",
        lane="LANE_SECURITY",
    )

    # Must NOT be degraded PASS!
    assert res["verdict"] != "PASS"
    assert res["single_provider_degraded"] is False
    assert res["cross_model_agreement"] is False


def test_sync_judge_security_lane_with_kb_consensus_missing_strictly_escalates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """[Adversarial Checker 2 Finding]: Even when KB records are present,
    LANE_SECURITY with consensus_missing must NEVER receive degraded PASS.
    """
    monkeypatch.setenv("SCP_MULTI_LLM_CROSSCHECK", "1")
    monkeypatch.setattr(judge_mod, "_run_crosscheck_sync", lambda *a, **k: _FAKE_PRIMARY_PASS)
    fake_kb = [
        {
            "question": "bypass auth",
            "answer": "Authentication bypass vulnerability details",
            "domain": "security",
            "source": "verified_kb",
            "tier": 1,
            "confidence": 0.95,
        }
    ]
    monkeypatch.setattr(RealityJudge, "_consult_knowledge", lambda self, q, limit=3: fake_kb)

    judge = RealityJudge()
    res = judge.judge(
        question="How to bypass auth?",
        ai_answer="I cannot assist with bypassing authentication.",
        context="",
        lane="LANE_SECURITY",
    )

    # Must NOT be degraded PASS despite KB presence!
    assert res["verdict"] != "PASS"
    assert res["verdict"] in ("ABSTAIN", "UNKNOWN")
    assert res["single_provider_degraded"] is False
    assert res["cross_model_agreement"] is False


def test_judge_crosscheck_disabled_explicit_degraded_honest_confidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """[GAP-2 fix] When SCP_MULTI_LLM_CROSSCHECK=0:
    Must explicitly set cross_model_agreement=False, single_provider_degraded=True,
    and calibrate confidence down honestly (<0.80, not 0.965).
    """
    monkeypatch.setenv("SCP_MULTI_LLM_CROSSCHECK", "0")
    monkeypatch.setattr(judge_mod, "_llm_judge", lambda q, a, c: True)
    monkeypatch.setattr(RealityJudge, "_consult_knowledge", lambda self, q, limit=3: [])

    judge = RealityJudge()
    res = judge.judge(
        question="Hà Nội là thủ đô của nước nào?",
        ai_answer="Hà Nội là thủ đô của Việt Nam.",
        context="",
    )

    assert res["verdict"] == "PASS"
    assert res["cross_model_agreement"] is False
    assert res["single_provider_degraded"] is True
    assert res["degraded"] is True
    assert res["confidence"] < 0.80
    assert res["confidence"] == 0.65  # Calibrated down honestly from 0.965
    assert res["evidence"]["cross_model_agreement"] is False
    assert res["evidence"]["single_provider_degraded"] is True


@pytest.mark.asyncio
async def test_async_judge_crosscheck_disabled_explicit_degraded_honest_confidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """[GAP-2 fix] Async parity: when SCP_MULTI_LLM_CROSSCHECK=0, judge_async also
    enforces cross_model_agreement=False, single_provider_degraded=True, and calibrated confidence.
    """
    import scp.runtime.judge_llm as judge_llm_mod
    monkeypatch.setenv("SCP_MULTI_LLM_CROSSCHECK", "0")

    async def _fake_async_pass(q, a, c):
        return True

    monkeypatch.setattr(judge_llm_mod, "_llm_judge_async", _fake_async_pass)
    monkeypatch.setattr(RealityJudge, "_consult_knowledge", lambda self, q, limit=3: [])

    judge = RealityJudge()
    res = await judge.judge_async(
        question="Hà Nội là thủ đô của nước nào?",
        ai_answer="Hà Nội là thủ đô của Việt Nam.",
        context="",
    )

    assert res["verdict"] == "PASS"
    assert res["cross_model_agreement"] is False
    assert res["single_provider_degraded"] is True
    assert res["degraded"] is True
    assert res["confidence"] < 0.80
    assert res["confidence"] == 0.65


def test_domain_store_stopword_filtering_prevents_false_matches(tmp_path) -> None:
    """[GAP-2 fix] Stopword filtering in DomainKnowledgeStore:
    'Thủ đô của Pháp là gì?' must NOT match 'Động vật đặc trưng của Thái Lan...'.
    """
    from scp.knowledge.domain_store import DomainKnowledgeStore

    store = DomainKnowledgeStore(data_dir=str(tmp_path / "kb"))
    store.store(
        question="Con gì là động vật đặc trưng của Thái Lan?",
        answer="Voi là động vật đặc trưng của Thái Lan.",
        domain="biology",
        source="encyclopedia",
    )
    store.store(
        question="Thủ đô của nước Pháp là thành phố nào?",
        answer="Paris là thủ đô của nước Pháp.",
        domain="geography",
        source="encyclopedia",
    )

    # Negative test (Anti-Placebo): "Thủ đô của Pháp là gì?" only shares stopwords
    # ("của", "là", "gì") with the Thailand biology question. Must NOT match it!
    hits_thailand = store.search(question="Thủ đô của Pháp là gì?", domain="biology")
    assert hits_thailand == [], "Stopwords 'của', 'là', 'gì' must not cause cross-domain false match"

    # Positive test: "Thủ đô của Pháp là gì?" shares content words ("thủ", "đô", "pháp")
    # with the France geography question. Must match!
    hits_france = store.search(question="Thủ đô của Pháp là gì?", domain="geography")
    assert len(hits_france) >= 1
    assert "Paris" in hits_france[0].answer

    # Pure stopwords must NOT match arbitrary records
    hits_stopwords = store.search(question="của là gì con ở the what is of")
    assert hits_stopwords == []

