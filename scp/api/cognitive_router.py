import logging

logger = logging.getLogger("scp.cognitive_router")

async def run_pre_judge_hooks(question: str, context_dict: dict):
    # 1. Threat Detector (Security)
    try:
        from scp.security.threat_detector import ThreatDetector
        td = ThreatDetector()
        threat_score = await td.analyze(ip="127.0.0.1", headers={}, body=question)
        context_dict["threat_score"] = threat_score
        if threat_score and threat_score.get("risk_level") == "CRITICAL":
            logger.warning(f"[SECURITY] ThreatDetector flagged query: {threat_score}")
    except Exception as e:
        logger.debug(f"ThreatDetector hook failed: {e}", exc_info=True)

    # 2. Why Engine (Meta) — [W2b-A 2026-10-30] MỌI câu đều được hỏi "Tại sao?" ở mức
    # risk-tier (W1.2: mức hỏi tỉ lệ hậu quả, không tỉ lệ việc câu có chữ "why");
    # nhánh keyword `"tại sao"/"why" in question` (placebo) đã xoá. Tier "high" mới
    # lập + lưu plan đầy đủ: create_verification_plan GHI câu hỏi vào DB và vòng nền
    # đi tra nguồn ngoài — không đẩy MỌI câu ra ngoài.
    tier, why_reason = why_risk_tier(question, context_dict.get("threat_score"))
    context_dict["why_risk_tier"] = {"tier": tier, "reason": why_reason}
    try:
        if tier == "high":
            from scp.meta.why_engine import WhyEngine
            why = WhyEngine()
            plan = why.create_verification_plan(question)
            if plan:
                context_dict["why_plan"] = plan.__dict__
    except Exception as e:
        logger.debug(f"WhyEngine hook failed: {e}", exc_info=True)

    return context_dict


#: Miền có hậu quả lớn khi sai (tiền, sức khoẻ, pháp lý, an ninh) — id của
#: scp.data_sources.domain_classifier (bộ phân loại miền có sẵn, không viết mới).
HIGH_CONSEQUENCE_DOMAINS = frozenset(
    {"finance", "crypto", "medical", "health", "medicine", "legal", "law", "security", "cybersecurity"}
)
#: Tác nhân request bị ThreatDetector gắn cờ (ThreatSignal.agent_type).
HIGH_RISK_AGENT_TYPES = frozenset({"ai_agent_2026", "anonymizing_proxy", "injector"})


def why_risk_tier(question: str, threat_signal: object = None) -> tuple[str, str]:
    """Risk-tier của câu hỏi cho WHY: ("high"|"low", reason). Không raise."""
    agent_type = str(getattr(threat_signal, "agent_type", "") or "")
    if agent_type in HIGH_RISK_AGENT_TYPES and float(getattr(threat_signal, "confidence", 0.0) or 0.0) >= 0.5:
        return "high", f"threat:{agent_type}"
    try:
        from scp.data_sources.domain_classifier import classify_top1
        domain = str(classify_top1(question or "") or "general")
    except Exception as e:  # classifier hỏng ⇒ không biết hậu quả ⇒ hỏi kỹ (fail-closed)
        logger.debug(f"why_risk_tier domain classify failed: {e}", exc_info=True)
        return "high", "domain_classifier_error"
    if domain in HIGH_CONSEQUENCE_DOMAINS:
        return "high", f"domain:{domain}"
    return "low", f"domain:{domain}"
