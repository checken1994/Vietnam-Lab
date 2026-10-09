# SCP CIRCUIT: M02 — Pipeline Pattern Refactor
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from fastapi import HTTPException

import scp.api_server_parts._async_fact_check as afc_mod
import scp.api_server_parts.helpers as helpers
from scp.api_server_parts.pipeline.base import PipelineStage
from scp.api_server_parts.pipeline.context import AskPipelineContext
from scp.core.request_run_ledger import stage_request
from scp.core.top_systems_learning import inspect_untrusted as _sf_inspect
from scp.knowledge.domain_knowledge import FactSeparator
from scp.meta.simple_explainer import SimpleExplainer
import scp.runtime.judge as judge_mod
import scp.runtime.question_router as question_router
from scp.security.multi_turn_tracker import MultiTurnTracker

logger = logging.getLogger(__name__)


class DotDict(dict):
    """Attribute-accessible dictionary for judge verdict normalization."""

    def __getattr__(self, name: str) -> Any:
        return self.get(name, None)

    def __setattr__(self, name: str, value: Any) -> None:
        self[name] = value


class VerificationStage(PipelineStage):
    """Stage 4: Semantic firewall inspection, RealityJudge adjudication,

    stale-fact boundary guard, delivery decision matrix (10 branches),
    identity post-guard, fact separation, and bounded fact checking.
    """

    name: str = "verification"

    async def execute(self, ctx: AskPipelineContext) -> None:
        stage_req = ctx.resolve("stage_request", None) or stage_request
        stage_req(ctx.request, "verifier_started")

        # 1. Semantic Firewall inspection
        _raw_evidence = [str(c) for c in ctx.req.contexts or [] if str(c).strip()] + (
            [str(ctx.req.retrieved_context).strip()]
            if str(getattr(ctx.req, "retrieved_context", "") or "").strip()
            else []
        )
        if ctx.pre_gen_evidence:
            _raw_evidence.extend(ctx.pre_gen_evidence)
        if ctx.retrieval_res and ctx.retrieval_res.get("clean_evidence_snippets"):
            _raw_evidence.extend(ctx.retrieval_res["clean_evidence_snippets"])

        _clean_evidence = []
        _injection_blocked = 0
        for _ev in _raw_evidence:
            _quarantined, _reason = _sf_inspect(_ev)
            if _quarantined:
                _injection_blocked += 1
                logger.warning("[SEMANTIC-FIREWALL] Blocked injection in evidence: %s", _reason)
                continue
            _clean_evidence.append(_ev)

        if _injection_blocked:
            ctx.v98_context["semantic_firewall"] = {"blocked": _injection_blocked, "total": len(_raw_evidence)}

        ctx.clean_evidence = _clean_evidence
        ctx.injection_blocked = _injection_blocked

        # 2. RealityJudge execution
        judge = ctx.judge
        if judge is None:
            judge_fn = ctx.resolve("get_judge", None) or helpers.get_judge
            judge = judge_fn() if callable(judge_fn) else None
            ctx.judge = judge

        _evidence_context = " ".join(_clean_evidence) + ((" " + ctx.ai_answer) if ctx.ai_answer else "")
        if hasattr(judge, "judge_with_react_fallback"):
            v = await judge.judge_with_react_fallback(
                question=ctx.req.question,
                ai_answer=ctx.ai_answer,
                cycle_count=0,
                source=ctx.req.source,
                context=_evidence_context,
                v98_context=ctx.v98_context,
            )
        else:
            v = await asyncio.to_thread(
                judge.judge,
                question=ctx.req.question,
                ai_answer=ctx.ai_answer,
                cycle_count=0,
                source=ctx.req.source,
                context=_evidence_context,
                v98_context=ctx.v98_context,
            )

        if isinstance(v, dict):
            if v.get("evidence") is None:
                v["evidence"] = {}
            if v.get("slm_responses") is None:
                v["slm_responses"] = []
            if v.get("confidence") is None:
                v["confidence"] = 0.0
            if v.get("final_answer") is None:
                v["final_answer"] = v.get("evidence", {}).get("final_answer", "")
            v = DotDict(v)
        elif v is not None:
            if getattr(v, "evidence", None) is None:
                v.evidence = {}
            if getattr(v, "slm_responses", None) is None:
                v.slm_responses = []
            if getattr(v, "confidence", None) is None:
                v.confidence = 0.0
            if getattr(v, "final_answer", None) is None:
                v.final_answer = v.evidence.get("final_answer", "") if isinstance(v.evidence, dict) else ""

        # 3. W8-e1 stale-fact boundary guard
        _w8_time_signal = ctx.resolve("question_has_time_signal", None) or judge_mod.question_has_time_signal
        if (
            v.verdict == "PASS"
            and _w8_time_signal(str(ctx.req.question or ""))
            and not (
                ctx.web_fallback_used
                or ctx.has_provided_evidence
                or bool((ctx.retrieval_res or {}).get("clean_evidence_snippets"))
            )
        ):
            _w8_failures = list(v.failures or []) + ["time_signal_without_fresh_evidence"]
            v.failures = _w8_failures
            if ctx.is_chatbot_lane:
                v.verdict = "ABSTAIN"
                if isinstance(v.evidence, dict):
                    v.evidence["abstain_reasons"] = ["time_signal_without_fresh_evidence"]
                logger.warning(
                    "[W8-e1] time-signal question without fresh evidence: "
                    "chatbot PASS downgraded to ABSTAIN (labeled delivery)"
                )
            else:
                v.verdict = "FAIL"
                if isinstance(v.evidence, dict):
                    v.evidence["governance_decision"] = "ESCALATE"
                logger.warning(
                    "[W8-e1] time-signal question without fresh evidence: "
                    "PASS downgraded to FAIL/ESCALATE (withheld, fail-closed)"
                )

        stage_req(
            ctx.request,
            "verifier_completed",
            verdict=getattr(v, "verdict", "FAIL"),
            governance_decision=getattr(v, "evidence", {}).get("governance_decision", ""),
        )

        # 4. DoS protection verdict recording
        if hasattr(judge, "dos_protection") and judge.dos_protection:
            try:
                judge.dos_protection.record_verdict(getattr(v, "verdict", "FAIL"))
                ctx.dos_slot_taken = False
            except Exception as e:
                logger.debug(f"[V104.41 #AC] DoS record_verdict error: {e}", exc_info=True)

        elapsed_ms = (time.time() - ctx.start_time) * 1000
        ctx.elapsed_ms = elapsed_ms

        # 5. Observability & tracking
        if hasattr(judge, "response_monitor") and judge.response_monitor:
            try:
                judge.response_monitor.observe(
                    prompt=ctx.req.question, response=v.final_answer or "", latency_ms=elapsed_ms
                )
            except Exception as e:
                logger.debug(f"[V104.41 #AD] ResponseMonitor observe error: {e}", exc_info=True)

        slm_trace = []
        for r in v.slm_responses:
            slm_trace.append(
                {
                    "domain": r.get("domain", "?"),
                    "slm_name": r.get("slm_name", r.get("domain", "?")),
                    "answer": str(r.get("answer", ""))[:200],
                    "confidence": r.get("confidence", 0),
                    "source": (
                        r.get("evidence", {}).get("source", "?") if isinstance(r.get("evidence"), dict) else "?"
                    ),
                    "evidence": r.get("evidence", {}) if isinstance(r.get("evidence"), dict) else {},
                    "processing_time_ms": r.get("processing_time", 0),
                }
            )

        phase_timings = v.evidence.get("v100_phase_timings", {})
        mt_result = None
        _mt_session = ctx.req.session_id or ctx.v98_context.get("session_id", "")
        if _mt_session:
            mt_tracker = ctx.resolve("_multi_turn_tracker", None) or MultiTurnTracker()
            mt_result = mt_tracker.track(_mt_session, ctx.req.question, v.verdict)
            if mt_result.suspicious:
                if v.verdict in ("PASS", "FAIL", "UNKNOWN", "PARTIAL"):
                    v.verdict = "FLAGGED"
                    v.confidence = (v.confidence or 0.0) * 0.5
                    if v.reasoning:
                        v.reasoning += f" [V104 Multi-turn: {mt_result.pattern_type}]"
                logger.warning(
                    f"V104 Multi-turn attack: session={_mt_session}, pattern={mt_result.pattern_type}, reason={mt_result.reason}"
                )
                ctx.v98_context["v104_multi_turn"] = mt_result.__dict__

        has_attack = bool(ctx.v98_context.get("v99_vietnamese_attack"))
        has_bypass = bool(v.evidence.get("v100_bypass_detected"))
        has_human_review = bool(v.evidence.get("v102_human_review_pending"))
        source_count = len([r for r in v.slm_responses if r.get("answer")])
        explainer = ctx.resolve("_simple_explainer", None) or SimpleExplainer()
        explainer.explain(
            verdict=v.verdict,
            confidence=v.confidence,
            domain=v.domain or "" or "" or "",
            sources=source_count,
            has_attack=has_attack,
            has_bypass=has_bypass,
            has_human_review=has_human_review,
            lineage_overlap=0.0,
            reliability_factor=v.evidence.get("v102_reliability_factor", 1.0),
        )

        # 6. Delivery & Decision Boundary
        _api_final_answer = v.final_answer
        _gov_decision = v.evidence.get("governance_decision", "")
        _api_slm_responses = [{k: str(v2)[:200] for k, v2 in r.items()} for r in v.slm_responses]
        _api_slm_trace = slm_trace
        _api_reasoning = v.reasoning[:500] if v.reasoning else None
        _api_v100_claims = v.evidence.get("v100_claims")
        _api_v103_antibodies = v.evidence.get("v103_antibodies")
        _api_speculative_mode = v.evidence.get("speculative_mode")
        _api_v98_canary_token = v.evidence.get("v98_canary_token")
        _api_v98_guard = v.evidence.get("v98_guard_verdict") or v.evidence.get("v98_guard")
        _api_v98_classification = v.evidence.get("v98_classification")
        _api_v98_attack_policy = v.evidence.get("v98_attack_policy")
        _api_v98_counter_executed = v.evidence.get("v98_counter_executed")
        _api_v98_bypass_recorded = v.evidence.get("v98_bypass_recorded")
        _api_falsification_status = v.evidence.get("falsification_status")

        _lane = getattr(ctx.route_decision, "lane", "LANE_CHATBOT") if ctx.route_decision else "LANE_CHATBOT"
        _is_true_security_threat = (
            _gov_decision == "KILL"
            or v.verdict == "FLAGGED"
            or ctx.multimodal_block
            or _lane == question_router.LANE_SECURITY
            or bool(v.evidence.get("threat_detected"))
            or bool(v.evidence.get("injection_detected"))
        )

        _w7_is_refusal = ctx.resolve("is_refusal_abstain_answer", None) or judge_mod.is_refusal_abstain_answer
        _w7_rt_reason = str(getattr(ctx.route_decision, "reason", "")) if ctx.route_decision else ""
        _w7_rt_domain = str(getattr(ctx.route_decision, "domain", "") or "").strip().lower() if ctx.route_decision else ""
        _w7_realtime_no_tool_abstain = (
            _lane == question_router.LANE_FACTUAL
            and (
                any(_tag in _w7_rt_reason for _tag in ("weather_fact", "finance_fact"))
                or _w7_rt_domain in ("weather", "finance")
            )
            and not ctx.web_fallback_used
            and not ctx.has_provided_evidence
            and not (ctx.retrieval_res or {}).get("retrieval_triggered")
            and bool(_w7_is_refusal(str(ctx.ai_answer or "")))
        )

        _w16_is_honest_abstain = ctx.resolve("is_honest_abstain_answer", None) or judge_mod.is_honest_abstain_answer
        _w16_factual_honest_abstain = (
            _lane == question_router.LANE_FACTUAL
            and bool(_w16_is_honest_abstain(str(_api_final_answer or "")))
        )

        if isinstance(v.evidence, dict):
            v.evidence["judge_evaluated"] = True
            v.evidence["routing"] = ctx.route_decision.to_dict() if ctx.route_decision else {}
        if isinstance(_api_v98_classification, dict):
            _api_v98_classification["judge_evaluated"] = True
        elif _api_v98_classification is None:
            _api_v98_classification = {"judge_evaluated": True}

        # 10 Branches of Delivery Matrix
        if _is_true_security_threat:
            _gov_decision = "KILL"
            _api_final_answer = "[SCP: Answer withheld]"
            if _gov_decision == "KILL":
                _api_final_answer = "[SCP: Answer withheld — Governance KILL]"
            _api_slm_responses = []
            _api_slm_trace = []
            _api_reasoning = "[SCP: Answer withheld]"
            _api_v100_claims = None
            _api_v103_antibodies = None
            _api_speculative_mode = None
            _api_v98_canary_token = None
            _api_v98_guard = None
            _api_v98_classification = None
            _api_v98_attack_policy = None
            _api_v98_counter_executed = None
            _api_v98_bypass_recorded = None
            _api_falsification_status = None
            logger.info(
                f"[V104.41 #X] API boundary enforcing abstain (all fields cleared): verdict={v.verdict}, gov={_gov_decision}"
            )
        elif v.verdict == "PASS" and (not _gov_decision or _gov_decision == "UNKNOWN"):
            logger.warning(
                "[SEC-R2-02] Governance decision missing or UNKNOWN in _ask_impl — enforcing fail-closed withhold"
            )
            raise HTTPException(status_code=403, detail="Governance clearance missing — fail-closed")
        elif (
            v.verdict == "ABSTAIN"
            and not (mt_result and mt_result.suspicious)
            and (ctx.is_chatbot_lane or _w7_realtime_no_tool_abstain or _w16_factual_honest_abstain)
            and str(_api_final_answer or "").strip()
            and not str(_api_final_answer).startswith("User Safety:")
            and str(_api_final_answer).strip() != "safe"
        ):
            _w7_abstain_base = str(_api_final_answer).strip()
            _api_final_answer = f"[unverified — abstain] {_w7_abstain_base}"
            _gov_decision = "ABSTAIN"
            _api_reasoning = "[W7-e6 abstain-delivery] " + (
                str(v.reasoning)[:400]
                if v.reasoning
                else "honest abstain delivered with unverified-abstain label; no factual claim was verified"
            )
            logger.info(
                "[W7-e6] benign abstain delivered with label: lane=%s verdict=ABSTAIN governance=ABSTAIN",
                getattr(ctx.route_decision, "lane", ""),
            )
        elif not ctx.is_chatbot_lane and _gov_decision == "ESCALATE":
            _api_final_answer = (
                "[SCP: Answer withheld — không xác minh được câu trả lời (governance: ESCALATE)]"
            )
            _api_slm_responses = []
            _api_slm_trace = []
            _api_reasoning = (
                "[SCP: Answer withheld — không xác minh được câu trả lời (governance: ESCALATE)]"
            )
            _api_v100_claims = None
            _api_v103_antibodies = None
            _api_speculative_mode = None
            _api_v98_canary_token = None
            _api_v98_guard = None
            _api_v98_classification = None
            _api_v98_attack_policy = None
            _api_v98_counter_executed = None
            _api_v98_bypass_recorded = None
            _api_falsification_status = None
            logger.info(
                "[W3-e1] API boundary withholding unverified benign answer: verdict=%s governance=ESCALATE",
                v.verdict,
            )
        elif not ctx.is_chatbot_lane and _gov_decision == "DEGRADED":
            _api_final_answer = "[SCP: Answer withheld — governance degraded]"
            _api_slm_responses = []
            _api_slm_trace = []
            _api_reasoning = "[SCP: Answer withheld — governance degraded]"
            _api_v100_claims = None
            _api_v103_antibodies = None
            _api_speculative_mode = None
            _api_v98_canary_token = None
            _api_v98_guard = None
            _api_v98_classification = None
            _api_v98_attack_policy = None
            _api_v98_counter_executed = None
            _api_v98_bypass_recorded = None
            _api_falsification_status = None
            logger.info(
                "[S-H1] API boundary withholding answer: verdict=PASS but governance=%s", _gov_decision
            )
        elif not ctx.is_chatbot_lane and v.verdict in ("FAIL", "FLAGGED", "DEGRADED", "UNCERTAIN", "ESCALATE"):
            _api_final_answer = "[SCP: Answer withheld]"
            _api_slm_responses = []
            _api_slm_trace = []
            _api_reasoning = "[SCP: Answer withheld]"
            _api_v100_claims = None
            _api_v103_antibodies = None
            _api_speculative_mode = None
            _api_v98_canary_token = None
            _api_v98_guard = None
            _api_v98_classification = None
            _api_v98_attack_policy = None
            _api_v98_counter_executed = None
            _api_v98_bypass_recorded = None
            _api_falsification_status = None
            logger.info(f"[V104.41 #X] API boundary enforcing factual abstain: verdict={v.verdict}")
        elif not ctx.is_chatbot_lane and v.verdict == "UNKNOWN" and v.evidence.get("why_gate", {}).get("decision") == "REJECT":
            _api_final_answer = "[SCP: Answer withheld — WHY Gate blocked]"
            _api_slm_responses = []
            _api_slm_trace = []
            _api_reasoning = "[SCP: WHY Gate blocked]"
            _api_v100_claims = None
            _api_v103_antibodies = None
            _api_speculative_mode = None
            _api_v98_canary_token = None
            _api_v98_guard = None
            _api_v98_classification = None
            _api_v98_attack_policy = None
            _api_v98_counter_executed = None
            _api_v98_bypass_recorded = None
            _api_falsification_status = None
            logger.info(f"[FIX-1] API boundary enforcing WHY Gate block (all fields cleared): verdict={v.verdict}")
        elif ctx.is_chatbot_lane:
            if str(_api_final_answer).startswith("User Safety:") or str(_api_final_answer).strip() == "safe":
                _api_final_answer = None

            if not _api_final_answer or str(_api_final_answer).startswith("[SCP: Answer withheld"):
                if ctx.ai_answer and not str(ctx.ai_answer).startswith("User Safety:"):
                    _api_final_answer = ctx.ai_answer
                else:
                    _ans_from_mem = None
                    _q_lower = ctx.req.question.lower()
                    if any(
                        k in _q_lower
                        for k in (
                            "tên tôi là gì",
                            "tôi tên là gì",
                            "tôi tên gì",
                            "tên của tôi",
                            "nhớ tôi không",
                            "nhớ tên tôi",
                        )
                    ):
                        for _h in reversed(ctx.history or []):
                            if _h.get("role") == "user":
                                import re as _re_mem

                                _m_name = _re_mem.search(
                                    r"(?:tên\s+là|tôi\s+là|mình\s+là)\s+([A-Za-zÀ-ỹ]+)",
                                    _h.get("content", ""),
                                    _re_mem.IGNORECASE,
                                )
                                if _m_name:
                                    _ans_from_mem = f"Bạn đã giới thiệu bạn tên là {_m_name.group(1)}! Tôi luôn ghi nhớ thông tin bạn chia sẻ trong phiên trò chuyện này."
                                    break
                        if not _ans_from_mem:
                            _ans_from_mem = "Trong phiên trò chuyện này, bạn chưa nói cho tôi biết tên của bạn. Bạn có muốn chia sẻ tên với tôi không?"
                    elif any(k in _q_lower for k in ("chào", "hello", "hi")):
                        _ans_from_mem = "Chào bạn! Tôi là SCP — rất vui được trò chuyện và hỗ trợ bạn."
                    elif any(k in _q_lower for k in ("bạn là ai", "who are you")):
                        _ans_from_mem = "Mình là SCP — một trợ lý AI thông minh, hỗ trợ trao đổi tự nhiên, tra cứu thông tin và xử lý tác vụ an toàn."

                    _api_final_answer = (
                        _ans_from_mem or getattr(v, "final_answer", "") or "Tôi là SCP, trợ lý AI của bạn."
                    )
                    if str(_api_final_answer).startswith("User Safety:"):
                        _api_final_answer = "Tôi là SCP, trợ lý AI của bạn. Rất vui được hỗ trợ bạn!"

            if _gov_decision == "ESCALATE":
                _api_final_answer = (
                    "[SCP: Answer withheld — không xác minh được câu trả lời (governance: ESCALATE)]"
                )
                _api_reasoning = _api_final_answer
                logger.info(
                    "[W3-e1] Chatbot lane withholding unverified benign answer: verdict=%s governance=ESCALATE",
                    v.verdict,
                )
            elif _gov_decision in ("KILL", "REJECT", "DENY", "DEGRADED"):
                raise HTTPException(status_code=403, detail="Governance KILL enforced")
            if not _gov_decision or _gov_decision == "UNKNOWN":
                logger.warning(
                    "[SEC-R2-02] Governance decision missing or UNKNOWN in _ask_impl — enforcing fail-closed withhold"
                )
                _gov_decision = "DENY"
                raise HTTPException(status_code=403, detail="Governance clearance missing — fail-closed")
            if not _api_reasoning:
                _api_reasoning = v.reasoning[:500] if v.reasoning else "Conversational response"

        elif v.verdict == "UNKNOWN":
            if _api_final_answer and "[SCP: unverified]" not in _api_final_answer:
                _sources = []
                for r in v.slm_responses:
                    if r.get("answer"):
                        _sources.append(f"  • {r.get('slm_name', '?')}: {str(r.get('answer', ''))[:60]}")
                _source_text = "\n".join(_sources) if _sources else "  (không có SLM nào trả lời)"
                _api_final_answer = str(_api_final_answer) + str(
                    f"\n\nSCP đã kiểm tra:\n{_source_text}\nĐộ tin cậy: {v.confidence:.0%} — chưa đạt ngưỡng (cần ≥70%)"
                )
        elif not ctx.is_chatbot_lane and v.verdict == "ABSTAIN":
            _api_final_answer = "[SCP: Answer withheld]"
            _api_slm_responses = []
            _api_slm_trace = []
            _api_reasoning = "[SCP: Answer withheld]"
            _api_v100_claims = None
            _api_v103_antibodies = None
            _api_speculative_mode = None
            _api_v98_canary_token = None
            _api_v98_guard = None
            _api_v98_classification = None
            _api_v98_attack_policy = None
            _api_v98_counter_executed = None
            _api_v98_bypass_recorded = None
            _api_falsification_status = None
            logger.info(
                "[W16-f4] API boundary withholding non-deliverable factual ABSTAIN (unlabeled shape must not leave boundary)"
            )

        # 7. Post-guard identity
        if _api_final_answer:
            strip_vendor_fn = ctx.resolve("_strip_vendor_identity_claims", None)
            if strip_vendor_fn:
                _api_final_answer, _w3_identity_changed = strip_vendor_fn(str(_api_final_answer))
                if _w3_identity_changed:
                    logger.warning(
                        "[W3-e4] Identity post-guard replaced vendor self-attribution in final answer (root-5)"
                    )

        # 8. Milestone 2: Fact Separation & Confidence Badge Payload
        _fact_separator = FactSeparator()
        _fact_res = _fact_separator.separate(
            question=ctx.req.question,
            answer=str(_api_final_answer or ""),
            lane=_lane,
            confidence=float(v.confidence if v.confidence is not None else 0.8),
            retrieval_result=ctx.retrieval_res if ctx.retrieval_res else {},
            contexts=ctx.req.contexts,
        )
        _verified_facts = _fact_res["verified_facts"]
        _llm_reasoning = _fact_res["llm_reasoning"]
        _confidence_badge = _fact_res["confidence_badge"]

        if _is_true_security_threat:
            _verified_facts = []
            _llm_reasoning = "[SCP: Answer withheld]"
            _confidence_badge = {
                "badge": "UNVERIFIED_CONJECTURE",
                "score": 0.0,
                "sources_consulted": [],
                "transparency_notes": "Security boundary triggered fail-closed withhold.",
            }

        # 9. Bounded fact check await
        if v.verdict == "PASS" and _api_final_answer and (len(_api_final_answer) > 20):
            try:
                afc_fn = ctx.resolve("_async_fact_check", None) or afc_mod._async_fact_check
                timeout_s = float(ctx.resolve("_FACTCHECK_AWAIT_TIMEOUT_S", 10.0))
                await asyncio.wait_for(
                    afc_fn(_api_final_answer, ctx.req.question, ctx.v98_context.get("session_id", "")),
                    timeout=timeout_s,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "fact-check bound exceeded — proceeding (auxiliary branch, verdict unchanged)"
                )
            except Exception as e:
                logger.debug(f"[V104.37] api_server.py: e={e}", exc_info=True)

        ctx.judge_verdict = v
        ctx.gov_decision = _gov_decision
        ctx.api_final_answer = _api_final_answer
        ctx.api_reasoning = _api_reasoning
        ctx.is_true_security_threat = _is_true_security_threat
        ctx.verified_facts = _verified_facts
        ctx.llm_reasoning = _llm_reasoning
        ctx.confidence_badge = _confidence_badge
        ctx.slm_trace = _api_slm_trace
        ctx.slm_responses = _api_slm_responses
        ctx.phase_timings = phase_timings
        ctx.mt_result = mt_result
        ctx.api_v100_claims = _api_v100_claims
        ctx.api_v103_antibodies = _api_v103_antibodies
        ctx.api_speculative_mode = _api_speculative_mode
        ctx.api_v98_canary_token = _api_v98_canary_token
        ctx.api_v98_guard = _api_v98_guard
        ctx.api_v98_classification = _api_v98_classification
        ctx.api_v98_attack_policy = _api_v98_attack_policy
        ctx.api_v98_counter_executed = _api_v98_counter_executed
        ctx.api_v98_bypass_recorded = _api_v98_bypass_recorded
        ctx.api_falsification_status = _api_falsification_status
