# SCP CIRCUIT: W14 — geocode fallback contract cho weather tier.
"""T02/W14 — city NGOÀI bảng 16+8 của WeatherDataSource phải được trả lời qua
geocode fallback (geocoding-api.open-meteo.com, owner duyệt W14) thay vì
fail-closed None → LLM fallback stale.

Hợp đồng W14:
  * `_match_city` miss → extract candidate từ question → sanitize (inject-guard:
    chỉ ký tự tên thành phố, không & = / % ; hay CRLF, ≤ 80) → geocode fetch
    (safe_urlopen, scoped grant CHỈ host geocode) → lat/lon → forecast fetch
    như bảng local → compose answer. Geocode 0 result / fetch lỗi / candidate
    rỗng / quá dài → None (fail-closed, hành vi cũ giữ nguyên).
  * Old-fails/new-passes: trước W14, "Thời tiết Vinh hôm nay thế nào?" → None
    (city ngoài bảng); sau W14 → answer từ forecast fixture.

HERMETIC: `safe_urlopen` mock toàn bộ (geocode + forecast), KHÔNG mạng, KHÔNG
LLM. Giá trị trong fixture là mock shape theo API thật (Open-Meteo geocoding:
results[].name/latitude/longitude) — không bịa runtime data: runtime gọi API
thật, test gọi fixture.

EGRESS SEAM (CI ×2 platforms fix 2026-10-07): CI baseline đặt
`SCP_EGRESS_MODE=deny` (ci.yml env) — dry-check `geocode_host_allowed` chặn
geocode tier TRƯỚC fetch (deny thắng scoped grant theo Invariant 3, egress.py
"blocks all non-loopback outbound traffic" trước khi xét token hosts) → 0
fetch → các test cần geocode fetch chạy phải tự khai `SCP_EGRESS_MODE=allowlist`
qua monkeypatch.setenv (pattern conftest EE-G1: test cần mode nào tự khai mode
đó — cùng seam với file W13). Hợp đồng deny-mode của geocode tier (dry-check
block, 0 fetch) đã được pin riêng tại tests/T05_gateway/test_w14_weather_geocode_egress.py.
"""
from __future__ import annotations

import json
import urllib.parse
from typing import Any, Self

import pytest

from scp.data_sources.weather import (
    OPEN_METEO_EGRESS_HOST,
    OPEN_METEO_GEOCODE_EGRESS_HOST,
    WeatherDataSource,
    _sanitize_city_name,
    build_geocode_url,
)

GEOCODE_FIXTURE: dict[str, Any] = {
    "results": [
        {
            "id": 1566001,
            "name": "Vinh",
            "latitude": 18.6734702,
            "longitude": 105.6889209,
            "elevation": 6.0,
            "feature_code": "PPLA",
            "country_code": "VN",
            "country": "Việt Nam",
            "admin1": "Nghệ An",
            "timezone": "Asia/Ho_Chi_Minh",
            "population": 232279,
        }
    ],
    "generationtime_ms": 0.512,
}

FORECAST_FIXTURE: dict[str, Any] = {
    "latitude": 18.7,
    "longitude": 105.7,
    "timezone": "Asia/Ho_Chi_Minh",
    "current": {
        "time": "2026-10-07T09:00",
        "temperature_2m": 26.3,
        "relative_humidity_2m": 78,
        "wind_speed_10m": 9.2,
        "weather_code": 2,
    },
    "daily": {
        "time": ["2026-10-07", "2026-10-08"],
        "temperature_2m_max": [31.5, 32.1],
        "temperature_2m_min": [23.0, 23.4],
        "weather_code": [80, 3],
    },
}

Q_VINH = "Thời tiết Vinh hôm nay thế nào?"


class _RecordingUrlopen:
    """Mock safe_urlopen: dispatch theo host (geocode vs forecast), ghi lại
    mọi URL + kwargs để assert scoped grant."""

    def __init__(self, payloads: dict[str, Any]):
        self._payloads = payloads  # host → payload hoặc Exception
        self.calls: list[dict[str, Any]] = []

    def __call__(self, req: Any, timeout: float = 8, **kwargs: Any) -> Any:
        url = req.full_url if hasattr(req, "full_url") else str(req)
        host = urllib.parse.urlsplit(url).hostname or ""
        self.calls.append({"url": url, "host": host, "kwargs": kwargs})
        payload = self._payloads.get(host)
        if isinstance(payload, Exception):
            raise payload
        if payload is None:
            raise AssertionError(f"unexpected fetch to host {host}: {url}")
        return _FakeResponse(payload)


