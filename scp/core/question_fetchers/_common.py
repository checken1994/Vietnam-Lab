"""
[Task 8-A] Shared helpers for question fetchers — extracted from real_question_fetcher.py

TẠI SAO: real_question_fetcher.py 1,467 LOC god file. Tách shared helpers
(logger, _http_get_json, _SOURCE_HEALTH, _clean, _guess_domain, _extract_specific_fact,
init_external_questions_db) vào module này để fetcher modules có thể import mà
không gây circular import với real_question_fetcher.py.
"""
import html
import json
import logging
import re
import threading
import urllib.parse
import urllib.request
from typing import Optional

from scp.security.url_safety import (  # noqa: B310
    SAFE_MAX_REDIRECT_HOPS,
    enforce_egress_policy,
    safe_urlopen,
    same_egress_host,
    session_without_credentials,
    strip_credentials_on_host_change,
    validate_redirect_target,
    validate_url,
)

logger = logging.getLogger("scp.real_fetcher")

# [SEC-A] 3xx statuses whose Location header the requests hop loop must
# re-validate before following (same set the urllib handler covers).
_REDIRECT_STATUS_CODES = frozenset({301, 302, 303, 307, 308})

try:
    import requests
    HAS_REQUESTS = True
    #  Persistent session for connection pooling — reuses TCP connections
    _SESSION = requests.Session()
    _SESSION.headers.update({
        'User-Agent': 'SCP-V70-Bot/1.0 (https://scp-vietnam.example.com; '
                      'Vietnamese educational research project; contact: scp-vietnam@example.com)',
        'Accept': 'application/json',
    })
except ImportError as exc:
    # silent-by-design: requests is an optional dependency; fetchers degrade to urllib.
    logger.debug("requests unavailable; fetchers fall back to urllib: %s", exc, exc_info=True)
    HAS_REQUESTS = False
    _SESSION = None

try:
    from scp.core.db_manager import db_exec
    _DB_AVAILABLE = True
except Exception as exc:
    # silent-by-design: db_manager import is optional at module load; availability flag drives fallback.
    logger.debug("db_manager unavailable for question fetchers: %s", exc, exc_info=True)
    _DB_AVAILABLE = False

#  Source health tracking — skip sources that fail 3 times in a row
# [EXEC-3] _SOURCE_HEALTH_LOCK must be a real Lock — it is mutated from
# ThreadPoolExecutor workers. Was None → race condition on _SOURCE_HEALTH dict.
_SOURCE_HEALTH: dict[str, int] = {}  # source_name → consecutive_failures
_SOURCE_HEALTH_LOCK = threading.Lock()
_MAX_CONSECUTIVE_FAILURES = 5  # [V92 MAX] more resilient — skip after 5 failures


# ============================================================
# DB Schema
# ============================================================
def init_external_questions_db():
    """Tạo bảng external_questions nếu chưa có."""
    if not _DB_AVAILABLE:
        return
    try:
        db_exec("""
            CREATE TABLE IF NOT EXISTS external_questions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                question TEXT NOT NULL,
                ai_answer TEXT,
                source TEXT NOT NULL,
                source_url TEXT,
                domain TEXT,
                category TEXT,
                fetched_at TEXT NOT NULL,
                used INTEGER DEFAULT 0,
                used_at TEXT,
                UNIQUE(question)
            )
        """)
        #  Indexes for fast get_unused() and stats queries
        db_exec("CREATE INDEX IF NOT EXISTS idx_eq_used ON external_questions(used, fetched_at)")
        db_exec("CREATE INDEX IF NOT EXISTS idx_eq_source ON external_questions(source)")
        db_exec("CREATE INDEX IF NOT EXISTS idx_eq_domain ON external_questions(domain)")
    except Exception as e:
        logger.warning(f"init_external_questions_db error: {e}")


