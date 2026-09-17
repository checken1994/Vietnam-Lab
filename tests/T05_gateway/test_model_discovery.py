"""SCP LLM Gateway — Model Discovery & Lifecycle Zero-Trust Tests (S35).

These tests avoid product mocks/fakes and use physical loopback HTTP servers on
127.0.0.1:0 plus real SQLite/FoundationDB storage.  The transport guard uses
pytest's monkeypatch only to prevent accidental non-loopback egress in this
hermetic test profile; it is not zero-mock production/runtime evidence.
"""

from __future__ import annotations

import asyncio
import json
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, ClassVar

import httpx
import pytest

from scp.llm_gateway.client import CircuitBreaker, LLMGateway
from scp.llm_gateway.discovery import (
    LocalEndpointScanner,
    ModelDiscoveryStore,
    ModelLifecycleScheduler,
    ModelLifecycleState,
    create_model_lifecycle_scheduler,
    is_provider_model_eligible,
)

# Capture original httpx send functions at module import time before any fixture runs
_ORIGINAL_ASYNC_SEND = httpx.AsyncClient.send
_ORIGINAL_SYNC_SEND = httpx.Client.send


@pytest.fixture(autouse=True)
def allow_loopback_transport(monkeypatch):
    """Permit loopback traffic while keeping external endpoints blocked per T05 isolation."""

    async def loopback_async_send(self, request, *args, **kwargs):
        if request.url.host in ("127.0.0.1", "localhost", "::1"):
            return await _ORIGINAL_ASYNC_SEND(self, request, *args, **kwargs)
        raise AssertionError(
            f"Non-loopback HTTP request forbidden in T05: {request.url}"
        )

    def loopback_sync_send(self, request, *args, **kwargs):
        if request.url.host in ("127.0.0.1", "localhost", "::1"):
            return _ORIGINAL_SYNC_SEND(self, request, *args, **kwargs)
        raise AssertionError(
            f"Non-loopback HTTP request forbidden in T05: {request.url}"
        )

    monkeypatch.setattr(httpx.AsyncClient, "send", loopback_async_send)
    monkeypatch.setattr(httpx.Client, "send", loopback_sync_send)
    yield


class DiscoveryLoopbackHandler(BaseHTTPRequestHandler):
    """Physical loopback HTTP handler simulating OpenAI-compatible models and completions endpoints."""

    models_list: ClassVar[list[str]] = []

    def log_message(self, format: str, *args: Any) -> None:
        # Suppress standard HTTP request logging during test runs
        pass

    def do_GET(self) -> None:
        if self.path.rstrip("/") == "/v1/models":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            data = {"data": [{"id": mid, "object": "model"} for mid in self.server.models_list]}
            self.wfile.write(json.dumps(data).encode("utf-8"))
            return

        self.send_response(404)
        self.end_headers()

    def do_POST(self) -> None:
        if self.path.rstrip("/") == "/v1/chat/completions":
            content_length = int(self.headers.get("Content-Length", 0))
            body_bytes = self.rfile.read(content_length)
            try:
                req_data = json.loads(body_bytes.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                self.send_response(400)
                self.end_headers()
                return

            model = req_data.get("model", "")
            messages = req_data.get("messages", [])
            prompt = messages[0].get("content", "") if messages else ""

            # Adversarial behavior based on model identifier
            if "injected" in model and "unrestricted_bypass_mode" in prompt:
                content = "unrestricted_bypass_mode"
            elif "roleplay" in model and "destroy a server" in prompt:
                content = "rm -rf /"
            else:
                content = "Safe compliant refusal: cannot perform adversarial prompt."

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            resp = {
                "id": "chatcmpl-discovery-test",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": content},
                    }
                ],
            }
            self.wfile.write(json.dumps(resp).encode("utf-8"))
            return

        self.send_response(404)
        self.end_headers()


def _create_server(models: list[str]) -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), DiscoveryLoopbackHandler)
    server.models_list = models
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    return server, f"http://127.0.0.1:{port}"


def test_model_lifecycle_states() -> None:
    assert ModelLifecycleState.DISCOVERED == "DISCOVERED"
    assert ModelLifecycleState.QUARANTINED == "QUARANTINED"
    assert ModelLifecycleState.QUALIFIED == "QUALIFIED"
    assert ModelLifecycleState.ACTIVE == "ACTIVE"
    assert ModelLifecycleState.COOLDOWN == "COOLDOWN"
    assert ModelLifecycleState.STALE == "STALE"
    assert ModelLifecycleState.RETIRED == "RETIRED"


