"""[MATH-VERIFY · 2026-10-31] Bài học từ kiểm chứng phép tính — dùng ErrorStore SẴN CÓ.

Owner (31-10): mọi lần AI/SCP lệch và mọi UNKNOWN là BÀI HỌC. Lane này chỉ GHI (không
xây vòng học/tự sửa): 1 bản ghi có cấu trúc vào ``scp.brain.error_store.ErrorStore``
(kho "mỗi lần thực tế chứng minh SCP sai" của falsification_engine). Nhãn luôn
HYPOTHESIS / verified=False / promotion=never_automatic — KHÔNG BAO GIỜ thành tri thức
verified (KnowledgeStore tách riêng). Có giới hạn tần suất + kích thước (chống DoS);
lỗi ghi KHÔNG ảnh hưởng quyết định giao/withheld.
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

from scp.runtime import math_claims as mc

logger = logging.getLogger(__name__)


def _enabled(name: str, default: str = "1") -> bool:
    return os.environ.get(name, default).strip().lower() not in {"0", "false", "off", "no"}


def lineage_of(label: str) -> str:
    """Lineage model từ nhãn provider (``glm:glm-5.3`` → glm, ``groq:openai/gpt-oss`` → openai).

    HYPOTHESIS: tên model phản ánh họ model; cùng họ ⇒ coi là cùng lineage.
    """
    lab = str(label or "").strip().lower()
    if lab.startswith("llm:"):
        lab = lab[4:]
    provider, _, model = lab.partition(":")
    if "/" in model:
        return model.split("/", 1)[0]
    base = model or provider
    return re.split(r"[-_.:\d]", base, maxsplit=1)[0] or provider


LESSON_STATUSES = frozenset({
    mc.MISMATCH_VALUE, mc.MISMATCH_EXPRESSION, mc.UNRECOMPUTABLE, mc.SAME_LINEAGE, mc.EXPRESSION_UNCONFIRMED,
    mc.MISSING_RESULT,
})
_HYPOTHESES = {
    mc.MISMATCH_VALUE: "AI arithmetic slip, or SCP calculator defect (two evaluators agreed on SCP value)",
    mc.MISMATCH_EXPRESSION: "two readings of the question differ: generator or independent reader misread it",
    mc.UNRECOMPUTABLE: "expression outside SCP calculator scope or evaluators disagree",
    mc.SAME_LINEAGE: "no independent reading: generator could misread the question undetected",
    mc.EXPRESSION_UNCONFIRMED: "answer value matches an independent reading but the expression was never stated",
    mc.MISSING_RESULT: "answer matched a source/topic but does not contain the computed result (match != answer)",
    "LOOKUP_NUMERIC_IRRELEVANT": "data-API passage matched the topic words but not the question's numbers "
                                 "(source match treated as answering the question)",
    "LEGACY_WORD_RELATION_MISMATCH": "AI arithmetic slip detected via legacy word-relation parser",
}
_LESSON_WINDOW_S = 60.0
_lesson_times: list[float] = []
_lesson_lock = threading.Lock()
_lesson_stores: dict[str, Any] = {}
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")


def _redact(text: str, limit: int) -> str:
    from scp.history.regression_corpus import redact_input

    out, _ = redact_input(str(text or ""))
    out = _EMAIL_RE.sub("<EMAIL_REDACTED>", out)
    try:
        from scp.security import output_behavior_gate as gd1

        red = gd1.redact_text(out, gd1.collect_runtime_secrets(), gd1.collect_runtime_canaries())
        out = red if isinstance(red, str) else out
    except Exception:
        logger.debug("[MATH-VERIFY] runtime secret redaction skipped", exc_info=True)
    return out[:limit]


def _lesson_store(ctx: Any) -> Any:
    factory = ctx.resolve("_math_lesson_store", None) if hasattr(ctx, "resolve") else None
    if factory is not None:
        return factory()
    # scp.brain là vùng CÔ LẬP (contract T03 test_flow_14 ALLOWED_ISOLATED_IMPORTS):
    # KHÔNG import trực tiếp. Đi qua wire ErrorStore đã được duyệt của
    # FalsificationEngine (authority 1cd3843c) ⇒ cùng file data/error_store.jsonl
    # mà judge/falsification đang ghi; không mở thêm cửa vào brain.
    with _lesson_lock:
        if "default" not in _lesson_stores:
            from scp.meta.falsification_engine import FalsificationEngine

            _lesson_stores["default"] = FalsificationEngine()._get_error_store()
        return _lesson_stores["default"]


def record_math_lesson(ctx: Any, assessment: dict[str, Any]) -> bool:
    """Ghi 1 bài học HYPOTHESIS (không bao giờ thành tri thức verified). Lỗi ⇒ False,
    KHÔNG ảnh hưởng quyết định giao/withheld. Giới hạn tần suất + kích thước (chống DoS)."""
    if not _enabled("SCP_MATH_LESSONS"):
        return False
    now = time.monotonic()
    cap = int(os.environ.get("SCP_MATH_LESSON_MAX_PER_MIN", "20") or 20)
    with _lesson_lock:
        _lesson_times[:] = [t for t in _lesson_times if now - t < _LESSON_WINDOW_S]
        if len(_lesson_times) >= cap:
            logger.warning("[MATH-VERIFY] lesson rate cap reached (%s/min) — not recorded", cap)
            return False
        _lesson_times.append(now)
    try:
        status = str(assessment.get("status") or "UNKNOWN")
        gen_label = str(getattr(ctx, "llm_provider", "") or "")
        details = {
            "epistemic_status": "HYPOTHESIS", "verified": False, "promotion": "never_automatic",
            "status": status, "hypothesis": _HYPOTHESES.get(status, "unknown cause"),
            "equations": [
                {k: _redact(str(e.get(k, "")), 200) for k in ("expression", "relation", "claimed", "scp_value", "status")}
                for e in (assessment.get("equations") or [])[:5]
            ],
            "reference_expression": _redact(str(assessment.get("reference_expression") or ""), 200),
            "reference_value": str(assessment.get("reference_value") or "")[:64],
            "reference_lineage": str(assessment.get("reference_lineage") or "")[:64],
            "generator": {"provider": gen_label[:80], "lineage": lineage_of(gen_label) if gen_label else "",
                          "generation_status": str(getattr(ctx, "generation_status", "") or "")[:32]},
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "run_id": str(getattr(ctx, "run_id", "") or "")[:64],
            "reason": _redact(str(assessment.get("reason") or ""), 400),
        }
        question = _redact(str(getattr(ctx.req, "question", "") or ""), 500)
        rec = _lesson_store(ctx).add(
            question=f"math_verify {status}: {question}",
            answer=_redact(str(getattr(ctx, "ai_answer", "") or ""), 500),
            verdict="UNVERIFIED", domain="math", error_type=f"math_verify_{status.lower()}",
            details=details,
        )
        if isinstance(rec, dict) and rec.get("rejected"):
            logger.warning("[MATH-VERIFY] lesson rejected by ErrorStore: %s", rec.get("reject_reason"))
            return False
        return True
    except Exception:
        logger.warning("[MATH-VERIFY] lesson record failed (answer decision unaffected)", exc_info=True)
        return False


def record_lookup_lesson(req: Any, relevant: str, data: dict[str, Any], decision: Any, missing: list[str]) -> bool:
    """Bài học HYPOTHESIS cho lookup fork: đoạn nguồn thiếu các số của câu hỏi.
    Best-effort — không bao giờ ảnh hưởng đường trả lời."""
    try:
        ctx = SimpleNamespace(
            req=req, ai_answer=relevant, llm_provider=f"lookup:{data.get('api_name', '')}",
            generation_status="lookup_fork", run_id="", resolve=lambda _n, d=None: d,
        )
        return record_math_lesson(ctx, {
            "status": "LOOKUP_NUMERIC_IRRELEVANT",
            "reason": f"via={getattr(decision, 'via', '')} missing_numbers={missing[:5]} api={data.get('api_url', '')}",
        })
    except Exception:  # noqa: BLE001 — bài học không được làm hỏng /ask
        logger.debug("[MATH-VERIFY] lookup lesson skipped", exc_info=True)
        return False
