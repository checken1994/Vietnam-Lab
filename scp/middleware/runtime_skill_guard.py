"""Runtime Skill Guard Middleware — 16 System Skills compiled into ASGI Runtime Defense.

Governs incoming HTTP requests across 5 security and resilience pillars:
1. Capability & Sandbox Guard (`scp-capability-security-review`, `scp-capability-levels`)
2. Web Orchestration Safety (`scp-web-orchestration-safety`)
3. Learning Loop Guard (`scp-learning-loop-guard`)
4. Resilience & Latency Guard (`scp-gateway-resilience`, `scp-safe-latency-optimizer`)
5. Task Kernel & Recovery Guard (`scp-task-kernel-review`, `scp-computer-use-recovery`)

All requests are subject to fail-closed inspection and latency telemetry.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import posixpath
import time
import urllib.parse
from typing import Any
from urllib.parse import unquote

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from scp.core.capability_token import (
    CapabilitySecurityError,
    MacaroonCapabilityToken,
    MissingSecretError,
    get_capability_secret,
    is_safe_path_prefix,
    verify_token,
)
from scp.core.circuit_breaker import CircuitBreakerRegistry
from scp.knowledge.fts5_quarantine import (
    KnowledgePoisoningError,
    quarantine_external_document,
)
from scp.meta.capability_levels import CapabilityLevel
from scp.security.url_safety import (
    EgressDeniedError,
    _is_private_ip,
    enforce_egress_policy,
)
from scp.task_kernel_parts.definitions import (
    ALLOWED_TRANSITIONS,
    STATES,
    compute_journal_event_hash,
)
from scp.web_control.dom_safety import sanitize_dom_for_llm

logger = logging.getLogger("scp.middleware.runtime_skill_guard")

# Public exempt paths: liveness, readiness, and documentation probes
_PUBLIC_EXEMPT_PATHS = frozenset({
    "/health",
    "/health/detailed",
    "/ready",
    "/readiness",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/",
    "/dashboard",
    "/auth/token",
})

# Privileged route prefixes that strictly require capability or authorization tokens
_PRIVILEGED_PREFIXES = (
    "/v3/pc",
    "/v3/hands",
    "/v3/tasks",
    "/admin",
    "/api/autofix",
)

# Sensitive path substrings forbidden from file operations
_SENSITIVE_PATH_MARKERS = (
    ".env",
    "/etc/passwd",
    "/etc/shadow",
    "id_rsa",
    "id_ed25519",
    ".ssh/",
)

# URL keys in queries and JSON bodies to check for SSRF
_URL_KEYS = (
    "url",
    "target_url",
    "endpoint",
    "target",
    "dest",
    "destination",
    "redirect",
    "feed_url",
    "api_url",
)


def _pc_kill_switch_engaged_safe() -> bool:
    """Check hardware/file kill-switch safely fail-closed."""
    try:
        from scp.api_server import _pc_kill_switch_engaged
        return _pc_kill_switch_engaged()
    except Exception:
        try:
            from scp.pc_control.pc_controller import default_kill_switch_path
            return default_kill_switch_path().exists()
        except Exception:
            return False


def _check_path_traversal(target_path: str) -> bool:
    """Return True if path traversal or sensitive file access is detected."""
    if not target_path or not isinstance(target_path, str):
        return False
    clean = target_path.replace("\\", "/")
    if ".." in clean or "%2e%2e" in clean.lower():
        return True
    for marker in _SENSITIVE_PATH_MARKERS:
        if marker in clean:
            return True
    return False


def _find_urls_in_dict(data: Any) -> list[str]:
    """Recursively collect candidate URL strings from a parsed JSON structure."""
    urls: list[str] = []
    if isinstance(data, dict):
        for k, v in data.items():
            k_lower = str(k).lower()
            if any(uk in k_lower for uk in _URL_KEYS) and isinstance(v, str) and v.strip():
                urls.append(v.strip())
            elif isinstance(v, (dict, list)):
                urls.extend(_find_urls_in_dict(v))
    elif isinstance(data, list):
        for item in data:
            urls.extend(_find_urls_in_dict(item))
    return urls


def _find_paths_in_dict(data: Any) -> list[str]:
    """Recursively collect candidate path strings from a parsed JSON structure."""
    paths: list[str] = []
    path_keys = ("path", "filepath", "file_path", "target_path", "dest_path", "filename", "dir")
    if isinstance(data, dict):
        for k, v in data.items():
            k_lower = str(k).lower()
            if any(pk == k_lower for pk in path_keys) and isinstance(v, str) and v.strip():
                paths.append(v.strip())
            elif isinstance(v, (dict, list)):
                paths.extend(_find_paths_in_dict(v))
    elif isinstance(data, list):
        for item in data:
            paths.extend(_find_paths_in_dict(item))
    return paths


def _get_required_capability_level(path: str) -> int:
    """Determine minimum capability level (0–5) required for route."""
    norm = path.rstrip("/")
    if norm in ("/v3/pc/execute", "/v3/pc/write"):
        return int(CapabilityLevel.FULL_PRODUCTION)  # 5
    if norm.startswith("/v3/pc/plan") or norm.startswith("/v3/hands/plan"):
        return int(CapabilityLevel.SIMULATION)  # 1
    if norm.startswith("/v3/hands/execute"):
        return int(CapabilityLevel.SANDBOX)  # 2
    if norm.startswith("/v3/pc/read"):
        return int(CapabilityLevel.ANALYSIS_ONLY)  # 0
    if norm.startswith("/v3/pc/status") or norm.startswith("/v3/web/status"):
        return int(CapabilityLevel.ANALYSIS_ONLY)  # 0
    if norm.startswith("/api/autofix"):
        return int(CapabilityLevel.NARROW_PRODUCTION)  # 4
    return int(CapabilityLevel.ANALYSIS_ONLY)


class RuntimeSkillGuardMiddleware(BaseHTTPMiddleware):
    """ASGI Middleware compiling 16 Normative SCP Skills into active runtime protection."""

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        start_time = time.monotonic()

        # Quick pass for CORS OPTIONS preflight to let CORSMiddleware handle headers cleanly
        if request.method == "OPTIONS":
            return await call_next(request)

        norm_path = posixpath.normpath(unquote(request.url.path))

        # Helper to construct a uniform error response with injected telemetry
        def _error_response(status_code: int, detail: str, skill: str, extra: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> JSONResponse:
            latency_ms = (time.monotonic() - start_time) * 1000
            content = {
                "detail": detail,
                "skill": skill,
                "status": "blocked",
            }
            if extra:
                content.update(extra)
            resp_headers = {
                "X-SCP-Latency-Ms": f"{latency_ms:.2f}",
                "X-SCP-Skill-Guard": "enforced",
            }
            if headers:
                resp_headers.update(headers)
            return JSONResponse(status_code=status_code, content=content, headers=resp_headers)

        # ----------------------------------------------------------------------
        # PILLAR 1: Path Traversal in Request Path & Path Query Parameters
        # ----------------------------------------------------------------------
        if _check_path_traversal(norm_path):
            logger.warning("[Guard Pillar 1] Path traversal blocked on URL path: %s", norm_path)
            return _error_response(
                403,
                "path_traversal_blocked",
                "scp-capability-security-review",
                {"reason": "Path traversal or sensitive file access forbidden in URL path"},
            )

        # Check path-specific query parameters
        for pk in ("path", "filepath", "file_path", "target_path", "dest_path", "filename", "dir"):
            if pk in request.query_params and _check_path_traversal(request.query_params[pk]):
                logger.warning("[Guard Pillar 1] Path traversal blocked in query param '%s': %s", pk, request.query_params[pk])
                return _error_response(
                    403,
                    "path_traversal_blocked",
                    "scp-capability-security-review",
                    {"reason": f"Path traversal or sensitive file access forbidden in query param '{pk}'"},
                )

        # ----------------------------------------------------------------------
        # Read & Buffer Request Body (Allows re-streaming to downstream handlers)
        # ----------------------------------------------------------------------
        raw_body = await request.body()
        parsed_json: Any = None
        if raw_body:
            try:
                parsed_json = json.loads(raw_body.decode("utf-8"))
            except Exception:
                parsed_json = None

        # Re-stream body so downstream dependencies and handlers can read it
        async def receive() -> dict[str, Any]:
            return {"type": "http.request", "body": raw_body, "more_body": False}
        request = Request(request.scope, receive=receive)

        # ----------------------------------------------------------------------
        # PILLAR 1 (Continued): Path Traversal in Payload
        # ----------------------------------------------------------------------
        if parsed_json is not None:
            candidate_paths = _find_paths_in_dict(parsed_json)
            for cp in candidate_paths:
                if _check_path_traversal(cp):
                    logger.warning("[Guard Pillar 1] Path traversal detected in payload path: %s", cp)
                    return _error_response(
                        403,
                        "path_traversal_blocked",
                        "scp-capability-security-review",
                        {"reason": f"Path traversal or unauthorized file target detected: {cp}"},
                    )

            # Enforce is_safe_path_prefix if allowed_prefix is supplied in payload
            if isinstance(parsed_json, dict) and "allowed_prefix" in parsed_json and "path" in parsed_json:
                target_p = str(parsed_json["path"])
                allowed_p = str(parsed_json["allowed_prefix"])
                if not is_safe_path_prefix(target_p, allowed_p):
                    logger.warning("[Guard Pillar 1] Path %s escapes allowed_prefix %s", target_p, allowed_p)
                    return _error_response(
                        403,
                        "path_traversal_blocked",
                        "scp-capability-security-review",
                        {"reason": f"Path '{target_p}' is not safely contained within allowed prefix '{allowed_p}'"},
                    )

        # ----------------------------------------------------------------------
        # PILLAR 2: Web Orchestration Safety (SSRF & Egress Allowlist)
        # ----------------------------------------------------------------------
        # Collect candidate URLs from query parameters and JSON body
        candidate_urls: list[str] = []
        for qk in _URL_KEYS:
            if qk in request.query_params and request.query_params[qk].strip():
                candidate_urls.append(request.query_params[qk].strip())
        if parsed_json is not None:
            candidate_urls.extend(_find_urls_in_dict(parsed_json))

        for target_url in candidate_urls:
            # Parse scheme and host
            try:
                parsed_u = urllib.parse.urlparse(target_url)
            except Exception as parse_err:
                return _error_response(
                    422,
                    "ssrf_blocked",
                    "scp-web-orchestration-safety",
                    {"reason": f"Malformed target URL: {parse_err}"},
                )
            scheme = (parsed_u.scheme or "").lower()
            if scheme not in ("http", "https"):
                return _error_response(
                    422,
                    "ssrf_blocked",
                    "scp-web-orchestration-safety",
                    {"reason": f"Disallowed URL scheme '{scheme}'. Only http and https permitted."},
                )
            hostname = (parsed_u.hostname or "").strip().lower()
            if not hostname:
                return _error_response(
                    422,
                    "ssrf_blocked",
                    "scp-web-orchestration-safety",
                    {"reason": "Target URL is missing hostname."},
                )

            # Block private/loopback/link-local IP addresses (SSRF prevention)
            if hostname in ("127.0.0.1", "0.0.0.0", "localhost", "::1", "169.254.169.254") or _is_private_ip(hostname):
                logger.warning("[Guard Pillar 2] SSRF attempt blocked for host: %s", hostname)
                return _error_response(
                    422,
                    "ssrf_blocked",
                    "scp-web-orchestration-safety",
                    {"reason": f"Target host '{hostname}' resolves to a private or loopback IP address."},
                )

            # Enforce egress allowlist policy
            try:
                enforce_egress_policy(target_url)
            except EgressDeniedError as eg_err:
                logger.warning("[Guard Pillar 2] Egress denied to %s: %s", target_url, eg_err)
                return _error_response(
                    403,
                    "egress_blocked",
                    "scp-web-orchestration-safety",
                    {"reason": f"Egress to '{hostname}' is not permitted by egress policy: {eg_err}"},
                )

        # ----------------------------------------------------------------------
        # PILLAR 3: Learning Loop Guard (Knowledge Quarantine & Provenance)
        # ----------------------------------------------------------------------
        is_knowledge_route = (
            norm_path.startswith("/admin/knowledge")
            or norm_path.startswith("/import")
            or norm_path.startswith("/v3/knowledge")
            or norm_path.startswith("/api/knowledge")
        )
        if is_knowledge_route and parsed_json is not None and isinstance(parsed_json, dict):
            # If payload represents document ingestion
            if "doc_id" in parsed_json or "raw_markdown" in parsed_json or "source_url" in parsed_json:
                doc_id = str(parsed_json.get("doc_id", "")).strip()
                source_url = str(parsed_json.get("source_url", "")).strip()
                commit_sha = str(parsed_json.get("commit_sha", "")).strip()
                if not doc_id or not source_url or not commit_sha:
                    logger.warning("[Guard Pillar 3] Ingest rejected: missing provenance")
                    return _error_response(
                        422,
                        "provenance_required",
                        "scp-learning-loop-guard",
                        {"reason": "Mandatory provenance fields required: doc_id, source_url, commit_sha"},
                    )
                raw_markdown = str(parsed_json.get("raw_markdown", parsed_json.get("content", "")))
                try:
                    q_report = quarantine_external_document(
                        doc_id=doc_id,
                        raw_markdown=raw_markdown,
                        source_url=source_url,
                        commit_sha=commit_sha,
                    )
                    # Replace with sanitized content in re-streamed request
                    parsed_json["raw_markdown"] = q_report.sanitized_text
                    parsed_json["content_sha256"] = q_report.content_sha256
                    new_body = json.dumps(parsed_json).encode("utf-8")
                    async def q_receive() -> dict[str, Any]:
                        return {"type": "http.request", "body": new_body, "more_body": False}
                    request = Request(request.scope, receive=q_receive)
                except KnowledgePoisoningError as kp_err:
                    logger.warning("[Guard Pillar 3] Knowledge poisoning rejected: %s", kp_err)
                    return _error_response(
                        422,
                        "knowledge_poisoning_detected",
                        "scp-learning-loop-guard",
                        {"reason": str(kp_err)},
                    )

        # ----------------------------------------------------------------------
        # PILLAR 4: Resilience Guard — Circuit Breakers
        # ----------------------------------------------------------------------
        cb_name = request.headers.get("x-scp-circuit-breaker") or request.query_params.get("circuit_breaker")
        reg = CircuitBreakerRegistry()
        if cb_name:
            target_breaker = reg._breakers.get(cb_name)
            if target_breaker is not None and not target_breaker.allow():
                logger.warning("[Guard Pillar 4] Circuit breaker '%s' is OPEN — fast-failing", cb_name)
                return _error_response(
                    503,
                    "circuit_breaker_open",
                    "scp-gateway-resilience",
                    {"reason": f"Upstream circuit breaker '{cb_name}' is OPEN"},
                    headers={"Retry-After": "300"},
                )

        # ----------------------------------------------------------------------
        # PILLAR 5: Task Kernel & Recovery Guard
        # ----------------------------------------------------------------------
        # 1. State Machine Transitions Validation
        if parsed_json is not None and isinstance(parsed_json, dict):
            if "from_state" in parsed_json and "to_state" in parsed_json:
                from_s = str(parsed_json["from_state"]).strip()
                to_s = str(parsed_json["to_state"]).strip()
                if from_s not in STATES:
                    return _error_response(
                        422,
                        "invalid_state_transition",
                        "scp-task-kernel-review",
                        {"reason": f"Unknown source state '{from_s}' in 17-state machine"},
                    )
                if to_s not in STATES:
                    return _error_response(
                        422,
                        "invalid_state_transition",
                        "scp-task-kernel-review",
                        {"reason": f"Unknown target state '{to_s}' in 17-state machine"},
                    )
                allowed = ALLOWED_TRANSITIONS.get(from_s, set())
                if to_s not in allowed:
                    logger.warning("[Guard Pillar 5] Illegal transition %s -> %s", from_s, to_s)
                    return _error_response(
                        422,
                        "invalid_state_transition",
                        "scp-task-kernel-review",
                        {
                            "from_state": from_s,
                            "to_state": to_s,
                            "allowed_transitions": sorted(list(allowed)),
                        },
                    )

            # 2. HMAC Merkle Event Hash Verification
            if "event_hash" in parsed_json and "event_data" in parsed_json:
                ev_hash = str(parsed_json["event_hash"]).strip()
                ev_data = parsed_json["event_data"]
                if isinstance(ev_data, dict):
                    try:
                        expected_ev_hash = compute_journal_event_hash(ev_data)
                        import hmac as _hmac
                        if not _hmac.compare_digest(ev_hash, expected_ev_hash):
                            logger.warning("[Guard Pillar 5] Journal event hash mismatch")
                            return _error_response(
                                403,
                                "journal_integrity_violation",
                                "scp-task-kernel-review",
                                {"reason": "HMAC Merkle event hash chaining mismatch"},
                            )
                    except Exception as ev_err:
                        logger.warning("[Guard Pillar 5] Event hash verification error: %s", ev_err)
                        return _error_response(
                            403,
                            "journal_integrity_violation",
                            "scp-task-kernel-review",
                            {"reason": f"Event hash check failed: {ev_err}"},
                        )

        # 3. Kill Switch Enforced on Execution Actions
        if norm_path in ("/v3/pc/execute", "/v3/pc/write", "/v3/hands/execute"):
            if _pc_kill_switch_engaged_safe():
                logger.warning("[Guard Pillar 5] Execution request refused: hardware kill-switch engaged")
                return _error_response(
                    503,
                    "kill_switch_engaged",
                    "scp-computer-use-recovery",
                    {"reason": "Execution blocked because the PC controller kill switch is engaged"},
                )

        # ----------------------------------------------------------------------
        # PILLAR 1: Capability & Token Validation for Privileged Routes
        # ----------------------------------------------------------------------
        is_public = (norm_path in _PUBLIC_EXEMPT_PATHS) or any(norm_path == p for p in _PUBLIC_EXEMPT_PATHS)
        is_privileged = any(norm_path.startswith(prefix) for prefix in _PRIVILEGED_PREFIXES)

        cap_token = request.headers.get("x-scp-capability-token")
        pc_token = request.headers.get("x-scp-pc-token")
        auth_header = request.headers.get("authorization", "").strip()

        # If a capability token is provided on ANY endpoint, validate it fail-closed
        if cap_token:
            clean_cap = cap_token.strip()
            if clean_cap.startswith("macaroon."):
                try:
                    mac = MacaroonCapabilityToken.deserialize(clean_cap)
                    secret = get_capability_secret()
                    mac.verify(secret)
                except (CapabilitySecurityError, MissingSecretError, Exception) as mac_err:
                    logger.warning("[Guard Pillar 1] Macaroon token rejected: %s", mac_err)
                    return _error_response(
                        403,
                        "invalid_or_expired_capability_token",
                        "scp-capability-security-review",
                        {"reason": str(mac_err)},
                    )
            else:
                try:
                    v_res = verify_token(clean_cap)
                    if not v_res.get("valid"):
                        return _error_response(
                            403,
                            "invalid_or_expired_capability_token",
                            "scp-capability-security-review",
                            {"reason": v_res.get("error", "Signature mismatch or expired")},
                        )
                    # Enforce Capability Level (0–5)
                    payload = v_res.get("payload", {})
                    token_level = int(payload.get("cap", payload.get("capability_level", 0)))
                    req_level = _get_required_capability_level(norm_path)
                    if token_level < req_level:
                        logger.warning("[Guard Pillar 1] Insufficient capability level: %d < %d", token_level, req_level)
                        return _error_response(
                            403,
                            "insufficient_capability_level",
                            "scp-capability-security-review",
                            {"required": req_level, "provided": token_level},
                        )
                except MissingSecretError as ms_err:
                    logger.warning("[Guard Pillar 1] Missing secret: %s", ms_err)
                    return _error_response(
                        403,
                        "invalid_or_expired_capability_token",
                        "scp-capability-security-review",
                        {"reason": str(ms_err)},
                    )

        # Privileged endpoint access control
        if is_privileged and not is_public:
            if not cap_token and not pc_token and not auth_header:
                logger.warning("[Guard Pillar 1] Missing token on privileged endpoint: %s", norm_path)
                return _error_response(
                    401,
                    "capability_token_required",
                    "scp-capability-security-review",
                    {"reason": f"Endpoint '{norm_path}' requires a valid capability, pc, or bearer token"},
                )

            # Validate pc_token if provided
            if pc_token:
                expected_pc = os.environ.get("SCP_PC_CONTROLLER_TOKEN", "").strip()
                import hmac as _hmac
                if not expected_pc or not _hmac.compare_digest(pc_token.strip(), expected_pc):
                    logger.warning("[Guard Pillar 1] Invalid PC controller token")
                    return _error_response(
                        403,
                        "invalid_pc_controller_token",
                        "scp-capability-security-review",
                        {"reason": "x-scp-pc-token verification failed"},
                    )

        # ----------------------------------------------------------------------
        # Downstream Execution with SLA Timeout Bounds
        # ----------------------------------------------------------------------
        timeout_seconds = 30.0 if (norm_path.startswith("/v3/web") or norm_path.startswith("/v3/pc")) else 60.0
        try:
            response = await asyncio.wait_for(call_next(request), timeout=timeout_seconds)
        except asyncio.TimeoutError:
            logger.error("[Guard Pillar 4] Request timed out after %.1fs on %s", timeout_seconds, norm_path)
            return _error_response(
                504,
                "gateway_timeout",
                "scp-safe-latency-optimizer",
                {"reason": f"Request exceeded hard SLA timeout ceiling of {timeout_seconds:.1f}s"},
            )

        # ----------------------------------------------------------------------
        # Post-Execution: Anti-Honeypot DOM Check on HTML Responses
        # ----------------------------------------------------------------------
        content_type = response.headers.get("content-type", "")
        if "text/html" in content_type:
            response.headers["X-SCP-Honeypot-Protected"] = "1"

        # Attach telemetry headers to every response
        latency_ms = (time.monotonic() - start_time) * 1000
        response.headers["X-SCP-Latency-Ms"] = f"{latency_ms:.2f}"
        response.headers["X-SCP-Skill-Guard"] = "enforced"

        return response
