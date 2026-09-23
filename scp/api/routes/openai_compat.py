# SCP CIRCUIT: M03 — STATUS: CLOSED_WITH_KNOWN_GAP (closure: docs/evidence-summary/M03-closure.json)
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

import asyncio
import time
import uuid

from fastapi import APIRouter, Request, Depends
from fastapi.responses import JSONResponse
from scp.security.jwt_guard import get_current_user

# Import shared deps from api_server (same pattern as api/chat.py + admin_v98.py)
from scp.api._shared import _extract_v98_context, get_judge, logger
from scp.core.release_identity import CANONICAL_MODEL_ID, model_id_candidates

from scp.core.request_run_ledger import RequestRunLedger, traced_request

_OPENAI_COMPAT_LEDGER = RequestRunLedger()

router = APIRouter(tags=["openai-compat"])


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

    judge = get_judge()
    v98_context = _extract_v98_context(request)
    v98_context["body"] = question

    # [V104.41 #AA] Táº I SAO: was calling judge.judge() synchronously in async def
    # â†’ blocks event loop when SLM/API slow. PyRIT/garak parallel requests â†’ server hang.
    # Fix: use asyncio.to_thread (same as /ask path).
    # [AUDIT-20260909 M3][ERR-1] A judge pipeline failure must surface as a
    # structured OpenAI 503 — never an unstructured 500, never an internal
    # message leak (same posture as the M2 BUG 4 fix). Logged at ERROR so the
    # failure stays loud (D6 fail-loudly).
    try:
        v = await asyncio.to_thread(
            judge.judge,
            question=question, ai_answer="", cycle_count=0, source="openai_compat",
            v98_context=v98_context
        )
    except Exception:
        logger.exception("[openai_compat] judge pipeline failure")
        return JSONResponse(
            {"error": {"message": "Upstream judge pipeline unavailable", "type": "server_error"}},
            status_code=503,
        )

    # [V104.41 #AC] Táº I SAO: DoS record_verdict never called â†’ verdict-quality circuit dead.
    # Fix: record verdict after judge completes.
    if hasattr(judge, 'dos_protection') and judge.dos_protection:
        try:
            judge.dos_protection.record_verdict(v.get("verdict", ""))
        except Exception as e:
            logger.debug(f"[V104.41 #AC] DoS record_verdict error: {e}")

    # [V104.41 #X] Enforce KILL/FAIL/FLAGGED at OpenAI boundary too (consistency with /ask)
    answer = v.get("final_answer", "")
    _gov = v.get("evidence", {}).get("governance_decision", "")
    if _gov == "KILL" or v.get("verdict", "") in ("FAIL", "FLAGGED"):
        answer = "I cannot comply with this request."
    elif v.get("verdict", "") == "UNKNOWN" and answer:
        answer = answer + "\n\n[SCP: unverified â€” confidence below threshold]"

    # [V104.41 #AB] Táº I SAO: canary was appended to visible content â†’ attacker sees it
    # immediately â†’ honeypot value destroyed. Fix: put canary in response metadata only,
    # NOT in visible content.
    canary = v.get("evidence", {}).get("v98_canary_token")

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
        "scp_metadata": {
            "verdict": v.get("verdict", ""),
            "confidence": v.get("confidence", 0.0),
            "falsification_status": v.get("evidence", {}).get("falsification_status"),
            "governance_decision": v.get("evidence", {}).get("governance_decision"),
            "v98_guard": v.get("evidence", {}).get("v98_guard_verdict", {}).get("recommendation") if v.get("evidence", {}).get("v98_guard_verdict") else None,
            "v98_classification": v.get("evidence", {}).get("v98_classification", {}).get("actor") if v.get("evidence", {}).get("v98_classification") else None,
            "v98_counter_phase": v.get("evidence", {}).get("v98_attack_policy", {}).get("phase") if v.get("evidence", {}).get("v98_attack_policy") else 0,
            "v98_canary_token": canary,
            "v98_bypass_recorded": v.get("evidence", {}).get("v98_bypass_recorded", False),
            "elapsed_ms": round((time.perf_counter() - _t0) * 1000, 1),
        },
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
