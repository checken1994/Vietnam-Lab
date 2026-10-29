"""[FU-KILL-LIST-500 2026-10-29] OpenAI multi-part ``content`` on
/v1/chat/completions must never crash the route with a 500.

Bug (root cause: the user ``content`` was assumed to be a str):

  * kill switch ENGAGED: the gate built ``AskRequest(question=content[:8000])``;
    a list/int content raised (pydantic ValidationError / TypeError) and the
    route answered 500 text/plain instead of the kill-switch 503 JSON.
  * kill switch CLEAR: list/int content reached ``detect_language`` in
    scp/runtime/router_rules.py (``.strip()``) -> AttributeError -> 500.

Contract under test:
  * kill ON  -> 503 kill JSON for ANY content shape (the gate reads no content);
  * kill OFF -> text parts (``[{"type": "text", "text": str}, ...]``) are joined
    with "\\n" and reach the gateway/judge as one str (200);
  * kill OFF -> non-text part / non-str scalar / malformed part -> 400 JSON
    invalid_request, the gateway is NOT called and no DoS slot is held;
  * str content is unchanged (control).

Seams (zero-mock where feasible): the REAL route, REAL kill-switch authority
(file flag in a tmp ``SCP_PC_WORKING_DIR``), REAL ``DoSProtectionEngine`` and
SlowAPI limiter. Stubbed: ``get_gateway`` (external LLM provider; hermetic, no
network) and ``get_judge`` as the carrier of the DoS guard (same seam pattern
as test_dos_slot_release_on_502.py / test_p1_boundary_security.py). Host
provider env keys are deleted.
"""
from __future__ import annotations

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

_FOREIGN_IP = "198.51.100.91"  # an in-flight request from another client

_TEXT_PARTS = [{"type": "text", "text": "hello"}, {"type": "text", "text": "world"}]
_IMAGE_PART = [{"type": "image_url", "image_url": {"url": "https://example.invalid/x.png"}}]

# Every content shape the kill gate must refuse with the kill 503 (it must not
# depend on parsing the content at all -- empty and malformed included).
_KILL_SHAPES = [
    pytest.param("hello there", id="str"),
    pytest.param(_TEXT_PARTS, id="text-parts"),
    pytest.param(_IMAGE_PART, id="image-part"),
    pytest.param(42, id="int"),
    pytest.param([{"type": "text"}], id="malformed-part-no-text"),
    pytest.param(["hi"], id="malformed-part-not-object"),
    pytest.param({"type": "text", "text": "hi"}, id="dict-not-list"),
    pytest.param(0, id="int-zero"),
    pytest.param([], id="empty-list"),
    pytest.param("", id="empty-str"),
    pytest.param(None, id="null"),
]

# Shapes that must be rejected 4xx with the kill switch clear.
_REJECTED_SHAPES = [
    pytest.param(_IMAGE_PART, id="image-part"),
    pytest.param([{"type": "text", "text": "describe"}] + _IMAGE_PART, id="text-plus-image"),
    pytest.param(42, id="int"),
    pytest.param(3.5, id="float"),
    pytest.param(True, id="bool"),
    pytest.param({"type": "text", "text": "hi"}, id="dict-not-list"),
    pytest.param([{"type": "text"}], id="malformed-part-no-text"),
    pytest.param([{"type": "text", "text": 7}], id="malformed-part-text-not-str"),
    pytest.param([{"text": "hi"}], id="malformed-part-no-type"),
    pytest.param(["hi"], id="malformed-part-not-object"),
]


class _Gateway:
    """Hermetic provider seam; records every touch and the question it got."""

    def __init__(self):
        self.seam_calls = 0
        self.chat_questions: list = []

    def seam(self):
        self.seam_calls += 1
        return self

    def has_active_providers(self) -> bool:
        return True

    async def chat(self, question, context: str = "", system_prompt: str = "", task: str = "chat"):
        self.chat_questions.append(question)
        return "multipart-stub-candidate", "fu-kill-list-stub"


class _Judge:
    """Carrier of the REAL DoS guard; returns a governance-cleared PASS."""

    def __init__(self, engine: DoSProtectionEngine):
        self.dos_protection = engine
        self.response_monitor = None
        self.questions: list = []

    def judge(self, **kwargs):
        self.questions.append(kwargs.get("question"))
        return {
            "verdict": "PASS",
            "confidence": 0.9,
            "final_answer": "ok",
            "evidence": {"governance_decision": "ALLOW"},
        }


def _count_dos_calls(engine: DoSProtectionEngine, monkeypatch) -> dict:
    calls = {"check_request": 0, "record_verdict": 0, "release_slot": 0}
    real = {name: getattr(engine, name) for name in calls}

    def _wrap(name):
        def _counted(*args, **kwargs):
            calls[name] += 1
            return real[name](*args, **kwargs)

        return _counted

    for name in calls:
        monkeypatch.setattr(engine, name, _wrap(name))
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
    monkeypatch.setattr(openai_compat, "get_gateway", gateway.seam)
    monkeypatch.setattr(openai_compat, "get_judge", lambda: judge)

    app = FastAPI()
    app.include_router(openai_compat.router)
    app.dependency_overrides[get_current_user] = lambda: "tester"
    # raise_server_exceptions=False: a crash must surface as the HTTP 500 a
    # real client would see, so the assertions below can name it.
    client = TestClient(app, client=("203.0.113.9", 50000), raise_server_exceptions=False)

    flag = tmp_path / "data" / "pc_controller" / "KILL_SWITCH"

    def engage_kill():
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.write_text("fu-kill-list-500 test", encoding="utf-8")
        assert flag.exists()

    return SimpleNamespace(engine=engine, judge=judge, gateway=gateway, client=client,
                           flag=flag, engage_kill=engage_kill)


