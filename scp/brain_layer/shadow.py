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

import logging
from pathlib import Path
from typing import Any, Callable, Mapping

from scp.brain_layer.adapters.base import ProviderAdapter
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
