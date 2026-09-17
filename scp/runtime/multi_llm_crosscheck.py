"""Cross-provider semantic verification.

A semantic consensus is only accepted when two distinct provider families
produce parseable verdicts for the same prompt. Task labels or model names are
not treated as evidence of independence.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("scp.runtime.multi_llm_crosscheck")


def _provider_lifecycle_eligible(gateway: Any, provider: Any) -> bool:
    """Apply the gateway's durable local-model lifecycle authority, if present.

    ``LLMGateway._provider_eligible`` is the canonical S35 choke point.  The
    fallback below supports gateway implementations that expose the same
    durable discovery authority without duplicating provider-name rules.  A
    gateway with no lifecycle authority is intentionally left unchanged so
    unknown cloud providers retain their existing behavior.
    """
    endpoint = getattr(provider, "base_url", "")
    model = getattr(provider, "model", "")
    if not endpoint and not model:
        # Some injected/cloud-compatible providers intentionally expose only
        # the chat seam.  They are not local registry entries, so preserve the
        # historical enabled-only behavior rather than treating missing
        # lifecycle metadata as a local denial.
        return True

    checker = getattr(gateway, "_provider_eligible", None)
    if callable(checker):
        return bool(checker(provider))

    discovery_store = getattr(gateway, "discovery_store", None)
    configured_endpoints = getattr(gateway, "_discovery_endpoints", None)
    if discovery_store is None and configured_endpoints is None:
        return True

    from scp.llm_gateway.discovery import is_provider_model_eligible

    return is_provider_model_eligible(
        discovery_store,
        getattr(provider, "base_url", ""),
        getattr(provider, "model", ""),
        configured_local_endpoints=list(configured_endpoints or []),
    )


def _candidate_providers(gateway: Any) -> list[Any]:
    """Return enabled, lifecycle-eligible judge candidates."""
    public = getattr(gateway, "provider_candidates", None)
    if callable(public):
        candidates = list(public("judge") or [])
    else:
        internal = getattr(gateway, "_provider_chain", None)
        candidates = list(internal("judge") or []) if callable(internal) else []

    eligible: list[Any] = []
    for provider in candidates:
        try:
            if not bool(getattr(provider, "enabled", False)):
                continue
            if not _provider_lifecycle_eligible(gateway, provider):
                logger.info(
                    "[MULTI-LLM] provider excluded by durable lifecycle authority: %s",
                    getattr(provider, "PROVIDER_NAME", "unknown"),
                )
                continue
            eligible.append(provider)
        except Exception as exc:
            logger.warning(
                "[MULTI-LLM] provider readiness/lifecycle check failed (%s): %s",
                getattr(provider, "PROVIDER_NAME", "unknown"),
                type(exc).__name__,
            )
    return eligible


def _missing_opinion() -> dict[str, Any]:
    """Explicit unresolved opinion; never expose a shape that implies success."""
    return {"family": "", "provider": "none", "verdict": None}


async def cross_verify(
    question: str,
    ai_answer: str,
    context: str = "",
    verdict_tier1: bool = True,
    *,
    gateway: Any | None = None,
) -> dict[str, Any]:
    """Verify one answer using two genuinely distinct provider families.

    Returns ``final=PASS|FAIL`` only when two different provider families
    return parseable and equal verdicts. Missing diversity, provider failure,
    ambiguous output, or disagreement all fail closed with ``final=None``.

    ``gateway`` is injectable so the independence rule can be tested without
    external network access; production callers continue to use get_gateway().
    ``verdict_tier1`` is retained for backward compatibility.
    """
    del verdict_tier1

    from scp.runtime.judge_llm import _parse_verdict

    if gateway is None:
        from scp.llm_gateway import get_gateway

        gateway = get_gateway()

    prompt = (
        f"Question: {question}\nContext: {context}\nAI Answer: {ai_answer}\n"
        "Evaluate if the AI Answer correctly answers the Question based ONLY on "
        "the Context (if provided) or general knowledge. Output only PASS or FAIL."
    )
    system = (
        "You are a factual judge. You MUST output exactly the word PASS or FAIL "
        "and nothing else."
    )

    seen_families: set[str] = set()
    attempts: list[dict[str, Any]] = []
    valid: list[dict[str, Any]] = []

    for provider in _candidate_providers(gateway):
        family = str(getattr(provider, "PROVIDER_NAME", "")).strip().lower()
        if not family or family in seen_families:
            continue
        # Mark before the request: a second instance of the same family is not
        # an independent opinion, even when the first instance errors.
        seen_families.add(family)

        try:
            content, provider_label = await provider.chat(
                prompt,
                system_prompt=system,
            )
            verdict = _parse_verdict(content)
            attempt = {
                "family": family,
                "provider": provider_label,
                "verdict": verdict,
            }
        except Exception as exc:
            # silent-by-design: per-attempt error recorded in the attempt record ('provider': error:<Exc>) returned to the caller
            attempt = {
                "family": family,
                "provider": f"error:{type(exc).__name__}",
                "verdict": None,
            }

        attempts.append(attempt)
        if attempt["verdict"] in {"PASS", "FAIL"}:
            valid.append(attempt)
            if len(valid) == 2:
                break

    primary = valid[0] if valid else _missing_opinion()
    secondary = valid[1] if len(valid) > 1 else _missing_opinion()

    if len(valid) < 2:
        consensus = "missing_distinct_providers"
        final = None
    elif primary["verdict"] == secondary["verdict"]:
        consensus = "agree"
        final = primary["verdict"]
    else:
        consensus = "disagree"
        final = None

    logger.info(
        "[MULTI-LLM] primary(%s)=%s secondary(%s)=%s consensus=%s final=%s",
        primary["provider"],
        primary["verdict"],
        secondary["provider"],
        secondary["verdict"],
        consensus,
        final,
    )
    return {
        "consensus": consensus,
        "primary": primary,
        "secondary": secondary,
        "final": final,
        "attempts": attempts,
        "distinct_families_attempted": [item["family"] for item in attempts],
    }
