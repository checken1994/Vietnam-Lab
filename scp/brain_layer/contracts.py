"""Brain Layer P0 — contracts (dataclass thuần, không object nặng khi import).

Envelope + context theo thiết kế Brain Layer §4. Chỉ là dataclass; mọi quyết
định chạy thật nằm ở BrainLayer (mode=off mặc định → denied).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping

CONTRACT_VERSION = "brain-p0/1"

STATUS_OK = "ok"
STATUS_SCHEMA_INVALID = "schema_invalid"
STATUS_PROVIDER_ERROR = "provider_error"
STATUS_TIMEOUT = "timeout"
STATUS_POLICY_DENIED = "policy_denied"
STATUS_WITHHELD = "withheld"

BRAIN_STATUSES: frozenset[str] = frozenset(
    {
        STATUS_OK,
        STATUS_SCHEMA_INVALID,
        STATUS_PROVIDER_ERROR,
        STATUS_TIMEOUT,
        STATUS_POLICY_DENIED,
        STATUS_WITHHELD,
    }
)

# Data class bắt buộc giữ cục bộ — không bao giờ route ra provider external.
LOCAL_ONLY_DATA_CLASSES: frozenset[str] = frozenset({"secret", "credentials", "pii"})


def _empty_mapping() -> Mapping[str, Any]:
    return MappingProxyType({})


@dataclass(frozen=True)
class BrainContext:
    """Ngữ cảnh 1 lần gọi brain (đi vào trace ở dạng hash/không phải prompt)."""

    run_id: str
    trace_id: str = ""
    task_id: str = ""
    locale: str = "vi-VN"
    data_class: str = "public"
    actor: str = "system"
    capability_level: str = "standard"
    budget: Mapping[str, Any] = field(default_factory=_empty_mapping)
    # Task names mà model được chọn KHÔNG ĐƯỢC trùng champion (runtime guard,
    # bổ sung cho independence khai trong registry).
    independence: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class Claim:
    """Một claim trích xuất/kiểm chứng — đơn vị nhỏ nhất của fact-check."""

    claim_id: str
    text: str
    evidence_refs: tuple[str, ...] = ()
    confidence: float | None = None


@dataclass(frozen=True)
class JudgeOutput:
    """Output chuẩn của task judge (independence với generate bắt buộc)."""

    verdict: str
    score: float | None = None
    claims: tuple[Claim, ...] = ()
    rationale: str = ""


@dataclass(frozen=True)
class BrainResponse:
    """Envelope trả về cho caller — caller quyết định dùng hay không (shadow)."""

    brain_trace_id: str
    task: str
    status: str
    output: Any = None
    provider_id: str | None = None
    model_id: str | None = None
    contract_version: str = CONTRACT_VERSION
    config_hash: str = ""
    latency_ms: float = 0.0
    usage: Mapping[str, Any] = field(default_factory=_empty_mapping)
    warnings: tuple[str, ...] = ()
    route_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "brain_trace_id": self.brain_trace_id,
            "task": self.task,
            "status": self.status,
            "output": self.output,
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "contract_version": self.contract_version,
            "config_hash": self.config_hash,
            "latency_ms": self.latency_ms,
            "usage": dict(self.usage),
            "warnings": list(self.warnings),
            "route_reason": self.route_reason,
        }
