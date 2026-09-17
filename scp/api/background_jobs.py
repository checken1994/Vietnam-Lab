# SCP CIRCUIT: M1 Boot & Background — STATUS: CLOSED (closure: reports/circuit-closures/M01-closure.json)
"""
scp/api/background_jobs.py
===========================
Background Job Registry cho SCP lifespan.

DNA #23 — Xây quá trình tự sửa: không để job quan trọng bị "quên" khi thêm mới.
DNA #19 — Missing piece: trước đây mỗi background job là ad-hoc try/except block riêng,
          không có registry → expire_leases, auto_reconcile_orphans không bao giờ được gọi.

Cách dùng:
  from scp.api.background_jobs import registry, BackgroundJob
  import time

  # Đăng ký job (thường ở module init của từng subsystem):
  @registry.register(name="kernel_watchdog", interval_seconds=30, required=True)
  def _kernel_watchdog_tick():
      from scp.api._shared import get_kernel
      k = get_kernel()
      if k:
          k.expire_leases()
          k.auto_reconcile_orphans()

  # Trong lifespan():
  registry.start_all()    # khởi động tất cả jobs
  yield
  registry.stop_all()     # dừng sạch khi shutdown
"""
from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass, field
from typing import Callable, Final

logger = logging.getLogger("scp.api.background_jobs")

# Required jobs fail closed on their first consecutive runtime error.  A
# required job is part of the readiness contract, so tolerating an error while
# reporting the service as ready would be a false-green startup.  The counter
# remains consecutive because a successful execution resets it to zero.
# Keep this constant as the single policy source; readiness and tests consume it
# instead of carrying a second, possibly divergent threshold.
REQUIRED_JOB_FAILURE_THRESHOLD: Final[int] = 1

_EXECUTION_PENDING = "pending"
_EXECUTION_RUNNING = "running"
_EXECUTION_SUCCEEDED = "succeeded"
_EXECUTION_FAILED = "failed"


