"""Verifier service khóa C (1.A): tiến trình OS riêng giữ private key C, ký receipt vr2.

Package tên ``verifier_service`` (không phải ``verifier``) vì repo đã có module ``scp/verifier.py``
— package cùng tên sẽ che module đó. Bố cục theo AG: daemon / verifier_engine / client.
"""
from scp.verifier_service.client import (
    VerifierClientError,
    VerifierConfigError,
    VerifierDaemonClient,
    VerifierRefusedError,
    VerifierUnavailableError,
    assert_worker_holds_no_private_key,
)
from scp.verifier_service.verifier_engine import PROFILE_HANDS_FILE, PROFILE_JOURNAL, REASONS

__all__ = [
    "PROFILE_HANDS_FILE", "PROFILE_JOURNAL", "REASONS", "VerifierClientError", "VerifierConfigError",
    "VerifierDaemonClient", "VerifierRefusedError", "VerifierUnavailableError",
    "assert_worker_holds_no_private_key",
]
