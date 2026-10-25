# SCP CIRCUIT: M1 Boot & Background — STATUS: CLOSED (closure: docs/evidence-summary/M01-closure.json)
"""SCP API server composition root.

High-coupling request execution and lifespan orchestration live in
``scp.api_server_parts``.  The extracted functions are rebound to this module's
namespace so existing process-global state, monkeypatch points, and route
contracts remain authoritative here.
"""
from __future__ import annotations

from scp.security.env_loader import load_selected_env

load_selected_env()

import asyncio
import base64  # noqa: F401  # [hygiene-keep] wire-parent namespace injection
import binascii  # noqa: F401  # [hygiene-keep] wire-parent namespace injection
import logging
import os
import threading
import time  # noqa: F401  # [hygiene-keep] wire-parent namespace injection
import types
from collections import deque
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from pydantic import BaseModel, Field

from scp import __version__ as _SCP_VERSION
from scp.api.rate_limiting import (
    RateLimitExceeded,
    _rate_limit_exceeded_handler,
    limiter,
    rate_limit_middleware,
)
from scp.core.settings import get  # [HC-03/E-W1] điểm đọc env chuẩn thay os.environ.get rải rác (secret vẫn qua secret_loader — HC-08)
from scp.api_server_parts import _ask_impl as _ask_impl_part
from scp.api_server_parts import _async_fact_check as _async_fact_check_part
from scp.api_server_parts import lifespan as _lifespan_part
from scp.api_server_parts.helpers import (
    AskRequest,
    AskResponse,
    _extract_v98_context,  # noqa: F401  # [hygiene-keep] wire-parent namespace injection
    _safe_fetch_url,  # noqa: F401  # [hygiene-keep] wire-parent namespace injection
    get_judge,
)
from scp.core.real_learning_engine import RealLearningEngine
from scp.core.release_identity import (
    DOMAIN_EXPERT_ENSEMBLE_TERM,
    RELEASE_LABEL,
    public_release_metadata,
)
from scp.core.request_run_ledger import (  # noqa: F401  # [hygiene-keep] wire-parent namespace injection
    RequestRunLedger,
    stage_request,
    traced_request,
)
from scp.core.runtime_paths import runtime_data_dir, runtime_path
from scp.core.streaming_factcheck import StreamingFactChecker
from scp.meta.simple_explainer import SimpleExplainer
from scp.observability.telemetry import setup_telemetry
from scp.pc_control.pc_controller import default_kill_switch_path  # noqa: F401  [HC-22] backward-compat re-export (tests/seam patch điểm này)
from scp.runtime.judge import RealityJudge
from scp.security.attack_crawler import AttackCrawler
from scp.security.auth import verify_admin
from scp.security.cross_language_learner import CrossLanguageLearner
# [H3-W1] Image/Voice/MultiTurn class imports dời xuống composition root —
# api_server không còn dựng trực tiếp (container là nguồn duy nhất).
from scp.security.jwt_guard import (  # noqa: F401  # [hygiene-keep] wire-parent namespace injection
    get_current_user,
    verify_jwt_token,
)
from scp.web_control.internet_search import (
    InternetSearch,  # noqa: F401  # [hygiene-keep] wire-parent namespace injection
)

logger = logging.getLogger("scp.api")
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s")

from prometheus_client import REGISTRY

if "scp_request_count" in REGISTRY._names_to_collectors:
    REQUEST_COUNT = REGISTRY._names_to_collectors["scp_request_count"]
else:
    REQUEST_COUNT = Counter("scp_request_count", "Total SCP Requests", ["method", "endpoint"])

if "scp_request_latency_seconds" in REGISTRY._names_to_collectors:
    REQUEST_LATENCY = REGISTRY._names_to_collectors["scp_request_latency_seconds"]
else:
    REQUEST_LATENCY = Histogram("scp_request_latency_seconds", "Request latency", ["endpoint"])

_CACHED_COMMIT: str | None = None


def _scp_service_identity() -> dict:
    """Expose bounded runtime identity for local service/port verification."""
    global _CACHED_COMMIT
    from scp.api_server_parts.server_helpers import compute_scp_service_identity

    identity, _CACHED_COMMIT = compute_scp_service_identity(_CACHED_COMMIT)
    return identity


_REQUEST_RUN_LEDGER = RequestRunLedger()
_ASK_KERNEL_ADAPTERS: dict[tuple[str, str], Any] = {}
_ASK_KERNEL_ADAPTER_LOCK = threading.Lock()
_ASK_KERNEL_INIT_ERROR: Exception | None = None


def _ask_is_context_rag(req: AskRequest) -> bool:
    return bool(
        getattr(req, "rag_enabled", False)
        or getattr(req, "contexts", None)
        or str(getattr(req, "retrieved_context", "") or "").strip()
    )


def _ask_kernel_enabled(req: AskRequest) -> bool:
    return get("SCP_ASK_KERNEL_ENABLED", "1") == "1"


def _question_routing_stats() -> dict:
    """[S24] Snapshot KPI question-router (LOOKUP→data-API fork)."""
    try:
        from scp.runtime.question_router import route_stats_snapshot

        return route_stats_snapshot()
    except Exception as exc:
        logger.warning("[S24] routing stats unavailable: %s", exc, exc_info=True)
        return {"error": type(exc).__name__}


