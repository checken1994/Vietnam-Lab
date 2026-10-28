"""R3 regression: commit_completed() must not complete a task on a bare 'VERIFIED' claim.

Before the fix, commit_completed(task_id, lease_id, "VERIFIED", <any evidence_ref>) moved a
VERIFYING task to COMPLETED with no signed VerifierReceipt, and journaled the worker's own
claim under actor "verifier". core/autonomous_cycle.py used exactly that shape with an
'evidence://…/nominal' ref, i.e. no verification ran at all.

Zero-mock: every test drives a real TaskKernel on a SQLite file under tmp_path and signs or
verifies receipts with real HMAC keys from the environment.
"""
from __future__ import annotations

import json
import sqlite3
import time

import pytest

from scp.core.verifier_receipt import (
    InvalidReceiptSignatureError,
    VerifierReceipt,
    sign_verifier_receipt,
)
from scp.task_kernel import KernelError, TaskKernel, stable_hash

_SECRET = "r3-test-verifier-secret-32-bytes-long!!"


@pytest.fixture
def kernel(tmp_path, monkeypatch):
    monkeypatch.setenv("SCP_VERIFIER_SECRET", _SECRET)
    monkeypatch.setenv("SCP_CAPABILITY_SECRET", _SECRET)
    k = TaskKernel(tmp_path / "r3_kernel.sqlite3")
    try:
        yield k
    finally:
        k.close()


def _to_verifying(k: TaskKernel, task_id: str, worker: str = "r3-worker"):
    k.create_task(task_id, "r3-owner", f"goal {task_id}", "R0", input_hash="sha256:" + "0" * 64)
    for state in ("PLANNING", "READY", "QUEUED"):
        k.transition(task_id, state, actor="r3-scheduler", reason="r3_setup")
    lease = k.claim(task_id, worker, ttl_seconds=120)
    k.start(task_id, lease.lease_id)
    k.transition(task_id, "VERIFYING", actor=worker, reason="r3_setup")
    return lease


def _completed_events(k: TaskKernel, task_id: str) -> list:
    return [e for e in k.get_events(task_id) if e["type"] == "TASK_COMPLETED"]


def _assert_not_completed(k: TaskKernel, task_id: str, lease_id: str) -> None:
    task = k.get_task(task_id)
    assert task["state"] == "VERIFYING"
    assert task["active_lease_id"] == lease_id
    assert _completed_events(k, task_id) == []


def test_autonomous_cycle_legacy_call_shape_is_rejected(kernel):
    task_id = "ask-auto-r3"
    lease = _to_verifying(kernel, task_id, worker="ask-auto-worker")
    with pytest.raises(InvalidReceiptSignatureError):
        kernel.commit_completed(
            task_id,
            lease.lease_id,
            verifier_verdict="VERIFIED",
            evidence_ref=f"evidence://ask/{task_id}/nominal",
        )
    _assert_not_completed(kernel, task_id, lease.lease_id)


def test_positional_verified_with_arbitrary_evidence_is_rejected(kernel):
    task_id = "r3-arbitrary"
    lease = _to_verifying(kernel, task_id)
    with pytest.raises(InvalidReceiptSignatureError):
        kernel.commit_completed(task_id, lease.lease_id, "VERIFIED", "x")
    _assert_not_completed(kernel, task_id, lease.lease_id)


def test_unsigned_receipt_dict_is_rejected(kernel):
    task_id = "r3-unsigned"
    lease = _to_verifying(kernel, task_id)
    unsigned = {
        "task_id": task_id,
        "verifier_id": "r3-verifier",
        "verdict": "VERIFIED",
        "evidence_ref": "evidence://r3/unsigned",
        "issued_at": time.time(),
    }
    with pytest.raises(InvalidReceiptSignatureError):
        kernel.commit_completed(task_id, lease.lease_id, receipt=unsigned)
    _assert_not_completed(kernel, task_id, lease.lease_id)


