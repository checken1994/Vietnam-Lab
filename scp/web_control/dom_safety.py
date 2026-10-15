"""Phase P2: Web Orchestration Safety — Anti-Honeypot DOM Sanitizer & CDP Hard Timeout (INV-03)."""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, Awaitable, Callable

_HIDDEN_STYLE_RE = re.compile(
    r"(?i)(?:display\s*:\s*none|visibility\s*:\s*hidden|opacity\s*:\s*0(?:\.0+)?(?:\s*;|\s*$)|font-size\s*:\s*0(?:px|pt|em|rem)?)"
)
_PROMPT_INJECTION_RE = re.compile(
    r"(?i)(?:ignore\s+(?:all\s+)?previous\s+instructions|phớt\s+lờ\s+lệnh\s+trước|system\s+prompt\s+override|delete\s+all\s+files|xóa\s+hệ\s+thống)"
)
_STRIP_TAGS = {"script", "style", "template", "noscript", "iframe", "svg", "object"}


@dataclass(frozen=True)
class SanitizedDOMResult:
    visible_text: str
    removed_hidden_nodes: int
    honeypot_injection_detected: bool
    honeypot_snippets: tuple[str, ...]


class _AntiHoneypotHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._visible_chunks: list[str] = []
        self._hidden_chunks: list[str] = []
        self.removed_hidden_nodes = 0

    @staticmethod
    def _is_hidden_attrs(tag: str, attrs: list[tuple[str, str | None]]) -> bool:
        if tag.lower() in _STRIP_TAGS:
            return True
        attr_map = {k.lower(): (v or "") for k, v in attrs}
        if "hidden" in attr_map:
            return True
        if attr_map.get("aria-hidden", "").strip().lower() == "true":
            return True
        if attr_map.get("type", "").strip().lower() == "hidden":
            return True
        style = attr_map.get("style", "")
        if style and _HIDDEN_STYLE_RE.search(style):
            return True
        return False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._skip_depth > 0:
            self._skip_depth += 1
            return
        if self._is_hidden_attrs(tag, attrs):
            self._skip_depth = 1
            self.removed_hidden_nodes += 1

    def handle_endtag(self, tag: str) -> None:
        if self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        cleaned = data.strip()
        if not cleaned:
            return
        if self._skip_depth > 0:
            self._hidden_chunks.append(cleaned)
        else:
            self._visible_chunks.append(cleaned)


def sanitize_dom_for_llm(raw_html: str) -> SanitizedDOMResult:
    """Strip invisible DOM nodes, comments, and honeypot traps before passing web content to LLM."""
    parser = _AntiHoneypotHTMLParser()
    parser.feed(raw_html or "")
    parser.close()
    honeypots: list[str] = []
    for chunk in parser._hidden_chunks:
        if _PROMPT_INJECTION_RE.search(chunk):
            honeypots.append(chunk[:200])
    visible_text = " ".join(parser._visible_chunks)
    return SanitizedDOMResult(
        visible_text=visible_text,
        removed_hidden_nodes=parser.removed_hidden_nodes,
        honeypot_injection_detected=bool(honeypots),
        honeypot_snippets=tuple(honeypots),
    )


async def run_cdp_with_hard_timeout(
    operation: Callable[[], Awaitable[Any]],
    *,
    timeout_seconds: float = 15.0,
) -> Any:
    """Enforce mechanical hard timeout (15-30s) on CDP navigate/evaluate calls (scp-web-orchestration-safety)."""
    if timeout_seconds <= 0 or timeout_seconds > 30.0:
        raise ValueError("CDP hard timeout must be between (0, 30.0] seconds")
    return await asyncio.wait_for(operation(), timeout=timeout_seconds)
