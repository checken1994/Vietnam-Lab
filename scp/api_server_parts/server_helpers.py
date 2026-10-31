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


def get_operational_mode_and_providers() -> tuple[str, list[str]]:
    """Return (operational_mode, active_providers) from the LLM Gateway."""
    try:
        from scp.llm_gateway import get_gateway
        gw = get_gateway()
        return gw.operational_mode(), gw.get_active_provider_names()
    except Exception as e:
        logger.debug("get_operational_mode_and_providers error: %s", e, exc_info=True)
        return "local_verified", []


def get_llm_status() -> dict[str, Any]:
    """[C1] Trạng thái LLM trung thực (configured != usable) từ gateway.
    Lỗi probe => state 'unknown' (không bịa 'ok')."""
    try:
        from scp.llm_gateway import get_gateway
        return get_gateway().llm_status()
    except Exception as e:
        logger.debug("get_llm_status error: %s", e, exc_info=True)
        return {"state": "unknown", "judge": {"state": "unknown", "reason": type(e).__name__}}


def llm_readiness_checks() -> dict[str, str]:
    """[C1] Mục LLM cho /ready: nhãn trạng thái + mã lý do judge. KHÔNG đổi mã
    HTTP của /ready (chế độ local_verified không-LLM là hợp lệ) nhưng không còn
    im lặng khi provider cấu hình mà model chết. Không lộ model id/key."""
    st = get_llm_status()
    out = {"llm_providers": str(st.get("state", "unknown")), "llm_judge": str(st["judge"].get("state", "unknown"))}
    if st["judge"].get("reason"):
        out["llm_judge_reason"] = str(st["judge"]["reason"])
    out.update(semantic_l1_readiness_checks())
    return out


def monitor_liveness_checks() -> dict[str, str]:
    """[HEARTBEAT-RECOVERY 2026-10-31] Liveness THẬT của các monitor nền cho /ready.

    Trước: attack monitor + deep audit chết (DB heartbeat hỏng) mà /ready vẫn "ready",
    không mục nào lộ ra. Nay mỗi monitor đã ``declare_cycle`` báo
    live / pending_first_cycle / degraded:<lý do> (chu kỳ hoàn tất gần nhất so với ngân
    sách N nhịp, storage). KHÔNG đổi mã HTTP/``status`` của /ready (phục vụ /ask vẫn
    được) nhưng ``checks.monitors="degraded"`` không còn im lặng."""
    try:
        from scp.core.subsystem_telemetry import registered_monitors

        monitors = registered_monitors()
    except Exception as e:  # noqa: BLE001 — /ready không được sập vì telemetry
        return {"monitors": "unknown", "monitors_reason": f"status_error:{type(e).__name__}"}
    if not monitors:
        return {"monitors": "none_registered"}
    out: dict[str, str] = {}
    degraded = False
    for name, telemetry in sorted(monitors.items()):
        try:
            live = telemetry.liveness()
        except Exception as e:  # noqa: BLE001
            live = {"state": "DEGRADED", "reason": f"liveness_error:{type(e).__name__}"}
        state = str(live.get("state", "UNKNOWN"))
        out[f"monitor_{name}"] = state.lower() + (f":{live['reason']}" if live.get("reason") else "")
        degraded = degraded or state == "DEGRADED"
    out["monitors"] = "degraded" if degraded else "ok"
    return out


def semantic_l1_readiness_checks() -> dict[str, str]:
    """[L1-MODEL 2026-10-31] Trạng thái THẬT của L1 semantic router cho /ready.

    Thử nạp L1 (verify lock + sha256 mọi artifact + onnxruntime) đúng một lần rồi
    cache trạng thái; unavailable kèm mã lý do (vd model_file_missing,
    artifact_sha256_mismatch, onnxruntime_not_installed). Không lộ model id.
    KHÔNG đổi mã HTTP của /ready: L1 chết ⇒ router fail-closed (không có đồng
    thuận CHATBOT), không phải lỗi sẵn sàng của dịch vụ."""
    try:
        from scp.runtime.semantic_router.lineage_embed import readiness

        st = readiness()
    except Exception as e:  # noqa: BLE001 — /ready không được sập vì L1
        st = {"state": "unavailable", "reason": f"status_error:{type(e).__name__}"}
    out = {"semantic_l1": "ok" if st.get("state") == "ready" else "unavailable"}
    if out["semantic_l1"] != "ok":
        out["semantic_l1_reason"] = str(st.get("reason") or "unknown")
    return out


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
    # [SEC-FIX 2026-10-27 · F5 audit af88fd31] Trước đây message ghi CỨNG
    # "(POST /v3/pc/kill)" — sai nguồn khi kill switch được bật qua kênh khác
    # (file flag data/pc_controller/KILL_SWITCH, env SCP_KILL_SWITCH, kernel
    # global kill). Nay đọc nguồn engage thật từ authority hợp nhất
    # scp/security/kill_switch.py (engaged_channel_label — fail-closed, không
    # raise) để withheld message ghi ĐÚNG kênh đã kích hoạt kill.
    from scp.security.kill_switch import engaged_channel_label

    return AskResponse(
        verdict="FAIL",
        final_answer=(
            "[SCP: Answer withheld — Kill switch engaged "
            f"({engaged_channel_label()})]"
        ),
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
        op_mode, active_providers = get_operational_mode_and_providers()

        return {
            "status": "ok",
            "version": version,
            "release": public_release_metadata(),
            "operational_mode": op_mode,
            "active_llm_providers": active_providers,
            "llm_status": get_llm_status(),
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


def build_minimal_health_payload(
    *,
    service_identity: dict,
    version: str,
    release: dict,
    routes_count: int,
) -> dict:
    """[ARC-01 2026-10-28] Payload /health tối giản tách khỏi api_server.py —
    commit ba3c37c5 (operational_mode + active_llm_providers) đẩy file lên 900
    > pin 897; luật repo: tách file thay vì tăng pin. api_server truyền vào
    identity/version/release/routes (sống ở api_server), helpers này trả payload
    hoàn toàn giống shape cũ — 0 behavior change (test
    tests/T05_gateway/test_multi_provider_transparency.py pin)."""
    op_mode, active_providers = get_operational_mode_and_providers()
    return {
        "status": "ok",
        "service_identity": service_identity,
        "version": version,
        "release": release,
        "operational_mode": op_mode,
        "active_llm_providers": active_providers,
        "routes": routes_count,
        "modules": get_python_module_count_label(),
        "note": "minimal health — use /health/detailed for full status",
    }
