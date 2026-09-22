"""Durable TaskKernel public contract and shared state-machine definitions."""
from __future__ import annotations

from typing import Any
from .task_kernel_parts.definitions import (
    STATES,
    TERMINAL,
    ALLOWED_TRANSITIONS,
    KernelError,
    InvalidTransition,
    StaleLease,
    OptimisticLockError,
    KillSwitchActive,
    CheckpointCorrupt,
    NotFound,
    Lease,
    RecoveryDecision,
    now_iso,
    stable_hash,
    as_json,
    _assert_checkpoint_safe,
    _checkpoint_contains_secret,
)
from scp.kernel_storage import KernelStorage, StorageIntegrityError, make_storage
from .task_kernel_parts.taskkernel import TaskKernel, verify_approval_authority

def _idempotency_claim_fenced(self, *args, **kwargs):
    return self.idempotency_claim(*args, **kwargs)

def _idempotency_complete_fenced(self, *args, **kwargs):
    return self.idempotency_complete(*args, **kwargs)

def _idempotency_status(self, *args, **kwargs):
    return self.idempotency_status(*args, **kwargs)

# The implementation may live in a part module, but the public class lived at
# ``scp.task_kernel.TaskKernel`` before the split. Preserve that identity for
# introspection and pickle/import compatibility.
TaskKernel.__module__ = __name__

__all__ = [
    "TaskKernel", "Lease", "RecoveryDecision", "KernelError",
    "InvalidTransition", "StaleLease", "OptimisticLockError", "KillSwitchActive",
    "CheckpointCorrupt", "NotFound", "STATES", "TERMINAL",
    "ALLOWED_TRANSITIONS", "now_iso", "stable_hash", "as_json",
    "verify_approval_authority",
    "_idempotency_claim_fenced", "_idempotency_complete_fenced", "_idempotency_status",
]
