# SCP CIRCUIT: WIRE-BRIDGE — FreeAPIAdapterBridge nối vào luồng /ask (hermetic).
"""T07/WIRE-BRIDGE — test [OWNER-MANDATE 2026-10-25]: nối toàn bộ catalog
2.226 free API vào luồng chạy chính của SCP.

Trước patch: `FreeAPIAdapterBridge` (4 tầng WAL → catalog → live → wikipedia)
là dead code — 0 caller. Sau patch: `resolve_lookup_data` gọi bridge qua
`_bridge_lookup` SAU tier dữ liệu local và TRƯỚC weak catalog tier.

Phủ test:
  (a) Positive: bridge trả kết quả catalog → provenance map đúng format
      {'text','api_name','api_url','evidence'}; store kwargs khớp signature
      DomainKnowledge.store (chống drift — KHÔNG lỏng trust gate).
  (b) Unwire-check (anti-placebo): resolve_lookup_data PHẢI gọi bridge kể cả
      khi miss — chặn regression dead-code quay lại; bridge short-circuit
      trước weak catalog tier.
  (c) Anti-placebo egress: SCP_EGRESS_MODE=deny chặn TRƯỚC fetch (Invariant 3
      của EgressPolicy chạy TRƯỚC Invariant 4 — scoped grant không override
      deny); entry private-IP/loopback/metadata không bao giờ được fetch;
      term-gate miss → None, không store, không fabrication.
  (d) Latency budget: max_candidates ≤ 3 + deadline 6s — fetch vượt budget
      bị bỏ, không đốt 15s worst case cũ.

HERMETIC: catalog inject từ tmp_path (không đụng data/), safe_urlopen mock
hoặc bị chặn bởi validate_url trước khi có socket, socket guard toàn cục.
Không skip/xfail.
"""
from __future__ import annotations

import inspect
import json
import socket
from pathlib import Path
from typing import Any

import pytest

from scp.data_sources.free_api_bridge import (
    DOMAIN_TO_CATALOG_CATEGORIES,
    FreeAPIAdapterBridge,
)
from scp.knowledge.domain_knowledge import DomainKnowledge
from scp.security import url_safety as url_safety_mod

CATALOG_ENTRY_NEWS = {
    "name": "Spaceflight News",
    "url": "https://api.test-spaceflight.test/v3/articles",
    # "latest" nằm trong description để weak catalog tier (search theo terms)
    # cũng match entry này — test router-level deny đo được cả 2 reason
    # (bridge_miss + egress_blocked từ weak tier).
    "description": "latest spaceflight news data",
    "auth": "No",
    "https": "Yes",
    "cors": "yes",
    "category": "News",
}

SPACEFLIGHT_QUESTION = "What is the latest spaceflight news?"


# ---------------------------------------------------------------------------
# Hermetic fixtures
# ---------------------------------------------------------------------------
class _FakeKnowledge:
    """DomainKnowledge thay thế: không SQLite thật, ghi lại store kwargs.

    sqlite_path trỏ tới file không tồn tại → Tier 0 WAL miss sạch (không đọc
    DB production — test determinstic).
    """

    def __init__(self, tmp_path: Path) -> None:
        self.sqlite_path = tmp_path / "nonexistent-knowledge.sqlite3"
        self.store_calls: list[dict[str, Any]] = []

    def store(self, **kwargs: Any) -> dict[str, Any] | None:
        self.store_calls.append(kwargs)
        return {"ok": True}


def _catalog_with(tmp_path: Path, entries: list[dict[str, Any]]) -> Any:
    """FreeAPICatalog hermetic: cache JSON ghi sẵn vào tmp_path (entries()
    chỉ đọc cache file — không refresh, không mạng)."""
    from scp.data_sources.free_api_catalog import FreeAPICatalog

    payload = {
        "schema_version": 1,
        "source": "test-fixture",
        "fetched_at": 0.0,
        "count": len(entries),
        "entries": entries,
    }
    (tmp_path / "free_api_catalog.json").write_text(json.dumps(payload), encoding="utf-8")
    return FreeAPICatalog(data_dir=str(tmp_path))


def _make_bridge(tmp_path: Path, entries: list[dict[str, Any]]) -> tuple[FreeAPIAdapterBridge, _FakeKnowledge]:
    kb = _FakeKnowledge(tmp_path)
    return FreeAPIAdapterBridge(catalog=_catalog_with(tmp_path, entries), knowledge_store=kb), kb


