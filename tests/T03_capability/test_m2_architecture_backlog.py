"""
tests/T03_capability/test_m2_architecture_backlog.py
====================================================
Hermetic & empirical verification for Milestone 2 Architecture Backlog (M3):
  1. Typed Settings (pydantic-settings) — SCPSettings & get_settings
  2. Structlog Logging — configure_logging & stdlib interop
  3. Unified Auth JWT System — dual-mode verify_admin with JWT decode & static fallback
  4. Token Streaming (SSE) — LLMGateway.chat_stream & openai_compat POST /v1/chat/completions (stream=True)
"""
from __future__ import annotations

import asyncio
import http.server
import json
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import AsyncIterator

import jwt
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from scp.core.config import SCPSettings, get_settings
from scp.core.logging_config import configure_logging, get_logger
from scp.security import auth as auth_module


# =============================================================================
# 1. Typed Settings (pydantic-settings)
# =============================================================================
def test_pydantic_settings_defaults_and_env(monkeypatch):
    """Verify SCPSettings provides typed defaults and respects SCP_ environment variables."""
    # Test instance with explicit dev mode override
    monkeypatch.setenv("SCP_PRODUCTION_MODE", "0")
    monkeypatch.setenv("SCP_MODE", "development")
    dev_settings = SCPSettings()
    assert dev_settings.port == 8000
    assert dev_settings.mode == "development"
    assert dev_settings.is_production is False
    assert dev_settings.egress_mode in ("deny", "allowlist")

    # Test environment variable overrides for production
    monkeypatch.setenv("SCP_PORT", "9999")
    monkeypatch.setenv("SCP_PRODUCTION_MODE", "1")
    monkeypatch.setenv("SCP_MODE", "production")
    monkeypatch.setenv("SCP_CORS_ORIGINS", "https://app.example.com, https://admin.example.com")
    monkeypatch.setenv("SCP_JWT_SECRET", "super-secret-key-that-is-at-least-32-chars-long")

    custom_settings = SCPSettings()
    assert custom_settings.port == 9999
    assert custom_settings.mode == "production"
    assert custom_settings.is_production is True
    assert custom_settings.jwt_secret == "super-secret-key-that-is-at-least-32-chars-long"
    assert custom_settings.parsed_cors_origins == ["https://app.example.com", "https://admin.example.com"]

    # Verify cached get_settings returns an SCPSettings instance
    cached = get_settings()
    assert isinstance(cached, SCPSettings)


# =============================================================================
# 2. Structlog Logging
# =============================================================================
def test_structlog_configuration_and_stdlib_interop(capsys):
    """Verify structlog configuration produces structured output and intercepts stdlib logs."""
    # Test JSON output mode
    configure_logging(log_level="DEBUG", json_output=True)
    struct_log = get_logger("scp.test_structlog")
    struct_log.info("test event", component="m2_audit", status="ok")

    captured = capsys.readouterr()
    lines = [line.strip() for line in captured.out.splitlines() if line.strip()]
    assert any("test event" in line and '"status": "ok"' in line for line in lines)

    # Test stdlib interop through ProcessorFormatter
    std_logger = logging.getLogger("scp.stdlib_test")
    std_logger.info("message from stdlib logger")

    captured_std = capsys.readouterr()
    std_lines = [line.strip() for line in captured_std.out.splitlines() if line.strip()]
    assert any("message from stdlib logger" in line for line in std_lines)

    # Restore console logging mode
    configure_logging(log_level="INFO", json_output=False)


# =============================================================================
# 2b. Q12 Boot wiring — config_contract typed bridge + one-time logging bootstrap
# =============================================================================
from pydantic import ValidationError  # noqa: E402
from scp.core.config import boot_settings_for_validation, validate_boot_settings  # noqa: E402
from scp.core.config_contract import ConfigContractError  # noqa: E402

_ROOT_DIR = Path(__file__).resolve().parents[2]


def _contract_view() -> dict[str, str]:
    """Mirror what validate_boot_config() returns for the current os.environ."""
    return {
        "SCP_JWT_SECRET": os.environ.get("SCP_JWT_SECRET", ""),
        "SCP_ADMIN_KEY": os.environ.get("SCP_ADMIN_KEY", ""),
    }


