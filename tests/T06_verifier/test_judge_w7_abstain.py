"""[W7-e5] Judge 3-state {PASS, FAIL, ABSTAIN} — Option A (GA.md B1b, owner duyệt).

Root-cause W6-RE: judge nhị phân không có class ABSTAIN → câu benign không
verify được (chào hỏi q06, thời tiết realtime q07) bị chấm FAIL → withheld;
ESCALATE-flip q03/q05 = inherent variance của đường verify.

Hợp đồng sau e5 (scp/runtime/judge.py):
  * answer là lời từ chối/abstain trung thực (không chứa factual claim cần
    verify) → verdict ABSTAIN + governance ESCALATE (audit giữ nguyên),
    KHÔNG bị chấm FAIL;
  * crosscheck consensus missing trên câu benign → ABSTAIN + ESCALATE
    (thiếu opinion độc lập ≠ FAIL — không ai adjudicated answer);
  * answer CHỨA factual claim → giữ nguyên đường PASS/FAIL. CHỐNG LỘNG:
    ABSTAIN không được phép thay thế FAIL cho answer có claim (test riêng).
  * KILL (REJECT_INTERNAL_MARKER), DEGRADED, disagree→UNKNOWN, REJECT_EMPTY
    → FAIL: toàn bộ giữ nguyên hành vi W3-e1.

Anti-placebo: các test (1)-(3) chạy trên code TRƯỚC e5 sẽ FAIL (verdict
FAIL/UNKNOWN thay vì ABSTAIN). Không gọi LLM — crosscheck inject qua seam.
"""
from __future__ import annotations

import pytest

from scp.runtime.judge import (
    RealityJudge,
    is_honest_abstain_answer,
    is_refusal_abstain_answer,
)

_QUESTION = "Thời tiết Hà Nội hôm nay thế nào?"
_CLAIM_ANSWER = "Theo dữ liệu được cung cấp, câu trả lời là Donald Trump."
_CLAIM_Q = "Ai là tổng thống Mỹ hiện tại?"
_REFUSAL_ANSWER = (
    "Tôi không thể xác minh thời tiết hiện tại vì không có dữ liệu thời gian thực."
)
_GREETING_ANSWER = "Chào bạn! Mình khỏe, cảm ơn bạn đã hỏi. Bạn khỏe không?"


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch: pytest.MonkeyPatch) -> None:
    """Không chạm mạng: không expert injection, không KB consult."""
    monkeypatch.setattr(
        "scp.data_sources.domain_classifier.classify_top1", lambda _q: "__no_domain__"
    )
    monkeypatch.setattr(
        RealityJudge, "_consult_knowledge", lambda self, question, limit=3: []
    )
    monkeypatch.setenv("SCP_MULTI_LLM_CROSSCHECK", "1")


def _agree_fail_crosscheck() -> dict:
    """Crosscheck consensus 'agree' trên FAIL — benign không verify được."""
    return {"consensus": "agree", "final": "FAIL", "primary": {}, "secondary": {}}


