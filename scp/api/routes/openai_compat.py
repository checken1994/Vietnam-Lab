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
import json
import logging
import threading
import time
import uuid

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse

# Import shared deps from api_server (same pattern as api/chat.py + admin_v98.py)
# [BLE001-fix] logger được định nghĩa local (cùng singleton "scp.api" như
# scp.api._shared) để ruff resolve được logging call trong except handler.
from scp.api._shared import _extract_v98_context, get_judge
from scp.api.rate_limiting import limiter
from scp.security.jwt_guard import get_current_user

logger = logging.getLogger("scp.api")
from scp.core.release_identity import CANONICAL_MODEL_ID, model_id_candidates
from scp.core.request_run_ledger import RequestRunLedger, traced_request

_OPENAI_COMPAT_LEDGER = RequestRunLedger()

router = APIRouter(tags=["openai-compat"])

# [M03-STUB-VISIBILITY 2026-09-26] TẠI SAO: /v1/chat/completions hiện KHÔNG
# sinh câu trả lời bằng LLM — nó chạy judge với ai_answer='' (known gap M03,
# M03-closure.json: CLOSED_WITH_KNOWN_GAP). PyRIT/garak consumers previously
# could mistake the canned/refusal-shaped completion for real generation.
# Minimal fix (không xây feature): mark rõ stub — top-level "warning" field +
# response header "x-scp-stub: true" + WARNING đúng 1 lần mỗi process.
_STUB_WARNING = "openai_compat stub: no LLM generation performed (M03 gap)"
_STUB_WARNING_LOGGED = False
_STUB_WARNING_LOCK = threading.Lock()


def _log_stub_warning_once() -> None:
    """Emit the stub WARNING exactly once per process (no per-request spam)."""
    global _STUB_WARNING_LOGGED
    with _STUB_WARNING_LOCK:
        if not _STUB_WARNING_LOGGED:
            _STUB_WARNING_LOGGED = True
            logger.warning("[openai_compat] %s", _STUB_WARNING)


