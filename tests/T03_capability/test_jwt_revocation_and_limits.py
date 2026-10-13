"""Tests for SCP-06 & M-09: JWT jti tracking, token revocation, and body payload limits."""
import os
import jwt
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from scp.security.jwt_guard import (
    create_access_token,
    verify_jwt_token,
    revoke_token,
    is_token_revoked,
    _REVOKED_JTIS,
    JWT_ALGORITHM,
    _get_jwt_secret,
)
from scp.auth.jwt_handler import (
    create_access_token as auth_create_token,
    verify_jwt_token as auth_verify_token,
    revoke_token as auth_revoke_token,
    is_token_revoked as auth_is_revoked,
)


@pytest.fixture(autouse=True)
def setup_jwt_env(monkeypatch, tmp_path):
    monkeypatch.setenv("SCP_JWT_SECRET", "test-secret-key-for-jwt-revocation-testing-32chars")
    rev_file = tmp_path / "revoked.txt"
    monkeypatch.setenv("SCP_REVOKED_TOKENS_FILE", str(rev_file))
    _REVOKED_JTIS.clear()
    yield
    _REVOKED_JTIS.clear()


def test_jwt_create_token_includes_unique_jti():
    """SCP-06: create_access_token automatically includes a unique jti identifier."""
    token1 = create_access_token({"sub": "admin1"})
    token2 = create_access_token({"sub": "admin2"})

    payload1 = jwt.decode(token1, _get_jwt_secret(), algorithms=[JWT_ALGORITHM])
    payload2 = jwt.decode(token2, _get_jwt_secret(), algorithms=[JWT_ALGORITHM])

    assert "jti" in payload1
    assert "jti" in payload2
    assert payload1["jti"] != payload2["jti"]


def test_jwt_token_revocation_fails_verification():
    """SCP-06 / M-09: Revoking a token's jti blocks it fail-closed upon verification."""
    token = create_access_token({"sub": "test_user"})
    payload = verify_jwt_token(token)
    assert payload["sub"] == "test_user"

    jti = payload["jti"]
    assert not is_token_revoked(jti)

    # Revoke token
    revoke_token(jti)
    assert is_token_revoked(jti)

    with pytest.raises(HTTPException) as exc_info:
        verify_jwt_token(token)
    assert exc_info.value.status_code == 401
    assert "revoked" in str(exc_info.value.detail).lower()


def test_jwt_auth_handler_reexport_parity():
    """SCP-06: scp.auth.jwt_handler maintains full parity with jwt_guard."""
    token = auth_create_token({"sub": "reexport_user"})
    payload = auth_verify_token(token)
    assert payload["sub"] == "reexport_user"
    assert "jti" in payload

    jti = payload["jti"]
    assert not auth_is_revoked(jti)

    auth_revoke_token(jti)
    assert auth_is_revoked(jti)

    with pytest.raises(HTTPException) as exc_info:
        auth_verify_token(token)
    assert exc_info.value.status_code == 401


def test_jwt_persistent_file_revocation(tmp_path, monkeypatch):
    """M-09: Revoked token identifiers persist to file and survive in-memory cache clear."""
    rev_file = tmp_path / "persisted_revoked.txt"
    monkeypatch.setenv("SCP_REVOKED_TOKENS_FILE", str(rev_file))

    token = create_access_token({"sub": "persistent_user"})
    payload = jwt.decode(token, _get_jwt_secret(), algorithms=[JWT_ALGORITHM])
    jti = payload["jti"]

    revoke_token(jti)
    assert rev_file.exists()
    assert jti in rev_file.read_text(encoding="utf-8")

    # Clear memory cache
    _REVOKED_JTIS.clear()

    # Must still recognize revocation from file
    assert is_token_revoked(jti)
    with pytest.raises(HTTPException):
        verify_jwt_token(token)


def test_chat_completions_payload_limit_enforced():
    """SCP-06 / M-09: /v1/chat/completions rejects requests with Content-Length > 2MB."""
    from scp.api_server import app
    client = TestClient(app)
    headers = {
        "Content-Length": str(3 * 1024 * 1024),
        "Content-Type": "application/json",
        "Authorization": f"Bearer {create_access_token({'sub': 'test'})}",
    }
    resp = client.post("/v1/chat/completions", headers=headers, content=b'{"model":"test"}')
    assert resp.status_code == 413


def test_import_jsonl_payload_limit_enforced(monkeypatch):
    """SCP-06 / M-09: /import/jsonl rejects requests with Content-Length > 5MB."""
    admin_token = "admin-secret-token-32-chars-long!"
    monkeypatch.setenv("SCP_AUTH_TOKEN_SECRET", admin_token)
    from scp.api_server import app
    client = TestClient(app)
    headers = {
        "Content-Length": str(6 * 1024 * 1024),
        "Content-Type": "application/json",
        "Authorization": f"Bearer {admin_token}",
    }
    resp = client.post("/import/jsonl", headers=headers, content=b'{"question":"test"}')
    assert resp.status_code == 413
