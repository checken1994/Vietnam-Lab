# SCP CIRCUIT: F-2 — /ask ↔ CanonicalRetriever HTTP contract (Level-C, in-process).
"""T02/F-2 (companion tới tests/T07_learning/test_f2_ask_retrieval_wiring.py).

Chứng minh CẤP-C (end-to-end trong process) của seam auto-retrieval:
request thật đi qua route /ask THẬT → AskKernelAdapter.run_rag THẬT →
auto-retrieve trên FIXTURE corpus → grounding judge THẬT (tier1 + semantic
qua gateway thật tới local OpenAI-compatible fixture provider, pattern
test_flow_02) → canonical verify_response THẬT → response.slm_trace MANG
bằng chứng canonical_bm25 — đúng field mà benchmark
``D_evidence_recall`` đọc (benchmark/run_benchmark_v2.py:607).

Không skip/xfail. Provider chain bị cô lập tuyệt đối (mọi key env bị xóa,
load_openrouter_keys patched rỗng) — traffic chỉ tới 127.0.0.1 fixture
server, không chạm cloud. Không mock scorer/judge/kernel.
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

import scp.rag.canonical_retriever as cr_mod
from scp.rag import retrieval_eval as ev
from scp.rag.canonical_retriever import CanonicalRetriever

from scp.api_server import app

GEO_LOOKUP = "Thủ đô của Pháp là gì"
CORPUS_ANSWER = "Paris là thủ đô của Pháp."
F2_JWT_SECRET = "f2-test-jwt-secret-0123456789abcdef-40chars"


class _LocalOpenAICompatHandler(BaseHTTPRequestHandler):
    """Local fixture provider (pattern T02): judge prompt (chứa 'PASS or
    FAIL') → 'PASS'; chat prompt → đáp án khớp ngữ cảnh fixture corpus."""

    def log_message(self, *args):  # im lặng noise per-request
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        messages = body.get("messages", [])
        is_judge_prompt = any(
            "PASS or FAIL" in str(m.get("content", "")) for m in messages
        )
        content = "PASS" if is_judge_prompt else CORPUS_ANSWER
        payload = json.dumps(
            {"choices": [{"message": {"role": "assistant", "content": content}}]}
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def _start_local_server() -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _LocalOpenAICompatHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.fixture(autouse=True)
def _reset_auth_rate_limit_accounting():
    from scp.security import auth as _auth

    _auth._auth_failures.clear()
    yield
    _auth._auth_failures.clear()


@pytest.fixture()
def fixture_corpus(tmp_path, monkeypatch):
    root = tmp_path / "corpus"
    ev.build_fixture(root)
    monkeypatch.setattr(cr_mod, "_default", CanonicalRetriever(root=root))


def _isolated_ask_env(monkeypatch, tmp_path):
    monkeypatch.setenv("SCP_JWT_SECRET", F2_JWT_SECRET)
    monkeypatch.setenv("SCP_WEB_FALLBACK", "0")
    monkeypatch.setenv("SCP_MULTI_LLM_CROSSCHECK", "0")
    monkeypatch.setenv("SCP_T2_ROUTER", "0")  # data-API fork off → generation+retrieval
    for key in (
        "SCP_ASK_RETRIEVAL",
        "SCP_ASK_RETRIEVAL_K",
        "SCP_ASK_RETRIEVAL_MIN_SCORE",
        "SCP_ASK_RETRIEVAL_TIMEOUT_SECONDS",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("SCP_KERNEL_DB_PATH", str(tmp_path / "f2-http-kernel.sqlite3"))
    monkeypatch.setenv("SCP_KERNEL_TRACE_PATH", str(tmp_path / "f2-http-trace.jsonl"))
    for key in list(os.environ):
        if key.startswith((
            "OPENROUTER_", "GROQ_", "CEREBRAS_", "SAMBANOVA_", "GEMINI_",
            "NVIDIA_", "GITHUB_",
        )) or key in {
            "OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL",
            "SCP_LLM_FALLBACK_PROVIDERS",
        }:
            monkeypatch.delenv(key, raising=False)
    from scp.llm_gateway import client as gw_client
    from scp.llm_gateway.client import OpenRouterProvider

    monkeypatch.setattr(gw_client, "load_openrouter_keys", lambda: [])
    monkeypatch.setattr(OpenRouterProvider, "_API_KEYS", [])
    monkeypatch.setattr(OpenRouterProvider, "_key_cycle", None)
    monkeypatch.setattr(OpenRouterProvider, "_dynamic_models_loaded", True)

    server = _start_local_server()
    port = server.server_address[1]
    monkeypatch.setenv("F2_GW_KEY", "f2-fixture-key")
    monkeypatch.setenv("F2_GW_BASE", f"http://127.0.0.1:{port}")
    monkeypatch.setenv("F2_GW_MODEL", "f2-local-model")
    monkeypatch.setenv(
        "SCP_LLM_FALLBACK_PROVIDERS",
        "openai_compat:F2_GW_KEY:F2_GW_BASE:F2_GW_MODEL",
    )
    monkeypatch.setattr("scp.llm_gateway.client._gateway", None)

    from scp.llm_gateway.zero_cost_runtime import get_runtime_guard

    guard = get_runtime_guard()
    now = datetime.now(timezone.utc)
    guard.proof_store.record(
        provider="openai_compat",
        model="f2-local-model",
        prompt_price=0,
        completion_price=0,
        catalog_hash="f2-fixture-catalog",
        observed_at=now.isoformat(),
        expires_at=(now + timedelta(hours=2)).isoformat(),
        evidence_id="price://f2-local-fixture",
    )

    def _stop():
        server.shutdown()
        server.server_close()

    return _stop


def _wait_until_ready(client: TestClient, timeout_s: int = 180) -> None:
    deadline = time.time() + timeout_s
    last = None
    while time.time() < deadline:
        r = client.get("/readiness")
        last = (r.status_code, r.json().get("status"))
        if r.status_code == 200 and last[1] == "ready":
            return
        time.sleep(1)
    raise AssertionError(f"server never became ready; last readiness={last}")


def test_ask_http_route_surfaces_retrieved_evidence_in_slm_trace(
    monkeypatch, tmp_path, fixture_corpus
):
    stop = _isolated_ask_env(monkeypatch, tmp_path)
    try:
        from scp.security.jwt_guard import create_access_token

        headers = {"Authorization": f"Bearer {create_access_token({'sub': 'f2'})}"}
        with TestClient(app) as client:
            _wait_until_ready(client)

            # 1. LOOKUP thiếu-context → bằng chứng corpus có thật surface vào
            #    slm_trace (benchmark đọc field này), answer vẫn PASS vì nó
            #    ĐƯỢC đỡ bởi đúng evidence đó (tier1 grounding + judge).
            resp = client.post("/ask", json={"question": GEO_LOOKUP}, headers=headers)
            assert resp.status_code == 200
            data = resp.json()
            bm25_entries = [
                e
                for e in (data.get("slm_trace") or [])
                if e.get("slm_name") == "canonical_bm25"
            ]
            assert data["verdict"] == "PASS", data
            assert bm25_entries, "LOOKUP ask phải mang bằng chứng canonical trong slm_trace"
            assert any("Paris" in str(e["evidence"].get("text", "")) for e in bm25_entries)
            assert all(e["source"] == "canonical-corpus" for e in bm25_entries)
            assert all(
                str(e["evidence"].get("source_url", "")).startswith("https://")
                for e in bm25_entries
            )

            # 2. Ambiguous (L0 None) → KHÔNG retrieve — chat giữ hợp đồng cũ,
            #    seam không biến mọi câu hỏi thành RAG ask.
            resp2 = client.post(
                "/ask", json={"question": "hello there my friend"}, headers=headers
            )
            assert resp2.status_code == 200
            trace2 = resp2.json().get("slm_trace") or []
            assert [e for e in trace2 if e.get("slm_name") == "canonical_bm25"] == []

            # 3. Client tự gửi contexts → không double-retrieve (evidence của
            #    client thắng theo hợp đồng begin()/input-hash).
            resp3 = client.post(
                "/ask",
                json={
                    "question": GEO_LOOKUP,
                    "contexts": ["Băng là nguồn khách hàng tự chứng."],
                },
                headers=headers,
            )
            assert resp3.status_code == 200
            trace3 = resp3.json().get("slm_trace") or []
            assert [e for e in trace3 if e.get("slm_name") == "canonical_bm25"] == []
    finally:
        stop()