def _get_ask_kernel_adapter() -> Any:
    global _ASK_KERNEL_INIT_ERROR
    db_path = str(runtime_path("SCP_KERNEL_DB_PATH", "ask_task_kernel.sqlite3"))
    trace_path = str(runtime_path("SCP_KERNEL_TRACE_PATH", "ask_task_kernel_trace.jsonl"))
    key = (db_path, trace_path)
    with _ASK_KERNEL_ADAPTER_LOCK:
        if key in _ASK_KERNEL_ADAPTERS:
            return _ASK_KERNEL_ADAPTERS[key]
        try:
            from scp.ask_kernel_adapter import AskKernelAdapter
            adapter = AskKernelAdapter(db_path, trace_path)
            _ASK_KERNEL_ADAPTERS[key] = adapter
            return adapter
        except Exception as exc:
            _ASK_KERNEL_INIT_ERROR = exc
            logger.error("[ASK-KERNEL] durable adapter initialization failed: %s", type(exc).__name__, exc_info=True)
            return None


def _kernel_gate_unavailable_response(req: AskRequest, exc: Exception) -> AskResponse:
    from scp.api_server_parts.server_helpers import build_kernel_gate_unavailable_response

    return build_kernel_gate_unavailable_response(req, exc)


def _pc_kill_switch_engaged() -> bool:
    """[HC-22 unified] Kill-switch admission: BẤT KỲ kênh nào engaged → True.

    Delegate sang ``scp.security.kill_switch.is_engaged()`` (file flag +
    env ``SCP_KILL_SWITCH``; kênh kernel global_kill bị bỏ qua tường minh ở
    gate này vì kernel=None — enforcement kernel đã nằm trong claim/lease qua
    ``_assert_not_killed``, không tự mở connection ở đây). Giữ tên hàm cho
    backward compat (chat.py, openai_compat.py, middleware, tests import
    điểm này). Fail-closed được kế thừa từ module hợp nhất: flag không đọc
    được → tính là engaged (F-RUN-01 audit-r2 semantics giữ nguyên).
    """
    from scp.security.kill_switch import is_engaged

    return is_engaged()


def _kill_switch_blocked_response(req: AskRequest) -> AskResponse:
    """Fail-closed /ask admission answer while the kill switch is engaged."""
    from scp.api_server_parts.server_helpers import build_kill_switch_blocked_response

    return build_kill_switch_blocked_response(req)


if TYPE_CHECKING:
    from scp.prediction.predictive import PredictiveOrchestrator as PredictiveEngine

try:
    from scp.api.chat import router as chat_router
    _CHAT_AVAILABLE = True
except ImportError as e:
    logger.warning("V104.48 Chat router unavailable: %s", e)
    _CHAT_AVAILABLE = False

_V98_V100_ROUTERS_AVAILABLE = False
v98_admin_router = None
v100_admin_router = None

try:
    from scp.core.fast_learning_engine import (  # noqa: F401  # [hygiene-keep] wire-parent namespace injection
        FastLearningEngine,
        start_fast_learning_thread,
    )
    _V1042_AVAILABLE = True
except ImportError as e:
    logger.warning("V104.2 FastLearningEngine unavailable: %s", e)
    _V1042_AVAILABLE = False

try:
    from scp.core.startup_optimizer import (
        STARTUP_DEFER_SECONDS,  # noqa: F401  # [hygiene-keep] wire-parent namespace injection
        cleanup_data_directory,  # noqa: F401  # [hygiene-keep] wire-parent namespace injection
        deferred_background_start,  # noqa: F401  # [hygiene-keep] wire-parent namespace injection
        get_data_directory_stats,  # noqa: F401  # [hygiene-keep] wire-parent namespace injection
        run_startup_optimization,  # noqa: F401  # [hygiene-keep] wire-parent namespace injection
    )
    _V1043_AVAILABLE = True
except ImportError as e:
    logger.warning("V104.3 StartupOptimizer unavailable: %s", e)
    _V1043_AVAILABLE = False

try:
    from scp.core.data_partitioner import (
        DOMAIN_KEYWORDS,  # noqa: F401  # [hygiene-keep] wire-parent namespace injection
        DOMAIN_TABLES,  # noqa: F401  # [hygiene-keep] wire-parent namespace injection
        TTL_QUESTION_LOG,  # noqa: F401  # [hygiene-keep] wire-parent namespace injection
        TTL_VERDICT_CACHE,  # noqa: F401  # [hygiene-keep] wire-parent namespace injection
        TTL_VERDICT_CACHE_DB,  # noqa: F401  # [hygiene-keep] wire-parent namespace injection
        BypassLessonsStore,  # noqa: F401  # [hygiene-keep] wire-parent namespace injection
        DataPartitioner,  # noqa: F401  # [hygiene-keep] wire-parent namespace injection
        ThreeTierCache,  # noqa: F401  # [hygiene-keep] wire-parent namespace injection
        TTLExpirer,  # noqa: F401  # [hygiene-keep] wire-parent namespace injection
        detect_domain,  # noqa: F401  # [hygiene-keep] wire-parent namespace injection
        migrate_old_to_new,  # noqa: F401  # [hygiene-keep] wire-parent namespace injection
    )
    _V1044_AVAILABLE = True
