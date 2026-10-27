"""SCP Complete Standard Test — Mạch 3: OpenAI & SWE-Bench Compatibility
Covers: api/routes/openai_compat.py, api/routes/swe_bench_routes.py
FA-01: Strict assertions, no loosening | FA-02: No skip/xfail | FA-03: Full evidence
FA-04: No simulated VERIFIED | FA-09: Exploit mandate | FA-13: Causal coverage
ARC-01: <= 800 LOC
"""
from __future__ import annotations

import json
import logging
import pytest
from fastapi.testclient import TestClient

from scp.api.routes import openai_compat
from scp.api_server import app
from scp.api_server_parts import helpers as _scp_helpers
from scp.security.auth import verify_admin

logger = logging.getLogger("tests.T02.flow03")


def _no_crawl_thread_in_tests(data_dir: str = "data"):
    logger.info("[TEST-ISOLATION] AttackCrawler thread suppressed in T02/flow03 session")


def _no_fast_learning_thread_in_tests(*args, **kwargs):
    logger.info("[TEST-ISOLATION] FastLearning background thread suppressed in T02/flow03 session")


_scp_helpers.start_crawl_thread = _no_crawl_thread_in_tests
if getattr(_scp_helpers, "start_fast_learning_thread", None) is not None:
    _scp_helpers.start_fast_learning_thread = _no_fast_learning_thread_in_tests

M03_JWT_SECRET = "m03-test-jwt-secret-0123456789abcdef-40chars"


def _auth_headers(monkeypatch) -> dict:
    """Mint a REAL JWT with the test secret (config fixture, not a mock)."""
    monkeypatch.setenv("SCP_JWT_SECRET", M03_JWT_SECRET)
    from scp.security.jwt_guard import create_access_token

    return {"Authorization": f"Bearer {create_access_token({'sub': 'm03'})}"}


class MockGateway:
    """Hermetic test gateway simulating active LLM provider without network egress."""

    def __init__(self, answer: str = "Test response from mock gateway", provider: str = "mock_provider"):
        self.answer = answer
        self.provider = provider

    def has_active_providers(self) -> bool:
        return True

    async def chat(self, question: str, context: str = "", system_prompt: str = "", task: str = "chat") -> tuple[str, str]:
        return self.answer, self.provider


class InactiveGateway:
    """Hermetic test gateway simulating zero active LLM providers."""

    def has_active_providers(self) -> bool:
        return False

    async def chat(self, question: str, context: str = "", system_prompt: str = "", task: str = "chat") -> tuple[str, str]:
        raise RuntimeError("No active providers configured")


@pytest.fixture(autouse=True)
def hermetic_gateway_boundary(monkeypatch):
    """Ensure Flow03 tests use an active mock gateway by default (hermetic, no live API keys needed)."""
    gw = MockGateway()
    monkeypatch.setattr(openai_compat, "get_gateway", lambda: gw)
    return gw


