"""Brain Layer P2 — shadow generate + OpenAI-compatible adapter tests.

Phạm vi (đối chiếu nhiệm vụ P2):
(a) mode=off (default): hook generate no-op tuyệt đối — không dựng layer, không
    gọi adapter, không trace file; answer production không đổi.
(b) mode=shadow + FakeAdapter: BrainTrace có task=generate (status ok),
    production answer KHÔNG ĐỔI, record so sánh có shadow=True +
    adjudicated_by=provider của adapter. Cả 2 đường: tuần tự
    (SCP_BRAIN_SHADOW_GENERATE=1) và fire-and-forget mặc định (drain pending).
(c) adapter trả output sai schema ⇒ trace schema_valid=false / status
    schema_invalid, KHÔNG crash pipeline, KHÔNG retry fallback
    (BrainSchemaInvalid từ adapter không đi tiếp fallback chain).
(d) adapter raise ⇒ nuốt (stage-level + task-level), pipeline hoàn tất.
(e) openai_compatible adapter unit test với httpx.MockTransport — KHÔNG network:
    200 hợp lệ → {"answer": str} + usage tokens; 401 → auth; timeout →
    BrainTimeout; 429/5xx → rate_limit/provider_5xx; JSON hỏng/choices lệch →
    BrainSchemaInvalid; secret thiếu/không khai báo → auth fail-closed.

KHÔNG gọi LLM thật, KHÔNG đụng data/ thật (SCP_DATA_DIR → tmp).
Nguồn sự thật schema task generate = registry (config/brain/registry.example.yaml:
output_required [answer]) — KHÔNG bịa contract khác.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml

from scp.api_server_parts.helpers import AskRequest
from scp.api_server_parts.pipeline.context import AskPipelineContext
from scp.api_server_parts.pipeline.generation_stage import GenerationStage
from scp.brain_layer import (
    BrainConfig,
    BrainContext,
    BrainLayer,
    BrainProviderError,
    BrainSchemaInvalid,
    BrainTimeout,
)
from scp.brain_layer.adapters.fake import FakeAdapter
from scp.brain_layer.adapters.openai_compatible import (
    OpenAICompatibleAdapter,
    clamp_timeout_s,
)
import scp.brain_layer.shadow as brain_shadow
from scp.security.secret_registry import SECRET_PURPOSES, SecretSpec

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_REGISTRY = REPO_ROOT / "config" / "brain" / "registry.example.yaml"

QUESTION = "Thủ đô của Pháp là gì?"
PROD_ANSWER = "Thủ đô của Pháp là Paris."
LEAK_MARKERS = ("Thủ đô của Pháp", "Paris")
SECRET_REF = "SCP_BRAIN_P2_TEST_KEY"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def write_test_registry(tmp_path: Path) -> Path:
    """Registry P2: generate champion = local-fake/router (kind fake) ⇒
    build_generate_shadow_layer map sang FakeAdapter — không network.
    (judge champion openrouter/judge khác ⇒ independence vẫn pass.)"""
    raw = yaml.safe_load(EXAMPLE_REGISTRY.read_text(encoding="utf-8"))
    for task in raw["tasks"]:
        if task.get("task") == "generate":
            task["champion"] = "local-fake/router"
            task["fallbacks"] = []
    path = tmp_path / "registry.p2.yaml"
    path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    return path


def write_openai_registry(tmp_path: Path, *, timeout_s: int | None = None) -> Path:
    """Registry với generate champion = provider external_openai_compatible
    (base_url testserver, secret_ref chỉ là TÊN)."""
    raw = yaml.safe_load(EXAMPLE_REGISTRY.read_text(encoding="utf-8"))
    model_extra: dict[str, Any] = {
        "model_id": "test-ext/gen",
        "provider_id": "test-ext",
        "roles": ["generate"],
    }
    if timeout_s is not None:
        model_extra["limits"] = {"timeout_s": timeout_s}
    raw["providers"].append({
        "provider_id": "test-ext",
        "kind": "external_openai_compatible",
        "base_url": "http://testserver/v1",
        "secret_ref": SECRET_REF,
        "egress": "external",
    })
    raw["models"].append(model_extra)
    for task in raw["tasks"]:
        if task.get("task") == "generate":
            task["champion"] = "test-ext/gen"
            task["fallbacks"] = []
    path = tmp_path / "registry.p2.openai.yaml"
    path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    return path


def write_two_fake_registry(tmp_path: Path) -> Path:
    """Registry generate: champion prov-a + fallback prov-b (2 provider fake) —
    test 'BrainSchemaInvalid ⇒ KHÔNG retry fallback'."""
    raw = yaml.safe_load(EXAMPLE_REGISTRY.read_text(encoding="utf-8"))
    raw["providers"].extend([
        {"provider_id": "prov-a", "kind": "fake", "egress": "local"},
        {"provider_id": "prov-b", "kind": "fake", "egress": "local"},
    ])
    raw["models"].extend([
        {"model_id": "prov-a/gen", "provider_id": "prov-a", "roles": ["generate"]},
        {"model_id": "prov-b/gen", "provider_id": "prov-b", "roles": ["generate"]},
    ])
    for task in raw["tasks"]:
        if task.get("task") == "generate":
            task["champion"] = "prov-a/gen"
            task["fallbacks"] = ["prov-b/gen"]
    path = tmp_path / "registry.p2.twofake.yaml"
    path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    return path


def make_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    mode: str | None,
    registry: Path | None,
    seq: bool | None = None,
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
    if seq is None:
        monkeypatch.delenv("SCP_BRAIN_SHADOW_GENERATE", raising=False)
    elif seq:
        monkeypatch.setenv("SCP_BRAIN_SHADOW_GENERATE", "1")
    else:
        monkeypatch.delenv("SCP_BRAIN_SHADOW_GENERATE", raising=False)


def make_ctx(answer: str) -> AskPipelineContext:
    req = AskRequest(question=QUESTION)
    ctx = AskPipelineContext(req=req, request=None, namespace={})
    ctx.ai_answer = answer  # production answer có sẵn ⇒ stage bỏ qua nhánh LLM
    return ctx


def run_stage(ctx: AskPipelineContext) -> None:
    asyncio.run(GenerationStage().execute(ctx))


def run_stage_drain(ctx: AskPipelineContext) -> list[asyncio.Task[None]]:
    """Chạy stage trong loop sống đủ lâu để fire-and-forget task hoàn tất."""
    async def _main() -> None:
        await GenerationStage().execute(ctx)
        pending = brain_shadow.drain_pending_generate_tasks()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    asyncio.run(_main())
    return brain_shadow.drain_pending_generate_tasks()


def read_trace(tmp_path: Path) -> list[dict[str, Any]] | None:
    trace = tmp_path / "brain_trace.jsonl"
    if not trace.exists():
        return None
    lines = [l for l in trace.read_text(encoding="utf-8").strip().splitlines() if l]
    return [json.loads(l) for l in lines]


def declare_test_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(
        SECRET_PURPOSES,
        SECRET_REF,
        SecretSpec(
            name=SECRET_REF,
            purpose="P2 shadow generate test key (test-only)",
            consumers=("tests.T01_pipeline.test_brain_p2_shadow_generate",),
        ),
    )
    monkeypatch.setenv(SECRET_REF, "test-key-123")


def make_openai_adapter(
    handler: Any,
    *,
    secret_ref: str = SECRET_REF,
    **kwargs: Any,
) -> OpenAICompatibleAdapter:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OpenAICompatibleAdapter(
        "test-ext",
        base_url="http://testserver/v1",
        secret_ref=secret_ref,
        model_id="test-ext/gen",
        client=client,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# (a) mode=off (default) — hook no-op tuyệt đối
# ---------------------------------------------------------------------------


def test_a_mode_off_noop_no_trace_no_build_no_adapter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reg = write_test_registry(tmp_path)
    make_env(tmp_path, monkeypatch, mode=None, registry=reg)

    build_calls = {"n": 0}
    real_build = brain_shadow.build_generate_shadow_layer

    def counting_build(*args: Any, **kwargs: Any) -> Any:
        build_calls["n"] += 1
        return real_build(*args, **kwargs)

    monkeypatch.setattr(brain_shadow, "build_generate_shadow_layer", counting_build)

    ctx = make_ctx(PROD_ANSWER)
    run_stage(ctx)

    assert build_calls["n"] == 0  # mode=off ⇒ không dựng layer
    assert read_trace(tmp_path) is None  # không trace
    assert ctx.ai_answer == PROD_ANSWER  # answer không đổi


def test_a_mode_off_with_registry_and_seq_flag_still_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reg = write_test_registry(tmp_path)
    make_env(tmp_path, monkeypatch, mode=None, registry=reg, seq=True)
    ctx = make_ctx(PROD_ANSWER)
    run_stage(ctx)
    assert read_trace(tmp_path) is None
    assert ctx.ai_answer == PROD_ANSWER


# ---------------------------------------------------------------------------
# (b) mode=shadow + FakeAdapter — trace task=generate, answer KHÔNG đổi
# ---------------------------------------------------------------------------


def _assert_generate_trace_ok(records: list[dict[str, Any]]) -> None:
    gen_runs = [r for r in records if r.get("task") == "generate" and "shadow" not in r]
    comparisons = [
        r for r in records if r.get("task") == "generate" and r.get("shadow") is True
    ]
    assert len(gen_runs) == 1 and len(comparisons) == 1
    run_rec, cmp_rec = gen_runs[0], comparisons[0]
    assert run_rec["status"] == "ok"
    assert run_rec["schema_valid"] is True
    assert run_rec["provider_id"] == "local-fake"
    assert run_rec["model_id"] == "local-fake/router"
    # Provenance trung thực: adjudicated_by = provider của adapter.
    assert cmp_rec["adjudicated_by"] == "local-fake"
    assert cmp_rec["shadow_status"] == "ok"
    assert cmp_rec["production_answer_sha256"] == hashlib.sha256(
        PROD_ANSWER.encode("utf-8")
    ).hexdigest()
    assert cmp_rec["shadow_output_present"] is True


def test_b_shadow_generate_sequential_answer_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reg = write_test_registry(tmp_path)
    make_env(tmp_path, monkeypatch, mode="shadow", registry=reg, seq=True)

    ctx = make_ctx(PROD_ANSWER)
    run_stage(ctx)

    assert ctx.ai_answer == PROD_ANSWER  # KHÔNG ĐỔI đáp án user
    records = read_trace(tmp_path)
    assert records is not None
    _assert_generate_trace_ok(records)
    # Prompt thô không lộ vào trace — chỉ input_hash.
    raw = (tmp_path / "brain_trace.jsonl").read_text(encoding="utf-8")
    for marker in LEAK_MARKERS:
        assert marker not in raw


def test_b_shadow_generate_fire_and_forget_completes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reg = write_test_registry(tmp_path)
    make_env(tmp_path, monkeypatch, mode="shadow", registry=reg, seq=False)

    ctx = make_ctx(PROD_ANSWER)
    leftover = run_stage_drain(ctx)
    assert leftover == []  # task đã hoàn tất + tự gỡ khỏi strong-ref set

    records = read_trace(tmp_path)
    assert records is not None
    _assert_generate_trace_ok(records)
    assert ctx.ai_answer == PROD_ANSWER


def test_b_shadow_generate_missing_registry_swallows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_env(tmp_path, monkeypatch, mode="shadow", registry=None, seq=True)
    ctx = make_ctx(PROD_ANSWER)
    run_stage(ctx)  # không raise
    assert ctx.ai_answer == PROD_ANSWER
    assert read_trace(tmp_path) is None


# ---------------------------------------------------------------------------
# (c) output sai schema ⇒ trace schema_valid=false, KHÔNG crash, KHÔNG retry
# ---------------------------------------------------------------------------


def test_c_bad_schema_output_traced_not_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reg = write_test_registry(tmp_path)
    make_env(tmp_path, monkeypatch, mode="shadow", registry=reg, seq=True)

    config = BrainConfig.load(reg)
    bad_layer = BrainLayer(
        config,
        adapters={
            "local-fake": FakeAdapter(
                "local-fake",
                outputs={"generate": {"wrong": "x"}},  # thiếu key 'answer'
                model_id="local-fake/router",
            )
        },
    )
    monkeypatch.setattr(brain_shadow, "build_generate_shadow_layer", lambda: bad_layer)

    ctx = make_ctx(PROD_ANSWER)
    run_stage(ctx)  # KHÔNG crash

    assert ctx.ai_answer == PROD_ANSWER
    records = read_trace(tmp_path)
    assert records is not None
    gen_runs = [r for r in records if r.get("task") == "generate" and "shadow" not in r]
    comparisons = [r for r in records if r.get("shadow") is True]
    assert gen_runs[0]["status"] == "schema_invalid"
    assert gen_runs[0]["schema_valid"] is False
    assert comparisons[0]["shadow_status"] == "schema_invalid"
    assert comparisons[0]["adjudicated_by"] == "local-fake"


def test_c_adapter_schema_invalid_no_retry_no_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BrainSchemaInvalid từ adapter ⇒ envelope schema_invalid + trace, và
    fallback KHÔNG được thử (không retry — anti-placebo)."""
    reg = write_two_fake_registry(tmp_path)
    make_env(tmp_path, monkeypatch, mode="shadow", registry=reg, seq=True)

    class _RaisingAdapter(FakeAdapter):
        async def run(self, task: str, payload: Any, ctx: Any) -> Any:
            self.calls.append(task)
            raise BrainSchemaInvalid(f"schema hỏng cho task '{task}'")

    adapter_a = _RaisingAdapter("prov-a", model_id="prov-a/gen")
    adapter_b = FakeAdapter("prov-b", model_id="prov-b/gen")
    config = BrainConfig.load(reg)
    layer = BrainLayer(config, adapters={"prov-a": adapter_a, "prov-b": adapter_b})
    monkeypatch.setattr(brain_shadow, "build_generate_shadow_layer", lambda: layer)

    ctx = make_ctx(PROD_ANSWER)
    run_stage(ctx)

    assert ctx.ai_answer == PROD_ANSWER
    assert adapter_a.calls == ["generate"]
    assert adapter_b.calls == []  # KHÔNG retry qua fallback
    records = read_trace(tmp_path)
    assert records is not None
    gen_runs = [r for r in records if r.get("task") == "generate" and "shadow" not in r]
    assert gen_runs[0]["status"] == "schema_invalid"
    assert gen_runs[0]["schema_valid"] is False
    assert gen_runs[0]["provider_id"] == "prov-a"


