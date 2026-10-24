"""T13 — Core default profile lane (K5-W2 / HC-59): "hành trình người lạ".

Journey under test: a stranger boots SCP with the least-privileged default
(`SCP_API_PROFILE=core`, the production default per `scp/api/route_profile.py:
resolve_api_profile`) and tries the classic entry points. Expected reality:

  (a) the app boots and answers /health with 200;
  (b) the root route listing does NOT advertise /v1/models (the whole
      `openai_compat` group is frozen at profile core — GROUP_MINIMUM);
  (c) GET /v1/models → 404 (router not registered — absence, not auth);
  (d) POST /ask without credentials → 401 (auth gate even on core surface).

Anti-placebo arm (DNA #19/#25): a second boot with `SCP_ENABLED_GROUPS=
openai_compat` (fine-grained opt-in override, same route_profile module) must
flip the result to 401 — proving the 404 in the main lane comes from the
profile gate and not from a broken router import, and proving /v1/models
carries real auth once registered.

Why subprocess: `_API_PROFILE` is resolved ONCE at `scp.api_server` import
time; booting core and override in one interpreter is impossible without a
module reload, so each lane runs in a fresh python process with an explicit
env (compose.test.yml-compatible audit-test-* secrets — no real secrets).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# compose.test.yml-compatible test secrets (audit-test-*, never real ones).
TEST_ENV = {
    "SCP_CAPABILITY_SECRET": "audit-test-cap-secret-32chars",
    "SCP_JWT_SECRET": "audit-test-secret-change-me-32chars",
    "SCP_ADMIN_KEY": "audit-test-admin-key-32chars",
    "SCP_AUTH_TOKEN_SECRET": "audit-test-auth-secret-32chars",
    "OPENROUTER_API_KEY": "test-key",
    "SCP_PRODUCTION_MODE": "0",
    "SCP_EGRESS_MODE": "deny",
}

BOOT_PROBE = r"""
import json

from scp.api.route_profile import get_active_profile_summary, resolve_api_profile
profile = resolve_api_profile()
summary = get_active_profile_summary()

from fastapi.testclient import TestClient
from scp.api_server import app

client = TestClient(app)
root_res = client.get("/")
models_res = client.get("/v1/models")
ask_res = client.post("/ask", json={"question": "lane-probe: who am I?"})
health_res = client.get("/health")

print(json.dumps({
    "boot": True,
    "profile": profile,
    "openai_compat_enabled": "openai_compat" in summary["enabled_groups"],
    "health_status": health_res.status_code,
    "root_status": root_res.status_code,
    "root_advertises_v1_models": "/v1/models" in root_res.text,
    "models_status": models_res.status_code,
    "ask_no_auth_status": ask_res.status_code,
}))
"""


def _run_lane(extra_env: dict[str, str]) -> dict:
    env = os.environ.copy()
    env.update(TEST_ENV)
    env.update(extra_env)
    proc = subprocess.run(
        [sys.executable, "-c", BOOT_PROBE],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout)[-2000:]
        raise AssertionError(f"lane subprocess failed (rc={proc.returncode}):\n{tail}")
    # The probe prints exactly one JSON line on stdout (last non-empty line).
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    return json.loads(lines[-1])


def test_core_default_lane_stranger_journey():
    """(a) boots, (b) root hides /v1/models, (c) /v1/models → 404, (d) /ask → 401."""
    lane = _run_lane({"SCP_API_PROFILE": "core"})

    assert lane["boot"] is True
    assert lane["profile"] == "core", f"lane must resolve profile=core, got {lane['profile']!r}"
    assert lane["openai_compat_enabled"] is False, (
        "profile core must freeze the openai_compat group (GROUP_MINIMUM=standard)"
    )
    assert lane["health_status"] == 200, f"app must boot and answer /health, got {lane['health_status']}"
    assert lane["root_status"] == 200, f"root listing must answer 200, got {lane['root_status']}"
    assert lane["root_advertises_v1_models"] is False, (
        "root route listing must NOT advertise /v1/models under profile core "
        "(api_server.py only appends it when _route_enabled('openai_compat'))"
    )
    assert lane["models_status"] == 404, (
        f"/v1/models must be 404 (router frozen) under profile core, got {lane['models_status']}"
    )
    assert lane["ask_no_auth_status"] == 401, (
        f"/ask without credentials must be 401, got {lane['ask_no_auth_status']} "
        "(this is the wave-4d auth contract: Depends(verify_jwt_token))"
    )


def test_openai_compat_opt_in_override_flips_404_to_401():
    """Anti-placebo control: SCP_ENABLED_GROUPS=openai_compat overrides the core
    freeze — /v1/models must then EXIST and demand auth (401), and the root
    listing must advertise it. If this still returned 404, the main lane's 404
    would prove nothing about the gate (it could just be a dead router)."""
    lane = _run_lane({"SCP_API_PROFILE": "core", "SCP_ENABLED_GROUPS": "openai_compat"})

    assert lane["profile"] == "core"
    assert lane["openai_compat_enabled"] is True, "override must enable openai_compat despite profile=core"
    assert lane["root_advertises_v1_models"] is True, "root must now advertise /v1/models"
    assert lane["models_status"] == 401, (
        f"/v1/models must be 401 (registered + auth-gated) after opt-in, got {lane['models_status']}"
    )
