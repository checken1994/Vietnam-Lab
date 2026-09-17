"""Root pytest configuration and fixtures for SCP test suite.

Ensures required environment variables (such as SCP_CAPABILITY_SECRET)
are safely defaulted for automated test collection while preserving
fail-closed semantics if intentionally unset during explicit tests.
"""

from __future__ import annotations

import os

import pytest

os.environ['SCP_API_PROFILE'] = 'full'

# Default capability secret for automated test collection and suites (GAP-09).
# Test cases verifying fail-closed behavior when unset will explicitly remove
# or monkeypatch this variable in subprocesses or isolated scopes.
os.environ.setdefault(
    "SCP_CAPABILITY_SECRET",
    "test-capability-secret-for-automated-suites-only-32bytes",
)

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


# [SEC-provider-keys / AUDIT-20260909] Secret & provider-key hygiene for the
# TEST process. Importing any test module that pulls in scp.api_server runs
# scp.security.env_loader.load_selected_env() at collection time, which loads
# the repository .env (~20 real provider credentials: OPENROUTER_API_KEY_1..10,
# GROQ_API_KEY_1..3, NVIDIA_API_KEY_1..3, CEREBRAS/GEMINI/SAMBANOVA_API_KEY,
# GITHUB_TOKEN, NASA_API_KEY, ...) into os.environ. Before this fixture, every
# test — and every subprocess/SDK a test spawns — could see those keys, so the
# gateway round-robin could silently route test traffic to real paid providers
# (quota burn, machine-dependent results). The T02 flow tests already had to
# delete OPENROUTER_*/GROQ_*/... slots per-test
# (tests/T02_contract/test_flow_02_ask_chat_scp_standard.py
# ::_disable_openrouter and its two copies); this autouse fixture centralises
# that removal for the WHOLE suite.
#
# Mechanism: function-scoped autouse fixture using monkeypatch.delenv — each
# key is hidden for the duration of one test and RESTORED by monkeypatch
# teardown (pytest core guarantee); nothing is permanently deleted. Fixture
# setup order keeps this fail-safe: session/module-scoped fixtures read the
# environment BEFORE this function-scoped fixture runs, and test bodies (plus
# their own monkeypatch.setenv calls) run AFTER it — so explicit per-test
# configuration always wins over the scrub.
#
# Pattern (case-insensitive substring match on the key NAME only — values are
# never read, compared, logged or echoed):
#   API_KEY / APIKEY / TOKEN / SECRET / PASSWORD / PASSWD / _KEY
#
# Allowlist (explicit names, no wildcards) — SCP-local auth/config secrets the
# product reads at CALL time and the suite legitimately needs ambient:
#   SCP_CAPABILITY_SECRET  — test default set above (GAP-09); read at call
#                            time by scp/core/capability_token.py:45 (raises
#                            when missing) and verifier_receipt.py:66.
#   SCP_JWT_SECRET         — REQUIRED_ENV in scp/core/config_contract.py;
#                            read per call by scp/security/auth.py:125 and
#                            jwt_guard.py:16 (validate_boot_config runs in
#                            test bodies and TestClient lifespans).
#   SCP_ADMIN_KEY          — REQUIRED_ENV; read per request by
#                            scp/api_server.py:543; T01 reads os.environ
#                            in-body (test_flow_01_boot...py:410).
#   SCP_AUTH_TOKEN_SECRET  — verify_admin -> load_auth_config() reads these
#   SCP_AUTH_PASSWORD        per request (scp/security/auth.py:135); the m1
#                            challenger suite sets them at module scope
#                            (tests/test_m1_empirical_challenger.py:40-41).
# These are localhost auth-config values, NOT provider/egress credentials —
# every real provider key still matches the pattern and is scrubbed.
_SECRET_PATTERN_SUBSTRINGS = (
    "API_KEY",
    "APIKEY",
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "PASSWD",
    "_KEY",
)
_SECRET_ENV_ALLOWLIST = frozenset(
    {
        "SCP_CAPABILITY_SECRET",
        "SCP_JWT_SECRET",
        "SCP_ADMIN_KEY",
        "SCP_AUTH_TOKEN_SECRET",
        "SCP_AUTH_PASSWORD",
    }
)


def _is_secret_env_key(name: str) -> bool:
    """True when an env var NAME looks like a provider key / secret and is not
    explicitly allowlisted. Matching uses the name only; values are never
    read, compared or logged."""
    upper = name.upper()
    if upper in _SECRET_ENV_ALLOWLIST:
        return False
    return any(pattern in upper for pattern in _SECRET_PATTERN_SUBSTRINGS)


@pytest.fixture(autouse=True)
def _scrub_secret_env_keys(monkeypatch):
    """Hide provider-key/secret env vars from every test (see block above).

    monkeypatch teardown restores each removed key after the test, so the
    scrub is a per-test window, never a permanent deletion. Hermetic proof:
    tests/T00_integrity/test_conftest_secret_env_scrub.py.
    """
    for key in [name for name in list(os.environ) if _is_secret_env_key(name)]:
        monkeypatch.delenv(key, raising=False)
    yield
