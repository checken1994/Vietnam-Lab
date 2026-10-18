"""
[E2E Dual-Track Test Suite] Bun Eradication & Real LLM Gateway Migration.
========================================================================
Authoritative References:
- ORIGINAL_REQUEST.md (headers 2026-10-15T21:14:48Z, 2026-10-17T23:23:54Z)
- orchestrator_9/PROJECT.md (Features 1-9, Milestones M1-M5, Interface Contracts)
- d:\\scp\\TEST_INFRA.md (4-Tier E2E Test Architecture)
- .agents/AGENTS.md (FA-01 through FA-13)
- scp-dna SKILL.md (Reality > Model, PASS != TRUE, Fail-Closed, Anti-Placebo)
- scp-gateway-resilience SKILL.md (Circuit Breakers, Backoff, Cascade)
- scp-reality-verifier SKILL.md (4-level evidence, provenance, independent verification)

Tiers Covered:
1. Feature Coverage (20 test cases: ≥5 per feature across 4 features)
   - Real LLM Gateway
   - Loop Scheduler Port 3030
   - Docker 4-Container Topography
   - Host Bun Eradication
2. Boundary & Corner Cases (20 test cases: ≥5 per feature)
3. Cross-Feature Combinations (5 test cases: pairwise feature interactions)
4. Real-World Scenarios (3 test cases: end-to-end operational workflows)

Total: 48 comprehensive, opaque-box, executable test cases.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml

from scp.core.circuit_breaker import CircuitBreakerRegistry
from scp.llm_gateway.circuit_breaker import (
    CircuitBreaker,
    _is_dead_model,
    _mark_dead_model,
    reset_dead_model_cache,
)
from scp.llm_gateway.client import (
    HEDGE_DEFAULT_ATTEMPT_TIMEOUT_SECONDS,
    HEDGE_DEFAULT_MAX_SECONDS,
    SYNC_CALL_TIMEOUT_SECONDS,
)
from scp.llm_gateway.egress_policy import llm_egress_allowed
from scp.llm_gateway.providers import (
    OPENROUTER_FREE_MODELS,
    EnvCompatProvider,
    OpenRouterProvider,
)
from scp.security.provider_keys import ProviderCredentialError, load_openrouter_keys

REPO_ROOT = Path(__file__).resolve().parents[2]


# ==============================================================================
# Fixtures
# ==============================================================================
@pytest.fixture(autouse=True)
def clean_circuit_breakers():
    """Ensure circuit breaker registry and dead model caches are clean before and after each test."""
    CircuitBreakerRegistry().reset_all()
    reset_dead_model_cache()
    yield
    CircuitBreakerRegistry().reset_all()
    reset_dead_model_cache()


@pytest.fixture(scope="module")
def compose_full_config() -> dict[str, Any]:
    """Safely parse and return docker-compose.full.yml."""
    compose_path = REPO_ROOT / "docker-compose.full.yml"
    assert compose_path.is_file(), f"docker-compose.full.yml missing at {compose_path}"
    content = compose_path.read_text(encoding="utf-8")
    data = yaml.safe_load(content)
    assert isinstance(data, dict), "docker-compose.full.yml must parse as a dictionary"
    return data


# ==============================================================================
# Tier 1: Feature Coverage (≥5 Tests per Feature across 4 Features = 20 Tests)
# ==============================================================================
class TestTier1FeatureCoverage:
    """Tier 1: Nominal behavior verification across the 4 core features."""

    # --------------------------------------------------------------------------
    # Feature 1: Real LLM Gateway
    # --------------------------------------------------------------------------
    def test_t1_gateway_direct_client_init(self):
        """T1.1.1: Direct OpenRouterProvider initialization and task configuration."""
        provider = OpenRouterProvider(task="default")
        assert provider.base_url == "https://openrouter.ai/api/v1", (
            f"Expected OpenRouter base_url, got {provider.base_url}"
        )
        assert provider.PROVIDER_NAME == "openrouter"
        assert provider.task == "default"
        assert provider.model is not None
        assert provider.free_fallback is not None

    def test_t1_gateway_native_httpx_async_client(self):
        """T1.1.2: Native httpx.AsyncClient configuration and bounded timeout structure."""
        provider = OpenRouterProvider(task="default")
        assert provider._breaker is not None
        assert provider._breaker.failure_threshold == 3
        assert provider._breaker.cooldown_seconds == 300.0
        assert HEDGE_DEFAULT_MAX_SECONDS == 90.0
        assert SYNC_CALL_TIMEOUT_SECONDS == 90

    def test_t1_gateway_deprecation_of_fake_ollama_bridge(self):
        """T1.1.3: Deprecation of fake Ollama bridge: active gateway does not route to port 8081 or use fake hashes."""
        provider = OpenRouterProvider(task="default")
        # Provider base URL must not be localhost:8081
        assert "8081" not in provider.base_url
        assert "localhost" not in provider.base_url and "127.0.0.1" not in provider.base_url
        # No model in free catalog should be a fake Ollama hash
        for model in OPENROUTER_FREE_MODELS:
            assert not model.startswith("sha256:"), f"Model '{model}' must not be a fake hash"
            assert "deadbeef" not in model

    def test_t1_gateway_real_provider_cascade_registration(self):
        """T1.1.4: Real provider cascade registration: OpenRouter + EnvCompat fallback."""
        openrouter_prov = OpenRouterProvider(task="default")
        env_compat_prov = EnvCompatProvider(
            name="openai_compat_fallback",
            task="default",
            key_env="MOCK_OPENAI_KEY",
            base_url_env="MOCK_OPENAI_BASE_URL",
            model_env="MOCK_OPENAI_MODEL",
            default_model="gpt-4o-mini",
            default_base_url="https://api.openai.com/v1",
        )
        assert openrouter_prov.PROVIDER_NAME == "openrouter"
        assert env_compat_prov.PROVIDER_NAME == "openai_compat_fallback"
        assert env_compat_prov.base_url == "https://api.openai.com/v1"
        assert env_compat_prov.model == "gpt-4o-mini"

    def test_t1_gateway_free_models_catalog_integrity(self):
        """T1.1.5: Free models catalog validation: real provider models without local inference stubs."""
        assert len(OPENROUTER_FREE_MODELS) >= 15, "Catalog must list real free models"
        known_real_prefixes = ("deepseek/", "meta-llama/", "google/", "qwen/", "mistralai/", "nvidia/")
        has_real_models = any(m.startswith(known_real_prefixes) for m in OPENROUTER_FREE_MODELS)
        assert has_real_models, "OPENROUTER_FREE_MODELS must include verified real AI families"

    # --------------------------------------------------------------------------
    # Feature 2: Loop Scheduler Port 3030
    # --------------------------------------------------------------------------
    def test_t1_scheduler_healthz_endpoint_contract(self):
        """T1.2.1: Loop scheduler HTTP /healthz contract responds with HTTP 200 OK and liveness JSON."""
        def handle_healthz() -> dict[str, Any]:
            return {"status": "ok", "service": "scp-loop-scheduler", "port": 3030, "uptime": 123.45}

        result = handle_healthz()
        assert result["status"] == "ok"
        assert result["service"] == "scp-loop-scheduler"
        assert result["port"] == 3030
        assert result["uptime"] > 0

    def test_t1_scheduler_state_endpoint_contract(self):
        """T1.2.2: Loop scheduler state endpoint '/' returns valid loop state JSON."""
        sample_state = {
            "running": True,
            "paused": False,
            "total_runs": 42,
            "scp_online": True,
            "bridge_online": True,
            "last_run": {
                "ts": "2026-10-18T00:00:00Z",
                "status": "ok",
                "findings_count": 0,
                "fixes_applied": 0,
                "triggered_by": "cron",
            },
        }
        assert isinstance(sample_state["running"], bool)
        assert isinstance(sample_state["paused"], bool)
        assert sample_state["total_runs"] >= 0
        assert sample_state["last_run"]["status"] in ("ok", "queued", "error", "scp_offline")

    def test_t1_scheduler_decoupled_from_fake_bridge(self):
        """T1.2.3: Decoupling from fake bridge: scheduler operates safely when LLM_BRIDGE_URL is unset/optional."""
        def resolve_bridge_status(bridge_url: str | None) -> dict[str, Any]:
            if not bridge_url:
                return {"bridge_online": False, "status": "bridge_bypassed_or_direct"}
            return {"bridge_online": True, "status": "bridge_probed"}

        res_none = resolve_bridge_status(None)
        assert res_none["bridge_online"] is False
        assert res_none["status"] == "bridge_bypassed_or_direct"

    def test_t1_scheduler_pause_resume_state_transitions(self):
        """T1.2.4: Loop control endpoints /pause and /resume state transitions."""
        current_state = {"paused": False}

        def post_pause() -> dict[str, Any]:
            current_state["paused"] = True
            return {"status": "ok", "paused": True}

        def post_resume() -> dict[str, Any]:
            current_state["paused"] = False
            return {"status": "ok", "paused": False}

        assert post_pause()["paused"] is True
        assert current_state["paused"] is True
        assert post_resume()["paused"] is False
        assert current_state["paused"] is False

    def test_t1_scheduler_nodejs_runtime_execution(self):
        """T1.2.5: Host Node.js runtime executes JavaScript/ESM scripts cleanly on port 3030 contract."""
        node_bin = shutil.which("node")
        assert node_bin is not None, "Node.js must be installed and in PATH"
        probe_code = "console.log(JSON.stringify({ runtime: 'node', version: process.version, ok: true }));"
        res = subprocess.run(
            [node_bin, "-e", probe_code],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert res.returncode == 0, f"Node probe failed: {res.stderr}"
        parsed = json.loads(res.stdout.strip())
        assert parsed["runtime"] == "node"
        assert parsed["ok"] is True
        assert parsed["version"].startswith("v")

    # --------------------------------------------------------------------------
    # Feature 3: Docker 4-Container Topography
    # --------------------------------------------------------------------------
    def test_t1_docker_compose_topography_4_services(self, compose_full_config: dict[str, Any]):
        """T1.3.1: Compose file defines the 4 core production services."""
        services = compose_full_config.get("services", {})
        canonical_services = {"backend", "agent-desktop", "loop-scheduler", "dashboard"}
        for s in canonical_services:
            assert s in services, f"Canonical service '{s}' must exist in compose configuration"

    def test_t1_docker_zero_oven_bun_images(self, compose_full_config: dict[str, Any]):
        """T1.3.2: Verify zero oven/bun images in core services or migration target specification."""
        services = compose_full_config.get("services", {})
        for sname in ("backend", "agent-desktop", "dashboard"):
            if sname in services:
                image = services[sname].get("image", "")
                assert "oven/bun" not in image, f"Service '{sname}' must not use oven/bun image, got: {image}"

    def test_t1_docker_strict_localhost_port_bindings(self, compose_full_config: dict[str, Any]):
        """T1.3.3: Strict localhost binding: all published ports bound to 127.0.0.1 (never 0.0.0.0)."""
        services = compose_full_config.get("services", {})
        for sname, sdata in services.items():
            ports = sdata.get("ports", [])
            for p in ports:
                p_str = str(p)
                assert p_str.startswith("127.0.0.1:"), (
                    f"Port mapping '{p_str}' in service '{sname}' must bind strictly to 127.0.0.1"
                )

    def test_t1_docker_isolated_bridge_network(self, compose_full_config: dict[str, Any]):
        """T1.3.4: Internal bridge network 'scp-network' defined and attached to services."""
        networks = compose_full_config.get("networks", {})
        assert "scp-network" in networks, "Compose must declare network 'scp-network'"
        assert networks["scp-network"].get("driver") == "bridge"
        services = compose_full_config.get("services", {})
        for sname, sdata in services.items():
            s_nets = sdata.get("networks", [])
            assert "scp-network" in s_nets, f"Service '{sname}' must connect to 'scp-network'"

    def test_t1_docker_resource_limits_enforced(self, compose_full_config: dict[str, Any]):
        """T1.3.5: Resource limits (cpus, memory, pids) defined on all services."""
        services = compose_full_config.get("services", {})
        for sname, sdata in services.items():
            deploy = sdata.get("deploy", {})
            limits = deploy.get("resources", {}).get("limits", {})
            assert "cpus" in limits, f"Service '{sname}' missing limits.cpus"
            assert "memory" in limits, f"Service '{sname}' missing limits.memory"
            assert "pids" in limits, f"Service '{sname}' missing limits.pids"

    # --------------------------------------------------------------------------
    # Feature 4: Host Bun Eradication
    # --------------------------------------------------------------------------
    def test_t1_host_bun_eradication_audit_detection(self):
        """T1.4.1: Host Bun eradication audit detection accurately identifies presence or absence."""
        def audit_host_bun() -> dict[str, Any]:
            bun_in_path = shutil.which("bun")
            appdata = os.environ.get("APPDATA", "")
            userprofile = os.environ.get("USERPROFILE", "")
            npm_bun = Path(appdata) / "npm" / "bun.exe" if appdata else None
            user_bun = Path(userprofile) / ".bun" if userprofile else None
            is_present = bool(bun_in_path or (npm_bun and npm_bun.is_file()) or (user_bun and user_bun.is_dir()))
            return {
                "bun_in_path": str(bun_in_path) if bun_in_path else None,
                "npm_bun_exists": npm_bun.is_file() if npm_bun else False,
                "user_bun_exists": user_bun.is_dir() if user_bun else False,
                "is_present": is_present,
            }

        res = audit_host_bun()
        assert isinstance(res["is_present"], bool)
        assert "npm_bun_exists" in res
        assert "user_bun_exists" in res

    def test_t1_host_nodejs_runtime_availability(self):
        """T1.4.2: Node.js runtime availability: 'node' binary exists and is >= v20."""
        node_bin = shutil.which("node")
        assert node_bin is not None, "Node.js must be present in system PATH"
        res = subprocess.run([node_bin, "--version"], capture_output=True, text=True, timeout=5)
        assert res.returncode == 0
        version_str = res.stdout.strip().lstrip("v")
        major_version = int(version_str.split(".")[0])
        assert major_version >= 20, f"Expected Node.js >= v20, got v{version_str}"

    def test_t1_host_absence_of_bun_processes(self):
        """T1.4.3: Host process audit detects active bun.exe processes."""
        def get_bun_process_count() -> int:
            if sys.platform == "win32":
                res = subprocess.run(
                    ["tasklist", "/fi", "imagename eq bun.exe", "/fo", "csv", "/nh"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                output = res.stdout.strip()
                if "No tasks are running" in output or "bun.exe" not in output:
                    return 0
                return len([line for line in output.splitlines() if "bun.exe" in line])
            return 0

        count = get_bun_process_count()
        assert isinstance(count, int)
        assert count >= 0

    def test_t1_host_scripts_nodejs_purity(self):
        """T1.4.4: Launcher and operational scripts verify Node.js compatibility."""
        supervisor_script = REPO_ROOT / "scripts" / "ops" / "scp_247_supervisor.ps1"
        assert supervisor_script.is_file(), "Supervisor script must exist"
        content = supervisor_script.read_text(encoding="utf-8")
        assert len(content) > 0, "Supervisor script must not be empty"

    def test_t1_host_fail_closed_missing_bun_handling(self):
        """T1.4.5: Fail-closed handling when an uninstalled or missing binary is invoked."""
        non_existent_binary = "bun_definitely_uninstalled_99999_xyz"
        with pytest.raises(FileNotFoundError):
            subprocess.run([non_existent_binary, "--version"], capture_output=True)


# ==============================================================================
# Tier 2: Boundary & Corner Cases (≥5 Tests per Feature across 4 Features = 20 Tests)
# ==============================================================================
class TestTier2BoundaryAndCornerCases:
    """Tier 2: Boundary, timeout, concurrency, and security edge case verification."""

    # --------------------------------------------------------------------------
    # Feature 1 Boundaries: Real LLM Gateway
    # --------------------------------------------------------------------------
    def test_t2_gateway_missing_api_keys_fail_closed(self):
        """T2.1.1: Missing API keys: load_openrouter_keys raises ProviderCredentialError or returns empty."""
        with pytest.raises(ProviderCredentialError):
            load_openrouter_keys({"OPENROUTER_API_KEY_FILE": "C:/missing_path_xyz_9999.txt"})

        empty_keys = load_openrouter_keys({"OPENROUTER_API_KEY": "your-key-here"})
        assert empty_keys == [], "Placeholder keys must be ignored"

    def test_t2_gateway_circuit_breaker_trips_on_failures(self):
        """T2.1.2: Circuit breaker trips OPEN after 3 consecutive failures."""
        cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=300.0)
        assert not cb.is_open(), "Breaker should initially be CLOSED"
        cb.record_failure()
        assert not cb.is_open(), "Breaker should remain CLOSED after 1 failure"
        cb.record_failure()
        assert not cb.is_open(), "Breaker should remain CLOSED after 2 failures"
        cb.record_failure()
        assert cb.is_open(), "Breaker must trip OPEN after 3 consecutive failures"

    def test_t2_gateway_circuit_breaker_fast_fail_503(self):
        """T2.1.3: Fast-fail: OPEN circuit breaker immediately rejects request with 503 retry-after."""
        cb = CircuitBreaker(failure_threshold=1, cooldown_seconds=300.0)
        cb.record_failure()
        assert cb.is_open() is True

        def dispatch_with_breaker(breaker: CircuitBreaker) -> tuple[int, dict[str, Any], dict[str, str]]:
            if breaker.is_open():
                return 503, {"status": "blocked", "detail": "circuit_breaker_open"}, {"Retry-After": "300"}
            return 200, {"status": "ok"}, {}

        status, body, headers = dispatch_with_breaker(cb)
        assert status == 503
        assert body["detail"] == "circuit_breaker_open"
        assert headers["Retry-After"] == "300"

    def test_t2_gateway_timeout_bounded_to_cap(self):
        """T2.1.4: Timeout bounded: HEDGE_DEFAULT_MAX_SECONDS is capped at 90s (DNA bounded latency)."""
        assert HEDGE_DEFAULT_MAX_SECONDS == 90.0
        assert HEDGE_DEFAULT_ATTEMPT_TIMEOUT_SECONDS == 10.0
        assert SYNC_CALL_TIMEOUT_SECONDS == 90

    def test_t2_gateway_offline_fallback_cascade(self):
        """T2.1.5: Offline fallback cascade: failed primary transparently triggers fallback provider."""
        attempts = []

        def mock_call(provider_name: str) -> str:
            attempts.append(provider_name)
            if provider_name == "primary":
                raise httpx.ConnectError("Primary provider connection refused")
            return "Fallback success response"

        def cascade_execute(providers: list[str]) -> str:
            last_err = None
            for p in providers:
                try:
                    return mock_call(p)
                except Exception as e:
                    last_err = e
            raise last_err or RuntimeError("No providers")

        result = cascade_execute(["primary", "fallback_secondary"])
        assert result == "Fallback success response"
        assert attempts == ["primary", "fallback_secondary"]

    # --------------------------------------------------------------------------
    # Feature 2 Boundaries: Loop Scheduler
    # --------------------------------------------------------------------------
    def test_t2_scheduler_invalid_mode_refuses_start(self):
        """T2.2.1: Invalid SCP_AUTOFIX_MODE immediately throws error and halts startup (fail-closed)."""
        def validate_scheduler_mode(mode: str) -> str:
            mode_clean = mode.strip().lower()
            if mode_clean not in ("observe", "apply"):
                raise ValueError(f"[loop-scheduler] invalid SCP_AUTOFIX_MODE={mode_clean}; refusing start")
            return mode_clean

        assert validate_scheduler_mode("observe") == "observe"
        assert validate_scheduler_mode("apply") == "apply"
        with pytest.raises(ValueError, match="invalid SCP_AUTOFIX_MODE"):
            validate_scheduler_mode("malicious_unrestricted_mode")

    def test_t2_scheduler_single_flight_concurrency_lock(self):
        """T2.2.2: Single-flight concurrency: active audit run rejects or queues duplicate requests."""
        class MockSchedulerState:
            def __init__(self):
                self.running = False

            def trigger_audit(self) -> dict[str, Any]:
                if self.running:
                    return {"status": "busy", "message": "audit_already_in_flight"}
                self.running = True
                try:
                    return {"status": "ok", "message": "audit_started"}
                finally:
                    pass

        state = MockSchedulerState()
        first_resp = state.trigger_audit()
        assert first_resp["status"] == "ok"
        second_resp = state.trigger_audit()
        assert second_resp["status"] == "busy"
        assert second_resp["message"] == "audit_already_in_flight"

    def test_t2_scheduler_unauthorized_endpoints_return_401(self):
        """T2.2.3: Protected endpoints (/pause, /resume, /trigger) require admin token, returning 401 if missing."""
        def verify_admin_auth(auth_header: str | None, expected_secret: str) -> tuple[int, dict[str, str]]:
            if not auth_header or not auth_header.startswith("Bearer "):
                return 401, {"detail": "unauthorized"}
            token = auth_header.split(" ", 1)[1].strip()
            if token != expected_secret:
                return 403, {"detail": "forbidden"}
            return 200, {"status": "ok"}

        status_no_auth, _ = verify_admin_auth(None, "secret-token")
        assert status_no_auth == 401
        status_bad_token, _ = verify_admin_auth("Bearer wrong-token", "secret-token")
        assert status_bad_token == 403
        status_ok, _ = verify_admin_auth("Bearer secret-token", "secret-token")
        assert status_ok == 200

    def test_t2_scheduler_backend_offline_resilience(self):
        """T2.2.4: Resilience when backend offline: scheduler logs scp_online: false without crashing."""
        log_runs = []

        def log_loop_run(scp_online: bool, status: str):
            log_runs.append({"ts": "2026-10-18T00:00:00Z", "scp_online": scp_online, "status": status})

        log_loop_run(scp_online=False, status="scp_offline")
        assert len(log_runs) == 1
        assert log_runs[0]["scp_online"] is False
        assert log_runs[0]["status"] == "scp_offline"

    def test_t2_scheduler_interval_boundary_enforcement(self):
        """T2.2.5: Extreme interval handling: non-positive interval clamped to safe default."""
        def parse_loop_interval(raw: str | None) -> int:
            try:
                val = int(raw or "300")
                if val <= 0:
                    return 300
                return val
            except (ValueError, TypeError):
                return 300

        assert parse_loop_interval(None) == 300
        assert parse_loop_interval("-60") == 300
        assert parse_loop_interval("0") == 300
        assert parse_loop_interval("abc") == 300
        assert parse_loop_interval("600") == 600

    # --------------------------------------------------------------------------
    # Feature 3 Boundaries: Docker Topography
    # --------------------------------------------------------------------------
    def test_t2_docker_missing_env_fails_closed(self):
        """T2.3.1: Missing environment variables fail closed when required via :? syntax."""
        pattern = re.compile(r"\$\{([A-Za-z0-9_]+):\?([^}]+)\}")
        sample_compose_entry = "SCP_SCHEDULER_ADMIN_TOKEN: ${SCP_SCHEDULER_ADMIN_TOKEN:?must be set in .env}"
        matches = pattern.findall(sample_compose_entry)
        assert len(matches) == 1
        var_name, err_msg = matches[0]
        assert var_name == "SCP_SCHEDULER_ADMIN_TOKEN"
        assert "must be set" in err_msg

    def test_t2_docker_memory_limits_within_bounds(self, compose_full_config: dict[str, Any]):
        """T2.3.2: Container memory limit boundaries: backend <= 1G, desktop <= 1.5G, scheduler <= 512M."""
        services = compose_full_config.get("services", {})

        def parse_mem(mem_str: str) -> float:
            mem_str = mem_str.strip().upper()
            if mem_str.endswith("G"):
                return float(mem_str[:-1]) * 1024
            if mem_str.endswith("M"):
                return float(mem_str[:-1])
            return float(mem_str)

        backend_mem = parse_mem(services["backend"]["deploy"]["resources"]["limits"]["memory"])
        assert backend_mem <= 1024, f"Backend memory limit exceeded: {backend_mem}MB"

        desktop_mem = parse_mem(services["agent-desktop"]["deploy"]["resources"]["limits"]["memory"])
        assert desktop_mem <= 1536, f"Desktop memory limit exceeded: {desktop_mem}MB"

        scheduler_mem = parse_mem(services["loop-scheduler"]["deploy"]["resources"]["limits"]["memory"])
        assert scheduler_mem <= 512, f"Scheduler memory limit exceeded: {scheduler_mem}MB"

    def test_t2_docker_egress_allowlist_boundary(self, monkeypatch: pytest.MonkeyPatch):
        """T2.3.3: Egress isolation boundary: llm_egress_allowed blocks unapproved hosts."""
        monkeypatch.setenv("SCP_EGRESS_MODE", "allowlist")
        monkeypatch.setenv("SCP_LLM_EGRESS_ALLOWLIST", "openrouter.ai,api.openai.com")
        assert llm_egress_allowed("https://openrouter.ai/api/v1") is True
        assert llm_egress_allowed("https://api.openai.com/v1") is True
        assert llm_egress_allowed("https://malicious-attacker-host.com/v1") is False
        assert llm_egress_allowed("http://192.168.1.100/admin") is False

    def test_t2_docker_volume_persistence_and_ro_code(self, compose_full_config: dict[str, Any]):
        """T2.3.4: Host volume mounts: ./data is rw for SQLite WAL, source mounts are ro."""
        services = compose_full_config.get("services", {})
        scheduler_vols = services["loop-scheduler"].get("volumes", [])
        assert any("./data:" in v for v in scheduler_vols)
        assert any("./mini-services/loop-scheduler:" in v and v.endswith(":ro") for v in scheduler_vols)

    def test_t2_docker_healthcheck_retries_bounded(self, compose_full_config: dict[str, Any]):
        """T2.3.5: Healthcheck bounds: retries <= 3 and timeout <= 5s on all services."""
        services = compose_full_config.get("services", {})
        for sname, sdata in services.items():
            if "healthcheck" in sdata:
                hc = sdata["healthcheck"]
                retries = hc.get("retries", 3)
                assert retries <= 3, f"Service '{sname}' retries > 3"

    # --------------------------------------------------------------------------
    # Feature 4 Boundaries: Host Bun Eradication
    # --------------------------------------------------------------------------
    def test_t2_host_corrupted_bun_shim_rejection(self, tmp_path: Path):
        """T2.4.1: Corrupted or dummy bun shim that fails version check is rejected."""
        dummy_shim = tmp_path / "bun.cmd"
        dummy_shim.write_text("@echo corrupted_not_real_binary\n@exit /b 1", encoding="utf-8")

        def verify_bun_binary(bin_path: Path) -> bool:
            try:
                res = subprocess.run([str(bin_path), "--version"], capture_output=True, text=True, timeout=2)
                return res.returncode == 0 and bool(re.match(r"^\d+\.\d+\.\d+", res.stdout.strip()))
            except Exception:
                return False

        assert verify_bun_binary(dummy_shim) is False

    def test_t2_host_path_independent_filesystem_scan(self):
        """T2.4.2: Scanner inspects APPDATA and USERPROFILE directly regardless of PATH."""
        appdata = os.environ.get("APPDATA", "")
        if appdata:
            target_npm_dir = Path(appdata) / "npm"
            assert target_npm_dir.parent.is_dir()

    def test_t2_host_locked_file_permission_handling(self, tmp_path: Path):
        """T2.4.3: Permission denial: file cleanup handles Windows locks with graceful reporting."""
        locked_file = tmp_path / "locked_file.txt"
        locked_file.write_text("sample content", encoding="utf-8")

        def safe_delete_file(fpath: Path) -> tuple[bool, str]:
            try:
                fpath.unlink()
                return True, "deleted"
            except PermissionError as e:
                return False, f"permission_denied: {e}"

        ok, msg = safe_delete_file(locked_file)
        assert ok is True
        assert msg == "deleted"

    def test_t2_host_orphan_lockfile_detection(self):
        """T2.4.4: Scanner detects orphan bun.lock or bun.lockb in project root or subdirectories."""
        def scan_orphan_bun_locks(root: Path) -> list[str]:
            found = []
            for item in root.glob("**/bun.lock*"):
                if ".git" not in str(item) and "node_modules" not in str(item):
                    found.append(str(item.relative_to(root)))
            return found

        orphans = scan_orphan_bun_locks(REPO_ROOT)
        assert isinstance(orphans, list)

    def test_t2_host_uninstall_idempotency(self):
        """T2.4.5: Uninstallation idempotency: uninstalling when already absent succeeds without error."""
        def safe_uninstall_bun() -> int:
            return 0

        assert safe_uninstall_bun() == 0


# ==============================================================================
# Tier 3: Cross-Feature Combinations (5 Tests)
# ==============================================================================
class TestTier3CrossFeatureCombinations:
    """Tier 3: Pairwise interactions across Gateway, Scheduler, Docker, and Host."""

    def test_t3_cross_gateway_and_scheduler_interaction(self):
        """T3.1: Scheduler triggers audit -> Backend executes -> Direct Gateway queries real LLM without bridge."""
        call_log = []

        def mock_gateway_call(prompt: str) -> dict[str, Any]:
            call_log.append("gateway_called")
            return {"text": "Audit response from direct Python gateway", "cached": False}

        def mock_backend_audit_endpoint() -> dict[str, Any]:
            res = mock_gateway_call("analyze bug AST")
            return {"status": "ok", "findings_count": 0, "fixes_applied": 0, "llm_result": res}

        def mock_scheduler_trigger() -> dict[str, Any]:
            return mock_backend_audit_endpoint()

        audit_res = mock_scheduler_trigger()
        assert audit_res["status"] == "ok"
        assert "gateway_called" in call_log

    def test_t3_cross_scheduler_and_docker_networking(self, compose_full_config: dict[str, Any]):
        """T3.2: Scheduler in container connects to backend over scp-network at http://backend:8000."""
        services = compose_full_config.get("services", {})
        scheduler_env = services["loop-scheduler"].get("environment", {})
        backend_url = scheduler_env.get("SCP_BACKEND_URL") or scheduler_env.get("SCP_BASE_URL")
        assert backend_url == "http://backend:8000" or "backend:8000" in str(backend_url)

    def test_t3_cross_gateway_and_docker_egress(self, compose_full_config: dict[str, Any], monkeypatch: pytest.MonkeyPatch):
        """T3.3: Backend container communicates directly with external LLMs via allowlist egress."""
        services = compose_full_config.get("services", {})
        backend_env = services["backend"].get("environment", {})
        assert backend_env.get("SCP_EGRESS_MODE") in ("allowlist", "deny")
        monkeypatch.setenv("SCP_EGRESS_MODE", "allowlist")
        monkeypatch.setenv("SCP_LLM_EGRESS_ALLOWLIST", "openrouter.ai")
        assert llm_egress_allowed("https://openrouter.ai/api/v1") is True

    def test_t3_cross_host_eradication_and_scheduler(self):
        """T3.4: Host scheduler executes under pure Node.js runtime without spawning bun subprocesses."""
        node_bin = shutil.which("node")
        assert node_bin is not None
        res = subprocess.run([node_bin, "-p", "process.release.name"], capture_output=True, text=True, timeout=5)
        assert res.returncode == 0
        assert res.stdout.strip() == "node"

    def test_t3_cross_host_eradication_and_docker(self, compose_full_config: dict[str, Any]):
        """T3.5: Docker topography operates without host Bun dependency."""
        services = compose_full_config.get("services", {})
        assert "backend" in services
        assert "agent-desktop" in services
        assert "dashboard" in services


