# SCP CIRCUIT: W14 — egress pin cho geocode fallback (Open-Meteo Geocoding).
"""T05/W14 — host geocoding-api.open-meteo.com được owner duyệt (approval W14)
cho weather geocode fallback: scoped grant CODE-LEVEL RIÊNG của fetch geocode
(`weather.geocode_host_allowed` + `extra_allowed_hosts` trong safe_urlopen —
cùng pattern w13b mà W13 dùng cho forecast fetch), KHÔNG mượn dry-check
forecast (`_weather_host_allowed` giữ nguyên hợp đồng W13 — pin W13 không đổi).

Tách gate = least-privilege: dry-check forecast KHÔNG mở host geocode và
ngược lại; mỗi fetch chỉ authorize host của nó. Nguồn allowlist (pattern
w13b giữ nguyên): CONFIG env `SCP_EGRESS_ALLOWLIST` đọc bởi `EgressPolicy`
(scp/policy/egress.py); scoped grant trong code là lớp deterministic không
phụ thuộc .env.

Pin bắt buộc: host CHÍNH XÁC — không wildcard, KHÔNG subdomain, KHÔNG suffix
spoof, KHÔNG userinfo spoof; DENY mode vẫn thắng mọi grant; metadata/loopback
invariants nguyên.

HERMETIC: chỉ dry-check policy (KHÔNG fetch nào xảy ra); env pin bằng
monkeypatch.setenv (tự phục hồi — Phase 1.1 tier 5).
"""
from __future__ import annotations

import pytest

from scp.data_sources.weather import (
    OPEN_METEO_EGRESS_HOST,
    OPEN_METEO_GEOCODE_EGRESS_HOST,
    geocode_host_allowed,
)
from scp.runtime.question_router import _weather_host_allowed

FORECAST_URL = "https://api.open-meteo.com/v1/forecast?latitude=21.0285&longitude=105.8542&current=temperature_2m"
GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search?name=Vinh&count=1&language=vi"


@pytest.fixture(autouse=True)
def _pin_allowlist_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCP_EGRESS_MODE", "allowlist")
    monkeypatch.setenv("SCP_EGRESS_ALLOWLIST", "")


# ---------------------------------------------------------------------------
# Host được owner duyệt W14 nằm trong scoped grant riêng (old-fails: chưa có)
# ---------------------------------------------------------------------------
def test_geocode_host_allowed_via_scoped_grant_even_with_empty_config() -> None:
    assert OPEN_METEO_GEOCODE_EGRESS_HOST == "geocoding-api.open-meteo.com"
    assert geocode_host_allowed(GEOCODE_URL) is True


def test_gate_separation_forecast_dry_check_does_not_open_geocode_host() -> None:
    """W13 pin giữ nguyên + separation pin W14: dry-check forecast chỉ mở host
    forecast; dry-check geocode chỉ mở host geocode — least-privilege 2 chiều."""
    assert OPEN_METEO_EGRESS_HOST == "api.open-meteo.com"
    assert _weather_host_allowed(FORECAST_URL) is True
    assert _weather_host_allowed(GEOCODE_URL) is False
    assert geocode_host_allowed(FORECAST_URL) is False
    assert geocode_host_allowed(GEOCODE_URL) is True


# ---------------------------------------------------------------------------
# Host khác / spoof vẫn chặn — chính xác, không wildcard
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "blocked_url",
    [
        "https://evil.example.com/v1/search?name=Vinh",
        # Suffix spoof trên cả 2 host được duyệt:
        "https://api.open-meteo.com.evil.example.com/v1/forecast",
        "https://geocoding-api.open-meteo.com.evil.example.com/v1/search",
        # Subdomain/tên-giống-nhưng-khác-host:
        "https://evil-geocoding-api.open-meteo.com/v1/search?name=Vinh",
        "https://open-meteo.com/v1/search?name=Vinh",
        # Userinfo spoof: hostname thật là evil.example.com
        "https://geocoding-api.open-meteo.com@evil.example.com/v1/search",
    ],
)
def test_non_approved_hosts_stay_blocked_on_geocode_gate(blocked_url: str) -> None:
    assert geocode_host_allowed(blocked_url) is False


def test_deny_mode_beats_geocode_scoped_grant(monkeypatch: pytest.MonkeyPatch) -> None:
    """SCP_EGRESS_MODE=deny phải thắng extra_allowed_hosts (Invariant 3 trước
    Invariant 4 trong EgressPolicy.enforce) — grant scoped không thể mở deny."""
    monkeypatch.setenv("SCP_EGRESS_MODE", "deny")
    assert geocode_host_allowed(GEOCODE_URL) is False
    assert _weather_host_allowed(FORECAST_URL) is False


def test_cloud_metadata_and_loopback_invariants_untouched(monkeypatch) -> None:
    """Invariant EgressPolicy nguyên vẹn sau W14 (pattern W13: loopback đi
    generic gate `_host_allowed`; weather gates chỉ mở host đã duyệt)."""
    monkeypatch.setenv("SCP_EGRESS_ALLOWLIST", "geocoding-api.open-meteo.com")
    assert _weather_host_allowed("http://169.254.169.254/latest/meta-data") is False
    assert geocode_host_allowed("http://169.254.169.254/latest/meta-data") is False
    assert geocode_host_allowed("http://127.0.0.1:8090/health") is False
