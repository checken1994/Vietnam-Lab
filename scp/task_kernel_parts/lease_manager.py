"""Lease claiming, worker start/heartbeat, action dispatch, and journal/projection helpers for TaskKernel."""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import sqlite3
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

from scp.task_kernel_parts.definitions import (
    InvalidTransition,
    KernelError,
    Lease,
    NotFound,
    OptimisticLockError,
    StaleLease,
    _assert_checkpoint_safe,
    all_events_provable_under_previous_secrets,
    classify_chain_integrity,
    classify_journal_hash,
    finalize_journal_migration,
    compute_journal_event_hash,
    compute_keyed_hash,
    journal_event_body,
    legacy_capability_journal_keys,
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
            if kernel._journal_hold(task['task_id']) is not None:
                continue  # [JOURNAL-CHAIN] chuỗi không chứng minh được: task HOLD, không chặn cả hàng đợi
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
        payload_hash = compute_keyed_hash(payload, domain="checkpoint")  # [R6·(6)/R5] K_journal, miền checkpoint
        kernel.conn.execute(
            'INSERT INTO checkpoints(checkpoint_id,task_id,attempt_id,step_id,state,planned_action_hash,capability_epoch,idempotency_key,pre_observation_ref,post_observation_ref,tool_result_json,verifier_verdict,payload_hash,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (
                checkpoint_id,
                task_id,
                lease['attempt_id'],
                step_id,
                'UNKNOWN',
                compute_keyed_hash(planned_action, domain="checkpoint"),  # [R6·(6)/R5] K_journal, miền checkpoint
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

    # GAP-01: Global Journal Anchor check in verify_integrity
    try:
        ctrl = kernel._control() if hasattr(kernel, "_control") else None
        if ctrl:
            c_cnt = int(ctrl["event_count"]) if "event_count" in ctrl.keys() and ctrl["event_count"] is not None else 0
            actual_events_count = kernel.conn.execute('SELECT COUNT(*) FROM events').fetchone()[0]
            if c_cnt > 0 and actual_events_count == 0:
                chains['invalid'].append({
                    'task_id': '__global_journal__',
                    'errors': ['journal_deleted:events_table_empty_but_anchor_exists'],
                    'integrity_class': 'STRUCTURAL_TAMPER',
                })
            elif c_cnt > 0 and actual_events_count != c_cnt:
                # [TK-01 fix · 2026-10-24 · theo AUD-S2 độc lập] Trước đây chỉ
                # bắt actual < anchor (xóa sự kiện); mù khi actual > anchor —
                # chèn/restore hàng loạt không cập nhật anchor không bị phát
                # hiện (bằng chứng: ask_task_kernel.sqlite3 production lệch
                # Δ=3.986 event mà verify vẫn pass). Fail-closed HAI chiều.
                direction = 'less_than' if actual_events_count < c_cnt else 'greater_than'
                chains['invalid'].append({
                    'task_id': '__global_journal__',
                    'errors': [f'journal_tampered:events_count_{actual_events_count}_{direction}_anchor_{c_cnt}'],
                    'integrity_class': 'STRUCTURAL_TAMPER',
                })
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        logger.debug("Journal anchor check ignored error in verify_integrity: %s", exc)

    task_ids: set[str] = set()
    task_states: dict[str, str] = {}
    try:
        for row in kernel.conn.execute('SELECT task_id, state FROM tasks').fetchall():
            tid_val = row['task_id'] if hasattr(row, 'keys') and 'task_id' in row.keys() else str(row[0])
            task_ids.add(str(tid_val))
            task_states[str(tid_val)] = str(row['state'] if hasattr(row, 'keys') else row[1])
    except (AttributeError, KeyError, IndexError, ValueError) as exc:
        logger.debug("Tasks query exception in verify_integrity: %s", exc)
    try:
        for row in kernel.conn.execute('SELECT DISTINCT task_id FROM events').fetchall():
            tid_val = row['task_id'] if hasattr(row, 'keys') and 'task_id' in row.keys() else str(row[0])
            task_ids.add(str(tid_val))
    except (AttributeError, KeyError, IndexError, ValueError) as exc:
        logger.debug("Events query exception in verify_integrity: %s", exc)

    for tid in sorted(task_ids):
        chains['checked'] += 1
        result = kernel.verify_journal(tid)
        if not result['hash_chain_valid']:
            chains['invalid'].append({'task_id': tid, 'errors': result['errors'][:3],
                                      'integrity_class': result.get('integrity_class'),
                                      'state': task_states.get(tid)})
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

    # GAP-01: Journal Anchor check - detect tampering/deletion of events table
    anchor_exists = False
    anchor_head: str | None = None
    legacy_migrated = False  # [R6·(5)] cờ một chiều: sau migration, legacy_hash luôn sai
    try:
        ctrl = kernel._control() if hasattr(kernel, "_control") else None
        if ctrl:
            c_cnt = int(ctrl["event_count"]) if "event_count" in ctrl.keys() and ctrl["event_count"] is not None else 0
            c_head = ctrl["journal_head_hash"] if "journal_head_hash" in ctrl.keys() else None
            if c_cnt > 0 or (c_head and str(c_head).strip()):
                anchor_exists = True
            if c_head and str(c_head).strip():
                anchor_head = str(c_head)
            if "legacy_migration_done" in ctrl.keys() and int(ctrl["legacy_migration_done"] or 0) == 1:
                legacy_migrated = True
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        logger.debug("Journal anchor check ignored error: %s", exc)

    if len(events) == 0 and anchor_exists:
        errors.append("journal_deleted:events_table_empty_but_anchor_exists")
        return {'task_id': task_id, 'event_count': 0, 'hash_chain_valid': False, 'errors': errors,
                'legacy_unkeyed': False, 'capability_key_events': 0, 'hash_failed': 0,
                'integrity_class': classify_chain_integrity(1, 0, 0)}
    structural = 0  # [JOURNAL-CHAIN] đếm lỗi cấu trúc để phân loại nguyên nhân (không parse chuỗi lỗi)

    # Pass 1 — classify the hash origin of every event: keyed (HMAC under the
    # current secret or a declared previous secret) vs legacy unkeyed stable_hash.
    classified: list[tuple[Any, bool, bool, bool]] = []
    keyed_events = 0
    legacy_keys = legacy_capability_journal_keys(kernel)  # [R5] rỗng sau cutover K_journal
    capability_key_events = 0
    for i, e in enumerate(events, 1):
        if e['seq'] != i:
            errors.append(f"sequence:{e['seq']} expected {i}")
            structural += 1
        if e['prev_event_hash'] != prev:
            errors.append(f"prev_hash:{e['seq']}")
            structural += 1
        try:
            payload = json.loads(e['payload_json'])
        except (TypeError, ValueError):
            errors.append(f"payload_json:{e['seq']}")
            structural += 1
            prev = e['event_hash']
            classified.append((e, False, False, False))
            continue
        body = journal_event_body(e, payload)
        is_keyed, via_capability_key = classify_journal_hash(body, e['event_hash'], legacy_keys)
        capability_key_events += via_capability_key  # [R5] cửa sổ dual-accept: ký bằng khóa capability trước R5
        is_legacy = (not is_keyed) and (e['event_hash'] == stable_hash(body))
        if is_keyed:
            keyed_events += 1
        classified.append((e, is_keyed, is_legacy, True))
        prev = e['event_hash']

    # [R6 fix · đường 1] Legacy unkeyed fallback chỉ được chấp nhận khi TOÀN BỘ
    # chuỗi của task là legacy thuần (không event nào mang hash HMAC khớp).
    # Chuỗi trộn keyed + legacy -> các event legacy bị coi là invalid, chặn
    # whitewash từng-event qua stable_hash tự tính (không cần secret).
    # [R6·(5)] Sau khi migration một chiều hoàn tất (legacy_migration_done=1),
    # mọi hash legacy — kể cả chuỗi thuần legacy — đều coi là SAI.
    chain_allows_legacy = keyed_events == 0 and not legacy_migrated

    # [R6 fix · đường 1, chống-né-tránh] Một chuỗi "toàn stable_hash" mâu thuẫn
    # với control anchor đang sống: journal_head_hash do kernel ghi tại lần
    # append cuối là hash HMAC (hex trần, không prefix 'sha256:'). Nếu hash
    # anchor đó không còn nằm trong bất kỳ row event nào -> chuỗi từng được ký
    # HMAC đã bị viết lại toàn bộ thành legacy giả (whitewash 100%).
    legacy_whitewashed = False
    if events and chain_allows_legacy and anchor_head and not anchor_head.startswith('sha256:'):
        anchor_hit = kernel.conn.execute(
            'SELECT 1 FROM events WHERE event_hash = ? LIMIT 1', (anchor_head,)
        ).fetchone()
        if not anchor_hit:
            legacy_whitewashed = True
            errors.append('legacy_whitewash:anchored_head_unresolved')
            structural += 1

    legacy_unkeyed = False
    hash_failed = 0
    hash_checked_count = 0
    for e, is_keyed, is_legacy, hash_checked in classified:
        hash_checked_count += int(hash_checked)
        if is_keyed:
            continue
        if is_legacy and chain_allows_legacy and not legacy_whitewashed:
            legacy_unkeyed = True
            continue
        if hash_checked:
            hash_failed += 1
            errors.append(f"event_hash:{e['seq']}")
            if is_legacy and legacy_migrated:  # [R6·(5)] không còn đường rửa qua legacy
                errors.append(f"legacy_hash:post_migration:{e['seq']}")

    return {'task_id': task_id, 'event_count': len(events), 'hash_chain_valid': not errors, 'errors': errors,
            'legacy_unkeyed': legacy_unkeyed, 'capability_key_events': capability_key_events,
            'hash_failed': hash_failed,
            'integrity_class': classify_chain_integrity(structural, hash_failed, hash_checked_count) if errors else None}


def reanchor_journal_impl(kernel: Any, task_id: str) -> dict[str, Any]:
    """Recompute HMAC journal hash chain for a task using current SCP_JOURNAL_SECRET (K_journal).

    Updates events atomically within a write transaction.
    Fails closed if the existing journal fails verification (tamper bleaching prevention).

    [R6 fix · đường 2] Authorization gate — nhánh cấp quyền miễn phí khi 100%
    event lỗi hash đã BỎ: chỉ cho reanchor khi (i) verify hiện hành PASS
    (re-key chủ động / legacy migration), hoặc (ii) MỌI event lỗi hash khớp một
    K_journal đã nghỉ (SCP_PREVIOUS_JOURNAL_SECRETS) hoặc khóa capability trước R5 trong cửa sổ; không khớp
    nguồn nào -> từ chối tuyệt đối. Reanchor thành công ghi audit REANCHORED.
    """
    # 1. Pre-verify existing journal (fail closed against tampered records)
    verification = verify_journal_impl(kernel, task_id)
    reanchor_reason: str | None = None
    if verification.get("hash_chain_valid", False):
        if verification.get("legacy_unkeyed"):
            reanchor_reason = "legacy_migration"
        elif verification.get("capability_key_events"):
            reanchor_reason = "journal_key_migration"  # [R5] ký lại từ khóa capability sang K_journal
        else:
            reanchor_reason = "verify_pass_rekey"
    else:
        err_prefixes = ("sequence:", "prev_hash:", "payload_json:", "journal_deleted:", "legacy_whitewash:")
        has_structural_tampering = any(err.startswith(err_prefixes) for err in verification.get("errors", []))
        if has_structural_tampering:
            raise KernelError(
                f"Cannot reanchor tampered journal for task {task_id}: {verification.get('errors')}"
            )
        event_count = verification.get("event_count", 0)
        hash_errors = [e for e in verification.get("errors", []) if e.startswith("event_hash:")]
        if len(hash_errors) != event_count:
            raise KernelError(
                f"Cannot reanchor partially tampered journal for task {task_id}: {verification.get('errors')}"
            )
        # R6: toàn chuỗi lỗi hash — chỉ secret rotation khai báo mới được reanchor.
        if not all_events_provable_under_previous_secrets(kernel, task_id):
            raise KernelError(
                f"Cannot reanchor unproven journal for task {task_id}: hash errors "
                f"{hash_errors} match no retired K_journal (SCP_PREVIOUS_JOURNAL_SECRETS) nor, inside the "
                f"dual-accept window, the pre-R5 capability key (possible rewrite/whitewash)"
            )
        reanchor_reason = "secret_rotation"

    kernel._begin()
    try:
        events = kernel.get_events(task_id)
        if not events:
            kernel._rollback()
            return {"task_id": task_id, "reanchored": 0, "status": "NO_EVENTS"}

        # R6: head cũ phải được ghi lại TRƯỚC khi rewrite để audit event có evidence.
        old_head = events[-1]['event_hash']
        prev = None
        new_events = []
        for e in events:
            try:
                payload = json.loads(e["payload_json"])
            except (TypeError, ValueError) as exc:
                kernel._rollback()
                raise KernelError(f"Corrupt payload_json in event {e['event_id']}: {exc}") from exc

            body = {
                "event_id": e["event_id"],
                "task_id": e["task_id"],
                "seq": e["seq"],
                "type": e["type"],
                "from_state": e["from_state"],
                "to_state": e["to_state"],
                "actor": e["actor"],
                "reason": e["reason"],
                "payload": payload,
                "policy_hash": e["policy_hash"],
                "prev_event_hash": prev,
            }
            new_hash = compute_journal_event_hash(body)
            new_events.append((prev, new_hash, e["event_id"]))
            prev = new_hash

        for prev_hash, ev_hash, ev_id in new_events:
            kernel.conn.execute(
                "UPDATE events SET prev_event_hash = ?, event_hash = ? WHERE event_id = ?",
                (prev_hash, ev_hash, ev_id),
            )

        if prev:
            try:
                kernel.conn.execute(
                    "UPDATE control SET journal_head_hash = ? WHERE id = 1",
                    (prev,),
                )
            except (sqlite3.OperationalError, sqlite3.DatabaseError, AttributeError) as exc:
                logger.debug("control table does not support journal_head_hash: %s", exc)

        # [R6 fix · (3)] Reanchor thành công phải để lại audit trail bền trong
        # journal (append-only, HMAC bằng chính cơ chế _append_event sẵn có).
        audit_row = kernel._append_event(
            task_id,
            'REANCHORED',
            None,
            None,
            'kernel',
            reanchor_reason or 'unspecified',
            {
                'task_id': task_id,
                'old_head_hash': old_head,
                'new_head_hash': prev,
                'reanchored_events': len(new_events),
            },
        )
        final_head = audit_row.get('event_hash') if isinstance(audit_row, dict) else None
        kernel._commit()
        return {"task_id": task_id, "reanchored": len(new_events), "head_hash": final_head or prev,
                "status": "REANCHORED", "reason": reanchor_reason or 'unspecified'}
    except Exception:
        kernel._rollback()
        raise


def reanchor_all_journals_impl(kernel: Any) -> dict[str, Any]:
    """Re-anchor all tasks in the database to current SCP_JOURNAL_SECRET (K_journal)."""
    task_ids: set[str] = set()
    try:
        for row in kernel.conn.execute("SELECT task_id FROM tasks").fetchall():
            tid = row["task_id"] if hasattr(row, "keys") and "task_id" in row.keys() else str(row[0])
            task_ids.add(str(tid))
    except (sqlite3.OperationalError, sqlite3.DatabaseError, AttributeError) as exc:
        logger.debug("Unable to fetch tasks from tasks table: %s", exc)
    try:
        for row in kernel.conn.execute("SELECT DISTINCT task_id FROM events").fetchall():
            tid = row["task_id"] if hasattr(row, "keys") and "task_id" in row.keys() else str(row[0])
            task_ids.add(str(tid))
    except (sqlite3.OperationalError, sqlite3.DatabaseError, AttributeError) as exc:
        logger.debug("Unable to fetch task_ids from events table: %s", exc)

    total = len(task_ids)
    reanchored = 0
    failed: list[str] = []
    for tid in sorted(task_ids):
        try:
            res = reanchor_journal_impl(kernel, tid)
            if res.get("status") == "REANCHORED":
                reanchored += 1
        except KernelError as err:
            logger.warning("Skipping re-anchor of task %s due to verification failure: %s", tid, err)
            failed.append(tid)

    # [R6·(5)/R5/R5.1] One-way flags flip after a clean pass; the reported status follows the REAL
    # cutover flag (definitions.finalize_journal_migration).
    return {"total_tasks": total, "reanchored_tasks": reanchored, "failed_tasks": failed,
            **finalize_journal_migration(kernel, total, failed)}


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
