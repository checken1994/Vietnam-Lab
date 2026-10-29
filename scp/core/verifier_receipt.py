"""Cryptographic Verifier Receipt infrastructure for TaskKernel verification provenance (R3/GAP-14)."""
from __future__ import annotations

import dataclasses
import hashlib
import hmac
import math
import os
import time
from dataclasses import dataclass
from typing import Any

from scp.task_kernel_parts.definitions import KernelError


class MissingSecretError(RuntimeError):
    """Raised when neither SCP_VERIFIER_SECRET nor SCP_CAPABILITY_SECRET is configured."""
    pass


class InvalidReceiptSignatureError(KernelError, PermissionError):
    """Raised when a verifier receipt signature is missing, forged, tampered, or invalid."""
    pass


@dataclass(frozen=True)
class VerifierReceipt:
    task_id: str
    verifier_id: str
    verdict: str
    evidence_ref: str
    issued_at: float
    signature: str = ""
    attempt_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "verifier_id": self.verifier_id,
            "verdict": self.verdict,
            "evidence_ref": self.evidence_ref,
            "issued_at": self.issued_at,
            "signature": self.signature,
            "attempt_id": self.attempt_id,
        }


def get_verifier_secret(secret: str | bytes | None = None) -> bytes:
    """Resolve and return cryptographic secret bytes for verifier receipts.

    Priority:
    1. Explicit secret argument (if provided and non-empty)
    2. SCP_VERIFIER_SECRET environment variable
    3. SCP_CAPABILITY_SECRET environment variable
    Raises MissingSecretError if none is set or if empty.
    """
    if secret is not None:
        if isinstance(secret, bytes):
            if not secret:
                raise MissingSecretError("Provided secret bytes must not be empty.")
            return secret
        val = str(secret).strip()
        if not val:
            raise MissingSecretError("Provided secret string must not be empty.")
        return val.encode()

    env_secret = os.environ.get("SCP_VERIFIER_SECRET") or os.environ.get("SCP_CAPABILITY_SECRET")
    if not env_secret or not env_secret.strip():
        raise MissingSecretError(
            "SCP_VERIFIER_SECRET or SCP_CAPABILITY_SECRET environment variable is missing or empty. "
            "A cryptographic secret is required to sign and verify receipts (R3/GAP-14)."
        )
    return env_secret.strip().encode()


def _bound_task_id(raw: Any) -> str:
    """Return the task id a receipt binds, EXACTLY as given (B2) — never normalised.

    Kernel task ids are compared byte-exact, so trimming here would let one receipt for
    "t" also complete the distinct tasks "t ", " t", "t\\t". Padded ids are refused rather
    than trimmed (same rule as R5 approval tokens); characters ``str.strip`` keeps, such as
    zero-width ones, simply make a different id.
    """
    task_id = str(raw)
    if not task_id.strip():
        raise InvalidReceiptSignatureError("receipt task_id is required")
    if task_id != task_id.strip():
        raise InvalidReceiptSignatureError(
            f"receipt task_id {task_id!r} must not carry leading/trailing whitespace (B2 exact task binding)"
        )
    return task_id


def _escape_field(value: str) -> str:
    """FA-11: escape the delimiter in task_id / verifier_id / verdict ('\\' first, then ':').

    A field holding neither character keeps its exact v1 bytes, so already-issued receipts of
    that shape still verify; evidence_ref stays free text (the only middle field that may hold ':').
    """
    return value.replace("\\", "\\\\").replace(":", "\\:")


def canonical_receipt_bytes(receipt: VerifierReceipt | dict[str, Any]) -> bytes:
    """Construct deterministic canonical bytes for signing and verifying a receipt.

    Format: esc(task_id):esc(verifier_id):esc(verdict):evidence_ref:issued_at:.6f
    task_id is bound exactly (B2): a padded id raises instead of being trimmed. FA-11: esc()
    leaves no bare ':' in the first three fields and '.6f' never renders one, so evidence_ref is
    bounded by the third bare ':' and the last ':' -- one byte string names one field split.
    Bytes are unchanged for receipts whose first three fields hold neither ':' nor '\\'.
    """
    if isinstance(receipt, dict):
        task_id = _bound_task_id(receipt.get("task_id", ""))
        verifier_id = str(receipt.get("verifier_id", "")).strip()
        verdict = str(receipt.get("verdict", "")).strip()
        evidence_ref = str(receipt.get("evidence_ref", "")).strip()
        try:
            issued_at = float(receipt.get("issued_at", 0.0))
        except (TypeError, ValueError):
            raise InvalidReceiptSignatureError("Invalid issued_at timestamp in receipt") from None
    elif isinstance(receipt, VerifierReceipt):
        task_id = _bound_task_id(receipt.task_id)
        verifier_id = str(receipt.verifier_id).strip()
        verdict = str(receipt.verdict).strip()
        evidence_ref = str(receipt.evidence_ref).strip()
        try:
            issued_at = float(receipt.issued_at)
        except (TypeError, ValueError):
            raise InvalidReceiptSignatureError("Invalid issued_at timestamp in receipt") from None
    else:
        raise InvalidReceiptSignatureError("receipt must be a VerifierReceipt or dict")

    if not verifier_id:
        raise InvalidReceiptSignatureError("receipt verifier_id is required")
    if not verdict:
        raise InvalidReceiptSignatureError("receipt verdict is required")
    if not evidence_ref:
        raise InvalidReceiptSignatureError("receipt evidence_ref is required")
    if not math.isfinite(issued_at):
        # FA-11: NaN compares False with every freshness bound, so a NaN receipt never expired.
        raise InvalidReceiptSignatureError("receipt issued_at must be a finite timestamp")

    return (
        f"{_escape_field(task_id)}:{_escape_field(verifier_id)}:{_escape_field(verdict)}:"
        f"{evidence_ref}:{issued_at:.6f}"
    ).encode()


