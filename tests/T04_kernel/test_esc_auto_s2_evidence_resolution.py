# [esc-auto slice 2 · 2026-10-30] /ask ESCALATE ⇒ SCP tự xử lý bằng bằng chứng của chính nó.
#
# Owner: "đã tự động thì ai duyệt cho bạn ... ai rảnh mà ngồi pc cả ngày chỉ để bấm duyệt".
# Trước fix: ESCALATE thiếu chứng cứ ⇒ task ask-kernel nằm HUMAN_REVIEW chờ người; sau
# đó resolve_stuck_human_review_tasks đóng task với nhãn "*_audited_and_contained" dù
# KHÔNG có audit nào chạy (giải phóng im lặng, c1-escalation-done §5).
#
# Pin ở đây:
#   * ESCALATE ⇒ phiếu chứng cứ tự động (bounded, dedup theo claim), header cho người hỏi;
#   * nguồn đã staged ≥2 host độc lập ⇒ SCP tự promote qua cổng Tier-3 có receipt ⇒
#     RESOLVED_CITED (trích nguyên văn nguồn), task đóng với lý do thật;
#   * không có nguồn ⇒ hết lượt thử ⇒ UNKNOWN_WITH_REASON ERR_NO_EVIDENCE (không bao giờ deliver);
#   * nguồn mâu thuẫn ⇒ ERR_EVIDENCE_CONFLICT; bản ghi bị sửa ⇒ KHÔNG resolve;
#   * resolve_stuck không còn nhãn audit giả, không đóng sớm phiếu đang PENDING;
#   * câu hỏi gốc không bị persist; bản nháp AI luôn "withheld".
#
# Zero-mock cho kernel/kho/receipt: TaskKernel thật, CanonicalFTSWarehouse thật,
# AutoWebIngestor thật (raw_content thay cho fetch mạng), receipt HMAC thật.
from __future__ import annotations

import json
import sqlite3
import time

import pytest

from scp.ask_kernel_adapter import AskKernelAdapter

QUESTION = "Thời điểm khánh thành của công trình Xyzabc-17 là năm nào?"


def _wait_until(predicate, timeout: float = 8.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.03)
    return predicate()


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch, tmp_path):
    monkeypatch.setenv("SCP_MULTI_LLM_CROSSCHECK", "0")
    monkeypatch.setenv("SCP_DATA_DIR", str(tmp_path / "data"))


@pytest.fixture()
def judge_fails(monkeypatch):
    import scp.runtime.judge_llm as judge_mod

    async def _fake_judge(question: str, ai_answer: str, context: str = "") -> bool:
        return False

    monkeypatch.setattr(judge_mod, "_llm_judge_async", _fake_judge)


@pytest.fixture()
def warehouse(tmp_path):
    from scp.knowledge.canonical_fts_warehouse import CanonicalFTSWarehouse

    wh = CanonicalFTSWarehouse(db_path=tmp_path / "fts.sqlite", documents_dir=tmp_path / "nodocs", auto_ingest_dir=False)
    yield wh
    wh.close()


@pytest.fixture()
def resolver_factory(tmp_path, warehouse):
    from scp.core import evidence_resolution as er

    created = []

    def _make(schedule=(0.0,), max_pending=None):
        from scp.data_sources.auto_web_ingestor import AutoWebIngestor

        verify = er.receipt_bound_gate(lambda: warehouse.db_path)
        gates = [verify, er.staged_reconcile_gate(
            lambda: AutoWebIngestor(warehouse=warehouse, quarantine_db_path=tmp_path / "q.sqlite"), verify_gate=verify)]
        queue = er.EvidenceResolutionQueue(tmp_path / "resolver", gates=gates, retry_schedule=schedule, max_pending=max_pending)
        created.append(queue)
        return queue

    previous = er._QUEUE
    yield _make
    er._QUEUE = previous
    for queue in created:
        queue.shutdown()


