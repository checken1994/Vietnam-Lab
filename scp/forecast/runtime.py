"""[PREDICTION-P0 · 2026-10-31] Wiring runtime của forecast ledger R44 — đúng hợp đồng sẵn có.

TẠI SAO: hook /ask cũ gọi ``ForecastLedger(db_path=...)`` + ``record_case`` (không tồn tại) ⇒
TypeError 100% bị nuốt; route tự dựng registry RỖNG trong RAM với ``manifest_status="SKIPPED"``
(snapshot bịa) rồi ``hasattr`` vào hàm không tồn tại ⇒ "active" giả.

Hợp đồng thật (``scp/forecast/ledger.py``): ``ForecastLedger(registry, ledger_path)`` trên một
registry ĐÃ KHOÁ (``ForecastRegistry.load``, manifest hash); ledger KHÔNG BAO GIỜ sửa registry.
Vì vậy:

- Registry runtime = ``<data>/forecast_registry.json`` nạp bằng ``ForecastRegistry.load`` (trạng
  thái manifest trung thực; ``resolve_case`` tự từ chối anchor chưa xác minh). Không có file ⇒
  ``None`` (route trả "no_registry"), KHÔNG dựng registry giả.
- KHÔNG tự đăng ký case từ /ask và KHÔNG tự tính lại manifest (tự ký anchor của chính mình =
  phá ý nghĩa sổ khoá).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .ledger import ForecastLedger, ForecastRegistry

REGISTRY_FILENAME = "forecast_registry.json"
LEDGER_FILENAME = "forecast_ledger.jsonl"

#: Hook forecast trong /ask: TẮT. Không có tín hiệu ngữ nghĩa ĐÃ KIỂM CHỨNG cho "câu hỏi này là
#: một dự báo có thể chấm sau" (bảng từ khoá VI/EN cũ vi phạm luật 9 và không đa ngôn ngữ), và
#: hợp đồng R44 chỉ chấm case của registry đã khoá — câu hỏi /ask không phải case registry.
_ASK_HOOK_STATUS = {
    "status": "disabled",
    "epistemic_status": "UNKNOWN_WITH_REASON",
    "reason": (
        "no verified semantic signal for forecast intent (language keyword table removed, rule 9); "
        "R44 ForecastLedger only scores cases of a locked registry and never registers /ask questions"
    ),
    "next_lane": "semantic forecast-intent signal (L1/L2 consensus) + registry contract for runtime cases",
}


def ask_forecast_hook_status() -> dict[str, Any]:
    return dict(_ASK_HOOK_STATUS)


def registry_path(data_dir: str | Path) -> Path:
    return Path(data_dir) / REGISTRY_FILENAME


def load_runtime_ledger(data_dir: str | Path) -> ForecastLedger | None:
    """Ledger trên registry runtime đã khoá; ``None`` khi chưa có registry.

    Registry hỏng ⇒ ``ForecastContractError`` lan lên (fail-closed, không tự "sửa"/đổi tên file
    bằng chứng)."""
    path = registry_path(data_dir)
    if not path.exists():
        return None
    registry = ForecastRegistry.load(path)
    return ForecastLedger(registry, Path(data_dir) / LEDGER_FILENAME)


__all__ = ["LEDGER_FILENAME", "REGISTRY_FILENAME", "ask_forecast_hook_status", "load_runtime_ledger",
           "registry_path"]