except ImportError as e:
    logger.warning("V104.4 DataPartitioner unavailable: %s", e)
    _V1044_AVAILABLE = False

_judge: RealityJudge | None = None
_judge_lock = threading.Lock()
_background_task: asyncio.Task | None = None
# [F-18 · 2026-10-23 · theo AUDITOR-2] _rebind_part_function rebind các hàm
# lifespan vào __globals__ của module này; shutdown đọc _scheduler_offloop_stop
# (F-14) — nếu không khai báo sẵn ở đây, mọi boot không có judge (env test,
# boot sạch) sẽ NameError khi tắt, crash teardown ~21 test flow_01/flow_02.
_scheduler_offloop_stop: threading.Event | None = None
_attack_crawler: AttackCrawler | None = None
# [H3-W1 · 2026-10-25] 3 singleton dùng chung chuyển về DI composition root
# (scp/api_server_parts/composition.py). Vẫn ASSIGN IMPORT-TIME — contract B4-W1
# (docs/architecture/SINGLETON_LIFECYCLE.md): rebind machinery F-18 đọc chúng
# qua LOAD_GLOBAL (bỏ qua module __getattr__) nên KHÔNG được lazify ở module
# level; thay đổi duy nhất là NGUỒN instance: container dựng 1 lần (lazy
# container-level __getattr__), _ask_impl.py lấy cùng instance → hết trùng 2 bộ
# tracker/detector trong cùng process.
from scp.api_server_parts.composition import get_container as _get_composition_container

_h3w1_container = _get_composition_container()
_multi_turn_tracker = _h3w1_container.multi_turn_tracker
_image_detector = _h3w1_container.image_detector
_voice_detector = _h3w1_container.voice_detector
_cross_language_learner = CrossLanguageLearner()
_fact_checker = StreamingFactChecker()
_simple_explainer = SimpleExplainer()

# [HC-09/10/11 · 2026-10-23] `_real_learning` / `_fast_learning` chuyển sang
# PEP 562 lazy (module __getattr__ bên dưới) thay vì dựng lúc import. Đây là
# HAI singleton duy nhất an toàn để lazify: (1) không hàm nào thuộc cơ chế
# rebind F-18 (_rebind_part_function) LOAD_GLOBAL chúng — đã rg toàn bộ
# api_server_parts/; (2) không module nào đọc chúng lúc import của chính nó —
# scp/api/_shared.py delegate qua getattr (chạm được __getattr__), chat.py
# import function-local lúc call time; (3) constructor chỉ lưu path, không mở
# DB. Các singleton còn lại (_multi_turn_tracker, _image_detector,
# _voice_detector, _cross_language_learner, _fact_checker, _simple_explainer,
# _REQUEST_RUN_LEDGER) PHẢI ở import-time: rebind functions và decorator
# @traced_request(_REQUEST_RUN_LEDGER) đọc chúng qua LOAD_GLOBAL (bỏ qua
# module __getattr__) — chi tiết docs/architecture/SINGLETON_LIFECYCLE.md.
_learning_singletons_lock = threading.Lock()


def __getattr__(name: str):
    """PEP 562 — dựng lười `_real_learning` / `_fast_learning` ở lần đọc đầu.

    Kết quả được cache ngược vào module globals: các lần đọc sau là dict hit
    thuần (không re-enter hook), monkeypatch.setattr ghi đè bình thường, và
    nếu một hàm nào sau này LOAD_GLOBAL tên này thì giá trị đã có sẵn.
    """
    if name == "_real_learning":
        with _learning_singletons_lock:
            inst = globals().get(name)
            if inst is None:
                inst = RealLearningEngine(
                    scp_db_path=str(runtime_path("SCP_DB_PATH", "v13.db")),
                    data_dir=str(runtime_data_dir()),
                )
                globals()[name] = inst
            return inst
    if name == "_fast_learning":
        with _learning_singletons_lock:
            if name in globals():
                return globals()[name]
            inst: FastLearningEngine | None = (
                FastLearningEngine(
                    scp_db_path=str(runtime_path("SCP_DB_PATH", "v13.db")),
                    data_dir=str(runtime_data_dir()),
                )
                if _V1042_AVAILABLE
                else None
            )
            globals()[name] = inst
            return inst
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


_predictive_engine: PredictiveEngine | None = None
_async_factcheck_tasks: set = set()
_fact_check_retract_queue: deque[dict] = deque(maxlen=1000)


def _rebind_part_function(fn):
    """Execute extracted API code against this module's authoritative composition root state."""
    rebound = types.FunctionType(fn.__code__, globals(), fn.__name__, fn.__defaults__, fn.__closure__)
    rebound.__kwdefaults__ = fn.__kwdefaults__
    rebound.__annotations__ = dict(getattr(fn, "__annotations__", {}))
    rebound.__doc__ = fn.__doc__
    rebound.__module__ = __name__
    return rebound


