"""Soft Feedback Loop & Maker-Checker Interactive Remediation (B58).

When the Verifier rejects worker evidence, instead of immediately aborting
and failing closed on the very first attempt, this subsystem provides structured
FeedbackContext (rejection reason, specific error, assertion failed) and allows
the worker up to max_attempts = 3 cycles of self-correction inside an isolated
workspace snapshot. Each failed attempt is cleanly rolled back without dirty side-effects.
"""
from __future__ import annotations

import copy
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("scp.feedback_loop")

DEFAULT_MAX_ATTEMPTS = 3


@dataclass
class FeedbackContext:
    """Structured context passed back to the worker upon verifier rejection."""

    rejection_reason: str
    specific_error: str
    assertion_failed: str
    attempt: int = 1
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    snapshot_id: str | None = None
    feedback_data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rejection_reason": self.rejection_reason,
            "specific_error": self.specific_error,
            "assertion_failed": self.assertion_failed,
            "attempt": self.attempt,
            "max_attempts": self.max_attempts,
            "snapshot_id": self.snapshot_id,
            "feedback_data": copy.deepcopy(self.feedback_data),
        }


@dataclass
class RemediationResult:
    """Outcome of an interactive remediation session."""

    success: bool
    output: Any | None
    attempts_used: int
    rollback_performed: bool
    final_feedback: FeedbackContext | None = None
    remediation_history: list[dict[str, Any]] = field(default_factory=list)


class SoftFeedbackRemediationLoop:
    """Orchestrates interactive maker-checker self-correction cycles."""

    def __init__(self, max_attempts: int = DEFAULT_MAX_ATTEMPTS):
        self.max_attempts = max_attempts

    def run_remediation_loop(
        self,
        worker_fn: Callable[[FeedbackContext | None], Any],
        verifier_fn: Callable[[Any], tuple[bool, str, str, str]],
        workspace_snapshot_mgr: Any | None = None,
        task_id: str | None = None,
    ) -> RemediationResult:
        """Execute up to max_attempts maker-checker cycles in isolated workspace snapshots.

        verifier_fn returns: (is_valid: bool, rejection_reason: str, specific_error: str, assertion_failed: str)
        """
        task_ref = task_id or f"task-{uuid.uuid4().hex[:8]}"
        current_feedback: FeedbackContext | None = None
        history: list[dict[str, Any]] = []

        for attempt in range(1, self.max_attempts + 1):
            snapshot_id = f"snap-{task_ref}-att{attempt}-{uuid.uuid4().hex[:6]}"
            logger.info(
                "[FeedbackLoop] Starting remediation attempt %d/%d for task %s (snapshot %s)",
                attempt, self.max_attempts, task_ref, snapshot_id,
            )

            # 1. Establish isolated workspace snapshot if manager provided
            if workspace_snapshot_mgr is not None and hasattr(workspace_snapshot_mgr, "create_snapshot"):
                try:
                    workspace_snapshot_mgr.create_snapshot(snapshot_id)
                except (OSError, RuntimeError, ValueError) as exc:
                    logger.warning("[FeedbackLoop] Failed creating snapshot %s: %s", snapshot_id, exc)

            # 2. Worker executes with structured feedback from previous rejection
            worker_output = None
            worker_error: BaseException | None = None
            try:
                worker_output = worker_fn(current_feedback)
            except (RuntimeError, ValueError, TypeError, KeyError, AssertionError, OSError) as exc:
                worker_error = exc
                logger.warning("[FeedbackLoop] Worker crashed during attempt %d: %s", attempt, exc)

            # 3. Verify worker output
            if worker_error is not None:
                is_valid = False
                rejection_reason = "worker_execution_exception"
                specific_error = str(worker_error)
                assertion_failed = type(worker_error).__name__
            else:
                try:
                    is_valid, rejection_reason, specific_error, assertion_failed = verifier_fn(worker_output)
                except (RuntimeError, ValueError, TypeError, KeyError, AssertionError, OSError) as exc:
                    is_valid = False
                    rejection_reason = "verifier_execution_exception"
                    specific_error = str(exc)
                    assertion_failed = "verifier_internal_error"

            history.append({
                "attempt": attempt,
                "snapshot_id": snapshot_id,
                "verified": is_valid,
                "rejection_reason": rejection_reason if not is_valid else None,
                "specific_error": specific_error if not is_valid else None,
            })

            # 4. Handle success: commit clean output
            if is_valid:
                logger.info(
                    "[FeedbackLoop SUCCESS] Worker satisfied verifier on attempt %d/%d",
                    attempt, self.max_attempts,
                )
                if workspace_snapshot_mgr is not None and hasattr(workspace_snapshot_mgr, "commit_snapshot"):
                    try:
                        workspace_snapshot_mgr.commit_snapshot(snapshot_id)
                    except (OSError, RuntimeError, ValueError) as exc:
                        logger.warning("[FeedbackLoop] Snapshot commit warning: %s", exc)

                return RemediationResult(
                    success=True,
                    output=worker_output,
                    attempts_used=attempt,
                    rollback_performed=False,
                    remediation_history=history,
                )

            # 5. Handle failure: clean rollback of isolated workspace snapshot
            logger.info(
                "[FeedbackLoop REJECTED] Verifier rejected attempt %d/%d: %s (%s). Rolling back.",
                attempt, self.max_attempts, rejection_reason, specific_error,
            )
            if workspace_snapshot_mgr is not None and hasattr(workspace_snapshot_mgr, "rollback_snapshot"):
                try:
                    workspace_snapshot_mgr.rollback_snapshot(snapshot_id)
                except (OSError, RuntimeError, ValueError) as exc:
                    logger.warning("[FeedbackLoop] Snapshot rollback warning: %s", exc)

            # Construct structured FeedbackContext for next cycle
            current_feedback = FeedbackContext(
                rejection_reason=rejection_reason,
                specific_error=specific_error,
                assertion_failed=assertion_failed,
                attempt=attempt + 1,
                max_attempts=self.max_attempts,
                snapshot_id=snapshot_id,
            )

        # Max attempts exhausted: fail-closed with clean state
        logger.error(
            "[FeedbackLoop EXHAUSTED] Worker failed all %d remediation attempts. Failing closed.",
            self.max_attempts,
        )
        return RemediationResult(
            success=False,
            output=None,
            attempts_used=self.max_attempts,
            rollback_performed=True,
            final_feedback=current_feedback,
            remediation_history=history,
        )


__all__ = [
    "FeedbackContext",
    "RemediationResult",
    "SoftFeedbackRemediationLoop",
    "DEFAULT_MAX_ATTEMPTS",
]
