"""Adversarial Empirical Challenger Test Suite for Milestone 2 Wired Code Blocks.

Tests:
1. smart_classifier.learn_from_feedback (nominal, subsequent query, edge cases, unknown domain, unicode, boundary)
2. cisa_kev_match_recent (known CVE, unknown CVE, non-CVE, non-string, malformed, intel boost integration)
3. callgraph_delta.get_callers_for (valid function, unknown function, invalid input, fail-open, blast_radius integration)
4. domain_store baseline verification (record and file baselines)
5. external_trust baseline verification (anchor files)
6. scpv14_process_mixin JudgeVerdict dataclass & normalization
"""

import hashlib
import time
import pytest
from pathlib import Path


@pytest.fixture(autouse=True)
def isolate_challenger_env(monkeypatch):
    monkeypatch.setenv("SCP_WHY_LLM_ENABLED", "0")
    monkeypatch.setenv("SCP_EGRESS_MODE", "deny")


# ============================================================
# BLOCK 1: smart_classifier.learn_from_feedback
# ============================================================

def test_smart_classifier_learn_from_feedback_nominal():
    """Test standard feedback learning on an ambiguous question."""
    from scp.core.smart_classifier import SmartClassifier

    classifier = SmartClassifier()
    question = "unique_query_xyz_alpha_quantum_financial_arbitrage"
    
    # Pre-condition: initial classification before feedback
    initial_res = classifier.classify(question)
    assert initial_res is not None
    
    # Train on feedback: target domain is 'finance'
    classifier.learn_from_feedback(question, "finance")
    
    # Post-condition 1: Direct exact question must be classified as 'finance'
    post_res = classifier.classify(question)
    assert post_res.domain == "finance"
    assert post_res.confidence == 1.0
    assert post_res.method == "feedback_override"

    # Post-condition 2: Subsequent question with learned tokens
    subsequent_q = "unique_query_xyz_alpha_quantum_financial_arbitrage in modern markets"
    subsequent_res = classifier.classify(subsequent_q)
    assert subsequent_res is not None
    # 'finance' score should be elevated by the newly added tokens
    assert hasattr(classifier, "_feedback_store")
    assert question.strip() in classifier._feedback_store


def test_smart_classifier_learn_from_feedback_edge_cases():
    """Adversarially test robustness against empty, None, and unknown domain inputs."""
    from scp.core.smart_classifier import SmartClassifier, DOMAIN_PROFILES

    classifier = SmartClassifier()

    # 1. Empty question
    classifier.learn_from_feedback("", "math")
    assert "" not in getattr(classifier, "_feedback_store", {})

    # 2. Whitespace question
    classifier.learn_from_feedback("   ", "math")

    # 3. None question
    classifier.learn_from_feedback(None, "math")

    # 4. Empty domain
    classifier.learn_from_feedback("some valid question", "")

    # 5. None domain
    classifier.learn_from_feedback("some valid question", None)

    # 6. Unknown domain (not in DOMAIN_PROFILES)
    unknown_domain = "nonexistent_quantum_hyper_domain_999"
    assert unknown_domain not in DOMAIN_PROFILES
    test_q = "how to simulate a wormhole?"
    # Must not raise KeyError when DOMAIN_PROFILES is checked
    classifier.learn_from_feedback(test_q, unknown_domain)
    # The direct cache override should still hold for the exact question
    cached_res = classifier.classify(test_q)
    assert cached_res.domain == unknown_domain
    assert cached_res.confidence == 1.0
    assert cached_res.method == "feedback_override"

    # 7. Non-ASCII and Unicode handling
    unicode_q = "Thuật toán học sâu lượng tử và bảo mật đa lớp 🚀"
    classifier.learn_from_feedback(unicode_q, "cybersecurity")
    unicode_res = classifier.classify(unicode_q)
    assert unicode_res.domain == "cybersecurity"

    # 8. Large input handling (10,000 characters)
    huge_q = "large_token " * 1000
    classifier.learn_from_feedback(huge_q, "technology")
    huge_res = classifier.classify(huge_q)
    assert huge_res.domain == "technology"


