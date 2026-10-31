"""[HEARTBEAT-RECOVERY 2026-10-31] Storage guard for ``subsystem_heartbeat.sqlite``.

Tại sao module này tồn tại (bằng chứng, không phải giả định):
- 30/10 ~05:09Z file heartbeat sống (bind-mount ``D:\\scp\\data`` → ``/app/data``) bị
  ghi ĐỒNG THỜI bởi container Linux (pid 1) và tiến trình Windows trên host
  (pytest/tool chạy từ ``D:/scp`` với ``data_dir`` mặc định ``data``). SQLite WAL cần
  khoá + ``-shm`` dùng chung; hai nhân (Windows host / VM WSL2) KHÔNG chia sẻ chúng ⇒
  không có loại trừ lẫn nhau ⇒ file hỏng. Tái hiện: host+container cùng file ⇒
  "malformed"/"not a database" trong vài giây; đối chứng cùng-nhân (3 tiến trình host,
  2 container, kill -9 giữa giao dịch) sạch.
- Hệ quả cũ: ``SubsystemTelemetry.__init__`` raise ⇒ attack monitor + deep audit
  không đăng ký, chỉ còn 1 dòng warning; ``heartbeat_sleep`` raise ⇒ vòng monitor chết.

Module này cho SCP tự chặn + tự phục hồi từ bằng chứng của chính nó (không cần người
duyệt, không im lặng):
1. ``KernelGuard``: mỗi file heartbeat có "chủ nhân" là MỘT nhân hệ điều hành (sidecar
   ``.owner.json``). Tiến trình thuộc nhân KHÁC khi chủ còn tươi ⇒ dùng file riêng theo
   nhân (không bao giờ chạm file dùng chung) + sự kiện có cấu trúc + bài học HYPOTHESIS.
2. ``quarantine_corrupt``: lỗi SQLITE_CORRUPT/SQLITE_NOTADB (mã lỗi, không so chuỗi)
   ⇒ kiểm lại độc lập ⇒ đổi tên (timestamp + sha256, giữ nguyên làm bằng chứng) kèm
   manifest JSON ⇒ caller tạo DB mới. Trần bền vững (đếm manifest trên đĩa, restart
   không vượt được) + kill switch env.

Kill switch (người ở trên cùng): ``SCP_HEARTBEAT_AUTO_QUARANTINE=0`` (không đổi tên gì;
telemetry chuyển UNAVAILABLE, monitor vẫn chạy, /ready báo degraded),
``SCP_HEARTBEAT_KERNEL_GUARD=0`` (tắt guard nhân).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import sqlite3
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

# SQLite primary result codes (sqlite3.h): SQLITE_CORRUPT=11, SQLITE_NOTADB=26.
CORRUPTION_CODES = frozenset({11, 26})
QUARANTINE_INFIX = ".corrupt-"
OWNER_SUFFIX = ".owner.json"
_DAY_S = 86400.0

# Test seam: factory trả về object có ``.add(question, answer, verdict, domain,
# error_type, details)``. None ⇒ wire ErrorStore đã duyệt của FalsificationEngine.
LESSON_STORE_FACTORY: Callable[[], Any] | None = None

_lock = threading.Lock()
_lesson_seen: set[tuple[str, str]] = set()
_store_cache: dict[str, Any] = {}


def _env_on(name: str, default: str = "1") -> bool:
    return os.environ.get(name, default).strip().lower() not in {"0", "false", "off", "no"}


def _env_float(name: str, default: float, minimum: float) -> float:
    try:
        return max(minimum, float(os.environ.get(name, "") or default))
    except ValueError:
        return default


def _utc_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def kernel_identity() -> str:
    """Danh tính nhân hệ điều hành của tiến trình hiện tại.

    Linux: ``boot_id`` (mọi container trên cùng VM WSL2 chung nhân ⇒ chung danh tính;
    khoá POSIX giữa chúng hoạt động — đã đối chứng). Khác: ``platform:node``."""
    try:
        boot = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
        if boot:
            return f"linux:{boot}"
    except OSError:
        logger.debug("heartbeat_store: no linux boot_id; using platform identity", exc_info=True)
    return f"{sys.platform}:{platform.node()}"


def is_corruption(exc: BaseException) -> bool:
    if not isinstance(exc, sqlite3.DatabaseError):
        return False
    code = getattr(exc, "sqlite_errorcode", None)
    return code is not None and (int(code) & 0xFF) in CORRUPTION_CODES


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _still_corrupt(db: Path) -> bool:
    """Kiểm lại độc lập (kết nối riêng, ``quick_check``) trước khi đổi tên bất cứ gì."""
    try:
        conn = sqlite3.connect(db, timeout=5.0)
        try:
            row = conn.execute("PRAGMA quick_check(1)").fetchone()
        finally:
            conn.close()
        return not (row and str(row[0]).lower() == "ok")
    except sqlite3.DatabaseError as exc:
        return is_corruption(exc)


def quarantine_manifests(db: Path, within_s: float | None = None) -> list[Path]:
    now = time.time()
    out = []
    for p in db.parent.glob(f"{db.name}{QUARANTINE_INFIX}*.json"):
        try:
            if within_s is None or now - p.stat().st_mtime <= within_s:
                out.append(p)
        except OSError:
            continue
    return sorted(out)


def quarantine_corrupt(db: Path, exc: BaseException, *, subsystem: str) -> dict[str, Any]:
    """Cách ly file hỏng (giữ nguyên làm bằng chứng). Trả dict có ``quarantined``."""
    db = Path(db)
    base = {"quarantined": False, "db_path": str(db), "subsystem": subsystem,
            "error_class": type(exc).__name__, "error_code": getattr(exc, "sqlite_errorcode", None)}
    if not _env_on("SCP_HEARTBEAT_AUTO_QUARANTINE"):
        logger.error("[HEARTBEAT] %s corrupt (%s) — auto-quarantine DISABLED by kill switch; "
                     "telemetry UNAVAILABLE, monitor continues", db, base["error_code"])
        return {**base, "reason": "kill_switch"}
    cap = int(_env_float("SCP_HEARTBEAT_QUARANTINE_MAX_PER_DAY", 3, 0))
    with _lock:
        if not db.exists():
            # Một luồng/tiến trình khác vừa cách ly ⇒ caller mở DB mới.
            return {**base, "reason": "already_quarantined", "recovered": True}
        recent = quarantine_manifests(db, _DAY_S)
        if len(recent) >= cap:
            logger.error("[HEARTBEAT] %s corrupt but quarantine cap reached (%d/%d in 24h) — "
                         "NOT renaming; telemetry UNAVAILABLE until a human/hub acts", db, len(recent), cap)
            return {**base, "reason": "cap_reached", "recent_quarantines": len(recent)}
        if not _still_corrupt(db):
            logger.warning("[HEARTBEAT] %s raised %s but independent quick_check is ok — not quarantined",
                           db, base["error_code"])
            return {**base, "reason": "recheck_ok"}
        sha = _sha256(db)
        size = db.stat().st_size
        dest = db.with_name(f"{db.name}{QUARANTINE_INFIX}{_utc_compact()}-{sha[:12]}")
        try:
            os.replace(db, dest)
        except OSError as move_exc:
            logger.error("[HEARTBEAT] quarantine rename failed for %s: %s", db, move_exc, exc_info=True)
            return {**base, "reason": f"rename_failed:{type(move_exc).__name__}"}
        sidecars: dict[str, str | None] = {}
        for suffix in ("-wal", "-shm"):
            side = db.with_name(db.name + suffix)
            if side.exists():
                try:
                    side_sha = _sha256(side)
                    os.replace(side, dest.with_name(dest.name + suffix))
                    sidecars[suffix] = side_sha
                except OSError as side_exc:
                    sidecars[suffix] = f"move_failed:{type(side_exc).__name__}"
        manifest = {
            **base, "quarantined": True, "recovered": True, "quarantined_as": str(dest),
            "sha256": sha, "size_bytes": size, "sidecars_sha256": sidecars,
            "kernel": kernel_identity(), "pid": os.getpid(),
            "quarantined_at_utc": datetime.now(timezone.utc).isoformat(),
            "recent_quarantines_before": len(recent), "cap_per_day": cap,
        }
        try:
            dest.with_name(dest.name + ".json").write_text(
                json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=1), encoding="utf-8")
        except OSError:
            logger.error("[HEARTBEAT] quarantine manifest write failed for %s", dest, exc_info=True)
    logger.error("[HEARTBEAT] QUARANTINED corrupt heartbeat DB %s -> %s sha256=%s; fresh DB will be created",
                 db, dest, sha)
    record_lesson("heartbeat_db_corrupt", str(dest), manifest)
    return manifest


class EvolutionBlockedError(RuntimeError):
    """Evolution fail-closed sau khi DB heartbeat được phục hồi (xem ``evolution_block_reason``)."""


def evolution_block_reason(data_dir: str | Path, telemetry: Any = None) -> str | None:
    """Cổng fail-closed cho EvolutionEngine (đính chính coordinator 2026-10-31).

    Trên base, DB heartbeat hỏng làm ``EvolutionEngine.__init__`` raise ⇒ evolution
    *tình cờ* bị chặn. Khi module này cách ly file hỏng và tạo DB mới, evolution sẽ chạy
    lại (prod: SCP_EVOLUTION_ENABLED=1 + AUTOFIX_MODE=apply) trong khi các đường ghi
    file của evolution_parts chưa có guard protected-path (lane evolution-guard). Vì vậy:
    còn bằng chứng phục hồi (manifest cách ly) chưa được NGƯỜI xác nhận theo đúng sha
    ⇒ chặn. ``SCP_EVOLUTION_HEARTBEAT_RECOVERY_ACK`` = danh sách sha256[:12] (phẩy) của
    từng lần cách ly — một ack không mở khoá cho lần phục hồi sau. Storage telemetry
    UNAVAILABLE (file hỏng còn nguyên: cap/kill switch) ⇒ cũng chặn."""
    db = Path(data_dir) / "subsystem_heartbeat.sqlite"
    acks = {a.strip().lower() for a in os.environ.get("SCP_EVOLUTION_HEARTBEAT_RECOVERY_ACK", "").split(",") if a.strip()}
    pending = [m.name for m in quarantine_manifests(db) if m.name[: -len(".json")][-12:].lower() not in acks]
    if pending:
        return f"heartbeat_recovered_without_protected_path_guard:{len(pending)}_unacknowledged"
    if telemetry is not None and getattr(telemetry, "storage_state", "OK") == "UNAVAILABLE":
        return "heartbeat_storage_unavailable"
    return None


class KernelGuard:
    """Một file SQLite WAL chỉ có MỘT nhân ghi. Nhân lạ ⇒ file riêng theo nhân."""

    def __init__(self, primary: Path):
        self.primary = Path(primary)
        self.owner_path = self.primary.with_name(self.primary.name + OWNER_SUFFIX)
        self.me = kernel_identity()
        self._checked_at = 0.0
        self._resolved = self.primary
        self._reported: set[str] = set()
        self.state = "OWNER"

    def fallback_path(self) -> Path:
        tag = hashlib.sha256(self.me.encode("utf-8")).hexdigest()[:12]
        return self.primary.with_name(f"{self.primary.stem}.k-{tag}{self.primary.suffix}")

    def _read_owner(self) -> dict[str, Any] | None:
        try:
            data = json.loads(self.owner_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) and data.get("kernel") else None
        except (OSError, ValueError):
            return None

    def _claim(self) -> None:
        tmp = self.owner_path.with_name(f"{self.owner_path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps({"kernel": self.me, "pid": os.getpid(), "claimed_at": time.time()}),
                       encoding="utf-8")
        os.replace(tmp, self.owner_path)

    def resolve(self, subsystem: str) -> Path:
        if not _env_on("SCP_HEARTBEAT_KERNEL_GUARD"):
            self.state = "GUARD_DISABLED"
            return self.primary
        now = time.time()
        if now - self._checked_at < 30.0:
            return self._resolved
        self._checked_at = now
        ttl = _env_float("SCP_HEARTBEAT_OWNER_TTL_S", 900.0, 60.0)
        try:
            owner = self._read_owner()
            age = now - float(owner.get("claimed_at", 0) or 0) if owner else None
            foreign = owner is not None and owner.get("kernel") != self.me
            if foreign and age is not None and age <= ttl:
                self._resolved, self.state = self.fallback_path(), "FOREIGN_KERNEL_FALLBACK"
                self._report(subsystem, "foreign_kernel_fallback", owner)
                return self._resolved
            if owner is None or foreign or age is None or age > ttl / 3:
                if foreign:
                    self._report(subsystem, "foreign_claim_stale_takeover", owner)
                self._claim()
                confirmed = self._read_owner()
                if confirmed is not None and confirmed.get("kernel") != self.me:
                    self._resolved, self.state = self.fallback_path(), "FOREIGN_KERNEL_FALLBACK"
                    self._report(subsystem, "foreign_kernel_fallback", confirmed)
                    return self._resolved
        except OSError:
            logger.warning("[HEARTBEAT] owner claim I/O failed for %s — using primary", self.owner_path, exc_info=True)
        self._resolved, self.state = self.primary, "OWNER"
        return self._resolved

    def _report(self, subsystem: str, kind: str, owner: dict[str, Any] | None) -> None:
        if kind in self._reported:
            return
        self._reported.add(kind)
        details = {"kind": kind, "primary": str(self.primary), "fallback": str(self.fallback_path()),
                   "my_kernel": self.me, "owner_kernel": (owner or {}).get("kernel"),
                   "owner_pid": (owner or {}).get("pid"), "subsystem": subsystem}
        logger.warning("[HEARTBEAT] %s: %s", kind, json.dumps(details, sort_keys=True))
        record_lesson(kind, str(self.primary), details)


_HYPOTHESES = {
    "heartbeat_db_corrupt": (
        "heartbeat SQLite corrupted: most likely writers from two OS kernels (Windows host + "
        "container VM) shared one WAL file over a bind mount, so locks/-shm were not shared; "
        "alternatives: storage fault, non-durable fsync on the mount"),
    "foreign_kernel_fallback": (
        "a process on a different OS kernel tried to write the live heartbeat file "
        "(a test/tool writing live data?); it was redirected to a kernel-scoped file"),
    "foreign_claim_stale_takeover": (
        "the heartbeat file was last claimed by a different OS kernel; claim was stale so this "
        "kernel took ownership — if both are alive this is the corruption precondition"),
    "monitor_signal_no_source": (
        "a monitor counts an event type that no registered producer emits; reporting 0 would be "
        "a placebo, so the cycle is UNKNOWN until a real emitter is wired"),
}


def _lesson_store() -> Any:
    if LESSON_STORE_FACTORY is not None:
        return LESSON_STORE_FACTORY()
    with _lock:
        if "default" not in _store_cache:
            # scp.brain là vùng CÔ LẬP: đi qua wire ErrorStore đã duyệt (như math_lessons).
            from scp.meta.falsification_engine import FalsificationEngine

            _store_cache["default"] = FalsificationEngine()._get_error_store()
        return _store_cache["default"]


def record_lesson(kind: str, where: str, details: dict[str, Any]) -> bool:
    """Bài học HYPOTHESIS (không bao giờ thành tri thức verified). Dedupe theo
    (kind, where) mỗi tiến trình. Lỗi ghi ⇒ False + warning, không bao giờ raise."""
    key = (kind, where)
    with _lock:
        if key in _lesson_seen:
            return False
        _lesson_seen.add(key)
    try:
        rec = _lesson_store().add(
            question=f"heartbeat storage {kind}: {where}"[:500],
            answer=_HYPOTHESES.get(kind, "unknown cause"),
            verdict="UNVERIFIED", domain="runtime_telemetry", error_type=f"heartbeat_{kind}",
            details={"epistemic_status": "HYPOTHESIS", "verified": False, "promotion": "never_automatic",
                     "hypothesis": _HYPOTHESES.get(kind, "unknown cause"),
                     **{k: v for k, v in details.items() if isinstance(v, (str, int, float, bool, type(None), dict))}},
        )
        if isinstance(rec, dict) and rec.get("rejected"):
            logger.warning("[HEARTBEAT] lesson rejected by ErrorStore: %s", rec.get("reject_reason"))
            return False
        return True
    except Exception:  # noqa: BLE001 — bài học không được làm chết monitor
        with _lock:
            _lesson_seen.discard(key)
        logger.warning("[HEARTBEAT] lesson record failed (%s)", kind, exc_info=True)
        return False


__all__ = ["EvolutionBlockedError", "KernelGuard", "LESSON_STORE_FACTORY", "evolution_block_reason",
           "is_corruption", "kernel_identity",
           "quarantine_corrupt", "quarantine_manifests", "record_lesson"]
