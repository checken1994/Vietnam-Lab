"""Delivery decision rules and error codes for VerificationStage (Chain-of-Responsibility).

Provides structured decision codes (ERR_*, DELIVER_*) for the 10 branches of the
delivery decision matrix, eliminating deep nested branch sprawl and making audit
trails clear and reproducible.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException

import scp.runtime.judge as judge_mod
import scp.runtime.question_router as question_router

logger = logging.getLogger(__name__)


class DecisionCode:
    ERR_SECURITY_VIOLATION = "ERR_SECURITY_VIOLATION"
    ERR_GOVERNANCE_MISSING = "ERR_GOVERNANCE_MISSING"
    ERR_GOVERNANCE_ESCALATE = "ERR_GOVERNANCE_ESCALATE"
    ERR_GOVERNANCE_DEGRADED = "ERR_GOVERNANCE_DEGRADED"
    ERR_FACTUAL_FAILED = "ERR_FACTUAL_FAILED"
    ERR_WHY_GATE_REJECTED = "ERR_WHY_GATE_REJECTED"
    DELIVER_UNVERIFIED_ABSTAIN = "DELIVER_UNVERIFIED_ABSTAIN"
    DELIVER_CHATBOT = "DELIVER_CHATBOT"
    DELIVER_VERIFIED_PASS = "DELIVER_VERIFIED_PASS"


@dataclass
class DeliveryDecision:
    code: str
    final_answer: str | None
    gov_decision: str
    reasoning: str | None
    should_clear_fields: bool = False
    http_exception: HTTPException | None = None
    extra_metadata: dict[str, Any] | None = None


class DeliveryRuleEngine:
    """Evaluates the 10 branches of verification outcome sequentially as a Chain-of-Responsibility."""

    @staticmethod
    def evaluate(
        ctx: Any,
        v: Any,
        gov_decision: str,
        mt_result: Any = None,
    ) -> DeliveryDecision:
        lane = getattr(ctx.route_decision, "lane", "LANE_CHATBOT") if ctx.route_decision else "LANE_CHATBOT"
        evidence_dict = v.evidence if isinstance(v.evidence, dict) else {}

        # Branch 1: True Security Threat (KILL)
        is_true_security_threat = (
            gov_decision == "KILL"
            or v.verdict == "FLAGGED"
            or getattr(ctx, "multimodal_block", False)
            or lane == question_router.LANE_SECURITY
            or bool(evidence_dict.get("threat_detected"))
            or bool(evidence_dict.get("injection_detected"))
        )
        if is_true_security_threat:
            return DeliveryDecision(
                code=DecisionCode.ERR_SECURITY_VIOLATION,
                final_answer="[SCP: Answer withheld — Governance KILL]",
                gov_decision="KILL",
                reasoning="[SCP: Answer withheld]",
                should_clear_fields=True,
            )

        # Branch 2: Governance Clearance Missing on PASS
        if v.verdict == "PASS" and (not gov_decision or gov_decision == "UNKNOWN"):
            return DeliveryDecision(
                code=DecisionCode.ERR_GOVERNANCE_MISSING,
                final_answer=None,
                gov_decision=gov_decision,
                reasoning=None,
                http_exception=HTTPException(status_code=403, detail="Governance clearance missing — fail-closed"),
            )

        # Branch 3: Benign Abstain Delivered with Label (W7-e6 / W16)
        w7_is_refusal = ctx.resolve("is_refusal_abstain_answer", None) or judge_mod.is_refusal_abstain_answer
        w7_rt_reason = str(getattr(ctx.route_decision, "reason", "")) if ctx.route_decision else ""
        w7_rt_domain = str(getattr(ctx.route_decision, "domain", "") or "").strip().lower() if ctx.route_decision else ""
        w7_realtime_no_tool_abstain = (
            lane == question_router.LANE_FACTUAL
            and (
                any(tag in w7_rt_reason for tag in ("weather_fact", "finance_fact"))
                or w7_rt_domain in ("weather", "finance")
            )
            and not ctx.web_fallback_used
            and not ctx.has_provided_evidence
            and not (ctx.retrieval_res or {}).get("retrieval_triggered")
            and bool(w7_is_refusal(str(ctx.ai_answer or "")))
        )
        w16_is_honest_abstain = ctx.resolve("is_honest_abstain_answer", None) or judge_mod.is_honest_abstain_answer
        w16_factual_honest_abstain = (
            lane == question_router.LANE_FACTUAL
            and bool(w16_is_honest_abstain(str(v.final_answer or "")))
        )

        final_ans_str = str(v.final_answer or "").strip()
        if (
            v.verdict == "ABSTAIN"
            and not (mt_result and mt_result.suspicious)
            and (ctx.is_chatbot_lane or w7_realtime_no_tool_abstain or w16_factual_honest_abstain)
            and final_ans_str
            and not final_ans_str.startswith("User Safety:")
            and final_ans_str != "safe"
        ):
            w7_abstain_base = final_ans_str
            labeled_answer = f"[unverified — abstain] {w7_abstain_base}"
            reason = "[W7-e6 abstain-delivery] " + (
                str(v.reasoning)[:400]
                if v.reasoning
                else "honest abstain delivered with unverified-abstain label; no factual claim was verified"
            )
            return DeliveryDecision(
                code=DecisionCode.DELIVER_UNVERIFIED_ABSTAIN,
                final_answer=labeled_answer,
                gov_decision="ABSTAIN",
                reasoning=reason,
                should_clear_fields=False,
            )

        # Branch 4: Factual Lane with Governance ESCALATE
        if not ctx.is_chatbot_lane and gov_decision == "ESCALATE":
            return DeliveryDecision(
                code=DecisionCode.ERR_GOVERNANCE_ESCALATE,
                final_answer="[SCP: Answer withheld — không xác minh được câu trả lời (governance: ESCALATE)]",
                gov_decision=gov_decision,
                reasoning="[SCP: Answer withheld — không xác minh được câu trả lời (governance: ESCALATE)]",
                should_clear_fields=True,
            )

        # Branch 5: Factual Lane with Governance DEGRADED
        if not ctx.is_chatbot_lane and gov_decision == "DEGRADED":
            return DeliveryDecision(
                code=DecisionCode.ERR_GOVERNANCE_DEGRADED,
                final_answer="[SCP: Answer withheld — governance degraded]",
                gov_decision=gov_decision,
                reasoning="[SCP: Answer withheld — governance degraded]",
                should_clear_fields=True,
            )

        # Branch 3b: Factual Lane Non-Benign ABSTAIN Withhold
        if not ctx.is_chatbot_lane and v.verdict == "ABSTAIN":
            return DeliveryDecision(
                code=DecisionCode.ERR_FACTUAL_FAILED,
                final_answer="[SCP: Answer withheld]",
                gov_decision=gov_decision,
                reasoning="[SCP: Answer withheld]",
                should_clear_fields=True,
            )

        # Branch 6: Factual Lane Non-Pass Verdicts (FAIL, FLAGGED, UNCERTAIN, etc.)
        if not ctx.is_chatbot_lane and v.verdict in ("FAIL", "FLAGGED", "DEGRADED", "UNCERTAIN", "ESCALATE"):
            return DeliveryDecision(
                code=DecisionCode.ERR_FACTUAL_FAILED,
                final_answer="[SCP: Answer withheld]",
                gov_decision=gov_decision,
                reasoning="[SCP: Answer withheld]",
                should_clear_fields=True,
            )

        # Branch 7: Factual Lane UNKNOWN with WHY Gate REJECT
        if (
            not ctx.is_chatbot_lane
            and v.verdict == "UNKNOWN"
            and evidence_dict.get("why_gate", {}).get("decision") == "REJECT"
        ):
            return DeliveryDecision(
                code=DecisionCode.ERR_WHY_GATE_REJECTED,
                final_answer="[SCP: Answer withheld — WHY Gate blocked]",
                gov_decision=gov_decision,
                reasoning="[SCP: WHY Gate blocked]",
                should_clear_fields=True,
            )

        # Branch 8: Chatbot Lane Fallbacks and Memory Recall
        if ctx.is_chatbot_lane:
            cur_answer = v.final_answer
            if str(cur_answer).startswith("User Safety:") or str(cur_answer).strip() == "safe":
                cur_answer = None

            if not cur_answer or str(cur_answer).startswith("[SCP: Answer withheld"):
                if ctx.ai_answer and not str(ctx.ai_answer).startswith("User Safety:"):
                    cur_answer = ctx.ai_answer
                else:
                    ans_from_mem = None
                    q_lower = ctx.req.question.lower()
                    if any(
                        k in q_lower
                        for k in (
                            "tên tôi là gì", "tôi tên là gì", "tôi tên gì",
                            "tên của tôi", "nhớ tôi không", "nhớ tên tôi",
                        )
                    ):
                        for h in reversed(ctx.history or []):
                            if h.get("role") == "user":
                                m_name = re.search(
                                    r"(?:tên\s+là|tôi\s+là|mình\s+là)\s+([A-Za-zÀ-ỹ]+)",
                                    h.get("content", ""),
                                    re.IGNORECASE,
                                )
                                if m_name:
                                    ans_from_mem = (
                                        f"Bạn đã giới thiệu bạn tên là {m_name.group(1)}! "
                                        "Tôi luôn ghi nhớ thông tin bạn chia sẻ trong phiên trò chuyện này."
                                    )
                                    break
                        if not ans_from_mem:
                            ans_from_mem = (
                                "Trong phiên trò chuyện này, bạn chưa nói cho tôi biết tên của bạn. "
                                "Bạn có muốn chia sẻ tên với tôi không?"
                            )
                    elif any(k in q_lower for k in ("chào", "hello", "hi")):
                        ans_from_mem = "Chào bạn! Tôi là SCP — rất vui được trò chuyện và hỗ trợ bạn."
                    elif any(k in q_lower for k in ("bạn là ai", "who are you")):
                        ans_from_mem = "Mình là SCP — một trợ lý AI thông minh, hỗ trợ trao đổi tự nhiên, tra cứu thông tin và xử lý tác vụ an toàn."

                    cur_answer = ans_from_mem or getattr(v, "final_answer", "") or "Tôi là SCP, trợ lý AI của bạn."
                    if str(cur_answer).startswith("User Safety:"):
                        cur_answer = "Tôi là SCP, trợ lý AI của bạn. Rất vui được hỗ trợ bạn!"

            reason = None
            if gov_decision == "ESCALATE" and getattr(v, "verdict", "") == "FAIL":
                cur_answer = (
                    "[SCP: Answer withheld — không xác minh được câu trả lời (governance: ESCALATE)]"
                )
                reason = cur_answer
                logger.info(
                    "[W3-e1] Chatbot lane withholding unverified benign answer: verdict=%s governance=ESCALATE",
                    v.verdict,
                )
            elif gov_decision == "ESCALATE":
                gov_decision = "ABSTAIN"
            elif gov_decision in ("KILL", "REJECT", "DENY", "DEGRADED"):
                return DeliveryDecision(
                    code=DecisionCode.ERR_SECURITY_VIOLATION,
                    final_answer=None,
                    gov_decision=gov_decision,
                    reasoning=None,
                    http_exception=HTTPException(status_code=403, detail="Governance KILL enforced"),
                )
            if not gov_decision or gov_decision == "UNKNOWN":
                logger.warning(
                    "[SEC-R2-02] Governance decision missing or UNKNOWN in _ask_impl — enforcing fail-closed withhold"
                )
                return DeliveryDecision(
                    code=DecisionCode.ERR_GOVERNANCE_MISSING,
                    final_answer=None,
                    gov_decision="DENY",
                    reasoning=None,
                    http_exception=HTTPException(status_code=403, detail="Governance clearance missing — fail-closed"),
                )
            if not reason:
                reason = v.reasoning[:500] if v.reasoning else "Conversational response"

            return DeliveryDecision(
                code=DecisionCode.DELIVER_CHATBOT,
                final_answer=cur_answer,
                gov_decision=gov_decision,
                reasoning=reason,
                should_clear_fields=False,
            )

        # Branch 9: Factual Lane UNKNOWN without Why Gate rejection
        if v.verdict == "UNKNOWN":
            cur_answer = v.final_answer
            if cur_answer and "[SCP: unverified]" not in cur_answer:
                sources = []
                for r in getattr(v, "slm_responses", []) or []:
                    if r.get("answer"):
                        sources.append(f"  • {r.get('slm_name', '?')}: {str(r.get('answer', ''))[:60]}")
                source_text = "\n".join(sources) if sources else "  (không có SLM nào trả lời)"
                conf_pct = getattr(v, "confidence", 0.0) or 0.0
                cur_answer = str(cur_answer) + (
                    f"\n\nSCP đã kiểm tra:\n{source_text}\nĐộ tin cậy: {conf_pct:.0%} — chưa đạt ngưỡng (cần ≥70%)"
                )
            return DeliveryDecision(
                code=DecisionCode.DELIVER_VERIFIED_PASS,
                final_answer=cur_answer,
                gov_decision=gov_decision,
                reasoning=v.reasoning[:500] if v.reasoning else None,
                should_clear_fields=False,
            )

        # Branch 10: Standard Verified PASS Delivery
        return DeliveryDecision(
            code=DecisionCode.DELIVER_VERIFIED_PASS,
            final_answer=v.final_answer,
            gov_decision=gov_decision,
            reasoning=v.reasoning[:500] if v.reasoning else None,
            should_clear_fields=False,
        )
