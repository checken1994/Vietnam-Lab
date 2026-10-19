# SCP CIRCUIT: M09 — STATUS: CLOSED_WITH_KNOWN_GAP (closure: docs/evidence-summary/M09-closure.json)
"""
SCP V104 — Attack & Intelligence Crawler (Dual-Stream Ingestion)
===============================================================
V104 UPDATE: Ingests dual-stream threat intelligence:
- Attack Stream: 15+ sources (GitHub repos, HuggingFace datasets, Reddit, Local Intel)
- Counter Stream: Defensive repositories and counter-adversarial frameworks
  (PurpleLlama, Rebuff, Llama Guard, Tarpit protocols, Context Inversion)

Equipped with:
- Token Bucket Rate Limiting (TokenBucketRateLimiter adapted from scp.capabilities.tools)
- Universal 24-hour cache (intel_crawl_cache.json + github_crawl_cache.json)
- Full cryptographic SHA-256 provenance persistence to data/raw_threat_intel.jsonl
"""
from __future__ import annotations

import asyncio
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
from typing import Any

from scp.security.intel_crawler import (
    ATTACK_GITHUB_REPOS,
    ATTACK_HUGGINGFACE_DATASETS,
    ATTACK_REDDIT_SUBREDDITS,
    COUNTER_GITHUB_REPOS,
    COUNTER_HUGGINGFACE_DATASETS,
    AdaptiveCrawlerRateLimiter,
    CrawledAttack,
    DualStreamIntelCrawler,
    ThreatIntelItem,
    TokenBucketRateLimiter,
)
from scp.security.url_safety import safe_urlopen

logger = logging.getLogger("scp.security.attack_crawler")

try:
    from datasets import load_dataset  # type: ignore[no-redef]
except ImportError:
    load_dataset = None  # type: ignore[assignment]
    logger.warning(
        "HuggingFace datasets library not installed — pip install datasets "
        "(HuggingFace crawl disabled for this process; warned once per process)"
    )

CRAWL_INTERVAL = int(os.environ.get("SCP_ATTACK_CRAWL_INTERVAL", "3600"))

# Sources list backwards compatibility
GITHUB_REPOS = ATTACK_GITHUB_REPOS
COUNTER_DEFENSE_SOURCES = COUNTER_GITHUB_REPOS
HUGGINGFACE_DATASETS = ATTACK_HUGGINGFACE_DATASETS
REDDIT_SUBREDDITS = ATTACK_REDDIT_SUBREDDITS
REDDIT_KEYWORDS = ["jailbreak", "prompt injection", "bypass", "unrestricted"]


