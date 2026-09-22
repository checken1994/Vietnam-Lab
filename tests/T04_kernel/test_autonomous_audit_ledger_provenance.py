"""Cryptographic provenance and tamper-detection tests for AutonomousAuditLedger."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scp.core.autonomous_ledger import AutonomousAuditLedger, ProvenanceBlock


def test_autonomous_tool_two_phase_commit(tmp_path: Path) -> None:
    """Verifies Phase 1 Intent and Phase 2 Result form a continuous cryptographic SHA-256 hash chain."""
    ledger_file = tmp_path / "trace_ledger.jsonl"
    ledger = AutonomousAuditLedger(ledger_path=ledger_file)

    # Phase 1: Tool invocation intent
    intent = ledger.commit_intent(
        task_id="task_auton_01",
        step_id="step_01",
        tool_name="sys.inspect",
        params={"category": "hardware"},
        capability_token={"token_id": "tok_alpha", "signature": "sig_hmac_123"},
        parent_trace_id="trace_root_00",
    )

    assert intent["seq"] == 1
    assert intent["prev_hash"] is None
    assert intent["hash"].startswith("sha256:")
    assert intent["fields"]["task_id"] == "task_auton_01"
    assert intent["fields"]["event"] == "AUTONOMOUS_TOOL_INTENT"

    # Phase 2: Tool execution result
    result_entry = ledger.commit_result(
        task_id="task_auton_01",
        step_id="step_01",
        tool_name="sys.inspect",
        intent_entry_hash=intent["hash"],
        result_data={"cpu_cores": 8, "total_gb": 32.0},
        evidence={"metrics_collected": True},
        status="SUCCESS",
        duration_ms=4.2,
    )

    assert result_entry["seq"] == 2
    assert result_entry["prev_hash"] == intent["hash"]
    assert result_entry["hash"].startswith("sha256:")
    assert result_entry["fields"]["status"] == "SUCCESS"
    assert result_entry["fields"]["intent_entry_hash"] == intent["hash"]

    # Verify provenance across the entire chain
    verification = ledger.verify_provenance()
    assert verification["entries"] == 2
    assert verification["hash_chain_valid"] is True
    assert len(verification["errors"]) == 0


def test_autonomous_ledger_tamper_detection(tmp_path: Path) -> None:
    """Any modification or forgery in the ledger causes hash chain verification to fail."""
    ledger_file = tmp_path / "tamper_ledger.jsonl"
    ledger = AutonomousAuditLedger(ledger_path=ledger_file)

    intent = ledger.commit_intent(
        task_id="task_secure_01",
        step_id="step_01",
        tool_name="cmd.run",
        params={"command": "git status"},
        capability_token={"token_id": "tok_test", "signature": "sig_000"},
    )
    ledger.commit_result(
        task_id="task_secure_01",
        step_id="step_01",
        tool_name="cmd.run",
        intent_entry_hash=intent["hash"],
        result_data={"returncode": 0},
        evidence={"stdout": "clean"},
        status="SUCCESS",
        duration_ms=15.0,
    )

    # Confirm valid before tamper
    assert ledger.verify_provenance()["hash_chain_valid"] is True

    # Tamper with the ledger: modify a field in line 1 without updating the hash
    lines = ledger_file.read_text(encoding="utf-8").splitlines()
    tampered_entry = json.loads(lines[0])
    tampered_entry["fields"]["task_id"] = "task_forged_99"
    lines[0] = json.dumps(tampered_entry, ensure_ascii=False, sort_keys=True)
    ledger_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # Verification must catch the tamper
    check = ledger.verify_provenance()
    assert check["hash_chain_valid"] is False
    assert any("hash:1" in err for err in check["errors"])


def test_autonomous_ledger_redacts_tokens(tmp_path: Path) -> None:
    """Confidential parameters such as tokens and passwords are automatically redacted in ledger records."""
    ledger_file = tmp_path / "redact_ledger.jsonl"
    ledger = AutonomousAuditLedger(ledger_path=ledger_file)

    ledger.commit_intent(
        task_id="task_redact_01",
        step_id="step_01",
        tool_name="sys.inspect",
        params={
            "api_key": "sk-secret12345678",
            "password": "supersecretpassword",
            "token": "bearer sk-live-9999",
            "safe_option": "verbose",
        },
        capability_token={"token_id": "tok_redact", "signature": "sig_redact"},
    )

    record_line = ledger_file.read_text(encoding="utf-8").strip()
    # Raw credentials must NOT appear in plaintext
    assert "sk-secret12345678" not in record_line
    assert "supersecretpassword" not in record_line
    assert "sk-live-9999" not in record_line
    assert "[REDACTED]" in record_line or "[REDACTED_STRING]" in record_line


def test_provenance_block_deterministic_hashing() -> None:
    """ProvenanceBlock produces deterministic cryptographic digests and detects perturbations."""
    block1 = ProvenanceBlock(
        block_index=1,
        prev_block_hash=None,
        task_id="t1",
        step_id="s1",
        tool_name="sys.inspect",
        input_sha256="sha256:1111",
        capability_token_id="tok_1",
        capability_token_hash="sha256:2222",
        output_sha256="sha256:3333",
        status="SUCCESS",
        duration_ms=10.0,
        timestamp=1000.0,
    )

    h1 = block1.compute_hash()
    h2 = block1.compute_hash()
    assert h1 == h2
    assert h1.startswith("sha256:")

    # Perturbed block
    block_altered = ProvenanceBlock(
        block_index=1,
        prev_block_hash=None,
        task_id="t1",
        step_id="s1",
        tool_name="sys.inspect",
        input_sha256="sha256:1111",
        capability_token_id="tok_1",
        capability_token_hash="sha256:2222",
        output_sha256="sha256:3333",
        status="FAILED",  # Changed
        duration_ms=10.0,
        timestamp=1000.0,
    )
    assert block_altered.compute_hash() != h1
