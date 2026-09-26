"""
[OPT-14] DTICDataSource — Defense Technical Information Center (DTIC).

DNA SCP: SuperGPQA Military discipline needs verified defense research &
doctrine beyond the generic MilitaryDataSource. DTIC hosts 4M+ scientific &
technical reports (DoD-funded research, doctrine pubs, theses from NPS/AFIT).
Public search is free without an API key — fills the doctrine/research gap.
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request

from scp.interfaces.data_source import IDataSource
from scp.security.url_safety import safe_urlopen  # [AUDIT-20260909 SSRF-S1]
from typing import Optional

logger = logging.getLogger("scp.data_sources.dtic")

# [AUDIT-20260909 SSRF-S1] Hosts cố định — literal duy nhất của builders.
_DTIC_SEARCH_URL = "https://apps.dtic.mil/wti/api/search"
_DTIC_FALLBACK_URL = "https://discover.dtic.mil/results/"


def build_dtic_search_url(question: str, page_size: int = 5) -> str:
    """[AUDIT-20260909 SSRF-S1] Pure URL builder — question được urlencode
    thành query value; không thể đổi host/path. Host cố định apps.dtic.mil."""
    return _DTIC_SEARCH_URL + "?" + urllib.parse.urlencode({
        "q": str(question or ""),
        "page_size": int(page_size),
    })


def build_dtic_fallback_url(question: str) -> str:
    """[AUDIT-20260909 SSRF-S1] Pure URL builder — question được urlencode.
    Host cố định discover.dtic.mil."""
    return _DTIC_FALLBACK_URL + "?" + urllib.parse.urlencode({"q": str(question or "")})


class DTICDataSource(IDataSource):
    """DTIC API — defense research, doctrine, technical reports (no key)."""

    BASE_URL = "https://apps.dtic.mil"
    SEARCH_URL = "https://apps.dtic.mil/wti/api/search"

    def __init__(self):
        # DTIC public search is free, no key needed
        self.enabled = True
        logger.info("[DTIC] enabled (no API key required)")

    @property
    def name(self) -> str:
        return "dtic"

    @property
    def priority(self) -> int:
        return 18  # mid-low — military niche; cross-check MilitaryDataSource

    @property
    def ttl(self) -> int:
        return 86400  # 24h — doctrine/research doesn't change fast

    def get_supported_intents(self) -> list[str]:
        return ["military", "defense", "doctrine", "defense_research",
                "military_research", "dtic", "national_security"]

    def can_handle(self, intent: str, entity: Optional[str] = None) -> bool:
        if not self.enabled:
            return False
        if intent in self.get_supported_intents():
            return True
        question = intent or ""
        q = question.lower()
        keywords = [
            "military", "quân sự",
            "defense", "quốc phòng",
            "doctrine", "học thuyết quân sự",
            "tactical", "chiến thuật",
            "strategic", "chiến lược quân sự",
            "nato", "pentagon",
            "warfare", "chiến tranh",
            "army", "navy", "air force",
            "lục quân", "hải quân", "không quân",
            "dtic", "national defense",
        ]
        return any(k in q for k in keywords)

    def fetch(self, intent: str, entity: str, **kwargs) -> dict | None:
        """IDataSource interface — delegate to query()."""
        return self.query(entity or intent)

    def health_check(self) -> bool:
        return self.enabled

    def query(self, question: str) -> dict | None:
        if not self.enabled:
            return None
        try:
            return self._search(question)
        except Exception as e:
            logger.debug(f"[DTIC] query failed: {e}", exc_info=True)
            return None

    def _search(self, question: str) -> dict | None:
        try:
            headers = {"User-Agent": "SCP-Verifier/1.0 (educational)",
                       "Accept": "application/json"}
            # [AUDIT-20260909 SSRF-S1] builder urlencode question rồi fetch
            # qua safe_urlopen thay raw httpx.get.
            url = build_dtic_search_url(question, page_size=5)
            req = urllib.request.Request(url, headers=headers)  # noqa: S310 — validated by safe_urlopen
            try:
                with safe_urlopen(req, timeout=10) as r:
                    data = json.loads(r.read().decode("utf-8", errors="replace"))
            except urllib.error.HTTPError as he:
                logger.debug(f"[DTIC] search returned {he.code}")
                # Fall back to DTIC public search page (HTML) — giữ behavior cũ
                return self._fallback_search_page(question)
            results = data.get("results") or data.get("items") or []
            if not results:
                return None
            top = results[0]
            return {
                "value": top.get("title", ""),
                "source": "dtic",
                "metadata": {
                    "title": top.get("title", ""),
                    "authors": top.get("authors", []),
                    "date": top.get("date") or top.get("publication_date", ""),
                    "abstract": (top.get("abstract") or "")[:300],
                    "dtic_id": top.get("id") or top.get("document_id", ""),
                    "url": top.get("url") or "",
                    "result_count": data.get("count", len(results)),
                },
            }
        except Exception as e:
            logger.debug(f"[DTIC] search failed: {e}", exc_info=True)
            return None

    def _fallback_search_page(self, question: str) -> dict | None:
        """Fallback: hit DTIC public search HTML page (no JSON API)."""
        try:
            # [AUDIT-20260909 SSRF-S1] builder urlencode + safe_urlopen.
            url = build_dtic_fallback_url(question)
            req = urllib.request.Request(
                url, headers={"User-Agent": "SCP-Verifier/1.0 (educational)"}
            )  # noqa: S310 — validated by safe_urlopen
            with safe_urlopen(req, timeout=10) as r:
                body = r.read()
            return {
                "value": question,
                "source": "dtic",
                "metadata": {
                    "url": url,
                    "fallback": True,
                    "page_size": len(body),
                },
            }
        except Exception as e:
            logger.debug(f"[DTIC] fallback search failed: {e}", exc_info=True)
            return None
