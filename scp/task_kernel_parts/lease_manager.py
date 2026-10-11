"""Lease claiming, worker start/heartbeat, action dispatch, and journal/projection helpers for TaskKernel."""
from __future__ import annotations

import json
import secrets
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from scp.task_kernel_parts.definitions import (
    InvalidTransition,
    KernelError,
    Lease,
    NotFound,
    OptimisticLockError,
    StaleLease,
    _assert_checkpoint_safe,
    now_iso,
    stable_hash,
)


def claim_impl(
    kernel: Any,
    task_id: str,
    worker_id: str,
    attempt_id: str | None = None,
    ttl_seconds: float = 30.0,
) -> Lease:
    if ttl_seconds <= 0 or not worker_id:
        raise KernelError('invalid lease contract')
    kernel._begin()
    try:
        control = kernel._assert_not_killed()
        task = kernel._task(task_id)
        created_at = datetime.fromisoformat(task['created_at']).timestamp()
        if time.time() >= created_at + int(task['deadline_ms']) / 1000.0:
            raise KernelError('task deadline exceeded')
        if task['state'] != 'QUEUED':
            raise KernelError(f"task not queueable: {task['state']}")
        attempt_id = attempt_id or 'attempt_' + secrets.token_hex(8)
        old = kernel.conn.execute(
            'SELECT COALESCE(MAX(fencing_token),0) AS n FROM leases WHERE task_id=?',
            (task_id,),
        ).fetchone()['n']
        token = int(old) + 1
        now = time.time()
        lease_id = 'lease_' + secrets.token_hex(12)
        cur = kernel.conn.execute(
            "UPDATE tasks SET state='LEASED',version=version+1,active_lease_id=?,active_fencing_token=?,updated_at=? WHERE task_id=? AND version=?",
            (lease_id, token, now_iso(), task_id, task['version']),
        )
        if cur.rowcount != 1:
            raise StaleLease(f"concurrency conflict claiming task {task_id}")
        kernel.conn.execute(
            'INSERT INTO leases(lease_id,task_id,attempt_id,worker_id,issued_at,expires_at,heartbeat_at,fencing_token,global_kill_epoch) VALUES (?,?,?,?,?,?,?,?,?)',
            (lease_id, task_id, attempt_id, worker_id, now, now + ttl_seconds, now, token, control['global_kill_epoch']),
        )
        kernel.conn.execute(
            'INSERT INTO queue_accounts(owner,active,dispatch_count,last_dispatch_at) VALUES (?,?,?,?) ON CONFLICT(owner) DO UPDATE SET active=active+1,dispatch_count=dispatch_count+1,last_dispatch_at=excluded.last_dispatch_at',
            (task['owner'], 1, 1, now),
        )
        kernel._bound_leases[task_id] = lease_id
        kernel._append_event(
            task_id,
            'LEASE_GRANTED',
            'QUEUED',
            'LEASED',
            'kernel',
            'lease_granted',
            {'lease_id': lease_id, 'fencing_token': token, 'worker_id': worker_id},
        )
        kernel._commit()
        return Lease(lease_id, task_id, attempt_id, worker_id, now + ttl_seconds, token, control['global_kill_epoch'])
    except Exception:
        kernel._rollback()
        raise


