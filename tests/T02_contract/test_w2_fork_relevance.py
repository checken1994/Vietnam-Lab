# W2 CONTRACT: root-2 — S24 fork relevance gate + hết self-certify (GA.md B1b).
"""W2 — root-2: fork S24 không còn hardcode PASS cho answer lệch câu hỏi.

Thực tế W1: "What is the capital of France?" → Wikipedia 'France' → summary
không nhắc Paris vẫn verdict=PASS/governance=UPHOLD (hardcode, không check
relevance; evidence tự tham chiếu). Fix: (d5) relevance gate trong fork —
không covered → return None → LLM fallback; (d6) adapter chỉ coi fork là
already_judged khi có marker relevance_gate.checked — hết self-certify.
"""
from __future__ import annotations

import asyncio

from scp.ask_kernel_adapter import AskKernelAdapter
from scp.runtime.question_router import _terms_covered, attempt_lookup_fork


class _FakeReq:
    def __init__(self, question: str):
        self.question = question
        self.session_id = "w2-test"


def _dec():
    from scp.runtime.question_router import LOOKUP, RouteDecision

    return RouteDecision(LOOKUP, "geography", 0.75, "l0-keyword", "lookup_signal:geography_fact")


def _run_fork(monkeypatch, wiki_text: str, question: str = "What is the capital of France?"):
    async def _fake_route_async(question, gateway=None):
        return _dec()

    monkeypatch.setattr("scp.runtime.question_router.route_question_async", _fake_route_async)
    monkeypatch.setattr(
        "scp.runtime.question_router.resolve_lookup_data",
        lambda question, domain=None, decision=None: {
            "api_name": "wikipedia",
            "api_url": "https://en.wikipedia.org/wiki/France",
            "text": wiki_text,
        },
    )
    return asyncio.run(attempt_lookup_fork(_FakeReq(question)))


def test_d5_relevant_answer_passes_gate_and_contains_fact(monkeypatch):
    """[W2-d5] payload chứa capital/Paris → PASS, answer có 'Paris', marker
    relevance_gate.checked=True (điều kiện d6)."""
    response = _run_fork(
        monkeypatch,
        "France is a country in Western Europe. Its capital is Paris, "
        "the largest city of France.",
    )
    assert response is not None
    assert response["verdict"] == "PASS"
    assert "Paris" in response["final_answer"]
    assert response["relevance_gate"]["checked"] is True


def test_d5_irrelevant_answer_rejected_to_llm_fallback(monkeypatch):
    """[W2-d5] payload KHÔNG chứa salient terms ('capital') → gate chặn →
    return None (LLM fallback) — hết hardcoded-PASS cho answer lệch câu hỏi."""

    async def _fake_route_async(question, gateway=None):
        return _dec()

    calls = {"n": 0}

    def _resolve(question, domain=None, decision=None):
        calls["n"] += 1
        return {
            "api_name": "wikipedia",
            "api_url": "https://en.wikipedia.org/wiki/France",
            "text": "France has a population of about 68 million people.",  # không có 'capital'
        }

    monkeypatch.setattr("scp.runtime.question_router.route_question_async", _fake_route_async)
    monkeypatch.setattr("scp.runtime.question_router.resolve_lookup_data", _resolve)
    response = asyncio.run(attempt_lookup_fork(_FakeReq("What is the capital of France?")))
    assert response is None  # → LLM fallback, không deliver answer lệch
    assert calls["n"] == 1


def test_d5_terms_covered_semantics_preserved():
    """[W2-d5] _terms_covered reuse đúng semantic: all-terms, empty → False."""
    assert _terms_covered("the capital of France is Paris", ["capital", "france"]) is True
    assert _terms_covered("a country in Europe", ["capital"]) is False
    assert _terms_covered("anything", []) is False


def test_d6_fork_without_relevance_marker_is_not_self_certified():
    """[W2-d6] fork-shaped PASS thiếu relevance_gate marker → không được coi
    là already_judged (adapter phải rơi judge path đầy đủ)."""
    fork_response = {
        "verdict": "PASS",
        "governance_decision": "UPHOLD",
        "v98_classification": {"route": "lookup_data_api", "provenance": "input_context_only"},
        "slm_trace": [],
        "elapsed_ms": 12.0,
    }
    assert AskKernelAdapter._lookup_fork_self_certified(fork_response) is False


def test_d6_fork_with_marker_still_short_circuits():
    """[W2-d6] fork đã qua relevance gate → already_judged như cũ (không mất
    đường nhanh hợp lệ)."""
    fork_response = {
        "verdict": "PASS",
        "governance_decision": "UPHOLD",
        "v98_classification": {"route": "lookup_data_api"},
        "relevance_gate": {"checked": True, "terms": ["capital", "france"]},
        "slm_trace": [],
        "elapsed_ms": 12.0,
    }
    assert AskKernelAdapter._lookup_fork_self_certified(fork_response) is True


def test_d6_non_fork_response_unaffected():
    """[W2-d6] response thường (không phải fork) không bị điều kiện mới chạm."""
    llm_response = {
        "verdict": "PASS",
        "v98_classification": {"judge_evaluated": True},
        "slm_trace": [{"x": 1}],
        "elapsed_ms": 100.0,
    }
    assert AskKernelAdapter._lookup_fork_self_certified(llm_response) is True
