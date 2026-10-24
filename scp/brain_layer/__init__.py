"""SCP Brain Layer P0 — contract only, mode=off.

Skeleton thuần DI theo thiết kế Brain Layer: registry fail-closed, router
rule-based, adapter contract + FakeAdapter, JSONL trace không chứa prompt.
KHÔNG cắm vào /ask, KHÔNG gọi model thật (P0). Import nhẹ — không object nặng.
"""
from __future__ import annotations

from scp.brain_layer.brain_layer import ALLOWED_MODES, BrainLayer
from scp.brain_layer.config import BrainConfig
from scp.brain_layer.contracts import (
    BRAIN_STATUSES,
    CONTRACT_VERSION,
    STATUS_OK,
    STATUS_PROVIDER_ERROR,
    STATUS_SCHEMA_INVALID,
    STATUS_TIMEOUT,
    STATUS_WITHHELD,
    BrainContext,
    BrainResponse,
)
from scp.brain_layer.errors import (
    BrainConfigError,
    BrainLayerError,
    BrainPolicyDenied,
    BrainProviderError,
    BrainSchemaInvalid,
    BrainTimeout,
)

__all__ = [
    "ALLOWED_MODES",
    "BRAIN_STATUSES",
    "CONTRACT_VERSION",
    "STATUS_OK",
    "STATUS_PROVIDER_ERROR",
    "STATUS_SCHEMA_INVALID",
    "STATUS_TIMEOUT",
    "STATUS_WITHHELD",
    "BrainConfig",
    "BrainConfigError",
    "BrainContext",
    "BrainLayer",
    "BrainLayerError",
    "BrainPolicyDenied",
    "BrainProviderError",
    "BrainResponse",
    "BrainSchemaInvalid",
    "BrainTimeout",
]
