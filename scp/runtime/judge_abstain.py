"""Abstain, refusal, and time-signal deterministic detectors for TaskJudge.

Extracted from scp.runtime.judge for modularization and maintainability.
"""
from __future__ import annotations

import re

# [W7-e5 2026-10-05] Judge 3-state {PASS, FAIL, ABSTAIN} — Option A (GA.md
# B1b, owner đã duyệt). Root-cause W6-RE: judge nhị phân không có class
# ABSTAIN → câu benign không verify được (chào hỏi q06, thời tiết realtime
# q07) bị chấm FAIL → withheld; ESCALATE-flip q03/q05 là inherent variance
# của đường verify. Hợp đồng mới:
#   * answer là lời từ chối/abstain trung thực (KHÔNG chứa factual claim cần
#     verify) → verdict ABSTAIN + governance ESCALATE, KHÔNG bị chấm FAIL;
#   * crosscheck consensus missing trên câu benign → ABSTAIN + ESCALATE
#     (thiếu opinion độc lập ≠ FAIL của answer — không ai adjudicated);
#   * answer CHỨA factual claim → giữ nguyên đường PASS/FAIL. ABSTAIN KHÔNG
#     được phép thay thế FAIL cho answer có claim (chống lộng — test riêng).
# Deterministic: detector chỉ nhận dạng marker tường minh; mọi shape khác
# mặc định là "có claim" (conservative — FAIL path được giữ nguyên).
_ABSTAIN_REFUSAL_MARKERS: tuple[str, ...] = (
    # Vietnamese — từ chối trung thực / thiếu dữ liệu
    "không thể xác minh", "không thể kiểm chứng", "không xác minh được",
    # [W11-f2 2026-10-06] 'chưa có dữ liệu' ≡ 'không có dữ liệu' về nghĩa
    "chưa có dữ liệu",
    "không có dữ liệu", "chưa đủ dữ liệu", "không đủ dữ liệu",
    "không có thông tin", "tôi không có thông tin", "không thể trả lời",
    "tôi không thể", "không chắc chắn", "tôi không chắc",
    "tôi chưa được cập nhật", "chưa có cập nhật", "ngoài kiến thức của tôi",
    "không thể cung cấp thông tin", "không thể tra cứu",
    # English
    "i cannot verify", "i can't verify", "unable to verify",
    "cannot be verified", "i don't have", "i do not have",
    "i am unable", "i'm unable", "i cannot answer", "i can't answer",
    "not enough data", "no data available", "i don't know", "i do not know",
    "i'm not sure", "i am not sure",
)
# [W16-g1] Lịch sử: tier hội thoại KHÔNG còn match theo list này nữa — đã đảo
# chiều sang whitelist fullmatch (_ABSTAIN_CONV_WHITELIST_RE). Giữ làm nguồn
# tham chiếu shape conv (audit/hindsight); không còn decision-path nào đọc nó.
_ABSTAIN_CONV_MARKERS: tuple[str, ...] = (
    # Hội thoại xã giao / self-report — không có claim về thế giới ngoài
    "xin chào", "chào bạn", "chào anh", "chào chị", "chào em",
    "mình khỏe", "tôi khỏe", "vẫn khỏe", "rất vui được", "rất vui khi",
    "cảm ơn bạn đã hỏi", "cảm ơn đã hỏi", "bạn khỏe không", "bạn thế nào",
    "bạn cần hỗ trợ", "tôi là scp", "mình là scp",
    "hello", "hi there", "i'm scp", "i am scp", "i'm fine", "i am fine",
    "doing well", "thank you for asking", "thanks for asking",
)
# Cue phủ định tier hội thoại: câu smalltalk mà assert sự thật bên ngoài
# (copula + entity, số liệu, URL, dẫn nguồn) KHÔNG phải abstain.
_ABSTAIN_CLAIM_CUE_RE = re.compile(
    r"\b(là|thủ đô|tổng thống|president|capital|population|dân số|nằm|located"
    r"|is|are|was|were|theo (dữ liệu|nguồn|wikipedia|báo)|per (data|source))\b",
    re.IGNORECASE,
)
_ABSTAIN_URL_RE = re.compile(r"(https?://|www\.)", re.IGNORECASE)
_ABSTAIN_DIGIT_RE = re.compile(r"\d")
# [W7-hardening 2026-10-06 — BC-1] Assertion-verb cues: claim bọc trong phrase
# từ chối ("Tôi không thể xác minh — Donald Trump LÀ tổng thống Mỹ.") phải đi
# đường FAIL/verify, không được deliver qua nhãn abstain.
_ABSTAIN_ASSERTION_RE = re.compile(r"\b(là|thì|đang|are|is|was|were)\b", re.IGNORECASE)
# [W8-e2 2026-10-05 — BC-2] Assertion-verb closed set RIÊNG cho tier hội thoại
_ABSTAIN_CONV_ASSERTION_RE = re.compile(
    r"\b(fly|flies|flew|flying|means|meant|mean|migrate|migrates|migrated"
    r"|lives?|eats?|ate|grows?|grew|runs?|ran|causes?|caused|happens?|happened"
    r"|makes?|made|works?|needs?|swims?|bay|nghĩa là|sống|ăn|mọc|chạy|gây"
    r"|xảy ra|di cư)\b",
    re.IGNORECASE,
)
# [W16-g1 2026-10-07 — BC-E1/E2/E3 harden lần 3]
_ABSTAIN_CONV_FRAGMENT_PATTERNS: tuple[str, ...] = (
    # chào hỏi thuần
    r"xin\s+chào",
    r"chào\s+(?:bạn|anh|chị|em|quý\s+khách|cả\s+nhà)",
    r"chào",
    r"hello",
    r"hi\s+there",
    r"hi",
    r"hey",
    # self-report tình trạng bản thân (không claim thế giới ngoài)
    r"(?:mình|tôi)\s+(?:vẫn\s+)?khỏe",
    r"vẫn\s+khỏe",
    r"(?:mình|tôi)\s+(?:vẫn\s+)?ổn",
    r"i'?m\s+(?:fine|good|ok|okay|well|doing\s+well)",
    r"i\s+am\s+(?:fine|good|ok|okay|well|doing\s+well)",
    r"doing\s+(?:well|great)",
    r"rất\s+vui\s+(?:được|khi)\s+(?:gặp|được\s+gặp|trò\s+chuyện|nhắn\s+tin)"
    r"(?:\s+(?:với|cùng)\s+bạn)?",
    r"rất\s+vui\s+(?:được|khi)",
    # cảm ơn / lịch sự
    r"cảm\s+ơn(?:\s+bạn)?(?:\s+đã\s+hỏi)?",
    r"thank\s+you(?:\s+for\s+asking|\s+so\s+much|\s+very\s+much)?",
    r"thanks(?:\s+for\s+asking|\s+a\s+lot)?",
    # self-identity (report về bản thân, không claim thế giới ngoài)
    r"(?:tôi|mình)\s+là\s+scp",
    r"i'?m\s+scp",
    r"i\s+am\s+scp",
    # hỏi ngược xã giao / hỗ trợ
    r"bạn\s+(?:vẫn\s+)?khỏe\s+không",
    r"bạn\s+thế\s+nào",
    r"bạn\s+thì\s+sao",
    r"còn\s+bạn",
    r"bạn\s+cần\s+(?:hỗ\s+trợ|giúp)(?:\s+gì(?:\s+không)?|\s+không)?",
    r"(?:tôi|mình)\s+có\s+thể\s+giúp(?:\s+gì)?(?:\s+cho\s+bạn)?",
    r"how\s+are\s+you",
    r"and\s+you",
    r"what\s+about\s+you",
    r"i\s+can\s+help(?:\s+you)?",
    r"how\s+can\s+i\s+help(?:\s+you)?",
    r"what\s+can\s+i\s+do\s+for\s+you",
)
_ABSTAIN_CONV_FRAGMENT_RE = re.compile(
    "|".join(f"(?:{pattern})" for pattern in _ABSTAIN_CONV_FRAGMENT_PATTERNS),
    re.IGNORECASE,
)
_ABSTAIN_CONV_WHITELIST_RE = re.compile(
    rf"^\s*(?:{_ABSTAIN_CONV_FRAGMENT_RE.pattern})"
    rf"(?:[\s,.!?;:()\[\]{{}}\"…–—-]*(?:{_ABSTAIN_CONV_FRAGMENT_RE.pattern}))*"
    r"[\s,.!?;:()\[\]{}\"…–—-]*$",
    re.IGNORECASE,
)
# --- Safe-segment grammar cho tier refusal (post-refusal inversion) ---------
_ABSTAIN_SEG_CONNECTORS: frozenset[str] = frozenset({
    "nhưng", "mà", "vì", "và", "hoặc", "cũng", "nên", "tuy nhiên",
    "but", "however", "though", "although", "and", "or", "also", "so", "while",
})
_ABSTAIN_SEG_POLITENESS: frozenset[str] = frozenset({
    "xin lỗi", "sorry", "apologies", "cảm ơn", "thanks", "thank you",
    "vâng", "dạ", "um", "uh", "well",
})
_ABSTAIN_SEG_PRONOUNS: frozenset[str] = frozenset({
    "tôi", "mình", "tui", "i", "we", "you", "bạn", "it", "nó",
    "this", "that", "these", "those", "này", "đó", "kia", "them",
})
_ABSTAIN_SEG_TIME: frozenset[str] = frozenset({
    "hiện tại", "hiện nay", "bây giờ", "hôm nay", "lúc này", "hiện giờ",
    "now", "right now", "currently", "today",
})
_ABSTAIN_SEG_TOPICS: frozenset[str] = frozenset({
    "dữ liệu", "data", "thông tin", "information", "cập nhật", "update",
    "updates", "tin tức", "news", "thời tiết", "weather", "thời gian", "time",
    "tình hình", "situation", "chi tiết", "details", "kết quả", "results",
    "số liệu", "figures", "nguồn", "source", "realtime", "real-time",
    "tài liệu", "hồ sơ", "records",
})
_ABSTAIN_SEG_DESCRIPTORS: frozenset[str] = frozenset({
    "realtime", "real-time", "trực tiếp", "thực", "real", "mới", "new",
    "mới nhất", "latest", "current", "hiện có", "available", "direct",
    "đầy đủ", "full", "chính xác", "accurate",
})
_ABSTAIN_SEG_ACT_VERBS: frozenset[str] = frozenset({
    "xác minh", "kiểm chứng", "tra cứu", "cung cấp", "cập nhật", "tìm kiếm",
    "truy cập", "trả lời", "phản hồi", "đưa ra", "nêu",
    "verify", "check", "confirm", "provide", "update", "access", "answer",
    "respond", "reply", "search", "retrieve", "look up", "share", "reach",
})
_ABSTAIN_SEG_PREPS: frozenset[str] = frozenset({
    "về", "on", "about", "regarding", "liên quan đến", "liên quan", "của",
    "cho", "for", "with", "với", "to", "trên", "in", "at", "từ", "from",
    "within", "quanh",
})
_ABSTAIN_SEG_ANA_NP: frozenset[str] = frozenset({
    "điều này", "điều đó", "câu hỏi này", "câu hỏi đó", "vấn đề này",
    "vấn đề đó", "yêu cầu này", "yêu cầu đó", "thông tin này", "thông tin đó",
    "nội dung này", "nội dung đó", "câu hỏi trên", "vấn đề trên",
})
_ABSTAIN_CLASS_MAP: tuple[tuple[str, frozenset[str]], ...] = (
    ("ana", _ABSTAIN_SEG_ANA_NP),
    ("act", _ABSTAIN_SEG_ACT_VERBS),
    ("topic", _ABSTAIN_SEG_TOPICS),
    ("time", _ABSTAIN_SEG_TIME),
    ("desc", _ABSTAIN_SEG_DESCRIPTORS),
    ("prep", _ABSTAIN_SEG_PREPS),
    ("conn", _ABSTAIN_SEG_CONNECTORS),
    ("polite", _ABSTAIN_SEG_POLITENESS),
    ("pron", _ABSTAIN_SEG_PRONOUNS),
)
_ABSTAIN_FREE_NP_MAX_WORDS = 5
_ABSTAIN_TOPIC_CHAIN_MAX = 3
_ABSTAIN_TOPIC_MODIFIER_MAX = 3


