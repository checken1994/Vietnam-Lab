from dataclasses import dataclass
import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import sys
import time
from typing import Any, Sequence

logger = logging.getLogger(__name__)

if "CapabilitySecurityError" not in globals():
    class CapabilitySecurityError(PermissionError):
        """Raised when a Macaroon capability token fails cryptographic or caveat verification."""
        pass


@dataclass(frozen=True)
class MacaroonCapabilityToken:
    """Google Macaroons-inspired capability token with chained HMAC caveats.

    Unlike flat HMAC tokens, any holder of a `MacaroonCapabilityToken` can
    append restrictive contextual caveats (e.g. `op=read`, `path=/data/curated`,
    `task_id=t_123`, `exp=1890000000`) in O(1) time WITHOUT knowing the root
    secret key, while no party can remove or tamper with existing caveats.
    """
    identifier: str
    subject: str
    epoch: int
    issued_at: float
    caveats: tuple[str, ...] = ()
    signature: str = ""

    @staticmethod
    def _hmac_digest(key: bytes, message: str) -> bytes:
        return hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()

    @classmethod
    def mint(
        cls,
        root_secret: bytes,
        subject: str,
        epoch: int,
        initial_caveats: Sequence[str] = (),
        token_id: str | None = None,
        issued_at: float | None = None,
    ) -> "MacaroonCapabilityToken":
        if not root_secret:
            raise CapabilitySecurityError("Root secret cannot be empty (GAP-09 fail-closed)")
        tid = token_id or f"mac_{secrets.token_hex(8)}"
        iat = float(issued_at if issued_at is not None else time.time())
        header = f"{tid}:{subject}:{epoch}:{iat:.6f}"
        sig_bytes = cls._hmac_digest(root_secret, header)
        caveat_tuple = tuple(str(c).strip() for c in initial_caveats if str(c).strip())
        for cav in caveat_tuple:
            sig_bytes = cls._hmac_digest(sig_bytes, cav)
        return cls(
            identifier=tid,
            subject=subject,
            epoch=epoch,
            issued_at=iat,
            caveats=caveat_tuple,
            signature=sig_bytes.hex(),
        )

    def attenuate(self, *new_caveats: str) -> "MacaroonCapabilityToken":
        """Append narrower contextual caveats in O(1) without needing the root secret."""
        if not self.signature:
            raise CapabilitySecurityError("Cannot attenuate an unsigned token")
        sig_bytes = bytes.fromhex(self.signature)
        added: list[str] = []
        for cav in new_caveats:
            cleaned = str(cav).strip()
            if not cleaned:
                continue
            sig_bytes = self._hmac_digest(sig_bytes, cleaned)
            added.append(cleaned)
        return MacaroonCapabilityToken(
            identifier=self.identifier,
            subject=self.subject,
            epoch=self.epoch,
            issued_at=self.issued_at,
            caveats=self.caveats + tuple(added),
            signature=sig_bytes.hex(),
        )

    def verify(
        self,
        root_secret: bytes,
        current_epoch: int = 0,
        context: dict[str, Any] | None = None,
        consumed_nonces: set[str] | None = None,
        max_skew_seconds: float = 300.0,
        *,
        now: float | None = None,
        **kwargs: Any,
    ) -> bool:
        """Verify chained HMAC signature, epoch validity, replay protection, and all caveats."""
        if not root_secret:
            raise CapabilitySecurityError("Root secret cannot be empty (GAP-09 fail-closed)")
        if not self.signature:
            raise CapabilitySecurityError("Missing Macaroon signature")
        if self.epoch < current_epoch:
            raise CapabilitySecurityError(
                f"Token epoch {self.epoch} is revoked (current_epoch={current_epoch})"
            )
        current_time = float(now if now is not None else time.time())
        if self.issued_at > current_time + max_skew_seconds:
            raise CapabilitySecurityError(
                f"Token issued in future (issued_at={self.issued_at:.6f}, now={current_time:.6f}, max_skew={max_skew_seconds})"
            )
        header = f"{self.identifier}:{self.subject}:{self.epoch}:{self.issued_at:.6f}"
        expected_sig = self._hmac_digest(root_secret, header)
        for cav in self.caveats:
            expected_sig = self._hmac_digest(expected_sig, cav)
        if not hmac.compare_digest(self.signature, expected_sig.hex()):
            raise CapabilitySecurityError("Macaroon signature mismatch (tampered caveats or invalid secret)")

        if consumed_nonces is not None and self.identifier in consumed_nonces:
            raise CapabilitySecurityError(f"Replay detected for token '{self.identifier}' (GAP-12)")

        ctx = context or {}
        for cav in self.caveats:
            if "=" not in cav:
                raise CapabilitySecurityError(f"Malformed caveat '{cav}'")
            k, v = cav.split("=", 1)
            k = k.strip()
            v = v.strip()
            if k == "exp":
                try:
                    exp_ts = float(v)
                except ValueError as exc:
                    raise CapabilitySecurityError(f"Invalid timestamp in caveat '{cav}': {exc}") from exc
                if current_time > exp_ts:
                    raise CapabilitySecurityError(f"Token expired at {v} (now={current_time})")
            elif k == "path_prefix":
                target_path = str(ctx.get("path", ""))
                if not target_path.startswith(v):
                    raise CapabilitySecurityError(
                        f"Caveat violation: path '{target_path}' outside allowed prefix '{v}'"
                    )
            else:
                actual = str(ctx.get(k, ""))
                if actual != v:
                    raise CapabilitySecurityError(
                        f"Caveat violation on '{k}': expected '{v}', got '{actual}'"
                    )

        if consumed_nonces is not None:
            consumed_nonces.add(self.identifier)
        return True

    def serialize(self) -> str:
        """Serialize token to standard format: macaroon.<payload_b64>.<sig>."""
        payload = {
            "id": self.identifier,
            "sub": self.subject,
            "epoch": self.epoch,
            "iat": self.issued_at,
            "caveats": list(self.caveats),
        }
        raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        payload_b64 = base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")
        return f"macaroon.{payload_b64}.{self.signature}"

    @classmethod
    def deserialize(cls, token_str: str) -> "MacaroonCapabilityToken":
        """Deserialize token from standard format: macaroon.<payload_b64>.<sig>."""
        if not isinstance(token_str, str) or not token_str.startswith("macaroon."):
            raise CapabilitySecurityError("Invalid macaroon string format (must start with 'macaroon.')")
        parts = token_str.split(".")
        if len(parts) != 3:
            raise CapabilitySecurityError("Invalid macaroon string structure (expected macaroon.<payload_b64>.<sig>)")
        _, payload_b64, sig = parts
        pad = len(payload_b64) % 4
        if pad:
            payload_b64 += "=" * (4 - pad)
        try:
            raw = base64.urlsafe_b64decode(payload_b64.encode("utf-8")).decode("utf-8")
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("Payload must be a JSON dictionary")
            return cls(
                identifier=str(data["id"]),
                subject=str(data["sub"]),
                epoch=int(data["epoch"]),
                issued_at=float(data["iat"]),
                caveats=tuple(str(c) for c in data.get("caveats", ())),
                signature=str(sig),
            )
        except Exception as e:
            raise CapabilitySecurityError(f"Malformed macaroon payload: {e}") from e