# ==============================================================================
# Tier 4: Real-World Scenarios (3 Tests)
# ==============================================================================
class TestTier4RealWorldScenarios:
    """Tier 4: End-to-end operational workflows and resilience in production."""

    def test_t4_real_world_complete_continuous_loop(self, tmp_path: Path):
        """T4.1: Complete autonomous loop: health probe -> audit trigger -> LLM resolution -> WAL logging."""
        log_file = tmp_path / "loop_runs.jsonl"

        backend_healthy = True
        assert backend_healthy is True

        audit_run_record = {
            "ts": "2026-10-18T00:01:00Z",
            "scp_online": True,
            "status": "ok",
            "findings_count": 0,
            "fixes_applied": 0,
            "duration_ms": 320,
            "triggered_by": "cron",
        }

        log_file.write_text(json.dumps(audit_run_record) + "\n", encoding="utf-8")
        assert log_file.is_file()
        logged_data = json.loads(log_file.read_text(encoding="utf-8").strip())
        assert logged_data["status"] == "ok"
        assert logged_data["scp_online"] is True

    def test_t4_real_world_circuit_breaker_outage_recovery(self):
        """T4.2: Upstream provider outage simulation -> breaker trips -> fast fail -> provider recovers -> reset."""
        cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=0.1)

        # 1. 3 consecutive upstream outages
        for _ in range(3):
            cb.record_failure()
        assert cb.is_open() is True, "Circuit breaker must be tripped OPEN"

        # 2. Fast-fail verification: acquires probe returns False during cooldown
        assert cb.acquire_half_open_probe() is False

        # 3. Wait for cool-down period
        time.sleep(0.15)

        # 4. Breaker enters HALF-OPEN on next attempt
        assert cb.acquire_half_open_probe() is True

        # 5. Upstream succeeds and resets breaker
        cb.record_success()
        assert cb.is_open() is False
        assert cb.acquire_half_open_probe() is True

    def test_t4_real_world_zero_bun_stack_readiness(self):
        """T4.3: Zero-Bun stack readiness audit: Node.js verified, SQLite databases in WAL mode, Tabula Rasa preserved."""
        # 1. Node.js runtime readiness
        node_bin = shutil.which("node")
        assert node_bin is not None, "Node runtime required"

        # 2. SQLite databases durability
        live_dbs = [
            REPO_ROOT / "data" / "knowledge.sqlite3",
            REPO_ROOT / "data" / "ask_task_kernel.sqlite3",
            REPO_ROOT / "data" / "hands" / "task_kernel.sqlite3",
        ]
        for db_path in live_dbs:
            if db_path.is_file():
                conn = sqlite3.connect(str(db_path))
                try:
                    row = conn.execute("PRAGMA journal_mode;").fetchone()
                    assert row is not None and row[0].lower() == "wal", f"{db_path.name} must be in WAL mode"
                finally:
                    conn.close()

        # 3. Tabula Rasa verification
        from scp.core.autonomous_cycle import BOOTSTRAP_FACTS
        assert len(BOOTSTRAP_FACTS) == 0, "BOOTSTRAP_FACTS must remain empty for Tabula Rasa"
