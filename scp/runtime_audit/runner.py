"""Orchestration of the SCP runtime reality audit.

The runner wires the independent probes together, collects the claims the
running system makes about itself over HTTP, cross-checks those claims
against the physical observations, and derives one fail-closed verdict.

Probe independence (DNA #5): the probes below observe through different
mechanisms — the filesystem (SQLite files), the process boundary (HTTP),
and the on-disk ledgers. A defect must be visible to more than one probe
before it is treated as systemic; a single-probe observation is reported
but never escalated into a global claim on its own.
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any, Sequence

from scp.core.runtime_paths import runtime_data_dir
from scp.security.secret_registry import resolve
from scp.runtime_audit.models import (
    ProbeResult,
    RuntimeAuditVerdict,
    derive_verdict,
    utc_now_iso,
)
from scp.runtime_audit.probes import (
    BrainLayerProbe,
    ClaimRealityCrossCheck,
    JournalAnchorProbe,
    LedgerYieldProbe,
    ServiceLivenessProbe,
    SqliteRealityProbe,
)

logger = logging.getLogger(__name__)

#: Normative service endpoints. Every entry is probed with a plain GET.
DEFAULT_SERVICE_TARGETS: list[dict[str, Any]] = [
    {"name": "backend-health", "url_env": "SCP_AUDIT_BACKEND_URL", "url_default": "http://127.0.0.1:8000/health", "path": "/health"},
    {"name": "backend-ready", "url_env": "SCP_AUDIT_BACKEND_URL", "url_default": "http://127.0.0.1:8000/ready", "path": "/ready"},
    {"name": "backend-audit-stats", "url_env": "SCP_AUDIT_BACKEND_URL", "url_default": "http://127.0.0.1:8000/v105/audit/stats", "path": "/v105/audit/stats"},
    {"name": "backend-runtime-subsystems", "url_env": "SCP_AUDIT_BACKEND_URL", "url_default": "http://127.0.0.1:8000/v105/runtime/subsystems", "path": "/v105/runtime/subsystems"},
    {"name": "loop-scheduler", "url_env": "SCP_AUDIT_LOOP_URL", "url_default": "http://127.0.0.1:3030/healthz", "path": "/healthz"},
]

#: Claims the running system makes about itself, and the probe that
#: cross-checks them.
CLAIM_PROBE_MAP = {
    "backend_health": "backend-health",
    "backend_audit_stats": "backend-audit-stats",
    "backend_runtime_subsystems": "backend-runtime-subsystems",
}


def _resolve_admin_token() -> str:
    """Resolve an optional admin bearer token for authenticated claims.

    The audit runs as an operator tool. When no credential is available the
    protected endpoints simply stay protected — that is reported honestly as
    "could not observe", never as a pass.
    """
    for env_name in ("SCP_ADMIN_KEY", "SCP_AUTH_TOKEN_SECRET", "SCP_SCHEDULER_ADMIN_TOKEN"):
        value = os.environ.get(env_name, "").strip()
        if value:
            return value
    return ""


def _resolve_targets(targets: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    resolved: list[dict[str, Any]] = []
    for target in targets:
        base = os.environ.get(str(target.get("url_env") or ""), "").strip()
        url = str(target.get("url") or "")
        if not url and base:
            url = base.rstrip("/") + str(target.get("path") or "")
        elif not url and target.get("url_default"):
            url = str(target["url_default"])
        if not url:
            continue
        resolved.append({**target, "url": url})
    return resolved


class RuntimeAuditRunner:
    """Run every runtime probe and derive one fail-closed verdict."""

    def __init__(
        self,
        *,
        data_dir: Path | None = None,
        service_targets: Sequence[dict[str, Any]] | None = None,
        journal_secret: str | None = None,
        admin_token: str | None = None,
        http_timeout_sec: float = 5.0,
    ) -> None:
        self.data_dir = Path(data_dir) if data_dir is not None else runtime_data_dir()
        self.service_targets = _resolve_targets(
            list(service_targets) if service_targets is not None else DEFAULT_SERVICE_TARGETS
        )
        # [R5] Journal HMAC dùng K_journal (SCP_JOURNAL_SECRET), không còn SCP_CAPABILITY_SECRET.
        self.journal_secret = journal_secret if journal_secret is not None else (resolve("SCP_JOURNAL_SECRET") or "")
        self.admin_token = admin_token if admin_token is not None else _resolve_admin_token()
        self.http_timeout_sec = http_timeout_sec

    # ── helpers ──────────────────────────────────────────────────────────
    def _kernel_db_paths(self) -> list[Path]:
        """Locate every Task Kernel database under the data directory."""
        candidates: list[Path] = []
        if not self.data_dir.exists():
            return candidates
        seen: set[Path] = set()
        for pattern in ("*.sqlite3", "*.sqlite", "*.db"):
            for candidate in sorted(self.data_dir.rglob(pattern)):
                if not candidate.is_file():
                    continue
                resolved = candidate.resolve()
                if resolved in seen:
                    continue
                seen.add(resolved)
                if JournalAnchorProbe.looks_like_kernel_db(candidate):
                    candidates.append(candidate)
        return candidates

    def _claims_from_observations(self, observations: list[dict[str, Any]]) -> dict[str, Any]:
        by_name = {str(o.get("name")): o for o in observations}
        claims: dict[str, Any] = {}
        for claim_name, probe_name in CLAIM_PROBE_MAP.items():
            observation = by_name.get(probe_name)
            if observation is not None:
                claims[claim_name] = observation
        return claims

    # ── main entry point ────────────────────────────────────────────────
    def run(self) -> RuntimeAuditVerdict:
        started_at = utc_now_iso()
        started = time.monotonic()
        findings: list[ProbeResult] = []

        storage_probe = SqliteRealityProbe(self.data_dir)
        storage_results = storage_probe.run()
        findings.extend(storage_results)

        kernel_paths = self._kernel_db_paths()
        journal_probe = JournalAnchorProbe(
            kernel_paths,
            journal_secret=self.journal_secret or None,
            data_dir=self.data_dir,
        )
        findings.extend(journal_probe.run())

        liveness_probe = ServiceLivenessProbe(
            self.service_targets,
            timeout_sec=self.http_timeout_sec,
            bearer_token=self.admin_token or None,
        )
        liveness_results = liveness_probe.run()
        findings.extend(liveness_results)

        ledger_probe = LedgerYieldProbe(self.data_dir)
        ledger_results = ledger_probe.run()
        findings.extend(ledger_results)

        # [BRAIN-LAYER P1] Brain layer status probe — mode=off ⇒ UNKNOWN
        # (không healthy-giả), mode=shadow ⇒ quan sát registry thật.
        brain_probe = BrainLayerProbe()
        brain_results = brain_probe.run()
        findings.extend(brain_results)

        # ── claim vs reality ────────────────────────────────────────────
        liveness_observations: list[dict[str, Any]] = []
        for result in liveness_results:
            if result.probe_id == "services.liveness":
                liveness_observations = list(result.evidence.get("observations") or [])
        claims = self._claims_from_observations(liveness_observations)

        storage_census: list[dict[str, Any]] = []
        for result in storage_results:
            if result.probe_id == "storage.sqlite_reality":
                storage_census = list(result.evidence.get("census") or [])

        corrupt_databases = [
            record["path"] for record in storage_census
            if not record.get("openable")
            or (record.get("integrity_check") and str(record["integrity_check"]) != "ok")
        ]
        deep_audit_records = 0
        for result in ledger_results:
            if result.probe_id == "loops.effectiveness":
                deep_audit_records = int((result.details.get("deep_audit") or {}).get("total_records") or 0)

        cross_check = ClaimRealityCrossCheck(
            claims=claims,
            physical={
                "corrupt_databases": corrupt_databases,
                "deep_audit_records": deep_audit_records,
            },
        )
        findings.extend(cross_check.run())

        verdict = derive_verdict(findings)
        completed_at = utc_now_iso()
        elapsed = round(time.monotonic() - started, 3)

        return RuntimeAuditVerdict(
            verdict=verdict,
            findings=findings,
            target=f"{self.data_dir}",
            data_dir=str(self.data_dir),
            started_at=started_at,
            completed_at=completed_at,
            elapsed_sec=elapsed,
            scope={
                "probes": [
                    "storage.sqlite_reality",
                    "kernel.journal_anchor",
                    "services.liveness",
                    "loops.effectiveness",
                    "brain.layer_status",
                    "audit.claim_vs_reality",
                ],
                "kernel_databases": [str(p) for p in kernel_paths],
                "service_targets": [t["url"] for t in self.service_targets],
                "hmac_enabled": bool(self.journal_secret),
                "authenticated_claims": bool(self.admin_token),
            },
        )


def run_runtime_audit(
    *,
    data_dir: Path | None = None,
    journal_secret: str | None = None,
) -> RuntimeAuditVerdict:
    """Convenience wrapper: run the audit and return the verdict."""
    return RuntimeAuditRunner(
        data_dir=data_dir,
        journal_secret=journal_secret,
    ).run()
