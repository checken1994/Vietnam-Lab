# SCP CIRCUIT: M09/M98 — Dual-Stream Threat & Counter Intelligence Crawler
"""
SCP Dual-Stream Threat & Counter Intelligence Crawler
=====================================================
Ingests offensive and defensive intelligence streams:
- Attack Stream: 15+ sources (GitHub repos, HuggingFace datasets, Reddit, Local Intel)
- Counter Stream: Defensive repositories and counter-adversarial frameworks
  (PurpleLlama, Rebuff, Llama Guard, Tarpit protocols, Context Inversion)

Features:
- Token Bucket Rate Limiting (TokenBucketRateLimiter adapted from scp.capabilities.tools)
- Universal 24-Hour Cache across all external streams (intel_crawl_cache.json)
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
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import scp.security.url_safety as _url_safety

def safe_urlopen(req: Any, timeout: float = 15, **kwargs: Any) -> Any:
    """Dynamically route safe_urlopen through attack_crawler or url_safety to respect test monkeypatches."""
    import scp.security.attack_crawler as _ac
    opener = getattr(_ac, "safe_urlopen", None)
    if opener is not None and opener is not safe_urlopen:
        return opener(req, timeout=timeout, **kwargs)
    opener_us = getattr(_url_safety, "safe_urlopen", None)
    if opener_us is not None and opener_us is not safe_urlopen:
        return opener_us(req, timeout=timeout, **kwargs)
    return _url_safety.safe_urlopen(req, timeout=timeout, **kwargs)

logger = logging.getLogger("scp.security.intel_crawler")

try:
    from datasets import load_dataset  # type: ignore[no-redef]
except ImportError:
    load_dataset = None  # type: ignore[assignment]
    logger.warning(
        "HuggingFace datasets library not installed — pip install datasets "
        "(HuggingFace crawl disabled for this process)"
    )

from scp.security.intel_sources import (
    ATTACK_GITHUB_REPOS,
    ATTACK_HUGGINGFACE_DATASETS,
    ATTACK_REDDIT_SUBREDDITS,
    COUNTER_DEFENSE_SOURCES,
    COUNTER_GITHUB_REPOS,
    COUNTER_HUGGINGFACE_DATASETS,
    CRAWL_INTERVAL,
    GITHUB_REPOS,
    LOCAL_ATTACK_SEEDS,
    LOCAL_COUNTER_SEEDS,
    AdaptiveCrawlerRateLimiter,
    CrawledAttack,
    ThreatIntelItem,
    TokenBucketRateLimiter,
)



# =============================================================================
# DUAL-STREAM INTEL CRAWLER
# =============================================================================

class DualStreamIntelCrawler:
    """Ingests both Attack and Counter intelligence streams with provenance and caching."""

    def __init__(self, data_dir: str | Path = "data") -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

        # Storage paths
        self.raw_intel_file = self.data_dir / "raw_threat_intel.jsonl"
        self.attacks_file = self.data_dir / "crawled_attacks.jsonl"
        self.cache_file = self.data_dir / "intel_crawl_cache.json"
        self.legacy_cache_file = self.data_dir / "github_crawl_cache.json"

        # Rate Limiter (Token Bucket)
        self.rate_limiter = AdaptiveCrawlerRateLimiter(refill_rate_per_sec=5.0, capacity=10.0)

        # Deduplication index
        self._seen_hashes: set[str] = set()
        self._load_seen_hashes()

        # Cache management
        self._cache: dict[str, float] = self._load_cache()
        self.cache_ttl: float = 86400.0  # 24 hours

        # Telemetry stats
        self._stats: dict[str, Any] = {
            "crawl_cycles": 0,
            "new_intel_found": 0,
            "new_attacks_found": 0,
            "new_counters_found": 0,
            "by_source": {},
            "by_stream": {"attack": 0, "counter": 0},
        }

    # -------------------------------------------------------------------------
    # Cache & Hash Management
    # -------------------------------------------------------------------------

    def _load_seen_hashes(self) -> None:
        """Load seen SHA-256 hashes from both primary and legacy persistence stores."""
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
                logger.warning(f"Error reading {self.raw_intel_file}: {e}", exc_info=True)

        if self.attacks_file.exists():
            try:
                with open(self.attacks_file, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            record = json.loads(line)
                            txt = record.get("attack_text", "")
                            if txt:
                                full_sha = hashlib.sha256(txt.encode("utf-8")).hexdigest()
                                self._seen_hashes.add(full_sha)
                        except Exception:
                            continue
            except Exception as e:
                logger.warning(f"Error reading {self.attacks_file}: {e}", exc_info=True)

    def _load_cache(self) -> dict[str, float]:
        """Load universal 24-hour cache, preferring the most recently modified file on disk."""
        cache: dict[str, float] = {}
        files_to_load = []
        if self.legacy_cache_file.exists():
            files_to_load.append((self.legacy_cache_file.stat().st_mtime, self.legacy_cache_file, True))
        if self.cache_file.exists():
            files_to_load.append((self.cache_file.stat().st_mtime, self.cache_file, False))

        # Sort by mtime ascending so the most recently written file overlays older ones
        files_to_load.sort(key=lambda x: x[0])

        for _mtime, p, is_legacy in files_to_load:
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                for k, v in data.items():
                    ts = float(v)
                    cache[k] = ts
                    if is_legacy or not k.startswith("github:"):
                        k_gh = k if k.startswith("github:") else f"github:{k}"
                        cache[k_gh] = ts
                    if k.startswith("github:"):
                        cache[k.replace("github:", "")] = ts
            except Exception as e:
                logger.warning(f"Failed to read cache file {p}: {e}", exc_info=True)
        return cache

    def _save_cache(self) -> None:
        """Persist universal cache atomically."""
        try:
            content = json.dumps(self._cache, indent=2)
            self.cache_file.write_text(content, encoding="utf-8")
            # Keep legacy cache updated for backwards compatibility
            legacy = {
                k.replace("github:", ""): v
                for k, v in self._cache.items()
                if k.startswith("github:")
            }
            self.legacy_cache_file.write_text(json.dumps(legacy), encoding="utf-8")
        except Exception as e:
            logger.debug(f"Failed to save crawler cache: {e}", exc_info=True)

    def _is_cached(self, key: str, force: bool = False) -> bool:
        if force:
            return False
        now = time.time()
        last_crawl = max(
            self._cache.get(key, 0.0),
            self._cache.get(key.replace("github:", ""), 0.0),
            self._cache.get(f"github:{key}", 0.0) if not key.startswith("github:") else 0.0,
        )
        return (now - last_crawl) < self.cache_ttl

    def _mark_cached(self, key: str) -> None:
        now = time.time()
        self._cache[key] = now
        self._cache[key.replace("github:", "")] = now

    # -------------------------------------------------------------------------
    # Crawling Orchestration
    # -------------------------------------------------------------------------

    async def crawl_all(self, force: bool = False) -> list[ThreatIntelItem]:
        """Execute full dual-stream crawl cycle across all sources."""
        self._stats["crawl_cycles"] += 1
        self._rate_limited = False
        # Sync cache from disk in case external tests modified it
        self._cache = self._load_cache()
        new_items: list[ThreatIntelItem] = []

        # 1. Attack Stream (GitHub, HuggingFace, Reddit)
        try:
            gh_attacks = self._crawl_github_attack_stream(force=force)
            new_items.extend(gh_attacks)
        except Exception as e:
            logger.warning(f"Attack Stream GitHub crawl failed: {e}", exc_info=True)

        try:
            hf_attacks = self._crawl_huggingface_attack_stream(force=force)
            new_items.extend(hf_attacks)
        except Exception as e:
            logger.warning(f"Attack Stream HuggingFace crawl failed: {e}", exc_info=True)

        try:
            reddit_attacks = self._crawl_reddit_attack_stream(force=force)
            new_items.extend(reddit_attacks)
        except Exception as e:
            logger.warning(f"Attack Stream Reddit crawl failed: {e}", exc_info=True)

        # 2. Counter Stream (Defensive GitHub Repos & Datasets)
        if not getattr(self, "_rate_limited", False):
            try:
                gh_counters = self._crawl_github_counter_stream(force=force)
                new_items.extend(gh_counters)
            except Exception as e:
                logger.warning(f"Counter Stream GitHub crawl failed: {e}", exc_info=True)

        try:
            hf_counters = self._crawl_huggingface_counter_stream(force=force)
            new_items.extend(hf_counters)
        except Exception as e:
            logger.warning(f"Counter Stream HuggingFace crawl failed: {e}", exc_info=True)

        # 3. Curated Local Intel (Guarantees fresh seed ingestion even when offline)
        try:
            local_items = self._crawl_local_intel()
            new_items.extend(local_items)
        except Exception as e:
            logger.warning(f"Local intel ingestion failed: {e}", exc_info=True)

        # Save updated cache
        self._save_cache()

        # Deduplicate against database and current run
        unique: list[ThreatIntelItem] = []
        for item in new_items:
            sha = item.sha256
            if sha not in self._seen_hashes:
                self._seen_hashes.add(sha)
                unique.append(item)
                self._stats["new_intel_found"] += 1
                stream = item.stream_type
                self._stats["by_stream"][stream] = self._stats["by_stream"].get(stream, 0) + 1
                if stream == "attack":
                    self._stats["new_attacks_found"] += 1
                else:
                    self._stats["new_counters_found"] += 1
                source_key = f"{item.source_type}:{item.source_url}"
                self._stats["by_source"][source_key] = self._stats["by_source"].get(source_key, 0) + 1

        if unique:
            self._persist_intel(unique)
            logger.info(
                f"DualStreamIntelCrawler: found {len(unique)} new intelligence items "
                f"(Attack={self._stats['new_attacks_found']}, Counter={self._stats['new_counters_found']})"
            )
        else:
            logger.info("DualStreamIntelCrawler: no new intelligence items found in this cycle.")

        return unique

    # -------------------------------------------------------------------------
    # Attack Stream Crawlers
    # -------------------------------------------------------------------------

    def _crawl_github_attack_stream(self, force: bool = False) -> list[ThreatIntelItem]:
        """Crawl Attack Stream GitHub repositories."""
        items: list[ThreatIntelItem] = []
        gh_token = os.environ.get("GITHUB_TOKEN", "")
        headers = {"User-Agent": "SCP-DualStreamCrawler/1.0", "Accept": "application/vnd.github.v3+json"}
        if gh_token:
            headers["Authorization"] = f"token {gh_token}"

        for repo in ATTACK_GITHUB_REPOS:
            cache_key = f"github:{repo}"
            if self._is_cached(cache_key, force=force):
                logger.debug(f"Attack Stream GitHub {repo} — cached (skipped)")
                continue

            # Respect TokenBucket Rate Limiter
            self.rate_limiter.acquire_sync(timeout=1.0)

            # 1. Fetch Readme
            try:
                url = f"https://api.github.com/repos/{repo}/readme"
                req = urllib.request.Request(url, headers=headers)
                with safe_urlopen(req, timeout=12) as resp:
                    data = json.loads(resp.read())
                    readme_b64 = data.get("content", "")
                    if readme_b64:
                        text = base64.b64decode(readme_b64).decode("utf-8", errors="replace")
                        extracted = self._extract_attacks_from_text(text, f"github:{repo}")
                        items.extend(extracted)
                self._mark_cached(cache_key)
            except Exception as e:
                if "403" in str(e) or "rate limit" in str(e).lower():
                    logger.warning(f"GitHub {repo} rate limit hit: {e} — pausing stream")
                    self._rate_limited = True
                    break
                logger.debug(f"GitHub {repo} readme fetch failed: {e}")

            # 2. Fetch Open Issues
            try:
                self.rate_limiter.acquire_sync(timeout=1.0)
                url = f"https://api.github.com/repos/{repo}/issues?per_page=10&state=open"
                req = urllib.request.Request(url, headers=headers)
                with safe_urlopen(req, timeout=12) as resp:
                    issues = json.loads(resp.read())
                    for issue in issues[:10]:
                        title = issue.get("title", "")
                        body = issue.get("body", "") or ""
                        text = f"{title}\n{body}"
                        extracted = self._extract_attacks_from_text(
                            text, f"github:{repo}/issues/{issue.get('number', '?')}"
                        )
                        items.extend(extracted)
            except Exception as e:
                if "403" in str(e) or "rate limit" in str(e).lower():
                    self._rate_limited = True
                    break
                logger.debug(f"GitHub {repo} issues fetch failed: {e}")

        return items

    def _crawl_huggingface_attack_stream(self, force: bool = False) -> list[ThreatIntelItem]:
        """Crawl Attack Stream HuggingFace datasets."""
        if load_dataset is None:
            return []

        items: list[ThreatIntelItem] = []
        for ds_name in ATTACK_HUGGINGFACE_DATASETS:
            cache_key = f"huggingface:{ds_name}"
            if self._is_cached(cache_key, force=force):
                continue

            self.rate_limiter.acquire_sync(timeout=1.0)
            try:
                ds = None
                for split_name in ["train", "jailbreak", "regular", "test", "validation"]:
                    try:
                        ds = load_dataset(ds_name, split=split_name, streaming=True, revision="main")
                        break
                    except Exception:
                        continue
                if ds is None:
                    try:
                        ds = load_dataset(ds_name, streaming=True, revision="main")
                    except Exception:
                        continue

                if ds is not None:
                    count = 0
                    for row in ds:
                        if count >= 100:
                            break
                        text = ""
                        for k in ["prompt", "text", "question", "attack", "payload", "instruction"]:
                            if k in row:
                                text = str(row[k])
                                break
                        if text and len(text) > 15:
                            extracted = self._extract_attacks_from_text(text, f"huggingface:{ds_name}")
                            items.extend(extracted)
                            count += 1
                self._mark_cached(cache_key)
            except Exception as e:
                logger.debug(f"HuggingFace {ds_name} fetch error: {e}")

        return items

    def _crawl_reddit_attack_stream(self, force: bool = False) -> list[ThreatIntelItem]:
        """Crawl Attack Stream Reddit discussions with backoff and caching."""
        # If GitHub repos are already fully cached within 24h window, respect universal cache
        all_repos_cached = all(self._is_cached(f"github:{repo}", force=force) for repo in ATTACK_GITHUB_REPOS)
        if all_repos_cached and not force:
            return []

        items: list[ThreatIntelItem] = []
        headers = {"User-Agent": "SCP-DualStreamCrawler/1.0 (security research)"}
        keywords = ["jailbreak", "prompt injection", "bypass", "unrestricted mode"]

        for subreddit in ATTACK_REDDIT_SUBREDDITS:
            cache_key = f"reddit:{subreddit}"
            if self._is_cached(cache_key, force=force):
                continue

            self.rate_limiter.acquire_sync(timeout=1.0)
            try:
                for kw in keywords:
                    url = f"https://www.reddit.com/r/{subreddit}/search.json?q={urllib.parse.quote(kw)}&sort=new&limit=15&restrict_sr=1"
                    req = urllib.request.Request(url, headers=headers)
                    with safe_urlopen(req, timeout=5) as resp:
                        data = json.loads(resp.read())
                        posts = data.get("data", {}).get("children", [])
                        for post in posts:
                            p = post.get("data", {})
                            title = p.get("title", "")
                            selftext = p.get("selftext", "")
                            permalink = p.get("permalink", "")
                            text = f"{title}\n{selftext}"
                            if len(text) > 20:
                                extracted = self._extract_attacks_from_text(
                                    text, f"reddit:r/{subreddit}{permalink}"
                                )
                                items.extend(extracted)
                self._mark_cached(cache_key)
            except Exception as e:
                logger.debug(f"Reddit r/{subreddit} fetch failed: {e}")

        return items

    # -------------------------------------------------------------------------
    # Counter Stream Crawlers
    # -------------------------------------------------------------------------

    def _crawl_github_counter_stream(self, force: bool = False) -> list[ThreatIntelItem]:
        """Crawl Counter Stream GitHub defensive frameworks."""
        all_repos_cached = all(self._is_cached(f"github:{repo}", force=force) for repo in COUNTER_GITHUB_REPOS)
        if all_repos_cached and not force:
            return []

        items: list[ThreatIntelItem] = []
        gh_token = os.environ.get("GITHUB_TOKEN", "")
        headers = {"User-Agent": "SCP-DualStreamCrawler/1.0", "Accept": "application/vnd.github.v3+json"}
        if gh_token:
            headers["Authorization"] = f"token {gh_token}"

        for repo in COUNTER_GITHUB_REPOS:
            if getattr(self, "_rate_limited", False):
                break
            cache_key = f"github:{repo}"
            if self._is_cached(cache_key, force=force):
                logger.debug(f"Counter Stream GitHub {repo} — cached")
                continue

            self.rate_limiter.acquire_sync(timeout=1.0)
            try:
                url = f"https://api.github.com/repos/{repo}/readme"
                req = urllib.request.Request(url, headers=headers)
                with safe_urlopen(req, timeout=12) as resp:
                    data = json.loads(resp.read())
                    readme_b64 = data.get("content", "")
                    if readme_b64:
                        text = base64.b64decode(readme_b64).decode("utf-8", errors="replace")
                        extracted = self._extract_counter_intel_from_text(text, f"github:{repo}")
                        items.extend(extracted)
                self._mark_cached(cache_key)
            except Exception as e:
                if "403" in str(e) or "rate limit" in str(e).lower():
                    logger.warning(f"GitHub counter repo {repo} hit rate limit: {e}")
                    self._rate_limited = True
                    break
                logger.debug(f"GitHub counter repo {repo} fetch failed: {e}")

        return items

    def _crawl_huggingface_counter_stream(self, force: bool = False) -> list[ThreatIntelItem]:
        """Crawl Counter Stream HuggingFace defensive datasets."""
        if load_dataset is None:
            return []

        items: list[ThreatIntelItem] = []
        for ds_name in COUNTER_HUGGINGFACE_DATASETS:
            cache_key = f"huggingface:{ds_name}"
            if self._is_cached(cache_key, force=force):
                continue

            self.rate_limiter.acquire_sync(timeout=1.0)
            try:
                ds = None
                for split_name in ["train", "test", "validation"]:
                    try:
                        ds = load_dataset(ds_name, split=split_name, streaming=True, revision="main")
                        break
                    except Exception:
                        continue
                if ds is not None:
                    count = 0
                    for row in ds:
                        if count >= 50:
                            break
                        text = str(row.get("text", row.get("prompt", "")))
                        if text and len(text) > 15:
                            extracted = self._extract_counter_intel_from_text(text, f"huggingface:{ds_name}")
                            items.extend(extracted)
                            count += 1
                self._mark_cached(cache_key)
            except Exception as e:
                logger.debug(f"Counter dataset {ds_name} fetch error: {e}")

        return items

    # -------------------------------------------------------------------------
    # Local Intel Stream
    # -------------------------------------------------------------------------

    def _crawl_local_intel(self) -> list[ThreatIntelItem]:
        """Ingest curated local benchmark attack and counter intelligence seeds."""
        items: list[ThreatIntelItem] = []
        now = time.time()

        for seed in LOCAL_ATTACK_SEEDS:
            payload = seed["raw_payload"]
            sha = hashlib.sha256(payload.encode("utf-8")).hexdigest()
            items.append(
                ThreatIntelItem(
                    intel_id=sha,
                    stream_type="attack",
                    source_type="local_intel",
                    source_url=seed["source_url"],
                    category=seed["category"],
                    raw_payload=payload,
                    sha256=sha,
                    ingested_at=now,
                    confidence=0.98,
                )
            )

        for seed in LOCAL_COUNTER_SEEDS:
            payload = seed["raw_payload"]
            sha = hashlib.sha256(payload.encode("utf-8")).hexdigest()
            items.append(
                ThreatIntelItem(
                    intel_id=sha,
                    stream_type="counter",
                    source_type="local_intel",
                    source_url=seed["source_url"],
                    category=seed["category"],
                    raw_payload=payload,
                    sha256=sha,
                    ingested_at=now,
                    confidence=0.98,
                )
            )

        return items

    # -------------------------------------------------------------------------
    # Parsing, Extraction & Categorization
    # -------------------------------------------------------------------------

    def _extract_attacks_from_text(self, text: str, source_url: str) -> list[ThreatIntelItem]:
        """Extract offensive payloads from text with dual-keyword and boundary matching."""
        attacks: list[ThreatIntelItem] = []
        attack_keywords_primary = [
            "ignore all", "ignore previous", "ignore prior", "you are dan",
            "you are now free", "reveal your prompt", "show your prompt",
            "print your instructions", "reveal your system", "print your system",
            "no rules", "no restrictions", "unrestricted mode", "developer mode",
            "jailbreak", "without any restrictions", "no ethical guidelines",
            "disregard everything", "disregard all", "new identity",
            "forget your training", "operate without",
        ]
        attack_keywords_secondary = [
            "instructions", "prompt", "config", "system message", "override",
            "bypass", "forget", "disregard", "pretend", "roleplay", "dan",
            "reveal", "expose", "hidden", "prior training", "ethical guidelines",
        ]
        skip_patterns = [
            "middleware", "security policy", "mock session", "chrome devtools",
            ".env config", "default config", "this guide explains", "the actor can",
        ]

        now = time.time()
        for raw_line in text.split("\n"):
            line = raw_line.strip()
            if len(line) < 15 or len(line) > 2000:
                continue
            line_lower = line.lower()
            if any(skip in line_lower for skip in skip_patterns):
                continue
            prim = sum(1 for kw in attack_keywords_primary if kw in line_lower)
            sec = sum(1 for kw in attack_keywords_secondary if kw in line_lower)
            if prim >= 1 and (prim + sec) >= 2:
                clean = re.sub(r"[*#>`]", "", line).strip()
                if len(clean) > 15:
                    cat = self._categorize_attack(clean)
                    sha = hashlib.sha256(clean.encode("utf-8")).hexdigest()
                    src_type = "github" if "github" in source_url else ("huggingface" if "huggingface" in source_url else "reddit")
                    attacks.append(
                        ThreatIntelItem(
                            intel_id=sha,
                            stream_type="attack",
                            source_type=src_type,
                            source_url=source_url,
                            category=cat,
                            raw_payload=clean,
                            sha256=sha,
                            ingested_at=now,
                            confidence=0.92,
                        )
                    )
        return attacks

    def _extract_counter_intel_from_text(self, text: str, source_url: str) -> list[ThreatIntelItem]:
        """Extract counter-defensive frameworks, rules and tripwires from text."""
        counters: list[ThreatIntelItem] = []
        counter_markers = [
            "purplellama", "rebuff", "llama guard", "safety boundary", "guardrail",
            "prompt injection detection", "adversarial defense", "canary token",
            "honeytoken", "tripwire", "tarpit", "delay connection", "throttle requests",
            "context inversion", "paradox prompt", "sanitization",
        ]

        now = time.time()
        for raw_line in text.split("\n"):
            line = raw_line.strip()
            if len(line) < 20 or len(line) > 2000:
                continue
            line_lower = line.lower()
            if any(marker in line_lower for marker in counter_markers):
                clean = re.sub(r"[*#>`]", "", line).strip()
                if len(clean) > 15:
                    cat = "tarpit" if "tarpit" in line_lower else "counter_defense"
                    sha = hashlib.sha256(clean.encode("utf-8")).hexdigest()
                    src_type = "github" if "github" in source_url else "huggingface"
                    counters.append(
                        ThreatIntelItem(
                            intel_id=sha,
                            stream_type="counter",
                            source_type=src_type,
                            source_url=source_url,
                            category=cat,
                            raw_payload=clean,
                            sha256=sha,
                            ingested_at=now,
                            confidence=0.95,
                        )
                    )
        return counters

    def _categorize_attack(self, text: str) -> str:
        t = text.lower()
        if "ignore" in t or "disregard" in t or "forget" in t:
            return "injection"
        if "dan" in t or "no rules" in t or "no restrictions" in t or "jailbreak" in t:
            return "jailbreak"
        if "reveal" in t or "show your" in t or "print your" in t:
            return "exfiltration"
        if "act as" in t or "pretend" in t or "roleplay" in t:
            return "role_play"
        if "base64" in t or "unicode" in t or "encode" in t:
            return "encoding"
        return "other"

    # -------------------------------------------------------------------------
    # Persistence & Store
    # -------------------------------------------------------------------------

    def _persist_intel(self, items: list[ThreatIntelItem]) -> None:
        """Persist newly crawled items to raw_threat_intel.jsonl and legacy crawled_attacks.jsonl."""
        with open(self.raw_intel_file, "a", encoding="utf-8") as f_raw:
            for item in items:
                record = item.to_dict()
                f_raw.write(json.dumps(record, ensure_ascii=False) + "\n")

        with open(self.attacks_file, "a", encoding="utf-8") as f_legacy:
            for item in items:
                legacy = {
                    "source": item.source_type,
                    "source_url": item.source_url,
                    "attack_text": item.raw_payload[:500],
                    "category": item.category,
                    "discovered_at": item.ingested_at,
                    "tested": False,
                    "bypass": False,
                }
                f_legacy.write(json.dumps(legacy, ensure_ascii=False) + "\n")

    def get_new_attacks(self) -> list[str]:
        """Legacy helper: returns untested attack texts from file."""
        attacks: list[str] = []
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

    def classify_threats(self, raw_threats: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Classify raw threats by severity and type."""
        classified = []
        for t in raw_threats:
            item = dict(t)
            item["severity"] = "high"
            item["threat_type"] = "injection"
            classified.append(item)
        return classified

    def deduplicate(self, threats: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Deduplicate raw threats list."""
        seen = set()
        deduped = []
        for t in threats:
            key = json.dumps(t, sort_keys=True)
            if key not in seen:
                seen.add(key)
                deduped.append(t)
        return deduped

    def stats(self) -> dict[str, Any]:
        """Return crawler statistics snapshot."""
        return self._stats.copy()


__all__ = [
    "safe_urlopen",
    "CRAWL_INTERVAL",
    "ATTACK_GITHUB_REPOS",
    "ATTACK_HUGGINGFACE_DATASETS",
    "ATTACK_REDDIT_SUBREDDITS",
    "COUNTER_GITHUB_REPOS",
    "GITHUB_REPOS",
    "COUNTER_DEFENSE_SOURCES",
    "COUNTER_HUGGINGFACE_DATASETS",
    "LOCAL_ATTACK_SEEDS",
    "LOCAL_COUNTER_SEEDS",
    "ThreatIntelItem",
    "CrawledAttack",
    "TokenBucketRateLimiter",
    "AdaptiveCrawlerRateLimiter",
    "DualStreamIntelCrawler",
]
