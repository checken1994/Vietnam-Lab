"""Shared storage configuration; resolving a path performs no filesystem writes."""
from __future__ import annotations

import os
from pathlib import Path

from scp.core.settings import get


def runtime_data_dir() -> Path:
    # [HC-03 mẫu-2 2026-10-23] Đọc qua EffectiveSettings (điểm đọc chuẩn, đã
    # strip) thay vì os.environ trực tiếp. Quan hệ đặc biệt với
    # SCP_KERNEL_DB_PATH (component path thắng data root) giữ nguyên.
    configured = get("SCP_DATA_DIR", "")
    return (Path(configured).expanduser() if configured else
            Path(__file__).resolve().parents[2] / "data").resolve()


def runtime_path(env_key: str, filename: str) -> Path:
    """An explicit component path wins; otherwise use the configured data root."""
    configured = os.environ.get(env_key, "").strip()
    return (Path(configured).expanduser() if configured else
            runtime_data_dir() / filename).resolve()
