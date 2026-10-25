"""Brain Layer P0 — BrainLayer: điểm vào duy nhất của brain layer.

Contract only, mode=off: mặc định `SCP_BRAIN_MODE=off` ⇒ mọi `run()` raise
BrainPolicyDenied và KHÔNG gọi adapter nào. mode=shadow ⇒ chạy thật qua
adapter (P0 chỉ FakeAdapter) + ghi trace + trả envelope `status="ok"`; caller
QUYẾT ĐỊNH dùng output hay không (chưa cắm vào /ask).

H2 — mode=live: chạy thật qua adapter và caller DÙNG output thay gateway
legacy (BrainLayer không tự cắm vào /ask — caller quyết định). mode=canary:
giống live nhưng CHỈ kích hoạt theo tỷ lệ `SCP_BRAIN_CANARY_PERCENT` (0–100,
mặc định 0) chọn deterministic theo hash run_id; ngoài tỷ lệ ⇒ envelope
`status=policy_denied` + reason `canary_not_selected` (caller fallback legacy),
adapter KHÔNG bị gọi. Mọi trace ghi khi mode=canary mang flag `canary=true`.

Fail-closed:
- mode lạ/không nhận diện ⇒ coi như off (BrainPolicyDenied).
- canary percent env lỗi/không parse được ⇒ 0% (không ai được chọn).
- task không có trong registry ⇒ BrainSchemaInvalid.
- judge independence (config + runtime) vi phạm ⇒ BrainPolicyDenied.
- output thiếu key `output_required` ⇒ status=schema_invalid, KHÔNG trả như ok.
"""
from __future__ import annotations

import hashlib
import time
from types import MappingProxyType
from typing import Any, Mapping

from scp.brain_layer.adapters.base import AdapterResult, ProviderAdapter
from scp.brain_layer.config import TASK_JUDGE, BrainConfig
from scp.brain_layer.contracts import (
    CONTRACT_VERSION,
    STATUS_OK,
    STATUS_POLICY_DENIED,
    STATUS_PROVIDER_ERROR,
    STATUS_SCHEMA_INVALID,
    STATUS_TIMEOUT,
    STATUS_WITHHELD,
    BrainContext,
    BrainResponse,
)
from scp.brain_layer.errors import (
    BrainPolicyDenied,
    BrainProviderError,
    BrainSchemaInvalid,
    BrainTimeout,
)
from scp.brain_layer.registry import TaskDef
from scp.brain_layer.router import BrainRouter, RouteDecision
from scp.brain_layer.trace import (
    TraceWriter,
    build_trace_record,
    input_hash,
    new_brain_trace_id,
)

# Mode hoạt động: off (mặc định, deny-all) → shadow (quan sát, không dùng
# output) → canary (dùng output theo tỷ lệ) → live (dùng output, caller quyết
# định thay gateway legacy). Mode lạ ⇒ coi như off (fail-closed).
ALLOWED_MODES: tuple[str, ...] = ("off", "shadow", "live", "canary")
MODE_ENV_KEY = "SCP_BRAIN_MODE"
CANARY_PERCENT_ENV_KEY = "SCP_BRAIN_CANARY_PERCENT"
CANARY_NOT_SELECTED_REASON = "canary_not_selected"


def canary_percent() -> int:
    """Đọc tỷ lệ canary (0–100) từ `SCP_BRAIN_CANARY_PERCENT`. Fail-closed:
    env vắng/lỗi parse ⇒ 0 (không run nào được chọn)."""
    from scp.core.settings import get  # noqa: PLC0415 — import nhẹ, đọc live

    raw = str(get(CANARY_PERCENT_ENV_KEY, 0) or 0).strip()
    try:
        percent = int(float(raw))
    except (TypeError, ValueError):
        return 0
    return max(0, min(100, percent))