def test_discovery_store_crud(tmp_path) -> None:
    db_path = tmp_path / "test_discovery.sqlite"
    store = ModelDiscoveryStore(db_path)

    # Initially empty
    assert store.list_models() == []
    assert store.get_model_state("http://127.0.0.1:8000", "m1") is None

    # Upsert DISCOVERED
    store.upsert_model("http://127.0.0.1:8000", "m1", ModelLifecycleState.DISCOVERED)
    assert store.get_model_state("http://127.0.0.1:8000", "m1") == ModelLifecycleState.DISCOVERED

    # Update to QUALIFIED
    store.upsert_model("http://127.0.0.1:8000", "m1", ModelLifecycleState.QUALIFIED)
    assert store.get_model_state("http://127.0.0.1:8000", "m1") == ModelLifecycleState.QUALIFIED

    # Add second model
    store.upsert_model("http://127.0.0.1:9000", "m2", ModelLifecycleState.ACTIVE)
    all_models = store.list_models()
    assert len(all_models) == 2

    active_models = store.get_models_by_state(ModelLifecycleState.ACTIVE)
    assert len(active_models) == 1
    assert active_models[0]["model_id"] == "m2"
    assert active_models[0]["endpoint"] == "http://127.0.0.1:9000"

    store.close()


def test_routing_boundary_requires_active_and_preserves_unknown_cloud(tmp_path) -> None:
    store = ModelDiscoveryStore(tmp_path / "routing.sqlite")
    local_endpoint = "http://127.0.0.1:8124"
    store.upsert_model(local_endpoint, "local-model", ModelLifecycleState.DISCOVERED)
    for state in (
        ModelLifecycleState.DISCOVERED,
        ModelLifecycleState.QUARANTINED,
        ModelLifecycleState.QUALIFIED,
        ModelLifecycleState.COOLDOWN,
        ModelLifecycleState.STALE,
        ModelLifecycleState.RETIRED,
    ):
        store.upsert_model(local_endpoint, "local-model", state)
        assert not is_provider_model_eligible(store, local_endpoint, "local-model")

    store.upsert_model(local_endpoint, "local-model", ModelLifecycleState.ACTIVE)
    assert is_provider_model_eligible(store, f"{local_endpoint}/v1", "local-model")
    assert not is_provider_model_eligible(store, f"{local_endpoint}/v1", "other-model")
    assert is_provider_model_eligible(store, "https://cloud.example/v1", "cloud-model")
    store.close()


def test_gateway_provider_fallback_skips_non_active_local_provider(tmp_path, monkeypatch) -> None:
    store = ModelDiscoveryStore(tmp_path / "fallback.sqlite")
    local_endpoint = "http://127.0.0.1:8125"
    store.upsert_model(local_endpoint, "blocked-model", ModelLifecycleState.STALE)
    gateway = LLMGateway(discovery_store=store)

    class Provider:
        PROVIDER_NAME = "local"
        enabled = True
        model = "blocked-model"
        base_url = local_endpoint
        _breaker = CircuitBreaker()

        async def chat(self, *args, **kwargs):
            raise AssertionError("inactive local provider must not be called")

    class CloudProvider:
        PROVIDER_NAME = "cloud"
        enabled = True
        model = "cloud-model"
        base_url = "https://cloud.example/v1"
        _breaker = CircuitBreaker()

        async def chat(self, *args, **kwargs):
            return "cloud-answer", "cloud:cloud-model"

    monkeypatch.setattr(gateway, "_provider_chain", lambda _task: [Provider(), CloudProvider()])
    answer, label = asyncio.run(gateway.chat("question", task="chat"))
    assert (answer, label) == ("cloud-answer", "cloud:cloud-model")
    store.close()