_async_fact_check = _rebind_part_function(_async_fact_check_part._async_fact_check)
_ask_impl = _rebind_part_function(_ask_impl_part._ask_impl)
# [HIST-LEDGER-CONTRACT 2026-09-26] Rebind-namespace export: the rebound
# `_ask_impl` executes against THIS module's globals (see _rebind_part_function),
# so every global it references must live here — including the history-evidence
# helper its restored-subsystems hook calls. Importing it as a plain name would
# be flagged F401 (this is a namespace export, not an unused import).
_history_evidence_record = _ask_impl_part._history_evidence_record
# [W1-c1 2026-10-02] Rebind-namespace export (same contract as
# `_history_evidence_record` above): the rebound `_ask_impl` now awaits the
# fact-check with this bound, so the constant must live in THIS namespace —
# otherwise every PASS-verdict /ask raises NameError on LOAD_GLOBAL.
_FACTCHECK_AWAIT_TIMEOUT_S = _ask_impl_part._FACTCHECK_AWAIT_TIMEOUT_S
# [W3-e4 2026-10-04] Rebind-namespace export (same contract as
# `_history_evidence_record` above): the rebound `_ask_impl` calls the
# identity post-guard `_strip_vendor_identity_claims` on every /ask that
# delivers an answer — the name must live in THIS namespace or every such
# /ask raises NameError on LOAD_GLOBAL (pinned by
# tests/T02_contract/test_api_server_rebind_globals.py).
_strip_vendor_identity_claims = _ask_impl_part._strip_vendor_identity_claims
# [W14 2026-10-07] Rebind-namespace export (same rebind-namespace contract):
# the rebound `_ask_impl` injects pre-gen web evidence under this header
# (gen-with-evidence cho time-signal question) — the constant must live in
# THIS namespace or every time-signal factual /ask raises NameError on
# LOAD_GLOBAL (pinned by test_api_server_rebind_globals generic guard).
_EVIDENCE_HEADER = _ask_impl_part._EVIDENCE_HEADER
# [F-RUN-02 audit-r2 2026-10-01] Namespace export (same rebind-namespace
# contract as `_history_evidence_record` above): the rebound `lifespan`
# executes against THIS module's globals, and its nested deep-audit scheduler
# body (`_deep_audit_loop`, defined inside lifespan.py) calls
# `deep_audit_boot_run_enabled()` at its first cycle. That name used to exist
# only in scp/api_server_parts/lifespan.py — the call raised NameError on
# EVERY boot (observed in docker logs), killing the deep-audit scheduler
# body before its 24h cadence loop could start. Export the flag reader here
# so the LOAD_GLOBAL resolves; the generic guard is pinned by
# tests/T02_contract/test_api_server_rebind_globals.py (walks every code
# object of all three rebound functions and asserts each LOAD_GLOBAL
# resolves — the whole missing-export class, not just this one name).
deep_audit_boot_run_enabled = _lifespan_part.deep_audit_boot_run_enabled
# [HC-04 follow-up / K3-W1 2026-10-24] Namespace export (same rebind-namespace
# contract): HC-04 added `with temporary_override(...)` + `async with
# _lifespan_core(app)` inside lifespan()'s boot-window wrapper, but the rebound
# lifespan executes against THIS module's globals — LOAD_GLOBAL raised
# NameError on every boot (temporary_override first, then _lifespan_core;
# 32 flow_03 failures + red rebind-guards observed at 5e1d5a17). Export the
# context manager, and rebind `_lifespan_core` through the SAME rebind path as
# pre-HC-04: its body performs `global _background_task, _scheduler_offloop_stop`
# writes that shutdown() reads from THIS module — binding it to lifespan.py's
# globals instead would leave those writes invisible to shutdown (F-14 class).
temporary_override = _lifespan_part.temporary_override
# [HC-03 / B3-W1 2026-10-23] Namespace export (same rebind-namespace contract):
# B3-W1 migrated lifespan's env reads to the standard read point
# ``settings.get``; the rebound lifespan/_lifespan_core bodies execute against
# THIS module's globals, so the name must be exported here or every boot
# raises NameError on LOAD_GLOBAL (observed at e79bafd5 + B3-W1 edits,
# container run of test_app_state_contract). Generic guard:
# tests/T02_contract/test_api_server_rebind_globals.py.
get = _lifespan_part.get
_lifespan_core = asynccontextmanager(_rebind_part_function(
    getattr(_lifespan_part._lifespan_core, "__wrapped__", _lifespan_part._lifespan_core)
))
lifespan_raw = _rebind_part_function(getattr(_lifespan_part.lifespan, "__wrapped__", _lifespan_part.lifespan))
lifespan = asynccontextmanager(lifespan_raw)

# [OPT-21] InboxDaemon — Background Knowledge Ingestion Loop exports
try:
    from scp.knowledge.inbox_daemon import (  # noqa: F401  # [hygiene-keep] wire-parent namespace injection
        InboxDaemon,
        get_inbox_daemon,
        start_inbox_daemon,
        stop_inbox_daemon,
    )
