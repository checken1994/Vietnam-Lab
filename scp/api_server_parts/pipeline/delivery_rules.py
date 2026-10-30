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
from scp.api_server_parts.pipeline.generation_status import (
    GEN_INTERNAL_ERROR,
    LABEL_GENERATION_INTERNAL,
    LABEL_GENERATION_UNAVAILABLE,
    LLM_NOT_GENERATED,
)
from scp.security import output_behavior_gate as gd1_gate

logger = logging.getLogger(__name__)

#: (b) cú pháp thuần — chữ số (mọi hệ chữ số Unicode) hoặc URL. KHÔNG phải mẫu câu hỏi.
_STRUCTURAL_CLAIM_MARKERS = re.compile(r"\d|https?://|www\.", re.IGNORECASE)


class DecisionCode:
    ERR_SECURITY_VIOLATION = "ERR_SECURITY_VIOLATION"
    ERR_GOVERNANCE_MISSING = "ERR_GOVERNANCE_MISSING"
    ERR_GOVERNANCE_ESCALATE = "ERR_GOVERNANCE_ESCALATE"
    ERR_GOVERNANCE_DEGRADED = "ERR_GOVERNANCE_DEGRADED"
    ERR_FACTUAL_FAILED = "ERR_FACTUAL_FAILED"
    ERR_WHY_GATE_REJECTED = "ERR_WHY_GATE_REJECTED"
    ERR_GENERATION_UNAVAILABLE = "ERR_GENERATION_UNAVAILABLE"
    ERR_GENERATION_INTERNAL = "ERR_GENERATION_INTERNAL"
    ERR_MATH_UNVERIFIED = "ERR_MATH_UNVERIFIED"
    DELIVER_UNVERIFIED_ABSTAIN = "DELIVER_UNVERIFIED_ABSTAIN"
    DELIVER_CHATBOT = "DELIVER_CHATBOT"
    DELIVER_VERIFIED_PASS = "DELIVER_VERIFIED_PASS"
    GD1_REDACT_MEMORY = "GD1_REDACT_MEMORY"


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
    def is_strictly_conversational(ctx: Any) -> bool:
        """Hội thoại thuần (được giao không cần kiểm chứng) — predicate TỔNG QUÁT.

        [DEHARDCODE 2026-10-30] Luật owner: không mẫu câu hỏi trong code. Bản cũ
        dùng ~25 danh sách cụm từ (chào/danh tính/cảm ơn/"ở đâu"/...) và coi
        mọi REASONING+general là hội thoại (đường bypass cho câu bất định). Nay
        CHỈ True khi CẢ HAI điều kiện cấu trúc:
          1. router đạt đồng thuận CHATBOT 2 lineage ngữ nghĩa
             (``via == CONSENSUS_VIA``, arbitration Rule 3) — một lineage đơn lẻ
             (kể cả L2 LLM) hay classifier chết KHÔNG mở được bypass;
          2. câu hỏi không mang dấu hiệu claim thế giới dạng cấu trúc
             (chữ số, URL) — cú pháp, không phụ thuộc ngôn ngữ.
        Còn lại ⇒ False ⇒ đường xác minh / withheld trung thực.
        """
        from scp.runtime.semantic_router.consensus import CONSENSUS_VIA

        rd = getattr(ctx, "route_decision", None)
        if rd is None:
            return False
        if str(getattr(rd, "lane", "") or "") != question_router.LANE_CHATBOT:
            return False
        if str(getattr(rd, "via", "") or "") != CONSENSUS_VIA:
            return False
        q = str(getattr(getattr(ctx, "req", None), "question", "") or "")
        # (b) cú pháp: chữ số / URL = có thể chứa claim kiểm chứng được.
        return not _STRUCTURAL_CLAIM_MARKERS.search(q)

    @staticmethod
    def _gd1_block(reasoning: str, metadata: dict[str, Any]) -> DeliveryDecision:
        """DeliveryDecision withhold chuẩn của GĐ1 gate (fail-closed, clear fields)."""
        return DeliveryDecision(
            code=DecisionCode.ERR_SECURITY_VIOLATION,
            final_answer="[SCP: Answer withheld — Output Behavior Gate]",
            gov_decision="KILL", reasoning=reasoning, should_clear_fields=True,
            extra_metadata={"gd1_output_gate": metadata},
        )

    @staticmethod
    def _enforce_output_gate(ctx: Any, v: Any) -> DeliveryDecision | None:
        """GĐ1: scan mọi text ứng viên trả lời; match secret/canary → BLOCK —
        không lane nào được miễn trừ trên thực thể dữ liệu nhạy cảm (match là match);
        lỗi gate → withhold fail-closed; log chỉ ref + sha256[:12], không lộ giá trị."""
        candidates = [c for c in (getattr(v, "final_answer", None), getattr(ctx, "ai_answer", None))
                      if isinstance(c, str) and c and not c.startswith("[SCP:")]
        if not candidates:
            return None
        try:
            secrets, canaries = gd1_gate.collect_runtime_secrets(), gd1_gate.collect_runtime_canaries()
            if not secrets and not canaries:
                return None
            for text in candidates:
                res = gd1_gate.scan_output(text, secrets, canaries)
                if res.verdict == gd1_gate.VERDICT_BLOCK:
                    logger.warning("[GD1] Output Behavior Gate BLOCK refs=%s canary_alert=%s",
                                   list(res.matched_refs), res.canary_alert)
                    return DeliveryRuleEngine._gd1_block(
                        "[SCP: Answer withheld — sensitive data entity in output]",
                        {"matched": list(res.matched_refs), "canary_alert": res.canary_alert,
                         "secondary_action": res.secondary_action})
            return None
        except Exception:
            logger.exception("[GD1] Output gate error — enforcing fail-closed withhold")
            return DeliveryRuleEngine._gd1_block(
                "[SCP: Answer withheld — output gate failure (fail-closed)]", {"error": True})

    @staticmethod
    def enforce_memory_write_gate(text: str, secrets: dict[str, str] | None = None,
                                  canaries: list[str] | None = None) -> DeliveryDecision:
        """GĐ1 surface memory_write: owner 2.B mặc định REDACT (SCP_GD1_MEMORY_REDACT
        mặc định 1); =0 hoặc redact không sạch → BLOCK (fail-closed)."""
        secrets = gd1_gate.collect_runtime_secrets() if secrets is None else secrets
        canaries = gd1_gate.collect_runtime_canaries() if canaries is None else canaries
        res = gd1_gate.scan_output(text, secrets, canaries)
        if gd1_gate.apply_surface_policy(res, "memory_write") == gd1_gate.ACTION_REDACT and (
            redacted := gd1_gate.redact_text(text, secrets, canaries)
        ) is not None:
            return DeliveryDecision(code=DecisionCode.GD1_REDACT_MEMORY, final_answer=redacted,
                                    gov_decision="ABSTAIN", reasoning="[GD1] memory_write secret redacted")
        if res.verdict == gd1_gate.VERDICT_BLOCK:
            return DeliveryDecision(code=DecisionCode.ERR_SECURITY_VIOLATION, final_answer=None,
                                    gov_decision="KILL", reasoning=None, http_exception=HTTPException(
                                        status_code=403, detail="Output Behavior Gate: secret in memory_write"))
        return DeliveryDecision(code=DecisionCode.DELIVER_VERIFIED_PASS, final_answer=text,
                                gov_decision="ALLOW", reasoning=None)

    @staticmethod
    def evaluate(
        ctx: Any,
        v: Any,
        gov_decision: str,
        mt_result: Any = None,
    ) -> DeliveryDecision:
        decision = DeliveryRuleEngine._evaluate_chain(ctx, v, gov_decision, mt_result)
        return DeliveryRuleEngine._label_ungenerated(ctx, decision)

    # [F-E2E-2 RC-2] Chỉ các withhold "không xác minh được"/generic mới được
    # đổi nhãn. KILL/GD1 (ERR_SECURITY_VIOLATION), WHY Gate, degraded, 403 giữ nguyên.
    _UNGENERATED_RELABEL_CODES = frozenset(
        {DecisionCode.ERR_GOVERNANCE_ESCALATE, DecisionCode.ERR_FACTUAL_FAILED, DecisionCode.DELIVER_CHATBOT}
    )

    @staticmethod
    def _label_ungenerated(ctx: Any, d: DeliveryDecision) -> DeliveryDecision:
        """(a) không-có-đáp-án ≠ (b) có-đáp-án-nhưng-không-xác-minh-được.

        Chỉ ĐỔI NHÃN một quyết định VỐN ĐÃ withheld khi generation ghi nhận LLM
        không sinh được gì VÀ không có bản nháp nào; không bao giờ biến withheld
        thành delivered, không đụng KILL. Withheld (a) xoá field như mọi withhold.
        """
        if str(getattr(ctx, "generation_status", "") or "") not in LLM_NOT_GENERATED:
            return d
        if str(getattr(ctx, "ai_answer", "") or "").strip():
            return d  # có bản nháp (vd. evidence) ⇒ (b) là nhãn đúng
        if d.http_exception is not None or d.code not in DeliveryRuleEngine._UNGENERATED_RELABEL_CODES:
            return d
        if not str(d.final_answer or "").startswith("[SCP: Answer withheld"):
            return d
        logger.info("[DeliveryRules] withheld: no answer generated (LLM unavailable), was %s", d.code)
        return DeliveryDecision(
            code=DecisionCode.ERR_GENERATION_UNAVAILABLE,
            final_answer=LABEL_GENERATION_UNAVAILABLE,
            gov_decision=d.gov_decision,
            reasoning=LABEL_GENERATION_UNAVAILABLE,
            should_clear_fields=True,
            extra_metadata=d.extra_metadata,
        )

    @staticmethod
    def _evaluate_chain(
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

        # Branch 1b: [GD1] Output Behavior Gate (roadmap GĐ1) — match GIÁ TRỊ
        # secret/canary runtime (exact/normalized/encoded) → withhold fail-closed,
        # không lane nào được miễn trừ với thực thể dữ liệu nhạy cảm.
        decision_1b = DeliveryRuleEngine._enforce_output_gate(ctx, v)
        if decision_1b is not None:
            return decision_1b

        # Branch 1c: [F-E2E-2] lỗi lập trình trong generation ⇒ fail-closed
        # (SAU Branch 1/1b: KILL và GD1 luôn thắng).
        if str(getattr(ctx, "generation_status", "") or "") == GEN_INTERNAL_ERROR:
            return DeliveryDecision(
                code=DecisionCode.ERR_GENERATION_INTERNAL,
                final_answer=LABEL_GENERATION_INTERNAL,
                gov_decision="ESCALATE",
                reasoning=LABEL_GENERATION_INTERNAL,
                should_clear_fields=True,
            )

        # Branch 1d: [MATH-VERIFY] phép tính lệch / không tính lại được ⇒ withheld
        # (SAU KILL/GD1/1c). Nêu cả hai giá trị; KHÔNG giao giá trị của SCP thay AI.
        mv = (getattr(ctx, "v98_context", None) or {}).get("math_verification")
        if isinstance(mv, dict) and mv.get("action") == "withhold":
            label = f"[SCP: Answer withheld — phép tính không kiểm chứng được: {mv.get('reason', '')}]"
            return DeliveryDecision(
                code=DecisionCode.ERR_MATH_UNVERIFIED, final_answer=label, gov_decision="ESCALATE",
                reasoning=label, should_clear_fields=True, extra_metadata={"math_verification": mv},
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
            is_w8_downgrade = bool(
                isinstance(getattr(v, "evidence", None), dict)
                and "time_signal_without_fresh_evidence" in v.evidence.get("abstain_reasons", [])
            )
            if ctx.is_chatbot_lane and not DeliveryRuleEngine.is_strictly_conversational(ctx) and not is_w8_downgrade:
                if not (w16_is_honest_abstain(final_ans_str) or w7_is_refusal(final_ans_str)):
                    if gov_decision == "ESCALATE":
                        logger.info(
                            "[DeliveryRules] Branch 3 withholding non-conversational unverified abstain in chatbot lane (governance: ESCALATE)"
                        )
                        return DeliveryDecision(
                            code=DecisionCode.ERR_GOVERNANCE_ESCALATE,
                            final_answer="[SCP: Answer withheld — không xác minh được câu trả lời (governance: ESCALATE)]",
                            gov_decision=gov_decision,
                            reasoning="[SCP: Answer withheld — không xác minh được câu trả lời (governance: ESCALATE)]",
                            should_clear_fields=True,
                        )
                    logger.info(
                        "[DeliveryRules] Branch 3 withholding non-conversational unverified abstain in chatbot lane: gov=%s",
                        gov_decision,
                    )
                    return DeliveryDecision(
                        code=DecisionCode.ERR_FACTUAL_FAILED,
                        final_answer="[SCP: Answer withheld]",
                        gov_decision=gov_decision,
                        reasoning="[SCP: Answer withheld]",
                        should_clear_fields=True,
                    )

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
            is_conversational = DeliveryRuleEngine.is_strictly_conversational(ctx)
            verdict_str = getattr(v, "verdict", "") or ""

            # Fail-closed guard: Non-conversational or factual queries in chatbot lane
            # must not deliver unverified answers without proof.
            if not is_conversational and verdict_str != "PASS":
                if gov_decision == "ESCALATE":
                    logger.info(
                        "[DeliveryRules] Chatbot lane withholding non-conversational unverified query (governance: ESCALATE): verdict=%s",
                        verdict_str,
                    )
                    return DeliveryDecision(
                        code=DecisionCode.ERR_GOVERNANCE_ESCALATE,
                        final_answer="[SCP: Answer withheld — không xác minh được câu trả lời (governance: ESCALATE)]",
                        gov_decision=gov_decision,
                        reasoning="[SCP: Answer withheld — không xác minh được câu trả lời (governance: ESCALATE)]",
                        should_clear_fields=True,
                    )
                logger.info(
                    "[DeliveryRules] Chatbot lane withholding non-conversational unverified query: verdict=%s gov=%s",
                    verdict_str,
                    gov_decision,
                )
                return DeliveryDecision(
                    code=DecisionCode.ERR_FACTUAL_FAILED,
                    final_answer="[SCP: Answer withheld]",
                    gov_decision=gov_decision,
                    reasoning="[SCP: Answer withheld]",
                    should_clear_fields=True,
                )

            cur_answer = v.final_answer
            if str(cur_answer).startswith("User Safety:") or str(cur_answer).strip() == "safe":
                cur_answer = None

            if not cur_answer or str(cur_answer).startswith("[SCP: Answer withheld"):
                if ctx.ai_answer and not str(ctx.ai_answer).startswith("User Safety:"):
                    cur_answer = ctx.ai_answer
                else:
                    # [DEHARDCODE] Không còn câu trả lời đóng hộp (tự giới thiệu SCP,
                    # nhắc lại tên người dùng bằng regex) — SCP chỉ trả lời từ dữ liệu.
                    # Không có bản nháp ⇒ withheld; _label_ungenerated đổi nhãn
                    # thành "LLM không sinh được" khi đúng là vậy.
                    return DeliveryDecision(
                        code=DecisionCode.ERR_FACTUAL_FAILED,
                        final_answer="[SCP: Answer withheld]",
                        gov_decision=gov_decision,
                        reasoning="[SCP: Answer withheld — no data-backed answer]",
                        should_clear_fields=True,
                    )

            reason = None
            if gov_decision == "ESCALATE":
                if verdict_str in ("FAIL", "FLAGGED", "DEGRADED", "UNCERTAIN", "ESCALATE"):
                    cur_answer = (
                        "[SCP: Answer withheld — không xác minh được câu trả lời (governance: ESCALATE)]"
                    )
                    reason = cur_answer
                    logger.info(
                        "[W3-e1] Chatbot lane withholding unverified benign answer: verdict=%s governance=ESCALATE",
                        verdict_str,
                    )
                elif is_conversational and verdict_str in ("UNKNOWN", "ABSTAIN", "PASS", ""):
                    gov_decision = "ABSTAIN"
                else:
                    return DeliveryDecision(
                        code=DecisionCode.ERR_GOVERNANCE_ESCALATE,
                        final_answer="[SCP: Answer withheld — không xác minh được câu trả lời (governance: ESCALATE)]",
                        gov_decision=gov_decision,
                        reasoning="[SCP: Answer withheld — không xác minh được câu trả lời (governance: ESCALATE)]",
                        should_clear_fields=True,
                    )
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
            # [DEHARDCODE ⇄ SEC-R2-02] governance thiếu/UNKNOWN ⇒ fail-closed như Branch 2/8.
            # Trước đây chỉ lane CHATBOT kiểm (lời chào đi lane đó nhờ regex); gỡ regex làm
            # lộ đường factual giao đáp án khi governance UNKNOWN — đóng ở đây.
            if not gov_decision or gov_decision == "UNKNOWN":
                return DeliveryDecision(
                    code=DecisionCode.ERR_GOVERNANCE_MISSING,
                    final_answer=None,
                    gov_decision="DENY",
                    reasoning=None,
                    http_exception=HTTPException(status_code=403, detail="Governance clearance missing — fail-closed"),
                )
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


def apply_delivery_headers(response: Any, res: Any, req_state: Any = None) -> None:
    """Attach x-scp-delivery and x-scp-decision-code headers matching openai_compat semantics."""
    if response is None or not hasattr(response, "headers"):
        return

    verdict = str(getattr(res, "verdict", None) or (res.get("verdict") if isinstance(res, dict) else "") or "")
    gov_decision = str(getattr(res, "governance_decision", None) or (res.get("governance_decision") if isinstance(res, dict) else "") or "")
    final_answer = str(getattr(res, "final_answer", None) or (res.get("final_answer") if isinstance(res, dict) else "") or "")
    lane = str(getattr(res, "lane", None) or (res.get("lane") if isinstance(res, dict) else "") or "")

    decision_code = getattr(req_state, "decision_code", None)
    is_conversational = getattr(req_state, "is_conversational", None)
    if is_conversational is None:
        is_conversational = (lane == "LANE_CHATBOT")

    if not decision_code:
        if verdict == "PASS":
            decision_code = DecisionCode.DELIVER_CHATBOT if is_conversational else DecisionCode.DELIVER_VERIFIED_PASS
        elif gov_decision in ("KILL", "DENY"):
            decision_code = DecisionCode.ERR_SECURITY_VIOLATION
        elif gov_decision == "ESCALATE":
            decision_code = DecisionCode.ERR_GOVERNANCE_ESCALATE
        else:
            decision_code = DecisionCode.ERR_FACTUAL_FAILED

    is_withheld = False
    if gov_decision in ("KILL", "DENY") or verdict in ("FAIL", "FLAGGED"):
        is_withheld = True
    elif final_answer.startswith("[SCP: Answer withheld"):
        is_withheld = True
    elif decision_code in (
        DecisionCode.ERR_SECURITY_VIOLATION,
        DecisionCode.ERR_GOVERNANCE_ESCALATE,
        DecisionCode.ERR_GOVERNANCE_DEGRADED,
        DecisionCode.ERR_FACTUAL_FAILED,
        DecisionCode.ERR_WHY_GATE_REJECTED,
        DecisionCode.ERR_GOVERNANCE_MISSING,
        DecisionCode.ERR_GENERATION_UNAVAILABLE,
        DecisionCode.ERR_GENERATION_INTERNAL,
        DecisionCode.ERR_MATH_UNVERIFIED,
    ):
        is_withheld = True
    elif verdict in ("ABSTAIN", "DEGRADED", "UNKNOWN"):
        if not is_conversational or gov_decision == "ESCALATE":
            is_withheld = True

    if is_withheld:
        delivery = "withheld"
        if decision_code in (DecisionCode.DELIVER_VERIFIED_PASS, DecisionCode.DELIVER_CHATBOT):
            decision_code = DecisionCode.ERR_SECURITY_VIOLATION if gov_decision in ("KILL", "DENY") else DecisionCode.ERR_FACTUAL_FAILED
    elif is_conversational and decision_code in (
        DecisionCode.DELIVER_CHATBOT,
        DecisionCode.DELIVER_UNVERIFIED_ABSTAIN,
    ):
        delivery = "conversational"
    elif decision_code == DecisionCode.DELIVER_UNVERIFIED_ABSTAIN:
        delivery = "unverified_abstain"
    else:
        delivery = "verified"

    response.headers["x-scp-delivery"] = delivery
    response.headers["x-scp-decision-code"] = str(decision_code)
    # [esc-auto slice 2] Autonomous evidence ticket (GET /ask/evidence/{id}).
    ticket = getattr(req_state, "scp_evidence_ticket", None)
    if isinstance(ticket, dict) and is_withheld:
        if ticket.get("ticket_id"):
            response.headers["x-scp-evidence-ticket"] = str(ticket["ticket_id"])
        response.headers["x-scp-evidence-status"] = str(ticket.get("status") or "")
        if ticket.get("code"):
            response.headers["x-scp-evidence-code"] = str(ticket["code"])

