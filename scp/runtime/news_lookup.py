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

[WAVE26 2026-10-26] Relevance 2 tầng (owner duyệt): row-selection nới ANY
content term (recall tăng — trước đây ALL-terms per-row làm tier gần dead-code
với câu đa chủ đề), sau đó 1 call LLM gateway GỘP lọc các candidate liên quan
câu hỏi (format cố định 'LIÊN_QUAN: 1,3'). Fail-closed: LLM confirm 0 row →
None; LLM lỗi/timeout/rỗng → fallback ALL-terms gate cũ (không tệ hơn hiện
trạng); seam ``_llm_relevance_call`` để test monkeypatch (FA-04).
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
from scp.runtime.router_rules import _terms_covered, detect_language, extract_salient_terms

logger = logging.getLogger("scp.runtime.news_lookup")

# Cửa sổ freshness 48h: RSS nguồn tin ngày, learning cycle adaptive 1-30 phút
# nên headline mới luôn được chèn liên tục; headline tuổi > 48h không còn đại
# diện "tin mới nhất" — trả nó sẽ tái diễn đúng lỗi stale-fact mà W8-e1 chặn
# trên judge path. 48h (thay vì 24h) để sống sót gap cuối tuần mà vẫn chặn
# tin tuần trước. Cột timestamp là ISO local (datetime.now().isoformat() của
# writer) nên so sánh lexicographic với cutoff ISO là đúng.
NEWS_KB_FRESHNESS_HOURS = 48
# [WAVE26 2026-10-26] SELECT mới nhất 8 rows làm CANDIDATE pool (trước: 5) —
# row-selection nới ANY-term (xem (c-1)) cần pool lớn hơn để LLM có gì mà lọc;
# compose vẫn cap NEWS_MAX_ITEMS nên answer không phình.
NEWS_MAX_ROWS = 8
NEWS_MAX_ITEMS = 3
# [WAVE26 2026-10-26] 1 call LLM GỘP tối đa 6 headline (rẻ — 1 request cho cả
# batch, không per-row), đúng mandate owner.
NEWS_LLM_MAX_CANDIDATES = 6

# [OFFLINE-RECENCY] Ngưỡng lệch giờ để nhận diện môi trường offline / clean-clone:
# khi now cách xa MAX(timestamp) của KB > 84h (vượt quá khoảng cách weekend gap 72h),
# lấy MAX(timestamp) làm mốc anchor để câu hỏi tin tức hôm nay / mới nhất tra cứu đúng đợt tin tức mới nhất của KB.
NEWS_KB_OFFLINE_ANCHOR_THRESHOLD_HOURS = 84

# [W8-e1 / LUẬT 14] Phân loại intent thời sự/sự kiện/news ở mức cấu trúc
# theo tín hiệu thời gian + loại-thông-tin (thay cho regex mẫu câu cứng _NEWS_INTENT_*).
_INHERENT_NEWS_TERMS = frozenset({
    "news", "headline", "headlines", "dispatch",
})
_INHERENT_NEWS_VI_PHRASES = (
    "tin tức", "bản tin", "thời sự", "điểm tin", "thông tấn", "tin thế giới",
)
_RECENCY_SIGNALS_RE = re.compile(
    r"\b(mới nhất|mới|gần đây|vừa qua|vừa xong|nóng|today|recently|recent|breaking|just in|now)\b",
    re.IGNORECASE,
)
_CONTEXTUAL_EVENT_TERMS = frozenset({
    "sự kiện", "diễn biến", "tình hình", "bulletin",
})


def _is_news_intent_structural(q: str) -> bool:
    """Kiểm tra intent thời sự/sự kiện/news ở mức cấu trúc (Luật 14).

    Cấu trúc = Kết hợp tín hiệu thời gian (time signal / recency) và loại
    thông tin (information type: tin tức, thời sự, sự kiện, news, headlines...).
    """
    lowered = (q or "").lower()
    has_time = question_has_time_signal(lowered) or bool(_RECENCY_SIGNALS_RE.search(lowered))
    words = set(re.findall(r"\w+", lowered))

    has_inherent_news = bool(
        words.intersection(_INHERENT_NEWS_TERMS)
        or any(phrase in lowered for phrase in _INHERENT_NEWS_VI_PHRASES)
    )
    has_contextual_news = bool(
        has_time and (
            "tin" in words
            or any(term in lowered for term in _CONTEXTUAL_EVENT_TERMS)
        )
    )
    return has_inherent_news or has_contextual_news

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


def _any_term_covered(lowered_text: str, terms: list[str]) -> bool:
    """[WAVE26 2026-10-26] ANY-term match cho candidate selection — đối xứng với
    _terms_covered (ALL) của router_rules. terms đã lowercase (extract_salient_terms
    + rescue đều lowercase); lowered_text do caller lowercase trước khi gọi."""
    return any(term in lowered_text for term in terms)


