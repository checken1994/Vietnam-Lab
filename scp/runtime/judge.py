"""
SCP - Redesigned TaskJudge
Replaces the bloated RealityJudge and Multi-SLM engine.

[2026-08-29 Cổng D] Two-tier verification:
  Tier 1 (deterministic, ~1ms): tier1_guard — cấu trúc sai bị chém NGAY,
    không tốn một token LLM. LLM không bao giờ là Single Point of Truth
    cho các lỗi máy đọc được.
  Tier 2 (semantic): chỉ câu hỏi ngữ nghĩa mới xuống LLM, và LLM trả về
    TRI-STATE — None (không quyết định được / hai model bất đồng) →
    UNKNOWN + ESCALATE cho người, thay vì KILL oan (fail-closed đúng nghĩa).
"""
import asyncio
import logging
import re
import threading
from typing import Any

from scp.runtime.judge_llm import _llm_judge
from scp.security.tier1_guard import check as tier1_check
from scp.verifier import IndependentVerifier

logger = logging.getLogger("scp.judge")

# [W3-e1] Tín hiệu security THẬT ở mức judge: Tier-1 chặn nội dung cố tình
# mang marker nội bộ (tamper/anti-tamper signal). Các failure Tier-1 khác
# (REJECT_EMPTY, REJECT_GROUNDING, ...) và semantic-FAIL trên câu benign là
# VERIFICATION-FAIL — governance phải là ESCALATE (abstain trung thực), không
# phải KILL (mức dành cho nội dung nguy hiểm thật; đường security-wide vẫn
# được _ask_impl._is_true_security_threat / security lane enforcing độc lập).
_SECURITY_TIER1_TAGS = frozenset({"REJECT_INTERNAL_MARKER"})

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
    # [W11-f2 2026-10-06] 'chưa có dữ liệu' ≡ 'không có dữ liệu' về nghĩa —
    # W10 battery (server_log_runA.log:277 q08, server_log_runB.log:217)
    # answer dùng đúng cụm này bị chấm FAIL/UNKNOWN thay ABSTAIN (marker gap).
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
# đường FAIL/verify, không được deliver qua nhãn abstain. Topic-mention
# ("không có dữ liệu về thủ đô") vẫn là abstain hợp lệ.
_ABSTAIN_ASSERTION_RE = re.compile(r"\b(là|thì|đang|are|is|was|were)\b", re.IGNORECASE)
# [W8-e2 2026-10-05 — BC-2] Assertion-verb closed set RIÊNG cho tier hội thoại:
# câu conv-marker ("Hello, ...") kèm động từ khẳng định sự thật bên ngoài
# ("birds fly south in winter") không phải abstain thuần → conservative False
# (đường verify giữ nguyên). fly/means/bay... nằm ngoài _ABSTAIN_ASSERTION_RE
# và ngoài _ABSTAIN_CLAIM_CUE_RE (không có copula) — lỗ hổng BC-2: conv marker
# che claim. Set ĐÓNG, conservative: động từ nào chưa liệt kê mà nằm trong câu
# conv sẽ vẫn đi verify (fail-closed giữ nguyên); self-report ("I'm fine",
# "doing well") không chứa verb nào trong set nên không bị phá.
_ABSTAIN_CONV_ASSERTION_RE = re.compile(
    r"\b(fly|flies|flew|flying|means|meant|mean|migrate|migrates|migrated"
    r"|lives?|eats?|ate|grows?|grew|runs?|ran|causes?|caused|happens?|happened"
    r"|makes?|made|works?|needs?|swims?|bay|nghĩa là|sống|ăn|mọc|chạy|gây"
    r"|xảy ra|di cư)\b",
    re.IGNORECASE,
)
# [W16-g1 2026-10-07 — BC-E1/E2/E3 harden lần 3] ĐẢO CHIỀU cả hai tier
# detector (khuyến nghị checker độc lập; closed-setAssertion-verb đã fail 2
# lần: BC-2 "fly" → nay "revolves"/"tăng" — whack-a-mole không chấp nhận):
#   * Tier hội thoại: thay "conv marker + KHÔNG match claim-cue → abstain"
#     bằng WHITELIST self-report fullmatch — toàn bộ câu phải ghép được từ
#     các fragment chào hỏi / self-report tình trạng thuần (KHÔNG chứa động
#     từ khẳng định về thế giới ngoài). Mọi nội dung khác (kể cả không match
#     cue nào) → False (đường verify/FAIL giữ nguyên — fail-closed).
#   * Tier refusal: post-refusal segment chỉ được phép là topic-mention /
#     anaphora / purpose-infinitive theo grammar đóng (_refusal_segment_is_safe);
#     mọi segment khác (VD "nhưng giá cổ phiếu tăng") → False.
# Giá đổi (conservative, chấp nhận bởi owner task W16-g1): refusal/conv thật
# nằm NGOÀI whitelist grammar → rơi đường verify (withheld) thay vì
# ABSTAIN-delivery — KHÔNG bao giờ mở đường deliver claim.
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
# Câu conv hợp lệ = chuỗi fragment ghép bằng dấu câu/khoảng trắng, KHÔNG có
# gì khác (fullmatch — mảnh lạ bất kỳ → False).
_ABSTAIN_CONV_WHITELIST_RE = re.compile(
    rf"^\s*(?:{_ABSTAIN_CONV_FRAGMENT_RE.pattern})"
    rf"(?:[\s,.!?;:()\[\]{{}}\"…–—-]*(?:{_ABSTAIN_CONV_FRAGMENT_RE.pattern}))*"
    r"[\s,.!?;:()\[\]{}\"…–—-]*$",
    re.IGNORECASE,
)
# --- Safe-segment grammar cho tier refusal (post-refusal inversion) ---------
# Closed class sets — mỗi entry liệt kê tường minh (máy đọc được); free words
# CHỈ được phép bên trong phrase governed bởi giới từ ("về thủ đô" — mention,
# không assertion). Động từ khẳng định về thế giới ngoài KHÔNG thuộc class nào.
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
    "now", "currently", "today",
})
# Topic thuần dữ liệu/cập nhật — descriptor của marker "không có dữ liệu ...";
# KHÔNG chứa "giá" (mở đường "giá tăng" qua bare-topic) — topic ngoài list
# phải đi qua giới từ ("về giá cổ phiếu").
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
# Động từ HÀNH ĐỘNG CỦA ASSISTANT (xác minh/tra cứu/trả lời...) — mô tả act
# giao tiếp, không khẳng định sự thật thế giới ngoài.
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
# Anaphora-np: tham chiếu nội bộ câu hỏi/đó — không thể tự tạo claim.
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
# Giới hạn cấu trúc (conservative bounds — chặn NP dài kiểu "nhưng giá cổ
# phiếu tăng" và prep-phrase dài mang mệnh đề quan hệ).
_ABSTAIN_FREE_NP_MAX_WORDS = 5
_ABSTAIN_TOPIC_CHAIN_MAX = 3
_ABSTAIN_TOPIC_MODIFIER_MAX = 3


