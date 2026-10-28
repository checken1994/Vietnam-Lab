"""Key C (1.A) lane L1 — scp.security.receipt_authority: vr2 codec, keyring, completion gate.

Mỗi điều kiện từ chối có MỘT test riêng, khẳng định đúng ``ErrorCode`` (chống mutant tương
đương: probe 1.A cho thấy fencing/lease binding che lấp nhau khi chỉ test end-to-end).

Zero-mock: mọi khóa Ed25519 sinh tại chỗ; token giả mạo được ký THẬT. Wire format được cài lại
độc lập trong file này (domain literal, kid, canonical JSON, base64url) thay vì gọi codec của
module, để test và code không chung một lineage. Thời gian được truyền qua ``now=`` (giá trị
xác định), không patch đồng hồ.
"""
from __future__ import annotations

import base64
import dataclasses
import hashlib
import hmac
import json
import logging
import time

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

V2_DOMAIN = b"scp.verifier_receipt.v2\x00"
KID_DOMAIN = b"scp.verifier_key.kid.v1\x00"
NOW = 1_800_000_000.0
TASK = "keyc-task-1"
LEASE = "lease-keyc-1"
ATTEMPT = "attempt-keyc-1"
FENCING = 7


@pytest.fixture
def ra():
    # Import trong fixture: trên base (chưa có module) mỗi test đỏ riêng vì ImportError,
    # còn test registry vẫn chạy được và đỏ vì hành vi.
    import scp.security.receipt_authority as module

    return module


@pytest.fixture
def key_c():
    return Ed25519PrivateKey.generate()


