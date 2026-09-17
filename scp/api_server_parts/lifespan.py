# Auto-extracted from api_server.py
# SCP CIRCUIT: M1 Boot & Background — STATUS: CLOSED (closure: reports/circuit-closures/M01-closure.json)
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

# [MACH1-FIX-9 / F821] Bind this module's logger explicitly. In production these
# functions are re-bound into scp/api_server.py's globals (which defines
# `logger`), but a raw import / static analysis / refactor would hit NameError
# exactly on the fail-loudly branches. Same logger name as api_server -> same
# logger object, so no duplicate handlers and no double emit.
logger = logging.getLogger("scp.api")

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info('=' * 60)
    logger.info(f'{RELEASE_LABEL} API Server starting...')
    logger.info('=' * 60)
    # [MACH1-FIX-2] Boot config gate — fail-closed (DNA #6, #22): validate the
    # environment contract BEFORE any subsystem starts. Deliberately NOT inside
    # try/except: a ConfigContractError must propagate and abort boot instead of
    # being swallowed into a half-initialized server.
    from scp.core.config_contract import validate_boot_config
    _validated_env = validate_boot_config()
    logger.info('[MACH1-FIX-2] Boot config contract validated (fail-closed)')
    # [Q12] Typed-settings bridge — same fail-closed posture: config_contract
    # remains the authority for required secrets; validate_boot_settings only
    # adds type validation of SCP_* values + a consistency cross-check against
    # the contract-validated view. ConfigContractError propagates, abort boot.
    from scp.core.config import validate_boot_settings
    validate_boot_settings(_validated_env)
    logger.info('[Q12] Typed boot settings validated against config contract (fail-closed)')
    app.state.judge_ready = False
    app.state.startup_status = 'starting'
    app.state.readiness_reason = 'judge_initialization_pending'
    _orig_why_llm = os.environ.get('SCP_WHY_LLM_ENABLED', '0')
    _orig_evo_auto = os.environ.get('SCP_EVOLUTION_AUTO', '0')
    os.environ['SCP_WHY_LLM_ENABLED'] = '0'
    os.environ['SCP_EVOLUTION_AUTO'] = '0'

    async def _startup_gate_background():
        """Run bounded AST startup scan without blocking socket readiness."""
        await asyncio.sleep(max(0.0, float(os.environ.get('SCP_STARTUP_BACKGROUND_DELAY_SEC', '5'))))
        if os.environ.get('SCP_SKIP_STARTUP_GATE', '0') == '1':
            logger.warning('[STARTUP-GATE] SCP_SKIP_STARTUP_GATE=1 — audit BYPASSED (dev/test mode)')
            return
        try:
            from scp.autofix.runner import ast_scan_scp
            timeout_sec = float(os.environ.get('SCP_STARTUP_SCAN_TIMEOUT_SEC', '15'))
            bugs = await asyncio.wait_for(asyncio.to_thread(ast_scan_scp, max_files=int(os.environ.get('SCP_MAX_STARTUP_FILES', '100')), include_enterprise=os.environ.get('SCP_STARTUP_SCAN_ENTERPRISE', '0') == '1'), timeout=max(1.0, timeout_sec))
            logger.info(f'[STARTUP-GATE] Background scan complete: {len(bugs)} bugs found')
        except asyncio.TimeoutError:
            logger.warning(f'[STARTUP-GATE] Background scan timed out after {timeout_sec}s (non-blocking)')
        except Exception as e:
            logger.warning(f'[STARTUP-GATE] Background scan failed (non-blocking): {e}')
    _startup_gate_task = asyncio.create_task(_startup_gate_background())
    import threading as _threading_r20

    def _init_judge_background_r20():
        """Initialize the judge without claiming a legacy scheduler exists.

        RealityJudge is the canonical judge implementation and does not expose
        the removed ThreatSimulator/IntelCrawler scheduler hook.  The real
        background scheduler contract is the BackgroundJobRegistry started
        later in this lifespan; judge initialization only controls the judge
        readiness check.
        """
        global _judge
        try:
            judge = get_judge()
            _judge = judge
            app.state.judge_ready = True
            app.state.startup_status = 'ready'
            if getattr(app.state, 'background_scheduler_started', False):
                app.state.readiness_reason = None
            logger.info(f"[R20-ROOT-FIX-REAL] Judge ready: {len(getattr(judge, 'domain_experts', []))} SLMs")
            logger.info(f'[R20-ROOT-FIX-REAL] V98 status: {judge.get_v98_status()}')
        except Exception as e:
            app.state.judge_ready = False
            app.state.startup_status = 'failed'
            app.state.readiness_reason = 'judge_initialization_failed'
            logger.error(f'[R20-ROOT-FIX-REAL] Judge init FAILED: {e}')
            logger.error('[R20-ROOT-FIX-REAL] /ask will return 503 until judge is available')
    _judge_thread_r20 = _threading_r20.Thread(target=_init_judge_background_r20, name='scp-judge-init-r20', daemon=True)

    async def _launch_judge_deferred():
        await asyncio.sleep(max(0.0, float(os.environ.get('SCP_JUDGE_START_DELAY_SEC', '5'))))
        try:
            _judge_thread_r20.start()
        except RuntimeError as e:
            logger.warning(f'[R20-ROOT-FIX-REAL] Deferred judge launch skipped: {e}')
    _judge_launch_task = asyncio.create_task(_launch_judge_deferred())
    logger.info('[R20-ROOT-FIX-REAL] Judge init dispatched to background thread')
    logger.info('[R20-ROOT-FIX-REAL] Yielding NOW │Ă¢â€\x9aÂ¬Ă¢â‚¬Â\x9d port 8000 binds immediately')
    # Readiness is owned by the real BackgroundJobRegistry below; do not
    # create a task for the removed RealityJudge scheduler hook or mark it
    # started merely because asyncio.create_task() returned.
    app.state.background_scheduler_started = False
    app.state.background_scheduler_status = 'pending'
    app.state.background_scheduler_error = None
    app.state.readiness_reason = 'background_scheduler_pending'
    app.state.evolution_initialized = False

    async def _start_evolution_runtime():
        try:
            await asyncio.sleep(5)
            from scp.autofix.evolution import get_evolution_engine
            await asyncio.to_thread(get_evolution_engine, data_dir=os.environ.get('SCP_DATA_DIR', 'data'))
            app.state.evolution_initialized = True
            logger.info('[EVOLUTION] Runtime initialized; auto promotion remains env-gated')
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning('[EVOLUTION] Runtime initialization failed: %s', exc)
    _evolution_bootstrap_task = asyncio.create_task(_start_evolution_runtime())
    _audit_thread = None
    _attack_thread = None
    app.state.deep_audit_started = False
    app.state.attack_monitor_started = False
    try:
        from scp.api.background_jobs import registry
        from scp.autofix.runner import run_deep_audit
        from scp.core.subsystem_telemetry import SubsystemTelemetry, heartbeat_sleep
        _audit_telemetry = SubsystemTelemetry('deep_audit', os.environ.get('SCP_DATA_DIR', 'data'))
        _audit_telemetry.start(mode='background', config={'interval_seconds': 86400})
        _audit_telemetry.tick(status='IDLE')
        _audit_stop = threading.Event()
        _audit_state = {'status': 'STARTING'}
        app.state.deep_audit_stop = _audit_stop

        def _deep_audit_heartbeat_loop():
            while not _audit_stop.is_set():
                try:
                    _audit_telemetry.tick(status=_audit_state['status'])
                except Exception as exc:
                    logger.warning('[AUTO] deep-audit heartbeat tick failed: %s', exc)
                if _audit_stop.wait(15):
                    return
        threading.Thread(target=_deep_audit_heartbeat_loop, daemon=True, name='scp-deep-audit-heartbeat').start()

        def _deep_audit_loop():
            heartbeat_sleep(_audit_telemetry, 60, status='IDLE')
            while not _audit_stop.is_set():
                run_id = f'deep-audit-{time.time_ns()}'
                _audit_state['status'] = 'RUNNING'
                _audit_telemetry.cycle_started(run_id, trigger='interval')
                try:
                    logger.info('[AUTO] Deep audit cycle starting...')
                    results = run_deep_audit(max_bugs=int(os.environ.get('SCP_MAX_AUDIT_BUGS', '100')))
                    logger.info('[AUTO] Deep audit: %s bugs processed, %s auto-fixed', results.get('processed', 0), results.get('fixed', 0))
                    _audit_telemetry.cycle_completed(run_id, 'SUCCESS', bugs_found=results.get('processed', 0), bugs_fixed=results.get('fixed', 0), stored=results.get('stored', 0))
                except TimeoutError as exc:
                    logger.warning('[AUTO] Deep audit timeout: %s', exc)
                    _audit_telemetry.cycle_failed(run_id, exc, status='TIMEOUT')
                except Exception as exc:
                    logger.warning('[AUTO] Deep audit failed: %s', exc)
                    _audit_telemetry.cycle_failed(run_id, exc, status='PROVIDER_FAILED')
                _audit_state['status'] = 'IDLE'
                heartbeat_sleep(_audit_telemetry, 86400, status='IDLE')

        @registry.register(name="deep_audit_scheduler", interval_seconds=86400, required=False, initial_delay_seconds=60)
        def _deep_audit_job():
            _deep_audit_loop()

        app.state.deep_audit_started = True
        logger.info('[AUTO] Deep audit scheduler registered in background job registry (24h interval)')
    except Exception as exc:
        logger.warning('[AUTO] Deep audit scheduler failed to start: %s', exc)
    try:
        from scp.autofix.engine import get_autofix_engine
        from scp.core.subsystem_telemetry import SubsystemTelemetry, heartbeat_sleep
        _attack_telemetry = SubsystemTelemetry('attack_monitor', os.environ.get('SCP_DATA_DIR', 'data'))
        _attack_telemetry.start(mode='background', config={'interval_seconds': 300})
        _attack_telemetry.tick(status='IDLE')
        _attack_stop = threading.Event()
        _attack_state = {'status': 'STARTING'}
        app.state.attack_monitor_stop = _attack_stop

        def _attack_heartbeat_loop():
            while not _attack_stop.is_set():
                try:
                    _attack_telemetry.tick(status=_attack_state['status'])
                except Exception as exc:
                    logger.warning('[AUTO] attack-monitor heartbeat tick failed: %s', exc)
                if _attack_stop.wait(15):
                    return
        threading.Thread(target=_attack_heartbeat_loop, daemon=True, name='scp-attack-monitor-heartbeat').start()

        def _attack_mode_monitor():
            heartbeat_sleep(_attack_telemetry, 120, status='IDLE')
            while not _attack_stop.is_set():
                run_id = f'attack-monitor-{time.time_ns()}'
                _attack_state['status'] = 'RUNNING'
                _attack_telemetry.cycle_started(run_id, trigger='interval')
                try:
                    eng = get_autofix_engine()
                    notif = getattr(_judge, 'notifications', None)
                    cutoff = time.time() - 600
                    kill_count = notif.count_recent_by_type('governance_kill', cutoff) if notif is not None else 0
                    if kill_count > 20 and (not eng.in_attack_mode):
                        eng.set_attack_mode(True)
                        logger.warning('[AUTO] Attack mode ENABLED — %s KILLs in 10min', kill_count)
                    elif kill_count < 5 and eng.in_attack_mode:
                        eng.set_attack_mode(False)
                        logger.info('[AUTO] Attack mode DISABLED — %s KILLs in 10min', kill_count)
                    _attack_telemetry.cycle_completed(run_id, 'SUCCESS', asked=kill_count, verified=1)
                except TimeoutError as exc:
                    logger.warning('[AUTO] Attack mode monitor timeout: %s', exc)
                    _attack_telemetry.cycle_failed(run_id, exc, status='TIMEOUT')
                except Exception as exc:
                    logger.warning('[AUTO] Attack mode monitor: %s', exc)
                    _attack_telemetry.cycle_failed(run_id, exc, status='PROVIDER_FAILED')
                _attack_state['status'] = 'IDLE'
                heartbeat_sleep(_attack_telemetry, 300, status='IDLE')

        @registry.register(name="attack_mode_monitor", interval_seconds=300, required=False, initial_delay_seconds=120)
        def _attack_mode_monitor_job():
            _attack_mode_monitor()

        app.state.attack_monitor_started = True
        logger.info('[AUTO] Attack mode monitor registered in background job registry (5min interval)')
    except Exception as exc:
        logger.warning('[AUTO] Attack mode monitor failed to start: %s', exc)
    try:
        from scp.core.doubt_cron import get_doubt_cron
        _data_dir = os.environ.get('SCP_DATA_DIR', 'data')
        _kernel_db = os.path.join(_data_dir, 'ask_task_kernel.sqlite3')
        if os.path.exists(_kernel_db):
            from scp.task_kernel import TaskKernel
            _recovery_kernel = TaskKernel(_kernel_db)
            _recovery = _recovery_kernel.recover_on_boot()
            if _recovery['recovered']:
                logger.warning('[RECOVERY] Boot recovery: %d tasks recovered, %d corrupted', len(_recovery['recovered']), len(_recovery['corrupted']))
            _recovery_kernel.close()
    except Exception as exc:
        logger.warning('[RECOVERY] Boot recovery failed (non-fatal): %s', exc)
    try:
        from scp.core.doubt_cron import get_doubt_cron
        _doubt = get_doubt_cron(data_dir=os.environ.get('SCP_DATA_DIR', 'data'))
        _doubt.start()
        app.state.doubt_cron = _doubt
        logger.info('[DOUBT] Cronjob of Doubt started (interval=%ss)', _doubt.interval)
    except Exception as exc:
        logger.warning('[DOUBT] Cronjob of Doubt failed to start (non-fatal): %s', exc)

    # --- [M12-FIX PF-4b] Background WHY verify loop — wiring into the ACTIVE
    # lifespan. TẠI SAO: the only implementation of this loop lived in
    # scp/api/_lifespan.py, which is NOT the lifespan api_server.py uses
    # (api_server.py binds scp.api_server_parts.lifespan) — so the deferred
    # WHY verification scheduler never ran in the real deployment while the
    # comment block claimed it was fixed (R6-3 pattern: PASS ≠ TRUE).
    # Cadence matches R5's recommendation (3min warm-up, 5min cycle); the
    # engine arrives via the judge singleton wired in helpers.get_judge()
    # ([M12-FIX PF-4a]); every failure is logged, never raised.
    try:
        def _why_verify_loop():
            time.sleep(180)  # warm-up: let judge fully init + first verdicts land
            while True:
                try:
                    _j = None
                    try:
                        _j = get_judge()
                    except Exception as _judge_exc:
                        logger.debug('[WHY-VERIFY] get_judge not ready: %r', _judge_exc)
                    _we = getattr(_j, 'why_engine', None) if _j else None
                    if _we is not None and hasattr(_we, 'run_pending_verification_cycle'):
                        _stats = _we.run_pending_verification_cycle(limit=10)
                        if isinstance(_stats, dict) and _stats.get('executed', 0) > 0:
                            logger.info('[WHY-VERIFY] cycle: %s', _stats)
                except Exception as _loop_exc:
                    logger.warning('[WHY-VERIFY] cycle failed (non-fatal): %s', _loop_exc)
                time.sleep(300)  # 5min (R5 recommended cadence)

        _why_thread = threading.Thread(target=_why_verify_loop, daemon=True,
                                       name="scp-why-verify-scheduler")
        _why_thread.start()
        app.state.why_verify_thread = _why_thread
        logger.info('[WHY-VERIFY] Background WHY verification scheduler started (5min interval)')
    except Exception as exc:
        logger.warning('[WHY-VERIFY] scheduler failed to start (non-fatal): %s', exc)

    # --- [MACH1-FIX-3] RetryPolicy background worker — REAL kernel wiring ---
    # Previously this block built RetryPolicy around empty stubs (_get_waiting_plans
    # returned [], _retry_plan was `pass`) and targeted `_retry_policy.run_background`
    # which did not exist in scp/policy/retry_policy.py → AttributeError swallowed by
    # the broad except below → the retry worker never actually ran. Now wired to the
    # real TaskKernel DB: poll RETRY_SCHEDULED tasks, requeue them when the retry
    # timeout expires (transition validated by the kernel state machine).
    try:
        from scp.policy.retry_policy import RetryPolicy

        _retry_db_path = os.environ.get(
            'SCP_KERNEL_DB_PATH',
            os.path.join(os.environ.get('SCP_DATA_DIR', 'data'), 'ask_task_kernel.sqlite3'),
        )

        def _get_waiting_plans():
            """Real poll: tasks the kernel parked in RETRY_SCHEDULED."""
            if not os.path.exists(_retry_db_path):
                return []
            from scp.task_kernel import TaskKernel
            try:
                _rk = TaskKernel(_retry_db_path)
            except Exception as poll_exc:
                logger.warning('[RETRY-POLICY] kernel open failed: %s', poll_exc)
                return []
            try:
                rows = _rk.conn.execute(
                    "SELECT task_id, state FROM tasks WHERE state='RETRY_SCHEDULED'"
                ).fetchall()
                return [{'planId': row['task_id'], 'state': row['state']} for row in rows]
            finally:
                _rk.close()

        def _retry_plan(task_id, retry_reason):
            from scp.task_kernel import TaskKernel
            _rk = TaskKernel(_retry_db_path)
            try:
                _rk.transition(task_id, 'QUEUED', actor='retry_policy', reason='retry_policy_background')
                logger.info('[RETRY-POLICY] task %s requeued to QUEUED (%s)', task_id, retry_reason)
            finally:
                _rk.close()

        _retry_policy = RetryPolicy(get_plans_fn=_get_waiting_plans, retry_fn=_retry_plan)
        _retry_stop = threading.Event()
        app.state.retry_policy_stop = _retry_stop
        _retry_thread = threading.Thread(
            target=_retry_policy.run_background,
            kwargs={
                'interval_sec': float(os.environ.get('SCP_RETRY_POLL_SEC', '60')),
                'stop_event': _retry_stop,
            },
            daemon=True,
            name='scp-retry-policy',
        )
        _retry_thread.start()
        app.state.retry_policy = _retry_policy
        logger.info('[RESTORED-SYSTEMS] RetryPolicy background thread started (real kernel wiring, db=%s)', _retry_db_path)
    except Exception as exc:
        logger.warning('[RESTORED-SYSTEMS] RetryPolicy failed to start: %s', exc)
    # ------------------------------------------------------------------

    # [MACH1-FIX-1] Start all registered background jobs. The registry already
    # enforces the fail-closed contract: a required job that fails to start
    # raises RuntimeError from start_all() and aborts boot (correct behavior for
    # required=True). Jobs: kernel_lease_expiry, kernel_orphan_reconcile (required),
    # canary_token_cleanup, deep_audit_scheduler, attack_mode_monitor (optional).
    try:
        from scp.api.background_jobs import registry as _bg_registry
        _bg_registry.start_all()
        _registry_status = _bg_registry.status()
        _required_not_started = [
            name for name, status in _registry_status.items()
            if status.get('required') and not status.get('started')
        ]
        if _required_not_started:
            raise RuntimeError(
                'required background jobs not running: ' + ', '.join(sorted(_required_not_started))
            )
        # ``background_scheduler_started`` is the existing readiness gate
        # consumed by /readiness.  Keep it false until every required job has
        # completed one successful execution; thread creation alone is not
        # readiness evidence.  The monitor below promotes it after the first
        # successful cycle and revokes it on the first required-job failure.
        app.state.background_scheduler_started = False
        app.state.background_scheduler_status = 'starting'
        app.state.background_scheduler_error = None
        app.state.background_scheduler_failure_jobs = []
        app.state.background_scheduler_failure_ids = {}
        app.state.readiness_reason = 'background_scheduler_pending_first_execution'
        _required_job_names = sorted(
            name for name, status in _registry_status.items() if status.get('required')
        )
        logger.info('[MACH1-FIX-1] Background job registry started (%d jobs; awaiting required first execution: %s)', len(_bg_registry._jobs), ', '.join(_required_job_names))
    except Exception as exc:
        # Required-job failure must abort boot — re-raise, do not swallow.
        app.state.background_scheduler_started = False
        app.state.background_scheduler_status = 'failed'
        app.state.background_scheduler_error = type(exc).__name__
        app.state.readiness_reason = 'background_scheduler_failed'
        logger.error('[MACH1-FIX-1] Background job registry failed to start: %s', type(exc).__name__, exc_info=True)
        raise

    async def _monitor_background_registry():
        """Promote readiness after first success; observe later required errors."""
        _readiness_promoted = False
        try:
            while True:
                await asyncio.sleep(0.25)
                _statuses = _bg_registry.status()
                _required = {
                    name: status
                    for name, status in _statuses.items()
                    if status.get('required')
                }
                _not_ready_required = [
                    name for name, status in _required.items()
                    if not status.get('started') or not status.get('first_execution_completed')
                ]
                _failed_required = [
                    name for name, status in _required.items()
                    if status.get('readiness_revoked')
                    or status.get('error_count', 0) >= status.get('failure_threshold', 1)
                ]
                if _failed_required:
                    app.state.background_scheduler_started = False
                    app.state.background_scheduler_status = 'failed'
                    app.state.background_scheduler_error = 'required_job_failed'
                    app.state.background_scheduler_failure_jobs = sorted(_failed_required)
                    app.state.background_scheduler_failure_ids = {
                        name: _required[name].get('last_failure_id')
                        for name in _failed_required
                    }
                    app.state.readiness_reason = 'background_scheduler_failed'
                    logger.error(
                        '[MACH1-FIX-1] Required background job failed after startup; readiness revoked: %s (failure_ids=%s)',
                        ', '.join(sorted(_failed_required)),
                        app.state.background_scheduler_failure_ids,
                    )
                    return
                if _not_ready_required:
                    app.state.background_scheduler_started = False
                    app.state.background_scheduler_status = 'starting'
                    app.state.background_scheduler_failure_jobs = []
                    app.state.background_scheduler_failure_ids = {}
                    app.state.readiness_reason = 'background_scheduler_pending_first_execution'
                    _readiness_promoted = False
                    continue
                app.state.background_scheduler_started = True
                app.state.background_scheduler_status = 'running'
                app.state.background_scheduler_error = None
                app.state.background_scheduler_failure_jobs = []
                app.state.background_scheduler_failure_ids = {}
                if getattr(app.state, 'judge_ready', False):
                    app.state.readiness_reason = None
                else:
                    app.state.readiness_reason = 'judge_initialization_pending'
                if not _readiness_promoted:
                    logger.info('[MACH1-FIX-1] Required background jobs completed first execution; scheduler readiness promoted')
                    _readiness_promoted = True
        except asyncio.CancelledError:
            raise

    _background_registry_monitor_task = asyncio.create_task(_monitor_background_registry())

    # --- [S23-DISCOVERY] Free discovery scheduler — owner directive "tự động
    # tìm và cập nhật hơn 1000 API". Blueprint scp/core/free_discovery_scheduler.py
    # xây mới (S23): asyncio task nền, tick ĐẦU chạy ngay khi boot, các tick sau
    # mỗi 6h ± 10% jitter; mỗi tick refresh ĐỘC LẬP FreeAPICatalog + OpenRouter
    # free-model catalog (CHỈ GỌI seam refresh_free_catalog có sẵn). Kill-switch:
    # SCP_DISCOVERY_SCHEDULER=off. Mọi lỗi log WARNING, KHÔNG raise (non-fatal,
    # cùng contract với các background subsystem phía trên).
    try:
        from scp.core.free_discovery_scheduler import FreeDiscoveryScheduler
        _discovery = FreeDiscoveryScheduler()
        _discovery_task = _discovery.start()
        app.state.discovery_scheduler = _discovery
        if _discovery_task is None:
            logger.info('[S23-DISCOVERY] FreeDiscoveryScheduler not started (kill-switch SCP_DISCOVERY_SCHEDULER=off)')
        else:
            logger.info('[S23-DISCOVERY] Free discovery wired: FreeAPICatalog + LLM free-model catalog refresh on 6h cadence with jitter')
    except Exception as exc:
        logger.warning('[S23-DISCOVERY] scheduler failed to start (non-fatal): %s', exc)

    # --- [S35-LIFECYCLE] Operator-configured local model lifecycle scheduler.
    # This is deliberately a separate scheduler so S23's free-catalog cadence
    # and kill-switch contract remain unchanged.  No endpoint is accepted from
    # prompts, requests, or discovery responses: create_model_lifecycle_scheduler
    # reads only SCP_LOCAL_ENDPOINTS and re-checks egress at every HTTP call.
    try:
        from scp.llm_gateway.discovery import create_model_lifecycle_scheduler

        _model_lifecycle = create_model_lifecycle_scheduler()
        app.state.model_lifecycle_scheduler = _model_lifecycle
        if _model_lifecycle is None:
            logger.info('[S35-LIFECYCLE] not started (no operator-configured SCP_LOCAL_ENDPOINTS)')
        else:
            _lifecycle_task = _model_lifecycle.start()
            if _lifecycle_task is None:
                logger.info('[S35-LIFECYCLE] not started (kill-switch/profile policy)')
            else:
                logger.info('[S35-LIFECYCLE] model discovery/lifecycle scheduler wired')
    except Exception as exc:
        # Discovery is optional and must not turn an otherwise valid boot into
        # a half-started process.  The scanner itself remains fail-closed.
        app.state.model_lifecycle_scheduler = None
        logger.warning('[S35-LIFECYCLE] scheduler failed to start (non-fatal): %s', exc)

    # --- [Tripwire 5 / R2] DomainKnowledgeStore baseline verification on startup ---
    try:
        from scp.knowledge.domain_store import DomainKnowledgeStore
        _kb_store = DomainKnowledgeStore()
        for _f in _kb_store.data_dir.glob("*.jsonl"):
            _kb_store.register_file(_f.name)
        _record_res = _kb_store.verify_all_baselines()
        _file_res = _kb_store.verify_all_file_baselines()
        if any(v.get("corrupted", 0) > 0 for v in _record_res.values()):
            logger.error("[domain_store] RECORD CORRUPTION DETECTED: %s", _record_res)
        else:
            logger.info("[domain_store] Baseline integrity verified across all domain files")
    except Exception as _e:
        logger.warning("[Lifespan] domain_store baseline verification skipped: %s", _e)

    # --- [Tripwire 5 / R2] ExternalTrustRoot baseline verification on startup ---
    try:
        from scp.meta.external_trust import get_external_trust_root
        _trust_root = get_external_trust_root()
        for _f in _trust_root.EXPECTED_FILES:
            if not _f.endswith("/"):
                _trust_root.register_file(_f)
        _trust_res = _trust_root.verify_all_baselines()
        if not all(_trust_res.values()):
            logger.error("[external_trust] TAMPER DETECTED: %s", _trust_res)
        else:
            logger.info("[external_trust] Baseline integrity verified across anchor files")
    except Exception as _e:
        logger.warning("[Lifespan] external_trust baseline verification skipped: %s", _e)

    yield
    app.state.judge_ready = False
    app.state.startup_status = 'stopping'
    app.state.readiness_reason = 'server_shutting_down'
    try:
        from scp.meta.external_trust import get_external_trust_root
        _et = get_external_trust_root('.')
        _et_result = _et.verify_external()
        if _et_result['passed']:
            logger.info('[GĂ„â€\x9aĂ‚Â\xa0 -\x9aĂ‚Â§8] External trust roots verified │Ă…â€œĂ¢â‚¬Â¦ (audit tests + CI/CD + constitution)')
        else:
            logger.warning(f"[GĂ„â€\x9aĂ‚Â\xa0 -\x9aĂ‚Â§8] External trust BROKEN │Ă¢â€\x9aÂ¬Ă¢â‚¬Â\x9d missing: {_et_result['missing']}, constitution_approved: {_et_result['constitution_approved']}. Server will start but external anchors are not intact.")
    except Exception as _et_err:
        logger.warning(f'[GĂ„â€\x9aĂ‚Â\xa0 -\x9aĂ‚Â§8] External trust verification failed: {_et_err}')
    os.environ['SCP_WHY_LLM_ENABLED'] = _orig_why_llm
    os.environ['SCP_EVOLUTION_AUTO'] = _orig_evo_auto
    logger.info(f'[STARTUP] WHY LLM + Evolution AUTO restored (why={_orig_why_llm}, evo={_orig_evo_auto})')
    for _stop_event in (getattr(app.state, 'deep_audit_stop', None), getattr(app.state, 'attack_monitor_stop', None), getattr(app.state, 'retry_policy_stop', None)):
        if _stop_event is not None:
            _stop_event.set()
    # [S23-DISCOVERY] Hủy scheduler sạch (cancel + await, no leak) trước khi
    # cancel các task bootstrap khác.
    _discovery = getattr(app.state, 'discovery_scheduler', None)
    if _discovery is not None:
        try:
            await _discovery.stop(timeout=5.0)
            logger.info('[S23-DISCOVERY] FreeDiscoveryScheduler stopped cleanly')
        except Exception as exc:
            logger.warning('[S23-DISCOVERY] scheduler stop failed (non-fatal): %s', exc)
    _model_lifecycle = getattr(app.state, 'model_lifecycle_scheduler', None)
    if _model_lifecycle is not None:
        try:
            await _model_lifecycle.stop(timeout=5.0)
            logger.info('[S35-LIFECYCLE] model lifecycle scheduler stopped cleanly')
        except Exception as exc:
            logger.warning('[S35-LIFECYCLE] scheduler stop failed (non-fatal): %s', exc)
    for _task in (_background_registry_monitor_task, _evolution_bootstrap_task, _startup_gate_task, _judge_launch_task):
        if _task is not None and (not _task.done()):
            _task.cancel()
    try:
        from scp.core.doubt_cron import get_doubt_cron
        get_doubt_cron(data_dir=os.environ.get('SCP_DATA_DIR', 'data')).stop()
    except Exception as exc:
        # [MACH1-FIX-8 / D6 fail-loudly] Shutdown must not swallow: a failed stop
        # leaves the doubt-cron thread running after the server is gone.
        logger.warning('[DOUBT] Cronjob of Doubt stop failed (non-fatal): %s', exc, exc_info=True)
    # Stop all background jobs registered in the global registry
    try:
        from scp.api.background_jobs import registry
        registry.stop_all(timeout=10.0)
        logger.info('All background jobs stopped')
    except Exception as exc:
        logger.warning('Error stopping background jobs: %s', exc)
    logger.info(f'{RELEASE_LABEL} API Server shutting down...')