# ============================================================
# HTTP Helper
# ============================================================
#  Reduced timeout from 15s → 5s. With parallel fetching, slow sources
# don't block others — 5s is enough for fast sources, slow ones get skipped.
_DEFAULT_TIMEOUT = 3  # [V88 BOOST] 3s (was 5s) — fail fast, don't block cycle


def _http_get_json(url: str, timeout: int = _DEFAULT_TIMEOUT, headers: Optional[dict] = None) -> Optional[dict]:
    """GET request, return parsed JSON. Returns None on error or timeout.

    [SEC-A] Redirect policy: BOTH branches re-run the full SCP gate on every
    hop. The requests branch previously used allow_redirects=True (requests'
    default), which follows up to 30 cross-host 3xx hops with NO further
    enforce/validate — the same 302→169.254.169.254 SSRF + allowlist bypass
    class as the urllib default opener. It now sends allow_redirects=False
    and walks the chain explicitly (≤ SAFE_MAX_REDIRECT_HOPS, one canonical
    standard from scp.security.url_safety, credentials scrubbed on host
    change). Policy violations raise ValueError → the documented "returns
    None on error" fail-closed contract is unchanged.
    """
    try:
        # [AUDIT-20260909 SSRF-S1] validate_url trước MỌI fetch — chặn scheme
        # lạ + private/loopback IP cho cả nhánh requests.Session lẫn urllib.
        # Input xấu → ValueError → nhánh except → trả None (fail-closed).
        # [V-EE-1] enforce_egress_policy chạy TRƯỚC validate_url (cùng thứ tự
        # với safe_urlopen) — SCP_EGRESS_MODE=deny/allowlist chặn TRƯỚC mọi
        # DNS I/O, phủ CẢ nhánh requests.Session lẫn nhánh urllib fallback.
        # EgressDeniedError là ValueError subclass → rơi vào cùng nhánh except
        # dưới → trả None (contract "Returns None on error" giữ nguyên).
        # Nhánh urllib còn được gate lần 2 bên trong safe_urlopen (idempotent).
        # [SEC-A] MỌI hop >0 cũng được enforce+validate qua
        # validate_redirect_target trước khi GET tới Location mới.
        enforce_egress_policy(url)
        validate_url(url)
        if HAS_REQUESTS and _SESSION is not None:
            #  Use persistent session — connection pooling reduces overhead ~30%
            # [SEC-A] allow_redirects=False + hop loop: enforce/validate mỗi
            # Location; hop >0 internal-target luôn raise (allow_internal
            # không tồn tại ở fetcher này — validate_redirect_target mặc định
            # False), nên không cần origin-host exception.
            current_url = url
            hop_headers = dict(headers or {})
            # Session-level Authorization/Cookie/auth may be configured by a
            # caller or ambient integration.  Do not let those credentials
            # ride to an arbitrary dynamic hop-0 URL; only explicit
            # per-request headers below are retained for the intended URL.
            session = session_without_credentials(_SESSION)
            previous_url = current_url
            for _hop in range(SAFE_MAX_REDIRECT_HOPS + 1):
                # [V-EE-1]+[SEC-A] gate per-hop TRƯỚC driver (idempotent với
                # hop 0 đã check ở đầu function): enforce+SSRF+scheme cho
                # chính URL mà phiên này sẽ kết nối tới.
                if _hop:
                    enforce_egress_policy(current_url)
                    # Revalidate every URL immediately before the driver.  The
                    # redirect target was checked before assigning current_url,
                    # but this second check also covers mutated/dynamic state.
                    validate_url(current_url)
                    if not same_egress_host(previous_url, current_url):
                        session = session_without_credentials(session)
                    hop_headers = strip_credentials_on_host_change(
                        hop_headers, previous_url, current_url
                    )
                previous_url = current_url
                r = session.get(current_url, timeout=timeout,
                                headers=hop_headers, allow_redirects=False,
                                proxies={})
                if r.status_code not in _REDIRECT_STATUS_CODES:
                    break
                location = r.headers.get("Location")
                target = urllib.parse.urljoin(current_url, location) if location else ""
                # Raises ValueError/EgressDeniedError on a bad hop target.
                validate_redirect_target(target)
                hop_headers = strip_credentials_on_host_change(
                    hop_headers, current_url, target
                )
                previous_url = current_url
                current_url = target
            else:
                raise ValueError(
                    f"too many redirects (maximum {SAFE_MAX_REDIRECT_HOPS})"
                )
            if r.status_code == 200:
                try:
                    return r.json()
                except Exception as e:  # [RC-7 FIX Task 6-B] silent swallow → log context
                    logger.warning(f"[real_question_fetcher.fetch_json] JSON parse failed: {e}")
                    return None
            logger.debug(f"HTTP {r.status_code} for {url[:80]}")
            return None
        else:
            # Fallback to urllib — [SEC-A] safe_urlopen giờ tự re-validate
            # từng hop redirect (custom opener), không còn 302-blind-follow.
            hdrs = {
                'User-Agent': 'SCP-V70-Bot/1.0 (educational research)',
                'Accept': 'application/json',
            }
            if headers:
                hdrs.update(headers)
            req = urllib.request.Request(url, headers=hdrs)  # noqa: S310
            with safe_urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode('utf-8'))
    except Exception as e:
        logger.debug(f"HTTP GET error {url[:80]}: {e}")
        return None


