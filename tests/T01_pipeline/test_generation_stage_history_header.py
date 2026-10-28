"""R2 — GenerationStage history header cho câu hỏi không phải tiếng Việt.

TẠI SAO: `GenerationStage.execute` chỉ gán `_ctx_header` khi
`detect_language(question) == "vi"`. Khi `ctx.history` không rỗng và ngôn ngữ
là "en"/"other", việc dựng `_llm_context` gặp UnboundLocalError, bị
`except Exception` nuốt (log "[CHATBOT] LLM call failed") ⇒ `gateway.chat`
KHÔNG được gọi: lane chatbot trả rỗng, lane factual rơi xuống public web
fallback với trigger `llm_timeout_or_error`.

Test này quan sát HÀNH VI THẬT của stage (không placebo, FA-01):
- gateway giả ghi lại mọi lần gọi (không LLM thật) và BIND đối số theo đúng
  chữ ký thật `LLMGateway.chat` ⇒ kwarg lạ / thiếu đối số bắt buộc văng
  TypeError như production; InternetSearch giả ghi lại mọi lần khởi tạo/tìm
  kiếm (không network thật); guard fail-closed ghi lại + chặn mọi connect và
  DNS (`getaddrinfo`) ra ngoài loopback.
- Với "en"/"other" + history, trên cả lane chatbot và factual: gateway.chat
  được await đúng 1 lần với task="chat", context bắt đầu bằng header tiếng
  Anh và chứa các dòng "role: content"; ctx.ai_answer là đáp án của gateway;
  không có log "[CHATBOT] LLM call failed"; InternetSearch không bị gọi.
- Control: "vi" + history dùng header tiếng Việt; không history ⇒ context "".
- Tiền đề định tuyến: detect_language trả đúng mã mong đợi cho từng câu hỏi,
  để test fail ồn ào nếu giả định ngôn ngữ thay đổi.

Hermetic: env chỉ đổi qua monkeypatch (Tầng 5 EXECUTION_PROTOCOL).
"""
from __future__ import annotations

import inspect
import logging
import socket
from typing import Any

import pytest
from starlette.requests import Request as StarletteRequest

from scp.api.system_prompt import build_system_prompt
from scp.api_server_parts.helpers import AskRequest
from scp.api_server_parts.pipeline.context import AskPipelineContext
from scp.api_server_parts.pipeline.generation_stage import GenerationStage
from scp.llm_gateway import LLMGateway
from scp.runtime.question_router import (
    LANE_CHATBOT,
    LANE_FACTUAL,
    RouteDecision,
    detect_language,
)

STAGE_LOGGER = "scp.api_server_parts.pipeline.generation_stage"
FAILED_LOG_NEEDLE = "[CHATBOT] LLM call failed"

EN_HEADER = "Recent conversation history (for reference only):\n"
VI_HEADER = "Lịch sử gần đây (chỉ để tham khảo):\n"

FAKE_ANSWER = "FAKE-GATEWAY-ANSWER: a hash map stores key/value pairs in hashed buckets."
FAKE_PROVIDER = "fake-provider"

HISTORY = [
    {"role": "user", "content": "Hi there"},
    {"role": "assistant", "content": "Hello! How can I help?"},
]
HISTORY_LINES = ("user: Hi there", "assistant: Hello! How can I help?")

QUESTIONS = {
    "en": "Can you explain how a hash map works?",
    "vi": "Giải thích hash map hoạt động thế nào?",
    "other": "哈希表是如何工作的？",
}

LANES = (LANE_CHATBOT, LANE_FACTUAL)


# ---------------------------------------------------------------------------
# Recording test doubles (không trả VERIFIED, chỉ ghi lại tương tác thật)
# ---------------------------------------------------------------------------


_REAL_GATEWAY_CHAT_SIGNATURE = inspect.signature(LLMGateway.chat)


