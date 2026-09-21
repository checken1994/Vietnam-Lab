# SCP CIRCUIT: M02 — STATUS: CLOSED_WITH_KNOWN_GAP (closure: reports/circuit-closures/M02-closure.json)
# Auto-extracted from api_server.py
from __future__ import annotations
from scp.security.env_loader import load_selected_env
from fastapi import Depends
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST, Counter, Histogram
from fastapi.responses import Response
from scp.security.jwt_guard import get_current_user
from scp.observability.telemetry import setup_telemetry
import asyncio
import base64
import binascii
import logging
import os
import threading
from typing import Any
import time
from collections import deque
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
from scp.web_control.internet_search import InternetSearch
from scp.api_server_parts.helpers import AskRequest, AskResponse, _extract_v98_context, _safe_fetch_url, get_judge
from scp.core.request_run_ledger import RequestRunLedger, stage_request, traced_request
from typing import TYPE_CHECKING
from scp import __version__ as _SCP_VERSION
from scp.core.release_identity import DOMAIN_EXPERT_ENSEMBLE_TERM, RELEASE_LABEL, public_release_metadata
from scp.core.streaming_factcheck import StreamingFactChecker
from scp.meta.simple_explainer import SimpleExplainer
from scp.runtime.judge import RealityJudge
from scp.security.attack_crawler import AttackCrawler
from scp.security.cross_language_learner import CrossLanguageLearner
from scp.security.image_voice_detector import ImageJailbreakDetector, VoiceJailbreakDetector
from scp.security.multi_turn_tracker import MultiTurnTracker
from scp.core.real_learning_engine import RealLearningEngine
from scp.api.route_profile import resolve_api_profile, route_group_enabled
from pydantic import BaseModel

logger = logging.getLogger(__name__)



def _extend_ask_response_degradation_fields() -> None:
    """[AUDIT-20260909 MACH2-BUG2] Declare degradation-observability fields on
    the shared AskResponse model.

    TẠI SAO: khi image/voice fetch hoặc OCR/Whisper scan lỗi, pipeline cũ nuốt
    lỗi ở mức logger.debug rồi vẫn trả answer bình thường — multimodal jailbreak
    KHÔNG được scan nhưng client không có cách nào biết. helpers.py không thuộc
    phạm vi file được sửa của task này, nên các field bổ sung được khai báo động
    trên cùng một model class (mặc định None → JSON additive, không phá contract).
    """
    from pydantic.fields import FieldInfo

    _new_fields = {
        "detector_degraded": (bool | None, None),
        "detector_note": (str | None, None),
        "fact_check_degraded": (bool | None, None),
        # [AUDIT-20260909 MACH2-R2-2] fact_check_note từng là biến chết (gán
        # ở branch lỗi nhưng không bao giờ vào response). Note chứa loại
        # exception (`fact_check_error:<Type>`) — chẩn đoán hữu ích đi kèm
        # fact_check_degraded, cùng pattern với detector_note → expose thay vì xoá.
        "fact_check_note": (str | None, None),
    }
    _changed = False
    for _name, (_ann, _default) in _new_fields.items():
        if _name not in AskResponse.model_fields:
            AskResponse.model_fields[_name] = FieldInfo(default=_default, annotation=_ann)
            _changed = True
    if _changed:
        AskResponse.model_rebuild(force=True)


_extend_ask_response_degradation_fields()


