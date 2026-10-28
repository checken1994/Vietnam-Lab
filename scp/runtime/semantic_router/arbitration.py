"""GĐ2 Semantic Router — arbitration thuần Lane A (claude-review-gd2-spec.md §3.3, H3, C2/C3).

Hàm thuần `arbitrate(l1, l2, degraded)` gộp hai lineage verdict thành một
`SemanticRouteDecision`. Không I/O, không mạng, không trạng thái module —
đọc env threshold MỖI lần gọi (không cache) để test/ops đổi cấu hình được.

Thứ tự ưu tiên ĐÃ CHỐT trong review (§3.3(2), H3) — mắt xích nào khớp
trước thì thắng, các rule sau không chạy:

  Rule 1  SECURITY — BẤT KỲ lineage còn sống (not None) báo nhãn ánh xạ
          được sang SECURITY → LANE_SECURITY. Không yêu cầu đồng thuận
          đôi, không yêu cầu confidence tối thiểu (fail-closed: thà route
          nhầm sang SECURITY còn hơn lọt một câu tấn công).
  Rule 2  DEGRADED — hạ tầng lineage hỏng/timeout → LANE_FACTUAL,
          bypass False (C2: đích hạ cấp là SAFE_FACTUAL, KHÔNG BAO GIỜ
          LANE_CHATBOT, không bao giờ quay về L0 fail-open).
  Rule 3  CHATBOT — CHỈ khi KHÔNG degraded, CẢ HAI lineage còn sống,
          CẢ HAI báo CHATBOT, và min(conf1, conf2) >= threshold
          (ngưỡng kép — cổng yếu nhất quyết định).
  Rule 4  DEFAULT — mọi ca còn lại → LANE_FACTUAL, bypass False.

BẢNG CHÂN TRỊ ĐẦY ĐỦ (T = chatbot_consensus_threshold(); "chết" = None;
"SEC" = nhãn ánh xạ SECURITY; "CHAT"/"FACT" = nhãn ánh xạ CHATBOT/FACTUAL;
"REAS" = nhãn KHÔNG ánh xạ được — REAS/CODE/CREATIVE/garbage):

| #  | l1          | l2          | degraded | → lane     | rule | reason                   |
|----|-------------|-------------|----------|------------|------|--------------------------|
| 1  | None        | None        | False    | FACTUAL    | 4    | both_lineages_dead       |
| 2  | None        | None        | True     | FACTUAL    | 2    | degraded_fail_closed    |
| 3  | None        | SEC@0.9     | any      | SECURITY   | 1    | security_precedence:l2  |
| 4  | SEC@0.3     | None        | any      | SECURITY   | 1    | security_precedence:l1  |
| 5  | SEC@0.3     | CHAT@0.99   | False    | SECURITY   | 1    | security_precedence:l1  |
| 6  | CHAT@0.99   | SEC@0.3     | False    | SECURITY   | 1    | security_precedence:l2  |
| 7  | SEC@0.9     | CHAT@0.99   | True     | SECURITY   | 1    | security_precedence:l1  |
| 8  | CHAT@0.99   | CHAT@0.99   | True     | FACTUAL    | 2    | degraded_fail_closed    |
| 9  | CHAT@0.9    | CHAT@0.89   | False    | CHATBOT    | 3    | chatbot_dual_consensus  |
| 10 | CHAT@0.95   | FACT@0.95   | False    | FACTUAL    | 4    | insufficient_consensus  |
| 11 | FACT@0.95   | CHAT@0.95   | False    | FACTUAL    | 4    | insufficient_consensus  |
| 12 | CHAT@0.99   | None        | False    | FACTUAL    | 4    | no_l2_no_consensus:*    |
| 13 | None        | CHAT@0.99   | False    | FACTUAL    | 4    | no_l1_no_consensus:*    |
| 14 | CHAT@0.7    | CHAT@0.7    | False    | FACTUAL    | 3/4  | below_threshold (0.7<T) |
| 15 | REAS@0.99   | REAS@0.99   | False    | FACTUAL    | 4    | unmappable_label        |
| 16 | REAS@0.99   | CHAT@0.99   | False    | FACTUAL    | 4    | unmappable_label        |
| 17 | CHAT@0.99   | REAS@0.99   | False    | FACTUAL    | 4    | unmappable_label        |
| 18 | FACT@0.9    | FACT@0.9    | False    | FACTUAL    | 4    | factual_no_consensus_req|
| 19 | None        | FACT@0.9    | False    | FACTUAL    | 4    | no_l1_no_consensus      |

Mọi dòng: `bypass_verdict_pass` LUÔN False (property cố định của contract).
`*` = reason có suffix `:chatbot_single_vote` khi phiếu sống là CHATBOT.
Ghi chú ca biên: #7 chứng minh Rule 1 > Rule 2 (lineage còn sống báo
SECURITY thì DEGRADED không nuốt được — mutant m4 bắn đúng ca này);
#8/#14 bắn mutant m2/m3; #10–#11 bắn mutant m1.
"""
from __future__ import annotations

