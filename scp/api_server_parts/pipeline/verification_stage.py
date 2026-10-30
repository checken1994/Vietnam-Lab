# SCP CIRCUIT: M02 — Pipeline Pattern Refactor
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Mapping

import scp.api_server_parts._async_fact_check as afc_mod
import scp.api_server_parts.helpers as helpers
import scp.runtime.judge as judge_mod
import scp.runtime.question_router as question_router
from scp.api_server_parts.pipeline.base import PipelineStage
from scp.api_server_parts.pipeline.context import AskPipelineContext
from scp.api_server_parts.pipeline.delivery_rules import DecisionCode, DeliveryRuleEngine
from scp.api_server_parts.pipeline.verdict_contract import (
    PipelineContextKeys as _PCK,
    VerdictEvidenceKeys as _VEK,
    validate_verdict_shape as _validate_verdict_shape,
)
from scp.core.request_run_ledger import stage_request
from scp.core.top_systems_learning import inspect_untrusted as _sf_inspect
from scp.knowledge.domain_knowledge import FactSeparator
from scp.meta.simple_explainer import SimpleExplainer
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
            ctx.v98_context[_PCK.SEMANTIC_FIREWALL] = {"blocked": _injection_blocked, "total": len(_raw_evidence)}

        ctx.clean_evidence = _clean_evidence
        ctx.injection_blocked = _injection_blocked

        # 2. RealityJudge resolution
        judge = ctx.judge
        judge_fn = ctx.resolve("get_judge", None) or helpers.get_judge
        if judge is None:
            judge = judge_fn() if callable(judge_fn) else None
            ctx.judge = judge

        is_custom_judge = (
            (ctx.judge is not None and not isinstance(ctx.judge, helpers.get_judge().__class__))
            or (callable(judge_fn) and judge_fn is not helpers.get_judge and getattr(judge_fn, "__name__", "") != "get_judge")
        )

        # [W15-W18] 1b. SAFE-style Atomic Claim Decomposition & Local Deterministic Verifier
        _atomic_short_circuit = False
        _atomic_summary: dict[str, Any] = {}
        try:
            from scp.runtime.atomic_verifier import AtomicDeterministicVerifier, is_atomic_verifier_enabled

            if is_atomic_verifier_enabled() and ctx.ai_answer and not is_custom_judge:
                _atomic_verifier = AtomicDeterministicVerifier()
                _atomic_summary = _atomic_verifier.verify_and_prune(
                    question=ctx.req.question,
                    draft_answer=ctx.ai_answer,
                    clean_evidence=_clean_evidence,
                    retrieval_res=ctx.retrieval_res or {},
                )
                if _atomic_summary.get("repaired_answer"):
                    ctx.ai_answer = str(_atomic_summary["repaired_answer"])
                if (
                    _atomic_summary.get("total_claims", 0) > 0
                    and _atomic_summary.get("all_verified_locally")
                ):
                    _atomic_short_circuit = True
                elif _atomic_summary.get("refuted_count", 0) > 0 and ctx.is_chatbot_lane:
                    logger.warning("[VERIFIER] Contradiction detected in chatbot lane; stripping chatbot bypass")
                    ctx.is_chatbot_lane = False
        except (RuntimeError, ValueError, OSError, ImportError, AttributeError, KeyError) as _av_err:
            logger.warning("[W15-W18] AtomicDeterministicVerifier fallback to RealityJudge: %s", _av_err, exc_info=True)

        # [DEHARDCODE] Nhánh "deterministic_greeting" (câu chào đóng hộp được cấp
        # PASS giả) đã bị gỡ cùng câu trả lời đóng hộp ở generation_stage.
        if _atomic_short_circuit:
            v = DotDict({
                "verdict": "PASS",
                "confidence": 0.98,
                "deterministic_confidence": 1.0,
                "semantic_confidence": 1.0,
                "cross_model_agreement": True,
                "degraded": False,
                "reasoning": (
                    f"Verified deterministically across {_atomic_summary.get('verified_count', 1)}/"
                    f"{_atomic_summary.get('total_claims', 1)} atomic claims (0 external LLM calls)."
                ),
                "cycle_count": 0,
                "failures": [],
                "final_answer": _atomic_summary.get("pruned_answer") or ctx.ai_answer,
                "slm_responses": [],
                "domain": getattr(ctx.req, "domain", "general") or "general",
                "evidence": {
                    "governance_decision": "UPHOLD",
                    # [F1-S3 fix · 2026-10-24 · theo AUD-S3 độc lập] RealityJudge
                    # KHÔNG chạy trên nhánh này — cờ judge_evaluated phải nói sự
                    # thật (False); thẩm quyền adjudication là verifier
                    # deterministic cục bộ. Downstream dựa
                    # deterministic_verified/adjudicated_by thay vì cờ judge.
                    "judge_evaluated": False,
                    "deterministic_verified": True,
                    "adjudicated_by": "atomic_deterministic_local",
                    "provenance": "atomic_deterministic_local",
                    "v100_claims": _atomic_summary.get("claims_payload", []),
                    "v98_classification": {
                        "route": "atomic_deterministic_local",
                        "provenance": "atomic_deterministic_local",
                        "judge_evaluated": False,
                        "adjudicated_by": "atomic_deterministic_local",
                    },
                },
            })
        else:
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
            # [W18] Selective Pruning: if LLM Judge escalated due to unverified residual claims
            # (e.g. offline/missing consensus), but at least one atomic claim was deterministically
            # verified locally with 0 contradictions, deliver only the verified atomic claims!
            if (
                isinstance(v, dict)
                and v.get("verdict") in ("FAIL", "ABSTAIN", "UNKNOWN")
                and (v.get("evidence") or {}).get("governance_decision") == "ESCALATE"
                and _atomic_summary.get("partially_verified_locally")
                and _atomic_summary.get("pruned_answer")
            ):
                # [F2-S3 · 2026-10-24] Nhánh DUY NHẤT nâng verdict sau judge.
                # Giữ hành vi (có căn cứ deterministic + prune clause chưa
                # verify) nhưng ghi trung thực: judge ĐÃ chạy, verdict của judge
                # bị override — ghi lại verdict gốc để audit vết.
                _judge_original = v.get("verdict")
                v["verdict"] = "PASS"
                v["confidence"] = 0.90
                v["deterministic_confidence"] = 1.0
                v["final_answer"] = _atomic_summary["pruned_answer"]
                v["reasoning"] = (
                    f"Selective pruning delivered {_atomic_summary.get('verified_count')}/"
                    f"{_atomic_summary.get('total_claims')} deterministically verified atomic claims; "
                    "unverified residual clauses pruned."
                )
                v.setdefault("evidence", {})["governance_decision"] = "UPHOLD"
                v["evidence"]["judge_evaluated"] = True
                v["evidence"]["judge_verdict_overridden"] = True
                v["evidence"]["judge_original_verdict"] = _judge_original
                v["evidence"]["deterministic_verified"] = True
                v["evidence"]["adjudicated_by"] = "atomic_deterministic_local"
                v["evidence"]["provenance"] = "atomic_deterministic_local"
                v["evidence"]["v100_claims"] = _atomic_summary.get("claims_payload", [])
                v["evidence"]["v98_classification"] = {
                    "route": "atomic_deterministic_local",
                    "provenance": "atomic_deterministic_local",
                    "judge_evaluated": True,
                    "adjudicated_by": "atomic_deterministic_local",
                }

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

        # [HC-31 · 2026-10-24] Self-certification transparency. Khi gateway chỉ
        # còn 1 provider ENABLED cho cả vòng sinh→chấm, verdict do CHÍNH model
        # sinh answer tự chấm (không có cross-model second opinion thật) — phải
        # dán nhãn trung thực vào evidence thay vì để PASS trông như đã đối
        # chứng. Probe peek_gateway() KHÔNG khởi tạo singleton: custom judge
        # hoặc atomic-deterministic (không qua gateway) → không stamp (UNKNOWN),
        # tránh nhãn thiếu căn cứ (Reality over Model).
        if not _atomic_short_circuit and not is_custom_judge and isinstance(getattr(v, "evidence", None), dict):
            try:
                from scp.llm_gateway.client import peek_gateway
                _hc31_gw = peek_gateway()
                if _hc31_gw is not None:
                    _hc31_count = int(_hc31_gw.enabled_provider_count())
                    v.evidence["provider_count"] = _hc31_count
                    v.evidence["self_certified"] = (_hc31_count == 1)
            except Exception as _hc31_err:
                logger.debug("[HC-31] self-certification probe failed: %s", _hc31_err, exc_info=True)

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
                    v.evidence["governance_decision"] = "ABSTAIN"
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
        _mt_session = ctx.req.session_id or ctx.v98_context.get(_PCK.SESSION_ID, "")
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
                ctx.v98_context[_PCK.V104_MULTI_TURN] = mt_result.__dict__

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
            # [F1-S3 fix · 2026-10-24] setdefault thay vì ghi đè: nhánh atomic
            # short-circuit đã đánh dấu judge_evaluated=False (judge không chạy)
            # — không được vô tình xoá nhãn trung thực ở đây. Đường judge thật
            # vẫn mặc định True.
            v.evidence.setdefault("judge_evaluated", True)
            v.evidence.setdefault("adjudicated_by", "reality_judge")
            v.evidence["routing"] = ctx.route_decision.to_dict() if ctx.route_decision else {}
        if isinstance(_api_v98_classification, dict):
            _api_v98_classification.setdefault("judge_evaluated", True)
        elif _api_v98_classification is None:
            _api_v98_classification = {"judge_evaluated": True}
        if isinstance(_api_v98_classification, dict) and "provenance" not in _api_v98_classification:
            if ctx.web_fallback_used:
                _api_v98_classification["provenance"] = "web_search"
            elif (ctx.retrieval_res or {}).get("retrieval_triggered"):
                _api_v98_classification["provenance"] = "external_rag"
            elif getattr(ctx.req, "contexts", None) or getattr(ctx.req, "retrieved_context", None):
                _api_v98_classification["provenance"] = "input_context_only"
            else:
                _api_v98_classification["provenance"] = "general_knowledge"

        # 6. Delivery & Decision Boundary via Chain-of-Responsibility Rule Engine
        decision = DeliveryRuleEngine.evaluate(ctx, v, _gov_decision, mt_result=mt_result)
        if decision.http_exception:
            raise decision.http_exception

        _api_final_answer = decision.final_answer
        _gov_decision = decision.gov_decision
        _api_reasoning = decision.reasoning
        ctx.v98_context[_PCK.DECISION_CODE] = decision.code
        if hasattr(ctx.request, "state"):
            ctx.request.state.decision_code = str(decision.code)
            ctx.request.state.is_conversational = ctx.is_chatbot_lane or DeliveryRuleEngine.is_strictly_conversational(ctx)
        if decision.code == DecisionCode.ERR_SECURITY_VIOLATION:
            _is_true_security_threat = True
        # [FU-FE2E2-1] KILL-withheld KHÔNG BAO GIỜ mang verdict PASS (hay verdict
        # "giao được" nào): ép FAIL/0.0 ngay tại điểm áp quyết định KILL, TRƯỚC
        # fact-check/shadow/ledger (calibration không ghi pass cho câu bị KILL).
        # Fail-closed; giữ FLAGGED (nhãn tấn công multi-turn). Khớp kernel
        # build_safe_response (FAIL/0.0) — đường _ask_impl trực tiếp nay cũng vậy.
        if (_is_true_security_threat or _gov_decision == "KILL") and v.verdict not in ("FAIL", "FLAGGED"):
            logger.warning("[FU-1] KILL withhold: verdict %s forced to FAIL", v.verdict)
            v.verdict = "FAIL"
            v.confidence = 0.0

        if decision.should_clear_fields:
            _api_slm_responses = []
            _api_slm_trace = []
            _api_reasoning = decision.reasoning or "[SCP: Answer withheld]"
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
                "[RuleEngine] API boundary enforcing withhold (%s): verdict=%s gov=%s",
                decision.code, getattr(v, "verdict", "FAIL"), _gov_decision,
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
                    afc_fn(_api_final_answer, ctx.req.question, ctx.v98_context.get(_PCK.SESSION_ID, "")),
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

        # [BRAIN-LAYER P1 · 2026-10-23] Shadow hook — TỰ CHẾ ĐỘ. Chạy task
        # 'judge' shadow qua BrainLayer khi SCP_BRAIN_MODE=shadow + registry
        # đã cấu hình. KẾT QUẢ SHADOW KHÔNG BAO GIỜ đụng verdict/final_answer/
        # governance trả cho user (chỉ ghi BrainTrace + đo disagreement —
        # record có shadow=True). Mọi exception ⇒ log debug + bỏ qua; /ask
        # không được fail vì shadow. Điểm cắm: sau khi verdict cuối + final
        # answer đã chốt, trước khi ledger_stage đọc ctx.*.
        try:
            import scp.brain_layer.shadow as _brain_shadow

            _shadow_fn = ctx.resolve("_brain_shadow_judge", None) or _brain_shadow.run_shadow_judge
            _shadow_judge_inst = ctx.judge
            await _shadow_fn(
                question=str(ctx.req.question or ""),
                final_answer=str(_api_final_answer or ""),
                production_verdict=str(getattr(v, "verdict", "") or ""),
                claims=_api_v100_claims if isinstance(_api_v100_claims, list) else None,
                run_id=str(ctx.run_id or ""),
                trace_id=str(ctx.trace_id or ""),
                judge_factory=(lambda j=_shadow_judge_inst: j) if _shadow_judge_inst is not None else None,
            )
        except Exception as _brain_shadow_err:
            logger.debug(
                "[BRAIN-SHADOW] shadow judge skipped (non-fatal): %s",
                _brain_shadow_err,
                exc_info=True,
            )

        # [HC-52 · 2026-10-24 · phase 1 observe] Shape contract của verdict object:
        # VI PHẠM chỉ log structured WARNING (không chặn, không mutate) — điểm gắn
        # DUY NHẤT cuối verification_stage, trước khi ledger_stage đọc ctx.judge_verdict.
        _shape_target = (
            v
            if isinstance(v, Mapping)
            else {
                _k: getattr(v, _k, None)
                for _k in ("verdict", "confidence", "final_answer", "reasoning", "evidence")
            }
        )
        for _shape_err in _validate_verdict_shape(_shape_target):
            logger.warning(
                "[HC-52] verdict_shape_violation: %s | verdict=%s governance=%s session=%s",
                _shape_err,
                getattr(v, "verdict", None),
                (getattr(v, "evidence", {}) or {}).get(_VEK.GOVERNANCE_DECISION, ""),
                ctx.v98_context.get(_PCK.SESSION_ID, ""),
            )
