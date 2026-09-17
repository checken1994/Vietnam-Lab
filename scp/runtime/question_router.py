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
fallback_reasons — expose qua GET /v100/routing/stats (admin_v100, verify_admin; nhóm versioned_admin chỉ mount khi SCP_API_PROFILE=full — container core dùng seam question_routing trong GET /health/detailed).

Kill switch: SCP_T2_ROUTER=0 tắt toàn bộ fork (trở lại hành vi cũ).
"""
from __future__ import annotations

import asyncio
import inspect
import json
import logging
import math
import os
import re
import threading
import time
import urllib.parse
import uuid
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("scp.question_router")

LOOKUP = "LOOKUP"
REASONING = "REASONING"

DEFAULT_MIN_CONFIDENCE = 0.6
DEFAULT_LOOKUP_TIMEOUT_SECONDS = 8.0
MAX_LOOKUP_TIMEOUT_SECONDS = 30.0


def lookup_timeout_seconds() -> float:
    """Return the bounded total LOOKUP-fork budget in seconds.

    The data path is synchronous internally, so the async fork must put a
    finite ceiling around the *whole* catalog/fetch/provider cascade. Invalid,
    non-finite, non-positive, or unreasonably large values fail closed to the
    conservative default instead of allowing an unbounded lease hold.
    """
    raw = os.environ.get("SCP_LOOKUP_TIMEOUT_SECONDS", "").strip()
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_LOOKUP_TIMEOUT_SECONDS
    if not math.isfinite(value) or value <= 0 or value > MAX_LOOKUP_TIMEOUT_SECONDS:
        return DEFAULT_LOOKUP_TIMEOUT_SECONDS
    return value


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
# RouteDecision
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class RouteDecision:
    intent: str  # LOOKUP | REASONING
    domain: str  # domain hint (math/geography/...) — 'general' nếu không rõ
    confidence: float
    via: str  # 'l0-keyword' | 'l0-domain' | 'l2-llm' | 'l2-failsafe'
    reason: str = ""


# ---------------------------------------------------------------------------
# L0 — regex intent rules (bilingual EN/VI). Viết mới cho MỤC ĐÍCH INTENT
# (khác mục đích domain classifier đã có); không copy body hàm nào.
# ---------------------------------------------------------------------------
_REASONING_RULES: tuple[tuple[str, str], ...] = (
    # --- math computation ---
    # [S24] phép trừ PHẢI có khoảng trắng quanh toán tử — "CVE-2021-44228",
    # "15-20 người" là khoảng/ID, không phải phép tính.
    (r"\d+\s*[+*/^×÷]\s*\d+|\d+\s+-\s+\d+", "arithmetic"),
    (r"\b(sqrt|gcd|lcm|sin|cos|tan)\s*\(", "math_function"),
    (r"\d+\s*!", "factorial"),
    (r"\b(fibonacci|factorial|giai thừa|[uƯ]cln|bcnn|gcd|lcm)\b", "math_named"),
    (r"\blog\d*\s*(của|of|base)\b", "logarithm"),
    (r"\b\d+\s*(mũ|\^)\s*\d+", "power"),
    (r"\b\d+\s*%\s*của\b|\b\d+\s*percent of\b", "percentage"),
    (r"^(tính|calculate|compute|solve)\s+\d", "compute_command"),
    (r"\b(phương trình|đạo hàm|tích phân|logarit|logarithm|equation|derivative|integral)\b", "math_topic"),
    # --- statistics ---
    (r"\b(trung bình|trung vị|phương sai|độ lệch chuẩn|xác suất|median|mean of|standard deviation|variance|probability|average of)\b", "statistics"),
    (r"\b(min|max|range)\b\s*(của|of)\b", "list_stat"),
    # --- logic ---
    (r"\b(true|false)\s*(and|or|xor)\s*(true|false)\b|\bnot\s+(true|false|\()", "boolean_logic"),
    (r"[a-uw-zA-UW-Z]\s*[<>]\s*[a-uw-zA-UW-Z]", "symbolic_comparison"),
    (r"\bnếu\b.{0,60}\bthì\b|\bif\b.{0,60}\bthen\b", "conditional_reasoning"),
    (r"\btất cả\b.{0,40}\b(đều|là)\b|\ball\s+\w+\s+are\b", "syllogism"),
    (r"\bsuy luận\b|\blogic puzzle\b|\btiền đề\b", "logic_topic"),
    # --- code / generation ---
    (r"\b(viết|write|create|implement|xây dựng)\b.{0,30}\b(hàm|function|code|chương trình|program|script|class|api|algorithm|thuật toán)\b", "code_generation"),
    (r"\bpython\b.{0,30}\b(hàm|function)\b|\bfunction\s+(that|to|which)\b", "code_function"),
)

_LOOKUP_RULES: tuple[tuple[str, str], ...] = (
    (r"\b(what|who|where|when|which|how many|how much|how far|how fast|how tall|how deep|how long)\b", "interrogative_en"),
    # [S24] 'gì/nào/nhất?' — interrogative/superlative facts ("AES là thuật
    # toán gì?", "Kim loại nào nhẹ nhất?", "Lục địa lớn nhất?").
    (r"\b(là gì|ai là|ở đâu|khi nào|năm nào|bao nhiêu|vào năm|mấy|nào|người nào|cái nào|bởi ai|đâu)\b|gì\b|nhất\s*\?", "interrogative_vi"),
    (r"\b(thủ đô|capital of|dân số|population|diện tích|area of|sông|núi)\b", "geography_fact"),
    (r"\b(weather|thời tiết|nhiệt độ|temperature|dự báo|forecast)\b", "weather_fact"),
    (r"\b(giá|price|tỷ giá|exchange rate|tiền tệ|currency|bitcoin|blockchain|chứng khoán|stock market|crypto)\b", "finance_fact"),
    (r"\b(cve|lỗ hổng|vulnerability|malware|ransomware|phishing|https|ssl|tls)\b", "security_fact"),
    # [S24] chemistry facts ("pH của nước tinh khiết?").
    (r"\bph\b|\bhóa học\b|\baxit\b|\bbazơ\b", "chemistry_fact"),
    (r"\b(định nghĩa|nghĩa là|definition of|meaning of)\b", "definition"),
    (r"\b\d+\s*(km|kg|m|cm|mm|mile|inch|foot|feet|yard|gallon|lít|liter|lb|pound|hour|giờ|giây|second|phút|minute|acre|knot|celsius|fahrenheit)\b\s*(bằng|to|sang|=|in)\b", "unit_conversion"),
)

_COMPILED_REASONING: tuple[tuple[re.Pattern[str], str], ...] | None = None
_COMPILED_LOOKUP: tuple[tuple[re.Pattern[str], str], ...] | None = None


def _compiled_rules() -> tuple[
    tuple[tuple[re.Pattern[str], str], ...],
    tuple[tuple[re.Pattern[str], str], ...],
]:
    global _COMPILED_REASONING, _COMPILED_LOOKUP
    if _COMPILED_REASONING is None:
        _COMPILED_REASONING = tuple(
            (re.compile(pattern, re.IGNORECASE), tag)
            for pattern, tag in _REASONING_RULES
        )
    if _COMPILED_LOOKUP is None:
        _COMPILED_LOOKUP = tuple(
            (re.compile(pattern, re.IGNORECASE), tag)
            for pattern, tag in _LOOKUP_RULES
        )
    return _COMPILED_REASONING, _COMPILED_LOOKUP


def _domain_hint(question: str) -> str:
    """Domain hint: detect_domain (shard — design bắt buộc reuse) trước,
    classify_top1 (domain_classifier) khi detect_domain trả 'general' vì
    detect_domain chỉ phủ keyword tiếng Việt. Import lazy, fail-safe."""
    try:
        from scp.core.partition.shard import detect_domain

        domain = detect_domain(question)
        if domain and domain != "general":
            return domain
    except Exception as exc:
        logger.debug("detect_domain unavailable: %s", exc)
    try:
        from scp.data_sources.domain_classifier import classify_top1

        domain = classify_top1(question)
        if domain and domain != "general":
            return domain
    except Exception as exc:
        logger.debug("classify_top1 unavailable: %s", exc)
    return "general"


def classify_l0(question: str) -> RouteDecision | None:
    """L0 keyword/regex cascade. None = bất định → nhường L2."""
    text = (question or "").strip()
    if not text:
        return None
    reasoning_rules, lookup_rules = _compiled_rules()
    lowered = text.lower()
    for pattern, tag in reasoning_rules:
        if pattern.search(lowered):
            return RouteDecision(
                intent=REASONING,
                domain=_domain_hint(text),
                confidence=0.9,
                via="l0-keyword",
                reason=f"reasoning_signal:{tag}",
            )
    for pattern, tag in lookup_rules:
        if pattern.search(lowered):
            return RouteDecision(
                intent=LOOKUP,
                domain=_domain_hint(text),
                confidence=0.75,
                via="l0-keyword",
                reason=f"lookup_signal:{tag}",
            )
    domain = _domain_hint(text)
    if domain != "general":
        # Câu hỏi có domain rõ ràng, không có dấu hiệu tính toán/sáng tạo →
        # mặc định là câu hỏi sự thật (factual lookup) với confidence thấp hơn.
        return RouteDecision(
            intent=LOOKUP,
            domain=domain,
            confidence=0.7,
            via="l0-domain",
            reason=f"domain_hint:{domain}",
        )
    return None


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
    """L2 zero-shot qua synchronous ``LLMGateway.chat_sync``.

    ``LLMGateway.chat`` is async in production.  Calling it from this sync
    entrypoint would only create an un-awaited coroutine and could classify the
    coroutine repr rather than the model response.  The sync contract therefore
    requires ``chat_sync``; a gateway exposing only async ``chat`` fails closed
    without invoking it.  Production /ask uses :func:`classify_l2_async`.
    """
    domain = _domain_hint(question)
    try:
        if gateway is None:
            from scp.llm_gateway import get_gateway

            gateway = get_gateway()
        chat_sync = getattr(gateway, "chat_sync", None)
        if not callable(chat_sync):
            raise TypeError(
                "sync L2 classifier requires gateway.chat_sync; "
                "use classify_l2_async for an async gateway"
            )
        result = chat_sync(
            (question or "")[:500],
            context="",
            system_prompt=_L2_SYSTEM_PROMPT,
            task="route",
        )
        if inspect.isawaitable(result):
            close = getattr(result, "close", None)
            if callable(close):
                close()
            raise TypeError("gateway.chat_sync returned an awaitable")
        answer, provider = result
        if inspect.isawaitable(answer) or inspect.isawaitable(provider):
            for value in (answer, provider):
                close = getattr(value, "close", None)
                if inspect.isawaitable(value) and callable(close):
                    close()
            raise TypeError("gateway.chat_sync returned an awaitable")
    except Exception as exc:
        return _l2_failsafe(question, domain, exc)
    _stats.record_classifier_llm(ok=bool(answer))
    return _parse_l2_answer(str(answer or ""), provider, domain)


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
        return _l2_failsafe(question, domain, exc)
    _stats.record_classifier_llm(ok=bool(answer))
    return _parse_l2_answer(str(answer or ""), provider, domain)


def _l2_failsafe(question: str, domain: str, exc: Exception) -> RouteDecision:
    logger.warning("[S24] L2 classifier LLM call failed (%s: %s)", type(exc).__name__, exc)
    _stats.record_classifier_llm(ok=False)
    reason = "sync_contract_error" if isinstance(exc, TypeError) and "chat_sync" in str(exc) else "llm_error"
    return RouteDecision(REASONING, domain or "general", 0.5, "l2-failsafe", reason)


def _parse_l2_answer(answer_text: str, provider: Any, domain: str) -> RouteDecision:
    text = answer_text.upper()
    if "LOOKUP" in text[:40]:
        return RouteDecision(LOOKUP, domain or "general", 0.8, "l2-llm", f"llm:{provider}")
    if "REASONING" in text[:40]:
        return RouteDecision(REASONING, domain or "general", 0.8, "l2-llm", f"llm:{provider}")
    # Parse fail-safe: đường LLM là hành vi hiện tại — an toàn.
    return RouteDecision(REASONING, domain or "general", 0.5, "l2-failsafe", "unparseable")


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
    """Thread-safe counter snapshot (đọc qua admin route)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.route_counts: dict[str, int] = {}
        self.fallback_reasons: dict[str, int] = {}
        self.llm_bypassed_count = 0
        # Legacy counter: number of LOOKUP decisions that selected the
        # generation fallback path.  ``generation_calls_count`` below records
        # the actual handler invocation at the adapter boundary.
        self.llm_calls_count = 0
        self.generation_calls_count = 0
        self.classifier_llm_calls = 0
        self.classifier_llm_failures = 0
        self.verifier_calls = 0
        self.verifier_failures = 0
        self.lookup_attempts = 0
        self.lookup_success = 0
        self.lookup_fail = 0
        # Per-transport counters are intentionally kept separate: one lookup
        # can inspect multiple catalog entries before the overall result fails.
        self.lookup_fetch_ok = 0
        self.lookup_fetch_fail = 0
        self.lookup_timeout_count = 0
        # [Q07 2026-09-15] Controlled self-correction (Reflection) counters —
        # shared KPI cho benchmark F_self_correction + dashboard. Một attempt
        # = finalize gặp epistemic hold (canonical verify FAIL/UNKNOWN) VÀ thử
        # đúng 1 vòng critique->regenerate. success = round-2 qua CÙNG canonical
        # verify_response (Reflection không tự approve).
        self.correction_attempts = 0
        self.correction_success = 0
        self.correction_fail = 0
        self.correction_timeout = 0
        # [F-2 2026-09-15] Auto-retrieval KPI — đếm ĐÚNG ngữ nghĩa: attempt =
        # một LOOKUP-ask thiếu-context đã qua gate và gọi retrieve; hit = có
        # >=1 chunk trên floor được nạp; empty = retrieve trả 0 hoặc toàn bộ
        # dưới floor (fail-closed, KHÔNG bịa evidence); error = retriever/
        # corpus hỏng. Expose qua /v100/routing/stats (admin).
        self.ask_retrieval_attempts = 0
        self.ask_retrieval_hits = 0
        self.ask_retrieval_empty = 0
        self.ask_retrieval_errors = 0

    def record_ask_retrieval(self, outcome: str) -> None:
        """[F-2] Route one auto-retrieval outcome into counters. Unknown
        outcomes are recorded ONLY as prom labels (defensive: a future typo
        must not silently corrupt the four KPI ints)."""
        with self._lock:
            if outcome == "attempt":
                self.ask_retrieval_attempts += 1
            elif outcome == "hit":
                self.ask_retrieval_hits += 1
            elif outcome == "empty":
                self.ask_retrieval_empty += 1
            elif outcome == "error":
                self.ask_retrieval_errors += 1
        _prom_inc("scp_ask_retrieval_total", {"outcome": outcome})

    def record_route(self, decision: RouteDecision) -> None:
        key = f"{decision.via}:{decision.intent}"
        with self._lock:
            self.route_counts[key] = self.route_counts.get(key, 0) + 1
        _prom_inc("scp_ask_route_decisions_total", {"via": decision.via, "intent": decision.intent})

    def record_classifier_llm(self, ok: bool) -> None:
        with self._lock:
            self.classifier_llm_calls += 1
            if not ok:
                self.classifier_llm_failures += 1

    def record_bypass(self) -> None:
        with self._lock:
            self.llm_bypassed_count += 1
        _prom_inc("scp_ask_llm_bypassed_total")

    def record_llm_call(self) -> None:
        # Kept as the existing fallback-path KPI for compatibility.
        with self._lock:
            self.llm_calls_count += 1
        _prom_inc("scp_ask_llm_calls_total")

    def record_generation_call(self) -> None:
        with self._lock:
            self.generation_calls_count += 1
        _prom_inc("scp_ask_generation_calls_total")

    def record_verifier_call(self, ok: bool) -> None:
        with self._lock:
            self.verifier_calls += 1
            if not ok:
                self.verifier_failures += 1
        _prom_inc("scp_ask_verifier_calls_total")
        if not ok:
            _prom_inc("scp_ask_verifier_failures_total")

    def record_correction_attempt(self) -> None:
        """[Q07] Ghi nhận một vòng self-refine đã được BẮT ĐẦU (epistemic hold
        gặp phải và budget còn). Không có nghĩa answer đã được sửa."""
        with self._lock:
            self.correction_attempts += 1
        _prom_inc("scp_ask_correction_attempts_total")

    def record_correction_result(self, ok: bool, *, timed_out: bool = False) -> None:
        """[Q07] Kết quả của vòng self-refine DUY NHẤT. ok=True khi round-2 qua
        CÙNG canonical verify_response (gate không nới); False khi vẫn fail
        (withhold như cũ) — fail-closed, Reflection không tự approve."""
        with self._lock:
            if ok:
                self.correction_success += 1
            else:
                self.correction_fail += 1
            if timed_out:
                self.correction_timeout += 1
        _prom_inc("scp_ask_correction_total", {"outcome": "success" if ok else "fail"})
        if timed_out:
            _prom_inc("scp_ask_correction_timeouts_total")

    def record_lookup_attempt(self) -> None:
        with self._lock:
            self.lookup_attempts += 1

    def record_lookup_result(self, ok: bool, *, timed_out: bool = False) -> None:
        with self._lock:
            if ok:
                self.lookup_success += 1
            else:
                self.lookup_fail += 1
            if timed_out:
                self.lookup_timeout_count += 1

    def record_fallback(self, reason: str) -> None:
        key = str(reason)[:80]
        with self._lock:
            self.fallback_reasons[key] = self.fallback_reasons.get(key, 0) + 1

    def record_fetch(self, ok: bool) -> None:
        with self._lock:
            if ok:
                self.lookup_fetch_ok += 1
            else:
                self.lookup_fetch_fail += 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            snapshot = {
                "llm_bypassed_count": self.llm_bypassed_count,
                # Legacy fallback-path counter.  Use generation_calls_count for
                # actual handler invocations and lookup_* for the data path.
                "llm_calls_count": self.llm_calls_count,
                "generation_calls_count": self.generation_calls_count,
                "generation_llm_calls": self.generation_calls_count,
                "classifier_llm_calls": self.classifier_llm_calls,
                "classifier_llm_failures": self.classifier_llm_failures,
                "verifier_calls": self.verifier_calls,
                "verifier_failures": self.verifier_failures,
                "lookup_attempts": self.lookup_attempts,
                "lookup_success": self.lookup_success,
                "lookup_fail": self.lookup_fail,
                "lookup_timeout_count": self.lookup_timeout_count,
                "lookup_fetch_ok": self.lookup_fetch_ok,
                "lookup_fetch_fail": self.lookup_fetch_fail,
                "correction_attempts": self.correction_attempts,
                "correction_success": self.correction_success,
                "correction_fail": self.correction_fail,
                "correction_timeout": self.correction_timeout,
                "ask_retrieval_attempts": self.ask_retrieval_attempts,
                "ask_retrieval_hits": self.ask_retrieval_hits,
                "ask_retrieval_empty": self.ask_retrieval_empty,
                "ask_retrieval_errors": self.ask_retrieval_errors,
                "route_counts": dict(self.route_counts),
                "fallback_reasons": dict(self.fallback_reasons),
            }
        snapshot["bypass_ratio"] = round(
            snapshot["llm_bypassed_count"]
            / max(1, snapshot["llm_bypassed_count"] + snapshot["llm_calls_count"]),
            4,
        )
        # [Q07] correction_success_rate phản ánh ĐÚNG semantic benchmark
        # F_self_correction chỉ trên các attempt đã diễn ra (không phải trên
        # tổng mọi ask). Khi chưa có attempt nào -> None (không phải 0.0), để
        # dashboard không nhầm "0%" với "chưa đo".
        attempts = snapshot["correction_attempts"]
        snapshot["correction_success_rate"] = (
            round(snapshot["correction_success"] / attempts, 4) if attempts else None
        )
        return snapshot


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
        logger.debug("prometheus counter unavailable: %s", name)


