"""Key C lane L2a — the kernel commit gate routes every completion through receipt_authority.

Design keyc-1A §2/§4 (L2a) + checker-keyc-merge directive 4 / conditions 2 and 5:

* enforce: only a vr2 receipt signed with Key C (Ed25519, PUBLIC keyring in the kernel) can move
  VERIFYING -> COMPLETED; a self-signed HMAC receipt is refused (A2, B5-enforce, FA-11 v1).
* audit (GD1): an HMAC receipt is still accepted but journaled as SELF_ATTESTED_HMAC, warned and
  counted exactly ONCE per completion (A11); a vr2 claim is fully verified in every mode and a bad
  one is never downgraded to HMAC (A12/A13); a missing keyring refuses everything in enforce (A7).
* the receipt binding (task/lease/attempt/fencing) is checked against the lease row read INSIDE the
  commit transaction (B2, B3, FA-11, TOCTOU: expiry / kill / re-fence between pre-check and txn).
* TASK_COMPLETED carries the full vr2 token + provenance + kid + jti so an offline auditor re-verifies
  it with only the public keyring; a keyring change is journaled as __global__ RECEIPT_KEYRING_LOADED.

Zero-mock: real TaskKernel on temp SQLite files, real Ed25519 throwaway keys and real HMAC
signatures with test-only secrets set through monkeypatch. The TOCTOU tests interleave a second,
real TaskKernel writer on the same DB file at the exact point where the commit transaction opens
(a real SQLiteKernelStorage subclass whose begin() first runs that writer); nothing is stubbed.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import time

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from scp.core.verifier_receipt import (
    InvalidReceiptSignatureError,
    VerifierReceipt,
    canonical_receipt_bytes,
    sign_verifier_receipt,
)
from scp.kernel_storage import SQLiteKernelStorage
from scp.task_kernel import TaskKernel
from scp.task_kernel_parts.definitions import StaleLease

_HMAC_SECRET = "keyc-l2a-test-only-verifier-hmac-secret-0123456789"
_WORKER = "keyc-l2a-worker"
_RA_LOGGER = "scp.security.receipt_authority"
_V2_DOMAIN = b"scp.verifier_receipt.v2\x00"
_KEY_C_ENV = (
    "SCP_VERIFIER_PUBLIC_KEYS", "SCP_VERIFIER_PUBLIC_KEYS_FILE", "SCP_VERIFIER_PRIVATE_KEY",
    "SCP_VERIFIER_PRIVATE_KEY_FILE", "SCP_RECEIPT_AUTHORITY_MODE", "SCP_RECEIPT_AUTHORITY_MODE_FILE",
)


@pytest.fixture
def ra():
    # Import inside the fixture: each test fails on its own if the module is missing.
    import scp.security.receipt_authority as module

    return module


@pytest.fixture
def key_c():
    return Ed25519PrivateKey.generate()


def _ring(*keys, status: str = "active") -> str:
    entries = []
    for key in keys:
        raw = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        entries.append({"pub": base64.b64encode(raw).decode("ascii"), "status": status})
    return json.dumps({"keys": entries})


@pytest.fixture
def env(monkeypatch, key_c):
    for name in _KEY_C_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SCP_VERIFIER_SECRET", _HMAC_SECRET)
    monkeypatch.setenv("SCP_VERIFIER_PUBLIC_KEYS", _ring(key_c))
    return monkeypatch


@pytest.fixture
def enforce(env):
    env.setenv("SCP_RECEIPT_AUTHORITY_MODE", "enforce")
    return env


@pytest.fixture
def kernel(tmp_path):
    k = TaskKernel(tmp_path / "keyc_l2a.sqlite3")
    try:
        yield k
    finally:
        k.close()


def _to_verifying(k: TaskKernel, task_id: str, *, ttl: float = 120.0):
    k.create_task(task_id, "keyc-l2a-owner", "keyc l2a goal")
    for state in ("PLANNING", "READY", "QUEUED"):
        k.transition(task_id, state, actor="keyc-l2a-scheduler", reason="keyc_l2a_setup")
    lease = k.claim(task_id, _WORKER, ttl_seconds=ttl)
    k.start(task_id, lease.lease_id)
    k.transition(task_id, "VERIFYING", actor=_WORKER, reason="keyc_l2a_setup")
    return lease


def _mint(ra, key, lease, **overrides) -> str:
    fields = {
        "task_id": lease.task_id, "attempt_id": lease.attempt_id, "lease_id": lease.lease_id,
        "fencing_token": lease.fencing_token, "evidence_ref": f"evidence://keyc-l2a/{lease.task_id}",
        "profile": "journal_integrity",
    }
    fields.update(overrides)
    return ra.mint_vr2(key, **fields)


def _hmac(task_id: str, *, verifier_id: str = "hands-executor-self-verifier-v1", issued_at: float | None = None):
    return sign_verifier_receipt(
        VerifierReceipt(task_id=task_id, verifier_id=verifier_id, verdict="VERIFIED",
                        evidence_ref=f"evidence://self/{task_id}", issued_at=issued_at or time.time()),
        _HMAC_SECRET,
    )


def _completed_payload(k: TaskKernel, task_id: str) -> dict:
    events = [e for e in k.get_events(task_id) if e["type"] == "TASK_COMPLETED"]
    assert len(events) == 1
    return json.loads(events[0]["payload_json"])


def _refused(k: TaskKernel, task_id: str, call, *, code: str | None = None, exc=InvalidReceiptSignatureError):
    with pytest.raises(exc) as excinfo:
        call()
    if code is not None:
        assert getattr(excinfo.value, "code", None) == code, repr(excinfo.value)
    assert k.get_task(task_id)["state"] == "VERIFYING"
    assert [e for e in k.get_events(task_id) if e["type"] == "TASK_COMPLETED"] == []
    return excinfo.value


def _keyring_events(k: TaskKernel) -> list[dict]:
    return [json.loads(e["payload_json"]) for e in k.get_events("__global__") if e["type"] == "RECEIPT_KEYRING_LOADED"]


# ===================================================================== positive controls (A1)


@pytest.mark.parametrize("form", ["token", "carrier", "carrier-dict", "commit_completed"])
def test_a1_enforce_completes_with_a_key_c_receipt_and_journals_its_provenance(ra, enforce, key_c, kernel, form):
    task_id = f"keyc-l2a-a1-{form}"
    lease = _to_verifying(kernel, task_id)
    token = _mint(ra, key_c, lease)
    payload_c = ra.parse_vr2(token)
    if form == "commit_completed":
        final = kernel.commit_completed(task_id, lease.lease_id, receipt=token)
    else:
        receipt = {"token": token, "carrier": ra.receipt_carrier(token),
                   "carrier-dict": ra.receipt_carrier(token).to_dict()}[form]
        final = kernel.commit_verification_result(task_id, lease.lease_id, receipt)

    assert final["state"] == "COMPLETED" and final["active_lease_id"] is None
    payload = _completed_payload(kernel, task_id)
    assert (payload["receipt_provenance"], payload["receipt_mode"]) == ("VERIFIED_KEY_C", "enforce")
    assert payload["receipt_token"] == token
    assert (payload["receipt_kid"], payload["receipt_jti"]) == (payload_c["kid"], payload_c["jti"])
    assert (payload["lease_id"], payload["attempt_id"], payload["fencing_token"]) == (
        lease.lease_id, lease.attempt_id, lease.fencing_token)
    assert payload["verifier_id"] == payload_c["verifier_id"] and payload["evidence_ref"] == payload_c["evidence_ref"]
    assert payload["receipt_keyring_digest"] == "sha256:" + hashlib.sha256(_ring(key_c).encode()).hexdigest()
    # The digest field is a real sha256; the misleading truncated "signature_digest" is not used for vr2.
    assert payload["receipt_signature_sha256"] == "sha256:" + hashlib.sha256(token.encode()).hexdigest()
    assert "signature_digest" not in payload
    assert kernel.verify_journal(task_id)["hash_chain_valid"] is True


def test_a1o_offline_auditor_reverifies_the_journal_with_only_the_public_keyring(ra, enforce, key_c, kernel):
    """Independent re-implementation (no receipt_authority code): public key + journal rows only."""
    task_id = "keyc-l2a-a1o"
    lease = _to_verifying(kernel, task_id)
    kernel.commit_verification_result(task_id, lease.lease_id, ra.receipt_carrier(_mint(ra, key_c, lease)))

    public_ring = json.loads(_ring(key_c))
    events = kernel.get_events(task_id)
    granted = json.loads(next(e for e in events if e["type"] == "LEASE_GRANTED")["payload_json"])
    completed = _completed_payload(kernel, task_id)
    body_text, sig_text = completed["receipt_token"][len("vr2."):].split(".")
    body = base64.urlsafe_b64decode(body_text + "=" * (-len(body_text) % 4))
    sig = base64.urlsafe_b64decode(sig_text + "=" * (-len(sig_text) % 4))
    pub = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_ring["keys"][0]["pub"]))
    pub.verify(sig, _V2_DOMAIN + body)  # raises InvalidSignature on any forgery
    signed = json.loads(body)
    assert signed["task_id"] == task_id and signed["verdict"] == "VERIFIED"
    assert (signed["lease_id"], signed["fencing_token"]) == (granted["lease_id"], granted["fencing_token"])
    assert (signed["attempt_id"], signed["kid"], signed["jti"]) == (
        completed["attempt_id"], completed["receipt_kid"], completed["receipt_jti"])
    assert completed["receipt_provenance"] == "VERIFIED_KEY_C"


def test_audit_mode_verifies_a_vr2_receipt_fully_and_records_key_c_provenance(ra, env, key_c, kernel):
    lease = _to_verifying(kernel, "keyc-l2a-audit-vr2")
    kernel.commit_verification_result(lease.task_id, lease.lease_id, _mint(ra, key_c, lease))
    payload = _completed_payload(kernel, lease.task_id)
    assert (payload["receipt_provenance"], payload["receipt_mode"]) == ("VERIFIED_KEY_C", "audit")


# ============================================================ A11: audit HMAC = self-attested, once


@pytest.mark.parametrize("entry", ["commit_verification_result", "commit_completed"])
def test_a11_audit_accepts_hmac_as_self_attested_warned_and_counted_exactly_once(ra, env, kernel, caplog, entry):
    caplog.set_level(logging.WARNING, logger=_RA_LOGGER)
    task_id = f"keyc-l2a-a11-{entry}"
    lease = _to_verifying(kernel, task_id)
    receipt = _hmac(task_id)
    before = ra.self_signed_receipt_count()
    if entry == "commit_completed":
        final = kernel.commit_completed(task_id, lease.lease_id, receipt=receipt)
    else:
        final = kernel.commit_verification_result(task_id, lease.lease_id, receipt)

    assert final["state"] == "COMPLETED"
    warnings = [r for r in caplog.records if r.name == _RA_LOGGER and "SELF_ATTESTED_HMAC" in r.getMessage()]
    assert len(warnings) == 1, [r.getMessage() for r in warnings]
    assert ra.self_signed_receipt_count() - before == 1
    payload = _completed_payload(kernel, task_id)
    assert (payload["receipt_provenance"], payload["receipt_mode"]) == ("SELF_ATTESTED_HMAC", "audit")
    assert payload["receipt_token"] is None and payload["receipt_kid"] is None and payload["receipt_jti"] is None
    assert payload["receipt_signature_sha256"] == "sha256:" + hashlib.sha256(receipt.signature.encode()).hexdigest()
    assert (payload["attempt_id"], payload["fencing_token"]) == (lease.attempt_id, lease.fencing_token)
    assert _keyring_events(kernel) == []  # no keyring was involved


# ====================================================================== A2: enforce refuses HMAC


@pytest.mark.parametrize("carrier", ["receipt_object", "receipt_dict", "commit_completed"])
def test_a2_enforce_refuses_an_executor_self_signed_hmac_receipt(ra, enforce, kernel, caplog, carrier):
    caplog.set_level(logging.WARNING, logger=_RA_LOGGER)
    task_id = f"keyc-l2a-a2-{carrier}"
    lease = _to_verifying(kernel, task_id)
    receipt = _hmac(task_id)
    before = ra.self_signed_receipt_count()
    calls = {
        "receipt_object": lambda: kernel.commit_verification_result(task_id, lease.lease_id, receipt),
        "receipt_dict": lambda: kernel.commit_verification_result(task_id, lease.lease_id, receipt.to_dict()),
        "commit_completed": lambda: kernel.commit_completed(task_id, lease.lease_id, receipt=receipt),
    }
    _refused(kernel, task_id, calls[carrier], code=ra.ErrorCode.HMAC_REJECTED)
    assert ra.self_signed_receipt_count() == before
    assert not [r for r in caplog.records if "SELF_ATTESTED_HMAC receipt accepted" in r.getMessage()]


# ======================================================================= A7: keyring unavailable


@pytest.mark.parametrize("case", ["missing", "missing-file", "not-json", "no-active-key"])
def test_a7_enforce_without_a_usable_keyring_refuses_every_receipt(ra, enforce, key_c, kernel, tmp_path, case):
    task_id = f"keyc-l2a-a7-{case}"
    lease = _to_verifying(kernel, task_id)
    token = _mint(ra, key_c, lease)
    enforce.delenv("SCP_VERIFIER_PUBLIC_KEYS", raising=False)
    if case == "missing-file":
        enforce.setenv("SCP_VERIFIER_PUBLIC_KEYS_FILE", str(tmp_path / "absent-keyring.json"))
    elif case == "not-json":
        enforce.setenv("SCP_VERIFIER_PUBLIC_KEYS", "{not json")
    elif case == "no-active-key":
        enforce.setenv("SCP_VERIFIER_PUBLIC_KEYS", _ring(key_c, status="revoked"))
    for receipt in (token, _hmac(task_id)):  # a valid vr2 AND an HMAC: no fallback either way
        err = _refused(kernel, task_id, lambda r=receipt: kernel.commit_verification_result(task_id, lease.lease_id, r))
        assert getattr(err, "code", None) == ra.ErrorCode.KEYRING_UNAVAILABLE, repr(err)


# ============================================== A12/A13: a bad vr2 claim is refused in every mode


def _bad_vr2(ra, key_c, lease, defect: str, monkeypatch):
    if defect == "spliced-signature":
        body = _mint(ra, key_c, lease).split(".")[1]
        other_sig = _mint(ra, key_c, lease).split(".")[2]
        return f"vr2.{body}.{other_sig}", ra.ErrorCode.BAD_SIGNATURE
    if defect == "unknown-key":
        return _mint(ra, Ed25519PrivateKey.generate(), lease), ra.ErrorCode.UNKNOWN_KID
    if defect == "expired":
        return _mint(ra, key_c, lease, now=time.time() - 400.0, ttl_seconds=120.0), ra.ErrorCode.RECEIPT_EXPIRED
    if defect == "revoked-key":
        monkeypatch.setenv("SCP_VERIFIER_PUBLIC_KEYS", json.dumps({"keys": [
            json.loads(_ring(key_c, status="revoked"))["keys"][0],
            json.loads(_ring(Ed25519PrivateKey.generate()))["keys"][0],
        ]}))
        return _mint(ra, key_c, lease), ra.ErrorCode.KID_REVOKED
    if defect == "malformed-carrier":
        carrier = _hmac(lease.task_id)
        return VerifierReceipt(**{**carrier.to_dict(), "signature": "vr2.not-a.token"}), ra.ErrorCode.MALFORMED_TOKEN
    raise AssertionError(defect)


@pytest.mark.parametrize("mode", ["audit", "enforce"])
@pytest.mark.parametrize("defect", ["spliced-signature", "unknown-key", "expired", "revoked-key", "malformed-carrier"])
def test_a12_a_bad_vr2_claim_is_refused_in_every_mode_and_never_downgraded(ra, env, key_c, kernel, mode, defect):
    env.setenv("SCP_RECEIPT_AUTHORITY_MODE", mode)
    task_id = f"keyc-l2a-a12-{mode}-{defect}"
    lease = _to_verifying(kernel, task_id)
    receipt, code = _bad_vr2(ra, key_c, lease, defect, env)
    before = ra.self_signed_receipt_count()
    _refused(kernel, task_id, lambda: kernel.commit_verification_result(task_id, lease.lease_id, receipt), code=code)
    assert ra.self_signed_receipt_count() == before


def test_a13_audit_with_a_missing_keyring_refuses_vr2_instead_of_downgrading(ra, env, key_c, kernel):
    lease = _to_verifying(kernel, "keyc-l2a-a13")
    token = _mint(ra, key_c, lease)
    env.delenv("SCP_VERIFIER_PUBLIC_KEYS", raising=False)
    before = ra.self_signed_receipt_count()
    err = _refused(kernel, lease.task_id, lambda: kernel.commit_verification_result(lease.task_id, lease.lease_id, token))
    assert getattr(err, "code", None) == ra.ErrorCode.KEYRING_UNAVAILABLE, repr(err)
    assert ra.self_signed_receipt_count() == before


def test_a14_unknown_mode_fails_closed_at_the_kernel_gate(ra, env, key_c, kernel):
    env.setenv("SCP_RECEIPT_AUTHORITY_MODE", "enforced")
    lease = _to_verifying(kernel, "keyc-l2a-a14")
    for receipt in (_hmac(lease.task_id), _mint(ra, key_c, lease)):
        err = _refused(kernel, lease.task_id,
                       lambda r=receipt: kernel.commit_verification_result(lease.task_id, lease.lease_id, r))
        assert getattr(err, "code", None) == ra.ErrorCode.MODE_INVALID, repr(err)


# ======================================================================== binding: B2, FA-11, B3


def test_b2_key_c_receipt_binds_the_task_id_exactly(ra, enforce, key_c, kernel):
    plain = _to_verifying(kernel, "b2-ws")
    padded = _to_verifying(kernel, "b2-ws ")
    # Bound to the padded task's real lease: only the exact task_id comparison can refuse it.
    isolated = _mint(ra, key_c, padded, task_id="b2-ws")
    _refused(kernel, "b2-ws ", lambda: kernel.commit_verification_result("b2-ws ", padded.lease_id, isolated),
             code=ra.ErrorCode.TASK_MISMATCH)
    natural = _mint(ra, key_c, plain)
    _refused(kernel, "b2-ws ", lambda: kernel.commit_verification_result("b2-ws ", padded.lease_id, natural),
             code=ra.ErrorCode.TASK_MISMATCH)
    assert kernel.commit_verification_result("b2-ws", plain.lease_id, natural)["state"] == "COMPLETED"
    assert kernel.commit_verification_result("b2-ws ", padded.lease_id, _mint(ra, key_c, padded))["state"] == "COMPLETED"


def test_fa11_key_c_receipt_for_a_colon_extended_task_cannot_complete_the_prefix_task(ra, env, key_c, kernel):
    """FA-11: a receipt signed for 'pc-T:v' must never complete 'pc-T' (vr2 path, audit AND enforce)."""
    prefix = _to_verifying(kernel, "pc-T")
    longer = _to_verifying(kernel, "pc-T:v")
    for mode in ("audit", "enforce"):
        env.setenv("SCP_RECEIPT_AUTHORITY_MODE", mode)
        isolated = _mint(ra, key_c, prefix, task_id="pc-T:v")  # pc-T's own lease binding
        resplit = VerifierReceipt(task_id="pc-T", verifier_id="v:scp.verifier_service", verdict="VERIFIED",
                                  evidence_ref=ra.parse_vr2(isolated)["evidence_ref"],
                                  issued_at=float(ra.parse_vr2(isolated)["iat"]), signature=isolated)
        own_lease = _mint(ra, key_c, longer)
        for receipt in (isolated, resplit, ra.receipt_carrier(own_lease), own_lease):
            _refused(kernel, "pc-T", lambda r=receipt: kernel.commit_verification_result("pc-T", prefix.lease_id, r),
                     code=ra.ErrorCode.TASK_MISMATCH)
    assert kernel.commit_verification_result("pc-T:v", longer.lease_id, _mint(ra, key_c, longer))["state"] == "COMPLETED"
    assert kernel.commit_verification_result("pc-T", prefix.lease_id, _mint(ra, key_c, prefix))["state"] == "COMPLETED"


def test_fa11_v1_colon_resplit_hmac_receipt_is_refused_in_enforce(ra, enforce, kernel):
    """FA-11 on the HMAC v1 path. The v1 canonical joins fields with ':' (scp/core/verifier_receipt.py,
    owned by lane FA-11), so the same MAC re-splits into a receipt for 'pc-T'. Enforce refuses every
    HMAC receipt; the audit-mode result is reported by lane L2a as a finding, not pinned here."""
    _to_verifying(kernel, "pc-T:v")
    prefix = _to_verifying(kernel, "pc-T")
    signed = _hmac("pc-T:v", verifier_id="verifier-x")
    resplit = VerifierReceipt(task_id="pc-T", verifier_id="v:verifier-x", verdict="VERIFIED",
                              evidence_ref=signed.evidence_ref, issued_at=signed.issued_at, signature=signed.signature)
    assert canonical_receipt_bytes(resplit) == canonical_receipt_bytes(signed)  # the v1 gap mechanism
    for receipt in (resplit, resplit.to_dict()):
        _refused(kernel, "pc-T", lambda r=receipt: kernel.commit_verification_result("pc-T", prefix.lease_id, r),
                 code=ra.ErrorCode.HMAC_REJECTED)


def test_b3_receipt_of_attempt_1_cannot_complete_attempt_2(ra, enforce, key_c, kernel):
    task_id = "keyc-l2a-b3"
    first = _to_verifying(kernel, task_id)
    first_receipt = _mint(ra, key_c, first)
    kernel.commit_failed(task_id, first.lease_id, actor=_WORKER, failure_classification="RETRYABLE",
                         indictment_ref="evidence://keyc-l2a/b3/attempt-1")
    assert kernel.get_task(task_id)["state"] == "RETRY_SCHEDULED"
    kernel.transition(task_id, "QUEUED", actor="keyc-l2a-scheduler", reason="keyc_l2a_retry")
    second = kernel.claim(task_id, _WORKER, ttl_seconds=120)
    kernel.start(task_id, second.lease_id)
    kernel.transition(task_id, "VERIFYING", actor=_WORKER, reason="keyc_l2a_setup")
    assert (second.attempt_id != first.attempt_id) and second.fencing_token == first.fencing_token + 1

    def commit(receipt, lease_id=second.lease_id):
        return lambda: kernel.commit_verification_result(task_id, lease_id, receipt)

    _refused(kernel, task_id, commit(first_receipt), code=ra.ErrorCode.LEASE_MISMATCH)
    _refused(kernel, task_id, commit(_mint(ra, key_c, second, attempt_id=first.attempt_id)), code=ra.ErrorCode.ATTEMPT_MISMATCH)
    _refused(kernel, task_id, commit(_mint(ra, key_c, second, fencing_token=first.fencing_token)), code=ra.ErrorCode.FENCING_MISMATCH)
    _refused(kernel, task_id, commit(first_receipt, first.lease_id), exc=StaleLease)  # released attempt-1 lease
    _refused(kernel, task_id, commit(_hmac(task_id)), code=ra.ErrorCode.HMAC_REJECTED)  # v1 carries no attempt
    assert kernel.commit_verification_result(task_id, second.lease_id, _mint(ra, key_c, second))["state"] == "COMPLETED"
    assert _completed_payload(kernel, task_id)["attempt_id"] == second.attempt_id


# =================================================================================== B5-enforce

_ORIGINAL = "def mul(a, b):\n    return a * b\n"
_PATCHED = "def mul(a, b):\n    return int(a * b)\n"
_PATCH = "<<<<<<< SEARCH\n    return a * b\n=======\n    return int(a * b)\n>>>>>>> REPLACE\n"


def test_b5_enforce_refuses_everything_signed_before_execution(ra, enforce, key_c, kernel, tmp_path):
    from scp.security.approval_issuer import issue_approval_token

    task_id = "keyc-l2a-b5"
    (tmp_path / "calc.py").write_text(_ORIGINAL, encoding="utf-8")
    kernel.create_task(task_id, owner="keyc-l2a", goal="patch calc")
    kernel.transition(task_id, "PLANNING")
    kernel.transition(task_id, "WAITING_APPROVAL")
    review = kernel.autonomous_review_and_approve(task_id=task_id, candidate_patch=_PATCH, target_file="calc.py",
                                                  actor="autonomous_checker", repo_root=tmp_path)
    assert review.verdict == "RECOMMEND_APPROVE" and review.receipt is not None
    pre_execution = [review.receipt, _hmac(task_id, verifier_id="autonomous_checker")]  # the ddf9bf7c B5 shape
    pre_execution.append(ra.mint_vr2(key_c, task_id=task_id, attempt_id="attempt-pre-execution",
                                     lease_id="lease-pre-execution", fencing_token=1,
                                     evidence_ref="checker://pre-execution", profile="journal_integrity"))
    kernel.commit_approval(task_id, issue_approval_token(task_id, issuer="keyc-l2a-operator"), actor="keyc-l2a-operator")
    kernel.transition(task_id, "QUEUED")
    lease = kernel.claim(task_id, _WORKER, ttl_seconds=120)
    kernel.start(task_id, lease.lease_id)
    (tmp_path / "calc.py").write_bytes(_PATCHED.encode("utf-8"))
    kernel.transition(task_id, "VERIFYING", lease_id=lease.lease_id)

    codes = [ra.ErrorCode.HMAC_REJECTED, ra.ErrorCode.HMAC_REJECTED, ra.ErrorCode.LEASE_MISMATCH]
    for receipt, code in zip(pre_execution, codes):
        _refused(kernel, task_id, lambda r=receipt: kernel.commit_verification_result(task_id, lease.lease_id, r), code=code)
    observed = hashlib.sha256((tmp_path / "calc.py").read_bytes()).hexdigest()
    post = _mint(ra, key_c, lease, evidence_ref=f"file-sha256://calc.py/{observed}", profile="hands_file_postcondition")
    assert kernel.commit_verification_result(task_id, lease.lease_id, post)["state"] == "COMPLETED"


# ========================================================== TOCTOU (checker condition 2)


class _InterleavingStorage(SQLiteKernelStorage):
    """Real SQLite storage; the next BEGIN IMMEDIATE first lets one concurrent writer commit."""

    def __init__(self, db_path) -> None:
        super().__init__(db_path)
        self.before_next_begin = None
        self.interleaved = False

    def begin(self) -> None:
        hook, self.before_next_begin = self.before_next_begin, None
        if hook is not None:
            hook()
            self.interleaved = True
        super().begin()


@pytest.fixture
def racing(tmp_path):
    db = tmp_path / "keyc_l2a_toctou.sqlite3"
    storage = _InterleavingStorage(db)
    main, other = TaskKernel(storage=storage), TaskKernel(db)
    try:
        yield storage, main, other, db
    finally:
        main.close()
        other.close()


def test_toctou_positive_control_completes_without_interleaving(ra, enforce, key_c, racing):
    storage, main, _other, _db = racing
    lease = _to_verifying(main, "keyc-l2a-race-ok", ttl=2.0)
    storage.before_next_begin = lambda: None
    assert main.commit_verification_result(lease.task_id, lease.lease_id, _mint(ra, key_c, lease))["state"] == "COMPLETED"
    assert storage.interleaved


def test_toctou_lease_expiring_between_precheck_and_txn_is_refused(ra, enforce, key_c, racing):
    storage, main, _other, _db = racing
    lease = _to_verifying(main, "keyc-l2a-race-expiry", ttl=2.0)
    token = _mint(ra, key_c, lease)

    def let_lease_expire():
        while time.time() <= lease.expires_at + 0.05:
            time.sleep(0.05)

    storage.before_next_begin = let_lease_expire
    _refused(main, lease.task_id, lambda: main.commit_verification_result(lease.task_id, lease.lease_id, token), exc=StaleLease)
    assert storage.interleaved  # the pre-check passed; only the in-transaction lease read refused it


def test_toctou_global_kill_between_precheck_and_txn_is_refused(ra, enforce, key_c, racing):
    storage, main, other, _db = racing
    lease = _to_verifying(main, "keyc-l2a-race-kill")
    token = _mint(ra, key_c, lease)
    storage.before_next_begin = lambda: other.set_global_kill(True, actor="keyc-l2a-operator")
    _refused(main, lease.task_id, lambda: main.commit_verification_result(lease.task_id, lease.lease_id, token), exc=StaleLease)
    assert storage.interleaved


def test_toctou_lease_reassigned_and_refenced_between_precheck_and_txn_is_refused(ra, enforce, key_c, racing):
    storage, main, other, db = racing
    task_id = "keyc-l2a-race-refence"
    first = _to_verifying(main, task_id)
    stale_receipt = _mint(ra, key_c, first)
    auto = TaskKernel(db, autonomous_mode=True)
    second = {}

    def reassign():
        other.expire_leases(now=first.expires_at + 1.0)  # watchdog: VERIFYING -> HUMAN_REVIEW, lease released
        auto.auto_resolve_human_review(task_id)
        auto.transition(task_id, "QUEUED", actor="keyc-l2a-scheduler", reason="keyc_l2a_requeue")
        second["lease"] = auto.claim(task_id, "keyc-l2a-worker-2", ttl_seconds=120)
        auto.start(task_id, second["lease"].lease_id)
        auto.transition(task_id, "VERIFYING", actor="keyc-l2a-worker-2", reason="keyc_l2a_setup")

    try:
        storage.before_next_begin = reassign
        _refused(main, task_id, lambda: main.commit_verification_result(task_id, first.lease_id, stale_receipt), exc=StaleLease)
        assert storage.interleaved
        refenced = second["lease"]
        assert refenced.fencing_token == first.fencing_token + 1
        assert main.get_task(task_id)["active_lease_id"] == refenced.lease_id
        final = auto.commit_verification_result(task_id, refenced.lease_id, _mint(ra, key_c, refenced))
        assert final["state"] == "COMPLETED"
        assert _completed_payload(auto, task_id)["fencing_token"] == refenced.fencing_token
    finally:
        auto.close()


# ============================================================ keyring-load event (directive 4)


def test_keyring_load_is_journaled_once_per_distinct_keyring(ra, enforce, key_c, kernel):
    ring_a = _ring(key_c)
    digest_a = "sha256:" + hashlib.sha256(ring_a.encode()).hexdigest()
    for task_id in ("keyc-l2a-ring-1", "keyc-l2a-ring-2"):
        lease = _to_verifying(kernel, task_id)
        kernel.commit_verification_result(task_id, lease.lease_id, _mint(ra, key_c, lease))
    assert [e["keyring_digest"] for e in _keyring_events(kernel)] == [digest_a]

    rotated_key = Ed25519PrivateKey.generate()
    ring_b = _ring(key_c, rotated_key)
    enforce.setenv("SCP_VERIFIER_PUBLIC_KEYS", ring_b)
    lease = _to_verifying(kernel, "keyc-l2a-ring-3")
    kernel.commit_verification_result(lease.task_id, lease.lease_id, _mint(ra, rotated_key, lease))
    digest_b = "sha256:" + hashlib.sha256(ring_b.encode()).hexdigest()
    events = _keyring_events(kernel)
    assert [e["keyring_digest"] for e in events] == [digest_a, digest_b]
    assert events[1]["task_id"] == "keyc-l2a-ring-3" and events[1]["receipt_mode"] == "enforce"
    assert _completed_payload(kernel, "keyc-l2a-ring-3")["receipt_keyring_digest"] == digest_b
    assert kernel.verify_journal("__global__")["hash_chain_valid"] is True
