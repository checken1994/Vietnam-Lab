"""[FU-DOSLEAK 2026-10-29] /v1/chat/completions must return the DoS
resource-quota slot on EVERY exit after ``check_request`` admitted it.

Bug: ``scp/api/routes/openai_compat.py`` takes a slot through
``DoSProtectionEngine.check_request`` (``_dos_slot_taken = dos_alert is None``)
but the only ``release_slot()`` lived in a ``finally`` that wrapped the judge
call alone. Every exit during generation skipped it:

  * ``gateway.chat`` raises            -> 502 (f97d7293)
  * generation returns an empty answer -> 502 (f97d7293)
  * prompt prep raises (non-str ``content`` -> detect_language) -> 500 (6e32096c)
  * ``CancelledError`` during ``await gateway.chat`` (BaseException)

Each one leaked a slot forever, so ``MAX_CONCURRENT`` failed generations
refused every later request with 429 (availability DoS of the lane).

Seams (zero-mock where feasible): the REAL route, REAL ``DoSProtectionEngine``,
REAL kill-switch gate and SlowAPI limiter. Stubbed: ``get_gateway`` (external
LLM provider, deterministic modes, no network) and ``get_judge`` as the carrier
of the guard -- the production ``RealityJudge`` exposes no ``dos_protection``
attribute, so the engine is attached through the same judge seam that
``test_dos_quota_pairing.py`` uses. Host provider env is cleared and the
kill-switch authority dir is a tmp dir.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from scp.api.routes import openai_compat
from scp.security.dos_protection import DoSProtectionEngine
from scp.security.jwt_guard import get_current_user

_PROVIDER_ENV_KEYS = (
    "GEMINI_API_KEY",
    "OPENAI_API_KEY",
    "DEEPSEEK_API_KEY",
    "GROQ_API_KEY",
    "MISTRAL_API_KEY",
    "OPENROUTER_API_KEY",
    "OPENROUTER_API_KEY_2",
    "OPENROUTER_API_KEY_3",
    "OPENROUTER_API_KEY_FILE",
    "OPENROUTER_API_KEY_2_FILE",
    "OPENROUTER_API_KEY_3_FILE",
    "SCP_LLM_FALLBACK_PROVIDERS",
    "SCP_OPENAI_COMPAT_STUB",
)

_FOREIGN_IP = "198.51.100.77"  # an in-flight request from another client

# The two 502 branches of the generation step.
_BRANCHES_502 = [
    pytest.param("raise", id="branch1-gateway-raises"),
    pytest.param("empty", id="branch2-empty-answer"),
    pytest.param("whitespace", id="branch2-whitespace-answer"),
]


class _Gateway:
    """Hermetic provider seam. ``mode`` is switchable between requests."""

    def __init__(self, mode: str = "ok"):
        self.mode = mode
        self.chat_calls = 0

    def has_active_providers(self) -> bool:
        return True

    async def chat(self, question: str, context: str = "", system_prompt: str = "", task: str = "chat"):
        self.chat_calls += 1
        if self.mode == "raise":
            raise RuntimeError("provider down (fu-dosleak stub)")
        if self.mode == "cancel":
            raise asyncio.CancelledError()
        if self.mode == "empty":
            return "", "fu-dosleak-stub"
        if self.mode == "whitespace":
            return "   \n", "fu-dosleak-stub"
        return "stub-generated-candidate", "fu-dosleak-stub"


class _Judge:
    """Carrier of the REAL DoS guard; returns a governance-cleared PASS."""

    def __init__(self, engine: DoSProtectionEngine):
        self.dos_protection = engine
        self.response_monitor = None
        self.judge_calls = 0

    def judge(self, **kwargs):
        self.judge_calls += 1
        return {
            "verdict": "PASS",
            "confidence": 0.9,
            "final_answer": "ok",
            "evidence": {"governance_decision": "ALLOW"},
        }


def _count_dos_calls(engine: DoSProtectionEngine, monkeypatch) -> dict:
    calls = {"record_verdict": 0, "release_slot": 0}
    real_record = engine.record_verdict
    real_release = engine.release_slot

    def _record(verdict):
        calls["record_verdict"] += 1
        return real_record(verdict)

    def _release():
        calls["release_slot"] += 1
        return real_release()

    monkeypatch.setattr(engine, "record_verdict", _record)
    monkeypatch.setattr(engine, "release_slot", _release)
    return calls


@pytest.fixture()
def lane(monkeypatch, tmp_path: Path):
    for key in _PROVIDER_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("SCP_PC_WORKING_DIR", str(tmp_path))
    monkeypatch.delenv("SCP_KILL_SWITCH", raising=False)

    engine = DoSProtectionEngine()
    judge = _Judge(engine)
    gateway = _Gateway()
    monkeypatch.setattr(openai_compat, "get_gateway", lambda: gateway)
    monkeypatch.setattr(openai_compat, "get_judge", lambda: judge)

    app = FastAPI()
    app.include_router(openai_compat.router)
    app.dependency_overrides[get_current_user] = lambda: "tester"

    def client(ip: str = "203.0.113.5", raise_server_exceptions: bool = True) -> TestClient:
        return TestClient(app, client=(ip, 50000), raise_server_exceptions=raise_server_exceptions)

    return SimpleNamespace(engine=engine, judge=judge, gateway=gateway, client=client)


def _post(client: TestClient, content="hi"):
    return client.post(
        "/v1/chat/completions",
        json={"model": "scp", "messages": [{"role": "user", "content": content}]},
    )


@pytest.mark.parametrize("mode", _BRANCHES_502)
def test_502_generation_failure_releases_slot(lane, monkeypatch, mode):
    """Both 502 branches: the slot taken by check_request is returned exactly
    once via release_slot, and no verdict is fabricated into the breaker."""
    lane.gateway.mode = mode
    calls = _count_dos_calls(lane.engine, monkeypatch)

    resp = _post(lane.client())

    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == 502
    assert lane.gateway.chat_calls == 1
    assert lane.judge.judge_calls == 0  # generation failed before the judge
    stats = lane.engine.stats()
    assert stats["current_concurrent"] == 0, f"502 ({mode}) leaked its DoS slot: {stats}"
    assert calls["release_slot"] == 1
    assert calls["record_verdict"] == 0
    assert stats["consecutive_unknown"] == 0


@pytest.mark.parametrize("mode", _BRANCHES_502)
def test_502_release_is_exactly_once_and_spares_foreign_slot(lane, mode):
    """The clamp in release_slot (max(0, n-1)) would hide a double release at
    n == 0, so a foreign in-flight slot is held: after the 502 exactly that
    slot must remain (double release would steal it -> 0)."""
    assert lane.engine.check_request(_FOREIGN_IP) is None
    lane.gateway.mode = mode

    resp = _post(lane.client())

    assert resp.status_code == 502
    assert lane.engine.stats()["current_concurrent"] == 1


def test_no_release_when_no_slot_was_taken(lane, monkeypatch):
    """check_request failing (caught, fail-open by design) means NO slot was
    taken: the 502 exit must not release anything, i.e. must not free the
    foreign request's slot."""
    assert lane.engine.check_request(_FOREIGN_IP) is None

    def _broken_check(ip="unknown"):
        raise RuntimeError("dos check fault injected")

    monkeypatch.setattr(lane.engine, "check_request", _broken_check)
    calls = _count_dos_calls(lane.engine, monkeypatch)
    lane.gateway.mode = "raise"

    resp = _post(lane.client())

    assert resp.status_code == 502
    assert calls["release_slot"] == 0
    assert calls["record_verdict"] == 0
    assert lane.engine.stats()["current_concurrent"] == 1


