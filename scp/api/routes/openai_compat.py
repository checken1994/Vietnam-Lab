# SCP CIRCUIT: M03 — STATUS: CLOSED_WITH_KNOWN_GAP (closure: reports/circuit-closures/M03-closure.json)
"""
[Task 8-A] OpenAI-compatible endpoints â€” extracted from api_server.py

Táº I SAO: api_server.py 2,144 LOC god file. TĂ¡ch 2 routes /v1/* vĂ o module
nĂ y cho PyRIT/garak integration tests. Backward-compatible â€” public API
paths/methods unchanged.

Routes:
  POST /v1/chat/completions   â€” OpenAI-compatible chat (PyRIT/garak target)
  GET  /v1/models             â€” OpenAI models list
"""
from __future__ import annotations

import json
import time
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse
from scp.security.jwt_guard import get_current_user

# Import only the shared logger here.  The canonical /ask adapter is imported
# lazily below because api_server imports this router during application setup.
from scp.api._shared import logger
from scp.core.release_identity import CANONICAL_MODEL_ID, model_id_candidates

from scp.core.request_run_ledger import RequestRunLedger, traced_request

_OPENAI_COMPAT_LEDGER = RequestRunLedger()

router = APIRouter(tags=["openai-compat"])


def _response_data(response: Any) -> dict[str, Any]:
    if hasattr(response, "model_dump"):
        return response.model_dump()
    if isinstance(response, dict):
        return dict(response)
    return dict(vars(response))


def _openai_error(message: str, error_type: str = "invalid_request", status_code: int = 400) -> JSONResponse:
    return JSONResponse(
        {"error": {"message": message, "type": error_type}},
        status_code=status_code,
    )


def _kernel_gate_unavailable(data: dict[str, Any]) -> bool:
    """Identify the adapter's hard kernel gate result before translation."""
    if data.get("falsification_status") == "KERNEL_GATE_UNAVAILABLE":
        return True
    guard = data.get("v98_guard")
    if isinstance(guard, dict) and guard.get("kernel_error"):
        return True
    classification = data.get("v98_classification")
    return isinstance(classification, dict) and classification.get("provenance") == "kernel_gate"


async def _run_canonical_ask(
    question: str,
    messages: list[dict[str, Any]],
    request: Request,
) -> Any:
    """Run the OpenAI request through the same kernel/governance path as /ask.

    This is intentionally lazy-imported because api_server mounts this router
    during composition.  The adapter is the authority for lease, evidence
    verification, and final policy state; this boundary must not call a judge or
    gateway directly and must not expose a response before it returns.
    """
    from scp.api_server import (
        _ask_impl,
        _ask_kernel_enabled,
        _get_ask_kernel_adapter,
        _kernel_gate_unavailable_response,
        app,
    )
    from scp.api_server_parts.helpers import AskRequest

    req = AskRequest(
        question=question,
        ai_answer="",
        source="openai_compat",
        conversation_history=[
            {"role": str(message.get("role", "user")), "content": str(message.get("content", ""))}
            for message in messages[-8:]
            if message.get("role") in {"user", "assistant"}
            and message.get("content") is not None
        ],
    )
    if not getattr(app.state, "judge_ready", False):
        return JSONResponse(
            status_code=503,
            content={
                "detail": "judge_initializing",
                "reason": getattr(app.state, "readiness_reason", None)
                or "judge_initialization_pending",
                "retry_after_seconds": 5,
            },
        )
    if not _ask_kernel_enabled(req):
        return _kernel_gate_unavailable_response(req, RuntimeError("rag_kernel_disabled"))
    adapter = _get_ask_kernel_adapter()
    if adapter is None:
        return _kernel_gate_unavailable_response(
            req,
            RuntimeError("kernel_adapter_unavailable"),
        )
    return await adapter.run_rag(req, request, _ask_impl)