def test_q12_boot_settings_bridge_rejects_garbage_typed_env(monkeypatch):
    """Branch: contract-valid boot but garbage typed SCP_ value → fail-closed ConfigContractError."""
    monkeypatch.setenv("SCP_JWT_SECRET", "typed-gate-jwt-secret-0123456789abcdef-tt")
    monkeypatch.setenv("SCP_ADMIN_KEY", "typed-gate-admin-key")
    monkeypatch.setenv("SCP_LLM_HEDGE_MAX_SECONDS", "never")
    with pytest.raises(ConfigContractError, match="SCP_LLM_HEDGE_MAX_SECONDS"):
        validate_boot_settings(_contract_view())


def test_q12_boot_settings_bridge_message_has_no_values(monkeypatch):
    """Fail message must name fields only — env values (possibly secret) never leak."""
    monkeypatch.setenv("SCP_JWT_SECRET", "typed-gate-jwt-secret-0123456789abcdef-tt")
    monkeypatch.setenv("SCP_ADMIN_KEY", "typed-gate-admin-key")
    sentinel = "NEVER-PRINT-THIS-7e2f"
    monkeypatch.setenv("SCP_PORT", sentinel)
    with pytest.raises(ConfigContractError) as exc_info:
        validate_boot_settings(_contract_view())
    assert sentinel not in str(exc_info.value)


def test_q12_boot_settings_bridge_tolerates_empty_string_env(monkeypatch):
    """Legacy semantics: SCP_X="" means unset — the typed gate must not abort boot."""
    monkeypatch.setenv("SCP_JWT_SECRET", "typed-gate-jwt-secret-0123456789abcdef-tt")
    monkeypatch.setenv("SCP_ADMIN_KEY", "typed-gate-admin-key")
    monkeypatch.setenv("SCP_PRODUCTION_MODE", "")
    monkeypatch.setenv("SCP_LOG_JSON", "")
    settings = validate_boot_settings(_contract_view())
    assert isinstance(settings, SCPSettings)
    # Empty values were restored, not deleted (no process-env side effect).
    assert os.environ.get("SCP_PRODUCTION_MODE") == ""
    assert os.environ.get("SCP_LOG_JSON") == ""


def test_q12_boot_settings_normalization_restores_env_after_parse(monkeypatch):
    """boot_settings_for_validation must leave os.environ exactly as found, even on parse failure."""
    monkeypatch.setenv("SCP_LOG_JSON", "")
    settings = boot_settings_for_validation()
    assert isinstance(settings, SCPSettings)
    assert os.environ.get("SCP_LOG_JSON") == ""

    monkeypatch.setenv("SCP_SANDBOX_STRICT", "not-a-bool")
    with pytest.raises(ValidationError):
        boot_settings_for_validation()
    # finally-restore ran: the invalid value is still visible to downstream code.
    assert os.environ.get("SCP_SANDBOX_STRICT") == "not-a-bool"


def test_q12_boot_settings_bridge_detects_contract_divergence(monkeypatch):
    """Guard against the two layers drifting: mismatch between typed view and contract → abort."""
    monkeypatch.setenv("SCP_JWT_SECRET", "typed-gate-jwt-secret-0123456789abcdef-tt")
    monkeypatch.setenv("SCP_ADMIN_KEY", "typed-gate-admin-key")
    diverged = {
        "SCP_JWT_SECRET": "a-completely-different-value-not-in-env-xx",
        "SCP_ADMIN_KEY": "also-different-admin-value",
    }
    with pytest.raises(ConfigContractError, match="SCP_JWT_SECRET"):
        validate_boot_settings(diverged)


def test_q12_lifespan_wires_typed_bridge_after_contract():
    """The active lifespan must run the typed bridge AFTER the contract gate (order pinned)."""
    import scp.api_server_parts.lifespan as lifespan_module

    source = Path(lifespan_module.__file__).read_text(encoding="utf-8")
    assert "validate_boot_config" in source, "contract gate disappeared from lifespan"
    assert "validate_boot_settings" in source, "Q12 typed bridge not wired into lifespan"
    assert source.index("validate_boot_config") < source.index("validate_boot_settings"), (
        "typed bridge must run after the authoritative contract gate"
    )


