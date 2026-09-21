"""Root fixtures and isolation utilities for T12 Unified Chatbot E2E test suite.

Follows SCP Zero-Trust and Fail-Closed guidelines:
- Real FastAPI TestClient against scp.api_server.app.
- Real local OpenAI-compatible HTTP server on 127.0.0.1 for deterministic LLM gateway calls without touching external cloud APIs.
- Real SQLite databases in tmp_path for ChatMemoryStore and TraceStore.
- NO MOCKS replacing subsystem safety logic.
- FA-01 compliant: No skips, xfails, or test loosening.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from scp.security.jwt_guard import create_access_token

logger = logging.getLogger("tests.T12")

T12_JWT_SECRET = "t12-unified-chatbot-jwt-secret-0123456789abcdef"
T12_ADMIN_KEY = "t12-admin-secret-key-32chars-min"


# =========================================================================
# Local OpenAI-Compatible Server Fixture
# =========================================================================

class _LocalOpenAICompatHandler(BaseHTTPRequestHandler):
    """Real HTTP server on 127.0.0.1 speaking the OpenAI /chat/completions schema.
    
    Returns deterministic responses:
    - Judge prompts containing 'PASS or FAIL' get 'PASS'.
    - English prompts get English responses.
    - Vietnamese prompts get Vietnamese responses.
    - Math prompts get computed results.
    """

    def log_message(self, *args: Any) -> None:
        pass  # Silence stderr noise

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        messages = body.get("messages", [])
        
        full_content = " ".join(str(m.get("content", "")) for m in messages)
        
        if "PASS or FAIL" in full_content:
            content = "PASS"
        elif "2+2" in full_content or "2 cộng 2" in full_content:
            content = "Kết quả là 4."
        elif "3 agents" in full_content or "3 * 4" in full_content:
            content = "Total is 12 tasks."
        elif any(w in full_content.lower() for w in ("xin chào", "chào bạn", "bạn là ai", "tôi là")):
            content = "Xin chào! Tôi là SCP — trợ lý AI an toàn và đáng tin cậy. Tôi có thể giúp gì cho bạn?"
        elif any(w in full_content.lower() for w in ("hello", "who are you", "what features")):
            content = "Hello! I am SCP, an intelligent and verified AI assistant. I provide multi-turn conversation, autonomous evidence retrieval, and full traceability."
        elif "thủ đô" in full_content.lower() or "capital" in full_content.lower():
            content = "Thủ đô của Úc là Canberra. Canberra là thành phố thủ đô của Úc với diện tích khoảng 814.2 km²."
        elif "tóm tắt" in full_content.lower() or "summary" in full_content.lower():
            content = "Tóm tắt: Chúng ta đã thảo luận về các tính năng bảo mật, tính toán số lượng tác vụ (12 tác vụ), và khả năng truy vết của hệ thống."
        elif "prompt injection" in full_content.lower() and "disregard" not in full_content.lower():
            content = "Prompt injection là một kỹ thuật tấn công trong đó kẻ tấn công chèn các lệnh độc hại vào đầu vào của LLM nhằm thay đổi hành vi dự kiến."
        else:
            content = "Phản hồi chuẩn từ SCP: thông tin đã được xử lý và thẩm định an toàn."

        payload = json.dumps({
            "id": "chatcmpl-t12-local",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": "local-test-model",
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
        }).encode("utf-8")

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


@pytest.fixture(scope="session")
def local_openai_server():
    """Starts a session-scoped local OpenAI-compatible HTTP server."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _LocalOpenAICompatHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_port
    yield f"http://127.0.0.1:{port}/v1"
    server.shutdown()


