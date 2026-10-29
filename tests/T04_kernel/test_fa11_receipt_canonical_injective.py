"""FA-11 — the v1 receipt canonical must bind ONE field split, and a finite issued_at (zero-mock).

On bb8435b9 ``canonical_receipt_bytes`` is ``f"{task_id}:{verifier_id}:{verdict}:{evidence_ref}:{ts}"``
with no escaping, so a signature over one receipt also verifies any relabelling that moves a
':'-separated segment across a field boundary: a receipt signed for task 'pc-T:v' completes
task 'pc-T' (verifier_id becomes 'v:x'), and a REJECTED receipt whose evidence_ref starts with
'VERIFIED:' re-splits into a VERIFIED one. B2 exact binding does not help: the relabelled
receipt binds 'pc-T' exactly. The fix escapes '\\' and ':' in task_id / verifier_id / verdict.

A NaN issued_at compares False with every freshness bound, so a NaN receipt never expired;
the fix refuses any non-finite issued_at when the canonical bytes are built.

Two attacker models:
* NEW signature: what the module under test signs today;
* LEGACY signature: an oracle HMAC/Ed25519 over the pre-fix v1 bytes, computed independently of the
  module, i.e. a receipt issued before the fix. A fix that keeps valid receipts byte-identical can
  only guarantee that such a signature verifies for AT MOST ONE field split.
Real TaskKernel on temp SQLite, real HMAC / Ed25519, TEST-ONLY secrets via monkeypatch.
"""
from __future__ import annotations

import base64
import dataclasses
import hashlib
import hmac
import time

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from scp.core.verifier_receipt import (
    InvalidReceiptSignatureError,
    VerifierReceipt,
    canonical_receipt_bytes,
    sign_verifier_receipt,
    sign_verifier_receipt_ed25519,
    verify_verifier_receipt,
    verify_verifier_receipt_ed25519,
)
from scp.task_kernel import TaskKernel

_TEST_SECRET = "fa11-test-only-verifier-secret-never-a-real-key-0123456789"
_FIELDS = ("task_id", "verifier_id", "verdict", "evidence_ref")
_PLAIN = ("t-1", "fa11-independent-verifier", "VERIFIED", "evidence://fa11/check")
_NON_FINITE = {"nan": float("nan"), "+inf": float("inf"), "-inf": float("-inf")}

# (signed fields, relabelled fields): each relabelling keeps the legacy v1 bytes identical, except
# "backslash_vs_escaped_colon", which guards the escaping itself ('\' must be escaped too).
_SHIFTS = {
    "verdict_to_evidence": (("pc-T", "v", "VERIFIED:partial", "e"), ("pc-T", "v", "VERIFIED", "partial:e")),
    "backslash_vs_escaped_colon": (("pc-T\\", "v", "VERIFIED", "VERIFIED:e"), ("pc-T:v", "VERIFIED", "VERIFIED", "e")),
    "task_to_verifier": (("pc-T:v", "x", "VERIFIED", "evidence://e"), ("pc-T", "v:x", "VERIFIED", "evidence://e")),
    "verifier_to_task": (("pc-T", "v:x", "VERIFIED", "evidence://e"), ("pc-T:v", "x", "VERIFIED", "evidence://e")),
    "verdict_to_verifier": (("pc-T", "a", "X:VERIFIED", "evidence://e"), ("pc-T", "a:X", "VERIFIED", "evidence://e")),
    "rejected_reads_verified": (("pc-T", "v", "REJECTED", "VERIFIED:ev"), ("pc-T", "v:REJECTED", "VERIFIED", "ev")),
    "task_across_three": (("pc-T:v:VERIFIED", "x", "VERIFIED", "ev"), ("pc-T", "v", "VERIFIED", "x:VERIFIED:ev")),
    "ingest_shape": (("ingest:topic", "scp.data_sources.auto_web_ingestor", "VERIFIED", "a.example,b.example"),
                     ("ingest", "topic:scp.data_sources.auto_web_ingestor", "VERIFIED", "a.example,b.example")),
}


@pytest.fixture(autouse=True)
def _test_verifier_secret(monkeypatch):
    monkeypatch.setenv("SCP_VERIFIER_SECRET", _TEST_SECRET)


def _legacy_v1(fields: tuple[str, str, str, str], issued_at: float) -> bytes:
    return (":".join(fields) + f":{issued_at:.6f}").encode()


def _oracle_hmac(fields, issued_at: float, key: bytes = _TEST_SECRET.encode()) -> str:
    return hmac.new(key, _legacy_v1(fields, issued_at), hashlib.sha256).hexdigest()