def claim_next_impl(
    kernel: Any,
    worker_id: str,
    max_active_per_owner: int = 1,
    ttl_seconds: float = 30.0,
    now: float | None = None,
) -> Lease | None:
    """Claim one queued task using priority + owner fair-share + deadline guards."""
    if not worker_id or max_active_per_owner <= 0 or ttl_seconds <= 0:
        raise KernelError('invalid queue claim contract')
    now = time.time() if now is None else float(now)
    kernel._begin()
    try:
        control = kernel._assert_not_killed()
        candidates = kernel.conn.execute(
            """
            SELECT t.*, COALESCE(a.active,0) AS owner_active,
                   COALESCE(a.dispatch_count,0) AS owner_dispatch_count,
                   COALESCE(a.last_dispatch_at,0) AS owner_last_dispatch_at
            FROM tasks t LEFT JOIN queue_accounts a ON a.owner=t.owner
            WHERE t.state='QUEUED'
            ORDER BY t.priority ASC, owner_dispatch_count ASC,
                     owner_last_dispatch_at ASC, t.created_at ASC, t.task_id ASC
            """
        ).fetchall()
        for task in candidates:
            created_at = datetime.fromisoformat(task['created_at']).timestamp()
            if now >= created_at + int(task['deadline_ms']) / 1000.0:
                cur = kernel.conn.execute(
                    "UPDATE tasks SET state='FAILED',version=version+1,active_lease_id=NULL,active_fencing_token=0,updated_at=? WHERE task_id=? AND version=?",
                    (now_iso(), task['task_id'], task['version']),
                )
                if cur.rowcount != 1:
                    continue
                kernel._append_event(
                    task['task_id'],
                    'DEADLINE_EXPIRED',
                    'QUEUED',
                    'FAILED',
                    'kernel',
                    'queue_deadline_guard',
                    {'deadline_ms': task['deadline_ms']},
                )
                continue
            if int(task['owner_active']) >= max_active_per_owner:
                continue
            attempt_id = 'attempt_' + secrets.token_hex(8)
            latest = kernel.conn.execute(
                'SELECT COALESCE(MAX(fencing_token),0) AS n FROM leases WHERE task_id=?',
                (task['task_id'],),
            ).fetchone()['n']
            fencing_token = int(latest) + 1
            lease_id = 'lease_' + secrets.token_hex(12)
            expires_at = now + ttl_seconds
            cur = kernel.conn.execute(
                "UPDATE tasks SET state='LEASED',version=version+1,active_lease_id=?,active_fencing_token=?,updated_at=? WHERE task_id=? AND version=?",
                (lease_id, fencing_token, now_iso(), task['task_id'], task['version']),
            )
            if cur.rowcount != 1:
                continue
            kernel.conn.execute(
                'INSERT INTO leases(lease_id,task_id,attempt_id,worker_id,issued_at,expires_at,heartbeat_at,fencing_token,global_kill_epoch) VALUES (?,?,?,?,?,?,?,?,?)',
                (lease_id, task['task_id'], attempt_id, worker_id, now, expires_at, now, fencing_token, control['global_kill_epoch']),
            )
            kernel.conn.execute(
                'INSERT INTO queue_accounts(owner,active,dispatch_count,last_dispatch_at) VALUES (?,?,?,?) ON CONFLICT(owner) DO UPDATE SET active=active+1,dispatch_count=dispatch_count+1,last_dispatch_at=excluded.last_dispatch_at',
                (task['owner'], 1, 1, now),
            )
            kernel._bound_leases[task['task_id']] = lease_id
            kernel._append_event(
                task['task_id'],
                'LEASE_GRANTED',
                'QUEUED',
                'LEASED',
                'kernel',
                'fair_queue_claim',
                {'lease_id': lease_id, 'fencing_token': fencing_token, 'worker_id': worker_id},
            )
            kernel._commit()
            return Lease(lease_id, task['task_id'], attempt_id, worker_id, expires_at, fencing_token, control['global_kill_epoch'])
        kernel._commit()
        return None
    except Exception:
        kernel._rollback()
        raise


def assert_lease_impl(kernel: Any, lease_id: str, task_id: str, actor: str | None = None) -> Any:
    lease = kernel._lease(lease_id)
    control = kernel._control()
    now = time.time()
    if (
        lease['task_id'] != task_id
        or lease['released']
        or lease['expires_at'] <= now
        or (lease['global_kill_epoch'] != control['global_kill_epoch'])
        or control['global_kill']
    ):
        if lease['released']:
            raise OptimisticLockError(
                f"lease {lease_id} has already been released",
                table="leases",
                entity_id=lease_id,
            )
        raise StaleLease(lease_id)
    latest = kernel.conn.execute(
        'SELECT COALESCE(MAX(fencing_token), 0) AS n FROM leases WHERE task_id=?',
        (task_id,),
    ).fetchone()['n']
    if int(lease['fencing_token']) != int(latest):
        raise StaleLease(lease_id)
    if actor is not None and str(actor).strip():
        if lease['worker_id'] != str(actor).strip():
            raise InvalidTransition(f"actor '{actor}' does not match lease worker '{lease['worker_id']}'")
    return lease


