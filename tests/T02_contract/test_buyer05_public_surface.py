"""[BUYER-05 2026-10-30] Giảm bề mặt công khai — /health fingerprint + /docs.

Bằng chứng buyer (đợt 2, đối soát GO):
  1. ``GET /docs`` + ``GET /openapi.json`` (184 paths) phục vụ KHÔNG auth
     → bày toàn bộ bề mặt API cho trinh sát. Probe base 05e0e328: 184 paths
     chỉ xuất hiện ở posture non-production (profile full); production mode
     đã ẩn docs nhưng stack local mặc định (compose.yml) ghim
     SCP_PRODUCTION_MODE=0 ⇒ docs mở. Contract mới: docs OPT-IN ở MỌI mode
     (``SCP_ENABLE_DOCS=1``), mặc định tắt (fail-closed).
  2. ``GET /health`` không auth lộ fingerprint: commit đầy đủ, routes,
     model_id, active_llm_providers (+ version/release/pid/host/modules).
     Contract mới: không auth ⇒ chỉ ``status`` + identity liveness tối thiểu
     (service_name/mode/configured_port); bản đầy đủ chỉ khi Bearer admin hợp
     lệ qua ``verify_admin`` canonical. Token sai/thiếu/bị khoá ⇒ bản rút gọn,
     KHÔNG BAO GIỜ lỗi (healthcheck Docker chỉ cần HTTP 200).
  3. ``POST /auth/token`` 401 phải gợi ý scheme ``WWW-Authenticate: Bearer``.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[2]
_ADMIN_KEY = "buyer05-throwaway-admin-key-for-tests-only-32b"

# Field fingerprint không được xuất hiện (ở bất kỳ cấp lồng nào) trong body
# /health không auth.
FINGERPRINT_KEYS = frozenset({
    "commit",
    "routes",
    "model_id",
    "active_llm_providers",
    "operational_mode",
    "version",
    "release",
    "legacy_model_ids",
    "legacy_protocols",
    "modules",
    "pid",
    "host",
})


def _all_keys(obj) -> set[str]:
    keys: set[str] = set()
    if isinstance(obj, dict):
        for key, value in obj.items():
            keys.add(str(key))
            keys |= _all_keys(value)
    elif isinstance(obj, list):
        for item in obj:
            keys |= _all_keys(item)
    return keys


@pytest.fixture()
def admin_env(monkeypatch):
    monkeypatch.setenv("SCP_ADMIN_KEY", _ADMIN_KEY)
    monkeypatch.delenv("SCP_ADMIN_KEY_FILE", raising=False)
    from scp.security import auth as _auth

    with _auth._auth_failures_lock:
        _auth._auth_failures.clear()
    return _auth


def _client() -> TestClient:
    from scp import api_server

    return TestClient(api_server.app)  # no lifespan — routes only


# ── /health ─────────────────────────────────────────────────────────────────


def test_unauth_health_is_200_ok_without_fingerprint(admin_env):
    response = _client().get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    leaked = FINGERPRINT_KEYS & _all_keys(body)
    assert not leaked, f"/health unauth lộ fingerprint: {sorted(leaked)}"


def test_unauth_health_does_not_echo_commit_value(admin_env):
    """Không chỉ tên field: giá trị commit thật (lấy từ bản admin) không được
    xuất hiện ở bất kỳ đâu trong body unauth (kể cả đổi tên field)."""
    client = _client()
    full = client.get("/health", headers={"Authorization": f"Bearer {_ADMIN_KEY}"}).json()
    commit = full["service_identity"]["commit"]
    assert commit and len(commit) >= 7
    unauth_text = client.get("/health").text
    assert commit not in unauth_text
    assert commit[:12] not in unauth_text


def test_unauth_health_keeps_liveness_identity(admin_env):
    """Consumer liveness (scripts/run_scp_acceptance.py a01, test_flow_01,
    startup-troubleshooter) dựa vào service_name/mode/configured_port."""
    identity = _client().get("/health").json()["service_identity"]
    assert identity["service_name"] == "scp-backend"
    assert isinstance(identity["configured_port"], int)
    assert identity["mode"]


@pytest.mark.parametrize(
    "header",
    [
        "Bearer wrong-token",
        "Bearer ",
        "Basic Zm9vOmJhcg==",
        f"Bearer {_ADMIN_KEY}x",
    ],
)
def test_bad_credentials_get_reduced_payload_never_error(admin_env, header):
    response = _client().get("/health", headers={"Authorization": header})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ok"
    assert not (FINGERPRINT_KEYS & _all_keys(body))


def test_admin_bearer_gets_full_payload(admin_env):
    response = _client().get("/health", headers={"Authorization": f"Bearer {_ADMIN_KEY}"})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    identity = body["service_identity"]
    for key in ("service_name", "mode", "host", "configured_port", "pid", "commit"):
        assert key in identity, f"admin /health thiếu service_identity.{key}"
    assert identity["commit"]
    assert isinstance(body["routes"], int) and body["routes"] > 0
    assert body["release"]["model_id"]
    assert isinstance(body["active_llm_providers"], list)
    assert body["operational_mode"] in ("local_verified", "connected_llm")
    assert body["version"]
    # Hardening cũ vẫn giữ ở bản đầy đủ.
    assert "argv" not in identity
    assert "config_hash" not in json.dumps(body, default=str)


def test_admin_jwt_from_auth_token_gets_full_payload(admin_env):
    client = _client()
    token = client.post("/auth/token", json={"admin_key": _ADMIN_KEY})
    assert token.status_code == 200, token.text
    jwt = token.json()["access_token"]
    body = client.get("/health", headers={"Authorization": f"Bearer {jwt}"}).json()
    assert body["service_identity"]["commit"]
    assert "active_llm_providers" in body


def test_locked_out_ip_still_gets_200_reduced(admin_env):
    """Fail-closed: verify_admin trả 429 (IP bị khoá) ⇒ /health vẫn 200 rút
    gọn, kể cả với admin key đúng — healthcheck không bao giờ đỏ vì auth."""
    now = time.time()
    with admin_env._auth_failures_lock:
        admin_env._auth_failures["testclient"] = [now] * (admin_env._RATE_LIMIT_MAX_FAILURES + 1)
    response = _client().get("/health", headers={"Authorization": f"Bearer {_ADMIN_KEY}"})
    assert response.status_code == 200
    assert not (FINGERPRINT_KEYS & _all_keys(response.json()))


def test_unauth_probe_never_counts_as_auth_failure(admin_env):
    """Healthcheck Docker (không header) chạy mỗi 15-30s không được đẩy IP vào
    bucket auth-failure (sẽ khoá admin hợp lệ cùng IP)."""
    client = _client()
    for _ in range(admin_env._RATE_LIMIT_MAX_FAILURES + 2):
        assert client.get("/health").status_code == 200
    with admin_env._auth_failures_lock:
        assert not admin_env._auth_failures.get("testclient")
    body = client.get("/health", headers={"Authorization": f"Bearer {_ADMIN_KEY}"}).json()
    assert body["service_identity"]["commit"]


def test_health_without_request_object_is_reduced():
    """Gọi trực tiếp (không có Request) ⇒ không có bằng chứng auth ⇒ rút gọn."""
    import asyncio

    from scp import api_server

    body = asyncio.run(api_server.health())
    assert body["status"] == "ok"
    assert not (FINGERPRINT_KEYS & _all_keys(body))


# ── /auth/token ─────────────────────────────────────────────────────────────


def test_auth_token_401_advertises_bearer_scheme(admin_env):
    response = _client().post("/auth/token", json={"admin_key": "definitely-wrong"})
    assert response.status_code == 401
    assert response.headers.get("www-authenticate", "").lower().startswith("bearer")


# ── /docs, /redoc, /openapi.json ────────────────────────────────────────────

_DOC_PATHS = ("/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect")


def test_docs_flag_helper_contract():
    from scp.api_server_parts.public_surface import docs_exposure_enabled

    assert docs_exposure_enabled(lambda k, d=None: d) is False
    assert docs_exposure_enabled({"SCP_PRODUCTION_MODE": "1"}.get) is False
    assert docs_exposure_enabled({"SCP_PRODUCTION_MODE": "0"}.get) is False
    for raw in ("1", "true", "YES", " on "):
        assert docs_exposure_enabled({"SCP_ENABLE_DOCS": raw}.get) is True
    for raw in ("0", "", "false", "off", "2"):
        assert docs_exposure_enabled({"SCP_ENABLE_DOCS": raw}.get) is False


def test_live_app_hides_docs_by_default():
    """App thật của phiên test (conftest không đặt SCP_ENABLE_DOCS).

    Không skip (T00 no-skip gate): phiên nào đặt SCP_ENABLE_DOCS truthy thì
    test FAIL rõ ràng — chạy lại không có flag; nhánh flag=1 được phủ bởi
    test_docs_exposure_real_app_per_env (process riêng)."""
    flag = os.environ.get("SCP_ENABLE_DOCS", "")
    assert flag.strip().lower() not in {"1", "true", "yes", "on"}, (
        "phiên test đang đặt SCP_ENABLE_DOCS truthy — test này pin mặc định (unset)"
    )
    from scp import api_server

    assert api_server.app.openapi_url is None
    assert api_server.app.docs_url is None
    assert api_server.app.redoc_url is None
    client = _client()
    for path in _DOC_PATHS:
        assert client.get(path).status_code == 404, path
    # Schema vẫn sinh nội bộ được (tools/export_openapi_snapshot.py dùng app.openapi()).
    assert api_server.app.openapi().get("paths")


_SUBPROCESS_PROBE = r"""
import json, logging
logging.disable(logging.CRITICAL)
from fastapi.testclient import TestClient
from scp import api_server
c = TestClient(api_server.app)
out = {p: c.get(p).status_code for p in %r}
out["/health"] = c.get("/health").status_code
print("PROBE=" + json.dumps(out))
""".replace("%r", repr(_DOC_PATHS))


def _probe_docs_in_fresh_process(extra_env: dict[str, str]) -> dict[str, int]:
    env = {k: v for k, v in os.environ.items() if k not in {"SCP_ENABLE_DOCS", "SCP_PRODUCTION_MODE"}}
    env.update(extra_env)
    env.setdefault("SCP_AUTH_PASSWORD", "buyer05-throwaway-password-xx")
    env["PYTHONUTF8"] = "1"
    proc = subprocess.run(
        [sys.executable, "-c", _SUBPROCESS_PROBE],
        cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, timeout=240, check=False,
    )
    lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("PROBE=")]
    assert proc.returncode == 0 and lines, f"probe failed rc={proc.returncode}: {proc.stderr[-1500:]}"
    return json.loads(lines[-1][len("PROBE="):])


@pytest.mark.parametrize(
    "extra_env, expect_docs",
    [
        ({"SCP_PRODUCTION_MODE": "1"}, False),
        ({"SCP_PRODUCTION_MODE": "0"}, False),
        ({"SCP_PRODUCTION_MODE": "1", "SCP_ENABLE_DOCS": "0"}, False),
        ({"SCP_PRODUCTION_MODE": "0", "SCP_ENABLE_DOCS": "1"}, True),
        ({"SCP_PRODUCTION_MODE": "1", "SCP_ENABLE_DOCS": "1"}, True),
    ],
    ids=["prod-default", "dev-default", "prod-flag0", "dev-flag1", "prod-flag1"],
)
def test_docs_exposure_real_app_per_env(extra_env, expect_docs):
    statuses = _probe_docs_in_fresh_process(extra_env)
    assert statuses["/health"] == 200, statuses
    for path in _DOC_PATHS:
        if expect_docs:
            assert statuses[path] == 200, (path, statuses)
        else:
            assert statuses[path] in (403, 404), (path, statuses)