except ImportError as _ib_err:
    logger.warning("[OPT-21] InboxDaemon unavailable: %s", _ib_err)


class SimulationRequest(BaseModel):
    count: int = Field(50, ge=1, le=500)


try:
    from scp.api.routes.admin_v98 import router as v98_admin_router
    from scp.api.routes.admin_v100 import router as v100_admin_router
    _V98_V100_ROUTERS_AVAILABLE = True
except ImportError as e:
    logger.warning("[Task 7-A] V98/V100 admin routers unavailable: %s", e)
    _V98_V100_ROUTERS_AVAILABLE = False

try:
    from scp.api.dashboard_html import DASHBOARD_HTML
except ImportError as e:
    logger.warning("[Task 8-A] dashboard_html unavailable: %s", e)
    DASHBOARD_HTML = "<html><body>Dashboard unavailable</body></html>"

from scp.api.route_profile import resolve_api_profile, route_group_enabled

_API_PROFILE = resolve_api_profile()


def _route_enabled(group: str) -> bool:
    return route_group_enabled(group, _API_PROFILE)


_prod_mode = get("SCP_PRODUCTION_MODE", "0").strip().lower() in {"1", "true", "yes", "on"}
_enable_docs = get("SCP_ENABLE_DOCS", "0" if _prod_mode else "1").strip().lower() in {"1", "true", "yes", "on"}

app = FastAPI(
    title=f"{RELEASE_LABEL} - Self-Correcting Pipeline API",
    description=f"{DOMAIN_EXPERT_ENSEMBLE_TERM} + FalsificationEngine + Governance + Chat + Evolution",
    version=_SCP_VERSION,
    lifespan=lifespan,
    docs_url="/docs" if _enable_docs else None,
    redoc_url="/redoc" if _enable_docs else None,
    openapi_url="/openapi.json" if _enable_docs else None,
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.middleware("http")(rate_limit_middleware)
try:
    setup_telemetry(app)
except Exception as e:
    logger.warning("Telemetry setup skipped: %s", e, exc_info=True)


@app.get("/metrics", dependencies=[Depends(verify_admin)])
async def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


# [ARC-01] _compute_cors_origins tách sang scp/api_server_parts/cors.py (SEC-04 fail-closed giữ nguyên)
from scp.api_server_parts.cors import _compute_cors_origins


_cors_origins = _compute_cors_origins()
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
    allow_credentials=False,
)
from scp.middleware.runtime_skill_guard import RuntimeSkillGuardMiddleware
app.add_middleware(RuntimeSkillGuardMiddleware)
try:
    from fastapi.middleware.httpsredirect import HTTPSRedirectMiddleware
    _force_https = get("SCP_FORCE_HTTPS", "0").strip().lower() in ("1", "true", "yes", "on")
    if _force_https:
        app.add_middleware(HTTPSRedirectMiddleware)
        logger.info("[Security] HTTPS redirect enabled (SCP_FORCE_HTTPS=1)")
    else:
        _bind_host = get("SCP_HOST", "127.0.0.1").strip().lower()
        if _bind_host not in ("127.0.0.1", "localhost", "::1"):
            _prod = get("SCP_PRODUCTION_MODE", "0").strip().lower() in ("1", "true", "yes", "on")
            if _prod:
                raise RuntimeError(
                    "Production safety guard refused startup: SCP_FORCE_HTTPS=1 is required when SCP_HOST is not loopback"
                )
            logger.warning("[Security] production deployment without HTTPS; configure TLS termination")
        else:
            logger.info("[Security] HTTPS redirect disabled (set SCP_FORCE_HTTPS=1 in prod)")
except ImportError:
    logger.debug("[Security] HTTPSRedirectMiddleware unavailable")
logger.info("[Security] CSRF protection: Bearer token auth")


@app.middleware("http")
async def payload_size_limit_middleware(request: Request, call_next):
    MAX_GLOBAL_BODY_BYTES = 10 * 1024 * 1024  # 10MB ceiling
    cl = request.headers.get("content-length")
    if cl:
        try:
            if int(cl) > MAX_GLOBAL_BODY_BYTES:
                return JSONResponse({"detail": "Payload too large"}, status_code=413)
        except ValueError:
            logger.debug("[Security] Invalid content-length header: %s", cl)

    # Protect against unbounded chunked streaming payloads without Content-Length
    received_bytes = 0
    orig_receive = request._receive

    async def limited_receive():
        nonlocal received_bytes
        msg = await orig_receive()
        if msg.get("type") == "http.request":
            chunk = msg.get("body", b"")
            received_bytes += len(chunk)
            if received_bytes > MAX_GLOBAL_BODY_BYTES:
                from fastapi import HTTPException
                raise HTTPException(status_code=413, detail="Payload too large")
        return msg

    request._receive = limited_receive
    try:
        return await call_next(request)
    except Exception as exc:
        def _find_413(e):
            if isinstance(e, HTTPException) and e.status_code == 413:
                return e
            return next((h for sub in getattr(e, "exceptions", ()) if (h := _find_413(sub))), None)
        http_exc = _find_413(exc)
        if http_exc:
            return JSONResponse({"detail": http_exc.detail}, status_code=413)
        raise