import os
from typing import Protocol

from scp.runtime.router_rules import LANE_CHATBOT, LANE_FACTUAL, LANE_SECURITY
from scp.runtime.semantic_router.contract import SemanticRouteDecision

# ---------------------------------------------------------------------------
# Ngưỡng CHATBOT — env có FLOOR CLAMP (review M3: "SCP_ROUTER_CHATBOT_THRESHOLD
# cần floor clamp, đọc theo họ control-flag"). Đọc mỗi lần gọi, không cache.
# ---------------------------------------------------------------------------
CHATBOT_THRESHOLD_ENV = "SCP_ROUTER_CHATBOT_THRESHOLD"
#: Ngưỡng mặc định khi env không đặt/không parse được (spec GĐ2: 0.88 kép).
DEFAULT_CHATBOT_CONSENSUS_THRESHOLD = 0.88
#: Sàn cấu hình — env dưới sàn bị kẹp lên sàn; KHÔNG BAO GIỜ hạ về 0.
CHATBOT_THRESHOLD_FLOOR = 0.5


def chatbot_consensus_threshold() -> float:
    """Đọc ngưỡng đồng thuận CHATBOT từ env với floor clamp fail-closed.

    - env không đặt / không phải số → DEFAULT (0.88).
    - env < FLOOR (0.5) → kẹp lên FLOOR (m3: env "0" không thể mở toá CHATBOT).
    - env > 1.0 → kẹp về 1.0 (thắt chặt thì cho phép — CHATBOT vô hiệu hoá).
    """
    raw = os.environ.get(CHATBOT_THRESHOLD_ENV, "").strip()
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_CHATBOT_CONSENSUS_THRESHOLD
    if value < CHATBOT_THRESHOLD_FLOOR:
        return CHATBOT_THRESHOLD_FLOOR
    if value > 1.0:
        return 1.0
    return value


# ---------------------------------------------------------------------------
# Nhãn chung (H3 sửa: "thống nhất một tập nhãn chung")
# ---------------------------------------------------------------------------
#: Chỉ 3 nhãn lane là ánh xạ được. Mọi nhãn khác (REAS/REASONING-code/
#: CREATIVE/garbage/None) → None = KHÔNG ánh xạ được (ca 15–17).
_LABEL_TO_LANE: dict[str, str] = {
    "SECURITY": LANE_SECURITY,
    "LANE_SECURITY": LANE_SECURITY,
    "SEC": LANE_SECURITY,
    "FACTUAL": LANE_FACTUAL,
    "LANE_FACTUAL": LANE_FACTUAL,
    "FACT": LANE_FACTUAL,
    "LOOKUP": LANE_FACTUAL,
    "CHATBOT": LANE_CHATBOT,
    "LANE_CHATBOT": LANE_CHATBOT,
    "CHAT": LANE_CHATBOT,
}


def map_label_to_lane(label: object) -> str | None:
    """Ánh xạ nhãn lineage → lane hợp lệ; nhãn lạ trả None (không bỏ phiếu)."""
    return _LABEL_TO_LANE.get(str(label or "").strip().upper())


# ---------------------------------------------------------------------------
# Structural contract cho lineage verdict (Lane B/C implement; test dùng giả)
# ---------------------------------------------------------------------------
class L1Verdict(Protocol):
    """Lineage 1 (embed) phải có tối thiểu `label` + `confidence`.

    Structural (duck-typed): dataclass của lane B chỉ cần 2 attribute này;
    `language`/`domain` là tùy chọn (đọc qua getattr, thiếu → mặc định).
    """

    label: str
    confidence: float


class L2Verdict(Protocol):
    """Lineage 2 (SLM) — cùng giao diện tối thiểu như L1Verdict."""

    label: str
    confidence: float


# ---------------------------------------------------------------------------
# Helpers thuần
# ---------------------------------------------------------------------------
def _label(v: L1Verdict | L2Verdict) -> str:
    return str(getattr(v, "label", "") or "")


def _conf(v: L1Verdict | L2Verdict) -> float:
    try:
        c = float(getattr(v, "confidence", 0.0))
    except (TypeError, ValueError):
        return 0.0
    return min(max(c, 0.0), 1.0)  # clamp [0,1] — verdict lạ không nổ


def _language(living: list[L1Verdict | L2Verdict]) -> str:
    for v in living:
        lang = str(getattr(v, "language", "") or "").strip()
        if lang:
            return lang
    return "en"


def _domain(living: list[L1Verdict | L2Verdict]) -> str:
    for v in living:
        dom = str(getattr(v, "domain", "") or "").strip()
        if dom:
            return dom
    return "general"


def _clamp(c: float) -> float:
    return min(max(c, 0.0), 1.0)


