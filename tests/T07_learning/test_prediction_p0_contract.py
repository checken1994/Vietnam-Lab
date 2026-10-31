"""[PREDICTION-P0 · 2026-10-31] Hợp đồng sửa P0 + P2 của audit hệ dự đoán.

Tại sao (audit ``prediction-system-audit.md``, VERIFIED trên dee3ee5e):
- Hook forecast trong /ask gọi ``ForecastLedger(db_path=...)`` + ``record_case`` (không tồn tại)
  ⇒ TypeError 100%, bị ``except Exception`` nuốt; trigger là bảng từ khoá VI/EN cứng (luật 9).
- Hook calibration ghi 661 dự đoán / 0 đối chiếu: confidence NULL, không khoá chủ thể, domain
  luôn "general" ⇒ về cấu trúc KHÔNG đóng được.
- ``/v105/calibration/resolve`` + ``/v105/forecast/resolve`` văng 500 (sai chữ ký);
  ``/stats``/``/accuracy`` qua ``hasattr`` vào hàm không tồn tại ⇒ rỗng kèm "active" (placebo).

Mọi test dưới đây chạy trên code thật (LedgerStage, CalibrationLedger SQLite, route FastAPI
thật); chỉ thay ErrorStore bằng seam ``STORE_FACTORY`` để không ghi vào data/ của repo.
Trên dee3ee5e các test này ĐỎ (xem done file prediction-p0).
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import unicodedata
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request as StarletteRequest

from scp.api._shared import verify_admin
from scp.api_server_parts.helpers import AskRequest
from scp.api_server_parts.pipeline import AskPipelineContext, LedgerStage
from scp.runtime.question_router import LANE_FACTUAL, RouteDecision

FAKE_SHA = "ab" * 20


# ───────────────────────────── helpers ─────────────────────────────


def _request(run_id: str = "") -> StarletteRequest:
    scope = {
        "type": "http", "method": "POST", "path": "/ask", "headers": [], "query_string": b"",
        "client": ("127.0.0.1", 50000), "server": ("testserver", 80), "scheme": "http",
    }
    req = StarletteRequest(scope)
    req.state.scp_run = SimpleNamespace(run_id=run_id, trace_id="trace_p0") if run_id else None
    req.state.trace_id = "trace_p0"
    return req


class _Verdict:
    def __init__(self, verdict: str, confidence: Any, domain: str = "general") -> None:
        self.verdict = verdict
        self.confidence = confidence
        self.domain = domain
        self.reasoning = ""
        self.final_answer = "answer"
        self.slm_responses: list = []
        self.evidence: dict = {}


def _ctx(question: str, verdict: str = "ABSTAIN", confidence: Any = 0.0, *,
         judge_domain: str = "general", route_domain: str = "health", run_id: str = "run_p0") -> AskPipelineContext:
    ctx = AskPipelineContext(req=AskRequest(question=question), request=_request(run_id))
    ctx.route_decision = RouteDecision(
        intent="LOOKUP", domain=route_domain, confidence=0.8, via="test-semantic", lane=LANE_FACTUAL,
        reason="test",
    )
    ctx.judge_verdict = _Verdict(verdict, confidence, judge_domain)
    ctx.api_final_answer = "Không đủ bằng chứng để trả lời."
    return ctx


class _FakeLessonStore:
    def __init__(self) -> None:
        self.records: list[dict] = []

    def add(self, **kw: Any) -> dict:
        self.records.append(kw)
        return {"id": f"lesson-{len(self.records)}", **kw}


def _rows(db: Path) -> list[dict]:
    with sqlite3.connect(str(db)) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute("SELECT * FROM calibration_predictions")]


def _expected_subject(question: str) -> str:
    """Hợp đồng khoá chủ thể = ``claim_key`` (design §2.1): sha256(NFKC + casefold + gộp khoảng
    trắng). Tính ĐỘC LẬP trong test (không gọi code sản phẩm). Không mẫu câu/từ khoá."""
    norm = " ".join(unicodedata.normalize("NFKC", question).casefold().split())
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def _expected_input_hash(question: str, draft: str = "", contexts: tuple = ()) -> str:
    raw = json.dumps({"question": question, "draft": draft, "contexts": list(contexts)},
                     ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("SCP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SCP_GIT_SHA", FAKE_SHA)
    return tmp_path


@pytest.fixture
def lesson_store(monkeypatch: pytest.MonkeyPatch) -> _FakeLessonStore:
    store = _FakeLessonStore()
    try:
        from scp.calibration import ask_hook
    except ImportError:  # chỉ để test đỏ-trước chạy tới assertion HÀNH VI trên code cũ
        return store
    monkeypatch.setattr(ask_hook, "STORE_FACTORY", lambda: store)
    ask_hook._reset_for_tests()
    return store


# ───────────────────────── 1. forecast hook ─────────────────────────


@pytest.mark.parametrize("question", [
    "Ngày mai trời có mưa không, bạn dự đoán thế nào?",   # VI (từ khoá cũ "dự đoán")
    "Will the euro rise against the dollar next month?",  # EN (từ khoá cũ "will")
    "来年の東京の気温はどうなると思いますか?",              # JA (không thuộc bảng cũ)
    "¿Subirá el precio del petróleo el próximo año?",     # ES (không thuộc bảng cũ)
])
@pytest.mark.asyncio
async def test_forecast_hook_has_no_keyword_trigger_and_no_swallowed_typeerror(
    question: str, data_dir: Path, lesson_store: _FakeLessonStore,
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    """Không có tín hiệu ngữ nghĩa đã kiểm cho "ý định dự báo" ⇒ hook forecast TẮT
    (UNKNOWN_WITH_REASON) — không bảng từ khoá nào được kích hoạt, không TypeError bị nuốt."""
    from scp.forecast import ledger as fl

    calls: list[tuple] = []
    real_init = fl.ForecastLedger.__init__

    def _spy(self, *a: Any, **k: Any) -> None:
        calls.append((a, k))
        real_init(self, *a, **k)

    monkeypatch.setattr(fl.ForecastLedger, "__init__", _spy)
    caplog.set_level(logging.WARNING)
    ctx = _ctx(question, verdict="PASS", confidence=0.9)
    await LedgerStage().execute(ctx)

    assert ctx.final_response is not None
    assert calls == [], f"forecast ledger must not be triggered by wording: {calls}"
    assert not [r for r in caplog.records if "forecast hook failed" in r.getMessage()]
    assert not list(data_dir.glob("forecast*")), "no forecast artefact may be written from /ask"

    from scp.forecast.runtime import ask_forecast_hook_status

    st = ask_forecast_hook_status()
    assert st["status"] == "disabled"
    assert st["epistemic_status"] == "UNKNOWN_WITH_REASON"
    assert st["reason"]


# ───────────────────── 2. calibration hook: khoá chủ thể ─────────────────────


@pytest.mark.asyncio
async def test_calibration_hook_records_closable_prediction(
    data_dir: Path, lesson_store: _FakeLessonStore,
) -> None:
    question = "Thuốc   X có  an toàn cho trẻ em không?"
    ctx = _ctx(question, verdict="ABSTAIN", confidence=0.0, judge_domain="general", route_domain="health")
    await LedgerStage().execute(ctx)

    rows = _rows(data_dir / "calibration.sqlite")
    assert len(rows) == 1
    row = rows[0]
    # confidence là SỐ (0.0 là số hợp lệ; trước đây NULL 661/661)
    assert row["confidence"] is not None and float(row["confidence"]) == 0.0
    assert row["domain"] == "health", "domain phải lấy từ semantic router, không mặc định 'general'"
    assert row["subject_claim_id"] == _expected_subject(question)
    assert row["input_hash"] == _expected_input_hash(question)
    assert row["tested_sha"] == FAKE_SHA
    assert row["policy_hash"] and len(row["policy_hash"]) == 64
    assert row["semantic_verdict_at_prediction"] == "UNKNOWN"
    assert json.loads(row["evidence_refs_json"]) == ["run:run_p0"]
    pred = json.loads(row["prediction_json"])
    assert pred["kind"] == "withhold"
    assert pred["verdict"] == "ABSTAIN"
    assert pred["confidence"] == 0.0
    assert pred["domain_source"] == "semantic_router"
    assert pred["answer_sha256"] == hashlib.sha256(ctx.api_final_answer.encode("utf-8")).hexdigest()
    assert set(pred["policy"]) >= {"hook_version", "resolver_policy", "semantic_router_lock_sha256",
                                   "answer_relevance_calibration_sha256", "math_verify_code_sha256"}
    # riêng tư (design §2.3): ledger chỉ giữ HASH, không nguyên văn câu hỏi/đáp án
    assert question not in row["prediction_json"] and ctx.api_final_answer not in row["prediction_json"]
    assert lesson_store.records == []

    from scp.calibration.ask_hook import hook_health

    assert hook_health()["recorded"] == 1


@pytest.mark.asyncio
async def test_subject_key_is_stable_across_spacing_and_case(
    data_dir: Path, lesson_store: _FakeLessonStore,
) -> None:
    for q in ("Is aspirin safe for children?", "  is ASPIRIN   safe for children?"):
        await LedgerStage().execute(_ctx(q, verdict="UNKNOWN", confidence=0.25))
    rows = _rows(data_dir / "calibration.sqlite")
    assert len(rows) == 2
    assert rows[0]["subject_claim_id"] == rows[1]["subject_claim_id"]
    assert rows[0]["input_hash"] != rows[1]["input_hash"], "input_hash giữ nguyên văn (khác nhau)"
    assert {float(r["confidence"]) for r in rows} == {0.25}


@pytest.mark.asyncio
async def test_pass_delivered_is_a_scorable_verified_prediction(
    data_dir: Path, lesson_store: _FakeLessonStore,
) -> None:
    """PASS + DELIVER_VERIFIED_PASS ⇒ dự đoán VERIFIED (π-deliver) chấm được: sau khi Reality
    (resolver khác lineage) đối chiếu, ``metrics()`` có binary score + Brier THẬT (không None).
    Trên dee3ee5e: PASS không được ghi ⇒ Brier vĩnh viễn None."""
    from scp.calibration.ledger import CalibrationLedger

    question = "Thủ đô của Úc là gì?"
    ctx = _ctx(question, verdict="PASS", confidence=0.9, route_domain="geography")
    ctx.v98_context["decision_code"] = "DELIVER_VERIFIED_PASS"
    ctx.ai_answer = "Sydney"
    ctx.api_final_answer = "Sydney"
    await LedgerStage().execute(ctx)

    rows = _rows(data_dir / "calibration.sqlite")
    assert len(rows) == 1
    row = rows[0]
    assert row["semantic_verdict_at_prediction"] == "VERIFIED"
    assert float(row["confidence"]) == 0.9
    assert row["subject_claim_id"] == _expected_subject(question)
    assert row["input_hash"] == _expected_input_hash(question, "Sydney")
    pred = json.loads(row["prediction_json"])
    assert pred["kind"] == "deliver" and pred["decision_code"] == "DELIVER_VERIFIED_PASS"
    assert pred["draft_sha256"] == hashlib.sha256(b"Sydney").hexdigest()

    led = CalibrationLedger(db_path=str(data_dir / "calibration.sqlite"))
    try:
        assert led.metrics()["brier_score"] is None  # chưa đối chiếu ⇒ chưa có điểm (trung thực)
        led.resolve(row["prediction_id"], outcome="CONTRADICTED", resolver_type="reality_source",
                    resolver_id="independent-atlas", evidence_refs=["https://example.org/canberra"])
        m = led.metrics(domain="geography")
    finally:
        led.close()
    assert m["binary_scored_count"] == 1
    assert m["brier_score"] == pytest.approx(0.81)


@pytest.mark.parametrize("verdict,code,semantic,kind", [
    ("PASS", "", "UNKNOWN", "none"),                      # PASS không có mã giao-đã-kiểm ⇒ không chấm
    ("PASS", "DELIVER_CHATBOT", "UNKNOWN", "none"),
    ("PASS", "ERR_SECURITY_VIOLATION", "UNKNOWN", "none"),
    ("FAIL", "ERR_FACTUAL_FAILED", "CONTRADICTED", "withhold"),
    ("FAIL", "", "CONTRADICTED", "withhold"),
    ("FAIL", "ERR_RELEVANCE_UNVERIFIED", "UNKNOWN", "withhold"),   # chưa kiểm được ≠ khẳng định sai
    ("FAIL", "ERR_MATH_UNVERIFIED", "UNKNOWN", "withhold"),
    ("PARTIAL", "", "INSUFFICIENT", "withhold"),
    ("UNKNOWN", "", "UNKNOWN", "withhold"),
    ("FLAGGED", "", "UNKNOWN", "none"),
    ("ESCALATE", "ERR_GOVERNANCE_ESCALATE", "UNKNOWN", "none"),
])
@pytest.mark.asyncio
async def test_every_verdict_is_recorded_with_7_to_4_mapping(
    verdict: str, code: str, semantic: str, kind: str,
    data_dir: Path, lesson_store: _FakeLessonStore,
) -> None:
    ctx = _ctx("Câu hỏi kiểm ánh xạ verdict?", verdict=verdict, confidence=0.5)
    if code:
        ctx.v98_context["decision_code"] = code
    await LedgerStage().execute(ctx)
    rows = _rows(data_dir / "calibration.sqlite")
    assert len(rows) == 1
    assert rows[0]["semantic_verdict_at_prediction"] == semantic
    assert json.loads(rows[0]["prediction_json"])["kind"] == kind


@pytest.mark.usefixtures("relevance_confirmed")
@pytest.mark.asyncio
async def test_ask_pass_end_to_end_records_scorable_prediction(
    data_dir: Path, lesson_store: _FakeLessonStore, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """/ask thật (_ask_impl, judge giả PASS, reader độc lập xác nhận) ⇒ ledger có dự đoán VERIFIED
    đóng được. Trên dee3ee5e: 0 dòng (hook bỏ qua PASS)."""
    from scp.api_server_parts._ask_impl import _ask_impl

    monkeypatch.setattr(
        "scp.runtime.question_router.route_question",
        lambda q: RouteDecision(intent="REASONING", domain="geography", confidence=0.9, via="test",
                                lane=LANE_FACTUAL, reason="test"),
    )
    monkeypatch.setenv("SCP_MULTI_LLM_CROSSCHECK", "0")
    monkeypatch.setenv("SCP_WEB_FALLBACK", "0")

    async def _noop(*_a: Any, **_k: Any) -> None:
        return None

    monkeypatch.setattr("scp.api_server_parts._ask_impl._async_fact_check", _noop)

    class _Judge:
        async def judge_with_react_fallback(self, **_k: Any) -> dict:
            return {"verdict": "PASS", "confidence": 0.92, "reasoning": "mocked", "failures": [],
                    "evidence": {"governance_decision": "ALLOW"}, "slm_responses": [],
                    "final_answer": "Canberra is the capital of Australia."}

    monkeypatch.setattr("scp.api_server_parts._ask_impl.get_judge", lambda: _Judge())
    question = "What is the capital of Australia?"
    req = AskRequest(question=question, ai_answer="Canberra is the capital of Australia.")
    resp = await _ask_impl(req, _request())
    assert resp.verdict == "PASS", resp
    rows = _rows(data_dir / "calibration.sqlite")
    assert len(rows) == 1
    row = rows[0]
    assert row["semantic_verdict_at_prediction"] == "VERIFIED"
    assert row["confidence"] is not None
    assert row["subject_claim_id"] == _expected_subject(question)
    assert row["input_hash"] and row["tested_sha"] == FAKE_SHA and row["policy_hash"]
    assert json.loads(row["prediction_json"])["decision_code"] == "DELIVER_VERIFIED_PASS"


# ───────────────── 3. calibration hook: lỗi phải hiện ra ─────────────────


@pytest.mark.asyncio
async def test_calibration_write_failure_is_visible_lesson_and_ask_survives(
    data_dir: Path, lesson_store: _FakeLessonStore,
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    from scp.calibration import ledger as cl
    from scp.calibration.ask_hook import hook_health

    def _boom(self, **_k: Any) -> dict:
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(cl.CalibrationLedger, "record_prediction", _boom)
    caplog.set_level(logging.ERROR)
    ctx = _ctx("Giá vàng tuần sau thế nào?", verdict="UNKNOWN", confidence=0.4)
    await LedgerStage().execute(ctx)

    # /ask không hỏng, câu trả lời/verdict không bị đổi bởi lỗi ledger
    assert ctx.final_response is not None
    assert ctx.final_response.verdict == "UNKNOWN"
    # lỗi hiện ra: log có cấu trúc ở mức ERROR
    hits = [r for r in caplog.records if "calibration_hook_failed" in r.getMessage()]
    assert hits and hits[0].levelno == logging.ERROR
    assert getattr(hits[0], "scp_event", None) == "calibration_hook_failed"
    assert getattr(hits[0], "error_type", None) == "OperationalError"
    # lỗi hiện ra: đếm được
    health = hook_health()
    assert health["failed"] == 1 and health["recorded"] == 0
    assert health["last_failure"]["error_type"] == "OperationalError"
    # lỗi thành bài học HYPOTHESIS (không bao giờ verified)
    assert len(lesson_store.records) == 1
    lesson = lesson_store.records[0]
    assert lesson["error_type"] == "calibration_hook_operationalerror"
    assert lesson["details"]["epistemic_status"] == "HYPOTHESIS"
    assert lesson["details"]["verified"] is False
    assert lesson["details"]["promotion"] == "never_automatic"
    assert lesson["verdict"] == "UNVERIFIED"


def test_missing_confidence_is_not_fabricated(
    data_dir: Path, lesson_store: _FakeLessonStore,
) -> None:
    """confidence None ⇒ KHÔNG bịa 0.0; không ghi dòng không-đóng-được; lỗi hiện ra + bài học.
    (Gọi thẳng hook: AskResponse không nhận confidence None nên LedgerStage không tới được.)"""
    from scp.calibration import ask_hook

    ctx = _ctx("Ai thắng?", verdict="UNKNOWN", confidence=None)
    with pytest.raises(ask_hook.CalibrationHookError) as ei:
        ask_hook.record_ask_prediction(data_dir, question=ctx.req.question, answer="a",
                                       verdict=ctx.judge_verdict, route_decision=ctx.route_decision,
                                       run_id="r", trace_id="t")
    assert ei.value.error_type == "confidence_missing"
    assert not (data_dir / "calibration.sqlite").exists()
    ask_hook.report_failure(ei.value, question=ctx.req.question, verdict_label="UNKNOWN")
    assert ask_hook.hook_health()["failed"] == 1
    assert lesson_store.records and lesson_store.records[0]["error_type"] == "calibration_hook_confidence_missing"


@pytest.mark.asyncio
async def test_nan_confidence_is_not_recorded_and_failure_visible(
    data_dir: Path, lesson_store: _FakeLessonStore,
) -> None:
    from scp.calibration.ask_hook import hook_health

    ctx = _ctx("Ai thắng?", verdict="UNKNOWN", confidence=float("nan"))
    await LedgerStage().execute(ctx)
    assert ctx.final_response is not None
    db = data_dir / "calibration.sqlite"
    assert not db.exists() or _rows(db) == []
    assert hook_health()["last_failure"]["error_type"] == "confidence_out_of_range"
    assert lesson_store.records[0]["error_type"] == "calibration_hook_confidence_out_of_range"


@pytest.mark.asyncio
async def test_repeated_failures_are_counted_but_lessons_are_rate_limited(
    data_dir: Path, lesson_store: _FakeLessonStore, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scp.calibration import ledger as cl
    from scp.calibration.ask_hook import hook_health

    monkeypatch.setattr(cl.CalibrationLedger, "record_prediction",
                        lambda self, **_k: (_ for _ in ()).throw(sqlite3.OperationalError("locked")))
    for i in range(5):
        await LedgerStage().execute(_ctx(f"Câu {i} về thời tiết?", verdict="UNKNOWN", confidence=0.3))
    assert hook_health()["failed"] == 5
    assert len(lesson_store.records) == 1, "cùng loại lỗi trong cửa sổ ⇒ 1 bài học (chống lũ)"


# ─────────────────────── 4. calibration routes ───────────────────────


def _client(router_module: Any) -> TestClient:
    app = FastAPI()
    app.include_router(router_module.router)
    app.dependency_overrides[verify_admin] = lambda: None
    return TestClient(app)


def _seed_prediction(data_dir: Path, **kw: Any) -> str:
    from scp.calibration.ledger import CalibrationLedger

    led = CalibrationLedger(db_path=str(data_dir / "calibration.sqlite"))
    try:
        row = led.record_prediction(
            domain=kw.get("domain", "health"), task_class="ask", predictor_type="judge",
            predictor_id="judge_v3", prediction={"verdict": "VERIFIED"},
            semantic_verdict=kw.get("semantic_verdict", "UNKNOWN"),
            confidence=kw.get("confidence", 0.7), subject_claim_id="askq1:x", input_hash="h",
        )
    finally:
        led.close()
    return row["prediction_id"]


def test_calibration_stats_is_honest_not_placebo(data_dir: Path) -> None:
    from scp.api.routes import calibration_routes

    c = _client(calibration_routes)
    empty = c.get("/v105/calibration/stats")
    assert empty.status_code == 200
    assert empty.json()["status"] == "no_data"
    assert empty.json()["status"] != "active"

    _seed_prediction(data_dir)
    body = c.get("/v105/calibration/stats").json()
    assert body["status"] == "ok"
    assert body["count"] == 1 and body["resolved_count"] == 0
    assert body["closable_predictions"] == 1
    assert body["resolver"]["status"] == "not_implemented"
    assert "ask_hook" in body


def test_calibration_stats_fails_closed_when_metrics_missing(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Không có hàm số liệu ⇒ 500 (fail-closed), KHÔNG trả {} kèm 'active'."""
    from scp.api.routes import calibration_routes
    from scp.calibration import ledger as cl

    monkeypatch.delattr(cl.CalibrationLedger, "metrics")
    r = _client(calibration_routes).get("/v105/calibration/stats")
    assert r.status_code == 500