class _FakeResponse:
    def __init__(self, payload: Any):
        self._payload = payload
        self.status = 200

    def read(self, *args: Any, **kwargs: Any) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


def _source_with_mock(monkeypatch: pytest.MonkeyPatch, payloads: dict[str, Any]) -> _RecordingUrlopen:
    mock = _RecordingUrlopen(payloads)
    monkeypatch.setattr("scp.data_sources.weather.safe_urlopen", mock)
    return mock


# ---------------------------------------------------------------------------
# Geocode hit: city ngoài bảng → lat/lon từ geocode → forecast compose
# ---------------------------------------------------------------------------
def test_geocode_hit_outside_table_composes_forecast_answer(monkeypatch) -> None:
    """Old-fails/new-passes: trước W14 city ngoài bảng → None; sau W14 →
    answer compose từ forecast của toạ độ geocode (fixture)."""
    # Egress seam — xem module docstring: test cần geocode + forecast fetch
    # thật (qua mock safe_urlopen) → tự khai mode allowlist (EE-G1).
    monkeypatch.setenv("SCP_EGRESS_MODE", "allowlist")
    src = WeatherDataSource()
    assert src._match_city(Q_VINH) == (None, ""), "precondition: Vinh ngoài bảng local"
    mock = _source_with_mock(
        monkeypatch,
        {
            OPEN_METEO_GEOCODE_EGRESS_HOST: GEOCODE_FIXTURE,
            OPEN_METEO_EGRESS_HOST: FORECAST_FIXTURE,
        },
    )
    result = src.answer_from_question(Q_VINH)
    assert result is not None, "geocode fallback phải trả lời city ngoài bảng"
    assert "Vinh" in result["text"]
    assert "26.3" in result["text"], "nhiệt độ phải từ payload forecast (fixture)"
    assert result["location"] == "Vinh"

    geocode_calls = [c for c in mock.calls if c["host"] == OPEN_METEO_GEOCODE_EGRESS_HOST]
    forecast_calls = [c for c in mock.calls if c["host"] == OPEN_METEO_EGRESS_HOST]
    assert len(geocode_calls) == 1 and len(forecast_calls) == 1

    # URL geocode đúng contract: name/count/language + scoped grant CHỈ host geocode
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(geocode_calls[0]["url"]).query))
    assert q["name"] == "Vinh"
    assert q["count"] == "1" and q["language"] == "vi"
    assert geocode_calls[0]["kwargs"].get("extra_allowed_hosts") == frozenset(
        {OPEN_METEO_GEOCODE_EGRESS_HOST}
    ), "geocode fetch phải có scoped grant riêng, không mượn grant forecast"

    # Forecast fetch dùng toạ độ geocode (fixture), không phải bảng local
    fq = urllib.parse.parse_qs(urllib.parse.urlsplit(forecast_calls[0]["url"]).query)
    assert float(fq["latitude"][0]) == pytest.approx(18.6734702)
    assert float(fq["longitude"][0]) == pytest.approx(105.6889209)


def test_geocode_hit_fork_level_contract(monkeypatch) -> None:
    """Fork-level: question weather city ngoài bảng phải deliver PASS qua
    lookup fork (không rơi LLM fallback) — old-fails: fork trả None."""
    import asyncio

    from scp.runtime import question_router as qr

    async def _fake_route_async(q: str, gateway: Any = None) -> qr.RouteDecision:
        return qr.RouteDecision(
            intent=qr.LOOKUP,
            domain="weather",
            confidence=0.75,
            via="l0-keyword",
            reason="lookup_signal:weather_fact",
            lane=qr.LANE_FACTUAL,
            language="vi",
        )

    monkeypatch.setenv("SCP_EGRESS_MODE", "allowlist")
    monkeypatch.setattr(qr, "route_question_async", _fake_route_async)
    _source_with_mock(
        monkeypatch,
        {
            OPEN_METEO_GEOCODE_EGRESS_HOST: GEOCODE_FIXTURE,
            OPEN_METEO_EGRESS_HOST: FORECAST_FIXTURE,
        },
    )
    monkeypatch.setattr(qr, "_weather_source", WeatherDataSource())
    monkeypatch.setattr(qr, "_catalog_candidates", lambda terms: ([], ""))

    from types import SimpleNamespace

    req = SimpleNamespace(
        question=Q_VINH, contexts=[], retrieved_context="", ai_answer="", session_id="w14-test"
    )
    response = asyncio.run(qr.attempt_lookup_fork(req))
    assert response is not None, "fork phải bắt được city ngoài bảng qua geocode tier"
    assert response["verdict"] == "PASS"
    assert response["v98_classification"]["api_name"] == "Open-Meteo"
    assert "Vinh" in response["final_answer"]
    assert "26.3" in response["data_api_evidence"]


