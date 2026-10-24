"""Brain Layer P0 — TraceWriter ghi BrainTrace JSONL (bằng chứng, không prompt).

Mỗi record: brain_trace_id (bt-<32hex>), run_id, task, config_hash,
provider_id, model_id, schema_valid, latency_ms, judge_independence, status,
input_hash. KHÔNG BAO GIỜ ghi prompt đầy đủ — chỉ input_hash (sha256 của
payload chuẩn hóa). Vị trí file: runtime_path("SCP_BRAIN_TRACE_PATH",
"brain_trace.jsonl") dưới runtime_data_dir().
"""
from __future__ import annotations

import hashlib
import json
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from scp.core.runtime_paths import runtime_path

TRACE_PATH_ENV_KEY = "SCP_BRAIN_TRACE_PATH"
TRACE_FILENAME = "brain_trace.jsonl"


def new_brain_trace_id() -> str:
    return "bt-" + secrets.token_hex(16)  # bt- + 32 hex


def input_hash(payload: Mapping[str, Any]) -> str:
    """sha256 của payload chuẩn hóa — đại diện prompt mà không lộ nội dung."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_trace_record(
    *,
    brain_trace_id: str,
    run_id: str,
    trace_id: str,
    task: str,
    status: str,
    config_hash: str,
    contract_version: str,
    provider_id: str | None,
    model_id: str | None,
    schema_valid: bool | None,
    latency_ms: float,
    judge_independence: bool,
    payload_input_hash: str,
    route_reason: str,
    warnings: tuple[str, ...],
) -> dict[str, Any]:
    return {
        "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
        "brain_trace_id": brain_trace_id,
        "run_id": run_id,
        "trace_id": trace_id,
        "task": task,
        "status": status,
        "config_hash": config_hash,
        "contract_version": contract_version,
        "provider_id": provider_id,
        "model_id": model_id,
        "schema_valid": schema_valid,
        "latency_ms": round(latency_ms, 3),
        "judge_independence": judge_independence,
        "input_hash": payload_input_hash,
        "route_reason": route_reason,
        "warnings": list(warnings),
    }


class TraceWriter:
    """Append-only JSONL writer. Path resolve LIVE qua runtime_path (test có
    thể trỏ SCP_DATA_DIR/SCP_BRAIN_TRACE_PATH sang tmp)."""

    def __init__(self, path: Path | None = None) -> None:
        self._explicit_path = path

    @property
    def path(self) -> Path:
        if self._explicit_path is not None:
            return self._explicit_path
        return runtime_path(TRACE_PATH_ENV_KEY, TRACE_FILENAME)

    def write(self, record: Mapping[str, Any]) -> Path:
        path = self.path
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, sort_keys=True, ensure_ascii=False, default=str)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        return path