def test_calibration_resolve_uses_real_contract(data_dir: Path) -> None:
    from scp.api.routes import calibration_routes
    from scp.calibration.ledger import CalibrationLedger

    pid = _seed_prediction(data_dir)
    c = _client(calibration_routes)
    ok = c.post("/v105/calibration/resolve", json={
        "prediction_id": pid, "outcome": "CONTRADICTED", "resolver_type": "reality_source",
        "resolver_id": "independent-source-1", "evidence_refs": ["https://example.org/report"],
    })
    assert ok.status_code == 200, ok.text
    assert ok.json()["outcome"] == "CONTRADICTED"
    led = CalibrationLedger(db_path=str(data_dir / "calibration.sqlite"))
    try:
        assert led.current_resolution(pid)["resolver_id"] == "independent-source-1"
    finally:
        led.close()


@pytest.mark.parametrize("body,code", [
    ({"outcome": "VERIFIED", "resolver_type": "r", "resolver_id": "x", "evidence_refs": []}, 422),
    ({"outcome": "PASS", "resolver_type": "r", "resolver_id": "x", "evidence_refs": ["e"]}, 422),
    ({"outcome": "VERIFIED", "resolver_type": "judge", "resolver_id": "judge_v3", "evidence_refs": ["e"]}, 422),
    ({"reality_verdict": "PASS", "resolution_evidence": "x"}, 422),
])
def test_calibration_resolve_rejects_invalid_resolution(data_dir: Path, body: dict, code: int) -> None:
    from scp.api.routes import calibration_routes

    pid = _seed_prediction(data_dir)
    r = _client(calibration_routes).post("/v105/calibration/resolve", json={"prediction_id": pid, **body})
    assert r.status_code == code, r.text


