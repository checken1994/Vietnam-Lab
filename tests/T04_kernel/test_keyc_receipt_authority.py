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
import hashlib
import json

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


_MALFORMED_BODIES = {
    "not-json": b"not json",
    "json-array": b"[1,2]",
    "non-canonical-spacing": json.dumps({"a": 1}, indent=1).encode(),
    "duplicate-key": None,  # built in the test from a valid body
}


def _malformed_token(case: str, key) -> str:
    good = _payload(key)
    if case == "duplicate-key":
        body = _body(good)[:-1] + b',"task_id":"other"}'
        return _forge(key, body=body)
    if case in _MALFORMED_BODIES:
        return _forge(key, body=_MALFORMED_BODIES[case])
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


def test_verify_rejects_a_revoked_kid(ra, key_c):
    fresh = Ed25519PrivateKey.generate()
    ring = _ring(_entry(fresh), _entry(key_c, status="revoked"))
    _assert_rejected(ra, ra.ErrorCode.KID_REVOKED, lambda: ra.verify_vr2(_forge(key_c), ring, now=NOW))


def test_verify_accepts_a_retiring_key_before_not_after(ra, key_c):
    fresh = Ed25519PrivateKey.generate()
    ring = _ring(_entry(fresh), _entry(key_c, status="retiring", not_after=NOW + 360))
    assert ra.verify_vr2(_forge(key_c), ring, now=NOW)["kid"] == _kid(key_c)


def test_verify_rejects_a_retiring_key_after_not_after(ra, key_c):
    fresh = Ed25519PrivateKey.generate()
    ring = _ring(_entry(fresh), _entry(key_c, status="retiring", not_after=NOW - 1))
    _assert_rejected(ra, ra.ErrorCode.KID_EXPIRED, lambda: ra.verify_vr2(_forge(key_c), ring, now=NOW))


def test_verify_rejects_a_verdict_other_than_verified(ra, key_c):
    token = _forge(key_c, _payload(key_c, verdict="RECOMMEND_APPROVE"))
    _assert_rejected(ra, ra.ErrorCode.VERDICT_NOT_VERIFIED, lambda: ra.verify_vr2(token, _ring(_entry(key_c)), now=NOW))


def test_verify_rejects_an_expired_receipt(ra, key_c):
    token = _forge(key_c, _payload(key_c, iat=NOW - 200, exp=NOW - 1))
    _assert_rejected(ra, ra.ErrorCode.RECEIPT_EXPIRED, lambda: ra.verify_vr2(token, _ring(_entry(key_c)), now=NOW))


def test_verify_rejects_an_iat_beyond_the_clock_skew(ra, key_c):
    token = _forge(key_c, _payload(key_c, iat=NOW + 61, exp=NOW + 200))
    _assert_rejected(ra, ra.ErrorCode.RECEIPT_NOT_YET_VALID, lambda: ra.verify_vr2(token, _ring(_entry(key_c)), now=NOW))


def test_verify_rejects_a_validity_window_longer_than_300s(ra, key_c):
    token = _forge(key_c, _payload(key_c, iat=NOW - 1, exp=NOW + 300))
    _assert_rejected(ra, ra.ErrorCode.BAD_VALIDITY_WINDOW, lambda: ra.verify_vr2(token, _ring(_entry(key_c)), now=NOW))


def test_verify_rejects_an_empty_validity_window(ra, key_c):
    token = _forge(key_c, _payload(key_c, iat=NOW + 10, exp=NOW + 10))
    _assert_rejected(ra, ra.ErrorCode.BAD_VALIDITY_WINDOW, lambda: ra.verify_vr2(token, _ring(_entry(key_c)), now=NOW))


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