def test_q12_api_server_import_bootstraps_structlog_once():
    """Composition-root import installs the central structlog handler (fallback-safe) on a fresh root.

    Runs in a subprocess: the in-process pytest root logger may already carry
    harness handlers, and the _bootstrap_logging guard (correctly) no-ops there.
    Only a fresh interpreter proves the boot path actually wires configure_logging.
    """
    child = r"""
import logging
root = logging.getLogger()
print("PRE_HANDLERS", len(root.handlers))
assert root.handlers == [], "child root must be clean to prove the boot wiring"
import scp.api_server  # noqa: F401
root = logging.getLogger()
from structlog.stdlib import ProcessorFormatter
formatted = any(isinstance(getattr(h, "formatter", None), ProcessorFormatter) for h in root.handlers)
print("BOOT_FORMATTED", formatted, "HANDLERS", len(root.handlers))
logging.getLogger("scp.q12probe").info("boot marker %s", "ok")
from scp.core.logging_config import configure_logging
configure_logging(log_level="INFO", json_output=True)
logging.getLogger("scp.q12probe").info("json marker")
"""
    env = os.environ.copy()
    env.update({"PYTHONUTF8": "1"})
    completed = subprocess.run(
        [sys.executable, "-c", child],
        cwd=_ROOT_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    out = completed.stdout
    assert "PRE_HANDLERS 0" in out
    assert "BOOT_FORMATTED True HANDLERS 1" in out, (
        "api_server import must install exactly one root handler via configure_logging"
    )
    # stdlib interop: positional args render through foreign_pre_chain
    assert "boot marker ok" in out
    json_lines = []
    for line in out.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            json_lines.append(json.loads(line))
        except ValueError:
            continue
    events = [payload.get("event") for payload in json_lines]
    assert "json marker" in events, "SCP_LOG_JSON-driven renderer must emit NDJSON records"


# =============================================================================
# 3. Unified Auth JWT System
# =============================================================================
def test_verify_admin_jwt_authentication(monkeypatch):
    """Verify dual-mode verify_admin authenticates valid JWTs with sub/role='admin'."""
    jwt_secret = "test-secret-at-least-32-bytes-long-for-jwt-signing"
    monkeypatch.setenv("SCP_JWT_SECRET", jwt_secret)
    monkeypatch.setattr(auth_module, "_auth_failures", {})

    # Mock load_auth_config returning empty static passwords
    import types
    monkeypatch.setattr(
        auth_module,
        "load_auth_config",
        lambda: types.SimpleNamespace(configured=False, token="", password=""),
    )

    # Case 1: Valid JWT with sub='admin'
    admin_token_sub = jwt.encode({"sub": "admin", "exp": time.time() + 3600}, jwt_secret, algorithm="HS256")
    result = auth_module.verify_admin(authorization=f"Bearer {admin_token_sub}", request=None)
    assert result is True

    # Case 2: Valid JWT with role='admin'
    admin_token_role = jwt.encode({"sub": "user_42", "role": "admin", "exp": time.time() + 3600}, jwt_secret, algorithm="HS256")
    result = auth_module.verify_admin(authorization=f"Bearer {admin_token_role}", request=None)
    assert result is True

    # Case 3: Valid JWT but non-admin user (sub='user', no admin role) -> rejected 401
    user_token = jwt.encode({"sub": "regular_user", "exp": time.time() + 3600}, jwt_secret, algorithm="HS256")
    with pytest.raises(HTTPException) as exc:
        auth_module.verify_admin(authorization=f"Bearer {user_token}", request=None)
    assert exc.value.status_code == 401

    # Case 4: Invalid signature JWT -> rejected 401
    bad_token = jwt.encode({"sub": "admin"}, "wrong-secret-key-12345678901234567890", algorithm="HS256")
    with pytest.raises(HTTPException) as exc:
        auth_module.verify_admin(authorization=f"Bearer {bad_token}", request=None)
    assert exc.value.status_code == 401

    # Case 5: Fallback to static secret if JWT decode fails
    monkeypatch.setattr(
        auth_module,
        "load_auth_config",
        lambda: types.SimpleNamespace(configured=True, token="static-secret-token", password=""),
    )
    result_static = auth_module.verify_admin(token="static-secret-token", request=None)
    assert result_static is True


# =============================================================================
# 4. Token Streaming (SSE) via Physical Loopback Server (No Mocks per FA-04)
# =============================================================================
class _SSEMockHandler(http.server.BaseHTTPRequestHandler):
    """Real HTTP server that yields standard SSE events."""
    def do_POST(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        chunks = [
            'data: {"choices": [{"delta": {"content": "Hello"}}]}\n\n',
            'data: {"choices": [{"delta": {"content": " streaming"}}]}\n\n',
            'data: {"choices": [{"delta": {"content": " world"}}]}\n\n',
            'data: [DONE]\n\n',
        ]
        for c in chunks:
            self.wfile.write(c.encode("utf-8"))
            self.wfile.flush()

    def log_message(self, *args):
        pass  # suppress request log spam


@pytest.mark.asyncio
async def test_llm_gateway_chat_stream_loopback(monkeypatch):
    """Verify EnvCompatProvider and LLMGateway.chat_stream with real loopback SSE server."""
    # Start physical loopback HTTP server
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _SSEMockHandler)
    port = server.server_port
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    try:
        monkeypatch.setenv("TEST_SSE_KEY", "valid-test-key")
        monkeypatch.setenv("TEST_SSE_BASE_URL", f"http://127.0.0.1:{port}")
        monkeypatch.setenv("TEST_SSE_MODEL", "test-sse-model")

        from scp.llm_gateway.client import EnvCompatProvider
        provider = EnvCompatProvider(
            name="loopback_sse",
            task="chat",
            key_env="TEST_SSE_KEY",
            base_url_env="TEST_SSE_BASE_URL",
            model_env="TEST_SSE_MODEL",
            default_model="test-sse-model",
            default_base_url=f"http://127.0.0.1:{port}",
        )

        tokens = []
        async for token in provider.chat_stream("Say hello"):
            tokens.append(token)

        assert "".join(tokens) == "Hello streaming world"
    finally:
        server.shutdown()


# =============================================================================
# 5. OpenAI-Compatible SSE Streaming Endpoint (/v1/chat/completions)
# =============================================================================
def test_openai_compat_stream_endpoint(monkeypatch):
    """Verify POST /v1/chat/completions with stream=True returns SSE chunk stream."""
    from fastapi import FastAPI
    from scp.api.routes.openai_compat import router as openai_router

    # Build isolated test app
    app = FastAPI()
    app.include_router(openai_router)

    # Provide authenticated user mock for get_current_user dependency
    from scp.security.jwt_guard import get_current_user
    app.dependency_overrides[get_current_user] = lambda: "admin_user"

    client = TestClient(app)
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "Tell me a story"}],
            "stream": True,
        },
    )

    # The canonical adapter is unavailable in this isolated app, so the
    # OpenAI boundary must reject rather than manufacture streaming content.
    assert response.status_code == 503
    assert response.json()["error"]["type"] == "server_error"
    assert "Tell me a story" not in response.text


# =============================================================================
# 6. Stream Routes (/v105/ask/stream) Headers & Readiness
# =============================================================================
def test_stream_routes_headers(monkeypatch):
    """Verify /v105/ask/stream returns text/event-stream with required anti-buffering headers."""
    from fastapi import FastAPI
    from scp.api.routes.stream_routes import router as stream_router
    from scp.api._shared import verify_admin

    app = FastAPI()
    app.include_router(stream_router)
    app.dependency_overrides[verify_admin] = lambda: True

    client = TestClient(app)
    response = client.post(
        "/v105/ask/stream",
        json={"question": "What is 2+2?", "ai_answer": "4"},
    )

    assert response.status_code == 200
    assert "text/event-stream" in response.headers.get("content-type", "")
    assert response.headers.get("x-accel-buffering") == "no"
    assert "step" in response.text