# [WAVE26 2026-10-26] System prompt ngắn — ép format cố định để parse chắc;
# LLM tự quyết định tin nào liên quan câu hỏi (owner: "LLM lọc câu hỏi và trả
# lời như nào thì phải để SCP làm như vậy").
_LLM_RELEVANCE_SYSTEM_PROMPT = (
    "Bạn là bộ lọc liên quan tin tức. Chỉ trả đúng định dạng được yêu cầu,"
    " không giải thích, không thêm chữ nào khác."
)


def _llm_relevance_call(question: str, headlines: list[str]) -> str | None:
    """[WAVE26 2026-10-26] SEAM gọi LLM gateway lọc relevance — 1 call GỘP cho
    cả batch headline (rẻ hơn per-row).

    Dùng module-level ``chat_sync`` của scp.llm_gateway (đã verify tồn tại tại
    scp/llm_gateway/__init__.py:19; method LLMGateway.chat_sync tại client.py:572
    TỰ XỬ LÝ event-loop an toàn: không loop đang chạy → asyncio.run; có loop
    (resolve_lookup_data gọi trong attempt_lookup_fork async) → offload
    ThreadPoolExecutor + asyncio.run với timeout bounded) — news_lookup là hàm
    sync nên KHÔNG cần tự bọc asyncio.

    Returns:
        str: text LLM trả về để caller parse 'LIÊN_QUAN: ...'.
        None: LLM KHÔNG khả dụng (import lỗi / response rỗng / chat_sync trả
        None) → caller FALLBACK ALL-terms gate cũ. Exception lọt ra ngoài cũng
        do caller bắt thêm một lớp (defense-in-depth).

    Seam để test monkeypatch (FA-04 — không mock import).
    """
    numbered = "\n".join(f"{i}. {h}" for i, h in enumerate(headlines, start=1))
    prompt = (
        f"Câu hỏi: {question}\n"
        "Danh sách tin (đánh số):\n"
        f"{numbered}\n"
        "Trả về MỘT dòng liệt kê số thứ tự của các tin LIÊN QUAN câu hỏi, đúng"
        " dạng 'LIÊN_QUAN: 1,3' (phân tách bằng dấu phẩy). Nếu KHÔNG tin nào"
        " liên quan, trả đúng 'LIÊN_QUAN:'."
    )
    try:
        # Lazy import: tránh đụng gateway (egress guard install) khi module
        # news_lookup được import ở path không cần LLM.
        from scp.llm_gateway import chat_sync
    except Exception as exc:
        logger.warning("[S24][WAVE26] gateway import failed — LLM filter unavailable: %s", exc)
        return None
    response, _model = chat_sync(
        prompt, system_prompt=_LLM_RELEVANCE_SYSTEM_PROMPT, task="default"
    )
    if not str(response or "").strip():
        # chat_sync lỗi tự trả (None, 'none') — rỗng coi như LLM không khả dụng.
        return None
    return str(response)


_LLM_RELEVANCE_LINE_RE = re.compile(
    r"LI[ÊE]N[_\s]?QUAN\s*:\s*([0-9,\s]*)", re.IGNORECASE
)