def _stage(tmp_path, warehouse, key, sources):
    from scp.data_sources.auto_web_ingestor import AutoWebIngestor

    ingestor = AutoWebIngestor(warehouse=warehouse, quarantine_db_path=tmp_path / "q.sqlite")
    for url, text in sources:
        res = ingestor.stage_from_url(topic_key=key, domain=url.split("/")[2], title="probe", url=url, raw_content=text)
        assert res["ok"], res


class _Req:
    question = QUESTION
    contexts = ["unrelated context"]
    retrieved_context = ""
    session_id = "esc-auto-s2"


class _State:
    pass


class _Request:
    def __init__(self):
        self.state = _State()


async def _escalate(tmp_path):
    adapter = AskKernelAdapter(db_path=str(tmp_path / "ask.sqlite3"), trace_path=str(tmp_path / "trace.jsonl"))
    req = _Req()
    request = _Request()
    task = adapter.begin(req.question, list(req.contexts), req.retrieved_context, req.session_id)
    result = await adapter.finalize(
        task,
        {"final_answer": "Năm 1901", "verdict": "PASS", "governance_decision": "UPHOLD",
         "v98_classification": {"provenance": "input_context_only"}},
        req,
        request,
    )
    return adapter, result, request


def _task_row(tmp_path, task_id):
    conn = sqlite3.connect(str(tmp_path / "ask.sqlite3"))
    conn.row_factory = sqlite3.Row
    try:
        return dict(conn.execute("SELECT state, error FROM tasks WHERE task_id = ?", (task_id,)).fetchone())
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_escalate_resolves_autonomously_with_cited_source(judge_fails, resolver_factory, tmp_path, warehouse):
    from scp.core import evidence_resolution as er

    queue = resolver_factory(schedule=(0.0, 0.2))
    er._QUEUE = queue
    key = er.claim_key(QUESTION)
    # Lane discovery (tương lai) staged 2 nguồn khác tổ chức cho đúng claim này.
    _stage(tmp_path, warehouse, key, [
        ("https://api.worldbank.org/x", "Công trình Xyzabc-17 khánh thành năm 1932 theo hồ sơ."),
        ("https://data.un.org/y", "Hồ sơ lưu trữ: Xyzabc-17 được khánh thành vào năm 1932."),
    ])
    adapter, result, request = await _escalate(tmp_path)
    try:
        assert result["safe_response"]["governance_decision"] == "ESCALATE"
        ticket = request.state.scp_evidence_ticket
        assert ticket["ticket_id"].startswith("ev-") and ticket["status"] == er.PENDING
        assert _wait_until(lambda: queue.get(ticket["ticket_id"])["status"] != er.PENDING)
        view = queue.get(ticket["ticket_id"])
        assert view["status"] == er.RESOLVED_CITED and view["code"] == er.DELIVER_CITED_SOURCE
        citation = view["citations"][0]
        assert citation["receipt_verified"] is True
        # Sự thật về gap hiện có: receipt của ingestor chưa ràng buộc content hash.
        assert citation["content_bound_by_receipt"] is False
        assert set(citation["corroborating_hosts"]) == {"api.worldbank.org", "data.un.org"}
        assert "1932" in citation["excerpt"]
        assert view["draft_answer"] == "withheld" and "1901" not in json.dumps(view)
        task_id = result["task"]["task_id"]
        assert _wait_until(lambda: _task_row(tmp_path, task_id)["state"] == "FAILED")
        assert _task_row(tmp_path, task_id)["error"] == "evidence_resolution:RESOLVED_CITED:DELIVER_CITED_SOURCE"
    finally:
        adapter.kernel.close()


