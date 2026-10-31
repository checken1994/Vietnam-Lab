"""Question Router L0 Classification Rules, Language Detection, and Types.

Extracted from scp.runtime.question_router to enforce modular separation
of concerns (< 1,000 LOC ceiling).
"""
from __future__ import annotations

import json
import logging
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("scp.question_router")


class Intent(str):
    """Interoperable intent string supporting both legacy and lane-based comparisons."""

    def __eq__(self, other: object) -> bool:
        if super().__eq__(other):
            return True
        val = str(self)
        if val in ("REASONING", "LANE_CHATBOT", "CHATBOT") and other in (
            "REASONING",
            "LANE_CHATBOT",
            "CHATBOT",
        ):
            return True
        if val in ("LOOKUP", "LANE_FACTUAL", "FACTUAL") and other in (
            "LOOKUP",
            "LANE_FACTUAL",
            "FACTUAL",
        ):
            return True
        if val in ("SECURITY", "LANE_SECURITY") and other in ("SECURITY", "LANE_SECURITY"):
            return True
        return False

    def __hash__(self) -> int:
        return super().__hash__()


LANE_CHATBOT = "LANE_CHATBOT"
LANE_FACTUAL = "LANE_FACTUAL"
LANE_SECURITY = "LANE_SECURITY"

LOOKUP = Intent("LOOKUP")
REASONING = Intent("REASONING")
SECURITY = Intent("SECURITY")

DEFAULT_MIN_CONFIDENCE = 0.6


# ---------------------------------------------------------------------------
# Dynamic Language Detection
# ---------------------------------------------------------------------------
_VIETNAMESE_DIACRITICS = re.compile(
    r"[àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵđ"
    r"ÀÁẢÃẠĂẰẮẲẴẶÂẦẤẨẪẬÈÉẺẼẸÊỀẾỂỄỆÌÍỈĨỊÒÓỎÕỌÔỒỐỔỖỘƠỜỚỞỠỢÙÚỦŨỤƯỪỨỬỮỰỲÝỶỸỴĐ]"
)
_VIETNAMESE_WORDS = frozenset({
    "là", "và", "của", "những", "các", "có", "được", "cho", "trong", "một", "này", "người", "với", "khi", "như", "để",
    "không", "bạn", "tôi", "ai", "gì", "thế", "nào", "sao", "ở", "đâu", "tại", "mấy", "thì", "đã", "sẽ", "về", "hãy",
    "chào", "xin", "cảm", "ơn", "tốt", "rất", "làm", "biết",
})
_NON_LATIN_SCRIPT = re.compile(
    r"[\u0400-\u04FF\u4E00-\u9FFF\u0600-\u06FF\uAC00-\uD7AF\u3040-\u30FF]"
)


# ---------------------------------------------------------------------------
# Pre-normalization (stripping zero-width, bidi, soft-hyphen, tags, and confusables)
# ---------------------------------------------------------------------------
_INVISIBLE_CHARS_PATTERN = re.compile(
    r"[\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff\u00ad\U000e0000-\U000e007f]"
)
_HOMOGLYPH_MAP = str.maketrans({
    "\u0430": "a", "\u0410": "A", "\u0435": "e", "\u0415": "E", "\u043e": "o", "\u041e": "O",
    "\u0440": "p", "\u0420": "P", "\u0441": "c", "\u0421": "C", "\u0443": "y", "\u0423": "Y",
    "\u0445": "x", "\u0425": "X", "\u0456": "i", "\u0406": "I", "\u0455": "s", "\u0405": "S",
    "\u0458": "j", "\u0408": "J", "\u03bf": "o", "\u039f": "O", "\u03b1": "a", "\u0391": "A",
    "\u03c1": "p", "\u03a1": "P", "\u03bd": "v", "\u039d": "N", "\u03ba": "k", "\u039a": "K",
})
_CYRILLIC_HOMOGLYPH_MAP = _HOMOGLYPH_MAP


def strip_invisible_chars(text: str) -> str:
    """Strip zero-width spaces, word joiners, bidi overrides/isolates, tags, and soft hyphens."""
    if not text:
        return ""
    return _INVISIBLE_CHARS_PATTERN.sub("", text)


def normalize_homoglyphs(text: str) -> str:
    """Normalize Cyrillic and Greek confusable homoglyphs to standard ASCII characters."""
    if not text:
        return ""
    return text.translate(_HOMOGLYPH_MAP)


def to_unaccented(text: str) -> str:
    """Convert Vietnamese accented text to unaccented representation."""
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFD", text)
    unaccented = "".join(c for c in decomposed if unicodedata.category(c) != "Mn")
    return unaccented.replace("đ", "d").replace("Đ", "D")