def _receipt(fields, issued_at: float, signature: str = "") -> VerifierReceipt:
    return VerifierReceipt(**dict(zip(_FIELDS, fields)), issued_at=issued_at, signature=signature)


def _ed25519_keys():
    priv = Ed25519PrivateKey.generate()
    raw = priv.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
    pub = priv.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return priv, base64.b64encode(raw).decode(), base64.b64encode(pub).decode()


def _refused(fn) -> bool:
    try:
        fn()
    except InvalidReceiptSignatureError:
        return True
    return False


@pytest.mark.parametrize("shift", sorted(_SHIFTS))
@pytest.mark.parametrize("carrier", ["object", "dict"])
def test_new_hmac_signature_never_verifies_a_relabelled_receipt(shift, carrier):
    signed, relabelled = _SHIFTS[shift]
    issued = sign_verifier_receipt(_receipt(signed, time.time()))
    forged = dataclasses.replace(issued, **dict(zip(_FIELDS, relabelled)))
    with pytest.raises(InvalidReceiptSignatureError):
        verify_verifier_receipt(forged if carrier == "object" else forged.to_dict(), task_id=relabelled[0])


@pytest.mark.parametrize("shift", sorted(_SHIFTS))
def test_new_ed25519_signature_never_verifies_a_relabelled_receipt(shift):
    signed, relabelled = _SHIFTS[shift]
    _, priv_b64, _ = _ed25519_keys()
    issued, pub_b64 = sign_verifier_receipt_ed25519(_receipt(signed, time.time()), priv_b64)
    forged = dataclasses.replace(issued, **dict(zip(_FIELDS, relabelled)))
    with pytest.raises(InvalidReceiptSignatureError):
        verify_verifier_receipt_ed25519(forged, pub_b64)


@pytest.mark.parametrize("shift", sorted(_SHIFTS))
def test_legacy_signature_verifies_at_most_one_field_split(shift):
    """A pre-fix signature names one receipt: never both members of a colliding pair."""
    pair = _SHIFTS[shift]
    now = time.time()
    hmac_sig = _oracle_hmac(pair[0], now)
    priv, _, pub_b64 = _ed25519_keys()
    ed_sig = "ed25519:" + base64.b64encode(priv.sign(_legacy_v1(pair[0], now))).decode()

    hmac_hits = [f for f in pair if not _refused(lambda f=f: verify_verifier_receipt(_receipt(f, now, hmac_sig)))]
    ed_hits = [
        f for f in pair
        if not _refused(lambda f=f: verify_verifier_receipt_ed25519(_receipt(f, now, ed_sig), pub_b64))
    ]
    assert len(hmac_hits) <= 1, hmac_hits
    assert len(ed_hits) <= 1, ed_hits


@pytest.mark.parametrize("signed_task,actor,claimed_task,claimed_actor", [
    ("pc-T:v", "x", "pc-T", "v:x"),
    ("pc-T", "v:x", "pc-T:v", "x"),
])
def test_relabelled_checker_recommendation_never_verifies(signed_task, actor, claimed_task, claimed_actor):
    from scp.task_kernel_parts.autonomous_checker_gate import (
        CHECKER_RECOMMENDATION_TYP,
        CHECKER_RECOMMENDATION_VERDICT,
        _recommendation_key,
        verify_checker_recommendation,
    )

    now = time.time()
    evidence = f"{CHECKER_RECOMMENDATION_TYP}://ast_and_t00_pass/{signed_task}"
    signed = (signed_task, actor, CHECKER_RECOMMENDATION_VERDICT, evidence)
    claimed = (claimed_task, claimed_actor, CHECKER_RECOMMENDATION_VERDICT, evidence)
    forged = _receipt(claimed, now, _oracle_hmac(signed, now, key=_recommendation_key()))
    with pytest.raises(InvalidReceiptSignatureError):
        verify_checker_recommendation(forged, task_id=claimed_task)


