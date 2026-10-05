"""[W8-e6 2026-10-05] WS chat lane (/chat) tiêu thụ ABSTAIN cùng semantic e6.

Residual W7: /ask có đường benign-ABSTAIN delivery (W7-e6, Option A owner
duyệt) nhưng ws chat lane (scp/api/chat.py) withhold MỌI verdict không-PASS
kể cả ABSTAIN benign → câu chào hỏi/thiếu dữ liệu bị '[SCP: Answer withheld]'
rỗng thay vì deliver kèm nhãn.

Hợp đồng sau W8-e6 (CHỘT theo lane chatbot của ws):
  * verdict ABSTAIN + governance ESCALATE + answer thực có nội dung (không
    rỗng, không 'User Safety:', không 'safe') → deliver frame type 'answer'
    với answer = '[unverified — abstain] ' + answer gốc + governance 'ABSTAIN';
  * mọi shape khác giữ nguyên fail-closed: KILL/REJECT/DENY/DEGRADED, verdict
    FAIL/FLAGGED..., answer rỗng → '[SCP: Answer withheld]' + type 'rejected'.

Hermetic: transport stub ở WebSocket driver boundary (pattern
test_auth_fail_closed_contract), judge/ledger/conversation/candidate stub,
không mạng. Anti-placebo: test (1) chạy trên code TRƯỚC W8-e6 sẽ FAIL
(withheld + type 'rejected').
"""
from __future__ import annotations

import asyncio
import json
import types

import pytest
from fastapi import WebSocketDisconnect

_ANSWER = "Chào bạn! Mình khỏe, cảm ơn bạn đã hỏi."
_ABSTAIN_LABEL = "[unverified — abstain]"


class _ScriptedWS:
    """Transport stub: gửi frame tuần tự rồi ngắt kết nối."""

    def __init__(self, frames: list[str]):
        self.query_params = {"token": "scp-test-token-123", "session_id": "w8sess"}
        self.accepted = False
        self.close_calls: list[int] = []
        self.sent: list[dict] = []
        self._frames = list(frames)

    async def accept(self) -> None:
        self.accepted = True

    async def close(self, code: int = 1000) -> None:
        self.close_calls.append(code)

    async def send_json(self, payload: dict) -> None:
        self.sent.append(payload)

    async def receive_text(self) -> str:
        if self._frames:
            return self._frames.pop(0)
        raise WebSocketDisconnect(code=1000)


class _FakeConversationMgr:
    def get_history(self, session_id: str) -> list[dict]:
        return []

    def add_message(self, *args, **kwargs) -> None:
        return None

    def get_context_string(self, session_id: str) -> str:
        return ""


class _FakeRun:
    ledger_write_ok = True
    run_id = "run-w8-e6"
    trace_id = "trace-w8-e6"


class _FakeLedger:
    def begin(self, request, **metadata) -> _FakeRun:
        return _FakeRun()

    def stage(self, run, stage, status="RUNNING", **fields) -> bool:
        return True

    def finish(self, run, status, result=None, error=None, **fields):
        return (status, True)

    @staticmethod
    def classify_error(exc) -> str:
        return "INTERNAL_FAILED"


def _ws_env(monkeypatch: pytest.MonkeyPatch, judge_result: dict) -> None:
    from scp.api import chat as chat_module
    from scp.runtime.question_router import LANE_CHATBOT, RouteDecision

    monkeypatch.setattr(
        "scp.security.auth_config.load_auth_config",
        lambda: types.SimpleNamespace(configured=True, token="scp-test-token-123", password=""),
    )
    monkeypatch.setattr("scp.api_server._pc_kill_switch_engaged", lambda: False)

    class _FakeJudge:
        def judge(self, **kwargs):
            return dict(judge_result)

    monkeypatch.setattr("scp.api_server.get_judge", lambda: _FakeJudge())

    async def _fake_candidate(user_message, conversation_context):
        return str(judge_result.get("final_answer") or "")

    monkeypatch.setattr(chat_module, "_generate_candidate_answer", _fake_candidate)
    monkeypatch.setattr(chat_module, "_conversation_mgr", _FakeConversationMgr())
    monkeypatch.setattr(chat_module, "_CHAT_LEDGER", _FakeLedger())
    monkeypatch.setattr(
        "scp.runtime.question_router.route_question",
        lambda q: RouteDecision(
            intent="REASONING", domain="general", confidence=0.9,
            via="test", lane=LANE_CHATBOT, reason="test",
        ),
    )


