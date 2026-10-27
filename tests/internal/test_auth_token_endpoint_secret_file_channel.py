"""[R17 2026-10-28] /auth/token (scp/api_server.py) tôn trọng kênh FILE — zero-mock, fail-closed.

Hiện trạng trước R17: endpoint ``login_for_access_token`` đọc ``SCP_ADMIN_KEY``
trực tiếp từ ``os.environ``, bỏ qua kênh ``SCP_ADMIN_KEY_FILE`` (docker
secrets) dù registry đã khai báo ``file_variant=True`` (HC-08); jwt_guard
(P3-06) đã route qua ``secret_registry.resolve()`` từ wave trước. R17 đưa
endpoint về cùng pattern ``jwt_guard.verify_api_key``.

Contract pin (docstring scp/security/secret_registry.py, HC-08):
  * ``{name}_FILE`` set (khác rỗng) → FILE thắng env.
  * File hỏng/không tồn tại/rỗng → ``None`` + log ERROR (không bao giờ log
    giá trị), KHÔNG fallback về env ⇒ endpoint 401 như key chưa được cấp.
  * Chỉ-env vẫn hoạt động như cũ (resolve fallback env).

Zero-mock (FA-04): chỉ tmp_path + monkeypatch.setenv/delenv + app thật
(FastAPI TestClient) + JWT verify thật (pyjwt.decode). Rate limiter được
reset storage trong fixture theo pattern
tests/T03_capability/test_m09_comprehensive_rate_limiting.py — quota 5/phút
của /auth/token không phải đơn vị dưới test ở đây (được pin riêng tại M-09).
"""
from __future__ import annotations

import logging

import jwt as pyjwt
import pytest
from fastapi.testclient import TestClient

from scp.api.rate_limiting import limiter
from scp.api_server import app
from scp.security.jwt_guard import JWT_ALGORITHM

_REGISTRY_LOGGER = "scp.security.secret_registry"

# Endpoint dưới test đọc SCP_ADMIN_KEY (qua resolve) và SCP_JWT_SECRET
# (create_access_token → _get_jwt_secret, cũng qua resolve từ P3-06).
_ENDPOINT_ENV_VARS = (
    "SCP_ADMIN_KEY",
    "SCP_ADMIN_KEY_FILE",
    "SCP_JWT_SECRET",
    "SCP_JWT_SECRET_FILE",
)

# ≥32 ký tự theo min_length khai báo của SCP_JWT_SECRET trong registry.
_JWT_SECRET = "r17-test-jwt-secret-0123456789abcdef0123456789abcdef"


@pytest.fixture(autouse=True)
def _hermetic_auth_token_env(monkeypatch):
    """Dọn mọi kênh env/file-env (hermetic vs .env host có SCP_ADMIN_KEY và
    SCP_JWT_SECRET thật), cấp JWT secret xác định cho happy-path, và reset
    storage rate limiter in-memory để mỗi test có đủ quota như chạy riêng."""
    for name in _ENDPOINT_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SCP_JWT_SECRET", _JWT_SECRET)
    limiter.reset()
    yield
    limiter.reset()


class TestAuthTokenEndpointFileChannel:
    """POST /auth/token tôn trọng kênh SCP_ADMIN_KEY_FILE của registry."""

    def test_env_only_admin_key_still_mints_token(self, monkeypatch):
        """(1) Chỉ-env giữ nguyên hành vi cũ: đúng key → 200 + JWT thật."""
        monkeypatch.setenv("SCP_ADMIN_KEY", "r17-env-admin-key-legacy-value")
        client = TestClient(app)

        resp = client.post("/auth/token", json={"admin_key": "r17-env-admin-key-legacy-value"})
        assert resp.status_code == 200, (
            f"env-only phải nhận đúng key như trước R17, got {resp.status_code}: {resp.text}"
        )
        token = resp.json()["access_token"]
        # Đường kiểm thực thật: token phải ký bằng đúng JWT secret đã cấu hình
        payload = pyjwt.decode(token, _JWT_SECRET, algorithms=[JWT_ALGORITHM])
        assert payload["sub"] == "admin"

        # Sai key → 401 fail-closed
        resp_bad = client.post("/auth/token", json={"admin_key": "r17-wrong-key"})
        assert resp_bad.status_code == 401

        # Chưa cấu hình key nào → 401 (STEP0-FIX fail-closed giữ nguyên)
        monkeypatch.delenv("SCP_ADMIN_KEY", raising=False)
        resp_unconfigured = client.post("/auth/token", json={"admin_key": "r17-env-admin-key-legacy-value"})
        assert resp_unconfigured.status_code == 401

    def test_file_channel_wins_over_env_residue(self, tmp_path, monkeypatch):
        """(2) env + SCP_ADMIN_KEY_FILE → FILE thắng: key từ file được nhận,
        key env (residue) bị từ chối (precedence HC-08)."""
        key_file = tmp_path / "admin-key"
        key_file.write_text("r17-file-admin-key-docker-secret\n", encoding="utf-8")
        monkeypatch.setenv("SCP_ADMIN_KEY", "r17-env-residue-must-lose")
        monkeypatch.setenv("SCP_ADMIN_KEY_FILE", str(key_file))
        client = TestClient(app)

        resp_file = client.post("/auth/token", json={"admin_key": "r17-file-admin-key-docker-secret"})
        assert resp_file.status_code == 200, (
            f"key từ kênh FILE phải được nhận, got {resp_file.status_code}: {resp_file.text}"
        )

        resp_env = client.post("/auth/token", json={"admin_key": "r17-env-residue-must-lose"})
        assert resp_env.status_code == 401, "env residue phải thua kênh FILE"

    def test_broken_file_fails_closed_no_env_fallback(self, tmp_path, monkeypatch, caplog):
        """(3) FILE trỏ file hỏng (không tồn tại / rỗng) → 401 rõ ràng dù env
        có key hợp lệ — KHÔNG fallback env lặng lẽ; registry log ERROR nêu
        kênh hỏng và không bao giờ log giá trị secret."""
        env_key = "r17-env-key-would-work-if-fallback"
        monkeypatch.setenv("SCP_ADMIN_KEY", env_key)
        client = TestClient(app)

        # (3a) File không tồn tại
        monkeypatch.setenv("SCP_ADMIN_KEY_FILE", str(tmp_path / "does-not-exist"))
        with caplog.at_level(logging.ERROR, logger=_REGISTRY_LOGGER):
            resp_missing = client.post("/auth/token", json={"admin_key": env_key})
        assert resp_missing.status_code == 401, "file hỏng phải fail-closed 401, không fallback env"
        assert resp_missing.json()["detail"] == "Incorrect admin key"

        errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
        assert any("SCP_ADMIN_KEY_FILE" in msg for msg in errors), (
            "Registry phải log ERROR nêu rõ kênh FILE hỏng (fail rõ, không lặng lẽ)"
        )
        assert all(env_key not in msg for msg in errors), "Secrecy: không bao giờ log giá trị key"

        # (3b) File rỗng (chỉ whitespace)
        empty_file = tmp_path / "empty-admin-key"
        empty_file.write_text("   \n", encoding="utf-8")
        monkeypatch.setenv("SCP_ADMIN_KEY_FILE", str(empty_file))
        resp_empty = client.post("/auth/token", json={"admin_key": env_key})
        assert resp_empty.status_code == 401, "file rỗng phải fail-closed 401, không fallback env"