def _post(lane, content):
    return lane.client.post(
        "/v1/chat/completions",
        json={"model": "scp", "messages": [{"role": "user", "content": content}]},
    )


def _assert_json(resp):
    assert resp.headers.get("content-type", "").startswith("application/json"), (
        f"non-JSON {resp.status_code} response: {resp.text[:200]!r}"
    )
    return resp.json()


@pytest.mark.parametrize("content", _KILL_SHAPES)
def test_kill_engaged_refuses_any_content_shape_with_kill_503(lane, content):
    lane.engage_kill()

    resp = _post(lane, content)

    assert resp.status_code == 503, f"kill ON must answer the kill 503, got {resp.status_code}: {resp.text[:200]!r}"
    body = _assert_json(resp)
    assert body["error"]["type"] == "server_error"
    assert "Kill switch engaged" in body["error"]["message"]
    meta = body["scp_metadata"]
    assert meta["falsification_status"] == "KILL_SWITCH_ENGAGED"
    assert meta["governance_decision"] == "KILL"
    assert meta["verdict"] == "FAIL"
    assert meta["kill_switch"] == "engaged"
    # While killed: no gateway/provider work, no judge, no DoS slot.
    assert lane.gateway.seam_calls == 0
    assert lane.gateway.chat_questions == []
    assert lane.judge.questions == []
    assert lane.engine.stats()["current_concurrent"] == 0


def test_text_parts_are_joined_and_reach_gateway_and_judge(lane):
    resp = _post(lane, _TEXT_PARTS)

    assert resp.status_code == 200, resp.text[:300]
    body = _assert_json(resp)
    assert body["object"] == "chat.completion"
    assert lane.gateway.chat_questions == ["hello\nworld"]
    assert lane.judge.questions == ["hello\nworld"]
    assert lane.engine.stats()["current_concurrent"] == 0


def test_single_text_part_equals_plain_string(lane):
    resp = _post(lane, [{"type": "text", "text": "Ping"}])

    assert resp.status_code == 200, resp.text[:300]
    assert lane.gateway.chat_questions == ["Ping"]


@pytest.mark.parametrize("content", _REJECTED_SHAPES)
def test_unsupported_content_shape_is_clean_400_without_gateway_or_slot(lane, monkeypatch, content):
    calls = _count_dos_calls(lane.engine, monkeypatch)

    resp = _post(lane, content)

    assert resp.status_code == 400, f"expected clean 400, got {resp.status_code}: {resp.text[:200]!r}"
    body = _assert_json(resp)
    assert body["error"]["type"] == "invalid_request"
    assert "content must be a string or a list of" in body["error"]["message"]
    assert lane.gateway.chat_questions == []
    assert lane.judge.questions == []
    stats = lane.engine.stats()
    assert stats["current_concurrent"] == 0, f"4xx content rejection holds a DoS slot: {stats}"
    # Slot ledger balanced: a slot taken by check_request must have been given back.
    assert calls["check_request"] == calls["release_slot"] + calls["record_verdict"]


@pytest.mark.parametrize("content", _REJECTED_SHAPES[:3])
def test_rejected_shape_neither_leaks_nor_steals_foreign_slot(lane, content):
    """release_slot clamps at 0 (max(0, n-1)), which would hide a double
    release; a foreign in-flight slot is held so exactly it must remain."""
    assert lane.engine.check_request(_FOREIGN_IP) is None

    resp = _post(lane, content)

    assert resp.status_code == 400
    assert lane.engine.stats()["current_concurrent"] == 1


def test_error_message_does_not_echo_caller_content(lane):
    marker = "REFLECTION-MARKER-should-not-be-echoed"
    resp = _post(lane, [{"type": "text", "text": "ok"}, {"type": marker, "text": marker}])

    assert resp.status_code == 400
    assert marker not in resp.text


@pytest.mark.parametrize("content", [pytest.param("", id="empty-str"), pytest.param([], id="empty-list"),
                                     pytest.param(None, id="null"),
                                     pytest.param([{"type": "text", "text": ""}], id="empty-text-part")])
def test_empty_content_still_no_user_message_400(lane, content):
    resp = _post(lane, content)

    assert resp.status_code == 400
    body = _assert_json(resp)
    assert body["error"] == {"message": "No user message", "type": "invalid_request"}
    assert lane.gateway.chat_questions == []


def test_str_content_control_unchanged(lane, monkeypatch):
    """Control: a plain str is forwarded byte-for-byte; the slot is returned
    through record_verdict exactly once (FU-DOSLEAK pairing intact)."""
    calls = _count_dos_calls(lane.engine, monkeypatch)
    text = "  Hello! line1\nline2  "

    resp = _post(lane, text)

    assert resp.status_code == 200, resp.text[:300]
    assert lane.gateway.chat_questions == [text]
    assert lane.judge.questions == [text]
    assert calls == {"check_request": 1, "record_verdict": 1, "release_slot": 0}
    assert lane.engine.stats()["current_concurrent"] == 0
