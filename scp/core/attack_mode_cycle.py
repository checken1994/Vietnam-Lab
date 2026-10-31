"""[HEARTBEAT-RECOVERY 2026-10-31] Một chu kỳ attack-mode monitor — đếm TRUNG THỰC.

Trước: ``kill_count = notif.count_recent_by_type('governance_kill', ...)`` luôn 0 vì
không code nào phát ``governance_kill`` (88/88 chu kỳ lịch sử = 0) ⇒ chu kỳ ghi
SUCCESS ``asked=0`` như thể đã đếm và thấy "không bị tấn công" (placebo).

Sau: tín hiệu chỉ được đếm khi có nguồn phát đã đăng ký
(``scp.runtime.notifications.register_event_source``). Không nguồn / lỗi ⇒ chu kỳ
``UNKNOWN`` + ``signal_state`` ghi rõ, KHÔNG đổi attack mode (không biết ⇒ không
quyết), một bài học HYPOTHESIS. Liveness coi UNKNOWN liên tiếp là "không thành công"
⇒ /ready báo monitor degraded thay vì im lặng.
"""
from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

SIGNAL_EVENT = "governance_kill"
ENABLE_ABOVE = 20
DISABLE_BELOW = 5
WINDOW_S = 600


def run_attack_monitor_cycle(telemetry: Any, run_id: str, notif: Any, engine: Any) -> dict[str, Any]:
    cutoff = time.time() - WINDOW_S
    if notif is None:
        sig: dict[str, Any] = {"state": "NO_NOTIFIER", "count": None, "sources": []}
    elif hasattr(notif, "count_recent_signal"):
        sig = notif.count_recent_signal(SIGNAL_EVENT, cutoff)
    else:
        sig = {"state": "NO_SOURCE_API", "count": None, "sources": []}
    if sig.get("state") != "COUNTED":
        logger.warning("[AUTO] Attack mode monitor: signal %r state=%s — attack mode NOT judged "
                       "(no producer ⇒ not reported as 0)", SIGNAL_EVENT, sig.get("state"))
        from scp.core.heartbeat_store import record_lesson

        record_lesson("monitor_signal_no_source", f"attack_monitor:{SIGNAL_EVENT}",
                      {"signal": SIGNAL_EVENT, "signal_state": str(sig.get("state"))})
        telemetry.cycle_completed(run_id, "UNKNOWN", signal=SIGNAL_EVENT, signal_state=sig.get("state"))
        return sig
    kill_count = int(sig["count"])
    if kill_count > ENABLE_ABOVE and not engine.in_attack_mode:
        engine.set_attack_mode(True)
        logger.warning("[AUTO] Attack mode ENABLED — %s KILLs in 10min", kill_count)
    elif kill_count < DISABLE_BELOW and engine.in_attack_mode:
        engine.set_attack_mode(False)
        logger.info("[AUTO] Attack mode DISABLED — %s KILLs in 10min", kill_count)
    telemetry.cycle_completed(run_id, "SUCCESS", asked=kill_count, verified=1, signal=SIGNAL_EVENT,
                              signal_state="COUNTED", signal_sources=sig.get("sources", []))
    return sig


__all__ = ["run_attack_monitor_cycle", "SIGNAL_EVENT"]
