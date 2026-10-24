"""Brain Layer status probe — observe brain layer mode + registry reality.

Part of :mod:`scp.runtime_audit.probes`.

DNA #2 (Reality > Model): probe này chỉ quan sát những gì thấy được trên
process hiện tại — mode đọc live từ settings, registry được NẠP THẬT bằng
cùng code path với BrainLayer (BrainConfig.load). KHÔNG healthy-giả:

- mode=off (hoặc mode lạ)  ⇒ UNKNOWN — brain layer không chạy, không có gì
  để quan sát; UNKNOWN là kết quả trung thực, KHÔNG phải PASS.
- mode=shadow + registry nạp OK ⇒ PASS — cấu hình thật, nạp được, kèm
  config_hash + tasks + providers làm bằng chứng (DNA #22: PASS chỉ nghĩa là
  "không thấy lỗi trong phạm vi quan sát", không nghĩa shadow chạy đúng).
- mode=shadow nhưng registry thiếu/hỏng ⇒ WARN — deployment tưởng shadow bật
  nhưng hook sẽ no-op (sai lệch model vs reality).
"""
from __future__ import annotations

import logging
from typing import Any

from scp.runtime_audit.models import ProbeResult, Severity, utc_now_iso

logger = logging.getLogger(__name__)

PROBE_ID = "brain.layer_status"


class BrainLayerProbe:
    """Observe brain layer mode/config reality via the same load path as
    BrainLayer (fail-closed). Never raises; never claims health it cannot see."""

    probe_id = PROBE_ID

    def run(self) -> list[ProbeResult]:
        results: list[ProbeResult] = []
        details: dict[str, Any] = {}
        try:
            from scp.brain_layer.brain_layer import BrainLayer  # noqa: PLC0415 — import nhẹ khi probe

            mode = BrainLayer.mode()
        except Exception as exc:  # noqa: BLE001 — không bao giờ raise khỏi probe
            logger.debug("brain layer probe mode read failed: %s", exc, exc_info=True)
            results.append(ProbeResult(
                probe_id=self.probe_id,
                severity=Severity.UNKNOWN,
                title="Brain layer mode is not observable",
                summary="Could not read SCP_BRAIN_MODE via EffectiveSettings.",
                details={"error_type": type(exc).__name__},
                observed_at=utc_now_iso(),
            ))
            return results
        details["mode"] = mode

        if mode != "shadow":
            # mode=off là trạng thái hợp lệ — nhưng không có runtime brain layer
            # để quan sát ⇒ UNKNOWN trung thực, không healthy-giả.
            results.append(ProbeResult(
                probe_id=self.probe_id,
                severity=Severity.UNKNOWN,
                title=f"Brain layer is not running (mode={mode})",
                summary=(
                    "Brain layer disabled or in an unrecognized mode; no brain "
                    "reality could be observed. This is UNKNOWN, not PASS."
                ),
                details=details,
                observed_at=utc_now_iso(),
            ))
            return results

        from scp.brain_layer.shadow import resolve_registry_path  # noqa: PLC0415

        registry_path = resolve_registry_path()
        details["registry_path"] = str(registry_path) if registry_path else ""
        if registry_path is None:
            results.append(ProbeResult(
                probe_id=self.probe_id,
                severity=Severity.WARN,
                title="Brain layer mode=shadow but no registry configured",
                summary=(
                    "SCP_BRAIN_MODE=shadow but SCP_BRAIN_REGISTRY_PATH is unset "
                    "or the file does not exist; the shadow hook will no-op."
                ),
                details=details,
                observed_at=utc_now_iso(),
            ))
            return results

        try:
            from scp.brain_layer.config import BrainConfig  # noqa: PLC0415
            from scp.brain_layer.probes import collect_brain_layer_status  # noqa: PLC0415

            config = BrainConfig.load(registry_path)
            status = collect_brain_layer_status(config)
        except Exception as exc:  # noqa: BLE001 — config hỏng ⇒ WARN, không crash audit
            logger.debug("brain layer probe registry load failed: %s", exc, exc_info=True)
            results.append(ProbeResult(
                probe_id=self.probe_id,
                severity=Severity.WARN,
                title="Brain layer registry failed to load",
                summary=(
                    f"Brain registry '{registry_path}' could not be loaded "
                    f"({type(exc).__name__}); shadow runs cannot be trusted."
                ),
                details={**details, "error_type": type(exc).__name__},
                observed_at=utc_now_iso(),
            ))
            return results

        details.update({
            "config_hash": status.get("config_hash"),
            "contract_version": status.get("contract_version"),
            "tasks": status.get("tasks"),
            "providers": status.get("providers"),
            "models": status.get("models"),
            "warnings": status.get("warnings"),
        })
        results.append(ProbeResult(
            probe_id=self.probe_id,
            severity=Severity.PASS,
            title="Brain layer active in shadow mode with loadable registry",
            summary=(
                f"mode=shadow; registry loaded ({len(status.get('tasks') or [])} tasks, "
                f"{len(status.get('providers') or [])} providers); config_hash recorded. "
                "Shadow output never alters user-facing verdicts."
            ),
            details=details,
            observed_at=utc_now_iso(),
        ))
        return results
