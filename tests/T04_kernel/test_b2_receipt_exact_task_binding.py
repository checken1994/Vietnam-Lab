"""B2 — a VerifierReceipt binds EXACTLY one kernel task id (zero-mock).

Before the fix, ``verify_verifier_receipt`` (and the canonical payload) trimmed task ids, so a
receipt signed for "x" also completed the distinct kernel tasks "x ", " x", "x\\t" ... and a
receipt could be relabelled "y" -> "y " without breaking its HMAC. Same rule as R5 approval
tokens: padded ids are refused, never trimmed; a zero-width character makes a different id.

Everything here runs on a real TaskKernel over a temporary SQLite file with a TEST-ONLY
verifier secret; the HMAC oracle is computed independently of the module under test.
"""
from __future__ import annotations

import dataclasses
import hashlib
import hmac
import time

import pytest

from scp.core.verifier_receipt import (
    InvalidReceiptSignatureError,
    VerifierReceipt,
    canonical_receipt_bytes,
    sign_verifier_receipt,
    sign_verifier_receipt_ed25519,
    verify_verifier_receipt,
)
from scp.task_kernel import KernelError, TaskKernel

_TEST_SECRET = "b2-test-only-verifier-secret-never-a-real-key-0123456789"
_PADDED_TWINS = ["x ", " x", "x\t", "\tx", "x\n", "x\r\n", "x\u00a0", "x\u3000"]


@pytest.fixture(autouse=True)
def _test_verifier_secret(monkeypatch):
    monkeypatch.setenv("SCP_VERIFIER_SECRET", _TEST_SECRET)


@pytest.fixture
def kernel(tmp_path):
    k = TaskKernel(tmp_path / "b2_kernel.sqlite3")
    try:
        yield k
    finally:
        k.close()


def _to_verifying(k: TaskKernel, task_id: str):
    k.create_task(task_id, owner="b2-owner", goal="b2 exact binding", risk_tier="R0", deadline_ms=120_000)
    for state in ("PLANNING", "READY", "QUEUED"):
        k.transition(task_id, state, actor="b2-worker")
    lease = k.claim(task_id, "b2-worker", ttl_seconds=60.0)
    k.start(task_id, lease.lease_id)
    k.transition(task_id, "VERIFYING", actor="b2-worker")
    return lease


def _receipt(task_id: str) -> VerifierReceipt:
    return sign_verifier_receipt(VerifierReceipt(
        task_id=task_id, verifier_id="b2-independent-verifier", verdict="VERIFIED",
        evidence_ref="evidence://b2/check", issued_at=time.time(),
    ))