def _abstain_entries_at(tokens: list[str], i: int) -> tuple[str, frozenset[str], int]:
    """Match longest multiword entry (≤3 từ) tại vị trí i qua các class đóng."""
    for size in (3, 2, 1):
        if i + size <= len(tokens):
            candidate = " ".join(tokens[i : i + size])
            classes = {
                name for name, entries in _ABSTAIN_CLASS_MAP if candidate in entries
            }
            if classes:
                return candidate, frozenset(classes), size
    return "", frozenset(), 0


def _abstain_starts_with_act(tokens: list[str], i: int) -> bool:
    """tokens[i:] có bắt đầu bằng một act-verb (≤3 từ) không? Dùng cho purpose."""
    _candidate, classes, _size = _abstain_entries_at(tokens, i)
    return "act" in classes


def _refusal_segment_is_safe(segment: str) -> bool:
    """[W16-g1] Segment refusal (prefix trước marker / tail sau marker) chỉ
    abstain-safe khi toàn bộ token ghép được từ class đóng."""
    tokens = re.findall(r"[\w']+", segment, re.UNICODE)
    if not tokens:
        return True
    n = len(tokens)
    i = 0
    stage = "open"
    topic_chain = 0
    topic_mods = 0
    prep_words = 0
    while i < n:
        _candidate, classes, size = _abstain_entries_at(tokens, i)
        word = tokens[i]
        is_purpose = (
            word == "để" or ("prep" in classes and word == "to")
        ) and _abstain_starts_with_act(tokens, i + 1)

        if stage == "open":
            if is_purpose:
                stage = "purpose"
                i += 1
                continue
            if not classes:
                return False
            if "conn" in classes or "polite" in classes or "pron" in classes or "time" in classes:
                i += size
                continue
            if "topic" in classes:
                stage = "topic"
                topic_chain = 1
                topic_mods = 0
            elif "act" in classes:
                stage = "act"
            elif "prep" in classes:
                stage = "prep"
                prep_words = 0
            elif "ana" in classes or "desc" in classes:
                pass
            i += size
            continue

        if stage == "topic":
            if is_purpose:
                stage = "purpose"
                i += 1
                continue
            if not classes:
                return False
            if "desc" in classes or "time" in classes:
                if topic_mods >= _ABSTAIN_TOPIC_MODIFIER_MAX:
                    return False
                topic_mods += 1
            elif "topic" in classes:
                if topic_chain >= _ABSTAIN_TOPIC_CHAIN_MAX:
                    return False
                topic_chain += 1
                topic_mods = 0
            elif "prep" in classes:
                stage = "prep"
                prep_words = 0
            elif "act" in classes:
                stage = "act"
            elif "conn" in classes or "ana" in classes:
                stage = "open"
            else:
                return False
            i += size
            continue

        if stage == "act":
            if is_purpose:
                stage = "purpose"
                i += 1
                continue
            if not classes:
                return False
            if "topic" in classes:
                stage = "topic"
                topic_chain = 1
                topic_mods = 0
            elif "ana" in classes:
                stage = "open"
            elif "prep" in classes:
                stage = "prep"
                prep_words = 0
            elif "conn" in classes:
                stage = "open"
            else:
                return False
            i += size
            continue

        if stage == "prep":
            if is_purpose:
                stage = "purpose"
                i += 1
                continue
            if "conn" in classes:
                stage = "open"
                i += size
                continue
            if prep_words >= _ABSTAIN_FREE_NP_MAX_WORDS:
                return False
            step = size if size else 1
            prep_words += step
            i += step
            continue

        if "act" not in classes:
            return False
        stage = "act"
        i += size
        continue
    return True