def route_stats_snapshot() -> dict[str, Any]:
    """Snapshot KPI cho admin stats route (seam sạch, đã auth)."""
    return _stats.snapshot()


def record_generation_call() -> None:
    """Record an actual generation-handler invocation."""
    _stats.record_generation_call()


def record_verifier_call(ok: bool) -> None:
    """Record an observed verifier invocation and outcome."""
    _stats.record_verifier_call(ok=ok)


def record_correction_attempt() -> None:
    """[Q07] Module seam cho scp/ask_kernel_adapter: một vòng self-refine bắt đầu."""
    _stats.record_correction_attempt()


def record_correction_result(ok: bool, *, timed_out: bool = False) -> None:
    """[Q07] Module seam: kết quả vòng self-refine (canonical re-verify)."""
    _stats.record_correction_result(ok=ok, timed_out=timed_out)


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
        "astronomy", "medical", "technology", "general",
    }
)


def _catalog_entry_is_auth_free(entry: Any) -> bool:
    """Return True only for an explicitly keyless catalog entry.

    Missing/unknown auth metadata is intentionally unsafe here.  The owner
    directive is data-API-first *without hidden credential acquisition*; an
    entry that might require a key must never reach the transport path.
    """
    return str((entry or {}).get("auth", "")).strip().lower() == "no"


