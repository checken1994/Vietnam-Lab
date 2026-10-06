# SCP CIRCUIT: W13 — egress pin cho weather lookup (Open-Meteo).
"""T05/W13 — host allowlist của weather tier: CHỈ api.open-meteo.com.

Nguồn allowlist (đọc từ code hiện hành — w13b): CONFIG env
`SCP_EGRESS_ALLOWLIST` đọc bởi `EgressPolicy` (scp/policy/egress.py) —
KHÔNG phải hardcode list trong `question_router._host_allowed` (hàm đó chỉ
dry-check delegate qua `enforce_egress_policy`). W13 thêm host theo 2 lớp:
  1. Code-level scoped grant `extra_allowed_hosts={OPEN_METEO_EGRESS_HOST}`
     áp đúng cho weather fetch (weather.py) + dry-check scoped
     (`_weather_host_allowed`) — deterministric không phụ thuộc .env.
  2. Config `.env.example`/`.env` thêm host cho GENERIC path.

Pin bắt buộc: host chính xác — không wildcard, KHÔNG subdomain
(geocoding-api.open-meteo.com vẫn chặn), DENY mode vẫn thắng mọi grant,
metadata/loopback invariants nguyên.

HERMETIC: chỉ dry-check policy (KHÔNG fetch nào xảy ra); env pin bằng
monkeypatch.setenv (tự phục hồi — Phase 1.1 tier 5).
"""
from __future__ import annotations

import urllib.parse

import pytest

from scp.data_sources.weather import OPEN_METEO_EGRESS_HOST, build_open_meteo_url
from scp.runtime.question_router import _host_allowed, _weather_host_allowed

WEATHER_URL = "https://api.open-meteo.com/v1/forecast?latitude=21.0285&longitude=105.8542&current=temperature_2m"


@pytest.fixture(autouse=True)
def _pin_allowlist_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cho phép-list mode + allowlist rỗng mặc định: mỗi test tự khai env."""
    monkeypatch.setenv("SCP_EGRESS_MODE", "allowlist")
    monkeypatch.setenv("SCP_EGRESS_ALLOWLIST", "")


# ---------------------------------------------------------------------------
# (4a) Host được duyệt nằm trong allowlist của weather tier
# ---------------------------------------------------------------------------
def test_weather_host_allowed_via_scoped_grant_even_with_empty_config() -> None:
    """Code-level scoped grant (w13a) mở đúng host được owner duyệt — hành vi
    deterministic không phụ thuộc .env/runtime."""
    assert _weather_host_allowed(WEATHER_URL) is True


def test_pinned_host_constant_matches_fetch_url_host() -> None:
    """Hằng số egress pin phải khớp host mà URL builder thực sự dùng."""
    assert OPEN_METEO_EGRESS_HOST == "api.open-meteo.com"
    parsed = urllib.parse.urlsplit(build_open_meteo_url(21.0285, 105.8542))
    assert (parsed.hostname or "") == OPEN_METEO_EGRESS_HOST


# ---------------------------------------------------------------------------
# (4b) Host khác vẫn chặn — chính xác, không wildcard
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "blocked_url",
    [
        "https://evil.example.com/v1/forecast?latitude=21&longitude=105",
        # Subdomain giống tên nhưng KHÁC host — wildcard bị cấm:
        "https://geocoding-api.open-meteo.com/v1/search?name=Hanoi",
        # Host spoof kiểu suffix (open-meteo.com.evil.example.com):
        "https://api.open-meteo.com.evil.example.com/v1/forecast",
    ],
)
def test_non_approved_hosts_stay_blocked(blocked_url: str) -> None:
    assert _weather_host_allowed(blocked_url) is False


def test_deny_mode_beats_weather_scoped_grant(monkeypatch) -> None:
    """SCP_EGRESS_MODE=deny phải thắng extra_allowed_hosts (Invariant 3 trước
    Invariant 4 trong EgressPolicy.enforce) — grant scoped không thể mở deny."""
    monkeypatch.setenv("SCP_EGRESS_MODE", "deny")
    assert _weather_host_allowed(WEATHER_URL) is False


# ---------------------------------------------------------------------------
# (4c) Config allowlist (nguồn GENERIC path) — thêm host vào config mở đúng host
# ---------------------------------------------------------------------------
def test_generic_host_allowed_reads_config_allowlist(monkeypatch) -> None:
    """`_host_allowed` (generic catalog path) nguồn = SCP_EGRESS_ALLOWLIST:
    thêm host vào config → host đó được phép; host khác vẫn chặn."""
    monkeypatch.setenv("SCP_EGRESS_ALLOWLIST", "api.open-meteo.com,en.wikipedia.org")
    assert _host_allowed(WEATHER_URL) is True
    assert _host_allowed("https://en.wikipedia.org/wiki/Hà_Nội") is True
    assert _host_allowed("https://evil.example.com/x") is False


def test_generic_host_allowed_without_config_still_blocks_weather_host() -> None:
    """Không có host trong config → generic path KHÔNG tự mở (weather tier có
    scoped grant riêng; generic path phải được duyệt qua config)."""
    assert _host_allowed(WEATHER_URL) is False


def test_cloud_metadata_and_loopback_invariants_untouched(monkeypatch) -> None:
    """Invariant EgressPolicy nguyên vẹn: metadata host bị chặn vô điều kiện,
    loopback luôn cho phép (không liên quan weather grant)."""
    monkeypatch.setenv("SCP_EGRESS_ALLOWLIST", "api.open-meteo.com")
    assert _host_allowed("http://169.254.169.254/latest/meta-data") is False
    assert _host_allowed("http://127.0.0.1:8090/health") is True
    assert _weather_host_allowed("http://169.254.169.254/latest/meta-data") is False
