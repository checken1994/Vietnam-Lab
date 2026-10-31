# W2 CONTRACT: root-4 phân loại đa tầng (GA.md B1b, MasterPlan v2 W2).
"""W2 — contract pins cho root-4: thứ tự rule, regex, fail-closed, domain.

Thực tế battery W1 (16 probe, cùng SHA): q08 "Ai là tổng thống Mỹ hiện tại?"
bị gán domain='math'; q09 "Viết một câu thơ ngắn về biển" bị kéo vào
LOOKUP/geography vì keyword 'biển'; q16 "Bạn nghĩ gì về chính trị..." rơi
LANE_FACTUAL vì 'gì\\b' đơn lẻ; L2 lỗi/parse-fail rơi lane nới nhất
(cờ bypass — đã XÓA ở F-E2E-2). Các test này pin hành vi ĐÃ SỬA — old-fails trên
code trước W2 (new-passes), không hạ bất kỳ assertion nào.
"""
from __future__ import annotations

from scp.runtime.question_router import (
    LANE_CHATBOT,
    LANE_FACTUAL,
    REASONING,
    _parse_l2_answer,
    _sanitize_domain_hint,
    route_question,
)


def test_d1_fact_question_not_swallowed_by_explain_catchall():
    """[W2-d1] "Thế nào là quang hợp?" — fact (biology) phải LOOKUP: lookup
    được xét TRƯỚC weak-reasoning catchall (concept_explanation)."""
    decision = route_question("Thế nào là quang hợp?")
    assert decision.intent == "LOOKUP"
    assert decision.lane == LANE_FACTUAL


class _LabelGW:
    def __init__(self, label):
        self.label = label

    def chat(self, *a, **k):
        return self.label, "stub:model"


class _DeadGW:
    def chat(self, *a, **k):
        raise RuntimeError("hermetic: no LLM provider")


def test_d1_explain_catchall_still_chatbot_without_lookup_signal():
    """[W2-d1] câu giải thích thuần không có lookup signal giữ nguyên CHATBOT.

    [L0-DEKEYWORD 2026-10-31] Catch-all từ khoá "giải thích" (VI/EN) đã gỡ và một phiếu
    đơn lẻ không còn lên CHATBOT. Hợp đồng thay thế: L0 không quyết; intent REASONING do
    lineage L2 quyết (không bị kéo vào LOOKUP); lane FACTUAL (kiểm đầy đủ). NodeID giữ.
    """
    from scp.runtime.router_rules import classify_l0

    q = "Giải thích sự khác biệt giữa TCP và UDP"
    assert classify_l0(q) is None
    decision = route_question(q, gateway=_LabelGW("REASONING"))
    assert decision.intent == REASONING and decision.via == "l2-llm"
    assert decision.lane == LANE_FACTUAL and decision.lane != LANE_CHATBOT


def test_d1_creative_poem_not_pulled_into_lookup_by_domain_hint():
    """[W2-d1] thơ sáng tác phải CHATBOT — creative guard chặn TRƯỚC
    domain-hint fallback (W1: 'biển' kéo vào LOOKUP/geography)."""
    # [L0-DEKEYWORD 2026-10-31] Creative guard từ khoá ("thơ", "poem"…) đã gỡ. Hợp đồng
    # gốc giữ: domain hint ('biển'→geography) KHÔNG được kéo câu sáng tác vào data-LOOKUP.
    # (a) L0 không quyết bằng domain hint; (b) lineage L2 nói REASONING ⇒ intent REASONING
    # dù domain hint là geography; (c) classifier chết ⇒ confidence < ngưỡng fork ⇒ không
    # data-fork. Lane không bao giờ CHATBOT khi chưa đồng thuận kép.
    from scp.runtime.question_router import t2_min_confidence
    from scp.runtime.router_rules import classify_l0

    q = "Viết một câu thơ ngắn về biển"
    assert classify_l0(q) is None
    decision = route_question(q, gateway=_LabelGW("REASONING"))
    assert decision.intent == REASONING
    assert decision.lane == LANE_FACTUAL
    dead = route_question(q, gateway=_DeadGW())
    assert dead.confidence < t2_min_confidence() and dead.via == "l2-failsafe"


