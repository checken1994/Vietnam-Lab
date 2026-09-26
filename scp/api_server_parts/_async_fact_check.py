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

logger = logging.getLogger("scp.api")
_fact_checker = StreamingFactChecker()
_fact_check_retract_queue: deque[dict] = deque(maxlen=1000)

async def _async_fact_check(answer: str, question: str, session_id: str=''):
    """Run fact-check in background │Ă¢â€\x9aÂ¬Ă¢â‚¬Â\x9d update stats + queue retract if FALSE."""
    try:
        results = await _fact_checker.check_text(answer[:500], question)
        if results:
            false_claims = [r for r in results if r.verdict == 'FALSE']
            if false_claims:
                logger.warning(f"[V104.45 #Z] FactCheck: {len(false_claims)} FALSE claims found in answer to '{question[:50]}' │Ă¢â€\x9aÂ¬Ă¢â‚¬Â\x9d queuing retract")
                _fact_check_retract_queue.append({'question': question[:200], 'answer': answer[:200], 'false_claims': len(false_claims), 'session_id': session_id, 'timestamp': time.time()})
    except Exception as e:
        logger.debug(f'V104 async fact-check error: {e}', exc_info=True)
