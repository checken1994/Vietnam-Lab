"""Data models for the SCP runtime reality audit.

DNA #2 (Reality > Model): a verdict here may only ever be derived from
observed runtime evidence. There is no code path that produces PASS without
at least one successful observation, and no code path that produces PASS
when an observation was impossible (that is UNKNOWN, fail-closed).

DNA #22 (PASS != TRUE): a PASS verdict means "no defect was observed inside
the probed scope, with the evidence listed". It never means "the system is
correct".
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Severity(str, enum.Enum):
    """Per-finding severity. Ordering matters for verdict derivation."""

    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"

    @property
    def rank(self) -> int:
        return _SEVERITY_RANK[self]


_SEVERITY_RANK = {
    Severity.PASS: 0,
    Severity.WARN: 1,
    Severity.UNKNOWN: 2,
    Severity.FAIL: 3,
}


class Verdict(str, enum.Enum):
    """Overall runtime audit verdict."""

    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"


def derive_verdict(findings: list["ProbeResult"]) -> Verdict:
    """Fail-closed verdict derivation.

    * any FAIL  -> FAIL
    * any UNKNOWN -> UNKNOWN (we could not observe part of the scope)
    * any WARN  -> WARN
    * otherwise -> PASS
    """
    if not findings:
        # Nothing observed at all is never a PASS.
        return Verdict.UNKNOWN
    worst = max(findings, key=lambda f: f.severity.rank)
    return Verdict(worst.severity.value)


@dataclass(frozen=True)
class ProbeResult:
    """One observed fact about the running system.

    ``details`` carries machine-readable observations; ``evidence`` carries
    the raw values the observation was derived from (hashes, counts, status
    codes) so a reviewer can re-derive the conclusion independently.
    """

    probe_id: str
    severity: Severity
    title: str
    summary: str
    details: dict[str, Any] = field(default_factory=dict)
    evidence: dict[str, Any] = field(default_factory=dict)
    observed_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "probe_id": self.probe_id,
            "severity": self.severity.value,
            "title": self.title,
            "summary": self.summary,
            "details": self.details,
            "evidence": self.evidence,
            "observed_at": self.observed_at,
        }


@dataclass(frozen=True)
class RuntimeAuditVerdict:
    """The fail-closed result of one runtime audit pass."""

    verdict: Verdict
    findings: list[ProbeResult]
    target: str
    data_dir: str
    started_at: str
    completed_at: str
    elapsed_sec: float
    scope: dict[str, Any] = field(default_factory=dict)

    def counts(self) -> dict[str, int]:
        counts = {s.value: 0 for s in Severity}
        for finding in self.findings:
            counts[finding.severity.value] += 1
        return counts

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict.value,
            "target": self.target,
            "data_dir": self.data_dir,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "elapsed_sec": self.elapsed_sec,
            "scope": self.scope,
            "counts": self.counts(),
            "findings": [f.to_dict() for f in self.findings],
        }
