"""[ZONE-FIX 2026-09-26] Regression — ImmutableAuditLog hash chain must stay
valid across PROCESSES, and CHAIN_RECOVERY must rehabilitate a corrupt tail.

BEFORE the fix: _last_hash was cached per-instance at init; two processes
appending to the same audit JSONL chained from a stale tail, permanently
breaking the on-disk chain — verify_chain() returned False forever, and the
deterministic_worker / _auto_fix_gates callers then rejected EVERY fix
(fail-closed DoS). Probe-verified 3/3.

AFTER: append() re-reads the tail under the OS-level cross-process lock
(mirroring scp/trace_ledger.py), and a corrupt tail gets a CHAIN_RECOVERY
anchor that starts a new chain segment (history preserved as tamper evidence,
active segment strictly verified, tampering still detected).
"""
from __future__ import annotations

import json
import logging
import subprocess
import sys


from scp.autofix.policy_gate import ImmutableAuditLog

logging.disable(logging.CRITICAL)

_WORKER = r"""
import sys, time
sys.path.insert(0, r"D:\scp")
from scp.autofix.policy_gate import ImmutableAuditLog
log = ImmutableAuditLog(log_file=sys.argv[1])
for i in range(20):
    log.append({"event": "probe", "worker": sys.argv[2], "i": i})
    time.sleep(0.005)
"""


def test_two_process_appends_keep_chain_valid(tmp_path):
    log_file = tmp_path / "policy_blocks.jsonl"
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", _WORKER, str(log_file), worker],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for worker in ("A", "B")
    ]
    assert all(p.wait() == 0 for p in procs)

    fresh = ImmutableAuditLog(log_file=str(log_file))
    ok, reason = fresh.verify_chain()
    assert ok, f"cross-process appends broke the hash chain: {reason}"
    lines = log_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 40


def test_corrupt_tail_gets_chain_recovery_anchor(tmp_path):
    log_file = tmp_path / "recovery.jsonl"
    log = ImmutableAuditLog(log_file=str(log_file))
    log.append({"event": "good1"})
    log.append({"event": "good2"})
    with log_file.open("a", encoding="utf-8") as f:
        f.write('{"event": "torn write bytes...\n')

    ok_before, _ = log.verify_chain()
    assert ok_before is False, "corrupt tail must fail verification"

    log.append({"event": "good3"})

    fresh = ImmutableAuditLog(log_file=str(log_file))
    ok_after, reason = fresh.verify_chain()
    assert ok_after, f"CHAIN_RECOVERY anchor did not rehabilitate the log: {reason}"
    assert "CHAIN_RECOVERY anchor" in reason
    types = []
    for line in log_file.read_text(encoding="utf-8").splitlines():
        try:
            types.append(json.loads(line).get("type", "entry"))
        except Exception:
            types.append("corrupt")
    assert "CHAIN_RECOVERY" in types
    assert types.count("corrupt") == 1, "corrupted history must be preserved, never rewritten"


def test_tampering_in_active_segment_still_detected(tmp_path):
    log_file = tmp_path / "tamper.jsonl"
    log = ImmutableAuditLog(log_file=str(log_file))
    log.append({"event": "a"})
    log.append({"event": "b"})
    lines = log_file.read_text(encoding="utf-8").splitlines()
    tampered = json.loads(lines[1])
    tampered["event"] = "TAMPERED"
    lines[1] = json.dumps(tampered, sort_keys=True)
    log_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    ok, reason = ImmutableAuditLog(log_file=str(log_file)).verify_chain()
    assert ok is False, "payload tampering must still fail verify_chain"
    assert "payload tampered" in reason


def test_single_process_chain_stays_intact(tmp_path):
    log_file = tmp_path / "single.jsonl"
    log = ImmutableAuditLog(log_file=str(log_file))
    for i in range(5):
        assert log.append({"event": f"e{i}"}) is not None
    ok, reason = ImmutableAuditLog(log_file=str(log_file)).verify_chain()
    assert ok, f"single-process chain broken by the cross-process fix: {reason}"