def test_receipt_signed_for_other_task_cannot_complete_kernel_task(tmp_path):
    k = TaskKernel(tmp_path / "fa11_kernel.sqlite3")
    try:
        k.create_task("pc-T", owner="fa11-owner", goal="victim", risk_tier="R0", deadline_ms=120_000)
        for state in ("PLANNING", "READY", "QUEUED"):
            k.transition("pc-T", state, actor="fa11-worker")
        lease = k.claim("pc-T", "fa11-worker", ttl_seconds=60.0)
        k.start("pc-T", lease.lease_id)
        k.transition("pc-T", "VERIFYING", actor="fa11-worker")
        signed, relabelled = _SHIFTS["task_to_verifier"]
        now = time.time()
        forged = _receipt(relabelled, now, _oracle_hmac(signed, now))  # receipt issued for task 'pc-T:v'
        with pytest.raises(InvalidReceiptSignatureError):
            k.commit_verification_result("pc-T", lease.lease_id, forged)
        assert k.get_task("pc-T")["state"] == "VERIFYING"
        assert [e for e in k.get_events("pc-T") if e["type"] == "TASK_COMPLETED"] == []
    finally:
        k.close()


@pytest.mark.parametrize("evidence_ref", [
    "evidence://scope/t-1/journal-verify/sha256:" + "ab" * 32,
    "sha256:" + "cd" * 32,
    "https://a.example:8443/p?q=a:b",
    "local:scp/runtime/safe_math.py#25+75=100",
    "scp.checker_recommendation://ast_and_t00_pass/t-1",
    "C:\\scp\\evidence\\run.log",
])
def test_valid_receipts_keep_legacy_bytes_and_verify(evidence_ref):
    """Compatibility control: ':'-free task/verifier/verdict -> bytes identical to v1, still verify."""
    fields = ("t-1", "scp-independent-postcondition-verifier-v1", "VERIFIED", evidence_ref)
    now = time.time()
    r = _receipt(fields, now, _oracle_hmac(fields, now))
    assert canonical_receipt_bytes(r) == _legacy_v1(fields, now)
    assert canonical_receipt_bytes(r.to_dict()) == _legacy_v1(fields, now)
    assert verify_verifier_receipt(r, task_id="t-1") is True
    assert sign_verifier_receipt(dataclasses.replace(r, signature="")).signature == r.signature


def test_colon_bearing_receipt_still_signs_and_verifies_for_its_own_task():
    """Compatibility control: the auto_web_ingestor shape ('ingest:<topic>') keeps working."""
    fields = _SHIFTS["ingest_shape"][0]
    issued = sign_verifier_receipt(_receipt(fields, time.time()))
    assert verify_verifier_receipt(issued, task_id="ingest:topic") is True
    assert verify_verifier_receipt(issued.to_dict(), task_id="ingest:topic") is True


def test_issued_at_rendering_never_contains_the_delimiter():
    """The split is unique only if the trailing .6f field cannot hold ':'."""
    for value in (0.0, -0.0, 5e-324, 1e308, -1e308, 1700000000.5, float("inf"), float("-inf"), float("nan")):
        assert ":" not in f"{value:.6f}"


@pytest.mark.parametrize("label", sorted(_NON_FINITE))
def test_non_finite_issued_at_is_never_signed(label):
    value = _NON_FINITE[label]
    _, priv_b64, _ = _ed25519_keys()
    signers = {
        "hmac_object": lambda: sign_verifier_receipt(_receipt(_PLAIN, value)),
        "hmac_dict": lambda: sign_verifier_receipt(_receipt(_PLAIN, value).to_dict()),
        "ed25519": lambda: sign_verifier_receipt_ed25519(_receipt(_PLAIN, value), priv_b64),
    }
    accepted = [name for name, sign in signers.items() if not _refused(sign)]
    assert accepted == []


@pytest.mark.parametrize("label", sorted(_NON_FINITE))
def test_non_finite_issued_at_never_verifies(label):
    """An already-signed non-finite receipt never verifies, with or without expiry enforced."""
    value = _NON_FINITE[label]
    priv, _, pub_b64 = _ed25519_keys()
    hmac_r = _receipt(_PLAIN, value, _oracle_hmac(_PLAIN, value))
    ed_r = _receipt(_PLAIN, value, "ed25519:" + base64.b64encode(priv.sign(_legacy_v1(_PLAIN, value))).decode())
    verifiers = {
        "hmac_default": lambda: verify_verifier_receipt(hmac_r, task_id="t-1"),
        "hmac_dict_default": lambda: verify_verifier_receipt(hmac_r.to_dict(), task_id="t-1"),
        "hmac_expiry_off": lambda: verify_verifier_receipt(hmac_r, task_id="t-1", max_skew_seconds=0),
        "ed25519_default": lambda: verify_verifier_receipt_ed25519(ed_r, pub_b64),
        "ed25519_expiry_off": lambda: verify_verifier_receipt_ed25519(ed_r, pub_b64, max_age_seconds=0),
    }
    accepted = [name for name, verify in verifiers.items() if not _refused(verify)]
    assert accepted == []
