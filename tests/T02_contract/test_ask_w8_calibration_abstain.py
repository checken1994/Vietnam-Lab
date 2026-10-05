"""[W8-e3 2026-10-05] Calibration ledger phải TIÊU THỤ verdict ABSTAIN.

Residual W7 (residual c): hook calibration trong _ask_impl chỉ ghi prediction
cho UNKNOWN/PARTIAL/FLAGGED — verdict ABSTAIN (Option A e5/e6) rơi ngoài →
dữ liệu calibration mất một class verdict thật của judge. ABSTAIN là một
prediction có confidence 0 (không adjudicated) — ghi nhận additive, không đụng
fail-closed (không đổi verdict/governance, chỉ record dữ liệu).

Anti-placebo: test chạy trên code TRƯỚC W8-e3 sẽ FAIL (không có row nào với
prediction verdict ABSTAIN trong calibration.sqlite).
"""
from __future__ import annotations

import json
import sqlite3
from unittest.mock import MagicMock

import pytest

from scp.api_server_parts._ask_impl import _ask_impl
from scp.api_server_parts.helpers import AskRequest
from scp.runtime.question_router import LANE_CHATBOT, RouteDecision

_ABSTAIN_ANSWER = "Tôi không thể xác minh thời tiết hiện tại vì không có dữ liệu thời gian thực."


def _force_chatbot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "scp.runtime.question_router.route_question",
        lambda q: RouteDecision(
            intent="REASONING", domain="general", confidence=0.9,
            via="test", lane=LANE_CHATBOT, reason="test",
        ),
    )


def _mock_request() -> MagicMock:
    mock_request = MagicMock()
    mock_request.client.host = "127.0.0.1"
    mock_request.state = MagicMock()
    mock_request.state.scp_run = None
    mock_request.state.trace_id = "test-w8-e3"
    return mock_request


async def _noop_fact_check(*_a, **_k) -> None:
    return None


@pytest.mark.asyncio
async def test_calibration_ledger_records_abstain_prediction(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Chatbot + judge ABSTAIN (đường e6 deliver) → calibration.sqlite phải có
    một prediction với verdict ABSTAIN (class verdict thật của judge)."""
    _force_chatbot(monkeypatch)
    monkeypatch.setenv("SCP_MULTI_LLM_CROSSCHECK", "0")
    monkeypatch.setenv("SCP_WEB_FALLBACK", "0")
    monkeypatch.setenv("SCP_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        "scp.api_server_parts._ask_impl._async_fact_check", _noop_fact_check
    )

    class FakeJudge:
        async def judge_with_react_fallback(self, **kwargs):
            return {
                "verdict": "ABSTAIN",
                "confidence": 0.0,
                "reasoning": "mocked judge",
                "failures": [],
                "evidence": {"governance_decision": "ESCALATE"},
                "slm_responses": [],
                "final_answer": _ABSTAIN_ANSWER,
            }

    monkeypatch.setattr(
        "scp.api_server_parts._ask_impl.get_judge", lambda: FakeJudge()
    )

    req = AskRequest(question="Thời tiết Hà Nội hôm nay thế nào?", ai_answer=_ABSTAIN_ANSWER)
    response = await _ask_impl(req, _mock_request())

    assert response.verdict == "ABSTAIN"
    assert response.governance_decision == "ABSTAIN"

    db_path = tmp_path / "calibration.sqlite"
    assert db_path.exists(), "calibration hook must create the ledger on ABSTAIN"
    with sqlite3.connect(str(db_path)) as conn:
        rows = conn.execute(
            "SELECT prediction_json, task_class, predictor_type FROM calibration_predictions"
        ).fetchall()
    assert rows, "ABSTAIN verdict must be recorded as a calibration prediction"
    predictions = [json.loads(r[0]) for r in rows]
    assert any(p.get("verdict") == "ABSTAIN" for p in predictions), (
        f"no ABSTAIN prediction found in calibration ledger: {predictions}"
    )
    assert all(r[1] == "ask" and r[2] == "judge" for r in rows)
