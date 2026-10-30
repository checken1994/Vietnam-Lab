"""[F-E2E-2] Trạng thái generation tường minh cho /ask (RC-1/RC-2).

Trước fix: ``LLMGateway.chat`` trả ``(None, "none")`` (không raise) khi không có
provider dùng được; ``GenerationStage`` lặng lẽ để đáp án rỗng; delivery dán nhãn
"không xác minh được câu trả lời" lên một đáp án KHÔNG HỀ tồn tại; ``except
Exception`` nuốt cả lỗi lập trình như thể "LLM lỗi".

Module này chỉ chứa HẰNG SỐ + helper ghi trạng thái (log + metric). Nó KHÔNG
quyết định giao/chặn — quyết định nằm ở ``DeliveryRuleEngine`` và chỉ được phép
chặt hơn (đổi nhãn của kết quả vốn đã withheld, hoặc withhold khi lỗi nội bộ).
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("scp.api_server_parts.pipeline.generation_stage")

#: Đáp án do caller cung cấp sẵn (``req.ai_answer``) — không gọi LLM.
GEN_PROVIDED = "provided"
#: LLM sinh được nội dung khác rỗng.
GEN_LLM = "llm"
#: [F-E2E-5] Fast-path số học tất định (safe_math) — KHÔNG gọi LLM, không phải caller cung cấp.
GEN_DETERMINISTIC = "deterministic"
#: Gateway trả rỗng/None mà KHÔNG raise (pool rỗng, breaker mở, hết budget…).
GEN_LLM_UNAVAILABLE = "llm_unavailable"
#: Gateway/provider raise lỗi vận hành (mạng, timeout, provider) — LLM không khả dụng.
GEN_GATEWAY_ERROR = "gateway_error"
#: Lỗi lập trình trong code của chính SCP — fail-closed, KHÔNG giả làm "LLM lỗi".
GEN_INTERNAL_ERROR = "internal_error"

#: Các trạng thái nghĩa là "LLM không sinh được câu trả lời".
LLM_NOT_GENERATED = frozenset({GEN_LLM_UNAVAILABLE, GEN_GATEWAY_ERROR})

#: Lớp exception được coi là BUG của code SCP (không phải sự cố gateway).
#: ``NameError`` phủ cả ``UnboundLocalError``; ``ImportError`` phủ
#: ``ModuleNotFoundError``. Provider gateway tự nuốt lỗi của nó
#: (``client.py`` _chat_hedged/provider.chat) nên các lớp này lọt tới đây gần
#: như chắc chắn là lỗi của chính pipeline ⇒ fail-closed là an toàn.
PROGRAMMING_ERRORS: tuple[type[BaseException], ...] = (
    TypeError,
    AttributeError,
    NameError,
    KeyError,
    IndexError,
    AssertionError,
    ImportError,
)

#: Nhãn withheld trung thực (a): KHÔNG có đáp án nào để xác minh.
LABEL_GENERATION_UNAVAILABLE = (
    "[SCP: Answer withheld — chưa sinh được câu trả lời (LLM không khả dụng)]"
)
#: Nhãn withheld khi lỗi lập trình trong generation (fail-closed).
LABEL_GENERATION_INTERNAL = (
    "[SCP: Answer withheld — lỗi nội bộ khi sinh câu trả lời (fail-closed)]"
)

_PROM_COUNTER: list[Any] = []


def _prom_inc(status: str) -> None:
    """Counter ``scp_ask_generation_status_total{status}`` — tùy chọn, không bao giờ chặn /ask."""
    try:
        if not _PROM_COUNTER:
            from prometheus_client import Counter

            _PROM_COUNTER.append(
                Counter(
                    "scp_ask_generation_status_total",
                    "F-E2E-2 /ask generation outcome by explicit status",
                    ["status"],
                )
            )
        _PROM_COUNTER[0].labels(status=status).inc()
    except Exception:  # metric là quan sát phụ — lỗi registry không được ảnh hưởng /ask
        logger.debug("generation status metric unavailable", exc_info=True)


def record_generation_status(ctx: Any, status: str, provider: str = "") -> None:
    """Ghi trạng thái lên ctx + v98_context, log (không log nội dung) và metric."""
    ctx.generation_status = status
    if isinstance(getattr(ctx, "v98_context", None), dict):
        ctx.v98_context["generation_status"] = status
    if status == GEN_LLM_UNAVAILABLE:
        logger.warning(
            "[GENERATION] status=%s provider=%s — gateway returned no content (no exception); "
            "no LLM answer was generated",
            status,
            provider or "none",
        )
    _prom_inc(status)
