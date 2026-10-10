# ARC-04 Vế 2: Runtime Route Authentication Inspector Test
"""Comprehensive runtime inspection of all canonical FastAPI routes in scp.api_server:app.

Enforces that 100% of routes are either explicitly classified in the PUBLIC_ALLOWLIST
or protected by authoritative authentication mechanisms:
- Router-level dependencies (e.g. Depends(verify_admin), Depends(verify_jwt_token))
- Route-level dependencies (e.g. route.dependencies)
- Endpoint function parameter dependencies (Depends(...), Security(...), Header(...))
- Endpoint function implementation guards (e.g. _guard(), _require_admin(), token verification)
"""
from __future__ import annotations

import inspect
from typing import Any

from scp.api_server import app

PUBLIC_ALLOWLIST = frozenset({
    "/",
    "/health",
    "/health/detailed",
    "/readiness",
    "/ready",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/favicon.ico",
    "/v1/models",
    "/auth/token",
    "/dashboard",
    "/chat",
    "/v3/call/sessions/{call_id}/signal",
})

AUTH_KEYWORD_TOKENS = (
    "auth",
    "admin",
    "token",
    "jwt",
    "guard",
    "key",
    "bearer",
    "verify",
    "security",
    "credential",
)

FUNCTION_BODY_AUTH_TOKENS = (
    "_guard",
    "_require_admin",
    "verify_admin",
    "verify_jwt_token",
    "x_scp_pc_token",
    "x_scp_capability_token",
    "load_auth_config",
    "secrets.compare_digest",
)


def _walk_routes(app_or_router: Any, prefix: str = "", parent_deps: list | None = None) -> list[tuple[str, Any, list]]:
    """Recursively unwrap and flatten all routes and their inherited router dependencies."""
    if parent_deps is None:
        parent_deps = []
    collected = []

    for r in getattr(app_or_router, "routes", []):
        if hasattr(r, "original_router"):
            # Handle _IncludedRouter instances from APIRouter.include_router
            ctx = getattr(r, "include_context", None)
            sub_prefix = prefix + (getattr(ctx, "prefix", "") if ctx else "")
            ctx_deps = parent_deps + list(getattr(ctx, "dependencies", []) or [])
            collected.extend(_walk_routes(r.original_router, sub_prefix, ctx_deps))
        elif hasattr(r, "routes"):
            # Sub-router with routes attribute
            sub_prefix = prefix + getattr(r, "prefix", "")
            r_deps = parent_deps + list(getattr(r, "dependencies", []) or [])
            collected.extend(_walk_routes(r, sub_prefix, r_deps))
        else:
            path = prefix + getattr(r, "path", "")
            collected.append((path, r, list(parent_deps)))

    return collected


def _is_route_auth_protected(path: str, route: Any, parent_deps: list) -> tuple[bool, str]:
    """Inspect all layers of route definition for authentication controls."""
    # 1. Router-level & route-level declared dependencies
    all_declared_deps = list(parent_deps) + list(getattr(route, "dependencies", []) or [])
    dependant = getattr(route, "dependant", None)
    if dependant:
        all_declared_deps.extend(getattr(dependant, "dependencies", []) or [])

    for dep in all_declared_deps:
        call = getattr(dep, "dependency", dep)
        name = getattr(call, "__name__", str(call)).lower()
        if any(token in name for token in AUTH_KEYWORD_TOKENS):
            return True, f"declared_dependency:{name}"

    # 2. Security requirements in OpenAPI / dependant metadata
    if dependant and getattr(dependant, "security_requirements", None):
        return True, "security_requirements"

    # 3. Endpoint function parameters and implementation inspection
    endpoint = getattr(route, "endpoint", None)
    if endpoint and callable(endpoint):
        try:
            # Check function parameter names and explicit Depends/Security definitions
            sig = inspect.signature(endpoint)
            for param in sig.parameters.values():
                param_name = param.name.lower()
                if param_name in ("x_scp_pc_token", "x_scp_capability_token", "authorization", "api_key", "admin_token"):
                    return True, f"parameter_guard:{param.name}"
                param_default_str = str(param.default).lower()
                if ("depends(" in param_default_str or "security(" in param_default_str) and any(
                    token in param_default_str for token in ("auth", "admin", "jwt", "bearer", "verify")
                ):
                    return True, f"parameter_dependency:{param.name}"

            # Check function source code for inline auth verification
            src = inspect.getsource(endpoint).lower()
            for token in FUNCTION_BODY_AUTH_TOKENS:
                if token in src:
                    return True, f"function_body_guard:{token}"
        except (OSError, TypeError):
            pass

    return False, "unprotected"


def test_runtime_route_auth_audit_zero_unprotected_routes():
    """ARC-04 Vế 2: Verify that 100% of FastAPI routes are protected or in PUBLIC_ALLOWLIST."""
    routes = _walk_routes(app)
    assert len(routes) >= 180, f"Expected at least 180 total API routes, found {len(routes)}"

    unprotected_routes = []
    protected_count = 0
    allowlisted_count = 0

    for path, route, p_deps in routes:
        if path in PUBLIC_ALLOWLIST:
            allowlisted_count += 1
            continue

        is_protected, reason = _is_route_auth_protected(path, route, p_deps)
        if is_protected:
            protected_count += 1
        else:
            methods = getattr(route, "methods", None)
            unprotected_routes.append(f"{path} [{methods}] - {type(route).__name__}")

    assert not unprotected_routes, (
        f"ARC-04 VIOLATION: {len(unprotected_routes)} route(s) lack authentication enforcement:\n"
        + "\n".join(unprotected_routes)
    )
    assert protected_count > 0, "Expected positive protected route count"
    assert allowlisted_count > 0, "Expected positive allowlisted route count"


def test_public_allowlist_integrity_and_bounds():
    """Verify that PUBLIC_ALLOWLIST only contains necessary non-sensitive public endpoints."""
    # Ensure sensitive administration, execution, or mutation paths are never in public allowlist
    forbidden_prefixes = ("/v98", "/v100", "/v102", "/v104", "/v105", "/v106", "/v3/pc", "/v3/hands", "/v3/agent", "/admin")
    for public_path in PUBLIC_ALLOWLIST:
        for prefix in forbidden_prefixes:
            assert not public_path.startswith(prefix), f"Sensitive path {public_path} must not be in PUBLIC_ALLOWLIST"
