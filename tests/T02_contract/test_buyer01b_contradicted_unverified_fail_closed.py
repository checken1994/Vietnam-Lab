"""[BUYER-01b · 2026-10-30] Verdict-map contract, nhánh (b):
có đáp án + 0 xác minh độc lập + claim CONTRADICTED ⇒ KHÔNG-PASS, withheld,
governance ESCALATE, run_status REJECTED (ledger vẫn OK).

Gốc bệnh (trace thật, bounded smoke ``ask_rag`` trên a2282b10): verifier atomic
đánh dấu claim của draft ``CONTRADICTED`` (domain_fact, đối chiếu kho canonical)
rồi THAY nguyên clause bằng bản ghi canonical và đếm sự thay thế đó là "verified
after deterministic repair" ⇒ ``all_verified_locally=True`` ⇒ VerificationStage
short-circuit ``PASS/UPHOLD/0.98`` ("Verified deterministically across 0/1
atomic claims"), judge KHÔNG chạy, kernel ghi ``COMPLETED/VERIFIED``,
``run_status=SUCCESS``. Đúng task buyer ``ask-8b8a187a38300a8e065fd63f``.

Hợp đồng W17 (docstring module atomic_verifier) chỉ cho phép "repairs
deterministic calculation/conversion errors in-place": sửa tại chỗ bằng engine
tính toán (arithmetic/conversion) mới được coi là verified; thay thế canonical
KHÔNG phải xác minh draft. Test giữ cả hai chiều:
- (b) workload thật của smoke/buyer qua app /ask THẬT (kernel bật, egress deny,
  không provider, không web) ⇒ NOT-PASS/withheld/ESCALATE/REJECTED + kernel task
  không COMPLETED/VERIFIED + request-run ledger đồng nhất với response.
- (c) đối chứng dương: đáp án được xác minh thật vẫn PASS/delivered/SUCCESS;
  sửa số học tại chỗ (W17) vẫn được tính là verified.
Không special-case câu hỏi smoke: workload thứ hai (H2O 99.5 g/mol) đi cùng luật.
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from scp.runtime.atomic_verifier import AtomicDeterministicVerifier

REPO_ROOT = Path(__file__).resolve().parents[2]
SMOKE_TEXT = (
    "Spaced repetition is an evidence-based learning technique that is usually performed with flashcards."
)
# Payload ask_rag nguyên văn của tools/run_bounded_system_smoke.py (release gate).
SMOKE_PAYLOAD: dict[str, Any] = {
    "question": "What is spaced repetition?",
    "domain": "consumer_travel_education",
    "rag_enabled": True,
    "contexts": [SMOKE_TEXT],
    "retrieved_context": SMOKE_TEXT,
    "ai_answer": SMOKE_TEXT,
    "ground_truth": SMOKE_TEXT,
    "session_id": "release-gate-bounded-smoke",
}
H2O_QUESTION = "Phân tử khối của H2O là bao nhiêu?"
H2O_WRONG = "Phân tử khối của H2O là 99.5 g/mol."
H2O_RIGHT = "Phân tử khối của H2O là 18.015 g/mol."
MIXED_DRAFT = "2 + 2 = 4. " + H2O_WRONG
WITHHELD_PREFIX = "[SCP: Answer withheld"


def test_regression_tracks_the_release_smoke_workload() -> None:
    """Payload ở đây phải là đúng workload smoke đang gác release (không trôi lệch)."""
    smoke_src = (REPO_ROOT / "tools" / "run_bounded_system_smoke.py").read_text(encoding="utf-8")
    assert f'"question": "{SMOKE_PAYLOAD["question"]}"' in smoke_src
    assert f'"domain": "{SMOKE_PAYLOAD["domain"]}"' in smoke_src
    assert SMOKE_TEXT in smoke_src


# ---------------------------------------------------------------------------
# 1. Atomic verifier: thay thế canonical KHÔNG phải xác minh; sửa tính toán tại chỗ thì có
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("question", "draft", "evidence"),
    [
        (SMOKE_PAYLOAD["question"], SMOKE_TEXT, [SMOKE_TEXT, SMOKE_TEXT]),
        (H2O_QUESTION, H2O_WRONG, []),
    ],
    ids=["smoke-spaced-repetition", "h2o-numeric"],
)
def test_canonical_substitution_is_not_local_verification(
    question: str, draft: str, evidence: list[str]
) -> None:
    res = AtomicDeterministicVerifier().verify_and_prune(
        question=question, draft_answer=draft, clean_evidence=evidence
    )
    claims = res["claims_payload"]
    # Tiền điều kiện (đỏ ồn ào nếu kho canonical đổi): draft bị canonical bác bỏ và có bản thay thế.
    assert [c["category"] for c in claims] == ["domain_fact"], claims
    assert claims[0]["status"] == "CONTRADICTED" and claims[0]["verified"] is False, claims
    assert claims[0]["repaired_clause"], claims
    assert res["verified_count"] == 0 and res["refuted_count"] == 1
    # Bất biến: 0 claim verified + 1 claim bị bác bỏ ⇒ KHÔNG được coi là đã xác minh cục bộ.
    assert res["all_verified_locally"] is False, res
    assert res["partially_verified_locally"] is False, res


def test_mixed_draft_substitution_cannot_be_pruned_into_partial_verification() -> None:
    """Một claim verified + một claim bị canonical bác bỏ ⇒ không all/partial (chặn W18 prune→PASS)."""
    res = AtomicDeterministicVerifier().verify_and_prune(question=H2O_QUESTION, draft_answer=MIXED_DRAFT)
    assert [(c["category"], c["status"]) for c in res["claims_payload"]] == [
        ("arithmetic", "VERIFIED"),
        ("domain_fact", "CONTRADICTED"),
    ], res["claims_payload"]
    assert res["all_verified_locally"] is False, res
    assert res["partially_verified_locally"] is False, res


@pytest.mark.parametrize(
    ("question", "draft", "repaired"),
    [("Tính 17 * 23", "17 * 23 = 400", "17 * 23 = 391."), ("Tính 2 + 2", "2 + 2 = 5.", "2 + 2 = 4.")],
)
def test_computed_inplace_repair_still_counts_as_verified(question: str, draft: str, repaired: str) -> None:
    res = AtomicDeterministicVerifier().verify_and_prune(question=question, draft_answer=draft)
    assert res["claims_payload"][0]["category"] == "arithmetic"
    assert res["all_verified_locally"] is True, res
    assert res["repaired_answer"] == repaired


# ---------------------------------------------------------------------------
# 2. Pipeline 5 stage trực tiếp (_ask_impl, judge THẬT, egress deny): quyết định
#    phải đúng NGAY tại pipeline, không trông chờ kernel ghi đè phía sau.
# ---------------------------------------------------------------------------

B_WORKLOADS = [
    pytest.param(dict(SMOKE_PAYLOAD), "Lặp lại ngắt quãng", id="smoke-spaced-repetition"),
    pytest.param({"question": H2O_QUESTION, "ai_answer": H2O_WRONG}, "18.0153", id="h2o-numeric"),
    pytest.param({"question": H2O_QUESTION, "ai_answer": MIXED_DRAFT}, "18.0153", id="mixed-draft"),
]


def _direct_request() -> Any:
    from starlette.requests import Request as StarletteRequest

    scope = {
        "type": "http", "method": "POST", "path": "/ask",
        "headers": [(b"host", b"127.0.0.1:8000")], "query_string": b"",
        "client": ("127.0.0.1", 54321), "server": ("testserver", 80), "scheme": "http",
    }
    req = StarletteRequest(scope)
    req.state.scp_run = None
    req.state.trace_id = "test-buyer01b"
    return req


@pytest.fixture
def deny_egress(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCP_EGRESS_MODE", "deny")
    monkeypatch.setenv("SCP_WEB_FALLBACK", "0")


@pytest.mark.asyncio
@pytest.mark.parametrize(("payload", "forbidden"), B_WORKLOADS)
async def test_direct_pipeline_contradicted_unverified_is_withheld_escalate(
    deny_egress, payload: dict[str, Any], forbidden: str
) -> None:
    from scp.api_server_parts._ask_impl import _ask_impl
    from scp.api_server_parts.helpers import AskRequest

    response = await _ask_impl(AskRequest(**payload), _direct_request())
    assert response.verdict != "PASS", response
    assert response.governance_decision == "ESCALATE", response
    assert str(response.final_answer).startswith(WITHHELD_PREFIX), response.final_answer
    dumped = response.model_dump_json() if hasattr(response, "model_dump_json") else str(response)
    assert forbidden not in dumped


@pytest.mark.asyncio
async def test_direct_pipeline_positive_control_verified_answer_passes(deny_egress) -> None:
    from scp.api_server_parts._ask_impl import _ask_impl
    from scp.api_server_parts.helpers import AskRequest

    req = AskRequest(question=H2O_QUESTION, ai_answer=H2O_RIGHT, contexts=[H2O_RIGHT], rag_enabled=True)
    response = await _ask_impl(req, _direct_request())
    assert response.verdict == "PASS", response
    assert response.governance_decision == "UPHOLD", response
    assert "18.015" in str(response.final_answer)


# ---------------------------------------------------------------------------
# 3. /ask THẬT (app + kernel + ledger), egress deny, không provider — như smoke
# ---------------------------------------------------------------------------

@pytest.fixture
def ask_api(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[tuple[TestClient, Path, Any]]:
    from scp import api_server
    from scp.security.jwt_guard import create_access_token

    monkeypatch.setenv("SCP_EGRESS_MODE", "deny")  # chặn mọi LLM provider trước DNS (0 xác minh độc lập)
    monkeypatch.setenv("SCP_WEB_FALLBACK", "0")
    monkeypatch.setenv("SCP_ASK_KERNEL_ENABLED", "1")
    monkeypatch.setenv("SCP_KERNEL_DB_PATH", str(tmp_path / "kernel.sqlite3"))
    monkeypatch.setenv("SCP_KERNEL_TRACE_PATH", str(tmp_path / "kernel_trace.jsonl"))
    monkeypatch.setattr(api_server._REQUEST_RUN_LEDGER, "path", tmp_path / "request_runs.jsonl")
    monkeypatch.setattr(api_server.app.state, "judge_ready", True, raising=False)
    saved_adapters = dict(api_server._ASK_KERNEL_ADAPTERS)
    api_server._ASK_KERNEL_ADAPTERS.clear()
    client = TestClient(api_server.app)
    client.headers["Authorization"] = f"Bearer {create_access_token({'sub': 'buyer01b-regression'})}"
    try:
        yield client, tmp_path, api_server
    finally:
        for adapter in list(api_server._ASK_KERNEL_ADAPTERS.values()):
            adapter.kernel.close()
        api_server._ASK_KERNEL_ADAPTERS.clear()
        api_server._ASK_KERNEL_ADAPTERS.update(saved_adapters)


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _kernel_outcomes(tmp_path: Path) -> list[dict[str, Any]]:
    rows = [r.get("fields", {}) for r in _jsonl(tmp_path / "kernel_trace.jsonl")]
    return [f for f in rows if f.get("outcome") and f.get("outcome") != "STARTED"]


def _assert_fail_closed_consistent(resp: Any, tmp_path: Path, api_server: Any, forbidden: str) -> None:
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Response: KHÔNG-PASS + withheld + ESCALATE + REJECTED, ledger OK.
    assert body["verdict"] != "PASS", body
    assert body["final_answer"].startswith(WITHHELD_PREFIX), body["final_answer"]
    assert "withheld" in body["final_answer"].lower()
    assert body["governance_decision"] == "ESCALATE", body
    assert body["run_status"] == "REJECTED", body
    assert body["ledger_status"] == "OK", body
    assert (body.get("confidence") or 0.0) == 0.0
    assert resp.headers.get("x-scp-delivery") == "withheld"
    assert forbidden not in json.dumps(body, ensure_ascii=False), "bản thay thế canonical chưa xác minh bị giao"
    # Kernel: task KHÔNG được COMPLETED/VERIFIED khi chưa có xác minh.
    outcomes = _kernel_outcomes(tmp_path)
    assert outcomes, "kernel trace must record a terminal outcome"
    assert all(o.get("verdict") != "VERIFIED" and o.get("outcome") != "COMPLETED" for o in outcomes), outcomes
    adapter = next(iter(api_server._ASK_KERNEL_ADAPTERS.values()))
    task = adapter.kernel.get_task(outcomes[-1]["task_id"])
    assert task["state"] != "COMPLETED", task
    # Request-run ledger của CHÍNH run này đồng nhất với response.
    run_rows = [r for r in _jsonl(tmp_path / "request_runs.jsonl") if r.get("run_id") == body["run_id"]]
    verifier = [r for r in run_rows if r.get("stage") == "verifier_completed"]
    assert verifier and verifier[-1].get("verdict") != "PASS", run_rows
    assert verifier[-1].get("governance_decision") == "ESCALATE", run_rows
    finished = [r for r in run_rows if r.get("event") == "request_finished"]
    assert finished and finished[-1]["status"] == "REJECTED" and finished[-1].get("verdict") != "PASS", run_rows


def test_api_smoke_workload_contradicted_unverified_fails_closed(ask_api) -> None:
    client, tmp_path, api_server = ask_api
    resp = client.post("/ask", json=SMOKE_PAYLOAD)
    _assert_fail_closed_consistent(resp, tmp_path, api_server, forbidden="Lặp lại ngắt quãng")


def test_api_wrong_numeric_claim_not_passed_via_canonical_substitution(ask_api) -> None:
    client, tmp_path, api_server = ask_api
    resp = client.post("/ask", json={"question": H2O_QUESTION, "ai_answer": H2O_WRONG})
    _assert_fail_closed_consistent(resp, tmp_path, api_server, forbidden="18.0153")


def test_api_mixed_draft_substitution_not_pruned_into_pass(ask_api) -> None:
    client, tmp_path, api_server = ask_api
    resp = client.post("/ask", json={"question": H2O_QUESTION, "ai_answer": MIXED_DRAFT})
    _assert_fail_closed_consistent(resp, tmp_path, api_server, forbidden="18.0153")


def _assert_verified_delivery(resp: Any, tmp_path: Path, expect_in_answer: str) -> None:
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["verdict"] == "PASS", body
    assert body["governance_decision"] == "UPHOLD", body
    assert body["run_status"] == "SUCCESS", body
    assert body["ledger_status"] == "OK", body
    assert not body["final_answer"].startswith("[SCP:"), body["final_answer"]
    assert expect_in_answer in body["final_answer"], body["final_answer"]
    outcomes = _kernel_outcomes(tmp_path)
    assert outcomes and outcomes[-1].get("verdict") == "VERIFIED", outcomes


def test_api_positive_control_verified_rag_answer_still_delivered(ask_api) -> None:
    client, tmp_path, _ = ask_api
    resp = client.post(
        "/ask",
        json={"question": H2O_QUESTION, "ai_answer": H2O_RIGHT, "contexts": [H2O_RIGHT], "rag_enabled": True},
    )
    _assert_verified_delivery(resp, tmp_path, "18.015")


def test_api_positive_control_offline_conversion_still_delivered(ask_api) -> None:
    client, tmp_path, _ = ask_api
    resp = client.post("/ask", json={"question": "100 celsius to fahrenheit"})
    _assert_verified_delivery(resp, tmp_path, "212")
