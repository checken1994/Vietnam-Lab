"""Brain Layer P0 — router rule-based (không AI, không tự chế chain).

Lọc theo role + data_class + egress policy → chọn champion; champion lỗi thì
đi theo fallback list đã khai trong registry. Mọi quyết định đều kèm
`route_reason` (string) làm bằng chứng. KHÔNG bao giờ tạo model/chain ngoài
contract registry.
"""
from __future__ import annotations

from dataclasses import dataclass

from scp.brain_layer.contracts import (
    LOCAL_ONLY_DATA_CLASSES,
    BrainContext,
)
from scp.brain_layer.errors import BrainSchemaInvalid
from scp.brain_layer.registry import BrainRegistry, ProviderDef, TaskDef


@dataclass(frozen=True)
class RouteDecision:
    model_id: str | None
    provider_id: str | None
    route_reason: str

    @property
    def ok(self) -> bool:
        return self.model_id is not None and self.provider_id is not None


class BrainRouter:
    """Rule-based router trên registry bất biến (stateless)."""

    def __init__(self, registry: BrainRegistry) -> None:
        self._registry = registry

    def _egress_locked(self, ctx: BrainContext) -> bool:
        # Data class nhạy cảm hoặc capability sandboxed → cấm provider external.
        return ctx.data_class in LOCAL_ONLY_DATA_CLASSES or (
            ctx.capability_level == "sandboxed"
        )

    def _explain_block(
        self, task_def: TaskDef, model_id: str, provider: ProviderDef, ctx: BrainContext
    ) -> str | None:
        model = self._registry.models.get(model_id)
        if model is None:  # đã validate lúc load — phòng thủ thêm
            return f"model '{model_id}' không tồn tại trong registry"
        if task_def.role not in model.roles:
            return (
                f"model '{model_id}' không phủ role '{task_def.role}' "
                f"(roles={list(model.roles)})"
            )
        if self._egress_locked(ctx) and provider.egress == "external":
            return (
                f"egress policy chặn provider external '{provider.provider_id}' "
                f"(data_class={ctx.data_class!r}, capability={ctx.capability_level!r})"
            )
        return None

    def _eligible(self, task: str, ctx: BrainContext, model_id: str) -> tuple[bool, str]:
        task_def = self._registry.tasks[task]
        model = self._registry.models[model_id]
        provider = self._registry.providers[model.provider_id]
        reason = self._explain_block(task_def, model_id, provider, ctx)
        return (reason is None, reason or "ok")

    def select(self, task: str, ctx: BrainContext) -> RouteDecision:
        """Chọn model cho task: champion → fallback, theo đúng contract."""
        task_def = self._registry.tasks.get(task)
        if task_def is None:
            raise BrainSchemaInvalid(f"task '{task}' không có trong brain registry")

        if task_def.data_classes and ctx.data_class not in task_def.data_classes:
            return RouteDecision(
                None,
                None,
                f"withheld: data_class '{ctx.data_class}' không nằm trong "
                f"data_classes của task '{task}' ({list(task_def.data_classes)})",
            )

        candidates = [task_def.champion, *task_def.fallbacks] if task_def.champion else list(
            task_def.fallbacks
        )
        notes: list[str] = []
        for model_id in candidates:
            if not model_id:
                continue
            eligible, reason = self._eligible(task, ctx, model_id)
            if eligible:
                if notes:
                    notes.append(f"chọn '{model_id}'")
                    return RouteDecision(
                        model_id,
                        self._registry.models[model_id].provider_id,
                        "; ".join(notes),
                    )
                provider = self._registry.providers[
                    self._registry.models[model_id].provider_id
                ]
                return RouteDecision(
                    model_id,
                    provider.provider_id,
                    (
                        f"champion '{model_id}' (task='{task}', role='{task_def.role}', "
                        f"data_class='{ctx.data_class}', egress='{provider.egress}', "
                        f"egress_locked={self._egress_locked(ctx)})"
                    ),
                )
            notes.append(f"bỏ '{model_id}': {reason}")
        return RouteDecision(
            None,
            None,
            f"no eligible model cho task '{task}': " + ("; ".join(notes) or "không có candidate"),
        )

    def fallback_chain(
        self, task: str, ctx: BrainContext, exclude: tuple[str, ...] = ()
    ) -> list[RouteDecision]:
        """Các fallback còn eligible sau khi champion lỗi (theo thứ tự đã khai)."""
        task_def = self._registry.tasks.get(task)
        if task_def is None:
            raise BrainSchemaInvalid(f"task '{task}' không có trong brain registry")
        chain: list[RouteDecision] = []
        for model_id in task_def.fallbacks:
            if model_id in exclude:
                continue
            eligible, reason = self._eligible(task, ctx, model_id)
            if eligible:
                chain.append(
                    RouteDecision(
                        model_id,
                        self._registry.models[model_id].provider_id,
                        f"fallback '{model_id}' (theo fallback list của task '{task}')",
                    )
                )
        return chain
