"""Q02: CISA KEV cache provenance validation + force-refresh dedupe.

Finding under test (from audit + verifier probes, proven pre-fix):
  - `_load_cache` trusted the on-disk `data/cisa_kev.json` timestamp/schema
    without payload provenance.  Under SCP_EGRESS_MODE=deny a future-dated or
    tampered local cache made refresh() return {"action": "skipped",
    "reason": "cache fresh"} and is_exploited(...) return True — a false CVE
    confidence claim derived from an unvalidated local file.
  - refresh(force=True) bypassed TTL/backoff entirely, so N concurrent forced
    callers each issued their own transport (stampede).

Fix under test: the cache envelope must carry a schema version, a trusted
source URL, a sane fetch timestamp (bounded future skew, bounded staleness),
a consistent record count and a canonical SHA-256 digest of the payload.
Anything else loads neutral (never positive).  Forced refreshes are
deduplicated through the single-flight lock.

All transports here are controlled: either a module-level fake that counts
invocations, or the real egress choke point with SCP_EGRESS_MODE=deny.  No
test in this file performs real network I/O.
"""
from __future__ import annotations

import json
import threading
import time

import pytest

from scp.security import cisa_kev

VULNS = [
    {"cveID": "CVE-2023-38606", "vendorProject": "Fortinet", "product": "FortiOS", "dateAdded": "2023-05-26"},
    {"cveID": "CVE-2024-12345", "vendorProject": "Acme", "product": "Widget", "dateAdded": "2024-01-02"},
]


def _valid_envelope(**overrides):
    envelope = {
        "schemaVersion": cisa_kev.CACHE_SCHEMA_VERSION,
        "source": cisa_kev.CISA_KEV_URL,
        "timestamp": time.time() - 60,
        "catalogVersion": "2026.09.14",
        "count": len(VULNS),
        "checksum": cisa_kev._vuln_payload_digest(VULNS),
        "vulnerabilities": [dict(v) for v in VULNS],
    }
    envelope.update(overrides)
    if "vulnerabilities" in overrides and isinstance(overrides["vulnerabilities"], list):
        # Keep self-consistent envelopes: the digest follows the payload.
        # (Cases that WANT a stale digest mutate the dict outside this helper.)
        envelope["checksum"] = cisa_kev._vuln_payload_digest(overrides["vulnerabilities"])
    return envelope


def _write_cache(data_dir, envelope):
    (data_dir / "cisa_kev.json").write_text(json.dumps(envelope), encoding="utf-8")


def _exploding_transport(url):
    raise AssertionError(f"transport must not be called after validated cache load: {url}")


def _denied_transport(url):
    raise PermissionError("egress denied (controlled)")


# ---------------------------------------------------------------------------
# Positive path: a provenance-valid cache loads and serves WITHOUT any transport
# ---------------------------------------------------------------------------


def test_validated_cache_loads_and_serves_without_transport(tmp_path, monkeypatch):
    monkeypatch.setattr(cisa_kev, "_open_cisa_feed", _exploding_transport)
    _write_cache(tmp_path, _valid_envelope())
    feed = cisa_kev.CisaKevFeed(data_dir=str(tmp_path))
    assert feed.is_exploited("CVE-2023-38606") is True
    assert feed.is_exploited("cve-2023-38606") is True
    assert feed.is_exploited("CVE-2099-00000") is False
    summary = feed.refresh()
    assert summary["action"] == "skipped"
    assert summary["reason"] == "cache fresh"


def test_refresh_writes_provenance_and_reloads_offline(tmp_path, monkeypatch):
    payload = json.dumps({"catalogVersion": "2026.09.14", "vulnerabilities": VULNS}).encode()

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return payload

    calls = {"n": 0}

    def transport(_url):
        calls["n"] += 1
        return Response()

    monkeypatch.setattr(cisa_kev, "_open_cisa_feed", transport)
    feed = cisa_kev.CisaKevFeed(data_dir=str(tmp_path))
    assert feed.refresh()["action"] == "refreshed"
    assert calls["n"] == 1
    envelope = json.loads((tmp_path / "cisa_kev.json").read_text(encoding="utf-8"))
    assert envelope["schemaVersion"] == cisa_kev.CACHE_SCHEMA_VERSION
    assert envelope["source"] == cisa_kev.CISA_KEV_URL
    assert envelope["count"] == len(VULNS)
    assert envelope["checksum"] == cisa_kev._vuln_payload_digest(envelope["vulnerabilities"])
    # Simulate process restart: a fresh feed reloads the validated file with
    # zero further transports.
    monkeypatch.setattr(cisa_kev, "_open_cisa_feed", _exploding_transport)
    reloaded = cisa_kev.CisaKevFeed(data_dir=str(tmp_path))
    assert reloaded.is_exploited("CVE-2023-38606") is True
    assert reloaded.refresh()["action"] == "skipped"
    assert calls["n"] == 1


