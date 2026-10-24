"""Effectiveness of the self-audit loops, read from their ledgers.
This module is part of :mod:`scp.runtime_audit.probes`.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from scp.runtime_audit.models import ProbeResult, Severity, utc_now_iso
from scp.runtime_audit.probes._shared import as_int


class LedgerYieldProbe:
    """Observe whether the self-audit loops actually produce findings.

    A loop that reports ``status: ok`` forever while emitting zero findings
    is not healthy — it is a no-op wearing a green badge. This probe reads
    the run ledgers and reports *effectiveness*, not just liveness.
    """

    probe_id = "loops.effectiveness"

    def __init__(
        self,
        data_dir: Path,
        *,
        loop_ledger: str = "loop_runs.jsonl",
        deep_audit_ledger: str = "deep_audit_results.jsonl",
        max_records: int = 20_000,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.loop_ledger = loop_ledger
        self.deep_audit_ledger = deep_audit_ledger
        self.max_records = max_records

    def _read_jsonl(self, name: str) -> list[dict[str, Any]]:
        path = self.data_dir / name
        records: list[dict[str, Any]] = []
        if not path.exists():
            return records
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                for line_no, line in enumerate(handle):
                    if line_no >= self.max_records:
                        break
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        parsed = json.loads(line)
                    except (TypeError, ValueError):
                        continue
                    if isinstance(parsed, dict):
                        records.append(parsed)
        except OSError:
            return []
        return records

    def inspect_loop_ledger(self) -> dict[str, Any]:
        records = self._read_jsonl(self.loop_ledger)
        statuses = Counter(str(r.get("status", "unknown")) for r in records)
        with_findings = [r for r in records if "findings_count" in r]
        nonzero_findings = [r for r in with_findings if as_int(r.get("findings_count")) > 0]
        total = len(records)
        error_like = sum(count for status, count in statuses.items() if status in {"error", "scp_offline", "bridge_offline", "auth_required"})
        return {
            "ledger": self.loop_ledger,
            "present": bool(records),
            "total_runs": total,
            "status_counts": dict(statuses),
            "runs_with_findings_field": len(with_findings),
            "runs_with_nonzero_findings": len(nonzero_findings),
            "error_like_runs": error_like,
            "error_like_ratio": round(error_like / total, 4) if total else None,
            "zero_finding_ratio": (
                round((len(with_findings) - len(nonzero_findings)) / len(with_findings), 4)
                if with_findings else None
            ),
        }

    def inspect_deep_audit_ledger(self) -> dict[str, Any]:
        records = self._read_jsonl(self.deep_audit_ledger)
        actions = Counter()
        reasons = Counter()
        for record in records:
            result = record.get("result")
            if isinstance(result, dict):
                action = str(result.get("action", "unknown"))
                actions[action] += 1
                reason = result.get("reason")
                if reason:
                    reasons[str(reason)[:120]] += 1
            else:
                actions["unstructured"] += 1
        total = len(records)
        skipped = actions.get("skipped", 0)
        return {
            "ledger": self.deep_audit_ledger,
            "present": bool(records),
            "total_records": total,
            "action_counts": dict(actions),
            "skipped_ratio": round(skipped / total, 4) if total else None,
            "top_skip_reasons": dict(reasons.most_common(5)),
        }

    def run(self) -> list[ProbeResult]:
        results: list[ProbeResult] = []
        loop = self.inspect_loop_ledger()
        deep = self.inspect_deep_audit_ledger()

        if not loop["present"] and not deep["present"]:
            results.append(ProbeResult(
                probe_id=self.probe_id,
                severity=Severity.UNKNOWN,
                title="No self-audit ledger is observable",
                summary="Neither loop_runs.jsonl nor deep_audit_results.jsonl exists; loop effectiveness is unknown.",
                details={"loop": loop, "deep_audit": deep},
            ))
            return results

        problems: list[str] = []
        warnings: list[str] = []
        if loop["present"]:
            zero_ratio = loop.get("zero_finding_ratio")
            if zero_ratio is not None and zero_ratio >= 0.99 and loop["runs_with_findings_field"] >= 20:
                problems.append(
                    f"{loop['runs_with_nonzero_findings']}/{loop['runs_with_findings_field']} audit runs produced findings"
                )
            error_ratio = loop.get("error_like_ratio")
            if error_ratio is not None and error_ratio >= 0.10:
                warnings.append(f"{round(error_ratio * 100, 1)}% of audit runs ended in an error-like state")
        if deep["present"]:
            skipped_ratio = deep.get("skipped_ratio")
            if skipped_ratio is not None and skipped_ratio >= 0.90 and deep["total_records"] >= 20:
                problems.append(
                    f"{round(skipped_ratio * 100, 1)}% of deep-audit findings were skipped without resolution"
                )

        if problems:
            severity = Severity.FAIL
            title = "Self-audit loop reports success without producing findings"
            summary = "; ".join(problems)
        elif warnings:
            severity = Severity.WARN
            title = "Self-audit loop is degraded"
            summary = "; ".join(warnings)
        else:
            severity = Severity.PASS
            title = "Self-audit loop shows observable yield"
            summary = "Audit runs produced findings and deep-audit records were not overwhelmingly skipped."

        results.append(ProbeResult(
            probe_id=self.probe_id,
            severity=severity,
            title=title,
            summary=summary,
            details={
                "loop": loop,
                "deep_audit": deep,
                "problems": problems,
                "warnings": warnings,
            },
            evidence={"loop": loop, "deep_audit": deep},
            observed_at=utc_now_iso(),
        ))
        return results