def _abstain_entries_at(tokens: list[str], i: int) -> tuple[str, frozenset[str], int]:
    """Match longest multiword entry (≤3 từ) tại vị trí i qua các class đóng.

    Trả về (candidate, tên-class, số-token); không khớp → ("", frozenset(), 0).
    """
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
    abstain-safe khi toàn bộ token ghép được từ class đóng:

      open   : connector/politeness/pronoun/time (lặp); hoặc vào topic/act/
               prep/purpose/anaphora; free word → False
      topic  : topic (chain ≤3) + modifier desc/time (≤3); ra prep/purpose/
               act/connector/anaphora; free word → False
      act    : ra topic/anaphora/prep/connector/purpose; free word → False
               (chặn "xác minh giá tăng" — verb + claim-np)
      prep   : mention governed bởi giới từ — free words ≤5, có thể kết bằng
               purpose/connector
      purpose: "để/to" + act-verb bắt buộc ("để trả lời ...")

    Segment rỗng/thuần punctuation → True. Đây là ĐẢO CHIỀU so với guard
    copula BC-1: mọi shape không nằm trong grammar → False (verify path).
    """
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
                return False  # free word ngoài whitelist ở vị trí mở
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
                pass  # anaphora/descriptor cô lập — vẫn open
            i += size
            continue

        if stage == "topic":
            if is_purpose:
                stage = "purpose"
                i += 1
                continue
            if not classes:
                return False  # "… thời tiết trực tiếp | tăng" — free word
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
                return False  # chặn "xác minh giá tăng" (verb + claim-np)
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
            # free word (size=0) cũng phải tiêu tiến 1 token — tránh vòng lặp
            step = size if size else 1
            prep_words += step
            i += step
            continue

        # stage == "purpose": bắt buộc act-verb kế tiếp
        if "act" not in classes:
            return False
        stage = "act"
        i += size
        continue
    return True
# [W8-e1 2026-10-05] Time-signal detector (q08 stale-fact): câu hỏi hỏi về
# trạng thái HIỆN TẠI của thế giới. Answer không có evidence từ web/data cho
# câu hỏi này không được PASS-thuần (judge LLM có training cutoff stale —
# GA.md B1b W7-battery run C). Signals: danh sách ĐÓNG theo owner-đã-duyệt;
# \b chặn false-positive dạng "coroutine" chứa "current".
_TIME_SIGNAL_RE = re.compile(
    r"\b(hiện tại|hiện nay|hôm nay|bây giờ|currently|latest)\b",
    re.IGNORECASE,
)


def question_has_time_signal(question: str) -> bool:
    """[W8-e1] Question có signal hỏi trạng thái hiện tại của thế giới?

    Deterministic, máy đọc (không cảm tính): chỉ match danh sách signal đóng
    ở _TIME_SIGNAL_RE. Rỗng → False.
    """
    return bool(_TIME_SIGNAL_RE.search(str(question or "")))


def is_refusal_abstain_answer(answer: str) -> bool:
    """[W7-e5] Tier-1 detector: answer là lời TỪ CHỐI trung thực tường minh.

    Điều kiện: non-empty, match marker từ chối/thiếu dữ liệu, không chứa URL
    và không chứa chữ số (refusal kèm số liệu/URL = answer có payload cần
    verify → không phải abstain, đường FAIL giữ nguyên). Rỗng → False
    (REJECT_EMPTY vẫn là FAIL theo hợp đồng W3-e1 đã pin).

    [W7-hardening — BC-1]: refusal marker theo sau bởi assertion-verb
    ("... Donald Trump là tổng thống Mỹ.") = claim bọc wrapper từ chối →
    False (đường FAIL/verify giữ nguyên). Topic-mention sau refusal
    ("không có dữ liệu về thủ đô") vẫn là abstain hợp lệ.

    [W16-g1 2026-10-07 — BC-E1/E3]: BC-1 chỉ chặn copula (là/thì/đang/is/are/
    was/were) — "Tôi chưa có dữ liệu, nhưng giá cổ phiếu tăng" lọt (tăng
    không copula). ĐẢO CHIỀU: prefix trước marker đầu + MỌI post-marker
    segment phải safe theo grammar đóng `_refusal_segment_is_safe`
    (topic-mention/anaphora/purpose/act-verb-assistant; free words chỉ trong
    phrase governed giới từ). Mọi segment khác — kể cả không match copula hay
    cue nào — → False (verify path). Copula guard giữ nguyên làm layer belt.
    """
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
    for match in matches:
        if _ABSTAIN_ASSERTION_RE.search(lowered[match.end():]):
            # [W7-hardening — BC-1] claim assertion sau refusal marker → có
            # payload cần verify, không phải abstain thuần.
            return False
    # [W16-g1 — BC-E1/E3] inversion: prefix + mọi post-marker segment phải
    # safe-shape; một segment lạ → False (conservative, fail-closed).
    if not _refusal_segment_is_safe(lowered[: matches[0].start()]):
        return False
    for index, match in enumerate(matches):
        segment_end = (
            matches[index + 1].start() if index + 1 < len(matches) else len(lowered)
        )
        if not _refusal_segment_is_safe(lowered[match.end() : segment_end]):
            return False
    return True


def is_honest_abstain_answer(answer: str) -> bool:
    """[W7-e5] Full detector: answer KHÔNG chứa factual claim cần verify.

    Hai lớp tường minh:
      1. refusal tường minh (is_refusal_abstain_answer);
      2. hội thoại xã giao/self-report — [W16-g1 — BC-E2] ĐẢO CHIỀU: toàn bộ
         câu phải fullmatch whitelist self-report thuần
         (_ABSTAIN_CONV_WHITELIST_RE — chào hỏi + self-report tình trạng,
         KHÔNG chứa động từ khẳng định về thế giới ngoài). Mọi nội dung khác
         (kể cả conv marker + text không match cue nào, VD "Hello! The earth
         revolves around the sun.") → False = "có claim", verify path.
    Guards belt giữ nguyên: claim-cue / conv-assertion closed set / URL /
    chữ số phủ định conv-tier như trước (BC-1/W8).
    Anti-lộng: "Theo dữ liệu được cung cấp, câu trả lời là Donald Trump."
    KHÔNG match lớp nào → False → đường PASS/FAIL giữ nguyên.
    """
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
    # [W16-g1 — BC-E2] whitelist fullmatch: chỉ self-report thuần → abstain.
    return bool(_ABSTAIN_CONV_WHITELIST_RE.fullmatch(lowered))


def _run_crosscheck_sync(question: str, ai_answer: str, context: str) -> dict[str, Any]:
    """[A2] Chạy cross_verify (async) từ sync judge() — crosscheck phải chạy THẬT.

    Audit (M2): trước đây sync judge() gọi `cross_verify(...)` KHÔNG await →
    nhận coroutine → `cross["final"]` TypeError → except nuốt im lặng → mọi
    sync verdict thực chất chỉ qua single cascade. Crosscheck đã "sống lại".

    Caller sync judge() chạy trong worker thread (asyncio.to_thread) nên thường
    không có event loop → asyncio.run trực tiếp. Nếu vô tình gọi từ thread đang
    chạy loop, bridge qua worker thread riêng (loop riêng) để crosscheck vẫn
    chạy thật thay vì raise RuntimeError.
    """
    from scp.runtime.multi_llm_crosscheck import cross_verify

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # silent-by-design: documented nested-loop fallback — no running loop, so one is created for the crosscheck
        return asyncio.run(cross_verify(question, ai_answer, context))

    box: dict[str, Any] = {}

    def _worker() -> None:
        try:
            box["result"] = asyncio.run(cross_verify(question, ai_answer, context))
        except BaseException as exc:  # bridge truyền lỗi nguyên trạng ra ngoài
            logger.debug("Crosscheck sync bridge worker failed: %s", exc, exc_info=True)
            box["error"] = exc

    bridge = threading.Thread(target=_worker, daemon=True, name="scp-crosscheck-sync-bridge")
    bridge.start()
    bridge.join()
    if "error" in box:
        raise box["error"]
    return box["result"]


class RealityJudge:
    """
    Unified Task Judge. Delegates strictly to IndependentVerifier + Tier-1
    deterministic guard + tri-state LLM semantic cascade.
    """
    def __init__(self, *args, **kwargs):
        self.verifier = IndependentVerifier()
        self.judged_count = 0
        self.fail_count = 0
        # [B-S1] Số lần judge consult KB thành công (search không raise, kể cả 0 hit).
        self.knowledge_consult_count = 0


    @property
    def domain_experts(self):
        if not hasattr(self, '_experts'):
            self._experts = {}
            import importlib
            import inspect
            import os

            from scp.runtime.slm_base import BaseSLM
            experts_dir = os.path.join(os.path.dirname(__file__), 'experts')
            for filename in os.listdir(experts_dir):
                if filename.endswith('.py') and filename != '__init__.py':
                    module_name = f'scp.runtime.experts.{filename[:-3]}'
                    try:
                        module = importlib.import_module(module_name)
                        for _name, obj in inspect.getmembers(module, inspect.isclass):
                            if issubclass(obj, BaseSLM) and obj != BaseSLM:
                                try:
                                    instance = obj()
                                    self._experts[instance.domain] = instance
                                except Exception as exc:
                                    # best-effort expert discovery — a broken expert module is skipped, the rest still load
                                    logger.debug("Failed to instantiate expert %s: %s", obj, exc, exc_info=True)
                                    continue
                    except Exception as exc:
                        # same best-effort expert discovery contract as above
                        logger.debug("Failed to import expert module %s: %s", module_name, exc, exc_info=True)
                        continue
        return self._experts
    @property
    def falsification(self):
        """Lazy FalsificationEngine instance. Fail-closed on error."""
        if not hasattr(self, "_falsification") or self._falsification is None:
            try:
                from scp.meta.falsification_engine import FalsificationEngine
                self._falsification = FalsificationEngine()
            except Exception as exc:
                logger.warning("FalsificationEngine unavailable (%s: %s)", type(exc).__name__, exc, exc_info=True)
                self._falsification = None
        return self._falsification

    @property
    def error_store(self):
        """Lazy ErrorStore instance. Fail-closed on error."""
        if not hasattr(self, "_error_store") or self._error_store is None:
            try:
                from scp.brain.error_store import ErrorStore
                self._error_store = ErrorStore()
            except Exception as exc:
                logger.warning("ErrorStore unavailable (%s: %s)", type(exc).__name__, exc, exc_info=True)
                self._error_store = None
        return self._error_store

    @property
    def governance(self):
        """Lazy Governance instance. Fail-closed on error."""
        if not hasattr(self, "_governance") or self._governance is None:
            try:
                from scp.meta.governance_v97 import Governance
                self._governance = Governance()
            except Exception as exc:
                logger.warning("Governance unavailable (%s: %s)", type(exc).__name__, exc, exc_info=True)
                self._governance = None
        return self._governance

    @property
    def counter_response(self):
        """Lazy CounterResponseEngine instance. Fail-closed on error."""
        if not hasattr(self, "_counter_response") or self._counter_response is None:
            try:
                from scp.security.counter_response import CounterResponseEngine
                self._counter_response = CounterResponseEngine()
            except Exception as exc:
                logger.warning("CounterResponseEngine unavailable (%s: %s)", type(exc).__name__, exc, exc_info=True)
                self._counter_response = None
        return self._counter_response

    @property
    def canary_monitor(self):
        """Lazy CanaryTokenMonitor instance. Fail-closed on error."""
        if not hasattr(self, "_canary_monitor") or self._canary_monitor is None:
            try:
                from scp.security.canary_monitor import CanaryTokenMonitor
                self._canary_monitor = CanaryTokenMonitor()
            except Exception as exc:
                logger.warning("CanaryTokenMonitor unavailable (%s: %s)", type(exc).__name__, exc, exc_info=True)
                self._canary_monitor = None
        return self._canary_monitor

    @property
    def attack_memory(self):
        """Lazy AttackPatternMemory instance. Fail-closed on error."""
        if not hasattr(self, "_attack_memory") or self._attack_memory is None:
            try:
                from scp.security.attack_memory import AttackPatternMemory
                self._attack_memory = AttackPatternMemory()
            except Exception as exc:
                logger.warning("AttackPatternMemory unavailable (%s: %s)", type(exc).__name__, exc, exc_info=True)
                self._attack_memory = None
        return self._attack_memory

    def get_v98_status(self) -> dict[str, Any]:
        """Return operational status of V98 modules."""
        return {
            "counter_response": self.counter_response is not None,
            "canary_monitor": self.canary_monitor is not None,
            "error_store": self.error_store is not None,
            "attack_memory": self.attack_memory is not None,
            "falsification": self.falsification is not None,
            "governance": self.governance is not None,
        }
    @property
    def domain_knowledge_store(self):
        """[B-S1] Lazy DomainKnowledgeStore — audit 52-mảnh @9ec8d6b (commit
        e0696f5): property cũ trả None cứng tại line này → KB nội bộ chết
        (không consult khi thẩm định, /v100/knowledge/* luôn 503).
        Fail-closed: constructor lỗi → None + log WARNING, judge vẫn chạy như cũ.
        Test/ops có thể pre-seed `self._kb_store` để inject store riêng."""
        if not hasattr(self, "_kb_store"):
            try:
                from scp.knowledge.domain_store import DomainKnowledgeStore
                self._kb_store = DomainKnowledgeStore()
            except Exception as exc:
                logger.warning(
                    "[B-S1] DomainKnowledgeStore unavailable (%s: %s) — judge continues without KB",
                    type(exc).__name__,
                    exc,
                    exc_info=True,
                )
                self._kb_store = None
        return self._kb_store

    def _consult_knowledge(self, question: str, limit: int = 3) -> list[dict[str, Any]]:
        """[B-S1] Consult DomainKnowledgeStore — kết quả CHỈ là evidence bổ trợ:
        không quyết định thay verifier/Tier-1/Tier-2 (DNA #4, #11 — LLM/KB không
        phải Single Point of Truth). Refs được ghi vào verdict output
        evidence["knowledge"] và inject vào context cho cascade ngữ nghĩa.
        Fail-closed: KB lỗi/empty → [] + log, judge chạy như cũ (không raise).
        """
        store = self.domain_knowledge_store
        if store is None:
            return []
        try:
            records = store.search(question, limit=limit)
        except Exception as exc:
            logger.warning(
                "[B-S1] KB consult failed (%s: %s) — verdict proceeds without KB",
                type(exc).__name__,
                exc,
                exc_info=True,
            )
            return []
        self.knowledge_consult_count += 1
        return [
            {
                "question": r.question,
                "answer": r.answer,
                "domain": r.domain,
                "source": r.source,
                "tier": int(r.source_tier),
                "confidence": float(r.confidence),
            }
            for r in records
        ]

    def _inject_kb_refs(self, context: str, kb_refs: list[dict[str, Any]]) -> str:
        """[B-S1] Ghép KB refs vào context như evidence bổ trợ cho cascade."""
        if not kb_refs:
            return context
        lines = "".join(
            f"\n- ({r['domain']}/{r['source']} tier={r['tier']} conf={r['confidence']:.2f}) "
            f"Q: {r['question']} A: {r['answer']}"
            for r in kb_refs
        )
        return context + "\n[KNOWLEDGE BASE REF]" + lines

    @property
    def h8_redteam(self): return None
    def judge(self, question: str, ai_answer: str = "", cycle_count: int = 0, context: str = "", **kwargs) -> dict[str, Any]:
        """Synchronous judge interface."""

        # 1. Base structural validation (is there an answer?)
        is_structurally_pass = bool(ai_answer.strip())

        is_pass = False
        escalated = False
        failures = []
        # [W7-e5] 3-state inputs: answer không chứa factual claim → ABSTAIN
        # thay FAIL; crosscheck consensus missing (benign) → ABSTAIN thay
        # UNKNOWN-escalated. Cả hai giữ governance ESCALATE (audit nguyên).
        answer_is_abstain = is_honest_abstain_answer(ai_answer)
        consensus_missing = False

        # 2. TIER-1 deterministic guard — chém trước, không tốn LLM.
        tier1 = tier1_check(question, ai_answer, context)
        slm_responses_list = []
        kb_refs: list[dict[str, Any]] = []  # [B-S1] KB refs — evidence bổ trợ
        if not tier1.passed:
            failures.extend(tier1.failures)
        elif is_structurally_pass and ai_answer:
            # 2.5. TIER-1.5: Dynamic API Expert Injection
            try:
                from scp.data_sources.domain_classifier import classify_top1
                domain = classify_top1(question)
                expert = self.domain_experts.get(domain)
                if expert:
                    resp = expert.predict(question)
                    if getattr(resp, "answer", None):
                        context += f"\n[SYSTEM EXPERT DATA] For {domain}: {resp.answer}"
                        slm_responses_list.append(resp.__dict__)
            except Exception as e:
                # silent-by-design: failure is already logged via getLogger('scp.judge').debug in the handler body
                import logging
                logging.getLogger("scp.judge").debug(f"Expert injection failed: {e}", exc_info=True)

            # 2.6. [B-S1] KB consult — tri thức nội bộ là evidence BỔ TRỢ cho
            # cascade ngữ nghĩa, không bao giờ quyết định thay verifier.
            # Fail-closed: KB lỗi/empty → kb_refs rỗng, flow như cũ.
            kb_refs = self._consult_knowledge(question)
            context = self._inject_kb_refs(context, kb_refs)

        # 3. TIER-2 semantic cascade — chỉ chạy khi Tier-1 sạch.
        #    [MẢNH 5+43] Cross-vendor verification: 2 provider khác nhau đánh giá
        #    độc lập → giảm xác suất ảo giác đồng thuận (DNA #5).

            import os as _os
            if _os.environ.get("SCP_MULTI_LLM_CROSSCHECK", "1") == "1":
                try:
                    cross = _run_crosscheck_sync(question, ai_answer, context)
                    semantic = cross["final"]  # None nếu disagree/unavailable
                    if cross["consensus"] == "disagree":
                        failures.append("multi_llm_disagreement")
                    # [W7-e5] Missing distinct providers = KHÔNG có opinion
                    # độc lập nào được thu thập (không phải FAIL của answer).
                    consensus_missing = (
                        str(cross.get("consensus", "")) == "missing_distinct_providers"
                    )
                except Exception as _cc_err:
                    # [SEC-R2-01] Crosscheck lỗi phải LOG RÕ, không treat fallback single cascade as PASS
                    logger.warning(
                        "[SEC-R2-01] multi-LLM crosscheck failed (%s: %s) — marking DEGRADED/UNCERTAIN fail-closed",
                        type(_cc_err).__name__,
                        _cc_err,
                        exc_info=True,
                    )
                    failures.append("crosscheck_fallback_degraded")
                    semantic = _llm_judge(question, ai_answer, context)
            else:
                semantic = _llm_judge(question, ai_answer, context)

            is_degraded = "crosscheck_fallback_degraded" in failures
            if semantic is None:
                escalated = True
            elif semantic == "PASS" or semantic is True:
                # [SEC-R2-01] Never grant full PASS when multi-LLM crosscheck failed.
                # Must be marked DEGRADED; is_pass remains False to prevent UPHOLD governance.
                if is_degraded:
                    is_pass = False
                else:
                    is_pass = True
            else:
                failures.append("semantic_judge_fail")


        self.judged_count += 1
        if not is_pass and not escalated:
            self.fail_count += 1

        if escalated:
            # [W7-e5] ABSTAIN thay UNKNOWN trên đường escalated benign: answer
            # không chứa claim (không có gì để verify) HOẶC crosscheck thiếu
            # consensus (chưa từng có opinion độc lập adjudicate answer).
            # Degraded (fallback cascade lỗi) giữ nguyên UNKNOWN; disagree giữ
            # nguyên UNKNOWN (đã pin W3-e1/T05); security-tag → không ABSTAIN.
            # [W7-hardening — BC-3] multi_llm_disagreement + abstain-shaped
            # answer → UNKNOWN (không ABSTAIN): disagree là "2 opinion bất
            # nhất" — contract W3-e1/T05 pin rõ, không đổi class theo shape.
            _esc_benign = not _SECURITY_TIER1_TAGS.intersection(failures)
            _esc_is_degraded = "crosscheck_fallback_degraded" in failures
            _esc_is_disagreement = "multi_llm_disagreement" in failures
            _abstain_reasons: list[str] = []
            if _esc_benign and not _esc_is_degraded and not _esc_is_disagreement:
                if answer_is_abstain:
                    _abstain_reasons.append("answer_without_verifiable_claim")
                if consensus_missing:
                    _abstain_reasons.append("crosscheck_consensus_missing")
            if _abstain_reasons:
                return {
                    "verdict": "ABSTAIN",
                    "confidence": 0.0,
                    "reasoning": (
                        "Honest abstain — no independent verification available "
                        f"({', '.join(_abstain_reasons)}); not a FAIL, escalate for audit"
                    ),
                    "cycle_count": cycle_count,
                    "failures": failures + ["semantic_judge_unavailable"],
                    "final_answer": ai_answer,
                    "slm_responses": slm_responses_list,
                    "evidence": {
                        "governance_decision": "ESCALATE",
                        "knowledge": kb_refs,  # [B-S1] KB consult refs (bổ trợ)
                        "abstain_reasons": _abstain_reasons,  # [W7-e5] marker audit
                    },
                }
            return {
                "verdict": "UNKNOWN",
                "confidence": 0.0,
                "reasoning": "Semantic judge unavailable or model disagreement — escalated to human",
                "cycle_count": cycle_count,
                "failures": failures + ["semantic_judge_unavailable"],
                "final_answer": ai_answer,
            "slm_responses": slm_responses_list,
                "evidence": {
                    "governance_decision": "ESCALATE",
                    "knowledge": kb_refs,  # [B-S1] KB consult refs (bổ trợ)
                },
            }

        conf, det_conf, sem_conf = self._calibrate_confidence(
            is_pass=is_pass,
            escalated=escalated,
            is_structurally_pass=is_structurally_pass,
            failures=failures,
            kb_refs=kb_refs,
        )

        is_degraded = "crosscheck_fallback_degraded" in failures
        # [W3-e1] Tách hai khái niệm từng bị trộn vào một mapping:
        #   (i)  verification FAIL trên câu benign (semantic judge không xác
        #        minh được / grounding trượt / không có answer) → governance
        #        ESCALATE kèm lý do verification — KHÔNG được KILL;
        #   (ii) KILL chỉ dành cho security-threat thật mà judge tự thấy
        #        (_SECURITY_TIER1_TAGS). Đường security-wide (security lane,
        #        FLAGGED, threat/injection) vẫn do boundary
        #        (_ask_impl._is_true_security_threat) enforcing KILL-withhold.
        # [W7-e5] Thêm nhánh thứ ba: answer abstain trung thực (không chứa
        # factual claim) → verdict ABSTAIN + ESCALATE, không chấm FAIL.
        if is_degraded:
            verdict_val = "DEGRADED"
            gov_val = "DEGRADED"
            cross_agreement_val = False
        elif is_pass:
            verdict_val = "PASS"
            gov_val = "UPHOLD"
            cross_agreement_val = not escalated
        elif _SECURITY_TIER1_TAGS.intersection(failures):
            # [W7-e5] Anti-lộng: security-tag không bao giờ bị hạ thành ABSTAIN.
            verdict_val = "FAIL"
            gov_val = "KILL"
            cross_agreement_val = not escalated
        elif answer_is_abstain:
            verdict_val = "ABSTAIN"
            gov_val = "ESCALATE"
            cross_agreement_val = not escalated
        else:
            verdict_val = "FAIL"
            gov_val = "ESCALATE"
            cross_agreement_val = not escalated

        if verdict_val == "ABSTAIN":
            reasoning_val = (
                "Answer is an honest abstention without a verifiable factual "
                "claim — classified ABSTAIN (not FAIL); governance ESCALATE for audit"
            )
        elif gov_val == "ESCALATE" and not is_pass:
            # [W3-e1] Lý do verification phải quan sát được — abstain trung
            # thực thay vì KILL oan cho câu benign.
            reasoning_val = (
                "Verification failed on a benign request — answer withheld as "
                f"unverified (failures: {', '.join(failures) or 'n/a'}); escalate, not KILL"
            )
        else:
            reasoning_val = "Delegated to IndependentVerifier and LLM Semantic Judge"

        return {
            "verdict": verdict_val,
            "confidence": conf,
            "deterministic_confidence": det_conf,
            "semantic_confidence": sem_conf,
            "cross_model_agreement": cross_agreement_val,
            "degraded": is_degraded,
            "reasoning": reasoning_val,
            "cycle_count": cycle_count,
            "failures": failures,
            "final_answer": ai_answer,
            "slm_responses": slm_responses_list,
            "evidence": {
                "governance_decision": gov_val,
                "knowledge": kb_refs,  # [B-S1] KB consult refs (bổ trợ)
                # [W7-e5] marker audit — chỉ ABSTAIN mới có reason; verdict
                # khác → list rỗng (additive, không đổi shape cũ).
                "abstain_reasons": (
                    ["answer_without_verifiable_claim"] if verdict_val == "ABSTAIN" else []
                ),
            },
        }

    async def judge_async(self, question: str, ai_answer: str = "", cycle_count: int = 0, context: str = "", **kwargs) -> dict[str, Any]:
        """Asynchronous judge interface."""
        from scp.runtime.judge_llm import _llm_judge_async

        if not ai_answer:
            is_structurally_pass = False
        else:
            is_structurally_pass = bool(ai_answer.strip())

        is_pass = False
        escalated = False
        failures = []
        # [W7-e5] Cùng 3-state inputs với judge() sync (xem comment ở đó).
        answer_is_abstain = is_honest_abstain_answer(ai_answer)
        consensus_missing = False

        tier1 = tier1_check(question, ai_answer, context)
        slm_responses_list = []
        kb_refs: list[dict[str, Any]] = []  # [B-S1] KB refs — evidence bổ trợ
        if not tier1.passed:
            failures.extend(tier1.failures)
        elif is_structurally_pass and ai_answer:
            try:
                from scp.data_sources.domain_classifier import classify_top1
                domain = classify_top1(question)
                expert = self.domain_experts.get(domain)
                if expert:
                    resp = expert.predict(question)
                    if getattr(resp, "answer", None):
                        context += f"\n[SYSTEM EXPERT DATA] For {domain}: {resp.answer}"
                        slm_responses_list.append(resp.__dict__)
            except Exception as e:
                # silent-by-design: failure is already logged via getLogger('scp.judge').debug in the handler body
                import logging
                logging.getLogger("scp.judge").debug(f"Expert injection failed: {e}", exc_info=True)

            # 2.6. [B-S1] KB consult — tri thức nội bộ là evidence BỔ TRỢ cho
            # cascade ngữ nghĩa, không bao giờ quyết định thay verifier.
            # Fail-closed: KB lỗi/empty → kb_refs rỗng, flow như cũ.
            kb_refs = self._consult_knowledge(question)
            context = self._inject_kb_refs(context, kb_refs)

            import os as _os
            if _os.environ.get("SCP_MULTI_LLM_CROSSCHECK", "1") == "1":
                try:
                    from scp.runtime.multi_llm_crosscheck import cross_verify
                    cross = await cross_verify(question, ai_answer, context)
                    semantic = cross["final"]
                    if cross["consensus"] == "disagree":
                        failures.append("multi_llm_disagreement")
                    # [W7-e5] Missing distinct providers ≠ FAIL của answer.
                    consensus_missing = (
                        str(cross.get("consensus", "")) == "missing_distinct_providers"
                    )
                except Exception as _cc_err:
                    logger.warning(
                        "[SEC-R2-01] async multi-LLM crosscheck failed (%s: %s) — marking DEGRADED/UNCERTAIN fail-closed",
                        type(_cc_err).__name__,
                        _cc_err,
                        exc_info=True,
                    )
                    failures.append("crosscheck_fallback_degraded")
                    semantic = await _llm_judge_async(question, ai_answer, context)
            else:
                semantic = await _llm_judge_async(question, ai_answer, context)

            is_degraded = "crosscheck_fallback_degraded" in failures
            if semantic is None:
                escalated = True
            elif semantic == "PASS" or semantic is True:
                if is_degraded:
                    is_pass = False
                else:
                    is_pass = True
            else:
                failures.append("semantic_judge_fail")

        self.judged_count += 1
        if not is_pass and not escalated:
            self.fail_count += 1

        if escalated:
            # [W7-e5] Cùng hợp đồng ABSTAIN thay UNKNOWN với judge() sync
            # (benign + không degraded + abstain-answer/consensus-missing).
            # [W11-f3 2026-10-06 — BC-3 async parity] thêm exclusion
            # multi_llm_disagreement giống sync path: disagreement là '2
            # opinion bất nhất' — contract W3-e1/T05 pin UNKNOWN, không đổi
            # class theo shape answer (disagreement + abstain-shaped trên
            # đường async từng rơi ABSTAIN do thiếu exclusion này).
            _esc_benign = not _SECURITY_TIER1_TAGS.intersection(failures)
            _esc_is_degraded = "crosscheck_fallback_degraded" in failures
            _esc_is_disagreement = "multi_llm_disagreement" in failures
            _abstain_reasons: list[str] = []
            if _esc_benign and not _esc_is_degraded and not _esc_is_disagreement:
                if answer_is_abstain:
                    _abstain_reasons.append("answer_without_verifiable_claim")
                if consensus_missing:
                    _abstain_reasons.append("crosscheck_consensus_missing")
            if _abstain_reasons:
                return {
                    "verdict": "ABSTAIN",
                    "confidence": 0.0,
                    "reasoning": (
                        "Honest abstain — no independent verification available "
                        f"({', '.join(_abstain_reasons)}); not a FAIL, escalate for audit"
                    ),
                    "cycle_count": cycle_count,
                    "failures": failures + ["semantic_judge_unavailable"],
                    "final_answer": ai_answer,
                    "slm_responses": slm_responses_list,
                    "evidence": {
                        "governance_decision": "ESCALATE",
                        "knowledge": kb_refs,  # [B-S1] KB refs (bổ trợ)
                        "abstain_reasons": _abstain_reasons,  # [W7-e5] marker audit
                    },
                }
            return {
                "verdict": "UNKNOWN",
                "confidence": 0.0,
                "reasoning": "Semantic judge unavailable or model disagreement — escalated to human",
                "cycle_count": cycle_count,
                "failures": failures + ["semantic_judge_unavailable"],
                "final_answer": ai_answer,
                "slm_responses": slm_responses_list,
                "evidence": {"governance_decision": "ESCALATE", "knowledge": kb_refs},  # [B-S1] KB refs (bổ trợ)
            }

        conf, det_conf, sem_conf = self._calibrate_confidence(
            is_pass=is_pass,
            escalated=escalated,
            is_structurally_pass=is_structurally_pass,
            failures=failures,
            kb_refs=kb_refs,
        )

        is_degraded = "crosscheck_fallback_degraded" in failures
        # [W3-e1] Cùng hợp đồng với judge() sync: benign verification FAIL →
        # ESCALATE (kèm lý do), KILL chỉ cho _SECURITY_TIER1_TAGS.
        # [W7-e5] + nhánh ABSTAIN cho answer abstain trung thực.
        if is_degraded:
            verdict_val = "DEGRADED"
            gov_val = "DEGRADED"
            cross_agreement_val = False
        elif is_pass:
            verdict_val = "PASS"
            gov_val = "UPHOLD"
            cross_agreement_val = not escalated
        elif _SECURITY_TIER1_TAGS.intersection(failures):
            # [W7-e5] Anti-lộng: security-tag không bao giờ bị hạ thành ABSTAIN.
            verdict_val = "FAIL"
            gov_val = "KILL"
            cross_agreement_val = not escalated
        elif answer_is_abstain:
            verdict_val = "ABSTAIN"
            gov_val = "ESCALATE"
            cross_agreement_val = not escalated
        else:
            verdict_val = "FAIL"
            gov_val = "ESCALATE"
            cross_agreement_val = not escalated

        if verdict_val == "ABSTAIN":
            reasoning_val = (
                "Answer is an honest abstention without a verifiable factual "
                "claim — classified ABSTAIN (not FAIL); governance ESCALATE for audit"
            )
        elif gov_val == "ESCALATE" and not is_pass:
            reasoning_val = (
                "Verification failed on a benign request — answer withheld as "
                f"unverified (failures: {', '.join(failures) or 'n/a'}); escalate, not KILL"
            )
        else:
            reasoning_val = "Delegated to IndependentVerifier and LLM Semantic Judge"

        return {
            "verdict": verdict_val,
            "confidence": conf,
            "deterministic_confidence": det_conf,
            "semantic_confidence": sem_conf,
            "cross_model_agreement": cross_agreement_val,
            "degraded": is_degraded,
            "reasoning": reasoning_val,
            "cycle_count": cycle_count,
            "failures": failures,
            "final_answer": ai_answer,
            "slm_responses": slm_responses_list,
            "evidence": {
                "governance_decision": gov_val,
                "knowledge": kb_refs,  # [B-S1] KB refs (bổ trợ)
                # [W7-e5] marker audit — additive, verdict khác → list rỗng.
                "abstain_reasons": (
                    ["answer_without_verifiable_claim"] if verdict_val == "ABSTAIN" else []
                ),
            },
        }

    @staticmethod
    def _calibrate_confidence(
        is_pass: bool,
        escalated: bool,
        is_structurally_pass: bool,
        failures: list[str],
        kb_refs: list[dict[str, Any]],
    ) -> tuple[float, float, float]:
        """Dynamically compute calibrated confidence based on verified evidence rather than constants.

        Factors:
        - Structural integrity
        - Multi-model crosscheck consensus vs degraded single-judge fallback
        - Corroboration by internal Knowledge Base evidence
        - Deductions per failure/warning tag
        """
        if "crosscheck_fallback_degraded" in failures:
            # [SEC-R2-01] Degraded fallback cannot exceed 0.50; strictly clamp confidence
            det = 0.5 if is_structurally_pass else 0.0
            sem = 0.35
            conf = round(0.30 * det + 0.70 * sem, 4)
            return conf, round(det, 4), round(sem, 4)

        if escalated or not is_pass:
            det = 1.0 if is_structurally_pass else 0.0
            return 0.0, det, 0.0

        det = 1.0 if is_structurally_pass else 0.0

        sem = 0.95


        if kb_refs:
            avg_kb = sum(float(r.get("confidence", 0.8)) for r in kb_refs) / len(kb_refs)
            sem = min(0.99, sem + 0.03 * avg_kb)

        other_failures = [f for f in failures if f != "crosscheck_fallback_degraded"]
        sem = max(0.1, min(1.0, sem - 0.05 * len(other_failures)))

        conf = round(0.30 * det + 0.70 * sem, 4)
        return conf, round(det, 4), round(sem, 4)

    async def judge_with_react_fallback(self, *args, **kwargs) -> dict[str, Any]:
        return await self.judge_async(*args, **kwargs)

    def get_stats(self) -> dict:
        return {"total_judged": self.judged_count, "total_failed": self.fail_count}

    def analyze_session_rogue(self, *args, **kwargs) -> dict | None:
        return None

    # Stubs for legacy interfaces so we don't break import sites
    async def run_threat_simulation(self, *args, **kwargs): pass
    async def run_threat_intel_crawl(self, *args, **kwargs): return []
    def get_v100_status(self): return {}
    async def run_scheduled_crawl(self, *args, **kwargs): return {}
    async def schedule_v100_background_jobs(self): pass
    async def schedule_background_jobs(self):
        return await self.schedule_v100_background_jobs()
