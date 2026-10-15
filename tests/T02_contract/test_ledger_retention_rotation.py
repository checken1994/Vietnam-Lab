from pathlib import Path
import json
import os
import pytest

from scp.core.request_run_ledger import RequestRunLedger
from scp.core.doubt_cron import _rotate_jsonl_if_needed
from scp.core.fitness_engine import gate


def test_request_run_ledger_rotates_when_oversized(tmp_path):
    ledger_path = tmp_path / "request_runs.jsonl"
    ledger = RequestRunLedger(str(ledger_path))

    # Append small record: size is small, no rotation
    ledger._append({"test": "row1"})
    assert ledger_path.exists()
    assert not (tmp_path / "request_runs.jsonl.1").exists()

    # Artificially expand ledger file to exceed 100 bytes
    ledger_path.write_bytes(b"x" * 200)

    # Force rotation with max_bytes=100
    ledger._rotate_if_needed(max_bytes=100, max_backups=2)
    assert (tmp_path / "request_runs.jsonl.1").exists()
    assert (tmp_path / "request_runs.jsonl.1").stat().st_size == 200
    assert not ledger_path.exists()

    # Next append writes to clean file
    ledger._append({"test": "row2"})
    assert ledger_path.exists()
    assert b"row2" in ledger_path.read_bytes()


def test_doubt_cron_ledger_rotation(tmp_path):
    doubt_file = tmp_path / "doubt_ledger.jsonl"
    doubt_file.write_bytes(b"A" * 500)

    # Below threshold -> no rotation
    _rotate_jsonl_if_needed(doubt_file, max_bytes=1000, max_backups=2)
    assert doubt_file.exists()
    assert not (tmp_path / "doubt_ledger.jsonl.1").exists()

    # Exceeds threshold -> rotated to .1
    _rotate_jsonl_if_needed(doubt_file, max_bytes=200, max_backups=2)
    assert (tmp_path / "doubt_ledger.jsonl.1").exists()
    assert (tmp_path / "doubt_ledger.jsonl.1").stat().st_size == 500
    assert not doubt_file.exists()


def test_fitness_engine_gate_latency_threshold_anti_placebo():
    prev = {
        "decision_accuracy": 1.0,
        "false_accept_rate": 0.0,
        "avg_decision_ms": 0.0068,
    }

    # Case 1: Sub-millisecond OS noise (6 microseconds jump) -> NO ROLLBACK
    nxt_noise = {
        "decision_accuracy": 1.0,
        "false_accept_rate": 0.0,
        "avg_decision_ms": 0.0125,  # +83% in relative terms, but only 0.0057ms delta!
    }
    verdict_noise = gate(prev, nxt_noise)
    assert verdict_noise["verdict"] == "PROMOTE"
    assert not verdict_noise["reasons"]

    # Case 2: Negative test (Anti-Placebo) — REAL latency regression (>20% AND >0.5ms) -> MUST ROLLBACK
    nxt_real_regression = {
        "decision_accuracy": 1.0,
        "false_accept_rate": 0.0,
        "avg_decision_ms": 1.25,  # 0.0068ms -> 1.25ms (>+20% and >0.5ms)
    }
    verdict_reg = gate(prev, nxt_real_regression)
    assert verdict_reg["verdict"] == "ROLLBACK"
    assert any("latency grew" in r for r in verdict_reg["reasons"])