# ---------------------------------------------------------------------------
# Fail-closed: geocode miss / lỗi / candidate rỗng / quá dài
# ---------------------------------------------------------------------------
def test_geocode_miss_zero_results_fail_closed(monkeypatch) -> None:
    src = WeatherDataSource()
    _source_with_mock(monkeypatch, {OPEN_METEO_GEOCODE_EGRESS_HOST: {"results": []}})
    assert src.answer_from_question(Q_VINH) is None


def test_geocode_error_fail_closed(monkeypatch) -> None:
    src = WeatherDataSource()
    _source_with_mock(
        monkeypatch, {OPEN_METEO_GEOCODE_EGRESS_HOST: ConnectionError("simulated geocode outage")}
    )
    assert src.answer_from_question(Q_VINH) is None


def test_geocode_malformed_payload_fail_closed(monkeypatch) -> None:
    """Payload geocode thiếu latitude/longitude hoặc results[] không dict →
    None (không compose answer từ toạ độ không tin cậy)."""
    src = WeatherDataSource()
    for bad in ({"results": [{"name": "X"}]}, {"results": ["nope"]}, {}, {"results": None}):
        _source_with_mock(monkeypatch, {OPEN_METEO_GEOCODE_EGRESS_HOST: bad})
        assert src.answer_from_question(Q_VINH) is None, f"payload {bad!r} phải fail-closed"


def test_no_candidate_no_fetch(monkeypatch) -> None:
    """Câu hỏi weather không trích được candidate (toàn filler) → KHÔNG fetch
    nào, fail-closed như cũ."""
    src = WeatherDataSource()
    mock = _source_with_mock(monkeypatch, {})
    assert src.answer_from_question("Thời tiết hôm nay thế nào?") is None
    assert mock.calls == []


def test_city_candidate_over_80_chars_fail_closed(monkeypatch) -> None:
    src = WeatherDataSource()
    mock = _source_with_mock(monkeypatch, {})
    long_q = "Thời tiết " + "x" * 120 + " hôm nay thế nào?"
    assert src.answer_from_question(long_q) is None
    assert mock.calls == [], "candidate quá 80 ký tự phải fail-closed trước fetch"


# ---------------------------------------------------------------------------
# Inject-guard: sanitize city name trước khi vào URL geocode
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "raw",
    [
        "Hà Nội&param=evil",
        "../../etc/passwd",
        "Hà Nội\r\nHost: evil.example.com",
        "Vinh; DROP TABLE users",
        "Vinh%3Bx",
        "Vinh=x&y",
        "<script>alert(1)</script>",
    ],
)
def test_sanitize_city_name_strips_injection_chars(raw: str) -> None:
    """Chỉ ký tự tên thành phố được chấp nhận: không & = / % ; < > cũng như
    CRLF; whitespace collapse thành 1 space; ≤ 80."""
    out = _sanitize_city_name(raw)
    if not out:
        return  # fail-closed hoàn toàn cũng chấp nhận được
    for ch in "&=/%;<>\\(){}[]$!@#^*|~`\"'?:":
        assert ch not in out, f"char {ch!r} sót trong {out!r}"
    assert "\r" not in out and "\n" not in out
    assert "\t" not in out
    assert len(out) <= 80
    assert out == out.strip()