def test_receipt_signed_with_foreign_key_is_rejected(kernel):
    task_id = "r3-foreign-key"
    lease = _to_verifying(kernel, task_id)
    forged = sign_verifier_receipt(
        VerifierReceipt(
            task_id=task_id,
            verifier_id="r3-verifier",
            verdict="VERIFIED",
            evidence_ref="evidence://r3/forged",
            issued_at=time.time(),
        ),
        secret="attacker-chosen-key-not-the-kernel-secret",
    )
    with pytest.raises(InvalidReceiptSignatureError):
        kernel.commit_completed(task_id, lease.lease_id, receipt=forged)
    _assert_not_completed(kernel, task_id, lease.lease_id)


def test_signed_receipt_completes_and_journals_its_provenance(kernel):
    task_id = "r3-signed"
    lease = _to_verifying(kernel, task_id)
    receipt = sign_verifier_receipt(
        VerifierReceipt(
            task_id=task_id,
            verifier_id="r3-independent-verifier",
            verdict="VERIFIED",
            evidence_ref="evidence://r3/signed",
            issued_at=time.time(),
        )
    )
    task = kernel.commit_completed(task_id, lease.lease_id, receipt=receipt)
    assert task["state"] == "COMPLETED"
    events = _completed_events(kernel, task_id)
    assert len(events) == 1
    payload = json.loads(events[0]["payload_json"])
    assert events[0]["actor"] == "r3-independent-verifier"
    assert payload["verifier_id"] == "r3-independent-verifier"
    assert payload["evidence_ref"] == "evidence://r3/signed"
    assert payload["signature_digest"].startswith("sha256:")
    assert kernel.verify_journal(task_id)["hash_chain_valid"] is True


def test_signed_receipt_dict_journals_its_own_provenance(kernel):
    task_id = "r3-signed-dict"
    lease = _to_verifying(kernel, task_id)
    receipt = sign_verifier_receipt(
        VerifierReceipt(
            task_id=task_id,
            verifier_id="r3-dict-verifier",
            verdict="VERIFIED",
            evidence_ref="evidence://r3/signed-dict",
            issued_at=time.time(),
        )
    ).to_dict()
    task = kernel.commit_completed(task_id, lease.lease_id, receipt=receipt)
    assert task["state"] == "COMPLETED"
    events = _completed_events(kernel, task_id)
    assert len(events) == 1
    payload = json.loads(events[0]["payload_json"])
    assert events[0]["actor"] == "r3-dict-verifier"
    assert payload["verifier_id"] == "r3-dict-verifier"
    assert payload["evidence_ref"] == "evidence://r3/signed-dict"
    assert payload["signature_digest"] == f"sha256:{receipt['signature'][:16]}..."


def test_autonomous_completion_uses_signed_receipt_over_real_journal_check(kernel):
    from scp.core.journal_receipt import complete_with_journal_receipt

    task_id = "ask-auto-r3-real"
    lease = _to_verifying(kernel, task_id, worker="ask-auto-worker")
    expected_digest = stable_hash(kernel.verify_journal(task_id))
    task = complete_with_journal_receipt(kernel, task_id, lease.lease_id, "ask")
    assert task["state"] == "COMPLETED"
    events = _completed_events(kernel, task_id)
    assert len(events) == 1
    payload = json.loads(events[0]["payload_json"])
    assert payload["verifier_id"] != "ask-auto-worker"
    assert payload["signature_digest"].startswith("sha256:")
    assert payload["evidence_ref"] == f"evidence://ask/{task_id}/journal-verify/{expected_digest}"
    assert kernel.verify_journal(task_id)["hash_chain_valid"] is True


def test_autonomous_completion_refuses_when_journal_is_tampered(kernel, tmp_path):
    from scp.core.journal_receipt import complete_with_journal_receipt

    task_id = "ask-auto-r3-tampered"
    lease = _to_verifying(kernel, task_id, worker="ask-auto-worker")
    side = sqlite3.connect(tmp_path / "r3_kernel.sqlite3")
    try:
        side.execute(
            "UPDATE events SET payload_json=? WHERE task_id=? AND seq=1",
            (json.dumps({"tampered": True}), task_id),
        )
        side.commit()
    finally:
        side.close()
    assert kernel.verify_journal(task_id)["hash_chain_valid"] is False
    with pytest.raises(KernelError):
        complete_with_journal_receipt(kernel, task_id, lease.lease_id, "ask")
    _assert_not_completed(kernel, task_id, lease.lease_id)
