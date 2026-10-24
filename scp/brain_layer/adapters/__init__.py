"""Brain Layer P0 — adapters (chỉ contract; P0 KHÔNG có adapter network thật)."""
from __future__ import annotations

from scp.brain_layer.adapters.base import AdapterResult, ProviderAdapter, normalize_exception
from scp.brain_layer.adapters.fake import FakeAdapter

__all__ = ["AdapterResult", "ProviderAdapter", "FakeAdapter", "normalize_exception"]