# ============================================================
# Domain Helpers
# ============================================================
def _map_opentdb_category(category: str) -> str:
    if not category:
        return "unknown"
    c = category.lower()
    rules = [
        (("science", "math", "computers", "gadgets"), "math"),
        (("history",), "history"),
        (("geography",), "geography"),
        (("politics",), "history"),
        (("animals", "nature"), "biology"),
        (("film", "music", "television", "cartoon", "entertainment", "actor", "actress", "singer", "band",
          "anime", "manga"), "entertainment"),  # [V93.8] added anime/manga
        (("sports",), "sports"),
        (("mythology", "religion", "god", "deity", "prayer"), "religion"),
        (("celebrities",), "history"),
        (("art", "books", "comics", "theatre"), "arts"),
        (("vehicles",), "technology"),
        (("board", "video", "games"), "technology"),
        (("general knowledge", "general"), "general"),  # [V93.8] was falling to 'unknown'
    ]
    for keywords, domain in rules:
        if any(k in c for k in keywords):
            return domain
    return "unknown"


def _guess_domain(text: str) -> str:
    t = (text or "").lower()
    rules = [
        (("nước", "thành phố", "đất nước", "quốc gia", "capital", "country", "city"), "geography"),
        (("năm", "trận", "chiến tranh", "sự kiện", "history", "war", "battle", "bc", "ad"), "history"),
        (("nguyên tố", "hóa học", "compound", "molecule", "chemical"), "chemistry"),
        (("sao", "hành tinh", "thiên hà", "planet", "star", "galaxy", "nasa"), "astronomy"),
        (("phương trình", "định lý", "math", "equation", "number"), "math"),
        (("protein", "dna", "cell", "enzyme", "sinh học", "animal", "plant"), "biology"),
        (("thần", "tôn giáo", "bible", "quran"), "history"),
        (("phim", "diễn viên", "actor", "film", "movie", "music", "ca sĩ", "nhạc"), "entertainment"),
        (("thức ăn", "món ăn", "recipe", "food", "cocktail", "drink"), "food"),
        (("công nghệ", "programming", "software", "tech"), "technology"),
        (("thời tiết", "weather", "temperature"), "weather"),
        (("bitcoin", "crypto", "currency", "exchange"), "finance"),
    ]
    for keywords, domain in rules:
        if any(k in t for k in keywords):
            return domain
    return "unknown"


def _clean(s: str, maxlen: int = 500) -> str:
    """Clean + truncate string."""
    if not s:
        return ""
    s = html.unescape(str(s)).strip()
    s = re.sub(r'\s+', ' ', s)
    return s[:maxlen]