@router.post("/v1/chat/completions")
@traced_request(_OPENAI_COMPAT_LEDGER, require_write=False, action="openai_chat")
async def openai_chat(request: Request, current_user: str = Depends(get_current_user)):
    """OpenAI-compatible endpoint â€” PyRIT/garak gá»i endpoint nĂ y.

    Extracts user message â†’ runs V98 pipeline â†’ returns OpenAI-format response.
    """
    # [SCP-DNA-FIX 4-a-007] Capture _t0 at the very START of the handler.
    # Táº I SAO: previously `elapsed_ms` was computed as
    # `round((time.time() - v98_context.get("_t0", time.time())) * 1000, 1)`,
    # but `_t0` was NEVER set in v98_context (both _shared._extract_v98_context
    # and helpers._extract_v98_context omit it). The fallback `time.time()`
    # was evaluated at the same moment as the subtraction's left operand â†’
    # elapsed_ms â‰ˆ 0 always (DNA #22: PASSâ‰ TRUE â€” field present but always 0).
    # Operators/PyRIT could not see real latency. Fix: local `_t0` captured
    # at handler entry, used in the response builder. perf_counter() for
    # precision (monotonic, not wall-clock).
    _t0 = time.perf_counter()

    # [AUDIT-20260909 M3] Malformed bodies used to escape as an unstructured
    # 500 (Request.json() JSONDecodeError) at an OpenAI-compat boundary that
    # PyRIT/garak fuzz by design. Fail closed with the OpenAI error envelope.
    try:
        body = await request.json()
    except Exception:
        logger.warning("[openai_compat] request body is not valid JSON")
        return JSONResponse(
            {"error": {"message": "Invalid JSON body", "type": "invalid_request"}},
            status_code=400,
        )

    if not isinstance(body, dict):
        logger.warning("[openai_compat] request body is not a JSON object")
        return JSONResponse(
            {"error": {"message": "Invalid JSON body: expected root object", "type": "invalid_request"}},
            status_code=400,
        )
    messages = body.get("messages", [])
    model = body.get("model", CANONICAL_MODEL_ID)

    # [AUDIT-20260909 M3] A wrongly-shaped `messages` (string / non-dict
    # items) previously reached the extraction loop and crashed with
    # AttributeError ('str' object has no attribute 'get') → unstructured
    # 500. Same fail-closed envelope as above.
    if not isinstance(messages, list) or not all(isinstance(m, dict) for m in messages):
        logger.warning("[openai_compat] rejected malformed messages payload")
        return JSONResponse(
            {"error": {"message": "messages must be a list of message objects", "type": "invalid_request"}},
            status_code=400,
        )

    # Extract last user message
    question = ""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            question = msg.get("content", "")
            break

    if not question:
        return JSONResponse({"error": {"message": "No user message", "type": "invalid_request"}}, status_code=400)

    try:
        canonical = await _run_canonical_ask(question, messages, request)
    except Exception:
        # Keep OpenAI compatibility shape while preserving /ask's fail-closed
        # posture.  No provider/judge detail crosses this boundary.
        logger.exception("[openai_compat] canonical ask pipeline failure")
        return _openai_error(
            "Upstream judge pipeline unavailable",
            error_type="server_error",
            status_code=503,
        )
    if isinstance(canonical, JSONResponse):
        status_code = int(getattr(canonical, "status_code", 503) or 503)
        if status_code >= 500:
            return _openai_error(
                "Upstream judge pipeline unavailable",
                error_type="server_error",
                status_code=status_code,
            )
        return _openai_error(
            "Request rejected by SCP policy",
            error_type="invalid_request",
            status_code=status_code,
        )
    v = _response_data(canonical)
    kernel_blocked = _kernel_gate_unavailable(v)
    if kernel_blocked:
        # A blocked kernel is an authorization failure, not an answer.  Return
        # an OpenAI-shaped error before constructing JSON/SSE choices so the
        # adapter's withheld marker (and any candidate) cannot become content.
        return _openai_error(
            "Kernel gate unavailable",
            error_type="server_error",
            status_code=503,
        )
    answer = str(v.get("final_answer", ""))
    verdict = str(v.get("verdict", ""))
    governance = str(v.get("governance_decision", ""))
    # UNKNOWN, policy verdicts, and policy governance are all holds.  Return
    # before either JSON choices or SSE can expose a refusal/candidate.
    withheld = (
        verdict in {"UNKNOWN", "ESCALATE", "REJECT", "DENY", "FAIL", "FLAGGED"}
        or governance in {
            "UNKNOWN", "KILL", "ESCALATE", "REJECT", "DENY", "FAIL", "FLAGGED"
        }
    )
    if withheld:
        return _openai_error(
            "Request withheld by SCP policy",
            error_type="policy_denied",
            status_code=503,
        )

    metadata = {
        "verdict": verdict,
        "confidence": v.get("confidence", 0.0),
        "falsification_status": v.get("falsification_status"),
        "governance_decision": governance,
        "v98_guard": (v.get("v98_guard") or {}).get("recommendation") if isinstance(v.get("v98_guard"), dict) else None,
        "v98_classification": (v.get("v98_classification") or {}).get("actor") if isinstance(v.get("v98_classification"), dict) else None,
        "v98_counter_phase": (v.get("v98_attack_policy") or {}).get("phase", 0) if isinstance(v.get("v98_attack_policy"), dict) else 0,
        "v98_canary_token": v.get("v98_canary_token"),
        "v98_bypass_recorded": v.get("v98_bypass_recorded", False),
        "elapsed_ms": round((time.perf_counter() - _t0) * 1000, 1),
        "kernel_run_status": v.get("run_status"),
        "kernel_ledger_status": v.get("ledger_status"),
    }
    if body.get("stream") is True:
        if withheld:
            # A policy-held/epistemically unknown answer is never tokenized.
            # Return an OpenAI-shaped error before constructing StreamingResponse
            # so a client cannot observe refusal text as content deltas.
            return _openai_error(
                "Request withheld by SCP policy",
                error_type="policy_denied",
                status_code=503,
            )
        completion_id = f"chatcmpl-{uuid.uuid4().hex[:8]}"
        created_time = int(time.time())

        async def generate_sse():
            # The canonical adapter has already completed policy/evidence checks.
            # Only this verified/withheld answer is translated to SSE; no token or
            # provider call occurs from inside the stream generator.
            if answer:
                words = answer.split(" ")
                for index, word in enumerate(words):
                    content = word if index == len(words) - 1 else word + " "
                    chunk = {
                        "id": completion_id,
                        "object": "chat.completion.chunk",
                        "created": created_time,
                        "model": model,
                        "choices": [{
                            "index": 0,
                            "delta": {"content": content},
                            "finish_reason": None,
                        }],
                    }
                    yield f"data: {json.dumps(chunk)}\n\n"
            stop_chunk = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created_time,
                "model": model,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            }
            yield f"data: {json.dumps(stop_chunk)}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(
            generate_sse(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:8]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": answer},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        "scp_metadata": metadata,
        "run_id": v.get("run_id"),
        "trace_id": v.get("trace_id"),
        "run_status": v.get("run_status"),
        "ledger_status": v.get("ledger_status"),
    }



@router.get("/v1/models")
@traced_request(_OPENAI_COMPAT_LEDGER, require_write=False, action="openai_models")
async def openai_models(current_user: str = Depends(get_current_user)):
    """OpenAI-compatible models list."""
    return {
        "object": "list",
        "data": [{
            "id": model_id,
            "object": "model",
            "created": int(time.time()),
            "owned_by": "scp",
            "canonical": index == 0,
            "deprecated": index != 0,
        } for index, model_id in enumerate(model_id_candidates())],
    }
