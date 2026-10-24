"""Brain Layer P0 — probe skeleton cho scp/runtime_audit (CHỜ wire).

Chỉ là hàm thuần `collect_brain_layer_status(config)` trả dict trạng thái:
mode, config_hash, tasks, providers. KHÔNG tự gọi, KHÔNG đăng ký side-effect;
việc wire vào runtime audit làm ở P1 khi audit console cần.
"""
from __future__ import annotations

from typing import Any

from scp.brain_layer.config import BrainConfig
from scp.brain_layer.contracts import CONTRACT_VERSION

PROBE_ID = "brain_layer_status"


def collect_brain_layer_status(config: BrainConfig) -> dict[str, Any]:
    """Trạng thái brain layer hiện tại (mode đọc live từ settings)."""
    from scp.brain_layer.brain_layer import BrainLayer  # noqa: PLC0415 — tránh cycle

    registry = config.registry
    return {
        "probe": PROBE_ID,
        "contract_version": CONTRACT_VERSION,
        "mode": BrainLayer.mode(),
        "config_hash": config.config_hash,
        "tasks": sorted(registry.tasks),
        "providers": sorted(registry.providers),
        "models": sorted(registry.models),
        "warnings": list(config.warnings),
    }