def _catalog_candidates(terms: list[str]) -> tuple[list[dict[str, Any]], str]:
    """Search only the catalog's explicitly ``auth=No`` entries.

    The previous progressive fallback used ``auth=None`` after the keyless
    search.  That made an auth-required API look selectable and allowed the
    generic fetcher to call it without a credential.  Every query now remains
    constrained to ``auth=No`` and results are filtered again at this boundary
    because test doubles/alternate catalog implementations may ignore filters.
    """
    if not terms:
        return [], ""
    try:
        from scp.data_sources.free_api_catalog import get_catalog

        catalog = get_catalog()
        saw_auth_required = False
        for query in (" ".join(terms), terms[0]):
            entries = list(catalog.search(query=query, auth="No", limit=10) or [])
            safe_entries = []
            for entry in entries:
                if _catalog_entry_is_auth_free(entry):
                    safe_entries.append(entry)
                else:
                    saw_auth_required = True
            if safe_entries:
                return safe_entries, f"{query}|auth=No"
        if saw_auth_required:
            _stats.record_fallback("auth_required_catalog_entry_skipped")
            logger.info("[S24] skipped auth-required catalog entries; no safe auth=No match")
    except Exception as exc:
        logger.warning("[S24] catalog search failed (%s: %s)", type(exc).__name__, exc)
        _stats.record_fallback(f"catalog_search_error:{type(exc).__name__}")
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
        logger.debug("[S24] egress blocked %s: %s", urllib.parse.urlsplit(url).hostname, exc)
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
        logger.info("[S24] data fetch failed %s (%s: %s)", url, type(exc).__name__, exc)
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
        logger.warning("[S24] wiki lookup failed (%s: %s)", type(exc).__name__, exc)
    return None


