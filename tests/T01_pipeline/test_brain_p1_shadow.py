"""Brain Layer P1 — shadow mode tests (mode=off anti-regression + shadow không đổi đáp án).

Phạm vi:
1. mode=off (default): verification stage hành vi GIỐNG HỆT trước — không trace
   file, không dựng BrainLayer, không đụng judge shadow (anti-regression).
2. mode=shadow + LegacyGatewayAdapter (bọc judge instance có sẵn): verdict /
   final_answer / governance trả cho user GIỐNG HỆT case off — shadow chỉ ghi
   BrainTrace + đo disagreement; record có shadow=True,
   adjudicated_by=reality_judge_legacy (không mạo danh model judge — §3.10.4).
3. Shadow gặp exception ⇒ stage vẫn hoàn tất bình thường (fail-open có kiểm
   soát, log debug).
4. Runtime audit probe brain_layer: mode=off ⇒ UNKNOWN (không healthy-giả);
   mode=shadow + registry nạp được ⇒ PASS kèm config_hash/tasks/providers;
   mode=shadow thiếu registry ⇒ WARN.

KHÔNG network, KHÔNG gọi LLM thật (judge stub inject qua ctx / judge_factory),
KHÔNG đụng data/ thật (SCP_DATA_DIR → tmp).
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from scp.api_server_parts.helpers import AskRequest
from scp.api_server_parts.pipeline.context import AskPipelineContext
from scp.api_server_parts.pipeline.verification_stage import VerificationStage
from scp.brain_layer import (
    BrainConfig,
    BrainContext,
    BrainLayer,
    BrainProviderError,
    BrainSchemaInvalid,
)
from scp.brain_layer.adapters import ADJUDICATED_BY, PROVIDER_ID, LegacyGatewayAdapter
import scp.brain_layer.shadow as brain_shadow
from scp.brain_layer.shadow import run_shadow_judge
from scp.runtime_audit.models import Severity
from scp.runtime_audit.probes import BrainLayerProbe
from scp.runtime_audit.runner import RuntimeAuditRunner

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_REGISTRY = REPO_ROOT / "config" / "brain" / "registry.example.yaml"

QUESTION = "Thủ đô của Pháp là gì?"
ANSWER = "Thủ đô của Pháp là Paris."
LEAK_MARKERS = ("Thủ đô của Pháp", "Paris")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class StubJudge:
    """Judge stub cho đường production — đếm mọi lần adjudication."""

    def __init__(self, verdict: str = "PASS") -> None:
        self.verdict = verdict
        self.calls = 0

    def _verdict_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "confidence": 0.9,
            "reasoning": "stub judge",
            "final_answer": ANSWER,
            "evidence": {"governance_decision": "UPHOLD"},
            "failures": [],
            "slm_responses": [],
        }

    async def judge_with_react_fallback(self, **kwargs: Any) -> dict[str, Any]:
        self.calls += 1
        return self._verdict_dict()

    def judge(self, **kwargs: Any) -> dict[str, Any]:
        self.calls += 1
        return self._verdict_dict()


def write_test_registry(tmp_path: Path) -> Path:
    """Registry test: copy example + thêm provider legacy-reality-judge làm
    champion cho task judge (vẫn khác champion generate ⇒ independence OK)."""
    raw = yaml.safe_load(EXAMPLE_REGISTRY.read_text(encoding="utf-8"))
    raw["providers"].append({"provider_id": PROVIDER_ID, "kind": "local", "egress": "local"})
    raw["models"].append({
        "model_id": "reality-judge-legacy",
        "provider_id": PROVIDER_ID,
        "roles": ["judge"],
    })
    for task in raw["tasks"]:
        if task.get("task") == "judge":
            task["champion"] = "reality-judge-legacy"
    path = tmp_path / "registry.p1.yaml"
    path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    return path


def make_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    mode: str | None,
    registry: Path | None,
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


async def _noop_fact_check(*args: Any, **kwargs: Any) -> list[Any]:
    return []


def make_ctx(judge: Any) -> AskPipelineContext:
    req = AskRequest(question=QUESTION)
    namespace: dict[str, Any] = {"_async_fact_check": _noop_fact_check}
    ctx = AskPipelineContext(req=req, request=None, namespace=namespace)
    ctx.ai_answer = ANSWER
    ctx.judge = judge
    return ctx


def run_stage(ctx: AskPipelineContext) -> None:
    asyncio.run(VerificationStage().execute(ctx))


def read_trace(tmp_path: Path) -> list[dict[str, Any]] | None:
    trace = tmp_path / "brain_trace.jsonl"
    if not trace.exists():
        return None
    lines = [l for l in trace.read_text(encoding="utf-8").strip().splitlines() if l]
    return [json.loads(l) for l in lines]


# ---------------------------------------------------------------------------
# 1. mode=off (default) — anti-regression: giống hệt trước P1
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("relevance_confirmed")  # [RELEVANCE] tiền điều kiện: reader độc lập đã xác nhận
def test_mode_off_noop_no_trace_no_layer_no_shadow_judge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_env(tmp_path, monkeypatch, mode=None, registry=None)
    build_calls = {"n": 0}
    real_build = brain_shadow.build_shadow_layer

    def counting_build(*args: Any, **kwargs: Any) -> Any:
        build_calls["n"] += 1
        return real_build(*args, **kwargs)

    monkeypatch.setattr(brain_shadow, "build_shadow_layer", counting_build)

    judge = StubJudge()
    ctx = make_ctx(judge)
    run_stage(ctx)

    # Không dựng BrainLayer, không trace file, judge production chỉ chạy 1 lần.
    assert build_calls["n"] == 0
    assert read_trace(tmp_path) is None
    assert judge.calls == 1
    assert ctx.judge_verdict.verdict == "PASS"
    assert ANSWER in ctx.api_final_answer


@pytest.mark.usefixtures("relevance_confirmed")  # [RELEVANCE] tiền điều kiện: reader độc lập đã xác nhận
def test_mode_off_with_registry_configured_still_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Registry có cấu hình nhưng mode=off ⇒ vẫn no-op tuyệt đối.
    reg = write_test_registry(tmp_path)
    make_env(tmp_path, monkeypatch, mode=None, registry=reg)

    judge = StubJudge()
    ctx = make_ctx(judge)
    run_stage(ctx)

    assert read_trace(tmp_path) is None
    assert judge.calls == 1
    assert ctx.judge_verdict.verdict == "PASS"


# ---------------------------------------------------------------------------
# 2. mode=shadow — không đổi đáp án + BrainTrace có disagreement/provenance
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("relevance_confirmed")  # [RELEVANCE] tiền điều kiện: reader độc lập đã xác nhận
def test_shadow_keeps_verdict_and_answer_and_writes_trace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reg = write_test_registry(tmp_path)

    # Baseline off
    make_env(tmp_path, monkeypatch, mode=None, registry=None)
    ctx_off = make_ctx(StubJudge())
    run_stage(ctx_off)
    assert read_trace(tmp_path) is None

    # Shadow run — cùng inputs
    make_env(tmp_path, monkeypatch, mode="shadow", registry=reg)
    ctx_sh = make_ctx(StubJudge())
    run_stage(ctx_sh)

    # Shadow KHÔNG BAO GIỜ đụng verdict/final_answer/governance của user.
    assert ctx_sh.judge_verdict.verdict == ctx_off.judge_verdict.verdict == "PASS"
    assert ctx_sh.api_final_answer == ctx_off.api_final_answer
    assert ctx_sh.gov_decision == ctx_off.gov_decision
    assert ctx_sh.is_true_security_threat == ctx_off.is_true_security_threat
    # Production judge 1 lần + shadow adjudication qua adapter 1 lần.
    assert ctx_sh.judge.calls == 2

    records = read_trace(tmp_path)
    assert records is not None and len(records) >= 2
    brain_runs = [r for r in records if "shadow" not in r]
    comparisons = [r for r in records if r.get("shadow") is True]
    assert len(brain_runs) == 1 and len(comparisons) == 1
    run_rec, cmp_rec = brain_runs[0], comparisons[0]

    # Record brain-run: route tới provider legacy, status ok.
    assert run_rec["provider_id"] == PROVIDER_ID == "legacy-reality-judge"
    assert run_rec["model_id"] == "reality-judge-legacy"
    assert run_rec["status"] == "ok"
    assert run_rec["schema_valid"] is True

    # Record so sánh: provenance trung thực legacy + disagreement metric.
    assert cmp_rec["adjudicated_by"] == ADJUDICATED_BY == "reality_judge_legacy"
    assert cmp_rec["production_verdict"] == "PASS"
    assert cmp_rec["shadow_verdict"] == "PASS"
    assert cmp_rec["shadow_disagreement"] is False
    assert isinstance(cmp_rec["shadow_disagreement"], bool)

    # PROMPT THÔ KHÔNG LỘ vào trace — chỉ input_hash.
    raw = (tmp_path / "brain_trace.jsonl").read_text(encoding="utf-8")
    for marker in LEAK_MARKERS:
        assert marker not in raw


@pytest.mark.usefixtures("relevance_confirmed")  # [RELEVANCE] tiền điều kiện: reader độc lập đã xác nhận
def test_shadow_disagreement_true_when_shadow_verdict_differs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reg = write_test_registry(tmp_path)
    make_env(tmp_path, monkeypatch, mode="shadow", registry=reg)

    # Shadow layer dùng judge KHÁC (verdict FAIL) — chỉ để đo disagreement.
    config = BrainConfig.load(reg)
    shadow_layer = BrainLayer(
        config,
        adapters={
            PROVIDER_ID: LegacyGatewayAdapter(judge_factory=lambda: StubJudge(verdict="FAIL"))
        },
        trace=brain_shadow.TraceWriter(),
    )
    monkeypatch.setattr(brain_shadow, "build_shadow_layer", lambda **kwargs: shadow_layer)

    ctx = make_ctx(StubJudge())  # production verdict PASS
    run_stage(ctx)

    # Production KHÔNG bị shadow kéo theo.
    assert ctx.judge_verdict.verdict == "PASS"
    assert ANSWER in ctx.api_final_answer

    cmp_rec = [r for r in read_trace(tmp_path) if r.get("shadow") is True][0]
    assert cmp_rec["production_verdict"] == "PASS"
    assert cmp_rec["shadow_verdict"] == "FAIL"
    assert cmp_rec["shadow_disagreement"] is True
    assert cmp_rec["adjudicated_by"] == "reality_judge_legacy"


# ---------------------------------------------------------------------------
# 3. Shadow gặp exception ⇒ /ask vẫn hoàn tất (fail-open có kiểm soát)
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("relevance_confirmed")  # [RELEVANCE] tiền điều kiện: reader độc lập đã xác nhận
def test_stage_survives_shadow_hook_raising(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reg = write_test_registry(tmp_path)
    make_env(tmp_path, monkeypatch, mode="shadow", registry=reg)

    async def boom(**kwargs: Any) -> None:
        raise RuntimeError("shadow exploded")

    monkeypatch.setattr(brain_shadow, "run_shadow_judge", boom)

    ctx = make_ctx(StubJudge())
    run_stage(ctx)  # KHÔNG raise

    assert ctx.judge_verdict.verdict == "PASS"
    assert ANSWER in ctx.api_final_answer
    assert read_trace(tmp_path) is None


def test_shadow_hook_swallows_missing_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_env(tmp_path, monkeypatch, mode="shadow", registry=None)
    # Không raise, không trace.
    asyncio.run(
        run_shadow_judge(question=QUESTION, final_answer=ANSWER, production_verdict="PASS")
    )
    assert read_trace(tmp_path) is None


def test_shadow_hook_swallows_bad_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bad = tmp_path / "registry.bad.yaml"
    bad.write_text("providers: 123\nmodels: []\n", encoding="utf-8")
    make_env(tmp_path, monkeypatch, mode="shadow", registry=bad)
    asyncio.run(
        run_shadow_judge(question=QUESTION, final_answer=ANSWER, production_verdict="PASS")
    )
    assert read_trace(tmp_path) is None


def test_shadow_hook_swallows_judge_provider_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # get_judge() trả None ⇒ adapter provider_error ⇒ BrainLayer trả envelope
    # provider_error ⇒ record so sánh trung thực, không crash.
    reg = write_test_registry(tmp_path)
    make_env(tmp_path, monkeypatch, mode="shadow", registry=reg)
    asyncio.run(
        run_shadow_judge(
            question=QUESTION,
            final_answer=ANSWER,
            production_verdict="PASS",
            judge_factory=lambda: None,
        )
    )
    records = read_trace(tmp_path)
    assert records is not None
    comparisons = [r for r in records if r.get("shadow") is True]
    assert len(comparisons) == 1
    assert comparisons[0]["status"] == "provider_error"
    assert comparisons[0]["shadow_verdict"] is None
    assert comparisons[0]["shadow_disagreement"] is False  # không tự bịa agree/disagree
    assert comparisons[0]["provider_id"] == PROVIDER_ID


# ---------------------------------------------------------------------------
# 4. LegacyGatewayAdapter — bọc judge có sẵn, nhãn provenance trung thực
# ---------------------------------------------------------------------------


def test_legacy_adapter_labels_provenance_and_wraps_existing_judge() -> None:
    judge = StubJudge(verdict="PASS")
    adapter = LegacyGatewayAdapter(judge_factory=lambda: judge)
    res = asyncio.run(
        adapter.run(
            "judge",
            {"question": QUESTION, "final_answer": ANSWER},
            BrainContext(run_id="run-adapter"),
        )
    )
    assert res.output["verdict"] == "PASS"
    assert res.output["adjudicated_by"] == "reality_judge_legacy"
    assert "claims" in res.output
    assert adapter.calls == ["judge"]
    assert judge.calls == 1  # dùng đúng instance được bọc, không tạo judge mới


def test_legacy_adapter_none_judge_is_provider_error_not_crash() -> None:
    adapter = LegacyGatewayAdapter(judge_factory=lambda: None)
    with pytest.raises(BrainProviderError):
        asyncio.run(
            adapter.run(
                "judge",
                {"question": "q", "final_answer": "a"},
                BrainContext(run_id="run-adapter-none"),
            )
        )


def test_legacy_adapter_factory_raising_is_provider_error() -> None:
    def bad_factory() -> Any:
        raise RuntimeError("resolve failed")

    adapter = LegacyGatewayAdapter(judge_factory=bad_factory)
    with pytest.raises(BrainProviderError):
        asyncio.run(
            adapter.run(
                "judge",
                {"question": "q", "final_answer": "a"},
                BrainContext(run_id="run-adapter-bad"),
            )
        )


def test_legacy_adapter_rejects_non_judge_task() -> None:
    adapter = LegacyGatewayAdapter(judge_factory=lambda: StubJudge())
    with pytest.raises(BrainSchemaInvalid):
        asyncio.run(adapter.run("generate", {}, BrainContext(run_id="run-adapter-g")))


def test_brain_layer_shadow_with_legacy_adapter_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reg = write_test_registry(tmp_path)
    make_env(tmp_path, monkeypatch, mode="shadow", registry=reg)
    config = BrainConfig.load(reg)
    layer = BrainLayer(
        config,
        adapters={PROVIDER_ID: LegacyGatewayAdapter(judge_factory=lambda: StubJudge())},
    )
    resp = asyncio.run(
        layer.run(
            "judge",
            {"question": QUESTION, "final_answer": ANSWER},
            BrainContext(run_id="run-p1-e2e"),
        )
    )
    assert resp.status == "ok"
    assert resp.provider_id == PROVIDER_ID
    assert resp.model_id == "reality-judge-legacy"
    assert resp.output["adjudicated_by"] == "reality_judge_legacy"


def test_brain_layer_mode_off_still_denies_legacy_adapter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scp.brain_layer import BrainPolicyDenied

    reg = write_test_registry(tmp_path)
    make_env(tmp_path, monkeypatch, mode=None, registry=reg)
    config = BrainConfig.load(reg)
    adapter = LegacyGatewayAdapter(judge_factory=lambda: StubJudge())
    layer = BrainLayer(config, adapters={PROVIDER_ID: adapter})
    with pytest.raises(BrainPolicyDenied):
        asyncio.run(
            layer.run(
                "judge",
                {"question": "q", "final_answer": "a"},
                BrainContext(run_id="run-p1-off"),
            )
        )
    assert adapter.calls == []  # mode=off ⇒ adapter KHÔNG bị gọi


# ---------------------------------------------------------------------------
# 5. Runtime audit probe brain_layer — không healthy-giả
# ---------------------------------------------------------------------------


def test_probe_off_reports_unknown_not_healthy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_env(tmp_path, monkeypatch, mode=None, registry=None)
    results = BrainLayerProbe().run()
    assert len(results) == 1
    finding = results[0]
    assert finding.probe_id == "brain.layer_status"
    assert finding.severity == Severity.UNKNOWN  # UNKNOWN hợp lệ cho mode=off
    assert finding.details["mode"] == "off"


def test_probe_shadow_with_registry_pass_with_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reg = write_test_registry(tmp_path)
    make_env(tmp_path, monkeypatch, mode="shadow", registry=reg)
    finding = BrainLayerProbe().run()[0]
    assert finding.severity == Severity.PASS
    assert finding.details["mode"] == "shadow"
    assert len(finding.details["config_hash"]) == 64
    assert "judge" in finding.details["tasks"]
    assert PROVIDER_ID in finding.details["providers"]


def test_probe_shadow_without_registry_warns_not_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_env(tmp_path, monkeypatch, mode="shadow", registry=None)
    finding = BrainLayerProbe().run()[0]
    assert finding.severity == Severity.WARN
    assert finding.severity != Severity.PASS  # không healthy-giả


def test_probe_shadow_with_broken_registry_warns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bad = tmp_path / "registry.bad.yaml"
    bad.write_text("providers: 123\nmodels: []\n", encoding="utf-8")
    make_env(tmp_path, monkeypatch, mode="shadow", registry=bad)
    finding = BrainLayerProbe().run()[0]
    assert finding.severity == Severity.WARN


def test_runner_wires_brain_layer_probe_off_is_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_env(tmp_path, monkeypatch, mode=None, registry=None)
    runner = RuntimeAuditRunner(
        data_dir=tmp_path,
        service_targets=[],  # offline — không HTTP
        journal_secret="",
        admin_token="",
    )
    audit = runner.run()
    brain = [f for f in audit.findings if f.probe_id == "brain.layer_status"]
    assert brain, "BrainLayerProbe phải được wire vào RuntimeAuditRunner"
    assert brain[0].severity == Severity.UNKNOWN  # mode=off ⇒ UNKNOWN, không PASS giả
    assert "brain.layer_status" in audit.scope["probes"]