# ============================================================
# Fetchers — Each returns List[Dict{question, ai_answer, source, source_url, domain, category}]
# ============================================================
def _extract_specific_fact(title: str, extract: str, lang: str) -> Optional[tuple]:
    """
     Extract 1 specific fact (question, real_value) from a Wikipedia extract.

    Returns: (question, real_value) or None if no extractable fact.
    Strategy:
      1. Find first sentence containing "is a/an/the" or "là" → extract type
      2. Find years (born/died/founded) → extract date fact
      3. Find numbers (population/area) → extract numeric fact
      4. Fallback: None (skip this article — no good question)
    """
    if not extract or len(extract) < 30:
        return None

    text = extract.strip()
    # First sentence
    first_period = text.find('.')
    first_sent = text[:first_period + 1] if first_period > 0 else text
    first_sent.lower()

    # Strategy 1: "X is a Y" → "What type of thing is X?" → Y
    is_patterns_en = [
        re.compile(r'^([^.]+?)\s+is\s+(?:a|an|the)\s+([^.]+?)(?:[,.]|\s+(?:that|which|who))', re.IGNORECASE),
        re.compile(r'^([^.]+?)\s+was\s+(?:a|an|the)\s+([^.]+?)(?:[,.]|\s+(?:that|which|who))', re.IGNORECASE),
    ]
    is_patterns_vi = [
        re.compile(r'^([^.]+?)\s+là\s+(?:một\s+)?([^,.]+?)(?:[,.]|\s+(?:được|tại|trong))', re.IGNORECASE),
    ]
    if lang == "vi":
        for pat in is_patterns_vi:
            m = pat.match(first_sent)
            if m:
                # Use the WHOLE type phrase (not just first word)
                type_phrase = m.group(2).strip().split(',')[0].strip()
                # Take first 5 words max
                words = type_phrase.split()[:5]
                type_clean = ' '.join(words)
                if 2 <= len(type_clean) <= 80:
                    return (f"{title} là loại gì?", type_clean)
    else:
        for pat in is_patterns_en:
            m = pat.match(first_sent)
            if m:
                type_phrase = m.group(2).strip().split(',')[0].strip()
                words = type_phrase.split()[:5]
                type_clean = ' '.join(words)
                if 2 <= len(type_clean) <= 80:
                    return (f"What type of thing is {title}?", type_clean)

    # Strategy 2: Years — "founded in 1945", "born in 1879"
    year_pat = re.compile(r'\b(?:born|founded|established|created|built|opened)\s+(?:in\s+)?(\d{3,4})\b', re.IGNORECASE)
    m = year_pat.search(text)
    if m:
        year = m.group(1)
        verb_match = re.search(r'\b(born|founded|established|created|built|opened)\b', text, re.IGNORECASE)
        verb = verb_match.group(1).lower() if verb_match else "established"
        if lang == "vi":
            return (f"{title} được {verb} vào năm nào?", year)
        else:
            return (f"When was {title} {verb}?", year)

    # Strategy 3: Population/area numbers
    pop_pat = re.compile(r'(?:population|pop\.)\s*(?:of\s+)?(?:[~≈]?\s*(\d[\d,.]+\s*(?:million|billion|thousand)?))', re.IGNORECASE)
    m = pop_pat.search(text)
    if m:
        pop = m.group(1).strip()
        if lang == "vi":
            return (f"Dân số của {title} là bao nhiêu?", pop)
        else:
            return (f"What is the population of {title}?", pop)

    area_pat = re.compile(r'(?:area|size)\s*(?:of\s+)?(?:[~≈]?\s*(\d[\d,.]+\s*(?:km²|sq\s*mi|square\s*(?:kilometers|miles))?))', re.IGNORECASE)
    m = area_pat.search(text)
    if m:
        area = m.group(1).strip()
        if lang == "vi":
            return (f"Diện tích của {title} là bao nhiêu?", area)
        else:
            return (f"What is the area of {title}?", area)

    # No extractable fact
    return None

