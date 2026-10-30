"""W2b-A — WHY hỏi theo risk-tier cho MỌI câu, không theo chữ "why" (why-w2b-amendments.md §A).

Red-first: base chỉ lập plan khi câu chứa "tại sao"/"why" (placebo keyword-gate)
⇒ câu hậu quả lớn KHÔNG chứa "why" không được lập plan ⇒ ĐỎ.
"""
from __future__ import annotations

import asyncio
import types

import pytest

from scp.api import cognitive_router
from scp.security.threat_detector import ThreatSignal


class _FakeWhy:
    calls: list[str] = []

    def create_verification_plan(self, question):
        _FakeWhy.calls.append(question)
        return types.SimpleNamespace(question=question)


@pytest.fixture
def hooks(monkeypatch):
    import scp.meta.why_engine as why_mod
    import scp.security.threat_detector as td_mod

    _FakeWhy.calls = []
    signal = {"value": ThreatSignal()}

    class _FakeTD:
        async def analyze(self, **kwargs):
            return signal["value"]

    monkeypatch.setattr(why_mod, "WhyEngine", _FakeWhy)
    monkeypatch.setattr(td_mod, "ThreatDetector", _FakeTD)

    def run(question):
        return asyncio.run(cognitive_router.run_pre_judge_hooks(question, {}))

    run.signal = signal
    return run


@pytest.mark.parametrize("q", ["Thuốc paracetamol uống bao nhiêu là quá liều?", "Giá bitcoin hiện tại bao nhiêu?"])
def test_high_consequence_question_without_why_gets_a_plan(q, hooks):
    ctx = hooks(q)
    assert "why_plan" in ctx, "câu hậu quả lớn không chứa 'why' phải được lập WHY plan"
    assert ctx["why_risk_tier"]["tier"] == "high"


def test_every_question_is_tiered_and_low_risk_why_question_is_not_planned(hooks):
    ctx = hooks("Tại sao bầu trời màu xanh?")
    assert ctx["why_risk_tier"]["tier"] == "low"
    assert "why_plan" not in ctx and _FakeWhy.calls == []


def test_threat_flagged_agent_raises_tier_for_a_harmless_question(hooks):
    hooks.signal["value"] = ThreatSignal(is_ai_agent=True, agent_type="ai_agent_2026", confidence=0.9)
    ctx = hooks("Hello how are you")
    assert ctx["why_risk_tier"] == {"tier": "high", "reason": "threat:ai_agent_2026"}
    assert "why_plan" in ctx


def test_low_confidence_threat_signal_does_not_raise_tier(hooks):
    hooks.signal["value"] = ThreatSignal(agent_type="ai_agent_2026", confidence=0.2)
    assert hooks("Hello how are you")["why_risk_tier"]["tier"] == "low"


def test_domain_classifier_failure_fails_closed_to_high(hooks, monkeypatch):
    import scp.data_sources.domain_classifier as dc

    def _boom(_q):
        raise RuntimeError("classifier down")

    monkeypatch.setattr(dc, "classify_top1", _boom)
    assert cognitive_router.why_risk_tier("Hello how are you") == ("high", "domain_classifier_error")


def test_injector_agent_type_raises_tier(hooks):
    """ThreatDetector gắn agent_type="injector" (conf>=0.9) khi phát hiện injection — phải là high, không low."""
    hooks.signal["value"] = ThreatSignal(agent_type="injector", confidence=0.9)
    ctx = hooks("Hello how are you")
    assert ctx["why_risk_tier"] == {"tier": "high", "reason": "threat:injector"}
