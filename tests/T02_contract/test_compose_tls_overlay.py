"""[F-E2E-1 · 2026-10-30] Contract for docker-compose.tls.yml (production TLS overlay).

Finding F-E2E-1 (E2E report 2026-10-30, live): docker-compose.full.yml binds the
backend to SCP_HOST=0.0.0.0 with SCP_FORCE_HTTPS defaulting to 0, so in the
default production posture scp/security/production_guard.py refuses startup
("SCP_FORCE_HTTPS=1 is required when SCP_HOST is not loopback") and the
container restart-loops. The guard is right; the compose had no valid
production boot path.

Fix pinned here (no guard / product change): the overlay binds uvicorn to
LOOPBACK only and puts a TLS terminator (Caddy, internal CA) in the backend's
network namespace as the ONLY network-facing listener on the registered
BACKEND port. These tests pin the structural facts that make the guard's
loopback branch truthful, and run the REAL guard against the env the base
file and the merged files produce (base => refused, base+overlay => allowed).

Scope (DNA #16): static + in-process guard check only. Runtime boot, TLS
handshake and /ask evidence live in the F-E2E-1 done report, not here.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from scp.security.production_guard import enforce_production_safety

REPO_ROOT = Path(__file__).resolve().parents[2]
BASE_FILE = REPO_ROOT / "docker-compose.full.yml"
OVERLAY_FILE = REPO_ROOT / "docker-compose.tls.yml"
LOOPBACK = {"127.0.0.1", "localhost", "::1"}
POSTURE_KEYS = (
    "SCP_PRODUCTION_MODE",
    "SCP_FORCE_HTTPS",
    "SCP_EGRESS_MODE",
    "SCP_AUTH_PASSWORD",
    "SCP_AUTH_PASSWORD_FILE",
    "SCP_DEV_MODE",
    "SCP_SKIP_STARTUP_GATE",
)


def _load(path: Path) -> dict:
    assert path.is_file(), f"missing {path.name} (F-E2E-1 production boot path)"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict), f"{path.name} must parse as a YAML mapping"
    return data


def _resolve_default(value: object) -> str:
    """Resolve '${VAR:-default}' to its default (operator unset)."""
    text = str(value)
    match = re.fullmatch(r"\$\{[A-Z0-9_]+:-([^}]*)\}", text)
    return match.group(1) if match else text


def _caddyfile(overlay: dict) -> str:
    proxy = overlay["services"]["tls-proxy"]
    mounts = [v for v in proxy["volumes"] if str(v).split(":")[1:2] == ["/etc/caddy/Caddyfile"]]
    assert len(mounts) == 1, "tls-proxy must mount exactly one Caddyfile"
    source, _target, mode = str(mounts[0]).split(":")
    assert mode == "ro", "Caddyfile must be mounted read-only"
    path = REPO_ROOT / source
    assert path.is_file(), f"Caddyfile source missing: {source}"
    content = path.read_text(encoding="utf-8")
    assert content.strip()
    return content


def test_overlay_backend_binds_loopback_only_and_keeps_posture():
    overlay = _load(OVERLAY_FILE)
    backend = overlay["services"]["backend"]
    env = backend["environment"]
    assert env["SCP_HOST"] in LOOPBACK, "overlay must bind uvicorn to loopback"
    # The overlay may only move the bind address/port; every posture knob is
    # inherited from the base file (an overlay that sets them could weaken it).
    # [esc-auto slice 4] One extra key, pinned to its exact value: proxy trust is
    # declared for the netns-shared Caddy ONLY (loopback), never a network or
    # wildcard, and Caddy must not trust client-supplied X-Forwarded-For.
    assert set(env) == {"SCP_HOST", "SCP_PORT", "SCP_MODE", "SCP_TRUSTED_PROXY_IPS"}, sorted(env)
    assert env["SCP_TRUSTED_PROXY_IPS"] == "127.0.0.1"
    assert "trusted_proxies" not in _caddyfile(overlay)
    # Identity label must stay truthful (port 8001 would otherwise read "test").
    assert env["SCP_MODE"] == "production"
    for key in POSTURE_KEYS:
        assert key not in env
    assert "ports" not in backend, "overlay must not change the published ports"
    port = int(env["SCP_PORT"])
    assert backend["command"] == [str(port)], "uvicorn port (command) must equal SCP_PORT"
    hc = " ".join(backend["healthcheck"]["test"])
    assert f"http://127.0.0.1:{port}/health" in hc


def test_tls_proxy_is_the_only_listener_on_the_published_backend_port():
    base = _load(BASE_FILE)
    overlay = _load(OVERLAY_FILE)
    published = base["services"]["backend"]["ports"]
    assert published == ["127.0.0.1:8000:8000"], published
    public_port = 8000
    uvicorn_port = int(overlay["services"]["backend"]["environment"]["SCP_PORT"])
    assert uvicorn_port != public_port, "plaintext uvicorn must not own the published port"

    proxy = overlay["services"]["tls-proxy"]
    assert proxy["network_mode"] == "service:backend"
    assert "ports" not in proxy and "networks" not in proxy
    assert re.fullmatch(r"caddy:[\w.-]+@sha256:[0-9a-f]{64}", proxy["image"]), proxy["image"]

    caddyfile = _caddyfile(overlay)
    sites = [
        line.strip().rstrip("{").strip()
        for line in caddyfile.splitlines()
        if line.strip().endswith("{") and line.strip() != "{"
    ]
    assert sites, "Caddyfile has no site block"
    for site in sites:
        for addr in (a.strip() for a in site.split(",")):
            assert addr.startswith("https://"), f"non-TLS site address: {addr!r}"
            assert addr.endswith(f":{public_port}"), f"site not on published port: {addr!r}"
    assert re.search(r"^\s*tls internal\s*$", caddyfile, re.M), "TLS source missing"
    assert re.findall(r"reverse_proxy\s+(\S+)", caddyfile) == [f"127.0.0.1:{uvicorn_port}"]
    assert not re.search(r"auto_https\s+off", caddyfile)
    assert re.search(r"^\s*admin off\s*$", caddyfile, re.M), "Caddy admin API must stay off"


def test_tls_proxy_is_hardened():
    proxy = _load(OVERLAY_FILE)["services"]["tls-proxy"]
    assert proxy["read_only"] is True
    assert proxy["cap_drop"] == ["ALL"]
    assert proxy["cap_add"] == ["NET_BIND_SERVICE"]
    assert "no-new-privileges:true" in proxy["security_opt"]
    limits = proxy["deploy"]["resources"]["limits"]
    assert {"cpus", "memory", "pids"} <= set(limits)


def _container_env(compose: dict) -> dict[str, str]:
    env = compose["services"]["backend"]["environment"]
    return {k: _resolve_default(v) for k, v in env.items()}


@pytest.fixture()
def _clean_env(monkeypatch):
    for key in ("SCP_HOST", "SCP_PORT", "SCP_AUTH_PASSWORD_FILE", "SCP_DEV_MODE",
                "SCP_SKIP_STARTUP_GATE", "SCP_EVOLUTION_AUTO", "SCP_ENABLE_CLOSED_LOOP",
                "SCP_TIER3_ALLOW_RELAXATION", "SCP_TIER3_ALLOW_BAREEXCEPTPASS",
                "SCP_AUTONOMOUS_MODE"):
        monkeypatch.delenv(key, raising=False)
    return monkeypatch


def _apply(monkeypatch, env: dict[str, str]) -> None:
    for key in ("SCP_HOST", "SCP_FORCE_HTTPS", "SCP_PRODUCTION_MODE",
                "SCP_EGRESS_MODE", "SCP_AUTO_APPROVE_TIER3"):
        monkeypatch.setenv(key, env[key])
    # Operator must supply a >=16-char password in production (not a default).
    monkeypatch.setenv("SCP_AUTH_PASSWORD", "x" * 24)


def test_real_guard_refuses_base_alone_in_default_production_posture(_clean_env):
    env = _container_env(_load(BASE_FILE))
    assert env["SCP_PRODUCTION_MODE"] == "1" and env["SCP_FORCE_HTTPS"] == "0"
    _apply(_clean_env, env)
    with pytest.raises(RuntimeError, match="SCP_FORCE_HTTPS=1 is required when SCP_HOST is not loopback"):
        enforce_production_safety()


def test_real_guard_allows_base_plus_overlay_and_still_checks_password(_clean_env):
    env = _container_env(_load(BASE_FILE))
    env.update(_container_env(_load(OVERLAY_FILE)))
    assert env["SCP_PRODUCTION_MODE"] == "1", "overlay must keep production posture"
    _apply(_clean_env, env)
    enforce_production_safety()  # loopback branch, truthfully
    # The rest of the guard is still live under the overlay (not bypassed).
    _clean_env.setenv("SCP_AUTH_PASSWORD", "short")
    with pytest.raises(RuntimeError, match="at least 16 characters"):
        enforce_production_safety()
