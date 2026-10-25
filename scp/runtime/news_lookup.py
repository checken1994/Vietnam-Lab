"""[WIRE-NEWS 2026-10-25] Tier tin tức KB cho lookup fork — tách khỏi question_router.

[OWNER-MANDATE 2026-10-25] Nối reader cho tin tức ĐÃ CÓ SẴN trong Knowledge
Base vào luồng /ask: FastLearningEngine (news_learning_cycle — RSS NYT World /
BBC World / VNExpress the-gioi, qua semantic firewall + verify Ollama) lưu
headline vào bảng ``knowledge`` với attribute='news_fact' mỗi ngày
(fastlearningengine.py:721 — entity=headline[:200].lower(), value='verified_true',
source='news+ollama:{rss_url}', timestamp=ISO local), nhưng trước wire này
KHÔNG có reader nào kéo "tin hôm nay" — các tier FTS của /ask chỉ truy vấn
theo terms của câu hỏi.

Egress: tier này CHỈ ĐỌC SQLite local (mode=ro — không socket, không fetch
mạng mới). RSS fetching vẫn thuộc FastLearningEngine bên trong learning
thread như cũ — không thay đổi chính sách egress.

Fail-closed toàn bộ nhánh: câu không phải tin tức / DB thiếu-lỗi / 0 rows
trong cửa sổ freshness / terms không cover → None → chuỗi fallback cũ
(bridge → weak catalog → wiki → LLM) chạy nguyên vẹn. KHÔNG bịa nội dung
ngoài record (Tabula Rasa — headline hiển thị verbatim từ entity column).

stats được truyền vào từ caller (duck-type ``record_fallback(reason)`` /
``record_fetch(ok=bool)`` của RouteStats) — KHÔNG late-import ngược
question_router để tránh circular import.
"""
from __future__ import annotations

import logging
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from scp.core.runtime_paths import runtime_path
# [W8-e1 reuse] Time-signal detector dùng đúng của judge (một nguồn sự thật),
# không circular: judge_abstain không import tier lookup nào.
from scp.runtime.judge_abstain import question_has_time_signal
from scp.runtime.router_rules import _terms_covered, extract_salient_terms

logger = logging.getLogger("scp.runtime.news_lookup")

# Cửa sổ freshness 48h: RSS nguồn tin ngày, learning cycle adaptive 1-30 phút
# nên headline mới luôn được chèn liên tục; headline tuổi > 48h không còn đại
# diện "tin mới nhất" — trả nó sẽ tái diễn đúng lỗi stale-fact mà W8-e1 chặn
# trên judge path. 48h (thay vì 24h) để sống sót gap cuối tuần mà vẫn chặn
# tin tuần trước. Cột timestamp là ISO local (datetime.now().isoformat() của
# writer) nên so sánh lexicographic với cutoff ISO là đúng.
NEWS_KB_FRESHNESS_HOURS = 48
# SELECT mới nhất 5 rows, compose tối đa 3 items — đủ khung "tin hôm nay"
# mà không phình answer (provenance suffix + relevance gate vẫn chạy phía sau).
NEWS_MAX_ROWS = 5
NEWS_MAX_ITEMS = 3

# [W8-e1 reuse] Time-signal dùng đúng detector sẵn có của judge (một nguồn
# sự thật: hiện tại|hiện nay|hôm nay|bây giờ|currently|latest) + từ khóa
# news-intent tách EN/VI để header answer đúng ngôn ngữ câu hỏi. 'tin' đơn lẻ
# quá rộng (tin nhắn, tin đồn...) — chỉ nhận cụm rõ.
_NEWS_INTENT_EN_RE = re.compile(r"\b(news|headlines?|breaking)\b", re.IGNORECASE)
_NEWS_INTENT_VI_RE = re.compile(
    r"\b(tin tức|tin nóng|tin mới|bản tin|tin thế giới)\b", re.IGNORECASE
)

# Token định TUYẾN (news-intent/time-signal) — có trong terms của gần MỌI câu
# hỏi tin tức nhưng gần như KHÔNG bao giờ nằm trong headline (smoke test thật
# bắt được: gate per-row với terms thô làm tier thành dead code — placebo).
# Relevance gate per-row chỉ dùng CONTENT terms còn lại; hết content terms →
# fail-closed None (không discriminating term = không kiểm chứng được relevance).
# LƯU Ý: chỉ lọc token định tuyến — KHÔNG lọc token chủ đề ('thế giới', tên
# nước, sự kiện...) để gate vẫn siết theo nội dung câu hỏi.
_GENERIC_NEWS_TOKENS = frozenset(
    """news headlines headline breaking latest currently today
    tin tức bản nóng mới nhất hôm bây hiện nay giờ""".split()
)

