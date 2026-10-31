# SCP CIRCUIT: M12 — STATUS: CLOSED_WITH_KNOWN_GAP (closure: docs/evidence-summary/M12-closure.json)
"""
Mảnh ghép #11 — Cronjob of Doubt: vòng nghi ngờ TỰ KÍCH HOẠT.

TẠI SAO tồn tại (Chain Audit #11): SCP không hề tự kích hoạt vòng kiểm
toán — nó cần "Gà" gõ phím "Tôi không tin". Sự tự chủ đó là giả tạo: nếu
người vận hành biến mất, hệ thống dừng lại và tự huyễn hoặc rằng nó đã
hoàn hảo. Cronjob of Doubt là ngọn lửa "Nghi ngờ mặc định" chạy nền: ĐỊNH
KỲ tự bóc trần chính nó theo 4 phép kiểm không xin phép:

  1. FITNESS DRIFT  — Golden Suite chạy lại: accuracy có tụt so với baseline
     không? (tiến hóa mù = random mutation, DNA #22)
  2. KERNEL INTEGRITY — quick_check + hash-chain toàn bộ journal còn nguyên?
  3. ESCALATION BACKLOG — bao nhiêu task đang kẹt HUMAN_REVIEW/UNKNOWN?
     (ngập lụt review là dấu hiệu tầng verification đang ốm)
  4. WHY GATE ANOMALY — tỉ lệ REJECT gần đây có bất thường không?

Fail-safe: mỗi check bọc try/except riêng — một check lỗi không chết vòng;
mọi kết quả (kể cả lỗi) ghi vào data/doubt_ledger.jsonl làm bằng chứng.
Khác mảnh #53: KHÔNG tự inject lỗi — chỉ đo lường và BÁO CÁO cho người.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

logger = logging.getLogger("scp.core.doubt_cron")

DOUBT_LEDGER_DEFAULT = Path("data") / "doubt_ledger.jsonl"

# [FU-DOUBTCRON] Khóa duy nhất mà fallback .env của _check_kernel_integrity được nạp: đúng các
# biến journal verify của kernel đọc (scp/task_kernel_parts/definitions.py): journal_secret ->
# SCP_JOURNAL_SECRET (+ tách khóa với SCP_CAPABILITY_SECRET); previous_journal_secrets ->
# SCP_PREVIOUS_JOURNAL_SECRETS; legacy_capability_journal_keys (cửa sổ dual-accept R5) ->
# SCP_CAPABILITY_SECRET + SCP_PREVIOUS_CAPABILITY_SECRETS. Mọi key khác của .env không được nạp.
_KERNEL_JOURNAL_DOTENV_KEYS = frozenset({
    "SCP_JOURNAL_SECRET",
    "SCP_PREVIOUS_JOURNAL_SECRETS",
    "SCP_CAPABILITY_SECRET",
    "SCP_PREVIOUS_CAPABILITY_SECRETS",
})


def _check_fitness(data_dir: str) -> dict[str, Any]:
    from scp.core.fitness_engine import gate, load_baseline, run_suite

    report = run_suite()
    prev = load_baseline()
    verdict = gate(prev, report)
    return {
        "check": "fitness_drift",
        "ok": verdict["verdict"] != "ROLLBACK",
        "detail": {
            "accuracy": report["decision_accuracy"],
            "false_accept_rate": report["false_accept_rate"],
            "avg_decision_ms": report["avg_decision_ms"],
            "verdict": verdict["verdict"],
            "reasons": verdict["reasons"],
        },
    }


def _kernel_db_targets(data_dir: str) -> list[tuple[str, Path]]:
    """[JOURNAL-CHAIN] Các DB kernel mà runtime THẬT SỰ dùng, không chỉ tên mặc định.

    Bằng chứng 2026-10-31: container đặt SCP_KERNEL_DB_PATH=/app/data/task_kernel.sqlite3 (/ask ghi
    vào đó) nhưng vòng Nghi ngờ + boot recovery chỉ đọc <data_dir>/ask_task_kernel.sqlite3 — một DB do
    tiến trình host/test ghi qua bind-mount. DB production chưa từng được kiểm chuỗi. Kiểm CẢ HAI
    (DB đã cấu hình + DB mặc định nếu có), mỗi DB một nhãn riêng.
    """
    targets: list[tuple[str, Path]] = []
    configured = os.environ.get("SCP_KERNEL_DB_PATH", "").strip()
    if configured:
        targets.append((Path(configured).name, Path(configured)))
    default = Path(data_dir) / "ask_task_kernel.sqlite3"
    if not configured or Path(configured).resolve() != default.resolve():
        targets.append((default.name, default))
    return targets


def _verify_kernel_db(label: str, db_path: Path) -> dict[str, Any]:
    from scp.task_kernel import TERMINAL, TaskKernel

    kernel = TaskKernel(db_path)
    try:
        integrity = kernel.verify_integrity()
    finally:
        kernel.close()
    invalid = integrity.get("invalid_chains") or []
    by_class: dict[str, int] = {}
    for chain in invalid:
        cls = str(chain.get("integrity_class") or "UNCLASSIFIED")
        by_class[cls] = by_class.get(cls, 0) + 1
    # Task CHƯA kết thúc trên chuỗi không chứng minh được = task kernel đang HOLD (không lease/resume/
    # hoàn tất được) — báo ra để người và SCP thấy phạm vi, journal không bị sửa/xóa.
    held = [
        {"task_id": c["task_id"], "state": c.get("state"), "integrity_class": c.get("integrity_class")}
        for c in invalid
        if c.get("state") and c.get("state") not in TERMINAL
    ]
    return {
        "db": label,
        "quick_check": integrity.get("quick_check"),
        "tasks": integrity.get("tasks"),
        "invalid_chains_count": len(invalid),
        "by_class": by_class,
        "held_tasks": held[:20],
        "held_count": len(held),
        "sample_invalid": invalid[:3],
    }


def _check_kernel_integrity(data_dir: str) -> dict[str, Any]:
    targets = [(label, path) for label, path in _kernel_db_targets(data_dir) if path.exists()]
    if not targets:
        return {"check": "kernel_integrity", "ok": True, "detail": "no kernel db yet"}
    # [R5] kernel journal verifies under K_journal. [FU-DOUBTCRON] Kênh K_journal đã cấu hình
    # (env HOẶC SCP_JOURNAL_SECRET_FILE, HC-08) -> không đụng .env; _FILE hỏng là fail-closed
    # của secret_registry, không lấy .env bù. Fallback .env chỉ nạp khóa journal verify cần.
    journal_channel = os.environ.get("SCP_JOURNAL_SECRET") or os.environ.get("SCP_JOURNAL_SECRET_FILE", "").strip()
    if not journal_channel:
        env_file = Path(__file__).resolve().parents[2] / ".env"
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
                if "=" in line and not line.strip().startswith("#"):
                    k, v = line.split("=", 1)
                    k = k.strip()
                    if k in _KERNEL_JOURNAL_DOTENV_KEYS and k not in os.environ:
                        os.environ[k] = v.strip().strip("\"'")
    reports = [_verify_kernel_db(label, path) for label, path in targets]
    ok = all(r["quick_check"] == "ok" and not r["invalid_chains_count"] for r in reports)
    by_class: dict[str, int] = {}
    for r in reports:
        for cls, n in r["by_class"].items():
            by_class[cls] = by_class.get(cls, 0) + n
    detail = {
        # khóa cũ giữ nguyên nghĩa (tổng mọi DB đích) cho consumer/ledger hiện có
        "quick_check": "ok" if all(r["quick_check"] == "ok" for r in reports) else
        next(str(r["quick_check"]) for r in reports if r["quick_check"] != "ok"),
        "invalid_chains_count": sum(r["invalid_chains_count"] for r in reports),
        "sample_invalid": [s for r in reports for s in r["sample_invalid"]][:3],
        "by_class": by_class,
        "held_count": sum(r["held_count"] for r in reports),
        "targets": reports,
    }
    return {"check": "kernel_integrity", "ok": ok, "detail": detail}


def _check_escalation_backlog(data_dir: str) -> dict[str, Any]:
    targets = [(label, path) for label, path in _kernel_db_targets(data_dir) if path.exists()]
    if not targets:
        return {"check": "escalation_backlog", "ok": True, "detail": "no kernel db yet"}
    from scp.task_kernel import TaskKernel

    backlog: dict[str, int] = {}
    per_db: dict[str, int] = {}
    for label, db_path in targets:
        kernel = TaskKernel(db_path)
        try:
            # [HC-21 2026-10-23] Kernel accessor pending_review_breakdown thay SQL
            # thô GROUP BY — contract đếm backlog nằm trong kernel.
            part = kernel.pending_review_breakdown()
        finally:
            kernel.close()
        per_db[label] = sum(part.values())
        for state, n in part.items():
            backlog[state] = backlog.get(state, 0) + n
    total = sum(backlog.values())
    # Ngưỡng cảnh báo: >500 task kẹt = tầng verification đang ốm
    return {
        "check": "escalation_backlog",
        "ok": total <= int(os.environ.get("SCP_DOUBT_BACKLOG_LIMIT", "500")),
        "detail": {"backlog": backlog, "total": total, "per_db": per_db},
    }


def _check_why_gate_anomaly(data_dir: str) -> dict[str, Any]:
    audit_path = Path(data_dir) / "why_gate_audit.jsonl"
    if not audit_path.exists():
        return {"check": "why_gate_anomaly", "ok": True, "detail": "no audit yet"}
    lines = audit_path.read_text(encoding="utf-8", errors="replace").splitlines()[-500:]
    reject = sum(1 for line in lines if '"decision": "REJECT"' in line)
    total = len(lines)
    reject_rate = reject / total if total else 0.0
    return {
        "check": "why_gate_anomaly",
        "ok": reject_rate <= 0.5,
        "detail": {"recent": total, "reject": reject, "reject_rate": round(reject_rate, 3)},
    }


CHECKS: tuple[Callable[[str], dict[str, Any]], ...] = (
    _check_fitness,
    _check_kernel_integrity,
    _check_escalation_backlog,
    _check_why_gate_anomaly,
)


def _rotate_jsonl_if_needed(path: Path, max_bytes: int = 10 * 1024 * 1024, max_backups: int = 3) -> None:
    """Rotate ledger file when it exceeds max_bytes, keeping up to max_backups."""
    try:
        if not path.exists() or path.stat().st_size < max_bytes:
            return
        for i in range(max_backups - 1, 0, -1):
            src = path.with_name(f"{path.name}.{i}")
            dst = path.with_name(f"{path.name}.{i + 1}")
            if src.exists():
                src.replace(dst)
        first_backup = path.with_name(f"{path.name}.1")
        path.replace(first_backup)
    except (OSError, RuntimeError) as exc:
        logger.debug("doubt_cron: log rotation skipped: %s", exc, exc_info=True)


# [JOURNAL-CHAIN] None = wire ErrorStore đã duyệt (FalsificationEngine). Test có thể chỉ một kho tạm.
_LESSON_STORE_FACTORY: Callable[[str], Any] | None = None


def run_doubt_cycle(data_dir: str = "data", interval_seconds: float | None = None) -> dict[str, Any]:
    """Chạy 1 vòng nghi ngờ đầy đủ. Không bao giờ raise (fail-safe per check)."""
    started = time.time()
    checks: list[dict[str, Any]] = []
    # resolve động theo tên — cho phép test/module monkeypatch từng check
    display_names = {
        "_check_fitness": "fitness_drift",
        "_check_kernel_integrity": "kernel_integrity",
        "_check_escalation_backlog": "escalation_backlog",
        "_check_why_gate_anomaly": "why_gate_anomaly",
    }
    for check_ref in CHECKS:
        check = globals().get(check_ref.__name__, check_ref)
        display = display_names.get(check_ref.__name__, check_ref.__name__)
        try:
            checks.append(check(data_dir))
        except Exception as exc:
            # silent-by-design: the failure is carried in the report entry below (ok=False + detail).
            logger.debug("doubt_cron: check %s failed: %s", display, exc, exc_info=True)
            checks.append({"check": display, "ok": False, "detail": f"{type(exc).__name__}: {str(exc)[:150]}"})
    report = {
        "ran_at": started,
        "verdict": "CLEAN" if all(c.get("ok") for c in checks) else "DOUBT_DETECTED",
        "checks": checks,
    }
    # [JOURNAL-CHAIN] Phản ứng: báo động thành TRẠNG THÁI BỀN theo nguyên nhân gốc (đếm, lần đầu/
    # lần cuối) + đúng 1 bài học HYPOTHESIS mỗi lần nguyên nhân mở; /ready đọc trạng thái này.
    # Lỗi ở đây không được làm chết vòng và không được nuốt im (WARNING + ghi vào report/ledger).
    alarms: dict[str, Any]
    try:
        from scp.core import doubt_alarm

        alarms = doubt_alarm.record_cycle(
            data_dir,
            report,
            interval_seconds=interval_seconds,
            lesson_store_factory=_LESSON_STORE_FACTORY,
        )
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RuntimeError, ImportError) as exc:
        # fail-safe như mọi check: lỗi đi vào report/ledger + WARNING, không làm chết vòng
        logger.warning("[DOUBT] alarm state update failed: %s", type(exc).__name__, exc_info=True)
        alarms = {"error": f"{type(exc).__name__}: {str(exc)[:150]}"}
    report["alarms"] = {k: v for k, v in alarms.items() if k != "alarms"}
    ledger = Path(data_dir) / "doubt_ledger.jsonl"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    max_bytes = int(os.environ.get("SCP_DOUBT_LEDGER_MAX_BYTES", str(10 * 1024 * 1024)))
    _rotate_jsonl_if_needed(ledger, max_bytes=max_bytes)
    with ledger.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(report, ensure_ascii=False) + "\n")
    report["alarms"] = alarms
    return report


class DoubtCron:
    """Vòng nghi ngờ chạy nền theo interval. Đứng lên bằng thread daemon."""

    def __init__(self, data_dir: str = "data", interval_seconds: float | None = None):
        self.data_dir = data_dir
        self.interval = float(
            interval_seconds
            if interval_seconds is not None
            else os.environ.get("SCP_DOUBT_INTERVAL_SEC", "21600")  # mặc định 6h
        )
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_report: dict[str, Any] | None = None

    def run_once(self) -> dict[str, Any] | None:
        """[HC-39 · 2026-10-24] Một vòng doubt — thân _loop tách ra để
        BackgroundJobRegistry bọc làm background job (registry thấy được trong
        status()/stop_all()). Trả last_report.

        [JOURNAL-CHAIN 2026-10-31] Báo động lặp lại KHÔNG còn bị hạ xuống DEBUG: mỗi vòng còn báo
        động mở là một WARNING có số lần/lần đầu (chu kỳ 6h nên không ồn); lần đầu in chi tiết.
        Trạng thái bền nằm ở doubt_alarm (data_dir/doubt_alarm_state.json), không ở RAM.
        """
        try:
            self.last_report = run_doubt_cycle(self.data_dir, interval_seconds=self.interval)
            alarms = self.last_report.get("alarms") or {}
            if self.last_report["verdict"] != "CLEAN":
                active = alarms.get("alarms") or {}
                opened = set(alarms.get("opened") or [])
                for key in sorted(active):
                    alarm = active[key]
                    if key in opened:
                        logger.warning("[DOUBT] %s opened: %s %s", self.last_report["verdict"], key,
                                       json.dumps(alarm.get("last_detail"), ensure_ascii=False, default=str)[:400])
                    else:
                        logger.warning("[DOUBT] %s persisting: %s occurrences=%s first_seen=%s",
                                       self.last_report["verdict"], key, alarm.get("occurrences"),
                                       alarm.get("first_seen"))
                if not active:
                    logger.warning("[DOUBT] %s: %s", self.last_report["verdict"],
                                   json.dumps(self.last_report["checks"], ensure_ascii=False, default=str)[:400])
            for key in alarms.get("cleared") or []:
                logger.info("[DOUBT] alarm cleared by evidence: %s", key)
        except Exception as exc:
            logger.warning("[DOUBT] cycle failed (non-fatal): %s", exc, exc_info=True)
        return self.last_report

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()

        def _loop() -> None:
            # Chạy ngay 1 vòng đầu (nghi ngờ không cần chờ 6 tiếng đầu tiên)
            while not self._stop.is_set():
                self.run_once()
                self._stop.wait(self.interval)

        self._thread = threading.Thread(target=_loop, daemon=True, name="scp-doubt-cron")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()


_CRON: DoubtCron | None = None
_CRON_LOCK = threading.Lock()


def get_doubt_cron(data_dir: str = "data") -> DoubtCron:
    global _CRON
    if _CRON is None:
        with _CRON_LOCK:
            if _CRON is None:
                _CRON = DoubtCron(data_dir=data_dir)
    return _CRON
