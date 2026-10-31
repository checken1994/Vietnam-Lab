"""[RELEVANCE · 2026-10-31] Cổng "đáp án có đáp câu hỏi không" cho VerificationStage.

Đường generation: judge LLM nói "correctly answers" (1 lineage, có thể trùng lineage với
generator — HC-31). VERIFIED nay cần thêm bằng chứng ĐỘC LẬP về độ liên quan câu hỏi↔đáp án
(``scp/runtime/answer_relevance.py``: embedding L1 hash-lock, và khi E không tự quyết thì
một provider KHÁC lineage với generator). Không xác nhận được / lệch ⇒ withheld, nêu cả ý
kiến judge lẫn lý do của cổng (Branch 1e ``delivery_rules``); KHÔNG sửa đáp án.
Phép tính đã được math-verify xác nhận 2 tầng (đáp án chứa kết quả của chính biểu thức câu
hỏi) ⇒ không chạy lại cổng này.
"""
from __future__ import annotations

import logging
from typing import Any

from scp.runtime import answer_relevance as ar

logger = logging.getLogger(__name__)

V98_KEY = ar.V98_KEY


def _applicable(ctx: Any, v: Any) -> str:
    """Câu trả lời ứng viên cho nhãn VERIFIED ⇒ trả đáp án; ngược lại ""."""
    if not ar._enabled("SCP_ANSWER_RELEVANCE"):
        return ""
    if str(getattr(v, "verdict", "") or "") not in ("PASS", "UNKNOWN") or getattr(ctx, "is_chatbot_lane", False):
        return ""
    mv = (getattr(ctx, "v98_context", None) or {}).get("math_verification")
    if isinstance(mv, dict) and mv.get("action") in ("deliver_verified", "withhold"):
        return ""  # cổng phép tính đã quyết (khớp 2 tầng ⇒ đáp án chứa kết quả của câu hỏi)
    answer = str(getattr(v, "final_answer", "") or getattr(ctx, "ai_answer", "") or "").strip()
    if not answer or answer.startswith("[SCP:") or not str(getattr(ctx.req, "question", "") or "").strip():
        return ""
    return answer


def _gateway(ctx: Any) -> Any:
    gw_fn = ctx.resolve("get_gateway", None) if hasattr(ctx, "resolve") else None
    try:
        if gw_fn is not None:
            return gw_fn()
        from scp.llm_gateway.client import peek_gateway

        return peek_gateway()
    except Exception:  # không có gateway ⇒ không có reader độc lập
        logger.debug("[RELEVANCE] gateway unavailable", exc_info=True)
        return None


async def run_relevance_verification(ctx: Any, v: Any) -> ar.RelevanceAssessment | None:
    """Đánh giá + ghi ``ctx.v98_context['answer_relevance']`` (+ bài học khi không xác nhận)."""
    answer = _applicable(ctx, v)
    if not answer:
        return None
    gen_label = str(getattr(ctx, "llm_provider", "") or "")
    gen_is_llm = str(getattr(ctx, "generation_status", "") or "") == "llm"
    gw = _gateway(ctx)

    async def _reader(q: str, a: str) -> tuple[str, str, str]:
        return await ar.independent_reader(q, a, gw, generator_label=gen_label, generator_is_llm=gen_is_llm)

    question = str(getattr(ctx.req, "question", "") or "")
    res = await ar.assess(question, answer, path="generation", reader=_reader)
    info = res.to_dict()
    info["judge_verdict"] = str(getattr(v, "verdict", "") or "")
    ctx.v98_context[V98_KEY] = info
    logger.info("[RELEVANCE] status=%s score=%s reader=%s", res.status, info["score"], res.reader_verdict or "-")
    if not res.relevant or res.embed_dissent:  # chặn / bất định / lineage lệch nhau ⇒ bài học
        ar.record_relevance_lesson(ctx, res, generator=gen_label, judge_opinion=info["judge_verdict"])
    return res


def withheld_label(info: dict[str, Any]) -> str:
    return (
        "[SCP: Answer withheld — chưa chứng minh được câu trả lời đáp đúng câu hỏi: "
        f"judge={info.get('judge_verdict', '?')}; {info.get('status', '?')}: {info.get('reason', '')}]"
    )