def _generic_entry_lookup(entries: list[dict[str, Any]], terms: list[str]) -> dict[str, Any] | None:
    """Fetch entry catalog khớp (egress-gated). JSON/HTML → text → term gate."""
    for entry in entries:
        url = str(entry.get("url") or "").strip()
        name = str(entry.get("name") or "unknown")
        try:
            parsed = urllib.parse.urlsplit(url)
        except ValueError:
            parsed = None
        if parsed is None or parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            _stats.record_fetch(ok=False)
            _stats.record_fallback(f"invalid_url:{name[:40]}")
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


def resolve_lookup_data(
    question: str,
    domain: str = "general",
    decision: RouteDecision | None = None,
) -> dict[str, Any] | None:
    """Nhánh data-API: catalog search → generic fetch → provider encyclopedic.

    Trả {'text','api_name','api_url','evidence'} hoặc None (fallback LLM).
    KHÔNG gọi API ngoài search match / provider được catalog liệt kê.
    """
    terms = extract_salient_terms(question)
    if not terms:
        return None
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
    _stats.record_lookup_attempt()
    lookup_budget = lookup_timeout_seconds()
    try:
        # ``resolve_lookup_data`` is intentionally synchronous because the
        # catalog and canonical clients expose blocking APIs.  Never call it
        # directly from this async path: one slow DNS/HTTP call would pause
        # both the request and the S20 lease heartbeat on the event loop.
        data = await asyncio.wait_for(
            asyncio.to_thread(
                resolve_lookup_data,
                question,
                domain=decision.domain,
                decision=decision,
            ),
            timeout=lookup_budget,
        )
    except asyncio.TimeoutError:
        _stats.record_lookup_result(False, timed_out=True)
        _stats.record_fallback("lookup_timeout")
        _stats.record_llm_call()
        logger.warning(
            "[S24] LOOKUP fork timed out after %.3fs (via=%s domain=%s) → LLM fallback",
            lookup_budget, decision.via, decision.domain,
        )
        return None
    except Exception as exc:
        # The fork is an optional read path.  Any worker/transport failure is
        # fail-closed for the lookup and falls back to the existing generation
        # handler; the exception must not escape and break /ask.
        _stats.record_lookup_result(False)
        _stats.record_fallback(f"lookup_error:{type(exc).__name__}")
        _stats.record_llm_call()
        logger.warning(
            "[S24] LOOKUP fork failed (%s: %s) → LLM fallback",
            type(exc).__name__, exc,
        )
        return None
    if data is None:
        _stats.record_lookup_result(False)
        _stats.record_llm_call()
        logger.info(
            "[S24] LOOKUP fork miss (via=%s domain=%s reason=%s) → LLM fallback",
            decision.via, decision.domain, decision.reason,
        )
        return None

    _stats.record_lookup_result(True)
    _stats.record_bypass()
    # Provenance suffix là PHẦN CỦA answer; evidence đưa vào verification phải
    # chứa cả suffix, nếu không Tier-1 grounding của judge REJECT vì các từ
    # "Nguồn dữ liệu..." không có trong context (bug phát hiện bằng test S24:
    # REJECT_GROUNDING(0.53) trên answer có provenance but evidence thiếu nó).
    provenance_suffix = f"(Nguồn dữ liệu: {data['api_name']} — {data['api_url']})"
    relevant = _select_relevant_text(data["text"], extract_salient_terms(question))
    final_answer = f"{relevant}\n\n{provenance_suffix}"
    evidence_text = f"{relevant}\n{provenance_suffix}"
    elapsed_ms = (time.time() - started) * 1000
    # [R1 2026-09-15] Fix benchmark NF-1: fork CÓ provenance thật — payload
    # data-API đã đi vào canonical verify_response qua ``data_api_evidence``
    # — nhưng trước đây trả ``slm_trace: []``. HTTP serialization của
    # AskResponse lại KHÔNG giữ field ``data_api_evidence`` (không có trong
    # model; pydantic drop extra), nên surface bằng chứng duy nhất mà harness
    # D_evidence_recall nhìn thấy là ``slm_trace`` → fork đóng góp 0 dù hệ
    # retrieval/data-API hoạt động. Đây là TRINH BÀY lại đúng payload mà
    # verifier đã chấm, theo ĐÚNG convention của _ask_impl (canonical_bm25:
    # domain/slm_name/answer[:200]/confidence/source/evidence/
    # processing_time_ms) — metric không phạt fork vì định dạng field.
    # KHÔNG phải input mới cho bất kỳ quyết định nào (verdict đã chốt ở trên),
    # KHÔNG thay đổi/suy yếu data_api_evidence path của verify_response.
    # answer[:200] cùng trần cắt với canonical_bm25; text đầy đủ nằm trong
    # evidence.text cho runner đọc cả payload (fold rule).
    fork_slm_trace: list[dict[str, Any]] = []
    if evidence_text.strip():
        fork_slm_trace.append({
            "domain": decision.domain,
            "slm_name": "lookup_data_api",
            "answer": evidence_text[:200],
            "confidence": min(0.9, max(0.6, decision.confidence)),
            "source": "data-api",
            "evidence": {
                "source_url": str(data.get("api_url") or ""),
                "api_name": str(data.get("api_name") or ""),
                "route": "lookup_data_api",
                "text": evidence_text,
            },
            "processing_time_ms": round(elapsed_ms, 1),
        })
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
        # Evidence thô cho verify_response: payload data-API CHÍNH LÀ input
        # context mà answer được compose từ — đưa vào cùng grounding check.
        "data_api_evidence": evidence_text,
        "slm_responses": [],
        "slm_trace": fork_slm_trace,
        "elapsed_ms": round(elapsed_ms, 1),
        "session_id": getattr(req, "session_id", None) or "ask-lookup-fork",
        "run_id": "run-lookup-" + uuid.uuid4().hex,
        "trace_id": "trace-lookup-" + uuid.uuid4().hex,
        "run_status": "COMPLETED",
        "ledger_status": "COMMITTED",
    }


