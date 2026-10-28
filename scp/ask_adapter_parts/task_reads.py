"""Task reads inside error handlers that never replace the error being handled (R22).

A ``get_task`` issued from an ``except`` block can itself fail: the row was purged concurrently
(``TaskKernel.purge_tasks``), or the storage errored. Before R22 that secondary ``NotFound``
escaped and replaced the original error (InvalidTransition, OptimisticLockError, the
auto-resolve failure), so operators saw a phantom "task not found" instead of the real cause.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("scp.ask_kernel_adapter")


def refresh_task_or_reraise(kernel: Any, task_id: str, original: BaseException) -> dict[str, Any]:
    """Re-read ``task_id`` while handling ``original``; if the read fails, re-raise ``original``.

    The read failure is logged and attached to ``original`` as a note, never raised in its place.
    """
    try:
        return kernel.get_task(task_id)
    except Exception as read_exc:
        logger.warning(
            "[ask-kernel] task %s unreadable while handling %s (%s: %s); re-raising the original error",
            task_id, type(original).__name__, type(read_exc).__name__, read_exc,
        )
        original.add_note(f"[R22] task re-read failed: {type(read_exc).__name__}: {read_exc}")
    raise original


def unreadable_task(task_id: str, read_exc: BaseException, original: BaseException | None = None) -> dict[str, Any]:
    """Explicit result for never-raise paths when the task cannot be read.

    ``state`` is UNKNOWN (outcome not observable, as in ``AskKernelAdapter.begin``) and
    ``kernel_read_error`` marks that it was NOT reported by the kernel; ``kernel_error`` keeps the
    error that was being handled, if any.
    """
    logger.warning(
        "[ask-kernel] task %s unreadable (%s: %s) while handling %s; reporting state UNKNOWN",
        task_id, type(read_exc).__name__, read_exc, type(original).__name__ if original else "nothing",
    )
    result: dict[str, Any] = {
        "task_id": task_id,
        "state": "UNKNOWN",
        "kernel_read_error": f"{type(read_exc).__name__}: {read_exc}",
    }
    if original is not None:
        result["kernel_error"] = f"{type(original).__name__}: {original}"
    return result
