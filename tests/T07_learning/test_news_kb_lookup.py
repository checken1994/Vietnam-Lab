# SCP CIRCUIT: WIRE-NEWS — reader tin tức KB (news_fact) nối vào luồng /ask (hermetic).
"""T07/WIRE-NEWS — test [OWNER-MANDATE 2026-10-25]: nối reader cho tin tức ĐÃ
CÓ SẴN trong Knowledge Base vào luồng /ask.

Trước patch: FastLearningEngine học RSS (NYT World / BBC World / VNExpress
the-gioi) và _store_kb(attribute='news_fact') vào bảng knowledge (data/v13.db
qua runtime_path("SCP_DB_PATH", "v13.db")) mỗi ngày nhưng KHÔNG reader nào
kéo "tin hôm nay" — /ask chỉ truy vấn theo terms câu hỏi. Sau patch:
resolve_lookup_data gọi news_lookup SAU domain_data_fork, TRƯỚC _bridge_lookup.

Phủ test:
  (a) Positive: DB tạm (tmp_path — INPUT test, không phải mock product) có
      news_fact rows tươi → answer compose từ record verbatim + provenance
      {'text','api_name','api_url','evidence'}; caller-gate W2-d5 covered.
  (b) Anti-placebo: câu KHÔNG phải tin tức → 0 query DB (đếm qua seam
      module-level _connect — pytest monkeypatch, KHÔNG import mock — FA-04);
      rows stale → None reason stale; rows tươi nhưng terms không cover →
      None không deliver; unwire-check resolve_lookup_data PHẢI gọi news
      tier; DB rỗng/thiếu bảng/file mất → None không raise, không tạo file.
  (c) Tier-order: news tier đứng TRƯỚC bridge (KB local miễn phí mạng).

Không skip/xfail. Tabula Rasa: headline trong fixture là input tổng hợp cho
reader, không phải dữ liệu sự kiện hardcode vào product.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from scp.core.db_manager import _KNOWLEDGE_CANONICAL_DDL
from scp.runtime import news_lookup as nl_mod
from scp.runtime.news_lookup import NEWS_KB_FRESHNESS_HOURS, news_lookup

QUESTION_EN = "What is the latest news about testflight?"
QUESTION_VI = "Tin tức mới nhất về testflight hôm nay?"
DEFAULT_FEED = "https://feeds.bbci.co.uk/news/world/rss.xml"
# Mirror writer _store_kb: source = f'news+ollama:{rss_url}'
# (fastlearningengine.py:721) — fixture phải giống record thật, không tự chế.
DEFAULT_SOURCE = f"news+ollama:{DEFAULT_FEED}"

# Column list KHỚP writer _store_kb (fastlearningengine.py:388) — chống drift.
_INSERT_SQL = (
    "INSERT INTO knowledge"
    " (entity, attribute, value, value_type, confidence, source,"
    "  timestamp, times_verified, last_verified)"
    " VALUES (?, ?, 'verified_true', 'str', 0.6, ?, ?, 1, ?)"
)


class _FakeStats:
    """RouteStats duck-type: record_fallback/record_fetch ghi lại để assert."""

    def __init__(self) -> None:
        self.fallbacks: list[str] = []
        self.fetches: list[bool] = []

    def record_fallback(self, reason: str) -> None:
        self.fallbacks.append(reason)

    def record_fetch(self, ok: bool = True) -> None:
        self.fetches.append(bool(ok))


def _make_kb(tmp_path: Path, rows: list[tuple[str, int]], source: str = DEFAULT_SOURCE) -> Path:
    """KB tạm đúng canonical schema; entity LOWERCASE như writer _store_kb.

    rows: (headline, hours_ago) — giờ âm/dương tính theo datetime.now() thật
    nên freshness window test được mà không cần patch đồng hồ.
    """
    db = tmp_path / "kb.sqlite3"
    conn = sqlite3.connect(db)
    conn.execute(_KNOWLEDGE_CANONICAL_DDL)
    for headline, hours_ago in rows:
        ts = (datetime.now() - timedelta(hours=hours_ago)).isoformat()
        conn.execute(_INSERT_SQL, (headline.lower(), "news_fact", source, ts, ts))
    conn.commit()
    conn.close()
    return db


def _pin_db(monkeypatch: pytest.MonkeyPatch, db: Path) -> None:
    """Reader resolve DB qua runtime_path('SCP_DB_PATH', 'v13.db') mỗi lần gọi
    — pin env là đủ hermetic, không chạm data/v13.db production."""
    monkeypatch.setenv("SCP_DB_PATH", str(db))


# ---------------------------------------------------------------------------
# (a) Positive — compose verbatim từ record + provenance + caller-gate covered
# ---------------------------------------------------------------------------
def test_positive_english_question_composes_relevant_rows_with_provenance(monkeypatch, tmp_path) -> None:
    _pin_db(monkeypatch, _make_kb(tmp_path, [
        ("testflight probe returns safely from orbit", 2),
        ("testflight crew completes first spacewalk", 5),
        ("unrelated zebra migration pattern shifts", 1),
    ]))
    from scp.runtime.router_rules import _terms_covered, extract_salient_terms

    terms = extract_salient_terms(QUESTION_EN)
    stats = _FakeStats()
    result = news_lookup(QUESTION_EN, terms, stats)

    assert result is not None
    # Chỉ rows CÓ content term ('testflight') được compose — row lạc đề bị loại.
    assert "testflight probe returns safely from orbit" in result["text"]
    assert "testflight crew completes first spacewalk" in result["text"]
    assert "zebra" not in result["text"]
    # Provenance contract khớp shape caller attempt_lookup_fork kỳ vọng.
    assert result["api_name"] == "kb_news_fact"
    assert result["api_url"] == DEFAULT_FEED  # feed URL từ record, không bịa
    assert result["evidence"] == result["text"]
    # Mỗi item kèm nguồn + thời gian (bắt buộc theo mandate).
    assert "(nguồn: feeds.bbci.co.uk, lúc " in result["text"]
    # Caller W2-d5 gate (FULL terms gốc) phải covered bởi text — nếu không,
    # attempt_lookup_fork sẽ chặn answer này → tier placebo.
    assert _terms_covered(result["text"], terms) is True
    assert stats.fetches == [True] and stats.fallbacks == []


def test_positive_vietnamese_question_topic_beyond_default_term_limit(monkeypatch, tmp_path) -> None:
    """Câu VI: 4 slot terms đầu bị token generic ('tin','tức','mới','nhất')
    chiếm — content terms phải được re-extract (limit 6) để không fail-closed
    oan; header VI phải chứa đủ token generic cho caller gate."""
    _pin_db(monkeypatch, _make_kb(tmp_path, [("testflight probe returns safely", 3)]))
    from scp.runtime.router_rules import _terms_covered, extract_salient_terms

    terms = extract_salient_terms(QUESTION_VI)
    stats = _FakeStats()
    result = news_lookup(QUESTION_VI, terms, stats)

    assert result is not None, "topic 'testflight' ngoài limit-4 không được làm tier mù"
    assert "Tin tức mới nhất từ kho kiến thức nội bộ" in result["text"]
    assert "testflight probe returns safely" in result["text"]
    assert _terms_covered(result["text"], terms) is True


def test_resolve_lookup_data_answers_news_from_kb_before_bridge(monkeypatch, tmp_path) -> None:
    """Tier-order e2e: news tier TRẢ LỜI trước bridge — bridge sentinel KHÔNG
    được chạy (short-circuit), domain_data_fork bị ép miss để đường đi chắc
    qua news tier."""
    from scp.runtime import question_router as qr

    _pin_db(monkeypatch, _make_kb(tmp_path, [("testflight probe returns safely", 1)]))
    monkeypatch.setattr(
        "scp.runtime.domain_data_fork.resolve_domain_data_lookup", lambda q: None
    )

    def _bridge_sentinel(**kwargs: Any) -> dict[str, Any]:
        raise AssertionError("news tier phải trả lời TRƯỚC bridge khi KB có rows tươi")

    monkeypatch.setattr(qr, "_bridge_lookup", _bridge_sentinel)
    monkeypatch.setattr(qr, "_catalog_candidates", lambda terms: ([], ""))

    result = qr.resolve_lookup_data(QUESTION_EN, domain="general")
    assert result is not None
    assert result["api_name"] == "kb_news_fact"
    assert "testflight probe returns safely" in result["text"]


# ---------------------------------------------------------------------------
# (b-1) Anti-placebo: câu KHÔNG phải tin tức → 0 query DB
# ---------------------------------------------------------------------------
def test_non_news_question_never_touches_db(monkeypatch, tmp_path) -> None:
    _pin_db(monkeypatch, _make_kb(tmp_path, [("testflight probe returns safely", 1)]))
    connects: list[str] = []
    real_connect = nl_mod._connect

    def _counting_connect(db_path: Path) -> sqlite3.Connection:
        connects.append(str(db_path))
        return real_connect(db_path)

    monkeypatch.setattr(nl_mod, "_connect", _counting_connect)
    stats = _FakeStats()

    result = news_lookup("What is the capital of France?", ["capital", "france"], stats)

    assert result is None
    assert connects == [], "câu không news-intent không được đốt bất kỳ query DB nào"
    assert stats.fallbacks == ["news_kb_miss:not_news"]
    assert stats.fetches == []


# ---------------------------------------------------------------------------
# (b-2) Anti-placebo: rows stale (già hơn cửa sổ freshness) → None reason stale
# ---------------------------------------------------------------------------
def test_stale_rows_return_none_with_stale_reason(monkeypatch, tmp_path) -> None:
    _pin_db(monkeypatch, _make_kb(tmp_path, [("testflight probe returns safely", 72)]))
    stats = _FakeStats()

    result = news_lookup(QUESTION_EN, ["latest", "news", "testflight"], stats)

    assert result is None
    assert stats.fallbacks == ["news_kb_miss:stale"]
    assert stats.fetches == []


def test_freshness_window_pinned_48h() -> None:
    """Pin hằng số cửa sổ: 48h = 2 chu kỳ tin ngày (sống sót weekend gap, chặn
    tin tuần trước). Đổi giá trị phải là quyết định thiết kế tường minh."""
    assert NEWS_KB_FRESHNESS_HOURS == 48


def test_mixed_rows_only_fresh_composed(monkeypatch, tmp_path) -> None:
    """1 fresh + 1 stale cùng topic → chỉ fresh được deliver (belt-and-suspenders
    lọc lại bằng datetime thật sau khi SQL lọc theo ISO string)."""
    _pin_db(monkeypatch, _make_kb(tmp_path, [
        ("testflight probe returns safely", 2),
        ("testflight old capsule retired", 96),
    ]))
    stats = _FakeStats()

    result = news_lookup(QUESTION_EN, ["latest", "news", "testflight"], stats)

    assert result is not None
    assert "testflight probe returns safely" in result["text"]
    assert "old capsule retired" not in result["text"]


# ---------------------------------------------------------------------------
# (b-3) Anti-placebo: rows tươi nhưng terms không cover → None, không deliver
# ---------------------------------------------------------------------------
def test_fresh_rows_without_term_coverage_return_none(monkeypatch, tmp_path) -> None:
    """Row tươi nhưng không chứa content term câu hỏi → không deliver gì
    (không phản hồi lệch câu hỏi — cùng tinh thần W2-d5)."""
    _pin_db(monkeypatch, _make_kb(tmp_path, [("zebra migration pattern shifts", 1)]))
    stats = _FakeStats()

    result = news_lookup(QUESTION_EN, ["latest", "news", "testflight"], stats)

    assert result is None
    assert stats.fallbacks == ["news_kb_miss:not_relevant"]
    assert stats.fetches == [], "relevance miss không được record_fetch(ok)"


def test_compose_caps_at_three_items(monkeypatch, tmp_path) -> None:
    """5 rows tươi cùng topic → tối đa 3 items compose (pin latency/size)."""
    rows = [(f"testflight update number {i} published", i + 1) for i in range(5)]
    _pin_db(monkeypatch, _make_kb(tmp_path, rows))
    stats = _FakeStats()

    result = news_lookup(QUESTION_EN, ["latest", "news", "testflight"], stats)

    assert result is not None
    bullets = [ln for ln in result["text"].splitlines() if ln.startswith("- ")]
    assert len(bullets) == 3, f"compose phải cap 3 items, thấy {len(bullets)}"


# ---------------------------------------------------------------------------
# (b-4) Unwire-check — resolve_lookup_data PHẢI gọi news tier (tripwire)
# ---------------------------------------------------------------------------
def test_resolve_lookup_data_must_call_news_tier_even_on_miss(monkeypatch, tmp_path) -> None:
    """Anti-placebo unwire-check: news tier MISS cũng phải ĐƯỢC GỌI (đếm call)
    — nếu ai gỡ lời gọi trong resolve_lookup_data (dead-code regression), test
    này đỏ. Sau miss, chuỗi fallback cũ chạy nguyên vẹn → None."""
    from scp.runtime import question_router as qr

    _pin_db(monkeypatch, _make_kb(tmp_path, [("testflight probe returns safely", 72)]))  # stale
    calls: list[int] = []

    def _counting_news(question: str, terms: list[str], stats: Any) -> None:
        calls.append(1)
        return None

    monkeypatch.setattr(qr, "news_lookup", _counting_news)
    monkeypatch.setattr(
        "scp.runtime.domain_data_fork.resolve_domain_data_lookup", lambda q: None
    )
    monkeypatch.setattr(qr, "_bridge_lookup", lambda **kwargs: None)
    monkeypatch.setattr(qr, "_catalog_candidates", lambda terms: ([], ""))
    monkeypatch.setattr(qr, "_wiki_lookup", lambda question, terms: None)

    result = qr.resolve_lookup_data(QUESTION_EN, domain="general")

    assert calls == [1], "resolve_lookup_data PHẢI gọi news_lookup (chặn dead-code regression)"
    assert result is None


# ---------------------------------------------------------------------------
# (b-5) Fail-closed DB: rỗng / thiếu bảng / file mất / file hỏng
# ---------------------------------------------------------------------------
def test_empty_db_returns_none_no_rows(monkeypatch, tmp_path) -> None:
    db = tmp_path / "empty.sqlite3"
    conn = sqlite3.connect(db)
    conn.execute(_KNOWLEDGE_CANONICAL_DDL)
    conn.commit()
    conn.close()
    _pin_db(monkeypatch, db)
    stats = _FakeStats()

    assert news_lookup(QUESTION_EN, ["latest", "news", "testflight"], stats) is None
    assert stats.fallbacks == ["news_kb_miss:no_rows"]


def test_missing_table_returns_none_db_error(monkeypatch, tmp_path) -> None:
    """DB có file nhưng thiếu bảng knowledge (schema khác) → db_error, không raise."""
    db = tmp_path / "other.sqlite3"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE unrelated (x TEXT)")
    conn.commit()
    conn.close()
    _pin_db(monkeypatch, db)
    stats = _FakeStats()

    assert news_lookup(QUESTION_EN, ["latest", "news", "testflight"], stats) is None
    assert stats.fallbacks == ["news_kb_miss:db_error"]


def test_missing_db_file_returns_none_and_creates_nothing(monkeypatch, tmp_path) -> None:
    """mode=ro: reader KHÔNG được tạo file khi DB chưa tồn tại (side effect = 0)."""
    missing = tmp_path / "missing.sqlite3"
    _pin_db(monkeypatch, missing)
    stats = _FakeStats()

    assert news_lookup(QUESTION_EN, ["latest", "news", "testflight"], stats) is None
    assert stats.fallbacks == ["news_kb_miss:db_error"]
    assert not missing.exists(), "reader mode=ro không được tạo file DB mới"


def test_corrupt_db_file_returns_none_db_error(monkeypatch, tmp_path) -> None:
    db = tmp_path / "corrupt.sqlite3"
    db.write_bytes(b"this is not a sqlite database" * 10)
    _pin_db(monkeypatch, db)
    stats = _FakeStats()

    assert news_lookup(QUESTION_EN, ["latest", "news", "testflight"], stats) is None
    assert stats.fallbacks == ["news_kb_miss:db_error"]
