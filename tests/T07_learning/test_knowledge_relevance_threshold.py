"""[Blocker 3 Probe] Verification of KB Relevance Threshold & 1-Token False Match Elimination.

Enforces Fail-Closed factual search in DomainKnowledgeStore:
1. Multi-token entity question ("Thủ đô của Pháp") matches relevant query ("Thủ đô của Pháp là gì?")
2. Multi-token entity question does NOT false match unrelated questions ("Món ăn nổi tiếng nhất của Pháp là gì?")
3. Single-token record ("Pháp") does NOT false match arbitrary questions mentioning France.
4. Near-exact queries ("Nước Pháp", "Pháp là gì?") match single-token entity record.
5. TF-IDF fallback rejects low-confidence single-token matches below relevance threshold.
"""
from __future__ import annotations

import pytest

from scp.knowledge.domain_store import DomainKnowledgeStore


@pytest.fixture
def kb_store(tmp_path):
    return DomainKnowledgeStore(data_dir=str(tmp_path / "kb"))


def test_kb_relevance_multi_token_match(kb_store):
    """Test 1: Specific question matches, unrelated question about different topic does NOT false match."""
    kb_store.store(
        question="Thủ đô của Pháp",
        answer="Paris",
        domain="geography",
        source="wikipedia",
        confidence=0.9,
    )

    # Query 1: "Thủ đô của Pháp là gì?" -> MATCHES
    hits1 = kb_store.search("Thủ đô của Pháp là gì?")
    assert len(hits1) >= 1
    assert hits1[0].question == "Thủ đô của Pháp"
    assert hits1[0].answer == "Paris"

    # Query 2: "Món ăn nổi tiếng nhất của Pháp là gì?" -> DOES NOT FALSE MATCH
    hits2 = kb_store.search("Món ăn nổi tiếng nhất của Pháp là gì?")
    assert len(hits2) == 0, f"Query 2 must NOT false match, got: {[r.question for r in hits2]}"


def test_kb_single_token_record_anti_placebo_rejection(kb_store):
    """Test 2: Single-token record ('Pháp') does NOT false match arbitrary questions about France."""
    kb_store.store(
        question="Pháp",
        answer="Một quốc gia ở Tây Âu",
        domain="geography",
        source="wikipedia",
        confidence=0.9,
    )

    # Arbitrary questions about food, ministers, population must NOT match single token record
    assert len(kb_store.search("Món ăn nổi tiếng nhất của Pháp là gì?")) == 0
    assert len(kb_store.search("Thủ tướng Pháp hiện nay là ai?")) == 0
    assert len(kb_store.search("Dân số của nước Pháp hiện nay là bao nhiêu?")) == 0

    # Near-exact and exact queries DO match
    hits_exact = kb_store.search("Pháp")
    assert len(hits_exact) == 1
    assert hits_exact[0].question == "Pháp"

    hits_definition = kb_store.search("Pháp là gì?")
    assert len(hits_definition) == 1
    assert hits_definition[0].question == "Pháp"

    hits_country = kb_store.search("Nước Pháp")
    assert len(hits_country) == 1
    assert hits_country[0].question == "Pháp"


def test_kb_coexistence_precise_dispatch(kb_store):
    """Test 3: Both specific record and entity record coexist in KB - queries dispatch to correct record."""
    kb_store.store(
        question="Thủ đô của Pháp",
        answer="Paris",
        domain="geography",
        source="wikipedia",
        confidence=0.9,
    )
    kb_store.store(
        question="Pháp",
        answer="Một quốc gia ở Tây Âu",
        domain="geography",
        source="wikipedia",
        confidence=0.9,
    )

    # Capital query matches "Thủ đô của Pháp"
    cap_hits = kb_store.search("Thủ đô của Pháp là gì?")
    assert len(cap_hits) == 1
    assert cap_hits[0].question == "Thủ đô của Pháp"
    assert cap_hits[0].answer == "Paris"

    # Food question does NOT match either record
    food_hits = kb_store.search("Món ăn nổi tiếng nhất của Pháp là gì?")
    assert len(food_hits) == 0

    # Minister question does NOT match either record
    minister_hits = kb_store.search("Thủ tướng Pháp hiện nay là ai?")
    assert len(minister_hits) == 0

    # Definition query matches entity record "Pháp"
    def_hits = kb_store.search("Pháp là gì?")
    assert len(def_hits) == 1
    assert def_hits[0].question == "Pháp"
