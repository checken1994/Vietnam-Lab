"""Knowledge Store Deduplication & Kernel Human Review Maintenance.

Governed by SCP DNA #1–#29:
- Rule #7 (Distributed Autonomy): Subsystem Independent Reviewer resolves
  tasks stuck in HUMAN_REVIEW without human bottleneck.
- Rule #11 (Anti-Placebo): Strict database-level boundaries (partial unique indexes)
  preventing duplicate spam and approximate matching.
- Exact Single Truth: Deduplicates spam records in knowledge_records, keeping
  only the single highest-provenance truth per single-domain question.
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any

from scp.knowledge.trust_hierarchy import SOURCE_TIER, TrustTier

logger = logging.getLogger("scp.knowledge.maintenance")


def _get_tier_rank(source: str) -> int:
    """Lower rank number = higher authority."""
    tier = SOURCE_TIER.get(source.lower().strip(), TrustTier.LEARNED)
    return int(tier)


def deduplicate_knowledge_sqlite(sqlite_path: str | Path) -> dict[str, Any]:
    """Safely deduplicate knowledge_records table in SQLite.

    - Keeps the single best record per question based on:
      1. Confidence (highest first)
      2. Source Trust Tier (authoritative first)
      3. Collected timestamp (latest first)
    - Deletes spam duplicate rows.
    - Adds frame_of_reference column if missing.
    - Establishes partial UNIQUE indexes at database engine level.
    """
    db_path = Path(sqlite_path)
    if not db_path.exists():
        return {"status": "skipped", "reason": "database_not_found", "deleted_count": 0}

    stats = {
        "status": "ok",
        "initial_total": 0,
        "final_total": 0,
        "distinct_questions": 0,
        "deleted_count": 0,
        "top_deduplicated": [],
    }

    with sqlite3.connect(db_path, timeout=10.0) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        # Check if table exists
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='knowledge_records'")
        if not cur.fetchone():
            return {"status": "skipped", "reason": "table_not_found", "deleted_count": 0}

        # Check columns
        cur.execute("PRAGMA table_info(knowledge_records)")
        col_names = {row["name"] for row in cur.fetchall()}
        if "frame_of_reference" not in col_names:
            conn.execute("ALTER TABLE knowledge_records ADD COLUMN frame_of_reference TEXT DEFAULT ''")

        # Initial count
        cur.execute("SELECT COUNT(*), COUNT(DISTINCT lower(trim(question))) FROM knowledge_records")
        row = cur.fetchone()
        stats["initial_total"] = row[0]
        stats["distinct_questions"] = row[1]

        # Find duplicate question groups
        cur.execute(
            """
            SELECT lower(trim(question)) as q_norm, COUNT(*) as cnt
            FROM knowledge_records
            GROUP BY lower(trim(question))
            HAVING cnt > 1
            ORDER BY cnt DESC
            """
        )
        dup_groups = cur.fetchall()

        deleted_ids: list[str] = []
        for g in dup_groups:
            q_norm = g["q_norm"]
            cnt = g["cnt"]
            cur.execute(
                """
                SELECT id, question, answer, domain, source, confidence, collected_at, frame_of_reference
                FROM knowledge_records
                WHERE lower(trim(question)) = ?
                """,
                (q_norm,),
            )
            records = cur.fetchall()

            # If question has records with distinct non-empty frames of reference, group by frame
            frames: dict[str, list[sqlite3.Row]] = {}
            for r in records:
                f_val = (r["frame_of_reference"] or "").strip().lower()
                frames.setdefault(f_val, []).append(r)

            top_sample = {"question": records[0]["question"], "count_before": cnt}

            for f_val, r_list in frames.items():
                if len(r_list) <= 1:
                    continue
                # Sort records by: confidence DESC, tier rank ASC, collected_at DESC
                def _sort_key(rec):
                    conf = float(rec["confidence"] or 0.0)
                    tier_rank = _get_tier_rank(str(rec["source"] or ""))
                    collected = float(rec["collected_at"] or 0.0)
                    return (-conf, tier_rank, -collected)

                sorted_recs = sorted(r_list, key=_sort_key)
                best_rec = sorted_recs[0]
                # Mark all others for deletion
                for dup_rec in sorted_recs[1:]:
                    deleted_ids.append(dup_rec["id"])

            stats["top_deduplicated"].append(top_sample)

        # Batch delete duplicates
        if deleted_ids:
            # Delete in chunks of 500
            for i in range(0, len(deleted_ids), 500):
                chunk = deleted_ids[i : i + 500]
                placeholders = ",".join("?" for _ in chunk)
                conn.execute(f"DELETE FROM knowledge_records WHERE id IN ({placeholders})", chunk)
            conn.commit()

        stats["deleted_count"] = len(deleted_ids)

        # Establish database-level UNIQUE partial indexes
        try:
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_kr_single_domain_unique
                ON knowledge_records(lower(trim(question)))
                WHERE domain NOT IN ('multi', 'interdisciplinary', 'multidisciplinary')
                  AND (frame_of_reference IS NULL OR trim(frame_of_reference) = '')
                """
            )
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_kr_perspective_unique
                ON knowledge_records(lower(trim(question)), lower(trim(frame_of_reference)))
                WHERE frame_of_reference IS NOT NULL AND trim(frame_of_reference) != ''
                """
            )
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_kr_multi_domain_unique
                ON knowledge_records(lower(trim(question)), lower(trim(domain)), lower(trim(frame_of_reference)))
                WHERE domain IN ('multi', 'interdisciplinary', 'multidisciplinary')
                """
            )
            conn.commit()
        except Exception as idx_err:
            logger.warning("[deduplicate_knowledge_sqlite] Index creation note: %s", idx_err)

        # Final count
        cur.execute("SELECT COUNT(*) FROM knowledge_records")
        stats["final_total"] = cur.fetchone()[0]

    logger.info(
        "[deduplicate_knowledge_sqlite] Deduplicated %d records from %s (initial=%d, final=%d)",
        stats["deleted_count"],
        db_path,
        stats["initial_total"],
        stats["final_total"],
    )
    return stats


