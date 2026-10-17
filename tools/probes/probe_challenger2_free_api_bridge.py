"""
Empirical Stress and Adversarial Verification Harness for FreeAPIAdapterBridge (M1).
Challenger 2 — Teamwork Preview M1 Verification.
"""
from __future__ import annotations

import io
import json
import os
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.request
from typing import Any
from unittest.mock import MagicMock, patch

# Ensure proper encoding
os.environ["PYTHONIOENCODING"] = "utf-8"

from scp.data_sources.free_api_bridge import (
    FreeAPIAdapterBridge,
    get_bridge,
    DOMAIN_TO_CATALOG_CATEGORIES,
    extract_salient_terms,
    _terms_covered,
    _extract_readable,
)
from scp.interfaces.data_source import reset_shared_health_cache
from scp.security.url_safety import EgressDeniedError


class EmpiricalTestRunner:
    def __init__(self) -> None:
        self.results: list[dict[str, Any]] = []

    def record(self, test_name: str, passed: bool, details: str, metrics: dict[str, Any] | None = None) -> None:
        status = "PASS" if passed else "FAIL"
        print(f"[{status}] {test_name}: {details}")
        self.results.append({
            "name": test_name,
            "passed": passed,
            "status": status,
            "details": details,
            "metrics": metrics or {},
        })

    def run_all(self) -> bool:
        print("=" * 70)
        print("EMPIRICAL STRESS & SAFETY VERIFICATION: FreeAPIAdapterBridge")
        print("=" * 70)

        self.test_egress_mode_deny_health_check()
        self.test_health_check_catalog_missing()
        self.test_sqlite_wal_cache_latency_sla()
        self.test_sqlite_cache_expiration()
        self.test_adversarial_sql_injection()
        self.test_tier1_timeout_fallback_to_live_knowledge()
        self.test_tier1_http_errors_fallback_to_wikipedia()
        self.test_tier1_binary_rejection()
        self.test_all_tiers_failing_returns_none_fail_closed()
        self.test_term_relevance_gate_prevents_unrelated_content()
        self.test_concurrent_multithreading_stress()
        self.test_ssrf_egress_blocking()

        print("=" * 70)
        total = len(self.results)
        passed = sum(1 for r in self.results if r["passed"])
        failed = total - passed
        print(f"Summary: {passed}/{total} tests passed ({failed} failures).")
        print("=" * 70)
        return failed == 0

    # -----------------------------------------------------------------------
    # TEST 1: Egress Deny & Health Check
    # -----------------------------------------------------------------------
    def test_egress_mode_deny_health_check(self) -> None:
        """Requirement: health_check() when SCP_EGRESS_MODE=deny must return False."""
        orig_mode = os.environ.get("SCP_EGRESS_MODE")
        try:
            os.environ["SCP_EGRESS_MODE"] = "deny"
            reset_shared_health_cache()
            bridge = FreeAPIAdapterBridge()
            hc = bridge.health_check()
            if hc is False:
                self.record(
                    "HealthCheck_EgressDeny",
                    True,
                    "health_check() returned False under SCP_EGRESS_MODE=deny",
                    {"egress_mode": "deny", "health_check": False},
                )
            else:
                self.record(
                    "HealthCheck_EgressDeny",
                    False,
                    f"health_check() unexpectedly returned {hc} under SCP_EGRESS_MODE=deny",
                    {"egress_mode": "deny", "health_check": hc},
                )
        finally:
            if orig_mode is not None:
                os.environ["SCP_EGRESS_MODE"] = orig_mode
            else:
                os.environ.pop("SCP_EGRESS_MODE", None)
            reset_shared_health_cache()

    def test_health_check_catalog_missing(self) -> None:
        """Fail-closed: health_check() when catalog is empty or None must return False."""
        bridge_empty = FreeAPIAdapterBridge(catalog=MagicMock(entries=lambda: []))
        reset_shared_health_cache()
        hc = bridge_empty.health_check()
        self.record(
            "HealthCheck_EmptyCatalog",
            hc is False,
            f"health_check() with empty catalog returned {hc} (expected False)",
            {"health_check": hc},
        )

    # -----------------------------------------------------------------------
    # TEST 2: SQLite WAL Cache Latency SLA (<1.5ms)
    # -----------------------------------------------------------------------
    def test_sqlite_wal_cache_latency_sla(self) -> None:
        """Requirement: Test SQLite cache integration with data/knowledge.sqlite3 (<1.5ms latency)."""
        bridge = get_bridge()
        db_path = "data/knowledge.sqlite3"
        if not os.path.exists(db_path):
            self.record("SQLite_Latency_SLA", False, f"Database not found at {db_path}", {})
            return

        # Seed or find a test question
        test_q = "Việt Nam có bao nhiêu tỉnh thành?"
        test_ans = "Việt Nam có 63 tỉnh và thành phố trực thuộc trung ương."
        domain = "geography"

        bridge.knowledge.store(
            question=test_q,
            answer=test_ans,
            domain=domain,
            source="TestSeed",
            confidence=0.99,
            collected_by="test_runner",
        )

        # Warm-up 10 iterations
        for _ in range(10):
            _ = bridge.fetch_for_domain(domain=domain, intent="query", entity=test_q, question=test_q)

        # Benchmark 200 iterations
        latencies_ms: list[float] = []
        for _ in range(200):
            t0 = time.perf_counter()
            res = bridge.fetch_for_domain(domain=domain, intent="query", entity=test_q, question=test_q)
            t1 = time.perf_counter()
            latencies_ms.append((t1 - t0) * 1000.0)
            assert res is not None and res.get("found") is True
            assert res.get("epistemic_tier") == "wal_cache"

        latencies_ms.sort()
        p50 = latencies_ms[int(len(latencies_ms) * 0.50)]
        p95 = latencies_ms[int(len(latencies_ms) * 0.95)]
        p99 = latencies_ms[int(len(latencies_ms) * 0.99)]
        avg = sum(latencies_ms) / len(latencies_ms)
        min_lat = latencies_ms[0]
        max_lat = latencies_ms[-1]

        passed = p95 < 1.5 and avg < 1.5
        details = (
            f"Iterations: 200 | Avg: {avg:.3f}ms | p50: {p50:.3f}ms | "
            f"p95: {p95:.3f}ms | p99: {p99:.3f}ms | Min: {min_lat:.3f}ms | Max: {max_lat:.3f}ms"
        )
        self.record("SQLite_Latency_SLA", passed, details, {
            "avg_ms": avg, "p50_ms": p50, "p95_ms": p95, "p99_ms": p99, "min_ms": min_lat, "max_ms": max_lat,
        })

    def test_sqlite_cache_expiration(self) -> None:
        """Fail-closed cache: Expired records must not be returned."""
        bridge = FreeAPIAdapterBridge()
        db_path = "data/knowledge.sqlite3"
        exp_q = "Câu hỏi thử nghiệm hết hạn TTL?"
        # Insert record with expires_at in the past
        past_epoch = time.time() - 3600
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO knowledge_records
                (id, question, answer, domain, source, source_url, confidence, collected_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (f"kr_exp_{int(time.time()*1000)}", exp_q, "Dữ liệu cũ đã hết hạn", "general", "TestOld", "", 0.95, past_epoch - 86400, past_epoch),
            )
            conn.commit()

        # Query via bridge
        result = bridge._lookup_wal_cache(exp_q, "general")
        passed = (result is None)
        self.record(
            "SQLite_Cache_TTL_Expiration",
            passed,
            f"Expired record returned: {result} (expected None)",
            {"result": result},
        )

    # -----------------------------------------------------------------------
    # TEST 3: Adversarial Input / SQL Injection
    # -----------------------------------------------------------------------
    def test_adversarial_sql_injection(self) -> None:
        """SQL injection, quotes, unicode, long inputs must be handled safely without syntax error."""
        bridge = get_bridge()
        malicious_inputs = [
            "'; DROP TABLE knowledge_records; --",
            "' UNION SELECT 1,2,3,4,5,6,7,8,9 --",
            "\" OR \"\"=\"",
            "\x00\x01\x02\xff",
            "🚀🔥" * 50,
            "A" * 15000,
        ]
        all_safe = True
        err_msg = ""
        for evil in malicious_inputs:
            try:
                res = bridge._lookup_wal_cache(evil, "cybersecurity")
                # Should safely return None (or valid hit if somehow matched), but never crash
            except Exception as exc:
                all_safe = False
                err_msg = f"Crash on input {repr(evil[:30])}: {exc}"
                break

        # Verify table still intact
        with sqlite3.connect("data/knowledge.sqlite3") as conn:
            cur = conn.execute("SELECT count(*) FROM knowledge_records")
            cnt = cur.fetchone()[0]

        self.record(
            "Adversarial_SQL_Injection",
            all_safe and cnt > 0,
            f"Safe={all_safe}, Table rows count={cnt}. {err_msg}",
            {"table_row_count": cnt},
        )

    # -----------------------------------------------------------------------
    # TEST 4: Tier 1 Timeout / Error Fallback to Tier 2 (LiveKnowledge)
    # -----------------------------------------------------------------------
    def test_tier1_timeout_fallback_to_live_knowledge(self) -> None:
        """When Tier 1 public API times out, must smoothly fall back to Tier 2 LiveKnowledgeFetcher."""
        bridge = FreeAPIAdapterBridge()
        q = "Vận tốc ánh sáng trong chân không là bao nhiêu?"
        domain = "physics"

        # Mock urllib.request to raise TimeoutError on Tier 1
        with patch("scp.data_sources.free_api_bridge.safe_urlopen", side_effect=TimeoutError("Connection timed out")):
            with patch("scp.data_sources.free_api_bridge.LiveKnowledgeFetcher.fetch_live") as mock_live:
                mock_live.return_value = {
                    "value": "299,792,458 mét trên giây",
                    "source": "LiveKnowledgeFetcher",
                    "confidence": 0.85,
                    "metadata": {"url": "https://vi.wikipedia.org/wiki/V%E1%BA%ADn_t%E1%BB%91c_%C3%A1nh_s%C3%A1ng"},
                }
                # Ensure no Tier 0 hit
                with patch.object(bridge, "_lookup_wal_cache", return_value=None):
                    res = bridge.fetch_for_domain(domain=domain, intent="query", entity=q, question=q)

                    passed = (
                        res is not None
                        and res.get("found") is True
                        and res.get("epistemic_tier") == "live_knowledge"
                        and "299,792,458" in res.get("value", "")
                    )
                    self.record(
                        "Fallback_Tier1_Timeout_To_Tier2",
                        passed,
                        f"Smooth fallback under TimeoutError. Result tier: {res.get('epistemic_tier') if res else None}",
                        {"result": res},
                    )

    # -----------------------------------------------------------------------
    # TEST 5: Tier 1 HTTP 500 / 404 Fallback to Tier 3 (Wikipedia)
    # -----------------------------------------------------------------------
    def test_tier1_http_errors_fallback_to_wikipedia(self) -> None:
        """When Tier 1 returns HTTP 500/404 and Tier 2 fails, falls back to Tier 3 Wikipedia."""
        bridge = FreeAPIAdapterBridge()
        q = "Thuyết tương đối rộng của ai?"
        domain = "physics"

        http_500 = urllib.error.HTTPError("https://api.example.com", 500, "Internal Server Error", {}, None)

        with patch("scp.data_sources.free_api_bridge.safe_urlopen", side_effect=http_500):
            # Tier 2 returns None (fails)
            with patch("scp.data_sources.free_api_bridge.LiveKnowledgeFetcher.fetch_live", return_value=None):
                # Tier 3 succeeds
                with patch("scp.data_sources.free_api_bridge.wikipedia_client.search_then_summary") as mock_wiki:
                    mock_wiki.return_value = {
                        "title": "Thuyết tương đối rộng",
                        "extract": "Thuyết tương đối rộng là lý thuyết hình học về lực hấp dẫn được Albert Einstein công bố.",
                        "url": "https://vi.wikipedia.org/wiki/Thuyet_tuong_doi_rong",
                    }
                    with patch.object(bridge, "_lookup_wal_cache", return_value=None):
                        res = bridge.fetch_for_domain(domain=domain, intent="query", entity=q, question=q)

                        passed = (
                            res is not None
                            and res.get("found") is True
                            and res.get("epistemic_tier") == "wikipedia"
                            and "Albert Einstein" in res.get("value", "")
                        )
                        self.record(
                            "Fallback_Tier1_HTTP500_To_Tier3_Wikipedia",
                            passed,
                            f"Smooth fallback under HTTP 500. Result tier: {res.get('epistemic_tier') if res else None}",
                            {"result": res},
                        )

    # -----------------------------------------------------------------------
    # TEST 6: Binary Content-Type Rejection in Tier 1
    # -----------------------------------------------------------------------
    def test_tier1_binary_rejection(self) -> None:
        """Tier 1 must reject binary content types (image, zip, pdf, etc.) and proceed to fallback."""
        bridge = FreeAPIAdapterBridge()
        q = "Hình ảnh kính thiên văn James Webb?"
        domain = "astronomy"

        mock_resp = MagicMock()
        mock_resp.headers = {"Content-Type": "image/jpeg"}
        mock_resp.read.return_value = b"\xff\xd8\xff\xe0\x00\x10JFIF"
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.__exit__.return_value = None

        with patch("scp.security.url_safety.safe_urlopen", return_value=mock_resp):
            with patch.object(bridge, "_lookup_wal_cache", return_value=None):
                with patch.object(bridge, "_fallback_live_knowledge", return_value=None):
                    with patch.object(bridge, "_fallback_wikipedia", return_value=None):
                        res = bridge.fetch_for_domain(domain=domain, intent="query", entity=q, question=q)
                        passed = (res is None)
                        self.record(
                            "Tier1_BinaryRejection",
                            passed,
                            f"Binary content-type image/jpeg rejected, returned: {res}",
                            {"result": res},
                        )

    # -----------------------------------------------------------------------
    # TEST 7: All Tiers Failing Returns None (Fail-Closed, FA-04)
    # -----------------------------------------------------------------------
    def test_all_tiers_failing_returns_none_fail_closed(self) -> None:
        """When all tiers fail, system must return None and query_domain must return found=False. No fake data!"""
        bridge = FreeAPIAdapterBridge()
        impossible_q = "xyz_nonexistent_entity_query_999999"
        domain = "astronomy"

        with patch.object(bridge, "_lookup_wal_cache", return_value=None):
            with patch("scp.security.url_safety.safe_urlopen", side_effect=urllib.error.URLError("No route to host")):
                with patch("scp.data_sources.live_knowledge.fetch_live", return_value=None):
                    with patch("scp.core.wikipedia_client.search_then_summary", return_value=None):
                        res = bridge.fetch_for_domain(domain=domain, intent="query", entity=impossible_q, question=impossible_q)
                        q_res = bridge.query_domain(domain=domain, question_str=impossible_q)

                        passed = (
                            res is None
                            and q_res["found"] is False
                            and q_res["answer"] is None
                            and q_res["confidence"] == 0.0
                        )
                        self.record(
                            "FailClosed_AllTiersFailing",
                            passed,
                            f"Properly failed-closed with None / found=False (no fabrication)",
                            {"fetch_result": res, "query_result": q_res},
                        )

    # -----------------------------------------------------------------------
    # TEST 8: Term Relevance Gate
    # -----------------------------------------------------------------------
    def test_term_relevance_gate_prevents_unrelated_content(self) -> None:
        """If external API returns content that does not cover question salient terms, it must be rejected."""
        bridge = FreeAPIAdapterBridge()
        q = "Độ sáng sao Kim lúc nửa đêm?"
        domain = "astronomy"

        mock_resp = MagicMock()
        mock_resp.headers = {"Content-Type": "application/json"}
        # Return valid JSON but completely unrelated to 'sao' or 'kim' or astronomy
        mock_resp.read.return_value = json.dumps({"status": "ok", "message": "Weather in London is rainy today."}).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.__exit__.return_value = None

        with patch("scp.security.url_safety.safe_urlopen", return_value=mock_resp):
            with patch.object(bridge, "_lookup_wal_cache", return_value=None):
                with patch.object(bridge, "_fallback_live_knowledge", return_value=None):
                    with patch.object(bridge, "_fallback_wikipedia", return_value=None):
                        res = bridge.fetch_for_domain(domain=domain, intent="query", entity=q, question=q)
                        passed = (res is None)
                        self.record(
                            "TermRelevanceGate_Rejection",
                            passed,
                            f"Unrelated response was rejected by gate: {res}",
                            {"result": res},
                        )

    # -----------------------------------------------------------------------
    # TEST 9: Concurrency & Multithreading Stress
    # -----------------------------------------------------------------------
    def test_concurrent_multithreading_stress(self) -> None:
        """20 concurrent threads querying bridge (cache hits & fallback misses) simultaneously."""
        bridge = get_bridge()
        errors: list[Exception] = []
        threads: list[threading.Thread] = []

        with patch.object(bridge, "_query_catalog_apis", return_value=None):
            with patch.object(bridge, "_fallback_live_knowledge", return_value=None):
                with patch.object(bridge, "_fallback_wikipedia", return_value=None):
                    def worker(thread_idx: int) -> None:
                        try:
                            for i in range(15):
                                if i % 2 == 0:
                                    # Cache hit query
                                    res = bridge.query_domain("geography", "Việt Nam có bao nhiêu tỉnh thành?")
                                    assert res["found"] is True
                                else:
                                    # Uncached or missing query
                                    res = bridge.query_domain("astronomy", f"Thread query {thread_idx}-{i}")
                                    assert res["found"] is False
                        except Exception as exc:
                            errors.append(exc)

                    for t_id in range(20):
                        t = threading.Thread(target=worker, args=(t_id,))
                        threads.append(t)
                        t.start()

                    for t in threads:
                        t.join(timeout=10.0)

        passed = len(errors) == 0
        self.record(
            "Concurrency_Multithreading_20Threads",
            passed,
            f"Completed 20 threads x 15 ops (300 total). Errors encountered: {len(errors)}",
            {"error_count": len(errors), "first_error": str(errors[0]) if errors else None},
        )

    # -----------------------------------------------------------------------
    # TEST 10: SSRF / Egress Policy Blocking
    # -----------------------------------------------------------------------
    def test_ssrf_egress_blocking(self) -> None:
        """Bridge must call enforce_egress_policy on catalog URLs; blocked URLs must be skipped."""
        bridge = FreeAPIAdapterBridge()
        evil_entry = {
            "name": "SSRF Target",
            "category": "Science & Math",
            "url": "https://169.254.169.254/latest/meta-data/",
            "auth": "No",
            "https": "Yes",
        }
        with patch.object(bridge, "get_apis_for_domain", return_value=[evil_entry]):
            with patch("scp.data_sources.free_api_bridge.enforce_egress_policy", side_effect=EgressDeniedError("169.254.169.254", "SSRF attempt")):
                with patch.object(bridge, "_lookup_wal_cache", return_value=None):
                    with patch.object(bridge, "_fallback_live_knowledge", return_value=None):
                        with patch.object(bridge, "_fallback_wikipedia", return_value=None):
                            res = bridge.fetch_for_domain("physics", "query", "meta", "AWS Metadata?")
                            passed = (res is None)
                            self.record(
                                "SSRF_EgressPolicy_Blocked",
                                passed,
                                f"EgressDeniedError on SSRF URL was handled safely without crash: {res}",
                                {"result": res},
                            )


if __name__ == "__main__":
    runner = EmpiricalTestRunner()
    success = runner.run_all()
    sys.exit(0 if success else 1)