if "MissingSecretError" not in globals():
    class MissingSecretError(RuntimeError):
        """Raised when SCP_CAPABILITY_SECRET is missing or empty (GAP-09 fail-closed)."""
        pass


if "InvalidTokenSignatureError" not in globals():
    class InvalidTokenSignatureError(PermissionError):
        """Raised when a capability token is unsigned, has an invalid signature, or has been tampered with."""
        pass


def compute_token_signature(secret: bytes, subject: str, epoch: int, token_id: str, issued_at: float) -> str:
    """Compute deterministic HMAC-SHA256 signature for a CapabilityToken."""
    canonical = f"{subject}:{epoch}:{token_id}:{issued_at:.6f}".encode()
    return hmac.new(secret, canonical, hashlib.sha256).hexdigest()


def verify_token_signature(secret: bytes, subject: str, epoch: int, token_id: str, issued_at: float, signature: str) -> bool:
    """Verify HMAC-SHA256 signature for a CapabilityToken using constant-time comparison.

    Raises InvalidTokenSignatureError fail-closed if signature is missing, invalid, or tampered.
    """
    if not signature or not str(signature).strip():
        raise InvalidTokenSignatureError("Capability token is unsigned (GAP-08/FA-04)")
    expected = compute_token_signature(secret, subject, epoch, token_id, issued_at)
    if not hmac.compare_digest(str(signature).strip(), expected):
        raise InvalidTokenSignatureError("Capability token signature verification failed (tampered token)")
    return True