def normalize_query(text: str) -> str:
    """Pre-normalize query: strip evasion chars, map lookalike homoglyphs, and NFKC normalize."""
    if not text:
        return ""
    stripped = strip_invisible_chars(text)
    trans = normalize_homoglyphs(stripped)
    return unicodedata.normalize("NFKC", trans).strip()


def detect_language(text: str) -> str:
    """Dynamic language detection: 'vi', 'en', or 'other'."""
    raw = (text or "").strip()
    if not raw:
        return "en"
    if _NON_LATIN_SCRIPT.search(raw):
        return "other"
    if _VIETNAMESE_DIACRITICS.search(raw):
        return "vi"
    words = re.findall(r"\b[a-zA-Z]+\b", raw.lower())
    vi_word_count = sum(1 for w in words if w in _VIETNAMESE_WORDS)
    if vi_word_count >= 2 or (len(words) <= 3 and vi_word_count >= 1):
        return "vi"
    return "en"


# ---------------------------------------------------------------------------
# RouteDecision
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class RouteDecision:
    intent: str  # LOOKUP | REASONING | SECURITY
    domain: str  # domain hint (math/geography/...) — 'general' nếu không rõ
    confidence: float
    via: str  # 'l0-keyword' | 'l0-domain' | 'l2-llm' | 'l2-failsafe' | 'l0-security'
    reason: str = ""
    lane: str = LANE_CHATBOT  # LANE_CHATBOT | LANE_FACTUAL | LANE_SECURITY
    language: str = "en"  # "vi" | "en" | "other"

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": str(self.intent),
            "lane": self.lane,
            "language": self.language,
            "confidence": self.confidence,
            "domain": self.domain,
            "via": self.via,
            "reason": self.reason,
        }


# ---------------------------------------------------------------------------
# L0 — regex intent & security rules (bilingual EN/VI).
# ---------------------------------------------------------------------------
_SECURITY_RULES: tuple[tuple[str, str], ...] = (
    (r"\b(ignore\s+(all\s+)?previous\s+instructions|system\s+prompt|dan\s+mode|jailbreak)\b", "prompt_injection"),
    (r"\b(bỏ\s+qua\s+(toàn\s+bộ\s+)?(hướng\s+dẫn|chỉ\s+dẫn)|lệnh\s+hệ\s+thống)\b", "prompt_injection_vi"),
    (r"\b(reveal\s+(api\s+)?key|show\s+(your\s+)?hidden\s+prompt)\b", "secret_leak"),
    (r"(\b(scp[-_\s]*(admin[-_\s]*key|jwt[-_\s]*secret|capability[-_\s]*secret)|(?:openai|gemini|anthropic|api)[-_\s]*key|ad[o\u03bf]?min[-_\s]*key|jwt[-_\s]*secret|secret[-_\s]*key|private[-_\s]*key|access[-_\s]*token|credentials)\b|(?<![a-zA-Z0-9_-])\.env(?!\.(?:template|example|sample|dist|default))\b|(?:mật\s*khẩu|mat\s*khau)\s*(?:admin|quản\s*trị|quan\s*tri))", "secret_fishing"),
    (r"\b(cat\s+/etc/(passwd|shadow)|rm\s+-rf\b|powershell\s+-enc|format\s+c:)\b", "dangerous_command"),
    (r"(\bunion\s+select\b|\bselect\s+\*\s+from\s+users|\bdrop\s+table\b)", "sql_injection"),
)