class _FakeResp:
    """Response giả cho safe_urlopen mock (context manager + headers dict)."""

    def __init__(self, payload: bytes, content_type: str = "application/json") -> None:
        self._payload = payload
        self.headers = {"Content-Type": content_type}
        self.status = 200

    def __enter__(self) -> "_FakeResp":
        return self

    def __exit__(self, *args: Any) -> bool:
        return False

    def read(self, n: int = -1) -> bytes:
        return self._payload


def _pin_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin posture allowlist + allowlist rỗng (pattern conftest EE-G1 — test
    tự khai mode; scoped grant của bridge là thứ mở host catalog fixture)."""
    monkeypatch.setenv("SCP_EGRESS_MODE", "allowlist")
    monkeypatch.setenv("SCP_EGRESS_ALLOWLIST", "")


def _block_sockets(monkeypatch: pytest.MonkeyPatch) -> None:
    """Chặn mọi socket thật — anti-placebo: nếu test nào thử mở kết nối mạng
    thật thì test ĐỎ (không phải product âm thầm fetch)."""

    def _no_socket(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("network socket attempted — test phải hermetic")

    monkeypatch.setattr(socket, "socket", _no_socket)
    monkeypatch.setattr(socket, "create_connection", _no_socket)
    monkeypatch.setattr(socket, "getaddrinfo", _no_socket)


def _spy_urlopen(monkeypatch: pytest.MonkeyPatch, payload: bytes = b"{}") -> list[str]:
    """Mock safe_urlopen ở seam bridge dùng (getattr(url_safety, 'safe_urlopen'))
    — ghi lại URL được attempt fetch. Mock THAY tầng transport, các gate phía
    trước (egress dry-check) vẫn chạy thật."""
    calls: list[str] = []

    def _fake(req: Any, timeout: float = 30, **kwargs: Any) -> _FakeResp:
        calls.append(str(getattr(req, "full_url", req)))
        return _FakeResp(payload)

    monkeypatch.setattr(url_safety_mod, "safe_urlopen", _fake)
    return calls


# ---------------------------------------------------------------------------
# (a) Positive — Tier 1 catalog trả kết quả có provenance + store đúng contract
# ---------------------------------------------------------------------------
def test_bridge_tier1_catalog_answer_with_provenance_and_store(monkeypatch, tmp_path) -> None:
    _pin_allowlist(monkeypatch)
    monkeypatch.setattr(url_safety_mod, "safe_urlopen", lambda req, timeout=30, **kw: _FakeResp(
        b'{"title": "Latest Artemis spaceflight news update"}'
    ))
    bridge, kb = _make_bridge(tmp_path, [dict(CATALOG_ENTRY_NEWS)])

    result = bridge.fetch_for_domain("news", "lookup", "spaceflight news", SPACEFLIGHT_QUESTION)

    assert result is not None and result["found"] is True
    assert result["epistemic_tier"] == "free_api_catalog"
    assert result["api_name"] == "Spaceflight News"
    assert result["api_url"] == CATALOG_ENTRY_NEWS["url"]
    assert "spaceflight" in result["value"].lower()
    # Store contract (task item 4): kwargs khớp signature DomainKnowledge.store
    # — drift signature sẽ làm test đỏ (KHÔNG lỏng trust gate, chỉ pin call).
    sig_params = set(inspect.signature(DomainKnowledge.store).parameters)
    assert len(kb.store_calls) == 1
    call = kb.store_calls[0]
    assert set(call) <= sig_params
    assert call["collected_by"] == "free_api_catalog"
    assert call["confidence"] == 0.85
    assert call["source"] == "Spaceflight News"
    assert call["source_url"] == CATALOG_ENTRY_NEWS["url"]


def test_domain_news_maps_to_news_category(tmp_path) -> None:
    """[WIRE-BRIDGE 2026-10-25] DOMAIN_TO_CATALOG_CATEGORIES có 'news' → chỉ
    entry category News được chọn (ánh xạ category, không phải dữ liệu sự kiện
    — không vi phạm Tabula Rasa)."""
    entries = [
        dict(CATALOG_ENTRY_NEWS),
        {
            "name": "Noozra",
            "url": "https://www.test-noozra.test/search",
            "description": "news search engine",
            "auth": "No",
            "https": "Yes",
            "cors": "yes",
            "category": "News",
        },
        {
            "name": "ChemSpider",
            "url": "https://api.test-chem.test/chem",
            "description": "chemistry database",
            "auth": "No",
            "https": "Yes",
            "cors": "yes",
            "category": "Science & Math",
        },
    ]
    bridge, _kb = _make_bridge(tmp_path, entries)
    apis = bridge.get_apis_for_domain("news")
    names = {a["name"] for a in apis}
    assert names == {"Spaceflight News", "Noozra"}


# ---------------------------------------------------------------------------
# (b) Unwire-check — resolve_lookup_data PHẢI gọi bridge (dead-code tripwire)
# ---------------------------------------------------------------------------
def test_resolve_lookup_data_calls_bridge_and_maps_provenance(monkeypatch) -> None:
    """Bridge trả kết quả → resolve map đúng format + short-circuit TRƯỚC weak
    catalog tier; pin latency budget (max_candidates ≤ 3, budget ≤ 6s,
    include_live_fallbacks=False — Tier 2/3 trùng chuỗi fallback cũ)."""
    from scp.runtime import question_router as qr
    import scp.data_sources.free_api_bridge as bridge_mod

    calls: dict[str, Any] = {}

    class _FakeBridge:
        def fetch_for_domain(self, **kwargs: Any) -> dict[str, Any]:
            calls.update(kwargs)
            return {
                "found": True,
                "value": "Bhutan is a landlocked country in South Asia with a measured population.",
                "source": "Countries Now",
                "api_name": "Countries Now",
                "api_url": "https://countriesnow.test/api/v0.1/countries/population",
                "confidence": 0.85,
                "epistemic_tier": "free_api_catalog",
            }

    monkeypatch.setattr(bridge_mod, "get_bridge", lambda: _FakeBridge())
    # domain_data_fork đứng trước bridge — ép miss để đường đi CHẮC qua bridge
    monkeypatch.setattr(
        "scp.runtime.domain_data_fork.resolve_domain_data_lookup", lambda q: None
    )
    # Sentinel: weak tier KHÔNG được chạy khi bridge đã trả lời (short-circuit)
    def _sentinel(terms: list[str]) -> tuple[list[dict[str, Any]], str]:
        raise AssertionError("weak catalog tier must NOT run when bridge answers")

    monkeypatch.setattr(qr, "_catalog_candidates", _sentinel)

    result = qr.resolve_lookup_data("What is the population of Bhutan?", domain="geography")

    assert result is not None
    assert result["text"].startswith("Bhutan is a landlocked country")
    assert result["api_name"] == "Countries Now"
    assert result["api_url"] == "https://countriesnow.test/api/v0.1/countries/population"
    assert result["evidence"] == result["text"]
    # Latency budget pin ([OWNER-MANDATE 2026-10-25]): ≤3 candidate, ≤6s,
    # tắt Tier 2/3 trên ask path.
    assert calls["domain"] == "geography"
    assert calls["intent"] == "lookup"
    assert calls["max_candidates"] <= 3
    assert 0 < calls["budget_seconds"] <= 6.0
    assert calls["include_live_fallbacks"] is False


def test_resolve_lookup_data_must_call_bridge_even_on_miss(monkeypatch) -> None:
    """Anti-placebo unwire-check: bridge MISS cũng phải đi QUA bridge (đếm
    call) — nếu ai gỡ lời gọi bridge (dead-code regression), test này đỏ.
    Sau bridge miss, chuỗi fallback cũ chạy nguyên vẹn → None."""
    from scp.runtime import question_router as qr
    import scp.data_sources.free_api_bridge as bridge_mod

    class _CountingBridge:
        def __init__(self) -> None:
            self.calls = 0

        def fetch_for_domain(self, **kwargs: Any) -> None:
            self.calls += 1
            return None

    fake = _CountingBridge()
    monkeypatch.setattr(bridge_mod, "get_bridge", lambda: fake)
    monkeypatch.setattr(
        "scp.runtime.domain_data_fork.resolve_domain_data_lookup", lambda q: None
    )
    monkeypatch.setattr(qr, "_catalog_candidates", lambda terms: ([], ""))
    monkeypatch.setattr(qr, "_wiki_lookup", lambda question, terms: None)  # hermetic guard

    result = qr.resolve_lookup_data("What is the population of Bhutan?", domain="geography")

    assert fake.calls == 1, "resolve_lookup_data PHẢI gọi bridge (chặn dead-code regression)"
    assert result is None


# ---------------------------------------------------------------------------
# (c-1) Anti-placebo: SCP_EGRESS_MODE=deny chặn TRƯỚC fetch (dưới cấp bridge)
# ---------------------------------------------------------------------------
def test_bridge_deny_mode_blocks_catalog_fetch_and_returns_none(monkeypatch, tmp_path) -> None:
    """Invariant bắt buộc: DENY mode thắng scoped grant của catalog — bridge
    trả None, 0 fetch attempt, 0 store. (Ask path dùng include_live_fallbacks
    =False — đúng cấu hình _bridge_lookup; Tier 2/3 có egress gate riêng.)"""
    monkeypatch.setenv("SCP_EGRESS_MODE", "deny")
    monkeypatch.setenv("SCP_EGRESS_ALLOWLIST", "")
    _block_sockets(monkeypatch)
    fetch_calls = _spy_urlopen(monkeypatch)
    bridge, kb = _make_bridge(tmp_path, [dict(CATALOG_ENTRY_NEWS)])

    result = bridge.fetch_for_domain(
        "news", "lookup", "spaceflight news", SPACEFLIGHT_QUESTION,
        include_live_fallbacks=False,
    )

    assert result is None
    assert fetch_calls == [], "DENY phải chặn ở egress dry-check TRƯỚC fetch"
    assert kb.store_calls == []


def test_router_deny_mode_bridge_miss_falls_back_with_reason(monkeypatch, tmp_path) -> None:
    """Router-level: deny → bridge miss (reason được record vào _stats) → weak
    tier cũng bị egress chặn (egress_blocked) → None → LLM fallback path."""
    from scp.runtime import question_router as qr
    import scp.data_sources.free_api_bridge as bridge_mod
    import scp.data_sources.free_api_catalog as cat_mod

    monkeypatch.setenv("SCP_EGRESS_MODE", "deny")
    monkeypatch.setenv("SCP_EGRESS_ALLOWLIST", "")
    _block_sockets(monkeypatch)
    monkeypatch.setattr(url_safety_mod, "safe_urlopen", lambda req, timeout=30, **kw: _FakeResp(b"{}"))

    catalog = _catalog_with(tmp_path, [dict(CATALOG_ENTRY_NEWS)])
    kb = _FakeKnowledge(tmp_path)
    monkeypatch.setattr(bridge_mod, "get_bridge", lambda: FreeAPIAdapterBridge(catalog=catalog, knowledge_store=kb))
    # Weak tier dùng CÙNG catalog fixture — dưới deny mọi host đều bị chặn
    monkeypatch.setattr(cat_mod, "get_catalog", lambda data_dir="data": catalog)
    monkeypatch.setattr(
        "scp.runtime.domain_data_fork.resolve_domain_data_lookup", lambda q: None
    )
    # [WIRE-NEWS 2026-10-25] Hermetic guard: news tier mới trong chuỗi fork —
    # SPACEFLIGHT_QUESTION có news-intent; cô lập khỏi DB thật để test này
    # chỉ pin egress deny của bridge + weak tier.
    monkeypatch.setattr(qr, "news_lookup", lambda *a, **k: None)

    before = qr.route_stats_snapshot()
    result = qr.resolve_lookup_data(SPACEFLIGHT_QUESTION, domain="news")

    assert result is None
    after = qr.route_stats_snapshot()
    new_reasons = [
        key
        for key, count in after["fallback_reasons"].items()
        if before["fallback_reasons"].get(key, 0) < count
    ]
    assert any(k.startswith("bridge_miss:") for k in new_reasons), new_reasons
    assert any(k.startswith("egress_blocked:") for k in new_reasons), new_reasons
    assert kb.store_calls == []


# ---------------------------------------------------------------------------
# (c-2) Anti-placebo: entry private-IP / metadata / loopback không bao giờ fetch
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "bad_url,blocked_before_fetch",
    [
        # Metadata endpoint — Invariant 1 của EgressPolicy, chặn vô điều kiện
        # ở dry-check (TRƯỚC fetch).
        ("https://169.254.169.254/latest/meta-data", True),
        # Private IP — không được grant (_catalog_egress_hosts loại IP literal)
        # → Invariant 4 chặn ở dry-check.
        ("https://10.0.0.5/secret-api", True),
        # Loopback — dry-check cho qua (Invariant 2: microservices nội bộ),
        # safe_urlopen.validate_url(allow_internal=False) chặn TRƯỚC socket.
        ("https://127.0.0.1:8080/local-api", False),
    ],
)
def test_bridge_private_ip_entries_never_fetched(monkeypatch, tmp_path, bad_url, blocked_before_fetch) -> None:
    _pin_allowlist(monkeypatch)
    _block_sockets(monkeypatch)
    # Spy DELEGATE sang safe_urlopen thật: ghi attempt + vẫn chạy validate_url
    # (chặn IP private/loopback trước socket — không bao giờ có mạng thật).
    real_urlopen = url_safety_mod.safe_urlopen
    fetch_calls: list[str] = []

    def _spy(req: Any, timeout: float = 30, **kwargs: Any) -> Any:
        fetch_calls.append(str(getattr(req, "full_url", req)))
        return real_urlopen(req, timeout=timeout, **kwargs)

    monkeypatch.setattr(url_safety_mod, "safe_urlopen", _spy)
    entry = {**CATALOG_ENTRY_NEWS, "url": bad_url}
    bridge, kb = _make_bridge(tmp_path, [entry])

    # Grant không chứa IP literal nào (catalog không phải nguồn tin cậy IP)
    assert bridge._catalog_egress_hosts() == frozenset()

    result = bridge.fetch_for_domain(
        "news", "lookup", "spaceflight news", SPACEFLIGHT_QUESTION,
        include_live_fallbacks=False,
    )

    assert result is None
    assert kb.store_calls == []
    if blocked_before_fetch:
        assert fetch_calls == [], f"{bad_url} phải bị chặn ở egress dry-check"
    else:
        # Loopback: attempt 1 lần rồi bị safe_urlopen chặn tại validate_url
        assert fetch_calls == [bad_url]


# ---------------------------------------------------------------------------
# (c-3) Anti-placebo: term-gate miss → None, không store, không fabrication
# ---------------------------------------------------------------------------
def test_bridge_term_gate_miss_returns_none_without_store(monkeypatch, tmp_path) -> None:
    _pin_allowlist(monkeypatch)
    # Payload KHÔNG chứa salient terms ('latest','spaceflight') của câu hỏi
    monkeypatch.setattr(url_safety_mod, "safe_urlopen", lambda req, timeout=30, **kw: _FakeResp(
        b'{"data": "completely unrelated zebra content"}'
    ))
    bridge, kb = _make_bridge(tmp_path, [dict(CATALOG_ENTRY_NEWS)])

    result = bridge.fetch_for_domain(
        "news", "lookup", "spaceflight news", SPACEFLIGHT_QUESTION,
        include_live_fallbacks=False,
    )

    assert result is None
    assert kb.store_calls == [], "term-gate miss không được store — không fabrication"


# ---------------------------------------------------------------------------
# (d) Latency budget — deadline dừng loop Tier 1, không đốt 15s worst case
# ---------------------------------------------------------------------------
def test_bridge_budget_deadline_stops_remaining_candidates(monkeypatch, tmp_path) -> None:
    """Budget 6s exhausted sau candidate 1 → các candidate còn lại bị bỏ
    (0 fetch), KHÔNG chạm Tier 2/3 (ask path). Dùng time.monotonic giả để
    determinstic (không sleep wall-clock)."""
    _pin_allowlist(monkeypatch)
    # 3 candidate cùng miss term-gate (payload không chứa terms)
    entries = [
        {
            **CATALOG_ENTRY_NEWS,
            "name": f"News API {i}",
            "url": f"https://host{i}.test/api",
        }
        for i in range(3)
    ]
    bridge, kb = _make_bridge(tmp_path, entries)

    fetch_calls: list[tuple[str, float]] = []
    monkeypatch.setattr(
        url_safety_mod,
        "safe_urlopen",
        lambda req, timeout=30, **kw: (
            fetch_calls.append((str(getattr(req, "full_url", req)), timeout)),
            _FakeResp(b'{"data": "unrelated zebra"}'),
        )[1],
    )

    # Kịch bản monotonic: deadline = start + 6.0 = 106.0. Sau fetch đầu tiên
    # đồng hồ nhảy sang 107.0 → remaining < 0 → break (không fetch tiếp).
    ticks = iter([100.0, 100.0, 107.0, 107.0, 107.0])

    import scp.data_sources.free_api_bridge as bridge_mod

    monkeypatch.setattr(bridge_mod.time, "monotonic", lambda: next(ticks, 107.0))

    result = bridge.fetch_for_domain(
        "news", "lookup", "spaceflight news", SPACEFLIGHT_QUESTION,
        max_candidates=3, budget_seconds=6.0, include_live_fallbacks=False,
    )

    assert result is None
    assert len(fetch_calls) == 1, f"budget phải dừng sau fetch đầu, thấy: {fetch_calls}"
    # Timeout fetch đầu được scale theo thời gian còn lại (min(3.0, 6.0) = 3.0)
    assert fetch_calls[0][1] == 3.0
    assert kb.store_calls == []
