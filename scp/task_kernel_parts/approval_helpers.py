"""Lease renewal, expiration sweep, and cooperative release helpers for CommitApprovalEngine."""
from __future__ import annotations

import logging
import time
from typing import Any

from scp.task_kernel_parts.definitions import (
    OptimisticLockError,
    StaleLease,
    checkpointed_from_controlled,
    now_iso,
)

logger = logging.getLogger(__name__)


def renew_lease_impl(
    engine: Any,
    task_id: str,
    lease_id: str,
    fencing_token: int,
    ttl_seconds: float | None = None,
) -> bool:
    """Expiry-only lease renewal for long-running holders."""
    engine._assert_bound_kernel()
    if not task_id or not lease_id:
        return False
    try:
        presented_token = int(fencing_token)
    except (TypeError, ValueError):
        return False
    if ttl_seconds is not None:
        try:
            presented_ttl = float(ttl_seconds)
        except (TypeError, ValueError):
            return False
        if presented_ttl <= 0:
            return False
    else:
        presented_ttl = None
    engine.kernel._begin()
    try:
        lease = engine.conn.execute("SELECT * FROM leases WHERE lease_id=?", (lease_id,)).fetchone()
        if lease is None or str(lease["task_id"]) != str(task_id):
            engine.kernel._commit()
            return False
        now = time.time()
        if int(lease["released"]) != 0 or float(lease["expires_at"]) <= now:
            engine.kernel._commit()
            return False
        if int(lease["fencing_token"]) != presented_token:
            engine.kernel._commit()
            return False
        latest = engine.conn.execute(
            "SELECT COALESCE(MAX(fencing_token), 0) AS n FROM leases WHERE task_id=?",
            (task_id,),
        ).fetchone()["n"]
        if presented_token != int(latest):
            engine.kernel._commit()
            return False
        control = engine.kernel._control()
        if int(control["global_kill"]) != 0 or int(lease["global_kill_epoch"]) != int(control["global_kill_epoch"]):
            engine.kernel._commit()
            return False
        task = engine.conn.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        if task is None:
            engine.kernel._commit()
            return False
        if (
            (task["active_lease_id"] is None or str(task["active_lease_id"]) != str(lease_id))
            or str(task["state"]) not in engine._RENEWABLE_LEASE_STATES
        ):
            engine.kernel._commit()
            return False
        extend = (
            presented_ttl
            if presented_ttl is not None
            else (float(lease["expires_at"]) - float(lease["issued_at"]))
        )
        if extend <= 0:
            engine.kernel._commit()
            return False
        expires = now + extend
        lease_version = int(lease["version"]) if "version" in lease.keys() else 1
        cur = engine.conn.execute(
            "UPDATE leases SET heartbeat_at=?,expires_at=?,version=version+1 WHERE lease_id=? AND released=0 AND version=?",
            (now, expires, lease_id, lease_version),
        )
        if cur.rowcount != 1:
            engine.kernel._commit()
            return False
        engine.kernel._commit()
        return True
    except Exception:
        engine.kernel._rollback()
        raise


