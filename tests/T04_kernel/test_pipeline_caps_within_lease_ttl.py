"""[W1-c7 2026-10-02] Invariant pin: TOTAL pipeline caps < ask lease TTL.

Root cause being pinned (runtime audit, same SHA): q05 122.4s / q11 89.4s were
withheld as ``lifecycle_authority_lost`` although the answer had been generated
correctly — the pipeline could legitimately run longer than the lease TTL, so
the watchdog expired the lease under a living worker. Wave 1 bounds every
stage and raises the TTL:

  - generation (LLM chat): hedge race total cap 90s
    (SCP_LLM_HEDGE_MAX_SECONDS / HEDGE_DEFAULT_MAX_SECONDS) OR, on the
    sequential path, the seq total cap 90s
    (SCP_LLM_SEQ_MAX_SECONDS / SEQ_DEFAULT_MAX_SECONDS) — [W1-c5]. The two
    modes are ALTERNATIVES, so the generation budget is the MAX of the two.
  - multi-LLM crosscheck deadline: 15s total
    (SCP_CROSSCHECK_MAX_SECONDS / CROSSCHECK_DEFAULT_MAX_SECONDS) — [W1-c6].
  - /ask fact-check await bound: 10s (_FACTCHECK_AWAIT_TIMEOUT_S) — [W1-c1].
  - ask lease TTL default: 120s (SCP_ASK_LEASE_TTL_SECONDS /
    DEFAULT_ASK_LEASE_TTL_SECONDS) — [W1-c4].

Pinned invariant (must FAIL if anyone later raises a stage cap or lowers the
TTL default):  max(hedge_cap, seq_cap) + crosscheck + factcheck < TTL.

All numbers are imported from the production modules — nothing is redefined
here, so the pin tracks the real defaults.

Known scope limit (honest): the S24 LOOKUP data-API fork runs before the
handler without a Wave-1 deadline knob; it is covered by the data layer's own
fetch timeouts, not by this pin.
"""
from __future__ import annotations

import asyncio

import pytest

# Real production constants — NOT redefined in this test.
from scp.ask_kernel_adapter import (  # noqa: F401  (re-exported knob helpers)
    DEFAULT_ASK_LEASE_TTL_SECONDS,
    ask_lease_ttl_seconds,
)
from scp.llm_gateway.client import (
    HEDGE_DEFAULT_MAX_SECONDS,
    SEQ_DEFAULT_MAX_SECONDS,
    _seq_max_seconds,
)
from scp.runtime.multi_llm_crosscheck import (
    CROSSCHECK_DEFAULT_MAX_SECONDS,
    _crosscheck_max_seconds,
)


def test_total_pipeline_caps_stay_below_lease_ttl_default():
    """The Wave-1 invariant: generation (max of the two alternative mode
    caps) + crosscheck deadline + fact-check bound < lease TTL. 90 + 15 + 10
    = 115 < 120 today. Raising any stage cap or lowering the TTL default must
    fail this pin loudly instead of silently re-opening the
    lifecycle_authority_lost window."""
    generation_cap = max(HEDGE_DEFAULT_MAX_SECONDS, SEQ_DEFAULT_MAX_SECONDS)
    total_caps = generation_cap + CROSSCHECK_DEFAULT_MAX_SECONDS + 10.0  # 10.0 = _FACTCHECK_AWAIT_TIMEOUT_S [W1-c1]

    assert total_caps < DEFAULT_ASK_LEASE_TTL_SECONDS, (
        f"pipeline caps {generation_cap}+{CROSSCHECK_DEFAULT_MAX_SECONDS}+10.0 = "
        f"{total_caps}s must stay below the {DEFAULT_ASK_LEASE_TTL_SECONDS}s lease TTL "
        "(lifecycle_authority_lost window re-opens otherwise)"
    )


def test_factcheck_bound_imported_from_production_is_ten_seconds():
    """The fact-check bound must come from the REAL production module (not a
    copy): scp.api_server_parts._ask_impl._FACTCHECK_AWAIT_TIMEOUT_S == 10.0,
    and the rebind seam (api_server namespace export) exposes the SAME object
    — [W1-c1] contract this pin depends on."""
    from scp import api_server
    from scp.api_server_parts import _ask_impl as ask_part

    assert ask_part._FACTCHECK_AWAIT_TIMEOUT_S == 10.0
    assert (
        api_server._ask_impl.__globals__["_FACTCHECK_AWAIT_TIMEOUT_S"]
        is ask_part._FACTCHECK_AWAIT_TIMEOUT_S
    )


@pytest.mark.parametrize(
    ("env_name", "reader", "default"),
    [
        ("SCP_LLM_SEQ_MAX_SECONDS", _seq_max_seconds, SEQ_DEFAULT_MAX_SECONDS),
        ("SCP_CROSSCHECK_MAX_SECONDS", _crosscheck_max_seconds, CROSSCHECK_DEFAULT_MAX_SECONDS),
    ],
)
def test_stage_cap_knobs_are_live_and_fail_closed(monkeypatch, env_name, reader, default):
    """Each stage cap must be a LIVE knob (env value honored) with fail-closed
    parsing (bad/zero/negative → default) — otherwise the pinned totals would
    be decoration instead of enforced budgets."""
    monkeypatch.delenv(env_name, raising=False)
    assert reader() == default

    monkeypatch.setenv(env_name, "7")
    assert reader() == 7.0

    for bad in ("abc", "", "0", "-3", "nan", "inf"):
        monkeypatch.setenv(env_name, bad)
        assert reader() == default, f"{env_name}={bad!r} must fall back to the default"


def test_ttl_knob_is_live_and_waves_defaults_consistent(monkeypatch):
    """The TTL knob must honor valid env values and fall back to the 120s
    default [W1-c4] — the exact number the cap invariant is pinned against."""
    monkeypatch.delenv("SCP_ASK_LEASE_TTL_SECONDS", raising=False)
    assert ask_lease_ttl_seconds() == DEFAULT_ASK_LEASE_TTL_SECONDS == 120

    monkeypatch.setenv("SCP_ASK_LEASE_TTL_SECONDS", "180")
    assert ask_lease_ttl_seconds() == 180


def test_sequential_cap_honored_end_to_end_against_budget(monkeypatch):
    """Behavioural spot-check of the pinned budget on the real
    _chat_sequential path: a provider slower than the configured total cap is
    cut off (fail-closed) instead of running unbounded — the mechanism the
    invariant above relies on."""
    from scp.llm_gateway.client import LLMGateway

    monkeypatch.setenv("SCP_LLM_SEQ_MAX_SECONDS", "0.5")

    class _SlowProvider:
        PROVIDER_NAME = "slowpin"

        def __init__(self):
            self.enabled = True
            self._breaker = type("_B", (), {"is_open": lambda self: False})()

        async def chat(self, question, context="", system_prompt="", prioritize_free=False):
            await asyncio.sleep(5.0)
            return "late", "slowpin:model"

    async def _scenario():
        gateway = LLMGateway()
        gateway._provider_chain = lambda _task: [_SlowProvider()]  # noqa: SLF001 — test seam used by existing gateway tests
        return await gateway.chat("q", task="chat")

    answer, label = asyncio.run(_scenario())

    assert answer is None and label == "none"