@router.post("/v1/chat/completions")
@limiter.limit("60/minute")
@traced_request(_OPENAI_COMPAT_LEDGER, require_write=False, action="openai_chat")
async def openai_chat(request: Request, response: Response, current_user: str = Depends(get_current_user)):
    """OpenAI-compatible endpoint â€” PyRIT/garak gá»i endpoint nĂ y.

    Extracts user message â†’ runs V98 pipeline â†’ returns OpenAI-format response.
    `response: Response` được FastAPI DI cung cấp để gắn header stub mà vẫn
    trả plain dict (traced_request.attach phải inject run_status/ledger_status
    vào body — trả JSONResponse sẽ làm mất chúng, gãy ledger contract).
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

    # [SCP-06 / M-09] Limit request body size to max 2MB to prevent memory exhaustion DoS
    MAX_CHAT_BODY_BYTES = 2 * 1024 * 1024
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_CHAT_BODY_BYTES:
                return JSONResponse(
                    {"error": {"message": "Payload too large (max 2MB)", "type": "invalid_request_error"}},
                    status_code=413,
                )
        except ValueError:
            logger.debug("[openai_compat] invalid content-length header: %s", content_length)

    chunks: list[bytes] = []
    total_bytes = 0
    async for chunk in request.stream():
        total_bytes += len(chunk)
        if total_bytes > MAX_CHAT_BODY_BYTES:
            return JSONResponse(
                {"error": {"message": "Payload too large (max 2MB)", "type": "invalid_request_error"}},
                status_code=413,
            )
        chunks.append(chunk)
    raw_body = b"".join(chunks)

    # [AUDIT-20260909 M3] Malformed bodies used to escape as an unstructured
    # 500 (Request.json() JSONDecodeError) at an OpenAI-compat boundary that
    # PyRIT/garak fuzz by design. Fail closed with the OpenAI error envelope.
    try:
        body = json.loads(raw_body.decode("utf-8")) if raw_body else {}
    except Exception:
        logger.warning("[openai_compat] request body is not valid JSON", exc_info=True)
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

    # [GAP-5 FIX / FAIL-CLOSED] Check if an active LLM provider is configured.
    # If running as stub or no active provider is enabled, fail closed with HTTP 501.
    import os
    from scp.llm_gateway.client import get_gateway

    is_stub = os.environ.get("SCP_OPENAI_COMPAT_STUB", "0").lower() in ("1", "true", "yes")
    gateway = get_gateway()
    if is_stub or not gateway.has_active_providers():
        return JSONResponse(
            {
                "error": {
                    "message": (
                        "LLM chat completions endpoint requires an active LLM provider. "
                        "Please configure GEMINI_API_KEY, DEEPSEEK_API_KEY, GROQ_API_KEY, "
                        "OPENAI_API_KEY, MISTRAL_API_KEY, or OPENROUTER_API_KEY."
                    ),
                    "type": "not_implemented_error",
                    "code": 501,
                }
            },
            status_code=501,
        )

    # [F-RUN-01 audit-r2 2026-10-01] Kill-switch admission gate for the chat lane.
    from scp.api_server import _kill_switch_blocked_response, _pc_kill_switch_engaged
    from scp.api_server_parts.helpers import AskRequest

    if _pc_kill_switch_engaged():
        _blocked = _kill_switch_blocked_response(
            AskRequest(question=question[:8000], source="openai_compat")
        )
        logger.warning(
            "[F-RUN-01] /v1/chat/completions refused: kill switch engaged "
            "(falsification_status=%s)",
            _blocked.falsification_status,
        )
        return JSONResponse(
            {
                "error": {"message": _blocked.final_answer, "type": "server_error"},
                "scp_metadata": {
                    "verdict": _blocked.verdict,
                    "falsification_status": _blocked.falsification_status,
                    "governance_decision": _blocked.governance_decision,
                    "run_status": _blocked.run_status,
                    "ledger_status": _blocked.ledger_status,
                    "kill_switch": "engaged",
                },
            },
            status_code=503,
        )

    judge = get_judge()
    v98_context = _extract_v98_context(request)
    v98_context["body"] = question

    # [AUDIT-FIX 2026-09-24] Resource-quota pairing on DoSProtectionEngine
    dos = getattr(judge, "dos_protection", None)
    _dos_slot_taken = False
    if dos:
        try:
            client_ip = request.client.host if request.client else "unknown"
            dos_alert = dos.check_request(client_ip)
            action = getattr(dos_alert, "action_taken", "") if dos_alert else ""
            should_block = action in ("block", "throttle") or (isinstance(dos_alert, dict) and dos_alert.get("should_block"))
            if should_block:
                status_code = int(getattr(dos_alert, "status_code", 0) or 429)
                headers = dict(getattr(dos_alert, "recommended_headers", {}) or {})
                if status_code == 429 and "Retry-After" not in headers:
                    headers["Retry-After"] = "60"
                return JSONResponse(
                    {"error": {"message": "Rate limit exceeded", "type": "rate_limit_error"}},
                    status_code=status_code,
                    headers=headers,
                )
            _dos_slot_taken = dos_alert is None
        except Exception as e:
            logger.debug(f"[openai_compat] DoS check error: {e}", exc_info=True)

    # [GAP-5 ROUTING] When active provider exists, route generation via LLMGateway
    conversation_context = ""
    history_parts = []
    for msg in messages[:-1]:
        r = msg.get("role", "")
        c = msg.get("content", "")
        if r and c:
            history_parts.append(f"{r}: {c}")
    if history_parts:
        conversation_context = "\n".join(history_parts)

    from scp.runtime.question_router import detect_language
    from scp.api.system_prompt import build_system_prompt

    lang = detect_language(question)
    sys_prompt = build_system_prompt(lang)
    for msg in messages:
        if msg.get("role") == "system" and msg.get("content"):
            sys_prompt = f"{sys_prompt}\n\n{msg.get('content')}"
            break

    ai_answer = ""
    try:
        gen_answer, _provider = await gateway.chat(
            question,
            context=conversation_context,
            system_prompt=sys_prompt,
            task="chat",
        )
        if gen_answer and gen_answer.strip():
            ai_answer = gen_answer.strip()
    except Exception as exc:
        logger.warning("[openai_compat] LLM generation error: %s", exc, exc_info=True)

    _verdict_recorded = False
    try:
        v = await asyncio.to_thread(
            judge.judge,
            question=question, ai_answer=ai_answer, cycle_count=0, source="openai_compat",
            v98_context=v98_context
        )
        if dos and _dos_slot_taken and not _verdict_recorded:
            try:
                dos.record_verdict(v.get("verdict", ""))
                _verdict_recorded = True
                _dos_slot_taken = False
            except Exception as e:
                logger.debug(f"[V104.41 #AC] DoS record_verdict error: {e}", exc_info=True)
    except Exception:
        logger.exception("[openai_compat] judge pipeline failure")
        return JSONResponse(
            {"error": {"message": "Upstream judge pipeline unavailable", "type": "server_error"}},
            status_code=503,
        )
    finally:
        if _dos_slot_taken and dos and not _verdict_recorded:
            _dos_slot_taken = False
            try:
                dos.release_slot()
            except Exception as _dos_release_err:
                logger.debug(f"[openai_compat] DoS slot release error: {_dos_release_err}", exc_info=True)

    answer = v.get("final_answer", "") or ai_answer
    _gov = v.get("evidence", {}).get("governance_decision", "")
    if _gov == "KILL" or v.get("verdict", "") in ("FAIL", "FLAGGED"):
        answer = "I cannot comply with this request."
    elif v.get("verdict", "") == "UNKNOWN" and answer:
        answer = answer + "\n\n[SCP: unverified — confidence below threshold]"

    canary = v.get("evidence", {}).get("v98_canary_token")

    payload = {
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
    return payload


@router.get("/v1/models")
@limiter.limit("60/minute")
@traced_request(_OPENAI_COMPAT_LEDGER, require_write=False, action="openai_models")
async def openai_models(request: Request, current_user: str = Depends(get_current_user)):
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