def _raw_pub(private_key) -> bytes:
    return private_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def _kid(private_key) -> str:
    return hashlib.sha256(KID_DOMAIN + _raw_pub(private_key)).hexdigest()[:16]


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64u_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _body(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _payload(private_key, **overrides) -> dict:
    payload = {
        "v": 2,
        "typ": "scp.verifier_receipt",
        "kid": _kid(private_key),
        "task_id": TASK,
        "attempt_id": ATTEMPT,
        "lease_id": LEASE,
        "fencing_token": FENCING,
        "verifier_id": "scp.verifier_service",
        "profile": "journal_integrity",
        "verdict": "VERIFIED",
        "evidence_ref": "evidence://keyc/l1/1",
        "iat": NOW - 10.0,
        "exp": NOW + 110.0,
        "jti": "a" * 32,
    }
    payload.update(overrides)
    return payload


def _forge(private_key, payload: dict | None = None, *, domain: bytes = V2_DOMAIN, body: bytes | None = None) -> str:
    body = _body(payload if payload is not None else _payload(private_key)) if body is None else body
    return "vr2." + _b64u(body) + "." + _b64u(private_key.sign(domain + body))


def _entry(private_key, status: str = "active", not_after: float | None = None) -> dict:
    entry = {"kid": _kid(private_key), "pub": base64.b64encode(_raw_pub(private_key)).decode("ascii"), "status": status}
    if not_after is not None:
        entry["not_after"] = not_after
    return entry


def _ring(*entries: dict) -> str:
    return json.dumps({"keys": list(entries)})


def _assert_rejected(ra, code: str, call) -> None:
    with pytest.raises(ra.ReceiptAuthorityError) as excinfo:
        call()
    assert excinfo.value.code == code, str(excinfo.value)


# ============================================================ codec / wire format


def test_domain_prefix_and_type_constants_are_pinned(ra):
    assert ra.RECEIPT_SIGNING_DOMAIN == V2_DOMAIN
    assert ra.DOMAIN == V2_DOMAIN
    assert ra.RECEIPT_V2_PREFIX == "vr2."
    assert ra.RECEIPT_TOKEN_TYPE == "scp.verifier_receipt"
    assert ra.MAX_TTL_SECONDS == 300.0


def test_mint_wire_format_matches_an_independent_implementation(ra, key_c):
    token = ra.mint_vr2(
        key_c, task_id=TASK, attempt_id=ATTEMPT, lease_id=LEASE, fencing_token=FENCING,
        evidence_ref="evidence://keyc/l1/1", profile="journal_integrity", ttl_seconds=120, now=NOW,
    )
    assert token.startswith("vr2.") and "=" not in token
    body_text, sig_text = token[len("vr2."):].split(".")
    body = _b64u_decode(body_text)
    payload = json.loads(body)
    assert body == _body(payload)
    assert set(payload) == {
        "v", "typ", "kid", "task_id", "attempt_id", "lease_id", "fencing_token",
        "verifier_id", "profile", "verdict", "evidence_ref", "iat", "exp", "jti",
    }
    assert payload["kid"] == _kid(key_c) == ra.compute_kid(_raw_pub(key_c))
    assert (payload["v"], payload["typ"], payload["verdict"]) == (2, "scp.verifier_receipt", "VERIFIED")
    assert (payload["task_id"], payload["attempt_id"], payload["lease_id"]) == (TASK, ATTEMPT, LEASE)
    assert payload["fencing_token"] == FENCING and payload["profile"] == "journal_integrity"
    assert (payload["iat"], payload["exp"]) == (NOW, NOW + 120)
    assert len(payload["jti"]) == 32 and int(payload["jti"], 16) >= 0
    key_c.public_key().verify(_b64u_decode(sig_text), V2_DOMAIN + body)


def test_mint_gives_every_receipt_a_fresh_jti(ra, key_c):
    kwargs = dict(task_id=TASK, attempt_id=ATTEMPT, lease_id=LEASE, fencing_token=FENCING, evidence_ref="e://1", now=NOW)
    assert ra.parse_vr2(ra.mint_vr2(key_c, **kwargs))["jti"] != ra.parse_vr2(ra.mint_vr2(key_c, **kwargs))["jti"]


@pytest.mark.parametrize("task_id", ["x ", "\tx", "x\u200b"], ids=["trailing-space", "leading-tab", "zero-width"])
def test_mint_keeps_the_task_id_verbatim(ra, key_c, task_id):
    token = ra.mint_vr2(key_c, task_id=task_id, attempt_id=ATTEMPT, lease_id=LEASE, fencing_token=1, evidence_ref="e://1", now=NOW)
    assert ra.parse_vr2(token)["task_id"] == task_id


def test_parse_returns_the_payload_without_verifying_the_signature(ra):
    rogue = Ed25519PrivateKey.generate()
    payload = ra.parse_vr2(_forge(rogue))
    assert payload == _payload(rogue)


def _malformed_token(case: str, key) -> str:
    good = _payload(key)
    bodies = {
        "not-json": b"not json", "json-array": b"[1,2]", "non-canonical-spacing": json.dumps(good, indent=1).encode(),
        "duplicate-key": _body(good)[:-1] + b',"task_id":"other"}',
    }
    if case in bodies:
        return _forge(key, body=bodies[case])
    mutations = {
        "extra-field": {**good, "scope": "x"},
        "missing-jti": {k: v for k, v in good.items() if k != "jti"},
        "fencing-bool": {**good, "fencing_token": True},
        "fencing-string": {**good, "fencing_token": "7"},
        "fencing-zero": {**good, "fencing_token": 0},
        "kid-uppercase": {**good, "kid": good["kid"].upper()},
        "kid-short": {**good, "kid": good["kid"][:8]},
        "empty-task": {**good, "task_id": ""},
        "blank-evidence": {**good, "evidence_ref": "   "},
        "iat-string": {**good, "iat": str(NOW)},
        "exp-bool": {**good, "exp": True},
    }
    return _forge(key, mutations[case])


@pytest.mark.parametrize("case", [
    "not-json", "json-array", "non-canonical-spacing", "duplicate-key", "extra-field", "missing-jti",
    "fencing-bool", "fencing-string", "fencing-zero", "kid-uppercase", "kid-short", "empty-task",
    "blank-evidence", "iat-string", "exp-bool",
])
def test_parse_rejects_each_malformed_payload(ra, key_c, case):
    _assert_rejected(ra, ra.ErrorCode.MALFORMED_TOKEN, lambda: ra.parse_vr2(_malformed_token(case, key_c)))


def test_parse_rejects_non_finite_timestamps(ra, key_c):
    body = json.dumps(_payload(key_c, exp=float("inf")), sort_keys=True, separators=(",", ":")).encode()
    assert b"Infinity" in body
    _assert_rejected(ra, ra.ErrorCode.MALFORMED_TOKEN, lambda: ra.parse_vr2(_forge(key_c, body=body)))


@pytest.mark.parametrize("token", [
    "", "vr1.e30.e30", "VR2.e30.e30", "vr2.onlyone", "vr2.a.b.c", "vr2.e30=.e30", "vr2.e3+.e30", "vr2..e30",
], ids=["empty", "wrong-prefix", "upper-prefix", "one-part", "three-parts", "padding", "non-url-alphabet", "empty-body"])
def test_parse_rejects_each_malformed_envelope(ra, token):
    _assert_rejected(ra, ra.ErrorCode.MALFORMED_TOKEN, lambda: ra.parse_vr2(token))


@pytest.mark.parametrize("overrides", [{"typ": "scp.checker_recommendation"}, {"v": 1}], ids=["checker-typ", "v1"])
def test_parse_rejects_a_token_of_another_type_or_version(ra, key_c, overrides):
    _assert_rejected(ra, ra.ErrorCode.WRONG_TOKEN_TYPE, lambda: ra.parse_vr2(_forge(key_c, _payload(key_c, **overrides))))


@pytest.mark.parametrize("overrides", [
    {"ttl_seconds": 0}, {"ttl_seconds": 300.5}, {"ttl_seconds": float("nan")},
    {"fencing_token": True}, {"fencing_token": 0}, {"fencing_token": "7"},
    {"task_id": ""}, {"task_id": "   "}, {"lease_id": None}, {"kid": "0" * 16},
], ids=["ttl-zero", "ttl-over-300", "ttl-nan", "fencing-bool", "fencing-zero", "fencing-str",
        "task-empty", "task-blank", "lease-none", "kid-mismatch"])
def test_mint_refuses_each_invalid_input(ra, key_c, overrides):
    kwargs = dict(task_id=TASK, attempt_id=ATTEMPT, lease_id=LEASE, fencing_token=FENCING, evidence_ref="e://1", now=NOW)
    kwargs.update(overrides)
    with pytest.raises(ValueError):
        ra.mint_vr2(key_c, **kwargs)


def test_mint_requires_an_ed25519_private_key_object(ra, key_c):
    raw_seed = key_c.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
    with pytest.raises(TypeError):
        ra.mint_vr2(raw_seed, task_id=TASK, attempt_id=ATTEMPT, lease_id=LEASE, fencing_token=1, evidence_ref="e://1")


def test_receipt_carrier_mirrors_the_signed_payload(ra, key_c):
    token = _forge(key_c)
    carrier = ra.receipt_carrier(token)
    assert (carrier.task_id, carrier.attempt_id, carrier.verifier_id) == (TASK, ATTEMPT, "scp.verifier_service")
    assert (carrier.verdict, carrier.evidence_ref, carrier.issued_at) == ("VERIFIED", "evidence://keyc/l1/1", NOW - 10.0)
    assert carrier.signature == token


# ===================================================== daemon request/response schema (AG §3.2)


def _request(**overrides) -> dict:
    request = {
        "task_id": "hands-7c9f8a12e34b",
        "lease_id": "lease-8899aabb-1122",
        "checkpoint_id": "ckpt-3344-waiting-tool",
        "evidence_ref": "hands://hands-7c9f8a12e34b/result/9f8e7d6c5b4a",
        "execution_hash": "sha256:" + "e3" * 32,
        "verifier_scope": "hands.mutating_action",
    }
    request.update(overrides)
    return request


def test_request_schema_accepts_the_ag_example_verbatim(ra):
    request = _request(task_id="hands-1 ")
    assert ra.validate_receipt_request(request) == request


@pytest.mark.parametrize("request_obj", [
    ["not", "an", "object"],
    {k: v for k, v in _request().items() if k != "lease_id"},
    _request(lease_id=""),
    _request(verifier_scope=7),
    _request(execution_hash="md5:abc"),
    _request(attempt_id="smuggled"),
], ids=["not-object", "missing-lease", "empty-lease", "non-string", "bad-hash", "unknown-field"])
def test_request_schema_rejects_each_violation(ra, request_obj):
    _assert_rejected(ra, ra.ErrorCode.MALFORMED_REQUEST, lambda: ra.validate_receipt_request(request_obj))


def test_success_response_round_trips_the_vr2_token(ra, key_c):
    token = _forge(key_c)
    response = ra.build_receipt_response(token)
    assert response["success"] is True and response["audit_trail"]["domain"] == "scp.verifier_receipt.v2"
    assert response["audit_trail"]["kid"] == _kid(key_c)
    assert ra.parse_receipt_response(json.loads(json.dumps(response))) == token


def test_error_response_raises_its_error_code(ra):
    response = ra.build_error_response(ra.ErrorCode.TASK_NOT_VERIFYING, "task is RUNNING")
    assert response == {"success": False, "error_code": "TASK_NOT_VERIFYING", "message": "task is RUNNING"}
    _assert_rejected(ra, ra.ErrorCode.TASK_NOT_VERIFYING, lambda: ra.parse_receipt_response(response))


@pytest.mark.parametrize("response", [
    None,
    {"success": "yes", "receipt": "vr2.e30.e30"},
    {"success": False, "error_code": "SOMETHING_NEW"},
    {"success": True, "receipt": "ed25519:abc"},
    {"success": True},
], ids=["none", "string-success", "unknown-code", "legacy-receipt", "missing-receipt"])
def test_response_schema_rejects_each_violation(ra, response):
    _assert_rejected(ra, ra.ErrorCode.MALFORMED_RESPONSE, lambda: ra.parse_receipt_response(response))


def test_error_codes_are_a_stable_contract(ra):
    assert {
        "MALFORMED_TOKEN", "WRONG_TOKEN_TYPE", "UNKNOWN_KID", "KID_REVOKED", "KID_EXPIRED", "BAD_SIGNATURE",
        "VERDICT_NOT_VERIFIED", "TASK_MISMATCH", "LEASE_MISMATCH", "ATTEMPT_MISMATCH", "FENCING_MISMATCH",
        "BAD_VALIDITY_WINDOW", "RECEIPT_NOT_YET_VALID", "RECEIPT_EXPIRED", "CARRIER_MISMATCH",
        "KEYRING_UNAVAILABLE", "MODE_INVALID", "HMAC_REJECTED", "MALFORMED_REQUEST", "MALFORMED_RESPONSE",
        "UNAUTHORIZED", "TASK_NOT_VERIFYING", "LEASE_INVALID", "KILL_SWITCH_ACTIVE", "PROFILE_UNSUPPORTED",
        "POSTCONDITION_FAILED", "INTERNAL_ERROR",
    } == set(ra.ERROR_CODES)
    with pytest.raises(ValueError):
        ra.build_error_response("NOT_A_CODE", "x")


# ===================================================================== verify_vr2


def test_verify_vr2_accepts_a_receipt_signed_by_an_active_key(ra, key_c):
    assert ra.verify_vr2(_forge(key_c), _ring(_entry(key_c)), now=NOW) == _payload(key_c)


@pytest.mark.parametrize("domain", [b"", b"scp.verifier_receipt.v1\x00", b"scp.approval.v1\x00", b"scp.verifier_receipt.v2"],
                         ids=["bare-canonical", "domain-v1", "approval-domain", "v2-without-nul"])
def test_verify_rejects_a_signature_under_another_domain(ra, key_c, domain):
    token = _forge(key_c, domain=domain)
    _assert_rejected(ra, ra.ErrorCode.BAD_SIGNATURE, lambda: ra.verify_vr2(token, _ring(_entry(key_c)), now=NOW))


def test_verify_rejects_an_unknown_kid(ra, key_c):
    rogue = Ed25519PrivateKey.generate()
    _assert_rejected(ra, ra.ErrorCode.UNKNOWN_KID, lambda: ra.verify_vr2(_forge(rogue), _ring(_entry(key_c)), now=NOW))


def test_verify_rejects_a_trusted_kid_with_a_rogue_signature(ra, key_c):
    rogue = Ed25519PrivateKey.generate()
    token = _forge(rogue, _payload(key_c))  # claims key C's kid, signed by the rogue key
    _assert_rejected(ra, ra.ErrorCode.BAD_SIGNATURE, lambda: ra.verify_vr2(token, _ring(_entry(key_c)), now=NOW))


@pytest.mark.parametrize(("status", "not_after", "code"), [
    ("revoked", None, "KID_REVOKED"), ("revoked", NOW + 360, "KID_REVOKED"), ("retiring", NOW - 1, "KID_EXPIRED"),
], ids=["revoked", "revoked-with-future-not-after", "retiring-past-not-after"])
def test_verify_rejects_a_key_that_is_no_longer_trusted(ra, key_c, status, not_after, code):
    ring = _ring(_entry(Ed25519PrivateKey.generate()), _entry(key_c, status=status, not_after=not_after))
    _assert_rejected(ra, code, lambda: ra.verify_vr2(_forge(key_c), ring, now=NOW))


def test_verify_accepts_a_retiring_key_before_not_after(ra, key_c):
    ring = _ring(_entry(Ed25519PrivateKey.generate()), _entry(key_c, status="retiring", not_after=NOW + 360))
    assert ra.verify_vr2(_forge(key_c), ring, now=NOW)["kid"] == _kid(key_c)


def test_verify_rejects_a_verdict_other_than_verified(ra, key_c):
    token = _forge(key_c, _payload(key_c, verdict="RECOMMEND_APPROVE"))
    _assert_rejected(ra, ra.ErrorCode.VERDICT_NOT_VERIFIED, lambda: ra.verify_vr2(token, _ring(_entry(key_c)), now=NOW))


@pytest.mark.parametrize(("iat", "exp", "code"), [
    (NOW - 200, NOW - 1, "RECEIPT_EXPIRED"),
    (NOW + 61, NOW + 200, "RECEIPT_NOT_YET_VALID"),
    (NOW - 1, NOW + 300, "BAD_VALIDITY_WINDOW"),
    (NOW + 10, NOW + 10, "BAD_VALIDITY_WINDOW"),
], ids=["expired", "iat-beyond-skew", "ttl-over-300", "empty-window"])
def test_verify_rejects_each_bad_time_window(ra, key_c, iat, exp, code):
    token = _forge(key_c, _payload(key_c, iat=iat, exp=exp))
    _assert_rejected(ra, code, lambda: ra.verify_vr2(token, _ring(_entry(key_c)), now=NOW))


# ======================================================================== keyring


def _broken_keyring(case: str, key) -> str:
    good = _entry(key)
    return {
        "not-json": "{keys:",
        "no-keys": json.dumps({"keys": []}),
        "extra-top-level": json.dumps({"keys": [good], "default": good["kid"]}),
        "bad-base64": _ring({**good, "pub": "***"}),
        "short-key": _ring({**good, "pub": base64.b64encode(b"\x01" * 31).decode()}),
        "kid-lies": _ring({**good, "kid": "0" * 16}),
        "unknown-status": _ring({**good, "status": "trusted"}),
        "retiring-without-not-after": _ring(_entry(Ed25519PrivateKey.generate()), {**good, "status": "retiring"}),
        "not-after-string": _ring({**good, "not_after": "tomorrow"}),
        "unknown-entry-field": _ring({**good, "alg": "none"}),
        "duplicate-key": _ring(good, dict(good)),
        "no-active-key": _ring({**good, "status": "revoked"}),
    }[case]


_BROKEN_KEYRINGS = [
    "not-json", "no-keys", "extra-top-level", "bad-base64", "short-key", "kid-lies", "unknown-status",
    "retiring-without-not-after", "not-after-string", "unknown-entry-field", "duplicate-key", "no-active-key",
]


@pytest.mark.parametrize("case", _BROKEN_KEYRINGS)
def test_parse_keyring_refuses_each_broken_keyring(ra, key_c, case):
    with pytest.raises(ra.VerifierKeyMissingError) as excinfo:
        ra.parse_keyring(_broken_keyring(case, key_c))
    assert excinfo.value.code == ra.ErrorCode.KEYRING_UNAVAILABLE


def test_parse_keyring_recomputes_kid_when_the_file_omits_it(ra, key_c):
    entry = {k: v for k, v in _entry(key_c).items() if k != "kid"}
    ring = ra.parse_keyring(_ring(entry))
    assert ring.lookup(_kid(key_c)).status == "active"
    assert ring.digest == "sha256:" + hashlib.sha256(_ring(entry).encode()).hexdigest()


# ========================================================== verify_completion_receipt

_HMAC_SECRET = "keyc-l1-verifier-hmac-test-secret-0123456789"
_KEY_C_ENV = (
    "SCP_VERIFIER_PUBLIC_KEYS", "SCP_VERIFIER_PUBLIC_KEYS_FILE", "SCP_VERIFIER_PRIVATE_KEY",
    "SCP_VERIFIER_PRIVATE_KEY_FILE", "SCP_RECEIPT_AUTHORITY_MODE", "SCP_RECEIPT_AUTHORITY_MODE_FILE",
)


@pytest.fixture
def env(monkeypatch, key_c):
    for name in _KEY_C_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SCP_VERIFIER_SECRET", _HMAC_SECRET)
    monkeypatch.setenv("SCP_VERIFIER_PUBLIC_KEYS", _ring(_entry(key_c)))
    return monkeypatch


@pytest.fixture
def enforce(env):
    env.setenv("SCP_RECEIPT_AUTHORITY_MODE", "enforce")
    return env


def _lease_row(**overrides) -> dict:
    row = {"lease_id": LEASE, "task_id": TASK, "attempt_id": ATTEMPT, "worker_id": "keyc-worker",
           "fencing_token": FENCING, "released": 0}
    row.update(overrides)
    return row


def _complete(ra, receipt, *, task_id: str = TASK, lease_row=None, **kwargs):
    return ra.verify_completion_receipt(
        receipt, task_id=task_id, lease_row=_lease_row() if lease_row is None else lease_row, now=NOW, **kwargs,
    )


def _hmac_receipt(task_id: str = TASK, *, secret: str | None = None, verifier_id: str = "hands-executor-self-verifier-v1"):
    from scp.core.verifier_receipt import VerifierReceipt, sign_verifier_receipt

    return sign_verifier_receipt(
        VerifierReceipt(task_id=task_id, verifier_id=verifier_id, verdict="VERIFIED",
                        evidence_ref=f"evidence://self/{task_id}", issued_at=time.time()),
        secret,
    )


# ---------------------------------------------------------------- positive controls


def test_positive_control_enforce_accepts_a_daemon_receipt_with_key_c_provenance(ra, enforce, key_c):
    token = _forge(key_c)
    result = _complete(ra, token)
    assert (result.provenance, result.mode) == ("VERIFIED_KEY_C", "enforce")
    assert (result.task_id, result.lease_id, result.attempt_id, result.fencing_token) == (TASK, LEASE, ATTEMPT, FENCING)
    assert (result.kid, result.jti, result.token) == (_kid(key_c), "a" * 32, token)
    assert result.keyring_digest == "sha256:" + hashlib.sha256(_ring(_entry(key_c)).encode()).hexdigest()
    assert result.journal_fields() == {
        "receipt_provenance": "VERIFIED_KEY_C", "receipt_mode": "enforce", "receipt_kid": _kid(key_c),
        "receipt_jti": "a" * 32, "receipt_token": token, "receipt_keyring_digest": result.keyring_digest,
    }


@pytest.mark.parametrize("form", ["token", "carrier", "carrier-dict"])
def test_positive_control_accepts_each_receipt_form(ra, enforce, key_c, form):
    token = _forge(key_c)
    receipt = {"token": token, "carrier": ra.receipt_carrier(token), "carrier-dict": ra.receipt_carrier(token).to_dict()}[form]
    assert _complete(ra, receipt).provenance == "VERIFIED_KEY_C"


def test_audit_mode_still_verifies_vr2_fully(ra, env, key_c):
    result = _complete(ra, _forge(key_c))
    assert (result.provenance, result.mode) == ("VERIFIED_KEY_C", "audit")


def test_positive_control_against_a_real_kernel_lease_row(ra, enforce, key_c, tmp_path):
    from scp.task_kernel import TaskKernel

    kernel = TaskKernel(tmp_path / "keyc_l1.sqlite3")
    try:
        task_id = "keyc-real-1"
        kernel.create_task(task_id, "keyc-owner", "goal", "R0", input_hash="sha256:" + "0" * 64)
        for state in ("PLANNING", "READY", "QUEUED"):
            kernel.transition(task_id, state, actor="keyc-scheduler", reason="keyc_setup")
        lease = kernel.claim(task_id, "keyc-worker", ttl_seconds=120)
        kernel.start(task_id, lease.lease_id)
        kernel.transition(task_id, "VERIFYING", actor="keyc-worker", reason="keyc_setup")
        row = kernel._assert_lease(lease.lease_id, task_id)
        token = ra.mint_vr2(key_c, task_id=task_id, attempt_id=lease.attempt_id, lease_id=lease.lease_id,
                            fencing_token=lease.fencing_token, evidence_ref="evidence://keyc/real", profile="journal_integrity")
        result = ra.verify_completion_receipt(ra.receipt_carrier(token), task_id=task_id, lease_row=row)
        assert (result.provenance, result.lease_id, result.fencing_token) == ("VERIFIED_KEY_C", lease.lease_id, lease.fencing_token)
        stale = ra.mint_vr2(key_c, task_id=task_id, attempt_id=lease.attempt_id, lease_id=lease.lease_id,
                            fencing_token=lease.fencing_token + 1, evidence_ref="evidence://keyc/real")
        _assert_rejected(ra, ra.ErrorCode.FENCING_MISMATCH,
                         lambda: ra.verify_completion_receipt(stale, task_id=task_id, lease_row=row))
    finally:
        kernel.close()


# ------------------------------------------------------------------- exact binding


@pytest.mark.parametrize(("receipt_task", "kernel_task"), [
    (TASK + " ", TASK), ("\t" + TASK, TASK), (TASK + "​", TASK), (TASK.upper(), TASK), (TASK, TASK + " "),
], ids=["receipt-trailing-space", "receipt-leading-tab", "receipt-zero-width", "receipt-case", "kernel-padded"])
def test_task_id_binding_is_exact(ra, enforce, key_c, receipt_task, kernel_task):
    token = _forge(key_c, _payload(key_c, task_id=receipt_task))
    _assert_rejected(ra, ra.ErrorCode.TASK_MISMATCH,
                     lambda: _complete(ra, token, task_id=kernel_task, lease_row=_lease_row(task_id=kernel_task)))


def test_lease_row_of_another_task_is_rejected(ra, enforce, key_c):
    _assert_rejected(ra, ra.ErrorCode.LEASE_MISMATCH, lambda: _complete(ra, _forge(key_c), lease_row=_lease_row(task_id="other")))


@pytest.mark.parametrize(("overrides", "code"), [
    ({"lease_id": "lease-keyc-old"}, "LEASE_MISMATCH"),
    ({"attempt_id": "attempt-keyc-0"}, "ATTEMPT_MISMATCH"),
    ({"fencing_token": FENCING - 1}, "FENCING_MISMATCH"),
], ids=["other-lease", "other-attempt", "stale-fencing"])
def test_receipt_bound_to_another_lease_attempt_or_fencing_is_rejected(ra, enforce, key_c, overrides, code):
    token = _forge(key_c, _payload(key_c, **overrides))
    _assert_rejected(ra, code, lambda: _complete(ra, token))


@pytest.mark.parametrize("row", [None, {"task_id": TASK, "lease_id": LEASE, "fencing_token": FENCING},
                                 _lease_row(fencing_token="seven")], ids=["none", "no-attempt", "fencing-not-int"])
def test_unusable_lease_row_is_rejected(ra, enforce, key_c, row):
    _assert_rejected(ra, ra.ErrorCode.LEASE_MISMATCH,
                     lambda: ra.verify_completion_receipt(_forge(key_c), task_id=TASK, lease_row=row, now=NOW))


# ------------------------------------------------------------------------- carrier


@pytest.mark.parametrize("change", [
    {"verifier_id": "independent-verifier"}, {"verdict": "VERIFIED "}, {"evidence_ref": "evidence://other"},
    {"task_id": TASK + " "}, {"attempt_id": None}, {"issued_at": NOW},
], ids=["verifier_id", "verdict", "evidence_ref", "task_id", "attempt_id", "issued_at"])
def test_carrier_fields_must_equal_the_signed_payload(ra, enforce, key_c, change):
    carrier = dataclasses.replace(ra.receipt_carrier(_forge(key_c)), **change)
    _assert_rejected(ra, ra.ErrorCode.CARRIER_MISMATCH, lambda: _complete(ra, carrier))


@pytest.mark.parametrize("shape", ["extra-key", "missing-key"])
def test_dict_carrier_must_have_exactly_the_receipt_fields(ra, enforce, key_c, shape):
    carrier = ra.receipt_carrier(_forge(key_c)).to_dict()
    if shape == "extra-key":
        carrier["provenance"] = "VERIFIED_KEY_C"
    else:
        del carrier["attempt_id"]
    _assert_rejected(ra, ra.ErrorCode.CARRIER_MISMATCH, lambda: _complete(ra, carrier))


# --------------------------------------------------------------------------- modes


def test_unset_mode_is_audit(ra, env):
    assert ra.resolve_mode() == "audit"


@pytest.mark.parametrize("value", ["enforced", "permissive", "off", "1"])
def test_unknown_mode_fails_closed_instead_of_falling_back_to_audit(ra, env, value):
    env.setenv("SCP_RECEIPT_AUTHORITY_MODE", value)
    _assert_rejected(ra, ra.ErrorCode.MODE_INVALID, lambda: _complete(ra, _hmac_receipt()))


def test_known_mode_is_read_case_insensitively(ra, env):
    env.setenv("SCP_RECEIPT_AUTHORITY_MODE", " Enforce ")
    _assert_rejected(ra, ra.ErrorCode.HMAC_REJECTED, lambda: _complete(ra, _hmac_receipt()))


def test_mode_file_wins_over_env(ra, env, tmp_path):
    mode_file = tmp_path / "mode"
    mode_file.write_text("enforce\n", encoding="utf-8")
    env.setenv("SCP_RECEIPT_AUTHORITY_MODE", "audit")
    env.setenv("SCP_RECEIPT_AUTHORITY_MODE_FILE", str(mode_file))
    _assert_rejected(ra, ra.ErrorCode.HMAC_REJECTED, lambda: _complete(ra, _hmac_receipt()))


@pytest.mark.parametrize("content", [None, "  \n"], ids=["missing-file", "empty-file"])
def test_broken_mode_file_fails_closed_without_env_fallback(ra, env, tmp_path, content):
    mode_file = tmp_path / "mode"
    if content is not None:
        mode_file.write_text(content, encoding="utf-8")
    env.setenv("SCP_RECEIPT_AUTHORITY_MODE", "audit")
    env.setenv("SCP_RECEIPT_AUTHORITY_MODE_FILE", str(mode_file))
    _assert_rejected(ra, ra.ErrorCode.MODE_INVALID, lambda: _complete(ra, _hmac_receipt()))


def test_enforce_floor_overrides_an_audit_env(ra, env):
    env.setenv("SCP_RECEIPT_AUTHORITY_MODE", "audit")
    _assert_rejected(ra, ra.ErrorCode.HMAC_REJECTED, lambda: _complete(ra, _hmac_receipt(), mode_floor="enforce"))


def test_unknown_floor_fails_closed(ra, env):
    _assert_rejected(ra, ra.ErrorCode.MODE_INVALID, lambda: _complete(ra, _hmac_receipt(), mode_floor="strict"))


# ------------------------------------------------------ no downgrade / fail-closed


def _set_broken_keyring(monkeypatch, case: str, key, tmp_path) -> None:
    if case == "unset":
        monkeypatch.delenv("SCP_VERIFIER_PUBLIC_KEYS", raising=False)
    elif case == "file-missing":
        monkeypatch.setenv("SCP_VERIFIER_PUBLIC_KEYS_FILE", str(tmp_path / "missing-keyring.json"))
    else:
        monkeypatch.setenv("SCP_VERIFIER_PUBLIC_KEYS", _broken_keyring(case, key))


@pytest.mark.parametrize("case", ["unset", "file-missing", *_BROKEN_KEYRINGS])
def test_enforce_with_a_missing_or_broken_keyring_rejects_a_valid_receipt(ra, enforce, key_c, tmp_path, case):
    _set_broken_keyring(enforce, case, key_c, tmp_path)
    _assert_rejected(ra, ra.ErrorCode.KEYRING_UNAVAILABLE, lambda: _complete(ra, _forge(key_c)))


@pytest.mark.parametrize("case", ["unset", "not-json"])
def test_enforce_with_a_broken_keyring_never_falls_back_to_hmac(ra, enforce, key_c, tmp_path, case):
    _set_broken_keyring(enforce, case, key_c, tmp_path)
    _assert_rejected(ra, ra.ErrorCode.KEYRING_UNAVAILABLE, lambda: _complete(ra, _hmac_receipt()))


def test_audit_with_a_missing_keyring_rejects_vr2_instead_of_downgrading(ra, env, key_c):
    env.delenv("SCP_VERIFIER_PUBLIC_KEYS")
    _assert_rejected(ra, ra.ErrorCode.KEYRING_UNAVAILABLE, lambda: _complete(ra, _forge(key_c)))


@pytest.mark.parametrize("mode", ["audit", "enforce"])
@pytest.mark.parametrize(("signature", "code"), [
    ("vr2.garbage", "MALFORMED_TOKEN"), ("rogue", "UNKNOWN_KID"),
], ids=["garbage", "rogue-signed"])
def test_a_bad_vr2_claim_is_rejected_in_every_mode(ra, env, mode, signature, code):
    env.setenv("SCP_RECEIPT_AUTHORITY_MODE", mode)
    if signature == "rogue":
        signature = _forge(Ed25519PrivateKey.generate())
    carrier = {**_hmac_receipt().to_dict(), "signature": signature}
    _assert_rejected(ra, code, lambda: _complete(ra, carrier))


@pytest.mark.parametrize("receipt", ["not-a-token", 42, None], ids=["bare-string", "int", "none"])
def test_unsupported_receipt_shapes_are_rejected(ra, env, receipt):
    _assert_rejected(ra, ra.ErrorCode.MALFORMED_TOKEN, lambda: _complete(ra, receipt))


# ------------------------------------------------------------- audit (GĐ1) HMAC path


def test_audit_accepts_hmac_as_self_attested_with_warning_and_metric(ra, env, caplog):
    caplog.set_level(logging.WARNING, logger="scp.security.receipt_authority")
    before = ra.self_signed_receipt_count()
    result = _complete(ra, _hmac_receipt())
    assert (result.provenance, result.mode, result.kid, result.token) == ("SELF_ATTESTED_HMAC", "audit", None, None)
    assert (result.task_id, result.verifier_id) == (TASK, "hands-executor-self-verifier-v1")
    assert result.journal_fields()["receipt_provenance"] == "SELF_ATTESTED_HMAC"
    assert ra.self_signed_receipt_count() == before + 1
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING and "SELF_ATTESTED_HMAC" in r.getMessage()]
    assert len(warnings) == 1 and "security.key_c.self_signed_receipt_count" in warnings[0].getMessage()