@dataclass
class BackgroundJob:
    """Mô tả một background job định kỳ."""
    name: str
    fn: Callable[[], None]
    interval_seconds: float
    required: bool = False          # nếu True → lỗi khi start = fail boot
    initial_delay_seconds: float = 10.0
    _stop_event: threading.Event = field(default_factory=threading.Event, repr=False)
    _thread: threading.Thread | None = field(default=None, repr=False)
    _started: bool = field(default=False, repr=False)
    _first_execution_completed: bool = field(default=False, repr=False)
    _error_count: int = field(default=0, repr=False)
    _last_error_type: str | None = field(default=None, repr=False)
    _last_failure_id: str | None = field(default=None, repr=False)
    _readiness_revoked: bool = field(default=False, repr=False)
    _state_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def _record_success(self) -> bool:
        """Record one successful execution and return whether it is the first."""
        with self._state_lock:
            first = not self._first_execution_completed
            self._first_execution_completed = True
            self._error_count = 0
            # Keep the last failure identifier after a recovery so an observed
            # required-job fault remains traceable even if the next cycle
            # succeeds before the readiness monitor samples the state.
            if not self._readiness_revoked:
                self._last_error_type = None
                self._last_failure_id = None
            return first

    def _record_failure(self, exc: Exception) -> tuple[int, str]:
        """Record failure metadata without exposing exception payloads."""
        with self._state_lock:
            self._error_count += 1
            self._last_error_type = type(exc).__name__
            self._last_failure_id = uuid.uuid4().hex
            return self._error_count, self._last_failure_id

    def readiness_status(self) -> dict[str, object]:
        """Return an observable, fail-closed status for this job."""
        with self._state_lock:
            started = self._started
            first_execution_completed = self._first_execution_completed
            error_count = self._error_count
            last_error_type = self._last_error_type
            last_failure_id = self._last_failure_id
            readiness_revoked = self._readiness_revoked
        return {
            "started": started,
            "required": self.required,
            "interval_seconds": self.interval_seconds,
            "first_execution_completed": first_execution_completed,
            "error_count": error_count,
            "failure_threshold": REQUIRED_JOB_FAILURE_THRESHOLD if self.required else None,
            "last_error_type": last_error_type,
            "last_failure_id": last_failure_id,
            "readiness_revoked": readiness_revoked,
            "ready": (
                started
                and first_execution_completed
                and not readiness_revoked
                and (not self.required or error_count < REQUIRED_JOB_FAILURE_THRESHOLD)
            ),
        }

    def start(self) -> None:
        if self._started:
            return
        self._stop_event.clear()
        with self._state_lock:
            self._first_execution_completed = False
            self._error_count = 0
            self._last_error_type = None
            self._last_failure_id = None
            self._readiness_revoked = False

        def _run_once() -> bool:
            try:
                self.fn()
                first = self._record_success()
                if first:
                    # This log is the runtime evidence used by readiness: a
                    # thread being alive is not a successful job execution.
                    logger.info("[BackgroundJob] %s: first execution completed", self.name)
                return True
            except Exception as exc:
                error_count, failure_id = self._record_failure(exc)
                logger.warning(
                    "[BackgroundJob] %s: error #%d — %s (failure_id=%s)",
                    self.name,
                    error_count,
                    type(exc).__name__,
                    failure_id,
                )
                if self.required and error_count >= REQUIRED_JOB_FAILURE_THRESHOLD:
                    with self._state_lock:
                        self._readiness_revoked = True
                    logger.error(
                        "[BackgroundJob] %s: %d consecutive errors — readiness revoked",
                        self.name,
                        error_count,
                    )
                return False

        skip_first_loop_execution = False

        def _loop():
            # Initial delay — để server boot xong trước
            if self._stop_event.wait(self.initial_delay_seconds):
                return
            logger.info("[BackgroundJob] %s: started (interval=%.0fs)", self.name, self.interval_seconds)
            if skip_first_loop_execution:
                # Required jobs with initial_delay_seconds=0 already ran once
                # synchronously in start(); do not duplicate that side effect.
                if self._stop_event.wait(self.interval_seconds):
                    return
            while not self._stop_event.is_set():
                _run_once()
                self._stop_event.wait(self.interval_seconds)

        # For required jobs with no initial delay, run once synchronously to fail fast.
        if self.required and self.initial_delay_seconds <= 0:
            try:
                self.fn()
                first = self._record_success()
                skip_first_loop_execution = True
                if first:
                    logger.info("[BackgroundJob] %s: first execution completed", self.name)
            except Exception as exc:
                _error_count, failure_id = self._record_failure(exc)
                with self._state_lock:
                    self._readiness_revoked = True
                logger.error(
                    "[BackgroundJob] %s: required job failed synchronously — %s (failure_id=%s)",
                    self.name,
                    type(exc).__name__,
                    failure_id,
                )
                raise

        self._thread = threading.Thread(
            target=_loop,
            daemon=True,
            name=f"scp-bg-{self.name}",
        )
        with self._state_lock:
            self._started = True
        try:
            self._thread.start()
        except Exception:
            with self._state_lock:
                self._started = False
            raise

    def stop(self, timeout: float = 5.0) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        with self._state_lock:
            self._started = False