def start_impl(kernel: Any, task_id: str, lease_id: str) -> dict[str, Any]:
    kernel._begin()
    try:
        lease = kernel._assert_lease(lease_id, task_id)
        task = kernel._task(task_id)
        if task['state'] != 'LEASED':
            raise InvalidTransition(f"{task['state']}->RUNNING")
        if task['active_lease_id'] and task['active_lease_id'] != lease_id:
            raise StaleLease(f"start lease {lease_id} does not match active task lease {task['active_lease_id']}")
        is_system = getattr(kernel, "_system_authority", False)
        bound = getattr(kernel, "_bound_leases", {}).get(task_id)
        if not is_system:
            if not bound:
                raise StaleLease(f"kernel instance does not possess active lease authority to start task {task_id}")
            if bound != lease_id:
                raise StaleLease(f"caller lease {lease_id} does not match bound instance lease {bound}")
        cur = kernel.conn.execute(
            "UPDATE tasks SET state='RUNNING',version=version+1,active_lease_id=?,active_fencing_token=?,updated_at=? WHERE task_id=? AND version=? AND (active_lease_id=? OR active_lease_id IS NULL)",
            (lease_id, int(lease['fencing_token']), now_iso(), task_id, task['version'], lease_id),
        )
        if cur.rowcount != 1:
            raise StaleLease(f"concurrency conflict starting task {task_id}")
        kernel._bound_leases[task_id] = lease_id
        kernel._append_event(task_id, 'WORKER_STARTED', 'LEASED', 'RUNNING', 'worker', 'lease_valid', {'lease_id': lease_id})
        kernel._commit()
    except Exception:
        kernel._rollback()
        raise
    return kernel.get_task(task_id)


def heartbeat_impl(
    kernel: Any,
    task_id: str,
    lease_id: str,
    extend_seconds: float = 30.0,
    expected_version: int | None = None,
) -> Lease:
    kernel._begin()
    try:
        lease = kernel._assert_lease(lease_id, task_id)
        task = kernel._task(task_id)
        if task['active_lease_id'] != lease_id:
            raise StaleLease(f"heartbeat lease {lease_id} does not match active task lease {task['active_lease_id']}")
        if task['state'] not in {"LEASED", "RUNNING", "WAITING_TOOL", "VERIFYING", "CHECKPOINTED", "UNKNOWN"}:
            raise StaleLease(f"cannot heartbeat task in non-leased state {task['state']}")
        is_system = getattr(kernel, "_system_authority", False)
        bound = getattr(kernel, "_bound_leases", {}).get(task_id)
        if not is_system:
            if not bound:
                raise StaleLease(f"kernel instance does not possess active lease authority for task {task_id}")
            if bound != lease_id:
                raise StaleLease(f"caller lease {lease_id} does not match bound instance lease {bound}")
        current_lease_version = int(lease['version']) if 'version' in lease.keys() else 1
        if expected_version is not None and current_lease_version != expected_version:
            raise OptimisticLockError(
                f"concurrency conflict on lease {lease_id}: expected version {expected_version}, found {current_lease_version}",
                table="leases",
                entity_id=lease_id,
                expected_version=expected_version,
            )
        target_version = expected_version if expected_version is not None else current_lease_version
        now = time.time()
        expires = now + extend_seconds
        cur = kernel.conn.execute(
            'UPDATE leases SET heartbeat_at=?,expires_at=?,version=version+1 WHERE lease_id=? AND released=0 AND version=?',
            (now, expires, lease_id, target_version),
        )
        if cur.rowcount != 1:
            raise OptimisticLockError(
                f"concurrency conflict heartbeating lease {lease_id} (version mismatch or lease released)",
                table="leases",
                entity_id=lease_id,
                expected_version=target_version,
            )
        kernel._commit()
        return Lease(
            lease['lease_id'],
            lease['task_id'],
            lease['attempt_id'],
            lease['worker_id'],
            expires,
            lease['fencing_token'],
            lease['global_kill_epoch'],
        )
    except Exception:
        kernel._rollback()
        raise


