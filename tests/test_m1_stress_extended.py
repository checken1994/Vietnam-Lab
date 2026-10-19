# SPDX-License-Identifier: AGPL-3.0-only
"""
tests/test_m1_stress_extended.py
================================
Extended Adversarial Stress Testing for Milestone M1:
TokenBucket Concurrency & Edge Cases, Cache Resilience, Clock Drift,
and Ingestion Integrity.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scp.runtime.judge import RealityJudge
from scp.security.attack_crawler import AttackCrawler, CrawledAttack
from scp.security.intel_crawler import (
    DualStreamIntelCrawler,
    ThreatIntelItem,
    TokenBucketRateLimiter,
)


class TestTokenBucketExtendedStress:
    """Extreme stress & boundary testing for TokenBucketRateLimiter."""

    def test_extreme_thread_contention_100_threads_with_timeout(self):
        """100 threads contending for a bucket with capacity 20 and refill 50/sec.

        Every thread requests with a small timeout.
        Verify: No deadlock, accurate accounting, tokens dispensed never exceed capacity + refill * time.
        """
        capacity = 20.0
        refill_rate = 50.0
        limiter = TokenBucketRateLimiter(refill_rate_per_sec=refill_rate, capacity=capacity)

        num_threads = 100
        barrier = threading.Barrier(num_threads)
        results = [False] * num_threads

        def worker(idx: int):
            barrier.wait()
            # Try to acquire with up to 0.2s timeout
            results[idx] = limiter.acquire(blocking=True, timeout=0.2)

        start = time.monotonic()
        threads = [threading.Thread(target=worker, args=(i,)) for i in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        elapsed = time.monotonic() - start

        success_count = sum(1 for r in results if r is True)
        max_possible = capacity + (elapsed * refill_rate) + 2.0

        assert success_count <= max_possible, (
            f"Over-dispensation! {success_count} dispensed, max possible {max_possible}"
        )
        assert success_count >= int(capacity), (
            f"Expected at least initial capacity ({int(capacity)}) to be acquired, got {success_count}"
        )

    def test_clock_zero_and_minimal_elapsed_boundaries(self):
        """Under microsecond-resolution successive calls (elapsed ~ 0.0), limiter must not over-dispense."""
        limiter = TokenBucketRateLimiter(refill_rate_per_sec=10.0, capacity=10.0)
        # Burst 10 acquisitions in immediate loop with near-zero elapsed time
        acquired = [limiter.acquire(blocking=False) for _ in range(10)]
        assert all(acquired)
        # Bucket exhausted; 11th acquisition must fail
        assert limiter.acquire(blocking=False) is False
        assert limiter.tokens >= 0.0
        assert limiter.tokens < 1.0
        assert limiter.tokens <= limiter.capacity
        assert not (limiter.tokens != limiter.tokens)  # not NaN

    def test_extreme_parameter_boundaries(self):
        """Microsecond refills, astronomical capacities, and fractional tokens."""
        # Micro refill
        micro_limiter = TokenBucketRateLimiter(refill_rate_per_sec=1e-6, capacity=1.0)
        assert micro_limiter.acquire(blocking=False) is True
        assert micro_limiter.acquire(blocking=False) is False

        # Astronomical capacity
        huge_limiter = TokenBucketRateLimiter(refill_rate_per_sec=1e6, capacity=1e9)
        assert huge_limiter.acquire(blocking=False) is True
        assert huge_limiter.tokens > 1e8

    @pytest.mark.asyncio
    async def test_async_await_limiter_high_concurrency(self):
        """50 async tasks concurrently acquiring tokens with async await syntax."""
        limiter = TokenBucketRateLimiter(refill_rate_per_sec=100.0, capacity=25.0)

        async def worker():
            return await limiter

        tasks = [asyncio.create_task(worker()) for _ in range(50)]
        results = await asyncio.gather(*tasks)
        assert all(r is True for r in results)


class TestCacheResilienceExtended:
    """Stress tests on cache persistence, boundary TTLs, and hostile payloads."""

    def test_cache_ttl_exact_boundary(self, tmp_path):
        """Verify boundary condition: exactly at TTL, before TTL, after TTL."""
        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        crawler = DualStreamIntelCrawler(data_dir=data_dir)

        now = time.time()
        # Item 1: 86399 seconds ago (< 24h -> cached)
        crawler._cache["github:recent"] = now - 86390.0
        # Item 2: 86410 seconds ago (> 24h -> expired)
        crawler._cache["github:expired"] = now - 86410.0

        assert crawler._is_cached("github:recent") is True
        assert crawler._is_cached("github:expired") is False

        # Force bypasses cache even if recent
        assert crawler._is_cached("github:recent", force=True) is False

    def test_cache_large_entries_and_unicode_keys(self, tmp_path):
        """Hostile cache with 1000 repos, unicode characters, emojis, and very long names."""
        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        cache_file = data_dir / "intel_crawl_cache.json"

        large_cache: dict[str, float] = {}
        now = time.time()
        for i in range(500):
            large_cache[f"github:repo_测试_💣_{i}"] = now
            large_cache[f"hf:dataset_{'x' * 200}_{i}"] = now

        cache_file.write_text(json.dumps(large_cache, ensure_ascii=False), encoding="utf-8")

        crawler = DualStreamIntelCrawler(data_dir=data_dir)
        assert len(crawler._cache) >= 1000
        assert crawler._is_cached("github:repo_测试_💣_42") is True
        assert crawler._is_cached("hf:dataset_" + "x" * 200 + "_99") is True

    def test_cache_concurrent_save_race_condition(self, tmp_path):
        """Simultaneous threads saving cache state should not corrupt JSON."""
        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        crawler = DualStreamIntelCrawler(data_dir=data_dir)

        errors = []

        def writer_task(worker_id: int):
            try:
                for i in range(20):
                    crawler._cache[f"repo_{worker_id}_{i}"] = time.time()
                    crawler._save_cache()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer_task, args=(w,)) for w in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Concurrent save raised errors: {errors}"

        # Verify on-disk file remains valid JSON
        cache_data = json.loads(crawler.cache_file.read_text(encoding="utf-8"))
        assert isinstance(cache_data, dict)
        assert len(cache_data) == 100


class TestDeduplicationAndProvenanceIntegrity:
    """Stress tests on deduplication index, persistence format, and AttackCrawler compatibility."""

    def test_full_hash_case_insensitive_handling(self, tmp_path):
        """Verify that SHA-256 hashes in hex format preserve exact matching."""
        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        crawler = DualStreamIntelCrawler(data_dir=data_dir)

        payload = "Case sensitivity test payload"
        raw_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest().lower()

        item = ThreatIntelItem(
            intel_id=raw_hash,
            stream_type="attack",
            source_type="test",
            source_url="test://1",
            category="jailbreak",
            raw_payload=payload,
            sha256=raw_hash,
            ingested_at=time.time(),
        )
        crawler._persist_intel([item])

        # Reloading in new instance must contain the exact hash
        reloaded = DualStreamIntelCrawler(data_dir=data_dir)
        assert raw_hash in reloaded._seen_hashes

    def test_attack_crawler_cross_persistence_compatibility(self, tmp_path):
        """Items persisted by DualStreamIntelCrawler must be recognized by AttackCrawler as seen."""
        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True, exist_ok=True)

        dual = DualStreamIntelCrawler(data_dir=data_dir)
        payload = "Shared cross-crawler payload"
        sha = hashlib.sha256(payload.encode("utf-8")).hexdigest()

        item = ThreatIntelItem(
            intel_id=sha,
            stream_type="attack",
            source_type="github",
            source_url="https://github.com/test/repo",
            category="injection",
            raw_payload=payload,
            sha256=sha,
            ingested_at=time.time(),
        )
        dual._persist_intel([item])

        # AttackCrawler instantiated on same data_dir
        ac = AttackCrawler(data_dir=data_dir)
        assert sha in ac._seen_hashes or sha in getattr(ac, "_seen", set()), (
            f"AttackCrawler failed to recognize hash {sha} persisted by DualStreamIntelCrawler"
        )


class TestJudgeThreatIntelCrawlResilience:
    """Stress tests on RealityJudge.run_threat_intel_crawl() under boundary conditions."""

    @pytest.mark.asyncio
    async def test_judge_crawl_with_all_cached_sources_returns_recent_records(self, tmp_path):
        """When all sources are 100% cached, judge must load recent records from raw_threat_intel.jsonl."""
        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True, exist_ok=True)

        # Seed 60 valid items into raw_threat_intel.jsonl
        intel_file = data_dir / "raw_threat_intel.jsonl"
        items = []
        for i in range(60):
            rec = {
                "intel_id": f"sha_{i:04d}",
                "category": "injection",
                "raw_payload": f"payload {i}",
                "stream_type": "attack",
                "sha256": f"sha_{i:04d}",
            }
            items.append(json.dumps(rec))
        intel_file.write_text("\n".join(items) + "\n", encoding="utf-8")

        # Mock crawler that returns [] (simulating fully cached cycle)
        crawler = DualStreamIntelCrawler(data_dir=data_dir)
        async def fake_cached_crawl(force=False):
            return []
        crawler.crawl_all = fake_cached_crawl

        judge = RealityJudge()
        judge._intel_crawler = crawler

        results = await judge.run_threat_intel_crawl()
        # Must return the 50 most recent records
        assert len(results) == 50
        assert results[-1]["intel_id"] == "sha_0059"
        assert results[0]["intel_id"] == "sha_0010"

    @pytest.mark.asyncio
    async def test_judge_crawl_with_zero_records_on_disk_returns_empty_list(self, tmp_path):
        """When raw_threat_intel.jsonl does not exist and crawl returns [], return [] safely without exception."""
        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True, exist_ok=True)

        crawler = DualStreamIntelCrawler(data_dir=data_dir)
        async def fake_empty_crawl(force=False):
            return []
        crawler.crawl_all = fake_empty_crawl

        judge = RealityJudge()
        judge._intel_crawler = crawler

        results = await judge.run_threat_intel_crawl()
        assert results == []
