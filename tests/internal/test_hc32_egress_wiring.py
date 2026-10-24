"""[B1-W2 2026-10-23] HC-32 — egress enforcement wiring cho 7 module AUD-S1.

Kết quả verify từng module trong danh sách AUD-S1 (evidence-first, không tin
claim — đọc call site thật):

  * ``scp/autofix/llm_fix_parts/_call_smart_llm.py`` — 2 nhánh đều đã qua choke
    point: gateway path → ``scp/llm_gateway/providers.py`` enforce egress per
    chat (:337-355); fallback ``_call_openrouter`` → ``safe_urlopen`` (:45)
    vốn gọi ``enforce_egress_policy`` TRƯỚC SSRF/DNS. Không thêm gate trùng —
    gate ở tầng sai là placebo.
  * ``scp/core/free_discovery_scheduler.py`` — scheduler shell, KHÔNG gọi mạng;
    2 callee fetch đã gated tại điểm fetch: ``free_api_catalog._http_get`` →
    ``safe_urlopen`` (:144), ``llm_gateway/free_catalog._fetch_catalog_models``
    → ``enforce_egress_policy`` (:44).
  * ``scp/runtime/multi_llm_crosscheck.py`` — chỉ gọi ``provider.chat``;
    transport nằm trong gateway providers (đã enforce egress per attempt).
  * ``enterprise_scanners.py`` / ``resource_leak_scanner.py`` /
    ``shadow_canary.py`` — KHÔNG có call site HTTP (chỉ comment/bảng pattern
    AST của scanner tĩnh) → skip có ledger.
  * ``scp/runtime_audit/probes/services.py`` — probe out-of-process CỐ ĐỊNH
    gọi trực tiếp để đo lường dịch vụ normative (docstring) → giữ nguyên theo
    directive, ghi ledger skip.

Suite này PIN ĐƯỜNG DÂY (anti-placebo) trên nhánh fallback thật sự chạm
transport ngoài gateway — ``_call_openrouter``:

  * URL/host NGOÀI allowlist → ``EgressDeniedError`` bởi gate chuẩn
    (``scp.security.url_safety``), TRƯỚC mọi transport/DNS (negative case
    thuần, không cần mock — khớp evidence policy của
    tests/T03_capability/test_egress_enforcement.py).
  * Host TRONG allowlist → gate cho qua, request tới được mock transport
    (stub opener, KHÔNG network thật).

Chặn quayTest: nếu ai đó gỡ ``safe_urlopen`` khỏi ``_call_openrouter`` hoặc
gỡ gate khỏi ``safe_urlopen``, test NGATIVE sẽ fail (transport stub bị chạm
hoặc gate không deny) — không thể "xanh" bằng cách bỏ enforcement.
"""
from __future__ import annotations

import json

import pytest

import scp.autofix.llm_fix  # noqa: F401  # _wire_parts() inject helper vào part module
import scp.autofix.llm_fix_parts._call_openrouter as _cor_mod
from scp.security.url_safety import (
    EgressDeniedError,
    enforce_egress_policy,
    safe_urlopen,
)

_BLOCKED_HOST = "egress-blocked.invalid"
_ALLOWED_HOST = "egress-allowed.invalid"
_BLOCKED_URL = f"https://{_BLOCKED_HOST}/api/v1/chat/completions"
_ALLOWED_URL = f"https://{_ALLOWED_HOST}/api/v1/chat/completions"
_FAKE_PUBLIC_IP = "93.184.216.34"  # TEST-NET-3 docs address, không route thật


class _StubResponse:
    def __init__(self, body: bytes):
        self._body = body

    def read(self, n: int = -1) -> bytes:
        return self._body

    def close(self) -> None:
        pass

    def __enter__(self) -> "_StubResponse":
        return self

    def __exit__(self, *exc) -> bool:
        return False


class _StubOpener:
    """Mock transport: ghi nhận mọi request, KHÔNG bao giờ ra network thật."""

    def __init__(self, response: _StubResponse):
        self._response = response
        self.opened: list[str] = []

    def open(self, req, timeout=None, **kwargs):  # noqa: ANN001, ANN003
        self.opened.append(str(getattr(req, "full_url", req)))
        return self._response


@pytest.fixture()
def stub_transport(monkeypatch):
    """Thay build_opener bằng stub opener ghi nhận request (mock transport)."""
    opener = _StubOpener(_StubResponse(b"{}"))

    def _fake_build_opener(*handlers):  # noqa: ANN002, ANN003
        return opener

    import scp.security.url_safety as url_safety

    monkeypatch.setattr(url_safety.urllib.request, "build_opener", _fake_build_opener)
    return opener


