"""GĐ2 Semantic Router — contract Lane A (claude-review-gd2-spec.md §6, C3, H7).

`SemanticRouteDecision` là SUPERSET của `RouteDecision` hiện có
(`scp/runtime/router_rules.py:140-161`) — mọi field và phương thức đọc mà
các consumer đang dùng (`reason`, `language`, `to_dict()`, `lane`) đều vẫn
truy cập được, NHƯNG:

- KHÔNG có cờ miễn trừ verification nào (C3 — "Lane chỉ quyết định cách trả
  lời, không hạ tier kiểm"). Cờ chết cũ của `RouteDecision` đã bị XÓA hẳn ở
  F-E2E-2 (không consumer nào đọc; khóa bởi test tĩnh F-E2E-2 trong
  tests/T02_contract/) nên contract này cũng không còn mô phỏng nó.
  Constructor từ chối keyword lạ (TypeError).
- `lane` CHỈ nhận đúng 3 giá trị `LANE_SECURITY | LANE_FACTUAL |
  LANE_CHATBOT` (hằng số của router_rules) — giá trị khác bị từ chối ở
  constructor (ValueError, fail-closed).
- Default `lane` đổi từ `LANE_CHATBOT` (fail-open, H7) sang
  `LANE_FACTUAL` (fail-closed — mọi ca mơ hồ đi verification đầy đủ).

File này KHÔNG sửa `RouteDecision`; hai type song song trong giai đoạn GĐ2.
"""
from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any

from scp.runtime.router_rules import (
    LANE_CHATBOT,
    LANE_FACTUAL,
    LANE_SECURITY,
    RouteDecision,
)

#: Tập lane hợp lệ duy nhất của GĐ2 (giá trị lấy từ router_rules — không copy).
VALID_LANES: frozenset[str] = frozenset({LANE_SECURITY, LANE_FACTUAL, LANE_CHATBOT})

#: Lane → intent tương đương hệ thống cũ (LOOKUP/REASONING/SECURITY).
LANE_INTENT: dict[str, str] = {
    LANE_SECURITY: "SECURITY",
    LANE_FACTUAL: "LOOKUP",
    LANE_CHATBOT: "REASONING",
}

#: Tên các field của RouteDecision mà superset này phải phủ.
SUPERSET_FIELDS: frozenset[str] = frozenset(f.name for f in fields(RouteDecision))


@dataclass(frozen=True)
class SemanticRouteDecision:
    """Superset fail-closed của `RouteDecision` cho GĐ2 dual-lineage router.

    So với `RouteDecision` (`router_rules.py:140`): giữ nguyên bộ field
    `intent, domain, confidence, via, reason, lane, language` (cùng thứ tự
    positional), thêm ràng buộc whitelist lane; không có cờ miễn trừ
    verification (xem module docstring).
    """

    intent: str  # LOOKUP | REASONING | SECURITY (từ LANE_INTENT)
    domain: str  # domain hint — 'general' nếu không rõ
    confidence: float
    via: str  # 'arb:security-precedence' | 'arb:degraded-fail-closed' | ...
    reason: str = ""
    # [H7] Default FACTUAL — KHÔNG còn default fail-open LANE_CHATBOT.
    lane: str = LANE_FACTUAL
    language: str = "en"  # "vi" | "en" | "other"

    def __post_init__(self) -> None:
        if self.lane not in VALID_LANES:
            raise ValueError(
                f"SemanticRouteDecision.lane must be one of "
                f"{sorted(VALID_LANES)}, got {self.lane!r}"
            )

    def to_dict(self) -> dict[str, Any]:
        """Cùng shape với `RouteDecision.to_dict()` (router_rules.py:151)."""
        return {
            "intent": str(self.intent),
            "lane": self.lane,
            "language": self.language,
            "confidence": self.confidence,
            "domain": self.domain,
            "via": self.via,
            "reason": self.reason,
        }


__all__ = [
    "LANE_INTENT",
    "SemanticRouteDecision",
    "SUPERSET_FIELDS",
    "VALID_LANES",
]