def test_audit_hmac_path_binds_the_task_id_exactly(ra, env):
    from scp.core.verifier_receipt import verify_verifier_receipt

    padded = _hmac_receipt(TASK + " ")
    assert verify_verifier_receipt(padded, task_id=TASK) is True  # legacy gate strips: B2
    _assert_rejected(ra, ra.ErrorCode.TASK_MISMATCH, lambda: _complete(ra, padded))


def test_audit_rejects_a_tampered_hmac_receipt(ra, env):
    from scp.core.verifier_receipt import InvalidReceiptSignatureError

    tampered = dataclasses.replace(_hmac_receipt(), evidence_ref="evidence://forged")
    with pytest.raises(InvalidReceiptSignatureError, match="tampered"):
        _complete(ra, tampered)


# --------------------------------------------------------------- role separation


def test_approval_issuer_private_key_cannot_sign_a_receipt(ra, enforce):
    from scp.security.secret_registry import resolve

    approval = Ed25519PrivateKey.from_private_bytes(base64.b64decode(resolve("SCP_APPROVAL_ISSUER_PRIVATE_KEY")))
    _assert_rejected(ra, ra.ErrorCode.UNKNOWN_KID, lambda: _complete(ra, _forge(approval)))


def test_keyring_reusing_the_approval_issuer_key_is_refused(ra, enforce, key_c):
    approval = Ed25519PrivateKey.generate()
    enforce.setenv("SCP_APPROVAL_ISSUER_PUBLIC_KEY", base64.b64encode(_raw_pub(approval)).decode())
    enforce.setenv("SCP_VERIFIER_PUBLIC_KEYS", _ring(_entry(key_c), _entry(approval)))
    _assert_rejected(ra, ra.ErrorCode.KEYRING_UNAVAILABLE, lambda: _complete(ra, _forge(key_c)))