# [L0-DEKEYWORD 2026-10-31] Luật owner cấp cao nhất (luật 9) + đa quốc gia/đa ngôn
# ngữ: SCP KHÔNG chứa mẫu câu hỏi / danh sách từ khoá theo ngôn ngữ để định tuyến.
# Trước đây L0 còn ~60 regex từ khoá CHỈ tiếng Việt + tiếng Anh (``_LOOKUP_RULES``
# interrogative_vi/en + "từ miền", ``_CREATIVE_RULES``, ``_REASONING_WEAK_RULES``,
# phần chữ của ``_REASONING_STRONG_RULES``, ``pH``, domain-hint → LOOKUP). Hệ quả đo
# được trên uvicorn thật: câu hỏi tên/danh tính tiếng Việt và tiếng Anh bị L0 chốt
# FACTUAL/lookup TRƯỚC khi tầng ngữ nghĩa (L1 nói CHAT@1.0) kịp xét, trong khi cùng câu
# ở 10 ngôn ngữ khác đi tầng ngữ nghĩa ⇒ hành vi phụ thuộc ngôn ngữ; và yêu cầu viết
# code/thơ VI/EN bị L0 đẩy LANE_CHATBOT không cần đồng thuận kép (fail-open).
#
# L0 giờ CHỈ còn 2 loại tín hiệu KHÔNG phụ thuộc ngôn ngữ tự nhiên:
#   (1) chữ ký an ninh (``_SECURITY_RULES`` + detector) — chỉ đẩy lane CHẶT HƠN;
#   (2) cú pháp ký hiệu số học (không có chữ cái nào) — chỉ đẩy LANE_FACTUAL.
# Mọi câu ngôn ngữ tự nhiên ⇒ ``None`` ⇒ tầng ngữ nghĩa (L1 embedding đa ngôn ngữ →
# đồng thuận kép L1+L2 cho CHATBOT) rồi L2 phân loại, fail-closed LANE_FACTUAL.
# KHÔNG thêm danh sách từ nào vào đây (test chống tái mã hoá cứng
# tests/T02_contract/test_l0_dekeyword_routing.py quét các bảng này).
_SYMBOLIC_SYNTAX_RULES: tuple[tuple[str, str], ...] = (
    (r"\d+\s*[+*/^×÷]\s*\d+|\d+\s+-\s+\d+", "arithmetic"),
    # Ký pháp hàm f(số): một định danh (bất kỳ hệ chữ nào — ``[^\W\d_]`` không liệt kê
    # chữ cụ thể) dính liền "(" + đối số THUẦN SỐ. Cấu trúc, không phải danh sách tên hàm.
    (r"[^\W\d_]{2,}\(\s*\d+(?:\.\d+)?\s*\)", "math_function"),
    (r"\d+\s*!", "factorial"),
)

#: Tag lookup_stage coi là phép tính ký hiệu (bỏ qua evidence retrieval). Giữ
#: nguyên chuỗi tag cũ để consumer (``pipeline/lookup_stage.py``) không đổi.
_ARITHMETIC_STRONG_TAGS: frozenset[str] = frozenset({"arithmetic", "math_function", "factorial"})

_CONCEPTUAL_PATTERN_STR = (
    r"(?:\b(?:khái\s+niệm|concept|giải\s+thích|explain|khác\s+(?:\w+\s+){0,4}(?:thế\s+nào|gì|như\s+thế\s+nào)|difference\s+between|different\s+from|differences?\s+in\b|"
    r"(?:admin\s+)?password\s+policy(?:\s+in\b)?|(?:security\s+)?policy\s+in\b|(?:variables?|entries?|keys?)\s+belong(?:\s+in\b)?|"
    r"(?:cách|làm\s+sao|làm\s+thế\s+nào|hướng\s+dẫn)\s+(?:để\s+)?(?:bảo\s+vệ|xoay\s+vòng|quản\s+lý)|(?:show\s+me\s+)?how\s+to\s+(?:rotate|protect|secure|manage|store|configure|use)\b|"
    r"(?:bảo\s+vệ|xoay\s+vòng)\s+.*(?:như\s+thế\s+nào|thế\s+nào|sao)|là\s+gì\s+và\s+(?:cách|làm\s+sao|làm\s+thế\s+nào)|what\s+is\s+.*and\s+how|\b(?:safely|best\s+practices?|guidelines?)\b)\b|(?<!\w)\.env\.(?:template|example|sample|dist|default)\b)"
)
_CONCEPTUAL_EXPLANATION_PATTERN = re.compile(_CONCEPTUAL_PATTERN_STR, re.IGNORECASE)
_CONCEPTUAL_EXPLANATION_UNACCENTED = re.compile(to_unaccented(_CONCEPTUAL_PATTERN_STR), re.IGNORECASE)


_SECRET_INDICATORS_PATTERN = re.compile(
    r"(?<![a-zA-Z0-9_-])\.env(?!\.(?:template|example|sample|dist|default))\b|"
    r"(?<![a-zA-Z0-9])(?:scp[-_\s]*)?(?:key|keys|token|tokens|secret|secrets|admin|jwt|credential|credentials|password)(?![a-zA-Z0-9])",
    re.IGNORECASE,
)
_EXTRACTION_TERMS_PATTERN = re.compile(
    r"\b(print|xem|cat|dump|show(?!\s+(?:me\s+)?how\s+to\b)|lấy|đọc|nội\s+dung|extract|reveal|display|output|đưa\s+ra|lộ|xuất|export|hiển\s+thị|trích\s+xuất|gửi|send|chuyển|cung\s+cấp|provide)\b",
    re.IGNORECASE,
)
_EXTRACTION_TERMS_UNACCENTED = re.compile(
    r"\b(print|xem|cat|dump|show(?!\s+(?:me\s+)?how\s+to\b)|lay|doc|noi\s+dung|extract|reveal|display|output|dua\s+ra|lo|xuat|export|hien\s+thi|trich\s+xuat|gui|send|chuyen|cung\s+cap|provide)\b",
    re.IGNORECASE,
)
_EXTRACTION_IN_PATTERN = re.compile(
    r"\bin\s+(?:ra\b|(?:\w+\s+){0,6}(?:\.env|key|keys|secret|secrets|token|tokens|mật\s*khẩu|mat\s*khau|admin)\b)",
    re.IGNORECASE,
)

