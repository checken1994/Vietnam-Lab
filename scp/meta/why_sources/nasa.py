# SCP CIRCUIT: M12 — STATUS: CLOSED_WITH_KNOWN_GAP (closure: docs/evidence-summary/M12-closure.json)
"""
[Task 8-A] NASA APOD source handler — extracted from why_engine.py

TẠI SAO: WhyEngine._query_nasa() was 13 LOC inline. Extracted as standalone
function for modularity. Backward-compatible — WhyEngine delegates.
"""
from __future__ import annotations

import json as _json
import logging
import os
import urllib.request

from scp.core.api_utils import redact_query_secrets  # [AUDIT-FIX low-8]
from scp.security.url_safety import safe_urlopen  # noqa: B310

logger = logging.getLogger("scp.why.sources.nasa")

# [GLM-AUDIT-FIX] Read NASA API key from environment; DEMO_KEY is public fallback
_NASA_API_KEY = os.environ.get("NASA_API_KEY", "DEMO_KEY")


def query_nasa(target: str) -> str | None:
    """Query NASA APOD."""
    try:
        # [M12-FIX PF-5] Endpoint seam (same trust level as OPENAI_BASE_URL):
        # SCP_WHY_NASA_BASE redirects the API host (default unchanged:
        # https://api.nasa.gov). When overridden, loopback/private targets are
        # explicitly allowed; default path keeps SSRF protection.
        _base_override = os.environ.get("SCP_WHY_NASA_BASE")
        _nasa_base = _base_override or "https://api.nasa.gov"
        url = f"{_nasa_base}/planetary/apod?api_key={_NASA_API_KEY}"
        req = urllib.request.Request(url, headers={"User-Agent": "SCP-WHY/1.0"})
        with safe_urlopen(req, timeout=8, allow_internal=bool(_base_override)) as resp:
            data = _json.loads(resp.read().decode('utf-8'))
            return data.get("title", "") + ": " + data.get("explanation", "")[:100]
        return None
    except Exception as e:  # [RC-7 FIX Task 6-B] silent swallow → log context
        # [AUDIT-FIX low-8] URL chứa api_key → exception (vd: ValueError
        # "unparseable URL ..." từ url_safety) có thể nhúng cả URL vào message.
        # Mọi text chạm log phải qua redact_query_secrets.
        logger.warning(
            "[why_sources.nasa] failed for target='%s': %s",
            target, redact_query_secrets(str(e)),
        exc_info=True)
        return None

