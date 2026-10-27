"""[P3-06 2026-10-28] jwt_guard kênh FILE (*_FILE) — zero-mock, fail-closed.

Khảo sát (grep repo) mọi biến secret jwt_guard đọc:
  * SCP_JWT_SECRET — ``_get_jwt_secret`` qua ``secret_registry.resolve``
    (từ P3-06 registry khai báo ``file_variant=True`` ⇒ kênh
    SCP_JWT_SECRET_FILE được công nhận).
  * SCP_ADMIN_KEY / SCP_AUTH_TOKEN_SECRET / SCP_SCHEDULER_ADMIN_TOKEN —
    ``verify_api_key``, đọc qua ``resolve()`` (cả ba đã được registry khai
    báo ``file_variant=True`` từ HC-08).
  * SCP_API_KEY — ``verify_api_key``, NGOÀI registry ⇒ env-only (giữ nguyên).

Contract pin (docstring ``scp/security/secret_registry.py``, HC-08):
  * ``{name}_FILE`` set (khác rỗng) → FILE thắng env.
  * File hỏng/không tồn tại/rỗng → ``None`` + log ERROR (không bao giờ log
    giá trị), KHÔNG fallback về env.
  * jwt_guard fail-closed: thiếu secret ký ⇒ RuntimeError chặn request.

Zero-mock (FA-04): chỉ tmp_path + monkeypatch.setenv/delenv + jwt thật.
"""
from __future__ import annotations

import logging

import jwt as pyjwt
import pytest
from fastapi import HTTPException

from scp.security import jwt_guard
from scp.security.jwt_guard import JWT_ALGORITHM

_REGISTRY_LOGGER = "scp.security.secret_registry"

_GUARD_ENV_VARS = (
    "SCP_JWT_SECRET",
    "SCP_JWT_SECRET_FILE",
    "SCP_ADMIN_KEY",
    "SCP_ADMIN_KEY_FILE",
    "SCP_AUTH_TOKEN_SECRET",
    "SCP_AUTH_TOKEN_SECRET_FILE",
    "SCP_SCHEDULER_ADMIN_TOKEN",
    "SCP_SCHEDULER_ADMIN_TOKEN_FILE",
    "SCP_API_KEY",
)

_FILE_JWT_SECRET = "file-jwt-secret-0123456789abcdef0123456789abcdef"


@pytest.fixture(autouse=True)
def _clean_guard_secret_env(monkeypatch):
    """Dọn mọi kênh env/file-env của secret jwt_guard đọc (hermetic vs .env host)."""
    for name in _GUARD_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    yield


class TestJwtSecretFileChannel:
    """Kênh SCP_JWT_SECRET_FILE của _get_jwt_secret/create_access_token."""

    def test_only_file_channel_signs_and_verifies(self, tmp_path, monkeypatch):
        """(a) Chỉ có *_FILE → guard dùng đúng giá trị từ file (sign/verify thật)."""
        secret_file = tmp_path / "jwt-secret"
        secret_file.write_text(_FILE_JWT_SECRET + "\n", encoding="utf-8")
        monkeypatch.setenv("SCP_JWT_SECRET_FILE", str(secret_file))

        assert jwt_guard._get_jwt_secret() == _FILE_JWT_SECRET
        token = jwt_guard.create_access_token({"sub": "p3-06-file-user"}, expires_delta=60)
        # Verify bằng ĐÚNG secret đọc từ file (đường kiểm thực thật, không assert trống)
        payload = pyjwt.decode(token, _FILE_JWT_SECRET, algorithms=[JWT_ALGORITHM])
        assert payload["sub"] == "p3-06-file-user"

    def test_file_wins_over_env_residue(self, tmp_path, monkeypatch):
        """Precedence pin HC-08: cả hai kênh set → FILE thắng, env là residue."""
        secret_file = tmp_path / "jwt-secret"
        secret_file.write_text(_FILE_JWT_SECRET + "\n", encoding="utf-8")
        monkeypatch.setenv("SCP_JWT_SECRET", "env-jwt-residue-secret-should-lose")
        monkeypatch.setenv("SCP_JWT_SECRET_FILE", str(secret_file))

        assert jwt_guard._get_jwt_secret() == _FILE_JWT_SECRET
        token = jwt_guard.create_access_token({"sub": "precedence-user"}, expires_delta=60)
        # Token phải ký bằng secret FILE: decode với env-residue phải FAIL
        with pytest.raises(pyjwt.InvalidTokenError):
            pyjwt.decode(token, "env-jwt-residue-secret-should-lose", algorithms=[JWT_ALGORITHM])

    def test_missing_file_fails_closed_no_env_fallback(self, tmp_path, monkeypatch, caplog):
        """(b) *_FILE trỏ file không tồn tại → RuntimeError rõ ràng, KHÔNG fallback env."""
        monkeypatch.setenv("SCP_JWT_SECRET", "env-jwt-residue-secret")
        monkeypatch.setenv("SCP_JWT_SECRET_FILE", str(tmp_path / "does-not-exist"))

        with caplog.at_level(logging.ERROR, logger=_REGISTRY_LOGGER):
            with pytest.raises(RuntimeError, match="SCP_JWT_SECRET is not set"):
                jwt_guard._get_jwt_secret()

        errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
        assert any("SCP_JWT_SECRET_FILE" in msg for msg in errors), (
            "Registry phải log ERROR nêu rõ kênh FILE hỏng (nguyên nhân gốc)"
        )
        # Secrecy invariant: không bao giờ log giá trị secret
        assert all("env-jwt-residue-secret" not in msg for msg in errors)

    def test_empty_file_fails_closed(self, tmp_path, monkeypatch):
        """File rỗng (chỉ whitespace) → fail rõ, không coi là 'có secret'."""
        secret_file = tmp_path / "jwt-secret-empty"
        secret_file.write_text("   \n", encoding="utf-8")
        monkeypatch.setenv("SCP_JWT_SECRET_FILE", str(secret_file))

        with pytest.raises(RuntimeError, match="SCP_JWT_SECRET is not set"):
            jwt_guard._get_jwt_secret()

    def test_env_only_behaviour_unchanged(self, monkeypatch):
        """(c) Hành vi cũ chỉ-env vẫn nguyên: env → dùng được; thiếu → RuntimeError."""
        env_secret = "env-jwt-secret-value-legacy-0123456789abcdef"
        monkeypatch.setenv("SCP_JWT_SECRET", env_secret)
        assert jwt_guard._get_jwt_secret() == env_secret
        token = jwt_guard.create_access_token({"sub": "env-user"}, expires_delta=60)
        payload = pyjwt.decode(token, env_secret, algorithms=[JWT_ALGORITHM])
        assert payload["sub"] == "env-user"

        monkeypatch.delenv("SCP_JWT_SECRET", raising=False)
        with pytest.raises(RuntimeError, match="SCP_JWT_SECRET is not set"):
            jwt_guard._get_jwt_secret()