_BENIGN_CONCEPTUAL_PHRASES = re.compile(
    r"\b(differences?\s+in|difference\s+between|different\s+from|(?:admin\s+)?password\s+policy|security\s+policy|policy\s+in|variables?\s+belong(?:\s+in)?|belong\s+in|how\s+to\s+rotate|rotate\s+(?:api\s+)?keys?|safely|best\s+practices?|guidelines?)\b",
    re.IGNORECASE,
)


def has_extraction_intent(text: str, unaccented_text: str | None = None) -> bool:
    """Check if query contains extraction terms combined with secret indicators."""
    if not text:
        return False
    if not _SECRET_INDICATORS_PATTERN.search(text):
        return False
    if _BENIGN_CONCEPTUAL_PHRASES.search(text):
        explicit_attack = re.search(
            r"\b(cat|dump|reveal|export|trích\s+xuất|trich\s+xuat|làm\s+lộ|lam\s+lo|in\s+ra|hiển\s+thị|hien\s+thi)\b",
            text,
            re.IGNORECASE,
        )
        if not explicit_attack:
            return False
    if _EXTRACTION_TERMS_PATTERN.search(text) or _EXTRACTION_IN_PATTERN.search(text):
        return True
    if unaccented_text is None:
        unaccented_text = to_unaccented(text)
    return bool(_EXTRACTION_TERMS_UNACCENTED.search(unaccented_text) or _EXTRACTION_IN_PATTERN.search(unaccented_text))


def is_conceptual_question(text: str, unaccented_text: str | None = None) -> bool:
    """Check if query asks for explanation/comparison/protection rather than secret extraction."""
    if not text:
        return False
    if has_extraction_intent(text, unaccented_text):
        return False
    if _CONCEPTUAL_EXPLANATION_PATTERN.search(text):
        return True
    if unaccented_text is None:
        unaccented_text = to_unaccented(text)
    return bool(_CONCEPTUAL_EXPLANATION_UNACCENTED.search(unaccented_text))


_COMPILED_SECURITY: tuple[tuple[re.Pattern[str], str], ...] | None = None
_COMPILED_SECURITY_UNACCENTED: tuple[tuple[re.Pattern[str], str], ...] | None = None
_COMPILED_SYMBOLIC: tuple[tuple[re.Pattern[str], str], ...] | None = None


def _compiled_rules() -> tuple[
    tuple[tuple[re.Pattern[str], str], ...],
    tuple[tuple[re.Pattern[str], str], ...],
]:
    """(security, symbolic-syntax) — L0 không còn bảng từ khoá ngôn ngữ nào."""
    global _COMPILED_SECURITY, _COMPILED_SECURITY_UNACCENTED, _COMPILED_SYMBOLIC
    if _COMPILED_SECURITY is None:
        _COMPILED_SECURITY = tuple(
            (re.compile(pattern, re.IGNORECASE), tag)
            for pattern, tag in _SECURITY_RULES
        )
        _COMPILED_SECURITY_UNACCENTED = tuple(
            (re.compile(to_unaccented(pattern), re.IGNORECASE), tag)
            for pattern, tag in _SECURITY_RULES
        )
    if _COMPILED_SYMBOLIC is None:
        _COMPILED_SYMBOLIC = tuple(
            (re.compile(pattern), tag) for pattern, tag in _SYMBOLIC_SYNTAX_RULES
        )
    return _COMPILED_SECURITY, _COMPILED_SYMBOLIC


def _domain_hint(question: str) -> str:
    """Domain hint: detect_domain (shard) trước, classify_top1 (domain_classifier) khi general."""
    try:
        from scp.core.partition.shard import detect_domain

        domain = detect_domain(question)
        if domain and domain != "general":
            return domain
    except Exception as exc:
        logger.debug("detect_domain unavailable: %s", exc, exc_info=True)
    try:
        from scp.data_sources.domain_classifier import classify_top1

        domain = classify_top1(question)
        if domain and domain != "general":
            return domain
    except Exception as exc:
        logger.debug("classify_top1 unavailable: %s", exc, exc_info=True)
    return "general"