def test_sanitize_city_name_keeps_normal_city_names() -> None:
    """Tên thành phố hợp lệ phải giữ nguyên (kể cả dấu tiếng Việt, dấu nháy,
    dấu gạch, dấu chấm, dấu cách)."""
    assert _sanitize_city_name("Vinh") == "Vinh"
    assert _sanitize_city_name("Hà Nội") == "Hà Nội"
    assert _sanitize_city_name("Winston-Salem") == "Winston-Salem"
    assert _sanitize_city_name("Xi'an") == "Xi'an"
    assert _sanitize_city_name("São Paulo") == "São Paulo"
    assert _sanitize_city_name("St. Louis") == "St. Louis"


def test_sanitize_rejects_digits_and_oversize() -> None:
    """Conservative: chữ số/underscore không thuộc tên thành phố → loại;
    rỗng sau sanitize → ''; quá 80 → '' (fail-closed)."""
    assert _sanitize_city_name("") == ""
    assert _sanitize_city_name("   ") == ""
    assert _sanitize_city_name("12345") == ""
    assert _sanitize_city_name("Vinh_123") == "Vinh"
    assert _sanitize_city_name("a" * 81) == ""
    assert _sanitize_city_name("a" * 80) == "a" * 80


def test_injection_question_geocode_url_contains_sanitized_value_only(monkeypatch) -> None:
    """'Vinh&param=evil' (city NGOÀI bảng — bắt buộc để đi đường geocode) →
    geocode fetch (nếu có) chỉ chứa giá trị đã sanitize; giá trị injection
    không bao giờ xuất hiện nguyên vẹn trong URL. Geocode miss → None
    (fail-closed)."""
    # Egress seam — xem module docstring: test pin URL geocode SAU khi fetch
    # chạy (1 call) → tự khai mode allowlist (EE-G1).
    monkeypatch.setenv("SCP_EGRESS_MODE", "allowlist")
    src = WeatherDataSource()
    mock = _source_with_mock(
        monkeypatch, {OPEN_METEO_GEOCODE_EGRESS_HOST: {"results": []}}
    )
    result = src.answer_from_question("Thời tiết Vinh&param=evil hôm nay thế nào?")
    assert result is None
    geocode_calls = [c for c in mock.calls if c["host"] == OPEN_METEO_GEOCODE_EGRESS_HOST]
    assert len(geocode_calls) == 1
    url = geocode_calls[0]["url"]
    assert "param=evil" not in url, "payload injection không được vào URL nguyên vẹn"
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
    assert "&" not in q["name"] and "=" not in q["name"] and "/" not in q["name"]
    assert q["name"] == _sanitize_city_name(q["name"])
    assert q["name"] == "Vinh param evil"


def test_header_injection_question_no_crlf_in_url(monkeypatch) -> None:
    """Header injection qua city name (CRLF) — city NGOÀI bảng để đi geocode →
    không còn \r \n trong URL; geocode miss → None."""
    # Egress seam — xem module docstring: test pin URL sau khi fetch chạy
    # (mock.calls[0]) → tự khai mode allowlist (EE-G1).
    monkeypatch.setenv("SCP_EGRESS_MODE", "allowlist")
    src = WeatherDataSource()
    mock = _source_with_mock(
        monkeypatch, {OPEN_METEO_GEOCODE_EGRESS_HOST: {"results": []}}
    )
    result = src.answer_from_question("Thời tiết Vinh\r\nX-Evil: 1 hôm nay?")
    assert result is None
    url = mock.calls[0]["url"]
    assert "\r" not in url and "\n" not in url


# ---------------------------------------------------------------------------
# URL builder pin
# ---------------------------------------------------------------------------
def test_build_geocode_url_fixed_host_and_query() -> None:
    """Host cố định geocoding-api.open-meteo.com; name nằm trong query value
    (percent-encoded), path không đổi — không thể path traversal."""
    url = build_geocode_url("Vinh")
    parsed = urllib.parse.urlsplit(url)
    assert parsed.hostname == OPEN_METEO_GEOCODE_EGRESS_HOST
    assert parsed.path == "/v1/search"
    q = dict(urllib.parse.parse_qsl(parsed.query))
    assert q == {"name": "Vinh", "count": "1", "language": "vi"}


def test_build_geocode_url_percent_encodes_special_chars() -> None:
    url = build_geocode_url("Hà Nội")
    parsed = urllib.parse.urlsplit(url)
    q = dict(urllib.parse.parse_qsl(parsed.query))
    assert q["name"] == "Hà Nội"
    assert parsed.path == "/v1/search"
