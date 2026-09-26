"""Trace lookup router and implementation for SCP API (Milestone 3).

Exposes GET /api/scp/v3/trace/{trace_id} with sensitive attribute redaction
and causal graph serialization. Requires admin verification.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from scp.api._shared import verify_admin
from scp.core.trace_contract import redact_attributes
from scp.core.trace_store import get_trace_store

logger = logging.getLogger("scp.api.trace")

router = APIRouter(dependencies=[Depends(verify_admin)])


@router.get("/api/scp/v3/trace/{trace_id}")
@router.get("/api/v3/trace/{trace_id}")
@router.get("/v3/trace/{trace_id}")
async def get_trace_record(trace_id: str) -> dict[str, Any]:
    """Retrieve backward-traceable causal history for a specific trace_id."""
    store = get_trace_store()
    record = store.get_trace(trace_id)

    # Fallback to legacy ledgers if not found in primary SQLite TraceStore
    if not record:
        try:
            from scp.trace_ledger import TraceLedger

            data_dir = Path("data")
            if not data_dir.exists():
                data_dir = Path(__file__).resolve().parent.parent.parent / "data"

            for candidate_filename in (
                "trace_ledger.jsonl",
                "ask_task_kernel_trace.jsonl",
                "trace.jsonl",
                "request_runs.jsonl",
            ):
                file_path = data_dir / candidate_filename
                if file_path.exists():
                    # Read-only lookup: verify_on_init=False so a GET can never
                    # trigger the boot CHAIN_RECOVERY re-anchor write (recovery
                    # belongs to the boot/append path, not to read paths).
                    ledger_record = TraceLedger(file_path, verify_on_init=False).get_trace(trace_id)
                    if ledger_record:
                        if isinstance(ledger_record.get("fields"), dict):
                            merged = dict(ledger_record["fields"])
                            merged.update({k: v for k, v in ledger_record.items() if k != "fields"})
                            merged["_ledger_entry"] = ledger_record
                            record = merged
                        else:
                            record = ledger_record
                        break
        except Exception as _exc:
            logger.debug("Legacy trace ledger fallback lookup error: %s", _exc)

    if not record:
        raise HTTPException(status_code=404, detail=f"Trace record not found: {trace_id}")

    return redact_attributes(record)


__all__ = ["router", "get_trace_record"]