# [WAVE25 2026-10-26] Rescue topic-term ngắn cho news intent: 'ai' trùng đại từ
# hỏi VI ("ai"=who) trong stopword chung của extract_salient_terms, 'uk'/'us'
# là địa danh ngắn — bị drop làm tier news MÙ với chủ đề đó (probe thật
# CHECKER: "latest news about AI" → None not_relevant vì content terms rỗng).
# Khi câu ĐÃ qua news-intent gate phía trên, token này là chủ đề thực
# (AI/Anh/Mỹ) — được cứu về content terms bằng word-boundary regex trên câu
# hỏi thô. KHÔNG đụng _STOPWORDS chung của router_rules/free_api_bridge (trong
# câu hỏi thường 'ai' vẫn phải là đại từ hỏi — sửa chung ảnh hưởng toàn hệ).
_NEWS_TOPIC_RESCUE = frozenset({"ai", "uk", "us"})


def _rescue_topic_terms(question: str) -> list[str]:
    """[WAVE25 2026-10-26] Topic-term rescue CÓ MẶT trong câu hỏi — word-boundary
    regex: 'ai' trong 'train'/'said' KHÔNG match, 'AI'/'ai'/'uk'/'us' đứng riêng
    (kể cả trước dấu câu) mới match. Sorted để determinstic; CHỈ gọi SAU khi
    news-intent gate đã pass (nếu không sẽ mở cửa cho câu hỏi thường)."""
    lowered = (question or "").lower()
    return sorted(w for w in _NEWS_TOPIC_RESCUE if re.search(rf"\b{w}\b", lowered))

# [FA-04 / seam] Connection factory module-level: reader mở SQLite mode=ro
# (KHÔNG bao giờ tạo file mới, KHÔNG ghi) — và là seam để test anti-placebo
# đếm số lần chạm DB (monkeypatch _connect — pytest fixture, không mock import).
# mode=ro chọn thay vì db_manager.db_query_all vì reader không được có side
# effect tạo file khi DB chưa tồn tại (sqlite3.connect thường sẽ tạo).
def _connect(db_path: Path) -> sqlite3.Connection:
    uri = db_path.resolve().as_uri() + "?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=2.0)


def _kb_db_path() -> Path:
    """Cùng resolution với writer: helpers.py start_fast_learning_thread truyền
    ``runtime_path("SCP_DB_PATH", "v13.db")`` cho FastLearningEngine.scp_db_path
    — reader phải đọc đúng DB đó (env component path thắng data root)."""
    return runtime_path("SCP_DB_PATH", "v13.db")