if _CHAT_AVAILABLE and _route_enabled("chat"):
    app.include_router(chat_router, tags=["chat"])
if _V98_V100_ROUTERS_AVAILABLE and _route_enabled("versioned_admin"):
    app.include_router(v98_admin_router)
    app.include_router(v100_admin_router)
try:
    from scp.api.admin_knowledge_routes import router as admin_knowledge_router
    if _route_enabled("versioned_admin"):
        app.include_router(admin_knowledge_router)
except ImportError as _ak_err:
    logger.warning("Admin knowledge router unavailable: %s", _ak_err)

_EXTRA_ROUTERS_AVAILABLE = False
try:
    from scp.api.routes.evaluation_routes import router as evaluation_router
    from scp.api.routes.import_routes import router as import_router
    from scp.api.routes.openai_compat import router as openai_compat_router
    from scp.api.routes.swe_bench_routes import router as swe_bench_router
    from scp.api.routes.v102_v103_routes import router as v102_v103_router
    from scp.api.routes.v104_routes import router as v104_router
    from scp.api.routes.v105_routes import router as v105_router
    _EXTRA_ROUTERS_AVAILABLE = True
except ImportError as e:
    logger.warning("[Task 9-B] V102-V105/import routers unavailable: %s", e)

if _EXTRA_ROUTERS_AVAILABLE:
    if _route_enabled("openai_compat"):
        app.include_router(openai_compat_router)
    if _route_enabled("evaluation"):
        app.include_router(evaluation_router)
    if _route_enabled("versioned_admin"):
        app.include_router(v102_v103_router)
        app.include_router(v104_router)
        app.include_router(v105_router)
        app.include_router(swe_bench_router)
        try:
            from scp.api.routes.v106_routes import audit_router, capability_router
            app.include_router(audit_router)
            app.include_router(capability_router)
        except ImportError as _v106_err:
            # [MACH1-FIX-8 / D6 fail-loudly] A missing v106 module silently
            # drops the audit/capability route group — operators must see it.
            logger.warning("[Task 9-B] v106 audit/capability routers unavailable: %s", _v106_err)
    if _route_enabled("import"):
        app.include_router(import_router)

try:
    from scp.api.routes.control_routes import router as control_router
    if _route_enabled("control"):
        app.include_router(control_router, tags=["control"])
        _CONTROL_ROUTES_AVAILABLE = True
    else:
        _CONTROL_ROUTES_AVAILABLE = False
except ImportError as e:
    logger.warning("[SCP Control] Control router unavailable: %s", e)
    _CONTROL_ROUTES_AVAILABLE = False

for _group, _module_name, _router_name, _tags in (
    ("stream", "scp.api.routes.stream_routes", "router", ["stream"]),
    ("threat", "scp.api.routes.threat_routes", "router", ["threats"]),
    ("audit", "scp.api.routes.audit_routes", "router", ["audit"]),
    ("prediction", "scp.api.routes.prediction_routes", "router", ["predictions"]),
    # Wave-1: Restored subsystems
    ("risk_intelligence", "scp.api.routes.risk_routes", "router", ["risk-intelligence"]),
    ("world_state", "scp.api.routes.world_state_routes", "router", ["world-state"]),
    # Wave-2: Restored subsystems
    ("calibration", "scp.api.routes.calibration_routes", "router", ["calibration"]),
    ("forecast", "scp.api.routes.forecast_routes", "router", ["forecast"]),
    ("history", "scp.api.routes.history_routes", "router", ["history"]),
):
    try:
        if _route_enabled(_group):
            import importlib as _importlib
            _router = getattr(_importlib.import_module(_module_name), _router_name)
            app.include_router(_router, tags=_tags)
    except ImportError as _e:
        logger.warning("%s router unavailable: %s", _group, _e)

try:
    from scp.api.webhook import router as webhook_router
    if _route_enabled("webhook"):
        app.include_router(webhook_router)
        _WEBHOOK_ROUTER_AVAILABLE = True
    else:
        _WEBHOOK_ROUTER_AVAILABLE = False
except ImportError as e:
    logger.warning("[Task 42-B] Webhook router unavailable: %s", e)
    _WEBHOOK_ROUTER_AVAILABLE = False

try:
    from scp.api.routes.pc_controller_routes import router as pc_controller_router
    if _route_enabled("pc_controller"):
        app.include_router(pc_controller_router)
        _PC_CONTROLLER_AVAILABLE = True
    else:
        _PC_CONTROLLER_AVAILABLE = False
except ImportError as e:
    logger.warning("[V3.1] PC Controller router unavailable: %s", e)
    _PC_CONTROLLER_AVAILABLE = False

try:
    from scp.api.routes.web_control_routes import router as web_control_router
    if _route_enabled("web_control"):
        app.include_router(web_control_router)
        _WEB_CONTROL_AVAILABLE = True
    else:
        _WEB_CONTROL_AVAILABLE = False
except ImportError as e:
    logger.warning("[V3.1] Web control router unavailable: %s", e)
    _WEB_CONTROL_AVAILABLE = False