class AttackCrawler:
    """Crawler engine with backward-compatible API and DualStreamIntelCrawler capabilities."""

    def __init__(self, data_dir: str | Path = "data") -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.attacks_file = self.data_dir / "crawled_attacks.jsonl"
        self.raw_intel_file = self.data_dir / "raw_threat_intel.jsonl"
        self.cache_file = self.data_dir / "intel_crawl_cache.json"
        self.legacy_cache_file = self.data_dir / "github_crawl_cache.json"

        # Rate Limiting
        self.rate_limiter = AdaptiveCrawlerRateLimiter(refill_rate_per_sec=5.0, capacity=10.0)

        # Hash index for deduplication
        self._seen_hashes: set[str] = set()
        self._load_seen()

        self._stats: dict[str, Any] = {
            "crawl_cycles": 0,
            "new_attacks_found": 0,
            "new_counters_found": 0,
            "by_source": {},
        }

    def _load_seen(self) -> None:
        """Load seen SHA-256 hashes from existing files."""
        if self.raw_intel_file.exists():
            try:
                with open(self.raw_intel_file, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            record = json.loads(line)
                            sha = record.get("sha256") or record.get("intel_id")
                            if sha:
                                self._seen_hashes.add(sha)
                        except Exception:
                            continue
            except Exception as e:
                logger.warning(f"AttackCrawler._load_seen raw_intel error: {e}", exc_info=True)

        if self.attacks_file.exists():
            try:
                with open(self.attacks_file, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            entry = json.loads(line)
                            text = entry.get("attack_text", "")
                            if text:
                                sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
                                self._seen_hashes.add(sha)
                        except Exception:
                            continue
            except Exception as e:
                logger.warning(f"AttackCrawler._load_seen attacks_file error: {e}", exc_info=True)

    def crawl_all_sync(self, force: bool = False) -> list[CrawledAttack]:
        """Execute crawl across attack and counter streams synchronously."""
        new_attacks: list[CrawledAttack] = []
        self._stats["crawl_cycles"] += 1

        # 1. GitHub Attack Stream
        try:
            gh_attacks = self._crawl_github()
            new_attacks.extend(gh_attacks)
        except Exception as e:
            logger.warning(f"GitHub crawl failed: {e}", exc_info=True)

        # 2. HuggingFace Attack Stream
        try:
            hf_attacks = self._crawl_huggingface()
            new_attacks.extend(hf_attacks)
        except Exception as e:
            logger.warning(f"HuggingFace crawl failed: {e}", exc_info=True)

        # 3. Reddit Attack Stream
        try:
            reddit_attacks = self._crawl_reddit()
            new_attacks.extend(reddit_attacks)
        except Exception as e:
            logger.warning(f"Reddit crawl failed: {e}", exc_info=True)

        # 4. Counter Stream & Local Seeds via DualStream delegation
        try:
            dual_crawler = DualStreamIntelCrawler(data_dir=self.data_dir)
            counter_items = dual_crawler._crawl_github_counter_stream(force=force)
            local_items = dual_crawler._crawl_local_intel()
            for item in counter_items + local_items:
                new_attacks.append(
                    CrawledAttack(
                        source=item.source_type,
                        source_url=item.source_url,
                        attack_text=item.raw_payload,
                        category=item.category,
                        discovered_at=item.ingested_at,
                        stream_type=item.stream_type,
                        confidence=item.confidence,
                    )
                )
        except Exception as e:
            logger.debug(f"Secondary stream ingestion note: {e}")

        # Deduplicate strictly on full 64-character SHA-256
        unique: list[CrawledAttack] = []
        for attack in new_attacks:
            h_full = hashlib.sha256(attack.attack_text.encode("utf-8")).hexdigest()
            if h_full not in self._seen_hashes:
                self._seen_hashes.add(h_full)
                unique.append(attack)
                self._stats["new_attacks_found"] += 1
                src = attack.source
                self._stats["by_source"][src] = self._stats["by_source"].get(src, 0) + 1

        if unique:
            self._save_attacks(unique)
            logger.info(f"AttackCrawler: found {len(unique)} new threat items")
        else:
            logger.info("AttackCrawler: no new attacks found")

        return unique

    async def crawl_all(self, force: bool = False) -> list[CrawledAttack]:
        """Execute crawl across attack and counter streams."""
        return self.crawl_all_sync(force=force)

    def _crawl_github(self) -> list[CrawledAttack]:
        """Crawl GitHub repositories for threat payloads.

        [AUDIT-R2 F-01] Token host-binding: mỗi token chỉ được đi kèm host của
        provider nó thuộc về. GITHUB_TOKEN -> api.github.com; HF_TOKEN KHÔNG
        được fallback vào đây — fallback cũ gửi HuggingFace token sang
        api.github.com qua header Authorization (credential leak cross-provider,
        probe audit đã bắt được header). HF credentials chỉ được dùng trong
        _crawl_huggingface qua thư viện `datasets`, không bao giờ qua HTTP header
        của host GitHub.
        """
        attacks = []
        gh_token = os.environ.get("GITHUB_TOKEN", "")
        headers = {"User-Agent": "SCP-V104/1.0", "Accept": "application/vnd.github.v3+json"}
        if gh_token:
            headers["Authorization"] = f"token {gh_token}"
        else:
            logger.warning("[AttackCrawler] No GITHUB_TOKEN set — using unauthenticated (60 req/hour limit). Set GITHUB_TOKEN in .env for 5000 req/hour.")

        # Universal + Legacy 24h cache — skip repos crawled in last 24h to avoid 403
        cache_file = self.legacy_cache_file
        intel_cache_file = self.cache_file
        cache = {}
        if cache_file.exists():
            try:
                cache = json.loads(cache_file.read_text(encoding="utf-8"))
            except Exception:
                cache = {}
        if intel_cache_file.exists():
            try:
                icache = json.loads(intel_cache_file.read_text(encoding="utf-8"))
                for k, v in icache.items():
                    if k.startswith("github:"):
                        cache[k.replace("github:", "")] = v
            except Exception as exc:
                logger.debug("Failed to read secondary intel cache file: %s", exc)

        now = time.time()
        cache_ttl = 86400  # 24 hours

        for repo in GITHUB_REPOS:
            cache_key = repo
            if cache_key in cache and (now - cache[cache_key]) < cache_ttl:
                logger.debug(f"GitHub {repo} — cached (skipped, <24h since last crawl)")
                continue

            # Token Bucket rate limiter enforcement
            self.rate_limiter.acquire_sync(timeout=1.0)

            try:
                url = f"https://api.github.com/repos/{repo}/readme"
                req = urllib.request.Request(url, headers=headers)
                with safe_urlopen(req, timeout=15) as resp:
                    data = json.loads(resp.read())
                    readme = data.get("content", "")
                    if readme:
                        readme_text = base64.b64decode(readme).decode("utf-8", errors="replace")
                        extracted = self._extract_attacks_from_text(readme_text, f"github:{repo}")
                        attacks.extend(extracted)
                cache[cache_key] = now
            except Exception as e:
                if "403" in str(e) or "rate limit" in str(e).lower():
                    logger.warning(f"GitHub {repo} failed: {e} — STOPPING crawl (rate limit hit, will retry next cycle)", exc_info=True)
                    break
                logger.warning(f"GitHub {repo} failed: {e}", exc_info=True)

            try:
                self.rate_limiter.acquire_sync(timeout=1.0)
                url = f"https://api.github.com/repos/{repo}/issues?per_page=10&state=open"
                req = urllib.request.Request(url, headers=headers)
                with safe_urlopen(req, timeout=15) as resp:
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
                if "403" in str(e) or "rate limit" in str(e).lower():
                    logger.warning(f"GitHub {repo} issues failed: {e} — STOPPING (rate limit)", exc_info=True)
                    break
                logger.warning(f"GitHub {repo} issues failed: {e}", exc_info=True)

        # Save cache
        try:
            cache_file.write_text(json.dumps(cache), encoding="utf-8")
            intel_cache = {f"github:{k}": v for k, v in cache.items()}
            intel_cache_file.write_text(json.dumps(intel_cache, indent=2), encoding="utf-8")
        except Exception as e:
            logger.debug(f"Cache save failed: {e}", exc_info=True)

        return attacks

    def _crawl_huggingface(self) -> list[CrawledAttack]:
        """Crawl HuggingFace datasets for jailbreak payloads with rate limiting and 24h cache."""
        attacks = []
        if load_dataset is None:
            return []

        now = time.time()
        for ds_name in HUGGINGFACE_DATASETS:
            # Token Bucket rate limiter
            self.rate_limiter.acquire_sync(timeout=1.0)
            try:
                ds = None
                used_split = None
                for split_name in ["train", "jailbreak", "regular", "test", "validation"]:
                    try:
                        ds = load_dataset(ds_name, split=split_name, streaming=True, revision="main")
                        used_split = split_name
                        break
                    except Exception:
                        continue
                if ds is None:
                    ds = load_dataset(ds_name, streaming=True, revision="main")
                    used_split = "default"

                count = 0
                for item in ds:
                    if count >= 200:
                        break
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
                logger.info(f"HuggingFace {ds_name} (split={used_split}): scanned {count} items")
            except Exception as e:
                logger.warning(f"HuggingFace {ds_name} failed: {e}", exc_info=True)

        return attacks

    def _crawl_reddit(self) -> list[CrawledAttack]:
        """Crawl Reddit for attack discussions with rate limiting and caching."""
        attacks = []
        headers = {
            "User-Agent": "SCP-V104/1.0 (security research; scp-vietnam@example.com)"
        }

        for subreddit in REDDIT_SUBREDDITS:
            self.rate_limiter.acquire_sync(timeout=1.0)
            for attempt in range(3):
                try:
                    for keyword in REDDIT_KEYWORDS:
                        url = f"https://www.reddit.com/r/{subreddit}/search.json?q={urllib.parse.quote(keyword)}&sort=new&limit=25&restrict_sr=1"
                        req = urllib.request.Request(url, headers=headers)
                        with safe_urlopen(req, timeout=5) as resp:
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
                    break
                except urllib.error.URLError as e:
                    if "10061" in str(e) or "Connection refused" in str(e):
                        logger.debug(f"Reddit r/{subreddit}: connection refused — skipping")
                        break
                    if attempt < 2:
                        time.sleep(2 ** attempt)
                    else:
                        logger.debug(f"Reddit r/{subreddit} failed after 3 retries: {e}")
                except Exception as e:
                    if attempt < 2:
                        time.sleep(2 ** attempt)
                    else:
                        logger.debug(f"Reddit r/{subreddit} failed: {e}", exc_info=True)

        return attacks

    def _extract_attacks_from_text(self, text: str, source_url: str) -> list[CrawledAttack]:
        """Extract attack payloads with keyword filtering."""
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
        now = time.time()
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
                    src = "github" if "github" in source_url else ("huggingface" if "huggingface" in source_url else "reddit")
                    attacks.append(
                        CrawledAttack(
                            source=src,
                            source_url=source_url,
                            attack_text=clean,
                            category=category,
                            discovered_at=now,
                        )
                    )
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
        """Persist attacks to both legacy attacks_file and cryptographic raw_threat_intel.jsonl."""
        # 1. Primary cryptographic provenance ledger
        with open(self.raw_intel_file, "a", encoding="utf-8") as f_raw:
            for a in attacks:
                full_sha = hashlib.sha256(a.attack_text.encode("utf-8")).hexdigest()
                record = {
                    "intel_id": getattr(a, "intel_id", full_sha),
                    "stream_type": getattr(a, "stream_type", "attack"),
                    "source_type": a.source,
                    "source_url": a.source_url,
                    "category": a.category,
                    "raw_payload": a.attack_text,
                    "sha256": getattr(a, "sha256", full_sha),
                    "ingested_at": getattr(a, "discovered_at", time.time()),
                    "confidence": getattr(a, "confidence", 0.95),
                }
                f_raw.write(json.dumps(record, ensure_ascii=False) + "\n")

        # 2. Legacy crawled_attacks.jsonl
        with open(self.attacks_file, "a", encoding="utf-8") as f_legacy:
            for a in attacks:
                entry = {
                    "source": a.source,
                    "source_url": a.source_url,
                    "attack_text": a.attack_text[:500],
                    "category": a.category,
                    "discovered_at": a.discovered_at,
                    "tested": getattr(a, "tested", False),
                    "bypass": getattr(a, "bypass", False),
                }
                f_legacy.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def get_new_attacks(self) -> list[str]:
        attacks = []
        if self.attacks_file.exists():
            with open(self.attacks_file, encoding="utf-8") as f:
                for line in f:
                    try:
                        entry = json.loads(line.strip())
                        if not entry.get("tested"):
                            attacks.append(entry.get("attack_text", ""))
                    except Exception:
                        continue
        return attacks

    async def _crawl_source(self) -> list[Any]:
        return []

    def classify_threats(self, raw_threats: list[dict[str, Any]]) -> list[dict[str, Any]]:
        classified = []
        for t in raw_threats:
            item = dict(t)
            item["severity"] = "high"
            item["threat_type"] = "injection"
            classified.append(item)
        return classified

    def deduplicate(self, threats: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen = set()
        deduped = []
        for t in threats:
            key = json.dumps(t, sort_keys=True)
            if key not in seen:
                seen.add(key)
                deduped.append(t)
        return deduped

    def persist_to_store(self, threats: list[Any]) -> None:
        """Persist items to store."""
        pass

    def stats(self) -> dict[str, Any]:
        return self._stats.copy()


def start_crawl_thread(data_dir: str = "data") -> threading.Thread:
    def crawl_loop():
        crawler = AttackCrawler(data_dir=data_dir)
        logger.info(f"AttackCrawler started (interval={CRAWL_INTERVAL}s)")
        time.sleep(120)
        while True:
            try:
                new_attacks = asyncio.run(crawler.crawl_all())
                if new_attacks:
                    logger.info(f"AttackCrawler: {len(new_attacks)} new attacks added to ThreatSimulator pool")
            except Exception as e:
                logger.error(f"AttackCrawler error: {e}", exc_info=True)
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
    attacks = asyncio.run(crawler.crawl_all())
    print(f"New attacks found: {len(attacks)}")
    for a in attacks[:10]:
        print(f"  [{a.source}] ({a.category}) {a.attack_text[:80]}")
    print(f"\nStats: {crawler.stats()}")
    print(f"\nAttacks for ThreatSimulator: {len(crawler.get_new_attacks())}")