def sign_verifier_receipt(
    receipt: VerifierReceipt | dict[str, Any],
    secret: str | bytes | None = None,
) -> VerifierReceipt:
    """Compute HMAC-SHA256 signature for VerifierReceipt and return signed VerifierReceipt."""
    sec = get_verifier_secret(secret)
    if isinstance(receipt, dict):
        task_id = str(receipt.get("task_id", ""))  # exact (B2): canonical_receipt_bytes refuses padding
        verifier_id = str(receipt.get("verifier_id", "")).strip()
        verdict = str(receipt.get("verdict", "")).strip()
        evidence_ref = str(receipt.get("evidence_ref", "")).strip()
        issued_at = float(receipt.get("issued_at", 0.0)) if receipt.get("issued_at") else time.time()
        attempt_id = receipt.get("attempt_id")
        receipt_obj = VerifierReceipt(
            task_id=task_id,
            verifier_id=verifier_id,
            verdict=verdict,
            evidence_ref=evidence_ref,
            issued_at=issued_at,
            attempt_id=attempt_id,
        )
    elif isinstance(receipt, VerifierReceipt):
        issued_at = receipt.issued_at if receipt.issued_at else time.time()
        if issued_at != receipt.issued_at:
            receipt_obj = dataclasses.replace(receipt, issued_at=issued_at)
        else:
            receipt_obj = receipt
    else:
        raise InvalidReceiptSignatureError("receipt must be a VerifierReceipt or dict")

    canonical = canonical_receipt_bytes(receipt_obj)
    signature = hmac.new(sec, canonical, hashlib.sha256).hexdigest()
    return dataclasses.replace(receipt_obj, signature=signature)


def verify_verifier_receipt(
    receipt: VerifierReceipt | dict[str, Any],
    secret: str | bytes | None = None,
    *,
    task_id: str | None = None,
    max_skew_seconds: float = 300.0,
) -> bool:
    """Verify HMAC-SHA256 signature of a receipt using constant-time comparison.

    Raises InvalidReceiptSignatureError fail-closed if:
    - receipt is missing, empty, or invalid type
    - task_id is mismatched (if task_id provided)
    - verifier_id or evidence_ref is missing
    - verdict != 'VERIFIED'
    - signature is missing or empty
    - timestamp is invalid, in future (>60s), or expired (>max_skew_seconds)
    - signature does not match computed HMAC
    Returns True on valid receipt.
    """
    if receipt is None:
        raise InvalidReceiptSignatureError("Verifier receipt is missing or empty (R3/FA-04)")

    if isinstance(receipt, dict):
        rcpt_task_id = str(receipt.get("task_id", ""))  # exact (B2): never normalised before binding
        verifier_id = str(receipt.get("verifier_id", "")).strip()
        verdict = str(receipt.get("verdict", "")).strip()
        evidence_ref = str(receipt.get("evidence_ref", "")).strip()
        signature = str(receipt.get("signature", "")).strip()
        ts_raw = receipt.get("issued_at")
    elif isinstance(receipt, VerifierReceipt):
        rcpt_task_id = str(receipt.task_id)
        verifier_id = str(receipt.verifier_id).strip()
        verdict = str(receipt.verdict).strip()
        evidence_ref = str(receipt.evidence_ref).strip()
        signature = str(receipt.signature).strip()
        ts_raw = receipt.issued_at
    else:
        raise InvalidReceiptSignatureError("Verifier receipt must be a VerifierReceipt or dict")

    if not rcpt_task_id.strip():
        raise InvalidReceiptSignatureError("Verifier receipt missing task_id")
    rcpt_task_id = _bound_task_id(rcpt_task_id)
    # B2: exact comparison — a receipt for "x" must not complete the distinct task "x ".
    if task_id is not None and str(task_id) != rcpt_task_id:
        raise InvalidReceiptSignatureError(
            f"Receipt task_id {rcpt_task_id!r} does not match expected task_id {str(task_id)!r}"
        )
    if not verifier_id:
        raise InvalidReceiptSignatureError("Verifier receipt missing verifier_id")
    if verdict != "VERIFIED":
        raise InvalidReceiptSignatureError(
            f"Completion requires verifier verdict 'VERIFIED', got '{verdict}'"
        )
    if not evidence_ref:
        raise InvalidReceiptSignatureError("Verifier receipt missing evidence_ref")
    if not signature:
        raise InvalidReceiptSignatureError("Verifier receipt is unsigned (R3/FA-04)")

    try:
        issued_at = float(ts_raw)
    except (TypeError, ValueError):
        raise InvalidReceiptSignatureError("Invalid timestamp in verifier receipt") from None

    now = time.time()
    if issued_at > now + 60.0:
        raise InvalidReceiptSignatureError("Verifier receipt issued_at is in the future")
    if max_skew_seconds > 0 and (now - issued_at) > max_skew_seconds:
        raise InvalidReceiptSignatureError("Verifier receipt has expired")

    canonical = canonical_receipt_bytes(receipt)
    sec = get_verifier_secret(secret)
    expected_sig = hmac.new(sec, canonical, hashlib.sha256).hexdigest()

    if not hmac.compare_digest(signature, expected_sig):
        raise InvalidReceiptSignatureError(
            "Verifier receipt signature verification failed (tampered receipt)"
        )

    return True