# ---------------------------------------------------------------------------
# (d) adapter raise ⇒ nuốt, pipeline hoàn tất
# ---------------------------------------------------------------------------


def test_d_stage_survives_shadow_generate_hook_raising(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reg = write_test_registry(tmp_path)
    make_env(tmp_path, monkeypatch, mode="shadow", registry=reg, seq=True)

    async def boom(**kwargs: Any) -> None:
        raise RuntimeError("shadow generate exploded")

    ctx = make_ctx(PROD_ANSWER)
    ctx.namespace["_brain_shadow_generate"] = boom
    run_stage(ctx)  # KHÔNG raise — stage-level try/except nuốt

    assert ctx.ai_answer == PROD_ANSWER
    assert read_trace(tmp_path) is None


def test_d_adapter_timeout_swallowed_trace_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reg = write_test_registry(tmp_path)
    make_env(tmp_path, monkeypatch, mode="shadow", registry=reg, seq=True)

    timeout_layer = BrainLayer(
        BrainConfig.load(reg),
        adapters={
            "local-fake": FakeAdapter(
                "local-fake", fail_for={"generate": "timeout"}, model_id="local-fake/router"
            )
        },
    )
    monkeypatch.setattr(brain_shadow, "build_generate_shadow_layer", lambda: timeout_layer)

    ctx = make_ctx(PROD_ANSWER)
    run_stage(ctx)  # KHÔNG crash

    assert ctx.ai_answer == PROD_ANSWER
    records = read_trace(tmp_path)
    assert records is not None
    gen_runs = [r for r in records if r.get("task") == "generate" and "shadow" not in r]
    assert gen_runs[0]["status"] == "timeout"
    assert gen_runs[0]["schema_valid"] is None


# ---------------------------------------------------------------------------
# (e) OpenAI-compatible adapter — httpx.MockTransport (KHÔNG network)
# ---------------------------------------------------------------------------


_OK_BODY = {
    "choices": [{"message": {"role": "assistant", "content": "Paris"}}],
    "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
}


def test_e_openai_200_valid_maps_answer_and_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    declare_test_secret(monkeypatch)
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("Authorization")
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json=_OK_BODY)

    adapter = make_openai_adapter(handler)
    res = asyncio.run(
        adapter.run("generate", {"question": QUESTION}, BrainContext(run_id="run-e1"))
    )

    assert res.output == {"answer": "Paris"}
    assert res.model_id == "test-ext/gen"
    assert res.usage["input_tokens"] == 10
    assert res.usage["output_tokens"] == 5
    assert res.usage["total_tokens"] == 15
    assert res.usage["provider"] == "test-ext"
    assert res.usage["latency_ms"] >= 0.0
    assert adapter.calls == ["generate"]
    # Request đúng shape OpenAI-compatible + Authorization Bearer từ secret_ref.
    assert captured["url"].endswith("/v1/chat/completions")
    assert captured["auth"] == "Bearer test-key-123"
    assert captured["body"]["model"] == "test-ext/gen"
    assert captured["body"]["messages"][0]["role"] == "system"
    assert captured["body"]["messages"][1]["role"] == "user"
    assert QUESTION in captured["body"]["messages"][1]["content"]


