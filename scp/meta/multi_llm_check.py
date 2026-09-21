"""SCP Multi-LLM Cross-Check — combat consensus illusion (DNA #20).

P0 zero-cost migration: every direct provider call is authorized immediately
before urllib opens the network connection. Unknown/paid/stale pricing returns
no answer; it never falls through to a paid provider.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import urllib.error
import urllib.request
from difflib import SequenceMatcher
from typing import Optional

from scp.contracts.data_class import DataClass


# [AUDIT-20260909 S6a] Gọi provider qua safe_urlopen — validate scheme + chặn
# private/loopback IP trừ khi operator chủ động cấu hình base_url nội bộ.
from scp.security.url_safety import safe_urlopen

logger = logging.getLogger("scp.meta.multi_llm_check")

_MAX_CHECKS_PER_HOUR = 30
_recent_checks: list[float] = []
_checks_lock = threading.Lock()


def _check_rate_limit() -> bool:
    with _checks_lock:
        global _recent_checks
        import time
        now = time.time()
        _recent_checks = [t for t in _recent_checks if now - t < 3600]
        if len(_recent_checks) >= _MAX_CHECKS_PER_HOUR:
            return False
        _recent_checks.append(now)
        return True


class MultiLLMChecker:
    def __init__(self, providers: Optional[list[str]] = None):
        self.providers = providers or ["openrouter", "groq"]
        self._lock = threading.Lock()

    def check(self, question: str, primary_answer: str) -> dict:
        if not _check_rate_limit():
            return {
                "consensus": "unavailable",
                "similarity": 0.0,
                "provider_answers": {},
                "speculative": False,
                "reason": "rate limit exceeded",
            }
        provider_answers: dict[str, str | None] = {}
        for provider in self.providers:
            try:
                if provider == "openrouter":
                    answer = self._call_openrouter(question)
                elif provider == "groq":
                    answer = self._call_groq(question)
                else:
                    continue
                provider_answers[provider] = answer
            except Exception as exc:
                logger.warning("[multi_llm_check] %s failed: %s", provider, exc)
                provider_answers[provider] = None
        return self._compare_answers(primary_answer, provider_answers)

    @staticmethod
    def _authorize(provider: str, model: str):
        try:
            return authorize_outbound(
                provider=provider,
                model=model,
                task_class="fact_check",
                data_class=DataClass.INTERNAL,
            )
        except ZeroCostDenied as exc:
            logger.info("[multi_llm_check] zero-cost PEP denied %s/%s: %s", provider, model, exc.decision.value)
            return None

    def _call_openrouter(self, question: str) -> str | None:
        api_key = os.environ.get("OPENROUTER_API_KEY", "")
        if not api_key:
            for i in (2, 3):
                api_key = os.environ.get(f"OPENROUTER_API_KEY_{i}", "")
                if api_key:
                    break
        if not api_key:
            return None
        base_url = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
        model = os.environ.get("OPENROUTER_MODEL", "deepseek/deepseek-v4-flash-0731")
        authorized = self._authorize("openrouter", model)
        if authorized is None:
            return None
        zreq, zproof = authorized
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": "Answer concisely (1-2 sentences). Be factual."},
                {"role": "user", "content": question},
            ],
            "max_tokens": 200,
            "temperature": 0.1,
        }
        try:
            req = urllib.request.Request(
                f"{base_url}/chat/completions",
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://scp-vietnam.local",
                    "X-Title": "SCP MultiLLM Check",
                },
                method="POST",
            )
            record_outbound_sent(zreq, zproof)
            with safe_urlopen(req, timeout=30, allow_internal=True) as resp:  # provider URL operator-governed
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("choices", [{}])[0].get("message", {}).get("content", "")
        except urllib.error.HTTPError as exc:
            logger.warning("[multi_llm_check] OpenRouter HTTP %s", exc.code)
            return None
        except Exception as exc:
            logger.warning("[multi_llm_check] OpenRouter failed: %s", exc)
            return None

    def _call_groq(self, question: str) -> str | None:
        api_key = os.environ.get("GROQ_API_KEY", "")
        if not api_key:
            return None
        base_url = os.environ.get("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
        model = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
        authorized = self._authorize("groq", model)
        if authorized is None:
            return None
        zreq, zproof = authorized
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": "Answer concisely (1-2 sentences). Be factual."},
                {"role": "user", "content": question},
            ],
            "max_tokens": 200,
            "temperature": 0.1,
        }
        try:
            req = urllib.request.Request(
                f"{base_url}/chat/completions",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                method="POST",
            )
            record_outbound_sent(zreq, zproof)
            with safe_urlopen(req, timeout=30, allow_internal=True) as resp:  # provider URL operator-governed
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("choices", [{}])[0].get("message", {}).get("content", "")
        except urllib.error.HTTPError as exc:
            logger.warning("[multi_llm_check] Groq HTTP %s", exc.code)
            return None
        except Exception as exc:
            logger.warning("[multi_llm_check] Groq failed: %s", exc)
            return None

    def _compare_answers(self, primary: str, others: dict[str, str | None]) -> dict:
        valid_others = {k: v for k, v in others.items() if v}
        if not valid_others:
            return {
                "consensus": "unavailable",
                "similarity": 0.0,
                "provider_answers": others,
                "speculative": False,
                "reason": "no provider answers available",
            }
        similarities = [SequenceMatcher(None, primary.lower(), answer.lower()).ratio() for answer in valid_others.values()]
        avg_sim = sum(similarities) / len(similarities)
        if avg_sim >= 0.8:
            consensus, speculative = "agree", False
            reason = f"providers agree (sim={avg_sim:.2f})"
        elif avg_sim < 0.5:
            consensus, speculative = "disagree", True
            reason = f"providers DISAGREE (sim={avg_sim:.2f}) — possible consensus illusion"
        else:
            consensus, speculative = "partial", True
            reason = f"providers partial agree (sim={avg_sim:.2f}) — flag as speculative"
        return {
            "consensus": consensus,
            "similarity": round(avg_sim, 3),
            "provider_answers": others,
            "speculative": speculative,
            "reason": reason,
        }
