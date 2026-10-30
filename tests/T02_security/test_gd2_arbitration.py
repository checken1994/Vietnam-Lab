"""GĐ2 Lane A — test arbitration thuần + contract (claude-review-gd2-spec.md §6 lane A).

Lineage GIẢ (dataclass tuple-style) — chỉ chứng minh LOGIC ARBITRATION, không
claim gì về lineage thật (FA-04: không VERIFIED từ mock).

4 test bắn mutant m1–m4 của review §4.3 (mỗi mutant phải làm test ĐỎ rồi file
được khôi phục nguyên byte — quy trình chạy ngoài test file này):
  - m1 (AND→OR)         : test_chatbot_requires_both_votes
  - m2 (DEGRADED→CHATBOT): test_degraded_routes_factual_fail_closed
  - m3 (threshold env=0) : test_threshold_floor_clamp
  - m4 (bỏ security precedence): test_security_beats_degraded
"""
from __future__ import annotations

from dataclasses import FrozenInstanceError, dataclass, fields

import pytest

from scp.runtime.router_rules import (
    LANE_CHATBOT,
    LANE_FACTUAL,
    LANE_SECURITY,
    RouteDecision,
)
from scp.runtime.semantic_router.arbitration import (
    CHATBOT_THRESHOLD_ENV,
    arbitrate,
    chatbot_consensus_threshold,
    map_label_to_lane,
)
from scp.runtime.semantic_router.contract import (
    LANE_INTENT,
    SemanticRouteDecision,
    SUPERSET_FIELDS,
    VALID_LANES,
)


# --- lineage GIẢ — duck-typed theo Protocol L1Verdict/L2Verdict -------------
@dataclass(frozen=True)
class FakeVerdict:
    """Verdict giả tối giản: đúng 2 attribute bắt buộc + 2 tùy chọn."""

    label: str
    confidence: float
    language: str = ""
    domain: str = ""


def V(label: str, conf: float, language: str = "vi") -> FakeVerdict:
    return FakeVerdict(label=label, confidence=conf, language=language)


