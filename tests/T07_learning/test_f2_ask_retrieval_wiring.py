# SCP CIRCUIT: F-2 — /ask ↔ CanonicalRetriever wiring tests (hermetic, fixture corpus).
"""T07/F-2 — wire auto-retrieval vào /ask (seam AskKernelAdapter.run_rag).

Bối cảnh (Q08 report, F-2 HIGH): retriever BM25 đã recall@5≈0.98 trên FIXTURE
corpus nhưng /ask không gọi nó ở đâu cả → evidence_recall e2e (0.3846,
bench 2026-09-12) bất động. Seam mới: LOOKUP ask thiếu-context →
``question_router.attempt_canonical_retrieval`` → contexts nạp vào
``req.contexts`` TRƯỚC handler → cùng bằng chứng đi vào grounding judge
(_ask_impl) + canonical ``verify_response``; structured evidence stash vào
``request.state.scp_ask_auto_evidence`` → ``_ask_impl`` surface vào
``slm_trace`` (field mà benchmark D_evidence_recall đọc). Level-C HTTP proof:
tests/T02_contract/test_flow_02b_f2_ask_retrieval_http.py.

Phạm vi chứng minh (Reality Verifier):
  (a) helper gate đúng: LOOKUP retrieve; REASONING/ambiguous/kill-switch/
      confidence-dưới-ngưỡng → [] (không attempt, không L2 LLM call);
  (b) fail-closed: corpus empty → [] — KHÔNG bịa evidence; mọi hit dưới
      min-score floor → []; lỗi/timeout → req không đổi;
  (c) wiring qua run_rag THẬT: kernel TaskKernel thật + verify_response thật
      + tier1 grounding thật (chỉ gate _llm_judge_async — credential/env
      isolation theo pattern test_ask_lookup_fork, KHÔNG thay thế check nào);
  (d) chống vòng lặp tự-duyệt: answer KHÔNG khớp bằng chứng → tier1
      REJECT_GROUNDING → verify CONTRADICTED DÙ LLM-judge gate always-PASS;
  (e) no double-retrieve: client đã gửi contexts → helper không chạy;
  (f) format hợp đồng đúng CanonicalRetriever.contexts().

Corpus: FIXTURE (retrieval_eval.build_fixture từ gold data của repo) — corpus
prod absent trong checkout (Q08 F-3). Không skip/xfail; không mock scorer.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

import pytest

import scp.rag.canonical_retriever as cr_mod
from scp.ask_kernel_adapter import AskKernelAdapter
from scp.rag import retrieval_eval as ev
from scp.rag.canonical_retriever import CanonicalRetriever
from scp.runtime import question_router as qr

pytestmark = pytest.mark.asyncio

GEO_LOOKUP = "Thủ đô của Pháp là gì"
CORPUS_ANSWER = "Paris là thủ đô của Pháp."


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _router_env(monkeypatch):
    """Chốt cấu hình mặc định của seam cho mọi test (env isolation)."""
    for key in (
        "SCP_ASK_RETRIEVAL",
        "SCP_ASK_RETRIEVAL_K",
        "SCP_ASK_RETRIEVAL_MIN_SCORE",
        "SCP_ASK_RETRIEVAL_TIMEOUT_SECONDS",
        "SCP_T2_ROUTER",
        "SCP_T2_MIN_CONFIDENCE",
        "SCP_MULTI_LLM_CROSSCHECK",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("SCP_MULTI_LLM_CROSSCHECK", "0")


@pytest.fixture()
def fixture_corpus(tmp_path, monkeypatch):
    """Real fixture corpus (repo gold data) injected as the default singleton."""
    root = tmp_path / "corpus"
    meta = ev.build_fixture(root)
    inst = CanonicalRetriever(root=root)
    monkeypatch.setattr(cr_mod, "_default", inst)
    return {"root": root, "inst": inst, "probes": meta["probes"]}


@pytest.fixture()
def empty_corpus(tmp_path, monkeypatch):
    """Corpus-absent scenario = đúng hiện trạng checkout này (prod corpus trống)."""
    root = tmp_path / "empty"
    root.mkdir()
    monkeypatch.setattr(cr_mod, "_default", CanonicalRetriever(root=root))
    return root


@pytest.fixture()
def judge_gate(monkeypatch):
    """Hermetic semantic LLM call ONLY (same pattern as test_ask_lookup_fork).

    RealityJudge + tier1 grounding + verify_response checks vẫn là code THẬT;
    chỉ credential-gated LLM call được gate về True để test offline chạy được —
    gate không thay thế bất kỳ check nào khác."""
    import scp.runtime.judge_llm as judge_mod

    async def _gate(question: str, ai_answer: str, context: str = "") -> bool:
        return True

    monkeypatch.setattr(judge_mod, "_llm_judge_async", _gate)


def _stats_guard():
    return qr.route_stats_snapshot()


def _retrieval_delta(before):
    after = qr.route_stats_snapshot()
    return {
        key: after[key] - before[key]
        for key in (
            "ask_retrieval_attempts",
            "ask_retrieval_hits",
            "ask_retrieval_empty",
            "ask_retrieval_errors",
        )
    }


class _State:
    pass


class _FakeRequest:
    """Starlette-Request-shaped stub: .state + .headers (begin() đọc headers)."""

    def __init__(self):
        self.state = _State()
        self.headers = {}


class _Req:
    def __init__(self, question=GEO_LOOKUP, contexts=None, retrieved_context="", ai_answer=""):
        self.question = question
        self.ai_answer = ai_answer
        self.contexts = list(contexts or [])
        self.retrieved_context = retrieved_context
        self.session_id = "sess-f2-test"
        self.source = "api"


def _good_response(answer: str) -> dict:
    return {
        "verdict": "PASS",
        "final_answer": answer,
        "confidence": 0.85,
        "domain": "geography",
        "governance_decision": "UPHOLD",
        "reasoning": "f2-test",
        "v98_classification": {"provenance": "input_context_only"},
        "slm_responses": [],
        "slm_trace": [],
        "elapsed_ms": 5.0,
        "session_id": "sess-f2-test",
        "run_id": "run-f2-test",
        "trace_id": "trace-f2-test",
        "run_status": "COMPLETED",
        "ledger_status": "COMMITTED",
    }


def _make_adapter(tmp_path):
    return AskKernelAdapter(
        db_path=str(tmp_path / "f2-kernel.sqlite3"),
        trace_path=str(tmp_path / "f2-trace.jsonl"),
    )


def _trace_rows(adapter):
    """TraceLedger entries nest the appended kwargs under ``fields``
    (scp/trace_ledger.py:31) — đọc đúng schema ledger, không đoán key phẳng."""
    return [
        json.loads(line).get("fields", {})
        for line in Path(adapter.trace.path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


# ---------------------------------------------------------------------------
# (a) helper gates — real BM25 over real fixture corpus
# ---------------------------------------------------------------------------
async def test_lookup_question_gets_grounded_fixture_hits(fixture_corpus):
    hits = qr.attempt_canonical_retrieval(GEO_LOOKUP)
    assert hits, "LOOKUP trên fixture corpus phải có bằng chứng"
    assert any("Paris" in h["text"] for h in hits)
    for h in hits:
        assert h["retrieval_score"] >= qr.ask_retrieval_min_score()
        assert 0 < len(h["text"]) <= qr.ASK_RETRIEVAL_CHUNK_MAX_CHARS
    assert len(hits) <= qr.ask_retrieval_k()


async def test_reasoning_and_ambiguous_never_retrieve(fixture_corpus):
    before = _stats_guard()
    assert qr.attempt_canonical_retrieval("Tính 2 + 2 bằng bao nhiêu") == []
    assert qr.attempt_canonical_retrieval("hello there") == []
    assert qr.attempt_canonical_retrieval("") == []
    assert _retrieval_delta(before)["ask_retrieval_attempts"] == 0


async def test_kill_switch_and_confidence_gate(fixture_corpus, monkeypatch):
    monkeypatch.setenv("SCP_ASK_RETRIEVAL", "0")
    before = _stats_guard()
    assert qr.attempt_canonical_retrieval(GEO_LOOKUP) == []
    assert _retrieval_delta(before) == {
        "ask_retrieval_attempts": 0,
        "ask_retrieval_hits": 0,
        "ask_retrieval_empty": 0,
        "ask_retrieval_errors": 0,
    }
    monkeypatch.delenv("SCP_ASK_RETRIEVAL")
    # LOOKUP do rule 'là gì' có confidence 0.75 < 0.9 → gate theo ngưỡng fork.
    monkeypatch.setenv("SCP_T2_MIN_CONFIDENCE", "0.9")
    before = _stats_guard()
    assert qr.attempt_canonical_retrieval(GEO_LOOKUP) == []
    assert _retrieval_delta(before)["ask_retrieval_attempts"] == 0


async def test_l0_gate_does_not_call_llm(fixture_corpus, monkeypatch):
    """Seam retrieval chỉ dùng L0 — một L2 call có thể mất 30-260s (S20
    evidence) + chi phí provider; gọi L2 cho read-path phụ là cấm theo
    scp-safe-latency-optimizer. Chốt bằng monkeypatch raise nếu L2 được gọi."""

    async def _boom(*a, **k):  # pragma: no cover - must never run
        raise AssertionError("auto-retrieval must not invoke L2 LLM classifier")

    monkeypatch.setattr(qr, "classify_l2_async", _boom)
    monkeypatch.setattr(qr, "classify_l2", _boom)
    assert qr.attempt_canonical_retrieval(GEO_LOOKUP)  # hits qua L0
    assert qr.attempt_canonical_retrieval("hello there") == []


# ---------------------------------------------------------------------------
# (b) fail-closed paths
# ---------------------------------------------------------------------------
async def test_empty_corpus_is_fail_closed_no_fabricated_evidence(empty_corpus):
    before = _stats_guard()
    hits = qr.attempt_canonical_retrieval(GEO_LOOKUP)
    assert hits == []  # KHÔNG bịa evidence khi corpus absent
    assert _retrieval_delta(before)["ask_retrieval_empty"] == 1


async def test_low_score_floor_rejects_generic_token_hits(tmp_path, monkeypatch):
    """4 doc CHỈ chứa term 'paris' với N=df=4 → idf ~0.1, mọi score << floor
    1.0 → empty (guard 'low-score thì không nạp'). Cùng corpus với floor=0 giữ
    lại → chứng minh floor là active control, không phải dead code."""
    root = tmp_path / "tiny"
    out = root / ev.CORPUS_SUBPATH
    out.parent.mkdir(parents=True)
    docs = [
        {
            "document_id": f"d{i}",
            "final_url": f"https://example.org/d{i}",
            "source_title": "",
            "chunks": [
                {"chunk_id": f"d{i}#c0", "document_id": f"d{i}", "text": "paris " * 700}
            ],
        }
        for i in range(4)
    ]
    out.write_text("\n".join(json.dumps(d) for d in docs) + "\n", encoding="utf-8")
    monkeypatch.setattr(cr_mod, "_default", CanonicalRetriever(root=root))
    before = _stats_guard()
    # 'What is paris?' khớp L0 interrogative_en → LOOKUP 0.75; token query
    # duy nhất 'paris' (what/is là stopword) → chỉ hit low-idf.
    assert qr.attempt_canonical_retrieval("What is paris?") == []
    assert _retrieval_delta(before)["ask_retrieval_empty"] == 1
    monkeypatch.setenv("SCP_ASK_RETRIEVAL_MIN_SCORE", "0")
    kept = qr.attempt_canonical_retrieval("What is paris?")
    assert kept, "floor=0 phải giữ các hit low-idf"
    # Budget trần: mỗi chunk ≤ 2400, tổng ≤ 8000 → tối đa 3/4 doc.
    assert len(kept) == 3
    assert all(len(h["text"]) <= qr.ASK_RETRIEVAL_CHUNK_MAX_CHARS for h in kept)
    assert sum(len(h["text"]) for h in kept) <= qr.ASK_RETRIEVAL_TOTAL_MAX_CHARS


@pytest.mark.parametrize(
    "raw,expected",
    [("", 1.0), ("abc", 1.0), ("-3", 1.0), ("inf", 1.0), ("nan", 1.0),
     ("1000001", 1.0), ("0", 0.0), ("2.5", 2.5)],
)
async def test_min_score_env_fails_closed(raw, expected, monkeypatch):
    monkeypatch.setenv("SCP_ASK_RETRIEVAL_MIN_SCORE", raw)
    assert qr.ask_retrieval_min_score() == expected


@pytest.mark.parametrize(
    "raw,expected",
    [("", 10.0), ("x", 10.0), ("0", 10.0), ("-2", 10.0), ("45", 10.0), ("20", 20.0)],
)
async def test_timeout_env_fails_closed(raw, expected, monkeypatch):
    monkeypatch.setenv("SCP_ASK_RETRIEVAL_TIMEOUT_SECONDS", raw)
    assert qr.ask_retrieval_timeout_seconds() == expected


@pytest.mark.parametrize("raw,expected", [("", 5), ("0", 5), ("9", 5), ("3", 3)])
async def test_k_env_fails_closed(raw, expected, monkeypatch):
    monkeypatch.setenv("SCP_ASK_RETRIEVAL_K", raw)
    assert qr.ask_retrieval_k() == expected


# ---------------------------------------------------------------------------
# (c)(d)(e) real adapter path: run_rag + kernel + verify_response + tier1
# ---------------------------------------------------------------------------
async def test_run_rag_injects_evidence_and_verifies_grounded_answer(
    fixture_corpus, judge_gate, tmp_path, monkeypatch
):
    monkeypatch.setenv("SCP_T2_ROUTER", "0")  # deterministic: data-API fork off
    adapter = _make_adapter(tmp_path)
    req = _Req()
    request = _FakeRequest()
    seen = {}

    async def handler(r, _request):
        seen["contexts"] = list(r.contexts)
        seen["state_hits"] = getattr(_request.state, "scp_ask_auto_evidence", None)
        return _good_response(CORPUS_ANSWER)

    before = _stats_guard()
    safe = await adapter.run_rag(req, request, handler)

    assert seen["contexts"], "LOOKUP thiếu-context phải được nạp contexts trước handler"
    assert all(str(c).startswith("[chunk_id=") for c in seen["contexts"])
    assert any("Paris" in c for c in seen["contexts"])
    assert seen["state_hits"] and seen["state_hits"][0]["source_url"].startswith("https://")
    assert safe["verdict"] == "PASS"  # grounded answer passes canonical verify
    assert _retrieval_delta(before) == {
        "ask_retrieval_attempts": 1,
        "ask_retrieval_hits": 1,
        "ask_retrieval_empty": 0,
        "ask_retrieval_errors": 0,
    }
    final_rows = [r for r in _trace_rows(adapter) if r.get("step_id") == "rag-read" and r.get("outcome")]
    rag_row = final_rows[-1]
    assert rag_row["verdict"] == "VERIFIED"
    assert (rag_row.get("grounded_ratio") or 0) > 0.5, "verify phải chấm trên corpus evidence đã nạp"


async def test_run_rag_anti_loop_ungrounded_answer_still_rejected(
    fixture_corpus, judge_gate, tmp_path, monkeypatch
):
    """CHỐNG VÒNG LẶP TỰ-DUYỆT: LLM-judge gate always-PASS nhưng answer
    không khớp bằng chứng corpus → tier1 REJECT_GROUNDING (chính là check mà
    retrieval KÍCH HOẠT) → canonical verify CONTRADICTED → withheld.
    Retrieval thêm evidence cho grounding check, không phải đường tự duyệt."""
    monkeypatch.setenv("SCP_T2_ROUTER", "0")
    adapter = _make_adapter(tmp_path)
    req = _Req()
    hallucination = "Chuối là một loại khí quyển nặng phát ra tiếng vang lạ thường."

    async def handler(r, _request):
        assert r.contexts  # evidence đã nạp — answer vẫn phải bị chấm trên nó
        return _good_response(hallucination)

    safe = await adapter.run_rag(req, _FakeRequest(), handler)
    assert str(safe["verdict"]) != "PASS"
    assert str(safe["final_answer"]).startswith("[SCP:")  # withheld
    rows = [r for r in _trace_rows(adapter) if r.get("step_id") == "rag-read" and r.get("outcome")]
    assert rows[-1]["verdict"] == "CONTRADICTED"
    assert (rows[-1].get("grounded_ratio") or 1.0) < 0.6


async def test_run_rag_no_double_retrieve_when_client_provides_contexts(
    fixture_corpus, judge_gate, tmp_path, monkeypatch
):
    monkeypatch.setenv("SCP_T2_ROUTER", "0")
    adapter = _make_adapter(tmp_path)
    client_ctx = "Băng là nguồn khách hàng tự chứng."
    req = _Req(contexts=[client_ctx])
    request = _FakeRequest()
    seen = {}

    async def handler(r, _request):
        seen["contexts"] = list(r.contexts)
        return _good_response(client_ctx)

    before = _stats_guard()
    await adapter.run_rag(req, request, handler)
    assert seen["contexts"] == [client_ctx]  # client evidence thắng, không mutate
    assert not hasattr(request.state, "scp_ask_auto_evidence")
    assert _retrieval_delta(before)["ask_retrieval_attempts"] == 0


async def test_run_rag_empty_corpus_leaves_request_unchanged(
    empty_corpus, judge_gate, tmp_path, monkeypatch
):
    """Prod-corpus-absent = hiện trạng checkout này: /ask chạy ĐÚNG như trước
    khi nối wire (fail-closed), không evidence, không lỗi."""
    monkeypatch.setenv("SCP_T2_ROUTER", "0")
    adapter = _make_adapter(tmp_path)
    req = _Req()
    request = _FakeRequest()
    seen = {}

    async def handler(r, _request):
        seen["contexts"] = list(r.contexts)
        return _good_response(CORPUS_ANSWER)

    before = _stats_guard()
    await adapter.run_rag(req, request, handler)
    assert seen["contexts"] == []
    assert not hasattr(request.state, "scp_ask_auto_evidence")
    assert _retrieval_delta(before)["ask_retrieval_empty"] == 1


async def test_retrieval_error_is_fail_closed(tmp_path, monkeypatch, judge_gate):
    """Fault injection vào tầng storage/corpus (RuntimeError) — KHÔNG được
    chặn /ask, KHÔNG được bịa evidence. Test giả lập LỖI, không giả lập PASS."""
    monkeypatch.setenv("SCP_T2_ROUTER", "0")
    broken = CanonicalRetriever(root=tmp_path / "nope")

    def _boom(*a, **k):
        raise RuntimeError("simulated corpus storage fault")

    monkeypatch.setattr(broken, "retrieve", _boom)
    monkeypatch.setattr(cr_mod, "_default", broken)
    adapter = _make_adapter(tmp_path)
    seen = {}

    async def handler(r, _request):
        seen["contexts"] = list(r.contexts)
        return _good_response(CORPUS_ANSWER)

    before = _stats_guard()
    safe = await adapter.run_rag(_Req(), _FakeRequest(), handler)
    assert seen["contexts"] == []  # lỗi retrieval không được chặn hay đổi /ask
    assert safe["verdict"] == "PASS"  # general-chat verify path như cũ
    delta = _retrieval_delta(before)
    assert delta["ask_retrieval_errors"] == 1


async def test_timeout_budget_fails_closed(fixture_corpus, monkeypatch, tmp_path):
    monkeypatch.setenv("SCP_T2_ROUTER", "0")
    monkeypatch.setenv("SCP_ASK_RETRIEVAL_TIMEOUT_SECONDS", "0.001")
    inst = fixture_corpus["inst"]
    real_load = inst._load

    def _slow_load():
        time.sleep(0.35)  # > budget 1ms
        real_load()

    monkeypatch.setattr(inst, "_load", _slow_load)
    adapter = _make_adapter(tmp_path)
    seen = {}

    async def handler(r, _request):
        seen["contexts"] = list(r.contexts)
        return _good_response(CORPUS_ANSWER)

    safe = await adapter.run_rag(_Req(), _FakeRequest(), handler)
    assert seen["contexts"] == []  # timeout → không evidence, /ask vẫn chạy
    assert safe["verdict"] == "PASS"


# ---------------------------------------------------------------------------
# verify_response accounting (real function, real checks)
# ---------------------------------------------------------------------------
async def test_verify_response_counts_injected_contexts(fixture_corpus, judge_gate, tmp_path):
    adapter = _make_adapter(tmp_path)
    req = _Req()
    task = adapter.begin(req.question, list(req.contexts), req.retrieved_context, req.session_id)
    hits = qr.attempt_canonical_retrieval(req.question)
    req.contexts = [qr.format_canonical_context(h) for h in hits]
    verification = await adapter.verify_response(req, _good_response(CORPUS_ANSWER), task)
    assert verification["verdict"] == "VERIFIED"
    assert verification["evidence_context_count"] == len(req.contexts) > 0
    assert verification["grounded_ratio"] > 0.5
    assert verification["checked"]["judge_pass"] is True


# ---------------------------------------------------------------------------
# (f) format contract: same canonical shape as CanonicalRetriever.contexts()
# ---------------------------------------------------------------------------
async def test_format_matches_retriever_contexts_shape(fixture_corpus):
    inst = fixture_corpus["inst"]
    hits = inst.retrieve(GEO_LOOKUP, k=5)
    formatted = [qr.format_canonical_context(h) for h in hits]
    assert formatted == inst.contexts(GEO_LOOKUP, k=5)
    assert re.match(r"^\[chunk_id=\S+\] source_url=https://\S+\n", formatted[0])
