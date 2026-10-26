"""Helper implementations for scp/api_server.py (service identity, gate responses, detailed health)."""
from __future__ import annotations

import logging
import os
import sys as _sys
from functools import lru_cache
from pathlib import Path as _Path
from typing import Any

from scp.api_server_parts.helpers import AskRequest, AskResponse
from scp.core.release_identity import public_release_metadata
from scp.core.safe_process import safe_run

logger = logging.getLogger("scp.api")


@lru_cache(maxsize=1)
def get_python_module_count_label() -> str:
    """Dynamically count .py files in scp/ and return formatted label."""
    scp_dir = _Path(__file__).resolve().parent.parent
    count = sum(1 for p in scp_dir.rglob("*.py") if p.is_file())
    return f"{count}+ Python files"


def compute_scp_service_identity(cached_commit: str | None) -> tuple[dict[str, Any], str]:
    """Compute bounded runtime identity for local service/port verification."""
    _port = None
    for _arg in _sys.argv[1:]:
        if _arg.isdigit() and 1 <= int(_arg) <= 65535:
            _port = int(_arg)
            break
    if _port is None and "SCP_PORT" in os.environ:
        _raw_port = os.environ["SCP_PORT"]
        try:
            _port = int(_raw_port)
        except (TypeError, ValueError) as _port_err:
            _port_text = _raw_port if isinstance(_raw_port, str) else f"<{type(_raw_port).__name__}>"
            _port_preview = "".join(
                _ch if _ch.isprintable() else "?" for _ch in _port_text[:24]
            )
            logger.warning(
                "[MACH1-FIX-8] SCP_PORT is not an integer (%s, len=%d, preview=%r) — falling back to argv/default",
                type(_port_err).__name__, len(_port_text), _port_preview,
            )
    if _port is None:
        _port = 8000
    _mode = os.environ.get("SCP_MODE")
    if not _mode:
        _mode = "production" if _port == 8000 else "test" if _port == 8001 else "unknown"
    if cached_commit is None:
        _env_sha = os.environ.get("SCP_GIT_SHA", "").strip()
        if _env_sha and _env_sha != "unknown":
            _commit = _env_sha
        else:
            try:
                _res = safe_run(
                    ["git", "-C", str(_Path(__file__).resolve().parent.parent.parent), "rev-parse", "HEAD"],
                    timeout=2,
                    check=True,
                )
                _commit = _res.stdout.strip()
            except Exception:
                logger.warning('_scp_service_identity: Exception not handled', exc_info=True)
                _commit = "unknown"
        cached_commit = _commit or "unknown"
    identity = {
        "service_name": os.environ.get("SCP_SERVICE_NAME", "scp-backend"),
        "mode": _mode,
        "host": os.environ.get("SCP_HOST", "127.0.0.1"),
        "configured_port": _port,
        "pid": os.getpid(),
        "commit": cached_commit or "unknown",
    }
    return identity, (cached_commit or "unknown")


def build_kernel_gate_unavailable_response(req: AskRequest, exc: Exception) -> AskResponse:
    return AskResponse(
        verdict="FAIL",
        final_answer="[SCP: Answer withheld — Kernel gate unavailable]",
        confidence=0.0,
        domain=req.domain_override or req.domain or "general",
        falsification_status="KERNEL_GATE_UNAVAILABLE",
        governance_decision="KILL",
        v98_guard={"mode": "rag-verified", "readOnly": True, "security_blocked": True, "kernel_error": type(exc).__name__},
        v98_classification={"provenance": "kernel_gate", "evidence_count": 0},
        elapsed_ms=0.0,
        session_id=req.session_id or "ask-kernel-unavailable",
        run_status="REJECTED",
        ledger_status="BLOCKED",
    )


def build_kill_switch_blocked_response(req: AskRequest) -> AskResponse:
    return AskResponse(
        verdict="FAIL",
        final_answer="[SCP: Answer withheld — Kill switch engaged (POST /v3/pc/kill)]",
        confidence=0.0,
        domain=req.domain_override or req.domain or "general",
        falsification_status="KILL_SWITCH_ENGAGED",
        governance_decision="KILL",
        v98_guard={"mode": "rag-verified", "readOnly": True, "security_blocked": True, "kill_switch": "engaged"},
        v98_classification={"provenance": "kill_switch_gate", "evidence_count": 0},
        elapsed_ms=0.0,
        session_id=req.session_id or "ask-kill-switch-blocked",
        run_status="REJECTED",
        ledger_status="BLOCKED",
    )


def build_health_detailed_payload(
    *,
    app: Any,
    judge_fn: Any,
    version: str,
    multi_turn_tracker: Any,
    cross_language_learner: Any,
    fact_checker: Any,
    routing_stats_fn: Any,
) -> dict[str, Any]:
    try:
        judge = judge_fn()
        data_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data")
        db_size = 0
        if os.path.exists(data_dir):
            for filename in os.listdir(data_dir):
                filepath = os.path.join(data_dir, filename)
                if os.path.isfile(filepath):
                    db_size += os.path.getsize(filepath)
        _sched_started = getattr(app.state, "background_scheduler_started", False)
        from scp.security.os_sandbox import isolation_capability

        return {
            "status": "ok",
            "version": version,
            "release": public_release_metadata(),
            "domain_experts": len(judge.domain_experts),
            "slms": len(judge.domain_experts),
            "v98_modules": sum(1 for value in judge.get_v98_status().values() if value != "inactive"),
            "routes": len(app.routes),
            "modules": get_python_module_count_label(),
            "data_size_mb": round(db_size / (1024 * 1024), 2),
            "multi_turn_tracker": multi_turn_tracker.stats(),
            "cross_language": cross_language_learner.stats(),
            "fact_checker": fact_checker.stats(),
            "sandbox_capability": isolation_capability(),
            "runtime_routing": {
                "math_probe_route": list(judge._route_question("2+2")) if hasattr(judge, "_route_question") else ["math"],
                "domain_expert_loaded": "math" in getattr(judge, "domain_experts", {}),
                "math_slm_loaded": "math" in getattr(judge, "domain_experts", {}),
            },
            "question_routing": routing_stats_fn(),
            "background_scheduler_started": _sched_started,
        }
    except Exception as e:
        logger.debug(f"health_detailed ignored: {e}", exc_info=True)
        _sched_started = getattr(app.state, "background_scheduler_started", False)
        return {
            "status": "initializing",
            "version": version,
            "routes": len(app.routes),
            "error": type(e).__name__,
            "background_scheduler_started": _sched_started,
            "note": "judge init in progress — /health returns ok, /ask may be slow",
        }
