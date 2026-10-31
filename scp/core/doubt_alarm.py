"""[JOURNAL-CHAIN 2026-10-31] Trạng thái báo động BỀN của vòng Nghi ngờ + bài học HYPOTHESIS + /ready.

TẠI SAO tồn tại: từ 2026-10-27 vòng Nghi ngờ (doubt_cron) báo ``DOUBT_DETECTED`` cho chuỗi journal
kernel hơn 2.000 lần; phản ứng duy nhất là 1 dòng WARNING, lặp lại thì tự hạ xuống DEBUG; không ai
đọc ledger; /ready vẫn báo "ready". SCP biết bộ nhớ công việc của mình có thể đã bị sửa mà không
làm gì. Module này biến một báo động thành:

  1. TRẠNG THÁI BỀN theo NGUYÊN NHÂN GỐC (khóa = check + phạm vi + lớp nguyên nhân), có
     ``occurrences`` / ``first_seen`` / ``last_seen`` / ``consecutive``; lặp lại chỉ tăng đếm,
     không bao giờ "im" (không hạ DEBUG). Báo động chỉ được GỠ khi chính check đó chạy ra kết quả
     có cấu trúc và không còn thấy nguyên nhân (thiếu bằng chứng ≠ hết bệnh).
  2. ĐÚNG MỘT bài học HYPOTHESIS cho mỗi lần một nguyên nhân gốc MỞ (qua wire ErrorStore đã duyệt
     của FalsificationEngine — không import thẳng scp.brain), verified=False,
     promotion=never_automatic. Chỉ ghi vào kho nằm TRONG data_dir của vòng đang chạy (vòng chạy
     trên data tạm không được ghi bài học vào kho của data khác).
  3. /ready báo DEGRADED trung thực khi còn báo động mở, hoặc khi chính vòng Nghi ngờ đã ngừng
     cập nhật (stale — "im lặng là cảnh báo"). Mã HTTP của /ready KHÔNG đổi (dịch vụ vẫn phục vụ;
     HOLD thật nằm ở kernel theo phạm vi task, không phải ở /ready).

KHÔNG tự bật kill switch toàn hệ thống (quyền của người). HOLD theo phạm vi chuỗi hỏng do kernel tự
suy ra từ journal (scp/task_kernel_parts/taskkernel.py::_journal_hold), không cần file này.
Giới hạn đã biết: nhiều tiến trình cùng ghi một data_dir (host + container trên bind-mount) thì
người ghi sau thắng (đọc-sửa-ghi nguyên tử theo file, không theo hệ thống).
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("scp.core.doubt_alarm")

STATE_FILENAME = "doubt_alarm_state.json"
STATE_SCHEMA = "scp.doubt_alarm_state.v1"
_STATE_LOCK = threading.Lock()
_MAX_DETAIL_CHARS = 1200

# Lớp nguyên nhân -> giả thuyết (HYPOTHESIS, không phải kết luận). Lớp do CẤU TRÚC dữ liệu quyết
# (kernel verify_journal: cấu trúc chuỗi + tỉ lệ event lệch HMAC), không do câu chữ.
_HYPOTHESES: dict[str, str] = {
    "KEY_DOMAIN_MISMATCH": (
        "every event of the chain fails HMAC under all keys this verifier holds while the chain "
        "structure is intact: it was signed under a key domain this process does not hold "
        "(K_journal rotated without SCP_PREVIOUS_JOURNAL_SECRETS, another writer process/key such "
        "as a test or host process writing this DB, or a full rewrite by another key holder); "
        "needs key evidence, never auto-accept"
    ),
    "CONTENT_TAMPER": (
        "some events verify under the current keys and others of the same chain do not: those "
        "event bodies changed after signing (out-of-band edit or tamper)"
    ),
    "STRUCTURAL_TAMPER": (
        "sequence / prev-hash / payload / anchor of the journal broken: rows inserted, deleted or "
        "reordered outside the kernel API"
    ),
    "SQLITE_CORRUPTION": "SQLite quick_check failed: storage-level corruption of the kernel DB file",
    "CHECK_ERROR": "the doubt check itself could not run (I/O, schema, missing key): evidence unavailable",
    "THRESHOLD": "a doubt metric crossed its threshold (fitness drift, review backlog, WHY reject rate)",
}


def _now_iso(ts: float | None = None) -> str:
    return datetime.fromtimestamp(time.time() if ts is None else ts, timezone.utc).isoformat()


def _parse_iso(value: Any) -> float | None:
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except (TypeError, ValueError):
        return None


def state_path(data_dir: str | os.PathLike[str]) -> Path:
    return Path(data_dir) / STATE_FILENAME


def load_state(data_dir: str | os.PathLike[str]) -> dict[str, Any] | None:
    path = state_path(data_dir)
    if not path.exists():
        return None
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("[DOUBT-ALARM] state unreadable (%s): %s", path.name, type(exc).__name__)
        return {"schema": STATE_SCHEMA, "alarms": {}, "unreadable": True}
    return state if isinstance(state, dict) else {"schema": STATE_SCHEMA, "alarms": {}, "unreadable": True}


def _write_state(data_dir: str | os.PathLike[str], state: dict[str, Any]) -> None:
    path = state_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, sort_keys=True, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def _trim(value: Any) -> Any:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    if len(text) <= _MAX_DETAIL_CHARS:
        return value
    return {"truncated": text[:_MAX_DETAIL_CHARS]}


def alarm_signals(report: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], set[str]]:
    """Nguyên nhân gốc của MỘT vòng: (khóa -> tín hiệu, tập check đã chạy ra kết quả có cấu trúc).

    kernel_integrity tách theo (DB đích, lớp nguyên nhân) — 100 vòng cùng một lỗi chain là MỘT
    nguyên nhân, không phải 100; số chain thay đổi giữa các vòng không mở báo động mới.
    """
    signals: dict[str, dict[str, Any]] = {}
    evaluated: set[str] = set()
    for check in report.get("checks") or []:
        name = str(check.get("check") or "unknown")
        detail = check.get("detail")
        if isinstance(detail, dict):
            evaluated.add(name)
        if check.get("ok"):
            continue
        if name == "kernel_integrity" and isinstance(detail, dict) and detail.get("targets"):
            for target in detail["targets"]:
                scope = str(target.get("db") or "-")
                if target.get("quick_check") not in (None, "ok"):
                    signals[f"{name}|{scope}|SQLITE_CORRUPTION"] = {
                        "check": name, "scope": scope, "classification": "SQLITE_CORRUPTION",
                        "detail": {"quick_check": str(target.get("quick_check"))[:200]},
                    }
                for cls, count in sorted((target.get("by_class") or {}).items()):
                    signals[f"{name}|{scope}|{cls}"] = {
                        "check": name, "scope": scope, "classification": str(cls),
                        "detail": {
                            "chains": int(count),
                            "held_tasks": [h for h in target.get("held_tasks") or [] if h.get("integrity_class") == cls][:10],
                            "held_count": sum(
                                1 for h in target.get("held_tasks") or [] if h.get("integrity_class") == cls
                            ),
                            "sample_invalid": [s for s in target.get("sample_invalid") or [] if s.get("integrity_class") == cls][:3],
                        },
                    }
            if any(key.startswith(f"{name}|") for key in signals):
                continue
        cls = "CHECK_ERROR" if not isinstance(detail, dict) else "THRESHOLD"
        signals[f"{name}|-|{cls}"] = {"check": name, "scope": "-", "classification": cls, "detail": detail}
    return signals, evaluated


def _default_lesson_store(data_dir: str) -> Any:
    # scp.brain là vùng CÔ LẬP (T03 test_flow_14 ALLOWED_ISOLATED_IMPORTS): đi qua wire ErrorStore
    # đã duyệt của FalsificationEngine (cùng mẫu scp/runtime/math_lessons.py).
    from scp.meta.falsification_engine import FalsificationEngine

    return FalsificationEngine()._get_error_store()


def _record_lesson(
    data_dir: str,
    key: str,
    alarm: dict[str, Any],
    factory: Callable[[str], Any] | None,
) -> str:
    """Ghi 1 bài học HYPOTHESIS. Trả trạng thái: recorded / skipped:<lý do> / error:<loại lỗi>."""
    try:
        store = (factory or _default_lesson_store)(data_dir)
        store_path = getattr(store, "path", None)
        if store_path is None or Path(store_path).resolve().parent != Path(data_dir).resolve():
            # Kho bài học phải thuộc chính data_dir đang được nghi ngờ: vòng chạy trên data tạm
            # (test/probe) không được viết bài học vào kho sống của tiến trình (và ngược lại).
            return "skipped:store_outside_data_dir"
        cls = alarm["classification"]
        rec = store.add(
            question=f"doubt_cron {alarm['check']} {cls} @ {alarm['scope']}",
            answer=f"doubt alarm opened: {alarm['check']} ({cls}) on {alarm['scope']}",
            verdict="UNVERIFIED",
            domain="kernel_integrity" if alarm["check"] == "kernel_integrity" else "doubt",
            error_type=f"doubt_{alarm['check']}_{cls.lower()}",
            details={
                "epistemic_status": "HYPOTHESIS", "verified": False, "promotion": "never_automatic",
                "alarm_key": key, "classification": cls, "scope": alarm["scope"],
                "hypothesis": _HYPOTHESES.get(cls, "unknown cause"),
                "evidence": _trim(alarm.get("last_detail")),
                "first_seen": alarm.get("first_seen"), "observed_at": _now_iso(),
                "source": "scp.core.doubt_cron",
            },
        )
        if isinstance(rec, dict) and rec.get("rejected"):
            return f"skipped:rejected:{str(rec.get('reject_reason'))[:60]}"
        return "recorded"
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RuntimeError, ImportError) as exc:
        # bài học là best-effort: không được làm chết vòng Nghi ngờ, nhưng không nuốt im (WARNING + trạng thái error:)
        logger.warning("[DOUBT-ALARM] lesson for %s not recorded: %s", key, type(exc).__name__, exc_info=True)
        return f"error:{type(exc).__name__}"


def record_cycle(
    data_dir: str,
    report: dict[str, Any],
    *,
    interval_seconds: float | None = None,
    lesson_store_factory: Callable[[str], Any] | None = None,
) -> dict[str, Any]:
    """Cập nhật trạng thái bền từ MỘT vòng; trả tóm tắt {opened, repeated, cleared, active}."""
    signals, evaluated = alarm_signals(report)
    now = _now_iso(report.get("ran_at"))
    summary: dict[str, list[str]] = {"opened": [], "repeated": [], "cleared": [], "active": []}
    with _STATE_LOCK:
        state = load_state(data_dir) or {}
        alarms: dict[str, Any] = dict(state.get("alarms") or {})
        for key, sig in signals.items():
            alarm = alarms.get(key)
            if alarm is None or alarm.get("status") != "ACTIVE":
                alarm = {
                    "check": sig["check"], "scope": sig["scope"], "classification": sig["classification"],
                    "status": "ACTIVE", "first_seen": now, "occurrences": 0, "consecutive": 0,
                    "lesson": None, "opened_count": int((alarm or {}).get("opened_count") or 0) + 1,
                }
                summary["opened"].append(key)
            else:
                summary["repeated"].append(key)
            alarm["occurrences"] = int(alarm.get("occurrences") or 0) + 1
            alarm["consecutive"] = int(alarm.get("consecutive") or 0) + 1
            alarm["last_seen"] = now
            alarm["last_detail"] = _trim(sig.get("detail"))
            alarm.pop("cleared_at", None)
            if not alarm.get("lesson") or str(alarm["lesson"]).startswith("error:"):
                alarm["lesson"] = _record_lesson(data_dir, key, alarm, lesson_store_factory)
            alarms[key] = alarm
        for key, alarm in alarms.items():
            if key in signals or alarm.get("status") != "ACTIVE":
                continue
            # Chỉ gỡ khi chính check đó đã chạy ra kết quả có cấu trúc trong vòng này.
            if alarm.get("check") in evaluated:
                alarm["status"] = "CLEARED"
                alarm["cleared_at"] = now
                alarm["consecutive"] = 0
                summary["cleared"].append(key)
        summary["active"] = sorted(k for k, a in alarms.items() if a.get("status") == "ACTIVE")
        state = {
            "schema": STATE_SCHEMA,
            "updated_at": _now_iso(),
            "last_cycle_at": now,
            "last_verdict": report.get("verdict"),
            "interval_seconds": float(interval_seconds) if interval_seconds else state.get("interval_seconds"),
            "alarms": alarms,
        }
        _write_state(data_dir, state)
    summary["alarms"] = {k: alarms[k] for k in summary["active"]}  # type: ignore[assignment]
    return summary


def _stale_after(interval: float | None) -> float:
    interval = float(interval or 21600.0)
    return max(2.0 * interval, interval + 900.0)


def readiness(data_dir: str | os.PathLike[str], now: float | None = None) -> dict[str, Any]:
    """Mục /ready từ trạng thái bền (không chạy verify — rẻ, không I/O nặng).

    unknown: chưa có vòng nào (thiếu bằng chứng ≠ hỏng, không đổi status);
    stale: đã từng chạy nhưng ngừng cập nhật quá 2 chu kỳ ⇒ degraded;
    degraded: còn báo động ACTIVE; ok: không có báo động mở.
    """
    state = load_state(data_dir)
    if state is None:
        return {"checks": {"doubt": "unknown", "kernel_journal": "unknown"}, "degraded": False}
    alarms = state.get("alarms") or {}
    active = {k: a for k, a in alarms.items() if a.get("status") == "ACTIVE"}
    kernel_active = {k: a for k, a in active.items() if a.get("check") == "kernel_integrity"}
    checks: dict[str, str] = {"kernel_journal": "degraded" if kernel_active else "ok"}
    last = _parse_iso(state.get("last_cycle_at"))
    current = time.time() if now is None else float(now)
    stale = state.get("unreadable") or last is None or current - last > _stale_after(state.get("interval_seconds"))
    if stale:
        checks["doubt"] = "stale"
    else:
        checks["doubt"] = "degraded" if active else "ok"
    if active:
        parts = []
        for key in sorted(active):
            a = active[key]
            detail = a.get("last_detail") if isinstance(a.get("last_detail"), dict) else {}
            extra = f"x{detail['chains']}(held={detail.get('held_count', 0)})" if "chains" in detail else ""
            parts.append(f"{a.get('check')}:{a.get('classification')}@{a.get('scope')}{extra}#{a.get('occurrences')}")
        checks["doubt_active_alarms"] = ";".join(parts)[:400]
        firsts = [a.get("first_seen") for a in active.values() if a.get("first_seen")]
        if firsts:
            checks["doubt_alarm_since"] = min(firsts)
    return {"checks": checks, "degraded": bool(active) or bool(stale)}


def apply_readiness(payload: dict[str, Any], data_dir: str | os.PathLike[str]) -> dict[str, Any]:
    """Gộp mục doubt/kernel_journal vào payload /ready; status 'ready' -> 'degraded' khi cần."""
    try:
        result = readiness(data_dir)
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        # /ready không được sập vì trạng thái doubt; không đọc được = degraded (fail-closed), không phải ok
        logger.warning("[DOUBT-ALARM] readiness unavailable: %s", type(exc).__name__)
        result = {"checks": {"doubt": f"error:{type(exc).__name__}"}, "degraded": True}
    payload.setdefault("checks", {}).update(result["checks"])
    if result["degraded"] and payload.get("status") == "ready":
        payload["status"] = "degraded"
    return payload
