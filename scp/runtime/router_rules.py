"""Question Router L0 Classification Rules, Language Detection, and Types.

Extracted from scp.runtime.question_router to enforce modular separation
of concerns (< 1,000 LOC ceiling).
"""
from __future__ import annotations

import logging
import re
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
_VIETNAMESE_WORDS = frozenset(
    {
        "là", "và", "của", "những", "các", "có", "được", "cho", "trong", "một",
        "này", "người", "với", "khi", "như", "để", "không", "bạn", "tôi", "ai",
        "gì", "thế", "nào", "sao", "ở", "đâu", "tại", "mấy", "thì", "đã", "sẽ",
        "về", "hãy", "chào", "xin", "cảm", "ơn", "tốt", "rất", "làm", "biết",
    }
)
_NON_LATIN_SCRIPT = re.compile(
    r"[\u0400-\u04FF\u4E00-\u9FFF\u0600-\u06FF\uAC00-\uD7AF\u3040-\u30FF]"
)


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
    bypass_verdict_pass: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": str(self.intent),
            "lane": self.lane,
            "language": self.language,
            "confidence": self.confidence,
            "domain": self.domain,
            "via": self.via,
            "bypass_verdict_pass": self.bypass_verdict_pass,
            "reason": self.reason,
        }


# ---------------------------------------------------------------------------
# L0 — regex intent & security rules (bilingual EN/VI).
# ---------------------------------------------------------------------------
_SECURITY_RULES: tuple[tuple[str, str], ...] = (
    (r"\b(ignore\s+(all\s+)?previous\s+instructions|system\s+prompt|dan\s+mode|jailbreak)\b", "prompt_injection"),
    (r"\b(bỏ\s+qua\s+(toàn\s+bộ\s+)?(hướng\s+dẫn|chỉ\s+dẫn)|lệnh\s+hệ\s+thống)\b", "prompt_injection_vi"),
    (r"\b(reveal\s+(api\s+)?key|show\s+(your\s+)?hidden\s+prompt)\b", "secret_leak"),
    (r"\b(cat\s+/etc/(passwd|shadow)|rm\s+-rf\b|powershell\s+-enc|format\s+c:)\b", "dangerous_command"),
    (r"(\bunion\s+select\b|\bselect\s+\*\s+from\s+users|\bdrop\s+table\b)", "sql_injection"),
)

_REASONING_STRONG_RULES: tuple[tuple[str, str], ...] = (
    (r"\b(bạn là ai|who are you|bạn tên gì|bạn có thể làm gì|mày là ai|what can you do|giới thiệu bản thân)\b", "conversational_identity"),
    (r"^(xin chào|chào bạn|chào|hello|hi|hey|good morning|good evening|tạm biệt|goodbye|bye)\b", "conversational_greeting"),
    (r"\b(bạn khỏe không|how are you|how is it going|có khỏe không)\b", "conversational_smalltalk"),
    (r"\b(cảm ơn|thank you|thanks|cảm ơn bạn)\b", "conversational_thanks"),
    (r"\b(giúp tôi|help me|can you help|tư vấn cho tôi|bạn nghĩ sao|bạn nghĩ gì|ý kiến của bạn|ý kiến về)\b", "conversational_assist"),
    (r"\b(tôi là ai|who am i|tên tôi là gì|tôi tên là gì|tôi tên gì|tên của tôi|what is my name)\b", "user_identity"),
    (r"\b(bạn có nhớ|nhớ tôi không|bạn nhớ tôi|bạn nhớ không|nhớ không|nhớ gì về tôi|do you remember|remember me)\b", "conversational_memory"),
    (r"\b(tôi vừa nói|chúng ta vừa nói|trước đó tôi|như tôi đã nói|nhắc lại cho tôi|what did i say|what were we talking)\b", "conversational_context"),
    (r"\b(nói chuyện|trò chuyện|tâm sự|kể chuyện|tell me a story|kể một câu chuyện|tell a joke|kể chuyện cười)\b", "conversational_entertainment"),
    (r"\d+\s*[+*/^×÷]\s*\d+|\d+\s+-\s+\d+", "arithmetic"),
    (r"\b(sqrt|gcd|lcm|sin|cos|tan)\s*\(", "math_function"),
    (r"\d+\s*!", "factorial"),
    (r"\b(fibonacci|factorial|giai thừa|[uƯ]cln|bcnn|gcd|lcm)\b", "math_named"),
    (r"\blog\d*\s*(của|of|base)\b", "logarithm"),
    (r"\b\d+\s*(mũ|\^)\s*\d+", "power"),
    (r"\b\d+\s*%\s*của\b|\b\d+\s*percent of\b", "percentage"),
    (r"^(tính|calculate|compute|solve)\s+\d", "compute_command"),
    (r"\b(phương trình|đạo hàm|tích phân|logarit|logarithm|equation|derivative|integral)\b", "math_topic"),
    (r"\b(trung bình|trung vị|phương sai|độ lệch chuẩn|xác suất|median|mean of|standard deviation|variance|probability|average of)\b", "statistics"),
    (r"\b(min|max|range)\b\s*(của|of)\b", "list_stat"),
    (r"\b(true|false)\s*(and|or|xor)\s*(true|false)\b|\bnot\s+(true|false|\()", "boolean_logic"),
    (r"[a-uw-zA-UW-Z]\s*[<>]\s*[a-uw-zA-UW-Z]", "symbolic_comparison"),
    (r"\bsuy luận\b|\blogic puzzle\b|\btiền đề\b", "logic_topic"),
    (r"\b(viết|write|create|implement|xây dựng)\b.{0,30}\b(hàm|function|code|chương trình|program|script|class|api|algorithm|thuật toán)\b", "code_generation"),
    (r"\bpython\b.{0,30}\b(hàm|function)\b|\bfunction\s+(that|to|which)\b", "code_function"),
)