class RecordingGateway:
    """Double của LLMGateway.chat: đối số được bind theo CHỮ KÝ THẬT.

    Kwarg không tồn tại / thiếu `question` ⇒ TypeError y như production (ghi
    vào `rejected` rồi re-raise). Default lấy từ chữ ký thật (vd task="default"),
    nên product bỏ `task="chat"` cũng bị bắt.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.rejected: list[str] = []

    async def chat(self, *args: Any, **kwargs: Any) -> tuple[str, str]:
        try:
            bound = _REAL_GATEWAY_CHAT_SIGNATURE.bind(self, *args, **kwargs)
        except TypeError as exc:
            self.rejected.append(f"{type(exc).__name__}: {exc}")
            raise
        bound.apply_defaults()
        arguments = dict(bound.arguments)
        arguments.pop("self")
        self.calls.append(
            {
                "prompt": arguments["question"],
                "context": arguments["context"],
                "system_prompt": arguments["system_prompt"],
                "task": arguments["task"],
                "messages": arguments["messages"],
                "preferred_provider": arguments["preferred_provider"],
                "explicit_kwargs": sorted(kwargs),
                "n_positional": len(args),
            }
        )
        return FAKE_ANSWER, FAKE_PROVIDER


def make_recording_search_cls() -> tuple[type, list[dict[str, Any]]]:
    events: list[dict[str, Any]] = []

    class RecordingInternetSearch:
        def __init__(self, timeout: float = 4.0) -> None:
            events.append({"event": "init", "timeout": timeout})

        async def search(self, query: str, max_results: int = 6) -> dict[str, Any]:
            events.append({"event": "search", "query": query, "max_results": max_results})
            return {
                "success": True,
                "method": "recording-search",
                "results": [
                    {"title": "recorded", "snippet": "recorded snippet", "url": "https://recorded.invalid"}
                ],
            }

    return RecordingInternetSearch, events


class RecordingBrainShadow:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, **kwargs: Any) -> None:
        self.calls.append(dict(kwargs))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def network_attempts(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Fail-closed guard: mọi connect + DNS ra ngoài loopback bị chặn + ghi lại.

    Giới hạn đã biết: connect bất đồng bộ của asyncio Proactor (Windows,
    overlapped ConnectEx) tới IP literal KHÔNG đi qua socket.socket.connect nên
    không bị chặn ở đây; hostname vẫn bị bắt ở bước getaddrinfo.
    """
    attempts: list[str] = []
    orig_connect = socket.socket.connect
    orig_connect_ex = socket.socket.connect_ex
    orig_getaddrinfo = socket.getaddrinfo
    loopback = ("127.0.0.1", "::1", "localhost")

    def _host(address: Any) -> str:
        return address[0] if isinstance(address, tuple) else str(address)

    def guarded_getaddrinfo(host: Any, port: Any, *args: Any, **kwargs: Any) -> Any:
        name = host.decode("idna") if isinstance(host, bytes) else host
        if name not in (None, "") and str(name) not in loopback:
            attempts.append(f"dns:{name}:{port}")
            raise socket.gaierror(socket.EAI_NONAME, f"[test] outbound DNS blocked: {name}")
        return orig_getaddrinfo(host, port, *args, **kwargs)

    def guarded_connect(sock: socket.socket, address: Any, *args: Any, **kwargs: Any) -> Any:
        if _host(address) not in loopback:
            attempts.append(str(address))
            raise OSError(f"[test] outbound network blocked: {address}")
        return orig_connect(sock, address, *args, **kwargs)

    def guarded_connect_ex(sock: socket.socket, address: Any, *args: Any, **kwargs: Any) -> Any:
        if _host(address) not in loopback:
            attempts.append(str(address))
            return 111
        return orig_connect_ex(sock, address, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", guarded_connect_ex)
    monkeypatch.setattr(socket, "getaddrinfo", guarded_getaddrinfo)

    # Anti-placebo: guard phải thật sự được cài và thật sự chặn (không gửi gì ra
    # mạng: kiểm tra identity trước, gọi guard với sock=None nên nhánh chặn
    # raise trước khi chạm socket thật).
    assert socket.getaddrinfo is guarded_getaddrinfo
    assert socket.socket.connect is guarded_connect
    with pytest.raises(socket.gaierror):
        socket.getaddrinfo("r2-guard-selftest.invalid", 443)
    with pytest.raises(OSError, match="outbound network blocked"):
        socket.socket.connect(None, ("192.0.2.1", 9))  # type: ignore[arg-type]
    assert attempts == ["dns:r2-guard-selftest.invalid:443", "('192.0.2.1', 9)"]
    attempts.clear()
    return attempts


@pytest.fixture(autouse=True)
def _hermetic_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # Bật web fallback để lỗi (nếu có) lộ ra quan sát được ở lane factual.
    monkeypatch.setenv("SCP_WEB_FALLBACK", "1")
    monkeypatch.delenv("SCP_BRAIN_MODE", raising=False)


def _make_starlette_request() -> StarletteRequest:
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


def _build_ctx(
    lang: str,
    lane: str,
    with_history: bool,
) -> tuple[AskPipelineContext, RecordingGateway, list[dict[str, Any]], RecordingBrainShadow]:
    gateway = RecordingGateway()
    search_cls, search_events = make_recording_search_cls()
    shadow = RecordingBrainShadow()
    req = AskRequest(question=QUESTIONS[lang])
    ctx = AskPipelineContext(
        req=req,
        request=_make_starlette_request(),
        namespace={
            "get_gateway": lambda: gateway,
            "InternetSearch": search_cls,
            "_brain_shadow_generate": shadow,
        },
    )
    ctx.history = [dict(turn) for turn in HISTORY] if with_history else []
    ctx.route_decision = RouteDecision(
        intent="REASONING" if lane == LANE_CHATBOT else "LOOKUP",
        domain="general",
        confidence=0.9,
        via="test",
        reason="test",
        lane=lane,
        language=lang,
    )
    ctx.is_chatbot_lane = lane == LANE_CHATBOT
    return ctx, gateway, search_events, shadow


def _expected_history_context(header: str) -> str:
    return header + "\n".join(HISTORY_LINES)


def _stage_records(caplog: pytest.LogCaptureFixture, needle: str) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == STAGE_LOGGER and needle in r.getMessage()]