def test_sync_judge_honest_refusal_answer_verdict_abstain_not_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Old-fails/new-passes: refusal benign + semantic agree-FAIL.

    OLD (nhị phân): verdict FAIL → withheld. NEW: verdict ABSTAIN +
    governance ESCALATE (audit giữ nguyên), KHÔNG bị chấm FAIL."""
    import scp.runtime.judge as judge_module

    monkeypatch.setattr(
        judge_module, "_run_crosscheck_sync", lambda *a, **k: _agree_fail_crosscheck()
    )

    verdict = RealityJudge().judge(
        question=_QUESTION, ai_answer=_REFUSAL_ANSWER, context=""
    )

    assert verdict["verdict"] == "ABSTAIN", (
        "honest refusal must be ABSTAIN, not FAIL (old behavior)"
    )
    assert verdict["evidence"]["governance_decision"] == "ESCALATE"
    assert "abstain_reasons" in verdict["evidence"]
    assert "answer_without_verifiable_claim" in verdict["evidence"]["abstain_reasons"]
    assert verdict["final_answer"] == _REFUSAL_ANSWER


@pytest.mark.asyncio
async def test_async_judge_honest_refusal_answer_verdict_abstain_not_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Đường async (judge_async — đường /ask thật qua judge_with_react_fallback)
    phải cùng hợp đồng: refusal benign → ABSTAIN, không FAIL."""
    import scp.runtime.multi_llm_crosscheck as crosscheck_module

    async def _agree_fail(*_a, **_k):
        return _agree_fail_crosscheck()

    monkeypatch.setattr(crosscheck_module, "cross_verify", _agree_fail)

    verdict = await RealityJudge().judge_async(
        question=_QUESTION, ai_answer=_REFUSAL_ANSWER, context=""
    )

    assert verdict["verdict"] == "ABSTAIN"
    assert verdict["evidence"]["governance_decision"] == "ESCALATE"
    assert "answer_without_verifiable_claim" in verdict["evidence"]["abstain_reasons"]


def test_sync_judge_greeting_smalltalk_no_claim_verdict_abstain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """q06 shape: answer hội thoại xã giao (không claim bên ngoài) + semantic
    agree-FAIL → ABSTAIN. OLD: FAIL (nguyên nhân q06 bị withhold)."""
    import scp.runtime.judge as judge_module

    monkeypatch.setattr(
        judge_module, "_run_crosscheck_sync", lambda *a, **k: _agree_fail_crosscheck()
    )

    verdict = RealityJudge().judge(
        question="Chào bạn, dạo này bạn thế nào?", ai_answer=_GREETING_ANSWER, context=""
    )

    assert verdict["verdict"] == "ABSTAIN"
    assert verdict["evidence"]["governance_decision"] == "ESCALATE"


def test_sync_judge_factual_claim_answer_keeps_fail_not_abstain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CHỐNG LỘNG (anti-lộng): answer chứa factual claim + semantic agree-FAIL
    → verdict PHẢI vẫn FAIL (không lọt ABSTAIN). ABSTAIN không được phép thay
    thế FAIL cho answer có khẳng định kiểm chứng được."""
    import scp.runtime.judge as judge_module

    monkeypatch.setattr(
        judge_module, "_run_crosscheck_sync", lambda *a, **k: _agree_fail_crosscheck()
    )

    verdict = RealityJudge().judge(
        question=_CLAIM_Q, ai_answer=_CLAIM_ANSWER, context=""
    )

    assert verdict["verdict"] == "FAIL", (
        "ABSTAIN must never replace FAIL for an answer carrying a factual claim"
    )
    assert verdict["evidence"]["governance_decision"] == "ESCALATE"
    assert "semantic_judge_fail" in verdict["failures"]


def test_sync_judge_factual_claim_answer_pass_keeps_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Claim answer + crosscheck agree PASS → PASS giữ nguyên (e5 không đụng
    đường PASS, không hạ PASS thành ABSTAIN)."""
    import scp.runtime.judge as judge_module

    monkeypatch.setattr(
        judge_module,
        "_run_crosscheck_sync",
        lambda *a, **k: {"consensus": "agree", "final": "PASS", "primary": {}, "secondary": {}},
    )

    verdict = RealityJudge().judge(
        question=_CLAIM_Q, ai_answer=_CLAIM_ANSWER, context=""
    )

    assert verdict["verdict"] == "PASS"
    assert verdict["evidence"]["governance_decision"] == "UPHOLD"


