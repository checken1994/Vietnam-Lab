"""Contract tests for Pilot USGS and F1 Mechanism-Level Fix (WP2).

Verifies Schema v1 canonical earthquake matching, superlative intent,
reverse magnitude lookup, verification intent, negative anti-placebo guards,
audit trail provenance, and feature flag toggling.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from scp.knowledge.earthquake_matcher import match_earthquake_query
from scp.knowledge.store import KnowledgeStore
from scp.runtime.domain_data_fork import _resolve_usgs


@pytest.fixture
def catalog_store() -> KnowledgeStore:
    """Fixture providing store pointed to canonical catalog."""
    repo_root = Path(__file__).resolve().parent.parent.parent
    cat_dir = repo_root / "data" / "knowledge" / "catalog"
    return KnowledgeStore(catalog_dir=cat_dir)


# ==============================================================================
# Positive Tests: Natural Queries Without Answer Tokens
# ==============================================================================

def test_valdivia_natural_questions_without_answer_token(catalog_store: KnowledgeStore) -> None:
    """Valdivia queries must match M9.5 without requiring '9.5' in query."""
    queries = [
        "Trận động đất Valdivia năm 1960 có độ lớn bao nhiêu?",
        "Động đất Valdivia mạnh bao nhiêu?",
        "Magnitude of the 1960 Valdivia earthquake?",
    ]
    for q in queries:
        res = match_earthquake_query(q, store=catalog_store)
        assert res is not None, f"Expected match for query: {q}"
        assert res["record_id"] == "geology.earthquake:valdivia-1960"
        assert "9.5" in res["text"]
        assert "Mw" in res["text"]
        assert "Valdivia" in res["text"] or "Chile" in res["text"]


def test_sumatra_natural_questions(catalog_store: KnowledgeStore) -> None:
    """Sumatra queries must match M9.1 without requiring '9.1' in query."""
    queries = [
        "Trận động đất Sumatra năm 2004 có độ lớn bao nhiêu?",
        "Động đất Sumatra 2004 mạnh cỡ nào?",
    ]
    for q in queries:
        res = match_earthquake_query(q, store=catalog_store)
        assert res is not None, f"Expected match for query: {q}"
        assert res["record_id"] == "geology.earthquake:sumatra-2004"
        assert "9.1" in res["text"]
        assert "Mw" in res["text"]
        assert "Sumatra" in res["text"]


def test_superlative_question_resolves_valdivia(catalog_store: KnowledgeStore) -> None:
    """Superlative query dynamically resolves to largest magnitude (Valdivia M9.5)."""
    q = "Trận động đất mạnh nhất từng ghi nhận là gì?"
    res = match_earthquake_query(q, store=catalog_store)
    assert res is not None
    assert res["record_id"] == "geology.earthquake:valdivia-1960"
    assert res["matched_by"] == "superlative_magnitude"
    assert "9.5" in res["text"]
    assert "mạnh nhất" in res["text"].lower()


def test_reverse_lookup_by_magnitude(catalog_store: KnowledgeStore) -> None:
    """Reverse lookup from magnitude 9.5 identifies Valdivia/Chile."""
    q = "Trận động đất 9.5 xảy ra ở đâu?"
    res = match_earthquake_query(q, store=catalog_store)
    assert res is not None
    assert res["record_id"] == "geology.earthquake:valdivia-1960"
    assert res["matched_by"] == "reverse_magnitude_lookup"
    assert "9.5" in res["text"]
    assert "Valdivia" in res["text"] or "Chile" in res["text"]


def test_verification_question_returns_factual_magnitude(catalog_store: KnowledgeStore) -> None:
    """Verification question returns actual factual record (M9.5)."""
    q = "Valdivia có phải mạnh 8.0 không?"
    res = match_earthquake_query(q, store=catalog_store)
    assert res is not None
    assert res["record_id"] == "geology.earthquake:valdivia-1960"
    assert "9.5" in res["text"]


# ==============================================================================
# Negative Anti-Placebo Tests: Fail-Closed Disambiguation
# ==============================================================================

def test_negative_bare_year_does_not_falsely_match_sumatra(catalog_store: KnowledgeStore) -> None:
    """Bare year '2004' without entity name MUST NOT match Sumatra (anti-placebo)."""
    q = "Động đất năm 2004 mạnh bao nhiêu?"
    res = match_earthquake_query(q, store=catalog_store)
    assert res is None, f"Expected None for bare year query, got: {res}"


def test_negative_unrelated_region_does_not_match(catalog_store: KnowledgeStore) -> None:
    """Unrelated region with year 2004 (California) MUST NOT match Sumatra."""
    q = "Động đất năm 2004 tại California"
    res = match_earthquake_query(q, store=catalog_store)
    assert res is None, f"Expected None for California query, got: {res}"


def test_negative_unknown_earthquake_in_catalog(catalog_store: KnowledgeStore) -> None:
    """Earthquake event not in catalog (Kobe 1995) returns None."""
    q = "Động đất năm 1995 tại Kobe"
    res = match_earthquake_query(q, store=catalog_store)
    assert res is None, f"Expected None for Kobe query, got: {res}"


def test_negative_unrelated_domain(catalog_store: KnowledgeStore) -> None:
    """Non-earthquake query returns None immediately."""
    q = "Hôm nay trời mưa không?"
    res = match_earthquake_query(q, store=catalog_store)
    assert res is None, f"Expected None for weather query, got: {res}"


def test_negative_conflicting_year_rejects_candidate(catalog_store: KnowledgeStore) -> None:
    """Query with conflicting year for entity (Valdivia + 2004) is rejected."""
    q = "Trận động đất Valdivia năm 2004 có độ lớn bao nhiêu?"
    res = match_earthquake_query(q, store=catalog_store)
    assert res is None, f"Expected None for conflicting year query, got: {res}"


# ==============================================================================
# Audit Trail & Provenance Verification
# ==============================================================================

def test_audit_trail_provenance(catalog_store: KnowledgeStore) -> None:
    """Result payload must contain complete audit trail metadata."""
    q = "Động đất Valdivia mạnh bao nhiêu?"
    res = match_earthquake_query(q, store=catalog_store)
    assert res is not None

    # Mandatory audit trail keys
    assert res["record_id"] == "geology.earthquake:valdivia-1960"
    assert res["dataset_version"] == catalog_store.dataset_version
    assert res["api_name"] == "USGSDataSource (Unified Knowledge Catalog)"
    assert res["api_url"] == "https://earthquake.usgs.gov/earthquakes/eventpage/official19600522191120_30/executive"
    assert res["review_status"] == "verified"
    assert isinstance(res["evidence"], str) and len(res["evidence"]) > 10
    assert "May 22, 1960" in res["evidence"]


# ==============================================================================
# Feature Flag Wiring in domain_data_fork
# ==============================================================================

def test_feature_flag_toggle_in_domain_data_fork(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify runtime integration switches between unified catalog and legacy fallback."""
    q_valdivia = "Trận động đất Valdivia năm 1960 có độ lớn bao nhiêu?"

    # Case 1: Flag OFF -> unified store bypassed, falls back to legacy path
    monkeypatch.setenv("SCP_UNIFIED_KNOWLEDGE_STORE", "off")
    legacy_res = _resolve_usgs(q_valdivia.lower())
    assert legacy_res is not None
    assert legacy_res["api_name"] == "USGSDataSource (Historical Catalog)"

    # Case 2: Flag ON -> routes through unified knowledge catalog
    monkeypatch.setenv("SCP_UNIFIED_KNOWLEDGE_STORE", "on")
    unified_res = _resolve_usgs(q_valdivia.lower())
    assert unified_res is not None
    assert unified_res["api_name"] == "USGSDataSource (Unified Knowledge Catalog)"
    assert unified_res["record_id"] == "geology.earthquake:valdivia-1960"
    assert unified_res["dataset_version"] == KnowledgeStore().dataset_version
    assert unified_res["review_status"] == "verified"
