# SCP CIRCUIT: M02 — Pipeline Pattern Refactor
from __future__ import annotations

import asyncio
import base64
import binascii
import logging
import os
from typing import Any

from fastapi import HTTPException
from fastapi.responses import JSONResponse

from scp.api_server_parts.helpers import (
    AskResponse,
    _extract_v98_context,
    _safe_fetch_url,
    get_judge,
)
from scp.api_server_parts.pipeline.base import PipelineStage
from scp.api_server_parts.pipeline.context import AskPipelineContext
from scp.core.request_run_ledger import stage_request
from scp.security.image_voice_detector import ImageJailbreakDetector, VoiceJailbreakDetector

logger = logging.getLogger(__name__)


class SecurityStage(PipelineStage):
    """Stage 1: DoS protection & rate limit check, multimodal image/voice jailbreak

    scanning, and SSRF fail-closed egress enforcement.
    """

    name: str = "security"

    async def execute(self, ctx: AskPipelineContext) -> None:
        judge = ctx.judge
        if judge is None:
            judge_fn = ctx.resolve("get_judge", get_judge)
            judge = judge_fn() if callable(judge_fn) else None
            ctx.judge = judge

        stage_req = ctx.resolve("stage_request", stage_request)
        stage_req(ctx.request, "judge_ready")

        extract_fn = ctx.resolve("_extract_v98_context", _extract_v98_context)
        v98_context = extract_fn(ctx.request)
        v98_context["body"] = ctx.req.question
        if ctx.req.session_id:
            v98_context["session_id"] = ctx.req.session_id

        # Pre-load conversation history into v98_context
        _history: list[dict[str, str]] = []
        for _turn in (ctx.req.conversation_history or [])[-8:]:
            if not isinstance(_turn, dict):
                continue
            _role = str(_turn.get("role", "user"))[:16]
            _content = str(_turn.get("content", ""))[:500].strip()
            if _content and _role in {"user", "assistant", "scp"}:
                _norm_role = "assistant" if _role in {"assistant", "scp"} else "user"
                _history.append({"role": _norm_role, "content": _content})

        if ctx.req.session_id and not _history:
            try:
                from scp.core.chat_memory import get_chat_memory_store

                _mem_store_preload = get_chat_memory_store()
                _loaded = await asyncio.to_thread(_mem_store_preload.load, ctx.req.session_id, limit=8)
                for _rec in _loaded:
                    _r = str(_rec.get("role", "user"))
                    _c = str(_rec.get("content", ""))[:500].strip()
                    if _c and _r in {"user", "assistant", "scp"}:
                        _norm_r = "assistant" if _r in {"assistant", "scp"} else "user"
                        _history.append({"role": _norm_r, "content": _c})
            except Exception as _mem_ld_exc:
                logger.warning("[SecurityStage] Failed to load chat memory: %s", _mem_ld_exc, exc_info=True)

        if _history:
            v98_context["conversation_history"] = _history

        # Pre-judge cognitive router hooks
        try:
            from scp.api.cognitive_router import run_pre_judge_hooks

            v98_context = await run_pre_judge_hooks(ctx.req.question, v98_context)
        except Exception as e:
            logger.warning(f"Cognitive router failed: {e}", exc_info=True)

        ctx.v98_context = v98_context

        # DoS protection & rate limit check
        ctx.dos_slot_taken = False
        if hasattr(judge, "dos_protection") and judge.dos_protection:
            try:
                client_ip = ctx.request.client.host if ctx.request.client else "unknown"
                ctx.client_ip = client_ip
                dos_alert = judge.dos_protection.check_request(client_ip)
                ctx.dos_slot_taken = dos_alert is None
                action = getattr(dos_alert, "action_taken", "") if dos_alert else ""
                should_block = action in ("block", "throttle") or (
                    isinstance(dos_alert, dict) and dos_alert.get("should_block")
                )
                if should_block:
                    status_code = int(getattr(dos_alert, "status_code", 0) or 429)
                    headers = dict(getattr(dos_alert, "recommended_headers", {}) or {})
                    ctx.early_response = JSONResponse(
                        {"error": {"message": "Rate limit exceeded", "type": "rate_limit_error"}},
                        status_code=status_code,
                        headers=headers,
                    )
                    return
            except Exception as e:
                logger.debug(f"[V104.17] DoS check error: {e}", exc_info=True)

        # Multimodal image & voice validation
        _multimodal_block = False
        _img_bytes = None
        _detector_degraded = False
        _detector_notes: list[str] = []
        _DETECT_TIMEOUT_SECONDS = 10.0

        from scp.core.url_fetcher import FetchBlockedError as _FetchBlockedError
        from scp.policy.egress import EgressDeniedError as _EgressDeniedError

        safe_fetch_fn = ctx.resolve("_safe_fetch_url", _safe_fetch_url)
        img_detector = ctx.resolve("_image_detector", None) or ImageJailbreakDetector()
        voice_detector = ctx.resolve("_voice_detector", None) or VoiceJailbreakDetector()

        if ctx.req.image_data:
            try:
                _raw_image = ctx.req.image_data
                if "," in _raw_image and _raw_image.lower().startswith("data:"):
                    _raw_image = _raw_image.split(",", 1)[1]
                _img_bytes = base64.b64decode(_raw_image, validate=True)
                if not _img_bytes or len(_img_bytes) > 6000000:
                    raise ValueError("image_too_large_or_empty")
            except (binascii.Error, ValueError):
                raise HTTPException(status_code=400, detail="Invalid or oversized image_data") from None

        if ctx.req.image_url and _img_bytes is None:
            try:
                _img_bytes = await asyncio.to_thread(safe_fetch_fn, ctx.req.image_url)
            except (_FetchBlockedError, _EgressDeniedError):
                logger.warning("[V104.45 #CP][A07] /ask image_url blocked by fetch policy (SSRF fail-closed)")
                raise HTTPException(status_code=400, detail="Invalid or disallowed image_url") from None
            except ValueError as e:
                logger.warning(f"[V104.45 #CP] Image fetch failed: {e}")
                _img_bytes = None
                _detector_degraded = True
                _detector_notes.append(f"image_fetch_failed:{type(e).__name__}")
            except Exception as e:
                logger.warning(f"[V104.45 #CP] Image fetch error: {e}", exc_info=True)
                _img_bytes = None
                _detector_degraded = True
                _detector_notes.append(f"image_fetch_failed:{type(e).__name__}")

        if _img_bytes:
            try:
                _img_result = await asyncio.wait_for(
                    asyncio.to_thread(img_detector.detect, image_bytes=_img_bytes),
                    timeout=_DETECT_TIMEOUT_SECONDS,
                )
                if _img_result and _img_result.jailbreak_detected:
                    logger.warning("[V104.45 #CP] Image jailbreak detected on /ask")
                    _multimodal_block = True
                elif _img_result and _img_result.method == "ocr_unavailable":
                    logger.warning("[V104.45 #CP] Image OCR unavailable — jailbreak scan degraded, request continues")
                    _detector_degraded = True
                    _detector_notes.append("image_ocr_unavailable")
            except asyncio.TimeoutError:
                logger.warning(f"[V104.45 #CP] Image detect exceeded {_DETECT_TIMEOUT_SECONDS:.0f}s bound — scan not completed")
                _detector_degraded = True
                _detector_notes.append("image_detect_timeout")
            except Exception as e:
                logger.warning(f"[V104.45 #CP] Image detect error: {e}", exc_info=True)
                _detector_degraded = True
                _detector_notes.append(f"image_detect_error:{type(e).__name__}")

        ctx.img_bytes = _img_bytes
        _voice_transcription = ""

        if ctx.req.voice_url:
            try:
                _voice_bytes = await asyncio.to_thread(safe_fetch_fn, ctx.req.voice_url)
            except (_FetchBlockedError, _EgressDeniedError):
                logger.warning("[V104.45 #CP][A07] /ask voice_url blocked by fetch policy (SSRF fail-closed)")
                raise HTTPException(status_code=400, detail="Invalid or disallowed voice_url") from None
            except ValueError as e:
                logger.warning(f"[V104.45 #CP] Voice fetch failed: {e}")
                _voice_bytes = None
                _detector_degraded = True
                _detector_notes.append(f"voice_fetch_failed:{type(e).__name__}")
            except Exception as e:
                logger.warning(f"[V104.45 #CP] Voice fetch error: {e}", exc_info=True)
                _voice_bytes = None
                _detector_degraded = True
                _detector_notes.append(f"voice_fetch_failed:{type(e).__name__}")

            if _voice_bytes:
                try:
                    _voice_result = await asyncio.wait_for(
                        asyncio.to_thread(voice_detector.detect, audio_bytes=_voice_bytes),
                        timeout=_DETECT_TIMEOUT_SECONDS,
                    )
                    if _voice_result and _voice_result.jailbreak_detected:
                        logger.warning("[V104.45 #CP] Voice jailbreak detected on /ask")
                        _multimodal_block = True
                    elif _voice_result and _voice_result.method == "whisper_unavailable":
                        logger.warning("[V104.45 #CP] Voice whisper unavailable — jailbreak scan degraded, request continues")
                        _detector_degraded = True
                        _detector_notes.append("voice_whisper_unavailable")
                    else:
                        from scp.capabilities.voice import VoiceHandler

                        _vh = VoiceHandler()
                        _transcribe_timeout = 30.0
                        try:
                            _env_timeout = float(os.environ.get("SCP_TRANSCRIBE_TIMEOUT_SEC", ""))
                            if _env_timeout > 0 and _env_timeout != float("inf"):
                                _transcribe_timeout = _env_timeout
                        except (TypeError, ValueError):
                            logger.debug("SecurityStage: TypeError, ValueError ignored", exc_info=True)
                        try:
                            _voice_transcription = await asyncio.wait_for(
                                asyncio.to_thread(_vh.transcribe, _voice_bytes),
                                timeout=_transcribe_timeout,
                            )
                        except asyncio.TimeoutError:
                            logger.warning(
                                f"[V104.45 #CP] Voice transcribe exceeded {_transcribe_timeout:.0f}s bound — transcription not completed"
                            )
                            _detector_degraded = True
                            _detector_notes.append("transcribe_timeout")
                        if _voice_transcription:
                            ctx.req.question += f"\n[Voice Transcription]: {_voice_transcription}"
                except asyncio.TimeoutError:
                    logger.warning(f"[V104.45 #CP] Voice detect exceeded {_DETECT_TIMEOUT_SECONDS:.0f}s bound — scan not completed")
                    _detector_degraded = True
                    _detector_notes.append("voice_detect_timeout")
                except Exception as e:
                    logger.warning(f"[V104.45 #CP] Voice detect error: {e}", exc_info=True)
                    _detector_degraded = True
                    _detector_notes.append(f"voice_detect_error:{type(e).__name__}")

        ctx.voice_transcription = _voice_transcription
        ctx.detector_degraded = _detector_degraded
        ctx.detector_notes = _detector_notes
        ctx.multimodal_block = _multimodal_block

        if _multimodal_block:
            ctx.early_response = AskResponse(
                verdict="FAIL",
                final_answer="[SCP: Answer withheld — multimodal jailbreak detected]",
                confidence=0.0,
                domain="security",
                elapsed_ms=0,
                session_id=ctx.v98_context.get("session_id", ""),
            )
            return