def record_action_dispatched_impl(
    kernel: Any,
    task_id: str,
    lease_id: str,
    step_id: str,
    planned_action: Any,
    capability_epoch: int,
    idempotency_key: str,
    provider_request_id: str,
    pre_observation_ref: str | None = None,
) -> dict[str, Any]:
    """Persist the side-effect boundary before a provider response is trusted."""
    if not step_id or not idempotency_key or (not str(provider_request_id).strip()):
        raise KernelError('dispatch requires step, idempotency key and provider request')
    _assert_checkpoint_safe(
        {'planned_action': planned_action, 'pre_observation_ref': pre_observation_ref, 'provider_request_id': provider_request_id}
    )
    kernel._begin()
    try:
        lease = kernel._assert_lease(lease_id, task_id)
        task = kernel._task(task_id)
        if task['state'] not in {'RUNNING', 'WAITING_TOOL'}:
            raise InvalidTransition(f"{task['state']}->UNKNOWN")
        if task['active_lease_id'] != lease_id:
            raise StaleLease(f"dispatch lease {lease_id} does not match active task lease {task['active_lease_id']}")
        is_system = getattr(kernel, "_system_authority", False)
        bound = getattr(kernel, "_bound_leases", {}).get(task_id)
        if not is_system:
            if not bound:
                raise StaleLease(f"kernel instance does not possess active lease authority for task {task_id}")
            if bound != lease_id:
                raise StaleLease(f"caller lease {lease_id} does not match bound instance lease {bound}")
        existing = kernel.conn.execute(
            "SELECT * FROM checkpoints WHERE task_id=? AND attempt_id=? AND step_id=? AND idempotency_key=? AND state='UNKNOWN' ORDER BY created_at DESC LIMIT 1",
            (task_id, lease['attempt_id'], step_id, idempotency_key),
        ).fetchone()
        if existing:
            existing_result = json.loads(existing['tool_result_json'] or '{}')
            if existing_result.get('provider_request_id') != str(provider_request_id):
                raise KernelError('idempotency key reused with different provider request')
            kernel._commit()
            result = dict(existing)
            result['dispatch_status'] = 'UNKNOWN'
            result['provider_request_id'] = existing_result.get('provider_request_id')
            return result
        tool_result = {'dispatch_status': 'UNKNOWN', 'provider_request_id': str(provider_request_id), 'planned_action': planned_action}
        payload = {
            'task_id': task_id,
            'attempt_id': lease['attempt_id'],
            'step_id': step_id,
            'state': 'UNKNOWN',
            'planned_action': planned_action,
            'capability_epoch': capability_epoch,
            'idempotency_key': idempotency_key,
            'pre_observation_ref': pre_observation_ref,
            'post_observation_ref': None,
            'tool_result': tool_result,
            'verifier_verdict': None,
        }
        checkpoint_id = 'cp_' + secrets.token_hex(10)
        payload_hash = stable_hash(payload)
        kernel.conn.execute(
            'INSERT INTO checkpoints(checkpoint_id,task_id,attempt_id,step_id,state,planned_action_hash,capability_epoch,idempotency_key,pre_observation_ref,post_observation_ref,tool_result_json,verifier_verdict,payload_hash,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (
                checkpoint_id,
                task_id,
                lease['attempt_id'],
                step_id,
                'UNKNOWN',
                stable_hash(planned_action),
                capability_epoch,
                idempotency_key,
                pre_observation_ref,
                None,
                json.dumps(tool_result, ensure_ascii=False, sort_keys=True),
                None,
                payload_hash,
                now_iso(),
            ),
        )
        old_state = task['state']
        cur = kernel.conn.execute(
            "UPDATE tasks SET state='UNKNOWN',version=version+1,active_lease_id=?,active_fencing_token=?,updated_at=? WHERE task_id=? AND version=?",
            (lease_id, int(lease['fencing_token']), now_iso(), task_id, task['version']),
        )
        if cur.rowcount != 1:
            raise StaleLease(f"concurrency conflict on task {task_id}")
        kernel._append_event(
            task_id,
            'ACTION_DISPATCHED',
            old_state,
            'UNKNOWN',
            'worker',
            'side_effect_response_unknown',
            {'checkpoint_id': checkpoint_id, 'step_id': step_id, 'idempotency_key': idempotency_key, 'provider_request_id': str(provider_request_id)},
        )
        kernel._commit()
        result = kernel.get_checkpoint(checkpoint_id)
        result['dispatch_status'] = 'UNKNOWN'
        result['provider_request_id'] = str(provider_request_id)
        return result
    except Exception:
        kernel._rollback()
        raise


def verify_integrity_impl(kernel: Any) -> dict[str, Any]:
    quick = kernel.conn.execute('PRAGMA quick_check').fetchone()[0]
    chains: dict[str, Any] = {'checked': 0, 'invalid': []}
    for row in kernel.conn.execute('SELECT DISTINCT task_id FROM events').fetchall():
        chains['checked'] += 1
        result = kernel.verify_journal(row['task_id'])
        if not result['hash_chain_valid']:
            chains['invalid'].append({'task_id': row['task_id'], 'errors': result['errors'][:3]})
    return {'quick_check': quick, 'tasks': chains['checked'], 'invalid_chains': chains['invalid']}


