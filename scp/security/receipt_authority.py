"""Key C (1.A) — verifier receipt authority: ``vr2`` Ed25519 receipts bound to one lease attempt.

Trước Key C, receipt hoàn tất task là HMAC dưới SCP_VERIFIER_SECRET (fallback
SCP_CAPABILITY_SECRET): mọi code trong tiến trình kernel (executor hands, soak, prober)
đọc cùng khóa nên tự ký được "VERIFIED" cho chính việc mình làm (P2 / FA-05).

Key C tách quyền ký: PRIVATE key chỉ nằm ở verifier daemon (tiến trình riêng, lane L3);
kernel chỉ giữ PUBLIC keyring nên verify được nhưng không mint được.

Định dạng DUY NHẤT (merge directive Khóa C: bỏ domain v1 và bare-canonical):
``vr2.<base64url(payload)>.<base64url(signature)>``; chữ ký Ed25519 phủ
``b"scp.verifier_receipt.v2\\x00" + payload``; payload là canonical JSON
(sort_keys, separators gọn, UTF-8, cấm NaN/Infinity) gồm đúng các trường ``PAYLOAD_FIELDS``.

Module này là codec + phía mint (``mint_vr2``, dùng cho daemon L3 và test) + schema
request/response của daemon (AG §3.2) + bộ ``ErrorCode`` dùng chung giữa kernel và daemon.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import math
import os
import re
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scp.core.verifier_receipt import InvalidReceiptSignatureError, VerifierReceipt, verify_verifier_receipt
from scp.security.secret_registry import resolve

logger = logging.getLogger("scp.security.receipt_authority")

RECEIPT_TOKEN_PREFIX = "vr2."
RECEIPT_DOMAIN_NAME = "scp.verifier_receipt.v2"
RECEIPT_SIGNING_DOMAIN = b"scp.verifier_receipt.v2\x00"
RECEIPT_V2_PREFIX = RECEIPT_TOKEN_PREFIX  # tên L3 import
DOMAIN = RECEIPT_SIGNING_DOMAIN  # tên L3 import
RECEIPT_TOKEN_TYPE = "scp.verifier_receipt"
RECEIPT_VERSION = 2
RECEIPT_VERDICT = "VERIFIED"
KID_DOMAIN = b"scp.verifier_key.kid.v1\x00"
MAX_TTL_SECONDS = 300.0
DEFAULT_TTL_SECONDS = 120.0
MAX_CLOCK_SKEW_SECONDS = 60.0
DEFAULT_VERIFIER_ID = "scp.verifier_service"
DEFAULT_PROFILE = "unspecified"
KEYRING_ENV = "SCP_VERIFIER_PUBLIC_KEYS"
APPROVAL_PUBLIC_KEY_ENV = "SCP_APPROVAL_ISSUER_PUBLIC_KEY"
KEY_STATUSES = ("active", "retiring", "revoked")
_KEY_ENTRY_FIELDS = frozenset({"kid", "pub", "status", "not_after"})

PAYLOAD_FIELDS = frozenset({
    "v", "typ", "kid", "task_id", "attempt_id", "lease_id", "fencing_token",
    "verifier_id", "profile", "verdict", "evidence_ref", "iat", "exp", "jti",
})
_TEXT_FIELDS = ("task_id", "attempt_id", "lease_id", "verifier_id", "profile", "verdict", "evidence_ref", "jti")
_KID_RE = re.compile(r"[0-9a-f]{16}")


class ErrorCode:
    """Mã lỗi ổn định dùng chung kernel ↔ daemon (AG §3.2). Chỉ thêm, không đổi nghĩa."""

    MALFORMED_TOKEN = "MALFORMED_TOKEN"
    WRONG_TOKEN_TYPE = "WRONG_TOKEN_TYPE"
    UNKNOWN_KID = "UNKNOWN_KID"
    KID_REVOKED = "KID_REVOKED"
    KID_EXPIRED = "KID_EXPIRED"
    BAD_SIGNATURE = "BAD_SIGNATURE"
    VERDICT_NOT_VERIFIED = "VERDICT_NOT_VERIFIED"
    TASK_MISMATCH = "TASK_MISMATCH"
    LEASE_MISMATCH = "LEASE_MISMATCH"
    ATTEMPT_MISMATCH = "ATTEMPT_MISMATCH"
    FENCING_MISMATCH = "FENCING_MISMATCH"
    BAD_VALIDITY_WINDOW = "BAD_VALIDITY_WINDOW"
    RECEIPT_NOT_YET_VALID = "RECEIPT_NOT_YET_VALID"
    RECEIPT_EXPIRED = "RECEIPT_EXPIRED"
    CARRIER_MISMATCH = "CARRIER_MISMATCH"
    KEYRING_UNAVAILABLE = "KEYRING_UNAVAILABLE"
    MODE_INVALID = "MODE_INVALID"
    HMAC_REJECTED = "HMAC_REJECTED"
    MALFORMED_REQUEST = "MALFORMED_REQUEST"
    MALFORMED_RESPONSE = "MALFORMED_RESPONSE"
    # Phía daemon (L3): kiểm độc lập từ DB/evidence trước khi ký.
    UNAUTHORIZED = "UNAUTHORIZED"
    TASK_NOT_VERIFYING = "TASK_NOT_VERIFYING"
    LEASE_INVALID = "LEASE_INVALID"
    KILL_SWITCH_ACTIVE = "KILL_SWITCH_ACTIVE"
    PROFILE_UNSUPPORTED = "PROFILE_UNSUPPORTED"
    POSTCONDITION_FAILED = "POSTCONDITION_FAILED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


ERROR_CODES = frozenset(v for k, v in vars(ErrorCode).items() if k.isupper())


class ReceiptAuthorityError(InvalidReceiptSignatureError):
    """Receipt bị từ chối fail-closed; ``code`` thuộc ``ERROR_CODES``."""

    def __init__(self, code: str, message: str) -> None:
        if code not in ERROR_CODES:
            raise ValueError(f"unknown receipt authority error code {code!r}")
        self.code = code
        super().__init__(f"[{code}] {message}")


class VerifierKeyMissingError(ReceiptAuthorityError):
    """Keyring public key thiếu/rỗng/hỏng (AG §4.3): fail-closed, KHÔNG BAO GIỜ fallback HMAC."""

    def __init__(self, message: str) -> None:
        super().__init__(ErrorCode.KEYRING_UNAVAILABLE, message)


def _fail(code: str, message: str) -> ReceiptAuthorityError:
    return ReceiptAuthorityError(code, message)


def _now(now: float | None) -> float:
    current = time.time() if now is None else float(now)
    if not math.isfinite(current):
        raise ValueError("now must be finite")
    return current


# --------------------------------------------------------------------------- codec

def b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def b64url_decode(text: str) -> bytes:
    """Giải mã base64url KHÔNG padding, chỉ nhận dạng mã hóa chuẩn (chống malleability)."""
    if not isinstance(text, str) or not text:
        raise _fail(ErrorCode.MALFORMED_TOKEN, "empty base64url segment")
    try:
        raw = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (binascii.Error, ValueError) as exc:
        raise _fail(ErrorCode.MALFORMED_TOKEN, "invalid base64url segment") from exc
    if b64url_encode(raw) != text:
        raise _fail(ErrorCode.MALFORMED_TOKEN, "non-canonical base64url segment")
    return raw


def canonical_payload_bytes(payload: dict[str, Any]) -> bytes:
    """Canonical JSON của payload; NaN/Infinity bị cấm (ValueError)."""
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False,
    ).encode("utf-8")


def compute_kid(public_key_raw: bytes) -> str:
    """kid = 16 hex đầu của sha256(KID_DOMAIN + raw public key). Kernel luôn tự tính lại."""
    if not isinstance(public_key_raw, (bytes, bytearray)) or len(public_key_raw) != 32:
        raise ValueError("Ed25519 public key must be 32 raw bytes")
    return hashlib.sha256(KID_DOMAIN + bytes(public_key_raw)).hexdigest()[:16]


def public_key_raw(private_key: Any) -> bytes:
    from cryptography.hazmat.primitives import serialization

    return private_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def keyring_entry(public_key: bytes, status: str = "active", not_after: float | None = None) -> dict[str, Any]:
    """Một entry keyring ``{kid,pub,status[,not_after]}`` (cho keygen L3 và test)."""
    entry: dict[str, Any] = {
        "kid": compute_kid(public_key),
        "pub": base64.b64encode(bytes(public_key)).decode("ascii"),
        "status": status,
    }
    if not_after is not None:
        entry["not_after"] = not_after
    return entry


@dataclass(frozen=True)
class Vr2Token:
    """Token đã parse cấu trúc + schema, CHƯA verify chữ ký."""

    token: str
    payload: dict[str, Any]
    body: bytes
    signature: bytes


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def parse_vr2(token: Any) -> dict[str, Any]:
    """Payload của token ``vr2.`` sau khi kiểm cấu trúc + schema. KHÔNG verify chữ ký."""
    return dict(decode_vr2(token).payload)


def decode_vr2(token: Any) -> Vr2Token:
    """Parse ``vr2.`` token: cấu trúc, canonical body, đúng tập trường và kiểu. Không verify chữ ký."""
    if not isinstance(token, str) or not token.startswith(RECEIPT_TOKEN_PREFIX):
        raise _fail(ErrorCode.MALFORMED_TOKEN, "receipt token must start with 'vr2.'")
    parts = token[len(RECEIPT_TOKEN_PREFIX):].split(".")
    if len(parts) != 2:
        raise _fail(ErrorCode.MALFORMED_TOKEN, "vr2 token must have exactly payload.signature")
    body = b64url_decode(parts[0])
    signature = b64url_decode(parts[1])
    try:
        payload = json.loads(body.decode("utf-8"))
        canonical = canonical_payload_bytes(payload)
    except (UnicodeDecodeError, ValueError) as exc:
        raise _fail(ErrorCode.MALFORMED_TOKEN, "vr2 payload is not canonical JSON") from exc
    if not isinstance(payload, dict) or canonical != body:
        raise _fail(ErrorCode.MALFORMED_TOKEN, "vr2 payload is not a canonical JSON object")
    if set(payload) != PAYLOAD_FIELDS:
        raise _fail(ErrorCode.MALFORMED_TOKEN, "vr2 payload fields do not match the v2 schema")
    if payload["v"] != RECEIPT_VERSION or payload["typ"] != RECEIPT_TOKEN_TYPE:
        raise _fail(ErrorCode.WRONG_TOKEN_TYPE, "token is not a v2 scp.verifier_receipt")
    for name in _TEXT_FIELDS:
        value = payload[name]
        if not isinstance(value, str) or not value.strip():
            raise _fail(ErrorCode.MALFORMED_TOKEN, f"vr2 field {name} must be a non-empty string")
    if not isinstance(payload["kid"], str) or not _KID_RE.fullmatch(payload["kid"]):
        raise _fail(ErrorCode.MALFORMED_TOKEN, "vr2 kid must be 16 lowercase hex")
    fencing = payload["fencing_token"]
    if type(fencing) is not int or fencing < 1:
        raise _fail(ErrorCode.MALFORMED_TOKEN, "vr2 fencing_token must be a positive integer")
    if not (_is_number(payload["iat"]) and _is_number(payload["exp"])):
        raise _fail(ErrorCode.MALFORMED_TOKEN, "vr2 iat/exp must be numbers")
    return Vr2Token(token=token, payload=payload, body=body, signature=signature)


def mint_vr2(
    private_key: Any,
    *,
    task_id: str,
    attempt_id: str,
    lease_id: str,
    fencing_token: int,
    evidence_ref: str,
    verdict: str = RECEIPT_VERDICT,
    kid: str | None = None,
    verifier_id: str = DEFAULT_VERIFIER_ID,
    profile: str = DEFAULT_PROFILE,
    ttl_seconds: float = DEFAULT_TTL_SECONDS,
    now: float | None = None,
) -> str:
    """Ký một receipt ``vr2`` cho đúng một lease attempt. Chỉ verifier daemon (giữ private key C) gọi.

    ``task_id`` được giữ NGUYÊN VĂN (không strip): receipt chỉ hoàn tất đúng task id đó.
    ``kid`` luôn tính từ public key của ``private_key``; nếu truyền vào thì phải khớp.
    Kernel chỉ nhận ``verdict == "VERIFIED"``; verdict khác vẫn ký được (attestation phủ định).
    """
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    if not isinstance(private_key, Ed25519PrivateKey):
        raise TypeError("mint_vr2 requires an Ed25519PrivateKey object")
    fields = {
        "task_id": task_id, "attempt_id": attempt_id, "lease_id": lease_id, "verdict": verdict,
        "evidence_ref": evidence_ref, "verifier_id": verifier_id, "profile": profile,
    }
    for name, value in fields.items():
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must be a non-empty string")
    if type(fencing_token) is not int or fencing_token < 1:
        raise ValueError("fencing_token must be a positive integer")
    ttl = float(ttl_seconds)
    if not math.isfinite(ttl) or ttl <= 0 or ttl > MAX_TTL_SECONDS:
        raise ValueError(f"ttl_seconds must be in (0, {MAX_TTL_SECONDS:.0f}]")
    issued_at = _now(now)
    own_kid = compute_kid(public_key_raw(private_key))
    if kid is not None and kid != own_kid:
        raise ValueError("kid does not match the signing key")
    payload = dict(fields)
    payload.update({
        "v": RECEIPT_VERSION,
        "typ": RECEIPT_TOKEN_TYPE,
        "kid": own_kid,
        "fencing_token": fencing_token,
        "iat": issued_at,
        "exp": issued_at + ttl,
        "jti": secrets.token_hex(16),
    })
    body = canonical_payload_bytes(payload)
    signature = private_key.sign(RECEIPT_SIGNING_DOMAIN + body)
    return f"{RECEIPT_TOKEN_PREFIX}{b64url_encode(body)}.{b64url_encode(signature)}"


def receipt_carrier(token: str) -> VerifierReceipt:
    """VerifierReceipt mang token (signature=token); mọi trường ngoài bằng đúng payload đã ký."""
    p = decode_vr2(token).payload
    return VerifierReceipt(
        task_id=p["task_id"], verifier_id=p["verifier_id"], verdict=p["verdict"],
        evidence_ref=p["evidence_ref"], issued_at=float(p["iat"]), signature=token,
        attempt_id=p["attempt_id"],
    )


# ------------------------------------------------- daemon request/response schema (AG §3.2)

REQUEST_REQUIRED_FIELDS = ("task_id", "lease_id", "evidence_ref", "verifier_scope")
REQUEST_OPTIONAL_FIELDS = ("checkpoint_id", "execution_hash")
_EXECUTION_HASH_RE = re.compile(r"sha256:[0-9a-f]{64}")


def validate_receipt_request(request: Any) -> dict[str, str]:
    """Schema request ``POST /v1/receipt/issue`` (AG §3.2), strict: thiếu/thừa/sai kiểu → lỗi.

    Giá trị giữ nguyên văn (không strip): daemon tự kiểm task/lease từ DB, không tin request.
    """
    if not isinstance(request, dict):
        raise _fail(ErrorCode.MALFORMED_REQUEST, "request must be a JSON object")
    allowed = set(REQUEST_REQUIRED_FIELDS) | set(REQUEST_OPTIONAL_FIELDS)
    unknown = sorted(set(request) - allowed)
    missing = [name for name in REQUEST_REQUIRED_FIELDS if name not in request]
    if unknown or missing:
        raise _fail(ErrorCode.MALFORMED_REQUEST, f"unknown fields {unknown}, missing fields {missing}")
    for name, value in request.items():
        if not isinstance(value, str) or not value.strip():
            raise _fail(ErrorCode.MALFORMED_REQUEST, f"{name} must be a non-empty string")
    execution_hash = request.get("execution_hash")
    if execution_hash is not None and not _EXECUTION_HASH_RE.fullmatch(execution_hash):
        raise _fail(ErrorCode.MALFORMED_REQUEST, "execution_hash must be sha256:<64 lowercase hex>")
    return dict(request)


def build_receipt_response(token: str) -> dict[str, Any]:
    """Response thành công: ``receipt`` là token vr2 (format duy nhất), kèm audit_trail."""
    p = decode_vr2(token).payload
    return {
        "success": True,
        "receipt": token,
        "audit_trail": {"domain": RECEIPT_DOMAIN_NAME, "kid": p["kid"], "jti": p["jti"], "exp": p["exp"]},
    }


def build_error_response(code: str, message: str) -> dict[str, Any]:
    if code not in ERROR_CODES:
        raise ValueError(f"unknown receipt authority error code {code!r}")
    return {"success": False, "error_code": code, "message": str(message)}


def parse_receipt_response(response: Any) -> str:
    """Đọc response của daemon: trả token vr2, hoặc raise ReceiptAuthorityError(error_code)."""
    if not isinstance(response, dict) or not isinstance(response.get("success"), bool):
        raise _fail(ErrorCode.MALFORMED_RESPONSE, "response must be an object with boolean success")
    if response["success"] is False:
        code = response.get("error_code")
        if code not in ERROR_CODES:
            raise _fail(ErrorCode.MALFORMED_RESPONSE, "error response carries an unknown error_code")
        raise _fail(code, str(response.get("message") or "verifier daemon refused the receipt"))
    receipt = response.get("receipt")
    if not isinstance(receipt, str) or not receipt.startswith(RECEIPT_TOKEN_PREFIX):
        raise _fail(ErrorCode.MALFORMED_RESPONSE, "success response must carry a vr2 receipt token")
    decode_vr2(receipt)
    return receipt


# ------------------------------------------------------------ public keyring + verify

@dataclass(frozen=True)
class KeyEntry:
    kid: str
    public_raw: bytes
    status: str
    not_after: float | None
    public_key: Any


@dataclass(frozen=True)
class Keyring:
    """Public keyring Key C. ``digest`` = sha256 văn bản nguồn (event RECEIPT_KEYRING_LOADED)."""

    entries: tuple[KeyEntry, ...]
    digest: str

    def lookup(self, kid: str) -> KeyEntry | None:
        for entry in self.entries:
            if entry.kid == kid:
                return entry
        return None


def _decode_public_key(value: Any) -> bytes:
    if not isinstance(value, str):
        raise VerifierKeyMissingError("keyring pub must be a base64 string")
    try:
        raw = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise VerifierKeyMissingError("keyring pub is not valid base64") from exc
    if len(raw) != 32:
        raise VerifierKeyMissingError("keyring pub must be a raw 32-byte Ed25519 key")
    return raw


def _parse_entry(item: Any, forbidden: tuple[bytes, ...]) -> KeyEntry:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    if not isinstance(item, dict) or not set(item) <= _KEY_ENTRY_FIELDS or not {"pub", "status"} <= set(item):
        raise VerifierKeyMissingError("keyring entry must be {kid?, pub, status, not_after?}")
    raw = _decode_public_key(item["pub"])
    kid = compute_kid(raw)
    if "kid" in item and item["kid"] != kid:
        raise VerifierKeyMissingError("keyring kid does not match its public key")
    status = item["status"]
    if status not in KEY_STATUSES:
        raise VerifierKeyMissingError("keyring status must be active, retiring or revoked")
    not_after = item.get("not_after")
    if not_after is not None and not (_is_number(not_after) and math.isfinite(not_after)):
        raise VerifierKeyMissingError("keyring not_after must be a finite number")
    if status == "retiring" and not_after is None:
        raise VerifierKeyMissingError("a retiring key needs not_after")
    if raw in forbidden:
        raise VerifierKeyMissingError("keyring reuses the approval issuer key (Key B/C role separation)")
    try:
        public_key = Ed25519PublicKey.from_public_bytes(raw)
    except ValueError as exc:
        raise VerifierKeyMissingError("keyring pub is not a usable Ed25519 key") from exc
    return KeyEntry(kid=kid, public_raw=raw, status=status, not_after=not_after, public_key=public_key)


def parse_keyring(source: Any, *, forbidden_public_keys: tuple[bytes, ...] = ()) -> Keyring:
    """Parse keyring ``{"keys":[{kid?,pub,status,not_after?}]}`` strict, fail-closed.

    kid luôn được tính lại từ pub; cần ít nhất một key active; cấm key trùng nhau và cấm
    public key nằm trong ``forbidden_public_keys`` (khóa của vai trò khác, ví dụ approval issuer).
    """
    try:
        text = source if isinstance(source, str) else json.dumps(source, sort_keys=True)
        doc = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise VerifierKeyMissingError("keyring is not valid JSON") from exc
    if not isinstance(doc, dict) or set(doc) != {"keys"} or not isinstance(doc["keys"], list) or not doc["keys"]:
        raise VerifierKeyMissingError("keyring must be an object with a non-empty 'keys' list")
    entries: list[KeyEntry] = []
    for item in doc["keys"]:
        entry = _parse_entry(item, tuple(forbidden_public_keys))
        if any(e.kid == entry.kid for e in entries):
            raise VerifierKeyMissingError("keyring lists the same key twice")
        entries.append(entry)
    if not any(e.status == "active" for e in entries):
        raise VerifierKeyMissingError("keyring has no active key")
    digest = "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()
    return Keyring(entries=tuple(entries), digest=digest)


def _approval_public_keys() -> tuple[bytes, ...]:
    value = resolve(APPROVAL_PUBLIC_KEY_ENV)
    if not value:
        return ()
    try:
        raw = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        return ()  # approval issuer tự fail-closed với khóa hỏng; không có gì để so
    return (raw,)


def load_keyring() -> Keyring:
    """Nạp keyring từ ``SCP_VERIFIER_PUBLIC_KEYS`` (env hoặc ``_FILE``); thiếu/hỏng → VerifierKeyMissingError."""
    text = resolve(KEYRING_ENV)
    if text is None:
        raise VerifierKeyMissingError(f"{KEYRING_ENV} is missing, empty or unreadable")
    return parse_keyring(text, forbidden_public_keys=_approval_public_keys())


def _coerce_keyring(keyring: Any) -> Keyring:
    if isinstance(keyring, Keyring):
        return keyring
    if keyring is None:
        raise VerifierKeyMissingError("no verifier keyring supplied")
    return parse_keyring(keyring, forbidden_public_keys=_approval_public_keys())


def verify_vr2(token: Any, keyring: Any, *, now: float | None = None) -> dict[str, Any]:
    """Verify chữ ký + kid + verdict + cửa sổ thời gian bằng PUBLIC keyring; trả payload.

    KHÔNG kiểm binding task/lease/attempt/fencing — đó là việc của ``verify_completion_receipt``.
    """
    from cryptography.exceptions import InvalidSignature

    ring = _coerce_keyring(keyring)
    parsed = decode_vr2(token)
    payload = parsed.payload
    entry = ring.lookup(payload["kid"])
    if entry is None:
        raise _fail(ErrorCode.UNKNOWN_KID, "receipt kid is not in the verifier keyring")
    if entry.status == "revoked":
        raise _fail(ErrorCode.KID_REVOKED, "receipt was signed by a revoked verifier key")
    current = _now(now)
    if entry.not_after is not None and current > entry.not_after:
        raise _fail(ErrorCode.KID_EXPIRED, "verifier key is past its not_after")
    try:
        entry.public_key.verify(parsed.signature, RECEIPT_SIGNING_DOMAIN + parsed.body)
    except InvalidSignature as exc:
        raise _fail(ErrorCode.BAD_SIGNATURE, "vr2 Ed25519 signature verification failed") from exc
    if payload["verdict"] != RECEIPT_VERDICT:
        raise _fail(ErrorCode.VERDICT_NOT_VERIFIED, "completion requires verdict VERIFIED")
    iat, exp = float(payload["iat"]), float(payload["exp"])
    if not (iat < exp and exp - iat <= MAX_TTL_SECONDS):
        raise _fail(ErrorCode.BAD_VALIDITY_WINDOW, f"receipt validity must be in (0, {MAX_TTL_SECONDS:.0f}] s")
    if iat > current + MAX_CLOCK_SKEW_SECONDS:
        raise _fail(ErrorCode.RECEIPT_NOT_YET_VALID, "receipt iat is in the future")
    if current > exp:
        raise _fail(ErrorCode.RECEIPT_EXPIRED, "receipt has expired")
    return dict(payload)


# ------------------------------------------------- completion gate (L2a gọi TRONG transaction)

MODE_ENV = "SCP_RECEIPT_AUTHORITY_MODE"
MODE_AUDIT = "audit"
MODE_ENFORCE = "enforce"
MODES = (MODE_AUDIT, MODE_ENFORCE)
PROVENANCE_KEY_C = "VERIFIED_KEY_C"
PROVENANCE_SELF_ATTESTED = "SELF_ATTESTED_HMAC"
SELF_SIGNED_METRIC = "security.key_c.self_signed_receipt_count"
_CARRIER_FIELDS = (
    ("task_id", "task_id"), ("verifier_id", "verifier_id"), ("verdict", "verdict"),
    ("evidence_ref", "evidence_ref"), ("attempt_id", "attempt_id"), ("issued_at", "iat"),
)
_DICT_CARRIER_KEYS = frozenset({"task_id", "verifier_id", "verdict", "evidence_ref", "issued_at", "signature", "attempt_id"})
_self_signed_lock = threading.Lock()
_self_signed_count = 0


def _read_mode_setting() -> str | None:
    """Đọc mode theo ngữ nghĩa HC-08: ``_FILE`` thắng env; ``_FILE`` hỏng/rỗng → MODE_INVALID.

    Mode KHÔNG nằm trong secret_registry: cổng GĐ1 coi mọi giá trị registry là secret, nên
    đăng ký "audit"/"enforce" sẽ chặn mọi câu trả lời chứa hai từ đó.
    """
    file_ref = os.environ.get(f"{MODE_ENV}_FILE", "").strip()
    if file_ref:
        try:
            value = Path(file_ref).expanduser().read_text(encoding="utf-8-sig").strip()
        except (OSError, UnicodeDecodeError) as exc:
            raise _fail(ErrorCode.MODE_INVALID, f"{MODE_ENV}_FILE is unreadable ({type(exc).__name__})") from exc
        if not value:
            raise _fail(ErrorCode.MODE_INVALID, f"{MODE_ENV}_FILE points to an empty file")
        return value
    return os.environ.get(MODE_ENV, "").strip() or None


def resolve_mode(mode_floor: str | None = None) -> str:
    """Mode hiệu lực = max(env, floor). Chưa đặt → audit (GĐ1). Giá trị lạ → MODE_INVALID, không rơi về audit."""
    raw = _read_mode_setting()
    mode = MODE_AUDIT if raw is None else raw.lower()
    if mode not in MODES:
        raise _fail(ErrorCode.MODE_INVALID, f"{MODE_ENV} must be 'audit' or 'enforce'")
    if mode_floor is not None:
        if mode_floor not in MODES:
            raise _fail(ErrorCode.MODE_INVALID, "receipt mode floor must be 'audit' or 'enforce'")
        if mode_floor == MODE_ENFORCE:
            return MODE_ENFORCE
    return mode


@dataclass(frozen=True)
class ReceiptVerification:
    """Kết quả verify có provenance; ``journal_fields()`` là phần payload TASK_COMPLETED (L2a)."""

    provenance: str
    mode: str
    task_id: str
    verifier_id: str
    verdict: str
    evidence_ref: str
    attempt_id: str | None = None
    lease_id: str | None = None
    fencing_token: int | None = None
    kid: str | None = None
    jti: str | None = None
    issued_at: float | None = None
    expires_at: float | None = None
    token: str | None = None
    keyring_digest: str | None = None

    def journal_fields(self) -> dict[str, Any]:
        return {
            "receipt_provenance": self.provenance,
            "receipt_mode": self.mode,
            "receipt_kid": self.kid,
            "receipt_jti": self.jti,
            "receipt_token": self.token,
            "receipt_keyring_digest": self.keyring_digest,
        }


def self_signed_receipt_count() -> int:
    """Số receipt HMAC tự ký đã được nhận ở audit trong tiến trình này (metric GĐ1)."""
    with _self_signed_lock:
        return _self_signed_count


def _row_value(lease_row: Any, key: str) -> Any:
    try:
        return lease_row[key]
    except (KeyError, IndexError, TypeError) as exc:
        raise _fail(ErrorCode.LEASE_MISMATCH, f"lease row has no {key}") from exc


def _check_binding(payload: dict[str, Any], task_id: Any, lease_row: Any) -> None:
    """So khớp CHÍNH XÁC (không strip) với lease row lấy từ ``_assert_lease`` trong txn."""
    if _row_value(lease_row, "task_id") != task_id:
        raise _fail(ErrorCode.LEASE_MISMATCH, "lease row belongs to another task")
    if payload["task_id"] != task_id:
        raise _fail(ErrorCode.TASK_MISMATCH, "receipt is bound to another task id")
    if payload["lease_id"] != _row_value(lease_row, "lease_id"):
        raise _fail(ErrorCode.LEASE_MISMATCH, "receipt is bound to another lease")
    if payload["attempt_id"] != _row_value(lease_row, "attempt_id"):
        raise _fail(ErrorCode.ATTEMPT_MISMATCH, "receipt is bound to another attempt")
    try:
        row_fencing = int(_row_value(lease_row, "fencing_token"))
    except (TypeError, ValueError) as exc:
        raise _fail(ErrorCode.LEASE_MISMATCH, "lease row fencing_token is not an integer") from exc
    if payload["fencing_token"] != row_fencing:
        raise _fail(ErrorCode.FENCING_MISMATCH, "receipt is bound to another fencing token")


def _split_carrier(receipt: Any) -> tuple[str | None, Any]:
    if isinstance(receipt, str):
        if receipt.startswith(RECEIPT_TOKEN_PREFIX):
            return receipt, None
        raise _fail(ErrorCode.MALFORMED_TOKEN, "a bare string receipt must be a vr2 token")
    if isinstance(receipt, VerifierReceipt):
        signature = receipt.signature
    elif isinstance(receipt, dict):
        signature = receipt.get("signature")
    else:
        raise _fail(ErrorCode.MALFORMED_TOKEN, "receipt must be a vr2 token, VerifierReceipt or dict")
    if isinstance(signature, str) and signature.startswith(RECEIPT_TOKEN_PREFIX):
        return signature, receipt
    return None, receipt


def _check_carrier(carrier: Any, payload: dict[str, Any]) -> None:
    """Các trường ngoài token phải bằng đúng payload đã ký (kernel journal từ chúng)."""
    if isinstance(carrier, dict):
        if set(carrier) != _DICT_CARRIER_KEYS:
            raise _fail(ErrorCode.CARRIER_MISMATCH, "receipt dict fields do not match a VerifierReceipt")
        values = carrier
    else:
        values = carrier.to_dict()
    for field, claim in _CARRIER_FIELDS:
        value = values[field]
        if field == "issued_at":
            same = _is_number(value) and float(value) == float(payload[claim])
        else:
            same = isinstance(value, str) and value == payload[claim]
        if not same:
            raise _fail(ErrorCode.CARRIER_MISMATCH, f"receipt {field} differs from the signed vr2 payload")


def _accept_self_attested(receipt: Any, task_id: Any) -> ReceiptVerification:
    global _self_signed_count
    values = receipt if isinstance(receipt, dict) else receipt.to_dict()
    if values.get("task_id") != task_id:
        raise _fail(ErrorCode.TASK_MISMATCH, "receipt is bound to another task id (exact match)")
    verify_verifier_receipt(receipt, task_id=task_id)
    with _self_signed_lock:
        _self_signed_count += 1
        count = _self_signed_count
    logger.warning(
        "[receipt_authority] SELF_ATTESTED_HMAC receipt accepted in audit mode for task %r (%s=%d); "
        "enforce mode rejects it", task_id, SELF_SIGNED_METRIC, count,
    )
    return ReceiptVerification(
        provenance=PROVENANCE_SELF_ATTESTED, mode=MODE_AUDIT, task_id=task_id,
        verifier_id=str(values.get("verifier_id", "")).strip(), verdict=str(values.get("verdict", "")).strip(),
        evidence_ref=str(values.get("evidence_ref", "")).strip(), attempt_id=values.get("attempt_id"),
        issued_at=float(values["issued_at"]),
    )


def verify_completion_receipt(
    receipt: Any,
    *,
    task_id: str,
    lease_row: Any,
    mode_floor: str | None = None,
    now: float | None = None,
) -> ReceiptVerification:
    """Cổng receipt cho VERIFYING → COMPLETED; fail-closed bằng ReceiptAuthorityError.

    ``lease_row`` phải là hàng lease đọc bởi ``_assert_lease`` BÊN TRONG transaction commit
    (chống TOCTOU). ``receipt``: token vr2, hoặc VerifierReceipt/dict mang token ở ``signature``.

    - enforce: keyring thiếu/hỏng → từ chối TẤT CẢ; receipt HMAC → HMAC_REJECTED (không hạ cấp).
    - audit (GĐ1): vr2 verify đầy đủ → VERIFIED_KEY_C; HMAC hợp lệ → SELF_ATTESTED_HMAC + WARNING.
    - Mọi thứ tự xưng ``vr2.`` mà sai bị từ chối ở MỌI mode, không bao giờ thử lại bằng HMAC.
    """
    mode = resolve_mode(mode_floor)
    keyring = load_keyring() if mode == MODE_ENFORCE else None
    token, carrier = _split_carrier(receipt)
    if token is None:
        if mode == MODE_ENFORCE:
            raise _fail(
                ErrorCode.HMAC_REJECTED,
                "Key C enforced: self-signed HMAC receipts are prohibited; a vr2 receipt from the verifier daemon is required",
            )
        return _accept_self_attested(receipt, task_id)
    if keyring is None:
        keyring = load_keyring()
    payload = verify_vr2(token, keyring, now=now)
    _check_binding(payload, task_id, lease_row)
    if carrier is not None:
        _check_carrier(carrier, payload)
    return ReceiptVerification(
        provenance=PROVENANCE_KEY_C, mode=mode, task_id=payload["task_id"],
        verifier_id=payload["verifier_id"], verdict=payload["verdict"], evidence_ref=payload["evidence_ref"],
        attempt_id=payload["attempt_id"], lease_id=payload["lease_id"], fencing_token=payload["fencing_token"],
        kid=payload["kid"], jti=payload["jti"], issued_at=float(payload["iat"]), expires_at=float(payload["exp"]),
        token=token, keyring_digest=keyring.digest,
    )


__all__ = [
    "DEFAULT_TTL_SECONDS", "DOMAIN", "ERROR_CODES", "ErrorCode", "KEYRING_ENV", "KID_DOMAIN",
    "KeyEntry", "Keyring", "MAX_CLOCK_SKEW_SECONDS", "MAX_TTL_SECONDS", "MODE_AUDIT", "MODE_ENFORCE",
    "MODE_ENV", "PAYLOAD_FIELDS", "PROVENANCE_KEY_C", "PROVENANCE_SELF_ATTESTED",
    "RECEIPT_DOMAIN_NAME", "RECEIPT_SIGNING_DOMAIN", "RECEIPT_TOKEN_PREFIX", "RECEIPT_TOKEN_TYPE",
    "RECEIPT_V2_PREFIX", "ReceiptAuthorityError", "ReceiptVerification", "SELF_SIGNED_METRIC",
    "VerifierKeyMissingError", "Vr2Token", "b64url_decode", "b64url_encode", "build_error_response",
    "build_receipt_response", "canonical_payload_bytes", "compute_kid", "decode_vr2", "keyring_entry",
    "load_keyring", "mint_vr2", "parse_keyring", "parse_receipt_response", "parse_vr2", "public_key_raw",
    "receipt_carrier", "resolve_mode", "self_signed_receipt_count", "validate_receipt_request",
    "verify_completion_receipt", "verify_vr2",
]