# ---------------------------------------------------------------------------
# Rejections: malformed / foreign / future-skewed / stale / tampered → neutral
# ---------------------------------------------------------------------------

REJECTIONS = {
    "legacy cache without provenance": {
        "timestamp": time.time() - 60,
        "vulnerabilities": VULNS,
    },
    "envelope not an object": ["not", "an", "object"],
    "missing schemaVersion": {k: v for k, v in _valid_envelope().items() if k != "schemaVersion"},
    "future-dated timestamp": _valid_envelope(timestamp=time.time() + 864000),
    "stale beyond max age": _valid_envelope(timestamp=time.time() - 8 * 24 * 3600),
    "foreign source url": _valid_envelope(source="https://evil.example.com/kev.json"),
    "count mismatch": _valid_envelope(count=9999),
    "tampered payload (record replaced)": {
        **_valid_envelope(),
        "vulnerabilities": [{"cveID": "CVE-2026-99999", "vendorProject": "EvilCorp", "product": "implant"}],
    },
    "tampered payload (fabricated record appended)": {
        **_valid_envelope(),
        "vulnerabilities": [dict(v) for v in VULNS] + [{"cveID": "CVE-2026-99999"}],
        "count": len(VULNS) + 1,
    },
    "non-numeric timestamp": _valid_envelope(timestamp="9999999999"),
    "non-finite timestamp": _valid_envelope(timestamp=float("inf")),
    "vulnerabilities not a list": _valid_envelope(vulnerabilities={"cveID": "CVE-2023-38606"}),
    "entry without cveID": _valid_envelope(
        vulnerabilities=[{"cveID": "CVE-2023-38606"}, {"product": "no-id"}],
    ),
}


@pytest.mark.parametrize("case_name", list(REJECTIONS))
def test_untrusted_cache_is_neutral_under_denied_egress(tmp_path, monkeypatch, case_name):
    """Every untrusted cache shape must yield neutral results — never positive.

    Pre-fix, the future-dated and tampered variants returned
    {"action": "skipped", "reason": "cache fresh"} and is_exploited() == True.
    """
    monkeypatch.setattr(cisa_kev, "_open_cisa_feed", _denied_transport)
    _write_cache(tmp_path, REJECTIONS[case_name])
    feed = cisa_kev.CisaKevFeed(data_dir=str(tmp_path))
    # No positive can come from an unvalidated file, regardless of transport.
    assert feed.is_exploited("CVE-2023-38606") is False
    assert feed.is_exploited("CVE-2026-99999") is False
    assert feed.stats()["total_vulns"] == 0
    summary = feed.refresh()
    # Rejected cache must NOT masquerade as "cache fresh"; it falls through to
    # the (here denied) transport.
    assert summary["action"] == "failed"


def test_future_skew_within_tolerance_is_trusted(tmp_path, monkeypatch):
    """A few minutes of clock drift is data hygiene, not a forgery signal."""
    monkeypatch.setattr(cisa_kev, "_open_cisa_feed", _exploding_transport)
    _write_cache(tmp_path, _valid_envelope(timestamp=time.time() + 120))
    feed = cisa_kev.CisaKevFeed(data_dir=str(tmp_path))
    assert feed.is_exploited("CVE-2023-38606") is True
    assert feed.refresh()["action"] == "skipped"


def test_validate_cache_payload_helper_accepts_and_rejects():
    ok, reason = cisa_kev._validate_cache_payload(_valid_envelope())
    assert ok and reason == "ok"
    empty = _valid_envelope(vulnerabilities=[], count=0)
    ok, _ = cisa_kev._validate_cache_payload(empty)
    assert ok  # a trusted zero-record catalog is neutral by data, not by rejection
    tampered = _valid_envelope()
    tampered["vulnerabilities"][0]["product"] = "BackdoorOS"
    ok, reason = cisa_kev._validate_cache_payload(tampered)
    assert not ok
    assert "checksum" in reason


# ---------------------------------------------------------------------------
# End-to-end through the REAL egress choke point (SCP_EGRESS_MODE=deny, no
# monkeypatched transport): unvalidated cache → neutral, never "cache fresh"
# ---------------------------------------------------------------------------


def test_deny_egress_end_to_end_tampered_future_cache_is_neutral(tmp_path, monkeypatch):
    monkeypatch.setenv("SCP_EGRESS_MODE", "deny")
    _write_cache(tmp_path, _valid_envelope(timestamp=time.time() + 864000))
    feed = cisa_kev.CisaKevFeed(data_dir=str(tmp_path))
    summary = feed.refresh()  # real safe_urlopen → EgressDeniedError → failed
    assert summary["action"] == "failed"
    assert feed.is_exploited("CVE-2023-38606") is False
    assert feed.is_exploited("CVE-2026-99999") is False