def test_sync_judge_consensus_missing_benign_verdict_abstain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Crosscheck consensus missing trên câu benign → ABSTAIN (thay UNKNOWN):
    thiếu opinion độc lập không phải FAIL cũng không phải "model bất đồng" —
    là abstain có lý do. Governance vẫn ESCALATE (fail-closed giữ nguyên ở
    boundary)."""
    import scp.runtime.judge as judge_module

    monkeypatch.setattr(
        judge_module,
        "_run_crosscheck_sync",
        lambda *a, **k: {
            "consensus": "missing_distinct_providers",
            "final": None,
            "primary": {},
            "secondary": {},
        },
    )

    verdict = RealityJudge().judge(
        question=_CLAIM_Q, ai_answer=_CLAIM_ANSWER, context=""
    )

    assert verdict["verdict"] == "ABSTAIN"
    assert verdict["evidence"]["governance_decision"] == "ESCALATE"
    assert "crosscheck_consensus_missing" in verdict["evidence"]["abstain_reasons"]


def test_sync_judge_disagreement_still_unknown_not_abstain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Consensus DISAGREE + claim answer giữ nguyên UNKNOWN (pin W3-e1/T05
    không bị ABSTAIN nuốt mất) — e5 chỉ đổi nhánh missing, không đổi disagree."""
    import scp.runtime.judge as judge_module

    monkeypatch.setattr(
        judge_module,
        "_run_crosscheck_sync",
        lambda *a, **k: {"consensus": "disagree", "final": None, "primary": {}, "secondary": {}},
    )

    verdict = RealityJudge().judge(
        question=_CLAIM_Q, ai_answer=_CLAIM_ANSWER, context=""
    )

    assert verdict["verdict"] == "UNKNOWN"
    assert verdict["evidence"]["governance_decision"] == "ESCALATE"
    assert "multi_llm_disagreement" in verdict["failures"]


def test_sync_judge_security_marker_answer_never_abstain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Anti-lộng KILL: tamper marker (REJECT_INTERNAL_MARKER) → KILL, verdict
    FAIL — security class không bao giờ bị hạ thành ABSTAIN."""
    import scp.runtime.judge as judge_module

    def _must_not_run(*_a, **_k):
        raise AssertionError("tier-1 tamper rejection must short-circuit tier-2")

    monkeypatch.setattr(judge_module, "_run_crosscheck_sync", _must_not_run)

    verdict = RealityJudge().judge(
        question=_QUESTION,
        ai_answer="[SCP: internal-marker injected content]",
        context="",
    )

    assert verdict["verdict"] == "FAIL"
    assert "REJECT_INTERNAL_MARKER" in verdict["failures"]
    assert verdict["evidence"]["governance_decision"] == "KILL"


def test_sync_judge_empty_answer_still_fail_not_abstain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """REJECT_EMPTY giữ nguyên FAIL (pin W3-e1): rỗng không phải lời từ chối
    trung thực — detector phải trả False cho empty."""
    assert is_honest_abstain_answer("") is False

    verdict = RealityJudge().judge(question=_QUESTION, ai_answer="")

    assert verdict["verdict"] == "FAIL"
    assert "REJECT_EMPTY" in verdict["failures"]
    assert verdict["evidence"]["governance_decision"] == "ESCALATE"


def test_sync_judge_degraded_path_not_reclassified_abstain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DEGRADED (crosscheck raise → fallback cascade) giữ nguyên class kể cả
    khi answer là refusal — degraded là lớp quan sát riêng (SEC-R2-01)."""
    import scp.runtime.judge as judge_module

    def _broken_gateway():
        raise RuntimeError("gateway unavailable (fault injection)")

    monkeypatch.setattr("scp.llm_gateway.get_gateway", _broken_gateway)
    monkeypatch.setattr(judge_module, "_llm_judge", lambda *a, **k: False)

    verdict = RealityJudge().judge(
        question=_QUESTION, ai_answer=_REFUSAL_ANSWER, context=""
    )

    assert verdict["verdict"] == "DEGRADED"
    assert verdict["degraded"] is True
    assert verdict["evidence"]["governance_decision"] == "DEGRADED"


