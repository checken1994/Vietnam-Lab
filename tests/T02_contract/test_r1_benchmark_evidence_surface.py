# SCP CIRCUIT: R1 — benchmark evidence-surface contract (hermetic, fixture corpus).
"""T02/R1 — coupling số học đo-lường giữa /ask evidence surface và benchmark
``D_evidence_recall`` (fix F-2 NF-1; KHÔNG đổi luật overlap của metric).

Bối cảnh (Q08 tốt recall@5=0.9812 fixture; F-2 đã nối contexts vào /ask, nhưng
F-2 NF-1 HIGH): harness (benchmark/run_benchmark_v2.py:607 +
scp/benchmark/run_benchmark_v2_parts/evaluate_questions_v2.py) CHỈ đọc
``response.slm_trace``; S24 fork trả ``slm_trace: []`` dù CÓ provenance thật
(``data_api_evidence``) → e2e D_evidence_recall vẫn 0. Phát hiện audit thêm:
``data_api_evidence`` KHÔNG phải field của AskResponse → HTTP serialization
drop nó — surface bằng chứng duy nhất client (benchmark) nhìn thấy là
``slm_trace``. Nên fork bắt buộc phải surface vào slm_trace (không thể chỉ
sửa harness).

Chứng minh (Reality Verifier, cấp B-unit + shape contract cấp C qua
AskResponse thật):
  (a) fork có provenance → ``slm_trace`` mang ``lookup_data_api``; entry
      survives ``AskResponse`` serialization; cả HAI runner (root + packaged)
      với luật overlap NGUYÊN VẸN cho D_evidence_recall > 0 trên gold fixture
      corpus; và entry answer derive từ ``data_api_evidence`` (payload),
      KHÔNG phải ``final_answer`` (hai chuỗi khác nhau — assert phân biệt).
  (b) LOOKUP-ask: seam F-2 THẬT (_attach_canonical_retrieval →
      _attach_canonical_evidence, gọi chứ không sửa) trên FIXTURE corpus →
      stash request.state đủ keys mà renderer _ask_impl (Level-C đã chứng
      minh trong tests/T02_contract/test_flow_02b) render vào slm_trace, với
      text khớp gold → luật đo hiện hành cho recall 1.0.
  (c) chống bằng chứng giả / self-approve: harness-read-source KHÔNG đọc
      final_answer — answer khớp gold không được tính; evidence không khớp
      gold cho recall 0; corpus rỗng → [] → recall 0 (thật, không bịa);
      merge ``data_api_evidence`` (dict path in-process) không double-count
      khi fork đã surface.

Không skip/xfail; không mock scorer/judge; không mạng. Corpus prod absent
trong checkout này (Q08 F-3) — số e2e thật cần orchestrator cấp
data/rag_corpus rồi rerun live; ở đây chứng minh bằng FIXTURE corpus.
"""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

import scp.rag.canonical_retriever as cr_mod
from scp.api_server_parts.helpers import AskResponse
from scp.ask_kernel_adapter import AskKernelAdapter
from scp.benchmark.run_benchmark_v2 import compute_evidence_metrics as pkg_compute
from scp.benchmark.run_benchmark_v2_parts.evaluate_questions_v2 import (
    _response_evidence_surface as pkg_surface,
)
from scp.rag import retrieval_eval as ev
from scp.rag.canonical_retriever import CanonicalRetriever
from scp.runtime import question_router as qr

# Root runner là file standalone ngoài package (namespace import theo pattern
# tests/T03_capability/test_security_sweep_s4.py::test_run_benchmark_v2_*).
import benchmark.run_benchmark_v2 as rbm

# (async test đánh dấu riêng — pytest-asyncio strict mode; sync test không
# mang mark asyncio để tránh PytestWarning.)

# Gold + question đúng bằng fixture corpus (retrieval_eval.build_fixture từ
# _GEO_FACTS của scp/benchmark/question_generator.py — "thủ đô Pháp" →
# "Paris là thủ đô của Pháp").
GEO_GOLD = "Paris là thủ đô của Pháp"
GEO_QUESTION = "Thủ đô của Pháp là gì"

FORK_DATA = {
    "text": "Paris là thủ đô của Pháp.",
    "api_name": "Wikipedia (vi) — Pháp",
    "api_url": "https://vi.wikipedia.org/wiki/Ph%C3%A1p",
    "evidence": "Paris là thủ đô của Pháp.",
}

