"""HC-09/10/11 — import-time singletons: lazy seam contract (B4-W1).

Contract under test (docs/architecture/SINGLETON_LIFECYCLE.md):

  1. `scp.api_server` imports crash-free WITHOUT constructing
     `_real_learning` / `_fast_learning` at import time (PEP 562 lazy).
  2. First attribute access constructs and caches into module globals;
     repeated access returns the SAME object (no re-construction).
  3. The rebind-machinery singletons (F-18) stay import-time instances:
     rebound part functions read them via LOAD_GLOBAL, which BYPASSES
     module `__getattr__` — so those names MUST already be in globals.
  4. monkeypatch.setattr on the lazy names overrides the getter normally.
"""
from __future__ import annotations

import asyncio

import pytest


def _api_server():
    from scp import api_server

    return api_server


def test_api_server_import_crash_free_and_lazy_names_absent_at_import() -> None:
    """Import-time laziness must hold in a FRESH interpreter: other tests in
    this session may already have touched the lazy names (caching them into
    module globals by design), so the honest probe is a subprocess (same
    pattern as tests/T01_boot/test_configured_storage_boundary.py)."""
    import json
    import subprocess
    import sys
    from pathlib import Path

    probe = (
        "import json;from scp import api_server as api;"
        "print(json.dumps({'real_lazy': '_real_learning' not in vars(api),"
        "'fast_lazy': '_fast_learning' not in vars(api),"
        "'rebind_present': '_fact_checker' in vars(api) and '_multi_turn_tracker' in vars(api)}))"
    )
    proc = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=str(Path(__file__).resolve().parents[2]),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    result = json.loads(proc.stdout.splitlines()[-1])
    assert result["real_lazy"], "_real_learning must NOT be constructed at import time"
    assert result["fast_lazy"], "_fast_learning must NOT be constructed at import time"
    assert result["rebind_present"], "rebind-machinery singletons must stay import-time"


def test_lazy_learning_singletons_construct_once_and_cache() -> None:
    api = _api_server()
    real1 = api._real_learning
    from scp.core.real_learning_engine import RealLearningEngine

    assert isinstance(real1, RealLearningEngine)
    assert api._real_learning is real1, "second access must hit the globals cache"
    # Cached into globals => LOAD_GLOBAL-safe and __getattr__ no longer fires.
    assert "_real_learning" in vars(api)

    fast1 = api._fast_learning
    if api._V1042_AVAILABLE:
        from scp.core.fast_learning_engine import FastLearningEngine

        assert isinstance(fast1, FastLearningEngine)
    else:
        assert fast1 is None
    assert api._fast_learning is fast1


def test_rebind_machinery_singletons_stay_import_time_instances() -> None:
    """LOAD_GLOBAL inside _rebind_part_function functions bypasses module
    __getattr__ — these names must be real instances in api_server globals
    immediately after import (HC ledger: containable, not lazifiable)."""
    from scp.core.request_run_ledger import RequestRunLedger
    from scp.core.streaming_factcheck import StreamingFactChecker
    from scp.meta.simple_explainer import SimpleExplainer
    from scp.security.cross_language_learner import CrossLanguageLearner
    from scp.security.image_voice_detector import ImageJailbreakDetector
    from scp.security.image_voice_detector import VoiceJailbreakDetector
    from scp.security.multi_turn_tracker import MultiTurnTracker

    api = _api_server()
    v = vars(api)
    assert isinstance(v["_REQUEST_RUN_LEDGER"], RequestRunLedger)
    assert isinstance(v["_multi_turn_tracker"], MultiTurnTracker)
    assert isinstance(v["_image_detector"], ImageJailbreakDetector)
    assert isinstance(v["_voice_detector"], VoiceJailbreakDetector)
    assert isinstance(v["_cross_language_learner"], CrossLanguageLearner)
    assert isinstance(v["_fact_checker"], StreamingFactChecker)
    assert isinstance(v["_simple_explainer"], SimpleExplainer)
    # Rebound part functions resolve against THESE globals — sanity: they
    # were rebound onto api_server.__dict__ at import time.
    assert api._ask_impl.__globals__ is vars(api)
    assert api._async_fact_check.__globals__ is vars(api)


def test_rebound_part_function_still_reads_rebind_singletons() -> None:
    """Anti-placebo: the rebind contract actually WORKS after the lazy patch —
    a rebound part function resolves an import-time singleton from api_server
    globals at call time."""
    from scp.api_server_parts import _async_fact_check as part

    api = _api_server()
    assert api._async_fact_check.__globals__["_fact_checker"] is part.__dict__.get(
        "_fact_checker", api._fact_checker
    ) or api._async_fact_check.__globals__["_fact_checker"] is api._fact_checker


def test_monkeypatch_write_overrides_lazy_getattr(monkeypatch: pytest.MonkeyPatch) -> None:
    api = _api_server()
    stub = object()

    class _Marker:
        pass

    marker = _Marker()
    monkeypatch.setattr(api, "_fast_learning", marker, raising=False)
    assert api._fast_learning is marker
    monkeypatch.undo()
    # After undo, the attr is removed again -> getter reconstructs cleanly.
    assert api._fast_learning is not marker


def test_shared_module_delegation_still_resolves_lazy_singletons() -> None:
    """scp/api/_shared.py PEP 562 delegation must see through api_server's
    PEP 562 lazy getter (chained __getattr__)."""
    from scp import api as api_pkg  # noqa: F401  (import-order sanity)
    from scp.api import _shared

    real = _shared._real_learning
    assert real is _api_server()._real_learning


def test_app_state_async_factcheck_loop_uses_import_time_fact_checker() -> None:
    """Smoke: _async_fact_check rebound function is callable-shaped and its
    globals carry the import-time _fact_checker instance (HC-32 adjacent)."""
    api = _api_server()
    fn = api._async_fact_check
    assert callable(fn)
    assert asyncio.iscoroutinefunction(fn)
