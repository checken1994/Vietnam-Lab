"""Runtime reality probes.

Each probe observes ONE aspect of the running system and returns
:class:`~scp.runtime_audit.models.ProbeResult` objects. A probe never
mutates the system under audit: every database is opened read-only and every
HTTP call is a plain GET.

Anti-Placebo (FA-01/FA-04): probes report what they observed. A probe that
cannot observe its target returns ``UNKNOWN`` — never PASS.

The probes are split by family to respect the repository's 800-line module
ceiling (ARC-01).
"""
from __future__ import annotations

from scp.runtime_audit.probes._shared import (
    BACKUP_DIR_MARKERS,
    INTEGRITY_CHECK_BUDGET_MS,
    MAX_TABLES_PER_DB,
    SELF_AUDIT_DB_NAMES,
    is_backup_path,
    open_readonly,
    quote_ident,
)
from scp.runtime_audit.probes.brain import BrainLayerProbe
from scp.runtime_audit.probes.claims import ClaimRealityCrossCheck
from scp.runtime_audit.probes.kernel import JournalAnchorProbe
from scp.runtime_audit.probes.ledgers import LedgerYieldProbe
from scp.runtime_audit.probes.services import ServiceLivenessProbe
from scp.runtime_audit.probes.storage import SqliteRealityProbe

__all__ = [
    "BACKUP_DIR_MARKERS",
    "BrainLayerProbe",
    "ClaimRealityCrossCheck",
    "INTEGRITY_CHECK_BUDGET_MS",
    "JournalAnchorProbe",
    "LedgerYieldProbe",
    "MAX_TABLES_PER_DB",
    "SELF_AUDIT_DB_NAMES",
    "ServiceLivenessProbe",
    "SqliteRealityProbe",
    "is_backup_path",
    "open_readonly",
    "quote_ident",
]