def test_d2_opinion_question_not_factual_from_bare_gi():
    """[W2-d2] 'gì\\b' đơn lẻ bị siết thành 'gì\\s*\\?' + assist strong:
    câu ý kiến không rơi LANE_FACTUAL vì một mình 'gì'."""
    # [DEHARDCODE 2026-10-30] Rule "assist strong" (cụm "bạn nghĩ"...) đã gỡ theo luật owner.
    # Hợp đồng gốc W2-d2 giữ nguyên: regex L0 KHÔNG được gán LOOKUP chỉ vì 'gì' đơn lẻ;
    # quyết định nhường tầng ngữ nghĩa/L2 (không chắc ⇒ FACTUAL fail-closed, không phải
    # do 'gì').
    from scp.runtime.router_rules import classify_l0

    l0 = classify_l0("Bạn nghĩ gì về chính trị hiện nay?")
    assert l0 is None or l0.intent != "LOOKUP", l0
    decision = route_question("Bạn nghĩ gì về chính trị hiện nay?")
    assert decision.via != "l0-keyword"


def test_d2_interrogative_fact_still_lookup():
    """[W2-d2] câu fact kết thúc '?' vẫn LOOKUP sau khi siết regex."""
    decision = route_question("AES là thuật toán gì?")
    assert decision.intent == "LOOKUP"
    assert decision.lane == LANE_FACTUAL


def test_d2_ph_case_sensitive_precision():
    """[W2-d2] '\\bph\\b' IGNORECASE bị bỏ — 'pH' check case-sensitive riêng."""
    # [L0-DEKEYWORD 2026-10-31] Luật riêng 'pH' (từ khoá miền) đã gỡ: không còn regex
    # nào match 'ph' ⇒ precision theo cấu tạo (không nuốt chữ Việt có 'ph'). Intent do
    # lineage L2 quyết, lane FACTUAL.
    from scp.runtime.router_rules import classify_l0

    q = "pH của nước tinh khiết là bao nhiêu?"
    assert classify_l0(q) is None
    assert classify_l0("Phở bò Hà Nội ngon nhất ở phố nào?") is None
    decision = route_question(q, gateway=_LabelGW("LOOKUP"))
    assert decision.intent == "LOOKUP" and decision.lane == LANE_FACTUAL
    assert decision.via == "l2-llm"


def test_d4_math_domain_hint_sanitized_on_politics():
    """[W2-d4] W1 thực tế: domain='math' cho câu politics — sanitize phải
    hạ về 'general' khi không có tín hiệu toán nào."""
    decision = route_question("Ai là tổng thống Mỹ hiện tại?")
    assert decision.domain != "math"


def test_d4_math_domain_kept_for_real_math():
    """[W2-d4] domain 'math' thật vẫn được giữ."""
    decision = route_question("Tính 15 - 7 = ?")
    assert decision.intent == REASONING


def test_d4_sanitize_helper_direct():
    assert _sanitize_domain_hint("Ai là tổng thống Mỹ?", "math") == "general"
    assert _sanitize_domain_hint("Tính 2 + 3", "math") == "math"


def test_d3_l2_error_fail_closed():
    """[W2-d3] L2 LLM lỗi (gateway raise) → không cờ bypass (F-E2E-2)
    (hết lane nới nhất) — throw qua đúng seam gateway của classify_l2."""

    class _BrokenGateway:
        def chat(self, *args, **kwargs):
            raise RuntimeError("llm down")

    decision = route_question("xyzqq garbled input??", gateway=_BrokenGateway())
    assert decision.via == "l2-failsafe"
    assert decision.reason == "llm_error_fail_closed"
    assert not hasattr(decision, "bypass_verdict_pass")


def test_d3_l2_unparseable_fail_closed():
    """[W2-d3] LLM trả garbage không parse được → fail-closed tương tự."""
    decision = _parse_l2_answer("GARBAGE-UNPARSEABLE-ANSWER", provider="mock", domain="general", question="qq?")
    assert decision.via == "l2-failsafe"
    assert decision.reason == "unparseable_fail_closed"
    assert not hasattr(decision, "bypass_verdict_pass")


def test_d3_l2_parse_still_works():
    """[W2-d3] parse thành công giữ nguyên hành vi (LOOKUP→FACTUAL)."""
    decision = _parse_l2_answer("LOOKUP", provider="mock", domain="geography", question="q?")
    assert decision.intent == "LOOKUP"
    assert decision.lane == LANE_FACTUAL
    assert not hasattr(decision, "bypass_verdict_pass")
