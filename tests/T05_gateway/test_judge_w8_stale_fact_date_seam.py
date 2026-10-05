"""[W8-e1 2026-10-05] q08 stale-fact — judge/cascade system prompt gắn NGÀY HIỆN TẠI.

Root-cause (GA.md B1b, W7-battery run C): "Joe Biden là tổng thống Mỹ hiện
tại" được uphold PASS-thuần vì:
  1. system prompt của judge cascade (_llm_judge) và crosscheck KHÔNG có ngày
     hiện tại → LLM judge chấm theo general knowledge với training cutoff cũ
     (thời điểm cutoff, claim còn đúng);
  2. crosscheck dedupe chỉ theo (base_url, model) — hai family KHÁC lineage
     vẫn có thể cùng stale cutoff = cùng blind spot trên trục thời gian
     (DNA #5 residual: độc lập provider ≠ độc lập kiến thức).

Fix (a): system prompt gắn ngày hiện tại qua seam `_current_date()` —
monkeypatch NGÀY GIẢ trong test (hermetic, KHÔNG dùng os.environ).

Anti-placebo: các test dưới đây chạy trên code TRƯỚC W8-e1 sẽ FAIL (không có
seam `_current_date` / prompt không chứa ngày).
"""
from __future__ import annotations

import pytest

from scp.runtime import judge_llm
from scp.runtime.judge_llm import _judge_system_prompt

_FAKE_DATE = "2030-01-01"


@pytest.fixture()
def fake_date(monkeypatch: pytest.MonkeyPatch) -> str:
    """Seam ngày giả — fixture hermetic (không os.environ)."""
    monkeypatch.setattr(judge_llm, "_current_date", lambda: _FAKE_DATE)
    return _FAKE_DATE


def test_judge_system_prompt_embeds_fake_current_date(fake_date: str) -> None:
    """Old-fails/new-passes: trước W8-e1 prompt tĩnh, không có ngày hiện tại.
    Sau fix: ngày từ seam PHẢI xuất hiện trong system prompt của judge."""
    prompt = _judge_system_prompt()
    assert f"Current date: {fake_date}" in prompt, (
        "judge system prompt must embed the current date from the seam"
    )
    # Hợp đồng PASS/FAIL gốc giữ nguyên (không hạ chuẩn prompt judge).
    assert "You MUST output exactly the word PASS or FAIL" in prompt
    # Contract stale-fact: hướng dẫn chấm claim time-sensitive theo ngày trên.
    assert "time-sensitive" in prompt.lower()


def test_judge_system_prompt_seam_is_called_dynamically(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Seam phải được gọi TẠI LÚC DÙNG (mỗi lần build prompt đọc ngày mới),
    không phải giá trị đóng băng lúc import — pin bằng 2 ngày giả khác nhau."""
    monkeypatch.setattr(judge_llm, "_current_date", lambda: "2030-01-01")
    first = _judge_system_prompt()
    monkeypatch.setattr(judge_llm, "_current_date", lambda: "2031-06-15")
    second = _judge_system_prompt()
    assert "Current date: 2030-01-01" in first
    assert "Current date: 2031-06-15" in second


@pytest.mark.asyncio
async def test_crosscheck_sends_dated_judge_system_prompt(
    fake_date: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cascade crosscheck phải dùng CÙNG system prompt có ngày (root-cause 2:
    2 opinion cùng stale cutoff đồng thuận PASS trên claim stale). Provider
    stub capture system_prompt thật được gửi đi."""
    captured: list[str] = []

    class _FakeProvider:
        PROVIDER_NAME = "fake"
        base_url = "https://fake.example/v1"
        model = "fake-model-1"
        enabled = True

        async def chat(self, prompt: str, system_prompt: str = "", **_kw):
            captured.append(system_prompt)
            return "PASS", "fake"

    class _FakeGateway:
        def provider_candidates(self, _task: str):
            return [_FakeProvider()]

    from scp.runtime.multi_llm_crosscheck import cross_verify

    result = await cross_verify("q", "a", "", gateway=_FakeGateway())

    assert captured, "crosscheck must call provider.chat with a system prompt"
    for system in captured:
        assert f"Current date: {fake_date}" in system, (
            "crosscheck system prompt must carry the current date (W8-e1)"
        )
    assert system == _judge_system_prompt()
    # Một provider duy nhất → không đủ 2 family độc lập → fail-closed như cũ.
    assert result["consensus"] == "missing_distinct_providers"
    assert result["final"] is None