def test_calibration_resolve_unknown_prediction_is_404(data_dir: Path) -> None:
    from scp.api.routes import calibration_routes

    _seed_prediction(data_dir)
    r = _client(calibration_routes).post("/v105/calibration/resolve", json={
        "prediction_id": "pred_missing", "outcome": "VERIFIED", "resolver_type": "r",
        "resolver_id": "x", "evidence_refs": ["e"],
    })
    assert r.status_code == 404


def test_calibration_accuracy_reports_real_metrics(data_dir: Path) -> None:
    from scp.api.routes import calibration_routes
    from scp.calibration.ledger import CalibrationLedger

    c = _client(calibration_routes)
    nodata = c.get("/v105/calibration/accuracy").json()
    assert nodata["status"] == "no_data" and nodata["accuracy"]["brier_score"] is None

    pid = _seed_prediction(data_dir, semantic_verdict="VERIFIED", confidence=0.8)
    led = CalibrationLedger(db_path=str(data_dir / "calibration.sqlite"))
    try:
        led.resolve(pid, outcome="CONTRADICTED", resolver_type="reality", resolver_id="src",
                    evidence_refs=["https://example.org"])
    finally:
        led.close()
    body = c.get("/v105/calibration/accuracy?domain=health").json()
    assert body["status"] == "ok"
    assert body["accuracy"]["binary_scored_count"] == 1
    assert body["accuracy"]["brier_score"] == pytest.approx(0.64)


