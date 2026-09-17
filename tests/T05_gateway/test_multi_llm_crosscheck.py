from __future__ import annotations

import asyncio

import pytest

from scp.llm_gateway.discovery import ModelDiscoveryStore, ModelLifecycleState


class FakeProvider:
    def __init__(self, name: str, answer: str, *, model: str | None = None, base_url: str = ""):
        self.PROVIDER_NAME = name
        self.answer = answer
        self.calls = 0
        self.enabled = True
        self.model = model or f"{name}-model"
        self.base_url = base_url

    async def chat(self, question: str, context: str = "", system_prompt: str = "", prioritize_free: bool = False):
        self.calls += 1
        return self.answer, f"{self.PROVIDER_NAME}:{self.model}"


class FakeGateway:
    def __init__(self, providers):
        self.providers = list(providers)

    def provider_candidates(self, task: str):
        assert task == "judge"
        return list(self.providers)


def _run(gateway):
    from scp.runtime.multi_llm_crosscheck import cross_verify

    return asyncio.run(
        cross_verify(
            "Is the supplied answer supported?",
            "Yes.",
            context="The supplied evidence says yes.",
            gateway=gateway,
        )
    )


def test_cross_verify_selects_two_distinct_provider_families_before_calling():
    first = FakeProvider("family_a", "PASS")
    duplicate_family = FakeProvider("family_a", "PASS")
    second = FakeProvider("family_b", "PASS")

    result = _run(FakeGateway([first, duplicate_family, second]))

    assert result["consensus"] == "agree"
    assert result["final"] == "PASS"
    assert result["primary"]["provider"].startswith("family_a:")
    assert result["secondary"]["provider"].startswith("family_b:")
    assert first.calls == 1
    assert duplicate_family.calls == 0
    assert second.calls == 1


def test_cross_verify_distinct_provider_disagreement_fails_closed():
    first = FakeProvider("family_a", "PASS")
    second = FakeProvider("family_b", "FAIL")

    result = _run(FakeGateway([first, second]))

    assert result["consensus"] == "disagree"
    assert result["final"] is None


def test_cross_verify_one_provider_family_is_unavailable_not_consensus():
    first = FakeProvider("family_a", "PASS")
    duplicate_family = FakeProvider("family_a", "PASS")

    result = _run(FakeGateway([first, duplicate_family]))

    assert result["consensus"] == "missing_distinct_providers"
    assert result["final"] is None
    assert first.calls == 1
    assert duplicate_family.calls == 0


class DeadProvider(FakeProvider):
    """[S22] Provider enabled nhưng model chết: chat trả (None, label).

    Production shape (bench 2026-09-13): groq 404 model_not_found → chat trả
    (None, "none"); gemini 404 model retired; cerebras/sambanova 402; nvidia
    429. Family được attempt nhưng không đóng góp verdict — vòng lặp PHẢI tiếp
    tục sang family sống kế tiếp thay vì dừng/đếm nhầm.
    """

    def __init__(self, name: str, label: str = "none"):
        super().__init__(name, "")
        self._label = label

    async def chat(self, question: str, context: str = "", system_prompt: str = "", prioritize_free: bool = False):
        self.calls += 1
        return None, self._label


def test_cross_verify_dead_family_does_not_block_later_live_families():
    dead = DeadProvider("family_dead", "none")
    live_a = FakeProvider("family_live_a", "PASS")
    live_b = FakeProvider("family_live_b", "PASS")
    never_needed = FakeProvider("family_never_needed", "PASS")

    result = _run(FakeGateway([dead, live_a, live_b, never_needed]))

    assert result["consensus"] == "agree"
    assert result["final"] == "PASS"
    assert dead.calls == 1
    assert live_a.calls == 1
    assert live_b.calls == 1  # gia nhập consensus → vòng lặp dừng ở đây
    assert never_needed.calls == 0
    # Attempt record giữ nguyên family chết với verdict None — audit không bịa.
    assert [a["family"] for a in result["attempts"]] == [
        "family_dead",
        "family_live_a",
        "family_live_b",
    ]
    assert result["attempts"][0]["verdict"] is None
    assert result["attempts"][0]["provider"] == "none"


def test_cross_verify_erroring_family_does_not_block_later_live_families():
    erroring = DeadProvider("family_err", "error:HTTPStatusError")
    live_a = FakeProvider("family_live_a", "FAIL")
    live_b = FakeProvider("family_live_b", "FAIL")

    result = _run(FakeGateway([erroring, live_a, live_b]))

    assert result["consensus"] == "agree"
    assert result["final"] == "FAIL"
    assert result["attempts"][0]["provider"] == "error:HTTPStatusError"
    assert result["attempts"][0]["verdict"] is None


class LifecycleGateway(FakeGateway):
    def __init__(self, providers, store, local_endpoints):
        super().__init__(providers)
        self.discovery_store = store
        self._discovery_endpoints = list(local_endpoints)


@pytest.mark.parametrize(
    "state",
    [
        ModelLifecycleState.DISCOVERED,
        ModelLifecycleState.QUARANTINED,
        ModelLifecycleState.QUALIFIED,
        ModelLifecycleState.COOLDOWN,
        ModelLifecycleState.STALE,
        ModelLifecycleState.RETIRED,
    ],
)
def test_cross_verify_excludes_every_non_active_discovered_model_and_falls_back_to_cloud(
    tmp_path, state
):
    endpoint = "http://127.0.0.1:8126"
    store = ModelDiscoveryStore(tmp_path / f"crosscheck-{state.value}.sqlite")
    store.upsert_model(endpoint, "local-judge", state)
    local = FakeProvider(
        "local-family",
        "PASS",
        model="local-judge",
        base_url=endpoint,
    )
    cloud = FakeProvider(
        "cloud-family",
        "PASS",
        model="cloud-judge",
        base_url="https://cloud.example/v1",
    )

    try:
        result = _run(LifecycleGateway([local, cloud], store, [endpoint]))
    finally:
        store.close()

    assert result["consensus"] == "missing_distinct_providers"
    assert result["final"] is None
    assert local.calls == 0
    assert cloud.calls == 1
    assert result["attempts"] == [
        {"family": "cloud-family", "provider": "cloud-family:cloud-judge", "verdict": "PASS"}
    ]


def test_cross_verify_calls_discovered_model_only_when_active_and_keeps_cloud_fallback(tmp_path):
    endpoint = "http://127.0.0.1:8127"
    store = ModelDiscoveryStore(tmp_path / "crosscheck-active.sqlite")
    store.upsert_model(endpoint, "local-judge", ModelLifecycleState.ACTIVE)
    local = FakeProvider("local-family", "PASS", model="local-judge", base_url=endpoint)
    cloud = FakeProvider(
        "cloud-family",
        "PASS",
        model="cloud-judge",
        base_url="https://cloud.example/v1",
    )

    try:
        result = _run(LifecycleGateway([local, cloud], store, [endpoint]))
    finally:
        store.close()

    assert result["consensus"] == "agree"
    assert result["final"] == "PASS"
    assert local.calls == 1
    assert cloud.calls == 1