class TokenRequest(BaseModel):
    admin_key: str


@app.post("/auth/token")
@limiter.limit("5/minute")
def login_for_access_token(req: TokenRequest, request: Request):
    # [STEP0-FIX 2026-09-02] Fail-closed: the previous default "admin" let
    # anyone mint an admin JWT on deployments without SCP_ADMIN_KEY (P0,
    # verified live 2026-08-29). Unconfigured auth is a 401 authorization
    # failure, matching the canonical verify_admin contract in
    # scp/security/auth.py (no dev-mode bypass).
    expected_key = os.environ.get("SCP_ADMIN_KEY", "")
    import secrets as _secrets
    if not expected_key or not _secrets.compare_digest(req.admin_key.encode(), expected_key.encode()):
        raise HTTPException(status_code=401, detail="Incorrect admin key")
    from scp.security.jwt_guard import create_access_token
    access_token = create_access_token(data={"sub": "admin"})
    return {"access_token": access_token, "token_type": "bearer"}


@app.post("/ask", response_model=AskResponse)
@limiter.limit("60/minute")
@traced_request(_REQUEST_RUN_LEDGER)
async def ask(req: AskRequest, request: Request, current_user: Any = Depends(verify_jwt_token)):
    REQUEST_COUNT.labels(method="POST", endpoint="/ask").inc()
    # [F-RUN-01 audit-r2 2026-10-01] Kill-switch admission gate — placed BEFORE
    # every other check (judge readiness, kernel adapter) because an engaged
    # kill switch is an absolute stop: no kernel task may be created, leased
    # or completed while it is engaged (audit A9 observed /ask reaching
    # COMPLETED after POST /v3/pc/kill). Kernel-level enforcement additionally
    # engages taskkernel.set_global_kill(True) on the live adapters (see
    # pc_controller_routes._engage_kernel_global_kill); this file-flag gate is
    # the process-independent authority that also covers adapters created
    # after the kill was engaged.
    if _pc_kill_switch_engaged():
        logger.warning(
            "[F-RUN-01] /ask refused: kill switch engaged (falsification_status=KILL_SWITCH_ENGAGED)"
        )
        return _kill_switch_blocked_response(req)
    # [MACH1-FIX-4] Fail-closed judge gate (matches the /readiness contract and
    # the lifespan comment "/ask returns 503 until ready"). Without this, a
    # request arriving before the judge is ready triggered a blocking 30-60s
    # lazy init inside the request path.
    if not getattr(app.state, "judge_ready", False):
        return JSONResponse(
            status_code=503,
            content={
                "detail": "judge_initializing",
                "reason": getattr(app.state, "readiness_reason", None) or "judge_initialization_pending",
                "retry_after_seconds": 5,
            },
        )
    if not _ask_kernel_enabled(req):
        return _kernel_gate_unavailable_response(req, RuntimeError("rag_kernel_disabled"))
    adapter = _get_ask_kernel_adapter()
    if adapter is None:
        return _kernel_gate_unavailable_response(
            req,
            _ASK_KERNEL_INIT_ERROR or RuntimeError("kernel_adapter_unavailable"),
        )
    return await adapter.run_rag(req, request, _ask_impl)


try:
    from scp.api_server_parts._trace_impl import router as trace_router
    if _route_enabled("trace"):
        app.include_router(trace_router, tags=["trace"])
        _TRACE_AVAILABLE = True
    else:
        _TRACE_AVAILABLE = False
except ImportError as e:
    logger.warning("[SCP Trace] Trace router unavailable: %s", e)
    _TRACE_AVAILABLE = False


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    return HTMLResponse(DASHBOARD_HTML)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service_identity": _scp_service_identity(),
        "version": _SCP_VERSION,
        "release": public_release_metadata(),
        "routes": len(app.routes),
        "modules": "136+ Python files",
        "note": "minimal health — use /health/detailed for full status",
    }


# [SEC-FIX /health-authz 2026-09-26] /health/detailed discloses internals
# (data dir size, tracker stats, sandbox capability, routing stats). It now
# requires admin auth like /metrics does; plain /health stays unauthenticated
# and minimal for liveness probes.
@app.get("/health/detailed", dependencies=[Depends(verify_admin)])
async def health_detailed():
    from scp.api_server_parts.server_helpers import build_health_detailed_payload

    return build_health_detailed_payload(
        app=app,
        judge_fn=get_judge,
        version=_SCP_VERSION,
        multi_turn_tracker=_multi_turn_tracker,
        cross_language_learner=_cross_language_learner,
        fact_checker=_fact_checker,
        routing_stats_fn=_question_routing_stats,
    )


@app.get("/ready")
@app.get("/readiness")
async def readiness():
    judge_ready = bool(getattr(app.state, "judge_ready", False))
    scheduler_started = bool(getattr(app.state, "background_scheduler_started", False))
    payload = {
        "status": "ready" if judge_ready else "initializing",
        "service": "scp-api",
        "version": _SCP_VERSION,
        "checks": {
            "judge": "ok" if judge_ready else "pending",
            "background_scheduler": "ok" if scheduler_started else "pending",
        },
        "reason": getattr(app.state, "readiness_reason", None),
    }
    return JSONResponse(payload, status_code=200 if judge_ready else 503)


