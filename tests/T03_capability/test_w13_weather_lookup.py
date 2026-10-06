# SCP CIRCUIT: W13 — WeatherDataSource (Open-Meteo) wired vào S24 lookup fork.
"""T03/W13 — câu hỏi thời tiết được trả lời từ dữ liệu Open-Meteo thật.

Bối cảnh W13 (GA.md B1b): q07 "Thời tiết Hà Nội hôm nay thế nào?" — W11 đã
fix route (weather_fact thắng interrogative_vi) nhưng `resolve_lookup_data`
không có weather tier (weather nằm ngoài _KNOWLEDGE_DOMAINS, catalog không
có entry data-API) → luôn LLM fallback → W8-e1 time-signal guard withhold.
Baseline đo được trên main @ bc3b1bb6: resolve_lookup_data(q07) → None.

W13 wire WeatherDataSource (class có sẵn trong repo, 7 intents, chưa từng
được consumer nào touch) làm tier sau conversion, trước catalog.

HERMETIC: mọi HTTP fetch được mock qua `scp.data_sources.weather.safe_urlopen`
— KHÔNG mạng trong test. Fixture JSON mô phỏng payload Open-Meteo; mọi giá trị
assert trong test đến từ fixture (không bịa số liệu ngoài fixture).

EGRESS SEAM (CI ×4 platforms fix 2026-10-06): CI baseline đặt
`SCP_EGRESS_MODE=deny` (ci.yml env) — dry-check `_weather_host_allowed`
(deny thắng scoped grant theo Invariant 3) chặn weather tier TRƯỚC fetch.
Các test đi qua router weather tier tự khai `SCP_EGRESS_MODE=allowlist` qua
monkeypatch.setenv (pattern conftest EE-G1: test cần mode nào tự khai mode đó).
Hợp đồng deny-mode của weather tier (0 fetch, fallback `egress_blocked`) đã
được pin riêng tại tests/T05_gateway/test_w13_weather_egress.py.

Không skip/xfail (kỷ luật test SCP).
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
from typing import Any

import pytest

from scp.data_sources.weather import (
    OPEN_METEO_EGRESS_HOST,
    WeatherDataSource,
    build_open_meteo_url,
)
from scp.runtime import question_router as qr

# Fixture Open-Meteo: shape thật của /v1/forecast (current + daily 2 ngày).
# Đây là "API truth" của test — mọi con số trong assertion đến từ đây.
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
        if isinstance(self._payload, bytes):
            return self._payload
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False


def _install_payload(monkeypatch: pytest.MonkeyPatch, payload: Any) -> list[str]:
    """Mock safe_urlopen trong namespace weather — ghi lại URL được gọi."""
    calls: list[str] = []

    def _fake_urlopen(req: Any, timeout: float = 8, **kwargs: Any) -> _FakeResponse:
        calls.append(str(getattr(req, "full_url", req)))
        return _FakeResponse(payload)

    monkeypatch.setattr("scp.data_sources.weather.safe_urlopen", _fake_urlopen)
    return calls


def _fresh_source() -> WeatherDataSource:
    return WeatherDataSource()


# ---------------------------------------------------------------------------
# (1) Compose đúng từ fixture Open-Meteo (nhiệt độ, thời gian/forecast)
# ---------------------------------------------------------------------------
def test_fetch_parses_current_and_daily_from_fixture(monkeypatch) -> None:
    calls = _install_payload(monkeypatch, OPEN_METEO_FIXTURE)
    src = _fresh_source()
    result = src._fetch_from_open_meteo(21.0285, 105.8542)
    assert result is not None
    assert result["value"] == 25.4
    assert result["metadata"]["temperature_c"] == 25.4
    assert result["metadata"]["humidity_pct"] == 82
    assert result["metadata"]["wind_speed_kmh"] == 11.5
    assert result["metadata"]["weather_code"] == 2
    assert result["metadata"]["daily_max_c"][:1] == [31.2]
    assert result["metadata"]["daily_min_c"][:1] == [22.8]
    # Fetch CHỈ đích đến host được duyệt (scoped egress — w13b).
    assert len(calls) == 1
    assert calls[0].startswith(f"https://{OPEN_METEO_EGRESS_HOST}/v1/forecast?")
    # URL giữ contract SSRF-S1 (test_ssrf_sweep_s1) + daily forecast params.
    assert "daily=temperature_2m_max" in calls[0]
    assert "forecast_days=2" in calls[0]


def test_build_open_meteo_url_host_pinned_and_ssrf_pin_intact() -> None:
    url = build_open_meteo_url(21.0278, 105.8342)
    assert url.startswith("https://api.open-meteo.com/v1/forecast?")
    assert OPEN_METEO_EGRESS_HOST == "api.open-meteo.com"


def test_answer_composes_vi_from_fixture_values(monkeypatch) -> None:
    _install_payload(monkeypatch, OPEN_METEO_FIXTURE)
    src = _fresh_source()
    result = src.answer_from_question(Q07)
    assert result is not None
    text = result["text"]
    # Mọi giá trị số đến từ fixture — không bịa.
    assert "25.4°C" in text
    assert "82%" in text
    assert "11.5 km/h" in text
    assert "22.8–31.2°C" in text
    # Weather code 2 → mô tả chuẩn WMO (bảng tĩnh, không phải dữ liệu bịa).
    assert "có mây từng phần" in text
    # Location (theo dạng người dùng viết) phải xuất hiện trong answer.
    assert "Hà Nội" in text
    assert result["location"] == "Hà Nội"
    # 1 câu duy nhất (chọn câu trực tiếp của fork không cắt mất forecast) —
    # tách câu của _select_relevant_text chỉ trên [.!?] + whitespace.
    import re as _re

    assert len([s for s in _re.split(r"(?<=[.!?])\s+", text) if s.strip()]) == 1


def test_answer_composes_en_and_echoes_user_location_form(monkeypatch) -> None:
    _install_payload(monkeypatch, OPEN_METEO_FIXTURE)
    src = _fresh_source()
    result = src.answer_from_question("What is the weather in Ha Noi today?")
    assert result is not None
    assert "25.4°C" in result["text"]
    assert "forecast today 22.8-31.2°C" in result["text"]
    # Dạng ASCII người dùng viết được echo — để relevance gate (terms câu hỏi)
    # khớp ('ha'/'noi' phải nằm trong answer).
    assert "Ha Noi" in result["text"]
    assert result["location"] == "Ha Noi"


# ---------------------------------------------------------------------------
# (2) API down / unparseable → fail-closed None → LLM fallback
# ---------------------------------------------------------------------------
def test_fail_closed_on_network_error(monkeypatch) -> None:
    def _raising_urlopen(req: Any, timeout: float = 8, **kwargs: Any) -> Any:
        raise urllib.error.URLError("connection refused (simulated)")

    monkeypatch.setattr("scp.data_sources.weather.safe_urlopen", _raising_urlopen)
    assert _fresh_source().answer_from_question(Q07) is None


def test_fail_closed_on_unparseable_payload(monkeypatch) -> None:
    _install_payload(monkeypatch, b"<!doctype html>gateway error page")
    assert _fresh_source().answer_from_question(Q07) is None


def test_fail_closed_on_missing_current_temperature(monkeypatch) -> None:
    payload = {
        "latitude": 21.0,
        "longitude": 105.3,
        "current": {"time": "2026-10-06T09:00", "weather_code": 2},
    }
    _install_payload(monkeypatch, payload)
    assert _fresh_source().answer_from_question(Q07) is None


def test_fail_closed_on_unknown_location_no_network_attempt(monkeypatch) -> None:
    """[W13→W14 cập nhật hợp đồng — owner duyệt egress W14] Location lạ:
    fail-closed None GIỮ NGUYÊN; phần "0 network attempt" của W13 đổi — city
    lạ được đúng 1 fetch geocode (geocoding-api.open-meteo.com, owner duyệt
    W14, scoped grant riêng) và TUYỆT ĐỐI KHÔNG có forecast fetch khi geocode
    miss (0 result / payload lệch). Câu hỏi không trích được candidate →
    0 fetch bất kỳ (fail-closed như cũ)."""
    # Egress seam — xem comment ở test_router_weather_tier_miss_falls_through_to_none
    # (CI baseline = deny; test này pin số fetch geocode sau khi dry-check mở →
    # tự khai mode allowlist, EE-G1).
    monkeypatch.setenv("SCP_EGRESS_MODE", "allowlist")
    calls = _install_payload(monkeypatch, OPEN_METEO_FIXTURE)
    src = _fresh_source()
    assert src.answer_from_question("Thời tiết ở Mèo Vạc hôm nay thế nào?") is None
    assert src.answer_from_question("Thời tiết hôm nay thế nào?") is None

    def _host(url: str) -> str:
        return urllib.parse.urlsplit(url).hostname or ""

    geocode_calls = [c for c in calls if _host(c) == "geocoding-api.open-meteo.com"]
    forecast_calls = [c for c in calls if _host(c) == OPEN_METEO_EGRESS_HOST]
    assert len(geocode_calls) == 1, "city lạ được đúng 1 geocode fetch (W14)"
    assert forecast_calls == [], "geocode miss → KHÔNG có forecast fetch nào (fail-closed)"


def test_router_weather_tier_miss_falls_through_to_none(monkeypatch) -> None:
    """API down → weather tier None → resolve_lookup_data None (LLM fallback
    đường cũ; fork không deliver rác)."""
    # CI baseline set SCP_EGRESS_MODE=deny (ci.yml env) — dry-check
    # `_weather_host_allowed` sẽ chặn tier TRƯỚC fetch (0 fetch, sai seam
    # muốn test: API-down path). Test này kiểm tra tier logic, không phải
    # egress policy — mode được test tự khai (pattern conftest EE-G1; hành vi
    # deny-mode của weather tier đã pin riêng ở T05 test_w13_weather_egress).
    monkeypatch.setenv("SCP_EGRESS_MODE", "allowlist")
    _install_payload(monkeypatch, b"gateway error")
    monkeypatch.setattr(qr, "_weather_source", _fresh_source())
    monkeypatch.setattr(qr, "_catalog_candidates", lambda terms: ([], ""))
    assert qr.resolve_lookup_data(Q07, domain="weather") is None


# ---------------------------------------------------------------------------
# (3) Location mismatch → relevance gate chặn
# ---------------------------------------------------------------------------
def test_location_echo_mismatch_blocks_answer(monkeypatch) -> None:
    """Payload của một location khác (Paris echo 48.85,2.35) cho request Hà
    Nội → gate chặn — không trả dữ liệu của city khác theo câu hỏi."""
    mismatched = dict(OPEN_METEO_FIXTURE)
    mismatched["latitude"] = 48.85
    mismatched["longitude"] = 2.35
    _install_payload(monkeypatch, mismatched)
    assert _fresh_source().answer_from_question(Q07) is None


def test_weather_level_location_gate_in_router(monkeypatch) -> None:
    """Router-level gate: answer không chứa location → None (dù fetch ok)."""
    # Egress seam — xem comment ở test_router_weather_tier_miss_falls_through_to_none.
    monkeypatch.setenv("SCP_EGRESS_MODE", "allowlist")
    calls = _install_payload(monkeypatch, OPEN_METEO_FIXTURE)

    class _BrokenSource(WeatherDataSource):
        def answer_from_question(self, question: str) -> dict[str, Any] | None:
            result = super().answer_from_question(question)
            if result is None:
                return None
            # Mô phỏng compose mất location (regression tương lai).
            return {**result, "text": "nhiệt độ 25.4°C"}

    monkeypatch.setattr(qr, "_weather_source", _BrokenSource())
    monkeypatch.setattr(qr, "_catalog_candidates", lambda terms: ([], ""))
    assert qr.resolve_lookup_data(Q07, domain="weather") is None
    assert len(calls) == 1, "gate chạy sau fetch — đúng thứ tự compose → gate"


# ---------------------------------------------------------------------------
# Router tier wiring (new-passes; old-fails đã đo trên main @ bc3b1bb6 → None)
# ---------------------------------------------------------------------------
def test_resolve_lookup_data_answers_weather_from_open_meteo_tier(monkeypatch) -> None:
    # Egress seam — xem comment ở test_router_weather_tier_miss_falls_through_to_none.
    monkeypatch.setenv("SCP_EGRESS_MODE", "allowlist")
    _install_payload(monkeypatch, OPEN_METEO_FIXTURE)
    monkeypatch.setattr(qr, "_weather_source", _fresh_source())
    result = qr.resolve_lookup_data(Q07, domain="weather")
    assert result is not None
    assert result["api_name"] == "Open-Meteo"
    assert "25.4°C" in result["text"]
    assert "Hà Nội" in result["text"]
    assert result["api_url"].startswith(f"https://{OPEN_METEO_EGRESS_HOST}/v1/forecast?")
    assert result["text"] == result["evidence"]


def test_resolve_lookup_data_accepts_weather_via_route_decision(monkeypatch) -> None:
    """Fork path truyền decision (domain='weather' từ route) — gate theo
    decision.domain phải mở cùng cách với domain kwarg."""
    # Egress seam — xem comment ở test_router_weather_tier_miss_falls_through_to_none.
    monkeypatch.setenv("SCP_EGRESS_MODE", "allowlist")
    _install_payload(monkeypatch, OPEN_METEO_FIXTURE)
    monkeypatch.setattr(qr, "_weather_source", _fresh_source())
    decision = qr.RouteDecision(
        intent=qr.LOOKUP,
        domain="weather",
        confidence=0.75,
        via="l0-keyword",
        reason="lookup_signal:weather_fact",
        lane=qr.LANE_FACTUAL,
    )
    result = qr.resolve_lookup_data(Q07, domain="general", decision=decision)
    assert result is not None
    assert result["api_name"] == "Open-Meteo"


def test_resolve_lookup_data_non_weather_domain_skips_weather_tier(monkeypatch) -> None:
    """Domain không phải weather → tier không chạy (0 fetch), flow cũ nguyên."""
    calls = _install_payload(monkeypatch, OPEN_METEO_FIXTURE)
    monkeypatch.setattr(qr, "_weather_source", _fresh_source())
    monkeypatch.setattr(qr, "_catalog_candidates", lambda terms: ([], ""))
    # Hermetic: chặn cả wiki provider (domain 'general' nằm trong
    # _KNOWLEDGE_DOMAINS — không cho network thật trong test).
    monkeypatch.setattr(qr, "_wiki_lookup", lambda question, terms: None)
    assert qr.resolve_lookup_data("Thủ đô Việt Nam?", domain="general") is None
    assert calls == [], "weather tier không được chạy ngoài domain weather"


def test_weather_tier_does_not_starve_conversion_tier(monkeypatch) -> None:
    """Tier-order regression guard: conversion vẫn thắng ở tier đầu tiên
    (W12 contract giữ nguyên)."""
    _install_payload(monkeypatch, OPEN_METEO_FIXTURE)
    monkeypatch.setattr(qr, "_weather_source", _fresh_source())
    result = qr.resolve_lookup_data("1 mile bằng bao nhiêu km?", domain="general")
    assert result is not None
    assert result["api_name"].startswith("ConversionDataSource")
    assert "1.609" in result["text"]
