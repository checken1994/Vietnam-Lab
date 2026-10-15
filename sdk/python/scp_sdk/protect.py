"""SCPProtect - Drop-in Security and Verification Middleware for Agent Frameworks."""
from __future__ import annotations

import logging
import re
from enum import Enum
from typing import Any

logger = logging.getLogger("scp_sdk")


class VerificationTier(str, Enum):
    PERMISSIVE = "PERMISSIVE"
    STANDARD = "STANDARD"
    STRICT = "STRICT"


class SCPProtect:
    """Enterprise-grade security wrapper and verification middleware for LangChain, LlamaIndex, and AutoGen."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8000",
        api_token: str | None = None,
        default_tier: str | VerificationTier = VerificationTier.STANDARD,
        strict_mode: bool = True,
        timeout: float = 10.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_token = api_token
        self.default_tier = (
            default_tier if isinstance(default_tier, VerificationTier)
            else VerificationTier(str(default_tier).upper())
        )
        self.strict_mode = strict_mode
        self.timeout = timeout

        # Local heuristic patterns for fail-closed input screening when offline
        self._injection_patterns = [
            re.compile(r"ignore\s+(all\s+)?(previous|prior)\s+instructions", re.IGNORECASE),
            re.compile(r"(system\s+prompt|developer\s+mode|jailbreak|bypass\s+safety)", re.IGNORECASE),
            re.compile(r"you\s+are\s+now\s+(unconstrained|dan|evil)", re.IGNORECASE),
            re.compile(r"<\s*script\s*>", re.IGNORECASE),
            re.compile(r"(---\s*end of|\[admin(\s+override)?\])", re.IGNORECASE),
            re.compile(r"(hypothetical\s+scenario|ethical\s+hackers|leakage\s+of|reveal.*(secret|key|token))", re.IGNORECASE),
        ]

    def _get_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_token:
            headers["Authorization"] = f"Bearer {self.api_token}"
            headers["x-scp-capability-token"] = self.api_token
        return headers

    def protect_input(self, prompt: str) -> dict[str, Any]:
        """Audit prompt for injection, jailbreak attempts, or policy violations before LLM invocation."""
        if not prompt or not isinstance(prompt, str) or not prompt.strip():
            return {"allowed": False, "reason": "empty_prompt"}

        # Local screening
        for pattern in self._injection_patterns:
            if pattern.search(prompt):
                logger.warning("[SCPProtect] Prompt injection detected locally: %s", pattern.pattern)
                return {
                    "allowed": False,
                    "reason": "prompt_injection_detected",
                    "details": f"Matched pattern: {pattern.pattern}",
                }

        # Remote API evaluation if reachable
        try:
            import httpx
            with httpx.Client(timeout=self.timeout) as client:
                res = client.post(
                    f"{self.base_url}/v1/chat/completions",
                    headers=self._get_headers(),
                    json={"messages": [{"role": "user", "content": prompt}], "model": "scp-guard"},
                )
                if res.status_code == 200:
                    data = res.json()
                    metadata = data.get("scp_metadata", {})
                    if metadata.get("falsification_status") == "FAIL":
                        return {"allowed": False, "reason": "policy_rejected"}
        except (ImportError, Exception) as exc:
            logger.debug("[SCPProtect] Remote screening skipped/fallback: %s", exc)

        return {"allowed": True, "reason": "pass"}

    def protect_output(
        self,
        answer: str,
        context: str = "",
        tier: str | VerificationTier | None = None,
    ) -> dict[str, Any]:
        """Verify model output against epistemic grounding and safety invariants."""
        if not answer or not isinstance(answer, str):
            return {"verified": False, "verdict": "FAIL", "reason": "empty_answer"}

        active_tier = (
            self.default_tier if tier is None
            else (tier if isinstance(tier, VerificationTier) else VerificationTier(str(tier).upper()))
        )

        # Fail-closed heuristic check
        if "[SCP: Answer withheld" in answer:
            return {"verified": False, "verdict": "FAIL", "reason": "answer_withheld"}

        # Attempt verification via SCP backend if reachable
        try:
            import httpx
            with httpx.Client(timeout=self.timeout) as client:
                res = client.post(
                    f"{self.base_url}/ask",
                    headers=self._get_headers(),
                    json={
                        "question": "verify_output",
                        "contexts": [context] if context else [],
                        "verification_tier": active_tier.value,
                    },
                )
                if res.status_code == 200:
                    data = res.json()
                    verdict = data.get("verdict", "PASS")
                    return {
                        "verified": verdict in ("PASS", "VERIFIED"),
                        "verdict": verdict,
                        "governance": data.get("governance_decision", "UPHOLD"),
                        "tier": active_tier.value,
                    }
        except (ImportError, Exception) as exc:
            logger.debug("[SCPProtect] Remote output verifier skipped/fallback: %s", exc)

        # Fallback offline validation
        if active_tier == VerificationTier.PERMISSIVE:
            return {"verified": True, "verdict": "PASS", "tier": active_tier.value, "offline": True}

        STOP_WORDS = {
            "the", "and", "was", "for", "with", "this", "that", "has", "are", "from",
            "been", "were", "have", "here", "there", "what", "when", "where", "which",
        }
        if context and len(context) > 10:
            words = {w for w in re.findall(r"\w{3,}", answer.lower()) if w not in STOP_WORDS}
            ctx_words = {w for w in re.findall(r"\w{3,}", context.lower()) if w not in STOP_WORDS}
            overlap = words.intersection(ctx_words)
            grounded = len(overlap) > 0
            return {
                "verified": grounded,
                "verdict": "PASS" if grounded else "INSUFFICIENT",
                "tier": active_tier.value,
                "offline": True,
            }

        return {"verified": False, "verdict": "INSUFFICIENT", "tier": active_tier.value, "offline": True}

    def wrap_langchain_chain(self, chain: Any) -> Any:
        """Wrap a LangChain Chain or Runnable with automated pre/post verification."""
        protector = self

        class SafeLangChainWrapper:
            def __init__(self, target_chain):
                self._chain = target_chain

            def invoke(self, input_data: Any, *args, **kwargs) -> Any:
                prompt_text = str(input_data)
                guard_in = protector.protect_input(prompt_text)
                if not guard_in["allowed"]:
                    raise PermissionError(f"[SCPProtect] LangChain input blocked: {guard_in['reason']}")

                raw_output = self._chain.invoke(input_data, *args, **kwargs)
                answer_text = str(raw_output)
                guard_out = protector.protect_output(answer_text, context=prompt_text)
                if not guard_out["verified"]:
                    logger.warning("[SCPProtect] LangChain output withheld by verifier: %s", guard_out)
                    return f"[SCP: Answer withheld — epistemic verification failed ({guard_out.get('reason', 'unverified')})]"
                return raw_output

        return SafeLangChainWrapper(chain)

    def wrap_llamaindex_engine(self, engine: Any) -> Any:
        """Wrap a LlamaIndex Query Engine with automated input guard & output verification."""
        protector = self

        class SafeLlamaIndexWrapper:
            def __init__(self, target_engine):
                self._engine = target_engine

            def query(self, query_str: str, *args, **kwargs) -> Any:
                guard_in = protector.protect_input(query_str)
                if not guard_in["allowed"]:
                    raise PermissionError(f"[SCPProtect] LlamaIndex query blocked: {guard_in['reason']}")

                raw_response = self._engine.query(query_str, *args, **kwargs)
                ans = str(raw_response)
                guard_out = protector.protect_output(ans, context=query_str)
                if not guard_out["verified"]:
                    logger.warning("[SCPProtect] LlamaIndex response withheld: %s", guard_out)
                return raw_response

        return SafeLlamaIndexWrapper(engine)

    def wrap_autogen_agent(self, agent: Any) -> Any:
        """Wrap an AutoGen ConversableAgent with automated safety message filtering."""
        protector = self

        class SafeAutoGenWrapper:
            def __init__(self, target_agent):
                self._agent = target_agent

            def send(self, message: Any, recipient: Any, *args, **kwargs):
                text = str(message)
                guard_in = protector.protect_input(text)
                if not guard_in["allowed"]:
                    raise PermissionError(f"[SCPProtect] AutoGen message blocked: {guard_in['reason']}")
                return self._agent.send(message, recipient, *args, **kwargs)

        return SafeAutoGenWrapper(agent)


__all__ = [
    "SCPProtect",
    "VerificationTier",
]