def test_e_openai_200_valid_through_shadow_layer_e2e(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reg = write_openai_registry(tmp_path)
    declare_test_secret(monkeypatch)
    make_env(tmp_path, monkeypatch, mode="shadow", registry=reg, seq=True)

    adapter = make_openai_adapter(lambda request: httpx.Response(200, json=_OK_BODY))
    layer = BrainLayer(BrainConfig.load(reg), adapters={"test-ext": adapter})
    monkeypatch.setattr(brain_shadow, "build_generate_shadow_layer", lambda: layer)

    ctx = make_ctx(PROD_ANSWER)
    run_stage(ctx)

    assert ctx.ai_answer == PROD_ANSWER
    records = read_trace(tmp_path)
    assert records is not None
    gen_runs = [r for r in records if r.get("task") == "generate" and "shadow" not in r]
    comparisons = [r for r in records if r.get("shadow") is True]
    assert gen_runs[0]["status"] == "ok"
    assert gen_runs[0]["provider_id"] == "test-ext"
    assert gen_runs[0]["model_id"] == "test-ext/gen"
    assert comparisons[0]["adjudicated_by"] == "test-ext"
    assert adapter.calls == ["generate"]
    raw = (tmp_path / "brain_trace.jsonl").read_text(encoding="utf-8")
    assert "test-key-123" not in raw  # secret không bao giờ lọt trace


@pytest.mark.parametrize(
    ("status", "kind"),
    [(401, "auth"), (403, "auth"), (429, "rate_limit"), (500, "provider_5xx"), (503, "provider_5xx"), (400, "bad_request")],
)
def test_e_openai_http_error_kinds(
    status: int, kind: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    declare_test_secret(monkeypatch)
    adapter = make_openai_adapter(
        lambda request, _s=status: httpx.Response(_s, json={"error": "x"})
    )
    with pytest.raises(BrainProviderError) as ei:
        asyncio.run(
            adapter.run("generate", {"question": "q"}, BrainContext(run_id="run-err"))
        )
    assert ei.value.kind == kind


def test_e_openai_timeout_maps_brain_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    declare_test_secret(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("read timed out")

    adapter = make_openai_adapter(handler, timeout_s=2.5)
    with pytest.raises(BrainTimeout):
        asyncio.run(
            adapter.run("generate", {"question": "q"}, BrainContext(run_id="run-t"))
        )


def test_e_openai_bad_json_is_schema_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    declare_test_secret(monkeypatch)
    adapter = make_openai_adapter(
        lambda request: httpx.Response(200, content=b"<html>not json</html>")
    )
    with pytest.raises(BrainSchemaInvalid):
        asyncio.run(
            adapter.run("generate", {"question": "q"}, BrainContext(run_id="run-bj"))
        )


def test_e_openai_unmappable_choices_is_schema_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    declare_test_secret(monkeypatch)
    adapter = make_openai_adapter(lambda request: httpx.Response(200, json={"choices": []}))
    with pytest.raises(BrainSchemaInvalid):
        asyncio.run(
            adapter.run("generate", {"question": "q"}, BrainContext(run_id="run-uc"))
        )


def test_e_openai_empty_content_is_schema_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    declare_test_secret(monkeypatch)
    body = {"choices": [{"message": {"role": "assistant", "content": "   "}}]}
    adapter = make_openai_adapter(lambda request: httpx.Response(200, json=body))
    with pytest.raises(BrainSchemaInvalid):
        asyncio.run(
            adapter.run("generate", {"question": "q"}, BrainContext(run_id="run-ec"))
        )


def test_e_openai_multi_key_contract_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Anti-placebo: contract nhiều key (fact_check: verdict+claims) không được
    # bịa từ 1 đoạn text — fail-closed BrainSchemaInvalid.
    declare_test_secret(monkeypatch)
    adapter = make_openai_adapter(lambda request: httpx.Response(200, json=_OK_BODY))
    with pytest.raises(BrainSchemaInvalid):
        asyncio.run(
            adapter.run("fact_check", {"question": "q"}, BrainContext(run_id="run-mk"))
        )


def test_e_openai_missing_secret_is_auth_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # secret_ref ĐÃ khai báo trong SECRET_PURPOSES nhưng env thiếu ⇒ resolve
    # trả None ⇒ auth error (không bịa key, không log giá trị).
    monkeypatch.setitem(
        SECRET_PURPOSES,
        SECRET_REF,
        SecretSpec(
            name=SECRET_REF,
            purpose="P2 test (declared, env intentionally missing)",
            consumers=("tests.T01_pipeline.test_brain_p2_shadow_generate",),
        ),
    )
    adapter = make_openai_adapter(
        lambda request: httpx.Response(200, json=_OK_BODY),
    )
    with pytest.raises(BrainProviderError) as ei:
        asyncio.run(
            adapter.run("generate", {"question": "q"}, BrainContext(run_id="run-ms"))
        )
    assert ei.value.kind == "auth"


def test_e_openai_undeclared_secret_ref_is_auth_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # secret_ref không có trong SECRET_PURPOSES ⇒ resolve raise ValueError ⇒
    # adapter chuẩn hóa auth error (KHÔNG fallback đọc env trực tiếp).
    adapter = make_openai_adapter(
        lambda request: httpx.Response(200, json=_OK_BODY),
        secret_ref="SCP_P2_NEVER_DECLARED",
    )
    monkeypatch.setenv("SCP_P2_NEVER_DECLARED", "leak-attempt")
    with pytest.raises(BrainProviderError) as ei:
        asyncio.run(
            adapter.run("generate", {"question": "q"}, BrainContext(run_id="run-ud"))
        )
    assert ei.value.kind == "auth"


def test_e_openai_empty_secret_ref_is_auth_fail_closed() -> None:
    adapter = OpenAICompatibleAdapter(
        "test-ext",
        base_url="http://testserver/v1",
        secret_ref="",
        model_id="m",
        client=httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=_OK_BODY)
        )),
    )
    with pytest.raises(BrainProviderError) as ei:
        asyncio.run(
            adapter.run("generate", {"question": "q"}, BrainContext(run_id="run-es"))
        )
    assert ei.value.kind == "auth"


