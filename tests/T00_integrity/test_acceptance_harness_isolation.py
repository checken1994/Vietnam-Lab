"""Acceptance must be hermetic and must never overwrite evidence."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import run_scp_acceptance as acceptance


def test_runtime_environment_never_inherits_parent_credentials(tmp_path, monkeypatch):
    sentinels = {
        "OPENROUTER_API_KEY": "parent-openrouter-sentinel",
        "OPENAI_API_KEY": "parent-openai-sentinel",
        "SCP_CAPABILITY_SECRET": "parent-capability-sentinel",
        "SCP_PG_TEST_DSN": "parent-pg-sentinel",
        "HTTPS_PROXY": "parent-proxy-sentinel",
    }
    monkeypatch.setattr(acceptance.os, "environ", dict(sentinels))
    runtime = acceptance.RuntimeHarness(tmp_path, 18180, 18181)
    child = runtime.environment()
    assert not ({value for value in sentinels.values()}.intersection(child.values()))
    assert child["SCP_EGRESS_MODE"] == "deny"
    assert child["SCP_SKIP_STARTUP_GATE"] == "0"
    assert Path(child["SCP_TRACE_STORE_PATH"]).is_relative_to(tmp_path)
    assert child["SCP_LLM_FALLBACK_PROVIDERS"] == acceptance.LOCAL_FALLBACK_SPEC


def test_acceptance_refuses_existing_evidence_before_overwriting(tmp_path):
    sentinel = tmp_path / "existing-evidence.txt"
    sentinel.write_text("preserve", encoding="utf-8")
    with pytest.raises(FileExistsError):
        acceptance.AcceptanceSuite(tmp_path, 18180, 18181, 2)
    assert sentinel.read_text(encoding="utf-8") == "preserve"


def test_incomplete_acceptance_report_never_claims_pass(tmp_path):
    suite = acceptance.AcceptanceSuite(tmp_path, 18180, 18181, 2)
    suite.scenarios.append({"id": "SCP-A01", "passed": True})
    suite._write_report(final=True)
    report = json.loads(suite.report_path.read_text(encoding="utf-8"))
    assert report["overall_pass"] is False