def canary_selected(run_id: str, percent: int | None = None) -> bool:
    """Chọn canary deterministic theo hash run_id — cùng run_id luôn cùng kết
    quả (không random, tái hiện được khi audit). percent>=100 ⇒ luôn chọn;
    percent<=0 ⇒ không chọn."""
    if percent is None:
        percent = canary_percent()
    if percent >= 100:
        return True
    if percent <= 0:
        return False
    digest = hashlib.sha256(f"brain-canary:{run_id}".encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % 100 < percent


def validate_output(task_def: TaskDef, output: Any) -> list[str]:
    """Kiểm output tối thiểu theo contract task (P0: dict + đủ key bắt buộc)."""
    if not isinstance(output, Mapping):
        return [f"output phải là object, nhận được {type(output).__name__}"]
    missing = [key for key in task_def.output_required if key not in output]
    if missing:
        return [
            f"output thiếu key bắt buộc: {missing} "
            f"(required={list(task_def.output_required)})"
        ]
    return []


class BrainLayer:
    """DI thuần: config + adapters + trace. Không singleton, không state ẩn."""

    def __init__(
        self,
        config: BrainConfig,
        adapters: Mapping[str, ProviderAdapter] | None = None,
        trace: TraceWriter | None = None,
    ) -> None:
        self.config = config
        self.registry = config.registry
        self.adapters: Mapping[str, ProviderAdapter] = MappingProxyType(dict(adapters or {}))
        self.trace = trace or TraceWriter()
        self.router = BrainRouter(self.registry)

    @staticmethod
    def mode() -> str:
        from scp.core.settings import get  # noqa: PLC0415 — import nhẹ, đọc live

        return (get(MODE_ENV_KEY, "off") or "off").strip().lower()

    def _trace(
        self,
        *,
        task: str,
        ctx: BrainContext,
        status: str,
        decision: RouteDecision | None,
        schema_valid: bool | None,
        latency_ms: float,
        brain_trace_id: str,
        payload_hash: str,
        route_reason: str,
        warnings: tuple[str, ...],
        extra: Mapping[str, Any] | None = None,
    ) -> None:
        # judge_independence: True khi task không phải judge; với task judge,
        # True chỉ khi guard independence đã chạy và pass (withheld ⇒ guard
        # chưa tới ⇒ False — bằng chứng trung thực).
        if task == TASK_JUDGE:
            judge_independence = decision is not None
        else:
            judge_independence = True
        record = build_trace_record(
            brain_trace_id=brain_trace_id,
            run_id=ctx.run_id,
            trace_id=ctx.trace_id,
            task=task,
            status=status,
            config_hash=self.config.config_hash,
            contract_version=CONTRACT_VERSION,
            provider_id=decision.provider_id if decision else None,
            model_id=decision.model_id if decision else None,
            schema_valid=schema_valid,
            latency_ms=latency_ms,
            judge_independence=judge_independence,
            payload_input_hash=payload_hash,
            route_reason=route_reason,
            warnings=warnings,
            extra=extra,
        )
        self.trace.write(record)

    def _envelope(
        self,
        *,
        brain_trace_id: str,
        task: str,
        status: str,
        output: Any,
        provider_id: str | None,
        model_id: str | None,
        latency_ms: float,
        usage: Mapping[str, Any],
        warnings: tuple[str, ...],
        route_reason: str,
    ) -> BrainResponse:
        return BrainResponse(
            brain_trace_id=brain_trace_id,
            task=task,
            status=status,
            output=output,
            provider_id=provider_id,
            model_id=model_id,
            contract_version=CONTRACT_VERSION,
            config_hash=self.config.config_hash,
            latency_ms=latency_ms,
            usage=MappingProxyType(dict(usage)),
            warnings=warnings,
            route_reason=route_reason,
        )

    async def run(
        self,
        task: str,
        payload: Mapping[str, Any],
        ctx: BrainContext,
    ) -> BrainResponse:
        mode = self.mode()
        if mode not in ALLOWED_MODES or mode == "off":
            # Fail-closed: mode=off hoặc mode lạ ⇒ denied, KHÔNG gọi adapter.
            raise BrainPolicyDenied(f"brain layer disabled (mode={mode!r})")

        canary = False
        if mode == "canary":
            # Canary: chỉ chạy brain theo tỷ lệ, chọn deterministic theo
            # run_id. Ngoài tỷ lệ ⇒ policy_denied envelope — caller fallback
            # legacy; adapter KHÔNG bị gọi.
            canary = canary_selected(ctx.run_id, canary_percent())
            if not canary:
                return await self._canary_denied(task, payload, ctx)
        return await self._execute(task, payload, ctx, canary=canary)

    async def run_task(
        self,
        task: str,
        payload: Mapping[str, Any],
        ctx: BrainContext,
        *,
        canary: bool = False,
    ) -> BrainResponse:
        """Entry OFFLINE (eval harness) — KHÔNG đọc mode env, KHÔNG qua canary
        gate; caller chịu trách nhiệm (eval chạy ngoài request path, chủ động
        chọn challenger cần đo). Vẫn đi đủ router + guard + trace."""
        return await self._execute(task, payload, ctx, canary=canary)

    async def _canary_denied(
        self,
        task: str,
        payload: Mapping[str, Any],
        ctx: BrainContext,
    ) -> BrainResponse:
        """Envelope policy_denied cho run canary ngoài tỷ lệ + trace trung thực
        (canary=true, canary_selected=false). Không gọi adapter."""
        started = time.perf_counter()
        brain_trace_id = new_brain_trace_id()
        payload_hash = input_hash(payload)
        latency = _elapsed(started)
        percent = canary_percent()
        self._trace(
            task=task, ctx=ctx, status=STATUS_POLICY_DENIED, decision=None,
            schema_valid=None, latency_ms=latency,
            brain_trace_id=brain_trace_id, payload_hash=payload_hash,
            route_reason=CANARY_NOT_SELECTED_REASON,
            warnings=(CANARY_NOT_SELECTED_REASON,),
            extra={
                "canary": True,
                "canary_selected": False,
                "canary_percent": percent,
            },
        )
        return self._envelope(
            brain_trace_id=brain_trace_id, task=task,
            status=STATUS_POLICY_DENIED, output=None,
            provider_id=None, model_id=None, latency_ms=latency, usage={},
            warnings=(CANARY_NOT_SELECTED_REASON,),
            route_reason=CANARY_NOT_SELECTED_REASON,
        )

    async def _execute(
        self,
        task: str,
        payload: Mapping[str, Any],
        ctx: BrainContext,
        *,
        canary: bool = False,
    ) -> BrainResponse:
        started = time.perf_counter()
        extra_canary: Mapping[str, Any] | None = (
            {"canary": True, "canary_selected": True} if canary else None
        )
        task_def = self.registry.tasks.get(task)
        if task_def is None:
            raise BrainSchemaInvalid(f"task '{task}' không có trong brain registry")

        brain_trace_id = new_brain_trace_id()
        payload_hash = input_hash(payload)
        warnings: list[str] = []

        decision = self.router.select(task, ctx)
        if not decision.ok:
            # withheld — không gọi adapter, không lộ gì.
            latency = _elapsed(started)
            self._trace(
                task=task, ctx=ctx, status=STATUS_WITHHELD, decision=None,
                schema_valid=None, latency_ms=latency,
                brain_trace_id=brain_trace_id, payload_hash=payload_hash,
                route_reason=decision.route_reason,
                warnings=(decision.route_reason,),
                extra=extra_canary,
            )
            return self._envelope(
                brain_trace_id=brain_trace_id, task=task, status=STATUS_WITHHELD,
                output=None, provider_id=None, model_id=None, latency_ms=latency,
                usage={}, warnings=(decision.route_reason,),
                route_reason=decision.route_reason,
            )

        # Runtime independence: model được chọn không được là champion của task
        # trong must_differ (config đã check lúc load; đây là guard chống
        # ctx.independence bổ sung tại runtime).
        must_differ = set(task_def.independence) | set(ctx.independence)
        for other_task in must_differ:
            other = self.registry.tasks.get(other_task)
            if other is not None and other.champion and other.champion == decision.model_id:
                raise BrainPolicyDenied(
                    f"independence violated: task '{task}' route tới "
                    f"'{decision.model_id}' trùng champion của '{other_task}'"
                )

        attempts: list[RouteDecision] = [decision]
        attempts.extend(
            self.router.fallback_chain(task, ctx, exclude=(decision.model_id or "",))
        )

        last_status = STATUS_PROVIDER_ERROR
        last_reason = decision.route_reason
        last_decision: RouteDecision | None = None
        for attempt in attempts:
            last_decision = attempt
            adapter = self.adapters.get(attempt.provider_id or "")
            if adapter is None:
                last_status = STATUS_PROVIDER_ERROR
                last_reason = f"không có adapter cho provider '{attempt.provider_id}'"
                warnings.append(last_reason)
                continue
            try:
                result: AdapterResult = await adapter.run(task, payload, ctx)
            except BrainSchemaInvalid as exc:
                # P2: adapter tự phát hiện response không map được output
                # contract ⇒ schema_invalid, KHÔNG retry/không đi fallback
                # (anti-placebo — response hỏng không tự trở nên hợp lệ ở lần
                # gọi kế). Trace ngay để shadow có bằng chứng schema_valid=False.
                status = STATUS_SCHEMA_INVALID
                latency = _elapsed(started)
                self._trace(
                    task=task, ctx=ctx, status=status, decision=attempt,
                    schema_valid=False, latency_ms=latency,
                    brain_trace_id=brain_trace_id, payload_hash=payload_hash,
                    route_reason=attempt.route_reason,
                    warnings=tuple(warnings + [f"schema_invalid: {exc}"]),
                    extra=extra_canary,
                )
                return self._envelope(
                    brain_trace_id=brain_trace_id, task=task, status=status,
                    output=None, provider_id=attempt.provider_id,
                    model_id=attempt.model_id, latency_ms=latency,
                    usage={}, warnings=tuple(warnings + [f"schema_invalid: {exc}"]),
                    route_reason=attempt.route_reason,
                )
            except BrainTimeout as exc:
                last_status = STATUS_TIMEOUT
                last_reason = f"timeout: {exc}"
                warnings.append(last_reason)
                continue
            except BrainProviderError as exc:
                last_status = STATUS_PROVIDER_ERROR
                last_reason = f"provider_error ({exc.kind}): {exc}"
                warnings.append(last_reason)
                continue

            errors = validate_output(task_def, result.output)
            if errors:
                # ANTI-PLACEBO: output sai schema không bao giờ trả như ok.
                status = STATUS_SCHEMA_INVALID
                latency = _elapsed(started)
                self._trace(
                    task=task, ctx=ctx, status=status, decision=attempt,
                    schema_valid=False, latency_ms=latency,
                    brain_trace_id=brain_trace_id, payload_hash=payload_hash,
                    route_reason=attempt.route_reason,
                    warnings=tuple(warnings + errors),
                    extra=extra_canary,
                )
                return self._envelope(
                    brain_trace_id=brain_trace_id, task=task, status=status,
                    output=None, provider_id=attempt.provider_id,
                    model_id=attempt.model_id, latency_ms=latency,
                    usage=dict(result.usage), warnings=tuple(warnings + errors),
                    route_reason=attempt.route_reason,
                )
            latency = _elapsed(started)
            self._trace(
                task=task, ctx=ctx, status=STATUS_OK, decision=attempt,
                schema_valid=True, latency_ms=latency,
                brain_trace_id=brain_trace_id, payload_hash=payload_hash,
                route_reason=attempt.route_reason, warnings=tuple(warnings),
                extra=extra_canary,
            )
            return self._envelope(
                brain_trace_id=brain_trace_id, task=task, status=STATUS_OK,
                output=dict(result.output), provider_id=attempt.provider_id,
                model_id=attempt.model_id, latency_ms=latency,
                usage=dict(result.usage), warnings=tuple(warnings),
                route_reason=attempt.route_reason,
            )

        latency = _elapsed(started)
        self._trace(
            task=task, ctx=ctx, status=last_status, decision=last_decision,
            schema_valid=None, latency_ms=latency,
            brain_trace_id=brain_trace_id, payload_hash=payload_hash,
            route_reason=last_reason, warnings=tuple(warnings),
            extra=extra_canary,
        )
        return self._envelope(
            brain_trace_id=brain_trace_id, task=task, status=last_status,
            output=None,
            provider_id=last_decision.provider_id if last_decision else None,
            model_id=last_decision.model_id if last_decision else None,
            latency_ms=latency, usage={}, warnings=tuple(warnings),
            route_reason=last_reason,
        )


def _elapsed(started: float) -> float:
    return (time.perf_counter() - started) * 1000.0
