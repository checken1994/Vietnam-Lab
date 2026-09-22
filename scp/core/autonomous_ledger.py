"""Cryptographic Ledger Provenance for Agent OS Autonomous Steps."""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from scp.core.trace_contract import redact_attributes
from scp.trace_ledger import TraceLedger


@dataclass(frozen=True)
class ProvenanceBlock:
    block_index: int
    prev_block_hash: str | None
    task_id: str
    step_id: str
    tool_name: str
    input_sha256: str
    capability_token_id: str
    capability_token_hash: str
    output_sha256: str
    status: str  # SUCCESS, FAILED, BLOCKED, UNKNOWN
    duration_ms: float
    timestamp: float
    block_hash: str = ""

    def canonical_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("block_hash", None)
        return d

    def compute_hash(self) -> str:
        serialized = json.dumps(self.canonical_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return "sha256:" + hashlib.sha256(serialized.encode("utf-8")).hexdigest()


class AutonomousAuditLedger:
    """Ledger recording two-phase tool provenance blocks into the append-only TraceLedger."""

    def __init__(self, trace_ledger: TraceLedger | None = None, ledger_path: str | Path | None = None) -> None:
        if trace_ledger is not None:
            self.trace_ledger = trace_ledger
        else:
            path = ledger_path or "data/trace_ledger.jsonl"
            self.trace_ledger = TraceLedger(path)

    @staticmethod
    def _hash_value(val: Any) -> str:
        raw = json.dumps(val, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def commit_intent(
        self,
        task_id: str,
        step_id: str,
        tool_name: str,
        params: dict[str, Any],
        capability_token: Any,
        parent_trace_id: str | None = None,
    ) -> dict[str, Any]:
        """Commit Phase 1: Tool invocation intent with parameter digest."""
        if isinstance(capability_token, dict):
            token_id = str(capability_token.get("token_id", "") or params.get("token_id", ""))
            token_sig = str(capability_token.get("signature", ""))
        else:
            token_id = str(getattr(capability_token, "token_id", "") or params.get("token_id", ""))
            token_sig = str(getattr(capability_token, "signature", ""))

        token_hash = self._hash_value({"token_id": token_id, "signature": token_sig})
        input_hash = self._hash_value(redact_attributes(params))

        entry = self.trace_ledger.append(
            event="AUTONOMOUS_TOOL_INTENT",
            task_id=task_id,
            step_id=step_id,
            tool_name=tool_name,
            input_sha256=input_hash,
            token_id=token_id,
            token_hash=token_hash,
            parent_trace_id=parent_trace_id or "",
            timestamp=time.time(),
        )
        return entry

    def commit_result(
        self,
        task_id: str,
        step_id: str,
        tool_name: str,
        intent_entry_hash: str,
        result_data: dict[str, Any],
        evidence: dict[str, Any],
        status: str,
        duration_ms: float,
    ) -> dict[str, Any]:
        """Commit Phase 2: Tool execution result with evidence and output digest."""
        output_hash = self._hash_value(redact_attributes(result_data))
        evidence_hash = self._hash_value(evidence)

        entry = self.trace_ledger.append(
            event="AUTONOMOUS_TOOL_RESULT",
            task_id=task_id,
            step_id=step_id,
            tool_name=tool_name,
            intent_entry_hash=intent_entry_hash,
            output_sha256=output_hash,
            evidence_sha256=evidence_hash,
            status=status,
            duration_ms=duration_ms,
            timestamp=time.time(),
        )
        return entry

    def verify_provenance(self) -> dict[str, Any]:
        """Verify the immutable ledger hash chain for all autonomous steps."""
        return self.trace_ledger.verify()


__all__ = [
    "ProvenanceBlock",
    "AutonomousAuditLedger",
]
