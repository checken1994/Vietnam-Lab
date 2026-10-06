# SCP CIRCUIT: W13 — fork contract cho weather answer (W2-d5/d6 + W8-e1).
"""T02/W13 — weather answer đi qua CÙNG fork contract của conversion/wiki.

Cam kết kiến trúc W13 (GA.md B1b): wire WeatherDataSource KHÔNG được thêm
đường deliver riêng — answer thời tiết phải đi qua attempt_lookup_fork nên
tự động mang: provenance suffix, marker `relevance_gate` (W2-d6 — điều kiện
adapter coi là already_judged, hết self-certify), `data_api_evidence`
(đưa vào grounding check của verify_response), và relevance gate chia sẻ
(W2-d5). W8-e1 time-signal guard: evidence có → guard tắt — weather fork
deliver PASS kèm data_api_evidence nên KHÔNG bị hạ PASS→FAIL/ABSTAIN.

HERMETIC: route_question_async + weather source được mock; KHÔNG mạng,
KHÔNG LLM thật. Egress seam: CI baseline đặt `SCP_EGRESS_MODE=deny`
(ci.yml) — dry-check `_weather_host_allowed` chặn weather tier trước fetch
(deny thắng scoped grant, Invariant 3). Các test tự khai
`SCP_EGRESS_MODE=allowlist` qua monkeypatch.setenv (pattern conftest EE-G1);
hợp đồng deny-mode đã pin riêng tại tests/T05_gateway/test_w13_weather_egress.py.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from scp.data_sources.weather import OPEN_METEO_EGRESS_HOST, WeatherDataSource
from scp.runtime import question_router as qr
from scp.runtime.judge import question_has_time_signal

OPEN_METEO_FIXTURE: dict[str, Any] = {
    "latitude": 21.0,
    "longitude": 105.3,
    "timezone": "Asia/Ho_Chi_Minh",
    "current": {
        "time": "2026-10-06T09:00",
        "temperature_2m": 25.4,
        "relative_humidity_2m": 82,
        "wind_speed_10m": 11.5,
        "weather_code": 2,
    },
    "daily": {
        "time": ["2026-10-06", "2026-10-07"],
        "temperature_2m_max": [31.2, 32.0],
        "temperature_2m_min": [22.8, 23.1],
        "weather_code": [95, 80],
    },
}

Q07 = "Thời tiết Hà Nội hôm nay thế nào?"


class _FakeResponse:
    def __init__(self, payload: Any):
        self._payload = payload
        self.status = 200

    def read(self, *args: Any, **kwargs: Any) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False


def _weather_decision() -> qr.RouteDecision:
    return qr.RouteDecision(
        intent=qr.LOOKUP,
        domain="weather",
        confidence=0.75,
        via="l0-keyword",
        reason="lookup_signal:weather_fact",
        lane=qr.LANE_FACTUAL,
        language="vi",
    )


def _run_weather_fork(monkeypatch: pytest.MonkeyPatch, question: str = Q07) -> dict[str, Any] | None:
    async def _fake_route_async(q: str, gateway: Any = None) -> qr.RouteDecision:
        return _weather_decision()

    def _fake_urlopen(req: Any, timeout: float = 8, **kwargs: Any) -> _FakeResponse:
        return _FakeResponse(OPEN_METEO_FIXTURE)

    # Egress seam: CI baseline = SCP_EGRESS_MODE deny → dry-check chặn tier
    # trước fetch. Test này kiểm tra fork contract, không phải egress policy
    # (deny contract pin ở T05 test_w13_weather_egress).
    monkeypatch.setenv("SCP_EGRESS_MODE", "allowlist")
    monkeypatch.setattr(qr, "route_question_async", _fake_route_async)
    monkeypatch.setattr("scp.data_sources.weather.safe_urlopen", _fake_urlopen)
    monkeypatch.setattr(qr, "_weather_source", WeatherDataSource())

    req = SimpleNamespace(
        question=question,
        contexts=[],
        retrieved_context="",
        ai_answer="",
        session_id="w13-test",
    )
    return asyncio_run_fork(req)


def asyncio_run_fork(req: Any) -> dict[str, Any] | None:
    import asyncio

    return asyncio.run(qr.attempt_lookup_fork(req))


# ---------------------------------------------------------------------------
# Fork e2e: weather deliver mang đủ marker contract
# ---------------------------------------------------------------------------
def test_fork_delivers_weather_answer_with_contract_markers(monkeypatch) -> None:
    response = _run_weather_fork(monkeypatch)
    assert response is not None, "fork phải bắt được q07 qua weather tier"
    assert response["verdict"] == "PASS"
    assert response["governance_decision"] == "UPHOLD"
    # W2-d6: marker relevance_gate — điều kiện adapter coi là already_judged.
    assert response["relevance_gate"]["checked"] is True
    assert "thời" in response["relevance_gate"]["terms"]
    # Route shape như conversion:
    v98 = response["v98_classification"]
    assert v98["route"] == "lookup_data_api"
    assert v98["api_name"] == "Open-Meteo"
    assert v98["provenance"] == "input_context_only"
    # Provenance suffix (Nguồn: Open-Meteo) là phần của answer + evidence:
    assert "(Nguồn dữ liệu: Open-Meteo" in response["final_answer"]
    assert OPEN_METEO_EGRESS_HOST in response["final_answer"]
    # data_api_evidence chứa dữ liệu thật (fixture) cho grounding check:
    assert "25.4°C" in response["data_api_evidence"]
    assert "22.8–31.2°C" in response["data_api_evidence"]
    # Evidence phải bao phủ answer (grounding: mọi từ answer nằm trong evidence):
    answer_core = response["final_answer"].split("(Nguồn dữ liệu")[0].strip()
    assert answer_core in response["data_api_evidence"]


def test_adapter_treats_weather_fork_as_self_certified(monkeypatch) -> None:
    """W2-d6 contract: fork PASS có marker → adapter coi là already_judged
    (không rơi judge path); thiếu marker → KHÔNG self-certify."""
    from scp.ask_kernel_adapter import AskKernelAdapter

    response = _run_weather_fork(monkeypatch)
    assert response is not None
    assert AskKernelAdapter._lookup_fork_self_certified(response) is True
    stripped = dict(response)
    stripped.pop("relevance_gate")
    assert AskKernelAdapter._lookup_fork_self_certified(stripped) is False


def test_fork_relevance_gate_blocks_off_topic_weather_answer(monkeypatch) -> None:
    """W2-d5 gate chia sẻ: weather answer lệch câu hỏi → None → LLM fallback."""
    async def _fake_route_async(q: str, gateway: Any = None) -> qr.RouteDecision:
        return _weather_decision()

    def _fake_urlopen(req: Any, timeout: float = 8, **kwargs: Any) -> _FakeResponse:
        return _FakeResponse(OPEN_METEO_FIXTURE)

    class _OffTopicSource(WeatherDataSource):
        def answer_from_question(self, question: str) -> dict[str, Any] | None:
            result = super().answer_from_question(question)
            if result is None:
                return None
            # Trả text KHÔNG chứa location/terms của câu hỏi (regression:
            # compose sai), vẫn giữ location field để vượt gate trong router —
            # gate chia sẻ của fork (terms_covered) phải là lớp chặn cuối.
            return {**result, "text": f"{result['location']}: dữ liệu không liên quan"}

    monkeypatch.setattr(qr, "route_question_async", _fake_route_async)
    monkeypatch.setattr("scp.data_sources.weather.safe_urlopen", _fake_urlopen)
    # Egress seam — xem docstring module + comment _run_weather_fork.
    monkeypatch.setenv("SCP_EGRESS_MODE", "allowlist")
    monkeypatch.setattr(qr, "_weather_source", _OffTopicSource())

    req = SimpleNamespace(question=Q07, contexts=[], retrieved_context="", ai_answer="", session_id="w13-test")
    # Router-level gate thấy location có trong text ('Hà Nội' nằm trong text)
    # → trả answer; fork-level gate thấy terms không covered → None.
    response = asyncio_run_fork(req)
    assert response is None


# ---------------------------------------------------------------------------
# W8-e1 interaction: weather evidence → time-signal guard KHÔNG hạ PASS
# ---------------------------------------------------------------------------
def test_w8e1_time_signal_question_delivered_with_evidence_not_downgraded(monkeypatch) -> None:
    """q07 chứa time-signal ('hôm nay') — trên judge path W8-e1 sẽ hạ PASS khi
    KHÔNG có evidence. Weather fork deliver PASS kèm data_api_evidence →
    evidence có → guard semantics tắt; đây là pin hợp đồng 2 phía:
      (a) question ĐÚNG trigger time-signal detector (W8-e1 active domain);
      (b) fork deliver giữ verdict PASS + evidence không rỗng."""
    assert question_has_time_signal(Q07) is True
    response = _run_weather_fork(monkeypatch)
    assert response is not None
    assert response["verdict"] == "PASS"
    assert str(response["data_api_evidence"]).strip(), "evidence phải không rỗng"
    assert "25.4" in str(response["data_api_evidence"])


def test_w8e1_hold_when_weather_api_down_fork_returns_none(monkeypatch) -> None:
    """Mặt fail-closed của W8-e1 interaction: weather API down → fork None →
    không có PASS không evidence nào được deliver qua fork; question time-signal
    đi LLM path sẽ bị W8-e1 withhold như thiết kế (không đổi)."""
    async def _fake_route_async(q: str, gateway: Any = None) -> qr.RouteDecision:
        return _weather_decision()

    def _raising_urlopen(req: Any, timeout: float = 8, **kwargs: Any) -> Any:
        raise ConnectionError("simulated open-meteo outage")

    monkeypatch.setattr(qr, "route_question_async", _fake_route_async)
    monkeypatch.setattr("scp.data_sources.weather.safe_urlopen", _raising_urlopen)
    # Egress seam — xem docstring module + comment _run_weather_fork.
    monkeypatch.setenv("SCP_EGRESS_MODE", "allowlist")
    monkeypatch.setattr(qr, "_weather_source", WeatherDataSource())
    monkeypatch.setattr(qr, "_catalog_candidates", lambda terms: ([], ""))

    req = SimpleNamespace(question=Q07, contexts=[], retrieved_context="", ai_answer="", session_id="w13-test")
    assert asyncio_run_fork(req) is None
    assert question_has_time_signal(Q07) is True
