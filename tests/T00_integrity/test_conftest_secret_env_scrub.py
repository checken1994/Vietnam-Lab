"""[SEC-provider-keys / AUDIT-20260909] Hermetic proof of the root conftest
secret-env scrub fixture (``tests/conftest.py::_scrub_secret_env_keys``).

WHY (causal chain): importing any test module that pulls in ``scp.api_server``
runs ``scp.security.env_loader.load_selected_env()`` at collection time, which
copies the repository ``.env`` — including ~20 REAL provider credentials
(OPENROUTER/GROQ/NVIDIA/CEREBRAS/GEMINI/SAMBANOVA API keys, GITHUB_TOKEN,
NASA_API_KEY) — into ``os.environ``.
``tests/T02_contract/test_flow_02_ask_chat_scp_standard.py`` documents the
consequence: the gateway round-robin silently routed test traffic to the real
openrouter.ai cloud. The T02 flow tests delete ``OPENROUTER_*``/``GROQ_*``/...
slots per-test; the root conftest autouse scrub fixture makes that hygiene
global instead of per-test whack-a-mole.

PROVEN HERE — all hermetic (fake key name, fake value, no network, no .env
read, no real secret ever printed or logged):
  1. A provider-key-pattern variable present in the process environment
     (simulating the .env leak via the same raw ``os.environ[...] = ...``
     mechanism env_loader uses) is HIDDEN from test code: the module fixture
     below observes it BEFORE the scrub window and the test body observes it
     GONE during the test (the root conftest autouse fixture ran at setup).
  2. CHILD processes inherit the already-scrubbed ``os.environ``, so any SDK /
     subprocess spawned inside a test cannot see the key either.
  3. The scrub is pattern-scoped: a non-matching control variable stays
     visible during the same window.
  4. The allowlisted ``SCP_CAPABILITY_SECRET`` (test default set by the root
     conftest; read at call time by ``scp/core/capability_token.py``) survives
     the scrub.
  5. The scrub hides without destroying: the pre-scrub snapshot still holds
     the value, and a delenv/undo cycle over the SAME monkeypatch operation
     the fixture uses restores it (the pytest.MonkeyPatch contract backing
     the fixture's per-test teardown).

NOT directly observed (stated per SCP DNA): the post-teardown os.environ
state between tests — the restore is pytest's monkeypatch undo guarantee and
is empirically re-verified by suites whose per-test credentials keep working
across tests (T02/T03 flow tests, m1/m2 challenger suites).
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

# Fake, clearly non-real provider-style key. The NAME must match the conftest
# scrub pattern ("_KEY"); the VALUE is a fixed dummy, never a real secret.
FAKE_LEAKED_PROVIDER_KEY = "SCP_TEST_FAKE_SDK_KEY"
FAKE_LEAKED_PROVIDER_VALUE = "scp-hermetic-fake-sdk-key-not-a-real-secret"
# Control variable that must NOT match the scrub pattern.
NON_SECRET_CONTROL_KEY = "SCP_TEST_HERMETIC_CONTROL_FLAG"


@pytest.fixture(autouse=True, scope="module")
def _simulate_dotenv_leak():
    """Reproduce the .env collection-time leak inside this module only.

    Uses the same raw ``os.environ`` write mechanism as
    ``scp.security.env_loader.load_selected_env()``. Module-scoped setup runs
    BEFORE the first test's function-scoped scrub, so the snapshot below is a
    genuine pre-scrub observation. Teardown removes both variables so nothing
    leaks into other test modules.
    """
    os.environ[FAKE_LEAKED_PROVIDER_KEY] = FAKE_LEAKED_PROVIDER_VALUE
    os.environ[NON_SECRET_CONTROL_KEY] = "visible-control-value"
    pre_scrub = {
        FAKE_LEAKED_PROVIDER_KEY: os.environ.get(FAKE_LEAKED_PROVIDER_KEY),
        NON_SECRET_CONTROL_KEY: os.environ.get(NON_SECRET_CONTROL_KEY),
    }
    yield pre_scrub
    os.environ.pop(FAKE_LEAKED_PROVIDER_KEY, None)
    os.environ.pop(NON_SECRET_CONTROL_KEY, None)


def test_conftest_scrub_hides_leaked_provider_keys(_simulate_dotenv_leak):
    """Leaked provider-style key is invisible to test code and child
    processes; scrub is pattern-scoped, allowlist-aware and non-destructive."""
    pre_scrub = _simulate_dotenv_leak

    # (1) The leak existed before the scrub window...
    assert pre_scrub[FAKE_LEAKED_PROVIDER_KEY] == FAKE_LEAKED_PROVIDER_VALUE
    # ...and is hidden from test code during the test (root conftest autouse
    # scrub ran at setup, BEFORE this body executes).
    assert FAKE_LEAKED_PROVIDER_KEY not in os.environ

    # (2) Child processes inherit the scrubbed environment.
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            "import os; print('VISIBLE' if os.environ.get("
            f"{FAKE_LEAKED_PROVIDER_KEY!r}) else 'SCRUBBED')",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert probe.stdout.strip() == "SCRUBBED"
    assert FAKE_LEAKED_PROVIDER_VALUE not in probe.stdout

    # (3) Scrub is pattern-scoped — the control variable stays visible.
    assert pre_scrub[NON_SECRET_CONTROL_KEY] == "visible-control-value"
    assert os.environ.get(NON_SECRET_CONTROL_KEY) == "visible-control-value"

    # (4) Allowlisted capability secret survives the scrub (non-empty test
    # default; .env cannot override it because the conftest setdefault runs
    # before any test-module import triggers load_selected_env()).
    assert os.environ.get("SCP_CAPABILITY_SECRET")

    # (5) The scrub mechanism restores instead of destroying: a delenv+undo
    # cycle over the SAME operation the conftest fixture performs returns the
    # value (pytest.MonkeyPatch contract backing the fixture's teardown).
    with pytest.MonkeyPatch.context() as mp:
        os.environ[FAKE_LEAKED_PROVIDER_KEY] = FAKE_LEAKED_PROVIDER_VALUE
        mp.delenv(FAKE_LEAKED_PROVIDER_KEY, raising=False)
        assert FAKE_LEAKED_PROVIDER_KEY not in os.environ
    assert os.environ.get(FAKE_LEAKED_PROVIDER_KEY) == FAKE_LEAKED_PROVIDER_VALUE
