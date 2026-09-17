# SCP CIRCUIT: M09 — STATUS: CLOSED_WITH_KNOWN_GAP (closure: reports/circuit-closures/M09-closure.json)
"""
SCP V104 — Attack Crawler (Updated Sources)
=============================================
V104 UPDATE: Thay thế repo cũ bằng nguồn thực tế mới (2026).
Thêm HuggingFace datasets + Reddit (r/LocalLLaMA).

Sources (verified active 2026):
  GitHub: 7 repos (payload + dataset + framework)
  HuggingFace: 4 datasets (jailbreak corpus)
  Reddit: r/LocalLLaMA + r/ArtificialIntelligence
  Apify: fallback corpus (7 sources aggregated)
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

# [AUDIT-20260909 S6a] Crawl fetch đi qua safe_urlopen — validate scheme +
# chặn private/loopback IP trước khi gọi.
from scp.security.url_safety import safe_urlopen

logger = logging.getLogger("scp.security.attack_crawler")

CRAWL_INTERVAL = int(os.environ.get("SCP_ATTACK_CRAWL_INTERVAL", "3600"))  # [ROOT-FIX] was 600s → 403 rate limit. 14 repos × 2 calls × 6 cycles/hour = 168 > 60 limit. Now 3600s = 28 calls/hour < 60.

# V104: Updated sources (verified active 2026)
GITHUB_REPOS = [
    # Original repos
    "nukIeer/AI-Prompt-Injection-Cheatsheet",
    "tuxsharxsec/Jailbreaks",
    "0x5477/deepseek-v4-pro-unrestricted",
    "kerberosmansour/AGT-Embeddings-Experiment",
    "perplext/LLMrecon",
    "Mr-Infect/AI-penetration-testing",
    "Juadsuarezsan/ai-safety-redteam",
    # V104.47: 2025 repos
    "llm-attacks/llm-attacks",              # GCG attacks
    "patrickrchao/JailbreakingLLMs",         # 2024-2025 jailbreaks
    "danielmiessler/fabric",                 # LLM security patterns (replaces dead wunderwuzzi23/llm-security)
    "NVIDIA/garak",                          # Garak probe definitions
    "GraySwanAI/nanoGCG",                    # GCG implementation
    "google/safetext",                       # SafeText LLM safety (replaces dead AILab-CVC/UniversalXProtect)
    "facebookresearch/llama-recipes",        # Llama safety configs
]

HUGGINGFACE_DATASETS = [
    "youbin2014/JailbreakDB",
    "Lakera/mosscap_prompt_injection",
]

# [A1-egress-choke AUDIT-20260909] HF fetch uses the official datasets-server
# public HTTP API exclusively, called through safe_urlopen. It must NOT go
# through the `datasets` / huggingface_hub stack — that library owns its own
# HTTP session and would sidestep the single SCP egress choke point (an
# earlier version did exactly that: datasets.load_dataset fetched payloads
# even under SCP_EGRESS_MODE=deny). The host is deliberately NOT added to any
# default allowlist; operators opt it in via SCP_EGRESS_ALLOWLIST.
HF_DATASETS_SERVER_URL = "https://datasets-server.huggingface.co"
HF_SCAN_ITEM_LIMIT = 200   # items (rows with usable text) per dataset, as before
HF_ROWS_PAGE_SIZE = 100    # datasets-server /rows page size (API maximum)
HF_SPLIT_PRIORITY = ("train", "jailbreak", "regular", "test", "validation")

REDDIT_SUBREDDITS = [
    "LocalLLaMA",
    "ArtificialIntelligence",
]
REDDIT_KEYWORDS = ["jailbreak", "prompt injection", "bypass", "unrestricted"]

# [W2-observability] Human-readable labels for the three crawl sources, used by
# the aggregate log. Keep keys stable — they are the tally buckets in crawl_all.
CRAWL_SOURCES = ("github", "huggingface", "reddit")
_SOURCE_LABELS = {"github": "GitHub", "huggingface": "HuggingFace", "reddit": "Reddit"}


@dataclass
class CrawledAttack:
    source: str
    source_url: str
    attack_text: str
    category: str
    discovered_at: float = field(default_factory=time.time)
    tested: bool = False
    bypass: bool = False


class AttackCrawler:
    def __init__(self, data_dir: str = "data"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.attacks_file = self.data_dir / "crawled_attacks.jsonl"
        self._seen_hashes: set[int] = set()
        self._load_seen()
        self._stats = {
            "crawl_cycles": 0,
            "new_attacks_found": 0,
            "by_source": {},
        }
        # [W2-observability] source -> set of exception CLASS NAMES seen while
        # fetching (e.g. {"EgressDeniedError"}). Only class names are stored so
        # the aggregate log can never leak a URL/token from str(e). Reset each
        # crawl_all() call. A source is "failed" when it returned 0 raw attacks
        # while recording >=1 error — this is what distinguishes "genuinely no
        # new attacks" from "the crawler is blind (egress denied)".
        self._crawl_errors: dict[str, set[str]] = {name: set() for name in CRAWL_SOURCES}

    def _record_crawl_error(self, source: str, exc: BaseException) -> None:
        """Tally a fetch failure for honest aggregate logging.

        Secret-safe: records only the exception CLASS NAME, never str(exc)
        (EgressDeniedError and urllib HTTPError embed the full URL in their
        message). This does NOT change crawl/egress behavior — callers keep
        their existing control flow and per-source warnings.
        """
        self._crawl_errors.setdefault(source, set()).add(type(exc).__name__)

    def _load_seen(self) -> None:
        if self.attacks_file.exists():
            with open(self.attacks_file, encoding="utf-8") as f:
                for line in f:
                    try:
                        entry = json.loads(line.strip())
                        self._seen_hashes.add(hashlib.sha256(entry.get("attack_text", "").encode()).hexdigest()[:16])  # [V104.32 #26a]
                    except Exception:  # noqa: S112
                        logger.warning('AttackCrawler._load_seen: Exception not handled', exc_info=True)
                        continue

    async def crawl_all(self) -> list[CrawledAttack]:
        new_attacks = []
        self._stats["crawl_cycles"] += 1
        # Reset the honest-observability tally for this cycle.
        self._crawl_errors = {name: set() for name in CRAWL_SOURCES}
        raw_counts = {name: 0 for name in CRAWL_SOURCES}

        try:
            gh_attacks = self._crawl_github()
        except Exception as e:  # source raised before returning anything
            self._record_crawl_error("github", e)
            logger.warning("AttackCrawler: GitHub crawl failed: %s", type(e).__name__)
            gh_attacks = []
        raw_counts["github"] = len(gh_attacks)
        new_attacks.extend(gh_attacks)

        try:
            hf_attacks = self._crawl_huggingface()
        except Exception as e:
            self._record_crawl_error("huggingface", e)
            logger.warning("AttackCrawler: HuggingFace crawl failed: %s", type(e).__name__)
            hf_attacks = []
        raw_counts["huggingface"] = len(hf_attacks)
        new_attacks.extend(hf_attacks)

        try:
            reddit_attacks = self._crawl_reddit()
        except Exception as e:
            self._record_crawl_error("reddit", e)
            logger.warning("AttackCrawler: Reddit crawl failed: %s", type(e).__name__)
            reddit_attacks = []
        raw_counts["reddit"] = len(reddit_attacks)
        new_attacks.extend(reddit_attacks)

        unique = []
        for attack in new_attacks:
            h = hashlib.sha256(attack.attack_text.encode()).hexdigest()[:16]  # [V104.38 #95] TẠI SAO: was hash() (int) vs _load_seen string → no dedup
            if h not in self._seen_hashes:
                self._seen_hashes.add(h)
                unique.append(attack)
                self._stats["new_attacks_found"] += 1
                self._stats["by_source"][attack.source] = (
                    self._stats["by_source"].get(attack.source, 0) + 1
                )

        # [W2-observability] Honest aggregate log. A source counts as FAILED when
        # it returned no raw attacks yet recorded >=1 fetch error (e.g.
        # EgressDeniedError swallowed deeper in _crawl_*). This separates "0 new
        # because genuinely nothing new / all cached" (INFO) from "0 new because
        # the sources could not be reached" (WARNING/ERROR) — so an operator can
        # never mistake a blind crawler for an all-clear.
        failed_sources = [
            name for name in CRAWL_SOURCES
            if raw_counts[name] == 0 and self._crawl_errors[name]
        ]
        degraded_note = ""
        if failed_sources:
            detail = "; ".join(
                f"{_SOURCE_LABELS[name]}={'/'.join(sorted(self._crawl_errors[name]))}"
                for name in failed_sources
            )
            degraded_note = f" | DEGRADED: {len(failed_sources)}/{len(CRAWL_SOURCES)} sources errored ({detail})"

        if unique:
            self._save_attacks(unique)
            breakdown = ", ".join(
                f"{_SOURCE_LABELS[name]}={raw_counts[name]}" for name in CRAWL_SOURCES
            )
            logger.info(
                "AttackCrawler: found %d new attacks (raw %s)%s",
                len(unique), breakdown, degraded_note,
            )
        elif failed_sources:
            # 0 results AND at least one source failed → do NOT claim "no new
            # attacks". Fully-blind (all sources failed) escalates to ERROR.
            n_failed, n_total = len(failed_sources), len(CRAWL_SOURCES)
            message = (
                "AttackCrawler: 0 new attacks but %d/%d crawl sources FAILED "
                "(%s). 'no new attacks' is NOT trustworthy — the crawler may be "
                "blind (e.g. egress denied). Only source names + error class "
                "names are shown; no URLs/secrets logged."
            ) % (n_failed, n_total, detail)
            if n_failed == n_total:
                logger.error(message)
            else:
                logger.warning(message)
        else:
            logger.info("AttackCrawler: no new attacks found")

        return unique

    def _crawl_github(self) -> list[CrawledAttack]:
        attacks = []
        # [SEC-B AUDIT-20260909] GitHub credentials ONLY. The previous lookup
        # os.environ.get("GITHUB_TOKEN", os.environ.get("HF_TOKEN", "")) sent
        # the HuggingFace token to api.github.com whenever GITHUB_TOKEN was
        # unset but HF_TOKEN was set — a cross-service credential leak (the HF
        # secret left the HF trust boundary in an `Authorization: token ...`
        # header bound for GitHub). Fail-closed in the useful sense: without
        # GITHUB_TOKEN this source degrades to the documented unauthenticated
        # path (60 req/hour warning below); it NEVER presents HF_TOKEN to
        # GitHub and never raises. .strip() mirrors _hf_api_get so a
        # whitespace-only value degrades to unauthenticated instead of
        # emitting a malformed credential header.
        gh_token = os.environ.get("GITHUB_TOKEN", "").strip()
        headers = {"User-Agent": "SCP-V104/1.0", "Accept": "application/vnd.github.v3+json"}
        if gh_token:
            headers["Authorization"] = f"token {gh_token}"
        else:
            logger.warning("[AttackCrawler] No GITHUB_TOKEN set — using unauthenticated (60 req/hour limit). Set GITHUB_TOKEN in .env for 5000 req/hour.")

        # [ROOT-FIX] 24h cache — skip repos crawled in last 24h to avoid 403
        cache_file = self.data_dir / "github_crawl_cache.json"
        cache = {}
        if cache_file.exists():
            try:
                cache = json.loads(cache_file.read_text())
            except Exception:
                logger.warning('AttackCrawler._crawl_github: Exception not handled', exc_info=True)
                cache = {}
        now = time.time()
        cache_ttl = 86400  # 24 hours

        for repo in GITHUB_REPOS:
            # Check cache — skip if crawled in last 24h
            cache_key = repo
            if cache_key in cache and now - cache[cache_key] < cache_ttl:
                logger.debug(f"GitHub {repo} — cached (skipped, <24h since last crawl)")
                continue

            try:
                url = f"https://api.github.com/repos/{repo}/readme"
                req = urllib.request.Request(url, headers=headers)
                with safe_urlopen(req, timeout=15) as resp:  # URL validated by safe_urlopen
                    data = json.loads(resp.read())
                    readme = data.get("content", "")
                    if readme:
                        readme_text = base64.b64decode(readme).decode("utf-8", errors="replace")
                        extracted = self._extract_attacks_from_text(readme_text, f"github:{repo}")
                        attacks.extend(extracted)
                # Update cache
                cache[cache_key] = now
            except Exception as e:
                self._record_crawl_error("github", e)
                # [ROOT-FIX] Detect 403 rate limit — stop crawling remaining repos
                if "403" in str(e) or "rate limit" in str(e).lower():
                    logger.warning(f"GitHub {repo} failed: {e} — STOPPING crawl (rate limit hit, will retry next cycle)")
                    break
                logger.warning(f"GitHub {repo} failed: {e}")

            try:
                url = f"https://api.github.com/repos/{repo}/issues?per_page=10&state=open"
                req = urllib.request.Request(url, headers=headers)  # noqa: S310
                with safe_urlopen(req, timeout=15) as resp:  # URL validated by safe_urlopen
                    issues = json.loads(resp.read())
                    for issue in issues[:10]:
                        title = issue.get("title", "")
                        body = issue.get("body", "") or ""
                        text = title + "\n" + body
                        extracted = self._extract_attacks_from_text(
                            text, f"github:{repo}/issues/{issue.get('number', '?')}"
                        )
                        attacks.extend(extracted)
            except Exception as e:
                self._record_crawl_error("github", e)
                if "403" in str(e) or "rate limit" in str(e).lower():
                    logger.warning(f"GitHub {repo} issues failed: {e} — STOPPING (rate limit)")
                    break
                logger.warning(f"GitHub {repo} issues failed: {e}")

        # Save cache
        try:
            cache_file.write_text(json.dumps(cache))
        except Exception as e:
            logger.debug(f"Cache save failed: {e}")

        return attacks

    def _hf_api_get(self, path: str, params: dict[str, str]) -> dict:
        """One GET against the official HuggingFace datasets-server API.

        [A1-egress-choke] Every HF byte goes through safe_urlopen — i.e.
        enforce_egress_policy (SCP_EGRESS_MODE fail-closed) + validate_url
        (scheme http/https, host validation, private/loopback/reserved IPs
        rejected). The optional bearer token is only ever placed in the
        request header — never in a URL, never in a log line.
        """
        url = f"{HF_DATASETS_SERVER_URL}{path}?{urllib.parse.urlencode(params)}"
        headers = {"User-Agent": "SCP-V104/1.0", "Accept": "application/json"}
        hf_token = os.environ.get("HF_TOKEN", "").strip()
        if hf_token:
            headers["Authorization"] = f"Bearer {hf_token}"
        req = urllib.request.Request(url, headers=headers)
        with safe_urlopen(req, timeout=15) as resp:  # URL validated by safe_urlopen
            return json.loads(resp.read())

    def _hf_resolve_split(self, ds_name: str) -> tuple[str, str]:
        """Resolve (config, split) from datasets-server /splits.

        Preserves the pre-rewrite split preference (train > jailbreak >
        regular > test > validation, falling back to any first available
        split, mirroring the old "load without specifying split" attempt).
        """
        info = self._hf_api_get("/splits", {"dataset": ds_name})
        by_split: dict[str, tuple[str, str]] = {}
        for entry in info.get("splits") or []:
            name, config = entry.get("split"), entry.get("config")
            if name and config and name not in by_split:
                by_split[name] = (config, name)
        for preferred in HF_SPLIT_PRIORITY:
            if preferred in by_split:
                return by_split[preferred]
        if by_split:
            return next(iter(by_split.values()))
        raise ValueError(f"datasets-server reports no splits for {ds_name!r}")

    def _hf_fetch_rows(self, ds_name: str) -> tuple[str, list[dict]]:
        """Fetch up to HF_SCAN_ITEM_LIMIT rows via /rows (100 rows/page)."""
        config, used_split = self._hf_resolve_split(ds_name)
        rows: list[dict] = []
        offset = 0
        while len(rows) < HF_SCAN_ITEM_LIMIT:
            data = self._hf_api_get("/rows", {
                "dataset": ds_name,
                "config": config,
                "split": used_split,
                "offset": str(offset),
                "length": str(HF_ROWS_PAGE_SIZE),
            })
            page = [entry.get("row") or {} for entry in (data.get("rows") or [])]
            if not page:
                break
            rows.extend(page[: HF_SCAN_ITEM_LIMIT - len(rows)])
            if len(page) < HF_ROWS_PAGE_SIZE:
                break
            offset += len(page)
        return used_split, rows

    def _crawl_huggingface(self) -> list[CrawledAttack]:
        """Crawl HuggingFace datasets for jailbreak payloads.

        [A1-egress-choke AUDIT-20260909] This source previously used
        datasets.load_dataset — huggingface_hub's private HTTP stack that
        bypassed the SCP egress choke point, so SCP_EGRESS_MODE=deny did not
        stop HF network fetches and W2 could never tally the source as failed
        (no error ever reached the choke). The rewrite talks to the canonical
        datasets-server HTTP API through safe_urlopen only:
          - deny      -> EgressDeniedError per dataset, source tallied FAILED
                         (W2 WARNING/ERROR aggregate, never a silent zero).
          - allowlist -> allowed only when the operator puts
                         datasets-server.huggingface.co in SCP_EGRESS_ALLOWLIST.
        """
        attacks = []
        for ds_name in HUGGINGFACE_DATASETS:
            try:
                used_split, rows = self._hf_fetch_rows(ds_name)
            except Exception as e:
                # W2 discipline: tally the CLASS NAME only — str(e) of an
                # EgressDeniedError/HTTPError embeds the full request URL.
                self._record_crawl_error("huggingface", e)
                logger.warning("HuggingFace %s failed: %s", ds_name, type(e).__name__)
                continue
            count = 0
            for item in rows:
                text = ""
                for key in ["prompt", "text", "question", "attack", "payload", "instruction"]:
                    if key in item:
                        text = str(item[key])
                        break
                if text and len(text) > 15:
                    extracted = self._extract_attacks_from_text(
                        text, f"huggingface:{ds_name}"
                    )
                    attacks.extend(extracted)
                    count += 1
                if count >= HF_SCAN_ITEM_LIMIT:
                    break
            logger.info(
                "HuggingFace %s (split=%s): scanned %d items",
                ds_name, used_split, count,
            )
        return attacks

    def _crawl_reddit(self) -> list[CrawledAttack]:
        """Crawl Reddit for attack discussions.

        [FIX] Reddit API thường block/timerout. Thêm:
        1. Retry 3 lần với backoff
        2. Timeout ngắn (5s thay vì 15s)
        3. Skip subreddit nếu connection refused (không spam log)
        4. User-Agent hợp lệ (Reddit yêu cầu)
        """
        attacks = []
        headers = {
            "User-Agent": "SCP-V104/1.0 (security research; scp-vietnam@example.com)"
        }

        for subreddit in REDDIT_SUBREDDITS:
            for attempt in range(3):  # [FIX] retry 3 lần
                try:
                    for keyword in REDDIT_KEYWORDS:
                        url = f"https://www.reddit.com/r/{subreddit}/search.json?q={urllib.parse.quote(keyword)}&sort=new&limit=25&restrict_sr=1"
                        req = urllib.request.Request(url, headers=headers)
                        with safe_urlopen(req, timeout=5) as resp:  # [FIX] 5s timeout  # URL validated by safe_urlopen
                            data = json.loads(resp.read())
                            posts = data.get("data", {}).get("children", [])
                            for post in posts:
                                p = post.get("data", {})
                                title = p.get("title", "")
                                selftext = p.get("selftext", "")
                                permalink = p.get("permalink", "")
                                text = title + "\n" + selftext
                                if len(text) > 20:
                                    extracted = self._extract_attacks_from_text(
                                        text, f"reddit:r/{subreddit}{permalink}"
                                    )
                                    attacks.extend(extracted)
                    break  # Thành công → thoát retry loop
                except urllib.error.URLError as e:
                    self._record_crawl_error("reddit", e)
                    # [FIX] Connection refused = skip subreddit (không spam)
                    if "10061" in str(e) or "Connection refused" in str(e):
                        logger.debug(f"Reddit r/{subreddit}: connection refused (firewall/VPN?) — skipping")
                        break  # Không retry nếu connection refused
                    if attempt < 2:
                        import time as _time
                        _time.sleep(2 ** attempt)  # backoff: 1s, 2s
                    else:
                        logger.debug(f"Reddit r/{subreddit} failed after 3 retries: {e}")
                except Exception as e:
                    self._record_crawl_error("reddit", e)
                    if attempt < 2:
                        import time as _time
                        _time.sleep(2 ** attempt)
                    else:
                        logger.debug(f"Reddit r/{subreddit} failed: {e}")

        return attacks

    def _extract_attacks_from_text(self, text: str, source_url: str) -> list[CrawledAttack]:
        """V104: Require ≥2 keyword matches + skip false positive patterns."""
        attacks = []

        attack_keywords_primary = [
            "ignore all", "ignore previous", "ignore prior",
            "you are dan", "you are free", "you are now free",
            "reveal your prompt", "show your prompt", "print your instructions",
            "reveal your system", "print your system", "reveal your base",
            "no rules", "no restrictions", "unrestricted mode",
            "developer mode", "jailbreak",
            "without any restrictions", "without restrictions",
            "no ethical guidelines", "no ethical",
            "disregard everything", "disregard all",
            "new identity", "new role",
            "output the hidden", "output your",
            "ignore all prior", "forget your training",
            "operate without", "function without",
        ]

        attack_keywords_secondary = [
            "instructions", "prompt", "config", "system message", "configuration",
            "override", "bypass", "forget", "disregard",
            "pretend", "act as an ai", "roleplay",
            "dan", "freegpt", "stan", "librebot",
            "ignore all previous", "forget your training",
            "reveal", "expose", "output your",
            "no longer", "from now on", "from this moment",
            "base instructions", "hidden", "prior training",
            "without any", "ethical guidelines",
            "new identity", "your task is",
        ]

        skip_patterns = [
            "middleware", "security policy", "mock session",
            "chrome devtools", ".env config", "default config",
            "this guide explains", "this middleware",
            "the actor can", "lets you act as a",
            "has no restrictions on", "set the system_prompt",
        ]

        lines = text.split("\n")
        for line in lines:
            line = line.strip()
            if len(line) < 15 or len(line) > 2000:
                continue
            line_lower = line.lower()
            if any(skip in line_lower for skip in skip_patterns):
                continue
            primary = sum(1 for kw in attack_keywords_primary if kw in line_lower)
            secondary = sum(1 for kw in attack_keywords_secondary if kw in line_lower)
            if primary >= 1 and (primary + secondary) >= 2:
                clean = re.sub(r"[*#>`]", "", line).strip()
                if len(clean) > 15:
                    category = self._categorize(clean)
                    attacks.append(CrawledAttack(
                        source="github" if "github" in source_url else ("huggingface" if "huggingface" in source_url else "reddit"),
                        source_url=source_url,
                        attack_text=clean,
                        category=category,
                    ))
        return attacks

    def _categorize(self, text: str) -> str:
        t = text.lower()
        if "ignore" in t or "disregard" in t or "forget" in t:
            return "injection"
        if "dan" in t or "no rules" in t or "no restrictions" in t:
            return "jailbreak"
        if "reveal" in t or "show your" in t or "print your" in t:
            return "exfiltration"
        if "act as" in t or "pretend" in t or "roleplay" in t:
            return "role_play"
        if "base64" in t or "unicode" in t or "encode" in t:
            return "encoding"
        return "other"

    def _save_attacks(self, attacks: list[CrawledAttack]) -> None:
        with open(self.attacks_file, "a", encoding="utf-8") as f:
            for a in attacks:
                entry = {
                    "source": a.source, "source_url": a.source_url,
                    "attack_text": a.attack_text[:500], "category": a.category,
                    "discovered_at": a.discovered_at, "tested": False, "bypass": False,
                }
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def get_new_attacks(self) -> list[str]:
        attacks = []
        if self.attacks_file.exists():
            with open(self.attacks_file, encoding="utf-8") as f:
                for line in f:
                    try:
                        entry = json.loads(line.strip())
                        if not entry.get("tested"):
                            attacks.append(entry.get("attack_text", ""))
                    except Exception:  # noqa: S112
                        logger.warning('AttackCrawler.get_new_attacks: Exception not handled', exc_info=True)
                        continue
        return attacks


    async def _crawl_source(self):
        return []

    def classify_threats(self, raw_threats):
        classified = []
        for t in raw_threats:
            t["severity"] = "high"
            t["threat_type"] = "injection"
            classified.append(t)
        return classified

    def deduplicate(self, threats):
        seen = set()
        deduped = []
        for t in threats:
            import json
            key = json.dumps(t, sort_keys=True)
            if key not in seen:
                seen.add(key)
                deduped.append(t)
        return deduped

    def persist_to_store(self, threats):
        pass

    def stats(self) -> dict:
        return self._stats.copy()


def start_crawl_thread(data_dir: str = "data") -> threading.Thread:
    import threading
    def crawl_loop():
        crawler = AttackCrawler(data_dir=data_dir)
        logger.info(f"AttackCrawler started (interval={CRAWL_INTERVAL}s)")
        # [SCP-DNA-FIX R12-27] Defer first crawl — sleep 120s trước khi crawl.
        # Tại sao: crawl_all() gọi GitHub API (network, 15s timeout × N repos).
        # Nếu chạy ngay trong startup → tiêu thụ CPU/network → slow get_judge()
        # → server không bind port 8000 kịp → Loop Scheduler báo offline.
        # Fix: defer 120s — server bind port first, crawl later.
        time.sleep(120)
        while True:
            try:
                new_attacks = __import__('asyncio').run(crawler.crawl_all())
                if new_attacks:
                    logger.info(f"AttackCrawler: {len(new_attacks)} new attacks added to ThreatSimulator pool")
            except Exception as e:
                logger.error(f"AttackCrawler error: {e}")
            time.sleep(CRAWL_INTERVAL)
    thread = threading.Thread(target=crawl_loop, daemon=True, name="scp-attack-crawler")
    thread.start()
    return thread


if __name__ == "__main__":
    print("=== Attack Crawler V104 — Test ===\n")
    import tempfile
    fd, db_path = tempfile.mkstemp(suffix=".jsonl")
    os.close(fd)
    os.remove(db_path)
    crawler = AttackCrawler(data_dir=os.path.dirname(db_path))
    attacks = __import__('asyncio').run(crawler.crawl_all())
    print(f"New attacks found: {len(attacks)}")
    for a in attacks[:10]:
        print(f"  [{a.source}] ({a.category}) {a.attack_text[:80]}")
    print(f"\nStats: {crawler.stats()}")
    print(f"\nAttacks for ThreatSimulator: {len(crawler.get_new_attacks())}")