def _parse_llm_relevance(answer: str, max_index: int) -> list[int]:
    """[WAVE26 2026-10-26] Parse dòng 'LIÊN_QUAN: 1,3,4' của LLM → các index
    1-based HỢP LỆ (≤ max_index). Nhận cả 'LIEN QUAN' không dấu. Parse fail /
    rỗng / token hỏng / out-of-range → bỏ qua; không index hợp lệ nào → []
    (caller coi như 0 tin được xác nhận — fail-closed, không deliver 'còn dư')."""
    match = _LLM_RELEVANCE_LINE_RE.search(str(answer or ""))
    if not match:
        return []
    seen: list[int] = []
    for token in re.split(r"[,\s]+", (match.group(1) or "").strip()):
        if not token:
            continue
        try:
            idx = int(token)
        except ValueError:
            continue
        if 1 <= idx <= max_index and idx not in seen:
            seen.append(idx)
    return seen


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
    # (a) Intent gate TRƯỚC khi chạm DB: cấu trúc thời gian + loại thông tin (Luật 14).
    # Không phải câu tin tức / sự kiện / time-signal → None ngay, 0 query DB.
    if not _is_news_intent_structural(q):
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
        conn = _connect(db_path)
        try:
            cur = conn.cursor()
            # [OFFLINE-RECENCY] Mốc anchor độ mới: lấy MAX(timestamp) của news_fact khi now cách xa KB
            cur.execute("SELECT MAX(timestamp) FROM knowledge WHERE attribute = 'news_fact'")
            max_row = cur.fetchone()
            max_ts_raw = max_row[0] if max_row else None
            max_kb_dt = _parse_ts(max_ts_raw)
            now = datetime.now()
            if max_kb_dt and (now - max_kb_dt).total_seconds() > NEWS_KB_OFFLINE_ANCHOR_THRESHOLD_HOURS * 3600:
                anchor_dt = max_kb_dt
            else:
                anchor_dt = now

            cutoff = (anchor_dt - timedelta(hours=NEWS_KB_FRESHNESS_HOURS)).isoformat()
            # (b) Chỉ rows FRESH trong cửa sổ 48h từ mốc anchor, mới nhất trước.
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
    oldest = anchor_dt - timedelta(hours=NEWS_KB_FRESHNESS_HOURS)
    fresh_rows: list[tuple[str, Any, datetime | None, Any]] = []
    for entity, _value, source, _confidence, timestamp in rows:
        headline = str(entity or "").strip()
        if not headline:
            continue
        dt = _parse_ts(timestamp)
        if dt is None or dt < oldest or dt > anchor_dt + timedelta(hours=1):
            continue
        fresh_rows.append((headline, source, dt, timestamp))

    # [WAVE26 2026-10-26] (c-1) Row-selection NỚI: candidates = rows khớp ANY
    # content term (thay ALL-terms per-row cũ — CHECKER F2: nhiều row liên quan
    # nhưng chỉ 1 deliver, recall thấp). ANY-match chỉ tạo CANDIDATE; quyết
    # định relevance cuối vẫn siết bởi LLM filter bên dưới, không bỏ gate.
    candidates = [
        r for r in fresh_rows if _any_term_covered(r[0].lower(), content_terms)
    ]
    if not candidates:
        stats.record_fallback("news_kb_miss:not_relevant")
        return None

    # [WAVE26 2026-10-26] (c-2) Latency guard: CHỈ gọi LLM khi có ≥2 candidates
    # VÀ ≥1 candidate không pass ALL-terms. Mọi candidates đã pass ALL-terms →
    # deliver như cũ KHÔNG đốt LLM (backward-compat nhanh, 0 LLM call); <2
    # candidates → LLM không đáng 1 call, giữ gate ALL-terms cũ.
    all_covered = all(
        _terms_covered(headline.lower(), content_terms)
        for headline, _source, _dt, _ts in candidates
    )
    if all_covered or len(candidates) < 2:
        relevant = candidates[:NEWS_MAX_ITEMS]
    else:
        # [WAVE26 2026-10-26] (c-3) LLM relevance filter — 1 call GỘP cho tối
        # đa NEWS_LLM_MAX_CANDIDATES headline đánh số; SCP dùng chính LLM
        # gateway để lọc tin liên quan câu hỏi.
        prompt_rows = candidates[:NEWS_LLM_MAX_CANDIDATES]
        try:
            llm_answer = _llm_relevance_call(q, [r[0] for r in prompt_rows])
        except Exception as exc:  # seam raise → fallback, tier không bao giờ raise
            logger.warning(
                "[S24][WAVE26] LLM relevance call failed — fallback ALL-terms: %s", exc
            )
            llm_answer = None
        if llm_answer is None:
            # [WAVE26] FALLBACK khi LLM lỗi (exception/timeout/response rỗng —
            # chat_sync lỗi tự trả (None, 'none')): quay về hành vi cũ ALL-terms
            # gate per-row → KHÔNG TỆ HƠN hiện trạng trước WAVE26.
            relevant = [
                r for r in candidates
                if _terms_covered(r[0].lower(), content_terms)
            ][:NEWS_MAX_ITEMS]
        else:
            confirmed_idx = _parse_llm_relevance(llm_answer, len(prompt_rows))
            # [WAVE26] LLM trả lời nhưng 0 tin được xác nhận (parse fail / rỗng /
            # từ chối hết) → fail-closed: KHÔNG deliver row nào chỉ vì "còn dư".
            if not confirmed_idx:
                stats.record_fallback("news_kb_miss:not_relevant")
                return None
            picked = set(confirmed_idx)
            relevant = [
                r for i, r in enumerate(prompt_rows, start=1) if i in picked
            ][:NEWS_MAX_ITEMS]
    if not relevant:
        # Fallback ALL-terms cũng không còn row ALL-match → như cũ, không deliver.
        stats.record_fallback("news_kb_miss:not_relevant")
        return None

    # (d) Compose MỖI item kèm nguồn + thời gian lấy từ record — verbatim,
    # không thêm nhận định. timestamp hiển thị định dạng YYYY-MM-DD HH:MM.
    # Header là meta-statement ĐÚNG THẬT về nguồn + cửa sổ (không fabrication
    # sự kiện) và cần thiết để chain gate W2-d5 của caller (_terms_covered trên
    # FULL terms gốc, gồm token 'latest'/'news' định tuyến) còn hiệu lực —
    # không có header thì mọi answer đều bị caller gate chặn → tier placebo.
    if detect_language(q) == "vi":
        header = (
            f"Tin tức mới nhất từ kho kiến thức nội bộ hôm nay"
            f" (cửa sổ {NEWS_KB_FRESHNESS_HOURS}h):"
        )
    else:
        header = (
            f"Latest news from the internal knowledge base today"
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
        "reader_tier": "kb_news_fact",
        "source_indicator": "KB:news_fact",
    }


__all__ = ["NEWS_KB_FRESHNESS_HOURS", "news_lookup"]
