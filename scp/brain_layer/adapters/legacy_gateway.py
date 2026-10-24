"""Brain Layer P1 — LegacyGatewayAdapter: bọc RealityJudge hiện tại.

Thiết kế §3.10.4: thứ tự serving khi chưa có model judge mới là (3) adapter
bọc RealityJudge cũ với nhãn provenance trung thực
``adjudicated_by="reality_judge_legacy"`` — KHÔNG BAO GIỜ giả vờ output của
RealityJudge là output của model judge mới.

Contract:
- Adapter KHÔNG tự tạo judge mới. Judge resolve LAZY qua factory (mặc định
  ``scp.api_server_parts.helpers.get_judge`` — singleton có sẵn của pipeline).
  Factory trả None / raise ⇒ ``BrainProviderError`` (không crash, không tạo
  judge thay thế).
- Chỉ nhận task ``judge`` (task khác ⇒ ``BrainSchemaInvalid``).
- Judge sync ``judge()`` chạy qua ``asyncio.to_thread``; nếu judge expose
  ``judge_with_react_fallback`` (đường production verification đang dùng) thì
  await trực tiếp để đo disagreement trên cùng đường adjudication.
- Exception thô từ judge được chuẩn hóa qua ``normalize_exception``.
"""
from __future__ import annotations

import asyncio
from typing import Any, Callable, Mapping

from scp.brain_layer.adapters.base import AdapterResult, ProviderAdapter
from scp.brain_layer.adapters.base import normalize_exception
from scp.brain_layer.config import TASK_JUDGE
from scp.brain_layer.contracts import BrainContext
from scp.brain_layer.errors import BrainProviderError, BrainSchemaInvalid

PROVIDER_ID = "legacy-reality-judge"
#: Nhãn provenance trung thực — đúng thiết kế §3.10.4, không mạo danh model judge.
ADJUDICATED_BY = "reality_judge_legacy"
MODEL_ID = "reality-judge-legacy"

JudgeFactory = Callable[[], Any]


def _default_judge_factory() -> Any:
    """Factory mặc định: dùng instance RealityJudge CÓ SẴN (lazy singleton).

    Import cục bộ để tránh cycle (helpers → api_server → runtime → …). Hàm này
    KHÔNG khởi tạo judge mới ngoài singleton ``helpers._judge`` hiện có.
    """
    from scp.api_server_parts.helpers import get_judge  # noqa: PLC0415 — tránh cycle

    return get_judge()


class LegacyGatewayAdapter(ProviderAdapter):
    """ProviderAdapter bọc RealityJudge hiện tại cho mode shadow (P1)."""

    provider_id = PROVIDER_ID

    def __init__(self, judge_factory: JudgeFactory | None = None) -> None:
        # None ⇒ dùng helpers.get_judge() (lazy, instance có sẵn).
        self._judge_factory: JudgeFactory = judge_factory or _default_judge_factory
        # Số lần run() được gọi — dùng để test assert "shadow không đụng judge
        # khi mode=off".
        self.calls: list[str] = []

    def _resolve_judge(self) -> Any:
        try:
            judge = self._judge_factory()
        except Exception as exc:  # noqa: BLE001 — chuẩn hóa mọi lỗi resolve
            raise normalize_exception(
                exc, provider_id=PROVIDER_ID, model_id=MODEL_ID
            ) from exc
        if judge is None:
            raise BrainProviderError(
                "legacy judge chưa khả dụng (get_judge() trả None) — "
                "provider_error, không crash, không tạo judge mới",
                kind="provider_5xx",
                provider_id=PROVIDER_ID,
                model_id=MODEL_ID,
            )
        return judge

    @staticmethod
    def _normalize_output(raw: Any) -> dict[str, Any]:
        """Chuẩn hóa verdict dict của RealityJudge về output contract task judge.

        Output bắt buộc đủ ``output_required`` của task judge trong registry
        (verdict, claims). Thêm ``adjudicated_by`` làm nhãn provenance — field
        này đi vào BrainTrace, không bao giờ đụng verdict trả cho user.
        """
        if not isinstance(raw, Mapping):
            raise BrainProviderError(
                f"legacy judge trả về non-dict ({type(raw).__name__})",
                kind="schema_invalid",
                provider_id=PROVIDER_ID,
                model_id=MODEL_ID,
            )
        verdict = str(raw.get("verdict") or "").strip().upper() or "UNKNOWN"
        claims: Any = raw.get("claims")
        if claims is None:
            # RealityJudge không trả claims ở mức top-level; dùng claims payload
            # deterministic nếu judge đã decompose (đọc-thôi, không mutate).
            evidence = raw.get("evidence")
            claims = evidence.get("v100_claims") if isinstance(evidence, Mapping) else None
        if not isinstance(claims, list):
            claims = []
        return {
            "verdict": verdict,
            "claims": claims,
            "adjudicated_by": ADJUDICATED_BY,
        }

    async def run(
        self,
        task: str,
        payload: Mapping[str, Any],
        ctx: BrainContext,
    ) -> AdapterResult:
        if task != TASK_JUDGE:
            raise BrainSchemaInvalid(
                f"LegacyGatewayAdapter chỉ phục vụ task '{TASK_JUDGE}', nhận '{task}'"
            )
        self.calls.append(task)
        judge = self._resolve_judge()
        question = str(payload.get("question") or "")
        ai_answer = str(payload.get("final_answer") or "")
        try:
            if hasattr(judge, "judge_with_react_fallback"):
                raw = await judge.judge_with_react_fallback(
                    question=question,
                    ai_answer=ai_answer,
                    cycle_count=0,
                )
            else:
                raw = await asyncio.to_thread(
                    judge.judge,
                    question=question,
                    ai_answer=ai_answer,
                    cycle_count=0,
                )
        except Exception as exc:  # noqa: BLE001 — chuẩn hóa mọi lỗi judge
            raise normalize_exception(
                exc, provider_id=PROVIDER_ID, model_id=MODEL_ID
            ) from exc
        output = self._normalize_output(raw)
        usage = {"provider": PROVIDER_ID, "legacy": True}
        return AdapterResult(output=output, usage=usage, model_id=MODEL_ID)
