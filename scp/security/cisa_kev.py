"""
CISA KEV (Known Exploited Vulnerabilities) feed — real-time CVE exploitation data.

Source: https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json
Updated: CISA updates this catalog multiple times per week.

Usage:
    from scp.security.cisa_kev import CisaKevFeed
    feed = CisaKevFeed(data_dir="data")
    feed.refresh()  # fetch latest
    is_exploited = feed.is_exploited("CVE-2026-20963")
    recent = feed.get_recent(limit=10)

Fail-closed cache (Q02): the on-disk cache is only trusted when it carries
verifiable provenance written by this module (schema version, trusted source
URL, fetch timestamp sanity, max staleness, record count and a canonical
SHA-256 payload digest).  Malformed, foreign, future-skewed, stale or tampered
cache files are rejected at load time; the feed then stays neutral (no
positive exploitation claims) until a trusted refresh succeeds.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

from scp.security.url_safety import safe_urlopen

logger = logging.getLogger("scp.security.cisa_kev")

# [FIX] CISA blocks direct access (403 Akamai). Use GitHub mirror instead.
CISA_KEV_URL = "https://raw.githubusercontent.com/cisagov/kev-data/main/known_exploited_vulnerabilities.json"
CISA_KEV_URL_FALLBACK = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"
CACHE_TTL_SECONDS = 6 * 3600  # 6 hours — CISA updates multiple times/week
DEFAULT_FETCH_TIMEOUT_SECONDS = 5.0
MAX_FETCH_TIMEOUT_SECONDS = 30.0
DEFAULT_FAILURE_RETRY_SECONDS = 60.0
MAX_FAILURE_RETRY_SECONDS = 300.0
_CISA_ALLOWED_HOSTS = {"raw.githubusercontent.com", "www.cisa.gov"}

# [Q02] On-disk cache provenance controls.  A cache file is only trusted when
# the writer embedded verifiable provenance (schema version, trusted source
# URL, fetch wall-clock, record count and a canonical SHA-256 digest of the
# payload) and the envelope still passes sanity windows.  Anything else —
# malformed, foreign, future-skewed or stale — is rejected and the feed stays
# neutral (no positive CVE claims) until a trusted refresh succeeds.  An empty
# catalog from a trusted source validates; fabricated or foreign payloads
# never match the embedded digest.
CACHE_SCHEMA_VERSION = 1
MAX_FUTURE_SKEW_SECONDS = 300.0  # clock-drift allowance before a cache is treated as forged
MAX_CACHE_STALENESS_SECONDS = 7 * 24 * 3600  # never trust an on-disk catalog older than this
_TRUSTED_CACHE_SOURCES = frozenset({CISA_KEV_URL, CISA_KEV_URL_FALLBACK})


def _vuln_payload_digest(vulnerabilities: list) -> str:
    """Canonical SHA-256 digest of a KEV vulnerability list.

    ``sort_keys`` + compact separators make the digest independent of dict key
    ordering and whitespace, so a payload written by us and read back still
    matches, while any tampered/added/dropped record changes the digest.
    """
    canonical = json.dumps(
        vulnerabilities, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _validate_cache_payload(data: object, *, now: float | None = None) -> tuple[bool, str]:
    """Validate an on-disk KEV cache envelope against provenance + sanity.

    Returns ``(trusted, reason)``.  Checks are ordered cheapest first so the
    reason string is useful for operators.  A valid envelope must carry the
    writing schema version, a trusted source URL, a plausible fetch timestamp
    (not future-skewed, not beyond max staleness), a record count consistent
    with the payload and a checksum matching the canonical payload digest.
    """
    if not isinstance(data, dict):
        return False, "envelope is not a JSON object"
    if data.get("schemaVersion") != CACHE_SCHEMA_VERSION:
        return False, "missing or unknown schemaVersion"
    source = data.get("source")
    if not isinstance(source, str) or source not in _TRUSTED_CACHE_SOURCES:
        return False, "untrusted provenance source"
    timestamp = data.get("timestamp")
    if (
        isinstance(timestamp, bool)
        or not isinstance(timestamp, (int, float))
        or not math.isfinite(timestamp)
        or timestamp <= 0
    ):
        return False, "invalid fetch timestamp"
    if now is None:
        now = time.time()
    if timestamp > now + MAX_FUTURE_SKEW_SECONDS:
        return False, "fetch timestamp is in the future beyond clock skew"
    if now - timestamp > MAX_CACHE_STALENESS_SECONDS:
        return False, "cache exceeds maximum staleness"
    catalog_version = data.get("catalogVersion")
    if catalog_version is not None and not isinstance(catalog_version, str):
        return False, "invalid catalogVersion"
    vulnerabilities = data.get("vulnerabilities")
    if not isinstance(vulnerabilities, list):
        return False, "vulnerabilities is not a list"
    for entry in vulnerabilities:
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("cveID"), str)
            or not entry["cveID"]
        ):
            return False, "vulnerability entry malformed"
    if data.get("count") != len(vulnerabilities):
        return False, "count does not match payload"
    checksum = data.get("checksum")
    if not isinstance(checksum, str) or checksum != _vuln_payload_digest(vulnerabilities):
        return False, "payload checksum mismatch (tampered or truncated cache)"
    return True, "ok"


def _configured_failure_retry_seconds() -> float:
    raw = os.environ.get("SCP_CISA_KEV_FAILURE_RETRY_SECONDS", "").strip()
    try:
        value = float(raw) if raw else DEFAULT_FAILURE_RETRY_SECONDS
    except (TypeError, ValueError):
        return DEFAULT_FAILURE_RETRY_SECONDS
    if not math.isfinite(value) or value <= 0 or value > MAX_FAILURE_RETRY_SECONDS:
        return DEFAULT_FAILURE_RETRY_SECONDS
    return value


def _configured_fetch_timeout() -> float:
    """Return a finite transport timeout; malformed config fails closed."""
    raw = os.environ.get("SCP_CISA_KEV_TIMEOUT_SECONDS", "").strip()
    try:
        value = float(raw) if raw else DEFAULT_FETCH_TIMEOUT_SECONDS
    except (TypeError, ValueError):
        return DEFAULT_FETCH_TIMEOUT_SECONDS
    if not math.isfinite(value) or value <= 0 or value > MAX_FETCH_TIMEOUT_SECONDS:
        return DEFAULT_FETCH_TIMEOUT_SECONDS
    return value


def _open_cisa_feed(url: str):
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in _CISA_ALLOWED_HOSTS:
        raise ValueError(f"Unsupported CISA feed URL: {url!r}")
    allowed_prefixes = {
        "raw.githubusercontent.com": "/cisagov/kev-data/",
        "www.cisa.gov": "/sites/default/files/feeds/",
    }
    if not parsed.path.startswith(allowed_prefixes[parsed.hostname]):
        raise ValueError(f"Unsupported CISA feed path: {parsed.path!r}")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("CISA feed URL must not contain credentials or query data")
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "SCP-CISA-KEV/1.0",
            "Accept": "application/json, text/plain, */*",
        },
    )
    # [EE] Route through the canonical egress choke point (safe_urlopen =
    # SCP_EGRESS_MODE gate + SSRF validation) instead of raw
    # urllib.request.urlopen. The fixed host/path allowlist above stays as
    # defense in depth.
    return safe_urlopen(request, timeout=_configured_fetch_timeout())  # nosec B310 — URL validated by SCP


_FEED_SINGLETON: CisaKevFeed | None = None
_FEED_SINGLETON_LOCK = threading.Lock()


def get_cisa_kev_feed(data_dir: str = "data") -> CisaKevFeed:
    """Return the process-scoped KEV feed cache.

    The predictor is on a verdict-adjacent hot path.  Constructing a feed per
    CVE reloads the durable cache and makes each verdict eligible for network
    I/O.  A singleton keeps the local catalog in memory and lets ``refresh``
    enforce one shared TTL/refresh lock without widening the egress boundary.
    """
    global _FEED_SINGLETON
    if _FEED_SINGLETON is None:
        with _FEED_SINGLETON_LOCK:
            if _FEED_SINGLETON is None:
                _FEED_SINGLETON = CisaKevFeed(data_dir=data_dir)
    return _FEED_SINGLETON


class CisaKevFeed:
    """CISA Known Exploited Vulnerabilities feed.

    Provides:
      - is_exploited(cve_id) → bool
      - get_vuln(cve_id) → dict (full record)
      - get_recent(limit) → list[dict]
      - get_by_product(product) → list[dict]
      - get_by_vendor(vendor) → list[dict]

    Every positive answer derives from either a live fetch through the egress
    choke point or an on-disk cache whose provenance validated — never from an
    unvalidated local file (Q02 fail-closed invariant).
    """

    def __init__(self, data_dir: str = "data"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.cache_file = self.data_dir / "cisa_kev.json"
        self._vulns: dict[str, dict] = {}  # cveID -> record
        self._last_refresh = 0
        self._last_failure = 0.0
        self._refresh_lock = threading.Lock()
        # [Q02] Single-flight bookkeeping.  ``_refresh_generation`` counts
        # completed refresh attempts under the lock; ``_last_refresh_summary``
        # is the honest outcome of the most recent attempt so callers that
        # queued behind a stampede reuse it instead of hitting the feed again.
        self._refresh_generation = 0
        self._last_refresh_summary: dict | None = None
        self._catalog_version = ""
        self._load_cache()

    def _load_cache(self):
        """Load the on-disk cache only if its provenance validates.

        A rejected file is not deleted (operators may inspect it) but is never
        used: ``_vulns``/``_last_refresh`` stay at their untrusted-empty
        values, which makes ``refresh`` fall through to the trusted transport
        and keeps every lookup neutral until a successful fetch rewrites the
        file with valid provenance.
        """
        if not self.cache_file.exists():
            return
        try:
            data = json.loads(self.cache_file.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 — unreadable cache fails closed, not the process
            logger.warning("[CISA KEV] Cache unreadable, ignoring: %s", exc)
            return
        trusted, reason = _validate_cache_payload(data)
        if not trusted:
            logger.warning(
                "[CISA KEV] Cache rejected (%s); staying neutral until a trusted refresh", reason
            )
            return
        self._vulns = {v["cveID"].upper(): v for v in data["vulnerabilities"]}
        self._last_refresh = float(data["timestamp"])
        self._catalog_version = str(data.get("catalogVersion") or "")
        logger.info(
            "[CISA KEV] Loaded %d validated vulns from cache (source %s)",
            len(self._vulns),
            data["source"],
        )

    def _write_cache_atomic(self, envelope: dict) -> None:
        """Persist the cache envelope atomically (temp file + ``os.replace``).

        A torn or partially-written file can never appear at the cache path,
        and the payload it holds always matches the embedded digest computed
        before the write.
        """
        fd, tmp_name = tempfile.mkstemp(
            dir=str(self.cache_file.parent), prefix=".cisa_kev.", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(envelope, fh, ensure_ascii=False)
            os.replace(tmp_name, self.cache_file)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError as cleanup_exc:
                # The original write error below is what callers must see; a
                # leftover temp file is logged, not swallowed silently.
                logger.debug("[CISA KEV] temp cache cleanup failed: %s", cleanup_exc)
            raise

    def refresh(self, force: bool = False) -> dict:
        """Fetch latest from CISA with a shared lock and finite timeout.

        Fail-closed behaviour:

        - Only cache data whose provenance validated (schema version, trusted
          source, fetch timestamp within skew/staleness windows, payload
          checksum) is ever served.  A malformed/foreign/future-skewed/stale/
          tampered on-disk file is rejected at load time, so ``refresh`` keeps
          trying the trusted transport and ``is_exploited`` stays False until a
          real refresh succeeds.
        - Under a transport failure the last trusted cache is left untouched
          and the call returns ``failed``; callers must treat that as
          unavailable rather than inventing a positive match or confidence
          boost.
        - ``force=True`` still bypasses TTL and failure backoff (explicit
          operator intent), but forced refreshes are deduplicated through the
          same single-flight lock: callers that queued while another attempt
          ran return that attempt's outcome (``deduplicated: True``) instead of
          stampeding the feed with one transport per caller.
        """
        generation_on_entry = self._refresh_generation
        now = time.time()
        if not force and self._last_refresh and now - self._last_refresh < CACHE_TTL_SECONDS:
            return {"action": "skipped", "reason": "cache fresh", "count": len(self._vulns)}

        with self._refresh_lock:
            if self._refresh_generation != generation_on_entry:
                # Another caller completed an attempt (success or failure)
                # while we waited for the lock.  Reuse its outcome honestly;
                # do not issue a second transport for the same demand.
                if self._last_refresh_summary is None:
                    summary: dict = {
                        "action": "skipped",
                        "reason": "cache fresh",
                        "count": len(self._vulns),
                    }
                else:
                    summary = dict(self._last_refresh_summary)
                summary["deduplicated"] = True
                return summary
            now = time.time()
            if not force and self._last_refresh and now - self._last_refresh < CACHE_TTL_SECONDS:
                return {"action": "skipped", "reason": "cache fresh", "count": len(self._vulns)}
            if (
                not force
                and self._last_failure
                and now - self._last_failure < _configured_failure_retry_seconds()
            ):
                return {"action": "failed", "error": "retry_backoff"}
            try:
                with _open_cisa_feed(CISA_KEV_URL) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                if not isinstance(data, dict):
                    raise ValueError("invalid CISA KEV payload")
                vulns = data.get("vulnerabilities", [])
                if not isinstance(vulns, list):
                    raise ValueError("invalid CISA KEV vulnerabilities payload")
                clean = [
                    v
                    for v in vulns
                    if isinstance(v, dict) and isinstance(v.get("cveID"), str) and v["cveID"]
                ]
                catalog_version = data.get("catalogVersion")
                if not isinstance(catalog_version, str):
                    catalog_version = ""
                refreshed_at = time.time()
                # Durability first: only swap in-memory state once the
                # provenance-bearing envelope is on disk, so memory and file
                # never disagree about what future loads will validate.
                self._write_cache_atomic(
                    {
                        "schemaVersion": CACHE_SCHEMA_VERSION,
                        "source": CISA_KEV_URL,
                        "timestamp": refreshed_at,
                        "catalogVersion": catalog_version[:64],
                        "count": len(clean),
                        "checksum": _vuln_payload_digest(clean),
                        "vulnerabilities": clean,
                    }
                )
                self._vulns = {v["cveID"].upper(): v for v in clean}
                self._last_refresh = refreshed_at
                self._last_failure = 0.0
                self._catalog_version = catalog_version
                summary = {
                    "action": "refreshed",
                    "count": len(self._vulns),
                    "catalog_version": catalog_version,
                }
                self._last_refresh_summary = summary
                self._refresh_generation += 1
                logger.info("[CISA KEV] Refreshed: %d vulns", len(self._vulns))
                return dict(summary)
            except Exception as exc:  # noqa: BLE001 — any transport/schema failure is neutral + backed off
                self._last_failure = time.time()
                summary = {"action": "failed", "error": type(exc).__name__}
                self._last_refresh_summary = summary
                self._refresh_generation += 1
                logger.warning("[CISA KEV] Refresh failed (%s): %s", type(exc).__name__, exc)
                return dict(summary)

    def is_exploited(self, cve_id: str) -> bool:
        """Check if a CVE is in CISA KEV (actively exploited).

        Returns True only against data from a trusted transport fetch or a
        provenance-validated cache; a rejected/never-refreshed feed returns
        False for every CVE.
        """
        if not isinstance(cve_id, str):
            return False
        with self._refresh_lock:
            return cve_id.upper() in self._vulns

    def get_vuln(self, cve_id: str) -> dict | None:
        """Get full record for a CVE."""
        if not isinstance(cve_id, str):
            return None
        with self._refresh_lock:
            return self._vulns.get(cve_id.upper())

    def get_recent(self, limit: int = 10) -> list[dict]:
        """Get most recently added vulnerabilities."""
        with self._refresh_lock:
            vulns = list(self._vulns.values())
        sorted_vulns = sorted(
            vulns,
            key=lambda v: v.get("dateAdded", ""),
            reverse=True,
        )
        return sorted_vulns[:limit]

    def get_by_product(self, product: str) -> list[dict]:
        """Get vulns by product name."""
        p_lower = product.lower()
        with self._refresh_lock:
            vulns = list(self._vulns.values())
        return [v for v in vulns if p_lower in v.get("product", "").lower()]

    def get_by_vendor(self, vendor: str) -> list[dict]:
        """Get vulns by vendor name."""
        v_lower = vendor.lower()
        with self._refresh_lock:
            vulns = list(self._vulns.values())
        return [v for v in vulns if v_lower in v.get("vendorProject", "").lower()]

    def stats(self) -> dict:
        """Get feed stats."""
        return {
            "total_vulns": len(self._vulns),
            "last_refresh": self._last_refresh,
            "cache_age_hours": round((time.time() - self._last_refresh) / 3600, 1) if self._last_refresh else None,
            "catalog_version": self._catalog_version,
        }

    # ---- [SCP-DNA-FIX R13-3 BUG-007] Public API (canonical names) ----
    # R5 added CisaKevFeed with `refresh()` + `is_exploited()` but vulture found
    # 0 callers across the entire codebase → SCP has no awareness of in-the-wild
    # CVE exploitation despite the predictor.py docstring claiming it does. The
    # fix has two parts:
    #   1. Public `is_in_kev()` + `refresh_feed()` aliases (canonical names that
    #      match the bug spec — keeps the existing is_exploited/refresh methods
    #      working as thin wrappers for backward-compat).
    #   2. [WIRED in scp/security/predictor.py via cisa_kev_match_recent]:
    #      from scp.security.cisa_kev import CisaKevFeed
    #      _kev_feed = CisaKevFeed()
    #      _kev_feed.refresh_feed()
    #      if _kev_feed.is_in_kev(cve_id):
    #          # boost confidence / record actively exploited CVE evidence
    def is_in_kev(self, cve_id: str) -> bool:
        """Public alias for `is_exploited` — check if a CVE is in the CISA KEV
        catalog (i.e., actively exploited in the wild).

        Args:
            cve_id: CVE identifier (e.g., "CVE-2026-20963"). Case-insensitive.

        Returns:
            True if the CVE appears in the most recently fetched CISA KEV
            catalog. Returns False if the feed has not been refreshed yet
            (call `refresh_feed()` first), if the on-disk cache failed
            provenance validation, or if the CVE is not in the catalog.
        """
        return self.is_exploited(cve_id)

    def refresh_feed(self, force: bool = False) -> dict:
        """Public alias for `refresh` — fetch the latest CISA Known Exploited
        Vulnerabilities catalog with caching + TTL.

        Source URL:
            https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json
        (Note: the actual fetch uses a GitHub mirror as primary because CISA
        blocks direct access via 403 Akamai — see CISA_KEV_URL at top of file.
        Both URLs serve the same catalog.)

        Caching:
            - Local cache file at `data/cisa_kev.json` survives restarts, but
              is only trusted after Q02 provenance validation (schema version,
              trusted source, fetch timestamp skew/staleness windows and a
              canonical payload checksum).  A rejected cache yields neutral
              results, never positive CVE matches.
            - TTL: 6 hours (CISA updates multiple times per week).
            - Pass `force=True` to bypass the TTL; concurrent forced refreshes
              are deduplicated to a single transport via the refresh lock.

        Args:
            force: If True, ignore cache TTL and fetch fresh.

        Returns:
            Summary dict:
                {"action": "refreshed"|"skipped"|"failed",
                 "count": int,
                 "catalog_version": str (if refreshed),
                 "error": str (if failed),
                 "reason": str (if skipped),
                 "deduplicated": bool (if served by a concurrent refresh)}
        """
        return self.refresh(force=force)
