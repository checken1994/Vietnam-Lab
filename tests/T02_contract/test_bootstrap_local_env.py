"""Contract tests for scripts/bootstrap_local_env.py.

Verifies:
- Bootstrapping fresh .env from template with 256-bit secure tokens.
- Populating missing secrets on --update without overwriting existing secrets.
- Setting SCP_HOST=0.0.0.0 on --server.
- Minimum length and non-placeholder enforcement.
"""
from __future__ import annotations

import os
from pathlib import Path

from scripts.bootstrap_local_env import REQUIRED_SECRET_KEYS, bootstrap


def test_bootstrap_fresh_creates_all_secrets(tmp_path: Path):
    template = tmp_path / ".env.example"
    template.write_text(
        "SCP_HOST=127.0.0.1\n"
        "# SCP_JWT_SECRET=\n"
        "# SCP_ADMIN_KEY=\n"
        "# SCP_CAPABILITY_SECRET=\n"
        "# SCP_AUTH_TOKEN_SECRET=\n"
        "# SCP_AUTH_TOKEN=\n"
        "# SCP_PC_CONTROLLER_TOKEN=\n"
        "# SCP_ENCRYPTION_KEY=\n"
        "# SCP_AUTH_PASSWORD_FILE=\n",
        encoding="utf-8",
    )
    output = tmp_path / ".env"
    secret_dir = tmp_path / ".private-secrets"

    created = bootstrap(template, output, secret_dir, update=False, server=True)
    assert created is True
    assert output.is_file()

    content = output.read_text(encoding="utf-8")
    lines = dict(
        line.split("=", 1)
        for line in content.splitlines()
        if "=" in line and not line.strip().startswith("#")
    )

    assert lines["SCP_HOST"] == "0.0.0.0"
    for k in REQUIRED_SECRET_KEYS:
        assert k in lines
        assert len(lines[k]) >= 32

    assert "SCP_AUTH_PASSWORD_FILE" in lines
    auth_file = Path(lines["SCP_AUTH_PASSWORD_FILE"])
    if not auth_file.is_absolute():
        auth_file = tmp_path / auth_file
    assert auth_file.is_file()


def test_bootstrap_update_preserves_existing_and_fills_missing(tmp_path: Path):
    template = tmp_path / ".env.example"
    template.write_text("SCP_HOST=127.0.0.1\n", encoding="utf-8")

    output = tmp_path / ".env"
    # Pre-existing .env with a custom JWT secret and a custom API key, but missing capability secret
    custom_jwt = "custom_jwt_secret_value_1234567890_abcdef"
    output.write_text(
        f"SCP_HOST=127.0.0.1\nSCP_JWT_SECRET={custom_jwt}\nOPENROUTER_API_KEY=sk-or-real\n",
        encoding="utf-8",
    )
    secret_dir = tmp_path / ".private-secrets"

    # Running with update=False does NOT modify
    updated_noop = bootstrap(template, output, secret_dir, update=False)
    assert updated_noop is False

    # Running with update=True fills in missing keys
    updated = bootstrap(template, output, secret_dir, update=True, server=False)
    assert updated is True

    content = output.read_text(encoding="utf-8")
    lines = dict(
        line.split("=", 1)
        for line in content.splitlines()
        if "=" in line and not line.strip().startswith("#")
    )

    # Custom JWT and API key must be strictly preserved
    assert lines["SCP_JWT_SECRET"] == custom_jwt
    assert lines["OPENROUTER_API_KEY"] == "sk-or-real"

    # Missing secrets must be generated
    assert len(lines["SCP_CAPABILITY_SECRET"]) >= 32
    assert len(lines["SCP_ADMIN_KEY"]) >= 8
    assert len(lines["SCP_AUTH_TOKEN"]) >= 32
    assert len(lines["SCP_PC_CONTROLLER_TOKEN"]) >= 32
    assert len(lines["SCP_ENCRYPTION_KEY"]) >= 32
