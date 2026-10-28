"""LLM CHECKER client (OpenAI-compatible, GLM/Z.ai mặc định).

Biên giới network — không chạy trong unit test (test inject transport giả).
Không bao giờ log key; timeout cứng theo policy.
"""
from __future__ import annotations

import json
import urllib.request


def chat(base_url: str, api_key: str, model: str, prompt: str,
         timeout: int = 240) -> str:
    """Một call chat, trả text. Mọi lỗi ném exception — caller fail-closed."""
    req = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
        }).encode(),
        headers={"Authorization": f"Bearer {api_key}",
                 "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = json.loads(resp.read().decode())
    return body["choices"][0]["message"]["content"] or ""