# ---------------------------------------------------------------------------
# [F-2 2026-09-15] Auto-retrieval cho LOOKUP ask thiếu-context — wire
# CanonicalRetriever (Q08 BM25) vào đúng grounding/verify path của /ask.
#
# TẠI SAO (F-2 HIGH, Q08 report): retriever đã recall@5=0.98 trên fixture
# corpus nhưng /ask KHÔNG gọi nó ở đâu cả — evidence_recall e2e (bench
# 2026-09-12: 0.3846) do đó bất kể chất lượng retriever. Seam này nạp bằng
# chứng corpus cho CÙNG grounding check đã tồn tại (tier1 REJECT_GROUNDING +
# judge context + AskKernelAdapter.verify_response) — THÊM bằng chứng, không
# bỏ/nới bất kỳ check nào.
#
# Bất biến chống vòng lặp xác nhận:
#   * bằng chứng được chọn theo QUESTION (BM25 lexical), không theo answer —
#     verifier không thể tự duyệt context do chính candidate answer sinh ra;
#   * answer vẫn đi qua đúng judge + governance + canonical verify cũ;
#   * empty corpus / mọi hit dưới min-score / lỗi / timeout → [] — KHÔNG bịa
#     evidence, hành vi y hệt trước khi nối (fail-closed). Khi corpus prod
#     absent (checkout này), đây chính là đường đi mặc định.
# Hệ quả SIẾT (không phải nới): với LOOKUP có bằng chứng, is_rag_ask flip ->
# True và tier1 check_grounding trở nên ÁP DỤNG (context rỗng trước đó khiến
# nó bị bỏ qua) — answer không được corpus đỡ sẽ bị REJECT_GROUNDING.
# Vector/hybrid: không — cùng dependency-policy đã chốt ở Q08.
# ---------------------------------------------------------------------------
DEFAULT_ASK_RETRIEVAL_K = 5
MAX_ASK_RETRIEVAL_K = 8
DEFAULT_ASK_RETRIEVAL_MIN_SCORE = 1.0
DEFAULT_ASK_RETRIEVAL_TIMEOUT_SECONDS = 10.0
MAX_ASK_RETRIEVAL_TIMEOUT_SECONDS = 30.0
# Budget nạp vào judge/verify context (scp-safe-latency-optimizer: không phình
# prompt): mỗi chunk cắt theo trần đơn, tổng các chunk bị trần tổng chặn.
ASK_RETRIEVAL_CHUNK_MAX_CHARS = 2400
ASK_RETRIEVAL_TOTAL_MAX_CHARS = 8000


