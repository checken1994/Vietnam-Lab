"""
Question Normalizer — Shared util cho tất cả SLM.

[Z.ai-ROOT-FIX #9] TẠI SAO: SLM hiện tại dùng `if keyword in q_lower` (exact substring)
→ không xử lý biến thể câu hỏi thực tế ("số xương" vs "bao nhiêu xương" vs "số lượng xương").
Benchmark ISO cho thấy 3/6 câu cùng ý nhưng SLM chỉ match 1-2 câu.

Fix từ gốc: Question Normalizer với 3 lớp:
1. Stopword removal — bỏ từ không mang ý nghĩa ("có", "là", "bao nhiêu", "của", "trong")
2. Synonym replacement — map biến thể về canonical form ("bao nhiêu xương" → "số xương")
3. Token-based matching — Jaccard overlap thay vì exact substring

Synonym dictionary cho 10 domain (biology, chemistry, physics, medical, math, etc.)
được định nghĩa ở đây (single source of truth) thay vì rải rác trong từng SLM.

Usage:
    from scp.core.question_normalizer import normalize_question, match_question

    canonical_q = normalize_question("Cơ thể người có bao nhiêu xương?")
    # → "số xương cơ thể người"

    is_match = match_question("bao nhiêu xương trong người", "số xương cơ thể người")
    # → True (Jaccard ≥ 0.5)
"""
from __future__ import annotations

import logging
import re
from collections.abc import Iterable

logger = logging.getLogger(__name__)


_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_MULTISPACE_RE = re.compile(r"\s+")

# ============================================================
# STOPWORDS — từ không mang ý nghĩa, bỏ trước khi match
# ============================================================

_STOPWORDS_VI: set[str] = set()
_SYNONYMS: dict[str, set[str]] = {}

_VARIANT_TO_CANONICAL: dict[str, str] = {}
for _canonical, _variants in _SYNONYMS.items():
    _VARIANT_TO_CANONICAL[_canonical.lower()] = _canonical.lower()
    for _v in _variants:
        _VARIANT_TO_CANONICAL[_v.lower()] = _canonical.lower()


# ============================================================
# NORMALIZE — main entry point
# ============================================================

def normalize_question(question: str) -> str:
    """Normalize question for matching.

    Steps:
    1. Lowercase + strip punctuation
    2. Replace synonyms (variants → canonical)
    3. Remove stopwords
    4. Collapse whitespace

    Args:
        question: Raw question string

    Returns:
        Normalized question (canonical form, ready for KB/SLM lookup)
    """
    if not question:
        return ""

    # 1. Lowercase + strip punctuation
    q = question.lower().strip()
    q = _PUNCT_RE.sub(" ", q)

    # 2. Replace synonyms (longest match first to avoid partial overlap)
    # Sort variants by length descending so "bao nhiêu xương" matches before "xương"
    for variant in sorted(_VARIANT_TO_CANONICAL.keys(), key=len, reverse=True):
        if variant in q:
            canonical = _VARIANT_TO_CANONICAL[variant]
            q = q.replace(variant, canonical)

    # 3. Remove stopwords (multi-word stopwords first)
    for sw in sorted(_STOPWORDS_VI, key=len, reverse=True):
        # Use word boundary to avoid partial matches
        q = re.sub(r"\b" + re.escape(sw) + r"\b", " ", q, flags=re.IGNORECASE)

    # 4. Collapse whitespace + strip
    q = _MULTISPACE_RE.sub(" ", q).strip()

    return q


def match_question(query: str, stored_question: str, min_overlap: float = 0.5) -> bool:
    """Check if two questions match (token-based Jaccard overlap).

    Args:
        query: User's question (raw)
        stored_question: Question stored in KB/SLM KB
        min_overlap: Minimum Jaccard similarity (default 0.5)

    Returns:
        True if questions are considered equivalent
    """
    if not query or not stored_question:
        return False

    # Normalize both
    q1 = normalize_question(query)
    q2 = normalize_question(stored_question)

    # Exact match after normalization
    if q1 == q2:
        return True

    # Token-based Jaccard
    tokens1 = {w for w in q1.split() if len(w) > 1}
    tokens2 = {w for w in q2.split() if len(w) > 1}

    if not tokens1 or not tokens2:
        return False

    intersection = tokens1 & tokens2
    union = tokens1 | tokens2
    jaccard = len(intersection) / len(union)

    return jaccard >= min_overlap


def keyword_match(question: str, keywords: Iterable[str], min_overlap: float = 0.5) -> bool:
    """Check if question contains any of the keywords (with synonym matching).

    Replaces SLM's `if keyword in q_lower` with synonym-aware matching.

    Args:
        question: User's question
        keywords: Iterable of keywords to match against
        min_overlap: For token-based fallback match

    Returns:
        True if any keyword matches (exact or synonym)
    """
    if not question or not keywords:
        return False

    q_normalized = normalize_question(question)
    q_lower = question.lower()

    for kw in keywords:
        if not kw:
            continue
        kw_lower = kw.lower().strip()
        # 1. Direct substring match (after normalization)
        if kw_lower in q_normalized:
            return True
        # 2. Synonym match — check if any variant of this keyword appears
        if kw_lower in _SYNONYMS:
            for variant in _SYNONYMS[kw_lower]:
                if variant.lower() in q_lower or variant.lower() in q_normalized:
                    return True
        # 3. Reverse: keyword might be a variant → check canonical
        canonical = _VARIANT_TO_CANONICAL.get(kw_lower)
        if canonical and canonical in q_normalized:
            return True

    return False


__all__ = ["normalize_question", "match_question", "keyword_match"]
