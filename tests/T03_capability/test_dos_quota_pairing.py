"""Regression tests for the DoS resource-quota slot pairing invariant.

[AUDIT-FIX 2026-09-24] External audit probe PROVED two-sided counter
corruption:

(a) /ask requests that exited early (HTTP 400/403 or exception) after
    check_request incremented the resource quota never reached
    record_verdict — each leak permanently consumed one of the 100 global
    slots until the WHOLE process 429'd for every IP.

(b) scp/api/routes/openai_compat.py called record_verdict WITHOUT ever
    calling check_request — chat-completions traffic decremented OTHER
    requests' slots, forging concurrency headroom past MAX_CONCURRENT.

Fail-closed contract pinned here:
  - check_request / (record_verdict | release_slot) must pair 1:1;
  - every early exit releases the slot taken;
  - chat-completions traffic cannot free /ask slots.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request as StarletteRequest

import scp.api_server as _api_server_mod
import scp.api.routes.openai_compat as openai_compat
from scp.api_server_parts import helpers as _scp_helpers
from scp.api_server_parts.helpers import AskRequest
from scp.security.dos_protection import DoSProtectionEngine
from scp.security.jwt_guard import get_current_user

logger = logging.getLogger("test_dos_quota_pairing")

# [TEST-ISOLATION] Background threads must never start from a unit test.
_scp_helpers.start_crawl_thread = lambda *a, **k: logger.info("[TEST-ISOLATION] crawl suppressed")


class _StubJudge:
    def __init__(self, engine: DoSProtectionEngine, result=None, raise_error: Exception | None = None):
        self.dos_protection = engine
        self.response_monitor = None
        self._result = result
        self._raise = raise_error

    async def judge_with_react_fallback(self, **kwargs):
        if self._raise:
            raise self._raise
        return self._result

    def judge(self, **kwargs):
        if self._raise:
            raise self._raise
        return self._result


PASS_RESULT = SimpleNamespace(
    verdict="PASS", confidence=0.9, reasoning="fixture", final_answer="ok",
    domain="general", evidence={}, slm_responses=[],
)


def _mount_judge(monkeypatch, judge):
    monkeypatch.setattr(_api_server_mod, "get_judge", lambda: judge)
    monkeypatch.setattr(_scp_helpers, "get_judge", lambda: judge)


def _make_request() -> StarletteRequest:
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/ask",
        "headers": [],
        "query_string": b"",
        "client": ("127.0.0.1", 54321),
        "server": ("testserver", 80),
        "scheme": "http",
    }
    return StarletteRequest(scope)


# ---------------------------------------------------------------------------
# Engine-level contract
# ---------------------------------------------------------------------------

def test_release_slot_returns_exactly_one_slot():
    engine = DoSProtectionEngine()
    assert engine.check_request("198.51.100.1") is None
    assert engine.stats()["current_concurrent"] == 1
    engine.release_slot()
    assert engine.stats()["current_concurrent"] == 0


def test_release_slot_does_not_touch_circuit_breaker():
    engine = DoSProtectionEngine()
    engine.check_request("198.51.100.1")
    engine.release_slot()
    stats = engine.stats()
    assert stats["consecutive_unknown"] == 0
    assert stats["circuit_state"] == "closed"


def test_leaked_slots_used_to_exhaust_the_process():
    """Proof of the leak mechanics: 100 early exits must NOT 429 the process
    when slots are released (this is the invariant the finally-guard fixes).
    Distinct IPs per cycle so the per-IP minute rate limit never fires."""
    engine = DoSProtectionEngine()
    for i in range(engine.MAX_CONCURRENT):
        assert engine.check_request(f"203.0.113.{i + 1}") is None
        engine.release_slot()  # pre-fix: this line did not exist at call sites
    # The next request must still be admitted.
    assert engine.check_request("203.0.113.200") is None
    engine.release_slot()


# ---------------------------------------------------------------------------
# /ask call site (_ask_impl) — early exit must release the slot
# ---------------------------------------------------------------------------

def test_ask_early_exit_releases_resource_slot(monkeypatch, tmp_path: Path):
    """Invalid image payload → HTTPException 400 BEFORE a verdict: the slot
    must be returned. Pre-fix: current_concurrent stayed at 1 (leaked)."""
    monkeypatch.setenv("SCP_MULTI_LLM_CROSSCHECK", "0")
    monkeypatch.setenv("SCP_WEB_FALLBACK", "0")
    engine = DoSProtectionEngine()
    _mount_judge(monkeypatch, _StubJudge(engine, raise_error=RuntimeError("should not be reached")))

    req = AskRequest(question="hello", image_data="!!!not-base64!!!")
    request = _make_request()

    with pytest.raises(Exception):
        asyncio.run(_api_server_mod._ask_impl(req, request))

    assert engine.stats()["current_concurrent"] == 0, (
        "early-exit /ask request leaked its resource-quota slot"
    )


def test_ask_success_path_records_verdict_and_releases_once(monkeypatch, tmp_path: Path):
    """The success path keeps pairing: record_verdict releases exactly once."""
    monkeypatch.setenv("SCP_MULTI_LLM_CROSSCHECK", "0")
    monkeypatch.setenv("SCP_WEB_FALLBACK", "0")
    engine = DoSProtectionEngine()
    _mount_judge(monkeypatch, _StubJudge(engine, result=PASS_RESULT))

    req = AskRequest(question="What is 2+2?")
    request = _make_request()

    asyncio.run(_api_server_mod._ask_impl(req, request))

    stats = engine.stats()
    assert stats["current_concurrent"] == 0
    assert stats["consecutive_unknown"] == 0  # PASS did not count toward circuit


def test_ask_slot_exhaustion_still_blocks_but_leaks_no_longer_accumulate(monkeypatch, tmp_path: Path):
    """Repeated early exits must not accumulate leaked slots (429-everything bug)."""
    monkeypatch.setenv("SCP_MULTI_LLM_CROSSCHECK", "0")
    monkeypatch.setenv("SCP_WEB_FALLBACK", "0")
    engine = DoSProtectionEngine()
    _mount_judge(monkeypatch, _StubJudge(engine, raise_error=RuntimeError("unreachable")))

    for _ in range(5):
        req = AskRequest(question="hello", image_data="!!!not-base64!!!")
        request = _make_request()
        with pytest.raises(Exception):
            asyncio.run(_api_server_mod._ask_impl(req, request))

    assert engine.stats()["current_concurrent"] == 0
    assert engine.check_request("198.51.100.7") is None  # process still admits work


# ---------------------------------------------------------------------------
# /v1/chat/completions call site — cannot free /ask slots
# ---------------------------------------------------------------------------

@pytest.fixture()
def openai_client(monkeypatch):
    engine = DoSProtectionEngine()
    judge = _StubJudge(engine, result={"verdict": "PASS", "confidence": 0.9, "final_answer": "ok", "evidence": {}})
    monkeypatch.setattr(openai_compat, "get_judge", lambda: judge)
    app = FastAPI()
    app.include_router(openai_compat.router)
    app.dependency_overrides[get_current_user] = lambda: "tester"
    return TestClient(app), engine


def test_chat_completions_cannot_free_ask_slots(openai_client):
    """record_verdict without check_request used to decrement /ask's slot."""
    client, engine = openai_client
    # Simulate one in-flight /ask holding a slot.
    assert engine.check_request("203.0.113.10") is None
    assert engine.stats()["current_concurrent"] == 1

    resp = client.post(
        "/v1/chat/completions",
        json={"model": "scp", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 200
    # The chat request took its own slot (2) and released exactly it (1).
    # Pre-fix: record_verdict without check_request dropped the counter to 0,
    # freeing the /ask slot it never owned.
    assert engine.stats()["current_concurrent"] == 1


def test_chat_completions_full_cycle_releases_own_slot(openai_client):
    client, engine = openai_client
    resp = client.post(
        "/v1/chat/completions",
        json={"model": "scp", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 200
    assert engine.stats()["current_concurrent"] == 0


def test_chat_completions_rate_limit_blocks_before_slot_taken(openai_client):
    """The shared per-IP rate limit now protects chat-completions too, and a
    blocked request must not consume a concurrency slot."""
    client, engine = openai_client
    status_codes = []
    for _ in range(DoSProtectionEngine.MAX_REQUESTS_PER_MINUTE + 1):
        resp = client.post(
            "/v1/chat/completions",
            json={"model": "scp", "messages": [{"role": "user", "content": "hi"}]},
        )
        status_codes.append(resp.status_code)
    assert 429 in status_codes
    assert status_codes[-1] == 429
    assert engine.stats()["current_concurrent"] == 0
