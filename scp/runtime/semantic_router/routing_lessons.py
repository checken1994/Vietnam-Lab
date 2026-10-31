"""[L0-DEKEYWORD · 2026-10-31] Bài học định tuyến — dùng ErrorStore SẴN CÓ (đường đã duyệt).

TẠI SAO: owner (luật học từ lỗi) — mỗi lần định tuyến KHÔNG CHẮC hoặc hai lineage ngữ nghĩa
LỆCH NHAU là một bài học SCP phải lưu, không được lặng lẽ fail-closed rồi quên. Module này chỉ
GHI 1 bản ghi có cấu trúc (không tự sửa router, không đổi ngưỡng, không ảnh hưởng quyết định):

- ``l1_chat_unconfirmed``: L1 bỏ phiếu CHAT trên ngưỡng nhưng đồng thuận kép không đạt
  (L2 lineage chết/timeout, L2 khác nhãn, hoặc dưới ngưỡng kép) ⇒ đã fail-closed FACTUAL.
- ``l1_l2_router_divergence``: L1 (embedding) và bộ phân loại L2 (LLM) gán hai họ nhãn khác
  nhau (FACT↔REASONING, REAS↔LOOKUP) cho cùng một câu.

Nhãn luôn ``HYPOTHESIS`` / ``verified=False`` / ``promotion=never_automatic`` — KHÔNG BAO GIỜ
thành tri thức verified. Ghi qua ``FalsificationEngine._get_error_store()`` (cùng wire
math-verify dùng: ``scp.brain`` là vùng cô lập, không import thẳng). Có trần tần suất (chống
DoS / lũ bản ghi khi L2 timeout hàng loạt) + redact secret/email; mọi lỗi ⇒ ``False``.
"""
from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("scp.runtime.semantic_router.routing_lessons")

L1_CHAT_UNCONFIRMED = "l1_chat_unconfirmed"
L1_L2_ROUTER_DIVERGENCE = "l1_l2_router_divergence"

_HYPOTHESES = {
    L1_CHAT_UNCONFIRMED: (
        "L1 embedding voted CHAT above the calibrated floor but the second lineage did not confirm "
        "(L2 dead/timeout, different label, or joint confidence below threshold): either L1 "
        "over-generalises CHAT for this phrasing/language, or L2 is unavailable/too slow — routing "
        "fell back to the fail-closed FACTUAL lane"
    ),
    L1_L2_ROUTER_DIVERGENCE: (
        "L1 embedding family and the L2 LLM router disagree on factual-vs-reasoning for the same "
        "question: one lineage misreads this phrasing/language (unknown which) — the decision kept "
        "the stricter path"
    ),
}
#: L1 class → họ nhãn của bộ phân loại L2 (question_router: LOOKUP | REASONING).
_L1_FAMILY = {"FACT": "LOOKUP", "REAS": "REASONING"}

_WINDOW_S = 60.0
_times: list[float] = []
#: (status, sha256(câu hỏi)) → lần ghi gần nhất: 1 /ask gọi router nhiều lần (lookup_stage,
#: response_verifier, fork) ⇒ không ghi trùng cùng bài học trong cửa sổ (không ăn trần).
_seen: dict[tuple[str, str], float] = {}
_lock = threading.Lock()
_store_cache: dict[str, Any] = {}

#: Seam cho test (hermetic): hàm không đối số trả object có ``add(...)``. ``None`` ⇒
#: ErrorStore thật qua FalsificationEngine.
STORE_FACTORY: Callable[[], Any] | None = None


def _enabled() -> bool:
    raw = os.environ.get("SCP_ROUTING_LESSONS", "1").strip().lower()
    return raw not in {"0", "false", "off", "no"}


def _cap() -> int:
    try:
        return max(0, int(os.environ.get("SCP_ROUTING_LESSON_MAX_PER_MIN", "20") or 20))
    except ValueError:
        return 20


def _store() -> Any:
    if STORE_FACTORY is not None:
        return STORE_FACTORY()
    with _lock:
        if "default" not in _store_cache:
            from scp.meta.falsification_engine import FalsificationEngine

            _store_cache["default"] = FalsificationEngine()._get_error_store()
        return _store_cache["default"]


def _redact(text: str, limit: int) -> str:
    """Redaction đã review (math_lessons). Lỗi ⇒ exception lên handler DUY NHẤT của
    ``record_routing_lesson`` ⇒ bài học KHÔNG được ghi (không bao giờ ghi nguyên văn)."""
    from scp.runtime.math_lessons import _redact as redact

    return redact(text, limit)


