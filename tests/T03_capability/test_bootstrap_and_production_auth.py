from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
from pathlib import Path

from scripts.bootstrap_local_env import bootstrap

ROOT = Path(__file__).resolve().parents[2]


def _env_value(text: str, key: str) -> str:
    prefix = f"{key}="
    return next(line[len(prefix) :] for line in text.splitlines() if line.startswith(prefix))


def test_first_run_bootstrap_generates_capability_secret_without_output_leak(tmp_path, capsys):
    template = tmp_path / ".env.example"
    output = tmp_path / ".env"
    secret_dir = tmp_path / ".private-secrets"
    template.write_text(
        "# SCP_JWT_SECRET=\n"
        "# SCP_ADMIN_KEY=\n"
        "# SCP_CAPABILITY_SECRET=\n"
        "# SCP_AUTH_PASSWORD_FILE=\n",
        encoding="utf-8",
    )

    assert bootstrap(template, output, secret_dir) is True
    rendered = output.read_text(encoding="utf-8")
    jwt_secret = _env_value(rendered, "SCP_JWT_SECRET")
    admin_key = _env_value(rendered, "SCP_ADMIN_KEY")
    capability_secret = _env_value(rendered, "SCP_CAPABILITY_SECRET")

    assert len(jwt_secret) >= 32
    assert len(admin_key) >= 8
    assert len(capability_secret) >= 32
    assert capability_secret
    assert "SCP_CAPABILITY_SECRET=<" not in rendered
    assert _env_value(rendered, "SCP_AUTH_PASSWORD_FILE") == ".private-secrets/scp-auth-password"
    assert (secret_dir / "scp-auth-password").read_text(encoding="utf-8")

    stdout = capsys.readouterr().out
    for secret in (jwt_secret, admin_key, capability_secret):
        assert secret not in stdout

    before = output.read_bytes()
    assert bootstrap(template, output, secret_dir) is False
    assert output.read_bytes() == before
    second_stdout = capsys.readouterr().out
    assert jwt_secret not in second_stdout
    assert admin_key not in second_stdout
    assert capability_secret not in second_stdout


def _run_api_probe(tmp_path: Path, *, production: bool) -> dict[str, int]:
    jwt_secret = secrets.token_hex(32)
    admin_key = secrets.token_urlsafe(32)
    capability_secret = secrets.token_hex(32)
    env_file = tmp_path / ("production.env" if production else "local.env")
    env_file.write_text(
        "\n".join(
            (
                f"SCP_PRODUCTION_MODE={'1' if production else '0'}",
                f"SCP_MODE={'production' if production else 'test'}",
                f"SCP_API_PROFILE={'core' if production else 'full'}",
                f"SCP_JWT_SECRET={jwt_secret}",
                f"SCP_ADMIN_KEY={admin_key}",
                f"SCP_CAPABILITY_SECRET={capability_secret}",
                "SCP_EGRESS_MODE=deny",
                "SCP_HOST=127.0.0.1",
                "SCP_FORCE_HTTPS=0",
                "SCP_SKIP_STARTUP_GATE=1",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    child = r'''
import json
from fastapi.testclient import TestClient
from scp.api_server import app

client = TestClient(app)
result = {}
for path in ("/docs", "/redoc", "/openapi.json", "/v1/chat/completions"):
    result[path] = client.get(path).status_code
result["health"] = client.get("/health").status_code
result["readiness"] = client.get("/readiness").status_code
result["metrics_no_auth"] = client.get("/metrics").status_code
result["detailed_no_auth"] = client.get("/health/detailed").status_code
wrong = client.post("/auth/token", json={"admin_key": "wrong"})
right = client.post("/auth/token", json={"admin_key": __ADMIN_KEY__})
result["auth_token_wrong"] = wrong.status_code
result["auth_token_right"] = right.status_code
if right.status_code == 200:
    bearer = {"Authorization": "Bearer " + right.json()["access_token"]}
    result["metrics_with_auth"] = client.get("/metrics", headers=bearer).status_code
print("API_PROBE=" + json.dumps(result, sort_keys=True))
'''.replace("__ADMIN_KEY__", repr(admin_key), 1)
    env = os.environ.copy()
    env.update({"SCP_ENV_FILE": str(env_file), "PYTHONUTF8": "1"})
    completed = subprocess.run(
        [sys.executable, "-c", child],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    marker = next(line for line in completed.stdout.splitlines() if line.startswith("API_PROBE="))
    return json.loads(marker.split("=", 1)[1])


def test_production_disables_docs_and_authenticates_observability(tmp_path):
    result = _run_api_probe(tmp_path, production=True)

    assert result["/docs"] == 404
    assert result["/redoc"] == 404
    assert result["/openapi.json"] == 404
    assert result["/v1/chat/completions"] == 404
    assert result["health"] == 200
    assert result["readiness"] == 503
    assert result["metrics_no_auth"] == 401
    assert result["detailed_no_auth"] == 401
    assert result["auth_token_wrong"] == 401
    assert result["auth_token_right"] == 200
    assert result["metrics_with_auth"] == 200


def test_local_profile_keeps_developer_docs_and_metrics_behavior(tmp_path):
    result = _run_api_probe(tmp_path, production=False)

    assert result["/docs"] == 200
    assert result["/redoc"] == 200
    assert result["/openapi.json"] == 200
    assert result["/v1/chat/completions"] != 404
    assert result["health"] == 200
    assert result["metrics_no_auth"] == 200
    assert result["auth_token_wrong"] == 401
    assert result["auth_token_right"] == 200
    assert result["metrics_with_auth"] == 200