class BackgroundJobRegistry:
    """
    Registry trung tâm cho tất cả background jobs của SCP.
    
    Nguyên tắc:
    - Mỗi job đăng ký một lần, start_all() gọi một lần trong lifespan.
    - Job required=True → lỗi start = raise → server không boot.
    - Job required=False → lỗi start = warn + tiếp tục.
    - Readiness của required jobs chỉ đạt sau first successful execution; thread
      được tạo ra không tự biến thành bằng chứng job đã chạy.
    """

    def __init__(self) -> None:
        self._jobs: dict[str, BackgroundJob] = {}
        self._lock = threading.Lock()

    def register(
        self,
        name: str,
        interval_seconds: float,
        required: bool = False,
        initial_delay_seconds: float = 10.0,
    ) -> Callable[[Callable], Callable]:
        """Decorator để đăng ký một background job."""
        def decorator(fn: Callable) -> Callable:
            with self._lock:
                if name in self._jobs:
                    raise ValueError(f"BackgroundJob '{name}' đã được đăng ký.")
                self._jobs[name] = BackgroundJob(
                    name=name,
                    fn=fn,
                    interval_seconds=interval_seconds,
                    required=required,
                    initial_delay_seconds=initial_delay_seconds,
                )
            return fn
        return decorator

    def add(self, job: BackgroundJob) -> None:
        """Thêm job đã tạo sẵn vào registry."""
        with self._lock:
            if job.name in self._jobs:
                raise ValueError(f"BackgroundJob '{job.name}' đã được đăng ký.")
            self._jobs[job.name] = job

    def start_all(self) -> None:
        """Khởi động tất cả jobs. Required jobs fail → raise."""
        failed_required: list[str] = []
        with self._lock:
            jobs = list(self._jobs.values())

        for job in jobs:
            try:
                job.start()
                logger.info("[BackgroundJobRegistry] Started: %s (required=%s)", job.name, job.required)
            except Exception as exc:
                status = job.readiness_status()
                failure_id = status.get("last_failure_id")
                error_type = type(exc).__name__
                if job.required:
                    failed_required.append(
                        f"{job.name}: Required job failed ({error_type}; failure_id={failure_id})"
                    )
                    logger.error(
                        "[BackgroundJobRegistry] REQUIRED job failed to start: %s — %s (failure_id=%s)",
                        job.name,
                        error_type,
                        failure_id,
                    )
                else:
                    logger.warning(
                        "[BackgroundJobRegistry] Optional job failed to start: %s — %s (failure_id=%s)",
                        job.name,
                        error_type,
                        failure_id,
                    )

        if failed_required:
            raise RuntimeError(
                f"[BackgroundJobRegistry] {len(failed_required)} required background job(s) failed to start:\n"
                + "\n".join(f"  - {e}" for e in failed_required)
            )

    def stop_all(self, timeout: float = 5.0) -> None:
        """Dừng sạch tất cả jobs khi shutdown."""
        with self._lock:
            jobs = list(self._jobs.values())
        for job in jobs:
            try:
                job.stop(timeout=timeout)
                logger.debug("[BackgroundJobRegistry] Stopped: %s", job.name)
            except Exception as exc:
                logger.warning("[BackgroundJobRegistry] Error stopping %s: %s", job.name, exc)

    def status(self) -> dict[str, dict]:
        """Trả về trạng thái của tất cả jobs — dùng trong health/readiness."""
        with self._lock:
            return {
                name: job.readiness_status()
                for name, job in self._jobs.items()
            }

    def required_status(self) -> dict[str, dict]:
        """Return only required jobs for the readiness contract."""
        return {
            name: status
            for name, status in self.status().items()
            if status["required"]
        }

    def required_ready(self) -> bool:
        """Required jobs are ready only after successful execution."""
        statuses = self.required_status()
        return bool(statuses) and all(bool(status["ready"]) for status in statuses.values())


# =============================================================================
# Global registry — import và dùng ở bất kỳ đâu
# =============================================================================
registry = BackgroundJobRegistry()


# =============================================================================
# Đăng ký các jobs BẮT BUỘC của SCP kernel
# =============================================================================