def backup_impl(kernel: Any, backup_dir: str | Path, retain: int = 7) -> dict[str, Any]:
    dest = Path(backup_dir)
    dest.mkdir(parents=True, exist_ok=True)
    stamp = f"{time.strftime('%Y%m%d-%H%M%S')}-{time.time_ns() % 10 ** 9:09d}-{uuid.uuid4().hex[:6]}"
    target = dest / f'kernel-backup-{stamp}.sqlite3'
    kernel._storage.backup_to(target)
    backups = sorted(dest.glob('kernel-backup-*.sqlite3'))
    pruned = 0
    for old in backups[:max(0, len(backups) - int(retain))]:
        old.unlink(missing_ok=True)
        pruned += 1
    return {'backup': str(target), 'size_bytes': target.stat().st_size, 'pruned': pruned, 'retained': len(backups) - pruned}


def verify_journal_impl(kernel: Any, task_id: str) -> dict[str, Any]:
    events = kernel.get_events(task_id)
    prev = None
    errors = []
    for i, e in enumerate(events, 1):
        if e['seq'] != i:
            errors.append(f"sequence:{e['seq']} expected {i}")
        if e['prev_event_hash'] != prev:
            errors.append(f"prev_hash:{e['seq']}")
        try:
            payload = json.loads(e['payload_json'])
        except (TypeError, ValueError):
            errors.append(f"payload_json:{e['seq']}")
            prev = e['event_hash']
            continue
        body = {
            'event_id': e['event_id'],
            'task_id': e['task_id'],
            'seq': e['seq'],
            'type': e['type'],
            'from_state': e['from_state'],
            'to_state': e['to_state'],
            'actor': e['actor'],
            'reason': e['reason'],
            'payload': payload,
            'policy_hash': e['policy_hash'],
            'prev_event_hash': e['prev_event_hash'],
        }
        if stable_hash(body) != e['event_hash']:
            errors.append(f"event_hash:{e['seq']}")
        prev = e['event_hash']
    return {'task_id': task_id, 'event_count': len(events), 'hash_chain_valid': not errors, 'errors': errors}


def rebuild_projection_impl(kernel: Any, task_id: str, expected_version: int | None = None) -> dict[str, Any]:
    kernel._begin()
    try:
        journal = kernel.verify_journal(task_id)
        if not journal['hash_chain_valid']:
            details = ';'.join(journal['errors'])
            raise KernelError(f'journal integrity invalid: {details}')
        events = kernel.get_events(task_id)
        if not events:
            raise NotFound(task_id)
        state = events[0]['to_state']
        for event in events[1:]:
            if event['to_state']:
                state = event['to_state']
        task = kernel._task(task_id)
        cur_version = int(task['version'])
        if expected_version is not None and cur_version != expected_version:
            raise OptimisticLockError(
                f"concurrency conflict rebuilding projection for task {task_id}: expected version {expected_version}, found {cur_version}",
                table="tasks",
                entity_id=task_id,
                expected_version=expected_version,
            )
        target_version = expected_version if expected_version is not None else cur_version
        if state in {"LEASED", "RUNNING", "WAITING_TOOL", "VERIFYING", "CHECKPOINTED", "UNKNOWN"}:
            lease_row = kernel.conn.execute(
                'SELECT lease_id, fencing_token FROM leases WHERE task_id=? AND released=0 ORDER BY fencing_token DESC LIMIT 1',
                (task_id,),
            ).fetchone()
            active_lease_id = lease_row['lease_id'] if lease_row else None
            active_fencing_token = int(lease_row['fencing_token']) if lease_row else 0
        else:
            active_lease_id = None
            active_fencing_token = 0
        cur = kernel.conn.execute(
            'UPDATE tasks SET state=?,version=version+1,active_lease_id=?,active_fencing_token=?,updated_at=? WHERE task_id=? AND version=?',
            (state, active_lease_id, active_fencing_token, now_iso(), task_id, target_version),
        )
        if cur.rowcount != 1:
            raise OptimisticLockError(
                f"concurrency conflict rebuilding projection for task {task_id}: expected version {target_version}",
                table="tasks",
                entity_id=task_id,
                expected_version=target_version,
            )
        kernel._commit()
    except Exception:
        kernel._rollback()
        raise
    return kernel.get_task(task_id)