__all__ = [
    "VerifierReceipt",
    "MissingSecretError",
    "InvalidReceiptSignatureError",
    "get_verifier_secret",
    "canonical_receipt_bytes",
    "sign_verifier_receipt",
    "verify_verifier_receipt",
    "sign_verifier_receipt_ed25519",
    "verify_verifier_receipt_ed25519",
]


def sign_verifier_receipt_ed25519(
    receipt: VerifierReceipt | dict[str, Any],
    private_key_b64: str,
) -> tuple[VerifierReceipt, str]:
    """Sign a VerifierReceipt using asymmetric Ed25519 (TCVN 11930:2017 Level 4).

    Returns (signed_receipt, public_key_b64) so downstream verification nodes only
    need the public key and cannot forge new receipts.
    """
    import base64
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    if isinstance(receipt, dict):
        base = VerifierReceipt(
            task_id=str(receipt.get("task_id", "")),  # exact (B2)
            verifier_id=str(receipt.get("verifier_id", "")).strip(),
            verdict=str(receipt.get("verdict", "")).strip(),
            evidence_ref=str(receipt.get("evidence_ref", "")).strip(),
            issued_at=float(receipt.get("issued_at", time.time())),
            attempt_id=receipt.get("attempt_id"),
        )
    else:
        base = receipt

    canonical = canonical_receipt_bytes(base)
    priv = Ed25519PrivateKey.from_private_bytes(base64.b64decode(private_key_b64))
    pub_bytes = priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    pub_b64 = base64.b64encode(pub_bytes).decode("ascii")
    sig_b64 = base64.b64encode(priv.sign(canonical)).decode("ascii")
    signed = dataclasses.replace(base, signature=f"ed25519:{sig_b64}")
    return signed, pub_b64


def verify_verifier_receipt_ed25519(
    receipt: VerifierReceipt | dict[str, Any],
    public_key_b64: str,
    *,
    max_age_seconds: float = 3600.0,
) -> bool:
    """Verify an Ed25519-signed VerifierReceipt using only the public key."""
    import base64
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    if isinstance(receipt, dict):
        sig_field = str(receipt.get("signature", ""))
        issued_at = float(receipt.get("issued_at", 0.0))
        verdict = str(receipt.get("verdict", "")).strip()
    else:
        sig_field = str(receipt.signature)
        issued_at = float(receipt.issued_at)
        verdict = str(receipt.verdict).strip()

    if verdict != "VERIFIED":
        raise InvalidReceiptSignatureError(f"Receipt verdict is {verdict!r}, expected 'VERIFIED'")
    if not sig_field.startswith("ed25519:"):
        raise InvalidReceiptSignatureError("Receipt signature does not carry 'ed25519:' prefix")
    if max_age_seconds > 0 and (time.time() - issued_at) > max_age_seconds:
        raise InvalidReceiptSignatureError("Ed25519 verifier receipt has expired")

    sig_b64 = sig_field.split("ed25519:", 1)[1]
    canonical = canonical_receipt_bytes(receipt)
    pub = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key_b64))
    try:
        pub.verify(base64.b64decode(sig_b64), canonical)
    except InvalidSignature as exc:
        raise InvalidReceiptSignatureError("Invalid Ed25519 signature on VerifierReceipt") from exc
    return True

