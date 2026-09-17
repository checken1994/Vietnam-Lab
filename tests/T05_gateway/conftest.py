"""T05 uses real pricing/egress guards, isolated state and synthetic transport.

Network-backed account/inference evidence is outside this contract test profile.
Any accidental HTTP request is a failure even if product code catches it.
"""

from __future__ import annotations

import itertools
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from scp.contracts.data_class import DataClass
from scp.epistemic.evidence_store import EvidenceStore
from scp.epistemic.evidence_writer import GovernedEvidenceWriter
from scp.governance.privacy import PrivacyWriteGate
from scp.llm_gateway import client, free_catalog, zero_cost_runtime
from scp.llm_gateway.zero_cost_guard import PricingProofStore

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def isolated_gateway_state(tmp_path, monkeypatch):
    data_root = tmp_path / "gateway-state"
    proof_db = data_root / "zero_cost.sqlite"
    values = {
        "SCP_MODE": "test",
        "SCP_PRODUCTION_MODE": "0",
        "SCP_API_PROFILE": "full",
        "SCP_DATA_DIR": str(data_root),
        "SCP_ZERO_COST_PROOF_DB": str(proof_db),
        "SCP_LLM_COST_MODE": "free_only",
        "SCP_ALLOW_PAID_FALLBACK": "0",
        "SCP_MAX_LLM_COST_USD": "0",
        "SCP_FREE_REQUIRE_PRICE_PROOF": "1",
        "SCP_FREE_FAIL_IF_PRICE_UNKNOWN": "1",
        "SCP_EGRESS_MODE": "allowlist",
        "SCP_LLM_EGRESS_ALLOWLIST": "openrouter.ai",
        "SCP_LLM_FALLBACK_PROVIDERS": "",
        "SCP_LLM_PROVIDER_MODE": "openrouter",
        "OPENROUTER_MODEL": "unverified-primary",
        "OPENROUTER_MODEL_CHAT": "unverified-chat",
        "OPENROUTER_BASE_URL": "https://openrouter.ai/api/v1",
        "SCP_BUDGET_ROUTING": "0",
    }
    for name in list(os.environ):
        if name.endswith(("_API_KEY", "_API_KEY_2", "_API_KEY_3")):
            monkeypatch.delenv(name)
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    # Restore pre-existing singleton references after each test without closing
    # another caller's store. Only this fixture's own temporary stores are closed.
    monkeypatch.setattr(zero_cost_runtime, "_store", None)
    monkeypatch.setattr(zero_cost_runtime, "_guard", None)
    monkeypatch.setattr(free_catalog, "_FOUNDATION", data_root)
    monkeypatch.setattr(client.OpenRouterProvider, "_API_KEYS", ["test-key"])
    monkeypatch.setattr(
        client.OpenRouterProvider, "_key_cycle", itertools.cycle(["test-key"])
    )
    monkeypatch.setattr(client.OpenRouterProvider, "_dynamic_models_loaded", True)
    unexpected_http = []

    def forbidden_sync(*_args, **_kwargs):
        unexpected_http.append("sync")
        raise AssertionError("T05 must inject a transport before any network request")

    async def forbidden_async(*_args, **_kwargs):
        unexpected_http.append("async")
        raise AssertionError("T05 must inject a transport before any network request")

    monkeypatch.setattr(httpx.Client, "send", forbidden_sync)
    monkeypatch.setattr(httpx.AsyncClient, "send", forbidden_async)
    # Windows fix: close any open _store handle BEFORE setting to None.
    # SQLite WAL mode keeps auxiliary files (.wal/.shm) locked until conn.close().
    # If we just setattr None without closing, the old handle leaks across tests.
    if zero_cost_runtime._store is not None:
        try:
            zero_cost_runtime._store.close()
        except Exception:
            pass
    yield
    # Post-test teardown: close store opened during this test's run.
    if zero_cost_runtime._store is not None:
        try:
            zero_cost_runtime._store.close()
        except Exception:
            pass
    assert unexpected_http == [], "Product swallowed an accidental network attempt"


@pytest.fixture
def pricing_runtime(tmp_path, isolated_gateway_state):
    store = PricingProofStore(zero_cost_runtime._runtime_store_path())
    evidence = EvidenceStore(tmp_path / "catalog.sqlite", tmp_path / "catalog-objects")
    writer = GovernedEvidenceWriter(
        evidence, PrivacyWriteGate(ROOT / "spec/data_policies.yaml")
    )

    def seed(
        model,
        *,
        provider="openrouter",
        prompt="0",
        completion="0",
        observed=None,
        expires=None,
    ):
        now = observed or datetime.now(timezone.utc)
        observation = writer.observe(
            kind="HTTP_RESPONSE",
            content=f"{provider}:{model}:{prompt}:{completion}".encode(),
            collector_id="t05-fixture",
            collector_version="1",
            data_class=DataClass.PUBLIC,
        )
        return store.record(
            provider=provider,
            model=model,
            prompt_price=prompt,
            completion_price=completion,
            observed_at=now.isoformat(),
            expires_at=(expires or now + timedelta(minutes=5)).isoformat(),
            catalog_hash=observation["content_hash"],
            evidence_id=observation["evidence_id"],
        )

    yield seed
    store.close()
    evidence.db.close()