@pytest.mark.asyncio
async def test_escalate_without_evidence_ends_unknown_with_reason_never_delivered(judge_fails, resolver_factory, tmp_path):
    from scp.core import evidence_resolution as er

    queue = resolver_factory(schedule=(0.0, 0.1, 0.2))
    er._QUEUE = queue
    adapter, result, request = await _escalate(tmp_path)
    try:
        ticket_id = request.state.scp_evidence_ticket["ticket_id"]
        assert _wait_until(lambda: queue.get(ticket_id)["status"] != er.PENDING)
        view = queue.get(ticket_id)
        assert view["status"] == er.UNKNOWN_WITH_REASON and view["code"] == er.ERR_NO_EVIDENCE
        assert view["attempts"] == 3, "all scheduled attempts run before the final UNKNOWN"
        assert view["citations"] == []
        task_id = result["task"]["task_id"]
        assert _wait_until(lambda: _task_row(tmp_path, task_id)["state"] == "FAILED")
        assert _task_row(tmp_path, task_id)["error"] == "evidence_resolution:UNKNOWN_WITH_REASON:ERR_NO_EVIDENCE"
        # Câu hỏi gốc không nằm trong state/journal của resolver.
        for name in ("evidence_resolution_state.json", "evidence_resolution_journal.jsonl"):
            assert QUESTION not in (tmp_path / "resolver" / name).read_text(encoding="utf-8")
    finally:
        adapter.kernel.close()


def test_conflicting_sources_end_with_conflict_code(resolver_factory, tmp_path, warehouse):
    from scp.core import evidence_resolution as er

    queue = resolver_factory(schedule=(0.0, 30.0))
    key = er.claim_key("conflict claim")
    _stage(tmp_path, warehouse, key, [
        ("https://api.worldbank.org/a", "Giá trị đo được là 1932."),
        ("https://data.un.org/b", "Giá trị đo được là 1874."),
    ])
    view = queue.enqueue("conflict claim")
    assert _wait_until(lambda: queue.get(view["ticket_id"])["status"] != er.PENDING)
    final = queue.get(view["ticket_id"])
    assert final["status"] == er.UNKNOWN_WITH_REASON and final["code"] == er.ERR_EVIDENCE_CONFLICT
    assert final["attempts"] == 1, "a conflict is final; retrying cannot change it"


def test_tampered_bound_record_is_never_resolved(resolver_factory, tmp_path, warehouse):
    from scp.core import evidence_resolution as er
    from scp.data_sources.auto_web_ingestor import AutoWebIngestor

    key = er.claim_key("tamper claim")
    _stage(tmp_path, warehouse, key, [
        ("https://api.worldbank.org/a", "Chỉ số là 42 đơn vị."),
        ("https://data.un.org/b", "Theo số liệu, chỉ số là 42."),
    ])
    promoted = AutoWebIngestor(warehouse=warehouse, quarantine_db_path=tmp_path / "q.sqlite").reconcile_and_promote(key)
    assert promoted["promoted"] is True
    conn = sqlite3.connect(str(warehouse.db_path))
    conn.execute("UPDATE kb_documents SET content = 'Chỉ số là 99 đơn vị.' WHERE doc_id = ?", (f"web_{key}",))
    conn.commit()
    conn.close()
    outcome = er.receipt_bound_gate(lambda: warehouse.db_path)(key)
    assert outcome.status == er.UNKNOWN_WITH_REASON and outcome.code == er.ERR_EVIDENCE_UNAVAILABLE
    assert outcome.final is True