def test_journal_secret_derived_key_cannot_sign_a_receipt(ra, enforce):
    journal_secret = "keyc-l1-journal-secret-0123456789abcdef!!"
    enforce.setenv("SCP_JOURNAL_SECRET", journal_secret)
    derived = Ed25519PrivateKey.from_private_bytes(hashlib.sha256(journal_secret.encode()).digest())
    _assert_rejected(ra, ra.ErrorCode.UNKNOWN_KID, lambda: _complete(ra, _forge(derived)))


def test_journal_secret_hmac_under_a_trusted_kid_is_not_a_signature(ra, enforce, key_c):
    journal_secret = b"keyc-l1-journal-secret-0123456789abcdef!!"
    body = _body(_payload(key_c))
    mac = hmac.new(journal_secret, V2_DOMAIN + body, hashlib.sha256).digest()
    token = "vr2." + _b64u(body) + "." + _b64u(mac + mac)  # 64 bytes, the Ed25519 signature length
    _assert_rejected(ra, ra.ErrorCode.BAD_SIGNATURE, lambda: _complete(ra, token))


def test_capability_secret_hmac_receipt_cannot_complete_in_enforce(ra, enforce):
    capability = "keyc-l1-capability-secret-0123456789abcdef"
    enforce.setenv("SCP_CAPABILITY_SECRET", capability)
    enforce.delenv("SCP_VERIFIER_SECRET")
    _assert_rejected(ra, ra.ErrorCode.HMAC_REJECTED, lambda: _complete(ra, _hmac_receipt(secret=capability)))