# ---------------------------------------------------------------------------
# Detector unit contract (deterministic, máy đọc — không cảm tính).
# ---------------------------------------------------------------------------
def test_detector_refusal_shapes() -> None:
    assert is_refusal_abstain_answer(
        "Tôi không có thông tin realtime về thời tiết hôm nay."
    )
    assert is_refusal_abstain_answer("I don't have data on that.")
    # Refusal kèm payload số liệu/URL → KHÔNG còn là abstain thuần.
    assert is_refusal_abstain_answer("Tôi không thể xác minh (HTTP 404).") is False
    assert is_refusal_abstain_answer("Xem https://example.com — không có dữ liệu.") is False
    assert is_refusal_abstain_answer("") is False


def test_detector_conv_shapes_and_claim_guard() -> None:
    assert is_honest_abstain_answer(_GREETING_ANSWER)
    assert is_honest_abstain_answer("Hello! I'm fine, thanks for asking!")
    # Anti-lộng detector: copula-assertion / claim payload phủ định conv-tier.
    assert is_honest_abstain_answer("Cảm ơn bạn. Thủ đô Pháp là Paris.") is False
    assert is_honest_abstain_answer(_CLAIM_ANSWER) is False
    assert is_honest_abstain_answer("Được nâng cấp vào năm 2024.") is False


# ---------------------------------------------------------------------------
# [W11-f2 2026-10-06] Marker gap 'chưa có dữ liệu' — evidence W10 battery
# (reports/scp_acceptance_ci/wave10_battery/): answer mô hình dùng đúng cụm
# này nhưng _ABSTAIN_REFUSAL_MARKERS chỉ có 'không có dữ liệu' / 'chưa đủ
# dữ liệu' / 'không đủ dữ liệu' → detector False → FAIL/UNKNOWN thay ABSTAIN
# — bất nhất nghĩa với 'không có dữ liệu' (cùng ý nghĩa, khác 1 từ):
#   * server_log_runA.log:277 (q08): 'Hiện tại tôi chưa có dữ liệu cập nhật
#     về tổng thống Mỹ.' → crosscheck agree-FAIL → verdict FAIL withheld;
#   * server_log_runB.log:217 (q07-weather): 'Xin lỗi, hiện tại tôi chưa có
#     dữ liệu thời tiết trực tiếp để trả lời.' → verdict UNKNOWN withheld.
# Anti-placebo: test (1) và (3) chạy TRƯỚC f2 → FAIL (detector False /
# verdict FAIL).
# ---------------------------------------------------------------------------
def test_detector_w11_chua_co_du_lieu_consistent_with_khong_co() -> None:
    """Pin bất nhất W10: 'chưa có dữ liệu' và 'không có dữ liệu' phải cùng
    verdict abstain trên cùng shape câu (probe thật run A q08 + run B)."""
    assert is_refusal_abstain_answer(
        "Hiện tại tôi chưa có dữ liệu cập nhật về tổng thống Mỹ."
    )
    assert is_refusal_abstain_answer(
        "Hiện tại tôi không có dữ liệu cập nhật về tổng thống Mỹ."
    )
    assert is_refusal_abstain_answer(
        "Xin lỗi, hiện tại tôi chưa có dữ liệu thời tiết trực tiếp để trả lời."
    )


def test_detector_w11_new_marker_respects_bc1_assertion_guard() -> None:
    """BC-1 giữ nguyên cho marker mới: refusal 'chưa có dữ liệu' + assertion
    sau marker = claim bọc wrapper từ chối → False (đường verify)."""
    assert (
        is_refusal_abstain_answer(
            "Tôi chưa có dữ liệu — Donald Trump là tổng thống Mỹ."
        )
        is False
    )


