"""Complete a VERIFYING TaskKernel task on a signed receipt over its journal check (R3).

``TaskKernel.commit_completed`` no longer accepts a bare ``"VERIFIED"`` verdict. Internal
callers whose only checkable postcondition is the task's own journal integrity
(autonomous cycle, soak harness, operational prober) use this helper instead of
self-asserting completion.

Scope limit: the receipt is signed in-process with the kernel's verifier secret and
attests journal integrity only, not the task goal. Its verifier identity is distinct
from the worker ids by convention, not by a separate trust domain.
"""
from __future__ import annotations

import time
from typing import Any

from scp.core.verifier_receipt import VerifierReceipt, sign_verifier_receipt
from scp.task_kernel_parts.definitions import KernelError, stable_hash

JOURNAL_VERIFIER_ID = "scp.kernel.verify_journal"


def complete_with_journal_receipt(kernel: Any, task_id: str, lease_id: str, scope: str) -> dict[str, Any]:
    """Verify the task journal, sign a receipt over that result, then commit COMPLETED.

    Raises KernelError (task stays VERIFYING) when the journal hash chain is invalid.
    The receipt's evidence_ref carries ``stable_hash`` of the verification result.
    """
    journal_check = kernel.verify_journal(task_id)
    if not journal_check.get("hash_chain_valid"):
        raise KernelError(f"journal verification failed for {task_id}: {journal_check.get('errors')}")
    receipt = sign_verifier_receipt(
        VerifierReceipt(
            task_id=task_id,
            verifier_id=JOURNAL_VERIFIER_ID,
            verdict="VERIFIED",
            evidence_ref=f"evidence://{scope}/{task_id}/journal-verify/{stable_hash(journal_check)}",
            issued_at=time.time(),
        )
    )
    return kernel.commit_verification_result(task_id, lease_id, receipt)


__all__ = ["JOURNAL_VERIFIER_ID", "complete_with_journal_receipt"]