def ask_retrieval_enabled() -> bool:
    """Kill switch SCP_ASK_RETRIEVAL=0|false|off|no — tắt auto-retrieval, /ask
    trở lại đúng hành vi trước F-2 (không retrieve, không thêm contexts)."""
    raw = os.environ.get("SCP_ASK_RETRIEVAL", "").strip().lower() or "1"
    return raw not in {"0", "false", "off", "no"}


def ask_retrieval_k() -> int:
    """SCP_ASK_RETRIEVAL_K (default 5, clamp 1..8 — cùng cap retriever.k)."""
    raw = os.environ.get("SCP_ASK_RETRIEVAL_K", "").strip()
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_ASK_RETRIEVAL_K
    if value < 1 or value > MAX_ASK_RETRIEVAL_K:
        return DEFAULT_ASK_RETRIEVAL_K
    return value


def ask_retrieval_min_score() -> float:
    """BM25 floor — hit dưới ngưỡng bị coi là không phải bằng chứng.

    Chọn theo SỐ, không đoán: Lucene-idf ``ln(1+(N-df+0.5)/(df+0.5))`` đạt
    ~0.69 ngay cả khi term phủ ~50% corpus (worst-case term phổ biến nhất);
    floor 1.0 chặn các hit chỉ-do-token-chung. Đo trên fixture Q08 (266
    chunks / 319 probes, 2026-09-15): min top-1 score mọi nhóm = 5.51 →
    floor 1.0 giữ 319/319 probe, cách xa mép dưới thật. corpus prod có N lớn
    hơn chỉ làm idf của term ĐẶC TRƯNG cao hơn, không thấp hơn. Env
    rác/âm/inf/nan/quá khổ → fail-closed về default; 0 hợp lệ (chặn
    empty-hit vẫn còn guard bên dưới)."""
    raw = os.environ.get("SCP_ASK_RETRIEVAL_MIN_SCORE", "").strip()
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_ASK_RETRIEVAL_MIN_SCORE
    if not math.isfinite(value) or value < 0 or value > 1_000_000:
        return DEFAULT_ASK_RETRIEVAL_MIN_SCORE
    return value


