# SCP CIRCUIT: M03 — STATUS: CLOSED_WITH_KNOWN_GAP (closure: docs/evidence-summary/M03-closure.json)
"""
[Task 8-A] OpenAI-compatible endpoints — extracted from api_server.py

TẠI SAO: api_server.py 2,144 LOC god file. Tách 2 routes /v1/* vào module
này cho PyRIT/garak integration tests. Backward-compatible — public API
paths/methods unchanged.

Routes:
  POST /v1/chat/completions   — OpenAI-compatible chat (PyRIT/garak target)
  GET  /v1/models             — OpenAI models list
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
import uuid
from types import SimpleNamespace

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse

# Import shared deps from api_server (same pattern as api/chat.py + admin_v98.py)
# [BLE001-fix] logger được định nghĩa local (cùng singleton "scp.api" như
# scp.api._shared) để ruff resolve được logging call trong except handler.
from scp.api._shared import _extract_v98_context, get_judge
from scp.api.rate_limiting import limiter
from scp.api_server_parts.pipeline.delivery_rules import DecisionCode, DeliveryRuleEngine
from scp.api_server_parts.pipeline.verification_stage import DotDict
from scp.core.release_identity import CANONICAL_MODEL_ID, model_id_candidates
from scp.core.request_run_ledger import RequestRunLedger, traced_request
from scp.llm_gateway.client import get_gateway
from scp.runtime.question_router import LANE_CHATBOT, detect_language, route_question
from scp.security.jwt_guard import get_current_user

logger = logging.getLogger("scp.api")

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
    """OpenAI-compatible endpoint — PyRIT/garak gọi endpoint này.

    Extracts user message → runs V98 pipeline → returns OpenAI-format response.
    `response: Response` được FastAPI DI cung cấp để gắn header stub mà vẫn
    trả plain dict (traced_request.attach phải inject run_status/ledger_status
    vào body — trả JSONResponse sẽ làm mất chúng, gãy ledger contract).
    """
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
        return JSONResponse(
            {"error": {"message": f"LLM generation failed: {exc}", "type": "server_error", "code": 502}},
            status_code=502,
        )

    if not ai_answer:
        logger.warning("[openai_compat] LLM generation returned empty response")
        return JSONResponse(
            {"error": {"message": "LLM generation returned empty response", "type": "server_error", "code": 502}},
            status_code=502,
        )

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

    # Delivery & Decision Boundary via DeliveryRuleEngine
    v_dict = dict(v) if isinstance(v, dict) else getattr(v, "__dict__", {})
    if not v_dict.get("final_answer"):
        v_dict["final_answer"] = v_dict.get("evidence", {}).get("final_answer", "") or ai_answer
    if "evidence" not in v_dict or not isinstance(v_dict["evidence"], dict):
        v_dict["evidence"] = {}
    v_norm = DotDict(v_dict)

    route_dec = route_question(question)
    is_chatbot = (getattr(route_dec, "lane", "") == LANE_CHATBOT)
    ctx = SimpleNamespace(
        req=SimpleNamespace(question=question),
        route_decision=route_dec,
        is_chatbot_lane=is_chatbot,
        ai_answer=ai_answer,
        history=messages[:-1],
        resolve=lambda k, d: d,
        web_fallback_used=False,
        has_provided_evidence=False,
        retrieval_res=None,
        multimodal_block=False,
    )

    gov_decision = v_norm.evidence.get("governance_decision", "")
    decision = DeliveryRuleEngine.evaluate(ctx, v_norm, gov_decision)
    if decision.http_exception:
        return JSONResponse(
            {
                "error": {
                    "message": decision.http_exception.detail,
                    "type": "access_denied",
                    "code": decision.http_exception.status_code,
                }
            },
            status_code=decision.http_exception.status_code,
        )

    is_conversational = DeliveryRuleEngine.is_strictly_conversational(ctx)
    verdict = v_norm.get("verdict", "")

    # Fail-closed delivery logic matching /ask policy:
    # If verdict is ABSTAIN, DEGRADED, UNKNOWN, FAIL, FLAGGED, or KILL:
    #   - If strictly conversational (harmless greeting/identity chitchat), allow answer.
    #   - If factual/mixed or governance ESCALATE/KILL or decision error: withhold fail-closed!
    is_withheld = False
    if gov_decision in ("KILL", "DENY") or verdict in ("FAIL", "FLAGGED"):
        is_withheld = True
    elif decision.code in (
        DecisionCode.ERR_SECURITY_VIOLATION,
        DecisionCode.ERR_GOVERNANCE_ESCALATE,
        DecisionCode.ERR_GOVERNANCE_DEGRADED,
        DecisionCode.ERR_FACTUAL_FAILED,
        DecisionCode.ERR_WHY_GATE_REJECTED,
        DecisionCode.ERR_GOVERNANCE_MISSING,
    ) or decision.should_clear_fields:
        is_withheld = True
    elif verdict in ("ABSTAIN", "DEGRADED", "UNKNOWN"):
        if not is_conversational or gov_decision == "ESCALATE":
            is_withheld = True

    if is_withheld:
        answer = "I cannot comply with this request."
        response.headers["x-scp-delivery"] = "withheld"
    elif is_conversational and decision.code in (
        DecisionCode.DELIVER_CHATBOT,
        DecisionCode.DELIVER_UNVERIFIED_ABSTAIN,
    ):
        answer = decision.final_answer or ai_answer
        response.headers["x-scp-delivery"] = "conversational"
    elif decision.code == DecisionCode.DELIVER_UNVERIFIED_ABSTAIN:
        answer = decision.final_answer or ai_answer
        response.headers["x-scp-delivery"] = "unverified_abstain"
    else:
        answer = decision.final_answer or v_norm.get("final_answer", "") or ai_answer
        response.headers["x-scp-delivery"] = "verified"

    response.headers["x-scp-decision-code"] = str(decision.code)

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
            "delivery_code": getattr(decision, "code", None),
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
