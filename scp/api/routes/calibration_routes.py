# SCP CIRCUIT: M02 — STATUS: CLOSED_WITH_KNOWN_GAP (closure: docs/evidence-summary/M02-closure.json)
"""
Calibration routes — Prediction→Reality resolution ledger (26-P0.08/09).

Routes:
  GET  /v105/calibration/stats    — Ledger summary stats
  POST /v105/calibration/predict  — Record a prediction to track
  POST /v105/calibration/resolve  — Resolve a prediction against reality
  GET  /v105/calibration/accuracy — Get accuracy metrics by domain
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request

from scp.api._shared import verify_admin
from scp.calibration.ledger import CalibrationLedger
from scp.core.request_run_ledger import RequestRunLedger, traced_request

router = APIRouter(prefix="/v105/calibration", tags=["calibration"])
_LEDGER = RequestRunLedger()

logger = logging.getLogger("scp.api.routes.calibration")

#: [PREDICTION-P0] Resolver tự động (đối chiếu Reality khác lineage) là lane P3 — chưa có.
_RESOLVER_STATUS = {
    "status": "not_implemented",
    "reason": "no automatic Reality resolver yet (lane P3); predictions stay unresolved, never self-graded",
}


def _data_dir() -> Path:
    # Đọc mỗi lần gọi (cùng ngữ nghĩa SCP_DATA_DIR với hook /ask trong ledger_stage).
    return Path(os.environ.get("SCP_DATA_DIR", "data"))


def _internal_error(exc: Exception) -> HTTPException:
    """[AUDIT-20260909 MACH2-BUG4] 500 fail-closed, không leak message nội bộ."""
    logger.warning("[calibration] internal error: %s", exc, exc_info=True)
    return HTTPException(status_code=500, detail="internal error")


def _get_ledger() -> CalibrationLedger:
    return CalibrationLedger(db_path=str(_data_dir() / "calibration.sqlite"))


def _str_list(value: object, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
        raise HTTPException(status_code=422, detail=f"{field} must be a list of strings")
    return value


@router.get("/stats", dependencies=[Depends(verify_admin)])
@traced_request(_LEDGER, require_write=False, action="calibration_stats")
async def calibration_stats():
    """Số liệu THẬT của ledger (không bao giờ "active" rỗng): đếm, đã/chưa đối chiếu,
    số dự đoán đóng được (có khoá chủ thể + confidence số), sức khoẻ hook /ask."""
    from scp.calibration.ask_hook import hook_health

    try:
        ledger = _get_ledger()
        try:
            metrics = ledger.metrics()
            closable = ledger.closability()
        finally:
            ledger.close()
    except Exception as exc:
        raise _internal_error(exc) from exc
    return {
        "subsystem": "calibration",
        "status": "ok" if metrics["count"] else "no_data",
        **metrics,
        **closable,
        "resolver": dict(_RESOLVER_STATUS),
        "ask_hook": hook_health(),
    }


@router.post("/predict", dependencies=[Depends(verify_admin)])
@traced_request(_LEDGER, require_write=True, action="calibration_record_prediction")
async def record_prediction(request: Request):
    """
    Record a new prediction for later reality resolution.

    Body: {"domain": "health", "task_class": "factcheck",
           "predictor_type": "judge", "predictor_id": "...",
           "prediction": {...}, "confidence": 0.85, "semantic_verdict": "VERIFIED",
           "subject_claim_id": "...", "input_hash": "...", "tested_sha": "...",
           "evidence_refs": ["..."]}
    """
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="body must be a JSON object")
    evidence_refs = _str_list(body.get("evidence_refs"), "evidence_refs")
    ledger = _get_ledger()
    try:
        return ledger.record_prediction(
            domain=str(body.get("domain", "general")),
            task_class=str(body.get("task_class", "factcheck")),
            predictor_type=str(body.get("predictor_type", "judge")),
            predictor_id=str(body.get("predictor_id", "")),
            predictor_version=body.get("predictor_version"),
            subject_claim_id=body.get("subject_claim_id"),
            prediction=body.get("prediction", {}),
            semantic_verdict=body.get("semantic_verdict", "UNKNOWN"),
            confidence=body.get("confidence"),
            input_hash=body.get("input_hash"),
            tested_sha=body.get("tested_sha"),
            evidence_refs=evidence_refs,
        )
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"invalid prediction: {type(exc).__name__}") from exc
    finally:
        ledger.close()


@router.post("/resolve", dependencies=[Depends(verify_admin)])
@traced_request(_LEDGER, require_write=True, action="calibration_resolve")
async def resolve_prediction(request: Request):
    """
    Resolve a tracked prediction against observed reality (hợp đồng ``CalibrationLedger.resolve``).

    Body: {"prediction_id": "...", "outcome": "VERIFIED|CONTRADICTED|INSUFFICIENT|UNKNOWN",
           "resolver_type": "...", "resolver_id": "...", "evidence_refs": ["..."],
           "supersedes_resolution_id": null}

    Không bằng chứng ⇒ 422 (giữ unresolved, không bịa sự thật). Resolver trùng predictor ⇒ 422
    (không tự chấm trên nhãn của chính mình).
    """
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="body must be a JSON object")
    missing = [k for k in ("prediction_id", "outcome", "resolver_type", "resolver_id")
               if not str(body.get(k) or "").strip()]
    if missing:
        raise HTTPException(status_code=422, detail=f"missing required fields: {missing}")
    evidence_refs = _str_list(body.get("evidence_refs"), "evidence_refs")
    prediction_id = str(body["prediction_id"])
    ledger = _get_ledger()
    try:
        try:
            pred = ledger.get_prediction(prediction_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="unknown prediction") from exc
        if (str(body["resolver_type"]).strip() == pred["predictor_type"]
                and str(body["resolver_id"]).strip() == pred["predictor_id"]):
            raise HTTPException(status_code=422, detail="resolver must differ from the predictor (no self-grading)")
        try:
            return ledger.resolve(
                prediction_id,
                outcome=str(body["outcome"]),
                resolver_type=str(body["resolver_type"]),
                resolver_id=str(body["resolver_id"]),
                evidence_refs=evidence_refs,
                supersedes_resolution_id=body.get("supersedes_resolution_id"),
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="unknown resolution to supersede") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)[:200]) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise _internal_error(exc) from exc
    finally:
        ledger.close()


@router.get("/accuracy", dependencies=[Depends(verify_admin)])
@traced_request(_LEDGER, require_write=False, action="calibration_accuracy")
async def calibration_accuracy(domain: str = ""):
    """Brier/ECE THẬT theo miền (``CalibrationLedger.metrics``); chưa có gì chấm được ⇒ no_data."""
    try:
        ledger = _get_ledger()
        try:
            result = ledger.metrics(domain=domain or None)
        finally:
            ledger.close()
    except Exception as exc:
        raise _internal_error(exc) from exc
    return {
        "domain_filter": domain or "*",
        "status": "ok" if result["binary_scored_count"] else "no_data",
        "accuracy": result,
    }
