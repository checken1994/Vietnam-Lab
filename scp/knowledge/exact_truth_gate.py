"""Exact Truth & Deduplication Gate for Knowledge Records.

Governed by SCP DNA #1–#29:
- DNA #22 (PASS != TRUE): Approximate or fuzzy matching must never be treated
  as true for single-domain facts (FA-01, FA-04).
- DNA #14 (Consensus != Truth): Multi-perspective questions require explicit
  frame-of-reference labeling rather than confusing consensus.
- DNA #11 (Anti-Placebo Gate Pattern): Prevent false positive numeric matches
  across unrelated facts (e.g. 100 deg C vs 100 meters).
- Fail-Closed: Unperspectival, approximate, empty, or unvalidated answers fail closed.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

# Compound approximate phrases that unconditionally indicate fuzzy/hedging answers
APPROXIMATE_COMPOUND_PHRASES = (
    "xấp xỉ", "xap xi", "ước chừng", "uoc chung",
    "gần đúng", "gan dung", "khoảng chừng", "khoang chung",
    "đại khái", "dai khai", "more or less",
)

# English keywords that unambiguously indicate approximation
APPROXIMATE_ENGLISH_WORDS = (
    "approximately", "roughly", "estimated",
)

# Standard stopwords for entity / unit alignment (Vietnamese + English)
STOPWORDS = {
    "là", "của", "và", "các", "có", "trong", "cho", "với", "được", "về",
    "đến", "khi", "những", "thì", "đó", "này", "một", "như", "theo", "tại",
    "người", "đã", "sẽ", "đang", "từ", "ra", "vào", "lại", "qua", "đây",
    "gì", "nào", "sao", "ai", "đâu", "ở", "làm", "rất", "nhiều", "cũng",
    "nhưng", "bởi", "vì", "để", "nếu", "hay", "hoặc",
    "the", "is", "at", "which", "on", "and", "a", "an", "in", "to", "for",
    "of", "with", "as", "by", "that", "this", "it", "from", "are", "was",
    "were", "be", "been", "being", "have", "has", "had", "do", "does", "did",
    "what", "who", "when", "where", "why", "how", "but", "not", "or", "there",
}


def is_approximate_claim(text: str) -> bool:
    """Check if the text contains fuzzy or approximate hedging for factual claims.

    Carefully avoids false-positive matching on valid compound nouns such as
    'khoảng cách' (distance), 'khoảng thời gian' (interval), 'tầm nhìn' (vision),
    'tầm quan trọng' (importance), or spatial descriptions like 'ở gần biển'.
    """
    if not text or not isinstance(text, str):
        return False
    t_lower = " " + text.lower().strip() + " "

    # 1. Direct compound phrases that are unconditionally approximate
    for phrase in APPROXIMATE_COMPOUND_PHRASES:
        if phrase in t_lower:
            return True

    for word in APPROXIMATE_ENGLISH_WORDS:
        if re.search(rf"\b{re.escape(word)}\b", t_lower):
            return True

    # 2. Contextual Vietnamese modifiers followed by quantity/number
    # 'khoảng' / 'khoang': approximate ONLY when followed by quantity/digits, NOT compound nouns
    if re.search(r"\b(?:khoảng|khoang)\s+(?!(?:cách|trống|thời\s+gian|không\s+gian|trời|chi|thu)\b)(?:\d+|chừng|vài|mấy|nửa|\$)", t_lower):
        return True

    # 'tầm' / 'tam': approximate quantifier (tầm 300, tầm vài), NOT 'tầm nhìn', 'tầm quan trọng'
    if re.search(r"\b(?:tầm|tam)\s+(?!(?:nhìn|quan\s+trọng|vóc|ảnh\s+hưởng|xa)\b)(?:\d+|vài|mấy)", t_lower):
        return True

    # 'gần' / 'gan': approximate marker when followed by numbers or qualifiers (gần 300, gần như, gần bằng)
    if re.search(r"\b(?:gần|gan)\s+(?:\d+|như|bằng|chạm)", t_lower):
        return True

    # 3. Contextual English modifiers followed by numbers/quantities
    if re.search(r"\babout\s+(?:\d+|half|several|a\s+few|\$)", t_lower):
        return True
    if re.search(r"\baround\s+(?:\d+|half|several|\$)", t_lower):
        return True
    if re.search(r"\bnearly\s+(?:\d+|all|every|\$)", t_lower):
        return True
    if re.search(r"\balmost\s+(?:\d+|all|every|\$)", t_lower):
        return True
    if re.search(r"\bclose\s+to\s+(?:\d+|\$)", t_lower):
        return True

    return False


def extract_canonical_numbers(text: str) -> list[float]:
    """Extract and normalize numbers from text, handling thousands separators."""
    matches = re.findall(r"\b\d+(?:[.,]\d+)*\b", text)
    nums: list[float] = []
    for m in matches:
        clean = m
        # Thousands separators: 299.792.458 or 299,792,458
        if re.search(r"^\d{1,3}(?:[.]\d{3})+$", clean):
            clean = clean.replace(".", "")
        elif re.search(r"^\d{1,3}(?:[,]\d{3})+$", clean):
            clean = clean.replace(",", "")
        elif re.search(r"^\d{1,3}(?:[.]\d{3})+,\d+$", clean):  # European decimal
            clean = clean.replace(".", "").replace(",", ".")
        elif re.search(r"^\d{1,3}(?:[,]\d{3})+\.\d+$", clean):  # US decimal
            clean = clean.replace(",", "")
        elif "," in clean and "." not in clean:  # Single comma decimal
            clean = clean.replace(",", ".")
        try:
            nums.append(float(clean))
        except ValueError:
            continue
    return nums


def extract_content_tokens(text: str) -> set[str]:
    """Extract semantic content tokens excluding stopwords and digits."""
    words = re.findall(r"[\wÀ-ỹ]+", text.lower())
    return {w for w in words if len(w) >= 1 and w not in STOPWORDS and not w.isdigit()}


def verify_exact_truth(
    question: str,
    candidate_answer: str,
    expected_truth: str,
    is_multi_domain: bool = False,
    frame_of_reference: str = "",
) -> dict[str, Any]:
    """Verify candidate answer against expected truth for factual exactness.

    Quy tắc 1 (Exact Single Truth):
      Single-domain fact accepts only the exact best truth.
      Approximate/fuzzy matching is strictly forbidden (FA-01, FA-04).
    Quy tắc 2 (Multi-domain & Multi-perspective Exception):
      Multiple answers are only allowed when marked as multi-domain or
      tagged with an explicit non-empty frame_of_reference.
    """
    # Boundary validation: non-empty strings required (Fail-Closed)
    if not isinstance(candidate_answer, str) or not isinstance(expected_truth, str):
        return {
            "verdict": "FAIL",
            "passed": False,
            "reason": "EMPTY_OR_INVALID_INPUT",
            "details": "Candidate answer and expected truth must be valid non-empty strings.",
        }

    cand = candidate_answer.strip()
    exp = expected_truth.strip()
    frame = (frame_of_reference or "").strip()

    if not cand or not exp:
        return {
            "verdict": "FAIL",
            "passed": False,
            "reason": "EMPTY_OR_INVALID_INPUT",
            "details": "Candidate answer and expected truth must be non-empty.",
        }

    # Rule 2: Multi-perspective check
    if is_multi_domain and not frame:
        return {
            "verdict": "FAIL",
            "passed": False,
            "reason": "FRAME_OF_REFERENCE_REQUIRED_FOR_MULTI_PERSPECTIVE",
            "details": "Multi-domain / diverse perspective questions require an explicit frame_of_reference label.",
        }

    # Rule 1: Exact Single Truth (reject approximate/fuzzy hedging)
    if is_approximate_claim(cand):
        return {
            "verdict": "FAIL",
            "passed": False,
            "reason": "APPROXIMATE_FUZZY_MATCH_REJECTED",
            "details": "Candidate answer uses approximate/fuzzy modifiers, which is rejected under Exact Single Truth rule.",
        }

    # Compare exact values (case-insensitive)
    cand_norm = cand.lower()
    exp_norm = exp.lower()

    if cand_norm == exp_norm:
        return {
            "verdict": "PASS",
            "passed": True,
            "reason": "EXACT_CANONICAL_MATCH",
            "details": "Candidate answer exactly matches expected truth.",
        }

    # Normalize trailing punctuation
    cand_stripped = re.sub(r"[.\s,;!?:/]+$", "", cand_norm)
    exp_stripped = re.sub(r"[.\s,;!?:/]+$", "", exp_norm)
    if cand_stripped == exp_stripped:
        return {
            "verdict": "PASS",
            "passed": True,
            "reason": "EXACT_CANONICAL_MATCH",
            "details": "Candidate answer matches expected truth after trailing punctuation normalization.",
        }

    # Check numeric equality if both answers contain exact numbers
    cand_nums = extract_canonical_numbers(cand)
    exp_nums = extract_canonical_numbers(exp)
    if cand_nums and exp_nums and len(cand_nums) == len(exp_nums):
        if cand_nums == exp_nums:
            c_tokens = extract_content_tokens(cand)
            e_tokens = extract_content_tokens(exp)
            # Both contain only numbers (e.g. "299792458" vs "299.792.458")
            if not e_tokens and not c_tokens:
                return {
                    "verdict": "PASS",
                    "passed": True,
                    "reason": "EXACT_NUMERIC_MATCH",
                    "details": "Candidate answer matches exact numeric value.",
                }
            # Entity and unit token alignment check (Anti-Placebo Rule #11):
            # Prevents unrelated facts with identical numbers from matching (e.g. 100 deg C vs 100 meters)
            if e_tokens and c_tokens:
                if e_tokens.issubset(c_tokens) or c_tokens.issubset(e_tokens):
                    return {
                        "verdict": "PASS",
                        "passed": True,
                        "reason": "EXACT_NUMERIC_MATCH",
                        "details": "Candidate answer matches exact numeric value and unit context.",
                    }

    return {
        "verdict": "FAIL",
        "passed": False,
        "reason": "EXACT_TRUTH_MISMATCH",
        "details": f"Candidate answer '{cand[:60]}' does not match expected exact truth '{exp[:60]}'.",
    }


def compute_knowledge_duplicate_rate(sqlite_path: str | Path) -> float:
    """Compute duplicate rate of questions in knowledge_records table.

    duplicate_rate = (total_records - distinct_questions) / total_records
    Returns 0.0 if table is empty or does not exist.
    """
    path = Path(sqlite_path)
    if not path.exists():
        return 0.0

    try:
        with sqlite3.connect(path, timeout=5.0) as conn:
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='knowledge_records'")
            if not cur.fetchone():
                return 0.0
            cur.execute("SELECT COUNT(*), COUNT(DISTINCT lower(trim(question))) FROM knowledge_records")
            row = cur.fetchone()
            total, distinct = row[0], row[1]
            if total == 0:
                return 0.0
            return float(total - distinct) / float(total)
    except Exception:
        return 0.0


def audit_knowledge_duplicates(
    sqlite_path: str | Path,
    max_allowed_rate: float = 0.10,
) -> dict[str, Any]:
    """Audit duplicate rate and invariant in knowledge_records table (Quy tắc 3)."""
    path = Path(sqlite_path)
    if not path.exists():
        return {
            "passed": True,
            "total_records": 0,
            "distinct_questions": 0,
            "duplicate_rate": 0.0,
            "max_allowed_rate": max_allowed_rate,
            "top_duplicates": [],
        }

    with sqlite3.connect(path, timeout=5.0) as conn:
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='knowledge_records'")
        if not cur.fetchone():
            return {
                "passed": True,
                "total_records": 0,
                "distinct_questions": 0,
                "duplicate_rate": 0.0,
                "max_allowed_rate": max_allowed_rate,
                "top_duplicates": [],
            }
        cur.execute("SELECT COUNT(*), COUNT(DISTINCT lower(trim(question))) FROM knowledge_records")
        row = cur.fetchone()
        total, distinct = row[0], row[1]
        rate = float(total - distinct) / float(total) if total > 0 else 0.0

        cur.execute(
            """
            SELECT lower(trim(question)), COUNT(*)
            FROM knowledge_records
            GROUP BY lower(trim(question))
            HAVING COUNT(*) > 1
            ORDER BY COUNT(*) DESC
            LIMIT 10
            """
        )
        top_dups = [{"question": r[0], "count": r[1]} for r in cur.fetchall()]

    return {
        "passed": rate <= max_allowed_rate,
        "total_records": total,
        "distinct_questions": distinct,
        "duplicate_count": total - distinct,
        "duplicate_rate": round(rate, 4),
        "max_allowed_rate": max_allowed_rate,
        "top_duplicates": top_dups,
    }