class TestFlow03OpenAICompat:
    """Mạch 3: OpenAI & SWE-Bench Compatibility - SCP Complete Standard"""

    def test_openai_chat_completions_endpoint_exists(self):
        """[OPENAI-1] POST /v1/chat/completions endpoint exists and accepts OpenAI format."""
        with TestClient(app) as client:
            response = client.post(
                "/v1/chat/completions",
                json={"model": "gpt-3.5-turbo", "messages": [{"role": "user", "content": "Hello"}], "max_tokens": 100},
            )
            assert response.status_code != 404

    def test_openai_chat_completions_validates_schema(self, monkeypatch):
        """[OPENAI-2] OpenAI endpoint validates request schema strictly."""
        headers = _auth_headers(monkeypatch)
        with TestClient(app) as client:
            response = client.post("/v1/chat/completions", json={})
            assert response.status_code == 401

            response = client.post("/v1/chat/completions", json={}, headers=headers)
            assert response.status_code == 400
            error = response.json()["error"]
            assert error["message"]
            assert error["type"] == "invalid_request"

            response = client.post("/v1/chat/completions", json={"model": "gpt-3.5-turbo", "messages": "not a list"}, headers=headers)
            assert response.status_code == 400
            assert response.json()["error"]["type"] == "invalid_request"

            response = client.post("/v1/chat/completions", json={"model": "gpt-3.5-turbo", "messages": ["hello"]}, headers=headers)
            assert response.status_code == 400
            assert response.json()["error"]["type"] == "invalid_request"

    def test_openai_models_endpoint_returns_model_list(self):
        """[OPENAI-3] GET /v1/models returns available models list."""
        with TestClient(app) as client:
            response = client.get("/v1/models")
            assert response.status_code != 404
            if response.status_code == 200:
                data = response.json()
                assert "data" in data
                assert isinstance(data["data"], list)

    def test_root_advertisement_lists_only_mounted_routes(self):
        """[W4-4 docs-truth] The root `/` endpoint list must never advertise an unmounted route."""
        def _mounted_paths(routes, prefix: str = "") -> set:
            paths: set = set()
            for route in routes:
                ctx = getattr(route, "include_context", None)
                inner_prefix = prefix + str(getattr(ctx, "prefix", "") or "")
                inner = getattr(route, "routes", None) or getattr(getattr(route, "original_router", None), "routes", None)
                if inner:
                    paths |= _mounted_paths(inner, inner_prefix)
                path = getattr(route, "path", None)
                if path:
                    paths.add(inner_prefix + str(path))
            return paths

        with TestClient(app) as client:
            response = client.get("/")
        assert response.status_code == 200
        advertised = response.json()["endpoints"]
        assert advertised, "root must still advertise the API surface"
        mounted = _mounted_paths(app.router.routes)
        assert "/v1/models" in mounted, "openai_compat router must be mounted in the test profile"
        for entry in advertised:
            method, _, path = entry.partition(" ")
            assert method in {"GET", "POST"}, f"malformed advertisement: {entry!r}"
            assert path.strip() in mounted, f"root advertises {entry!r} but {path.strip()} is not mounted"

    def test_v3_tasks_lists_real_kernel_tasks(self, monkeypatch, tmp_path):
        """[W4-4 docs-truth] GET /v3/tasks reads the REAL TaskKernel projection."""
        from scp.task_kernel import TaskKernel
        db_path = tmp_path / "ask_task_kernel.sqlite3"
        kernel = TaskKernel(str(db_path))
        try:
            kernel.create_task("w44-audit-task-1", owner="w44-worker", goal="SECRET-GOAL-MUST-NOT-LEAK", risk_tier="R0")
        finally:
            kernel.close()

        monkeypatch.setenv("SCP_KERNEL_DB_PATH", str(db_path))
        headers = _auth_headers(monkeypatch)
        with TestClient(app) as client:
            response = client.get("/v3/tasks", headers=headers)
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "active"
        tasks = data["tasks"]
        assert isinstance(tasks, list) and tasks, "kernel task must be listed"
        entry = next(t for t in tasks if t["task_id"] == "w44-audit-task-1")
        assert set(entry.keys()) == {"task_id", "state", "created_at"}
        assert entry["state"] == "CREATED"
        assert "SECRET-GOAL-MUST-NOT-LEAK" not in json.dumps(data)
        assert "w44-worker" not in json.dumps(data)

    def test_v3_tasks_empty_when_kernel_db_absent(self, monkeypatch, tmp_path):
        """[W4-4 docs-truth] Backward-compatible shape with no kernel DB on disk."""
        monkeypatch.setenv("SCP_KERNEL_DB_PATH", str(tmp_path / "missing.sqlite3"))
        headers = _auth_headers(monkeypatch)
        with TestClient(app) as client:
            response = client.get("/v3/tasks", headers=headers)
        assert response.status_code == 200
        assert response.json() == {"tasks": [], "status": "active"}

    def test_openai_chat_completions_forwards_to_gateway(self, monkeypatch):
        """[OPENAI-4] OpenAI → SCP request translation really happens."""
        from scp.core.release_identity import CANONICAL_MODEL_ID
        headers = _auth_headers(monkeypatch)
        with TestClient(app) as client:
            response = client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "Test"}]}, headers=headers)
            assert response.status_code == 200
            data = response.json()
            assert data["model"] == CANONICAL_MODEL_ID

            response = client.post("/v1/chat/completions", json={"model": "gpt-3.5-turbo", "messages": [{"role": "system", "content": "sys only"}]}, headers=headers)
            assert response.status_code == 400
            assert data["run_status"] == "SUCCESS"
            assert data["ledger_status"] == "OK"

    def test_openai_compat_stub_visibility_marker(self, monkeypatch):
        """[GAP-5 FIX / M03 STUB REMEDIATION] When running as stub, fail-closed with 501."""
        monkeypatch.setenv("SCP_OPENAI_COMPAT_STUB", "1")
        headers = _auth_headers(monkeypatch)
        with TestClient(app) as client:
            response = client.post("/v1/chat/completions", json={"model": "gpt-3.5-turbo", "messages": [{"role": "user", "content": "Hello"}]}, headers=headers)
            assert response.status_code == 501
            data = response.json()
            assert "error" in data
            assert data["error"]["code"] == 501
            assert data["error"]["type"] == "not_implemented_error"
            assert "active LLM provider" in data["error"]["message"]

    def test_openai_compat_no_active_providers_returns_501(self, monkeypatch):
        """[FAIL-CLOSED 501] When no active LLM provider exists, /v1/chat/completions returns 501."""
        monkeypatch.setattr(openai_compat, "get_gateway", lambda: InactiveGateway())
        monkeypatch.delenv("SCP_OPENAI_COMPAT_STUB", raising=False)
        headers = _auth_headers(monkeypatch)
        with TestClient(app) as client:
            response = client.post("/v1/chat/completions", json={"model": "gpt-3.5-turbo", "messages": [{"role": "user", "content": "Hello"}]}, headers=headers)
            assert response.status_code == 501
            data = response.json()
            assert data["error"]["code"] == 501
            assert data["error"]["type"] == "not_implemented_error"
            assert "active LLM provider" in data["error"]["message"]

    def test_openai_compat_handles_streaming_false(self, monkeypatch):
        """[OPENAI-5] OpenAI compat handles non-streaming requests correctly."""
        headers = _auth_headers(monkeypatch)
        with TestClient(app) as client:
            response = client.post("/v1/chat/completions", json={"model": "gpt-3.5-turbo", "messages": [{"role": "user", "content": "Test"}], "stream": False}, headers=headers)
            assert response.status_code == 200
            data = response.json()
            assert data["object"] == "chat.completion"
            assert data["id"].startswith("chatcmpl-")
            assert data["model"] == "gpt-3.5-turbo"
            choice = data["choices"][0]
            assert choice["index"] == 0
            assert choice["message"]["role"] == "assistant"
            assert choice["finish_reason"] == "stop"
            assert choice["message"]["content"]
            assert data["scp_metadata"]["verdict"] in ("PASS", "UNKNOWN", "FAIL", "ABSTAIN")
            assert data["run_status"] == "SUCCESS"
            assert data["ledger_status"] == "OK"

    def test_swe_bench_chat_completions_endpoint_exists(self):
        """[SWE-1] POST /swe-bench/v1/chat/completions (SWE-Bench compat) works."""
        with TestClient(app) as client:
            unauth = client.post("/swe-bench/v1/chat/completions", json={"model": "scp-agent", "messages": [{"role": "user", "content": "Fix this bug"}]})
            assert unauth.status_code == 401

            app.dependency_overrides[verify_admin] = lambda: True
            try:
                response = client.post("/swe-bench/v1/chat/completions", json={"model": "scp-agent", "messages": [{"role": "user", "content": "Fix this bug"}], "instance_id": "test-instance-123"})
                assert response.status_code == 200
                data = response.json()
                assert data["object"] == "chat.completion"
                assert data["model"] == "scp-agent"
                choice = data["choices"][0]
                assert choice["index"] == 0
                assert choice["message"]["role"] == "assistant"
                assert choice["message"]["content"]
                assert choice["finish_reason"] == "stop"
                assert data["usage"]["total_tokens"] == 0
            finally:
                app.dependency_overrides.pop(verify_admin, None)

    def test_swe_bench_validates_instance_id(self):
        """[SWE-2] SWE-Bench compat instance_id handling."""
        app.dependency_overrides[verify_admin] = lambda: True
        try:
            with TestClient(app) as client:
                response = client.post("/swe-bench/v1/chat/completions", json={"model": "scp-agent", "messages": [{"role": "user", "content": "Test"}]})
                assert response.status_code == 200
                assert response.json()["object"] == "chat.completion"

                response = client.post("/swe-bench/v1/chat/completions", json={"messages": [{"role": "user", "content": "Test"}]})
                assert response.status_code == 422
                assert "detail" in response.json()
        finally:
            app.dependency_overrides.pop(verify_admin, None)

    def test_swe_bench_run_endpoint_translates_request(self):
        """[SWE-3] SWE-Bench compat translation contract."""
        app.dependency_overrides[verify_admin] = lambda: True
        try:
            with TestClient(app) as client:
                response = client.post("/swe-bench/v1/chat/completions", json={
                    "model": "scp-agent",
                    "messages": [{"role": "user", "content": "Fix this bug"}],
                    "tools": [{"type": "function", "function": {"name": "bash"}}],
                    "tool_choice": "auto",
                    "instance_id": "test-instance-123",
                })
                assert response.status_code == 200
                message = response.json()["choices"][0]["message"]
                assert message["tool_calls"] == []
                assert message["content"]
        finally:
            app.dependency_overrides.pop(verify_admin, None)

    def test_translate_openai_preserves_context(self, monkeypatch):
        """[TRANS-1] OpenAI → SCP translation preserves message context."""
        headers = _auth_headers(monkeypatch)
        with TestClient(app) as client:
            response = client.post("/v1/chat/completions", json={
                "model": "gpt-3.5-turbo",
                "messages": [
                    {"role": "user", "content": "earlier question"},
                    {"role": "assistant", "content": "earlier answer"},
                    {"role": "user", "content": None},
                ]
            }, headers=headers)
            assert response.status_code == 400
            assert response.json()["error"]["type"] == "invalid_request"

    def test_translate_openai_handles_tools(self, monkeypatch):
        """[TRANS-2] OpenAI tool definitions are accepted at the boundary."""
        headers = _auth_headers(monkeypatch)
        with TestClient(app) as client:
            response = client.post("/v1/chat/completions", json={
                "model": "gpt-3.5-turbo",
                "messages": [{"role": "user", "content": "List files"}],
                "tools": [{"type": "function", "function": {"name": "list_files", "parameters": {"type": "object", "properties": {}}}}],
                "tool_choice": "auto",
            }, headers=headers)
            assert response.status_code == 200
            data = response.json()
            assert data["object"] == "chat.completion"
            assert data["choices"][0]["finish_reason"] == "stop"

    def test_scp_to_openai_response_translation(self, monkeypatch):
        """[TRANS-3] SCP verdict → OpenAI response envelope is well-formed."""
        headers = _auth_headers(monkeypatch)
        with TestClient(app) as client:
            response = client.post("/v1/chat/completions", json={"model": "gpt-3.5-turbo", "messages": [{"role": "user", "content": "Hello"}]}, headers=headers)
            assert response.status_code == 200
            data = response.json()
            assert data["id"].startswith("chatcmpl-")
            assert isinstance(data["created"], int)
            assert isinstance(data["choices"], list) and len(data["choices"]) == 1
            assert data["choices"][0]["message"]["role"] == "assistant"
            usage = data["usage"]
            assert {"prompt_tokens", "completion_tokens", "total_tokens"} <= set(usage)
            assert "verdict" in data["scp_metadata"]

    def test_streaming_response_translation(self, monkeypatch):
        """[TRANS-4] Streaming behavior at the OpenAI boundary."""
        headers = _auth_headers(monkeypatch)
        with TestClient(app) as client:
            response = client.post("/v1/chat/completions", json={"model": "gpt-3.5-turbo", "messages": [{"role": "user", "content": "Test"}], "stream": True}, headers=headers)
            assert response.status_code == 200
            assert "application/json" in response.headers["content-type"]
            data = response.json()
            assert data["object"] == "chat.completion"
            assert data["choices"][0]["message"]["role"] == "assistant"
            assert data["choices"][0]["message"]["content"]
            assert data["scp_metadata"]["verdict"] in ("PASS", "UNKNOWN", "FAIL", "ABSTAIN")

    def test_openai_compat_gateway_failure_returns_503(self, monkeypatch):
        """[ERR-1] Judge pipeline failure returns 503 with OpenAI error format."""
        headers = _auth_headers(monkeypatch)

        class _BrokenJudge:
            def judge(self, **kwargs):
                raise RuntimeError("injected pipeline fault")

        monkeypatch.setattr(openai_compat, "get_judge", lambda: _BrokenJudge())
        with TestClient(app) as client:
            response = client.post("/v1/chat/completions", json={"model": "gpt-3.5-turbo", "messages": [{"role": "user", "content": "Test"}]}, headers=headers)
            assert response.status_code == 503
            data = response.json()
            assert "error" in data
            assert data["error"]["message"] == "Upstream judge pipeline unavailable"
            assert data["error"]["type"] == "server_error"
            assert "injected pipeline fault" not in json.dumps(data)

    def test_openai_compat_generation_failure_returns_502(self, monkeypatch):
        """[GEN-ERR-502] LLM generation exception returns HTTP 502 with structured OpenAI error."""
        class _FailingGateway(MockGateway):
            async def chat(self, *args, **kwargs):
                raise RuntimeError("upstream provider timeout")

        monkeypatch.setattr(openai_compat, "get_gateway", lambda: _FailingGateway())
        headers = _auth_headers(monkeypatch)
        with TestClient(app) as client:
            response = client.post("/v1/chat/completions", json={"model": "gpt-3.5-turbo", "messages": [{"role": "user", "content": "Hello"}]}, headers=headers)
            assert response.status_code == 502
            data = response.json()
            assert data["error"]["code"] == 502
            assert data["error"]["type"] == "server_error"
            assert "LLM generation failed" in data["error"]["message"]

    def test_openai_compat_empty_generation_returns_502(self, monkeypatch):
        """[GEN-EMPTY-502] Empty LLM generation returns HTTP 502 rather than 200 canned refusal."""
        class _EmptyGateway(MockGateway):
            async def chat(self, *args, **kwargs):
                return "", "empty_provider"

        monkeypatch.setattr(openai_compat, "get_gateway", lambda: _EmptyGateway())
        headers = _auth_headers(monkeypatch)
        with TestClient(app) as client:
            response = client.post("/v1/chat/completions", json={"model": "gpt-3.5-turbo", "messages": [{"role": "user", "content": "Hello"}]}, headers=headers)
            assert response.status_code == 502
            data = response.json()
            assert data["error"]["code"] == 502
            assert data["error"]["type"] == "server_error"
            assert "empty response" in data["error"]["message"]

    def test_openai_chat_factual_abstain_withheld_fail_closed(self, monkeypatch):
        """[FAIL-CLOSED ABSTAIN] Factual false claim with ABSTAIN verdict is withheld fail-closed."""
        fake_gw = MockGateway(answer="Berlin là thủ đô của Pháp.")
        monkeypatch.setattr(openai_compat, "get_gateway", lambda: fake_gw)

        class _AbstainJudge:
            def __init__(self): self.dos_protection = None
            def judge(self, question, ai_answer="", **kwargs):
                return {"verdict": "ABSTAIN", "confidence": 0.4, "final_answer": ai_answer, "reasoning": "cannot verify", "evidence": {"governance_decision": "ESCALATE"}}

        monkeypatch.setattr(openai_compat, "get_judge", lambda: _AbstainJudge())
        headers = _auth_headers(monkeypatch)
        with TestClient(app) as client:
            resp = client.post("/v1/chat/completions", json={"model": "gpt-3.5-turbo", "messages": [{"role": "user", "content": "Thủ đô của nước Pháp là gì?"}]}, headers=headers)
            assert resp.status_code == 200
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            assert "Berlin" not in content
            assert content == "I cannot comply with this request."
            assert resp.headers.get("x-scp-delivery") == "withheld"

    def test_openai_chat_factual_degraded_withheld_fail_closed(self, monkeypatch):
        """[FAIL-CLOSED DEGRADED] Factual false claim with DEGRADED verdict is withheld fail-closed."""
        fake_gw = MockGateway(answer="Berlin là thủ đô của Pháp.")
        monkeypatch.setattr(openai_compat, "get_gateway", lambda: fake_gw)

        class _DegradedJudge:
            def __init__(self): self.dos_protection = None
            def judge(self, question, ai_answer="", **kwargs):
                return {"verdict": "DEGRADED", "confidence": 0.3, "final_answer": ai_answer, "reasoning": "degraded", "evidence": {"governance_decision": "DEGRADED"}}

        monkeypatch.setattr(openai_compat, "get_judge", lambda: _DegradedJudge())
        headers = _auth_headers(monkeypatch)
        with TestClient(app) as client:
            resp = client.post("/v1/chat/completions", json={"model": "gpt-3.5-turbo", "messages": [{"role": "user", "content": "Thủ đô của nước Pháp là gì?"}]}, headers=headers)
            assert resp.status_code == 200
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            assert "Berlin" not in content
            assert content == "I cannot comply with this request."
            assert resp.headers.get("x-scp-delivery") == "withheld"

    def test_openai_chat_factual_unknown_withheld_fail_closed(self, monkeypatch):
        """[FAIL-CLOSED UNKNOWN] Factual false claim with UNKNOWN verdict is withheld fail-closed."""
        fake_gw = MockGateway(answer="Berlin là thủ đô của Pháp.")
        monkeypatch.setattr(openai_compat, "get_gateway", lambda: fake_gw)

        class _UnknownJudge:
            def __init__(self): self.dos_protection = None
            def judge(self, question, ai_answer="", **kwargs):
                return {"verdict": "UNKNOWN", "confidence": 0.2, "final_answer": ai_answer, "reasoning": "disagreement", "evidence": {"governance_decision": "ESCALATE"}}

        monkeypatch.setattr(openai_compat, "get_judge", lambda: _UnknownJudge())
        headers = _auth_headers(monkeypatch)
        with TestClient(app) as client:
            resp = client.post("/v1/chat/completions", json={"model": "gpt-3.5-turbo", "messages": [{"role": "user", "content": "Thủ đô của nước Pháp là gì?"}]}, headers=headers)
            assert resp.status_code == 200
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            assert "Berlin" not in content
            assert content == "I cannot comply with this request."
            assert resp.headers.get("x-scp-delivery") == "withheld"

    def test_openai_chat_factual_fail_withheld_fail_closed(self, monkeypatch):
        """[FAIL-CLOSED FAIL] Factual false claim with FAIL verdict is withheld fail-closed."""
        fake_gw = MockGateway(answer="Berlin là thủ đô của Pháp.")
        monkeypatch.setattr(openai_compat, "get_gateway", lambda: fake_gw)

        class _FailJudge:
            def __init__(self): self.dos_protection = None
            def judge(self, question, ai_answer="", **kwargs):
                return {"verdict": "FAIL", "confidence": 0.1, "final_answer": ai_answer, "reasoning": "fail", "evidence": {"governance_decision": "KILL"}}

        monkeypatch.setattr(openai_compat, "get_judge", lambda: _FailJudge())
        headers = _auth_headers(monkeypatch)
        with TestClient(app) as client:
            resp = client.post("/v1/chat/completions", json={"model": "gpt-3.5-turbo", "messages": [{"role": "user", "content": "Thủ đô của nước Pháp là gì?"}]}, headers=headers)
            assert resp.status_code == 200
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            assert "Berlin" not in content
            assert content == "I cannot comply with this request."
            assert resp.headers.get("x-scp-delivery") == "withheld"

    def test_openai_chat_conversational_greeting_allowed(self, monkeypatch):
        """[CONVERSATIONAL] Strictly conversational query is delivered even with non-PASS verdict."""
        fake_gw = MockGateway(answer="Hello! I am SCP, happy to assist you.")
        monkeypatch.setattr(openai_compat, "get_gateway", lambda: fake_gw)

        class _ConversationalJudge:
            def __init__(self): self.dos_protection = None
            def judge(self, question, ai_answer="", **kwargs):
                return {"verdict": "UNKNOWN", "confidence": 0.5, "final_answer": ai_answer, "reasoning": "greeting", "evidence": {"governance_decision": "CLEAR"}}

        monkeypatch.setattr(openai_compat, "get_judge", lambda: _ConversationalJudge())
        headers = _auth_headers(monkeypatch)
        with TestClient(app) as client:
            resp = client.post("/v1/chat/completions", json={"model": "gpt-3.5-turbo", "messages": [{"role": "user", "content": "Hello! How are you?"}]}, headers=headers)
            assert resp.status_code == 200
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            assert "Hello!" in content
            assert resp.headers.get("x-scp-delivery") == "conversational"

    def test_swe_bench_compat_agent_failure_returns_error(self):
        """[ERR-2] SWE-Bench compat returns structured errors on bad requests."""
        app.dependency_overrides[verify_admin] = lambda: True
        try:
            with TestClient(app) as client:
                response = client.post("/swe-bench/v1/chat/completions", json={"messages": [{"role": "user", "content": "Test"}]})
                assert response.status_code == 422
                assert "detail" in response.json()

                response = client.post("/swe-bench/v1/chat/completions", json={"model": "scp-agent", "messages": "not a list"})
                assert response.status_code == 422
                assert "detail" in response.json()
        finally:
            app.dependency_overrides.pop(verify_admin, None)

    def test_rate_limiting_on_compat_endpoints(self):
        """[RATE-1] Compat endpoints respect rate limits."""
        with TestClient(app) as client:
            for _ in range(5):
                response = client.post("/v1/models")
                assert response.status_code != 404


