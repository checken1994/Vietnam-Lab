"""[B1-W2 2026-10-23] HC-07 — boot gate đọc secret qua secret_registry.resolve().

Contract được pin (xem ``scp/core/config_contract.py`` [HC-07/HC-08] và
``scp/security/secret_registry.py``):

  * Secret khai báo trong registry (SCP_ADMIN_KEY, SCP_JWT_SECRET) được kiểm
    tra presence qua ``resolve()`` — kênh ``{name}_FILE`` được công nhận:
    secret cấp chỉ qua file không còn bị báo MISSING nhầm.
  * Fail-closed giữ nguyên: thiếu thật, hoặc ``*_FILE`` set nhưng file
    hỏng/rỗng (KHÔNG fallback về env) → ``ConfigContractError`` → boot bị chặn.
  * Forbidden/min-length check vẫn chạy trên giá trị đã resolve — kênh file
    cũng bị ràng buộc độ dài (strict hơn, không hạ chuẩn).
"""
from __future__ import annotations

import pytest

from scp.core.config_contract import (
    ConfigContractError,
    validate_boot_config,
)
from scp.security.secret_registry import audit_missing

_ADMIN_FILE_VALUE = "admin-file-channel-key-12345"  # >= MIN_LENGTH (8), không thuộc FORBIDDEN
_JWT_VALUE = "jwt-env-channel-secret-0123456789abcdef"  # >= 32 chars


@pytest.fixture(autouse=True)
def _clean_boot_env(monkeypatch):
    """Dọn kênh env/file cho 2 required secret (không phụ thuộc .env host)."""
    for name in (
        "SCP_ADMIN_KEY",
        "SCP_ADMIN_KEY_FILE",
        "SCP_JWT_SECRET",
        "SCP_JWT_FILE",
    ):
        monkeypatch.delenv(name, raising=False)
    yield


def _set_jwt_env(monkeypatch):
    monkeypatch.setenv("SCP_JWT_SECRET", _JWT_VALUE)


def test_file_channel_secret_passes_boot_gate(monkeypatch, tmp_path):
    """[HC-07] SCP_ADMIN_KEY cấp qua SCP_ADMIN_KEY_FILE → hết MISSING, boot OK."""
    _set_jwt_env(monkeypatch)
    secret_file = tmp_path / "admin_key"
    secret_file.write_text(_ADMIN_FILE_VALUE + "\n", encoding="utf-8")
    monkeypatch.setenv("SCP_ADMIN_KEY_FILE", str(secret_file))

    validated = validate_boot_config()

    assert validated["SCP_ADMIN_KEY"] == _ADMIN_FILE_VALUE
    assert validated["SCP_JWT_SECRET"] == _JWT_VALUE


def test_missing_secret_still_blocks_boot_fail_closed(monkeypatch):
    """[Fail-closed] Thiếu thật (env + file đều không có) → vẫn chặn boot."""
    _set_jwt_env(monkeypatch)
    with pytest.raises(ConfigContractError, match="SCP_ADMIN_KEY"):
        validate_boot_config()


def test_unreadable_file_channel_blocks_even_with_env_residue(monkeypatch, tmp_path):
    """[Fail-closed HC-08] *_FILE set nhưng file không tồn tại → MISSING dù env
    còn residue: precedence FILE-wins KHÔNG fallback env (fail-open cấm)."""
    _set_jwt_env(monkeypatch)
    monkeypatch.setenv("SCP_ADMIN_KEY", _ADMIN_FILE_VALUE)
    monkeypatch.setenv("SCP_ADMIN_KEY_FILE", str(tmp_path / "does-not-exist"))

    with pytest.raises(ConfigContractError, match="SCP_ADMIN_KEY"):
        validate_boot_config()


def test_min_length_enforced_on_file_channel_value(monkeypatch, tmp_path):
    """[Không hạ chuẩn] Giá trị file-channel ngắn hơn MIN_LENGTH → TOO_SHORT."""
    _set_jwt_env(monkeypatch)
    secret_file = tmp_path / "admin_key"
    secret_file.write_text("short", encoding="utf-8")  # 5 < 8
    monkeypatch.setenv("SCP_ADMIN_KEY_FILE", str(secret_file))

    with pytest.raises(ConfigContractError, match="TOO_SHORT.*SCP_ADMIN_KEY|SCP_ADMIN_KEY.*TOO_SHORT"):
        validate_boot_config()


def test_env_only_path_unchanged(monkeypatch):
    """[Backward-compat] Không có *_FILE → hành vi env như cũ."""
    _set_jwt_env(monkeypatch)
    monkeypatch.setenv("SCP_ADMIN_KEY", _ADMIN_FILE_VALUE)

    validated = validate_boot_config()

    assert validated["SCP_ADMIN_KEY"] == _ADMIN_FILE_VALUE


def test_boot_gate_agrees_with_audit_missing(monkeypatch, tmp_path):
    """[HC-07 wiring] audit_missing([]) của registry và boot gate cùng verdict:
    thiếu → audit_missing liệt kê + boot raise; đủ (qua file) → audit_missing
    rỗng + boot OK."""
    _set_jwt_env(monkeypatch)
    monkeypatch.setenv("SCP_ADMIN_KEY", _ADMIN_FILE_VALUE)
    assert audit_missing(["SCP_ADMIN_KEY", "SCP_JWT_SECRET"]) == []
    assert validate_boot_config()["SCP_ADMIN_KEY"] == _ADMIN_FILE_VALUE

    monkeypatch.delenv("SCP_ADMIN_KEY", raising=False)
    assert audit_missing(["SCP_ADMIN_KEY"]) == ["SCP_ADMIN_KEY"]
    with pytest.raises(ConfigContractError, match="SCP_ADMIN_KEY"):
        validate_boot_config()
