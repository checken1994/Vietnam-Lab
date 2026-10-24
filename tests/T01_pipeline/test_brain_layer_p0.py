"""Brain Layer P0 — contract-only skeleton tests (mode=off mặc định).

Phạm vi: registry fail-closed, mode=off denied (adapter không bị gọi),
mode=shadow envelope + trace không lộ prompt, router champion/fallback,
anti-placebo schema_invalid. KHÔNG test /ask (chưa cắm), KHÔNG network.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Callable

import pytest
import yaml

from scp.brain_layer import (
    BRAIN_STATUSES,
    BrainConfig,
    BrainConfigError,
    BrainContext,
    BrainLayer,
    BrainPolicyDenied,
)
from scp.brain_layer.adapters import FakeAdapter
from scp.brain_layer.probes import collect_brain_layer_status

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_REGISTRY = REPO_ROOT / "config" / "brain" / "registry.example.yaml"
EXPECTED_TASKS = {
    "route",
    "extract_claims",
    "generate",
    "fact_check",
    "judge",
    "why",
    "tool_plan",
    "autofix_patch",
}

# Prompt thô dùng để chứng minh trace KHÔNG chứa nội dung prompt.
SECRET_PROMPT = "XIN CHAO BI MAT KHONG DUOC XUAT HIEN TRONG TRACE"


def make_layer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    mode: str | None = None,
    adapters: dict[str, FakeAdapter] | None = None,
) -> tuple[BrainLayer, dict[str, FakeAdapter]]:
    config = BrainConfig.load(EXAMPLE_REGISTRY)
    if adapters is None:
        adapters = {
            "openrouter": FakeAdapter("openrouter"),
            "local-fake": FakeAdapter("local-fake"),
        }
    layer = BrainLayer(config, adapters=adapters)
    if mode is None:
        monkeypatch.delenv("SCP_BRAIN_MODE", raising=False)
    else:
        monkeypatch.setenv("SCP_BRAIN_MODE", mode)
    # Trace ghi dưới runtime_data_dir() → trỏ SCP_DATA_DIR vào tmp để không
    # đụng data/ thật của repo.
    monkeypatch.setenv("SCP_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("SCP_BRAIN_TRACE_PATH", raising=False)
    return layer, adapters


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def write_bad_registry(
    tmp_path: Path, mutate: Callable[[dict[str, Any]], None]
) -> Path:
    raw = yaml.safe_load(EXAMPLE_REGISTRY.read_text(encoding="utf-8"))
    mutate(raw)
    path = tmp_path / "registry.bad.yaml"
    path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# (a) load example registry OK + config_hash ổn định
# ---------------------------------------------------------------------------


def test_a_load_example_registry_ok_and_hash_stable() -> None:
    config1 = BrainConfig.load(EXAMPLE_REGISTRY)
    config2 = BrainConfig.load(EXAMPLE_REGISTRY)

    assert set(config1.registry.tasks) == EXPECTED_TASKS
    assert set(config1.registry.providers) == {"openrouter", "local-fake"}
    judge = config1.registry.tasks["judge"]
    assert judge.independence == ("generate",)
    assert judge.champion != config1.registry.tasks["generate"].champion

    assert len(config1.config_hash) == 64
    assert all(c in "0123456789abcdef" for c in config1.config_hash)
    assert config1.config_hash == config2.config_hash


# ---------------------------------------------------------------------------
# (b) registry sai ⇒ BrainConfigError fail-closed
# ---------------------------------------------------------------------------


def test_b_model_pointing_to_missing_provider_fails_closed(tmp_path: Path) -> None:
    def mutate(raw: dict[str, Any]) -> None:
        for model in raw["models"]:
            if model["model_id"] == "openrouter/judge":
                model["provider_id"] = "ghost-provider"

    with pytest.raises(BrainConfigError) as excinfo:
        BrainConfig.load(write_bad_registry(tmp_path, mutate))
    assert "ghost-provider" in str(excinfo.value)


def test_b_judge_champion_same_as_generate_fails_closed(tmp_path: Path) -> None:
    def mutate(raw: dict[str, Any]) -> None:
        for task in raw["tasks"]:
            if task["task"] == "judge":
                task["champion"] = "openrouter/generator"

    with pytest.raises(BrainConfigError) as excinfo:
        BrainConfig.load(write_bad_registry(tmp_path, mutate))
    assert "independence" in str(excinfo.value)


def test_b_registry_with_secret_value_fails_closed(tmp_path: Path) -> None:
    def mutate(raw: dict[str, Any]) -> None:
        raw["providers"][0]["api_key"] = "sk-should-never-live-in-registry"

    with pytest.raises(BrainConfigError) as excinfo:
        BrainConfig.load(write_bad_registry(tmp_path, mutate))
    assert "secret" in str(excinfo.value)


# ---------------------------------------------------------------------------
# (c) mode=off ⇒ BrainPolicyDenied, adapter KHÔNG được gọi
# ---------------------------------------------------------------------------


def test_c_mode_off_denies_and_never_calls_adapter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layer, adapters = make_layer(tmp_path, monkeypatch)  # mode mặc định = off

    with pytest.raises(BrainPolicyDenied):
        run(
            layer.run(
                "generate", {"prompt": "hello"}, BrainContext(run_id="run-c")
            )
        )
    assert adapters["openrouter"].calls == []
    assert adapters["local-fake"].calls == []


def test_c_unknown_mode_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layer, adapters = make_layer(tmp_path, monkeypatch, mode="totally-unlisted")

    with pytest.raises(BrainPolicyDenied):
        run(
            layer.run("generate", {"prompt": "hi"}, BrainContext(run_id="run-c2"))
        )
    assert adapters["openrouter"].calls == []


# ---------------------------------------------------------------------------
# (d) mode=shadow + FakeAdapter ⇒ envelope đúng shape, trace không lộ prompt
# ---------------------------------------------------------------------------


def test_d_shadow_mode_envelope_shape_and_trace_no_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layer, adapters = make_layer(tmp_path, monkeypatch, mode="shadow")
    config_hash = layer.config.config_hash

    resp = run(
        layer.run(
            "generate",
            {"prompt": SECRET_PROMPT},
            BrainContext(run_id="run-d", trace_id="trace-d"),
        )
    )

    assert resp.status == "ok" and resp.status in BRAIN_STATUSES
    assert resp.brain_trace_id.startswith("bt-")
    assert len(resp.brain_trace_id) == 35  # "bt-" + 32 hex
    assert resp.contract_version == "brain-p0/1"
    assert resp.config_hash == config_hash
    assert resp.provider_id == "openrouter"
    assert resp.model_id == "openrouter/generator"
    assert resp.output["answer"] == "fake:generate"
    assert resp.usage["input_tokens"] > 0
    assert resp.latency_ms >= 0.0
    assert "champion" in resp.route_reason

    trace_path = tmp_path / "brain_trace.jsonl"
    assert trace_path.exists()
    raw_trace = trace_path.read_text(encoding="utf-8")
    lines = [line for line in raw_trace.strip().splitlines() if line]
    record = json.loads(lines[-1])
    assert record["task"] == "generate"
    assert record["status"] == "ok"
    assert record["schema_valid"] is True
    assert record["judge_independence"] is True
    assert record["config_hash"] == config_hash
    assert record["run_id"] == "run-d"
    assert len(record["input_hash"]) == 64
    # PROMPT THÔ KHÔNG ĐƯỢC LỘ: chỉ có input_hash.
    assert SECRET_PROMPT not in raw_trace
    assert "prompt" not in raw_trace


def test_d_shadow_judge_trace_records_independence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layer, _ = make_layer(tmp_path, monkeypatch, mode="shadow")

    resp = run(
        layer.run(
            "judge", {"answer": "something"}, BrainContext(run_id="run-d2")
        )
    )
    assert resp.status == "ok"
    assert resp.model_id == "openrouter/judge"

    trace_path = tmp_path / "brain_trace.jsonl"
    record = json.loads(trace_path.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert record["task"] == "judge"
    assert record["judge_independence"] is True


# ---------------------------------------------------------------------------
# (e) router chọn champion đúng + route_reason có nội dung
# ---------------------------------------------------------------------------


def test_e_router_selects_champion_with_route_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layer, _ = make_layer(tmp_path, monkeypatch, mode="shadow")

    decision = layer.router.select("route", BrainContext(run_id="run-e"))
    assert decision.model_id == "local-fake/router"
    assert decision.provider_id == "local-fake"
    assert decision.route_reason
    assert "champion" in decision.route_reason


def test_e_champion_failure_falls_back_per_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapters = {
        "openrouter": FakeAdapter("openrouter", fail_for={"generate": "provider_5xx"}),
        "local-fake": FakeAdapter("local-fake"),
    }
    layer, _ = make_layer(tmp_path, monkeypatch, mode="shadow", adapters=adapters)

    resp = run(
        layer.run("generate", {"prompt": "p"}, BrainContext(run_id="run-e2"))
    )
    assert resp.status == "ok"
    assert resp.model_id == "local-fake/router"
    assert "fallback" in resp.route_reason
    assert ("generate", FakeAdapter.input_hash({"prompt": "p"})) in adapters[
        "openrouter"
    ].calls


def test_e_secret_data_class_withheld_without_adapter_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layer, adapters = make_layer(tmp_path, monkeypatch, mode="shadow")

    resp = run(
        layer.run(
            "generate",
            {"prompt": "p"},
            BrainContext(run_id="run-e3", data_class="secret"),
        )
    )
    assert resp.status == "withheld"
    assert resp.output is None
    assert adapters["openrouter"].calls == []
    assert adapters["local-fake"].calls == []


def test_e_runtime_independence_guard_denies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layer, adapters = make_layer(tmp_path, monkeypatch, mode="shadow")

    with pytest.raises(BrainPolicyDenied) as excinfo:
        run(
            layer.run(
                "generate",
                {"prompt": "p"},
                BrainContext(run_id="run-e4", independence=("generate",)),
            )
        )
    assert "independence" in str(excinfo.value)
    assert adapters["openrouter"].calls == []


# ---------------------------------------------------------------------------
# (f) anti-placebo: output sai schema ⇒ schema_invalid, KHÔNG trả như ok
# ---------------------------------------------------------------------------


def test_f_missing_required_key_never_returned_as_ok(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapters = {
        "openrouter": FakeAdapter("openrouter", outputs={"generate": {"wrong": 1}}),
        "local-fake": FakeAdapter("local-fake"),
    }
    layer, _ = make_layer(tmp_path, monkeypatch, mode="shadow", adapters=adapters)

    resp = run(
        layer.run("generate", {"prompt": "p"}, BrainContext(run_id="run-f1"))
    )
    assert resp.status == "schema_invalid"
    assert resp.status != "ok"
    assert resp.output is None
    assert any("thiếu key bắt buộc" in w for w in resp.warnings)


def test_f_non_dict_output_schema_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapters = {
        "openrouter": FakeAdapter("openrouter", outputs={"generate": "just-a-string"}),
        "local-fake": FakeAdapter("local-fake"),
    }
    layer, _ = make_layer(tmp_path, monkeypatch, mode="shadow", adapters=adapters)

    resp = run(
        layer.run("generate", {"prompt": "p"}, BrainContext(run_id="run-f2"))
    )
    assert resp.status == "schema_invalid"
    assert resp.output is None

    trace_path = tmp_path / "brain_trace.jsonl"
    record = json.loads(trace_path.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert record["status"] == "schema_invalid"
    assert record["schema_valid"] is False


# ---------------------------------------------------------------------------
# probe skeleton: chỉ hàm thuần, chưa wire
# ---------------------------------------------------------------------------


def test_probe_collect_brain_layer_status_dict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layer, _ = make_layer(tmp_path, monkeypatch)  # mode off mặc định

    status = collect_brain_layer_status(layer.config)
    assert status["probe"] == "brain_layer_status"
    assert status["mode"] == "off"
    assert status["config_hash"] == layer.config.config_hash
    assert set(status["tasks"]) == EXPECTED_TASKS
    assert set(status["providers"]) == {"openrouter", "local-fake"}