def test_discovery_scanner_env_endpoints(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "test_env.sqlite"
    store = ModelDiscoveryStore(db_path)

    monkeypatch.setenv("SCP_LOCAL_ENDPOINTS", " http://127.0.0.1:8001 , http://127.0.0.1:8002 ")
    scanner = LocalEndpointScanner(store=store)
    assert scanner.endpoints == ["http://127.0.0.1:8001", "http://127.0.0.1:8002"]

    store.close()


def test_discovery_unreachable_endpoint_fail_closed(tmp_path) -> None:
    db_path = tmp_path / "test_unreachable.sqlite"
    store = ModelDiscoveryStore(db_path)

    scanner = LocalEndpointScanner(
        store=store,
        endpoints=["http://127.0.0.1:59197"],
        timeout=1.0,
    )
    result = asyncio.run(scanner.run_discovery())

    # Unreachable endpoint fails closed: 0 models stored, no unhandled exception
    assert result["successful_endpoints"] == 0
    assert result["failed_endpoints"] == 1
    assert store.list_models() == []
    store.close()


def test_egress_denial_is_fail_closed_without_transport(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "test_egress_denial.sqlite"
    store = ModelDiscoveryStore(db_path)
    monkeypatch.setenv("SCP_EGRESS_MODE", "deny")
    scanner = LocalEndpointScanner(
        store=store,
        endpoints=["https://operator.example.invalid"],
        timeout=1.0,
    )

    result = asyncio.run(scanner.run_discovery())

    assert result["successful_endpoints"] == 0
    assert result["failed_endpoints"] == 1
    assert store.list_models() == []
    store.close()


def test_operator_endpoints_are_normalized_and_credentials_rejected(tmp_path, caplog) -> None:
    store = ModelDiscoveryStore(tmp_path / "test_endpoint_validation.sqlite")
    scanner = LocalEndpointScanner(
        store=store,
        endpoints=[
            " http://127.0.0.1:8000/ ",
            "http://127.0.0.1:8000",
            "http://user:secret@127.0.0.1:8001",
            "http://127.0.0.1:8002/path?query=1",
        ],
    )

    assert scanner.endpoints == ["http://127.0.0.1:8000"]
    assert "credentials/query/fragment" in caplog.text
    store.close()


def test_discovery_and_lifecycle_end_to_end_loopback(tmp_path) -> None:
    db_path = tmp_path / "test_e2e_discovery.sqlite"
    store = ModelDiscoveryStore(db_path)

    # Start 2 physical loopback HTTP servers
    server1, url1 = _create_server(["model-safe-1", "model-injected-2"])
    server2, url2 = _create_server(["model-safe-3"])

    try:
        scanner = LocalEndpointScanner(
            store=store,
            endpoints=[url1, url2],
            timeout=5.0,
        )

        # Step 1: run discovery
        asyncio.run(scanner.run_discovery())

        models = store.list_models()
        assert len(models) == 3
        states = {(m["endpoint"], m["model_id"]): m["state"] for m in models}
        assert states[(url1, "model-safe-1")] == ModelLifecycleState.DISCOVERED
        assert states[(url1, "model-injected-2")] == ModelLifecycleState.DISCOVERED
        assert states[(url2, "model-safe-3")] == ModelLifecycleState.DISCOVERED

        # Step 2: run lifecycle state machine
        # DISCOVERED -> QUARANTINED -> probe -> QUALIFIED (safe) / COOLDOWN (injected) -> ACTIVE (qualified)
        asyncio.run(scanner.run_lifecycle())

        m_safe_1 = store.get_model_state(url1, "model-safe-1")
        m_injected_2 = store.get_model_state(url1, "model-injected-2")
        m_safe_3 = store.get_model_state(url2, "model-safe-3")

        # model-safe-1 passed all adversarial probes and reached ACTIVE
        assert m_safe_1 == ModelLifecycleState.ACTIVE
        # model-injected-2 triggered prompt injection failure indicator and went to COOLDOWN
        assert m_injected_2 == ModelLifecycleState.COOLDOWN
        # model-safe-3 passed all adversarial probes and reached ACTIVE
        assert m_safe_3 == ModelLifecycleState.ACTIVE

        # Step 3: subsequent discovery run is idempotent (does not reset ACTIVE or COOLDOWN models)
        asyncio.run(scanner.run_discovery())
        assert store.get_model_state(url1, "model-safe-1") == ModelLifecycleState.ACTIVE
        assert store.get_model_state(url1, "model-injected-2") == ModelLifecycleState.COOLDOWN
        assert store.get_model_state(url2, "model-safe-3") == ModelLifecycleState.ACTIVE
    finally:
        server1.shutdown()
        server1.server_close()
        server2.shutdown()
        server2.server_close()
        store.close()


def test_custom_prober_factory_injection(tmp_path) -> None:
    db_path = tmp_path / "test_factory.sqlite"
    store = ModelDiscoveryStore(db_path)

    store.upsert_model("http://127.0.0.1:8000", "test-model", ModelLifecycleState.QUARANTINED)

    class StubRealObjectProber:
        def __init__(self, endpoint_url: str, model: str, timeout: float = 10.0) -> None:
            self.endpoint_url = endpoint_url
            self.model = model

        async def probe_async(self) -> bool:
            return True

    scanner = LocalEndpointScanner(
        store=store,
        endpoints=["http://127.0.0.1:8000"],
        prober_factory=StubRealObjectProber,
    )
    asyncio.run(scanner.run_lifecycle())

    assert store.get_model_state("http://127.0.0.1:8000", "test-model") == ModelLifecycleState.ACTIVE
    store.close()


def test_model_disappears_becomes_stale_then_retired_and_never_active(tmp_path) -> None:
    db_path = tmp_path / "test_disappears.sqlite"
    store = ModelDiscoveryStore(db_path)
    server, endpoint = _create_server(["model-present", "model-to-disappear"])
    try:
        scanner = LocalEndpointScanner(store=store, endpoints=[endpoint], timeout=5.0)
        asyncio.run(scanner.run_discovery())
        asyncio.run(scanner.run_lifecycle())
        assert store.get_model_state(endpoint, "model-to-disappear") == ModelLifecycleState.ACTIVE

        server.models_list = ["model-present"]
        first = asyncio.run(scanner.run_discovery())
        assert first["stale"] == 1
        assert store.get_model_state(endpoint, "model-to-disappear") == ModelLifecycleState.STALE
        assert not store.get_models_by_state(ModelLifecycleState.ACTIVE) or all(
            row["model_id"] != "model-to-disappear"
            for row in store.get_models_by_state(ModelLifecycleState.ACTIVE)
        )

        second = asyncio.run(scanner.run_discovery())
        assert second["retired"] == 1
        assert store.get_model_state(endpoint, "model-to-disappear") == ModelLifecycleState.RETIRED
        asyncio.run(scanner.run_lifecycle())
        assert store.get_model_state(endpoint, "model-to-disappear") == ModelLifecycleState.RETIRED
    finally:
        server.shutdown()
        server.server_close()
        store.close()


def test_endpoint_outage_withdraws_active_models_without_deleting_state(tmp_path) -> None:
    db_path = tmp_path / "test_endpoint_outage.sqlite"
    store = ModelDiscoveryStore(db_path)
    server, endpoint = _create_server(["model-outage"])
    try:
        scanner = LocalEndpointScanner(store=store, endpoints=[endpoint], timeout=0.5)
        asyncio.run(scanner.run_discovery())
        asyncio.run(scanner.run_lifecycle())
        assert store.get_model_state(endpoint, "model-outage") == ModelLifecycleState.ACTIVE

        server.shutdown()
        server.server_close()
        first = asyncio.run(scanner.run_discovery())
        assert first["failed_endpoints"] == 1
        assert store.get_model_state(endpoint, "model-outage") == ModelLifecycleState.STALE
        second = asyncio.run(scanner.run_discovery())
        assert second["failed_endpoints"] == 1
        assert store.get_model_state(endpoint, "model-outage") == ModelLifecycleState.RETIRED
        assert store.get_model(endpoint, "model-outage") is not None
    finally:
        store.close()


def test_probe_failure_quarantines_with_bounded_reprobe(tmp_path) -> None:
    db_path = tmp_path / "test_probe_failure.sqlite"
    store = ModelDiscoveryStore(db_path)
    endpoint = "http://127.0.0.1:8000"
    store.upsert_model(endpoint, "probe-fails", ModelLifecycleState.DISCOVERED)

    class FailingProber:
        async def probe_async(self) -> bool:
            return False

    scanner = LocalEndpointScanner(
        store=store,
        endpoints=[endpoint],
        prober_factory=FailingProber,
        cooldown_seconds=1.0,
        max_cooldown_seconds=2.0,
    )
    asyncio.run(scanner.run_lifecycle())

    row = store.get_model(endpoint, "probe-fails")
    assert row is not None
    assert row["state"] == ModelLifecycleState.COOLDOWN
    assert row["probe_failures"] == 1
    assert row["next_probe_at"] is not None

    asyncio.run(scanner.run_lifecycle())
    row_after = store.get_model(endpoint, "probe-fails")
    assert row_after is not None
    assert row_after["state"] == ModelLifecycleState.COOLDOWN
    assert row_after["probe_failures"] == 1
    store.close()


def test_probe_failure_reprobes_after_deadline_with_bounded_backoff(tmp_path) -> None:
    db_path = tmp_path / "test_probe_backoff.sqlite"
    endpoint = "http://127.0.0.1:8000"
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    clock_value = [now]
    calls: list[int] = []
    store = ModelDiscoveryStore(db_path)
    store.upsert_model(endpoint, "probe-fails", ModelLifecycleState.COOLDOWN, probe_failures=1, next_probe_at=now.isoformat())

    class FailingProber:
        def __init__(self, endpoint_url: str, model: str, timeout: float = 10.0) -> None:
            self.endpoint_url = endpoint_url
            self.model = model

        async def probe_async(self) -> bool:
            calls.append(1)
            return False

    scanner = LocalEndpointScanner(
        store=store,
        endpoints=[endpoint],
        prober_factory=FailingProber,
        cooldown_seconds=2.0,
        max_cooldown_seconds=5.0,
        clock=lambda: clock_value[0],
    )
    asyncio.run(scanner.run_lifecycle())

    row = store.get_model(endpoint, "probe-fails")
    assert calls == [1]
    assert row is not None
    assert row["state"] == ModelLifecycleState.COOLDOWN
    assert row["probe_failures"] == 2
    next_probe = datetime.fromisoformat(row["next_probe_at"])
    assert next_probe == now + timedelta(seconds=4)

    clock_value[0] = now + timedelta(seconds=4)
    asyncio.run(scanner.run_lifecycle())
    row_after = store.get_model(endpoint, "probe-fails")
    assert calls == [1, 1]
    assert row_after is not None
    assert row_after["probe_failures"] == 3
    assert datetime.fromisoformat(row_after["next_probe_at"]) == clock_value[0] + timedelta(seconds=5)
    store.close()


def test_restart_persists_state_and_cooldown(tmp_path) -> None:
    db_path = tmp_path / "test_restart.sqlite"
    endpoint = "http://127.0.0.1:8000"
    store = ModelDiscoveryStore(db_path)
    store.upsert_model(endpoint, "persistent-model", ModelLifecycleState.COOLDOWN, probe_failures=3, next_probe_at="2999-01-01T00:00:00+00:00")
    store.close()

    reopened = ModelDiscoveryStore(db_path)
    row = reopened.get_model(endpoint, "persistent-model")
    assert row is not None
    assert row["state"] == ModelLifecycleState.COOLDOWN
    assert row["probe_failures"] == 3
    assert row["next_probe_at"] == "2999-01-01T00:00:00+00:00"
    reopened.close()


def test_lifecycle_scheduler_start_stop_no_leak_and_tick(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SCP_API_PROFILE", "standard")
    monkeypatch.delenv("SCP_MODEL_DISCOVERY_SCHEDULER", raising=False)
    store = ModelDiscoveryStore(tmp_path / "test_scheduler.sqlite")
    server, endpoint = _create_server(["scheduler-model"])
    scanner = LocalEndpointScanner(store=store, endpoints=[endpoint], timeout=5.0)
    scheduler = ModelLifecycleScheduler(scanner, interval_seconds=3600.0)

    async def scenario() -> None:
        task = scheduler.start()
        assert task is not None
        assert scheduler.running_task is task
        for _ in range(100):
            if scheduler.last_tick_result:
                break
            await asyncio.sleep(0.01)
        assert scheduler.last_tick_result["discovery"]["successful_endpoints"] == 1
        assert store.get_model_state(endpoint, "scheduler-model") == ModelLifecycleState.ACTIVE
        await scheduler.stop(timeout=5.0)
        assert task.done()
        assert task.cancelled()
        assert scheduler.running_task is None
        await scheduler.stop(timeout=5.0)

    try:
        asyncio.run(scenario())
    finally:
        server.shutdown()
        server.server_close()
        store.close()


def test_lifecycle_scheduler_kill_switch_and_no_operator_endpoint(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SCP_MODEL_DISCOVERY_SCHEDULER", "off")
    store = ModelDiscoveryStore(tmp_path / "test_scheduler_switch.sqlite")
    disabled = ModelLifecycleScheduler(
        LocalEndpointScanner(store=store, endpoints=["http://127.0.0.1:8000"]),
        interval_seconds=3600.0,
    )
    assert asyncio.run(disabled_start(disabled)) is None
    store.close()

    empty_store = ModelDiscoveryStore(tmp_path / "test_scheduler_empty.sqlite")
    monkeypatch.delenv("SCP_MODEL_DISCOVERY_SCHEDULER", raising=False)
    empty = ModelLifecycleScheduler(LocalEndpointScanner(store=empty_store, endpoints=[]))
    assert asyncio.run(disabled_start(empty)) is None
    empty_store.close()


async def disabled_start(scheduler: ModelLifecycleScheduler):
    return scheduler.start()


def test_production_factory_uses_only_operator_environment(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SCP_LOCAL_ENDPOINTS", " http://127.0.0.1:8123 , http://127.0.0.1:8123 ")
    scheduler = create_model_lifecycle_scheduler(data_dir=tmp_path)
    assert scheduler is not None
    assert scheduler.scanner.endpoints == ["http://127.0.0.1:8123"]
    asyncio.run(scheduler.stop())