# ------------------------------------------------------ AG §6.1 attack vectors A/B/C


def test_ag_vector_a_worker_self_signed_hmac_is_prohibited_in_enforce(ra, enforce):
    from scp.core.verifier_receipt import VerifierReceipt, sign_verifier_receipt

    legacy = sign_verifier_receipt(VerifierReceipt(TASK, "rogue-worker", "VERIFIED", "evidence://fake", time.time()))
    with pytest.raises(ra.ReceiptAuthorityError, match="self-signed HMAC receipts are prohibited") as excinfo:
        _complete(ra, legacy)
    assert excinfo.value.code == ra.ErrorCode.HMAC_REJECTED


def test_ag_vector_b_worker_own_ed25519_keypair_is_rejected(ra, enforce):
    from scp.core.verifier_receipt import VerifierReceipt, sign_verifier_receipt_ed25519

    rogue = Ed25519PrivateKey.generate()
    _assert_rejected(ra, ra.ErrorCode.UNKNOWN_KID, lambda: _complete(ra, _forge(rogue)))
    rogue_b64 = base64.b64encode(rogue.private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())).decode()
    legacy_ed25519, _ = sign_verifier_receipt_ed25519(
        VerifierReceipt(TASK, "rogue-worker", "VERIFIED", "evidence://fake", time.time()), private_key_b64=rogue_b64)
    _assert_rejected(ra, ra.ErrorCode.HMAC_REJECTED, lambda: _complete(ra, legacy_ed25519))