def _answer_frames(ws: _ScriptedWS) -> list[dict]:
    return [f for f in ws.sent if f.get("type") in {"answer", "rejected", "clarification", "error"}]


def test_ws_chatbot_abstain_delivered_with_label(monkeypatch: pytest.MonkeyPatch) -> None:
    """Old-fails/new-passes: chatbot + ABSTAIN + ESCALATE + answer thực →
    deliver kèm nhãn. Trước W8-e6: '[SCP: Answer withheld]' + type 'rejected'."""
    from scp.api import chat as chat_module

    _ws_env(
        monkeypatch,
        {
            "verdict": "ABSTAIN",
            "confidence": 0.0,
            "reasoning": "mocked ws judge",
            "failures": [],
            "evidence": {"governance_decision": "ESCALATE"},
            "slm_responses": [],
            "final_answer": _ANSWER,
        },
    )
    ws = _ScriptedWS([json.dumps({"message": "Chào bạn nhé"})])
    asyncio.run(chat_module.scp_chat(ws))

    frames = _answer_frames(ws)
    assert frames, f"no answer frame sent: {ws.sent}"
    frame = frames[-1]
    assert frame["type"] == "answer", f"benign abstain must be delivered, got {frame}"
    assert frame["verdict"] == "ABSTAIN"
    assert frame["governance"] == "ABSTAIN"
    assert str(frame["answer"]).startswith(_ABSTAIN_LABEL)
    assert "Mình khỏe" in str(frame["answer"]), (
        "original answer must be delivered with the label, not replaced"
    )
    assert "[SCP: Answer withheld" not in str(frame["answer"])


def test_ws_chatbot_kill_governance_still_withheld(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail-closed nguyên vẹn: governance KILL → '[SCP: Answer withheld]' +
    type 'rejected' — W8-e6 KHÔNG deliver, KHÔNG gắn nhãn abstain."""
    from scp.api import chat as chat_module

    _ws_env(
        monkeypatch,
        {
            "verdict": "FAIL",
            "confidence": 0.0,
            "reasoning": "mocked ws judge",
            "failures": [],
            "evidence": {"governance_decision": "KILL"},
            "slm_responses": [],
            "final_answer": _ANSWER,
        },
    )
    ws = _ScriptedWS([json.dumps({"message": "Chào bạn nhé"})])
    asyncio.run(chat_module.scp_chat(ws))

    frames = _answer_frames(ws)
    assert frames, f"no answer frame sent: {ws.sent}"
    frame = frames[-1]
    assert frame["type"] == "rejected"
    assert str(frame["answer"]) == "[SCP: Answer withheld]"
    assert frame["governance"] == "KILL"
    assert _ABSTAIN_LABEL not in str(frame["answer"])


def test_ws_chatbot_abstain_with_empty_answer_stays_withheld(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail-closed: ABSTAIN + ESCALATE nhưng KHÔNG có answer thực (candidate
    rỗng) → KHÔNG deliver frame nhãn rỗng — withheld như cũ."""
    from scp.api import chat as chat_module

    _ws_env(
        monkeypatch,
        {
            "verdict": "ABSTAIN",
            "confidence": 0.0,
            "reasoning": "mocked ws judge",
            "failures": [],
            "evidence": {"governance_decision": "ESCALATE"},
            "slm_responses": [],
            "final_answer": "",
        },
    )
    ws = _ScriptedWS([json.dumps({"message": "Chào bạn nhé"})])
    asyncio.run(chat_module.scp_chat(ws))

    frames = _answer_frames(ws)
    assert frames, f"no answer frame sent: {ws.sent}"
    frame = frames[-1]
    assert frame["type"] == "rejected"
    assert _ABSTAIN_LABEL not in str(frame["answer"])
