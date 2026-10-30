"""Bounded single-worker deadline scheduler (esc-auto slice 1).

TẠI SAO module này tồn tại:
  ``EscalationManager`` cũ tạo MỘT ``threading.Timer`` (1 OS thread sống tới
  30 phút) cho MỖI threat high/critical, và ``threat_id`` = hash nội dung.
  Kẻ tấn công chỉ cần gửi N payload khác nhau ⇒ N thread + N entry state
  không giới hạn (persist mỗi lần) = DoS (c1-escalation-done §5).

Hợp đồng ở đây (fail-closed, không bao giờ "tự duyệt"):
  * ĐÚNG MỘT worker thread daemon cho cả scheduler (lazy start), bất kể bao
    nhiêu deadline đang chờ.
  * Hàng đợi có trần ``max_pending``. Đầy mà có key MỚI ⇒ entry có deadline
    SỚM NHẤT bị bắn NGAY (chạy safe outcome sớm), không bao giờ bị vứt im
    lặng, không bao giờ bị coi như đã duyệt. Counter ``evicted_fired_early``.
  * Dedup theo key: schedule lại cùng key ⇒ deadline = min(cũ, mới). Lặp lại
    cùng một threat KHÔNG thể đẩy lùi safe outcome mãi mãi.
  * Callback chạy NGOÀI lock; exception được log + đếm, worker không chết.
"""
from __future__ import annotations

import heapq
import itertools
import logging
import threading
import time
from collections.abc import Callable
from typing import Any

logger = logging.getLogger("scp.security.deadline_scheduler")

DeadlineCallback = Callable[[str, str], None]
"""callback(key, cause) — cause ∈ {"deadline", "capacity_evicted", "shutdown_flush"}."""


class BoundedDeadlineScheduler:
    """One worker thread, capped pending set, dedup-by-key, early-fire eviction."""

    def __init__(
        self,
        callback: DeadlineCallback,
        *,
        max_pending: int = 256,
        name: str = "scp-deadline-scheduler",
        clock: Callable[[], float] = time.time,
    ) -> None:
        if max_pending < 1:
            raise ValueError("max_pending must be >= 1")
        self._callback = callback
        self._max_pending = int(max_pending)
        self._name = name
        self._clock = clock
        self._cond = threading.Condition(threading.Lock())
        self._deadlines: dict[str, float] = {}
        self._heap: list[tuple[float, int, str]] = []
        self._seq = itertools.count()
        self._worker: threading.Thread | None = None
        self._stopped = False
        self._stats = {
            "scheduled": 0,
            "deduped": 0,
            "fired": 0,
            "evicted_fired_early": 0,
            "cancelled": 0,
            "callback_errors": 0,
        }

    # ---- public API -------------------------------------------------------
    def schedule(self, key: str, deadline_at: float) -> float:
        """Arm ``key``; returns the EFFECTIVE deadline (earliest wins)."""
        key = str(key)
        evict: list[str] = []
        with self._cond:
            if self._stopped:
                raise RuntimeError("scheduler is stopped")
            existing = self._deadlines.get(key)
            if existing is not None:
                self._stats["deduped"] += 1
                if deadline_at >= existing:
                    return existing
            else:
                while len(self._deadlines) >= self._max_pending:
                    victim = self._pop_earliest_locked()
                    if victim is None:
                        break
                    evict.append(victim)
                self._stats["scheduled"] += 1
            self._deadlines[key] = float(deadline_at)
            heapq.heappush(self._heap, (float(deadline_at), next(self._seq), key))
            self._ensure_worker_locked()
            self._cond.notify()
            effective = float(deadline_at)
        for victim in evict:
            self._stats_inc("evicted_fired_early")
            self._run_callback(victim, "capacity_evicted")
        return effective

    def cancel(self, key: str) -> bool:
        with self._cond:
            removed = self._deadlines.pop(str(key), None) is not None
            if removed:
                self._stats["cancelled"] += 1
                self._cond.notify()
        return removed

    def deadline_of(self, key: str) -> float | None:
        with self._cond:
            return self._deadlines.get(str(key))

    def pending_keys(self) -> list[str]:
        with self._cond:
            return list(self._deadlines)

    def pending_count(self) -> int:
        with self._cond:
            return len(self._deadlines)

    def stats(self) -> dict[str, Any]:
        with self._cond:
            worker_alive = bool(self._worker is not None and self._worker.is_alive())
            return {
                **self._stats,
                "pending": len(self._deadlines),
                "max_pending": self._max_pending,
                "worker_threads": 1 if worker_alive else 0,
            }

    def shutdown(self, timeout: float = 2.0) -> None:
        """Stop the worker. Pending deadlines are NOT approved: they are kept
        in the caller's persisted state and re-armed on the next start."""
        with self._cond:
            self._stopped = True
            self._cond.notify_all()
            worker = self._worker
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout)

    # ---- internals --------------------------------------------------------
    def _stats_inc(self, name: str) -> None:
        with self._cond:
            self._stats[name] += 1

    def _pop_earliest_locked(self) -> str | None:
        while self._heap:
            deadline, _, key = heapq.heappop(self._heap)
            if self._deadlines.get(key) == deadline:
                del self._deadlines[key]
                return key
        return None

    def _ensure_worker_locked(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            return
        self._worker = threading.Thread(target=self._run, name=self._name, daemon=True)
        self._worker.start()

    def _run(self) -> None:
        while True:
            due: list[str] = []
            with self._cond:
                if self._stopped:
                    return
                now = self._clock()
                while self._heap:
                    deadline, _, key = self._heap[0]
                    if self._deadlines.get(key) != deadline:
                        heapq.heappop(self._heap)  # stale (cancelled / superseded)
                        continue
                    if deadline > now:
                        break
                    heapq.heappop(self._heap)
                    del self._deadlines[key]
                    due.append(key)
                if not due:
                    wait_for = (self._heap[0][0] - now) if self._heap else None
                    self._cond.wait(timeout=None if wait_for is None else max(0.0, min(wait_for, 60.0)))
                    continue
            for key in due:
                self._stats_inc("fired")
                self._run_callback(key, "deadline")

    def _run_callback(self, key: str, cause: str) -> None:
        try:
            self._callback(key, cause)
        except Exception:  # noqa: BLE001 — worker must survive any callback failure
            self._stats_inc("callback_errors")
            logger.exception("[deadline-scheduler:%s] callback failed for %s (%s)", self._name, key, cause)


__all__ = ["BoundedDeadlineScheduler", "DeadlineCallback"]
