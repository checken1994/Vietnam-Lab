# SCP CIRCUIT: M02 — Pipeline Pattern Refactor
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from scp.api_server_parts.pipeline.base import PipelineStage
from scp.api_server_parts.pipeline.context import AskPipelineContext
import scp.runtime.judge as judge_mod
import scp.runtime.question_router as question_router
import scp.web_control.internet_search as internet_search_mod

logger = logging.getLogger(__name__)

_FACT_CHECK_KEYWORDS = (
    "true or false",
    "fact check",
    "is it true",
    "fact-check",
    "có thật",
    "đúng không",
    "có thật không",
    "kiểm chứng",
    "real or fake",
    "verify this claim",
)


class LookupStage(PipelineStage):
    """Stage 2: Question routing, chat memory store retrieval, time-signal web lookup,

    autonomous evidence retrieval, and pre-judge multi-source fact checking.
    """

    name: str = "lookup"

    async def execute(self, ctx: AskPipelineContext) -> None:
        # 1. Chat memory store retrieval
        _history: list[dict[str, str]] = []
        if ctx.req.conversation_history:
            _history = list(ctx.req.conversation_history)
        if not _history and ctx.req.session_id:
            try:
                from scp.core.chat_memory import get_chat_memory_store

                _mem_store = get_chat_memory_store()
                _loaded = await asyncio.to_thread(_mem_store.load, ctx.req.session_id, limit=8)
                if _loaded:
                    _history = [{"role": m.get("role", "user"), "content": m.get("content", "")} for m in _loaded]
            except Exception as _mem_load_err:
                logger.warning(
                    f"[CHATBOT] Failed to load chat memory for session {ctx.req.session_id}: {_mem_load_err}",
                    exc_info=True,
                )
        ctx.history = _history

        # 2. Question routing (dynamic lookup to support monkeypatching)
        route_fn = ctx.resolve("route_question", None) or question_router.route_question
        _route_decision = route_fn(ctx.req.question)
        ctx.route_decision = _route_decision
        ctx.is_chatbot_lane = (_route_decision.lane == question_router.LANE_CHATBOT)

        # 3. Time-signal pre-generation web lookup (W14)
        _w14_time_signal = ctx.resolve("question_has_time_signal", None) or judge_mod.question_has_time_signal
        _pre_gen_evidence: list[str] = []
        if (
            _route_decision.lane == question_router.LANE_FACTUAL
            and _w14_time_signal(str(ctx.req.question or ""))
            and os.environ.get("SCP_WEB_FALLBACK", "1") == "1"
        ):
            try:
                _pre_timeout = min(float(os.environ.get("SCP_WEB_FALLBACK_TIMEOUT", "8")), 12.0)
                search_cls = ctx.resolve("InternetSearch", None) or internet_search_mod.InternetSearch
                _pre_search = search_cls(timeout=min(_pre_timeout / 2.0, 4.0))
                _pre_res = await asyncio.wait_for(
                    _pre_search.search(ctx.req.question, max_results=6), timeout=_pre_timeout
                )
                if _pre_res.get("success"):
                    from scp.core.top_systems_learning import inspect_untrusted as _pre_inspect

                    _pre_bullets: list[str] = []
                    for _item in _pre_res.get("results", [])[:6]:
                        _title = str(_item.get("title", "")).strip()
                        _snippet = str(_item.get("snippet", "")).strip()
                        _url = str(_item.get("url", "")).strip()
                        if not _title and not _snippet:
                            continue
                        _line = f"- {_title}: {_snippet} ({_url})"
                        _quarantined, _q_reason = _pre_inspect(_line)
                        if _quarantined:
                            logger.warning("[W14] pre-gen evidence quarantined (injection): %s", _q_reason)
                            continue
                        _pre_bullets.append(_line)
                    if _pre_bullets:
                        _pre_gen_evidence = _pre_bullets
                        ctx.web_fallback_used = True
                        ctx.web_fallback = {**_pre_res, "trigger": "pre_gen_time_signal"}
                        ctx.v98_context["web_fallback"] = ctx.web_fallback
                    else:
                        logger.info(
                            "[W14] pre-gen web evidence: all snippets quarantined — gen without evidence (fail-closed)"
                        )
            except Exception as _pre_err:
                logger.warning("[W14] pre-gen web evidence failed: %s", _pre_err, exc_info=True)

        ctx.pre_gen_evidence = _pre_gen_evidence

        # 4. Autonomous Evidence Retrieval
        _retrieval_res: dict[str, Any] = {}
        ctx.has_provided_evidence = bool(
            ctx.req.contexts
            or (getattr(ctx.req, "retrieved_context", None) and str(ctx.req.retrieved_context).strip())
        )
        try:
            if _route_decision.lane == question_router.LANE_FACTUAL and (not ctx.has_provided_evidence or ctx.req.confidence < 0.7):
                from scp.knowledge.domain_knowledge import AutonomousEvidenceRetriever

                _retriever = AutonomousEvidenceRetriever()
                _retrieval_res = await _retriever.retrieve(
                    question=ctx.req.question,
                    current_confidence=ctx.req.confidence,
                    domain=getattr(ctx.req, "domain", "general"),
                    allow_web=bool(os.environ.get("SCP_WEB_FALLBACK", "1") == "1"),
                )
                if _retrieval_res.get("retrieval_triggered"):
                    if _retrieval_res.get("web_search_hits") and not ctx.web_fallback_used:
                        ctx.web_fallback_used = True
                        ctx.web_fallback = {
                            "success": True,
                            "results": _retrieval_res.get("web_search_hits", []),
                            "method": "public-search",
                        }
                        ctx.v98_context["web_fallback"] = ctx.web_fallback
        except Exception as _ar_err:
            logger.warning("[LookupStage] Autonomous retrieval error: %s", _ar_err, exc_info=True)

        ctx.retrieval_res = _retrieval_res

        # 5. Pre-judge multi-source fact check
        _q_lower = ctx.req.question.lower() if ctx.req.question else ""
        _fact_check_degraded = False
        _fact_check_note = ""

        if any(kw in _q_lower for kw in _FACT_CHECK_KEYWORDS):
            try:
                from scp.core.multi_source_verifier import AsyncMultiSourceVerifier
                from scp.data_sources import get_registry

                _fc_sources = []
                try:
                    _registry = get_registry()
                    _candidates = []
                    try:
                        _candidates = _registry.get_sources_for_intent("fact_check") or []
                    except Exception:
                        logger.warning("LookupStage: Exception not handled", exc_info=True)
                        _candidates = list(getattr(_registry, "_sources", {}).values())
                    for _src in _candidates:
                        try:
                            if _src.can_handle("fact_check"):
                                _fc_sources.append(_src)
                        except Exception:
                            logger.warning("LookupStage: Exception not handled", exc_info=True)
                            continue
                except Exception as _reg_err:
                    logger.debug(f"[OPT-22] registry lookup failed: {_reg_err}", exc_info=True)

                async_verifier = AsyncMultiSourceVerifier()
                fact_result = await async_verifier.verify_async(ctx.req.question, sources=_fc_sources or None)
                _extra_verified = 0
                _extra_contradicted = 0
                for _raw in fact_result.get("results", []) or []:
                    try:
                        _meta = _raw.get("metadata", {}) if isinstance(_raw, dict) else {}
                        _verdict = str(_meta.get("consensus", "")).upper() if _meta else ""
                        if _verdict == "FALSE":
                            _extra_contradicted += 1
                        elif _verdict == "TRUE":
                            _extra_verified += 1
                    except Exception:
                        logger.warning("LookupStage: Exception not handled", exc_info=True)
                        continue
                if _extra_verified or _extra_contradicted:
                    fact_result["verified"] = fact_result.get("verified", 0) + _extra_verified
                    fact_result["contradicted"] = fact_result.get("contradicted", 0) + _extra_contradicted
                    if fact_result["contradicted"] > fact_result["verified"]:
                        fact_result["consensus"] = "contradicted"
                    elif fact_result["verified"] > fact_result["contradicted"]:
                        fact_result["consensus"] = "verified"
                if fact_result.get("consensus") == "contradicted":
                    logger.info(
                        f"[OPT-22] Pre-judge fact check: claim contradicted by {fact_result.get('contradicted', 0)} sources (sources_checked={fact_result.get('sources_checked', 0)})"
                    )
                    ctx.v98_context["fact_check_hint"] = fact_result
                elif fact_result.get("consensus") == "verified":
                    logger.info(
                        f"[OPT-22] Pre-judge fact check: claim verified by {fact_result.get('verified', 0)} sources (sources_checked={fact_result.get('sources_checked', 0)})"
                    )
                    ctx.v98_context["fact_check_hint"] = fact_result
            except Exception as e:
                _fact_check_degraded = True
                _fact_check_note = f"fact_check_error:{type(e).__name__}"
                logger.warning(f"[OPT-22] async fact check failed: {e}", exc_info=True)

        ctx.fact_check_degraded = _fact_check_degraded
        ctx.fact_check_note = _fact_check_note