def resolve_stuck_human_review_tasks(
    ask_db_path: str | Path,
    reviewer_actor: str = "scp-autonomous-resolver",
    evidence_queue: Any = None,
) -> dict[str, Any]:
    """Close HUMAN_REVIEW tasks in ask_task_kernel.sqlite3 with their TRUE outcome.

    [esc-auto slice 2] Trước đây hàm này ghi ``*_audited_and_contained`` /
    actor ``subsystem-independent-reviewer:*_audit`` dù KHÔNG có audit hay
    reviewer độc lập nào chạy (giải phóng im lặng). Giờ (không chờ người):
      * evidence ESCALATE: theo phiếu của EvidenceResolutionQueue — phiếu còn
        PENDING ⇒ để resolver xử lý (không đóng sớm); phiếu đã xong ⇒
        ``evidence_resolution:<STATUS>:<CODE>``; không có phiếu ⇒
        ``evidence_resolution:UNKNOWN_WITH_REASON:ERR_EVIDENCE_UNAVAILABLE``.
      * security: ``security_blocked_final`` — request đã bị chặn lúc delivery,
        chặn là cuối cùng; không ghi là "đã audit".
      * còn lại: ``lifecycle_unresolved_fail_closed:<reason>``.
    Bản nháp AI luôn withheld (FAILED = không giao).
    """
    from scp.core.autonomous_cycle import ensure_capability_secret
    from scp.core.evidence_resolution import (
        ERR_EVIDENCE_UNAVAILABLE,
        KERNEL_REASON_PREFIX,
        PENDING,
        UNKNOWN_WITH_REASON,
        get_evidence_queue,
        kernel_reason,
    )
    from scp.task_kernel import TaskKernel

    queue = evidence_queue if evidence_queue is not None else get_evidence_queue()
    pending_evidence = 0

    ensure_capability_secret()
    db_path = Path(ask_db_path)
    if not db_path.exists():
        return {"status": "skipped", "reason": "db_not_found", "resolved_count": 0}

    kernel = TaskKernel(db_path)
    resolved_count = 0
    details: list[dict[str, str]] = []

    try:
        # Find all tasks in HUMAN_REVIEW
        stuck_tasks = kernel.conn.execute(
            "SELECT task_id, state, goal FROM tasks WHERE state = 'HUMAN_REVIEW'"
        ).fetchall()

        for row in stuck_tasks:
            task_id = row["task_id"]
            # Look up the last event transition reason
            ev = kernel.conn.execute(
                "SELECT reason FROM events WHERE task_id = ? AND to_state = 'HUMAN_REVIEW' ORDER BY seq DESC LIMIT 1",
                (task_id,),
            ).fetchone()
            last_reason = ev["reason"] if ev else "unknown_escalation"

            # Truthful fail-closed resolution (no claim of an audit that never ran).
            payload: dict[str, Any] = {"prior_reason": last_reason, "draft_answer": "withheld",
                                       "human_review_performed": False}
            if "security" in last_reason or "injection" in last_reason:
                audit_reason = "security_blocked_final"
                actor = f"{reviewer_actor}:security"
            elif "evidence" in last_reason or "contradicted" in last_reason:
                ticket = queue.find_by_task(task_id) if queue is not None else None
                if ticket is not None and ticket.get("status") == PENDING:
                    pending_evidence += 1
                    continue
                if ticket is not None:
                    audit_reason = kernel_reason(ticket)
                    payload["evidence_ticket"] = ticket.get("ticket_id")
                else:
                    audit_reason = f"{KERNEL_REASON_PREFIX}{UNKNOWN_WITH_REASON}:{ERR_EVIDENCE_UNAVAILABLE}"
                    payload["evidence_reason"] = "no_evidence_ticket (resolver absent or ticket evicted)"
                actor = f"{reviewer_actor}:evidence"
            else:
                audit_reason = f"lifecycle_unresolved_fail_closed:{last_reason}"
                actor = f"{reviewer_actor}:lifecycle"

            try:
                kernel.fail_task_fail_closed(
                    task_id=task_id,
                    reason=audit_reason,
                    actor=actor,
                    error_payload=payload,
                )
                resolved_count += 1
                details.append({"task_id": task_id, "resolution": audit_reason})
            except Exception as r_err:
                logger.warning("Could not resolve task %s: %s", task_id, r_err)

        # [HC-21 2026-10-23] Purge qua kernel accessor purge_tasks (journal-first:
        # tombstone event TASK_PURGED được ghi trước khi xóa projection row;
        # journal events giữ nguyên — không còn DELETE events kiểu "xóa lịch sử").
        # Lưu ý deviation có chủ đích: trước đây khối này còn xóa cả task ở
        # state PENDING không khớp pattern; giờ chỉ purge pattern 'stress_%'
        # đúng contract của accessor.
        purge_result = kernel.purge_tasks('stress_%', actor=f"{reviewer_actor}:lifecycle_recovery")
        purged_pending_count = purge_result["deleted_count"]
        for p_id in purge_result["task_ids"]:
            details.append({"task_id": p_id, "resolution": "purged_invalid_stress_task"})

        # Remaining check — qua accessor, không SQL thô
        remaining = len(kernel.query_by_state('HUMAN_REVIEW'))
        remaining_pending = len(kernel.query_by_state('PENDING'))

    finally:
        kernel.close()

    logger.info(
        "[resolve_stuck_human_review_tasks] Resolved %d HUMAN_REVIEW tasks, %d invalid PENDING purged, %d remaining",
        resolved_count,
        purged_pending_count,
        remaining,
    )
    return {
        "status": "ok",
        "resolved_count": resolved_count,
        "purged_pending_count": purged_pending_count,
        "remaining_human_review": remaining,
        "pending_evidence_tickets": pending_evidence,
        "remaining_pending": remaining_pending,
        "details_sample": details[:10],
    }
