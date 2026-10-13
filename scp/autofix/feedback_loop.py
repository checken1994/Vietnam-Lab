"""AutoFix Feedback Loop adapter (Phase B58).

Re-exports FeedbackContext and SoftFeedbackRemediationLoop for AutoFix engine.
"""
from __future__ import annotations

from scp.task_kernel_parts.feedback_loop import (
    DEFAULT_MAX_ATTEMPTS,
    FeedbackContext,
    RemediationResult,
    SoftFeedbackRemediationLoop,
)

__all__ = [
    "FeedbackContext",
    "RemediationResult",
    "SoftFeedbackRemediationLoop",
    "DEFAULT_MAX_ATTEMPTS",
]