def expire_leases_impl(engine: Any, now: float | None = None) -> list[str]:
    """Sweep expired leases and transition tasks into recovery/reclaim."""
    engine._assert_bound_kernel()
    now = now or time.time()
    expired: list[str] = []
    candidates = engine.conn.execute(
        "SELECT * FROM leases WHERE released=0 AND expires_at<=?", (now,)
    ).fetchall()

    for lease in candidates:
        engine.kernel._begin()
        try:
            task = engine.kernel._task(lease["task_id"])
            cur_state = task["state"]
            is_active_lease = (task["active_lease_id"] == lease["lease_id"])
            if is_active_lease:
                if cur_state in {"LEASED", "RUNNING", "WAITING_TOOL"}:
                    old = cur_state
                    cur = engine.conn.execute(
                        "UPDATE tasks SET state='RECOVERING',version=version+1,active_lease_id=NULL,active_fencing_token=0,updated_at=? WHERE task_id=? AND version=?",
                        (now_iso(), task["task_id"], task["version"]),
                    )
                    if cur.rowcount == 0:
                        raise OptimisticLockError(
                            f"Task {task['task_id']} modified concurrently",
                            table="tasks",
                            entity_id=task["task_id"],
                        )
                    engine.kernel._append_event(
                        task["task_id"],
                        "LEASE_EXPIRED",
                        old,
                        "RECOVERING",
                        "kernel",
                        "heartbeat_expired",
                        {"lease_id": lease["lease_id"]},
                    )
                elif cur_state == "VERIFYING":
                    if getattr(engine.kernel, "autonomous_mode", False):
                        cur = engine.conn.execute(
                            "UPDATE tasks SET state='FAILED',error=?,version=version+1,active_lease_id=NULL,active_fencing_token=0,updated_at=? WHERE task_id=? AND version=?",
                            ("verification_lease_expired", now_iso(), task["task_id"], task["version"]),
                        )
                        if cur.rowcount == 0:
                            raise OptimisticLockError(
                                f"Task {task['task_id']} modified concurrently",
                                table="tasks",
                                entity_id=task["task_id"],
                            )
                        engine.kernel._append_event(
                            task["task_id"],
                            "LEASE_EXPIRED",
                            cur_state,
                            "FAILED",
                            "kernel:autonomous",
                            "heartbeat_expired_autonomous",
                            {"lease_id": lease["lease_id"], "autonomous": True},
                        )
                    else:
                        cur = engine.conn.execute(
                            "UPDATE tasks SET state='HUMAN_REVIEW',version=version+1,active_lease_id=NULL,active_fencing_token=0,updated_at=? WHERE task_id=? AND version=?",
                            (now_iso(), task["task_id"], task["version"]),
                        )
                        if cur.rowcount == 0:
                            raise OptimisticLockError(
                                f"Task {task['task_id']} modified concurrently",
                                table="tasks",
                                entity_id=task["task_id"],
                            )
                        engine.kernel._append_event(
                            task["task_id"],
                            "LEASE_EXPIRED",
                            cur_state,
                            "HUMAN_REVIEW",
                            "kernel",
                            "heartbeat_expired",
                            {"lease_id": lease["lease_id"]},
                        )
                elif cur_state == "CHECKPOINTED":
                    # [R7-1 2026-10-28] CHECKPOINTED->QUEUED là resume-edge
                    # (target thuộc RESUME_TARGETS): dưới global kill, giữ
                    # CHECKPOINTED (containment) — KHÔNG requeue; lease vẫn
                    # được release bên dưới để không kẹt tài nguyên. Watchdog
                    # không được là lối vòng quanh kill-switch theo loại cạnh
                    # của state_machine.transition(). Event from=to ghi audit
                    # containment và không làm `_checkpointed_from_controlled`
                    # nhầm (from_state=CHECKPOINTED không thuộc CONTROLLED_STATES),
                    # nên khi kill tắt, task requeue bình thường.
                    # [R7-2] A CHECKPOINTED task that entered from a controlled state is contained
                    # too: the watchdog must not requeue it any more than transition() may.
                    kill_active = bool(engine.kernel._control()["global_kill"])
                    if kill_active or checkpointed_from_controlled(engine.conn, task["task_id"]):
                        cur = engine.conn.execute(
                            "UPDATE tasks SET active_lease_id=NULL,active_fencing_token=0,version=version+1,updated_at=? WHERE task_id=? AND version=? AND active_lease_id=?",
                            (now_iso(), task["task_id"], task["version"], lease["lease_id"]),
                        )
                        if cur.rowcount == 0:
                            raise OptimisticLockError(
                                f"Task {task['task_id']} modified concurrently",
                                table="tasks",
                                entity_id=task["task_id"],
                            )
                        engine.kernel._append_event(
                            task["task_id"],
                            "LEASE_EXPIRED",
                            cur_state,
                            cur_state,
                            "kernel",
                            "heartbeat_expired_kill_containment" if kill_active else "heartbeat_expired_contained_checkpoint",
                            {"lease_id": lease["lease_id"], "global_kill": kill_active},
                        )
                    else:
                        cur = engine.conn.execute(
                            "UPDATE tasks SET state='QUEUED',version=version+1,active_lease_id=NULL,active_fencing_token=0,updated_at=? WHERE task_id=? AND version=?",
                            (now_iso(), task["task_id"], task["version"]),
                        )
                        if cur.rowcount == 0:
                            raise OptimisticLockError(
                                f"Task {task['task_id']} modified concurrently",
                                table="tasks",
                                entity_id=task["task_id"],
                            )
                        engine.kernel._append_event(
                            task["task_id"],
                            "LEASE_EXPIRED",
                            cur_state,
                            "QUEUED",
                            "kernel",
                            "heartbeat_expired",
                            {"lease_id": lease["lease_id"]},
                        )
                else:
                    cur = engine.conn.execute(
                        "UPDATE tasks SET active_lease_id=NULL,active_fencing_token=0,version=version+1,updated_at=? WHERE task_id=? AND version=?",
                        (now_iso(), task["task_id"], task["version"]),
                    )
                    if cur.rowcount == 0:
                        raise OptimisticLockError(
                            f"Task {task['task_id']} modified concurrently",
                            table="tasks",
                            entity_id=task["task_id"],
                        )
            else:
                logger.info(
                    "expire_leases: lease %s expired for task %s, but active_lease_id is %s; preserving active task",
                    lease["lease_id"],
                    task["task_id"],
                    task["active_lease_id"],
                )

            cur_lease = engine.conn.execute(
                "UPDATE leases SET released=1,version=version+1 WHERE lease_id=? AND released=0",
                (lease["lease_id"],),
            )
            if cur_lease.rowcount == 0:
                raise OptimisticLockError(
                    f"Lease {lease['lease_id']} modified concurrently or already released",
                    table="leases",
                    entity_id=lease["lease_id"],
                )

            owner = engine.kernel._task(lease["task_id"])["owner"]
            cur_acc = engine.conn.execute(
                "UPDATE queue_accounts SET active=CASE WHEN active>0 THEN active-1 ELSE 0 END,version=version+1 WHERE owner=?",
                (owner,),
            )
            if cur_acc.rowcount == 0:
                raise OptimisticLockError(
                    f"Queue account for owner {owner} modified concurrently or not found",
                    table="queue_accounts",
                    entity_id=owner,
                )

            if is_active_lease and hasattr(engine.kernel, "_bound_leases"):
                engine.kernel._bound_leases.pop(lease["task_id"], None)

            engine.kernel._commit()
            expired.append(lease["lease_id"])
        except OptimisticLockError as ole:
            engine.kernel._rollback()
            logger.warning(
                "OCC conflict during lease expiration for lease %s: %s",
                lease["lease_id"],
                ole,
            )
        except Exception:
            engine.kernel._rollback()
            raise

    return expired