# ─────────────────────── 5. forecast routes ───────────────────────


def _write_registry(data_dir: Path) -> None:
    payload = {
        "lock_date": "2026-06-22",
        "total_cases": 1,
        "cases": [{
            "id": "c1", "domain": "papers", "claimant": "x", "claim": "y", "date": "2026-06-22",
            "url": "https://example.org/c1", "verdict": "FRAUD", "confidence": "HIGH",
            "antibodies": [], "outcome_code": 9,
        }],
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    payload["manifest_sha256"] = hashlib.sha256(canonical).hexdigest()
    (data_dir / "forecast_registry.json").write_text(json.dumps(payload), encoding="utf-8")


def test_forecast_stats_without_registry_is_honest(data_dir: Path) -> None:
    from scp.api.routes import forecast_routes

    body = _client(forecast_routes).get("/v105/forecast/stats").json()
    assert body["status"] == "no_registry"
    assert body["registry_cases"] == 0
    assert body["ask_hook"]["status"] == "disabled"


def test_forecast_stats_with_registry_reports_ledger_status(data_dir: Path) -> None:
    from scp.api.routes import forecast_routes

    _write_registry(data_dir)
    body = _client(forecast_routes).get("/v105/forecast/stats").json()
    assert body["status"] == "ok"
    assert body["registry_cases"] == 1
    assert body["registry_manifest_status"] == "MATCH_CANONICAL"
    assert body["resolved_events"] == 0


def test_forecast_resolve_without_registry_is_409_not_500(data_dir: Path) -> None:
    from scp.api.routes import forecast_routes

    r = _client(forecast_routes).post("/v105/forecast/resolve", json={
        "case_id": "c1", "outcome_code": 1, "evidence_url": "https://e", "evidence_sha256": "a" * 64,
        "adjudicator_id": "adj",
    })
    assert r.status_code == 409


def test_forecast_resolve_uses_real_contract(data_dir: Path) -> None:
    from scp.api.routes import forecast_routes

    _write_registry(data_dir)
    c = _client(forecast_routes)
    ok = c.post("/v105/forecast/resolve", json={
        "case_id": "c1", "outcome_code": 1, "evidence_url": "https://example.org/proof",
        "evidence_sha256": "c" * 64, "adjudicator_id": "independent-adjudicator",
    })
    assert ok.status_code == 200, ok.text
    assert ok.json()["event"] == "outcome_observed"
    stats = c.get("/v105/forecast/stats").json()
    assert stats["resolved_events"] == 1

    # thiếu bằng chứng ⇒ ledger từ chối (không mặc định bằng chứng giả) ⇒ 422
    bad = c.post("/v105/forecast/resolve", json={"case_id": "c1", "outcome_code": 1,
                                                  "resolution_note": "trust me"})
    assert bad.status_code == 422
    unknown = c.post("/v105/forecast/resolve", json={
        "case_id": "nope", "outcome_code": 1, "evidence_url": "https://e", "evidence_sha256": "c" * 64,
        "adjudicator_id": "a",
    })
    assert unknown.status_code == 404


def test_forecast_case_registration_is_not_a_silent_sink(data_dir: Path) -> None:
    """POST /cases trước đây ghi forecast_registry.jsonl mà không ai đọc (LIVE-SINK).
    Registry R44 là sổ KHOÁ: đăng ký runtime chưa có hợp đồng ⇒ 501 trung thực, không ghi gì."""
    from scp.api.routes import forecast_routes

    r = _client(forecast_routes).post("/v105/forecast/cases", json={
        "id": "n1", "domain": "x", "claimant": "u", "claim": "c", "date": "2026-10-31", "url": "https://e",
        "verdict": "FRAUD", "confidence": "HIGH", "antibodies": [], "outcome_code": 9,
    })
    assert r.status_code == 501
    assert not (data_dir / "forecast_registry.jsonl").exists()


def test_forecast_list_cases_reads_registry(data_dir: Path) -> None:
    from scp.api.routes import forecast_routes

    c = _client(forecast_routes)
    assert c.get("/v105/forecast/cases").json()["status"] == "no_registry"
    _write_registry(data_dir)
    body = c.get("/v105/forecast/cases").json()
    assert body["count"] == 1 and body["cases"][0]["id"] == "c1"
    assert body["cases"][0]["current_outcome_code"] == 9