def test_ag_vector_c_missing_public_key_fails_closed_in_enforce(ra, enforce, key_c):
    enforce.delenv("SCP_VERIFIER_PUBLIC_KEYS")
    with pytest.raises(ra.VerifierKeyMissingError) as excinfo:
        _complete(ra, _forge(key_c))
    assert excinfo.value.code == ra.ErrorCode.KEYRING_UNAVAILABLE


# ------------------------------------------------------------- registry + hole proof


def test_registry_declares_the_key_c_keyring_and_private_key_with_a_file_channel(monkeypatch, tmp_path):
    from scp.security.secret_registry import SECRET_PURPOSES, resolve

    for name in _KEY_C_ENV:
        monkeypatch.delenv(name, raising=False)
    keyring = SECRET_PURPOSES["SCP_VERIFIER_PUBLIC_KEYS"]
    private = SECRET_PURPOSES["SCP_VERIFIER_PRIVATE_KEY"]
    assert keyring.file_variant is True and "scp.security.receipt_authority" in keyring.consumers
    assert private.file_variant is True and "scp.verifier_service" in private.consumers
    keyring_file = tmp_path / "keyring.json"
    keyring_file.write_text('{"keys": []}\n', encoding="utf-8")
    monkeypatch.setenv("SCP_VERIFIER_PUBLIC_KEYS", "env-residue")
    monkeypatch.setenv("SCP_VERIFIER_PUBLIC_KEYS_FILE", str(keyring_file))
    assert resolve("SCP_VERIFIER_PUBLIC_KEYS") == '{"keys": []}'
    # The mode is not a secret: registering it would make the GĐ1 output gate block every
    # answer containing "audit" or "enforce".
    assert "SCP_RECEIPT_AUTHORITY_MODE" not in SECRET_PURPOSES