def release_impl(
    engine: Any,
    task_id: str,
    lease_id: str,
    expected_version: int | None = None,
) -> None:
    """Cooperative lease release by active worker."""
    engine._assert_bound_kernel()
    engine.kernel._begin()
    try:
        engine.kernel._assert_lease(lease_id, task_id)
        task = engine.kernel._task(task_id)
        if task["active_lease_id"] != lease_id:
            raise StaleLease(f"release lease {lease_id} does not match active task lease {task['active_lease_id']}")
        is_system = getattr(engine.kernel, "_system_authority", False)
        bound = getattr(engine.kernel, "_bound_leases", {}).get(task_id)
        if not is_system:
            if not bound:
                raise StaleLease(f"kernel instance does not possess active lease authority for task {task_id}")
            if bound != lease_id:
                raise StaleLease(f"caller lease {lease_id} does not match bound instance lease {bound}")
        lease = engine.kernel._lease(lease_id)
        current_lease_version = int(lease["version"]) if "version" in lease.keys() else 1
        if expected_version is not None and current_lease_version != expected_version:
            raise OptimisticLockError(
                f"concurrency conflict releasing lease {lease_id}: expected version {expected_version}, found {current_lease_version}",
                table="leases",
                entity_id=lease_id,
                expected_version=expected_version,
            )
        target_version = expected_version if expected_version is not None else current_lease_version
        cur = engine.conn.execute(
            "UPDATE leases SET released=1,version=version+1 WHERE lease_id=? AND released=0 AND version=?",
            (lease_id, target_version),
        )
        if cur.rowcount != 1:
            raise OptimisticLockError(
                f"concurrency conflict releasing lease {lease_id}",
                table="leases",
                entity_id=lease_id,
                expected_version=target_version,
            )
        cur = engine.conn.execute(
            "UPDATE tasks SET active_lease_id=NULL,active_fencing_token=0,version=version+1,updated_at=? WHERE task_id=? AND version=?",
            (now_iso(), task_id, task["version"]),
        )
        if cur.rowcount != 1:
            raise StaleLease(f"concurrency conflict releasing lease on task {task_id}")
        engine.conn.execute(
            "UPDATE queue_accounts SET active=CASE WHEN active>0 THEN active-1 ELSE 0 END,version=version+1 WHERE owner=?",
            (task["owner"],),
        )
        engine.kernel._append_event(task_id, "LEASE_RELEASED", None, None, "kernel", "worker_release", {"lease_id": lease_id})
        if hasattr(engine.kernel, "_bound_leases"):
            engine.kernel._bound_leases.pop(task_id, None)
        engine.kernel._commit()
    except Exception:
        engine.kernel._rollback()
        raise
