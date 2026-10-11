"""[S24 2026-09-13] Question Router — LOOKUP→data-API fork TRƯỚC LLM generation.

OWNER ARCHITECTURE DIRECTIVE (chốt): "hơn 1000 API để lấy thông tin. LLM CHỈ
dùng khi 1000 API không có." Trước S24 mọi /ask bay thẳng LLM generation
(bằng chứng: reports/benchmark-2026-09-12/bench_combined_seed2026.json).

Cascade classifier (design chốt của task):
  L0 keyword/regex hint — reuse `detect_domain` từ scp.core.partition.shard
     (bắt buộc theo design) + classify_top1 từ scp.data_sources.domain_classifier
     khi detect_domain trả 'general' (câu tiếng Anh; detect_domain chỉ có
     keyword tiếng Việt). Rules intent LOOKUP/REASONING viết mới ở đây —
     KHÔNG copy logic domain đã có (S25 tripwire: duplicate logic).
  L1 embedding similarity — KHÔNG DÙNG: repo không có embedding dep
     (scp/requirements.txt ghi rõ "Optional ML / embeddings — Không cài trong
     runtime mặc định"). Đề xuất thêm dep nằm trong report S24, KHÔNG tự thêm.
  L2 LLM zero-shot 1 call nhỏ qua LLMGateway — chỉ chạy khi L0 bất định.
     Parse fail-safe → REASONING/general (hành vi hiện tại = đường LLM).

Fork data path (chỉ khi intent==LOOKUP và confidence >= SCP_T2_MIN_CONFIDENCE):
  1. FreeAPICatalog.search(salient_terms, auth='No' ưu tiên) — index 1,785
     entries. KHÔNG gọi API nào ngoài search match.
  2. Entry khớp → egress check → fetch qua safe_urlopen (egress choke có sẵn).
  3. Provider encyclopedic (Wikipedia được catalog liệt kê, entry 'Wikipedia'
     auth=No, category 'Open Data') → canonical scp.core.wikipedia_client
     (G3-CONSOLIDATE RE-05: mọi module PHẢI dùng client này) — host
     en.wikipedia.org/vi.wikipedia.org nằm trong SCP_EGRESS_ALLOWLIST.
  4. Compose answer từ dữ liệu + provenance (API name + URL) — KHÔNG có
     generation LLM nào chạy trên nhánh này.
  5. API fail/empty/data không khớp câu hỏi → fallback LLM + log reason.
Answer fork đi qua CÙNG verification/governance path của AskKernelAdapter
(provenance="input_context_only" hợp lệ theo checks ask_kernel_adapter.py:395);
payload dữ liệu được inject làm evidence cho chính grounding check đó —
THÊM bằng chứng, không nới lỏng check nào.

KPI: llm_bypassed_count / llm_calls_count / classifier_llm_calls /
fallback_reasons — expose qua /v100/routing/stats (admin_v100, auth sẵn).

Kill switch: SCP_T2_ROUTER=0 tắt toàn bộ fork (trở lại hành vi cũ).
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.parse
import uuid
from typing import Any

from scp.runtime.router_rules import (  # noqa: F401
    _CREATIVE_RULES,
    _LOOKUP_RULES,
    _MATH_HINT_GUARD,
    _NON_LATIN_SCRIPT,
    _PH_PATTERN,
    _REASONING_STRONG_RULES,
    _REASONING_WEAK_RULES,
    _SECURITY_RULES,
    _VIETNAMESE_DIACRITICS,
    _VIETNAMESE_WORDS,
    DEFAULT_MIN_CONFIDENCE,
    LANE_CHATBOT,
    LANE_FACTUAL,
    LANE_SECURITY,
    LOOKUP,
    REASONING,
    SECURITY,
    Intent,
    RouteDecision,
    _compiled_rules,
    _domain_hint,
    _sanitize_domain_hint,
    classify_l0,
    detect_language,
)

logger = logging.getLogger("scp.question_router")


# ---------------------------------------------------------------------------
# Env / kill switches
# ---------------------------------------------------------------------------
def t2_fork_enabled() -> bool:
    """Kill switch SCP_T2_ROUTER=0|false|off|no tắt fork — /ask về hành vi cũ."""
    raw = os.environ.get("SCP_T2_ROUTER", "").strip().lower() or "1"
    return raw not in {"0", "false", "off", "no"}


def t2_min_confidence() -> float:
    """Ngưỡng confidence tối thiểu để fork sang nhánh data-API (default 0.6)."""
    raw = os.environ.get("SCP_T2_MIN_CONFIDENCE", "").strip()
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_MIN_CONFIDENCE
    if not 0.0 <= value <= 1.0:
        return DEFAULT_MIN_CONFIDENCE
    return value


# ---------------------------------------------------------------------------
# L2 — LLM zero-shot 1 call nhỏ (label enum). Chỉ chạy khi L0 bất định.
# ---------------------------------------------------------------------------
_L2_SYSTEM_PROMPT = (
    "You are SCP's intent router. Classify the user message into exactly one "
    "label. LOOKUP: a short factual question whose answer is a piece of data "
    "a public database or encyclopedia could return. REASONING: needs "
    "computation, code writing, multi-step logic, opinions or creative "
    "writing. Reply with exactly one word: LOOKUP or REASONING."
)


def classify_l2(question: str, gateway: Any = None) -> RouteDecision:
    """L2 zero-shot qua LLMGateway (SYNC gateway — test/hermetic injection).

    Production dùng classify_l2_async (LLMGateway.chat là async).
    Parse fail-safe → REASONING/general.
    """
    domain = _domain_hint(question)
    try:
        if gateway is None:
            from scp.llm_gateway import get_gateway

            gateway = get_gateway()
        res = gateway.chat(
            (question or "")[:500],
            context="",
            system_prompt=_L2_SYSTEM_PROMPT,
            task="route",
        )
        import asyncio
        if asyncio.iscoroutine(res):
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if loop and loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    answer, provider = pool.submit(asyncio.run, res).result()
            else:
                answer, provider = asyncio.run(res)
        else:
            answer, provider = res
    except Exception as exc:
        logger.debug("L2 classify raised — entering failsafe routing: %s", exc, exc_info=True)
        return _l2_failsafe(question, domain, exc)
    _stats.record_classifier_llm(ok=bool(answer))
    return _parse_l2_answer(str(answer or ""), provider, domain, question=question)


async def classify_l2_async(question: str, gateway: Any = None) -> RouteDecision:
    """L2 production path — LLMGateway.chat là async (scp/llm_gateway/client.py)."""
    domain = _domain_hint(question)
    try:
        if gateway is None:
            from scp.llm_gateway import get_gateway

            gateway = get_gateway()
        answer, provider = await gateway.chat(
            (question or "")[:500],
            context="",
            system_prompt=_L2_SYSTEM_PROMPT,
            task="route",
        )
    except Exception as exc:
        logger.debug("L2 classify raised — entering failsafe routing: %s", exc, exc_info=True)
        return _l2_failsafe(question, domain, exc)
    _stats.record_classifier_llm(ok=bool(answer))
    return _parse_l2_answer(str(answer or ""), provider, domain, question=question)


def _l2_failsafe(question: str, domain: str, exc: Exception) -> RouteDecision:
    logger.warning("[S24] L2 classifier LLM call failed (%s: %s)", type(exc).__name__, exc)
    _stats.record_classifier_llm(ok=False)
    lang = detect_language(question)
    # [W2-d3] Fail-closed: L2 lỗi KHÔNG còn rơi vào lane nới nhất (bypass=True
    # cho phép verdict!=PASS). Giữ intent REASONING/lane CHATBOT (đường
    # delivery không đổi) nhưng bypass=False — verdict phải qua verification
    # đầy đủ như factual. Khi LLM classifier chết, hệ thống nghiêm ngặt hơn,
    # không lỏng hơn.
    return RouteDecision(
        intent=REASONING,
        domain=domain or "general",
        confidence=0.5,
        via="l2-failsafe",
        reason="llm_error_fail_closed",
        lane=LANE_CHATBOT,
        language=lang,
        bypass_verdict_pass=False,
    )


def _parse_l2_answer(answer_text: str, provider: Any, domain: str, question: str = "") -> RouteDecision:
    text = answer_text.upper()
    lang = detect_language(question)
    if "LOOKUP" in text[:40]:
        return RouteDecision(
            intent=LOOKUP,
            domain=domain or "general",
            confidence=0.8,
            via="l2-llm",
            reason=f"llm:{provider}",
            lane=LANE_FACTUAL,
            language=lang,
            bypass_verdict_pass=False,
        )
    if "REASONING" in text[:40]:
        return RouteDecision(
            intent=REASONING,
            domain=domain or "general",
            confidence=0.8,
            via="l2-llm",
            reason=f"llm:{provider}",
            lane=LANE_CHATBOT,
            language=lang,
            bypass_verdict_pass=True,
        )
    # Parse fail-safe: [W2-d3] fail-closed — LLM trả garbage (không parse
    # được) thì KHÔNG rơi lane nới nhất; bypass=False bắt verify đầy đủ.
    return RouteDecision(
        intent=REASONING,
        domain=domain or "general",
        confidence=0.5,
        via="l2-failsafe",
        reason="unparseable_fail_closed",
        lane=LANE_CHATBOT,
        language=lang,
        bypass_verdict_pass=False,
    )


def route_question(question: str, gateway: Any = None) -> RouteDecision:
    """Cascade đầy đủ L0 → L2 (sync). Entry point test/hermetic; production
    dùng route_question_async."""
    l0 = classify_l0(question)
    if l0 is not None:
        _stats.record_route(l0)
        return l0
    decision = classify_l2(question, gateway=gateway)
    _stats.record_route(decision)
    return decision


async def route_question_async(question: str, gateway: Any = None) -> RouteDecision:
    """Cascade đầy đủ L0 → L2 (async — production /ask fork path)."""
    l0 = classify_l0(question)
    if l0 is not None:
        _stats.record_route(l0)
        return l0
    decision = await classify_l2_async(question, gateway=gateway)
    _stats.record_route(decision)
    return decision


# ---------------------------------------------------------------------------
# KPI counters — in-memory + prometheus. Expose qua /v100/routing/stats.
# ---------------------------------------------------------------------------
class RouteStats:
    """Thread-unsafe-by-design counter snapshot (đọc qua admin route)."""

    def __init__(self) -> None:
        self.route_counts: dict[str, int] = {}
        self.fallback_reasons: dict[str, int] = {}
        self.llm_bypassed_count = 0
        self.llm_calls_count = 0
        self.classifier_llm_calls = 0
        self.classifier_llm_failures = 0
        self.lookup_fetch_ok = 0
        self.lookup_fetch_fail = 0

    def record_route(self, decision: RouteDecision) -> None:
        key = f"{decision.via}:{decision.intent}"
        self.route_counts[key] = self.route_counts.get(key, 0) + 1
        _prom_inc("scp_ask_route_decisions_total", {"via": decision.via, "intent": decision.intent})

    def record_classifier_llm(self, ok: bool) -> None:
        self.classifier_llm_calls += 1
        if not ok:
            self.classifier_llm_failures += 1

    def record_bypass(self) -> None:
        self.llm_bypassed_count += 1
        _prom_inc("scp_ask_llm_bypassed_total")

    def record_llm_call(self) -> None:
        self.llm_calls_count += 1
        _prom_inc("scp_ask_llm_calls_total")

    def record_fallback(self, reason: str) -> None:
        key = str(reason)[:80]
        self.fallback_reasons[key] = self.fallback_reasons.get(key, 0) + 1

    def record_fetch(self, ok: bool) -> None:
        if ok:
            self.lookup_fetch_ok += 1
        else:
            self.lookup_fetch_fail += 1

    def snapshot(self) -> dict[str, Any]:
        return {
            "llm_bypassed_count": self.llm_bypassed_count,
            "llm_calls_count": self.llm_calls_count,
            "classifier_llm_calls": self.classifier_llm_calls,
            "classifier_llm_failures": self.classifier_llm_failures,
            "lookup_fetch_ok": self.lookup_fetch_ok,
            "lookup_fetch_fail": self.lookup_fetch_fail,
            "route_counts": dict(self.route_counts),
            "fallback_reasons": dict(self.fallback_reasons),
            "bypass_ratio": round(
                self.llm_bypassed_count
                / max(1, self.llm_bypassed_count + self.llm_calls_count),
                4,
            ),
        }


_stats = RouteStats()
_PROM_COUNTERS: dict[str, Any] = {}


def _prom_inc(name: str, labels: dict[str, str] | None = None) -> None:
    """Prometheus counter — optional observability, không bao giờ chặn /ask."""
    try:
        from prometheus_client import Counter

        counter = _PROM_COUNTERS.get(name)
        if counter is None:
            counter = Counter(
                name,
                "S24 question-router fork metric",
                list(labels.keys()) if labels else [],
            )
            _PROM_COUNTERS[name] = counter
        if labels:
            counter.labels(**labels).inc()
        else:
            counter.inc()
    except Exception:
        logger.debug("prometheus counter unavailable: %s", name, exc_info=True)


def route_stats_snapshot() -> dict[str, Any]:
    """Snapshot KPI cho admin stats route (seam sạch, đã auth)."""
    return _stats.snapshot()


# ---------------------------------------------------------------------------
# Salient terms + catalog data path
# ---------------------------------------------------------------------------
_STOPWORDS = frozenset(
    """a an the is are was were of in on at to for from by with about what who
    where when which how many much far fast tall deep long does do did can
    could should would will was there their this that these those it its as
    và là của có ở được bằng bao nhiêu gì ai đâu nào khi năm the cho với vào
    trong một các này đó thì về trên tại theo nếu những""".split()
)


def extract_salient_terms(question: str, limit: int = 4) -> list[str]:
    """Từ khóa nổi bật của câu hỏi (bỏ interrogatives/stopwords, giữ thứ tự)."""
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


# Encyclopedic provider (Wikipedia) được catalog liệt kê — entry 'Wikipedia',
# auth='No', category 'Open Data'. URL trong catalog là trang docs mediawiki.org
# (không phải data endpoint) → resolve qua canonical wikipedia_client.
_KNOWLEDGE_DOMAINS = frozenset(
    {
        "geography", "history", "biology", "chemistry", "physics",
        "astronomy", "medical", "technology", "geology", "general",
    }
)


def _catalog_candidates(terms: list[str]) -> tuple[list[dict[str, Any]], str]:
    """Progressive FreeAPICatalog.search — trả (entries, query_used).

    (1) toàn bộ terms + auth='No'; (2) toàn bộ terms; (3) term đầu + auth='No'.
    Catalog lỗi/empty → ([], '').
    """
    if not terms:
        return [], ""
    try:
        from scp.data_sources.free_api_catalog import get_catalog

        catalog = get_catalog()
        for query, auth in (
            (" ".join(terms), "No"),
            (" ".join(terms), None),
            (terms[0], "No"),
        ):
            entries = catalog.search(query=query, auth=auth, limit=10)
            if entries:
                return list(entries), f"{query}|auth={auth or 'any'}"
    except Exception as exc:
        logger.warning("[S24] catalog search failed (%s: %s)", type(exc).__name__, exc, exc_info=True)
        return [], ""
    return [], ""


def _host_allowed(url: str) -> bool:
    """Egress dry-check — KHÔNG fetch. Dev mode (không SCP_EGRESS_MODE) trả True
    (SSRF checks vẫn chạy trong safe_urlopen)."""
    from scp.security.url_safety import enforce_egress_policy

    try:
        enforce_egress_policy(url)
        return True
    except Exception as exc:
        logger.debug("[S24] egress blocked %s: %s", urllib.parse.urlsplit(url).hostname, exc, exc_info=True)
        return False


def _fetch_url_text(url: str, timeout: float = 6.0, max_bytes: int = 262_144) -> str | None:
    """Fetch 1 URL qua safe_urlopen (egress choke có sẵn). Trả text hoặc None."""
    from scp.security.url_safety import safe_urlopen

    try:
        import urllib.request

        req = urllib.request.Request(  # noqa: S310 — scheme/host do egress gate kiểm
            url, headers={"User-Agent": "SCP-S24-lookup/1.0", "Accept": "application/json,text/plain,*/*"}
        )
        with safe_urlopen(req, timeout=timeout) as resp:
            return resp.read(max_bytes).decode("utf-8", errors="replace")
    except Exception as exc:
        logger.info("[S24] data fetch failed %s (%s: %s)", url, type(exc).__name__, exc, exc_info=True)
        return None


def _terms_covered(text: str, terms: list[str]) -> bool:
    """Quality gate: dữ liệu fetch về phải CHỨA các term nổi bật của câu hỏi
    (chặn compose garbage từ landing-page match)."""
    lowered = (text or "").lower()
    return bool(terms) and all(term in lowered for term in terms)


def _wiki_lookup(question: str, terms: list[str]) -> dict[str, Any] | None:
    """Provider encyclopedic (catalog-listed) → canonical wikipedia_client.

    Trả {'text', 'api_name', 'api_url', 'evidence'} hoặc None. safe_urlopen +
    SCP_EGRESS_ALLOWLIST vẫn là choke cuối (en.wikipedia.org allowlisted).

    [S24 fix runtime] Thứ tự query: ENTITY term (term cuối — "capital of
    France" → 'france') TRƯỚC, rồi mới full terms. Search full-terms trước
    trả article sai ("Capital punishment in France" cho "capital of France")
    — bug bắt được bằng runtime proof, judge fail-closed đã chặn answer sai.
    """
    try:
        from scp.core.wikipedia_client import search_then_summary

        has_vn = any("\u00c0" <= ch <= "\u1ef9" for ch in question)
        langs = ("vi", "en") if has_vn else ("en",)
        queries = [terms[-1]] if len(terms) == 1 else [terms[-1], " ".join(terms)]
        for lang in langs:
            for query in queries:
                result = search_then_summary(query, lang=lang, timeout=8)
                if not result:
                    continue
                extract = str(result.get("extract") or "").strip()
                title = str(result.get("title") or "").strip()
                url = str(result.get("url") or "").strip()
                if not extract or "may refer to" in extract.lower():
                    continue  # disambiguation / rỗng
                gate_terms = terms[:2] if len(terms) >= 2 else terms
                if not _terms_covered(extract, gate_terms):
                    continue
                evidence = extract[:700]
                return {
                    "text": evidence,
                    "api_name": f"Wikipedia ({lang}) — {title}",
                    "api_url": url or f"https://{lang}.wikipedia.org/wiki/{urllib.parse.quote(title)}",
                    "evidence": evidence,
                }
    except Exception as exc:
        logger.warning("[S24] wiki lookup failed (%s: %s)", type(exc).__name__, exc, exc_info=True)
    return None


def _generic_entry_lookup(entries: list[dict[str, Any]], terms: list[str]) -> dict[str, Any] | None:
    """Fetch entry catalog khớp (egress-gated). JSON/HTML → text → term gate."""
    for entry in entries:
        url = str(entry.get("url") or "").strip()
        name = str(entry.get("name") or "unknown")
        if not url.lower().startswith("https://"):
            _stats.record_fetch(ok=False)
            continue
        if not _host_allowed(url):
            _stats.record_fetch(ok=False)
            _stats.record_fallback(f"egress_blocked:{urllib.parse.urlsplit(url).hostname}")
            continue
        raw = _fetch_url_text(url)
        if not raw:
            _stats.record_fetch(ok=False)
            _stats.record_fallback(f"fetch_failed:{name[:40]}")
            continue
        text = _extract_readable(raw)
        if text and _terms_covered(text, terms[:2]):
            _stats.record_fetch(ok=True)
            snippet = text[:700]
            return {"text": snippet, "api_name": name, "api_url": url, "evidence": snippet}
        _stats.record_fetch(ok=False)
        _stats.record_fallback(f"data_not_matching:{name[:40]}")
    return None


def _extract_readable(raw: str) -> str | None:
    """JSON → nối string values; HTML → strip tags. Ngắn/rỗng → None."""
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
                if depth > 4 or len(parts) > 40:
                    return
                if isinstance(node, dict):
                    for value in node.values():
                        _walk(value, depth + 1)
                elif isinstance(node, list):
                    for value in node[:20]:
                        _walk(value, depth + 1)
                elif isinstance(node, str) and len(node.strip()) > 15:
                    parts.append(node.strip())

            _walk(data)
            text = " | ".join(parts)
            return text or None
    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", raw, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:4000] or None


# [W12-f1] ConversionDataSource singleton — bảng hệ số local thuần, xây 1 lần.
_conversion_source: Any = None


def _get_conversion_source() -> Any:
    global _conversion_source
    if _conversion_source is None:
        from scp.data_sources.conversion import ConversionDataSource

        _conversion_source = ConversionDataSource()
    return _conversion_source


def _conversion_lookup(question: str) -> dict[str, Any] | None:
    """[W12-f1] Tier dữ liệu LOCAL deterministic cho câu quy đổi đơn vị.

    ConversionDataSource có bảng hệ số thật trong repo (pure, không mạng).
    Trước W12 các câu conversion (iso_conv_001..010, iso_phys_007 — 11/143
    goldset) KHÔNG có đường dữ liệu nào: catalog search chỉ match rác
    substring (UGCdrop cho 'GCD'), domain 'conversion' nằm ngoài
    _KNOWLEDGE_DOMAINS nên wiki provider cũng không chạy → luôn LLM fallback.
    Đây là câu trả lời trực tiếp cho câu hỏi owner W12: "hàng nghìn API mà
    câu đơn giản không được trả lời" — source CÓ nhưng KHÔNG được wire.

    Fail-closed: parse/convert không chắc chắn (đơn vị lạ, cross-category) →
    None → catalog/wiki/LLM fallback như cũ. KHÔNG bịa hệ số mới.
    """
    try:
        result = _get_conversion_source().answer_from_question(question)
    except Exception as exc:
        logger.warning(
            "[S24][W12] conversion lookup failed (%s: %s)", type(exc).__name__, exc, exc_info=True
        )
        return None
    if not result:
        return None
    text = str(result.get("text") or "").strip()
    if not text:
        return None
    return {
        "text": text,
        "api_name": "ConversionDataSource (local)",
        "api_url": "local:scp/data_sources/conversion.py",
        "evidence": text,
    }


# [W13] WeatherDataSource singleton — Open-Meteo thật (có mạng), xây 1 lần.
_weather_source: Any = None


def _get_weather_source() -> Any:
    global _weather_source
    if _weather_source is None:
        from scp.data_sources.weather import WeatherDataSource

        _weather_source = WeatherDataSource()
    return _weather_source


def _weather_host_allowed(url: str) -> bool:
    """[W13] Egress dry-check scoped cho weather tier (host FORECAST).

    CHỈ host được owner duyệt (OPEN_METEO_EGRESS_HOST = api.open-meteo.com) —
    so khớp CHÍNH XÁC hostname, không wildcard, không subdomain
    (geocoding-api.open-meteo.com vẫn bị chặn — host geocode W14 có dry-check
    và scoped grant RIÊNG `weather.geocode_host_allowed`, không mượn gate
    forecast này: least-privilege, mỗi fetch chỉ authorize host của nó).
    `enforce_egress_policy` vẫn là gate thật: DENY mode thắng (Invariant 3
    trước extra hosts), metadata/loopback invariants nguyên, allowlist mode
    vẫn áp dụng đồng thời — extra host chỉ MỞ thêm đúng 1 host này.
    """
    from scp.data_sources.weather import OPEN_METEO_EGRESS_HOST
    from scp.security.url_safety import enforce_egress_policy

    host = (urllib.parse.urlsplit(url).hostname or "").strip().strip("[]").lower().rstrip(".")
    if host != OPEN_METEO_EGRESS_HOST:
        return False
    try:
        enforce_egress_policy(url, extra_allowed_hosts=frozenset({OPEN_METEO_EGRESS_HOST}))
        return True
    except Exception as exc:
        logger.debug(
            "[S24][W13] egress blocked weather host %s: %s", host, exc, exc_info=True
        )
        return False


def _weather_lookup(question: str) -> dict[str, Any] | None:
    """[W13/W14] Tier dữ liệu thời tiết THẬT (Open-Meteo) cho lookup fork.

    WeatherDataSource (có sẵn trong repo từ trước, 7 intents, chưa từng được
    wire) fetch current + forecast ngắn từ api.open-meteo.com. [W14] Location
    nằm trong bảng thành phố local HOẶC — miss — qua geocode fallback
    (geocoding-api.open-meteo.com, owner approval W14) với inject-guard
    sanitize; geocode miss → None (fail-closed). Fail-closed: candidate rỗng /
    fetch lỗi / payload thiếu nhiệt độ / echo location lệch → None → catalog/
    LLM fallback như cũ. KHÔNG bịa số liệu: mọi giá trị trong answer đến từ
    payload API (test mock HTTP; runtime gọi API thật).

    Relevance gate (weather-level): location bắt buộc xuất hiện trong answer —
    gate chia sẻ `_terms_covered` của fork (W2-d5) giữ nguyên phía sau.
    """
    from scp.data_sources.weather import OPEN_METEO_EGRESS_HOST

    # Dry-check scoped: policy từ chối host (vd SCP_EGRESS_MODE=deny) → bỏ
    # sớm, không đốt 8s timeout cho một fetch chắc chắn bị chặn.
    if not _weather_host_allowed(f"https://{OPEN_METEO_EGRESS_HOST}/v1/forecast"):
        _stats.record_fallback(f"egress_blocked:{OPEN_METEO_EGRESS_HOST}")
        return None
    try:
        result = _get_weather_source().answer_from_question(question)
    except Exception as exc:
        logger.warning(
            "[S24][W13] weather lookup failed (%s: %s)", type(exc).__name__, exc, exc_info=True
        )
        return None
    if not result:
        return None
    text = str(result.get("text") or "").strip()
    if not text:
        return None
    location = str(result.get("location") or "").strip().lower()
    if location and location not in text.lower():
        logger.warning(
            "[S24][W13] weather answer missing location %r → LLM fallback", location
        )
        return None
    return {
        "text": text,
        "api_name": "Open-Meteo",
        "api_url": str(result.get("api_url") or f"https://{OPEN_METEO_EGRESS_HOST}/v1/forecast"),
        "evidence": text,
    }


def resolve_lookup_data(
    question: str,
    domain: str = "general",
    decision: RouteDecision | None = None,
) -> dict[str, Any] | None:
    """Nhánh data-API: conversion local → weather (Open-Meteo, [W13]) →
    catalog search → generic fetch → provider encyclopedic.

    Trả {'text','api_name','api_url','evidence'} hoặc None (fallback LLM).
    KHÔNG gọi API ngoài weather host được duyệt / search match / provider
    được catalog liệt kê.
    """
    terms = extract_salient_terms(question)
    if not terms:
        return None
    # [W12-f1] Local deterministic tier ĐỨNG TRƯỚC catalog: quy đổi đơn vị
    # trả lời từ bảng hệ số trong repo — không mạng, không fetch, không bịa.
    answer = _conversion_lookup(question)
    if answer is not None:
        return answer
    # [W13] Tier thời tiết thật (Open-Meteo) — chỉ chạy khi route domain =
    # weather (weather_fact/domain hint). Location không extractable hoặc API
    # fail-closed → None → catalog/wiki/LLM fallback như cũ. Trước W13 câu
    # weather không có đường dữ liệu nào: catalog miss (không entry data-API)
    # + domain 'weather' ngoài _KNOWLEDGE_DOMAINS (wiki không chạy) → luôn
    # LLM fallback → q07 withheld (W10/W11/W12 battery đo được).
    if domain == "weather" or (decision is not None and str(getattr(decision, "domain", "")) == "weather"):
        answer = _weather_lookup(question)
        if answer is not None:
            return answer
    # [DomainDataFork] Tier dữ liệu chuyên ngành cục bộ (Thiên văn, Hóa học, Vật lý, Địa lý, Toán)
    try:
        from scp.runtime.domain_data_fork import resolve_domain_data_lookup

        domain_answer = resolve_domain_data_lookup(question)
        if domain_answer is not None:
            return domain_answer
    except Exception as _fork_err:
        logger.debug("[question_router] domain_data_lookup error: %s", _fork_err, exc_info=True)

    entries, query_used = _catalog_candidates(terms)
    if entries:
        logger.info("[S24] catalog search %r → %d entries (domain=%s)", query_used, len(entries), domain)
        answer = _generic_entry_lookup(entries, terms)
        if answer is not None:
            return answer
    # Provider encyclopedic (Wikipedia được catalog liệt kê — entry 'Wikipedia',
    # auth='No', category 'Open Data'; URL trong catalog là trang docs
    # mediawiki.org nên KHÔNG fetch được như data endpoint). Khi generic path
    # miss — kể cả khi token search đã match entry khác — resolve provider này
    # qua canonical wikipedia_client (RE-05). safe_urlopen + SCP_EGRESS_ALLOWLIST
    # vẫn là choke cuối; nếu egress chặn → None → LLM fallback có reason.
    if domain in _KNOWLEDGE_DOMAINS:
        answer = _wiki_lookup(question, terms)
        if answer is not None:
            return answer
    if not entries:
        logger.info("[S24] catalog has no matching entry (terms=%r) → LLM fallback", terms)
        _stats.record_fallback("no_matching_catalog_entry")
    return None


# ---------------------------------------------------------------------------
# Fork entry point — gọi từ AskKernelAdapter.run_rag TRƯỚC handler (generation)
# ---------------------------------------------------------------------------
def _trim(text: str, limit: int = 700) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _select_relevant_text(text: str, terms: list[str], limit: int = 700) -> str:
    """[S24 fix runtime] Chọn CÂU trả lời TRỰC TIẾP thay vì cắt mù theo ký tự.

    Bug thật #1 (bắt bằng test S24): extract Wikipedia France (981 chars) đặt
    fact "Its capital ... is Paris" ở câu CUỐI, _trim(700) cắt mất fact.
    Bug thật #2 (bắt bằng runtime proof): chọn TẤT CẢ câu match term → answer
    gồm câu địa lý không liên quan + câu capital → cả 2 LLM judge chấm FAIL
    (answer không trực diện), trong khi answer LLM thuần "Paris" được PASS.

    Heuristic: câu chứa TERM ĐẦU TIÊN của câu hỏi (topic noun — 'capital')
    là câu trả lời trực tiếp; không có → câu match term bất kỳ; vẫn không →
    giữ nguyên text (để judge quyết)."""
    text = re.sub(r"\s+", " ", text).strip()
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    if len(sentences) <= 1 or not terms:
        return _trim(text, limit)
    lowered = [t.lower() for t in terms]
    topic = lowered[0]
    primary = [s for s in sentences if topic in s.lower()]
    if primary:
        selected = " ".join(primary[:3])
    else:
        rest = lowered[1:]
        hits = [
            s for s in sentences
            if any(term in s.lower() for term in rest)
        ]
        selected = " ".join(hits) if hits else text
    return _trim(selected, limit)


async def attempt_lookup_fork(req: Any) -> dict[str, Any] | None:
    """Thử trả lời /ask bằng data-API. Trả response dict (AskResponse-compatible)
    hoặc None → caller chạy handler (generation path) như trước.

    Scope v1: chat ask thuần (KHÔNG contexts, KHÔNG ai_answer) — đúng profile
    bench_combined_seed2026. RAG ask / replay answer giữ nguyên đường cũ.
    """
    question = str(getattr(req, "question", "") or "").strip()
    if not question:
        return None
    if list(getattr(req, "contexts", None) or []) or str(getattr(req, "retrieved_context", "") or "").strip():
        return None
    if str(getattr(req, "ai_answer", "") or "").strip():
        return None

    decision = await route_question_async(question)
    if decision.intent != LOOKUP or decision.confidence < t2_min_confidence():
        _stats.record_llm_call()
        return None

    started = time.time()
    data = resolve_lookup_data(question, domain=decision.domain, decision=decision)
    if data is None:
        _stats.record_llm_call()
        logger.info(
            "[S24] LOOKUP fork miss (via=%s domain=%s reason=%s) → LLM fallback",
            decision.via, decision.domain, decision.reason,
        )
        return None

    _stats.record_bypass()
    # Provenance suffix là PHẦN CỦA answer; evidence đưa vào verification phải
    # chứa cả suffix, nếu không Tier-1 grounding của judge REJECT vì các từ
    # "Nguồn dữ liệu..." không có trong context (bug phát hiện bằng test S24:
    # REJECT_GROUNDING(0.53) trên answer có provenance but evidence thiếu nó).
    provenance_suffix = f"(Nguồn dữ liệu: {data['api_name']} — {data['api_url']})"
    terms = extract_salient_terms(question)
    relevant = _select_relevant_text(data["text"], terms)
    # [W2-d5] Relevance gate TRƯỚC khi hardcode PASS: answer compose từ
    # payload data-API phải CHỨA các salient terms của câu hỏi (reuse
    # _terms_covered — quality gate có sẵn). Thực tế W1: "What is the capital
    # of France?" trả summary nước France không nhắc capital/Paris vẫn PASS
    # (PASS ≠ TRUE — DNA #22). Không covered → return None → LLM fallback
    # (đường verify đầy đủ), KHÔNG deliver answer lệch câu hỏi.
    if terms and not _terms_covered(relevant, terms):
        logger.warning(
            "[S24][W2-d5] data-API answer failed relevance gate (terms=%r api=%s) → LLM fallback",
            terms, data["api_name"],
        )
        return None
    final_answer = f"{relevant}\n\n{provenance_suffix}"
    evidence_text = f"{relevant}\n{provenance_suffix}"
    elapsed_ms = (time.time() - started) * 1000
    logger.info(
        "[S24] /ask forked to data-API (no LLM generation): via=%s domain=%s api=%s",
        decision.via, decision.domain, data["api_name"],
    )
    return {
        "verdict": "PASS",
        "final_answer": final_answer,
        "confidence": min(0.9, max(0.6, decision.confidence)),
        "domain": decision.domain,
        "governance_decision": "UPHOLD",
        "reasoning": f"[S24 lookup via {decision.via}] answered from data-API: {data['api_name']}",
        # provenance="input_context_only" — hợp lệ theo checks
        # scp/ask_kernel_adapter.py verify_response (chỉ context-input, 0 LLM gen).
        "v98_classification": {
            "provenance": "input_context_only",
            "route": "lookup_data_api",
            "api_name": data["api_name"],
            "api_url": data["api_url"],
            "via": decision.via,
        },
        # [W2-d5] Marker: response fork đã qua relevance gate — adapter
        # (ask_kernel_adapter) yêu cầu marker này mới coi là already_judged
        # (không còn self-certify cho fork-shaped PASS thiếu gate).
        "relevance_gate": {"checked": bool(terms), "terms": list(terms)},
        # Evidence thô cho verify_response: payload data-API CHÍNH LÀ input
        # context mà answer được compose từ — đưa vào cùng grounding check.
        "data_api_evidence": evidence_text,
        "slm_responses": [],
        "slm_trace": [],
        "elapsed_ms": round(elapsed_ms, 1),
        "session_id": getattr(req, "session_id", None) or "ask-lookup-fork",
        "run_id": "run-lookup-" + uuid.uuid4().hex,
        "trace_id": "trace-lookup-" + uuid.uuid4().hex,
        "run_status": "COMPLETED",
        "ledger_status": "COMMITTED",
    }


__all__ = [
    "LOOKUP",
    "REASONING",
    "RouteDecision",
    "attempt_lookup_fork",
    "classify_l0",
    "classify_l2",
    "classify_l2_async",
    "extract_salient_terms",
    "resolve_lookup_data",
    "route_question",
    "route_question_async",
    "route_stats_snapshot",
    "t2_fork_enabled",
    "t2_min_confidence",
]
