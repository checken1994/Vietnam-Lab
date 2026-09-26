"""
[OPT-4] NewsAPIDataSource — news headlines for cross-checking RealLearning.
DNA SCP: RealLearning uses news headlines but stores them without verification.
NewsAPI adds structured news data with source attribution + timestamp.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request

from scp.interfaces.data_source import IDataSource
from scp.security.url_safety import safe_urlopen  # [AUDIT-20260909 SSRF-S1]
from typing import Optional

logger = logging.getLogger("scp.data_sources.newsapi")

# [AUDIT-20260909 SSRF-S1] Host cố định — literal duy nhất của builder.
_NEWSAPI_EVERYTHING_URL = "https://newsapi.org/v2/everything"


def build_newsapi_everything_url(question: str, api_key: str,
                                 page_size: int = 5) -> str:
    """[AUDIT-20260909 SSRF-S1] Pure URL builder — question + api_key được
    urlencode thành query values; host cố định newsapi.org."""
    return _NEWSAPI_EVERYTHING_URL + "?" + urllib.parse.urlencode({
        "q": str(question or "")[:100],
        "sortBy": "publishedAt",
        "pageSize": int(page_size),
        "language": "en",
        "apiKey": api_key,
    })


class NewsAPIDataSource(IDataSource):
    """NewsAPI.org — structured news headlines (free tier: 100 requests/day)."""

    BASE_URL = "https://newsapi.org/v2"

    def __init__(self):
        self.api_key = os.environ.get("NEWSAPI_API_KEY", "")
        self.enabled = bool(self.api_key)
        if not self.enabled:
            logger.info("[NewsAPI] disabled — set NEWSAPI_API_KEY to enable")

    @property
    def name(self) -> str:
        return "newsapi"

    @property
    def priority(self) -> int:
        return 15  # lower priority — supplementary source

    @property
    def ttl(self) -> int:
        return 1800  # 30min cache (news changes fast)

    def get_supported_intents(self) -> list[str]:
        return ["news", "headline", "current_event", "breaking"]

    def can_handle(self, intent: str, entity: Optional[str] = None) -> bool:
        if not self.enabled:
            return False
        if intent in self.get_supported_intents():
            return True
        # Question-based (legacy compat)
        question = intent or ""
        q = question.lower()
        keywords = ["news", "tin tức", "headline", "breaking", "latest", "mới nhất",
                    "happen", "xảy ra", "current event", "sự kiện"]
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
            # [AUDIT-20260909 SSRF-S1] builder urlencode + safe_urlopen thay
            # raw httpx.get; non-200 → HTTPError.
            req = urllib.request.Request(
                build_newsapi_everything_url(question, api_key=self.api_key)
            )  # noqa: S310 — validated by safe_urlopen
            with safe_urlopen(req, timeout=10) as r:
                data = json.loads(r.read().decode("utf-8", errors="replace"))
            articles = data.get("articles", [])
            if articles:
                headlines = [
                    {
                        "title": a.get("title", ""),
                        "source": a.get("source", {}).get("name", ""),
                        "published": a.get("publishedAt", ""),
                        "url": a.get("url", ""),
                    }
                    for a in articles[:5]
                ]
                return {
                    "value": headlines[0]["title"] if headlines else "",
                    "source": "newsapi",
                    "metadata": {"headlines": headlines, "count": len(headlines)},
                }
        except Exception as e:
            logger.debug(f"[NewsAPI] query failed: {e}", exc_info=True)
        return None
