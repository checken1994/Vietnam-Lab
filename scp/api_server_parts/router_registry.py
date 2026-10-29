"""[ARC-01 2026-10-30] Đăng ký router tùy chọn tách khỏi ``scp.api_server``.

api_server.py ở 898 > pin EXACT 897 (tests/T00_integrity/test_meta_audit.py,
test_arc01_taskkernel_loc_ceiling); luật repo: tách file thay vì tăng pin.
Move-only: mỗi hàm giữ NGUYÊN thứ tự include_router, điều kiện route profile,
thông điệp log (cùng logger "scp.api") và ngữ nghĩa cờ ``_*_AVAILABLE`` của khối
gốc. api_server.py gọi từng hàm tại ĐÚNG vị trí khối cũ, nên thứ tự route trên
``app.routes`` (quyết định khớp path) không đổi.

Khối vòng lặp (stream/threat/.../history) và chat/v98 vẫn ở api_server.py:
tests/T03_capability/test_flow_14_reintegrated_systems_scp_standard.py đọc
nguồn api_server.py để chứng minh các router đó được mount.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

logger = logging.getLogger("scp.api")

RouteEnabled = Callable[[str], bool]


def mount_admin_extra_control_routers(app: Any, route_enabled: RouteEnabled) -> tuple[bool, bool]:
    """admin_knowledge + V102–V106/import/openai_compat + control. Trả (_EXTRA_ROUTERS_AVAILABLE, _CONTROL_ROUTES_AVAILABLE)."""
    try:
        from scp.api.admin_knowledge_routes import router as admin_knowledge_router
        if route_enabled("versioned_admin"):
            app.include_router(admin_knowledge_router)
    except ImportError as _ak_err:
        logger.warning("Admin knowledge router unavailable: %s", _ak_err)

    extra_available = False
    try:
        from scp.api.routes.evaluation_routes import router as evaluation_router
        from scp.api.routes.import_routes import router as import_router
        from scp.api.routes.openai_compat import router as openai_compat_router
        from scp.api.routes.swe_bench_routes import router as swe_bench_router
        from scp.api.routes.v102_v103_routes import router as v102_v103_router
        from scp.api.routes.v104_routes import router as v104_router
        from scp.api.routes.v105_routes import router as v105_router
        extra_available = True
    except ImportError as e:
        logger.warning("[Task 9-B] V102-V105/import routers unavailable: %s", e)

    if extra_available:
        if route_enabled("openai_compat"):
            app.include_router(openai_compat_router)
        if route_enabled("evaluation"):
            app.include_router(evaluation_router)
        if route_enabled("versioned_admin"):
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
        if route_enabled("import"):
            app.include_router(import_router)

    try:
        from scp.api.routes.control_routes import router as control_router
        if route_enabled("control"):
            app.include_router(control_router, tags=["control"])
            control_available = True
        else:
            control_available = False
    except ImportError as e:
        logger.warning("[SCP Control] Control router unavailable: %s", e)
        control_available = False
    return extra_available, control_available


def mount_webhook_pc_web_routers(app: Any, route_enabled: RouteEnabled) -> tuple[bool, bool, bool]:
    """webhook + pc_controller + web_control. Trả (_WEBHOOK_ROUTER_AVAILABLE, _PC_CONTROLLER_AVAILABLE, _WEB_CONTROL_AVAILABLE)."""
    try:
        from scp.api.webhook import router as webhook_router
        if route_enabled("webhook"):
            app.include_router(webhook_router)
            webhook_available = True
        else:
            webhook_available = False
    except ImportError as e:
        logger.warning("[Task 42-B] Webhook router unavailable: %s", e)
        webhook_available = False

    try:
        from scp.api.routes.pc_controller_routes import router as pc_controller_router
        if route_enabled("pc_controller"):
            app.include_router(pc_controller_router)
            pc_controller_available = True
        else:
            pc_controller_available = False
    except ImportError as e:
        logger.warning("[V3.1] PC Controller router unavailable: %s", e)
        pc_controller_available = False

    try:
        from scp.api.routes.web_control_routes import router as web_control_router
        if route_enabled("web_control"):
            app.include_router(web_control_router)
            web_control_available = True
        else:
            web_control_available = False
    except ImportError as e:
        logger.warning("[V3.1] Web control router unavailable: %s", e)
        web_control_available = False
    return webhook_available, pc_controller_available, web_control_available


def mount_trace_router(app: Any, route_enabled: RouteEnabled) -> bool:
    """trace router. Trả _TRACE_AVAILABLE."""
    try:
        from scp.api_server_parts._trace_impl import router as trace_router
        if route_enabled("trace"):
            app.include_router(trace_router, tags=["trace"])
            return True
        return False
    except ImportError as e:
        logger.warning("[SCP Trace] Trace router unavailable: %s", e)
        return False


def mount_hands_agent_call_routers(app: Any, route_enabled: RouteEnabled) -> tuple[bool, bool, bool]:
    """hands + batch_benchmark + agent + call. Trả (_HANDS_AVAILABLE, _AGENT_ORCHESTRATOR_AVAILABLE, _CALL_SIGNALING_AVAILABLE)."""
    try:
        from scp.api.routes.hands_routes import router as hands_router
        if route_enabled("hands"):
            app.include_router(hands_router)
        from scp.api.routes.batch_benchmark_routes import router as batch_benchmark_router
        if route_enabled("batch_benchmark"):
            app.include_router(batch_benchmark_router)
        hands_available = route_enabled("hands")
    except ImportError as e:
        logger.warning("[SCP Hands v3.2] Hands router unavailable: %s", e)
        hands_available = False

    try:
        from scp.api.routes.agent_routes import router as agent_router
        if route_enabled("agent"):
            app.include_router(agent_router)
            agent_available = True
        else:
            agent_available = False
    except ImportError as e:
        logger.warning("[SCP Agent] Agent orchestrator router unavailable: %s", e)
        agent_available = False

    try:
        from scp.api.routes.call_routes import router as call_router
        if route_enabled("call"):
            app.include_router(call_router)
            call_available = True
        else:
            call_available = False
    except ImportError as e:
        logger.warning("[SCP Call] Call signaling router unavailable: %s", e)
        call_available = False
    return hands_available, agent_available, call_available
