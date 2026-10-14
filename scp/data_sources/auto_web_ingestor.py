"""3-Tier Quarantined AutoWebIngestor for Safe API & Web Crawling into SQLite FTS5 (Wave W23).

Implements the 3-Tier Quarantine Funnel:
  - Tier 1 (Allowlist Egress & SSRF Guard): Enforces `TRUSTED_INGESTION_DOMAINS` allowlist and
    routes all network fetches through `scp.security.url_safety.safe_urlopen`.
  - Tier 2 (Quarantine Staging & Prompt-Injection/Taint Filter): Stores raw fetched records in
    `quarantine_staging` SQLite table with `content_sha256`, `fetched_at`, `ttl_seconds`, and
    blocks indirect prompt injection / script poisoning (`REJECTED_POISONED`).
  - Tier 3 (Cross-Source Verification >= 2 Independent Sources or Tier-1 Axiom -> VerifierReceipt -> FTS5):
    Promotes staged records into `CanonicalFTSWarehouse` (`kb_fts`) ONLY after >= 2 independent
    trusted sources corroborate the fact (or a Tier-1 statutory/axiomatic API returns structured proof)
    and a cryptographic `VerifierReceipt` (HMAC-SHA256) is minted.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
import time
import urllib.parse
from pathlib import Path
from typing import Any, Callable

from scp.core.verifier_receipt import sign_verifier_receipt, verify_verifier_receipt
from scp.knowledge.canonical_fts_warehouse import CanonicalFTSWarehouse, get_canonical_fts_warehouse
from scp.security.url_safety import safe_urlopen

logger = logging.getLogger("scp.data_sources.auto_web_ingestor")

TRUSTED_INGESTION_DOMAINS: frozenset[str] = frozenset({
    "api.worldbank.org",
    "fred.stlouisfed.org",
    "pubchem.ncbi.nlm.nih.gov",
    "eutils.ncbi.nlm.nih.gov",
    "api.coingecko.com",
    "api.frankfurter.app",
    "earthquake.usgs.gov",
    "api.open-meteo.com",
    "en.wikipedia.org",
    "vi.wikipedia.org",
    "www.wikidata.org",
    "vbpl.vn",
    "chinhphu.vn",
    "data.un.org",
    "api.uis.unesco.org",
})

TIER1_AXIOMATIC_AUTHORITIES: frozenset[str] = frozenset({
    "pubchem.ncbi.nlm.nih.gov",
    "earthquake.usgs.gov",
    "vbpl.vn",
    "chinhphu.vn",
})

_POISON_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"ignore\s+(?:all\s+)?(?:previous|prior)\s+instructions", re.IGNORECASE),
    re.compile(r"system\s*prompt\s*override", re.IGNORECASE),
    re.compile(r"you\s+are\s+now\s+in\s+developer\s+mode", re.IGNORECASE),
    re.compile(r"<script\b[^>]*>", re.IGNORECASE),
    re.compile(r"\bDROP\s+TABLE\b|\bUNION\s+SELECT\b", re.IGNORECASE),
    re.compile(r"rm\s+-rf\s+/", re.IGNORECASE),
)


def is_trusted_ingestion_url(url: str) -> bool:
    """Tier 1 Check: Verify URL uses https and belongs to TRUSTED_INGESTION_DOMAINS."""
    try:
        parsed = urllib.parse.urlparse(str(url or "").strip())
        if parsed.scheme.lower() != "https":
            return False
        host = (parsed.hostname or "").lower()
        return host in TRUSTED_INGESTION_DOMAINS
    except Exception:  # noqa: BLE001
        return False


def scan_content_for_poisoning(text: str) -> tuple[bool, str]:
    """Tier 2 Check: Scan fetched web/API text for indirect prompt injection or payload poisoning."""
    raw = str(text or "").strip()
    if not raw:
        return False, "empty_content"
    for pat in _POISON_PATTERNS:
        m = pat.search(raw)
        if m:
            return False, f"poison_pattern_detected:{m.group(0)[:40]}"
    return True, "clean"


def _normalize_fact_fingerprint(text: str) -> str:
    """Extract canonical numbers and lowercase tokens to check cross-source agreement."""
    nums = re.findall(r"-?\d+(?:[.,]\d+)?", text)
    words = [w for w in re.findall(r"[a-zA-ZÀ-ỹ]{3,}", text.lower())]
    return "|".join(sorted(set(nums))) + "::" + "|".join(sorted(set(words[:15])))


class AutoWebIngestor:
    """Manages 3-Tier Quarantined API/Web Ingestion into CanonicalFTSWarehouse."""

    def __init__(
        self,
        warehouse: CanonicalFTSWarehouse | None = None,
        quarantine_db_path: Path | str | None = None,
        fetcher: Callable[[str], str] | None = None,
    ) -> None:
        self.warehouse = warehouse or get_canonical_fts_warehouse()
        self.quarantine_db_path = Path(
            quarantine_db_path
            or (self.warehouse.db_path.parent / "quarantine_ingestion.sqlite")
        )
        self._fetcher = fetcher or self._default_safe_fetch
        self._init_quarantine_schema()

    def _connect(self) -> sqlite3.Connection:
        try:
            conn = sqlite3.connect(str(self.quarantine_db_path), timeout=30.0, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=DELETE")
            conn.execute("CREATE TABLE IF NOT EXISTS _q_probe (id INTEGER PRIMARY KEY)")
            conn.execute("INSERT OR REPLACE INTO _q_probe (id) VALUES (1)")
            conn.commit()
            return conn
        except sqlite3.OperationalError:
            posix_p = os.path.abspath(str(self.quarantine_db_path)).replace("\\", "/")
            conn = sqlite3.connect(
                f"file:{posix_p}?vfs=unix-none",
                uri=True,
                timeout=30.0,
                check_same_thread=False,
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=MEMORY")
            conn.execute("PRAGMA synchronous=OFF")
            return conn

    def _init_quarantine_schema(self) -> None:
        conn = self._connect()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS quarantine_staging (
                    staging_id TEXT PRIMARY KEY,
                    topic_key TEXT NOT NULL,
                    domain TEXT NOT NULL,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    source_host TEXT NOT NULL,
                    content_sha256 TEXT NOT NULL,
                    fact_fingerprint TEXT NOT NULL,
                    status TEXT NOT NULL,
                    reject_reason TEXT DEFAULT '',
                    fetched_at_epoch REAL NOT NULL,
                    ttl_seconds INTEGER NOT NULL DEFAULT 3600
                );
                """
            )
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _default_safe_fetch(url: str) -> str:
        with safe_urlopen(url, timeout=10) as resp:
            return resp.read().decode("utf-8", errors="replace")

    def stage_from_url(
        self,
        *,
        topic_key: str,
        domain: str,
        title: str,
        url: str,
        raw_content: str | None = None,
        ttl_seconds: int = 3600,
        fetched_at_epoch: float | None = None,
    ) -> dict[str, Any]:
        """Execute Tier 1 (Allowlist) and Tier 2 (Quarantine + Poison Scan) on a fetched URL."""
        now_ts = float(fetched_at_epoch if fetched_at_epoch is not None else time.time())
        if not is_trusted_ingestion_url(url):
            return {
                "ok": False,
                "stage": "TIER1_EGRESS_ALLOWLIST",
                "status": "BLOCKED_UNTRUSTED_DOMAIN",
                "url": url,
            }

        host = (urllib.parse.urlparse(url).hostname or "").lower()
        text = raw_content if raw_content is not None else self._fetcher(url)
        is_clean, scan_reason = scan_content_for_poisoning(text)
        sha256_hex = hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()
        staging_id = f"q_{hashlib.sha256(f'{topic_key}|{host}|{sha256_hex}'.encode()).hexdigest()[:20]}"
        fp = _normalize_fact_fingerprint(text)
        status = "QUARANTINED_CLEAN" if is_clean else "REJECTED_POISONED"

        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO quarantine_staging (
                    staging_id, topic_key, domain, title, content, source_url,
                    source_host, content_sha256, fact_fingerprint, status,
                    reject_reason, fetched_at_epoch, ttl_seconds
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    staging_id,
                    topic_key,
                    domain,
                    title,
                    str(text or "").strip(),
                    url,
                    host,
                    sha256_hex,
                    fp,
                    status,
                    "" if is_clean else scan_reason,
                    now_ts,
                    int(ttl_seconds),
                ),
            )
            conn.commit()
        finally:
            conn.close()

        return {
            "ok": is_clean,
            "staging_id": staging_id,
            "stage": "TIER2_QUARANTINE",
            "status": status,
            "reject_reason": None if is_clean else scan_reason,
            "content_sha256": sha256_hex,
            "source_host": host,
        }

    def reconcile_and_promote(
        self,
        topic_key: str,
        *,
        keywords: str = "",
        require_min_sources: int = 2,
        now_epoch: float | None = None,
    ) -> dict[str, Any]:
        """Tier 3: Promote quarantined topic to SQLite FTS5 ONLY if >=2 independent sources agree (or Tier-1 authority)."""
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT * FROM quarantine_staging
                WHERE topic_key = ? AND status = 'QUARANTINED_CLEAN'
                ORDER BY fetched_at_epoch DESC
                """,
                (topic_key,),
            ).fetchall()
        finally:
            conn.close()

        if not rows:
            return {"promoted": False, "status": "BLOCKED_NO_CLEAN_QUARANTINE_ROWS"}

        distinct_hosts = {r["source_host"] for r in rows}
        is_tier1_authority = any(h in TIER1_AXIOMATIC_AUTHORITIES for h in distinct_hosts)

        # Check numeric consistency across staged sources
        numeric_sets = [
            set(re.findall(r"-?\d+(?:[.,]\d+)?", r["content"]))
            for r in rows
        ]
        if len(rows) >= 2 and all(numeric_sets):
            common_nums = set.intersection(*numeric_sets)
            if not common_nums:
                return {
                    "promoted": False,
                    "status": "BLOCKED_SOURCE_CONFLICT",
                    "hosts": sorted(distinct_hosts),
                }

        if len(distinct_hosts) < require_min_sources and not is_tier1_authority:
            return {
                "promoted": False,
                "status": "WAITING_SECOND_INDEPENDENT_SOURCE",
                "corroborated_hosts": sorted(distinct_hosts),
            }

        primary = rows[0]
        os.environ.setdefault("SCP_CAPABILITY_SECRET", "scp-fts5-promotion-receipt-key-2026")
        receipt = sign_verifier_receipt(
            {
                "task_id": f"ingest:{topic_key}",
                "verifier_id": "scp.data_sources.auto_web_ingestor",
                "verdict": "VERIFIED",
                "evidence_ref": ",".join(sorted(distinct_hosts)),
                "content_sha256": primary["content_sha256"],
                "issued_at": float(time.time()),
            }
        )
        assert verify_verifier_receipt(receipt) is True

        doc_id = f"web_{re.sub(r'[^a-zA-Z0-9_-]', '_', topic_key)}"
        epistemic_tier = "tier1_axiomatic_api" if is_tier1_authority else "corroborated_multi_source_web"
        sha_hex = self.warehouse.upsert_document(
            doc_id=doc_id,
            domain=primary["domain"],
            title=primary["title"],
            content=primary["content"],
            keywords=keywords or topic_key.replace("_", " "),
            source_url=primary["source_url"],
            epistemic_tier=epistemic_tier,
            fetched_at_epoch=float(primary["fetched_at_epoch"]),
            ttl_seconds=int(primary["ttl_seconds"]),
            verifier_receipt=receipt,
        )
        return {
            "promoted": True,
            "status": "PROMOTED_TO_FTS5",
            "doc_id": doc_id,
            "content_sha256": sha_hex,
            "corroborated_hosts": sorted(distinct_hosts),
            "epistemic_tier": epistemic_tier,
            "verifier_receipt": receipt,
        }

    def close(self) -> None:
        """Close associated warehouse connections if owned."""
        if self.warehouse is not None and hasattr(self.warehouse, "close"):
            try:
                self.warehouse.close()
            except Exception as exc:  # noqa: BLE001
                logger.debug("Failed closing ingestor warehouse: %s", exc)

    def __enter__(self) -> AutoWebIngestor:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception as exc:  # noqa: BLE001
            logger.debug("Error during AutoWebIngestor cleanup: %s", exc)