def _verdict_summary(v: Any) -> dict[str, Any]:
    if v is None:
        return {"state": "dead"}
    try:
        conf = round(float(getattr(v, "confidence", 0.0)), 4)
    except (TypeError, ValueError):
        conf = 0.0
    return {
        "state": "degraded" if getattr(v, "degraded", False) else "live",
        "label": str(getattr(v, "label", "") or "")[:24],
        "confidence": conf,
        "reason": str(getattr(v, "reason", "") or "")[:80],
    }


def _forget(key: tuple[str, str]) -> None:
    """Ghi thất bại ⇒ bỏ dấu dedupe để lần sau còn ghi được (không nuốt bài học)."""
    with _lock:
        _seen.pop(key, None)


def record_routing_lesson(status: str, question: str, *, l1: Any = None, l2: Any = None,
                          decision: Any = None, note: str = "",
                          when: Callable[[], bool] | None = None) -> bool:
    """Ghi 1 bài học HYPOTHESIS. Best-effort: lỗi/tắt/vượt trần/trùng ⇒ False, KHÔNG raise.

    ``when``: điều kiện ghi (đánh giá BÊN TRONG handler duy nhất của module — điều kiện lỗi
    cũng chỉ ra ``False``, không làm hỏng định tuyến)."""
    key: tuple[str, str] | None = None
    try:
        if status not in _HYPOTHESES or not _enabled():
            return False
        if when is not None and not when():
            return False
        now = time.monotonic()
        cap = _cap()
        key = (status, hashlib.sha256(str(question or "").encode("utf-8", "replace")).hexdigest())
        with _lock:
            _times[:] = [t for t in _times if now - t < _WINDOW_S]
            for k in [k for k, t in _seen.items() if now - t >= _WINDOW_S]:
                del _seen[k]
            if key in _seen:
                return False
            if len(_times) >= cap:
                logger.warning("[L0-DEKEYWORD] routing lesson rate cap reached (%s/min) — not recorded", cap)
                return False
            _times.append(now)
            _seen[key] = now
        details = {
            "epistemic_status": "HYPOTHESIS", "verified": False, "promotion": "never_automatic",
            "status": status, "hypothesis": _HYPOTHESES[status],
            "l1": _verdict_summary(l1), "l2": _verdict_summary(l2),
            "decision": {
                "lane": str(getattr(decision, "lane", "") or "")[:24],
                "intent": str(getattr(decision, "intent", "") or "")[:24],
                "via": str(getattr(decision, "via", "") or "")[:48],
                "reason": str(getattr(decision, "reason", "") or "")[:80],
            } if decision is not None else None,
            "note": _redact(note, 200),
            "observed_at": datetime.now(timezone.utc).isoformat(),
        }
        rec = _store().add(
            question=f"routing {status}: {_redact(question, 500)}",
            answer="",
            verdict="UNVERIFIED", domain="routing", error_type=f"routing_{status}",
            details=details,
        )
        if isinstance(rec, dict) and rec.get("rejected"):
            _forget(key)
            logger.warning("[L0-DEKEYWORD] routing lesson rejected by ErrorStore: %s", rec.get("reject_reason"))
            return False
        return True
    except Exception:  # noqa: BLE001 — ranh giới DUY NHẤT: bài học không được làm hỏng định tuyến
        if key is not None:
            _forget(key)
        logger.warning("[L0-DEKEYWORD] routing lesson record failed (routing unaffected)", exc_info=True)
        return False


def _diverges(l1: Any, decision: Any) -> bool:
    if l1 is None or getattr(l1, "degraded", True) or getattr(l1, "abstained", False):
        return False
    if decision is None or str(getattr(decision, "via", "")) != "l2-llm":
        return False
    family = _L1_FAMILY.get(str(getattr(l1, "label", "") or "").upper())
    if family is None:
        return False
    if str(getattr(decision, "intent", "")) == family:
        return False
    return True


def record_router_divergence(question: str, l1: Any, decision: Any) -> bool:
    """L1 live (không abstain) vs quyết định L2-LLM (``via == 'l2-llm'``) khác họ nhãn ⇒ bài học."""
    return record_routing_lesson(L1_L2_ROUTER_DIVERGENCE, question, l1=l1, decision=decision,
                                 when=lambda: _diverges(l1, decision))


__all__ = [
    "L1_CHAT_UNCONFIRMED",
    "L1_L2_ROUTER_DIVERGENCE",
    "STORE_FACTORY",
    "record_router_divergence",
    "record_routing_lesson",
]