def test_keyring_file_channel_wins_over_env(ra, enforce, key_c, tmp_path):
    keyring_file = tmp_path / "keyring.json"
    keyring_file.write_text(_ring(_entry(key_c)), encoding="utf-8")
    enforce.setenv("SCP_VERIFIER_PUBLIC_KEYS", "{not json")
    enforce.setenv("SCP_VERIFIER_PUBLIC_KEYS_FILE", str(keyring_file))
    assert _complete(ra, _forge(key_c)).provenance == "VERIFIED_KEY_C"


def test_unreadable_keyring_file_fails_closed_without_env_fallback(ra, enforce, key_c, tmp_path):
    enforce.setenv("SCP_VERIFIER_PUBLIC_KEYS_FILE", str(tmp_path / "missing.json"))  # env still holds a valid ring
    _assert_rejected(ra, ra.ErrorCode.KEYRING_UNAVAILABLE, lambda: _complete(ra, _forge(key_c)))


def test_p2_hole_legacy_gate_accepts_an_executor_self_signed_receipt(monkeypatch):
    """Lỗ gốc trên đường hiện có (xanh cả trên base): ai đọc được env đều tự ký được VERIFIED."""
    from scp.core.verifier_receipt import verify_verifier_receipt

    monkeypatch.setenv("SCP_VERIFIER_SECRET", _HMAC_SECRET)
    assert verify_verifier_receipt(_hmac_receipt(), task_id=TASK) is True


def test_p2_the_same_self_signed_receipt_is_rejected_by_key_c_enforce(ra, enforce):
    _assert_rejected(ra, ra.ErrorCode.HMAC_REJECTED, lambda: _complete(ra, _hmac_receipt()))
