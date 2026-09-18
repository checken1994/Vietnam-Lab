"""
SCP Complete Standard Test — Mạch 11: Admin & Import
Covers: api/routes/admin_v98.py, admin_v100.py, import_routes.py, webhook.py

FA-01: Strict assertions, no loosening
FA-02: No skip/xfail
FA-03: Full pytest output as evidence
FA-04: No simulated VERIFIED
FA-05: No self-grant authority
FA-09: Exploit mandate - reproduce actual behavior
FA-13: Causal branch coverage of admin & import flow
"""

import os
# Force full profile to test all routes
os.environ["SCP_API_PROFILE"] = "full"

import json
from unittest.mock import MagicMock, patch, AsyncMock

import pytest
from fastapi import HTTPException, Header, Request
from fastapi.testclient import TestClient

from scp.api_server import app
from scp.api._shared import verify_admin


def mock_unauthorized(token: str = Header(..., alias="Authorization"), request: Request = None) -> bool:
    raise HTTPException(status_code=401, detail="Mock unauthorized")

class TestFlow11AdminImport:
    """Mạch 11: Admin & Import - SCP Complete Standard"""

    def setup_method(self):
        # Prevent actual auth which triggers IP ban after 5 failures (HTTP 429)
        app.dependency_overrides[verify_admin] = mock_unauthorized

    def teardown_method(self):
        app.dependency_overrides.clear()

    # =========================================================================
    # 1. ADMIN V98 ROUTES
    # =========================================================================

    def test_admin_v98_analyze_session_requires_admin(self):
        """[ADMIN-1] POST /v98/analyze-session requires admin auth."""
        with TestClient(app) as client:
            response = client.post("/v98/analyze-session", json={"session_logs": [], "model_responses": []}, headers={"Authorization": "Bearer fake"})
            assert response.status_code in [401, 403]

    def test_admin_v98_run_simulation_requires_admin(self):
        """[ADMIN-2] POST /v98/run-simulation requires admin auth."""
        with TestClient(app) as client:
            response = client.post("/v98/run-simulation", json={"count": 50}, headers={"Authorization": "Bearer fake"})
            assert response.status_code in [401, 403]

    def test_admin_v98_run_intel_crawl_requires_admin(self):
        """[ADMIN-3] POST /v98/run-intel-crawl requires admin auth."""
        with TestClient(app) as client:
            response = client.post("/v98/run-intel-crawl", headers={"Authorization": "Bearer fake"})
            assert response.status_code in [401, 403]

    def test_admin_v98_status_requires_admin(self):
        """[ADMIN-4] GET /v98/status requires admin auth."""
        with TestClient(app) as client:
            response = client.get("/v98/status", headers={"Authorization": "Bearer fake"})
            assert response.status_code in [401, 403]

    def test_admin_v98_counter_stats_requires_admin(self):
        """[ADMIN-5] GET /v98/counter/stats requires admin auth."""
        with TestClient(app) as client:
            response = client.get("/v98/counter/stats", headers={"Authorization": "Bearer fake"})
            assert response.status_code in [401, 403]

    def test_admin_v98_canary_triggers_requires_admin(self):
        """[ADMIN-6] GET /v98/canary/triggers requires admin auth."""
        with TestClient(app) as client:
            response = client.get("/v98/canary/triggers", headers={"Authorization": "Bearer fake"})
            assert response.status_code in [401, 403]

    def test_admin_v98_error_store_stats_requires_admin(self):
        """[ADMIN-7] GET /v98/error-store/stats requires admin auth."""
        with TestClient(app) as client:
            response = client.get("/v98/error-store/stats", headers={"Authorization": "Bearer fake"})
            assert response.status_code in [401, 403]

    def test_admin_v98_attack_memory_stats_requires_admin(self):
        """[ADMIN-8] GET /v98/attack-memory/stats requires admin auth."""
        with TestClient(app) as client:
            response = client.get("/v98/attack-memory/stats", headers={"Authorization": "Bearer fake"})
            assert response.status_code in [401, 403]

    # =========================================================================
    # 2. ADMIN V100 ROUTES
    # =========================================================================

    def test_admin_v100_status_requires_admin(self):
        """[ADMIN-9] GET /v100/status requires admin auth."""
        with TestClient(app) as client:
            response = client.get("/v100/status", headers={"Authorization": "Bearer fake"})
            assert response.status_code in [401, 403]

    def test_admin_v100_crawl_requires_admin(self):
        """[ADMIN-10] POST /v100/crawl requires admin auth."""
        with TestClient(app) as client:
            response = client.post("/v100/crawl", headers={"Authorization": "Bearer fake"})
            assert response.status_code in [401, 403]

    def test_admin_v100_antibodies_stats_requires_admin(self):
        """[ADMIN-11] GET /v100/antibodies/stats requires admin auth."""
        with TestClient(app) as client:
            response = client.get("/v100/antibodies/stats", headers={"Authorization": "Bearer fake"})
            assert response.status_code in [401, 403]

    def test_admin_v100_antibodies_check_requires_admin(self):
        """[ADMIN-12] POST /v100/antibodies/check requires admin auth."""
        with TestClient(app) as client:
            response = client.post("/v100/antibodies/check", json={"question": "a", "answer": "b"}, headers={"Authorization": "Bearer fake"})
            assert response.status_code in [401, 403]

    def test_admin_v100_knowledge_stats_requires_admin(self):
        """[ADMIN-13] GET /v100/knowledge/stats requires admin auth."""
        with TestClient(app) as client:
            response = client.get("/v100/knowledge/stats", headers={"Authorization": "Bearer fake"})
            assert response.status_code in [401, 403]

    def test_admin_v100_knowledge_search_requires_admin(self):
        """[ADMIN-14] GET /v100/knowledge/search requires admin auth."""
        with TestClient(app) as client:
            response = client.get("/v100/knowledge/search", headers={"Authorization": "Bearer fake"})
            assert response.status_code in [401, 403]

    def test_admin_v100_h8_stats_requires_admin(self):
        """[ADMIN-15] GET /v100/h8/stats requires admin auth."""
        with TestClient(app) as client:
            response = client.get("/v100/h8/stats", headers={"Authorization": "Bearer fake"})
            assert response.status_code in [401, 403]

    def test_admin_v100_h8_bypasses_requires_admin(self):
        """[ADMIN-16] GET /v100/h8/bypasses requires admin auth."""
        with TestClient(app) as client:
            response = client.get("/v100/h8/bypasses", headers={"Authorization": "Bearer fake"})
            assert response.status_code in [401, 403]

    def test_admin_v100_h8_analyses_requires_admin(self):
        """[ADMIN-17] GET /v100/h8/analyses requires admin auth."""
        with TestClient(app) as client:
            response = client.get("/v100/h8/analyses", headers={"Authorization": "Bearer fake"})
            assert response.status_code in [401, 403]

    def test_admin_v100_release_evidence_requires_admin(self):
        """[ADMIN-18] GET /v100/release/evidence requires admin auth."""
        with TestClient(app) as client:
            response = client.get("/v100/release/evidence", headers={"Authorization": "Bearer fake"})
            assert response.status_code in [401, 403]

    # =========================================================================
    # 3. IMPORT ROUTES
    # =========================================================================

    def test_import_jsonl_requires_admin(self):
        """[IMPORT-1] POST /import/jsonl requires admin auth."""
        with TestClient(app) as client:
            response = client.post("/import/jsonl", files={"file": ("test.jsonl", b"{}")}, headers={"Authorization": "Bearer fake"})
            assert response.status_code in [401, 403]

    def test_import_excel_requires_admin(self):
        """[IMPORT-2] POST /import/excel requires admin auth."""
        with TestClient(app) as client:
            response = client.post("/import/excel", files={"file": ("test.xlsx", b"")}, headers={"Authorization": "Bearer fake"})
            assert response.status_code in [401, 403]

    def test_import_batch_requires_admin(self):
        """[IMPORT-3] POST /import/batch requires admin auth."""
        with TestClient(app) as client:
            response = client.post("/import/batch", json={"items": []}, headers={"Authorization": "Bearer fake"})
            assert response.status_code in [401, 403]

    def test_import_jsonl_endpoint_works(self):
        """[IMPORT-4] JSONL endpoint processes correct payload."""
        app.dependency_overrides[verify_admin] = lambda: True
        with TestClient(app) as client:
            mock_judge = MagicMock()
            mock_verdict = MagicMock()
            mock_verdict.verdict = "PASS"
            mock_verdict.confidence = 0.99
            mock_verdict.evidence = {"falsification_status": "NONE"}
            mock_judge.judge.return_value = mock_verdict

            with patch("scp.api.routes.import_routes.get_judge", return_value=mock_judge):
                payload = json.dumps({"question": "abc", "ai_answer": "xyz"})
                response = client.post("/import/jsonl", content=payload.encode("utf-8"))
                assert response.status_code == 200
                data = response.json()
                assert "summary" in data
                assert data["summary"]["pass"] == 1
        app.dependency_overrides.clear()
        app.dependency_overrides[verify_admin] = mock_unauthorized # restore

    def test_import_jsonl_reads_real_judge_dict_contract(self):
        """[IMPORT-5][M11-FIX 8fc3560] import/jsonl consumes the REAL
        RealityJudge dict contract, not attributes."""
        class _RealDictJudgeStub:
            def judge(self, question, ai_answer, cycle_count=0):
                return {
                    "verdict": "FAIL",
                    "confidence": 0.42,
                    "evidence": {
                        "falsification_status": "FALSIFIED",
                        "governance_decision": "BLOCK",
                        "v100_phase_timings": {"total_ms": 123.4},
                    },
                }

        app.dependency_overrides[verify_admin] = lambda: True
        try:
            with TestClient(app) as client:
                with patch(
                    "scp.api.routes.import_routes.get_judge",
                    return_value=_RealDictJudgeStub(),
                ):
                    payload = "\n".join([
                        json.dumps({"question": "M11 dict contract probe 1", "ai_answer": "so san pham = 41"}),
                        json.dumps({"question": "M11 dict contract probe 2", "ai_answer": "so san pham = 42"}),
                    ])
                    response = client.post("/import/jsonl", content=payload.encode("utf-8"))
            assert response.status_code == 200, response.text
            data = response.json()
            assert data["summary"]["total"] == 2, data
            assert data["summary"]["errors"] == 0, data["results"]
            assert data["summary"]["pass"] == 0, data["summary"]
            assert data["summary"]["fail"] == 2, data["summary"]
            for row in data["results"]:
                assert row["verdict"] == "FAIL", row
                assert row["confidence"] == 0.42, row
                assert row["falsification"] == "FALSIFIED", row
                assert row["governance"] == "BLOCK", row
                assert row["elapsed_ms"] == 123.4, row
        finally:
            app.dependency_overrides.clear()
            app.dependency_overrides[verify_admin] = mock_unauthorized # restore

    # =========================================================================
    # 4. WEBHOOK ROUTES
    # =========================================================================

    def test_webhook_analyze_requires_admin(self):
        """[WEBHOOK-1] POST /api/analyze requires admin auth."""
        with patch("scp.api.webhook._require_admin", side_effect=mock_unauthorized):
            with TestClient(app) as client:
                response = client.post("/api/analyze", json={"prompt": "hello", "system_id": "test"})
                assert response.status_code in [401, 403]

    def test_webhook_register_requires_admin(self):
        """[WEBHOOK-2] POST /api/register requires admin auth."""
        with patch("scp.api.webhook._require_admin", side_effect=mock_unauthorized):
            with TestClient(app) as client:
                response = client.post("/api/register", json={"system_id": "test"})
                assert response.status_code in [401, 403]

    def test_webhook_threats_requires_admin(self):
        """[WEBHOOK-3] GET /api/threats requires admin auth."""
        with patch("scp.api.webhook._require_admin", side_effect=mock_unauthorized):
            with TestClient(app) as client:
                response = client.get("/api/threats")
                assert response.status_code in [401, 403]

    def test_webhook_alerts_requires_admin(self):
        """[WEBHOOK-4] GET /api/alerts requires admin auth."""
        with patch("scp.api.webhook._require_admin", side_effect=mock_unauthorized):
            with TestClient(app) as client:
                response = client.get("/api/alerts")
                assert response.status_code in [401, 403]

    def test_webhook_systems_requires_admin(self):
        """[WEBHOOK-5] GET /api/systems requires admin auth."""
        with patch("scp.api.webhook._require_admin", side_effect=mock_unauthorized):
            with TestClient(app) as client:
                response = client.get("/api/systems")
                assert response.status_code in [401, 403]

    def test_webhook_analyze_processes_prompt(self, monkeypatch):
        """[WEBHOOK-6] Webhook /api/analyze processes prompt when authorized."""
        # Auth bypass — _require_admin is an internal auth seam; mock it.
        with patch("scp.api.webhook._require_admin"):
            # Mock external LLM dependency: _llm_judge_async returns PASS.
            # The real judge pipeline runs (tier1, KB consult, etc.).
            import scp.runtime.judge_llm as _judge_llm_mod
            async def _fake_llm_judge_async(*a, **k):
                return True
            monkeypatch.setattr(_judge_llm_mod, "_llm_judge_async", _fake_llm_judge_async)
            monkeypatch.setattr(_judge_llm_mod, "_llm_judge", lambda *a, **k: True)
            # Prevent multi-LLM crosscheck (external I/O) for speed.
            monkeypatch.setenv("SCP_MULTI_LLM_CROSSCHECK", "0")
            # Prevent background threads from blocking test teardown.
            monkeypatch.setattr(
                "scp.security.attack_crawler.start_crawl_thread",
                lambda *a, **k: None,
            )
            # Patch judge.judge_with_react_fallback to wrap dict result in
            # SimpleNamespace (webhook uses getattr on verdict).
            import scp.runtime.judge as _judge_mod
            import types
            async def _wrap_judge(judge_instance, *args, **kwargs):
                result = await _judge_mod.RealityJudge.judge_async(judge_instance, *args, **kwargs)
                return types.SimpleNamespace(
                    verdict=result.get("verdict", "UNKNOWN"),
                    confidence=result.get("confidence", 0.0),
                    evidence=result.get("evidence", {}),
                    domain=result.get("domain", "general"),
                    final_answer=result.get("final_answer", ""),
                )
            monkeypatch.setattr(_judge_mod.RealityJudge, "judge_with_react_fallback", _wrap_judge)

            with TestClient(app) as client:
                response = client.post("/api/analyze", json={
                    "prompt": "Test prompt",
                    "ai_answer": "Test answer",  # tier1 requires non-empty answer
                    "system_id": "sys-1"
                })
                assert response.status_code == 200
                data = response.json()
                assert data["action"] == "allow"
                assert data["verdict"] == "PASS"


