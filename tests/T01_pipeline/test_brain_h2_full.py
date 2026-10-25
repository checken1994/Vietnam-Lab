"""Brain Layer H2 — integration end-to-end: live/canary + promotion + eval harness.

Phạm vi (đối chiếu nhiệm vụ H2):
(a) mode=off ⇒ BrainPolicyDenied, KHÔNG trace, KHÔNG gọi adapter.
(b) mode=shadow ⇒ trace brain-run + record so sánh có shadow_disagreement.
(c) mode=canary: 100% ⇒ output dùng brain + trace canary=true; 0% ⇒ envelope
    policy_denied reason=canary_not_selected (caller fallback legacy), adapter
    KHÔNG bị gọi; 50% ⇒ deterministic theo hash run_id (cùng run_id cùng kết quả).
(d) mode=live ⇒ output dùng brain (caller dùng thay gateway legacy), trace ghi,
    KHÔNG mang flag canary; prompt thô không lộ vào trace.
(e) EvalRunner 10/10 với FakeAdapter ⇒ PromotionGate PROMOTE (canary → champion).
(f) Eval fail (verdict sai hệ thống) ⇒ PromotionGate BLOCK kèm reason.
(g) EvalRunner report đúng metrics (total/passed/schema_errors/avg_latency/
    disagreement với baseline khác verdict).
(h) DatasetFactory từ trace (7 dòng, 5 input_hash unique) ⇒ dataset 5 unique,
    version deterministic, dedup giữ bản cổ nhất, split theo timestamp.
(i) Fail-closed/anti-placebo: mode lạ ⇒ off; canary percent env lỗi ⇒ 0%;
    PromotionGate HOLD khi thiếu metrics/thresholds, task lạ, metric sai kiểu.

KHÔNG network, KHÔNG gọi LLM thật (FakeAdapter), KHÔNG đụng data/ thật
(SCP_DATA_DIR → tmp), KHÔNG commit.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from scp.brain_layer import (
    ALLOWED_MODES,
    BrainConfig,
    BrainContext,
    BrainLayer,
    BrainPolicyDenied,
    PromotionGate,
    canary_percent,
    canary_selected,
)
from scp.brain_layer.adapters.fake import FakeAdapter
from scp.brain_layer.evals.runner import EvalRunner, metrics_from_report
from scp.brain_layer.evals.datasets import DatasetFactory
from scp.brain_layer.promotion import DECISION_BLOCK, DECISION_HOLD, DECISION_PROMOTE
from scp.brain_layer.shadow import write_shadow_comparison

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_REGISTRY = REPO_ROOT / "config" / "brain" / "registry.example.yaml"
JUDGE_DATASET = REPO_ROOT / "config" / "brain" / "eval_datasets" / "judge_v1.jsonl"

QUESTION = "Thủ đô của Pháp là gì?"
ANSWER = "Thủ đô của Pháp là Paris."
LEAK_MARKERS = (QUESTION, ANSWER)

JUDGE_PAYLOAD = {"question": QUESTION, "final_answer": ANSWER}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def write_test_registry(tmp_path: Path, *, with_promotion: bool = True) -> Path:
    """Registry test: copy example + promotion.thresholds cho task judge."""
    raw = yaml.safe_load(EXAMPLE_REGISTRY.read_text(encoding="utf-8"))
    if with_promotion:
        raw["promotion"] = {
            "thresholds": {
                "judge": {
                    "pass_rate": 0.9,
                    "schema_valid_rate": 0.9,
                    "max_avg_latency_ms": 60000,
                    "baseline_disagreement_rate": 0.5,
                }
            }
        }
    path = tmp_path / "registry.h2.yaml"
    path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    return path


def make_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    mode: str | None,
    registry: Path | None = None,
    canary_percent_env: str | None = None,
) -> None:
    monkeypatch.setenv("SCP_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("SCP_BRAIN_TRACE_PATH", raising=False)
    if mode is None:
        monkeypatch.delenv("SCP_BRAIN_MODE", raising=False)
    else:
        monkeypatch.setenv("SCP_BRAIN_MODE", mode)
    if registry is None:
        monkeypatch.delenv("SCP_BRAIN_REGISTRY_PATH", raising=False)
    else:
        monkeypatch.setenv("SCP_BRAIN_REGISTRY_PATH", str(registry))
    if canary_percent_env is None:
        monkeypatch.delenv("SCP_BRAIN_CANARY_PERCENT", raising=False)
    else:
        monkeypatch.setenv("SCP_BRAIN_CANARY_PERCENT", canary_percent_env)


def fake_adapters() -> dict[str, FakeAdapter]:
    # judge champion = openrouter/judge (provider openrouter); model_id
    # 'fake-model' ≠ champion generate 'openrouter/generator' ⇒ independence OK.
    return {
        "openrouter": FakeAdapter("openrouter"),
        "local-fake": FakeAdapter("local-fake"),
    }


def make_layer(registry_path: Path, adapters: dict[str, FakeAdapter] | None = None):
    config = BrainConfig.load(registry_path)
    return config, BrainLayer(config, adapters=adapters or fake_adapters())


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def read_trace(tmp_path: Path) -> list[dict[str, Any]] | None:
    trace = tmp_path / "brain_trace.jsonl"
    if not trace.exists():
        return None
    lines = [l for l in trace.read_text(encoding="utf-8").strip().splitlines() if l]
    return [json.loads(l) for l in lines]


# ---------------------------------------------------------------------------
# (a) mode=off — denied, không trace, không gọi adapter
# ---------------------------------------------------------------------------


def test_a_mode_off_denies_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_env(tmp_path, monkeypatch, mode=None)
    _, layer = make_layer(write_test_registry(tmp_path))
    adapter = layer.adapters["openrouter"]
    with pytest.raises(BrainPolicyDenied):
        run(layer.run("judge", JUDGE_PAYLOAD, BrainContext(run_id="h2-off")))
    assert adapter.calls == []  # adapter KHÔNG bị gọi
    assert read_trace(tmp_path) is None  # không trace


def test_a_allowed_modes_include_live_and_canary() -> None:
    assert set(ALLOWED_MODES) == {"off", "shadow", "live", "canary"}


# ---------------------------------------------------------------------------
# (b) mode=shadow — trace + disagreement
# ---------------------------------------------------------------------------


def test_b_mode_shadow_writes_trace_with_disagreement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_env(tmp_path, monkeypatch, mode="shadow")
    config, layer = make_layer(write_test_registry(tmp_path))
    resp = run(layer.run("judge", JUDGE_PAYLOAD, BrainContext(run_id="h2-shadow")))
    assert resp.status == "ok"
    assert resp.output["verdict"] == "pass"

    # Record so sánh shadow: production FAIL vs shadow PASS ⇒ disagreement=True.
    write_shadow_comparison(
        layer=layer,
        payload=JUDGE_PAYLOAD,
        resp=resp,
        production_verdict="FAIL",
        run_id="h2-shadow",
        trace_id="trace-h2",
    )
    records = read_trace(tmp_path)
    assert records is not None and len(records) == 2
    comparisons = [r for r in records if r.get("shadow") is True]
    assert len(comparisons) == 1
    assert comparisons[0]["shadow_disagreement"] is True
    assert comparisons[0]["production_verdict"] == "FAIL"
    assert comparisons[0]["shadow_verdict"] == "PASS"


# ---------------------------------------------------------------------------
# (c) mode=canary — tỷ lệ + trace flag
# ---------------------------------------------------------------------------


def test_c_canary_100_percent_uses_brain_output_and_flags_trace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_env(tmp_path, monkeypatch, mode="canary", canary_percent_env="100")
    _, layer = make_layer(write_test_registry(tmp_path))
    resp = run(layer.run("judge", JUDGE_PAYLOAD, BrainContext(run_id="h2-canary100")))
    # Output DÙNG brain (caller thay gateway legacy).
    assert resp.status == "ok"
    assert resp.output["verdict"] == "pass"
    assert resp.output["echo_input_hash"]
    assert layer.adapters["openrouter"].calls == [("judge", resp.output["echo_input_hash"])]

    records = read_trace(tmp_path)
    assert records is not None and len(records) == 1
    assert records[0]["canary"] is True  # flag canary bắt buộc trong trace
    assert records[0]["canary_selected"] is True
    assert records[0]["status"] == "ok"


def test_c_canary_0_percent_falls_back_to_legacy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_env(tmp_path, monkeypatch, mode="canary", canary_percent_env=None)
    # default SCP_BRAIN_CANARY_PERCENT vắng ⇒ 0%.
    assert canary_percent() == 0
    _, layer = make_layer(write_test_registry(tmp_path))
    resp = run(layer.run("judge", JUDGE_PAYLOAD, BrainContext(run_id="h2-canary0")))
    # Envelope policy_denied + reason canary_not_selected ⇒ caller fallback legacy.
    assert resp.status == "policy_denied"
    assert resp.route_reason == "canary_not_selected"
    assert "canary_not_selected" in resp.warnings
    assert resp.output is None
    assert layer.adapters["openrouter"].calls == []  # adapter KHÔNG bị gọi

    records = read_trace(tmp_path)
    assert records is not None and len(records) == 1
    assert records[0]["canary"] is True
    assert records[0]["canary_selected"] is False
    assert records[0]["status"] == "policy_denied"


def test_c_canary_50_percent_is_deterministic_by_run_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_env(tmp_path, monkeypatch, mode="canary", canary_percent_env="50")
    _, layer = make_layer(write_test_registry(tmp_path))

    # Chọn 2 run_id có kết quả khác nhau theo chính hàm chọn (deterministic).
    candidates = [f"run-h2-{i}" for i in range(200)]
    selected = next(r for r in candidates if canary_selected(r, 50))
    not_selected = next(r for r in candidates if not canary_selected(r, 50))
    # Determinism: cùng run_id gọi nhiều lần cùng kết quả.
    assert canary_selected(selected, 50) and canary_selected(selected, 50)
    assert not canary_selected(not_selected, 50)

    resp_in = run(layer.run("judge", JUDGE_PAYLOAD, BrainContext(run_id=selected)))
    assert resp_in.status == "ok"  # trong tỷ lệ ⇒ chạy brain
    assert resp_in.output["verdict"] == "pass"

    resp_out = run(
        layer.run("judge", JUDGE_PAYLOAD, BrainContext(run_id=not_selected))
    )
    assert resp_out.status == "policy_denied"  # ngoài tỷ lệ ⇒ fallback legacy
    assert resp_out.route_reason == "canary_not_selected"

    # Cùng run_id chạy lại ⇒ cùng kết quả (không random).
    resp_in_again = run(layer.run("judge", JUDGE_PAYLOAD, BrainContext(run_id=selected)))
    assert resp_in_again.status == "ok"


# ---------------------------------------------------------------------------
# (d) mode=live — output dùng brain, trace không flag canary, không lộ prompt
# ---------------------------------------------------------------------------


def test_d_mode_live_returns_brain_output_and_traces(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_env(tmp_path, monkeypatch, mode="live")
    _, layer = make_layer(write_test_registry(tmp_path))
    resp = run(layer.run("judge", JUDGE_PAYLOAD, BrainContext(run_id="h2-live")))
    assert resp.status == "ok"
    assert resp.output["verdict"] == "pass"  # caller DÙNG output này
    assert resp.provider_id == "openrouter"
    assert layer.adapters["openrouter"].calls != []

    records = read_trace(tmp_path)
    assert records is not None and len(records) == 1
    assert records[0]["status"] == "ok"
    assert records[0]["schema_valid"] is True
    assert "canary" not in records[0]  # live KHÔNG mang flag canary
    # Prompt thô KHÔNG lộ — chỉ input_hash.
    raw = (tmp_path / "brain_trace.jsonl").read_text(encoding="utf-8")
    for marker in LEAK_MARKERS:
        assert marker not in raw


# ---------------------------------------------------------------------------
# (e)+(f) EvalRunner → PromotionGate: PROMOTE khi pass, BLOCK khi fail
# ---------------------------------------------------------------------------


def test_e_eval_pass_promotes_canary_to_champion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_env(tmp_path, monkeypatch, mode=None)
    config, _ = make_layer(write_test_registry(tmp_path))
    runner = EvalRunner(config, fake_adapters())
    report = runner.run_eval("judge", JUDGE_DATASET)

    assert (report.total, report.passed, report.failed) == (10, 10, 0)
    assert report.schema_errors == 0
    gate = PromotionGate(config.registry)
    decision = gate.evaluate("judge", metrics_from_report(report))
    assert decision.decision == DECISION_PROMOTE
    assert decision.level_from == "canary" and decision.level_to == "champion"
    assert decision.promoted is True


def test_f_eval_fail_blocks_promotion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_env(tmp_path, monkeypatch, mode=None)
    config, _ = make_layer(write_test_registry(tmp_path))
    # Challenger trả verdict sai hệ thống so với dataset ⇒ 0/10 pass.
    bad = {
        "openrouter": FakeAdapter(
            "openrouter", outputs={"judge": {"verdict": "fail", "claims": []}}
        ),
        "local-fake": FakeAdapter("local-fake"),
    }
    runner = EvalRunner(config, bad)
    report = runner.run_eval("judge", JUDGE_DATASET)
    assert report.passed == 0 and report.failed == 10
    assert report.pass_rate == 0.0

    gate = PromotionGate(config.registry)
    decision = gate.evaluate("judge", metrics_from_report(report))
    assert decision.decision == DECISION_BLOCK
    assert any("pass_rate" in reason for reason in decision.reasons)


# ---------------------------------------------------------------------------
# (g) EvalReport metrics + disagreement với baseline
# ---------------------------------------------------------------------------


def test_g_eval_report_metrics_and_baseline_disagreement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_env(tmp_path, monkeypatch, mode=None)
    config, _ = make_layer(write_test_registry(tmp_path))
    baseline = {
        "openrouter": FakeAdapter(
            "openrouter",
            outputs={"judge": {"verdict": "fail", "claims": []}},
            model_id="baseline-judge",
        ),
        "local-fake": FakeAdapter("local-fake"),
    }
    runner = EvalRunner(config, fake_adapters(), baseline_adapters=baseline)
    report = runner.run_eval("judge", JUDGE_DATASET)

    assert report.task == "judge"
    assert report.dataset == "judge_v1"
    assert report.total == 10 and report.passed == 10 and report.failed == 0
    assert report.schema_errors == 0
    assert report.avg_latency_ms >= 0.0
    # Baseline ok + challenger ok + verdict khác nhau ở cả 10 mẫu.
    assert report.baseline_present is True
    assert report.disagreement_count == 10
    assert abs(report.disagreement_rate - 1.0) < 1e-9

    d = report.to_dict()
    assert d["total"] == 10 and d["passed"] == 10 and d["disagreement_count"] == 10


def test_g_eval_missing_expectation_counts_failed_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_env(tmp_path, monkeypatch, mode=None)
    config, _ = make_layer(write_test_registry(tmp_path))
    dataset = tmp_path / "no_expect.jsonl"
    dataset.write_text(
        json.dumps({"id": "s1", "question": "q", "final_answer": "a"}) + "\n",
        encoding="utf-8",
    )
    runner = EvalRunner(config, fake_adapters())
    report = runner.run_eval("judge", dataset)
    # Fail-closed: mẫu không có expectation ⇒ FAILED, không được mặc nhiên pass.
    assert report.total == 1 and report.passed == 0 and report.failed == 1
    assert any("expected" in w for w in report.warnings)


# ---------------------------------------------------------------------------
# (h) DatasetFactory — từ BrainTrace sang dataset có version
# ---------------------------------------------------------------------------


def test_h_dataset_factory_dedups_and_versions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_env(tmp_path, monkeypatch, mode=None)
    trace_file = tmp_path / "brain_trace_src.jsonl"
    rows: list[Any] = [
        {"ts": "2026-10-20T10:00:00.000+00:00", "input_hash": "h1", "production_verdict": "PASS"},
        {"ts": "2026-10-20T11:00:00.000+00:00", "input_hash": "h2", "production_verdict": "FAIL"},
        {"ts": "2026-10-21T10:00:00.000+00:00", "input_hash": "h3"},
        {"ts": "2026-10-22T10:00:00.000+00:00", "input_hash": "h4", "production_verdict": "pass"},
        {"ts": "2026-10-23T10:00:00.000+00:00", "input_hash": "h5", "production_verdict": "PASS"},
        # Dup h1 (muộn hơn) ⇒ dedup giữ bản cổ nhất (PASS).
        {"ts": "2026-10-24T10:00:00.000+00:00", "input_hash": "h1", "production_verdict": "FAIL"},
        # Không input_hash ⇒ bỏ.
        {"ts": "2026-10-24T11:00:00.000+00:00", "production_verdict": "PASS"},
        "không phải JSON",
    ]
    trace_file.write_text(
        "\n".join(
            r if isinstance(r, str) else json.dumps(r) for r in rows
        )
        + "\n",
        encoding="utf-8",
    )

    dataset = DatasetFactory(trace_file, name="judge-trace")
    assert len(dataset) == 5  # 7 dòng parse được, 5 input_hash unique
    assert dataset.name == "judge-trace"
    assert dataset.version.startswith("v1.")
    assert dataset.source == "brain_trace"

    # Dedup giữ bản CỔ NHẤT của h1 (verdict PASS, ts 2026-10-20).
    h1 = next(s for s in dataset.samples if s.input_hash == "h1")
    assert h1.ts == "2026-10-20T10:00:00.000+00:00"
    assert h1.expected == {"verdict": "PASS"}
    h2 = next(s for s in dataset.samples if s.input_hash == "h2")
    assert h2.expected == {"verdict": "FAIL"}

    # Privacy: sample trace-derived KHÔNG có payload (trace không chứa prompt).
    assert all(s.payload is None for s in dataset.samples)

    # Split theo timestamp: 5 mẫu ⇒ train 3 / dev 1 / test 1, union đủ 5.
    splits = dict(dataset.splits)
    assert [len(splits[k]) for k in ("train", "dev", "test")] == [3, 1, 1]
    union = [s for part in splits.values() for s in part]
    assert sorted(s.input_hash for s in union) == sorted(
        s.input_hash for s in dataset.samples
    )
    # Thứ tự theo ts: train là 3 mẫu cổ nhất, test là mẫu mới nhất.
    assert splits["train"][0].input_hash == "h1"
    assert splits["test"][0].input_hash == "h5"

    # Version deterministic theo nội dung — build lại cùng kết quả.
    again = DatasetFactory(trace_file, name="judge-trace")
    assert again.version == dataset.version
    assert len(again) == len(dataset)


# ---------------------------------------------------------------------------
# (i) Fail-closed / anti-placebo
# ---------------------------------------------------------------------------


def test_i_unknown_mode_treated_as_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_env(tmp_path, monkeypatch, mode="hocus-pocus")
    _, layer = make_layer(write_test_registry(tmp_path))
    with pytest.raises(BrainPolicyDenied):
        run(layer.run("judge", JUDGE_PAYLOAD, BrainContext(run_id="h2-weird")))
    assert layer.adapters["openrouter"].calls == []
    assert read_trace(tmp_path) is None  # mode lạ ⇒ KHÔNG trace gì cả


def test_i_canary_percent_env_broken_fails_closed_to_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_env(
        tmp_path, monkeypatch, mode="canary", canary_percent_env="không-phải-số"
    )
    assert canary_percent() == 0  # env lỗi ⇒ 0% (không ai được chọn)
    _, layer = make_layer(write_test_registry(tmp_path))
    resp = run(layer.run("judge", JUDGE_PAYLOAD, BrainContext(run_id="h2-broken-pct")))
    assert resp.status == "policy_denied"
    assert resp.route_reason == "canary_not_selected"
    assert layer.adapters["openrouter"].calls == []


def test_i_promotion_gate_holds_on_missing_metrics_or_thresholds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_env(tmp_path, monkeypatch, mode=None)
    config, _ = make_layer(write_test_registry(tmp_path))
    gate = PromotionGate(config.registry)

    # Thiếu metrics bắt buộc ⇒ HOLD (không tự promote).
    d1 = gate.evaluate("judge", {"pass_rate": 1.0})
    assert d1.decision == DECISION_HOLD
    assert any("thiếu metrics" in r for r in d1.reasons)

    # Metric sai kiểu ⇒ HOLD.
    d2 = gate.evaluate(
        "judge",
        {
            "pass_rate": "không-phải-số",
            "schema_valid_rate": 1.0,
            "avg_latency_ms": 10.0,
            "disagreement_rate": 0.0,
        },
    )
    assert d2.decision == DECISION_HOLD

    # Task lạ ⇒ HOLD.
    d3 = gate.evaluate(
        "ghost-task",
        {
            "pass_rate": 1.0,
            "schema_valid_rate": 1.0,
            "avg_latency_ms": 1.0,
            "disagreement_rate": 0.0,
        },
    )
    assert d3.decision == DECISION_HOLD

    # Registry KHÔNG có promotion.thresholds (example) ⇒ HOLD, không bịa ngưỡng.
    gate_no_th = PromotionGate(BrainConfig.load(EXAMPLE_REGISTRY).registry)
    d4 = gate_no_th.evaluate(
        "judge",
        {"pass_rate": 1.0, "schema_valid_rate": 1.0,
         "avg_latency_ms": 1.0, "disagreement_rate": 0.0},
    )
    assert d4.decision == DECISION_HOLD
    assert any("promotion.thresholds" in r for r in d4.reasons)


def test_i_promotion_gate_blocks_on_disagreement_above_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_env(tmp_path, monkeypatch, mode=None)
    config, _ = make_layer(write_test_registry(tmp_path))
    gate = PromotionGate(config.registry)
    # pass_rate/schema/latency đạt nhưng disagreement 0.6 > baseline 0.5 ⇒ BLOCK.
    d = gate.evaluate(
        "judge",
        {
            "pass_rate": 0.95,
            "schema_valid_rate": 1.0,
            "avg_latency_ms": 100.0,
            "disagreement_rate": 0.6,
        },
    )
    assert d.decision == DECISION_BLOCK
    assert any("disagreement_rate" in r and "baseline" in r for r in d.reasons)