def _get_kernel_or_none():
    """Resolve the live shared TaskKernel, or None if the app has not opened one yet.

    [MACH1-FIX-1] Reality check (DNA #26): ``scp.api._shared`` never defined
    ``get_kernel`` (its PEP 562 delegation list does not include it), so the old
    ``from scp.api._shared import get_kernel`` raised ImportError on every cycle
    and the required watchdogs silently no-op'd (ImportError → pass). Resolution
    order now:
      1. scp.api._shared.get_kernel — future contract, kept first.
      2. scp.api_server._ASK_KERNEL_ADAPTERS — reuse kernels already opened by
         the running app. Reuse only: never triggers adapter initialization
         side effects (integrity check/backup) from a watchdog thread.
    """
    try:
        from scp.api._shared import get_kernel  # type: ignore[attr-defined]
        kernel = get_kernel()
        if kernel is not None:
            return kernel
    except (ImportError, AttributeError) as exc:
        # [MACH1-FIX-8 / D6 fail-loudly] Expected until scp.api._shared exposes
        # get_kernel — debug level keeps the fallback observable without noise.
        logger.debug("[MACH1-FIX-1] scp.api._shared.get_kernel unavailable (%s) — using adapter kernels", exc)
    try:
        from scp import api_server  # runtime import — safe after boot
        for adapter in list(getattr(api_server, "_ASK_KERNEL_ADAPTERS", {}).values()):
            kernel = getattr(adapter, "kernel", None)
            if kernel is not None:
                return kernel
    except Exception as exc:
        # [MACH1-FIX-8 / D6 fail-loudly] A broken fallback means the required
        # watchdogs would silently no-op — this must be visible.
        logger.warning("[MACH1-FIX-1] kernel resolver fallback failed: %s", exc, exc_info=True)
    return None


@registry.register(
    name="kernel_lease_expiry",
    interval_seconds=30,
    required=True,          # thiếu watchdog = owner lockout vĩnh viễn → PHẢI chạy
    # Execute once synchronously during startup.  Registry membership/thread
    # creation is not readiness evidence; this keeps the required execution
    # observable without imposing a 15-second false-pending window.
    initial_delay_seconds=0,
)
def _kernel_lease_expiry_tick() -> None:
    """Hết hạn các lease bị timeout — ngăn owner lockout vĩnh viễn."""
    # [MACH1-FIX-1] resolve the real live kernel instead of the broken
    # _shared.get_kernel import; None = app has not opened a kernel yet.
    kernel = _get_kernel_or_none()
    if kernel is not None:
        expired = kernel.expire_leases()
        if expired:
            logger.info("[Watchdog] expire_leases: %d expired", len(expired))


@registry.register(
    name="kernel_orphan_reconcile",
    interval_seconds=60,
    required=True,          # task orphan không được reconcile = task bị kẹt mãi mãi
    # Same startup proof contract as the lease watchdog above.
    initial_delay_seconds=0,
)
def _kernel_orphan_reconcile_tick() -> None:
    """Reconcile các task orphan (crash, mất kết nối) → RECONCILING state."""
    # [MACH1-FIX-1] same resolver fix as _kernel_lease_expiry_tick.
    kernel = _get_kernel_or_none()
    if kernel is not None:
        orphans = kernel.auto_reconcile_orphans()
        if orphans:
            logger.info("[Watchdog] auto_reconcile_orphans: %d reconciled", len(orphans))


@registry.register(
    name="canary_token_cleanup",
    interval_seconds=86400,  # 24h
    required=False,
    initial_delay_seconds=600,
)
def _canary_cleanup_tick() -> None:
    """Dọn dẹp canary token hết hạn — ngăn memory leak."""
    try:
        from scp.api._shared import _get_judge_lazy  # type: ignore[import]
        judge = _get_judge_lazy()
        cm = getattr(judge, "canary_monitor", None)
        if cm is not None and hasattr(cm, "cleanup_expired"):
            removed = cm.cleanup_expired()
            if removed:
                logger.info("[Watchdog] canary_cleanup: removed %d expired tokens", removed)
    except Exception as exc:
        # [MACH1-FIX-8 / D6 fail-loudly] The job is optional (required=False), so
        # a failure must not abort boot — but it must be observable, otherwise
        # expired tokens leak and the registry error counter is the only trace.
        logger.warning("[Watchdog] canary_cleanup failed: %s", exc, exc_info=True)
