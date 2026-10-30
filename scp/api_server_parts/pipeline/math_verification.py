"""[MATH-VERIFY · 2026-10-31] Cổng kiểm chứng phép tính cho VerificationStage.

Chính sách owner (31-10, "AI và SCP đều sai thì ai kiểm?"):
- AI và SCP LỆCH ⇒ KHÔNG ghi đè bằng giá trị SCP; withheld, nêu cả hai giá trị.
- VERIFIED chỉ khi khớp HAI tầng: biểu thức (cách hiểu câu hỏi độc lập với AI)
  và giá trị (tính lại bằng 2 bộ tính độc lập — ``math_claims.evaluate_dual``).
- Cách hiểu độc lập = (1) câu hỏi tự nó là biểu thức ký hiệu (SCP đọc trực tiếp,
  không diễn giải từ ngữ), hoặc (2) một provider KHÁC lineage với provider sinh
  đáp án được hỏi "viết câu hỏi thành MỘT biểu thức, KHÔNG tính" (có giới hạn
  thời gian, tắt bằng ``SCP_MATH_INDEPENDENT_READER=0``). Không có ⇒ SAME_LINEAGE:
  không bao giờ được nhãn verified tất định.
- Judge LLM KHÔNG được ghi đè quyết định withheld của cổng này.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from scp.runtime import math_claims as mc
from scp.runtime.math_lessons import LESSON_STATUSES, lineage_of, record_math_lesson

logger = logging.getLogger(__name__)

V98_KEY = "math_verification"
#: Nguồn độc lập khác lineage nói câu hỏi KHÔNG phải phép tính ⇒ phương trình trong
#: đáp án là phụ (vd "nhiệm kỳ 2021 + 4 = 2025"): đã tính lại giá trị, phần còn lại do judge.
READER_NONE = "reader_none:"

_READER_PROMPT = (
    "Rewrite the user's question as exactly ONE arithmetic expression using only digits, "
    "+ - * / ( ) and sqrt(). Do NOT compute or simplify it. If the question is not an "
    "arithmetic computation, reply NONE. Reply with the expression only."
)


def _enabled(name: str, default: str = "1") -> bool:
    return os.environ.get(name, default).strip().lower() not in {"0", "false", "off", "no"}


async def independent_reading(ctx: Any) -> tuple[mc.ParsedExpression | None, str, str]:
    """Hỏi provider KHÁC lineage đọc câu hỏi thành biểu thức. Trả (expr, lineage, note)."""
    if not _enabled("SCP_MATH_INDEPENDENT_READER"):
        return None, "", "independent reader disabled"
    gw_fn = ctx.resolve("get_gateway", None) if hasattr(ctx, "resolve") else None
    try:
        if gw_fn is not None:
            gw = gw_fn()
        else:
            from scp.llm_gateway.client import peek_gateway

            gw = peek_gateway()
    except Exception:  # noqa: BLE001 — gateway không khởi tạo được ⇒ không có nguồn độc lập
        logger.debug("[MATH-VERIFY] gateway unavailable", exc_info=True)
        return None, "", "no gateway"
    if gw is None:
        return None, "", "no gateway"
    gen_label = str(getattr(ctx, "llm_provider", "") or "")
    gen_lineage = lineage_of(gen_label) if str(getattr(ctx, "generation_status", "")) == "llm" else ""
    gen_provider = gen_label.split(":", 1)[0].lower()
    try:
        names = [n for n in (gw.get_active_provider_names() or []) if str(n).lower() != gen_provider]
    except Exception:  # noqa: BLE001
        names = []
    if not names:
        return None, "", "no provider of another lineage available"
    timeout = float(os.environ.get("SCP_MATH_INDEPENDENT_TIMEOUT_S", "8") or 8)
    question = str(getattr(ctx.req, "question", "") or "")
    for name in names[:2]:
        try:
            text, label = await asyncio.wait_for(
                gw.chat(question, system_prompt=_READER_PROMPT, task="chat", preferred_provider=name),
                timeout=timeout,
            )
        except Exception as exc:  # noqa: BLE001 — nguồn độc lập lỗi ⇒ thử nguồn khác / không có
            logger.info("[MATH-VERIFY] independent reader %s failed: %s", name, type(exc).__name__)
            continue
        lin = lineage_of(label)
        if not text or not label or label == "none" or (gen_lineage and lin == gen_lineage):
            continue  # cùng lineage (vd hedge trả về chính provider sinh) ⇒ không độc lập
        if str(text).strip().upper().startswith("NONE"):
            return None, f"{READER_NONE}{lin}", f"independent reader ({lin}) says not an arithmetic computation"
        expr = mc.parse_independent_expression(text)
        if expr is None:
            return None, lin, f"independent reader ({lin}) gave no parseable expression"
        return expr, f"independent:{lin}", ""
    return None, "", "no independent-lineage reading obtained"


async def run_math_verification(ctx: Any) -> mc.MathAssessment | None:
    """Đánh giá đáp án hiện tại; ghi kết quả vào ``ctx.v98_context['math_verification']``."""
    if not _enabled("SCP_MATH_VERIFY"):
        return None
    answer = str(getattr(ctx, "ai_answer", "") or "")
    if not answer.strip() or answer.startswith("[SCP:"):
        return None
    question = str(getattr(ctx.req, "question", "") or "")
    res = mc.assess(question, answer)
    # Đáp án không có phương trình nhưng câu hỏi có chữ số: có thể là phép tính viết bằng
    # chữ (mọi ngôn ngữ) ⇒ hỏi nguồn độc lập; nếu đọc được phép tính, đáp án phải chứa kết quả.
    if res.status == mc.SAME_LINEAGE or (res.status == mc.NO_ARITHMETIC and mc.has_digit(question)):
        ref, lineage, note = await independent_reading(ctx)
        if ref is not None:
            res = mc.assess(question, answer, reference=ref, reference_lineage=lineage)
        # NO_ARITHMETIC + không ai đọc ra phép tính ⇒ không phải việc của cổng toán (vùng UNKNOWN (e))
        elif res.status != mc.NO_ARITHMETIC:
            res.reference_lineage = lineage if lineage.startswith(READER_NONE) else ""
            if note:
                res.reason = f"{res.reason}; {note}"
    if res.status != mc.NO_ARITHMETIC:
        ctx.v98_context[V98_KEY] = res.to_dict()
        logger.info("[MATH-VERIFY] status=%s lineage=%s", res.status, res.reference_lineage or "-")
    if res.status in LESSON_STATUSES and not is_incidental(res):
        record_math_lesson(ctx, res.to_dict())
    return res


def is_incidental(res: mc.MathAssessment) -> bool:
    return res.status == mc.SAME_LINEAGE and res.reference_lineage.startswith(READER_NONE)


def must_withhold(res: mc.MathAssessment | None) -> bool:
    """Lệch / không tính lại được / chỉ cùng lineage / biểu thức chưa trùng ⇒ không giao.
    Ngoại lệ duy nhất: phương trình phụ (nguồn độc lập nói câu hỏi không phải phép tính)."""
    if res is None:
        return False
    if res.status in mc.WITHHOLD_STATUSES or res.status == mc.EXPRESSION_UNCONFIRMED:
        return True
    return res.status == mc.SAME_LINEAGE and not is_incidental(res)


def atomic_arithmetic_contradiction(atomic_summary: dict[str, Any]) -> str:
    """Lý do nếu bộ kiểm atomic thấy phép tính sai (kể cả đường từ ngữ legacy)."""
    for c in atomic_summary.get("claims_payload", []) or []:
        if c.get("category") == "arithmetic" and c.get("status") == "CONTRADICTED":
            return str(c.get("evidence_detail") or "arithmetic mismatch")
    return ""


def has_arithmetic_claim(atomic_summary: dict[str, Any]) -> bool:
    return any(c.get("category") == "arithmetic" for c in atomic_summary.get("claims_payload", []) or [])


def withheld_verdict(ctx: Any, reason: str) -> dict[str, Any]:
    """Verdict withheld của cổng (judge KHÔNG chạy, KHÔNG ghi đè)."""
    mv = ctx.v98_context.setdefault(V98_KEY, {})
    mv["action"] = "withhold"
    mv["reason"] = reason
    return {
        "verdict": "FAIL", "confidence": 0.0, "reasoning": f"[MATH-VERIFY] {reason}",
        "cycle_count": 0, "failures": ["math_unverified"], "final_answer": "", "slm_responses": [],
        "domain": getattr(ctx.req, "domain", "general") or "general",
        "evidence": {
            "governance_decision": "ESCALATE", "judge_evaluated": False,
            "adjudicated_by": "math_recompute", "math_verification": mv,
        },
    }


def verified_verdict(ctx: Any, res: mc.MathAssessment) -> dict[str, Any]:
    """PASS tất định 2 tầng; đáp án giao CHÍNH LÀ đáp án AI (có biểu thức hiển thị)."""
    mv = ctx.v98_context.setdefault(V98_KEY, res.to_dict())
    mv["action"] = "deliver_verified"
    route = {
        "route": "atomic_deterministic_local", "provenance": "atomic_deterministic_local",
        "judge_evaluated": False, "adjudicated_by": "atomic_deterministic_local",
    }
    return {
        "verdict": "PASS", "confidence": 0.98, "deterministic_confidence": 1.0,
        "semantic_confidence": 1.0, "cross_model_agreement": res.reference_lineage.startswith("independent:"),
        "degraded": False, "cycle_count": 0, "failures": [], "slm_responses": [],
        "reasoning": f"[MATH-VERIFY] {res.reason} (reading: {res.reference_lineage})",
        "final_answer": str(ctx.ai_answer or ""),
        "domain": getattr(ctx.req, "domain", "general") or "general",
        "evidence": {
            "governance_decision": "UPHOLD", "judge_evaluated": False, "deterministic_verified": True,
            "adjudicated_by": "atomic_deterministic_local", "provenance": "atomic_deterministic_local",
            "math_verification": mv, "v98_classification": route,
        },
    }