# ---------------------------------------------------------------------------
# Force-refresh dedupe: stampede collapses to one transport
# ---------------------------------------------------------------------------


class _CountingTransport:
    def __init__(self, payload: bytes, delay: float = 0.25):
        self.payload = payload
        self.delay = delay
        self.calls = 0
        self._lock = threading.Lock()

    def __call__(self, _url):
        with self._lock:
            self.calls += 1
        time.sleep(self.delay)
        payload = self.payload

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return payload

        return Response()


def test_concurrent_force_refresh_deduplicates_to_single_transport(tmp_path, monkeypatch):
    """Pre-fix, 8 concurrent force=True callers issued 8 transports."""
    transport = _CountingTransport(json.dumps({"catalogVersion": "x", "vulnerabilities": VULNS}).encode())
    monkeypatch.setattr(cisa_kev, "_open_cisa_feed", transport)
    feed = cisa_kev.CisaKevFeed(data_dir=str(tmp_path))
    results: list[dict] = []
    results_lock = threading.Lock()
    start = threading.Barrier(8)

    def worker():
        start.wait()
        summary = feed.refresh(force=True)
        with results_lock:
            results.append(summary)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert not any(t.is_alive() for t in threads)
    assert len(results) == 8
    assert transport.calls == 1  # the stampede is deduplicated
    assert all(r["action"] == "refreshed" for r in results)
    assert sum(1 for r in results if r.get("deduplicated")) == 7
    assert feed.is_exploited("CVE-2023-38606") is True


def test_concurrent_force_refresh_on_failure_deduplicates_to_single_transport(tmp_path, monkeypatch):
    """A failing transport must also not be retried once per queued caller."""
    transport = _CountingTransport(b"not json", delay=0.2)
    monkeypatch.setattr(cisa_kev, "_open_cisa_feed", transport)
    feed = cisa_kev.CisaKevFeed(data_dir=str(tmp_path))
    results: list[dict] = []
    results_lock = threading.Lock()
    start = threading.Barrier(8)

    def worker():
        start.wait()
        summary = feed.refresh(force=True)
        with results_lock:
            results.append(summary)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert len(results) == 8
    assert transport.calls == 1
    assert all(r["action"] == "failed" for r in results)
    assert sum(1 for r in results if r.get("deduplicated")) == 7
    assert feed.is_exploited("CVE-2023-38606") is False


def test_sequential_force_refresh_still_refetches(tmp_path, monkeypatch):
    """Dedupe only collapses concurrent demand; a later explicit force fetches again."""
    transport = _CountingTransport(json.dumps({"catalogVersion": "x", "vulnerabilities": VULNS}).encode(), delay=0.0)
    monkeypatch.setattr(cisa_kev, "_open_cisa_feed", transport)
    feed = cisa_kev.CisaKevFeed(data_dir=str(tmp_path))
    assert feed.refresh(force=True)["action"] == "refreshed"
    assert feed.refresh(force=True)["action"] == "refreshed"
    assert transport.calls == 2


def test_force_bypasses_failure_backoff_but_normal_call_respects_it(tmp_path, monkeypatch):
    calls = {"n": 0}

    def failing(_url):
        calls["n"] += 1
        raise OSError("controlled transport failure")

    monkeypatch.setattr(cisa_kev, "_open_cisa_feed", failing)
    monkeypatch.setenv("SCP_CISA_KEV_FAILURE_RETRY_SECONDS", "60")
    feed = cisa_kev.CisaKevFeed(data_dir=str(tmp_path))
    assert feed.refresh()["action"] == "failed"
    backoff = feed.refresh()
    assert backoff == {"action": "failed", "error": "retry_backoff"}
    assert calls["n"] == 1
    forced = feed.refresh(force=True)
    assert forced["action"] == "failed"
    assert forced["error"] == "OSError"
    assert calls["n"] == 2  # explicit operator force bypasses the backoff window


def test_failed_refresh_leaves_trusted_cache_untouched(tmp_path, monkeypatch):
    """A denied refresh must not wipe or downgrade an existing validated cache."""
    _write_cache(tmp_path, _valid_envelope())
    feed = cisa_kev.CisaKevFeed(data_dir=str(tmp_path))
    monkeypatch.setattr(cisa_kev, "_open_cisa_feed", _denied_transport)
    # Age the in-memory view past TTL so refresh really attempts the transport.
    feed._last_refresh = time.time() - cisa_kev.CACHE_TTL_SECONDS - 1
    assert feed.refresh(force=True)["action"] == "failed"
    assert feed.is_exploited("CVE-2023-38606") is True  # validated memory still serves
    assert feed.get_vuln("CVE-2024-12345") is not None
    envelope = json.loads((tmp_path / "cisa_kev.json").read_text(encoding="utf-8"))
    ok, _reason = cisa_kev._validate_cache_payload(envelope)
    assert ok  # durable file keeps its own digest