def _build(
    lane: str,
    intent: str,
    confidence: float,
    via: str,
    reason: str,
    living: list[L1Verdict | L2Verdict],
) -> SemanticRouteDecision:
    return SemanticRouteDecision(
        intent=intent,
        domain=_domain(living),
        confidence=_clamp(confidence),
        via=via,
        reason=reason,
        lane=lane,
        language=_language(living),
    )


# ---------------------------------------------------------------------------
# arbitrate — hàm thuần duy nhất của Lane A
# ---------------------------------------------------------------------------
def arbitrate(
    l1: L1Verdict | None,
    l2: L2Verdict | None,
    degraded: bool = False,
) -> SemanticRouteDecision:
    """Gộp hai lineage verdict → SemanticRouteDecision theo 4 rule ưu tiên.

    Xem BẢNG CHÂN TRỊ đầy đủ (19 ca) trong module docstring. `None` = lineage
    chết/lỗi. Hàm thuần: không I/O (trừ đọc env threshold), không mutate input.
    """
    living = [v for v in (l1, l2) if v is not None]
    l1_lane = map_label_to_lane(_label(l1)) if l1 is not None else None
    l2_lane = map_label_to_lane(_label(l2)) if l2 is not None else None

    # Rule 1 — SECURITY precedence: bất kỳ lineage còn sống báo SECURITY.
    # KHÔNG cần ngưỡng, KHÔNG cần đồng thuận đôi (fail-closed, ca 3–7).
    sec = [
        v
        for v, lane in ((l1, l1_lane), (l2, l2_lane))
        if v is not None and lane == LANE_SECURITY
    ]
    if sec:
        which = "l1" if any(v is l1 for v in sec) else "l2"
        return _build(
            lane=LANE_SECURITY,
            intent="SECURITY",
            confidence=max(_conf(v) for v in sec),
            via="arb:security-precedence",
            reason=f"security_precedence:{which}",
            living=living,
        )

    # Rule 2 — DEGRADED → FACTUAL fail-closed (C2: không bao giờ CHATBOT).
    if degraded:
        return _build(
            lane=LANE_FACTUAL,
            intent="LOOKUP",
            confidence=0.5,
            via="arb:degraded-fail-closed",
            reason="degraded_fail_closed",
            living=living,
        )

    # Rule 3 — CHATBOT chỉ khi CẢ HAI còn sống, CẢ HAI báo CHATBOT,
    # min(conf) >= threshold (ngưỡng kép; ca 9 đạt, ca 14 dưới ngưỡng).
    threshold = chatbot_consensus_threshold()
    if l1 is not None and l2 is not None:
        both_chatbot = l1_lane == LANE_CHATBOT and l2_lane == LANE_CHATBOT
        if both_chatbot:
            joint = min(_conf(l1), _conf(l2))
            if joint >= threshold:
                return _build(
                    lane=LANE_CHATBOT,
                    intent="REASONING",
                    confidence=joint,
                    via="arb:chatbot-dual-consensus",
                    reason=f"chatbot_dual_consensus:min={joint:.2f}",
                    living=living,
                )
            return _build(
                lane=LANE_FACTUAL,
                intent="LOOKUP",
                confidence=joint,
                via="arb:default-factual",
                reason=f"below_threshold:{joint:.2f}<{threshold:.2f}",
                living=living,
            )

    # Rule 4 — DEFAULT FACTUAL (fail-closed): 1 lineage chết + CHATBOT
    # (ca 12–13), nhãn không ánh xạ (ca 15–17), đồng thuận FACTUAL (18),
    # hoặc cả hai chết (ca 1–2).
    if not living:
        return SemanticRouteDecision(
            intent="LOOKUP",
            domain="general",
            confidence=0.5,
            via="arb:default-factual",
            reason="both_lineages_dead",
            lane=LANE_FACTUAL,
            language="en",
        )
    joint = min(_conf(v) for v in living)
    if l1 is None or l2 is None:
        reason = "no_l2_no_consensus" if l2 is None else "no_l1_no_consensus"
        if l1_lane == LANE_CHATBOT or l2_lane == LANE_CHATBOT:
            reason += ":chatbot_single_vote"
    elif l1_lane is None or l2_lane is None:
        reason = "unmappable_label"
    elif LANE_CHATBOT in (l1_lane, l2_lane):
        reason = "insufficient_consensus"  # ca 10–11: có phiếu CHATBOT, không đồng thuận
    else:
        reason = "factual_no_consensus_req"
    return _build(
        lane=LANE_FACTUAL,
        intent="LOOKUP",
        confidence=joint,
        via="arb:default-factual",
        reason=reason,
        living=living,
    )


__all__ = [
    "CHATBOT_THRESHOLD_ENV",
    "CHATBOT_THRESHOLD_FLOOR",
    "DEFAULT_CHATBOT_CONSENSUS_THRESHOLD",
    "L1Verdict",
    "L2Verdict",
    "arbitrate",
    "chatbot_consensus_threshold",
    "map_label_to_lane",
]
