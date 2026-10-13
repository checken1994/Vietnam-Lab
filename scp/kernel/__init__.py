"""SCP Kernel Domain Package (Phase B60 Domain-Driven Layout).

Provides canonical domain-level access to the SCP Task Kernel subsystem,
including state machine transitions, storage engine, lease management,
checkpointing, recovery reconciliation, and commit approval.

Backward compatibility:
- All symbols are identical to and backed by `scp.task_kernel_parts` and `scp.kernel_storage`.
"""
from __future__ import annotations

from scp.kernel_storage import KernelStorage, SQLiteKernelStorage, StorageIntegrityError
from scp.task_kernel_parts.checkpoint import CheckpointEngine
from scp.task_kernel_parts.commit_approval import CommitApprovalEngine
from scp.task_kernel_parts.definitions import (
    ALLOWED_TRANSITIONS,
    STATES,
    TERMINAL,
    CheckpointCorrupt,
    InvalidTransition,
    KernelError,
    KillSwitchActive,
    Lease,
    NotFound,
    OptimisticLockError,
    RecoveryDecision,
    StaleLease,
)
from scp.task_kernel_parts.feedback_loop import (
    DEFAULT_MAX_ATTEMPTS,
    FeedbackContext,
    RemediationResult,
    SoftFeedbackRemediationLoop,
)
from scp.task_kernel_parts.idempotency import IdempotencyEngine
from scp.task_kernel_parts.lease_manager import (
    assert_lease_impl,
    claim_impl,
    claim_next_impl,
    heartbeat_impl,
    record_action_dispatched_impl,
    start_impl,
    verify_integrity_impl,
    verify_journal_impl,
)
from scp.task_kernel_parts.recovery import RecoveryEngine
from scp.task_kernel_parts.state_machine import StateMachineEngine
from scp.task_kernel_parts.taskkernel import TaskKernel

# Canonical domain aliases
CheckpointManager = CheckpointEngine
IdempotencyManager = IdempotencyEngine
InvalidTransitionError = InvalidTransition
RecoveryManager = RecoveryEngine
StateMachine = StateMachineEngine
TaskKernelError = KernelError

__all__ = [
    "ALLOWED_TRANSITIONS",
    "CheckpointCorrupt",
    "CheckpointEngine",
    "CheckpointManager",
    "CommitApprovalEngine",
    "DEFAULT_MAX_ATTEMPTS",
    "FeedbackContext",
    "IdempotencyEngine",
    "IdempotencyManager",
    "InvalidTransition",
    "InvalidTransitionError",
    "KernelError",
    "KernelStorage",
    "KillSwitchActive",
    "Lease",
    "NotFound",
    "OptimisticLockError",
    "RecoveryDecision",
    "RecoveryEngine",
    "RecoveryManager",
    "RemediationResult",
    "SQLiteKernelStorage",
    "STATES",
    "SoftFeedbackRemediationLoop",
    "StaleLease",
    "StateMachine",
    "StateMachineEngine",
    "StorageIntegrityError",
    "TERMINAL",
    "TaskKernel",
    "TaskKernelError",
    "assert_lease_impl",
    "claim_impl",
    "claim_next_impl",
    "heartbeat_impl",
    "record_action_dispatched_impl",
    "start_impl",
    "verify_integrity_impl",
    "verify_journal_impl",
]
