"""SEC-04: CORS Fail-Closed in Production Contract Tests.

Verifies that when SCP is running in production mode (SCP_PRODUCTION_MODE=1
or SCP_ENV=production), CORS origins fail-closed to an empty list unless
explicitly configured via SCP_CORS_ORIGINS. Development mode retains the
localhost fallback.
"""
from __future__ import annotations

import pytest
from scp.api_server import _compute_cors_origins


def test_cors_production_mode_unset_origins_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """SEC-04: In production mode (SCP_PRODUCTION_MODE=1), unset CORS origins must fail-closed to []."""
    monkeypatch.setenv("SCP_PRODUCTION_MODE", "1")
    monkeypatch.delenv("SCP_ENV", raising=False)
    monkeypatch.delenv("SCP_CORS_ORIGINS", raising=False)
    assert _compute_cors_origins() == []


def test_cors_scp_env_production_unset_origins_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """SEC-04: In production mode (SCP_ENV=production), unset CORS origins must fail-closed to []."""
    monkeypatch.delenv("SCP_PRODUCTION_MODE", raising=False)
    monkeypatch.setenv("SCP_ENV", "production")
    monkeypatch.delenv("SCP_CORS_ORIGINS", raising=False)
    assert _compute_cors_origins() == []


def test_cors_production_empty_origins_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """SEC-04: Empty string in SCP_CORS_ORIGINS under production must fail-closed to []."""
    monkeypatch.setenv("SCP_PRODUCTION_MODE", "1")
    monkeypatch.setenv("SCP_CORS_ORIGINS", "   ")
    assert _compute_cors_origins() == []


def test_cors_production_explicit_origins_honored(monkeypatch: pytest.MonkeyPatch) -> None:
    """SEC-04: Explicitly configured origins in production must be parsed and allowed."""
    monkeypatch.setenv("SCP_PRODUCTION_MODE", "1")
    monkeypatch.setenv("SCP_CORS_ORIGINS", "https://app.example.com, https://admin.example.com")
    assert _compute_cors_origins() == [
        "https://app.example.com",
        "https://admin.example.com",
    ]


def test_cors_development_mode_fallback_to_localhost(monkeypatch: pytest.MonkeyPatch) -> None:
    """SEC-04: Development mode without SCP_CORS_ORIGINS falls back to http://localhost:3000."""
    monkeypatch.setenv("SCP_PRODUCTION_MODE", "0")
    monkeypatch.setenv("SCP_ENV", "development")
    monkeypatch.delenv("SCP_CORS_ORIGINS", raising=False)
    assert _compute_cors_origins() == ["http://localhost:3000"]


def test_cors_development_mode_explicit_origins_honored(monkeypatch: pytest.MonkeyPatch) -> None:
    """SEC-04: Development mode with explicit origins uses the configured origins."""
    monkeypatch.setenv("SCP_PRODUCTION_MODE", "0")
    monkeypatch.setenv("SCP_ENV", "development")
    monkeypatch.setenv("SCP_CORS_ORIGINS", "http://localhost:5173, http://127.0.0.1:5173")
    assert _compute_cors_origins() == [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]


def test_cors_middleware_production_strips_access_control_allow_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    """SEC-04: Empirical test verifying CORSMiddleware does not return allow-origin in production."""
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    from starlette.testclient import TestClient

    monkeypatch.setenv("SCP_PRODUCTION_MODE", "1")
    monkeypatch.delenv("SCP_CORS_ORIGINS", raising=False)

    test_app = FastAPI()
    test_app.add_middleware(
        CORSMiddleware,
        allow_origins=_compute_cors_origins(),
        allow_methods=["GET"],
        allow_headers=["*"],
    )

    @test_app.get("/ping")
    def ping():
        return {"ok": True}

    client = TestClient(test_app)
    response = client.get("/ping", headers={"Origin": "http://localhost:3000"})
    assert "access-control-allow-origin" not in response.headers

