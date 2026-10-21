"""SCP runtime reality audit.

This package answers one question honestly: *what is the running system
actually doing right now, and does its own reporting match?*

It exists because the pre-existing audit surfaces cannot answer it:

* ``scp/audit_engine/`` is an isolated, dead-zone subsystem whose planner
  returns a formatted string and whose runner returns a constant. Nothing
  in it executes or observes anything.
* ``TaskKernel.verify_journal(task_id)`` validates a single task's hash
  chain and never cross-checks the global ``control`` anchor against the
  physical event table.
* ``/health`` returns a static ``{"status": "ok"}`` string that is
  independent of the physical state of the storage underneath it.

The probes here observe through independent mechanisms (filesystem, process
boundary, on-disk ledgers) and derive one fail-closed verdict: any failure
makes the verdict FAIL, and anything that could not be observed makes it
UNKNOWN rather than PASS.

This package deliberately lives outside ``scp/audit_engine/`` so it does not
violate that package's isolation contract.
"""
from __future__ import annotations

from scp.runtime_audit.ledger import RuntimeAuditLedger
from scp.runtime_audit.models import (
    ProbeResult,
    RuntimeAuditVerdict,
    Severity,
    Verdict,
    derive_verdict,
)
from scp.runtime_audit.probes import (
    ClaimRealityCrossCheck,
    JournalAnchorProbe,
    LedgerYieldProbe,
    ServiceLivenessProbe,
    SqliteRealityProbe,
)
from scp.runtime_audit.runner import RuntimeAuditRunner, run_runtime_audit

__all__ = [
    "ClaimRealityCrossCheck",
    "JournalAnchorProbe",
    "LedgerYieldProbe",
    "ProbeResult",
    "RuntimeAuditLedger",
    "RuntimeAuditRunner",
    "RuntimeAuditVerdict",
    "ServiceLivenessProbe",
    "Severity",
    "SqliteRealityProbe",
    "Verdict",
    "derive_verdict",
    "run_runtime_audit",
]
