"""[W8-e2 2026-10-05] BC-2 harden — conv-tier marker + assertion verb ngoài set.

Checker W7 probe: câu "Hello, birds fly south in winter." đi abstain-delivered
vì (i) chứa conv marker "hello", (ii) KHÔNG chứa copula/claim-cue nào trong
_ABSTAIN_CLAIM_CUE_RE (is/are/was/were/là... đều vắng mặt — "fly" là động từ
thường) → detector chấm là abstain thuần trong khi câu KHẲNG ĐỊNH một sự thật
về thế giới ngoài ("chim bay về phương Nam vào mùa đông").

Fix (Option A tinh thần — conservative): tier hội thoại được phủ thêm
_ABSTAIN_CONV_ASSERTION_RE (assertion-verb closed set: fly/means/bay/nghĩa
là...) → conv marker + động từ khẳng định → conservative False (đường
verify/FAIL giữ nguyên). Chào hỏi thuần / self-report ("I'm fine", "doing
well", "Chào bạn! Mình khỏe") không chứa verb trong set → abstain thật KHÔNG
bị phá (test cũ trong test_judge_w7_abstain.py phải pass nguyên).

Anti-placebo: test (1) chạy trên code TRƯỚC W8-e2 sẽ FAIL (detector trả True).
"""
from __future__ import annotations

import pytest

from scp.runtime.judge import (
    RealityJudge,
    is_honest_abstain_answer,
    question_has_time_signal,
)

_BC2_ANSWER = "Hello, birds fly south in winter."
_GREETING_ANSWER = "Chào bạn! Mình khỏe, cảm ơn bạn đã hỏi. Bạn khỏe không?"


def test_bc2_conv_marker_with_assertion_verb_is_not_abstain() -> None:
    """Old-fails/new-passes: conv marker + động từ khẳng định ngoài marker list
    → conservative False. Trước W8-e2: True (lọt abstain-delivered)."""
    assert is_honest_abstain_answer(_BC2_ANSWER) is False, (
        "conv marker must not mask a world-assertion verb (BC-2)"
    )


def test_bc2_english_conv_assertion_shapes() -> None:
    """Các shape assertion-verb khác cùng class phải bị chặn (closed set, đọc
    được bằng máy — mỗi verb liệt kê tường minh trong _ABSTAIN_CONV_ASSERTION_RE)."""
    assert is_honest_abstain_answer("Hi there! Water means life.") is False
    assert is_honest_abstain_answer("Hello! Birds migrate in winter.") is False


def test_bc2_genuine_greetings_still_abstain() -> None:
    """CHỐNG LỘNG ngược: abstain thật (chào hỏi thuần / self-report) KHÔNG bị
    phá — không verb khẳng định trong câu → vẫn abstain (test cũ W7 giữ nguyên)."""
    assert is_honest_abstain_answer("Hello!")
    assert is_honest_abstain_answer("Xin chào! Mình khỏe.")
    assert is_honest_abstain_answer("Hello! I'm fine, thanks for asking!")
    assert is_honest_abstain_answer(_GREETING_ANSWER)
    assert is_honest_abstain_answer("Doing well, thank you!")


def test_bc2_conv_claim_through_judge_keeps_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """[BC-2 integration] claim bọc conv marker + crosscheck agree-FAIL →
    verdict FAIL (đường verify giữ nguyên), KHÔNG được ABSTAIN-deliver claim."""
    import scp.runtime.judge as judge_module

    monkeypatch.setattr(
        judge_module, "_run_crosscheck_sync",
        lambda *a, **k: {"consensus": "agree", "final": "FAIL", "primary": {}, "secondary": {}},
    )

    verdict = RealityJudge().judge(
        question="Chào bạn, chim có chuyện gì thế?",
        ai_answer=_BC2_ANSWER,
        context="",
    )

    assert verdict["verdict"] == "FAIL", (
        "conv-wrapped claim must keep the FAIL/verify path (BC-2 hardening)"
    )
    assert verdict["evidence"]["governance_decision"] == "ESCALATE"


# ---------------------------------------------------------------------------
# [W8-e1] question_has_time_signal — detector deterministic, closed set.
# ---------------------------------------------------------------------------
def test_time_signal_detector_matches_owner_closed_set() -> None:
    """Signals theo owner-đã-duyệt: hiện tại/hiện nay/hôm nay/bây giờ/
    currently/latest — mỗi signal match tường minh."""
    assert question_has_time_signal("Ai là tổng thống Mỹ hiện tại?")
    assert question_has_time_signal("Ai đang giữ chức vụ đó hiện nay?")
    assert question_has_time_signal("Thời tiết Hà Nội hôm nay thế nào?")
    assert question_has_time_signal("Bạn đang ở đâu bây giờ?")
    assert question_has_time_signal("Who is currently the US president?")
    assert question_has_time_signal("What is the latest news?")


def test_time_signal_detector_rejects_non_time_questions() -> None:
    """Không signal thời gian → False (guard W8-e1 phải tắt trên câu thường).
    'coroutine' chứa chuỗi 'current' nhưng không phải \bcurrently\b."""
    assert question_has_time_signal("Ai là thủ đô Pháp?") is False
    assert question_has_time_signal("Ai là tổng thống Mỹ?") is False
    assert question_has_time_signal("asyncio.run uses a coroutine") is False
    assert question_has_time_signal("") is False
    assert question_has_time_signal(None) is False
