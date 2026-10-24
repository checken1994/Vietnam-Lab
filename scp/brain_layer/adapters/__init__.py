"""Brain Layer adapters (P0 contract; P1 thêm adapter legacy không-network)."""
from __future__ import annotations

from scp.brain_layer.adapters.base import AdapterResult, ProviderAdapter, normalize_exception
from scp.brain_layer.adapters.fake import FakeAdapter
from scp.brain_layer.adapters.legacy_gateway import (
    ADJUDICATED_BY,
    PROVIDER_ID,
    LegacyGatewayAdapter,
)

__all__ = [
    "ADJUDICATED_BY",
    "AdapterResult",
    "FakeAdapter",
    "LegacyGatewayAdapter",
    "PROVIDER_ID",
    "ProviderAdapter",
    "normalize_exception",
]