@pytest.fixture(autouse=True)
def _clean_threshold_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Xoá env threshold ambient — test mặc định dùng ngưỡng default 0.88."""
    monkeypatch.delenv(CHATBOT_THRESHOLD_ENV, raising=False)


# =============================================================================
# 1) CONTRACT — superset của RouteDecision (C3, H7)
# =============================================================================
class TestContractSuperset:
    def test_superset_fields_cover_route_decision(self) -> None:
        sem_fields = {f.name for f in fields(SemanticRouteDecision)}
        # Mọi field của RouteDecision phải có mặt — KHÔNG còn ngoại lệ nào
        # (F-E2E-2 xóa cờ bypass chết khỏi RouteDecision; C3 giữ nguyên nghĩa).
        assert SUPERSET_FIELDS - sem_fields == set()
        assert isinstance(VALID_LANES, frozenset)
        assert VALID_LANES == {LANE_SECURITY, LANE_FACTUAL, LANE_CHATBOT}

    def test_bypass_verdict_pass_fixed_false_and_unsettable(self) -> None:
        d = SemanticRouteDecision(
            intent="REASONING", domain="general", confidence=0.9, via="t",
            lane=LANE_CHATBOT,
        )
        # (mọi lane) không có cờ bypass — kể cả lane CHATBOT (C3; F-E2E-2 xóa hẳn)
        assert not hasattr(d, "bypass_verdict_pass")
        assert "bypass_verdict_pass" not in d.to_dict()
        with pytest.raises((AttributeError, FrozenInstanceError)):
            d.bypass_verdict_pass = True  # type: ignore[misc]
        with pytest.raises(TypeError):
            SemanticRouteDecision(  # type: ignore[call-arg]
                intent="REASONING", domain="general", confidence=0.9,
                via="t", bypass_verdict_pass=True,
            )

    def test_lane_whitelist_rejects_invalid(self) -> None:
        for bad in ("LANE_FOO", "security", "LANE_CHATBOT ", ""):
            with pytest.raises(ValueError, match="lane"):
                SemanticRouteDecision(
                    intent="LOOKUP", domain="general", confidence=0.5,
                    via="t", lane=bad,
                )

    def test_default_lane_is_factual_not_fail_open_chatbot(self) -> None:
        d = SemanticRouteDecision(
            intent="LOOKUP", domain="general", confidence=0.5, via="t",
        )
        assert d.lane == LANE_FACTUAL  # H7: không còn default fail-open CHATBOT

    def test_to_dict_same_shape_as_route_decision(self) -> None:
        d = SemanticRouteDecision(
            intent="SECURITY", domain="general", confidence=0.9, via="t",
            reason="r", lane=LANE_SECURITY, language="vi",
        )
        legacy = RouteDecision(
            intent="SECURITY", domain="general", confidence=0.9, via="t",
            reason="r", lane=LANE_SECURITY, language="vi",
        ).to_dict()
        assert set(d.to_dict()) == set(legacy)
        assert LANE_INTENT[LANE_SECURITY] == "SECURITY"
        assert LANE_INTENT[LANE_FACTUAL] == "LOOKUP"
        assert LANE_INTENT[LANE_CHATBOT] == "REASONING"


# =============================================================================
# 2) BẢNG CHÂN TRỊ 19 CA (module docstring của arbitration.py)
# =============================================================================
TRUTH_TABLE: tuple[tuple[str, float | None, str, float | None, bool, str, str], ...] = (
    # (l1_label, l1_conf, l2_label, l2_conf, degraded, expected_lane, expected_reason_prefix)
    # noqa: E501 — bảng chân trị, giữ nguyên thứ tự docstring #1–#19
    (None, None, None, None, False, LANE_FACTUAL, "both_lineages_dead"),        # 1
    (None, None, None, None, True, LANE_FACTUAL, "degraded_fail_closed"),       # 2
    (None, None, "SEC", 0.9, False, LANE_SECURITY, "security_precedence"),      # 3
    ("SEC", 0.3, None, None, False, LANE_SECURITY, "security_precedence"),      # 4
    ("SEC", 0.3, "CHAT", 0.99, False, LANE_SECURITY, "security_precedence"),    # 5
    ("CHAT", 0.99, "SEC", 0.3, False, LANE_SECURITY, "security_precedence"),    # 6
    ("SEC", 0.9, "CHAT", 0.99, True, LANE_SECURITY, "security_precedence"),     # 7
    ("CHAT", 0.99, "CHAT", 0.99, True, LANE_FACTUAL, "degraded_fail_closed"),   # 8
    ("CHAT", 0.9, "CHAT", 0.89, False, LANE_CHATBOT, "chatbot_dual_consensus"), # 9
    ("CHAT", 0.95, "FACT", 0.95, False, LANE_FACTUAL, "insufficient_consensus"),# 10
    ("FACT", 0.95, "CHAT", 0.95, False, LANE_FACTUAL, "insufficient_consensus"),# 11
    ("CHAT", 0.99, None, None, False, LANE_FACTUAL, "no_l2_no_consensus"),     # 12
    (None, None, "CHAT", 0.99, False, LANE_FACTUAL, "no_l1_no_consensus"),     # 13
    ("CHAT", 0.7, "CHAT", 0.7, False, LANE_FACTUAL, "below_threshold"),        # 14
    ("REAS", 0.99, "REAS", 0.99, False, LANE_FACTUAL, "unmappable_label"),     # 15
    ("REAS", 0.99, "CHAT", 0.99, False, LANE_FACTUAL, "unmappable_label"),     # 16
    ("CHAT", 0.99, "REAS", 0.99, False, LANE_FACTUAL, "unmappable_label"),     # 17
    ("FACT", 0.9, "FACT", 0.9, False, LANE_FACTUAL, "factual_no_consensus_req"),# 18
    (None, None, "FACT", 0.9, False, LANE_FACTUAL, "no_l1_no_consensus"),      # 19
)


def _mk(label: str | None, conf: float | None) -> FakeVerdict | None:
    if label is None:
        return None
    assert conf is not None
    return V(label, conf)


class TestTruthTable:
    @pytest.mark.parametrize(
        "l1_label,l1_conf,l2_label,l2_conf,degraded,exp_lane,exp_reason",
        TRUTH_TABLE,
        ids=[f"row{i + 1}" for i in range(len(TRUTH_TABLE))],
    )
    def test_row(
        self,
        l1_label: str | None,
        l1_conf: float | None,
        l2_label: str | None,
        l2_conf: float | None,
        degraded: bool,
        exp_lane: str,
        exp_reason: str,
    ) -> None:
        d = arbitrate(_mk(l1_label, l1_conf), _mk(l2_label, l2_conf), degraded)
        assert isinstance(d, SemanticRouteDecision)
        assert d.lane == exp_lane, f"reason={d.reason!r} via={d.via!r}"
        # C3: bypass cố định False ở MỌI lane, kể cả CHATBOT
        assert not hasattr(d, "bypass_verdict_pass")
        assert "bypass_verdict_pass" not in d.to_dict()
        assert d.reason.startswith(exp_reason), d.reason
        assert d.intent == LANE_INTENT[d.lane]
        assert 0.0 <= d.confidence <= 1.0
        assert isinstance(d.language, str) and d.language

    def test_arbitrate_is_pure(self) -> None:
        l1, l2 = V("CHAT", 0.9), V("CHAT", 0.92)
        d1 = arbitrate(l1, l2, False)
        d2 = arbitrate(l1, l2, False)
        assert d1 == d2  # cùng input → cùng output (dataclass eq)
        assert (l1.label, l1.confidence) == ("CHAT", 0.9)  # input không bị mutate
        assert (l2.label, l2.confidence) == ("CHAT", 0.92)


# =============================================================================
# 3) MUTANT-TARGETED — mỗi test bắn đúng 1 mutant m1–m4 (§4.3)
# =============================================================================
class TestMutantTargets:
    def test_security_any_living_lineage_wins(self) -> None:
        """Rule 1: 1 lineage sống báo SECURITY là đủ, không cần ngưỡng."""
        assert arbitrate(None, V("SEC", 0.9)).lane == LANE_SECURITY
        assert arbitrate(V("SEC", 0.3), None, True).lane == LANE_SECURITY
        # security với confidence THẤP vẫn thắng consensus CHATBOT cao
        d = arbitrate(V("SEC", 0.3), V("CHAT", 0.99), False)
        assert d.lane == LANE_SECURITY
        assert not hasattr(d, "bypass_verdict_pass")

    def test_security_beats_degraded(self) -> None:
        """[m4] Rule 1 > Rule 2: bỏ security precedence → test này ĐỎ.

        degraded=True nhưng lineage sống báo SECURITY → vẫn SECURITY,
        không bị nuốt bởi nhánh DEGRADED→FACTUAL.
        """
        d = arbitrate(V("SEC", 0.9), V("CHAT", 0.99), True)
        assert d.lane == LANE_SECURITY, (
            "SECURITY precedence phải thắng DEGRADED (rule 1 > rule 2)"
        )
        assert d.reason.startswith("security_precedence")
        # m4 cũng bắn ca này (degraded + security từ l2)
        assert arbitrate(V("CHAT", 0.99), V("SEC", 0.3), True).lane == LANE_SECURITY

    def test_degraded_routes_factual_fail_closed(self) -> None:
        """[m2] DEGRADED trả CHATBOT → test này ĐỎ (C2: đích hạ cấp SAFE_FACTUAL)."""
        d = arbitrate(V("CHAT", 0.99), V("CHAT", 0.99), True)
        assert d.lane == LANE_FACTUAL, "DEGRADED phải hạ FACTUAL, không bao giờ CHATBOT"
        assert d.reason == "degraded_fail_closed"
        assert not hasattr(d, "bypass_verdict_pass")
        assert d.via == "arb:degraded-fail-closed"

    def test_chatbot_dual_consensus(self) -> None:
        """Rule 3: cả hai CHATBOT + min(conf) >= 0.88 → CHATBOT (bypass vẫn False)."""
        d = arbitrate(V("CHAT", 0.9), V("CHAT", 0.89), False)
        assert d.lane == LANE_CHATBOT
        assert not hasattr(d, "bypass_verdict_pass")  # C3: CHATBOT không cấp bypass
        assert d.reason.startswith("chatbot_dual_consensus")
        # dưới ngưỡng default 0.88 → FACTUAL
        assert arbitrate(V("CHAT", 0.8), V("CHAT", 0.9), False).lane == LANE_FACTUAL

    def test_chatbot_requires_both_votes(self) -> None:
        """[m1] CHATBOT đổi AND thành OR → test này ĐỎ.

        Một lineage CHATBOT@0.95 + lineage kia FACTUAL@0.95 → KHÔNG đồng thuận
        → FACTUAL. (OR mutant sẽ cho CHATBOT vì l1 đơn lẻ đủ.)
        """
        d = arbitrate(V("CHAT", 0.95), V("FACT", 0.95), False)
        assert d.lane == LANE_FACTUAL, "CHATBOT cần CẢ HAI phiếu (AND), không phải OR"
        d2 = arbitrate(V("FACT", 0.95), V("CHAT", 0.95), False)
        assert d2.lane == LANE_FACTUAL

    def test_single_chatbot_vote_insufficient(self) -> None:
        """1 lineage chết → không bao giờ đủ đồng thuận CHATBOT."""
        assert arbitrate(V("CHAT", 0.99), None, False).lane == LANE_FACTUAL
        assert arbitrate(None, V("CHAT", 0.99), False).lane == LANE_FACTUAL

    def test_threshold_floor_clamp(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """[m3] threshold env=0 (bỏ floor clamp) → test này ĐỎ (M3).

        env "0" phải bị kẹp lên sàn 0.5: consensus 0.3 < 0.5 → FACTUAL.
        Mutant bỏ clamp → threshold 0 → 0.3 >= 0 → CHATBOT → đỏ.
        """
        monkeypatch.setenv(CHATBOT_THRESHOLD_ENV, "0")
        assert chatbot_consensus_threshold() == 0.5
        d = arbitrate(V("CHAT", 0.3), V("CHAT", 0.3), False)
        assert d.lane == LANE_FACTUAL, "env=0 không được mở toá CHATBOT (floor 0.5)"
        # env hợp lệ > floor thì được dùng bình thường
        monkeypatch.setenv(CHATBOT_THRESHOLD_ENV, "0.6")
        assert chatbot_consensus_threshold() == 0.6
        assert arbitrate(V("CHAT", 0.65), V("CHAT", 0.7), False).lane == LANE_CHATBOT
        assert arbitrate(V("CHAT", 0.55), V("CHAT", 0.7), False).lane == LANE_FACTUAL


# =============================================================================
# 4) Ngưỡng + nhãn — hành vi biên
# =============================================================================
class TestThresholdAndLabels:
    def test_threshold_env_parse(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assert chatbot_consensus_threshold() == 0.88  # default (env đã del ở fixture)
        monkeypatch.setenv(CHATBOT_THRESHOLD_ENV, "garbage")
        assert chatbot_consensus_threshold() == 0.88
        monkeypatch.setenv(CHATBOT_THRESHOLD_ENV, "2.0")
        assert chatbot_consensus_threshold() == 1.0  # thắt chặt thì clamp 1.0
        monkeypatch.setenv(CHATBOT_THRESHOLD_ENV, "-5")
        assert chatbot_consensus_threshold() == 0.5  # sàn

    def test_map_label_to_lane(self) -> None:
        assert map_label_to_lane("SECURITY") == LANE_SECURITY
        assert map_label_to_lane("security") == LANE_SECURITY  # case-insensitive
        assert map_label_to_lane("LANE_FACTUAL") == LANE_FACTUAL
        assert map_label_to_lane("LOOKUP") == LANE_FACTUAL
        assert map_label_to_lane("CHATBOT") == LANE_CHATBOT
        # nhãn không ánh xạ được (H3: REAS/CODE/CREATIVE) → None
        assert map_label_to_lane("REAS") is None
        assert map_label_to_lane("REASONING") is None
        assert map_label_to_lane("CODE") is None
        assert map_label_to_lane("CREATIVE") is None
        assert map_label_to_lane("") is None
        assert map_label_to_lane(None) is None
        assert map_label_to_lane("junk") is None

    def test_verdict_confidence_out_of_range_is_clamped(self) -> None:
        """Verdict giả confidence ngoài [0,1] không làm nổ hàm thuần."""
        d = arbitrate(FakeVerdict(label="CHAT", confidence=7.0), V("CHAT", 0.9), False)
        assert d.lane == LANE_CHATBOT
        assert d.confidence == 0.9  # min(7→clamp 1.0, 0.9)
