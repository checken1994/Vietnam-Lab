"""Brain Layer H2 — PromotionGate: kiểm soát thăng cấp challenger → champion.

Thang cấp (ladder): discovered → challenger → shadow → canary → champion.
Gate CHỈ quyết định trên metrics ĐO ĐƯỢC (eval harness / shadow aggregate);
không bao giờ tự sửa registry — PROMOTE là quyết định trả về cho caller
(human/automation áp dụng qua thay đổi config, có review).

Fail-closed (không tự promote):
- thiếu metrics bắt buộc / metrics sai kiểu / ngoài miền giá trị ⇒ HOLD;
- thiếu promotion.thresholds cho task trong registry ⇒ HOLD (không bịa ngưỡng);
- task không có trong registry hoặc current_level không hợp lệ ⇒ HOLD.

Anti-placebo (BLOCK + reason):
- pass_rate dưới ngưỡng `pass_rate` của task;
- schema_valid_rate dưới ngưỡng `schema_valid_rate`;
- avg_latency_ms vượt trần `max_avg_latency_ms`;
- disagreement_rate VƯỢT baseline (`baseline_disagreement_rate`) — challenger
  mâu thuẫn với baseline nhiều hơn mức chấp nhận ⇒ không được thăng cấp.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from scp.brain_layer.registry import BrainRegistry

#: Thang cấp promotion — thăng cấp từng bậc, không nhảy cóc.
PROMOTION_LADDER: tuple[str, ...] = (
    "discovered",
    "challenger",
    "shadow",
    "canary",
    "champion",
)

DECISION_PROMOTE = "PROMOTE"
DECISION_HOLD = "HOLD"
DECISION_BLOCK = "BLOCK"

#: Metrics bắt buộc đầu vào của evaluate() — thiếu bất kỳ ⇒ HOLD.
REQUIRED_METRICS: tuple[str, ...] = (
    "pass_rate",
    "schema_valid_rate",
    "avg_latency_ms",
    "disagreement_rate",
)

#: Ngưỡng bắt buộc trong promotion.thresholds.<task> — thiếu bất kỳ ⇒ HOLD.
REQUIRED_THRESHOLDS: tuple[str, ...] = (
    "pass_rate",
    "schema_valid_rate",
    "max_avg_latency_ms",
    "baseline_disagreement_rate",
)


def next_level(current: str) -> str | None:
    """Bậc kế tiếp trên thang promotion; None khi đã ở đỉnh (champion) hoặc
    current không thuộc thang."""
    try:
        idx = PROMOTION_LADDER.index(current)
    except ValueError:
        return None
    if idx + 1 >= len(PROMOTION_LADDER):
        return None
    return PROMOTION_LADDER[idx + 1]


@dataclass(frozen=True)
class PromotionDecision:
    """Kết quả evaluate(): PROMOTE | HOLD | BLOCK + bằng chứng (reasons)."""

    task_id: str
    decision: str
    level_from: str
    level_to: str
    reasons: tuple[str, ...] = ()

    @property
    def promoted(self) -> bool:
        return self.decision == DECISION_PROMOTE

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "decision": self.decision,
            "level_from": self.level_from,
            "level_to": self.level_to,
            "reasons": list(self.reasons),
        }


class PromotionGate:
    """Gate thăng cấp đọc ngưỡng từ registry (`promotion.thresholds.<task>`).
    Stateless, không tự ghi đè gì — quyết định trả về cho caller."""

    def __init__(self, registry: BrainRegistry) -> None:
        self._registry = registry

    def thresholds_for(self, task_id: str) -> Mapping[str, Any]:
        raw = self._registry.promotion.get("thresholds")
        if isinstance(raw, Mapping):
            per_task = raw.get(task_id)
            if isinstance(per_task, Mapping):
                return MappingProxyType(dict(per_task))
        return MappingProxyType({})

    def evaluate(
        self,
        task_id: str,
        metrics: Mapping[str, Any],
        *,
        current_level: str = "canary",
    ) -> PromotionDecision:
        """Quyết định thăng cấp 1 bậc cho task dựa trên metrics đo được.

        - HOLD: dữ liệu không đủ/không hợp lệ — KHÔNG tự promote (fail-closed).
        - BLOCK: metrics đủ nhưng vi phạm ngưỡng (kèm reason cụ thể).
        - PROMOTE: mọi ngưỡng đều đạt.
        """
        level_to = next_level(current_level) or ""
        target = level_to or current_level

        def decision(
            kind: str, reasons: tuple[str, ...]
        ) -> PromotionDecision:
            return PromotionDecision(
                task_id=task_id,
                decision=kind,
                level_from=current_level,
                level_to=target,
                reasons=reasons,
            )

        def hold(*reasons: str) -> PromotionDecision:
            return decision(DECISION_HOLD, reasons)

        def block(*reasons: str) -> PromotionDecision:
            return decision(DECISION_BLOCK, reasons)

        if self._registry.tasks.get(task_id) is None:
            return hold(f"task '{task_id}' không có trong brain registry")
        if not level_to:
            return hold(
                f"cấp hiện tại '{current_level}' không thuộc thang promotion "
                f"hoặc đã ở đỉnh ({PROMOTION_LADDER[-1]})"
            )

        thresholds = self.thresholds_for(task_id)
        missing_thresholds = [k for k in REQUIRED_THRESHOLDS if k not in thresholds]
        if missing_thresholds:
            return hold(
                f"thiếu promotion.thresholds cho task '{task_id}': "
                f"{missing_thresholds} — không tự bịa ngưỡng"
            )
        threshold_values: dict[str, float] = {}
        for key in REQUIRED_THRESHOLDS:
            try:
                threshold_values[key] = float(thresholds[key])
            except (TypeError, ValueError):
                return hold(
                    f"threshold '{key}' cho task '{task_id}' không phải số: "
                    f"{thresholds[key]!r}"
                )

        if not isinstance(metrics, Mapping):
            return hold("metrics phải là mapping, nhận được "
                        f"{type(metrics).__name__}")
        missing_metrics = [k for k in REQUIRED_METRICS if k not in metrics]
        if missing_metrics:
            return hold(f"thiếu metrics bắt buộc: {missing_metrics} — HOLD, không tự promote")

        values: dict[str, float] = {}
        for key in REQUIRED_METRICS:
            try:
                values[key] = float(metrics[key])
            except (TypeError, ValueError):
                return hold(
                    f"metric '{key}' không phải số: {metrics[key]!r} — HOLD"
                )
        for key in ("pass_rate", "schema_valid_rate", "disagreement_rate"):
            if not 0.0 <= values[key] <= 1.0:
                return hold(
                    f"metric '{key}' ngoài miền [0,1]: {values[key]} — HOLD"
                )
        if values["avg_latency_ms"] < 0:
            return hold(
                f"metric 'avg_latency_ms' âm: {values['avg_latency_ms']} — HOLD"
            )

        blocks: list[str] = []
        if values["pass_rate"] < threshold_values["pass_rate"]:
            blocks.append(
                f"pass_rate {values['pass_rate']:.3f} < ngưỡng "
                f"{threshold_values['pass_rate']:.3f}"
            )
        if values["schema_valid_rate"] < threshold_values["schema_valid_rate"]:
            blocks.append(
                f"schema_valid_rate {values['schema_valid_rate']:.3f} < ngưỡng "
                f"{threshold_values['schema_valid_rate']:.3f}"
            )
        if values["avg_latency_ms"] > threshold_values["max_avg_latency_ms"]:
            blocks.append(
                f"avg_latency_ms {values['avg_latency_ms']:.1f} > trần "
                f"{threshold_values['max_avg_latency_ms']:.1f}"
            )
        if values["disagreement_rate"] > threshold_values["baseline_disagreement_rate"]:
            blocks.append(
                f"disagreement_rate {values['disagreement_rate']:.3f} vượt baseline "
                f"{threshold_values['baseline_disagreement_rate']:.3f} "
                f"(tăng so với baseline)"
            )
        if blocks:
            return block(*blocks)

        evidence = ", ".join(
            f"{key}={values[key]:.3f}" if key != "avg_latency_ms"
            else f"avg_latency_ms={values['avg_latency_ms']:.1f}ms"
            for key in REQUIRED_METRICS
        )
        return decision(
            DECISION_PROMOTE,
            (f"đạt đủ ngưỡng promotion.thresholds['{task_id}'] ({evidence})",),
        )