def ask_retrieval_timeout_seconds() -> float:
    """Trần thời gian cho MỘT lượt auto-retrieval (cold corpus load trên
    corpus prod lớn là rủi ro thật). Invalid/out-of-range → default 10s;
    quá hạn → caller (adapter) fail-closed về hành vi không-evidence."""
    raw = os.environ.get("SCP_ASK_RETRIEVAL_TIMEOUT_SECONDS", "").strip()
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_ASK_RETRIEVAL_TIMEOUT_SECONDS
    if not math.isfinite(value) or value <= 0 or value > MAX_ASK_RETRIEVAL_TIMEOUT_SECONDS:
        return DEFAULT_ASK_RETRIEVAL_TIMEOUT_SECONDS
    return value


def format_canonical_context(hit: dict[str, Any]) -> str:
    """Canonical evidence string — cùng format với ``CanonicalRetriever.contexts()``
    (``[chunk_id=...] source_url=...\\n<text>``) để một nguồn sự thật duy nhất."""
    return f"[chunk_id={hit.get('chunk_id')}] source_url={hit.get('source_url')}\n{hit.get('text', '')}"


def attempt_canonical_retrieval(question: str) -> list[dict[str, Any]]:
    """[F-2] BM25 hits trên canonical corpus cho một LOOKUP ask thiếu-context.

    Trả về list hit (đã lọc score-floor, đã cắt budget) hoặc [] =
    fail-closed — [] trả về khi: kill-switch off, L0 không/DƯỚI intent
    LOOKUP hoặc confidence < SCP_T2_MIN_CONFIDENCE, retriever empty, mọi hit
    dưới floor, hoặc exception. KHÔNG gọi L2 LLM cho quyết định này (thêm
    30-260s latency + chi phí cho một read path phụ — sai
    scp-safe-latency-optimizer). Retriever-level KPI qua RouteStats.
    """
    if not ask_retrieval_enabled():
        return []
    text = (question or "").strip()
    if not text:
        return []
    decision = classify_l0(text)
    if (
        decision is None
        or decision.intent != LOOKUP
        or decision.confidence < t2_min_confidence()
    ):
        return []
    _stats.record_ask_retrieval("attempt")
    try:
        from scp.rag.canonical_retriever import get_canonical_retriever

        hits = get_canonical_retriever().retrieve(text, k=ask_retrieval_k())
    except Exception as exc:
        logger.warning(
            "[F-2] canonical retrieval failed (%s: %s) — ask continues WITHOUT evidence (fail-closed)",
            type(exc).__name__, exc,
        )
        _stats.record_ask_retrieval("error")
        return []
    floor = ask_retrieval_min_score()
    kept: list[dict[str, Any]] = []
    total = 0
    for hit in hits or []:
        try:
            score = float(hit.get("retrieval_score") or 0.0)
        except (TypeError, ValueError):
            continue
        if score < floor:
            continue
        body = str(hit.get("text") or "").strip()
        if not body:
            continue
        body = _trim(body, ASK_RETRIEVAL_CHUNK_MAX_CHARS)
        if total + len(body) > ASK_RETRIEVAL_TOTAL_MAX_CHARS:
            break
        total += len(body)
        kept.append({**hit, "text": body})
    if kept:
        _stats.record_ask_retrieval("hit")
        logger.info(
            "[F-2] auto-retrieved %d/%d canonical chunk(s) (via=%s floor=%.2f top_score=%s)",
            len(kept), len(hits or []), decision.via, floor,
            (kept[0].get("retrieval_score") if kept else None),
        )
    else:
        _stats.record_ask_retrieval("empty")
        logger.info(
            "[F-2] canonical retrieval returned no above-threshold evidence (hits=%d floor=%.2f) — NO fabricated evidence",
            len(hits or []), floor,
        )
    return kept


__all__ = [
    "LOOKUP",
    "REASONING",
    "RouteDecision",
    "attempt_canonical_retrieval",
    "attempt_lookup_fork",
    "ask_retrieval_enabled",
    "ask_retrieval_k",
    "ask_retrieval_min_score",
    "ask_retrieval_timeout_seconds",
    "classify_l0",
    "classify_l2",
    "classify_l2_async",
    "extract_salient_terms",
    "format_canonical_context",
    "resolve_lookup_data",
    "route_question",
    "route_question_async",
    "route_stats_snapshot",
    "record_generation_call",
    "record_verifier_call",
    "record_correction_attempt",
    "record_correction_result",
    "lookup_timeout_seconds",
    "t2_fork_enabled",
    "t2_min_confidence",
]