def test_forged_receipt_is_never_resolved(tmp_path, warehouse):
    from scp.core import evidence_resolution as er
    from scp.data_sources.auto_web_ingestor import AutoWebIngestor

    key = er.claim_key("forged receipt claim")
    _stage(tmp_path, warehouse, key, [
        ("https://api.worldbank.org/a", "Chỉ số là 7 đơn vị."),
        ("https://data.un.org/b", "Theo số liệu, chỉ số là 7."),
    ])
    assert AutoWebIngestor(warehouse=warehouse, quarantine_db_path=tmp_path / "q.sqlite").reconcile_and_promote(key)["promoted"]
    assert er.receipt_bound_gate(lambda: warehouse.db_path)(key).status == er.RESOLVED_CITED
    conn = sqlite3.connect(str(warehouse.db_path))
    raw = conn.execute("SELECT verifier_receipt_json FROM kb_documents WHERE doc_id = ?", (f"web_{key}",)).fetchone()[0]
    receipt = json.loads(raw)
    receipt["signature"] = "0" * 64
    conn.execute("UPDATE kb_documents SET verifier_receipt_json = ? WHERE doc_id = ?", (json.dumps(receipt), f"web_{key}"))
    conn.commit()
    conn.close()
    outcome = er.receipt_bound_gate(lambda: warehouse.db_path)(key)
    assert outcome.status == er.UNKNOWN_WITH_REASON and outcome.code == er.ERR_EVIDENCE_UNAVAILABLE
    # Receipt cho claim KHÁC không được dùng cho claim này (task_id bind claim key).
    other = er.claim_key("another claim")
    conn = sqlite3.connect(str(warehouse.db_path))
    conn.execute("UPDATE kb_documents SET doc_id = ?, verifier_receipt_json = ? WHERE doc_id = ?",
                 (f"web_{other}", raw, f"web_{key}"))
    conn.commit()
    conn.close()
    assert er.receipt_bound_gate(lambda: warehouse.db_path)(other).status == er.UNKNOWN_WITH_REASON


def test_queue_dedups_by_claim_and_is_bounded_truthfully(resolver_factory):
    from scp.core import evidence_resolution as er

    queue = resolver_factory(schedule=(30.0,), max_pending=3)
    first = queue.enqueue("  Câu   hỏi  A ")
    again = queue.enqueue("câu hỏi a")
    assert again["ticket_id"] == first["ticket_id"]
    queue.enqueue("question B")
    queue.enqueue("question C")
    overflow = queue.enqueue("question D")
    assert overflow["ticket_id"] is None
    assert overflow["status"] == er.UNKNOWN_WITH_REASON and overflow["code"] == er.ERR_EVIDENCE_UNAVAILABLE
    stats = queue.stats()
    assert stats["pending"] == 3 and stats["rejected_capacity"] == 1 and stats["deduped"] == 1
    assert stats["scheduler"]["worker_threads"] <= 1


def _stuck_kernel(tmp_path, reasons):
    from scp.core.autonomous_cycle import ensure_capability_secret
    from scp.task_kernel import TaskKernel, stable_hash

    ensure_capability_secret()
    db = tmp_path / "ask_task_kernel.sqlite3"
    kernel = TaskKernel(db)
    for i, reason in enumerate(reasons):
        task_id = f"stuck-{i}"
        kernel.create_task(task_id, "test-owner", "Test task", "R0", input_hash=stable_hash({"i": i}))
        for state in ("PLANNING", "READY", "QUEUED"):
            kernel.transition(task_id, state, actor="test")
        lease = kernel.claim(task_id, "test-worker", ttl_seconds=60)
        kernel.start(task_id, lease.lease_id)
        kernel.transition(task_id, "VERIFYING", actor="test")
        kernel.transition(task_id, "HUMAN_REVIEW", actor="test", reason=reason)
    kernel.close()
    return db


def _errors(db):
    conn = sqlite3.connect(str(db))
    try:
        return {row[0]: (row[1], row[2]) for row in conn.execute("SELECT task_id, state, error FROM tasks")}
    finally:
        conn.close()