async def _ask_impl(req: AskRequest, request: Request):
    """Main endpoint │Ă¢â€šÂ¬Ă¢â‚¬Â  question → V98 pipeline → verdict.

    Pipeline:
      1.  MemoryPoisoningGuard + AttackPatternMemory + ThreatDetector
      2.  Route → SLM predict → RealityJudge
      3.  FalsificationEngine + ErrorStore + Governance
      4.  AttackPolicy + CounterResponse + Canary + AttackPatternMemory.record_bypass
    """
    t0 = time.time()
    judge = get_judge()
    stage_request(request, 'judge_ready')
    v98_context = _extract_v98_context(request)
    _web_fallback_used = False
    _web_fallback: dict = {}
    v98_context['body'] = req.question
    if req.session_id:
        v98_context['session_id'] = req.session_id
    _history = []
    for _turn in (req.conversation_history or [])[-8:]:
        if not isinstance(_turn, dict):
            continue
        _role = str(_turn.get('role', 'user'))[:16]
        _content = str(_turn.get('content', ''))[:500].strip()
        if _content and _role in {'user', 'assistant'}:
            _history.append({'role': _role, 'content': _content})
    if _history:
        v98_context['conversation_history'] = _history
    try:
        from scp.api.cognitive_router import run_pre_judge_hooks
        v98_context = await run_pre_judge_hooks(req.question, v98_context)
    except Exception as e:
        logger.warning(f'Cognitive router failed: {e}')
    if hasattr(judge, 'dos_protection') and judge.dos_protection:
        try:
            client_ip = request.client.host if request.client else 'unknown'
            dos_alert = judge.dos_protection.check_request(client_ip)
            action = getattr(dos_alert, 'action_taken', '') if dos_alert else ''
            should_block = action in ('block', 'throttle') or (isinstance(dos_alert, dict) and dos_alert.get('should_block'))
            if should_block:
                status_code = int(getattr(dos_alert, 'status_code', 0) or 429)
                headers = dict(getattr(dos_alert, 'recommended_headers', {}) or {})
                return JSONResponse({'error': {'message': 'Rate limit exceeded', 'type': 'rate_limit_error'}}, status_code=status_code, headers=headers)
        except Exception as e:
            logger.debug(f'[V104.17] DoS check error: {e}')
    _multimodal_block = False
    _img_bytes = None
    # [AUDIT-20260909 MACH2-BUG2a] Degradation observability: khi fetch/scan lỗi
    # hoặc OCR/Whisper unavailable, request vẫn chạy nhưng response phải mang
    # dấu hiệu quan sát được (detector_degraded + detector_note).
    _detector_degraded = False
    _detector_notes: list[str] = []
    _DETECT_TIMEOUT_SECONDS = 10.0

    def _is_local_media_url(u: str) -> bool:
        """Loopback/localhost URL — egress policy CHO PHÉP host local, nên
        ValueError từ _safe_fetch_url với URL local là LỖI FETCH (mục tiêu
        không tồn tại/không đọc được), không phải từ chối chính sách (400)."""
        import urllib.parse as _up

        try:
            _host = (_up.urlsplit(str(u or "")).hostname or "").lower()
        except Exception:
            logger.warning('_ask_impl._is_local_media_url: Exception not handled', exc_info=True)
            return False
        return _host in {"localhost", "127.0.0.1", "::1", "[::1]"}

    if req.image_data:
        try:
            _raw_image = req.image_data
            if ',' in _raw_image and _raw_image.lower().startswith('data:'):
                _raw_image = _raw_image.split(',', 1)[1]
            _img_bytes = base64.b64decode(_raw_image, validate=True)
            if not _img_bytes or len(_img_bytes) > 6000000:
                raise ValueError('image_too_large_or_empty')
        except (binascii.Error, ValueError):
            raise HTTPException(status_code=400, detail='Invalid or oversized image_data') from None
    if req.image_url and _img_bytes is None:
        _local_image_url = _is_local_media_url(req.image_url)
        try:
            _img_bytes = await asyncio.to_thread(_safe_fetch_url, req.image_url)
        except ValueError as e:
            if _local_image_url:
                # [AUDIT-20260909 MACH2-BUG2a] URL local được policy cho phép;
                # ValueError ở đây = fetch thật sự lỗi (URL không tồn tại) →
                # KHÔNG 400 mà đánh dấu degraded và tiếp tục pipeline.
                logger.warning(f'[V104.45 #CP] Local image fetch failed: {e}')
                _img_bytes = None
                _detector_degraded = True
                _detector_notes.append('image_fetch_failed:local_unreachable')
            else:
                logger.warning('[V104.45 #CP] /ask image_url rejected by _safe_fetch_url policy')
                raise HTTPException(status_code=400, detail='Invalid or disallowed image_url') from None
        except Exception as e:
            logger.warning(f'[V104.45 #CP] Image fetch error: {e}')
            _img_bytes = None
            _detector_degraded = True
            _detector_notes.append(f'image_fetch_failed:{type(e).__name__}')
        if _img_bytes:
            try:
                _img_result = await asyncio.wait_for(asyncio.to_thread(_image_detector.detect, image_bytes=_img_bytes), timeout=_DETECT_TIMEOUT_SECONDS)
                if _img_result and _img_result.jailbreak_detected:
                    logger.warning('[V104.45 #CP] Image jailbreak detected on /ask')
                    _multimodal_block = True
                elif _img_result and _img_result.method == 'ocr_unavailable':
                    logger.warning('[V104.45 #CP] Image OCR unavailable — jailbreak scan degraded, request continues')
                    _detector_degraded = True
                    _detector_notes.append('image_ocr_unavailable')
            except asyncio.TimeoutError:
                logger.warning(f'[V104.45 #CP] Image detect exceeded {_DETECT_TIMEOUT_SECONDS:.0f}s bound — scan not completed')
                _detector_degraded = True
                _detector_notes.append('image_detect_timeout')
            except Exception as e:
                logger.warning(f'[V104.45 #CP] Image detect error: {e}')
                _detector_degraded = True
                _detector_notes.append(f'image_detect_error:{type(e).__name__}')
    _voice_transcription = ""
    if req.voice_url:
        _local_voice_url = _is_local_media_url(req.voice_url)
        try:
            _voice_bytes = await asyncio.to_thread(_safe_fetch_url, req.voice_url)
        except ValueError as e:
            if _local_voice_url:
                logger.warning(f'[V104.45 #CP] Local voice fetch failed: {e}')
                _voice_bytes = None
                _detector_degraded = True
                _detector_notes.append('voice_fetch_failed:local_unreachable')
            else:
                logger.warning('[V104.45 #CP] /ask voice_url rejected by _safe_fetch_url policy')
                raise HTTPException(status_code=400, detail='Invalid or disallowed voice_url') from None
        except Exception as e:
            logger.warning(f'[V104.45 #CP] Voice fetch error: {e}')
            _voice_bytes = None
            _detector_degraded = True
            _detector_notes.append(f'voice_fetch_failed:{type(e).__name__}')
        if _voice_bytes:
            try:
                _voice_result = await asyncio.wait_for(asyncio.to_thread(_voice_detector.detect, audio_bytes=_voice_bytes), timeout=_DETECT_TIMEOUT_SECONDS)
                if _voice_result and _voice_result.jailbreak_detected:
                    logger.warning('[V104.45 #CP] Voice jailbreak detected on /ask')
                    _multimodal_block = True
                elif _voice_result and _voice_result.method == 'whisper_unavailable':
                    logger.warning('[V104.45 #CP] Voice whisper unavailable — jailbreak scan degraded, request continues')
                    _detector_degraded = True
                    _detector_notes.append('voice_whisper_unavailable')
                else:
                    from scp.capabilities.voice import VoiceHandler
                    _vh = VoiceHandler()
                    # [AUDIT-20260909 MACH2-R2-1] Whisper transcribe chạy local
                    # qua to_thread nhưng KHÔNG có bound → có thể treo request
                    # vô hạn. Bọc wait_for: default 30s, env override
                    # SCP_TRANSCRIBE_TIMEOUT_SEC (parse an toàn — env rác/≤0/inf
                    # → fallback 30s). Inline parse vì hàm này bị rebind vào
                    # globals của api_server.py (helper module-level sẽ không
                    # nhìn thấy qua rebind).
                    _transcribe_timeout = 30.0
                    try:
                        _env_timeout = float(os.environ.get('SCP_TRANSCRIBE_TIMEOUT_SEC', ''))
                        if _env_timeout > 0 and _env_timeout != float('inf'):
                            _transcribe_timeout = _env_timeout
                    except (TypeError, ValueError):
                        logger.debug('_ask_impl: TypeError, ValueError ignored', exc_info=True)
                    try:
                        _voice_transcription = await asyncio.wait_for(asyncio.to_thread(_vh.transcribe, _voice_bytes), timeout=_transcribe_timeout)
                    except asyncio.TimeoutError:
                        logger.warning(f'[V104.45 #CP] Voice transcribe exceeded {_transcribe_timeout:.0f}s bound — transcription not completed')
                        _detector_degraded = True
                        _detector_notes.append('transcribe_timeout')
                    if _voice_transcription:
                        req.question += f"\n[Voice Transcription]: {_voice_transcription}"
            except asyncio.TimeoutError:
                logger.warning(f'[V104.45 #CP] Voice detect exceeded {_DETECT_TIMEOUT_SECONDS:.0f}s bound — scan not completed')
                _detector_degraded = True
                _detector_notes.append('voice_detect_timeout')
            except Exception as e:
                logger.warning(f'[V104.45 #CP] Voice detect error: {e}')
                _detector_degraded = True
                _detector_notes.append(f'voice_detect_error:{type(e).__name__}')
    if _multimodal_block:
        return AskResponse(verdict='FAIL', final_answer='[SCP: Answer withheld │Ă¢â€\x9aÂ¬Ă¢â‚¬Â\x9d multimodal jailbreak detected]', confidence=0.0, domain='security', elapsed_ms=0, session_id=v98_context['session_id'])
    _ai_answer = req.ai_answer
    if not _ai_answer or not _ai_answer.strip():
        try:
            from scp.llm_gateway import get_gateway
            _gateway = get_gateway()
            _generated_answer, _provider = await _gateway.chat(req.question, context='Lịch sử gần đây (chỉ để tham khảo):\n' + '\n'.join((f"{t['role']}: {t['content']}" for t in _history)) if _history else '', system_prompt='Bạn là SCP — một trợ lý AI thông minh. Trả lời ngắn gọn, chính xác, bằng tiếng Việt. Chỉ trả lời câu hỏi HIỆN TẠI ở cuối yêu cầu. Không tiếp tục chủ đề cũ nếu câu hỏi mới đổi chủ đề. Nếu thiếu dữ liệu, nói rõ chưa đủ dữ liệu thay vì đoán.', task='chat')
            if _generated_answer:
                _ai_answer = _generated_answer
                logger.info(f'[CHATBOT] LLM ({_provider}) generated answer: {_generated_answer[:80]}...')
        except Exception as _generation_error:
            logger.warning(f'[CHATBOT] LLM call failed: {_generation_error}')
            if os.environ.get('SCP_WEB_FALLBACK', '1') == '1':
                try:
                    _web_timeout = min(float(os.environ.get('SCP_WEB_FALLBACK_TIMEOUT', '8')), 12.0)
                    _web_search = InternetSearch(timeout=min(_web_timeout / 2.0, 4.0))
                    _web_fallback = await asyncio.wait_for(_web_search.search(req.question, max_results=6), timeout=_web_timeout)
                    _web_fallback_used = bool(_web_fallback.get('success'))
                    _web_fallback['trigger'] = 'llm_timeout_or_error'
                    _web_fallback['llm_error'] = str(_generation_error)[:240]
                    if _web_fallback_used:
                        _snippets = []
                        for _item in _web_fallback.get('results', [])[:6]:
                            _title = str(_item.get('title', '')).strip()
                            _snippet = str(_item.get('snippet', '')).strip()
                            _url = str(_item.get('url', '')).strip()
                            _snippets.append(f'- {_title}: {_snippet} ({_url})')
                        _ai_answer = '[SCP public-web evidence; untrusted, requires verification]\n' + '\n'.join(_snippets)
                        v98_context['web_fallback'] = _web_fallback
                    else:
                        logger.warning('[CHATBOT] Public web fallback returned no result: %s', _web_fallback.get('errors'))
                except Exception as _web_err:
                    _web_fallback = {'success': False, 'method': 'public-search', 'error': str(_web_err)[:240]}
                    logger.warning('[CHATBOT] Public web fallback failed: %s', _web_err)
    _q_lower = req.question.lower() if req.question else ''
    _FACT_CHECK_KEYWORDS = ('true or false', 'fact check', 'is it true', 'fact-check', 'có thật', 'đúng không', 'có thật không', 'kiểm chứng', 'real or fake', 'verify this claim')
    # [AUDIT-20260909 MACH2-BUG2b] chỉ bật khi claim tồn tại (keyword match) và
    # việc verify LỖI — không phải khi câu hỏi không có claim cần kiểm chứng.
    _fact_check_degraded = False
    _fact_check_note = ''
    if any((kw in _q_lower for kw in _FACT_CHECK_KEYWORDS)):
        try:
            from scp.core.multi_source_verifier import AsyncMultiSourceVerifier
            from scp.data_sources import get_registry
            _fc_sources = []
            try:
                _registry = get_registry()
                _candidates = []
                try:
                    _candidates = _registry.get_sources_for_intent('fact_check') or []
                except Exception:
                    logger.warning('_ask_impl: Exception not handled', exc_info=True)
                    _candidates = list(getattr(_registry, '_sources', {}).values())
                for _src in _candidates:
                    try:
                        if _src.can_handle('fact_check'):
                            _fc_sources.append(_src)
                    except Exception:
                        logger.warning('_ask_impl: Exception not handled', exc_info=True)
                        continue
            except Exception as _reg_err:
                logger.debug(f'[OPT-22] registry lookup failed: {_reg_err}')
            async_verifier = AsyncMultiSourceVerifier()
            fact_result = await async_verifier.verify_async(req.question, sources=_fc_sources or None)
            _extra_verified = 0
            _extra_contradicted = 0
            for _raw in fact_result.get('results', []) or []:
                try:
                    _meta = _raw.get('metadata', {}) if isinstance(_raw, dict) else {}
                    _verdict = str(_meta.get('consensus', '')).upper() if _meta else ''
                    if _verdict == 'FALSE':
                        _extra_contradicted += 1
                    elif _verdict == 'TRUE':
                        _extra_verified += 1
                except Exception:
                    logger.warning('_ask_impl: Exception not handled', exc_info=True)
                    continue
            if _extra_verified or _extra_contradicted:
                fact_result['verified'] = fact_result.get('verified', 0) + _extra_verified
                fact_result['contradicted'] = fact_result.get('contradicted', 0) + _extra_contradicted
                if fact_result['contradicted'] > fact_result['verified']:
                    fact_result['consensus'] = 'contradicted'
                elif fact_result['verified'] > fact_result['contradicted']:
                    fact_result['consensus'] = 'verified'
            if fact_result.get('consensus') == 'contradicted':
                logger.info(f"[OPT-22] Pre-judge fact check: claim contradicted by {fact_result.get('contradicted', 0)} sources (sources_checked={fact_result.get('sources_checked', 0)})")
                v98_context['fact_check_hint'] = fact_result
            elif fact_result.get('consensus') == 'verified':
                logger.info(f"[OPT-22] Pre-judge fact check: claim verified by {fact_result.get('verified', 0)} sources (sources_checked={fact_result.get('sources_checked', 0)})")
                v98_context['fact_check_hint'] = fact_result
        except Exception as e:
            # [AUDIT-20260909 MACH2-BUG2b] Pre-judge fact-check FAILED (claim đã
            # có — keyword match) nên hint bị mất: phải quan sát được, không nuốt.
            _fact_check_degraded = True
            _fact_check_note = f'fact_check_error:{type(e).__name__}'
            logger.warning(f'[OPT-22] async fact check failed: {e}')
    stage_request(request, 'verifier_started')
    from scp.core.top_systems_learning import inspect_untrusted as _sf_inspect
    _raw_evidence = [str(c) for c in req.contexts or [] if str(c).strip()] + ([str(req.retrieved_context).strip()] if str(getattr(req, 'retrieved_context', '') or '').strip() else [])
    _clean_evidence = []
    _injection_blocked = 0
    for _ev in _raw_evidence:
        _quarantined, _reason = _sf_inspect(_ev)
        if _quarantined:
            _injection_blocked += 1
            logger.warning('[SEMANTIC-FIREWALL] Blocked injection in evidence: %s', _reason)
            continue
        _clean_evidence.append(_ev)
    if _injection_blocked:
        v98_context['semantic_firewall'] = {'blocked': _injection_blocked, 'total': len(_raw_evidence)}
    _evidence_context = ' '.join(_clean_evidence)
    if hasattr(judge, 'judge_with_react_fallback'):
        v = await judge.judge_with_react_fallback(question=req.question, ai_answer=_ai_answer, cycle_count=0, source=req.source, context=_evidence_context, v98_context=v98_context)
    else:
        v = await asyncio.to_thread(judge.judge, question=req.question, ai_answer=_ai_answer, cycle_count=0, source=req.source, context=_evidence_context, v98_context=v98_context)

    class DotDict(dict):

        def __getattr__(self, name):
            return self.get(name, None)

        def __setattr__(self, name, value):
            self[name] = value
    if isinstance(v, dict):
        if v.get('evidence') is None:
            v['evidence'] = {}
        if v.get('slm_responses') is None:
            v['slm_responses'] = []
        if v.get('confidence') is None:
            v['confidence'] = 0.0
        if v.get('final_answer') is None:
            v['final_answer'] = v.get('evidence', {}).get('final_answer', '')
        v = DotDict(v)
    stage_request(request, 'verifier_completed', verdict=getattr(v, 'verdict', 'FAIL'), governance_decision=getattr(v, 'evidence', {}).get('governance_decision', ''))
    if hasattr(judge, 'dos_protection') and judge.dos_protection:
        try:
            judge.dos_protection.record_verdict(getattr(v, 'verdict', 'FAIL'))
        except Exception as e:
            logger.debug(f'[V104.41 #AC] DoS record_verdict error: {e}')
    elapsed_ms = (time.time() - t0) * 1000
    if hasattr(judge, 'response_monitor') and judge.response_monitor:
        try:
            judge.response_monitor.observe(prompt=req.question, response=v.final_answer or '', latency_ms=elapsed_ms)
        except Exception as e:
            logger.debug(f'[V104.41 #AD] ResponseMonitor observe error: {e}')
    slm_trace = []
    for r in v.slm_responses:
        slm_trace.append({'domain': r.get('domain', '?'), 'slm_name': r.get('slm_name', r.get('domain', '?')), 'answer': str(r.get('answer', ''))[:200], 'confidence': r.get('confidence', 0), 'source': r.get('evidence', {}).get('source', '?') if isinstance(r.get('evidence'), dict) else '?', 'evidence': r.get('evidence', {}) if isinstance(r.get('evidence'), dict) else {}, 'processing_time_ms': r.get('processing_time', 0)})
    phase_timings = v.evidence.get('v100_phase_timings', {})
    mt_result = None
    _mt_session = req.session_id or v98_context.get('session_id', '')
    if _mt_session:
        mt_result = _multi_turn_tracker.track(_mt_session, req.question, v.verdict)
        if mt_result.suspicious:
            if v.verdict in ('PASS', 'FAIL', 'UNKNOWN', 'PARTIAL'):
                v.verdict = 'FLAGGED'
                v.confidence *= 0.5
                if v.reasoning:
                    v.reasoning += f' [V104 Multi-turn: {mt_result.pattern_type}]'
            logger.warning(f'V104 Multi-turn attack: session={_mt_session}, pattern={mt_result.pattern_type}, reason={mt_result.reason}')
            v98_context['v104_multi_turn'] = mt_result.__dict__
    has_attack = bool(v98_context.get('v99_vietnamese_attack'))
    has_bypass = bool(v.evidence.get('v100_bypass_detected'))
    has_human_review = bool(v.evidence.get('v102_human_review_pending'))
    source_count = len([r for r in v.slm_responses if r.get('answer')])
    _simple_explainer.explain(verdict=v.verdict, confidence=v.confidence, domain=v.domain or '' or '' or '', sources=source_count, has_attack=has_attack, has_bypass=has_bypass, has_human_review=has_human_review, lineage_overlap=0.0, reliability_factor=v.evidence.get('v102_reliability_factor', 1.0))
    _api_final_answer = v.final_answer
    _gov_decision = v.evidence.get('governance_decision', '')
    _api_slm_responses = [{k: str(v2)[:200] for k, v2 in r.items()} for r in v.slm_responses]
    _api_slm_trace = slm_trace
    _api_reasoning = v.reasoning[:500] if v.reasoning else None
    _api_v100_claims = v.evidence.get('v100_claims')
    _api_v103_antibodies = v.evidence.get('v103_antibodies')
    _api_speculative_mode = v.evidence.get('speculative_mode')
    _api_v98_canary_token = v.evidence.get('v98_canary_token')
    _api_v98_guard = v.evidence.get('v98_guard_verdict') or v.evidence.get('v98_guard')
    _api_v98_classification = v.evidence.get('v98_classification')
    _api_v98_attack_policy = v.evidence.get('v98_attack_policy')
    _api_v98_counter_executed = v.evidence.get('v98_counter_executed')
    _api_v98_bypass_recorded = v.evidence.get('v98_bypass_recorded')
    _api_falsification_status = v.evidence.get('falsification_status')
    if _gov_decision == 'KILL' or v.verdict in ('FAIL', 'FLAGGED'):
        _api_final_answer = f'[SCP: Answer withheld — verdict: {v.verdict}]'
        if _gov_decision == 'KILL':
            _api_final_answer = '[SCP: Answer withheld — Governance KILL]'
        _api_slm_responses = []
        _api_slm_trace = []
        _api_reasoning = '[SCP: Answer withheld]'
        _api_v100_claims = None
        _api_v103_antibodies = None
        _api_speculative_mode = None
        _api_v98_canary_token = None
        _api_v98_guard = None
        _api_v98_classification = None
        _api_v98_attack_policy = None
        _api_v98_counter_executed = None
        _api_v98_bypass_recorded = None
        _api_falsification_status = None
        logger.info(f'[V104.41 #X] API boundary enforcing abstain (all fields cleared): verdict={v.verdict}, gov={_gov_decision}')
    elif v.verdict == 'UNKNOWN' and v.evidence.get('why_gate', {}).get('decision') == 'REJECT':
        _api_final_answer = '[SCP: Answer withheld — WHY Gate blocked]'
        _api_slm_responses = []
        _api_slm_trace = []
        _api_reasoning = '[SCP: WHY Gate blocked]'
        _api_v100_claims = None
        _api_v103_antibodies = None
        _api_speculative_mode = None
        _api_v98_canary_token = None
        _api_v98_guard = None
        _api_v98_classification = None
        _api_v98_attack_policy = None
        _api_v98_counter_executed = None
        _api_v98_bypass_recorded = None
        _api_falsification_status = None
        logger.info(f'[FIX-1] API boundary enforcing WHY Gate block (all fields cleared): verdict={v.verdict}')
    elif v.verdict == 'UNKNOWN':
        if _api_final_answer and '[SCP: unverified]' not in _api_final_answer:
            _sources = []
            for r in v.slm_responses:
                if r.get('answer'):
                    _sources.append(f"  • {r.get('slm_name', '?')}: {str(r.get('answer', ''))[:60]}")
            _source_text = '\n'.join(_sources) if _sources else '  (không có SLM nào trả lời)'
            _api_final_answer = str(_api_final_answer) + str(f'\n\nSCP đã kiểm tra:\n{_source_text}\nĐộ tin cậy: {v.confidence:.0%} — chưa đạt ngưỡng (cần ≥70%)')
    if v.verdict == 'PASS' and _api_final_answer and (len(_api_final_answer) > 20):
        try:
            _fc_task = asyncio.create_task(_async_fact_check(_api_final_answer, req.question, v98_context.get('session_id', '')))
            _async_factcheck_tasks.add(_fc_task)
            _fc_task.add_done_callback(_async_factcheck_tasks.discard)
        except Exception as e:
            logger.debug(f'[V104.37] api_server.py: e={e}')

    # --- RESTORED SUBSYSTEMS HOOKS (Wave 1 & 2) ---
    # [AUDIT-20260909 MACH2-BUG2c] TẠI SAO tách: trước đây cả 5 hook dùng chung
    # MỘT try/except với logger.debug — 1 ledger fail làm MẤT toàn bộ các hook
    # còn lại một cách im lặng. Mỗi hook giờ fail độc lập và log WARNING.
    import uuid as _hook_uuid
    from pathlib import Path as _HookPath
    _data_dir = _HookPath(os.environ.get("SCP_DATA_DIR", "data"))

    # 1. Risk Intelligence
    try:
        from scp.risk_intelligence import RiskClassifier, RiskSignal
        _classifier = RiskClassifier()
        _signals = [RiskSignal(source_id="judge", kind="independent", observed_directly=True)]
        _risk = _classifier.classify(_signals, desired_level="PR2", hazard_severity="low")
        if _risk and _risk.level:
            v.evidence['risk_level'] = _risk.level.value
    except Exception as _hook_exc:
        logger.warning(f'[RESTORED-SYSTEMS] risk hook failed: {_hook_exc}')

    # 2. History Evidence Ledger
    try:
        from scp.history.evidence_ledger import EvidenceRecord, append_record
        _record = EvidenceRecord(
            subject_id=req.session_id or "session_unknown",
            lineage="ask_endpoint",
            kind="verdict_rendered",
            locator="ask_impl",
            observed_claim=req.question[:200],
            independent_of="",
            status=v.verdict
        )
        append_record(_data_dir / "history_evidence.jsonl", _record)
    except Exception as _hook_exc:
        logger.warning(f'[RESTORED-SYSTEMS] history hook failed: {_hook_exc}')

    # 3. World State (if PASS, record an event)
    # [C-S2 AUDIT-20260913] Root cause (evidence: scp/world_state/
    # temporal_authority.py:91-92): hook passed evidence_refs=[] while
    # record_observation() defaults epistemic_status="OBSERVED", and an
    # OBSERVED assertion REQUIRES evidence_refs ("unaudited world writes are
    # forbidden"). Result: WorldStateError raised on EVERY judge PASS, caught
    # below as a warning only — world_state never received a /ask record.
    # Fix at the failure point: the auditable provenance of this /ask run is
    # its request-ledger run_id (attached to request.state.scp_run by the
    # traced_request wrapper, same object stage_request already reads), so the
    # event is recorded with evidence_refs=[run_id]. Store-level failures stay
    # fail-open for /ask (hook is auxiliary) but MUST be logged — never silent.
    try:
        if v.verdict == 'PASS':
            _ws_run = getattr(getattr(request, "state", None), "scp_run", None)
            _ask_run_id = str(getattr(_ws_run, "run_id", "") or "")
            if _ask_run_id:
                from scp.world_state import EntityEventAuthority, TemporalAuthority
                from scp.contracts.time import now_utc_iso
                _temporal = TemporalAuthority(db_path=str(_data_dir / "world_state.sqlite"))
                try:
                    _eea = EntityEventAuthority(_temporal)
                    _eea.record_event(
                        entity_id="ask_session",
                        event_kind="pass_verdict",
                        payload={"confidence": v.confidence, "question": req.question[:100]},
                        valid_time=now_utc_iso(),
                        evidence_refs=[_ask_run_id],
                        actor_id="scp-judge"
                    )
                finally:
                    _temporal.close()
            else:
                logger.warning('[RESTORED-SYSTEMS] world_state hook: judge PASS without request run_id - world write skipped (unaudited world writes are forbidden)')
    except Exception as _hook_exc:
        logger.warning(f'[RESTORED-SYSTEMS] world_state hook failed: {_hook_exc}')

    # 4. Calibration (record prediction for UNKNOWN/PARTIAL)
    try:
        if v.verdict in ("UNKNOWN", "PARTIAL", "FLAGGED"):
            from scp.calibration.ledger import CalibrationLedger
            _cal = CalibrationLedger(db_path=str(_data_dir / "calibration.sqlite"))
            try:
                _cal.record_prediction(
                    domain=v.domain or "general",
                    task_class="ask",
                    predictor_type="judge",
                    predictor_id="judge_v3",
                    prediction={"verdict": v.verdict, "confidence": v.confidence}
                )
            finally:
                _cal.close()
    except Exception as _hook_exc:
        logger.warning(f'[RESTORED-SYSTEMS] calibration hook failed: {_hook_exc}')

    # 5. Forecast (if future intent detected)
    try:
        if any(w in req.question.lower() for w in ["sẽ", "dự đoán", "tương lai", "will", "predict"]):
            from scp.forecast.ledger import ForecastLedger
            from scp.contracts.time import now_utc_iso as _now_utc_iso
            _fc = ForecastLedger(db_path=str(_data_dir / "forecast.sqlite"))
            try:
                _fc.record_case({
                    "id": str(_hook_uuid.uuid4())[:8],
                    "domain": v.domain or "general",
                    "claimant": "user",
                    "claim": req.question[:200],
                    "date": _now_utc_iso(),
                    "verdict": v.verdict,
                    "confidence": v.confidence,
                    "outcome_code": 9
                })
            finally:
                _fc.close()
    except Exception as _hook_exc:
        logger.warning(f'[RESTORED-SYSTEMS] forecast hook failed: {_hook_exc}')
    # ----------------------------------------------

    stage_request(request, 'response_boundary', verdict=v.verdict, governance_decision=_gov_decision)
    if _web_fallback_used:
        _api_slm_trace.append({'domain': v.domain or '' or '', 'slm_name': 'public_web_search', 'answer': 'retrieved public snippets', 'time_ms': None, 'source': 'public-search', 'evidence': _web_fallback})
    return AskResponse(verdict=v.verdict, final_answer=_api_final_answer, confidence=v.confidence, domain=v.domain or '' or '', falsification_status=_api_falsification_status, governance_decision=v.evidence.get('governance_decision'), v98_guard=_api_v98_guard, v98_classification=_api_v98_classification, v98_attack_policy=_api_v98_attack_policy, v98_counter_executed=_api_v98_counter_executed, v98_canary_token=_api_v98_canary_token, v98_bypass_recorded=_api_v98_bypass_recorded, elapsed_ms=round(elapsed_ms, 1), session_id=v98_context['session_id'], slm_trace=_api_slm_trace, phase_timings=phase_timings, reasoning=_api_reasoning, slm_responses=_api_slm_responses, v100_claims=_api_v100_claims, v103_antibodies=_api_v103_antibodies, speculative_mode=_api_speculative_mode, web_fallback_used=_web_fallback_used, web_fallback=_web_fallback or None, detector_degraded=_detector_degraded, detector_note=('; '.join(_detector_notes) if _detector_notes else None), fact_check_degraded=_fact_check_degraded or None, fact_check_note=(_fact_check_note or None))