_REASONING_WEAK_RULES: tuple[tuple[str, str], ...] = (
    (r"\b(giải thích|hãy giải thích|thế nào là|khái niệm|explain|tell me about)\b", "concept_explanation"),
    (r"\bnếu\b.{0,60}\bthì\b|\bif\b.{0,60}\bthen\b", "conditional_reasoning"),
    (r"\btất cả\b.{0,40}\b(đều|là)\b|\ball\s+\w+\s+are\b", "syllogism"),
)

_CREATIVE_RULES: tuple[tuple[str, str], ...] = (
    (r"\b(thơ|poem|poetry|ca dao|tục ngữ|truyện ngắn|story|joke|bài văn|essay|sáng tác)\b", "creative_writing"),
)

_LOOKUP_RULES: tuple[tuple[str, str], ...] = (
    (r"\b(what|who|where|when|which|how many|how much|how far|how fast|how tall|how deep|how long)\b", "interrogative_en"),
    (r"\b(weather|thời tiết|nhiệt độ|temperature|dự báo|forecast)\b", "weather_fact"),
    (r"\b(giá|price|tỷ giá|exchange rate|tiền tệ|currency|bitcoin|blockchain|chứng khoán|stock market|crypto)\b", "finance_fact"),
    (r"\b(sinh học|biology|adn|dna|arn|rna|nhiễm sắc thể|chromosome|axit amin|amino acid|codon|mã di truyền|tế bào|cell|ty thể|mitochondria|ribosome|lục lạp|chloroplast)\b", "biology_fact"),
    (r"\b(địa chất|geology|khoáng sản|khoáng vật|mineral|thạch anh|quartz|kim cương|diamond|thang mohs|mohs scale|đá magma|đá trầm tích|đá biến chất|manti|vỏ trái đất|lõi trái đất)\b", "geology_fact"),
    (r"\b(động đất|earthquake|địa chấn|seismic|richter|tâm chấn|epicenter|dư chấn|aftershock)\b", "earthquake_fact"),
    (r"\b(là gì|ai là|ở đâu|khi nào|năm nào|bao nhiêu|vào năm|mấy|nào|người nào|cái nào|bởi ai|đâu)\b|gì\s*\?|nhất\s*\?", "interrogative_vi"),
    (r"\b(thủ đô|capital of|dân số|population|diện tích|area of|sông|núi)\b", "geography_fact"),
    (r"\b(cve|lỗ hổng|vulnerability|malware|ransomware|phishing|https|ssl|tls)\b", "security_fact"),
    (r"\bhóa học\b|\baxit\b|\bbazơ\b", "chemistry_fact"),
    (r"\b(vật lý|physics|điện tích|electron|proton|nguyên tử|phân tử|coulomb|volt|ampe|amper|newton|joule|quang hợp|quang phổ)\b", "physics_fact"),
    (r"\b(định nghĩa|nghĩa là|definition of|meaning of)\b", "definition"),
    (r"\b\d+\s*(km|kg|m|cm|mm|mile|inch|foot|feet|yard|gallon|lít|liter|lb|pound|hour|giờ|giây|second|phút|minute|acre|knot|celsius|fahrenheit)\b\s*(bằng|to|sang|=|in)\b", "unit_conversion"),
)

_PH_PATTERN = re.compile(r"\bpH\b")

_COMPILED_SECURITY: tuple[tuple[re.Pattern[str], str], ...] | None = None
_COMPILED_REASONING_STRONG: tuple[tuple[re.Pattern[str], str], ...] | None = None
_COMPILED_REASONING_WEAK: tuple[tuple[re.Pattern[str], str], ...] | None = None
_COMPILED_CREATIVE: tuple[tuple[re.Pattern[str], str], ...] | None = None
_COMPILED_LOOKUP: tuple[tuple[re.Pattern[str], str], ...] | None = None