def _assert_generation_reached_gateway(
    ctx: AskPipelineContext,
    gateway: RecordingGateway,
    search_events: list[dict[str, Any]],
    shadow: RecordingBrainShadow,
    caplog: pytest.LogCaptureFixture,
    network_attempts: list[str],
    lang: str,
    expected_context: str,
    expected_header: str | None,
) -> None:
    # lời gọi phải hợp lệ với chữ ký thật của LLMGateway.chat
    assert gateway.rejected == [], f"gateway.chat rejected={gateway.rejected!r}"
    # gateway.chat được await đúng 1 lần cho generation
    assert len(gateway.calls) == 1, f"gateway.chat calls={gateway.calls!r}"
    call = gateway.calls[0]
    assert call["task"] == "chat"
    assert call["prompt"] == QUESTIONS[lang]
    assert call["messages"] is None
    assert call["preferred_provider"] == ""
    if expected_header is not None:
        assert call["context"].startswith(expected_header)
        for line in HISTORY_LINES:
            assert line in call["context"]
        other_header = VI_HEADER if expected_header == EN_HEADER else EN_HEADER
        assert other_header not in call["context"]
    assert call["context"] == expected_context
    assert call["system_prompt"] == build_system_prompt(lang)
    assert ctx.system_prompt == call["system_prompt"]
    assert ctx.llm_context == expected_context

    # đáp án của gateway là đáp án production
    assert ctx.ai_answer == FAKE_ANSWER
    assert ctx.llm_provider == FAKE_PROVIDER

    # không đi nhánh except / web fallback
    assert _stage_records(caplog, FAILED_LOG_NEEDLE) == []
    # anti-placebo cho caplog: log thành công của CHÍNH logger stage phải bắt được
    assert len(_stage_records(caplog, f"[CHATBOT] LLM ({FAKE_PROVIDER}) generated answer")) == 1
    assert search_events == []
    assert ctx.web_fallback_used is False
    assert ctx.web_fallback == {}
    assert "web_fallback" not in ctx.v98_context

    # brain shadow hook nhận đúng answer/context đã chốt
    assert len(shadow.calls) == 1
    assert shadow.calls[0]["production_answer"] == FAKE_ANSWER
    assert shadow.calls[0]["context"] == expected_context
    assert shadow.calls[0]["question"] == QUESTIONS[lang]

    # hermetic: không có kết nối mạng ra ngoài
    assert network_attempts == []


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("lang", "question"), sorted(QUESTIONS.items()))
def test_detect_language_routing_precondition(lang: str, question: str) -> None:
    assert detect_language(question) == lang


@pytest.mark.asyncio
@pytest.mark.parametrize("lane", LANES)
@pytest.mark.parametrize("lang", ["en", "other"])
async def test_non_vi_question_with_history_reaches_gateway_with_english_header(
    lang: str,
    lane: str,
    caplog: pytest.LogCaptureFixture,
    network_attempts: list[str],
) -> None:
    assert detect_language(QUESTIONS[lang]) == lang
    caplog.set_level(logging.DEBUG, logger=STAGE_LOGGER)
    ctx, gateway, search_events, shadow = _build_ctx(lang, lane, with_history=True)

    await GenerationStage().execute(ctx)

    _assert_generation_reached_gateway(
        ctx,
        gateway,
        search_events,
        shadow,
        caplog,
        network_attempts,
        lang,
        _expected_history_context(EN_HEADER),
        EN_HEADER,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("lane", LANES)
async def test_vi_question_with_history_uses_vietnamese_header_control(
    lane: str,
    caplog: pytest.LogCaptureFixture,
    network_attempts: list[str],
) -> None:
    assert detect_language(QUESTIONS["vi"]) == "vi"
    caplog.set_level(logging.DEBUG, logger=STAGE_LOGGER)
    ctx, gateway, search_events, shadow = _build_ctx("vi", lane, with_history=True)

    await GenerationStage().execute(ctx)

    _assert_generation_reached_gateway(
        ctx,
        gateway,
        search_events,
        shadow,
        caplog,
        network_attempts,
        "vi",
        _expected_history_context(VI_HEADER),
        VI_HEADER,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("lane", LANES)
@pytest.mark.parametrize("lang", ["en", "vi", "other"])
async def test_no_history_passes_empty_context_control(
    lang: str,
    lane: str,
    caplog: pytest.LogCaptureFixture,
    network_attempts: list[str],
) -> None:
    assert detect_language(QUESTIONS[lang]) == lang
    caplog.set_level(logging.DEBUG, logger=STAGE_LOGGER)
    ctx, gateway, search_events, shadow = _build_ctx(lang, lane, with_history=False)

    await GenerationStage().execute(ctx)

    _assert_generation_reached_gateway(
        ctx, gateway, search_events, shadow, caplog, network_attempts, lang, "", None
    )