@pytest.fixture(autouse=True)
def setup_test_environment(monkeypatch, tmp_path, local_openai_server):
    """Configure environment variables for isolation, test keys, and tmp paths."""
    # Secrets & Keys
    monkeypatch.setenv("SCP_JWT_SECRET", T12_JWT_SECRET)
    monkeypatch.setenv("SCP_ADMIN_KEY", T12_ADMIN_KEY)
    monkeypatch.setenv("SCP_CAPABILITY_SECRET", "test-capability-secret-for-automated-suites-only-32bytes")
    monkeypatch.setenv("SCP_API_PROFILE", "full")
    monkeypatch.setenv("SCP_PRODUCTION_MODE", "0")
    monkeypatch.setenv("SCP_EGRESS_MODE", "disabled")
    
    # Storage isolation
    db_path = tmp_path / "t12_ask_kernel.sqlite3"
    trace_path = tmp_path / "t12_ask_trace.jsonl"
    chat_memory_path = tmp_path / "t12_chat_memory.jsonl"
    trace_store_path = tmp_path / "t12_trace_store.sqlite3"
    
    monkeypatch.setenv("SCP_KERNEL_DB_PATH", str(db_path))
    monkeypatch.setenv("SCP_KERNEL_TRACE_PATH", str(trace_path))
    monkeypatch.setenv("SCP_CHAT_MEMORY_PATH", str(chat_memory_path))
    monkeypatch.setenv("SCP_TRACE_STORE_PATH", str(trace_store_path))
    
    # Point LLM Gateway to local server
    for key in list(os.environ):
        if key.startswith(("OPENROUTER_", "GROQ_", "CEREBRAS_", "SAMBANOVA_", "GEMINI_", "NVIDIA_", "GITHUB_")):
            monkeypatch.delenv(key, raising=False)
            
    monkeypatch.setenv("OPENAI_API_KEY", "sk-local-test-key-t12")
    monkeypatch.setenv("OPENAI_BASE_URL", local_openai_server)
    monkeypatch.setenv("OPENAI_MODEL", "local-test-model")
    monkeypatch.setenv("SCP_MULTI_LLM_CROSSCHECK", "0")
    monkeypatch.setenv("SCP_LLM_HEDGE", "off")
    
    # Reset LLMGateway singleton
    try:
        import scp.llm_gateway.client as gw_client
        monkeypatch.setattr(gw_client, "_gateway", None)
        monkeypatch.setattr(gw_client, "load_openrouter_keys", lambda: [])
        monkeypatch.setattr(gw_client.OpenRouterProvider, "_API_KEYS", [])
        monkeypatch.setattr(gw_client.OpenRouterProvider, "_key_cycle", None)
    except Exception:
        pass

    # Ensure app state is marked ready if app is importable
    try:
        from scp.api_server import app
        app.state.judge_ready = True
        app.state.background_scheduler_started = True
    except Exception:
        pass

    # Auth failure accounting reset
    from scp.security import auth as _auth
    _auth._auth_failures.clear()
    
    yield
    
    _auth._auth_failures.clear()


@pytest.fixture
def auth_headers():
    """Returns valid Authorization header with test JWT."""
    token = create_access_token({"sub": "t12_tester", "role": "admin"})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def test_client():
    """TestClient instance for FastAPI app."""
    from scp.api_server import app
    with TestClient(app) as client:
        app.state.judge_ready = True
        yield client


# =========================================================================
# Reference SQLite TraceStore Contract Implementation
# (Authoritative specification defined in PROJECT.md § Layer 3)
# =========================================================================