def _parse_ts(raw: Any) -> datetime | None:
    """Parse timestamp ISO của writer; fail-closed → None (row bị skip)."""
    try:
        dt = datetime.fromisoformat(str(raw or "").strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is not None:  # tránh TypeError naive-vs-aware khi so sánh
        dt = dt.astimezone().replace(tzinfo=None)
    return dt


def _feed_url_from_source(source: Any) -> str:
    """Record chỉ chứa URL FEED RSS (source='news+ollama:{rss_url}'), không có
    URL bài báo — provenance api_url dùng feed URL lấy từ record, KHÔNG bịa
    URL bài. Không parse được → rỗng (caller vẫn còn api_name)."""
    text = str(source or "").strip()
    if "news+ollama:" in text:
        candidate = text.split("news+ollama:", 1)[1].strip()
        parsed = urlparse(candidate)
        if parsed.scheme in ("http", "https") and parsed.hostname:
            return candidate
    return ""


def _feed_label(source: Any) -> str:
    """Nhãn nguồn ngắn gọn cho answer text (hostname của feed, không fabrication)."""
    url = _feed_url_from_source(source)
    if url:
        return urlparse(url).hostname or "rss-feed"
    text = str(source or "").strip()
    return text[:60] if text else "kb_news_fact"


def news_lookup(question: str, terms: list[str], stats: Any) -> dict[str, Any] | None:
    """[WIRE-NEWS 2026-10-25] Tier news_fact từ Knowledge Base cho lookup fork.

    Args:
        question: câu hỏi /ask gốc.
        terms: salient terms đã extract ở caller (relevance gate dùng đúng
            pattern _terms_covered của W2-d5).
        stats: RouteStats-like counter (record_fallback / record_fetch).

    Returns:
        {'text','api_name','api_url','evidence'} hoặc None (fail-closed).
    """
    q = str(question or "")
    # (a) Intent gate TRƯỚC khi chạm DB: câu không có time-signal/news-keyword
    # → None ngay, 0 query — không đốt I/O cho câu định nghĩa/thủ đô/conversion.
    if not (
        _NEWS_INTENT_EN_RE.search(q)
        or _NEWS_INTENT_VI_RE.search(q)
        or question_has_time_signal(q)
    ):
        stats.record_fallback("news_kb_miss:not_news")
        return None
    # Content terms = terms câu hỏi trừ token định tuyến (xem _GENERIC_NEWS_TOKENS)
    # — gate per-row so với token NỘI DUNG, không phải token routing. Caller
    # extract_salient_terms default limit=4 dễ cắt mất topic term của câu VI
    # ('Tin tức mới nhất về X' → 4 slot đầu bị token generic chiếm) → re-extract
    # limit 6 trước khi kết luận hết content terms; vẫn empty → fail-closed.
    # [WAVE25 2026-10-26] Rescue union (điểm này chỉ tới khi news_intent=True
    # — return phía trên đảm bảo): union cả khi terms caller còn content khác
    # ('latest news about AI policy' cần cả 'policy' lẫn 'ai' ở gate per-row).
    rescued = _rescue_topic_terms(q)
    content_terms = [t for t in (terms or []) if str(t).lower() not in _GENERIC_NEWS_TOKENS]
    if rescued:
        known = {str(t).lower() for t in content_terms}
        content_terms = content_terms + [w for w in rescued if w not in known]
    if not content_terms:
        content_terms = [
            t for t in extract_salient_terms(q, limit=6)
            if t.lower() not in _GENERIC_NEWS_TOKENS
        ]
    if not content_terms:
        # Không còn token nội dung để kiểm chứng relevance → fail-closed.
        stats.record_fallback("news_kb_miss:not_relevant")
        return None
    try:
        db_path = _kb_db_path()
        cutoff = (datetime.now() - timedelta(hours=NEWS_KB_FRESHNESS_HOURS)).isoformat()
        conn = _connect(db_path)
        try:
            cur = conn.cursor()
            # (b) Chỉ rows FRESH trong cửa sổ 48h, mới nhất trước.
            cur.execute(
                "SELECT entity, value, source, confidence, timestamp"
                " FROM knowledge WHERE attribute = 'news_fact' AND timestamp >= ?"
                f" ORDER BY timestamp DESC LIMIT {int(NEWS_MAX_ROWS)}",
                (cutoff,),
            )
            rows = cur.fetchall()
            if not rows:
                # Phân biệt no_rows (KB trống/chưa từng chạy news learning) vs
                # stale (có rows nhưng già hơn cửa sổ) — reason cho audit.
                cur.execute(
                    "SELECT COUNT(*) FROM knowledge WHERE attribute = 'news_fact'"
                )
                total = int(cur.fetchone()[0] or 0)
                stats.record_fallback("news_kb_miss:stale" if total else "news_kb_miss:no_rows")
                return None
        finally:
            conn.close()
    except sqlite3.Error as exc:
        # DB thiếu / thiếu bảng / busy → fail-closed None, không raise.
        logger.warning("[S24][WIRE-NEWS] news KB read failed: %s", exc)
        stats.record_fallback("news_kb_miss:db_error")
        return None
    except OSError as exc:  # path resolve / as_uri lỗi môi trường
        logger.warning("[S24][WIRE-NEWS] news KB path failed: %s", exc)
        stats.record_fallback("news_kb_miss:db_error")
        return None

    # (c) Belt-and-suspenders freshness + parse: SQL lọc theo ISO string, đây
    # lọc lại bằng datetime thật — row unparseable/out-of-window bị skip.
    now = datetime.now()
    oldest = now - timedelta(hours=NEWS_KB_FRESHNESS_HOURS)
    relevant: list[tuple[str, Any, datetime | None, Any]] = []
    for entity, _value, source, _confidence, timestamp in rows:
        headline = str(entity or "").strip()
        if not headline:
            continue
        dt = _parse_ts(timestamp)
        if dt is None or dt < oldest or dt > now:
            continue
        # Relevance gate (W2-d5 pattern): CONTENT terms câu hỏi phải cover
        # trong headline của row — value chỉ là marker 'verified_true', không
        # phải nội dung. Không row nào cover → không deliver gì.
        if _terms_covered(headline.lower(), content_terms):
            relevant.append((headline, source, dt, timestamp))
        if len(relevant) >= NEWS_MAX_ITEMS:
            break
    if not relevant:
        stats.record_fallback("news_kb_miss:not_relevant")
        return None

    # (d) Compose MỖI item kèm nguồn + thời gian lấy từ record — verbatim,
    # không thêm nhận định. timestamp hiển thị định dạng YYYY-MM-DD HH:MM.
    # Header là meta-statement ĐÚNG THẬT về nguồn + cửa sổ (không fabrication
    # sự kiện) và cần thiết để chain gate W2-d5 của caller (_terms_covered trên
    # FULL terms gốc, gồm token 'latest'/'news' định tuyến) còn hiệu lực —
    # không có header thì mọi answer đều bị caller gate chặn → tier placebo.
    if _NEWS_INTENT_VI_RE.search(q):
        header = (
            f"Tin tức mới nhất từ kho kiến thức nội bộ"
            f" (cửa sổ {NEWS_KB_FRESHNESS_HOURS}h):"
        )
    else:
        header = (
            f"Latest news from the internal knowledge base"
            f" (within {NEWS_KB_FRESHNESS_HOURS}h):"
        )
    lines: list[str] = [header]
    for headline, source, dt, timestamp in relevant:
        ts_display = dt.strftime("%Y-%m-%d %H:%M") if dt else str(timestamp or "")[:16].replace("T", " ")
        lines.append(f"- {headline} (nguồn: {_feed_label(source)}, lúc {ts_display})")
    text = "\n".join(lines)
    stats.record_fetch(ok=True)
    return {
        "text": text,
        "api_name": "kb_news_fact",
        # Feed URL của item mới nhất (top relevant) — nếu có trong record.
        "api_url": _feed_url_from_source(relevant[0][1]),
        "evidence": text,
    }


__all__ = ["NEWS_KB_FRESHNESS_HOURS", "news_lookup"]
