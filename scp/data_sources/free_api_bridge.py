"""
FreeAPIAdapterBridge — Cầu nối chuyển đổi động giữa 60 Domain Data Sources
và Kho danh bạ 2,175+ Free Public APIs (live measured 2026-10-25; catalog varies; kèm Multi-Tier Fallback & SQLite WAL Cache).

Kiến trúc đa tầng (Multi-Tier Retrieval Pipeline):
  - Tier 0: SQLite WAL Cache (<1.5ms) tại `data/knowledge.sqlite3` (`knowledge_records` table)
  - Tier 1: Public APIs trong Free API Catalog (>2,175 APIs — live measured 2026-10-25; catalog varies, 51 categories, auth="No" ưu tiên)
  - Tier 2: Safe Fallback: `LiveKnowledgeFetcher.fetch_live()` (Wikipedia, Wikidata, arXiv, DuckDuckGo)
  - Tier 3: Canonical Wikipedia Client: `wikipedia_client.search_then_summary()`
  - Fail-Closed: Trả về None / found=False khi mọi tầng đều không có dữ liệu thực tế (FA-04).

Bảo toàn 100% Tabula Rasa: Không nhồi nhét bất kỳ từ điển sự thật tĩnh nào trong mã nguồn Python.
"""
from __future__ import annotations

import ipaddress
import json
import logging
import re
import sqlite3
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scp.core import wikipedia_client
from scp.data_sources import live_knowledge
from scp.data_sources.bridge_quality import is_docs_landing_page
from scp.data_sources.free_api_catalog import FreeAPICatalog, get_catalog
from scp.data_sources.live_knowledge import fetch_live
from scp.interfaces.data_source import shared_health_ping
from scp.knowledge.domain_knowledge import DomainKnowledge
from scp.security import url_safety
from scp.security.url_safety import enforce_egress_policy, safe_urlopen

# LiveKnowledgeFetcher interface / alias
class LiveKnowledgeFetcher:
    """LiveKnowledgeFetcher wrapper for multi-source knowledge retrieval."""

    @staticmethod
    def fetch_live(query: str, domain: str = "", force_refresh: bool = False) -> dict[str, Any] | None:
        return fetch_live(query, domain=domain, force_refresh=force_refresh)

logger = logging.getLogger("scp.data_sources.free_api_bridge")

# ---------------------------------------------------------------------------
# Bảng ánh xạ động 60 Domains -> Catalog Categories (51 categories)
# ---------------------------------------------------------------------------
DOMAIN_TO_CATALOG_CATEGORIES: dict[str, list[str]] = {
    "aerospace": ["Science & Math", "Transportation"],
    "agriculture": ["Environment", "Open Data"],
    "alphavantage": ["Finance", "Currency Exchange"],
    "architecture": ["Art & Design", "Open Data"],
    "arts": ["Art & Design"],
    "astronomy": ["Science & Math"],
    "audiovideo": ["Video", "Music"],
    "biology": ["Animals", "Science & Math", "Health"],
    "blockchain": ["Blockchain", "Cryptocurrency"],
    "cartography": ["Geocoding"],
    "chemistry": ["Science & Math"],
    "cornell_lii": ["Government", "Documents & Productivity"],
    "courtlistener": ["Government", "Documents & Productivity"],
    "crafts": ["Art & Design"],
    "cybersecurity": ["Security", "Anti-Malware"],
    "digitalmarketing": ["Business", "Social"],
    "diplomacy": ["Government"],
    "dtic": ["Government", "Security"],
    "ecommerce": ["Shopping", "Business"],
    "education": ["Books", "Documents & Productivity", "Open Data"],
    "energy": ["Environment", "Open Data"],
    "environment": ["Environment"],
    "eric": ["Books", "Documents & Productivity"],
    "finance": ["Finance", "Currency Exchange", "Open Data"],
    "foodtech": ["Food & Drink"],
    "fred": ["Finance", "Currency Exchange"],
    "genai": ["Machine Learning", "Development"],
    "geography": ["Geocoding", "Open Data"],
    "geology": ["Science & Math", "Open Data"],
    "glottolog": ["Dictionaries", "Open Data"],
    "google_factcheck": ["Open Data", "Text Analysis"],
    "gutenberg": ["Books"],
    "heritage": ["Open Data", "Government", "Art & Design"],
    "history": ["Open Data", "Government"],
    "legal": ["Government", "Documents & Productivity"],
    "logic": ["Science & Math"],
    "math": ["Science & Math"],
    "medical": ["Health"],
    "metmuseum": ["Art & Design"],
    "military": ["Government", "Security"],
    # [WIRE-BRIDGE 2026-10-25] Ánh xạ category cho domain 'news' (không phải
    # dữ liệu sự kiện — không vi phạm Tabula Rasa). Catalog News có 26 entries,
    # trong đó 7 keyless có thể fetch trực tiếp khi ask path đạt term gate.
    "news": ["News"],
    "newsapi": ["News"],
    "noaa": ["Weather", "Environment"],
    "oceanography": ["Environment", "Science & Math"],
    "physics": ["Science & Math"],
    "psychology": ["Personality", "Health"],
    "reality": ["Open Data", "Science & Math"],
    "religion": ["Books", "Open Data"],
    "social": ["Social", "Personality"],
    "spacemedicine": ["Health", "Science & Math"],
    "sports": ["Sports & Fitness"],
    "statistics": ["Science & Math", "Open Data"],
    "technology": ["Development", "Programming", "Machine Learning"],
    "tourism": ["Transportation", "Geocoding"],
    "transport": ["Transportation", "Vehicle"],
    "undata": ["Open Data", "Government"],
    "unesco": ["Open Data", "Government", "Art & Design"],
    "usgs": ["Science & Math", "Environment"],
    "uxui": ["Art & Design", "Development"],
    "wikiart": ["Art & Design"],
    "worldbank": ["Finance", "Open Data"],
}