def test_e_timeout_clamped_safe_bounds() -> None:
    assert clamp_timeout_s(None) == 30.0
    assert clamp_timeout_s(999.0) == 120.0
    assert clamp_timeout_s(0.001) == 1.0
    assert clamp_timeout_s(float("nan")) == 30.0
    assert clamp_timeout_s("abc") == 30.0  # type: ignore[arg-type]
    adapter = make_openai_adapter(
        lambda request: httpx.Response(200, json=_OK_BODY), timeout_s=999
    )
    assert adapter._timeout_s == 120.0


def test_e_build_layer_uses_model_limits_timeout(tmp_path: Path) -> None:
    reg = write_openai_registry(tmp_path, timeout_s=7)
    layer = brain_shadow.build_generate_shadow_layer(registry_path=reg)
    adapter = layer.adapters["test-ext"]
    assert isinstance(adapter, OpenAICompatibleAdapter)
    assert adapter._timeout_s == 7.0  # timeout lấy từ model limits registry
    assert adapter._model_id == "test-ext/gen"


def test_e_registry_limits_wrong_type_fail_closed(tmp_path: Path) -> None:
    from scp.brain_layer.errors import BrainConfigError

    bad = tmp_path / "registry.bad-limits.yaml"
    bad.write_text(
        "version: 1\n"
        "providers:\n  - {provider_id: p, kind: fake, egress: local}\n"
        "models:\n  - {model_id: m, provider_id: p, roles: [generate], limits: 30}\n"
        "tasks:\n  - {task: generate, role: generate, champion: m, output_required: [answer]}\n",
        encoding="utf-8",
    )
    with pytest.raises(BrainConfigError):
        BrainConfig.load(bad)
