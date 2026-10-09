"""Contract tests for domain knowledge rules, FactSeparator, and hallucination rejection (R4-F01)."""
from __future__ import annotations

from scp.knowledge.domain_knowledge import (
    ALL_FACT_STOPWORDS,
    FactSeparator,
    _extract_capitalized_entities,
    _extract_content_tokens,
)


def test_fact_stopwords_completeness():
    """Verify that essential Vietnamese and English stopwords are present."""
    sample_stopwords = {
        "là", "của", "và", "các", "có", "trong", "cho", "với", "được", "về",
        "thủ", "đô", "is", "the", "of", "in", "and",
    }
    assert sample_stopwords.issubset(ALL_FACT_STOPWORDS)


def test_content_tokens_filtering():
    """Verify stopwords are stripped and content tokens extracted."""
    text = "Canberra là thủ đô của Úc."
    tokens = _extract_content_tokens(text)
    assert "thủ" not in tokens
    assert "đô" not in tokens
    assert "là" not in tokens
    assert "của" not in tokens
    assert "canberra" in tokens
    assert "úc" in tokens


def test_capitalized_entities_extraction():
    """Verify capitalized entities are extracted properly."""
    text = "Sydney và Melbourne là các thành phố của nước Úc."
    entities = _extract_capitalized_entities(text)
    assert "sydney" in entities
    assert "melbourne" in entities
    assert "úc" in entities


def test_fact_separator_rejects_entity_hallucination():
    """R4-F01: Verify FactSeparator does NOT award FACT_VERIFIED when entities conflict."""
    separator = FactSeparator()
    retrieval = {
        "clean_evidence_snippets": ["Canberra là thủ đô của Úc."],
        "sources_consulted": ["knowledge_base"],
    }
    # False claim with entity 'Sydney' replacing 'Canberra'
    res = separator.separate(
        question="Thủ đô của Úc là gì?",
        answer="Sydney là thủ đô của Úc.",
        lane="LANE_FACTUAL",
        confidence=0.95,
        retrieval_result=retrieval,
    )
    assert res["confidence_badge"]["badge"] != "FACT_VERIFIED"
    assert res["confidence_badge"]["badge"] == "UNVERIFIED_CONJECTURE"
    assert len(res["verified_facts"]) == 0


def test_fact_separator_accepts_grounded_factual_claim():
    """R4-F01: Verify FactSeparator awards FACT_VERIFIED when evidence aligns."""
    separator = FactSeparator()
    retrieval = {
        "clean_evidence_snippets": ["Canberra là thủ đô của Úc."],
        "sources_consulted": ["knowledge_base"],
        "kb_hits": [
            {
                "question": "Thủ đô của Úc là gì?",
                "answer": "Canberra là thủ đô của Úc.",
                "source": "knowledge_base",
                "source_url": "https://vi.wikipedia.org/wiki/Canberra",
                "confidence": 0.98,
            }
        ],
    }
    res = separator.separate(
        question="Thủ đô của Úc là gì?",
        answer="Canberra là thủ đô của Úc.",
        lane="LANE_FACTUAL",
        confidence=0.95,
        retrieval_result=retrieval,
    )
    assert res["confidence_badge"]["badge"] == "FACT_VERIFIED"
    assert len(res["verified_facts"]) >= 1
    assert "Canberra" in res["verified_facts"][0]["claim"]


def test_fact_separator_conversational_lane_no_false_verification():
    """Verify conversational lane produces CONVERSATIONAL badge and 0 verified facts."""
    separator = FactSeparator()
    res = separator.separate(
        question="Chào bạn nhé",
        answer="Chào bạn! Tôi có thể giúp gì cho bạn hôm nay?",
        lane="LANE_CHATBOT",
        confidence=0.88,
    )
    assert res["confidence_badge"]["badge"] == "CONVERSATIONAL"
    assert res["verified_facts"] == []


def test_fact_separator_rejects_negated_evidence_fact01():
    """FACT-01: Verify FactSeparator does NOT match when evidence expresses negation."""
    from scp.knowledge.claim_extractor import Claim

    separator = FactSeparator()
    # Claim: Berlin is the capital of France
    claim = Claim(claim_id="c1", claim_type="entity", text="Berlin is the capital of France", entity="Berlin", target="France")

    # Negated evidence
    neg_evidence = "Berlin is definitely not the capital of France, Paris is."
    assert separator._is_match(claim, neg_evidence) is False

    # Vietnamese negation evidence
    vn_claim = Claim(claim_id="c2", claim_type="entity", text="Hà Nội là thành phố của Pháp", entity="Hà Nội", target="Pháp")
    vn_neg_evidence = "Hà Nội không phải là thành phố của Pháp."
    assert separator._is_match(vn_claim, vn_neg_evidence) is False


def test_fact_separator_confidence_calibration_fact02():
    """FACT-02: Low confidence in conversational lane is preserved without artificial inflation to 0.85."""
    separator = FactSeparator()
    res = separator.separate(
        question="Hello",
        answer="Hi there",
        lane="LANE_CHATBOT",
        confidence=0.35,
    )
    assert res["confidence_badge"]["badge"] == "CONVERSATIONAL"
    assert res["confidence_badge"]["score"] == 0.35