_STOPWORDS = frozenset(
    """a an the is are was were of in on at to for from by with about what who
    where when which how many much far fast tall deep long does do did can
    could should would will was there their this that these those it its as
    tell me please give find show explain list get describe provide between
    and or nor but why so than also into onto over under above below across
    through during before after since until against among within without
    because while both either neither
    và là của có ở được bằng bao nhiêu gì ai đâu nào khi năm the cho với vào
    trong một các này đó thì về trên tại theo nếu những
    tôi biết hay hoặc giữa hãy giúp bạn mình chúng ta họ thế sao
    thế_nào có_thể xin vui_lòng xem chỉ qua đến từ lại ra đây rất nhiều
    cũng đã sẽ đang""".split()
)


def extract_salient_terms(question: str, limit: int = 4) -> list[str]:
    """Trích xuất từ khóa nổi bật của câu hỏi (bỏ stopwords/interrogatives)."""
    tokens = re.findall(r"[\wÀ-ỹ][\wÀ-ỹ\-']*", (question or "").lower())
    terms: list[str] = []
    for token in tokens:
        if token in _STOPWORDS or len(token) < 2 or token.isdigit():
            continue
        if token not in terms:
            terms.append(token)
        if len(terms) >= limit:
            break
    return terms


def _terms_covered(text: str, terms: list[str]) -> bool:
    """Quality gate: dữ liệu trích xuất phải CHỨA các từ khóa nổi bật của câu hỏi."""
    lowered = (text or "").lower()
    return bool(terms) and all(term in lowered for term in terms)


def _extract_readable(raw: str) -> str | None:
    """Trích xuất chuỗi văn bản sạch từ JSON hoặc HTML, bảo toàn key và value ngắn."""
    raw = raw.strip()
    if not raw:
        return None
    if raw[0] in "[{":
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            data = None
        if data is not None:
            parts: list[str] = []

            def _walk(node: Any, depth: int = 0) -> None:
                if depth > 5 or len(parts) >= 50:
                    return
                if isinstance(node, dict):
                    for key, value in node.items():
                        k_str = str(key).strip()
                        if isinstance(value, (str, int, float, bool)):
                            v_str = str(value).strip()
                            if v_str:
                                if k_str and not k_str.isdigit():
                                    parts.append(f"{k_str}: {v_str}")
                                else:
                                    parts.append(v_str)
                        else:
                            _walk(value, depth + 1)
                elif isinstance(node, list):
                    for item in node[:20]:
                        if isinstance(item, (str, int, float, bool)):
                            i_str = str(item).strip()
                            if i_str:
                                parts.append(i_str)
                        else:
                            _walk(item, depth + 1)
                elif isinstance(node, (str, int, float, bool)):
                    val_str = str(node).strip()
                    if val_str:
                        parts.append(val_str)

            _walk(data)
            text = " | ".join(parts)
            return text[:4000] if text else None
    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", raw, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:4000] or None