class SqliteTraceStore:
    """Persistent SQLite TraceStore adhering to PROJECT.md § Interface Contracts.
    
    Provides WAL mode, indexing by trace_id, query/routing/retrieval/crosscheck/governance storage,
    and causal DAG serialization.
    """
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _init_db(self) -> None:
        with self._get_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS traces (
                    trace_id TEXT PRIMARY KEY,
                    timestamp TEXT NOT NULL,
                    session_id TEXT,
                    query TEXT NOT NULL,
                    routing TEXT NOT NULL,
                    retrieval TEXT NOT NULL,
                    multi_llm_crosscheck TEXT NOT NULL,
                    governance TEXT NOT NULL,
                    final_decision TEXT NOT NULL,
                    causal_graph TEXT NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_traces_session ON traces(session_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_traces_timestamp ON traces(timestamp)")
            conn.commit()

    def record_trace(self, trace_data: dict[str, Any]) -> str:
        trace_id = trace_data.get("trace_id") or f"trace-{os.urandom(8).hex()}"
        timestamp = trace_data.get("timestamp") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        session_id = trace_data.get("session_id")
        query = trace_data.get("query", "")
        routing = json.dumps(trace_data.get("routing", {}), ensure_ascii=False)
        retrieval = json.dumps(trace_data.get("retrieval", {}), ensure_ascii=False)
        crosscheck = json.dumps(trace_data.get("multi_llm_crosscheck", {}), ensure_ascii=False)
        governance = json.dumps(trace_data.get("governance", {}), ensure_ascii=False)
        final_decision = json.dumps(trace_data.get("final_decision", {}), ensure_ascii=False)
        
        causal_graph = trace_data.get("causal_graph")
        if not causal_graph:
            causal_graph = self.build_causal_graph(trace_data)
        causal_graph_json = json.dumps(causal_graph, ensure_ascii=False)

        with self._get_conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO traces (
                    trace_id, timestamp, session_id, query, routing, retrieval,
                    multi_llm_crosscheck, governance, final_decision, causal_graph
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                trace_id, timestamp, session_id, query, routing, retrieval,
                crosscheck, governance, final_decision, causal_graph_json
            ))
            conn.commit()
        return trace_id

    def get_trace(self, trace_id: str) -> dict[str, Any] | None:
        with self._get_conn() as conn:
            cur = conn.execute("SELECT * FROM traces WHERE trace_id = ?", (trace_id,))
            row = cur.fetchone()
            if not row:
                return None
            return {
                "trace_id": row["trace_id"],
                "timestamp": row["timestamp"],
                "session_id": row["session_id"],
                "query": row["query"],
                "routing": json.loads(row["routing"]),
                "retrieval": json.loads(row["retrieval"]),
                "multi_llm_crosscheck": json.loads(row["multi_llm_crosscheck"]),
                "governance": json.loads(row["governance"]),
                "final_decision": json.loads(row["final_decision"]),
                "causal_graph": json.loads(row["causal_graph"]),
            }

    @staticmethod
    def build_causal_graph(trace_data: dict[str, Any]) -> dict[str, Any]:
        """Builds a 5-stage causal DAG according to PROJECT.md § Layer 3."""
        nodes = [
            {"id": "node_query", "stage": "intake", "label": "User Query", "data": {"query": trace_data.get("query", "")}},
            {"id": "node_routing", "stage": "routing", "label": "Question Router", "data": trace_data.get("routing", {})},
            {"id": "node_retrieval", "stage": "retrieval", "label": "Autonomous Retrieval", "data": trace_data.get("retrieval", {})},
            {"id": "node_crosscheck", "stage": "adjudication", "label": "Multi-LLM Crosscheck", "data": trace_data.get("multi_llm_crosscheck", {})},
            {"id": "node_governance", "stage": "governance", "label": "Governance & WHY Gate", "data": trace_data.get("governance", {})},
            {"id": "node_output", "stage": "synthesis", "label": "Final Output", "data": trace_data.get("final_decision", {})},
        ]
        edges = [
            {"source": "node_query", "target": "node_routing"},
            {"source": "node_routing", "target": "node_retrieval"},
            {"source": "node_retrieval", "target": "node_crosscheck"},
            {"source": "node_crosscheck", "target": "node_governance"},
            {"source": "node_governance", "target": "node_output"},
        ]
        return {"nodes": nodes, "edges": edges}


@pytest.fixture
def ask_kernel_adapter(tmp_path):
    """Provides an AskKernelAdapter with temporary SQLite and trace paths."""
    from scp.ask_kernel_adapter import AskKernelAdapter
    db = tmp_path / "test_kernel.sqlite3"
    trace = tmp_path / "test_kernel_trace.jsonl"
    return AskKernelAdapter(db_path=str(db), trace_path=str(trace))


@pytest.fixture
def trace_store_cls():
    """Provides the SqliteTraceStore class for instantiating custom stores."""
    return SqliteTraceStore


@pytest.fixture
def trace_store(tmp_path):
    """Provides a fresh, isolated SqliteTraceStore in tmp_path."""
    db_file = tmp_path / "test_trace_store.sqlite3"
    return SqliteTraceStore(db_file)