_MATH_HINT_GUARD = re.compile(
    r"\d+\s*[+*/^×÷-]\s*\d+|\b(tính|phương trình|đạo hàm|tích phân|xác suất|fibonacci|giai thừa|logarit)\b",
    re.IGNORECASE,
)


def _sanitize_domain_hint(text: str, domain: str) -> str:
    """[W2-d4] Domain hint chỉ là HINT — không được đi kèm response khi tự mâu thuẫn với text."""
    if domain == "math" and not _MATH_HINT_GUARD.search(text or ""):
        return "general"
    return domain


def classify_l0(question: str) -> RouteDecision | None:
    """L0 = an ninh (chỉ chặt hơn) + cú pháp ký hiệu số học (không chữ cái).

    ``None`` = câu ngôn ngữ tự nhiên ⇒ tầng ngữ nghĩa quyết (KHÔNG BAO GIỜ trả
    LANE_CHATBOT — CHATBOT chỉ có qua đồng thuận kép ở ``semantic_router``).
    """
    text = normalize_query(question)
    if not text:
        return None
    lang = detect_language(text)
    security_rules, symbolic_rules = _compiled_rules()
    lowered = text.lower()
    unaccented_lowered = to_unaccented(lowered)

    # 1. Extraction intent override: secret indicators + extraction terms route directly to LANE_SECURITY
    if has_extraction_intent(lowered, unaccented_lowered):
        return RouteDecision(
            intent=SECURITY,
            domain="security",
            confidence=0.95,
            via="l0-security",
            reason="security_signal:secret_fishing",
            lane=LANE_SECURITY,
            language=lang,
        )

    # 2. Check security patterns (both exact and unaccented for Vietnamese evasion)
    unaccented_rules = _COMPILED_SECURITY_UNACCENTED or ()
    for (pattern, tag), (unacc_pattern, _) in zip(security_rules, unaccented_rules):
        if pattern.search(lowered) or unacc_pattern.search(unaccented_lowered):
            if tag == "secret_fishing" and is_conceptual_question(lowered, unaccented_lowered):
                continue
            return RouteDecision(
                intent=SECURITY,
                domain="security",
                confidence=0.95,
                via="l0-security",
                reason=f"security_signal:{tag}",
                lane=LANE_SECURITY,
                language=lang,
            )
    try:
        from scp.security.unified_detector import UnifiedPatternDetector

        detector = UnifiedPatternDetector()
        assessment = detector.detect(text)
        if assessment.is_attack or assessment.severity in ("critical", "high"):
            return RouteDecision(
                intent=SECURITY,
                domain="security",
                confidence=0.95,
                via="l0-security-detector",
                reason=f"detector:{assessment.detector_source or 'unified'}",
                lane=LANE_SECURITY,
                language=lang,
            )
    except Exception as _det_err:
        logger.debug("[S24] UnifiedPatternDetector failed: %s", _det_err, exc_info=True)

    # 3. Cú pháp ký hiệu số học (không chữ cái ⇒ không phụ thuộc ngôn ngữ). Lane
    #    LUÔN LANE_FACTUAL (chặt hơn). Tên via/reason giữ nguyên hợp đồng cũ
    #    ("l0-keyword", "reasoning_signal:<tag>") cho consumer đang đọc tag.
    for pattern, tag in symbolic_rules:
        if pattern.search(text):
            return RouteDecision(
                intent=REASONING,
                domain=_sanitize_domain_hint(text, _domain_hint(text)),
                confidence=0.9,
                via="l0-keyword",
                reason=f"reasoning_signal:{tag}",
                lane=LANE_FACTUAL,
                language=lang,
            )

    # 4. [L0-DEKEYWORD] Ngôn ngữ tự nhiên ⇒ L0 KHÔNG quyết (không còn lookup/
    #    creative/weak/domain-hint theo từ khoá). Domain hint vẫn được gắn vào
    #    quyết định của tầng sau (``question_router``) nhưng không còn chọn lane.
    return None


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


def _terms_covered(text: str, terms: list[str]) -> bool:
    """Quality gate: dữ liệu fetch về phải CHỨA các term nổi bật của câu hỏi
    (chặn compose garbage từ landing-page match)."""
    lowered = (text or "").lower()
    return bool(terms) and all(term in lowered for term in terms)


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


def _trim(text: str, limit: int = 700) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _select_relevant_text(text: str, terms: list[str], limit: int = 700) -> str:
    """[S24 fix runtime] Chọn CÂU trả lời TRỰC TIẾP thay vì cắt mù theo ký tự."""
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