def _compiled_rules() -> tuple[
    tuple[tuple[re.Pattern[str], str], ...],
    tuple[tuple[re.Pattern[str], str], ...],
    tuple[tuple[re.Pattern[str], str], ...],
    tuple[tuple[re.Pattern[str], str], ...],
    tuple[tuple[re.Pattern[str], str], ...],
]:
    global _COMPILED_SECURITY, _COMPILED_REASONING_STRONG, _COMPILED_REASONING_WEAK, _COMPILED_CREATIVE, _COMPILED_LOOKUP
    if _COMPILED_SECURITY is None:
        _COMPILED_SECURITY = tuple(
            (re.compile(pattern, re.IGNORECASE), tag)
            for pattern, tag in _SECURITY_RULES
        )
    if _COMPILED_REASONING_STRONG is None:
        _COMPILED_REASONING_STRONG = tuple(
            (re.compile(pattern, re.IGNORECASE), tag)
            for pattern, tag in _REASONING_STRONG_RULES
        )
    if _COMPILED_REASONING_WEAK is None:
        _COMPILED_REASONING_WEAK = tuple(
            (re.compile(pattern, re.IGNORECASE), tag)
            for pattern, tag in _REASONING_WEAK_RULES
        )
    if _COMPILED_CREATIVE is None:
        _COMPILED_CREATIVE = tuple(
            (re.compile(pattern, re.IGNORECASE), tag)
            for pattern, tag in _CREATIVE_RULES
        )
    if _COMPILED_LOOKUP is None:
        _COMPILED_LOOKUP = tuple(
            (re.compile(pattern, re.IGNORECASE), tag)
            for pattern, tag in _LOOKUP_RULES
        )
    return (
        _COMPILED_SECURITY,
        _COMPILED_REASONING_STRONG,
        _COMPILED_REASONING_WEAK,
        _COMPILED_CREATIVE,
        _COMPILED_LOOKUP,
    )


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
    """L0 keyword/regex cascade. None = bất định → nhường L2."""
    text = (question or "").strip()
    if not text:
        return None
    lang = detect_language(text)
    security_rules, strong_rules, weak_rules, creative_rules, lookup_rules = _compiled_rules()
    lowered = text.lower()

    # 1. Check security patterns
    for pattern, tag in security_rules:
        if pattern.search(lowered):
            return RouteDecision(
                intent=SECURITY,
                domain="security",
                confidence=0.95,
                via="l0-security",
                reason=f"security_signal:{tag}",
                lane=LANE_SECURITY,
                language=lang,
                bypass_verdict_pass=False,
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
                bypass_verdict_pass=False,
            )
    except Exception as _det_err:
        logger.debug("[S24] UnifiedPatternDetector failed: %s", _det_err, exc_info=True)

    # 2. [W2-d1] Reasoning STRONG
    for pattern, tag in strong_rules:
        if pattern.search(lowered):
            return RouteDecision(
                intent=REASONING,
                domain=_sanitize_domain_hint(text, _domain_hint(text)),
                confidence=0.9,
                via="l0-keyword",
                reason=f"reasoning_signal:{tag}",
                lane=LANE_CHATBOT,
                language=lang,
                bypass_verdict_pass=True,
            )

    # 3. [W2-d2] pH case-sensitive
    if _PH_PATTERN.search(text):
        return RouteDecision(
            intent=LOOKUP,
            domain="chemistry",
            confidence=0.75,
            via="l0-keyword",
            reason="lookup_signal:ph_precision",
            lane=LANE_FACTUAL,
            language=lang,
            bypass_verdict_pass=False,
        )

    # 4. [W2-d1] Lookup / factual
    for pattern, tag in lookup_rules:
        if pattern.search(lowered):
            return RouteDecision(
                intent=LOOKUP,
                domain=_sanitize_domain_hint(text, _domain_hint(text)),
                confidence=0.75,
                via="l0-keyword",
                reason=f"lookup_signal:{tag}",
                lane=LANE_FACTUAL,
                language=lang,
                bypass_verdict_pass=False,
            )

    # 5. [W2-d1] Creative guard
    for pattern, tag in creative_rules:
        if pattern.search(lowered):
            return RouteDecision(
                intent=REASONING,
                domain="creative",
                confidence=0.9,
                via="l0-creative",
                reason=f"creative_signal:{tag}",
                lane=LANE_CHATBOT,
                language=lang,
                bypass_verdict_pass=True,
            )

    # 6. [W2-d1] Reasoning WEAK
    for pattern, tag in weak_rules:
        if pattern.search(lowered):
            return RouteDecision(
                intent=REASONING,
                domain=_sanitize_domain_hint(text, _domain_hint(text)),
                confidence=0.9,
                via="l0-keyword",
                reason=f"reasoning_signal:{tag}",
                lane=LANE_CHATBOT,
                language=lang,
                bypass_verdict_pass=True,
            )

    # [W2-d4] Domain hint đi qua sanitize trước khi dùng
    domain = _sanitize_domain_hint(text, _domain_hint(text))
    if domain != "general":
        return RouteDecision(
            intent=LOOKUP,
            domain=domain,
            confidence=0.7,
            via="l0-domain",
            reason=f"domain_hint:{domain}",
            lane=LANE_FACTUAL,
            language=lang,
            bypass_verdict_pass=False,
        )
    return None