@pytest.fixture()
def fake_dns(monkeypatch):
    """DNS giả: host .invalid resolve sang IP public docs — cho pinning qua
    tầng SSRF mà không đụng DNS thật."""

    def _fake_getaddrinfo(host, port, *args, **kwargs):  # noqa: ANN001, ANN003
        return [(2, 1, 6, "", (_FAKE_PUBLIC_IP, port or 0))]

    import scp.security.url_safety as url_safety

    monkeypatch.setattr(url_safety.socket, "getaddrinfo", _fake_getaddrinfo)


@pytest.fixture(autouse=True)
def _allowlist_env(monkeypatch):
    """Mode allowlist + allowlist chỉ chứa host được phép của từng test."""
    monkeypatch.setenv("SCP_EGRESS_MODE", "allowlist")
    monkeypatch.delenv("SCP_EGRESS_ALLOWLIST", raising=False)
    monkeypatch.delenv("SCP_PRODUCTION_MODE", raising=False)
    yield


# ---------------------------------------------------------------------------
# A. Gate chuẩn: ngoài allowlist → deny trước transport (thuần, không mock)
# ---------------------------------------------------------------------------
def test_safe_urlopen_denies_host_outside_allowlist(stub_transport):
    with pytest.raises(EgressDeniedError):
        safe_urlopen(_BLOCKED_URL, timeout=5)
    # Gate phải chặn TRƯỚC khi chạm transport — stub opener không được mở.
    assert stub_transport.opened == []


def test_enforce_egress_policy_denies_blocked_url():
    with pytest.raises(EgressDeniedError):
        enforce_egress_policy(_BLOCKED_URL)


# ---------------------------------------------------------------------------
# B. Gate chuẩn: trong allowlist → cho qua tới mock transport
# ---------------------------------------------------------------------------
def test_safe_urlopen_passes_allowlisted_host_to_transport(
    monkeypatch, stub_transport, fake_dns
):
    monkeypatch.setenv("SCP_EGRESS_ALLOWLIST", _ALLOWED_HOST)

    resp = safe_urlopen(_ALLOWED_URL, timeout=5)

    assert resp is not None
    assert stub_transport.opened == [_ALLOWED_URL]


# ---------------------------------------------------------------------------
# C. Wiring _call_smart_llm → _call_openrouter: chặn khi host ngoài allowlist
# ---------------------------------------------------------------------------
def test_call_openrouter_blocked_when_base_url_not_allowlisted(monkeypatch, stub_transport):
    """Anti-placebo: URL target của _call_openrouter bị gate chuẩn chặn; module
    fail-closed trả None; transport stub KHÔNG bao giờ được chạm."""
    monkeypatch.setenv("SCP_EGRESS_ALLOWLIST", "example.com")
    monkeypatch.setenv("OPENROUTER_BASE_URL", f"https://{_BLOCKED_HOST}/api/v1")
    monkeypatch.setattr(_cor_mod, "load_openrouter_keys", lambda: ["test-key"])

    # Pin 1: chính gate chuẩn deny URL mà module sẽ build (chứng minh block là
    # egress, không phải DNS/SSRF).
    with pytest.raises(EgressDeniedError):
        enforce_egress_policy(_BLOCKED_URL)

    # Pin 2: đường dây module thật → bị chặn, không transport.
    result = _cor_mod._call_openrouter("fix this bug", max_tokens=100)

    assert result is None
    assert stub_transport.opened == []


# ---------------------------------------------------------------------------
# D. Wiring _call_smart_llm → _call_openrouter: allowlist → tới mock transport
# ---------------------------------------------------------------------------
def test_call_openrouter_allowlisted_reaches_mock_transport(
    monkeypatch, stub_transport, fake_dns
):
    # Host phải thuộc allowed_hosts của _validate_openrouter_base_url; egress
    # allowlist phải chứa nó để gate chuẩn cho qua; DNS giả để không ra network.
    monkeypatch.setenv("SCP_EGRESS_ALLOWLIST", "openrouter.ai")
    monkeypatch.setenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    monkeypatch.setattr(_cor_mod, "load_openrouter_keys", lambda: ["test-key"])

    stub_transport._response = _StubResponse(
        json.dumps({"choices": [{"message": {"content": "FIXED-CONTENT"}}]}).encode()
    )

    result = _cor_mod._call_openrouter("fix this bug", max_tokens=100)

    assert result == "FIXED-CONTENT"
    assert len(stub_transport.opened) == 1
    assert stub_transport.opened[0].endswith("/chat/completions")
