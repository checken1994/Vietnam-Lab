"""Canonical lower-assurance SSE transport for the /ask pipeline.

The route exposes progress metadata and one terminal event. It never calls a
local judge directly: generation and verification are delegated to the same
AskKernelAdapter boundary used by /ask and OpenAI compatibility routes.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from scp.api._shared import verify_admin
from scp.core.request_run_ledger import RequestRunLedger, traced_request

logger = logging.getLogger("scp.api.stream")
_STREAM_ROUTES_LEDGER = RequestRunLedger()
router = APIRouter(tags=["stream"])


class StreamAskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=5000)
    ai_answer: str = Field("", max_length=10000)


_WITHHELD_VERDICTS = frozenset(
    {"UNKNOWN", "FAIL", "ESCALATE", "KILL", "REJECT", "DENY", "FLAGGED"}
)
_WITHHELD_GOVERNANCE = frozenset(
    {"UNKNOWN", "FAIL", "ESCALATE", "KILL", "REJECT", "DENY", "FLAGGED"}
)


def _dump_response(response: Any) -> dict[str, Any]:
    if hasattr(response, "model_dump"):
        return response.model_dump()
    if isinstance(response, dict):
        return dict(response)
    return dict(vars(response))


def _withheld(data: dict[str, Any]) -> bool:
    verdict = str(data.get("verdict") or "").strip().upper()
    governance = str(
        data.get("governance_decision") or data.get("governance") or ""
    ).strip().upper()
    run_status = str(data.get("run_status") or "").strip().upper()
    return (
        verdict in _WITHHELD_VERDICTS
        or governance in _WITHHELD_GOVERNANCE
        or run_status in _WITHHELD_VERDICTS
    )


def _sse(event: dict[str, Any]) -> str:
    """Serialize one JSON SSE event; never split or tokenize candidate text."""
    return f"data: {json.dumps(event, ensure_ascii=False, sort_keys=True)}\n\n"


@router.post("/v105/ask/stream", dependencies=[Depends(verify_admin)])
@traced_request(_STREAM_ROUTES_LEDGER, require_write=False, action="ask_stream")
async def ask_stream(req: StreamAskRequest, request: Request):
    """Stream canonical /ask progress and a policy-gated terminal event."""

    async def generate():
        try:
            yield _sse({"step": "classify", "status": "running", "ts": time.time()})
            # Compatibility progress frame only; no independent classifier runs.
            yield _sse(
                {
                    "step": "classify",
                    "status": "done",
                    "domain": "general",
                    "confidence": 0.0,
                    "method": "canonical_ask",
                }
            )
            yield _sse({"step": "slm_predict", "status": "running", "ts": time.time()})
            yield _sse({"step": "judge", "status": "running", "ts": time.time()})

            from scp.api_server import (
                _ask_impl,
                _ask_kernel_enabled,
                _get_ask_kernel_adapter,
                _kernel_gate_unavailable_response,
                app,
            )
            from scp.api_server_parts.helpers import AskRequest

            if not getattr(app.state, "judge_ready", False):
                yield _sse(
                    {
                        "step": "final",
                        "status": "withheld",
                        "question": req.question,
                        "verdict": "UNKNOWN",
                        "governance_decision": "ESCALATE",
                        "confidence": 0.0,
                        "domain": "general",
                        "final_answer": None,
                        "candidate": None,
                        "reasoning": None,
                        "evidence": {},
                        "reason": "judge_initializing",
                    }
                )
                return

            canonical_req = AskRequest(
                question=req.question,
                ai_answer=req.ai_answer,
                source="v105_stream",
            )
            if not _ask_kernel_enabled(canonical_req):
                response = _kernel_gate_unavailable_response(
                    canonical_req, RuntimeError("rag_kernel_disabled")
                )
            else:
                adapter = _get_ask_kernel_adapter()
                if adapter is None:
                    response = _kernel_gate_unavailable_response(
                        canonical_req, RuntimeError("kernel_adapter_unavailable")
                    )
                else:
                    # Adapter owns generation, verification, signed receipt and
                    # lifecycle state. No direct judge call occurs here.
                    response = await adapter.run_rag(canonical_req, request, _ask_impl)

            data = _dump_response(response)
            if _withheld(data):
                final = {
                    "step": "final",
                    "status": "withheld",
                    "question": req.question,
                    "verdict": str(data.get("verdict") or "UNKNOWN").upper(),
                    "governance_decision": str(
                        data.get("governance_decision")
                        or data.get("governance")
                        or "ESCALATE"
                    ).upper(),
                    "confidence": data.get("confidence", 0.0),
                    "domain": data.get("domain"),
                    "final_answer": None,
                    "candidate": None,
                    "reasoning": None,
                    "evidence": {},
                    "reason": "canonical_policy_hold",
                    "run_id": data.get("run_id"),
                    "trace_id": data.get("trace_id"),
                    "run_status": data.get("run_status"),
                    "ledger_status": data.get("ledger_status"),
                }
            else:
                final = {
                    "step": "final",
                    "status": "complete",
                    "question": req.question,
                    "verdict": data.get("verdict"),
                    "confidence": data.get("confidence"),
                    "domain": data.get("domain"),
                    "final_answer": data.get("final_answer"),
                    "candidate": data.get("final_answer"),
                    "reasoning": data.get("reasoning"),
                    "evidence": data.get("evidence") if isinstance(data.get("evidence"), dict) else {},
                    "governance_decision": data.get("governance_decision"),
                    "run_id": data.get("run_id"),
                    "trace_id": data.get("trace_id"),
                    "run_status": data.get("run_status"),
                    "ledger_status": data.get("ledger_status"),
                }
            yield _sse(final)
        except Exception as exc:
            logger.error("Stream error: %s", type(exc).__name__, exc_info=True)
            # Do not leak candidate or internal exception details.
            yield _sse(
                {
                    "step": "final",
                    "status": "withheld",
                    "question": req.question,
                    "verdict": "FAIL",
                    "governance_decision": "KILL",
                    "confidence": 0.0,
                    "domain": None,
                    "final_answer": None,
                    "candidate": None,
                    "reasoning": None,
                    "evidence": {},
                    "reason": "stream_pipeline_unavailable",
                }
            )

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