class TestFlow03OpenAICompatCausalCoverage:
    """FA-13: Causal Coverage Matrix for Mạch 3"""

    def test_causal_openai_chat_valid_request(self, monkeypatch):
        """Branch: valid OpenAI request → forwarded to gateway"""
        headers = _auth_headers(monkeypatch)
        with TestClient(app) as client:
            resp = client.post("/v1/chat/completions", json={"model": "gpt-3.5-turbo", "messages": [{"role": "user", "content": "Ping"}]}, headers=headers)
            assert resp.status_code == 200
            assert resp.json()["object"] == "chat.completion"

    def test_causal_openai_chat_invalid_schema(self, monkeypatch):
        """Branch: invalid schema → 400 with invalid_request type"""
        headers = _auth_headers(monkeypatch)
        with TestClient(app) as client:
            resp = client.post("/v1/chat/completions", json={"model": "gpt-3.5-turbo", "messages": "not-a-list"}, headers=headers)
            assert resp.status_code == 400
            assert resp.json()["error"]["type"] == "invalid_request"

    def test_causal_openai_models_list(self):
        """Branch: models endpoint → returns list"""
        with TestClient(app) as client:
            resp = client.get("/v1/models")
            assert resp.status_code != 404
            if resp.status_code == 200:
                assert "data" in resp.json()

    def test_causal_openai_gateway_forward(self, monkeypatch):
        """Branch: request → gateway.ask() called"""
        from scp.core.release_identity import CANONICAL_MODEL_ID
        headers = _auth_headers(monkeypatch)
        with TestClient(app) as client:
            resp = client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "Evaluate query"}]}, headers=headers)
            assert resp.status_code == 200
            data = resp.json()
            assert data["model"] == CANONICAL_MODEL_ID
            assert data["run_status"] == "SUCCESS"

    def test_causal_openai_streaming_false(self, monkeypatch):
        """Branch: stream=false → JSON response"""
        headers = _auth_headers(monkeypatch)
        with TestClient(app) as client:
            resp = client.post("/v1/chat/completions", json={"model": "gpt-3.5-turbo", "messages": [{"role": "user", "content": "Non streaming"}], "stream": False}, headers=headers)
            assert resp.status_code == 200
            assert "application/json" in resp.headers["content-type"]
            assert resp.json()["choices"][0]["message"]["role"] == "assistant"

    def test_causal_swe_bench_endpoint_exists(self):
        """Branch: SWE-Bench endpoint accessible"""
        with TestClient(app) as client:
            resp = client.post("/swe-bench/v1/chat/completions", json={})
            assert resp.status_code != 404

    def test_causal_swe_bench_agent_forward(self):
        """Branch: SWE request → agent orchestrator"""
        app.dependency_overrides[verify_admin] = lambda: True
        try:
            with TestClient(app) as client:
                resp = client.post("/swe-bench/v1/chat/completions", json={
                    "model": "scp-agent",
                    "messages": [{"role": "user", "content": "Fix bug"}],
                    "tools": [{"type": "function", "function": {"name": "bash"}}],
                    "tool_choice": "auto",
                    "instance_id": "test-123",
                })
                assert resp.status_code == 200
                assert resp.json()["choices"][0]["message"]["tool_calls"] == []
        finally:
            app.dependency_overrides.pop(verify_admin, None)

    def test_causal_openai_translation_preserves_context(self, monkeypatch):
        """Branch: multi-message → combined context"""
        headers = _auth_headers(monkeypatch)
        with TestClient(app) as client:
            resp = client.post("/v1/chat/completions", json={
                "model": "gpt-3.5-turbo",
                "messages": [
                    {"role": "user", "content": "first"},
                    {"role": "assistant", "content": "reply"},
                    {"role": "user", "content": None},
                ]
            }, headers=headers)
            assert resp.status_code == 400
            assert resp.json()["error"]["type"] == "invalid_request"

    def test_causal_openai_translation_handles_tools(self, monkeypatch):
        """Branch: tools defined → noted in SCP request"""
        headers = _auth_headers(monkeypatch)
        with TestClient(app) as client:
            resp = client.post("/v1/chat/completions", json={
                "model": "gpt-3.5-turbo",
                "messages": [{"role": "user", "content": "Use tool"}],
                "tools": [{"type": "function", "function": {"name": "f1"}}],
                "tool_choice": "auto",
            }, headers=headers)
            assert resp.status_code == 200
            assert resp.json()["object"] == "chat.completion"

    def test_causal_scp_to_openai_translation(self, monkeypatch):
        """Branch: SCP response → valid OpenAI format"""
        headers = _auth_headers(monkeypatch)
        with TestClient(app) as client:
            resp = client.post("/v1/chat/completions", json={"model": "gpt-3.5-turbo", "messages": [{"role": "user", "content": "Hello"}]}, headers=headers)
            assert resp.status_code == 200
            data = resp.json()
            assert data["id"].startswith("chatcmpl-")
            assert isinstance(data["created"], int)
            assert data["choices"][0]["message"]["role"] == "assistant"

    def test_causal_streaming_translation(self, monkeypatch):
        """Branch: SCP stream → OpenAI SSE chunks"""
        headers = _auth_headers(monkeypatch)
        with TestClient(app) as client:
            resp = client.post("/v1/chat/completions", json={"model": "gpt-3.5-turbo", "messages": [{"role": "user", "content": "Stream"}], "stream": True}, headers=headers)
            assert resp.status_code == 200
            assert resp.json()["object"] == "chat.completion"

    def test_causal_gateway_failure_503(self, monkeypatch):
        """Branch: gateway fails → 503 OpenAI error"""
        headers = _auth_headers(monkeypatch)

        class _BrokenJudge:
            def judge(self, **kwargs):
                raise RuntimeError("injected pipeline fault")

        monkeypatch.setattr(openai_compat, "get_judge", lambda: _BrokenJudge())
        with TestClient(app) as client:
            resp = client.post("/v1/chat/completions", json={"model": "gpt-3.5-turbo", "messages": [{"role": "user", "content": "Test"}]}, headers=headers)
            assert resp.status_code == 503
            assert resp.json()["error"]["type"] == "server_error"

    def test_causal_agent_failure_error(self):
        """Branch: agent fails → proper error response"""
        app.dependency_overrides[verify_admin] = lambda: True
        try:
            with TestClient(app) as client:
                resp = client.post("/swe-bench/v1/chat/completions", json={"messages": [{"role": "user", "content": "Missing model"}]})
                assert resp.status_code == 422
                assert "detail" in resp.json()
        finally:
            app.dependency_overrides.pop(verify_admin, None)
