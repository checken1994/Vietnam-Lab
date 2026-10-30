"""DEHARDCODE — cổng ngữ nghĩa cho câu mà L0 (cú pháp + an ninh) không quyết được.

TẠI SAO (luật owner cấp cao nhất, 30/10/2026): SCP không được chứa mẫu câu hỏi
tự nhiên trong code để định tuyến. Trước đây lời chào / câu hỏi danh tính /
smalltalk được đẩy sang ``LANE_CHATBOT`` bằng regex cụm từ trong
``router_rules.py`` và delivery coi lane đó là "hội thoại thuần" ⇒ giao câu
trả lời KHÔNG kiểm chứng. Module này thay cơ chế đó bằng cơ chế tổng quát đã
được review (GĐ2): ``LineageEmbed`` (L1, embedding đa ngôn ngữ, centroid từ
dữ liệu) + ``SLMLineage`` (L2, gateway) → ``arbitrate`` (Rule 3: CHATBOT CHỈ
khi CẢ HAI lineage còn sống cùng báo CHATBOT trên ngưỡng kép).

Fail-closed:
- L1 chết/DEGRADED (chưa provision model — trạng thái mặc định) ⇒ ``None`` ⇒
  caller giữ đường cũ (L2 LOOKUP/REASONING), KHÔNG BAO GIỜ thành hội thoại
  thuần; delivery chỉ coi là hội thoại khi ``via == CONSENSUS_VIA``.
- L1 báo SECURITY ⇒ trả quyết định SECURITY (Rule 1; chỉ chặt hơn).
- L1 không báo CHATBOT trên ngưỡng ⇒ KHÔNG gọi L2 (Rule 3 không thể đạt —
  tiết kiệm 1 call mạng/quota, khớp guard "accidental network attempt").
- Mọi lỗi ⇒ ``None``.

Module KHÔNG chứa cụm từ / câu mẫu nào; lineage SLM dùng prompt mô tả ngữ
nghĩa (``lineage_slm.L2_SYSTEM_PROMPT``).
"""
from __future__ import annotations

import logging
from typing import Any

from scp.runtime.router_rules import LANE_CHATBOT, LANE_SECURITY
from scp.runtime.semantic_router.arbitration import (
    arbitrate,
    chatbot_consensus_threshold,
    map_label_to_lane,
)
from scp.runtime.semantic_router.contract import SemanticRouteDecision

logger = logging.getLogger("scp.runtime.semantic_router.consensus")

#: ``via`` duy nhất mà delivery được phép coi là hội thoại thuần (arbitration
#: Rule 3). Hằng số này là hợp đồng giữa router và ``DeliveryRuleEngine``.
CONSENSUS_VIA = "arb:chatbot-dual-consensus"

__all__ = ["CONSENSUS_VIA", "l1_verdict", "semantic_gate_sync", "semantic_gate_async"]


def l1_verdict(question: str) -> Any | None:
    """Verdict L1 còn sống, hoặc ``None`` khi lineage chết/DEGRADED/lỗi."""
    try:
        from scp.runtime.semantic_router import lineage_embed

        verdict = lineage_embed.classify(question)
    except Exception:  # noqa: BLE001 — lineage chết không kéo sập router
        logger.debug("[DEHARDCODE] L1 classify raised — treated as dead", exc_info=True)
        return None
    if getattr(verdict, "degraded", True):
        return None
    return verdict


def _l1_gate(question: str) -> tuple[Any | None, SemanticRouteDecision | None, bool]:
    """(l1, quyết định SECURITY nếu có, có cần hỏi L2 không)."""
    l1 = l1_verdict(question)
    if l1 is None:
        return None, None, False
    lane = map_label_to_lane(getattr(l1, "label", ""))
    if lane == LANE_SECURITY:
        return l1, arbitrate(l1, None), False
    try:
        conf = float(getattr(l1, "confidence", 0.0))
    except (TypeError, ValueError):
        conf = 0.0
    need_l2 = lane == LANE_CHATBOT and conf >= chatbot_consensus_threshold()
    return l1, None, need_l2


def semantic_gate_sync(question: str, gateway: Any = None) -> SemanticRouteDecision | None:
    """Đường sync (``route_question`` — /ask lookup_stage, /v1 compat dùng đường này).

    Cùng hợp đồng với bản async: L1 không báo CHATBOT trên ngưỡng ⇒ không gọi mạng.
    Chỉ khi cần L2 mới chạy coroutine lineage (thread riêng nếu đang trong event loop,
    cùng mẫu với ``question_router.classify_l2``) — nếu không, đồng thuận kép sẽ
    không bao giờ tới được từ /ask và cơ chế tổng quát thành placebo.
    """
    _l1, sec, need_l2 = _l1_gate(question)
    if sec is not None or not need_l2:
        return sec
    import asyncio

    try:
        try:
            running = asyncio.get_running_loop().is_running()
        except RuntimeError:
            running = False
        coro_fn = lambda: semantic_gate_async(question, gateway=gateway)  # noqa: E731
        if running:
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(lambda: asyncio.run(coro_fn())).result()
        return asyncio.run(coro_fn())
    except Exception:  # noqa: BLE001 — lỗi ⇒ không đồng thuận (fail-closed)
        logger.debug("[DEHARDCODE] sync semantic gate failed — no consensus", exc_info=True)
        return None


async def semantic_gate_async(question: str, gateway: Any = None) -> SemanticRouteDecision | None:
    """Đường production: L1 trước; chỉ khi L1 báo CHATBOT trên ngưỡng mới hỏi L2.

    Trả ``SemanticRouteDecision`` khi lane là SECURITY hoặc CHATBOT (đồng thuận
    kép); mọi ca khác ``None`` (caller giữ đường xác minh).
    """
    l1, sec, need_l2 = _l1_gate(question)
    if sec is not None:
        return sec
    if l1 is None or not need_l2:
        return None
    try:
        from scp.runtime.semantic_router.lineage_slm import classify_l2

        outcome = await classify_l2(question, gateway=gateway)
        l2 = None if outcome.degraded else outcome.verdict
    except Exception:  # noqa: BLE001
        logger.debug("[DEHARDCODE] L2 lineage raised — treated as dead", exc_info=True)
        l2 = None
    decision = arbitrate(l1, l2)
    if decision.lane in (LANE_CHATBOT, LANE_SECURITY):
        return decision
    return None