class AskReq:
    def __init__(self, question: str = GEO_QUESTION):
        self.question = question
        self.ai_answer = ""
        self.contexts: list[str] = []
        self.retrieved_context = ""
        self.session_id = "sess-r1-bench-surface"


@pytest.fixture(autouse=True)
def _router_env(monkeypatch):
    """Chốt default của seam (env isolation, pattern test_f2_ask_retrieval_wiring)."""
    for key in (
        "SCP_ASK_RETRIEVAL",
        "SCP_ASK_RETRIEVAL_K",
        "SCP_ASK_RETRIEVAL_MIN_SCORE",
        "SCP_ASK_RETRIEVAL_TIMEOUT_SECONDS",
        "SCP_T2_ROUTER",
        "SCP_T2_MIN_CONFIDENCE",
        "SCP_MULTI_LLM_CROSSCHECK",
        "SCP_LOOKUP_TIMEOUT_SECONDS",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("SCP_MULTI_LLM_CROSSCHECK", "0")


@pytest.fixture()
def fixture_corpus(tmp_path, monkeypatch):
    """FIXTURE corpus thật (repo gold data) làm canonical singleton."""
    root = tmp_path / "corpus"
    ev.build_fixture(root)
    monkeypatch.setattr(cr_mod, "_default", CanonicalRetriever(root=root))
    return root


async def _success_fork(monkeypatch, question: str = GEO_QUESTION) -> dict:
    """Chạy attempt_lookup_fork THẬT với data-API payload stub (hermetic —
    network/external transport bị cô lập, đúng pattern test_ask_lookup_fork).
    Phần ĐƯỢC đo (compose + surface slm_trace + serialization + harness đọc)
    là code thật, không mock."""

    async def _fake_route(q, gateway=None):
        return qr.RouteDecision(qr.LOOKUP, "geography", 0.75, "l0-keyword", "lookup_signal:interrogative")

    monkeypatch.setattr(qr, "route_question_async", _fake_route)
    monkeypatch.setattr(qr, "resolve_lookup_data", lambda *a, **k: dict(FORK_DATA))
    result = await qr.attempt_lookup_fork(AskReq(question))
    assert result is not None
    return result


# ---------------------------------------------------------------------------
# (a) Fork có provenance → D_evidence_recall > 0 qua CẢ HAI runner
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_fork_populates_slm_trace_from_real_evidence_not_empty(monkeypatch):
    result = await _success_fork(monkeypatch)
    trace = result["slm_trace"]
    # Trước R1: luôn luôn [] — fork đóng góp 0 vào metric.
    assert isinstance(trace, list) and len(trace) == 1
    entry = trace[0]
    assert entry["slm_name"] == "lookup_data_api"
    # Convention CỦA TÁCH với canonical_bm25 trong _ask_impl: cùng bộ keys.
    assert set(entry) >= {
        "domain", "slm_name", "answer", "confidence", "source", "evidence",
        "processing_time_ms",
    }
    assert entry["source"] == "data-api"
    assert entry["evidence"]["source_url"] == FORK_DATA["api_url"]
    assert entry["evidence"]["text"] == result["data_api_evidence"]
    # data_api_evidence path của verifier GIỮ NGUYÊN (API không đổi).
    assert result["v98_classification"]["provenance"] == "input_context_only"
    assert result["data_api_evidence"].startswith(FORK_DATA["text"])


@pytest.mark.asyncio
async def test_fork_evidence_entry_derives_from_payload_not_from_answer(monkeypatch):
    """(c-derivation) answer field của entry = payload evidence (data_api_evidence),
    không phải final_answer — hai chuỗi khác nhau (\n vs \n\n trước provenance
    suffix): equality bất đối xứng là bằng chứng derive đúng nguồn."""
    result = await _success_fork(monkeypatch)
    entry = result["slm_trace"][0]
    assert entry["answer"] == result["data_api_evidence"][:200]
    assert entry["answer"] != result["final_answer"][:200]


@pytest.mark.asyncio
async def test_fork_slm_trace_survives_askresponse_serialization_and_scores(
    monkeypatch,
):
    """Cấp shape-contract HTTP: validate qua AskResponse THẬT (model mà route
    /ask dùng response_model) → field ngoài model bị drop → harness chỉ còn
    slm_trace để đo; với R1, D_evidence_recall > 0 trên gold fixture."""
    result = await _success_fork(monkeypatch)
    payload = AskResponse(**result).model_dump()
    # Hợp đồng serialize: data_api_evidence KHÔNG thuộc model → HTTP client
    # không bao giờ thấy nó; slm_trace là surface duy nhất. Nếu ai đó thêm
    # field vào AskResponse, assert này buộc xem lại contract (fail-closed).
    assert "data_api_evidence" not in payload
    entries = pkg_surface(payload)
    assert any(e.get("slm_name") == "lookup_data_api" for e in entries)

    pkg = pkg_compute(entries, [GEO_GOLD])
    root = rbm.compute_evidence_metrics(rbm._response_evidence_surface(payload), [GEO_GOLD])
    assert pkg["evidence_recall"] == 1.0, pkg
    assert root["evidence_recall"] == 1.0, root


# ---------------------------------------------------------------------------
# (b) LOOKUP-ask (F-2 seam THẬT, fixture corpus) → map đúng vào metric
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_lookup_ask_real_seam_stashes_provenance_that_measures(
    fixture_corpus, tmp_path,
):
    trace_file = tmp_path / "trace.jsonl"
    adapter = AskKernelAdapter(db_path=":memory:", trace_path=str(trace_file))
    req = AskReq()
    state = SimpleNamespace()
    request = SimpleNamespace(state=state)
    # Code THẬT được GỌI (ask_kernel_adapter không bị sửa trong R1):
    await adapter._attach_canonical_evidence(req, request)

    hits = req.contexts
    assert hits, "fixture corpus phải cho bằng chứng LOOKUP"
    stash = getattr(state, "scp_ask_auto_evidence", None) or []
    assert len(stash) == len(hits)
    for hit in stash:
        # Provenance THẬT từ corpus — không phải từ answer:
        assert str(hit["source_url"]).startswith("https://")
        assert hit["chunk_id"] and hit["document_id"]
        # Renderer _ask_impl (Level-C: test_flow_02b) đọc đúng bộ keys này để
        # render slm_trace — stash contract guard:
        assert set(hit) >= {
            "chunk_id", "document_id", "source_url", "source_title",
            "text", "term_coverage", "retrieval_score",
        }
    # Render theo hợp đồng đã pin ở Level-C, rồi qua luật đo NGUYÊN VẸN:
    rendered = [
        {
            "slm_name": "canonical_bm25",
            "answer": str(h["text"])[:200],
            "source": "canonical-corpus",
            "evidence": {k: h[k] for k in ("source_url", "chunk_id", "text")},
        }
        for h in stash
    ]
    pkg = pkg_compute(rendered, [GEO_GOLD])
    root = rbm.compute_evidence_metrics(rendered, [GEO_GOLD])
    assert pkg["evidence_recall"] > 0.0, pkg
    assert root["evidence_recall"] > 0.0, root
    # Bằng chứng retrieve theo QUESTION khớp gold sentence của corpus:
    assert any(GEO_GOLD in str(h["text"]) for h in stash)


@pytest.mark.asyncio
async def test_real_retrieval_returns_empty_without_corpus_and_scores_zero(
    tmp_path, monkeypatch,
):
    """CORPUS_ABSENT → seam trả [] (fail-closed, không bịa evidence) →
    metric báo recall 0 trung thực. Đây chính là trạng thái prod của checkout
    này (Q08 F-3): số e2e thật cần corpus do orchestrator cấp."""
    empty_root = tmp_path / "no-corpus"
    (empty_root / "data" / "rag_corpus").mkdir(parents=True)
    monkeypatch.setattr(cr_mod, "_default", CanonicalRetriever(root=empty_root))
    hits = qr.attempt_canonical_retrieval(GEO_QUESTION)
    assert hits == []
    req = AskReq()
    request = SimpleNamespace(state=SimpleNamespace())
    adapter = AskKernelAdapter(db_path=":memory:", trace_path=str(tmp_path / "t.jsonl"))
    await adapter._attach_canonical_evidence(req, request)
    assert req.contexts == []  # req không đổi khi không có bằng chứng
    assert pkg_surface({"slm_trace": [], "final_answer": GEO_GOLD}) == []
    assert pkg_compute([], [GEO_GOLD])["evidence_recall"] == 0.0


# ---------------------------------------------------------------------------
# (c) Chống bằng chứng giả / self-approve ở tầng nguồn đọc harness
# ---------------------------------------------------------------------------
def test_read_source_never_uses_final_answer_as_evidence():
    """Answer khớp gold 100% nhưng slm_trace rỗng → recall 0. Metric đo bằng
    chứng được retrieve, không đo sự tự khớp của answer (DNA #21: phương tiện
    không được tự duyệt chính nó)."""
    data = {
        "final_answer": GEO_GOLD + " (Paris)",
        "verdict": "PASS",
        "confidence": 0.99,
        "slm_trace": [],
    }
    assert pkg_surface(data) == []
    assert rbm._response_evidence_surface(data) == []
    assert pkg_compute(pkg_surface(data), [GEO_GOLD])["evidence_recall"] == 0.0


def test_read_source_supports_expert_trace_fallback():
    """AskResponse công bố expert_* là canonical vocabulary, slm_* alias
    read-only — caller chỉ điền expert_trace vẫn phải được đo (nguồn đọc
    rộng đúng bản chất; luật không đổi)."""
    data = {
        "final_answer": "x",
        "expert_trace": [{"slm_name": "lookup_data_api", "answer": FORK_DATA["text"], "source": "data-api"}],
    }
    entries = pkg_surface(data)
    assert len(entries) == 1
    assert rbm.compute_evidence_metrics(rbm._response_evidence_surface(data), [GEO_GOLD])["evidence_recall"] == 1.0


def test_fork_dict_merge_branch_no_double_count(monkeypatch):
    """In-process dict còn giữ data_api_evidence: khi fork ĐÃ surface
    lookup_data_api vào slm_trace, surface không merge trùng (mỗi gold chỉ
    đếm 1 lần theo luật; không phình số đo)."""
    result = {"slm_trace": [{"slm_name": "lookup_data_api", "answer": FORK_DATA["text"], "source": "data-api"}], "data_api_evidence": FORK_DATA["text"], "final_answer": FORK_DATA["text"]}
    assert len(pkg_surface(result)) == 1
    assert len(rbm._response_evidence_surface(result)) == 1


def test_nonmatching_evidence_scores_zero(monkeypatch):
    """Evidence tới từ corpus thật nhưng KHÔNG khớp gold → recall 0. Có
    slm_trace không tự động là có điểm — luật overlap giữ quyền quyết định."""
    data = {
        "final_answer": GEO_GOLD,
        "slm_trace": [
            {"slm_name": "canonical_bm25", "answer": "Tokyo là thủ đô của Nhật Bản.", "source": "canonical-corpus"},
        ],
    }
    assert pkg_compute(pkg_surface(data), [GEO_GOLD])["evidence_recall"] == 0.0
    assert rbm.compute_evidence_metrics(rbm._response_evidence_surface(data), [GEO_GOLD])["evidence_recall"] == 0.0


def test_rules_unchanged_goldless_question_is_none():
    """Cố định luật hiện hành: gold rỗng → recall None (N/A), không phải 0 —
    R1 không âm thầm đổi semantics của denominator."""
    assert pkg_compute([{"answer": GEO_GOLD}], [])["evidence_recall"] is None
    assert rbm.compute_evidence_metrics([{"answer": GEO_GOLD}], [])["evidence_recall"] is None


def test_both_runners_read_evidence_via_surface_not_raw_field():
    """Mỗi điểm đọc trong evaluate-loop của CẢ HAI runner phải đi qua
    ``_response_evidence_surface`` — không còn read ``slm_trace`` thô.
    Test này bắt đúng failure pattern mà phiên R1 tự gặp trong verification
    loop (thêm helper nhưng sót call-site cũ khi code duplicate giữa hai
    runner) — để harness không bao giờ tự qua mặt chính nó nữa."""
    import inspect
    import re

    import scp.benchmark.run_benchmark_v2_parts.evaluate_questions_v2 as pkg_eval_mod

    sources = {
        "packaged": inspect.getsource(pkg_eval_mod.evaluate_questions_v2),
        "root": inspect.getsource(rbm.evaluate_questions_v2),
    }
    for name, src in sources.items():
        assert "_response_evidence_surface(data)" in src, name
        assert not re.search(
            r"scp_evidence\s*=\s*data\.get\(\s*['\"]slm_trace", src
        ), f"{name}: read-site thô còn sót"