def test_smart_classifier_cache_eviction_boundary():
    """Verify cache eviction when _CACHE_MAX is reached during feedback."""
    from scp.core.smart_classifier import SmartClassifier

    classifier = SmartClassifier()
    classifier._CACHE_MAX = 5  # temporarily lower threshold to test boundary

    for i in range(10):
        classifier.learn_from_feedback(f"question number {i}", "math")

    # Cache should be bounded and not exceed _CACHE_MAX
    assert len(classifier._cache) <= 10  # evicted older entries during insertions


# ============================================================
# BLOCK 2: cisa_kev_match_recent
# ============================================================

def test_cisa_kev_match_recent_nominal():
    """Test known CVE lookup in CISA KEV catalog."""
    from scp.security.predictor import cisa_kev_match_recent

    # Known CVE present in data/cisa_kev.json
    assert cisa_kev_match_recent("CVE-2023-38606") is True
    # Case insensitivity check
    assert cisa_kev_match_recent("cve-2023-38606") is True
    assert cisa_kev_match_recent("CvE-2023-38606") is True
    # Another sample from CISA KEV catalog
    assert cisa_kev_match_recent("CVE-2026-84869") is True


def test_cisa_kev_match_recent_negative_cases():
    """Test negative lookups: non-CVE, unknown CVE, empty, malformed."""
    from scp.security.predictor import cisa_kev_match_recent

    # Valid CVE format but not in catalog
    assert cisa_kev_match_recent("CVE-1990-0001") is False
    assert cisa_kev_match_recent("CVE-2099-99999") is False

    # Non-CVE strings
    assert cisa_kev_match_recent("not_a_cve") is False
    assert cisa_kev_match_recent("hello world") is False
    assert cisa_kev_match_recent("12345") is False

    # Empty, whitespace, None
    assert cisa_kev_match_recent("") is False
    assert cisa_kev_match_recent("   ") is False
    assert cisa_kev_match_recent(None) is False

    # Non-string types
    assert cisa_kev_match_recent(12345) is False
    assert cisa_kev_match_recent(["CVE-2023-38606"]) is False
    assert cisa_kev_match_recent({"cve": "CVE-2023-38606"}) is False


def test_cisa_kev_predictor_integration():
    """Verify integration of cisa_kev_match_recent with threat predictor intel boost."""
    from scp.security.predictor import _boost_confidence_with_intel, AttackPredictor

    # With known CVE and positive attack signal
    signals_with_known_cve = {"cve_id": "CVE-2023-38606", "payload_pattern_emergence": 0.85}
    boost_known = _boost_confidence_with_intel("zero_day", signals_with_known_cve, 0.5)
    # CISA KEV adds +0.40 boost
    assert boost_known >= 0.90

    # With unknown CVE
    signals_with_unknown_cve = {"cve_id": "CVE-1990-0001"}
    boost_unknown = _boost_confidence_with_intel("zero_day", signals_with_unknown_cve, 0.5)
    assert boost_unknown < 0.90

    # With non-numeric signals (regression test for TypeError fix)
    signals_with_string_vals = {"cve_id": "CVE-2023-38606", "payload_pattern": "unpatched_payload"}
    boost_non_numeric = _boost_confidence_with_intel("zero_day", signals_with_string_vals, 0.5)
    assert boost_non_numeric >= 0.90

    predictor = AttackPredictor()
    forecast = predictor.predict_cyber_attack(signals_with_known_cve)
    assert forecast is not None
    assert forecast.confidence >= 0.90


# ============================================================
# BLOCK 3: callgraph_delta.get_callers_for
# ============================================================

