"""[F-01/F-02 regression 2026-09-25] Unified trace-ledger final decision.

Runtime audit RUNTIME-AUDIT-20260925-0411 findings:

* F-01 (MEDIUM): the unified ledger (data/trace_ledger.jsonl — the record
  served by GET /v3/trace/{trace_id}) recorded the JUDGE-level
  verdict/governance even when the API boundary/kernel verification later
  overrode the run to FAIL/ESCALATE — the audit trail contradicted the HTTP
  response. Fix: _safe_response is computed BEFORE the ledger append and the
  entry now carries final_verdict / final_governance / final_outcome which
  MUST equal the values /ask returns for that run. Judge-level fields stay
  as provenance.

* F-02 (LOW): the security-lane withhold message embedded the judge verdict
  ("[SCP: Answer withheld — verdict: PASS]" while the API returned FAIL).
  Fix: message dropped the verdict interpolation. The behavioral proof for
  F-02 is the live runtime re-check; here we keep a source tripwire against
  reintroducing the stale interpolation.

FA-13: the boundary-escalated ledger branch previously had no test asserting
ledger==API equality; these tests close that unproven branch.
"""
from __future__ import annotations

import pytest

from scp.ask_kernel_adapter import AskKernelAdapter
from scp.trace_ledger import TraceLedger


@pytest.fixture(autouse=True)
def _no_crosscheck(monkeypatch):
    monkeypatch.setenv("SCP_MULTI_LLM_CROSSCHECK", "0")


@pytest.fixture()
def judge_gate(monkeypatch):
    """Hermetic semantic judge (same seam as test_ask_kernel_adapter_verify)."""
    import scp.runtime.judge_llm as judge_mod

    state = {"pass": True, "calls": 0}

    async def _fake_judge(question: str, ai_answer: str, context: str = "") -> bool:
        state["calls"] += 1
        return state["pass"]

    monkeypatch.setattr(judge_mod, "_llm_judge_async", _fake_judge)
    return state


class DummyReq:
    question = "what color is the sky?"
    contexts = ["sky is blue"]
    retrieved_context = ""
    session_id = "trace-final-decision-test"


JUDGE_LEVEL_RESPONSE = {
    "final_answer": "The sky is blue",
    "verdict": "PASS",
    "governance_decision": "UPHOLD",
    "v98_classification": {"provenance": "input_context_only"},
}


def _make_adapter(tmp_path) -> AskKernelAdapter:
    return AskKernelAdapter(
        db_path=str(tmp_path / "kernel.sqlite3"),
        trace_path=str(tmp_path / "adapter_trace.jsonl"),
    )


def _read_unified_entry(tmp_path, trace_id: str) -> dict:
    ledger = TraceLedger(tmp_path / "data" / "trace_ledger.jsonl")
    entry = ledger.get_trace(trace_id)
    assert entry is not None, "unified ledger entry missing for run trace_id"
    return entry


@pytest.mark.asyncio
async def test_escalated_run_ledger_records_final_decision_equal_to_api(judge_gate, monkeypatch, tmp_path):
    """F-01 core rule: boundary FAIL/ESCALATE run -> ledger final_* fields must
    equal exactly what the adapter returns to /ask; judge-level fields stay."""
    monkeypatch.chdir(tmp_path)  # unified ledger data/ dir lands inside tmp_path
    judge_gate["pass"] = False  # kernel verification CONTRADICTED -> FAIL/ESCALATE
    adapter = _make_adapter(tmp_path)
    try:
        req = DummyReq()
        task = adapter.begin(req.question, list(req.contexts), req.retrieved_context, req.session_id)
        result = await adapter.finalize(task, dict(JUDGE_LEVEL_RESPONSE), req)

        safe = result["safe_response"]
        assert safe["verdict"] == "FAIL"
        assert safe["governance_decision"] == "ESCALATE"

        entry = _read_unified_entry(tmp_path, safe["trace_id"])
        fields = entry["fields"]
        # Fail-closed equality: ledger final decision == API-returned decision
        assert fields["final_verdict"] == safe["verdict"] == "FAIL"
        assert fields["final_governance"] == safe["governance_decision"] == "ESCALATE"
        assert fields["final_outcome"] == result["task"]["state"] == "HUMAN_REVIEW"
        # Judge-level provenance preserved (never rewritten)
        assert fields["verdict"] == "PASS"
        assert fields["governance_decision"] == "UPHOLD"
        # Hash chain stays intact with the new fields included
        assert TraceLedger(tmp_path / "data" / "trace_ledger.jsonl").verify()["hash_chain_valid"] is True
    finally:
        adapter.kernel.close()


@pytest.mark.asyncio
async def test_verified_run_ledger_final_fields_match_delivered_response(judge_gate, monkeypatch, tmp_path):
    """Verified (COMPLETED) run: final_* fields must equal the delivered
    response and must not diverge from the judge-level fields."""
    monkeypatch.chdir(tmp_path)
    judge_gate["pass"] = True
    adapter = _make_adapter(tmp_path)
    try:
        req = DummyReq()
        task = adapter.begin(req.question, list(req.contexts), req.retrieved_context, req.session_id)
        result = await adapter.finalize(task, dict(JUDGE_LEVEL_RESPONSE), req)

        safe = result["safe_response"]
        assert result["task"]["state"] == "COMPLETED"

        entry = _read_unified_entry(tmp_path, safe["trace_id"])
        fields = entry["fields"]
        assert fields["final_verdict"] == safe["verdict"] == "PASS"
        assert fields["final_governance"] == safe["governance_decision"] == "UPHOLD"
        assert fields["final_outcome"] == "COMPLETED"
        assert fields["verdict"] == "PASS"  # no divergence on verified runs
    finally:
        adapter.kernel.close()


def test_security_withhold_message_carries_no_judge_verdict_interpolation():
    """F-02 tripwire: the security-lane withhold text must not interpolate the
    judge verdict — the boundary may override it afterwards (FAIL/ESCALATE),
    so a stale 'verdict: PASS' inside a withheld answer misled consumers.
    Behavioral proof for the message shape is the live runtime re-check."""
    import inspect

    from scp.api_server_parts import _ask_impl

    source = inspect.getsource(_ask_impl)
    assert "Answer withheld — verdict:" not in source
    assert "'[SCP: Answer withheld]'" in source
