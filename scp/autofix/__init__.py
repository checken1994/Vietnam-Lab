"""
SCP Auto-Fix System — Autonomous bug fixing with logic-change permission gate.

[DESIGN PRINCIPLE] "SCP tìm chỗ sai. Con người quyết định."
Refined by Rule 7 (Distributed Autonomy):
  - Lỗi nhỏ (không ảnh hưởng logic) → SCP tự fix, không cần báo cáo
  - Lỗi logic (cốt lõi) → SCP xin phép qua PermissionGate
  - Autonomous Reviewer (Rule 7) hoạt động như một Subsystem độc lập (Agent A / Subsystem B)
    kiểm duyệt tự động không tạo nút thắt cổ chai con người
  - Khi bị tấn công → SCP tự xử lý (nhanh hơn con người), nhưng chỉ RESTRAINT (thắt chặt)

4 tiers:
  Tier 1: Auto-Fix (no report) — implementation bugs with 1 correct answer
  Tier 2: Auto-Fix + Log — behavior bugs with clear fix
  Tier 3: Permission Required — logic bugs (change WHAT SCP decides)
  Tier 4: Attack Mode — autonomous restraints during active attack
"""
from scp.autofix.classifier import BugClassifier, BugTier
from scp.autofix.engine import AutoFixEngine, get_autofix_engine, reset_autofix_engine
from scp.autofix.permission import PermissionGate
from scp.autofix.autonomous_reviewer import (
    AutonomousReviewer,
    AutonomousReviewerConfig,
    EvaluationVerdict,
    get_autonomous_reviewer,
    reset_autonomous_reviewer,
)

__all__ = [
    "BugClassifier",
    "BugTier",
    "AutoFixEngine",
    "PermissionGate",
    "AutonomousReviewer",
    "AutonomousReviewerConfig",
    "EvaluationVerdict",
    "get_autofix_engine",
    "reset_autofix_engine",
    "get_autonomous_reviewer",
    "reset_autonomous_reviewer",
]