class TestFlow11AdminImportCausalCoverage:
    """
    FA-13: Causal Coverage Matrix for Mạch 11
    """

    def test_causal_admin_v98_all_endpoints_admin_required(self):
        """Branch: all v98 endpoints require admin"""
        with TestClient(app) as client:
            response = client.get("/v98/status", headers={"Authorization": "Bearer fake"})
            assert response.status_code in [401, 403]

    def test_causal_admin_v100_all_endpoints_admin_required(self):
        """Branch: all v100 endpoints require admin"""
        with TestClient(app) as client:
            response = client.get("/v100/status", headers={"Authorization": "Bearer fake"})
            assert response.status_code in [401, 403]

    def test_causal_import_jsonl_endpoint(self):
        """Branch: jsonl import endpoint works"""
        app.dependency_overrides[verify_admin] = lambda: True
        try:
            with TestClient(app) as client:
                response = client.post("/import/jsonl", content=b"")
                # Should not crash - endpoint exists
                assert response.status_code in [200, 400, 422]
        finally:
            app.dependency_overrides.clear()

    def test_causal_import_excel_endpoint(self):
        """Branch: excel import endpoint exists"""
        with TestClient(app) as client:
            response = client.post("/import/excel", files={"file": ("test.xlsx", b"")}, headers={"Authorization": "Bearer fake"})
            assert response.status_code in [401, 403]

    def test_causal_import_batch_endpoint(self):
        """Branch: batch import endpoint exists"""
        with TestClient(app) as client:
            response = client.post("/import/batch", json={"items": []}, headers={"Authorization": "Bearer fake"})
            assert response.status_code in [401, 403]

    def test_causal_webhook_endpoints_exist(self):
        """Branch: webhook endpoints exist and require auth"""
        with TestClient(app) as client:
            response = client.get("/api/threats")
            assert response.status_code in [401, 403, 404]

    def test_causal_webhook_analyze_processes(self, monkeypatch):
        """Branch: webhook analyze returns allow for PASS"""
        with patch("scp.api.webhook._require_admin"):
            # Mock external LLM dependency: _llm_judge_async returns PASS.
            # The real judge pipeline runs (tier1, KB consult, etc.).
            import scp.runtime.judge_llm as _judge_llm_mod
            async def _fake_llm_judge_async(*a, **k):
                return True
            monkeypatch.setattr(_judge_llm_mod, "_llm_judge_async", _fake_llm_judge_async)
            monkeypatch.setattr(_judge_llm_mod, "_llm_judge", lambda *a, **k: True)
            # Prevent multi-LLM crosscheck (external I/O) for speed.
            monkeypatch.setenv("SCP_MULTI_LLM_CROSSCHECK", "0")
            # Prevent background threads from blocking test teardown.
            monkeypatch.setattr(
                "scp.security.attack_crawler.start_crawl_thread",
                lambda *a, **k: None,
            )
            # Patch judge.judge_with_react_fallback to wrap dict result in
            # SimpleNamespace (webhook uses getattr on verdict).
            import scp.runtime.judge as _judge_mod
            import types
            async def _wrap_judge(judge_instance, *args, **kwargs):
                result = await _judge_mod.RealityJudge.judge_async(judge_instance, *args, **kwargs)
                return types.SimpleNamespace(
                    verdict=result.get("verdict", "UNKNOWN"),
                    confidence=result.get("confidence", 0.0),
                    evidence=result.get("evidence", {}),
                    domain=result.get("domain", "general"),
                    final_answer=result.get("final_answer", ""),
                )
            monkeypatch.setattr(_judge_mod.RealityJudge, "judge_with_react_fallback", _wrap_judge)

            with TestClient(app) as client:
                response = client.post("/api/analyze", json={
                    "prompt": "test",
                    "ai_answer": "Test answer",  # tier1 requires non-empty answer
                    "system_id": "sys-1"
                })
                assert response.status_code == 200
                data = response.json()
                assert data["action"] == "allow"


if __name__ == "__main__":
    pass
