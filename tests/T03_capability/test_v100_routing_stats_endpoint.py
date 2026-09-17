"""[A2] GET /v100/routing/stats — route quan sát KPI question-router, auth-gated.

Bằng chứng cấp B (integration qua TestClient trên router admin_v100 thật):
  1. Route đăng ký đúng một lần trong router và dependency bậc-route LÀ
     verify_admin canonical (scp.security.auth) — không có nhánh bypass.
  2. Từ chối auth -> 401 (override deny; không gọi verify_admin thật để tránh
     side-effect IP rate-limit, cùng pattern test_flow_11 [ADMIN-19]).
  3. Body khi auth đạt == route_stats_snapshot() — cùng snapshot mà
     /health/detailed nhúng dưới key question_routing, gồm các key F-2
     ask_retrieval_* mà comment question_router.py:36/:365/:405 trỏ tới.
  4. Fail-closed: lỗi snapshot -> 503, không biến failure thành 200 rỗng.

Profile-aware: router versioned_admin chỉ được mount khi SCP_API_PROFILE=full
(scp/api_server.py, khối include_router "versioned_admin"); container production
core vẫn giữ seam /health/detailed. Test này gắn router trực tiếp vào app nhỏ
nên không phụ thuộc profile gating của api_server.
"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import scp.api.routes.admin_v100 as admin_v100
import scp.runtime.question_router as question_router
from scp.security.auth import verify_admin as canonical_verify_admin

ROUTE_PATH = "/v100/routing/stats"


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(admin_v100.router)
    return app


def _route(app: FastAPI):
    routes = [r for r in app.routes if getattr(r, "path", "") == ROUTE_PATH]
    assert len(routes) == 1, f"{ROUTE_PATH} phải được đăng ký đúng một lần trong admin_v100"
    return routes[0]


def test_route_registered_with_canonical_verify_admin_dependency():
    route = _route(_app())
    deps = [d.dependency for d in route.dependencies]
    assert canonical_verify_admin in deps, (
        "auth bậc-route phải là verify_admin canonical (scp.security.auth), "
        "không phải placeholder"
    )


def test_auth_denied_returns_401():
    app = _app()

    def _deny():
        raise HTTPException(status_code=401, detail="denied")

    app.dependency_overrides[canonical_verify_admin] = _deny
    client = TestClient(app)
    response = client.get(ROUTE_PATH)
    assert response.status_code == 401


def test_snapshot_body_matches_route_stats_snapshot():
    app = _app()
    app.dependency_overrides[canonical_verify_admin] = lambda: True
    client = TestClient(app)
    response = client.get(ROUTE_PATH)
    assert response.status_code == 200
    body = response.json()
    # traced_request thêm đúng 4 field ledger chuẩn (RequestRunLedger.attach) —
    # giống mọi route v100 traced khác; phần còn lại phải ĐÚNG bằng snapshot.
    ledger_keys = {"run_id", "trace_id", "run_status", "ledger_status"}
    assert ledger_keys <= set(body), f"thiếu field ledger chuẩn: {ledger_keys - set(body)}"
    snapshot_part = {k: v for k, v in body.items() if k not in ledger_keys}
    expected = question_router.route_stats_snapshot()
    assert snapshot_part == expected
    for key in (
        "llm_bypassed_count",
        "llm_calls_count",
        "classifier_llm_calls",
        "fallback_reasons",
        "ask_retrieval_attempts",
        "ask_retrieval_hits",
        "ask_retrieval_empty",
        "ask_retrieval_errors",
        "correction_attempts",
        "correction_success_rate",
        "route_counts",
        "bypass_ratio",
    ):
        assert key in body


def test_snapshot_failure_is_fail_closed_503(monkeypatch):
    app = _app()
    app.dependency_overrides[canonical_verify_admin] = lambda: True

    def _boom():
        raise RuntimeError("simulated stats backend failure")

    # Handler import route_stats_snapshot từ module lúc gọi — monkeypatch trên
    # scp.runtime.question_router là seam đúng, không đổi product code.
    monkeypatch.setattr(question_router, "route_stats_snapshot", _boom)
    client = TestClient(app)
    response = client.get(ROUTE_PATH)
    assert response.status_code == 503