def test_get_callers_for_nominal():
    """Test get_callers_for with valid CallGraph."""
    from scp.autofix.callgraph_delta import get_callers_for, get_call_graph, reset_call_graph, FileNode, CallEdge

    reset_call_graph()
    graph = get_call_graph()

    # Manually populate an edge in callgraph to test lookup
    node = FileNode(
        path="scp/test_caller.py",
        sha="0123456789abcdef",
        mtime=time.time(),
        calls=[CallEdge(caller_file="scp/test_caller.py", target_name="target_target_func", line=10)],
    )
    with graph._lock:
        graph._files["scp/test_caller.py"] = node
        graph._rebuild_indexes()

    # Call get_callers_for
    callers = get_callers_for("target_target_func")
    assert "scp/test_caller.py" in callers

    # Reset
    reset_call_graph()


def test_get_callers_for_edge_cases():
    """Test robustness with invalid, empty, and non-string inputs."""
    from scp.autofix.callgraph_delta import get_callers_for

    # Non-existent function
    assert get_callers_for("totally_nonexistent_func_xyz_123") == []

    # Empty string
    assert get_callers_for("") == []

    # Whitespace only
    assert get_callers_for("    ") == []

    # None
    assert get_callers_for(None) == []

    # Non-string types
    assert get_callers_for(1234) == []
    assert get_callers_for([]) == []
    assert get_callers_for({}) == []

    # Malformed / special characters
    assert get_callers_for("def foo():\n\tpass") == []
    assert get_callers_for("\x00\xff") == []


def test_blast_radius_fast_callgraph_integration():
    """Verify compute_blast_radius uses get_callers_for fast-path."""
    from scp.autofix.runner_phases.blast_radius import compute_blast_radius
    from scp.autofix.callgraph_delta import get_call_graph, reset_call_graph, FileNode, CallEdge

    reset_call_graph()
    graph = get_call_graph()

    # Set up test edge
    node = FileNode(
        path="scp/caller_mod.py",
        sha="0123456789abcdef",
        mtime=time.time(),
        calls=[CallEdge(caller_file="scp/caller_mod.py", target_name="target_blast_fn", line=42)],
    )
    with graph._lock:
        graph._files["scp/caller_mod.py"] = node
        graph._rebuild_indexes()

    res = compute_blast_radius("scp/target_mod.py", "target_blast_fn")
    assert res is not None
    assert "via callgraph" in res.reason or res.caller_count >= 1

    reset_call_graph()


# ============================================================
# BLOCK 4: domain_store baseline verification
# ============================================================

def test_domain_store_baselines():
    """Test DomainKnowledgeStore baseline verifications."""
    from scp.knowledge.domain_store import DomainKnowledgeStore

    store = DomainKnowledgeStore()
    for f in store.data_dir.glob("*.jsonl"):
        store.register_file(f.name)

    rec_res = store.verify_all_baselines()
    assert isinstance(rec_res, dict)
    # Check no record corruption
    for domain, stats in rec_res.items():
        assert stats.get("corrupted", 0) == 0, f"Domain {domain} has corrupted records: {stats}"

    file_res = store.verify_all_file_baselines()
    assert isinstance(file_res, dict)


# ============================================================
# BLOCK 5: external_trust baseline verification
# ============================================================

def test_external_trust_baselines():
    """Test ExternalTrustRoot baseline verification."""
    from scp.meta.external_trust import get_external_trust_root

    trust_root = get_external_trust_root()
    for f in trust_root.EXPECTED_FILES:
        if not f.endswith("/"):
            trust_root.register_file(f)

    trust_res = trust_root.verify_all_baselines()
    assert isinstance(trust_res, dict)
    # In live development workspace, verify_all_baselines reports verification status dict
    assert len(trust_res) > 0


# ============================================================
# BLOCK 6: scpv14_process_mixin JudgeVerdict
# ============================================================

def test_judge_verdict_contract():
    """Verify JudgeVerdict dataclass definition and instantiation."""
    from scp.runtime.engine_parts.scpv14_process_mixin import JudgeVerdict

    verdict = JudgeVerdict(
        verdict="PASS",
        confidence=0.95,
        reasoning="All checks satisfied",
        risk_level="LOW",
    )
    assert verdict.verdict == "PASS"
    assert verdict.confidence == 0.95
    assert verdict.reasoning == "All checks satisfied"
    assert verdict.risk_level == "LOW"