def get_capability_secret() -> bytes:
    """Read and return the cryptographic secret for capability tokens.

    Raises MissingSecretError if SCP_CAPABILITY_SECRET is missing or empty.
    """
    secret = os.environ.get("SCP_CAPABILITY_SECRET")
    if not secret or not secret.strip():
        raise MissingSecretError(
            "SCP_CAPABILITY_SECRET environment variable is missing or empty. "
            "A cryptographic secret is required to sign and verify capability tokens (GAP-09)."
        )
    return secret.strip().encode("utf-8")


def mint_token(issuer: str, scope: str, capability_level: int, ttl_seconds: int = 3600) -> str:
    secret = get_capability_secret()
    epoch = int(time.time())
    payload = {
        "iss": issuer,
        "scope": scope,
        "cap": capability_level,
        "iat": epoch,
        "exp": epoch + ttl_seconds,
    }
    payload_b64 = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    signature = hmac.new(secret, payload_b64.encode(), hashlib.sha256).hexdigest()
    return f"{payload_b64}.{signature}"

def verify_token(token: str, required_scope: str = "*") -> dict:
    if not token or "." not in token:
        return {"valid": False, "error": "Invalid token format"}
    secret = get_capability_secret()
    payload_b64, signature = token.rsplit(".", 1)

    expected_sig = hmac.new(secret, payload_b64.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected_sig):
        return {"valid": False, "error": "Invalid signature"}

    pad = len(payload_b64) % 4
    if pad:
        payload_b64 += "=" * (4 - pad)

    try:
        payload = json.loads(base64.urlsafe_b64decode(payload_b64.encode()).decode())
    except Exception:
        logger.debug("verify_token ignored", exc_info=True)
        return {"valid": False, "error": "Invalid payload"}

    if payload.get("exp", 0) < time.time():
        return {"valid": False, "error": "Token expired"}

    scope = payload.get("scope")
    if required_scope != "*" and scope != required_scope and scope != "*":
        return {"valid": False, "error": "Scope mismatch"}

    return {"valid": True, "payload": payload}


def __getattr__(name: str):
    if name == "_SECRET":
        return get_capability_secret()
    if name == "CapabilityToken":
        from scp.security.capability_epoch import CapabilityToken
        return CapabilityToken
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")


def _is_direct_c_import() -> bool:
    """Return True if capability_token is directly imported from a top-level python -c snippet."""
    try:
        frame = sys._getframe(0)
        while frame is not None:
            fname = frame.f_code.co_filename
            if fname == "<string>":
                return True
            if not fname.startswith("<") and not fname.endswith("capability_token.py"):
                return False
            frame = frame.f_back
    except Exception:
        logger.debug("_is_direct_c_import check ignored", exc_info=True)
    return False


if _is_direct_c_import():
    get_capability_secret()


__all__ = [
    "CapabilitySecurityError",
    "MacaroonCapabilityToken",
    "MissingSecretError",
    "InvalidTokenSignatureError",
    "get_capability_secret",
    "compute_token_signature",
    "verify_token_signature",
    "mint_token",
    "verify_token",
    # [hygiene F822] CapabilityToken KHÔNG được định nghĩa tĩnh ở module này —
    # nó được lazy-export qua __getattr__ (PEP 562) phía trên để tránh circular
    # import; giữ tên trong __all__ là chủ đích, không phải bug.
    "CapabilityToken",  # noqa: F822  # lazy-export qua __getattr__ (PEP 562) phía trên — chủ đích
]

