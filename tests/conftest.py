"""Root pytest configuration and fixtures for SCP test suite.

Ensures required environment variables (such as SCP_CAPABILITY_SECRET)
are safely defaulted for automated test collection while preserving
fail-closed semantics if intentionally unset during explicit tests.
"""

from __future__ import annotations

import os

os.environ['SCP_API_PROFILE'] = 'full'

# Default capability secret for automated test collection and suites (GAP-09).
# Test cases verifying fail-closed behavior when unset will explicitly remove
# or monkeypatch this variable in subprocesses or isolated scopes.
os.environ.setdefault(
    "SCP_CAPABILITY_SECRET",
    "test-capability-secret-for-automated-suites-only-32bytes",
)
# [R5] K_journal is a separate key; the default must differ from the capability one so a
# test can never pass only because the two roles happen to share bytes.
os.environ.setdefault(
    "SCP_JOURNAL_SECRET",
    "test-journal-secret-for-automated-suites-only-32bytes!",
)

def _test_approval_issuer_keypair() -> tuple[str, str]:
    """[R5-B] Deterministic TEST-ONLY Ed25519 issuer keypair (seeded, never a real key)."""
    import base64
    import hashlib

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    private = Ed25519PrivateKey.from_private_bytes(hashlib.sha256(b"scp-test-approval-issuer-seed").digest())
    raw_private = private.private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()
    )
    raw_public = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(raw_private).decode("ascii"), base64.b64encode(raw_public).decode("ascii")


_ISSUER_PRIVATE_B64, _ISSUER_PUBLIC_B64 = _test_approval_issuer_keypair()
os.environ.setdefault("SCP_APPROVAL_ISSUER_PRIVATE_KEY", _ISSUER_PRIVATE_B64)
os.environ.setdefault("SCP_APPROVAL_ISSUER_PUBLIC_KEY", _ISSUER_PUBLIC_B64)
os.environ.setdefault("SCP_JWT_SECRET", "test-jwt-secret-for-automated-suites-only-32bytes-long")
os.environ.setdefault("SCP_ADMIN_KEY", "test-admin-key-for-automated-suites-only-32bytes")

# [EE-G1 / S14] Unit-test determinism against .env import side effects.
# Importing ANY test module that pulls in scp.api_server runs
# scp.security.env_loader.load_selected_env() at collection time, which loads
# the repository .env into os.environ. When that file carries egress keys,
# every test in the session silently ran under the server's egress policy.
# EE-G1 enforcement surfaced this: T03 test_web_browse_requires_token failed
# because .env set SCP_EGRESS_MODE=allowlist for the whole suite. Snapshot the
# parent environment here (before collection imports anything) and restore
# JUST these keys after collection, so test execution starts from the
# caller's environment. Tests that need an explicit mode set their own via
# monkeypatch.setenv — the strict, explicit path (this fix only REMOVES an
# accidental policy source; it never adds one).
_EGRESS_ENV_KEYS = ("SCP_EGRESS_MODE", "SCP_EGRESS_ALLOWLIST", "SCP_PRODUCTION_MODE")
_PARENT_EGRESS_ENV = {k: os.environ.get(k) for k in _EGRESS_ENV_KEYS}


def pytest_collection_finish(session) -> None:
    for key, parent_value in _PARENT_EGRESS_ENV.items():
        current = os.environ.get(key)
        if parent_value is None:
            if current is not None:
                del os.environ[key]
        elif current != parent_value:
            os.environ[key] = parent_value


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_auth_failures_between_tests():
    """Prevent failed-auth and SlowAPI rate-limit counters for 'testclient' from leaking across test modules."""
    try:
        from scp.security import auth as _auth
        _auth._auth_failures.clear()
    except Exception:
        pass
    try:
        from scp.api.rate_limiting import limiter
        limiter.reset()
    except Exception:
        pass
    yield
    try:
        from scp.security import auth as _auth
        _auth._auth_failures.clear()
    except Exception:
        pass
    try:
        from scp.api.rate_limiting import limiter
        limiter.reset()
    except Exception:
        pass




@pytest.fixture
def relevance_confirmed(monkeypatch):
    """[RELEVANCE 2026-10-31] Tiền điều kiện TƯỜNG MINH cho test viết trước cổng relevance.

    Từ commit relevance, nhãn VERIFIED (và lookup fork) cần bằng chứng ĐỘC LẬP rằng đáp án
    đáp đúng câu hỏi (scp/runtime/answer_relevance.py). Môi trường hermetic không có model
    L1 (117MB, ngoài repo) và không có provider LLM thật ⇒ không có bằng chứng ⇒ withheld.
    Test nào dùng fixture này KHẲNG ĐỊNH: "một reader khác lineage ĐÃ xác nhận đáp án đáp
    câu hỏi" — embedding bị tắt để kết quả không phụ thuộc máy có model hay không.
    Không assertion nào của test bị nới; cổng vẫn được chạy thật (reader được gọi).
    Hành vi KHI KHÔNG có bằng chứng được khoá bởi tests/T02_contract/test_answer_relevance_gate.py.
    """
    from scp.runtime import answer_relevance as ar

    calls: list[str] = []

    def _no_embedding(_q, _a):
        return None, "", "", "test: embedding not consulted"

    async def _independent_yes(question, answer, gateway, generator_label="", generator_is_llm=False):
        calls.append(str(question))
        return "YES", "test-independent-reader", ""

    monkeypatch.setattr(ar, "embedding_score", _no_embedding)
    monkeypatch.setattr(ar, "independent_reader", _independent_yes)
    return calls
