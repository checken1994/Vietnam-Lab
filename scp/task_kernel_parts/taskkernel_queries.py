"""Accessor/query method implementations for TaskKernel (HC-21).

[2026-10-29] ARC-01 tách file (move-only): taskkernel.py đạt 818 LOC vượt
trần 800 (tripwire test_arc01_taskkernel_loc_ceiling). Nhóm accessor/query
HC-21 (list_recent, query_by_state, pending_review_breakdown, purge_tasks)
chuyển VERBATIM thành module-level function (tham số đầu tiên là kernel
instance/self) và được TaskKernel gán vào class attribute theo đúng pattern
sẵn có của repo: taskkernel_checker_gate.py và _RENEWABLE_LEASE_STATES trong
taskkernel.py. Không dùng kế thừa mixin (ARC-02: zero mixin inheritance
trong scp/). Không đổi logic, signature, default hay docstring; import path
công khai không đổi.

in_flight_count / pending_review_count KHÔNG nằm ở file này: test
admission semantics (77d44816) đọc source taskkernel.py tìm docstring
in_flight_count, nên cặp counter admission phải giữ nguyên chỗ cũ.
"""
from __future__ import annotations

from typing import Any

from scp.task_kernel_parts.definitions import KernelError


def list_recent(self, limit: int = 20) -> list[dict[str, Any]]:
    """[HC-21] Metadata gần đây của projection tasks, mới nhất trước.

    Trả về list dict {'task_id','state','created_at'} theo
    ORDER BY created_at DESC, task_id DESC, giới hạn `limit` dòng
    (limit <= 0 → list rỗng). Chỉ expose metadata an toàn — không bao giờ
    trả owner/goal (có thể chứa user content). Read-only, không journal.
    """
    limit = int(limit)
    if limit <= 0:
        return []
    rows = self.conn.execute(
        'SELECT task_id, state, created_at FROM tasks '
        'ORDER BY created_at DESC, task_id DESC LIMIT ?',
        (limit,),
    ).fetchall()
    return [
        {'task_id': row['task_id'], 'state': row['state'], 'created_at': row['created_at']}
        for row in rows
    ]


def query_by_state(self, state: str) -> list[dict[str, Any]]:
    """[HC-21] Tất cả task đang ở `state`, dạng {'task_id','state'}.

    Read-only, không journal. Dùng cho worker polling (retry-policy) và
    đếm tồn đọng; không expose owner/goal payload.
    """
    rows = self.conn.execute(
        'SELECT task_id, state FROM tasks WHERE state=?', (state,)
    ).fetchall()
    return [{'task_id': row['task_id'], 'state': row['state']} for row in rows]


def pending_review_breakdown(self) -> dict[str, int]:
    """[HC-21] Số task kẹt theo từng state cần human/verification attention:
    HUMAN_REVIEW / UNKNOWN / RECONCILING.

    Parity với `GROUP BY state`: chỉ trả các state đang có count > 0
    (state không xuất hiện trong DB thì vắng mặt khỏi dict). Read-only,
    không journal.
    """
    rows = self.conn.execute(
        "SELECT state, COUNT(*) AS n FROM tasks "
        "WHERE state IN ('HUMAN_REVIEW','UNKNOWN','RECONCILING') "
        "GROUP BY state"
    ).fetchall()
    return {row['state']: int(row['n']) for row in rows}


def purge_tasks(self, task_id_pattern: str, *, actor: str = 'kernel:maintenance') -> dict[str, Any]:
    """[HC-21] Purge task theo LIKE pattern — journal-first, fail-closed.

    Với mỗi task match pattern: ghi tombstone event 'TASK_PURGED' vào
    journal của task đó (chuẩn _append_event, chain hash đầy đủ) TRƯỚC khi
    xóa, rồi xóa dòng trong projection `tasks`. Journal (events) được GIỮ
    nguyên — journal là authority, tasks chỉ là projection rebuildable;
    xóa events sẽ khiến purge biến mất khỏi lịch sử (anti-placebo).

    Deviation có chủ đích so với behavior inline cũ trong
    deduplication_maintenance (xóa cả tasks lẫn events): events của task
    purged được giữ làm audit record, chain intact nên verify_integrity /
    reanchor_all_journals vẫn sạch.

    Trả về {'pattern', 'deleted_count', 'task_ids'}; pattern không match
    → deleted_count=0, không mở transaction, không ghi journal.
    Pattern rỗng → KernelError (fail-closed, chặn purge toàn bảng).
    """
    if not task_id_pattern:
        raise KernelError('purge_tasks: task_id_pattern must not be empty')
    matched = self.conn.execute(
        'SELECT task_id, state FROM tasks WHERE task_id LIKE ?', (task_id_pattern,)
    ).fetchall()
    if not matched:
        return {'pattern': task_id_pattern, 'deleted_count': 0, 'task_ids': []}
    self._begin()
    try:
        deleted: list[str] = []
        for row in matched:
            task_id = row['task_id']
            self._append_event(
                task_id, 'TASK_PURGED', row['state'], None, actor,
                'purged_by_pattern', {'pattern': task_id_pattern},
            )
            self.conn.execute('DELETE FROM tasks WHERE task_id=?', (task_id,))
            deleted.append(task_id)
        self._commit()
    except Exception:
        self._rollback()
        raise
    return {'pattern': task_id_pattern, 'deleted_count': len(deleted), 'task_ids': deleted}
