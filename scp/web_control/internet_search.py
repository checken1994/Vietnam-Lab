"""Public Internet search for SCP.

This module performs retrieval only. Search results are untrusted data and are
never treated as executable instructions or as executable instructions.
"""
from __future__ import annotations

import logging
import time
from html.parser import HTMLParser
from typing import Any
from urllib.parse import quote_plus, urlparse

import httpx

from scp.security.url_safety import enforce_egress_policy  # [EE-G1]

logger = logging.getLogger(__name__)


class _SearchParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[dict[str, str]] = []
        self.stack: list[tuple[str, str]] = []
        self.current: dict[str, str] | None = None
        self.capture: str | None = None
        self.buffer: list[str] = []

    @staticmethod
    def _classes(attrs: list[tuple[str, str | None]]) -> str:
        return (dict(attrs).get("class") or "").lower()

    @staticmethod
    def _attrs(attrs: list[tuple[str, str | None]]) -> dict[str, str]:
        return {key: value or "" for key, value in attrs}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        classes = self._classes(attrs)
        values = self._attrs(attrs)
        self.stack.append((tag, classes))
        if "result__a" in classes:
            self.current = {"title": "", "url": values.get("href", ""), "snippet": "", "provider": "duckduckgo"}
            self.capture = "title"
            self.buffer = []
        elif tag == "a" and any("b_algo" in parent_classes for _, parent_classes in self.stack[:-1]):
            self.current = {"title": "", "url": values.get("href", ""), "snippet": "", "provider": "bing"}
            self.capture = "title"
            self.buffer = []
        elif self.current and ("result__snippet" in classes or (tag == "p" and any("b_algo" in c for _, c in self.stack))):
            self.capture = "snippet"
            self.buffer = []

    def handle_data(self, data: str) -> None:
        if self.capture:
            self.buffer.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self.capture and self.stack and self.stack[-1][0] == tag:
            text = " ".join("".join(self.buffer).split())
            if self.current is not None:
                self.current[self.capture] = text
            self.capture = None
            self.buffer = []
        if self.stack:
            self.stack.pop()
        if tag == "a" and self.current and self.current.get("title"):
            self.results.append(self.current)
            self.current = None


class InternetSearch:
    """Search public web indexes without requiring an AI login or API key."""

    def __init__(self, timeout: float = 20.0) -> None:
        self.timeout = timeout
        self.user_agent = "SCP-DNA-InternetSearch/3.2"

    @staticmethod
    def _clean_url(value: str) -> str:
        value = (value or "").strip()
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return ""
        return value

    @staticmethod
    def _dedupe(results: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
        seen: set[str] = set()
        output: list[dict[str, Any]] = []
        for item in results:
            url = item.get("url", "")
            if not url or url in seen:
                continue
            item["url"] = url
            seen.add(url)
            output.append(item)
            if len(output) >= limit:
                break
        return output

    def _parse(self, html: str, provider: str, limit: int) -> list[dict[str, Any]]:
        parser = _SearchParser()
        parser.feed(html)
        results: list[dict[str, Any]] = []
        for item in parser.results:
            if item.get("provider") != provider:
                continue
            item["url"] = self._clean_url(item.get("url", ""))
            if item["url"]:
                results.append(item)
        return self._dedupe(results, limit)

    async def search(self, query: str, max_results: int = 10) -> dict[str, Any]:
        query = query.strip()
        if not query:
            return {"success": False, "error": "Search query is empty", "results": []}
        max_results = max(1, min(int(max_results), 20))
        headers = {"User-Agent": self.user_agent, "Accept-Language": "vi,en;q=0.8"}
        errors: list[dict[str, str]] = []
        all_results: list[dict[str, Any]] = []
        async with httpx.AsyncClient(follow_redirects=True, timeout=self.timeout, headers=headers) as client:
            providers = [
                ("duckduckgo", f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"),
                ("bing", f"https://www.bing.com/search?q={quote_plus(query)}"),
            ]
            for name, url in providers:
                try:
                    # [EE-G1] đọc SCP_EGRESS_MODE trước mỗi provider fetch;
                    # denial → except dưới → errors[] (graceful như provider lỗi).
                    enforce_egress_policy(url)
                    response = await client.get(url)
                    response.raise_for_status()
                    all_results.extend(self._parse(response.text, name, max_results))
                except Exception as exc:
                    logger.debug("internet_search: provider %s failed: %s", name, exc, exc_info=True)
                    errors.append({"provider": name, "error": str(exc)})

        results = self._dedupe(all_results, max_results)
        return {
            "success": bool(results),
            "query": query,
            "results": results,
            "providersTried": ["duckduckgo", "bing"],
            "errors": errors,
            "untrustedData": True,
            "method": "public-search",
            "timestamp": time.time(),
        }
