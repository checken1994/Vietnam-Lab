"""Contradiction detector between what the system claims and reality.
This module is part of :mod:`scp.runtime_audit.probes`.
"""
from __future__ import annotations

from typing import Any

from scp.runtime_audit.models import ProbeResult, Severity, utc_now_iso


class ClaimRealityCrossCheck:
    """Contradiction detector: compare what the system *claims* with reality.

    The backend reports ``/health -> {"status": "ok"}`` and the scheduler
    reports ``status: ok`` regardless of whether the storage underneath is
    physically corrupt. This probe takes the claims observed over HTTP and
    the physical observations collected by the other probes, and reports a
    FAIL when they disagree.

    It exists because a system that is confidently wrong is more dangerous
    than a system that is honestly broken (DNA #22, DNA #25).
    """

    probe_id = "audit.claim_vs_reality"

    def __init__(
        self,
        *,
        claims: dict[str, Any] | None = None,
        physical: dict[str, Any] | None = None,
    ) -> None:
        self.claims = claims or {}
        self.physical = physical or {}

    def run(self) -> list[ProbeResult]:
        contradictions: list[dict[str, Any]] = []
        observations: list[dict[str, Any]] = []

        health_claim = self.claims.get("backend_health")
        db_failures = list(self.physical.get("corrupt_databases") or [])
        if isinstance(health_claim, dict) and health_claim.get("status") == 200:
            payload = health_claim.get("json") or {}
            claimed_status = str(payload.get("status", "")) if isinstance(payload, dict) else ""
            observations.append({
                "claim": "backend /health reports status=ok",
                "observed_status": claimed_status,
                "http_status": 200,
            })
            if claimed_status == "ok" and db_failures:
                contradictions.append({
                    "claim": "backend /health status=ok",
                    "observed": f"{len(db_failures)} live database(s) failed physical integrity",
                    "contradiction": "service declares itself healthy while its storage is corrupt",
                })

        audit_claim = self.claims.get("backend_audit_stats")
        deep_audit_records = int(self.physical.get("deep_audit_records") or 0)
        if isinstance(audit_claim, dict) and audit_claim.get("status") == 200:
            payload = audit_claim.get("json") or {}
            reported = payload.get("total_findings") if isinstance(payload, dict) else None
            observations.append({
                "claim": "backend /v105/audit/stats total_findings",
                "observed_status": reported,
                "http_status": 200,
            })
            if reported == 0 and deep_audit_records > 0:
                contradictions.append({
                    "claim": "backend reports total_findings=0",
                    "observed": f"{deep_audit_records} deep-audit records exist in the ledger",
                    "contradiction": "audit engine reports no findings while its own ledger is populated",
                })

        subsystem_claim = self.claims.get("backend_runtime_subsystems")
        if isinstance(subsystem_claim, dict):
            status = subsystem_claim.get("status")
            observations.append({
                "claim": "backend /v105/runtime/subsystems",
                "observed_status": status,
                "http_status": subsystem_claim.get("status_code"),
            })
            if isinstance(status, int) and status >= 500:
                contradictions.append({
                    "claim": "backend serves the subsystem brain view",
                    "observed": f"HTTP {status}",
                    "contradiction": "the subsystem telemetry surface is broken at runtime",
                })

        if contradictions:
            severity = Severity.FAIL
            title = "System claim contradicts observed reality"
            summary = "; ".join(c["contradiction"] for c in contradictions)
        elif observations:
            severity = Severity.PASS
            title = "System claims agree with observed reality"
            summary = f"{len(observations)} claim(s) cross-checked with no contradiction observed."
        else:
            severity = Severity.UNKNOWN
            title = "No claim could be cross-checked"
            summary = "Neither a health claim nor an audit-stat claim was observable."

        results = [ProbeResult(
            probe_id=self.probe_id,
            severity=severity,
            title=title,
            summary=summary,
            details={"contradictions": contradictions, "observations": observations},
            evidence={"claims": self.claims},
            observed_at=utc_now_iso(),
        )]
        return results