class FreeAPIAdapterBridge:
    """Cầu nối chuyển đổi động kết nối 60 chuyên ngành vào hơn 2,175 Public APIs (live measured 2026-10-25; catalog varies)."""

    def __init__(
        self,
        catalog: FreeAPICatalog | None = None,
        knowledge_store: DomainKnowledge | None = None,
    ) -> None:
        self.catalog = catalog or get_catalog()
        self.knowledge = knowledge_store or DomainKnowledge()
        self._cache: dict[str, Any] = {}
        self._lock = threading.Lock()
        self._local = threading.local()

    def _get_sqlite_conn(self, sqlite_file: Path) -> sqlite3.Connection:
        """Lấy hoặc khởi tạo kết nối SQLite thread-local tái sử dụng để đạt SLA <1.5ms."""
        conn = getattr(self._local, "conn", None)
        conn_path = getattr(self._local, "conn_path", None)
        if conn is None or conn_path != str(sqlite_file):
            conn = sqlite3.connect(sqlite_file, timeout=1.5)
            conn.row_factory = sqlite3.Row
            try:
                conn.execute("CREATE INDEX IF NOT EXISTS idx_kr_question_lower ON knowledge_records(lower(question));")
            except Exception as exc:
                logger.debug("[FreeAPIBridge] Could not create idx_kr_question_lower: %s", exc)
            self._local.conn = conn
            self._local.conn_path = str(sqlite_file)
        return conn

    def close(self) -> None:
        """Đóng kết nối SQLite thread-local nếu có."""
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            try:
                conn.close()
            except Exception as exc:
                logger.debug("[FreeAPIBridge] Error closing thread-local SQLite conn: %s", exc)
            self._local.conn = None

    def get_apis_for_domain(self, domain: str) -> list[dict[str, Any]]:
        """Lọc và sắp xếp các API trong catalog theo domain.

        Ưu tiên các API:
        1. auth == "No" (sử dụng được ngay không cần đăng ký API key)
        2. https == "Yes" (bảo đảm an toàn truyền dẫn)
        3. Loại bỏ hoàn toàn các feed đã bị phế truất (deprecated=True).
        """
        domain_key = (domain or "").strip().lower()
        categories = set(DOMAIN_TO_CATALOG_CATEGORIES.get(domain_key, []))

        # Fallback nếu domain truyền vào chính là tên danh mục catalog
        if not categories:
            for entry in self.catalog.entries():
                if entry.get("category", "").lower() == domain_key:
                    categories.add(entry["category"])
                    break

        if not categories:
            return []

        matched: list[dict[str, Any]] = []
        for entry in self.catalog.entries():
            if entry.get("deprecated"):
                continue
            if entry.get("category") in categories:
                matched.append(entry)

        # Sắp xếp ưu tiên auth="No", https="Yes"
        def _priority_score(e: dict[str, Any]) -> tuple[int, int, str]:
            auth_val = str(e.get("auth", "")).strip().lower()
            auth_rank = 0 if auth_val == "no" else 1
            https_val = str(e.get("https", "")).strip("'").strip().lower()
            https_rank = 0 if https_val == "yes" else 1
            return (auth_rank, https_rank, str(e.get("name", "")))

        matched.sort(key=_priority_score)
        return matched

    def fetch_for_domain(
        self,
        domain: str,
        intent: str,
        entity: str,
        question: str = "",
        *,
        max_candidates: int = 5,
        budget_seconds: float | None = None,
        include_live_fallbacks: bool = True,
    ) -> dict[str, Any] | None:
        """Tra cứu tri thức chuyên ngành theo mô hình đa tầng:

        Tier 0: Kiểm tra cache SQLite WAL (`data/knowledge.sqlite3`) -> <1.5ms
        Tier 1: Tra cứu Public APIs trong Catalog khớp với domain -> Term Relevance Gate
        Tier 2 Fallback: `LiveKnowledgeFetcher.fetch_live()` với Wikipedia/Wikidata/arXiv cross-check
        Tier 3 Fallback: Canonical `wikipedia_client.search_then_summary()`
        Fail-Closed: Trả về None khi không có dữ liệu thực tế (FA-04).

        [WIRE-BRIDGE 2026-10-25] Tham số mới (keyword-only, mặc định giữ nguyên
        hành vi cũ cho mọi caller hiện hữu):
          - max_candidates: giới hạn số candidate API Tier 1 (mặc định 5).
          - budget_seconds: deadline tổng (monotonic) — loop Tier 1 dừng khi
            hết giờ (timeout từng fetch được scale theo thời gian còn lại) và
            Tier 2/3 bị bỏ qua nếu đã hết ngân sách.
          - include_live_fallbacks=False: chạy CHỈ Tier 0+1 — dùng cho ask
            path nơi Tier 2/3 trùng lặp với chuỗi fallback có sẵn phía sau
            (weak catalog tier → _wiki_lookup → LLM) trong question_router.
        """
        q = (question or "").strip()
        if not q:
            q = (entity or "").strip()
        if not q:
            return None

        start = time.monotonic()
        deadline = start + budget_seconds if budget_seconds is not None else None
        terms = extract_salient_terms(q)

        # ------------------------------------------------------------------
        # Tier 0: SQLite WAL Cache Lookup (<1.5ms)
        # ------------------------------------------------------------------
        tier0_result = self._lookup_wal_cache(q, domain)
        if tier0_result is not None:
            return tier0_result

        # ------------------------------------------------------------------
        # Tier 1: Catalog Public API Execution
        # ------------------------------------------------------------------
        tier1_result = self._query_catalog_apis(
            domain, q, terms, max_candidates=max_candidates, deadline=deadline
        )
        if tier1_result is not None:
            return tier1_result

        # [WIRE-BRIDGE 2026-10-25] Ask path tắt Tier 2/3: không fetch kép
        # wikipedia/live-knowledge khi miss — chuỗi fallback cũ phía sau
        # (weak tier → wiki → LLM) đảm nhiệm phần đó.
        if not include_live_fallbacks:
            return None

        if deadline is not None and time.monotonic() > deadline:
            logger.debug(
                "[FreeAPIBridge] budget %.2fs exhausted — skip live fallback tiers",
                budget_seconds or 0.0,
            )
            return None

        # ------------------------------------------------------------------
        # Tier 2 Fallback: LiveKnowledgeFetcher (Multi-source RAG)
        # ------------------------------------------------------------------
        tier2_result = self._fallback_live_knowledge(domain, q)
        if tier2_result is not None:
            return tier2_result

        # ------------------------------------------------------------------
        # Tier 3 Fallback: Canonical Wikipedia Client
        # ------------------------------------------------------------------
        tier3_result = self._fallback_wikipedia(domain, q)
        if tier3_result is not None:
            return tier3_result

        # Fail-closed: Never fabricate or simulate fake facts (FA-04)
        return None

    def _lookup_wal_cache(self, question: str, domain: str) -> dict[str, Any] | None:
        """Tier 0: Tìm kiếm trong bảng knowledge_records (SQLite WAL mode, SLA <1.5ms)."""
        sqlite_file = getattr(self.knowledge, "sqlite_path", None) or Path("data/knowledge.sqlite3")
        if not sqlite_file.exists():
            return None

        try:
            now = time.time()
            clean_q = question.strip().rstrip("?.!")
            conn = self._get_sqlite_conn(sqlite_file)

            # 1. Exact indexed seek trước bằng idx_kr_question (tốc độ ~0.01ms)
            row = conn.execute(
                """
                SELECT * FROM knowledge_records
                WHERE question = ?
                  AND (expires_at = 0 OR expires_at IS NULL OR expires_at > ?)
                ORDER BY confidence DESC LIMIT 1
                """,
                (clean_q, now),
            ).fetchone()

            if not row and clean_q != question:
                row = conn.execute(
                    """
                    SELECT * FROM knowledge_records
                    WHERE question = ?
                      AND (expires_at = 0 OR expires_at IS NULL OR expires_at > ?)
                    ORDER BY confidence DESC LIMIT 1
                    """,
                    (question, now),
                ).fetchone()

            if not row:
                # 2. Case-insensitive seek dùng idx_kr_question_lower
                row = conn.execute(
                    """
                    SELECT * FROM knowledge_records
                    WHERE lower(question) = ?
                      AND (expires_at = 0 OR expires_at IS NULL OR expires_at > ?)
                    ORDER BY confidence DESC LIMIT 1
                    """,
                    (clean_q.lower(), now),
                ).fetchone()

            if not row and domain:
                # 3. Thử truy vấn có điều kiện domain
                row = conn.execute(
                    """
                    SELECT * FROM knowledge_records
                    WHERE domain = ? AND lower(question) LIKE ?
                      AND (expires_at = 0 OR expires_at IS NULL OR expires_at > ?)
                    ORDER BY confidence DESC LIMIT 1
                    """,
                    (domain, f"%{clean_q.lower()}%", now),
                ).fetchone()

            if row:
                ans = str(row["answer"]).strip()
                if ans:
                    collected_epoch = row["collected_at"] or now
                    ts_iso = datetime.fromtimestamp(collected_epoch, timezone.utc).isoformat()
                    return {
                        "found": True,
                        "value": ans,
                        "source": str(row["source"] or "KnowledgeWarehouse (WAL Cache)"),
                        "api_name": str(row["source"] or "KnowledgeWarehouse (WAL Cache)"),
                        "api_url": str(row["source_url"] or ""),
                        "confidence": float(row["confidence"] or 0.90),
                        "timestamp": ts_iso,
                        "epistemic_tier": "wal_cache",
                    }
        except Exception as exc:
            self._local.conn = None
            logger.debug("[FreeAPIBridge] Tier 0 WAL cache lookup exception: %s", exc, exc_info=True)

        return None

    def _catalog_egress_hosts(self) -> frozenset[str]:
        """[OWNER-MANDATE 2026-10-25] Scoped egress grant cho fetch Tier 1 —
        nối catalog free API 2,175+ (live measured 2026-10-25; catalog varies) vào luồng /ask.

        Hosts CHỈ lấy từ catalog nguồn cố định
        raw.githubusercontent.com/public-apis/public-apis (ALLOWED_HOSTS của
        free_api_catalog — URL catalog không bao giờ là user input), tính 1
        lần và cache instance-level. Chỉ grant hostname dạng domain từ entry
        https (fetch Tier 1 cũng chỉ chạy https):
          - LOẠI mọi IP literal (loopback/private/metadata/public): catalog
            không phải nguồn tin cậy cho IP đích — SSRF phải đi qua
            validate_url/safe_urlopen, không mở qua egress grant;
          - KHÔNG wildcard, KHÔNG đụng env mặc định (SCP_EGRESS_ALLOWLIST).

        Invariant bắt buộc: `SCP_EGRESS_MODE=deny` vẫn chặn TẤT CẢ —
        EgressPolicy.enforce chạy Invariant 3 (deny) TRƯỚC Invariant 4
        (allowlist + token_allowed_hosts), nên grant này không thể override
        deny; metadata/loopback invariants do EgressPolicy giữ nguyên. Grant
        áp dụng CHỈ cho fetch của Tier 1 (least-privilege — cùng pattern
        scoped-grant đã duyệt cho weather W13/W14 và wikipedia_client).
        """
        cached = self._cache.get("_catalog_egress_hosts")
        if isinstance(cached, frozenset):
            return cached
        hosts: set[str] = set()
        for entry in self.catalog.entries():
            url = str(entry.get("url", "")).strip()
            if not url.lower().startswith("https://"):
                continue
            host = (urllib.parse.urlsplit(url).hostname or "").strip().strip("[]").lower().rstrip(".")
            if not host or "." not in host:
                continue  # hostname 1 nhãn (localhost kiểu này) — không grant
            try:
                ipaddress.ip_address(host)
            except ValueError:
                hosts.add(host)  # hostname dạng domain — grant được
                continue
            # IP literal (kể cả public) — không grant qua cơ chế này
        grant = frozenset(hosts)
        self._cache["_catalog_egress_hosts"] = grant
        return grant

    def _query_catalog_apis(
        self,
        domain: str,
        question: str,
        terms: list[str],
        max_candidates: int = 5,
        deadline: float | None = None,
    ) -> dict[str, Any] | None:
        """Tier 1: Truy vấn các API từ Catalog thuộc danh mục của domain.

        [WIRE-BRIDGE 2026-10-25] max_candidates giới hạn số API được thử
        (ask path dùng 3 thay vì 5 — latency budget); deadline (monotonic,
        từ budget_seconds của fetch_for_domain) dừng loop khi hết ngân sách,
        timeout từng fetch được scale theo thời gian còn lại (tối thiểu 0.5s).
        """
        apis = self.get_apis_for_domain(domain)
        if not apis:
            return None

        # Lọc danh sách candidate APIs có liên quan đến terms của câu hỏi
        candidates: list[dict[str, Any]] = []
        if terms:
            for api in apis:
                haystack = f"{api.get('name', '')} {api.get('description', '')}".lower()
                if any(t in haystack for t in terms):
                    candidates.append(api)

        # Nếu không có match theo từ khóa, lấy tối đa 3 API auth="No" hàng đầu của domain
        if not candidates:
            candidates = [a for a in apis if str(a.get("auth", "")).strip().lower() == "no"][:3]
        else:
            candidates = candidates[:5]

        # [WIRE-BRIDGE 2026-10-25] Budget ask path: tối đa max_candidates
        # candidate (≤3 theo owner mandate — 5×3s=15s worst case là quá chậm).
        candidates = candidates[: max(1, int(max_candidates))]

        # Scoped egress grant cho catalog (xem _catalog_egress_hosts —
        # [OWNER-MANDATE 2026-10-25]; deny mode vẫn thắng mọi grant).
        egress_hosts = self._catalog_egress_hosts()

        for entry in candidates:
            remaining: float | None = None
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0.5:
                    logger.debug("[FreeAPIBridge] Tier 1 budget exhausted — %d candidate bỏ lại", len(candidates) - candidates.index(entry) - 1)
                    break
            fetch_timeout = 3.0 if remaining is None else max(0.5, min(3.0, remaining))
            url = str(entry.get("url", "")).strip()
            if not url.lower().startswith("https://"):
                continue

            # Kiểm tra chính sách egress trước khi gọi (dry-check cùng grant
            # với fetch bên dưới — DENY mode chặn ở đây, không đốt timeout)
            try:
                enforce_egress_policy(url, extra_allowed_hosts=egress_hosts)
            except Exception as exc:
                logger.debug("[FreeAPIBridge] Egress policy denied URL %s: %s", url, exc)
                continue

            # Thực thi fetch an toàn với giới hạn timeout và bytes. safe_urlopen
            # ép egress BÊN TRONG nên phải truyền cùng scoped grant (pattern
            # wikipedia_client: dry-check + fetch dùng chung 1 bộ host).
            try:
                req = urllib.request.Request(
                    url,
                    headers={
                        "User-Agent": "SCP-FreeAPIAdapterBridge/1.0 (+https://github.com/checken1994/GA-LAB)",
                        "Accept": "application/json, text/plain, */*",
                    },
                )
                fetcher = getattr(url_safety, "safe_urlopen", None) or safe_urlopen
                with fetcher(req, timeout=fetch_timeout, extra_allowed_hosts=egress_hosts) as resp:
                    content_type = str(resp.headers.get("Content-Type", "")).lower()
                    # Từ chối nội dung nhị phân (ảnh, file nén, video, binary stream)
                    if any(
                        b in content_type
                        for b in ("image/", "audio/", "video/", "application/zip", "application/pdf", "octet-stream")
                    ):
                        continue

                    raw_bytes = resp.read(262144)  # 256KB max
                    raw_text = raw_bytes.decode("utf-8", errors="replace")
                    # [WAVE25 2026-10-26] Quality gate chống docs/landing HTML
                    # (JSON luôn pass — xem bridge_quality): web-chrome không
                    # phải dữ liệu API → bỏ entry, thử candidate kế.
                    if is_docs_landing_page(raw_text, content_type):
                        continue
                    readable = _extract_readable(raw_text)

                    if readable:
                        gate_terms = terms[:2] if len(terms) >= 2 else terms
                        # Kiểm tra Term Relevance Gate: văn bản phải chứa từ khóa của câu hỏi
                        covered = (
                            not terms
                            or _terms_covered(readable, gate_terms)
                            or (len(terms) >= 2 and sum(1 for t in terms if t in readable.lower()) >= 2)
                        )
                        if covered:
                            snippet = readable[:700]
                            api_name = str(entry.get("name", "Free API Catalog"))
                            now_iso = datetime.now(timezone.utc).isoformat()

                            # Lưu kết quả đã học vào SQLite WAL cache
                            try:
                                self.knowledge.store(
                                    question=question,
                                    answer=snippet,
                                    domain=domain,
                                    source=api_name,
                                    source_url=url,
                                    confidence=0.85,
                                    collected_by="free_api_catalog",
                                )
                            except Exception as store_err:
                                logger.debug("[FreeAPIBridge] Cache store error: %s", store_err)

                            return {
                                "found": True,
                                "value": snippet,
                                "source": api_name,
                                "api_name": api_name,
                                "api_url": url,
                                "confidence": 0.85,
                                "timestamp": now_iso,
                                "epistemic_tier": "free_api_catalog",
                            }
            except Exception as fetch_exc:
                logger.debug(
                    "[FreeAPIBridge] API fetch failed for %s (%s): %s",
                    url, type(fetch_exc).__name__, fetch_exc,
                )
                continue

        return None

    def _fallback_live_knowledge(self, domain: str, question: str) -> dict[str, Any] | None:
        """Tier 2 Fallback: LiveKnowledgeFetcher (Wikipedia, Wikidata, arXiv, DuckDuckGo)."""
        try:
            live_res = LiveKnowledgeFetcher.fetch_live(question, domain=domain)
            if live_res and live_res.get("value"):
                val = str(live_res["value"]).strip()
                if val:
                    # Quality gate: kiểm tra độ liên quan trước khi lưu cache
                    terms = extract_salient_terms(question)
                    meta = live_res.get("metadata", {})
                    source_url = str(meta.get("url", "") or "")
                    unquoted_url = urllib.parse.unquote(source_url).lower()
                    meta_title = str(meta.get("title", "")).lower()
                    check_text = f"{val.lower()} {unquoted_url} {meta_title}"
                    if terms and not any(term in check_text for term in terms):
                        logger.debug("[FreeAPIBridge] Tier 2 fallback rejected irrelevant result for %r", question)
                        return None

                    source_name = str(live_res.get("source", "LiveKnowledgeFetcher"))
                    conf = float(live_res.get("confidence", 0.75))
                    now_iso = datetime.now(timezone.utc).isoformat()
                    snippet = val[:700]

                    try:
                        self.knowledge.store(
                            question=question,
                            answer=snippet,
                            domain=domain,
                            source=source_name,
                            source_url=source_url,
                            confidence=conf,
                            collected_by="live_knowledge",
                        )
                    except Exception as store_err:
                        logger.debug("[FreeAPIBridge] LiveKnowledge cache store error: %s", store_err)

                    return {
                        "found": True,
                        "value": snippet,
                        "source": source_name,
                        "api_name": source_name,
                        "api_url": source_url,
                        "confidence": conf,
                        "timestamp": now_iso,
                        "epistemic_tier": "live_knowledge",
                    }
        except Exception as exc:
            logger.debug("[FreeAPIBridge] Tier 2 LiveKnowledgeFetcher fallback failed: %s", exc)

        return None

    def _fallback_wikipedia(self, domain: str, question: str) -> dict[str, Any] | None:
        """Tier 3 Fallback: Canonical Wikipedia Client."""
        try:
            has_vn = any("\u00c0" <= ch <= "\u1ef9" for ch in question)
            langs = ("vi", "en") if has_vn else ("en",)
            for lang in langs:
                wiki_res = wikipedia_client.search_then_summary(question, lang=lang, timeout=8)
                if wiki_res and wiki_res.get("extract"):
                    extract = str(wiki_res["extract"]).strip()
                    if not extract or "may refer to" in extract.lower():
                        continue
                    title = str(wiki_res.get("title", question))
                    # Quality gate: kiểm tra độ liên quan trước khi lưu cache
                    terms = extract_salient_terms(question)
                    check_text = f"{extract.lower()} {title.lower()}"
                    if terms and not any(term in check_text for term in terms):
                        logger.debug("[FreeAPIBridge] Tier 3 Wikipedia fallback rejected irrelevant result for %r", question)
                        continue
                    url = str(
                        wiki_res.get(
                            "url",
                            f"https://{lang}.wikipedia.org/wiki/{urllib.parse.quote(title)}",
                        )
                    )
                    snippet = extract[:700]
                    now_iso = datetime.now(timezone.utc).isoformat()
                    source_name = f"Wikipedia ({lang}) — {title}"

                    try:
                        self.knowledge.store(
                            question=question,
                            answer=snippet,
                            domain=domain,
                            source=source_name,
                            source_url=url,
                            confidence=0.70,
                            collected_by="wikipedia",
                        )
                    except Exception as store_err:
                        logger.debug("[FreeAPIBridge] Wikipedia cache store error: %s", store_err)

                    return {
                        "found": True,
                        "value": snippet,
                        "source": source_name,
                        "api_name": source_name,
                        "api_url": url,
                        "confidence": 0.70,
                        "timestamp": now_iso,
                        "epistemic_tier": "wikipedia",
                    }
        except Exception as exc:
            logger.debug("[FreeAPIBridge] Tier 3 Wikipedia fallback failed: %s", exc)

        return None

    def query_domain(self, domain: str, question_str: str) -> dict[str, Any]:
        """Truy vấn tri thức chuẩn hóa trả về dictionary có cấu trúc:

        {"found": bool, "answer": str | None, "confidence": float, "source": str, "provenance": dict}
        """
        res = self.fetch_for_domain(
            domain=domain,
            intent="query",
            entity=question_str,
            question=question_str,
        )
        if res is not None and res.get("found"):
            return {
                "found": True,
                "answer": res["value"],
                "confidence": float(res.get("confidence", 0.0)),
                "source": str(res.get("source", "unknown")),
                "provenance": res,
            }
        return {
            "found": False,
            "answer": None,
            "confidence": 0.0,
            "source": "none",
            "provenance": {},
        }

    def health_check(self) -> bool:
        """Kiểm tra sức khỏe của Bridge: xác nhận catalog có dữ liệu và kết nối mạng an toàn.

        Tuân thủ Fail-Closed: khi SCP_EGRESS_MODE=deny hoặc ping mạng thất bại -> trả False.
        """
        healthy = shared_health_ping(type(self).__name__, self._live_ping)
        self._cache["_health_cache"] = healthy
        self._cache["_health_cache_ts"] = time.time()
        return healthy

    def _live_ping(self) -> bool:
        """Thực hiện live ping kiểm tra catalog và kết nối mạng qua cổng kiểm soát egress."""
        if not self.catalog or len(self.catalog.entries()) == 0:
            return False
        try:
            fetcher = getattr(url_safety, "safe_urlopen", None) or safe_urlopen
            with fetcher(
                "https://raw.githubusercontent.com/public-apis/public-apis/master/README.md",
                timeout=3,
            ) as resp:
                status = getattr(resp, "status", 200)
                return status == 200
        except Exception as exc:
            logger.debug("[FreeAPIBridge] health_check live ping failed (egress/network): %s", exc)
            return False


# Singleton instance
_BRIDGE: FreeAPIAdapterBridge | None = None
_BRIDGE_LOCK = threading.Lock()


def get_bridge() -> FreeAPIAdapterBridge:
    """Thread-safe singleton cho FreeAPIAdapterBridge."""
    global _BRIDGE
    if _BRIDGE is None:
        with _BRIDGE_LOCK:
            if _BRIDGE is None:
                _BRIDGE = FreeAPIAdapterBridge()
    return _BRIDGE
