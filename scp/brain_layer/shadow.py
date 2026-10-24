"""Brain Layer P1 — shadow hook cho pipeline /ask (tự chế độ, không đổi đáp án).

Bất biến của shadow mode (thiết kế Brain Layer §shadow):
- KHÔNG BAO GIỜ đụng verdict / final_answer / governance trả cho user. Kết quả
  shadow chỉ được ghi vào BrainTrace JSONL + đo disagreement.
- Mode mặc định = off ⇒ hook phải no-op hoàn toàn (không đọc config, không
  trace, không đụng judge).
- Hook chỉ kích hoạt khi ``SCP_BRAIN_MODE=shadow`` VÀ registry file tồn tại
  (``SCP_BRAIN_REGISTRY_PATH``). Thiếu 1 trong 2 ⇒ no-op (debug log).
- Mọi exception bên trong shadow bị nuốt ở đây (log debug) — /ask KHÔNG được
  fail vì shadow. VerificationStage vẫn bọc thêm 1 lớp try/except phòng hờ.

Đo disagreement (P1, per-run — KHÔNG aggregate):
- record so sánh có ``shadow``=True, ``production_verdict``, ``shadow_verdict``,
  ``shadow_disagreement`` (bool) và ``adjudicated_by`` provenance từ adapter
  (legacy ⇒ "reality_judge_legacy").
- shadow_disagreement=True CHỈ khi shadow status=ok và verdict khác production.
  Khi shadow không cho verdict (provider_error/timeout/withheld), field là
  False và ``shadow_status`` trong record nói sự thật — không tự bịa agree.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from pathlib import Path
from typing import Any, Callable, Mapping

from scp.brain_layer.adapters.base import ProviderAdapter
from scp.brain_layer.adapters.fake import FakeAdapter
from scp.brain_layer.adapters.legacy_gateway import LegacyGatewayAdapter
from scp.brain_layer.brain_layer import BrainLayer
from scp.brain_layer.config import BrainConfig
from scp.brain_layer.contracts import (
    STATUS_OK,
    BrainContext,
    BrainResponse,
)
from scp.brain_layer.trace import TraceWriter, build_trace_record, input_hash

logger = logging.getLogger("scp.brain_layer.shadow")

REGISTRY_PATH_ENV_KEY = "SCP_BRAIN_REGISTRY_PATH"

JudgeFactory = Callable[[], Any]

#: P2 — task generate shadow (mặc định fire-and-forget, không tăng latency
#: path chính); đặt ``SCP_BRAIN_SHADOW_GENERATE=1`` để chạy TUẦN TỰ (deterministic,
#: chủ yếu cho test).
TASK_GENERATE = "generate"
SHADOW_GENERATE_SEQ_ENV_KEY = "SCP_BRAIN_SHADOW_GENERATE"

# Strong refs cho fire-and-forget tasks (chống GC giữa chừng; drain ở test).
_PENDING_GENERATE_TASKS: set[asyncio.Task[None]] = set()


def resolve_registry_path() -> Path | None:
    """Path registry brain (từ settings); None khi chưa cấu hình/không tồn tại."""
    from scp.core.settings import get  # noqa: PLC0415 — đọc live, tránh cycle import nặng

    raw = str(get(REGISTRY_PATH_ENV_KEY, "") or "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    if not path.is_file():
        return None
    return path


def build_shadow_layer(
    judge_factory: JudgeFactory | None = None,
    registry_path: str | Path | None = None,
) -> BrainLayer:
    """Dựng BrainLayer cho shadow run: registry từ path cấu hình + adapter
    legacy bọc judge instance có sẵn. Raise BrainConfigError khi config hỏng
    (fail-closed — caller nuốt ở mức log debug)."""
    path = Path(registry_path) if registry_path is not None else resolve_registry_path()
    if path is None:
        raise FileNotFoundError("brain registry path chưa cấu hình hoặc không tồn tại")
    config = BrainConfig.load(path)
    adapters: dict[str, ProviderAdapter] = {
        LegacyGatewayAdapter.provider_id: LegacyGatewayAdapter(judge_factory=judge_factory),
    }
    return BrainLayer(config, adapters=adapters, trace=TraceWriter())


def _shadow_verdict_of(resp: BrainResponse) -> str | None:
    if resp.status != STATUS_OK or not isinstance(resp.output, Mapping):
        return None
    verdict = str(resp.output.get("verdict") or "").strip().upper()
    return verdict or None


def write_shadow_comparison(
    *,
    layer: BrainLayer,
    payload: Mapping[str, Any],
    resp: BrainResponse,
    production_verdict: str,
    run_id: str,
    trace_id: str,
) -> None:
    """Ghi record so sánh shadow vs production vào BrainTrace (append-only).

    Record KHÔNG chứa prompt thô — payload chỉ hiện diện dưới dạng input_hash.
    """
    task_def = layer.registry.tasks.get(resp.task)
    schema_valid = resp.status == STATUS_OK
    shadow_verdict = _shadow_verdict_of(resp)
    production_norm = str(production_verdict or "").strip().upper()
    record = build_trace_record(
        brain_trace_id=resp.brain_trace_id,
        run_id=run_id,
        trace_id=trace_id,
        task=resp.task,
        status=resp.status,
        config_hash=resp.config_hash,
        contract_version=resp.contract_version,
        provider_id=resp.provider_id,
        model_id=resp.model_id,
        schema_valid=schema_valid,
        latency_ms=resp.latency_ms,
        judge_independence=True,  # đã qua guard independence trong BrainLayer.run
        payload_input_hash=input_hash(payload),
        route_reason=resp.route_reason,
        warnings=tuple(resp.warnings),
        extra={"shadow_comparison_note": None if task_def is not None
               else f"task '{resp.task}' không có trong registry"},
    )
    # Field core của record so sánh LUÔN hiện diện (kể cả null) — schema ổn
    # định cho audit: thiếu key ≠ có key với giá trị None.
    record["shadow"] = True
    record["adjudicated_by"] = (
        resp.output.get("adjudicated_by")
        if isinstance(resp.output, Mapping)
        else None
    )
    record["production_verdict"] = production_norm or None
    record["shadow_verdict"] = shadow_verdict
    # True CHỈ khi shadow thật sự trả verdict và nó khác production.
    record["shadow_disagreement"] = (
        shadow_verdict is not None and shadow_verdict != production_norm
    )
    layer.trace.write(record)


async def run_shadow_judge(
    *,
    question: str,
    final_answer: str,
    production_verdict: str,
    claims: Any = None,
    run_id: str = "",
    trace_id: str = "",
    judge_factory: JudgeFactory | None = None,
) -> None:
    """Chạy task judge SHADOW qua BrainLayer và ghi BrainTrace. KHÔNG BAO GIỜ
    raise, KHÔNG BAO GIỜ trả output cho caller — an toàn để gọi sau verdict
    cuối trong verification_stage."""
    try:
        # Early mode check: mode=off (hoặc mode lạ) ⇒ no-op tuyệt đối — không
        # đọc registry, không dựng layer, không đụng judge, không trace.
        if BrainLayer.mode() != "shadow":
            return
        payload: dict[str, Any] = {
            "question": str(question or ""),
            "final_answer": str(final_answer or ""),
        }
        if claims:
            payload["claims"] = claims

        layer = build_shadow_layer(judge_factory=judge_factory)
        ctx = BrainContext(run_id=str(run_id or ""), trace_id=str(trace_id or ""))
        resp = await layer.run("judge", payload, ctx)
        write_shadow_comparison(
            layer=layer,
            payload=payload,
            resp=resp,
            production_verdict=str(production_verdict or ""),
            run_id=str(run_id or ""),
            trace_id=str(trace_id or ""),
        )
    except Exception as exc:  # noqa: BLE001 — shadow không được làm /ask fail
        logger.debug(
            "[BRAIN-SHADOW] shadow judge skipped (non-fatal): %s", exc, exc_info=True
        )


# ---------------------------------------------------------------------------
# P2 — shadow generate (task 'generate'): CHỈ ghi BrainTrace, KHÔNG đụng
# đáp án production trả cho user.
# ---------------------------------------------------------------------------


def _timeout_from_limits(model_limits: Mapping[str, Any]) -> float | None:
    """Đọc ``timeout_s`` từ model limits (registry là nguồn sự thật)."""
    raw = model_limits.get("timeout_s") if model_limits else None
    if raw is None:
        return None
    try:
        return float(raw)  # type: ignore[arg-type] — giá trị xấu ⇒ None ở caller
    except (TypeError, ValueError):
        return None


def build_generate_shadow_layer(
    registry_path: str | Path | None = None,
) -> BrainLayer:
    """Dựng BrainLayer cho shadow generate: map provider kind của
    champion/fallback task 'generate' sang adapter tương ứng
    (external_openai_compatible → OpenAICompatibleAdapter, fake → FakeAdapter;
    kind local không có adapter generate ⇒ layer trả provider_error trung thực).
    Raise khi config hỏng (fail-closed — caller nuốt ở mức log debug)."""
    path = Path(registry_path) if registry_path is not None else resolve_registry_path()
    if path is None:
        raise FileNotFoundError("brain registry path chưa cấu hình hoặc không tồn tại")
    config = BrainConfig.load(path)
    task_def = config.registry.tasks.get(TASK_GENERATE)
    adapters: dict[str, ProviderAdapter] = {}
    if task_def is not None:
        for model_id in (task_def.champion, *task_def.fallbacks):
            model = config.registry.models.get(model_id)
            if model is None:
                continue
            provider = config.registry.providers.get(model.provider_id)
            if provider is None or provider.provider_id in adapters:
                continue
            if provider.kind == "external_openai_compatible":
                # Import cục bộ — giữ package import nhẹ như P0/P1.
                from scp.brain_layer.adapters.openai_compatible import (  # noqa: PLC0415
                    OpenAICompatibleAdapter,
                )

                adapters[provider.provider_id] = OpenAICompatibleAdapter(
                    provider.provider_id,
                    base_url=provider.base_url,
                    secret_ref=provider.secret_ref,
                    model_id=model.model_id,
                    # Timeout lấy từ model limits trong registry (fail-safe
                    # clamp trong adapter).
                    timeout_s=_timeout_from_limits(model.limits),
                )
            elif provider.kind == "fake":
                adapters[provider.provider_id] = FakeAdapter(
                    provider.provider_id, model_id=model.model_id
                )
    return BrainLayer(config, adapters=adapters, trace=TraceWriter())


def write_shadow_generate_record(
    *,
    layer: BrainLayer,
    payload: Mapping[str, Any],
    resp: BrainResponse,
    production_answer: str,
    run_id: str,
    trace_id: str,
) -> None:
    """Ghi record so sánh shadow generate vào BrainTrace (append-only).

    KHÔNG chứa text answer (production hay shadow) — chỉ provenance + hash;
    ``adjudicated_by`` = provider_id của adapter đã adjudicate (trung thực:
    withheld ⇒ None).
    """
    record = build_trace_record(
        brain_trace_id=resp.brain_trace_id,
        run_id=run_id,
        trace_id=trace_id,
        task=resp.task,
        status=resp.status,
        config_hash=resp.config_hash,
        contract_version=resp.contract_version,
        provider_id=resp.provider_id,
        model_id=resp.model_id,
        schema_valid=resp.status == STATUS_OK,
        latency_ms=resp.latency_ms,
        judge_independence=True,  # task != judge; đã qua guard trong BrainLayer.run
        payload_input_hash=input_hash(payload),
        route_reason=resp.route_reason,
        warnings=tuple(resp.warnings),
    )
    # Field core luôn hiện diện (kể cả null) — schema ổn định cho audit.
    record["shadow"] = True
    record["adjudicated_by"] = resp.provider_id
    record["shadow_status"] = resp.status
    record["production_answer_sha256"] = (
        hashlib.sha256(str(production_answer or "").encode("utf-8")).hexdigest()
    )
    record["shadow_output_present"] = isinstance(resp.output, Mapping) and bool(resp.output)
    layer.trace.write(record)


def _on_generate_task_done(task: asyncio.Task[None]) -> None:
    _PENDING_GENERATE_TASKS.discard(task)
    if task.cancelled():
        logger.debug("[BRAIN-SHADOW] shadow generate task cancelled (non-fatal)")
        return
    exc = task.exception()
    if exc is not None:
        logger.debug(
            "[BRAIN-SHADOW] shadow generate task failed (non-fatal): %s", exc, exc_info=exc
        )


def drain_pending_generate_tasks() -> list[asyncio.Task[None]]:
    """Trả các fire-and-forget task còn pending (helper cho test drain)."""
    return list(_PENDING_GENERATE_TASKS)


async def _run_generate_now(
    *,
    question: str,
    context: str,
    production_answer: str,
    run_id: str,
    trace_id: str,
) -> None:
    """Chạy 1 lần task generate shadow qua BrainLayer + ghi record (có thể raise
    — run_shadow_generate nuốt)."""
    layer = build_generate_shadow_layer()
    payload: dict[str, Any] = {"question": str(question or "")}
    if context:
        payload["context"] = str(context)
    ctx = BrainContext(run_id=str(run_id or ""), trace_id=str(trace_id or ""))
    resp = await layer.run(TASK_GENERATE, payload, ctx)
    write_shadow_generate_record(
        layer=layer,
        payload=payload,
        resp=resp,
        production_answer=str(production_answer or ""),
        run_id=str(run_id or ""),
        trace_id=str(trace_id or ""),
    )


async def run_shadow_generate(
    *,
    question: str,
    production_answer: str,
    context: str = "",
    run_id: str = "",
    trace_id: str = "",
) -> None:
    """Hook shadow generate cho generation_stage — gọi SAU KHI answer production
    đã chốt. KHÔNG BAO GIỜ raise, KHÔNG BAO GIỜ trả/đổi output cho caller.

    - mode != 'shadow' ⇒ no-op tuyệt đối (không đọc registry, không trace).
    - Mặc định fire-and-forget qua asyncio.create_task (pipeline tiếp tục chạy
      song song — KHÔNG tăng latency path chính); exception trong task bị nuốt
      ở done-callback (log debug).
    - ``SCP_BRAIN_SHADOW_GENERATE=1`` ⇒ chạy TUẦN TỰ (deterministic — test).
    """
    try:
        # Early mode check: mode=off (hoặc mode lạ) ⇒ no-op tuyệt đối — không
        # đọc registry, không dựng layer, không trace.
        if BrainLayer.mode() != "shadow":
            return
        from scp.core.settings import get  # noqa: PLC0415 — đọc live

        if str(get(SHADOW_GENERATE_SEQ_ENV_KEY, "") or "").strip() == "1":
            await _run_generate_now(
                question=question,
                context=context,
                production_answer=production_answer,
                run_id=run_id,
                trace_id=trace_id,
            )
            return
        task = asyncio.create_task(
            _run_generate_now(
                question=question,
                context=context,
                production_answer=production_answer,
                run_id=run_id,
                trace_id=trace_id,
            )
        )
        _PENDING_GENERATE_TASKS.add(task)
        task.add_done_callback(_on_generate_task_done)
    except Exception as exc:  # noqa: BLE001 — shadow không được làm /ask fail
        logger.debug(
            "[BRAIN-SHADOW] shadow generate skipped (non-fatal): %s", exc, exc_info=True
        )
