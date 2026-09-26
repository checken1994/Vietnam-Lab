"""Append-only run ledger for SCP learning/evolution cycles.

The ledger is deliberately fail-open: inability to write an audit record must
never make a learning/evolution operation appear successful or crash the main
pipeline. Records contain counters and exception metadata only; no prompts,
answers, or credentials are persisted here.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import time
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Any, Callable, ParamSpec, TypeVar

logger = logging.getLogger("scp.learning_run_ledger")

P = ParamSpec("P")
R = TypeVar("R")

_ALLOWED_STATUSES = {
    "SUCCESS",
    "NO_NEW_FACTS",
    "PROVIDER_FAILED",
    "VERIFY_REJECTED",
    "DB_WRITE_FAILED",
    "TIMEOUT",
}


def _utc_iso(ts: float | None = None) -> str:
    return datetime.fromtimestamp(ts or time.time(), tz=timezone.utc).isoformat()


def _ledger_path() -> Path:
    raw = os.environ.get("SCP_LEARNING_RUN_LEDGER_PATH", "").strip()
    path = Path(raw) if raw else Path("data") / "learning_runs.jsonl"
    if not path.is_absolute():
        path = Path.cwd() / path
    return path


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError) as exc:
        # silent-by-design: coerce probe; None means "not an int" by contract.
        logger.debug("learning_run_ledger: int coercion failed: %s", exc, exc_info=True)
        return None


def _metrics(result: Any) -> dict[str, int | None]:
    payload = result if isinstance(result, dict) else {}
    asked = _int_or_none(payload.get("asked", payload.get("questions")))
    verified = _int_or_none(payload.get("verified"))
    stored = _int_or_none(payload.get("stored"))
    answered = _int_or_none(payload.get("answered"))
    if answered is None and asked is not None:
        provider_failed = _int_or_none(
            payload.get("provider_failed", payload.get("provider_errors"))
        ) or 0
        answered = max(asked - provider_failed, 0)
    rejected = _int_or_none(payload.get("rejected"))
    if rejected is None and answered is not None and verified is not None:
        rejected = max(answered - verified, 0)
    return {
        "asked": asked,
        "answered": answered,
        "verified": verified,
        "rejected": rejected,
        "stored": stored,
        "rows_before": _int_or_none(payload.get("rows_before")),
        "rows_after": _int_or_none(payload.get("rows_after")),
    }


def _status(mode: str, result: Any, error: BaseException | None) -> str:
    if error is not None:
        if isinstance(error, (TimeoutError, asyncio.TimeoutError)) or "timeout" in str(error).lower():
            return "TIMEOUT"
        return "PROVIDER_FAILED" if mode != "evolution" else "DB_WRITE_FAILED"
    payload = result if isinstance(result, dict) else {}
    metrics = _metrics(payload)
    if mode == "evolution":
        if payload.get("action") == "skipped":
            return "NO_NEW_FACTS"
        bugs_found = _int_or_none(payload.get("bugs_found")) or 0
        bugs_fixed = _int_or_none(payload.get("bugs_fixed")) or 0
        lessons_stored = _int_or_none(payload.get("lessons_stored")) or 0
        if bugs_found == 0 and lessons_stored == 0:
            return "NO_NEW_FACTS"
        if bugs_fixed == 0 and lessons_stored == 0:
            provider_failed = _int_or_none(payload.get('provider_failed')) or 0
            if provider_failed > 0:
                return "PROVIDER_FAILED"
            return "VERIFY_REJECTED"
        return "SUCCESS"
    asked = metrics["asked"] or 0
    verified = metrics["verified"] or 0
    stored = metrics["stored"] or 0
    if verified > stored:
        return "DB_WRITE_FAILED"
    if asked == 0 and (_int_or_none(payload.get("skipped_known")) or 0) > 0:
        return "NO_NEW_FACTS"
    if asked > 0 and verified == 0:
        return "VERIFY_REJECTED"
    return "SUCCESS"


def record_learning_run(
    *,
    mode: str,
    started_at: str,
    ended_at: str,
    result: Any = None,
    error: BaseException | None = None,
    ledger_path: str | Path | None = None,
) -> dict[str, Any]:
    """Append one sanitized run record and return it.

    This function never raises because telemetry must not alter the guarded
    learning/evolution result. A write failure is logged to stderr and the
    returned record carries ``ledger_write_error`` for callers/tests.
    """
    metrics = _metrics(result)
    status = _status(mode, result, error)
    if status not in _ALLOWED_STATUSES:
        status = "PROVIDER_FAILED"
    row: dict[str, Any] = {
        "run_id": f"{mode}-{time.time_ns()}",
        "mode": mode,
        "started_at": started_at,
        "ended_at": ended_at,
        "status": status,
        **metrics,
        "error_class": type(error).__name__ if error else None,
        "error_summary": str(error)[:300] if error else None,
    }
    try:
        path = Path(ledger_path) if ledger_path is not None else _ledger_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    except Exception as ledger_error:  # pragma: no cover - OS-specific failure
        row["ledger_write_error"] = type(ledger_error).__name__
        logger.warning("learning run ledger write failed: %s", ledger_error, exc_info=True)
    return row


def ledger_run(mode: str) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Decorate one sync or async learning/evolution boundary."""
    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        if inspect.iscoroutinefunction(func):
            @wraps(func)
            async def async_wrapper(*args: P.args, **kwargs: P.kwargs):
                started = _utc_iso()
                error: BaseException | None = None
                result: Any = None
                try:
                    result = await func(*args, **kwargs)  # type: ignore[misc]
                    return result
                except BaseException as exc:
                    error = exc
                    raise
                finally:
                    record_learning_run(mode=mode, started_at=started, ended_at=_utc_iso(), result=result, error=error)
            return async_wrapper  # type: ignore[return-value]

        @wraps(func)
        def sync_wrapper(*args: P.args, **kwargs: P.kwargs):
            started = _utc_iso()
            error: BaseException | None = None
            result: Any = None
            try:
                result = func(*args, **kwargs)
                return result
            except BaseException as exc:
                error = exc
                raise
            finally:
                record_learning_run(mode=mode, started_at=started, ended_at=_utc_iso(), result=result, error=error)
        return sync_wrapper  # type: ignore[return-value]

    return decorator