# [W8-e1 2026-10-05] Time-signal detector (q08 stale-fact)
_TIME_SIGNAL_RE = re.compile(
    r"\b(hiện tại|hiện nay|hôm nay|bây giờ|currently|latest)\b",
    re.IGNORECASE,
)


def question_has_time_signal(question: str) -> bool:
    """[W8-e1] Question có signal hỏi trạng thái hiện tại của thế giới?"""
    return bool(_TIME_SIGNAL_RE.search(str(question or "")))


def is_refusal_abstain_answer(answer: str) -> bool:
    """[W7-e5] Tier-1 detector: answer là lời TỪ CHỐI trung thực tường minh."""
    text = str(answer or "").strip()
    if not text:
        return False
    lowered = text.lower()
    if _ABSTAIN_URL_RE.search(text) or _ABSTAIN_DIGIT_RE.search(text):
        return False
    matches = list(
        re.finditer(
            "|".join(re.escape(marker) for marker in _ABSTAIN_REFUSAL_MARKERS), lowered
        )
    )
    if not matches:
        return False
    # [B1c GAP-1 FIX] Symmetric BC-1 assertion guard: scan BOTH prefix (before refusal marker)
    # and suffix (after refusal marker) for copula/claim/action assertions, stripping only
    # self-identity phrases ("tôi là scp", "mình là scp", "i am scp").
    _self_id_re = re.compile(r"\b(?:tôi\s+là\s+scp|mình\s+là\s+scp|i\s+am\s+scp|i'm\s+scp)\b", re.IGNORECASE)
    for match in matches:
        prefix_clean = _self_id_re.sub("", lowered[: match.start()])
        suffix_clean = _self_id_re.sub("", lowered[match.end() :])
        if _ABSTAIN_ASSERTION_RE.search(prefix_clean) or _ABSTAIN_ASSERTION_RE.search(suffix_clean):
            return False
        if _ABSTAIN_CLAIM_CUE_RE.search(prefix_clean) or _ABSTAIN_CONV_ASSERTION_RE.search(prefix_clean):
            return False
    if not _refusal_segment_is_safe(_self_id_re.sub("", lowered[: matches[0].start()])):
        return False
    for index, match in enumerate(matches):
        segment_end = (
            matches[index + 1].start() if index + 1 < len(matches) else len(lowered)
        )
        if not _refusal_segment_is_safe(lowered[match.end() : segment_end]):
            return False
    return True


def is_honest_abstain_answer(answer: str) -> bool:
    """[W7-e5] Full detector: answer KHÔNG chứa factual claim cần verify."""
    if is_refusal_abstain_answer(answer):
        return True
    text = str(answer or "").strip()
    if not text:
        return False
    lowered = text.lower().replace("\u2019", "'")
    if (
        _ABSTAIN_URL_RE.search(text)
        or _ABSTAIN_DIGIT_RE.search(text)
        or _ABSTAIN_CLAIM_CUE_RE.search(text)
        or _ABSTAIN_CONV_ASSERTION_RE.search(text)
    ):
        return False
    return bool(_ABSTAIN_CONV_WHITELIST_RE.fullmatch(lowered))
