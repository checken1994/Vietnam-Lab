# SCP CIRCUIT: M02 — STATUS: CLOSED_WITH_KNOWN_GAP (closure: docs/evidence-summary/M02-closure.json)
"""
Forecast routes — Prediction outcome tracking ledger.

Routes:
  GET  /v105/forecast/stats       — Forecast ledger stats
  POST /v105/forecast/cases       — Record a new forecast case
  GET  /v105/forecast/cases       — List forecast cases
  POST /v105/forecast/resolve     — Record the actual outcome of a case
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request

from scp.api._shared import verify_admin
from scp.core.request_run_ledger import RequestRunLedger, traced_request
from scp.forecast.ledger import ForecastContractError, ForecastLedger
from scp.forecast.runtime import ask_forecast_hook_status, load_runtime_ledger

router = APIRouter(prefix="/v105/forecast", tags=["forecast"])
_LEDGER = RequestRunLedger()

logger = logging.getLogger("scp.api.routes.forecast")


def _data_dir() -> Path:
    # Đọc mỗi lần gọi (cùng ngữ nghĩa SCP_DATA_DIR với hook /ask trong ledger_stage).
    return Path(os.environ.get("SCP_DATA_DIR", "data"))


def _internal_error(exc: Exception) -> HTTPException:
    """[AUDIT-20260909 MACH2-BUG4] 500 fail-closed, không leak message nội bộ."""
    logger.warning("[forecast] internal error: %s", exc, exc_info=True)
    return HTTPException(status_code=500, detail="internal error")


def _get_ledger() -> ForecastLedger | None:
    """[PREDICTION-P0] Ledger trên registry runtime ĐÃ KHOÁ (``scp.forecast.runtime``).

    Trước đây hàm này dựng registry rỗng trong RAM với snapshot bịa (``SKIPPED``) ⇒ mọi số liệu
    là placebo. Không có registry ⇒ ``None``; registry hỏng ⇒ 409 (fail-closed, không tự sửa)."""
    try:
        return load_runtime_ledger(_data_dir())
    except ForecastContractError as exc:
        logger.warning("[forecast] runtime registry invalid: %s", exc)
        raise HTTPException(status_code=409, detail={"status": "registry_invalid", "reason": str(exc)[:200]}) from exc


def _no_registry() -> HTTPException:
    return HTTPException(status_code=409, detail={
        "status": "no_registry",
        "reason": "no locked forecast registry in the runtime data dir; nothing can be resolved",
    })


@router.get("/stats", dependencies=[Depends(verify_admin)])
@traced_request(_LEDGER, require_write=False, action="forecast_stats")
async def forecast_stats():
    """Số liệu THẬT (``ForecastLedger.status``) hoặc trạng thái trung thực — không bao giờ "active" rỗng."""
    ledger = _get_ledger()
    if ledger is None:
        return {"subsystem": "forecast", "status": "no_registry", "registry_cases": 0,
                "resolved_events": 0, "ask_hook": ask_forecast_hook_status()}
    try:
        stats = ledger.status()
    except Exception as exc:
        raise _internal_error(exc) from exc
    return {"subsystem": "forecast", "status": "ok", **stats, "ask_hook": ask_forecast_hook_status()}


@router.post("/cases", dependencies=[Depends(verify_admin)])
@traced_request(_LEDGER, require_write=True, action="forecast_record_case")
async def record_case(request: Request):
    """[PREDICTION-P0] 501 trung thực.

    Trước đây route ghi ``forecast_registry.jsonl`` mà KHÔNG thành phần nào đọc (LIVE-SINK).
    Registry R44 là sổ khoá bằng manifest hash; ledger không bao giờ sửa registry. Đăng ký case
    runtime cần hợp đồng anchor riêng (lane sau) — không tự ký anchor của chính mình."""
    raise HTTPException(status_code=501, detail={
        "status": "not_implemented",
        "reason": "runtime case registration has no anchor contract yet; the R44 registry is locked",
    })


@router.get("/cases", dependencies=[Depends(verify_admin)])
@traced_request(_LEDGER, require_write=False, action="forecast_list_cases")
async def list_cases(domain: str = "", limit: int = 50):
    """List forecast cases of the locked registry, with their current ledger outcome."""
    ledger = _get_ledger()
    if ledger is None:
        return {"status": "no_registry", "cases": [], "count": 0}
    try:
        outcomes = ledger.current_outcomes()
        cases = [c for c in ledger.registry.cases if not domain or c.get("domain") == domain]
        out = [
            {**c, "current_outcome_code": (outcomes.get(c["id"]) or {}).get("outcome_code", c["outcome_code"])}
            for c in cases[: max(0, min(int(limit), 1000))]
        ]
    except Exception as exc:
        raise _internal_error(exc) from exc
    return {"status": "ok", "cases": out, "count": len(out), "total_matching": len(cases),
            "registry_manifest_status": ledger.registry.snapshot.manifest_status}


@router.post("/resolve", dependencies=[Depends(verify_admin)])
@traced_request(_LEDGER, require_write=True, action="forecast_resolve_case")
async def resolve_case(request: Request):
    """
    Record the actual outcome of a forecast case (hợp đồng ``ForecastLedger.resolve_case``).

    Body: {"case_id": "...", "outcome_code": 1..8, "evidence_url": "https://...",
           "evidence_sha256": "<64 hex>", "adjudicator_id": "...", "resolved_at": null,
           "rationale": null}

    KHÔNG có bằng chứng mặc định: thiếu bằng chứng ⇒ ledger ghi ``resolution_rejected`` ⇒ 422.
    """
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="body must be a JSON object")
    ledger = _get_ledger()
    if ledger is None:
        raise _no_registry()
    outcome_code = body.get("outcome_code")
    if not isinstance(outcome_code, int) or isinstance(outcome_code, bool):
        raise HTTPException(status_code=422, detail="outcome_code must be an integer")
    case_id = str(body.get("case_id", ""))
    try:
        ledger.registry.case(case_id)
    except ForecastContractError as exc:
        raise HTTPException(status_code=404, detail="unknown forecast case") from exc
    try:
        result = ledger.resolve_case(
            case_id,
            outcome_code=outcome_code,
            evidence_url=body.get("evidence_url"),
            evidence_sha256=body.get("evidence_sha256"),
            adjudicator_id=body.get("adjudicator_id"),
            resolved_at=body.get("resolved_at"),
            rationale=body.get("rationale"),
        )
    except ForecastContractError as exc:  # ledger hỏng/không khớp registry ⇒ fail-closed
        logger.warning("[forecast] ledger contract error: %s", exc)
        raise HTTPException(status_code=409, detail={"status": "ledger_invalid", "reason": str(exc)[:200]}) from exc
    except Exception as exc:
        raise _internal_error(exc) from exc
    if result.get("event") == "resolution_rejected":
        raise HTTPException(status_code=422, detail=result)
    return result