def test_resolve_stuck_is_truthful_and_never_claims_an_audit(tmp_path):
    from scp.knowledge.deduplication_maintenance import resolve_stuck_human_review_tasks

    db = _stuck_kernel(tmp_path, ["ask_security_escalate", "ask_evidence_insufficient_or_contradicted", "boot_recovery_in_flight"])
    resolve_stuck_human_review_tasks(db)
    rows = _errors(db)
    assert all(state == "FAILED" for state, _ in rows.values())
    assert not any("audited" in (err or "") for _, err in rows.values()), rows
    assert rows["stuck-0"][1] == "security_blocked_final"
    assert rows["stuck-1"][1] == "evidence_resolution:UNKNOWN_WITH_REASON:ERR_EVIDENCE_UNAVAILABLE"
    assert rows["stuck-2"][1] == "lifecycle_unresolved_fail_closed:boot_recovery_in_flight"


def test_resolve_stuck_leaves_pending_ticket_to_resolver_and_uses_terminal_verdict(tmp_path):
    from scp.core import evidence_resolution as er
    from scp.knowledge.deduplication_maintenance import resolve_stuck_human_review_tasks

    db = _stuck_kernel(tmp_path, ["ask_evidence_insufficient_or_contradicted"] * 2)
    terminal_key = er.claim_key("terminal claim")

    def gate(key):  # final only for the terminal claim; the other stays PENDING (next try in 1h)
        return er.EvidenceOutcome(er.UNKNOWN_WITH_REASON, er.ERR_EVIDENCE_UNAVAILABLE, "probe", (), "probe_gate",
                                  final=(key == terminal_key))

    queue = er.EvidenceResolutionQueue(tmp_path / "resolver", gates=[gate], retry_schedule=(0.0, 3600.0),
                                       on_terminal=lambda *a: "noop")
    try:
        queue.enqueue("pending claim", task_id="stuck-0", ask_db_path=str(db))
        queue.enqueue("terminal claim", task_id="stuck-1", ask_db_path=str(db))
        assert _wait_until(lambda: queue.find_by_task("stuck-1")["status"] != er.PENDING)
        assert _wait_until(lambda: queue.find_by_task("stuck-0")["attempts"] >= 1)
        result = resolve_stuck_human_review_tasks(db, evidence_queue=queue)
    finally:
        queue.shutdown()
    rows = _errors(db)
    assert result["pending_evidence_tickets"] == 1
    assert rows["stuck-0"][0] == "HUMAN_REVIEW", "a PENDING ticket must not be closed early"
    assert rows["stuck-1"] == ("FAILED", "evidence_resolution:UNKNOWN_WITH_REASON:ERR_EVIDENCE_UNAVAILABLE")


def test_withheld_response_carries_ticket_headers_and_route_serves_view(resolver_factory, monkeypatch):
    from fastapi.testclient import TestClient

    from scp.api_server import app
    from scp.api_server_parts.pipeline.delivery_rules import apply_delivery_headers
    from scp.core import evidence_resolution as er
    from scp.security.jwt_guard import create_access_token

    queue = resolver_factory(schedule=(3600.0,))
    er._QUEUE = queue
    view = queue.enqueue("header claim")

    class _Resp:
        headers: dict = {}

    resp = _Resp()
    resp.headers = {}
    state = _State()
    state.scp_evidence_ticket = view
    apply_delivery_headers(resp, {"verdict": "FAIL", "governance_decision": "ESCALATE", "final_answer": ""}, state)
    assert resp.headers["x-scp-delivery"] == "withheld"
    assert resp.headers["x-scp-evidence-ticket"] == view["ticket_id"]
    assert resp.headers["x-scp-evidence-status"] == er.PENDING

    client = TestClient(app)
    url = f"/ask/evidence/{view['ticket_id']}"
    assert client.get(url).status_code in (401, 403)
    token = create_access_token(data={"sub": "esc-auto-test"})
    got = client.get(url, headers={"Authorization": f"Bearer {token}"})
    assert got.status_code == 200, got.text
    assert got.json()["status"] == er.PENDING and got.json()["draft_answer"] == "withheld"
    missing = client.get("/ask/evidence/ev-doesnotexist123", headers={"Authorization": f"Bearer {token}"})
    assert missing.status_code == 404
