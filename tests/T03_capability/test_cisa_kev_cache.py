"""Focused CISA KEV cache/timeout tests for Slot B.

All transports are controlled and no external network is used.
"""
from __future__ import annotations

import asyncio
import json
import time


def _reset_cisa_singletons(monkeypatch):
    from scp.security import cisa_kev, predictor

    monkeypatch.setattr(cisa_kev, "_FEED_SINGLETON", None)
    with predictor._KEV_RESULT_CACHE_LOCK:
        predictor._KEV_RESULT_CACHE.clear()
    return cisa_kev, predictor


def test_cisa_feed_singleton_refreshes_once_within_ttl(monkeypatch, tmp_path):
    cisa_kev, _predictor = _reset_cisa_singletons(monkeypatch)
    calls = {"n": 0}
    payload = json.dumps({"catalogVersion": "test", "vulnerabilities": [{"cveID": "CVE-2026-1234"}]}).encode()

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return payload

    def transport(_url):
        calls["n"] += 1
        return Response()

    monkeypatch.setattr(cisa_kev, "_open_cisa_feed", transport)
    feed = cisa_kev.get_cisa_kev_feed(data_dir=str(tmp_path))
    assert feed is cisa_kev.get_cisa_kev_feed(data_dir=str(tmp_path / "other"))
    assert feed.refresh_feed()["action"] == "refreshed"
    assert feed.refresh_feed()["action"] == "skipped"
    assert calls["n"] == 1
    assert feed.is_in_kev("cve-2026-1234") is True


def test_cisa_no_egress_is_neutral_and_backed_off(monkeypatch, tmp_path):
    cisa_kev, predictor = _reset_cisa_singletons(monkeypatch)
    calls = {"n": 0}

    def denied(_url):
        calls["n"] += 1
        raise PermissionError("egress denied")

    monkeypatch.setattr(cisa_kev, "_open_cisa_feed", denied)
    monkeypatch.setenv("SCP_CISA_KEV_FAILURE_RETRY_SECONDS", "60")
    feed = cisa_kev.CisaKevFeed(data_dir=str(tmp_path))
    # [Q02] Pin the process singleton to the tmp feed so the predictor probe
    # below exercises the controlled transport instead of the repo data dir.
    monkeypatch.setattr(cisa_kev, "_FEED_SINGLETON", feed)
    assert feed.refresh_feed()["action"] == "failed"
    assert feed.refresh_feed()["action"] == "failed"
    assert calls["n"] == 1
    assert predictor.cisa_kev_match_recent("CVE-2026-1234") is False
    assert calls["n"] == 1


def test_cisa_async_slow_transport_is_bounded_and_neutral(monkeypatch, tmp_path):
    cisa_kev, predictor = _reset_cisa_singletons(monkeypatch)

    def slow(_url):
        time.sleep(0.2)
        raise TimeoutError("controlled slow transport")

    monkeypatch.setattr(cisa_kev, "_open_cisa_feed", slow)
    monkeypatch.setenv("SCP_CISA_KEV_LOOKUP_TIMEOUT_SECONDS", "0.02")
    # [Q02] Pin the singleton to an empty tmp feed so the async lookup really
    # reaches the slow transport instead of short-circuiting on the repo cache.
    feed = cisa_kev.CisaKevFeed(data_dir=str(tmp_path / "async"))
    monkeypatch.setattr(cisa_kev, "_FEED_SINGLETON", feed)

    async def probe():
        started = time.monotonic()
        result = await predictor.cisa_kev_match_recent_async("CVE-2026-9999")
        return result, time.monotonic() - started

    # Measure the await boundary from inside the running loop.  asyncio.run
    # waits for its default executor during shutdown even after wait_for has
    # returned; that shutdown is not request-path latency.
    result, elapsed = asyncio.run(probe())
    assert result is False
    assert elapsed < 0.15