def _oracle_signed(task_id_in_receipt: str, task_id_signed: str) -> dict:
    """Receipt dict whose HMAC is computed here over the documented format, not by the module."""
    issued_at = time.time()
    payload = f"{task_id_signed}:b2-independent-verifier:VERIFIED:evidence://b2/check:{issued_at:.6f}"
    signature = hmac.new(_TEST_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return {"task_id": task_id_in_receipt, "verifier_id": "b2-independent-verifier", "verdict": "VERIFIED",
            "evidence_ref": "evidence://b2/check", "issued_at": issued_at, "signature": signature}


@pytest.mark.parametrize("twin", _PADDED_TWINS, ids=repr)
def test_receipt_for_x_does_not_complete_padded_twin_task(kernel, twin):
    lease_x = _to_verifying(kernel, "x")
    lease_twin = _to_verifying(kernel, twin)
    receipt_x = _receipt("x")

    with pytest.raises(InvalidReceiptSignatureError, match="does not match expected task_id"):
        kernel.commit_verification_result(twin, lease_twin.lease_id, receipt_x)
    with pytest.raises(InvalidReceiptSignatureError, match="does not match expected task_id"):
        kernel.commit_completed(twin, lease_twin.lease_id, receipt=receipt_x.to_dict())
    assert kernel.get_task(twin)["state"] == "VERIFYING"

    # positive control: the same receipt still completes the task it was signed for
    assert kernel.commit_verification_result("x", lease_x.lease_id, receipt_x)["state"] == "COMPLETED"
    assert kernel.get_task(twin)["state"] == "VERIFYING"


def test_receipt_relabelled_to_padded_id_is_rejected(kernel):
    lease = _to_verifying(kernel, "y ")
    relabelled = dataclasses.replace(_receipt("y"), task_id="y ")
    with pytest.raises(InvalidReceiptSignatureError, match="leading/trailing whitespace"):
        kernel.commit_completed("y ", lease.lease_id, receipt=relabelled)
    assert kernel.get_task("y ")["state"] == "VERIFYING"


@pytest.mark.parametrize("twin", _PADDED_TWINS, ids=repr)
def test_hmac_valid_receipt_over_trimmed_id_does_not_bind_padded_task(twin):
    # A key holder's receipt over "x", relabelled to the padded twin (the pre-fix canonical form).
    with pytest.raises(InvalidReceiptSignatureError):
        verify_verifier_receipt(_oracle_signed(twin, "x"), task_id=twin)
    # Even an HMAC over the exact padded bytes is refused: padded ids are never bound.
    with pytest.raises(InvalidReceiptSignatureError, match="leading/trailing whitespace"):
        verify_verifier_receipt(_oracle_signed(twin, twin), task_id=twin)


@pytest.mark.parametrize("twin", _PADDED_TWINS, ids=repr)
def test_signing_refuses_padded_task_id_instead_of_trimming(twin):
    with pytest.raises(InvalidReceiptSignatureError, match="leading/trailing whitespace"):
        sign_verifier_receipt({"task_id": twin, "verifier_id": "v", "verdict": "VERIFIED",
                               "evidence_ref": "e://b2", "issued_at": time.time()})
    with pytest.raises(InvalidReceiptSignatureError, match="leading/trailing whitespace"):
        sign_verifier_receipt(VerifierReceipt(task_id=twin, verifier_id="v", verdict="VERIFIED",
                                              evidence_ref="e://b2", issued_at=time.time()))


def test_ed25519_signing_refuses_padded_task_id():
    import base64

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    raw = Ed25519PrivateKey.generate().private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
    with pytest.raises(InvalidReceiptSignatureError, match="leading/trailing whitespace"):
        sign_verifier_receipt_ed25519({"task_id": "x ", "verifier_id": "v", "verdict": "VERIFIED",
                                       "evidence_ref": "e://b2", "issued_at": time.time()},
                                      base64.b64encode(raw).decode("ascii"))


def test_zero_width_character_makes_a_different_task_id(kernel):
    zw = "x\u200b"
    lease_zw = _to_verifying(kernel, zw)
    with pytest.raises(InvalidReceiptSignatureError, match="does not match expected task_id"):
        kernel.commit_verification_result(zw, lease_zw.lease_id, _receipt("x"))
    assert kernel.commit_verification_result(zw, lease_zw.lease_id, _receipt(zw))["state"] == "COMPLETED"


def test_exact_receipt_completes_its_own_task(kernel):
    lease_a = _to_verifying(kernel, "b2-own-a")
    assert kernel.commit_verification_result("b2-own-a", lease_a.lease_id, _receipt("b2-own-a"))["state"] == "COMPLETED"
    lease_b = _to_verifying(kernel, "b2 own b")  # interior whitespace is part of an exact id
    receipt_b = _receipt("b2 own b").to_dict()
    assert kernel.commit_completed("b2 own b", lease_b.lease_id, receipt=receipt_b)["state"] == "COMPLETED"
    assert any(e["type"] == "TASK_COMPLETED" for e in kernel.get_events("b2 own b"))


def test_missing_or_empty_task_id_still_fails_closed(kernel):
    with pytest.raises(InvalidReceiptSignatureError):
        kernel.commit_completed("x", "no-lease", receipt=None)
    with pytest.raises(KernelError, match="task_id is required"):
        kernel.commit_verification_result("", "no-lease", _receipt("x"))
    with pytest.raises(InvalidReceiptSignatureError, match="missing task_id"):
        verify_verifier_receipt({**_receipt("x").to_dict(), "task_id": ""}, task_id="x")
    for blank in ("", " ", "\t"):
        # a key holder's HMAC over a blank id must never verify, bound or unbound
        with pytest.raises(InvalidReceiptSignatureError):
            verify_verifier_receipt(_oracle_signed(blank, blank), task_id=blank)
        with pytest.raises(InvalidReceiptSignatureError):
            verify_verifier_receipt(_oracle_signed(blank, blank))


def test_canonical_bytes_of_valid_receipts_are_unchanged():
    # Compatibility: exact binding must not change the signed bytes of any valid (unpadded) id.
    r = VerifierReceipt(task_id="task-42", verifier_id="verifier-judge-v1", verdict="VERIFIED",
                        evidence_ref="evidence://sha256-hash", issued_at=1700000000.5)
    expected = b"task-42:verifier-judge-v1:VERIFIED:evidence://sha256-hash:1700000000.500000"
    assert canonical_receipt_bytes(r) == expected
    assert canonical_receipt_bytes(r.to_dict()) == expected
    oracle = _oracle_signed("x", "x")
    assert verify_verifier_receipt(oracle, task_id="x") is True