@app.get("/")
async def root():
    # [W4-4 docs-truth] Advertise only routes that are actually mounted under
    # the active API profile (scp/api/route_profile.py). `/v1/chat/completions`
    # and `/v1/models` live in the `openai_compat` router, which is frozen when
    # SCP_API_PROFILE=core (the SCP_PRODUCTION_MODE default) — listing them
    # then would promise endpoints that answer 404.
    endpoints: list[str] = ["POST /ask"]
    if _route_enabled("openai_compat"):
        endpoints += ["POST /v1/chat/completions", "GET  /v1/models"]
    endpoints += [
        "POST /v98/analyze-session",
        "POST /v98/run-simulation",
        "POST /v98/run-intel-crawl",
        "GET  /v98/status",
        "GET  /v98/counter/stats",
        "GET  /v98/canary/triggers",
        "GET  /v98/error-store/stats",
        "GET  /v98/attack-memory/stats",
        "GET  /dashboard",
        "GET  /health",
    ]
    return {
        "name": "SCP",
        "version": _SCP_VERSION,
        "description": "Self-Correcting Pipeline with V98 security",
        "endpoints": endpoints,
    }


@app.get("/v3/tasks", dependencies=[Depends(verify_jwt_token)])
@limiter.limit("30/minute")
async def v3_tasks_list(request: Request) -> dict[str, Any]:
    """[M-09] Protected task lifecycle listing endpoint.

    [W4-4 docs-truth] Reads the REAL TaskKernel projection instead of a
    hardcoded empty list. Exposes only safe metadata (task_id, state,
    created_at) — never owner/goal payload, which may contain user content.
    Read-only: no task is created, leased, or transitioned. The response
    keeps the original shape `{"tasks": [...], "status": "active"}` and the
    list is empty when the kernel DB is absent (backward compatible).
    """
    tasks: list[dict[str, Any]] = []
    warning: str | None = None
    db_path = runtime_path("SCP_KERNEL_DB_PATH", "ask_task_kernel.sqlite3")
    if db_path.exists():
        try:
            from scp.task_kernel import TaskKernel

            # Short-lived read connection, same pattern as the lifespan
            # retry-policy worker (api_server_parts/lifespan.py).
            kernel = TaskKernel(str(db_path))
            try:
                # [HC-21 2026-10-23] Đi qua kernel accessor list_recent thay vì
                # SQL thô trên projection tasks — contract đọc nằm trong kernel.
                # Short-lived read connection, same pattern as the lifespan
                # retry-policy worker (api_server_parts/lifespan.py).
                tasks = kernel.list_recent(20)
            finally:
                kernel.close()
        except Exception as exc:  # non-fatal read; mirrors lifespan kernel polling
            logger.warning("[V3-TASKS] kernel read failed: %s", exc)
            warning = "task_kernel_unavailable"
    payload: dict[str, Any] = {"tasks": tasks, "status": "active"}
    if warning:
        payload["warning"] = warning
    return payload


try:
    from scp.api.routes.hands_routes import router as hands_router
    if _route_enabled("hands"):
        app.include_router(hands_router)
    from scp.api.routes.batch_benchmark_routes import router as batch_benchmark_router
    if _route_enabled("batch_benchmark"):
        app.include_router(batch_benchmark_router)
    _HANDS_AVAILABLE = _route_enabled("hands")
except ImportError as e:
    logger.warning("[SCP Hands v3.2] Hands router unavailable: %s", e)
    _HANDS_AVAILABLE = False

try:
    from scp.api.routes.agent_routes import router as agent_router
    if _route_enabled("agent"):
        app.include_router(agent_router)
        _AGENT_ORCHESTRATOR_AVAILABLE = True
    else:
        _AGENT_ORCHESTRATOR_AVAILABLE = False
except ImportError as e:
    logger.warning("[SCP Agent] Agent orchestrator router unavailable: %s", e)
    _AGENT_ORCHESTRATOR_AVAILABLE = False

try:
    from scp.api.routes.call_routes import router as call_router
    if _route_enabled("call"):
        app.include_router(call_router)
        _CALL_SIGNALING_AVAILABLE = True
    else:
        _CALL_SIGNALING_AVAILABLE = False
except ImportError as e:
    logger.warning("[SCP Call] Call signaling router unavailable: %s", e)
    _CALL_SIGNALING_AVAILABLE = False

try:
    from scp.observability.otel import configure_fastapi_otel
    _OTEL_STATUS = configure_fastapi_otel(app)
    if _OTEL_STATUS.get("enabled"):
        logger.info("[OTel] FastAPI tracing enabled without request-body/header capture")
    else:
        logger.info("[OTel] tracing disabled: %s", _OTEL_STATUS.get("reason", "not configured"))
except Exception as e:
    _OTEL_STATUS = {"enabled": False, "reason": type(e).__name__}
    logger.warning("[OTel] optional instrumentation unavailable: %s", type(e).__name__, exc_info=True)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("scp.api_server:app", host="127.0.0.1", port=8000, reload=False, log_level="info")

