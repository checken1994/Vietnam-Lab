"""KEYC-L5 · B5 — a checker-gate attestation signed BEFORE execution never completes a task.

AutonomousCheckerGate reviews a candidate patch before anything runs. On ddf9bf7c it signed an
HMAC VerifierReceipt with verdict VERIFIED under the completion key, so a worker could present it
at VERIFYING and reach COMPLETED although nobody checked what execution produced (B5, confirmed
by the GLM checker in checker-keyc-merge.md). The gate now signs only a typed recommendation
attestation (typ=scp.checker_recommendation, verdict RECOMMEND_APPROVE, MAC under a typ-derived
key), so the attestation can never be read as a completion receipt.

Zero-mock: real TaskKernel on a temp SQLite DB, real HMAC/Ed25519 signatures, test-only secrets
set through monkeypatch. Completion still works with a receipt signed AFTER execution by a
verifier that observed the postcondition (positive control).
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import time

import pytest

from scp.core.verifier_receipt import (
    InvalidReceiptSignatureError,
    VerifierReceipt,
    canonical_receipt_bytes,
    sign_verifier_receipt,
    verify_verifier_receipt,
)
from scp.security.approval_issuer import issue_approval_token
from scp.task_kernel import TaskKernel

_VERIFIER_SECRET = "keyc-l5-test-only-verifier-secret-32bytes!"
_ORIGINAL = "def mul(a, b):\n    return a * b\n"
_PATCHED = "def mul(a, b):\n    return int(a * b)\n"
_PATCH = "<<<<<<< SEARCH\n    return a * b\n=======\n    return int(a * b)\n>>>>>>> REPLACE\n"


@pytest.fixture
def kernel(tmp_path, monkeypatch):
    monkeypatch.setenv("SCP_VERIFIER_SECRET", _VERIFIER_SECRET)
    monkeypatch.setenv("SCP_CAPABILITY_SECRET", "keyc-l5-test-only-capability-secret-32b")
    k = TaskKernel(tmp_path / "keyc_l5.sqlite3")
    try:
        yield k
    finally:
        k.close()


def _to_waiting_approval(k: TaskKernel, root, task_id: str) -> None:
    (root / "calc.py").write_text(_ORIGINAL, encoding="utf-8")
    k.create_task(task_id, owner="keyc-l5", goal="patch calc")
    k.transition(task_id, "PLANNING")
    k.transition(task_id, "WAITING_APPROVAL")


def _recommend(k: TaskKernel, root, task_id: str, actor: str = "autonomous_checker"):
    """Bring a task to WAITING_APPROVAL and let the real checker gate review the patch."""
    _to_waiting_approval(k, root, task_id)
    result = k.autonomous_review_and_approve(
        task_id=task_id, candidate_patch=_PATCH, target_file="calc.py", actor=actor, repo_root=root,
    )
    assert result.verdict == "RECOMMEND_APPROVE"
    assert result.receipt is not None
    return result


def _approve_and_execute(k: TaskKernel, root, task_id: str):
    """Issuer approves the recommendation; the worker executes the patch and reaches VERIFYING."""
    k.commit_approval(task_id, issue_approval_token(task_id, issuer="keyc-l5-operator"), actor="keyc-l5-operator")
    k.transition(task_id, "QUEUED")
    lease = k.claim(task_id, "keyc-l5-worker", ttl_seconds=60)
    k.start(task_id, lease.lease_id)
    (root / "calc.py").write_bytes(_PATCHED.encode("utf-8"))  # the execution side effect (exact bytes)
    k.transition(task_id, "VERIFYING", lease_id=lease.lease_id)
    assert k.get_task(task_id)["state"] == "VERIFYING"
    return lease


def _assert_not_completed(k: TaskKernel, task_id: str) -> None:
    assert k.get_task(task_id)["state"] == "VERIFYING"
    assert [e for e in k.get_events(task_id) if e["type"] == "TASK_COMPLETED"] == []


@pytest.mark.parametrize("carrier", ["receipt_object", "receipt_dict", "commit_completed"])
def test_pre_execution_checker_attestation_cannot_complete_task(kernel, tmp_path, carrier):
    """B5: what the gate signed before execution is refused at VERIFYING through every carrier."""
    task_id = f"keyc-l5-b5-{carrier}"
    attestation = _recommend(kernel, tmp_path, task_id).receipt
    lease = _approve_and_execute(kernel, tmp_path, task_id)

    with pytest.raises(InvalidReceiptSignatureError):
        if carrier == "receipt_object":
            kernel.commit_verification_result(task_id, lease.lease_id, attestation)
        elif carrier == "receipt_dict":
            kernel.commit_verification_result(task_id, lease.lease_id, attestation.to_dict())
        else:
            kernel.commit_completed(task_id, lease.lease_id, receipt=attestation)
    _assert_not_completed(kernel, task_id)


def test_gate_signs_typed_recommendation_attestation_not_completion_receipt(kernel, tmp_path):
    task_id = "keyc-l5-typ"
    result = _recommend(kernel, tmp_path, task_id)
    attestation = result.receipt

    assert attestation.verdict == "RECOMMEND_APPROVE"
    assert attestation.evidence_ref == f"scp.checker_recommendation://ast_and_t00_pass/{task_id}"
    assert result.checks["attestation_typ"] == "scp.checker_recommendation"
    assert "verifier_receipt_signed" not in result.checks
    with pytest.raises(InvalidReceiptSignatureError):
        verify_verifier_receipt(attestation, task_id=task_id)

    # Still a genuine, task-bound attestation for whoever consumes the recommendation.
    from scp.task_kernel_parts.autonomous_checker_gate import (
        verify_checker_recommendation,
    )

    assert verify_checker_recommendation(attestation, task_id=task_id) is True
    with pytest.raises(InvalidReceiptSignatureError):
        verify_checker_recommendation(attestation, task_id=task_id + " ")
    raw_key_twin = sign_verifier_receipt(dataclasses.replace(attestation, signature=""), secret=_VERIFIER_SECRET)
    tampered = (
        dataclasses.replace(attestation, signature="0" * 64),
        dataclasses.replace(attestation, evidence_ref=attestation.evidence_ref + "/x"),
        raw_key_twin,  # same fields MACed with the completion key instead of the typ-derived key
    )
    for forged in tampered:
        with pytest.raises(InvalidReceiptSignatureError):
            verify_checker_recommendation(forged, task_id=task_id)


def test_gate_with_explicit_secret_derives_the_typ_key_from_that_secret(kernel, tmp_path):
    from scp.task_kernel import AutonomousCheckerGate
    from scp.task_kernel_parts.autonomous_checker_gate import (
        verify_checker_recommendation,
    )

    explicit = "keyc-l5-test-only-explicit-gate-secret-32b"
    task_id = "keyc-l5-explicit"
    _to_waiting_approval(kernel, tmp_path, task_id)
    gate = AutonomousCheckerGate(kernel=kernel, repo_root=tmp_path, secret=explicit)
    attestation = gate.evaluate_and_approve(task_id, _PATCH, "calc.py").receipt

    assert verify_checker_recommendation(attestation, task_id=task_id, secret=explicit) is True
    with pytest.raises(InvalidReceiptSignatureError):
        verify_checker_recommendation(attestation, task_id=task_id)  # env key is not the gate's key
    with pytest.raises(InvalidReceiptSignatureError):
        verify_verifier_receipt(attestation, task_id=task_id, secret=explicit)


def test_colon_crafted_actor_cannot_reparse_attestation_as_completion_receipt(kernel, tmp_path):
    """Canonical bytes are 'task:verifier:verdict:evidence:ts'; an actor holding ':VERIFIED:'
    re-splits the same bytes into a VERIFIED receipt. Only key separation stops that."""
    task_id = "keyc-l5-colon"
    attestation = _recommend(kernel, tmp_path, task_id, actor="crafted:VERIFIED:tail").receipt
    reparsed = VerifierReceipt(
        task_id=task_id,
        verifier_id="crafted",
        verdict="VERIFIED",
        evidence_ref=f"tail:{attestation.verdict}:{attestation.evidence_ref}",
        issued_at=attestation.issued_at,
        signature=attestation.signature,
    )
    assert canonical_receipt_bytes(reparsed) == canonical_receipt_bytes(attestation)
    lease = _approve_and_execute(kernel, tmp_path, task_id)

    with pytest.raises(InvalidReceiptSignatureError):
        kernel.commit_verification_result(task_id, lease.lease_id, reparsed)
    _assert_not_completed(kernel, task_id)


def test_checker_gated_lifecycle_completes_with_post_execution_verifier_receipt(kernel, tmp_path):
    """Positive control: recommend -> issuer approve -> execute -> verifier observes -> COMPLETED."""
    task_id = "keyc-l5-positive"
    _recommend(kernel, tmp_path, task_id)
    lease = _approve_and_execute(kernel, tmp_path, task_id)

    observed = hashlib.sha256((tmp_path / "calc.py").read_bytes()).hexdigest()
    assert observed == hashlib.sha256(_PATCHED.encode("utf-8")).hexdigest()  # postcondition observed first
    evidence_ref = f"file-sha256://calc.py/{observed}"
    post_execution = sign_verifier_receipt({
        "task_id": task_id,
        "verifier_id": "keyc-l5-post-execution-verifier",
        "verdict": "VERIFIED",
        "evidence_ref": evidence_ref,
        "issued_at": time.time(),
    })

    final = kernel.commit_verification_result(task_id, lease.lease_id, post_execution)
    assert final["state"] == "COMPLETED"
    assert final["active_lease_id"] is None
    completed = [e for e in kernel.get_events(task_id) if e["type"] == "TASK_COMPLETED"]
    assert len(completed) == 1
    payload = json.loads(completed[0]["payload_json"])
    assert payload["verifier_id"] == "keyc-l5-post-execution-verifier"
    assert payload["evidence_ref"] == evidence_ref
    assert payload["verifier_verdict"] == "VERIFIED"