def test_502_flood_beyond_max_concurrent_does_not_lock_out_next_request(lane):
    """More failed generations than MAX_CONCURRENT, then a normal request:
    it must be admitted. Pre-fix the first MAX_CONCURRENT 502s leaked every
    slot and the remaining requests were refused 429 for concurrency.
    Clients are spread over distinct IPs (< 60 req each) so neither the
    engine's per-IP rate limit nor SlowAPI's 60/minute can be the cause."""
    engine = lane.engine
    lane.gateway.mode = "raise"
    flood = engine.MAX_CONCURRENT + 5
    per_ip = 50
    codes = []
    for i in range(flood):
        codes.append(_post(lane.client(ip=f"203.0.113.{100 + i // per_ip}")).status_code)

    lane.gateway.mode = "ok"
    resp = _post(lane.client(ip="192.0.2.200"))

    stats = engine.stats()
    assert resp.status_code == 200, (
        f"normal request refused {resp.status_code} after {flood} failed generations; "
        f"current_concurrent={stats['current_concurrent']}/{engine.MAX_CONCURRENT}"
    )
    assert codes == [502] * flood, f"refused during flood: {sorted(set(codes))}"
    assert stats["total_blocked_rate"] == 0  # no rate-limit block was involved
    assert stats["current_concurrent"] == 0
    assert lane.judge.judge_calls == 1


def test_prompt_prep_raise_releases_slot(lane, monkeypatch):
    """An uncaught raise inside prompt prep after the slot was taken; the
    error exit must still release it.

    [FU-KILL-LIST-500] Trigger only: this test used an OpenAI multi-part list
    ``content`` as the vehicle (it crashed ``detect_language``). That shape is
    now normalised to a str and answers 200 (by contract, pinned in
    test_openai_compat_multipart_content.py), so the same prompt-prep raise is
    injected at the ``detect_language`` seam instead. Same code path (raise
    between check_request and the judge), every assertion unchanged."""
    def _prompt_prep_fault(text):
        raise AttributeError("prompt prep fault injected (fu-kill-list)")

    monkeypatch.setattr(openai_compat, "detect_language", _prompt_prep_fault)
    resp = _post(lane.client(raise_server_exceptions=False), content="hi")

    assert resp.status_code != 200
    assert lane.gateway.chat_calls == 0
    assert lane.judge.judge_calls == 0
    assert lane.engine.stats()["current_concurrent"] == 0


def test_cancelled_generation_releases_slot(lane):
    """CancelledError (BaseException, not caught by ``except Exception``)
    during ``await gateway.chat`` must not leak the slot. The TestClient
    portal re-raises the cancelled task as concurrent.futures.CancelledError."""
    lane.gateway.mode = "cancel"

    with pytest.raises((asyncio.CancelledError, concurrent.futures.CancelledError)):
        _post(lane.client())

    assert lane.gateway.chat_calls == 1
    assert lane.engine.stats()["current_concurrent"] == 0


def test_success_path_records_verdict_once_and_keeps_foreign_slot(lane, monkeypatch):
    """Control: success still releases via record_verdict exactly once and
    never via release_slot (no double release with the widened finally)."""
    assert lane.engine.check_request(_FOREIGN_IP) is None
    calls = _count_dos_calls(lane.engine, monkeypatch)

    resp = _post(lane.client())

    assert resp.status_code == 200
    assert lane.judge.judge_calls == 1
    assert calls["record_verdict"] == 1
    assert calls["release_slot"] == 0
    assert lane.engine.stats()["current_concurrent"] == 1