class TestVerifyApiKeyFileChannel:
    """verify_api_key tôn trọng kênh *_FILE của các secret trong registry."""

    def test_admin_key_via_file_channel(self, tmp_path, monkeypatch):
        """Chỉ có SCP_ADMIN_KEY_FILE → key từ file được chấp nhận (role admin)."""
        key_file = tmp_path / "admin-key"
        key_file.write_text("file-admin-key-abcdef0123456789\n", encoding="utf-8")
        monkeypatch.setenv("SCP_ADMIN_KEY_FILE", str(key_file))

        payload = jwt_guard.verify_api_key("file-admin-key-abcdef0123456789")
        assert payload == {"sub": "admin", "role": "admin", "auth_type": "api_key"}

    def test_scheduler_token_via_file_channel(self, tmp_path, monkeypatch):
        """Chỉ có SCP_SCHEDULER_ADMIN_TOKEN_FILE → key từ file được chấp nhận."""
        key_file = tmp_path / "scheduler-token"
        key_file.write_text("file-scheduler-token-xyz987\n", encoding="utf-8")
        monkeypatch.setenv("SCP_SCHEDULER_ADMIN_TOKEN_FILE", str(key_file))

        payload = jwt_guard.verify_api_key("file-scheduler-token-xyz987")
        assert payload == {"sub": "admin", "role": "admin", "auth_type": "api_key"}

    def test_broken_admin_key_file_rejects_env_residue(self, tmp_path, monkeypatch):
        """File hỏng + env residue → 401 (fail-closed, không fallback env)."""
        monkeypatch.setenv("SCP_ADMIN_KEY", "env-admin-key-residue")
        monkeypatch.setenv("SCP_ADMIN_KEY_FILE", str(tmp_path / "gone"))

        with pytest.raises(HTTPException) as excinfo:
            jwt_guard.verify_api_key("env-admin-key-residue")
        assert excinfo.value.status_code == 401

    def test_admin_key_env_only_unchanged(self, monkeypatch):
        """(c) cho verify_api_key: chỉ-env vẫn nhận admin key như cũ."""
        monkeypatch.setenv("SCP_ADMIN_KEY", "env-admin-key-legacy")
        payload = jwt_guard.verify_api_key("env-admin-key-legacy")
        assert payload["sub"] == "admin"

    def test_api_key_env_only_no_file_channel(self, tmp_path, monkeypatch):
        """SCP_API_KEY ngoài registry ⇒ env-only: *_FILE không có tác dụng."""
        key_file = tmp_path / "api-key"
        key_file.write_text("file-api-key-not-a-channel\n", encoding="utf-8")
        monkeypatch.setenv("SCP_API_KEY_FILE", str(key_file))

        with pytest.raises(HTTPException) as excinfo:
            jwt_guard.verify_api_key("file-api-key-not-a-channel")
        assert excinfo.value.status_code == 401

        # Cùng giá trị đó qua env SCP_API_KEY thì được (kênh được công nhận)
        monkeypatch.setenv("SCP_API_KEY", "file-api-key-not-a-channel")
        payload = jwt_guard.verify_api_key("file-api-key-not-a-channel")
        assert payload == {"sub": "user", "role": "user", "auth_type": "api_key"}