def test_sync_judge_w11_chua_co_du_lieu_answer_abstain_not_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Judge-level (W10 run A q08 shape — crosscheck agree-FAIL): answer dùng
    'chưa có dữ liệu' phải được chấm như refusal trung thực → ABSTAIN,
    KHÔNG FAIL — nhất quán với answer dùng 'không có dữ liệu' (cùng class)."""
    import scp.runtime.judge as judge_module

    monkeypatch.setattr(
        judge_module, "_run_crosscheck_sync", lambda *a, **k: _agree_fail_crosscheck()
    )

    verdict = RealityJudge().judge(
        question="Ai là tổng thống Mỹ hiện tại?",
        ai_answer="Hiện tại tôi chưa có dữ liệu cập nhật về tổng thống Mỹ.",
        context="",
    )

    assert verdict["verdict"] == "ABSTAIN", (
        "'chưa có dữ liệu' là refusal trung thực — cùng nghĩa với "
        "'không có dữ liệu' nên cùng class abstain (W10 run A q08: FAIL "
        "withheld là bất nhất marker-gap)"
    )
    assert verdict["evidence"]["governance_decision"] == "ESCALATE"
    assert "answer_without_verifiable_claim" in verdict["evidence"]["abstain_reasons"]


# ---------------------------------------------------------------------------
# [W7-hardening 2026-10-06] BC-1/BC-3 regression pins — probe từ checker
# độc lập (kc_f_bypass_probe.txt, reports/scp_acceptance_ci/wave7/).
# ---------------------------------------------------------------------------
def test_detector_hardening_bc1_claim_wrapped_in_refusal() -> None:
    """[BC-1] refusal wrapper + assertion sau refusal = claim, KHÔNG abstain.
    Checker probe: 3 shape này từng lọt detector → ABSTAIN deliver claim."""
    assert (
        is_refusal_abstain_answer(
            "Tôi không thể xác minh — Donald Trump là tổng thống Mỹ."
        )
        is False
    )
    assert (
        is_refusal_abstain_answer(
            "I do not have data on that, but the president is Donald Trump."
        )
        is False
    )
    assert (
        is_honest_abstain_answer(
            "Tôi không thể tra cứu thời tiết — trời đang mưa tại Hà Nội."
        )
        is False
    )


def test_detector_hardening_topic_mention_still_abstain() -> None:
    """[BC-1 không phá abstain thật]: topic-mention sau refusal (không có
    assertion verb) vẫn là abstain hợp lệ."""
    assert is_refusal_abstain_answer("Tôi không có dữ liệu về thủ đô.")
    assert is_refusal_abstain_answer("Chưa có cập nhật về thời tiết.")


def test_sync_judge_bc1_claim_wrapped_refusal_keeps_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    """[BC-1 integration] claim bọc refusal + crosscheck agree-FAIL → verdict
    FAIL (không ABSTAIN deliver claim qua nhãn)."""
    import scp.runtime.judge as judge_module

    monkeypatch.setattr(
        judge_module, "_run_crosscheck_sync", lambda *a, **k: _agree_fail_crosscheck()
    )

    verdict = RealityJudge().judge(
        question=_CLAIM_Q,
        ai_answer="Tôi không thể xác minh — Donald Trump là tổng thống Mỹ.",
        context="",
    )

    assert verdict["verdict"] == "FAIL", (
        "claim wrapped in refusal must keep the FAIL/verify path (BC-1 hardening)"
    )
    assert verdict["evidence"]["governance_decision"] == "ESCALATE"


def test_sync_judge_bc3_disagreement_with_refusal_shape_still_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """[BC-3] disagreement + abstain-shaped answer → UNKNOWN (không ABSTAIN):
    '2 opinion bất nhất' không được đổi class theo shape answer."""
    import scp.runtime.judge as judge_module

    monkeypatch.setattr(
        judge_module,
        "_run_crosscheck_sync",
        lambda *a, **k: {
            "consensus": "disagree",
            "final": None,
            "primary": {},
            "secondary": {},
        },
    )

    verdict = RealityJudge().judge(
        question=_QUESTION, ai_answer=_REFUSAL_ANSWER, context=""
    )

    assert verdict["verdict"] == "UNKNOWN"
    assert "multi_llm_disagreement" in verdict["failures"]


@pytest.mark.asyncio
async def test_async_judge_bc3_disagreement_with_refusal_shape_still_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """[W11-f3] BC-3 async parity — judge_async THIẾU exclusion
    multi_llm_disagreement mà sync path đã có (BC-3 hardening W8): disagreement
    + abstain-shaped answer trên đường async → ABSTAIN thay UNKNOWN. Đồng bộ
    với sync: '2 opinion bất nhất' không được đổi class theo shape answer
    (contract W3-e1/T05). Anti-placebo: chạy TRƯỚC f3 → FAIL (ABSTAIN)."""
    import scp.runtime.multi_llm_crosscheck as crosscheck_module

    async def _disagree_crosscheck(*_a, **_k):
        return {
            "consensus": "disagree",
            "final": None,
            "primary": {},
            "secondary": {},
        }

    monkeypatch.setattr(crosscheck_module, "cross_verify", _disagree_crosscheck)

    verdict = await RealityJudge().judge_async(
        question=_QUESTION, ai_answer=_REFUSAL_ANSWER, context=""
    )

    assert verdict["verdict"] == "UNKNOWN", (
        "async path phải cùng exclusion BC-3 với sync: disagreement + "
        "abstain-shaped → UNKNOWN, không ABSTAIN"
    )
    assert verdict["evidence"]["governance_decision"] == "ESCALATE"
    assert "multi_llm_disagreement" in verdict["failures"]


# ---------------------------------------------------------------------------
# [W16-g1 2026-10-07 — BC-E1/E2/E3 harden lần 3] ĐẢO CHIỀU detector (khuyến
# nghị checker độc lập). Closed-set assertion-verb đã fail 2 lần (BC-2 "fly"
# → nay "revolves"/"tăng" — whack-a-mole không chấp nhận):
#   * Tier conv: thay "conv marker + KHÔNG match cue → abstain" bằng whitelist
#     self-report fullmatch (_ABSTAIN_CONV_WHITELIST_RE) — mọi nội dung khác
#     → False (verify path).
#   * Tier refusal: prefix + mọi post-marker segment phải safe theo grammar
#     đóng (_refusal_segment_is_safe); copula guard BC-1 giữ làm belt.
# Anti-placebo: các test (1)-(3) chạy trên code TRƯỚC g1 sẽ FAIL (detector
# trả True → PoC lọt ABSTAIN-delivery kèm nhãn).
# ---------------------------------------------------------------------------
def test_detector_w16g1_bce1_refusal_wrapped_bare_claim_not_abstain() -> None:
    """[BC-E1] refusal marker + post-refusal KHÔNG copula ("giá cổ phiếu tăng"
    — "tăng" ngoài _ABSTAIN_ASSERTION_RE cũ) → vẫn phải False. Trước g1: True
    (bypass sống — deliver claim kèm nhãn abstain)."""
    assert (
        is_refusal_abstain_answer("Tôi chưa có dữ liệu, nhưng giá cổ phiếu tăng")
        is False
    )
    assert (
        is_honest_abstain_answer("Tôi chưa có dữ liệu, nhưng giá cổ phiếu tăng")
        is False
    )
    assert (
        is_refusal_abstain_answer(
            "I don't have data on that, but the stock price rises"
        )
        is False
    )
    # Variants cùng class: bare claim không cần copula, trước lẫn trong sau
    # marker, có/không connector — inversion chặn bằng grammar, không bằng set.
    assert is_refusal_abstain_answer("Tôi chưa có dữ liệu, nhưng FPT tăng") is False
    assert is_refusal_abstain_answer("Tôi chưa có dữ liệu. Giá cổ phiếu tăng.") is False
    assert is_refusal_abstain_answer("Giá cổ phiếu FPT tăng. Tôi chưa có dữ liệu.") is False


def test_detector_w16g1_bce2_conv_marker_world_claim_not_abstain() -> None:
    """[BC-E2] conv marker + claim thế giới ngoài NGOÀI mọi closed set cũ
    ("revolves"/"tăng" — set BC-2 không có) → False. "emits" không nằm trong
    _ABSTAIN_CONV_ASSERTION_RE lẫn _ABSTAIN_CLAIM_CUE_RE — inversion phải
    chặn mà không phụ thuộc việc liệt kê verb."""
    assert is_honest_abstain_answer("Hello! The earth revolves around the sun.") is False
    assert is_honest_abstain_answer("Xin chào! Cổ phiếu FPT đang tăng mạnh.") is False
    assert is_honest_abstain_answer("Hello! Water means life.") is False
    assert is_honest_abstain_answer("Hello, birds fly south in winter.") is False
    # Verb hoàn toàn mới, ngoài mọi closed set — inversion không whack-a-mole.
    assert is_honest_abstain_answer("Hello! The sun emits light.") is False
    # Self-report whitelist + claim ghép chung câu → fullmatch fail → False.
    assert (
        is_honest_abstain_answer("Hello! I'm fine. The earth revolves around the sun.")
        is False
    )


def test_detector_w16g1_bce3_refusal_wrapped_stale_nondigit_claim_not_abstain() -> None:
    """[BC-E3] stale claim bọc refusal KHÔNG digit ("tổng thống mới đã nhậm
    chức" — digits đã chặn sẵn shape có năm; shape không-digit từng lọt
    post-refusal guard copula) → False."""
    assert (
        is_refusal_abstain_answer(
            "Tôi chưa có dữ liệu, nhưng tổng thống mới đã nhậm chức"
        )
        is False
    )
    assert (
        is_honest_abstain_answer(
            "Tôi chưa có dữ liệu, nhưng tổng thống mới đã nhậm chức"
        )
        is False
    )


def test_detector_w16g1_genuine_abstain_not_broken_by_inversion() -> None:
    """CHỐNG LỘNG ngược: inversion KHÔNG được phá abstain thật — refusal
    thuần / topic-mention / greeting self-report thuần vẫn True; claim-cue
    ("Cảm ơn bạn. Thủ đô Pháp là Paris.") vẫn False."""
    assert is_refusal_abstain_answer(
        "Tôi không có dữ liệu realtime về thời tiết."
    )
    assert is_refusal_abstain_answer("I don't have data on that.")
    assert is_refusal_abstain_answer("không có dữ liệu về thủ đô")
    assert is_refusal_abstain_answer(
        "Tôi không thể xác minh thời tiết hiện tại vì không có dữ liệu thời gian thực."
    )
    assert is_refusal_abstain_answer(
        "Tôi chưa được cập nhật dữ liệu thời gian thực để trả lời câu hỏi này."
    )
    assert is_honest_abstain_answer(_GREETING_ANSWER)
    assert is_honest_abstain_answer("Hello! I'm fine, thanks for asking!")
    assert is_honest_abstain_answer("Xin chào! Mình khỏe.")
    assert is_honest_abstain_answer("Hello!")
    assert is_honest_abstain_answer("Doing well, thank you!")
    # Claim-cue giữ False trên cả inversion (whitelist không fullmatch + cue).
    assert is_honest_abstain_answer("Cảm ơn bạn. Thủ đô Pháp là Paris.") is False


def test_sync_judge_w16g1_bce1_bce2_claim_wrapped_keeps_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """[BC-E1/E2 integration] claim bọc refusal/conv marker + crosscheck
    agree-FAIL → verdict FAIL (đường verify giữ nguyên) — KHÔNG được
    ABSTAIN-deliver claim kèm nhãn qua lane FACTUAL."""
    import scp.runtime.judge as judge_module

    monkeypatch.setattr(
        judge_module, "_run_crosscheck_sync", lambda *a, **k: _agree_fail_crosscheck()
    )

    for wrapped in (
        "Tôi chưa có dữ liệu, nhưng giá cổ phiếu tăng",  # BC-E1
        "Hello! The earth revolves around the sun.",  # BC-E2
    ):
        verdict = RealityJudge().judge(question=_CLAIM_Q, ai_answer=wrapped, context="")
        assert verdict["verdict"] == "FAIL", (
            f"claim wrapped in refusal/conv marker must keep the FAIL/verify "
            f"path (BC-E hardening lần 3): {wrapped!r}"
        )
        assert verdict["evidence"]["governance_decision"] == "ESCALATE"
