"""
SCP - Viet Nam | Self-Correcting Pipeline
Copyright (c) 2026 SCP Vietnam Project. All Rights Reserved.

License: See LICENSE file
Contact: scp-vietnam@example.com

HKDF Key Hierarchy (RFC 5869 compliant HKDF-SHA256).
Enforces subsystem-isolated cryptographic key derivation across 5 core SCP subsystems.
"""
from __future__ import annotations

from enum import Enum
import hashlib
import hmac
from typing import Union


class SubsystemContext(str, Enum):
    TASK_KERNEL = "task_kernel"
    CAPABILITY = "capability_domain"
    LLM_GATEWAY = "llm_gateway"
    KNOWLEDGE_WAREHOUSE = "knowledge_warehouse"
    WORM_AUDIT_LEDGER = "worm_audit_ledger"


class HkdfKeyHierarchy:
    """RFC 5869 compliant Hierarchical Key Derivation Function (HKDF-SHA256).

    Prevents cross-subsystem key compromise by deterministically deriving domain-separated
    cryptographic keys for Task Kernel, Capability Tokens, LLM Gateway, Knowledge Warehouse,
    and WORM Audit Ledger from a single root master secret.
    """

    HASH_LEN = 32  # SHA-256 digest size in bytes

    def __init__(self, master_secret: Union[bytes, str], salt: bytes | None = None) -> None:
        if isinstance(master_secret, str):
            master_secret = master_secret.encode("utf-8")
        if not master_secret:
            raise ValueError("Master secret cannot be empty (Fail-Closed DNA #2)")

        self._salt = salt if (salt is not None and len(salt) > 0) else bytes([0] * self.HASH_LEN)
        self._prk = self.extract(self._salt, master_secret)

    @property
    def prk(self) -> bytes:
        """Pseudorandom Key (PRK) extracted from the root secret."""
        return self._prk

    @staticmethod
    def extract(salt: bytes | None, ikm: bytes) -> bytes:
        """HKDF-Extract(salt, IKM) -> PRK (RFC 5869 Section 2.2)."""
        if salt is None or len(salt) == 0:
            salt = bytes([0] * 32)
        return hmac.new(salt, ikm, hashlib.sha256).digest()

    @staticmethod
    def expand(prk: bytes, info: bytes, length: int = 32) -> bytes:
        """HKDF-Expand(PRK, info, L) -> OKM (RFC 5869 Section 2.3)."""
        hash_len = 32
        if length <= 0:
            raise ValueError("Length must be positive")
        if length > 255 * hash_len:
            raise ValueError("Cannot expand beyond 255 * HashLen (RFC 5869 limit)")

        n = (length + hash_len - 1) // hash_len
        okm = bytearray()
        t = b""
        for i in range(1, n + 1):
            t = hmac.new(prk, t + info + bytes([i]), hashlib.sha256).digest()
            okm.extend(t)
        return bytes(okm[:length])

    def derive_key(
        self,
        context: Union[SubsystemContext, str],
        info_suffix: str = "",
        length: int = 32,
    ) -> bytes:
        """Derive a domain-separated key for the specified subsystem context."""
        ctx_val = context.value if isinstance(context, SubsystemContext) else str(context).strip()
        if not ctx_val:
            raise ValueError("Context cannot be empty")
        info_str = f"scp:{ctx_val}:{info_suffix}" if info_suffix else f"scp:{ctx_val}"
        return self.expand(self._prk, info_str.encode("utf-8"), length=length)

    def derive_task_kernel_key(self, info_suffix: str = "", length: int = 32) -> bytes:
        """Derive key for Task Kernel journal HMAC and state integrity."""
        return self.derive_key(SubsystemContext.TASK_KERNEL, info_suffix=info_suffix, length=length)

    def derive_capability_token_key(self, info_suffix: str = "", length: int = 32) -> bytes:
        """Derive key for Capability Token minting and HMAC caveats."""
        return self.derive_key(SubsystemContext.CAPABILITY, info_suffix=info_suffix, length=length)

    derive_capability_key = derive_capability_token_key

    def derive_llm_gateway_key(self, info_suffix: str = "", length: int = 32) -> bytes:
        """Derive key for LLM Gateway circuit breaker & session signing."""
        return self.derive_key(SubsystemContext.LLM_GATEWAY, info_suffix=info_suffix, length=length)

    def derive_knowledge_key(self, info_suffix: str = "", length: int = 32) -> bytes:
        """Derive key for Knowledge Warehouse receipt signing & verification."""
        return self.derive_key(SubsystemContext.KNOWLEDGE_WAREHOUSE, info_suffix=info_suffix, length=length)

    def derive_audit_ledger_key(self, info_suffix: str = "", length: int = 32) -> bytes:
        """Derive key for WORM Audit Ledger tamper-evident sealing."""
        return self.derive_key(SubsystemContext.WORM_AUDIT_LEDGER, info_suffix=info_suffix, length=length)

    @classmethod
    def derive(
        cls,
        master_secret: Union[bytes, str],
        context: Union[SubsystemContext, str],
        salt: bytes | None = None,
        info_suffix: str = "",
        length: int = 32,
    ) -> bytes:
        """One-shot helper to derive a subsystem key directly from root secret."""
        hierarchy = cls(master_secret=master_secret, salt=salt)
        return hierarchy.derive_key(context=context, info_suffix=info_suffix, length=length)


__all__ = [
    "SubsystemContext",
    "HkdfKeyHierarchy",
]
