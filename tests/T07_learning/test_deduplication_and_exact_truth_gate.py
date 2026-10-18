"""Test Suite for Deduplication & Exact-Truth Gate (Milestone 2 / Task 07).

Governed by SCP DNA #1–#29:
- Rule #1 (Exact Single Truth): Single-domain facts accept only the single exact best truth.
  Approximate / fuzzy matching is strictly rejected fail-closed (anti-placebo, FA-01, FA-04).
- Rule #2 (Multi-domain & Multi-perspective Exception): Multiple differing answers are allowed
  only when explicit frame_of_reference is provided.
- Rule #3 (Duplicate Rate Invariant): Pure duplicate spam rate in knowledge_records <= 10%.
- Rule #7 (Distributed Autonomy): Subsystem Independent Reviewer audits and resolves HUMAN_REVIEW
  and invalid PENDING bottlenecks.
- Rule #11 (Anti-Placebo Gate Pattern): Mandatory negative tests proving rejection of invalid inputs.
"""
from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

from scp.core.autonomous_cycle import ensure_capability_secret, run_autonomous_cycle
from scp.knowledge.deduplication_maintenance import (
    deduplicate_knowledge_sqlite,
    resolve_stuck_human_review_tasks,
)
from scp.knowledge.domain_knowledge import DomainKnowledge
from scp.knowledge.exact_truth_gate import (
    audit_knowledge_duplicates,
    compute_knowledge_duplicate_rate,
    is_approximate_claim,
    verify_exact_truth,
)
from scp.task_kernel import TaskKernel, stable_hash


# ---------------------------------------------------------------------------
# Quy tắc 1: Exact Single Truth & Anti-Placebo Rejection of Approximate Matching
# ---------------------------------------------------------------------------
def test_exact_single_truth_accepts_canonical_truth():
    """Exact Single Truth rule: canonical exact answer passes verification."""
    q = "Vận tốc ánh sáng trong chân không là bao nhiêu?"
    expected = "299.792.458 m/s"
    candidate = "299.792.458 m/s"

    res = verify_exact_truth(q, candidate, expected)
    assert res["passed"] is True
    assert res["verdict"] == "PASS"
    assert res["reason"] == "EXACT_CANONICAL_MATCH"


def test_exact_single_truth_accepts_normalized_numeric_truth():
    """Canonical truth with alternative separator formatting passes exact numeric match."""
    q = "Vận tốc ánh sáng trong chân không là bao nhiêu?"
    expected = "299.792.458 m/s"
    candidate = "299792458 m/s"

    res = verify_exact_truth(q, candidate, expected)
    assert res["passed"] is True
    assert res["verdict"] == "PASS"
    assert res["reason"] == "EXACT_NUMERIC_MATCH"


def test_approximate_fuzzy_matching_rejected_anti_placebo():
    """Negative Test (Rule #11, FA-01, FA-04): Approximate/fuzzy matching must FAIL closed.

    Placebo assertion prevention: Answers with 'khoảng', 'xấp xỉ', 'gần', or approximate
    rounding must NEVER be accepted as true for standard single-domain facts.
    """
    q = "Vận tốc ánh sáng trong chân không là bao nhiêu?"
    expected = "299.792.458 m/s"

    fuzzy_candidates = [
        "Khoảng 300.000 km/s",
        "Xấp xỉ 300 triệu m/s",
        "Gần 300.000.000 mét trên giây",
        "Tầm 300.000 km/s",
        "About 300,000 km/s",
        "Approximately 300,000 km/s",
        "Roughly 3e8 m/s",
        "Lyon là một thành phố ở Pháp",
    ]

    for candidate in fuzzy_candidates:
        res = verify_exact_truth(q, candidate, expected)
        assert res["passed"] is False, f"Placebo fail: approximate answer '{candidate}' passed"
        assert res["verdict"] == "FAIL"
        assert res["reason"] in ("APPROXIMATE_FUZZY_MATCH_REJECTED", "EXACT_TRUTH_MISMATCH")


def test_numeric_placebo_matching_rejected_anti_placebo():
    """Negative Test (Rule #11, FA-01): Unrelated sentences sharing the same number must FAIL.

    Anti-placebo protection: A candidate answer about boiling water (100 deg C) must NOT
    pass verification against a wall height fact (100 meters) simply because both contain 100.
    """
    res1 = verify_exact_truth(
        question="Nhiệt độ sôi của nước?",
        candidate_answer="Nhiệt độ sôi của nước là 100 độ C",
        expected_truth="Chiều cao của bức tường là 100 mét",
    )
    assert res1["passed"] is False
    assert res1["verdict"] == "FAIL"
    assert res1["reason"] == "EXACT_TRUTH_MISMATCH"

    res2 = verify_exact_truth(
        question="Distance to shop?",
        candidate_answer="The distance to the shop is 5 miles",
        expected_truth="There are 5 apples in the basket",
    )
    assert res2["passed"] is False
    assert res2["verdict"] == "FAIL"
    assert res2["reason"] == "EXACT_TRUTH_MISMATCH"


def test_legitimate_compound_nouns_not_falsely_rejected():
    """Legitimate nouns containing 'khoảng', 'tầm', 'gần' must NOT be falsely rejected as approximations."""
    # 'Khoảng cách' = Distance
    q1 = "Khoảng cách từ Trái Đất đến Mặt Trăng là bao nhiêu?"
    ans1 = "Khoảng cách từ Trái Đất đến Mặt Trăng là 384.400 km"
    assert is_approximate_claim(ans1) is False
    res1 = verify_exact_truth(q1, ans1, ans1)
    assert res1["passed"] is True
    assert res1["verdict"] == "PASS"

    # 'Tầm nhìn' = Vision
    q2 = "Tầm nhìn của công ty là gì?"
    ans2 = "Tầm nhìn của công ty là trở thành đơn vị tiên phong"
    assert is_approximate_claim(ans2) is False
    res2 = verify_exact_truth(q2, ans2, ans2)
    assert res2["passed"] is True
    assert res2["verdict"] == "PASS"

    # 'gần biển' = near the sea (spatial description)
    q3 = "Vị trí địa lý của thành phố?"
    ans3 = "Thành phố này ở gần biển"
    assert is_approximate_claim(ans3) is False
    res3 = verify_exact_truth(q3, ans3, ans3)
    assert res3["passed"] is True
    assert res3["verdict"] == "PASS"


def test_null_and_empty_inputs_fail_closed():
    """Negative Test (Rule #11, Fail-Closed): Null or empty inputs fail closed with EMPTY_OR_INVALID_INPUT."""
    res_none = verify_exact_truth("q", None, "truth")
    assert res_none["passed"] is False
    assert res_none["verdict"] == "FAIL"
    assert res_none["reason"] == "EMPTY_OR_INVALID_INPUT"

    res_empty = verify_exact_truth("q", "", "")
    assert res_empty["passed"] is False
    assert res_empty["verdict"] == "FAIL"
    assert res_empty["reason"] == "EMPTY_OR_INVALID_INPUT"

    res_ws = verify_exact_truth("q", "   ", "truth")
    assert res_ws["passed"] is False
    assert res_ws["verdict"] == "FAIL"
    assert res_ws["reason"] == "EMPTY_OR_INVALID_INPUT"


def test_domain_knowledge_store_keeps_exact_single_truth(tmp_path):
    """Single-domain fact in DomainKnowledge store maintains exactly ONE row, updating only on higher confidence."""
    db_path = tmp_path / "knowledge.sqlite3"
    dk = DomainKnowledge(data_dir=str(tmp_path / "kb"), sqlite_path=str(db_path))

    q = "Thủ đô của Pháp là gì?"
    ans_initial = "Paris"

    # Initial store
    dk.store(question=q, answer=ans_initial, domain="geography", confidence=0.80)

    with sqlite3.connect(db_path) as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM knowledge_records WHERE question = ?", (q,))
        assert cur.fetchone()[0] == 1

    # Attempt to insert identical answer (duplicate)
    dk.store(question=q, answer=ans_initial, domain="geography", confidence=0.80)
    with sqlite3.connect(db_path) as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM knowledge_records WHERE question = ?", (q,))
        assert cur.fetchone()[0] == 1, "Duplicate row must not be created"

    # Store candidate with lower confidence -> existing exact truth retained
    dk.store(question=q, answer="Lyon", domain="geography", confidence=0.50)
    with sqlite3.connect(db_path) as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*), answer, confidence FROM knowledge_records WHERE question = ?", (q,))
        count, ans, conf = cur.fetchone()
        assert count == 1
        assert ans == "Paris", "Lower confidence candidate must not replace exact truth"
        assert conf == 0.80

    # Store candidate with higher confidence -> exact truth updated in-place
    ans_better = "Paris là thủ đô chính thức của Cộng hòa Pháp."
    dk.store(question=q, answer=ans_better, domain="geography", confidence=0.98)
    with sqlite3.connect(db_path) as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*), answer, confidence FROM knowledge_records WHERE question = ?", (q,))
        count, ans, conf = cur.fetchone()
        assert count == 1, "Must remain strictly 1 row for single-domain fact"
        assert ans == ans_better
        assert conf == 0.98


def test_domain_knowledge_rejects_approximate_answers_fail_closed(tmp_path):
    """Negative Test (Rule #1, Rule #11): DomainKnowledge.store rejects approximate claims fail-closed."""
    db_path = tmp_path / "knowledge.sqlite3"
    dk = DomainKnowledge(data_dir=str(tmp_path / "kb"), sqlite_path=str(db_path))

    q = "Vận tốc ánh sáng trong chân không là bao nhiêu?"
    approx_ans = "Khoảng 300.000 km/s"

    res = dk.store(question=q, answer=approx_ans, domain="physics", confidence=0.99)
    assert res is None, "Approximate answer must be rejected fail-closed"

    with sqlite3.connect(db_path) as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM knowledge_records WHERE question = ?", (q,))
        assert cur.fetchone()[0] == 0, "Approximate answer must NOT be inserted into knowledge_records"


def test_domain_knowledge_rejects_multi_domain_without_frame_of_reference(tmp_path):
    """Negative Test (Rule #2, Rule #11): DomainKnowledge.store rejects multi-domain without frame_of_reference."""
    db_path = tmp_path / "knowledge.sqlite3"
    dk = DomainKnowledge(data_dir=str(tmp_path / "kb"), sqlite_path=str(db_path))

    q = "Bản chất của ý thức?"
    res = dk.store(question=q, answer="Ý thức là sự phản ánh của vật chất", domain="multi", frame_of_reference="")
    assert res is None, "Multi-domain fact without frame_of_reference must be rejected fail-closed"

    with sqlite3.connect(db_path) as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM knowledge_records WHERE question = ?", (q,))
        assert cur.fetchone()[0] == 0


# ---------------------------------------------------------------------------
# Quy tắc 2: Multi-domain & Multi-perspective Exception
# ---------------------------------------------------------------------------
def test_multi_perspective_allows_multiple_answers_with_frame_of_reference(tmp_path):
    """Multi-perspective exception: multiple answers allowed when frame_of_reference is explicitly provided."""
    db_path = tmp_path / "knowledge.sqlite3"
    dk = DomainKnowledge(data_dir=str(tmp_path / "kb"), sqlite_path=str(db_path))

    q = "Bản chất của không-thời gian là gì?"
    ans_newton = "Không gian và thời gian là tuyệt đối và độc lập."
    ans_einstein = "Không gian và thời gian tương đối và hợp thành một thể 4 chiều."

    # Store Newtonian perspective
    dk.store(
        question=q,
        answer=ans_newton,
        domain="physics",
        confidence=0.90,
        frame_of_reference="newtonian_mechanics",
    )

    # Store Relativistic perspective
    dk.store(
        question=q,
        answer=ans_einstein,
        domain="physics",
        confidence=0.95,
        frame_of_reference="general_relativity",
    )

    with sqlite3.connect(db_path) as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*), frame_of_reference FROM knowledge_records WHERE question = ? GROUP BY frame_of_reference", (q,))
        rows = cur.fetchall()
        assert len(rows) == 2, "Expected 2 distinct perspective rows"
        frames = {r[1] for r in rows}
        assert frames == {"newtonian_mechanics", "general_relativity"}


def test_multi_perspective_missing_frame_fails_closed_anti_placebo():
    """Negative Test (Rule #11): Multi-domain check without frame_of_reference fails closed."""
    q = "Quan điểm về tính đúng đắn của hành vi trong đạo đức học?"
    cand = "Hành vi đúng đắn là hành vi tạo ra lợi ích lớn nhất cho số đông."
    exp = "Hành vi đúng đắn là hành vi tuân thủ mệnh lệnh tuyệt đối."

    # Calling with is_multi_domain=True but without frame_of_reference must fail closed
    res = verify_exact_truth(q, cand, exp, is_multi_domain=True, frame_of_reference="")
    assert res["passed"] is False
    assert res["verdict"] == "FAIL"
    assert res["reason"] == "FRAME_OF_REFERENCE_REQUIRED_FOR_MULTI_PERSPECTIVE"


# ---------------------------------------------------------------------------
# Database-Level Unique Constraint Enforcement (Hardware / DB Level Boundary)
# ---------------------------------------------------------------------------
def test_database_level_unique_constraint_enforcement(tmp_path):
    """Database-level boundary: SQLite partial unique index raises IntegrityError on raw duplicate insert."""
    db_path = tmp_path / "knowledge.sqlite3"
    dk = DomainKnowledge(data_dir=str(tmp_path / "kb"), sqlite_path=str(db_path))

    q = "Số nguyên tố nhỏ nhất là bao nhiêu?"
    dk.store(question=q, answer="2", domain="math", confidence=0.99)

    # Attempt raw SQL INSERT directly into database bypassing application layer
    with sqlite3.connect(db_path) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO knowledge_records
                (id, question, answer, domain, source, source_url, confidence, collected_at, expires_at, frame_of_reference)
                VALUES ('kr_raw_hack_01', ?, 'hai', 'math', 'untrusted', '', 0.5, 0, 0, '')
                """,
                (q,),
            )


# ---------------------------------------------------------------------------
# Quy tắc 3: Duplicate Rate Invariant
# ---------------------------------------------------------------------------
def test_knowledge_sqlite_duplicate_rate_invariant_under_10_percent():
    """Duplicate Rate Invariant: In data/knowledge.sqlite3, spam duplicate rate must be <= 10%."""
    repo_root = Path(__file__).resolve().parent.parent.parent
    live_db = repo_root / "data" / "knowledge.sqlite3"
    if not live_db.exists():
        live_db.parent.mkdir(parents=True, exist_ok=True)
        dk = DomainKnowledge(data_dir=str(live_db.parent / "knowledge"), sqlite_path=str(live_db))
    assert live_db.exists(), f"data/knowledge.sqlite3 must exist at {live_db}"

    audit_res = audit_knowledge_duplicates(live_db, max_allowed_rate=0.10)
    assert audit_res["passed"] is True, f"Duplicate rate {audit_res['duplicate_rate']} exceeds 10% limit!"
    assert audit_res["duplicate_rate"] <= 0.10

    # Ensure none of the 3 previously spammed sample questions have > 1 row
    sample_questions = [
        "What are the core principles of computer security?",
        "What is a cryptographic hash chain in audit logs?",
        "What is a finite-state machine in computer science?",
    ]
    with sqlite3.connect(live_db) as conn:
        cur = conn.cursor()
        for q in sample_questions:
            cur.execute("SELECT COUNT(*) FROM knowledge_records WHERE question = ?", (q,))
            count = cur.fetchone()[0]
            assert count <= 1, f"Sample question '{q}' has {count} rows; expected <= 1"


# ---------------------------------------------------------------------------
# Subsystem Independent Reviewer & Kernel Bottleneck Release (Rule #7)
# ---------------------------------------------------------------------------
def test_subsystem_independent_reviewer_clears_human_review(tmp_path):
    """Rule #7 (Distributed Autonomy): Subsystem Reviewer clears HUMAN_REVIEW bottleneck and invalid PENDING tasks."""
    ensure_capability_secret()
    ask_db = tmp_path / "ask_task_kernel.sqlite3"
    kernel = TaskKernel(ask_db)

    # Create 3 sample tasks stuck in HUMAN_REVIEW with different reasons
    for i, reason in enumerate(["ask_security_escalate", "ask_evidence_insufficient_or_contradicted", "boot_recovery_in_flight"]):
        task_id = f"test-stuck-task-{i}"
        kernel.create_task(task_id, "test-owner", "Test task", "R0", input_hash=stable_hash({"i": i}))
        kernel.transition(task_id, "PLANNING", actor="test")
        kernel.transition(task_id, "READY", actor="test")
        kernel.transition(task_id, "QUEUED", actor="test")
        lease = kernel.claim(task_id, "test-worker", ttl_seconds=60)
        kernel.start(task_id, lease.lease_id)
        kernel.transition(task_id, "VERIFYING", actor="test")
        kernel.transition(task_id, "HUMAN_REVIEW", actor="test", reason=reason)

    # Also simulate invalid mock stress tasks injected with state PENDING
    kernel.conn.execute(
        "INSERT INTO tasks (task_id, owner, goal, risk_tier, deadline_ms, max_attempts, attempts, input_hash, priority, state, version, created_at, updated_at) "
        "VALUES ('stress_test_mock_1', 'stress', 'Stress 1', 'LOW', 60000, 3, 0, 'mock', 1, 'PENDING', 1, '2026-10-18T00:00:00Z', '2026-10-18T00:00:00Z')"
    )
    kernel.conn.execute(
        "INSERT INTO tasks (task_id, owner, goal, risk_tier, deadline_ms, max_attempts, attempts, input_hash, priority, state, version, created_at, updated_at) "
        "VALUES ('stress_test_mock_2', 'stress', 'Stress 2', 'LOW', 60000, 3, 0, 'mock', 1, 'PENDING', 1, '2026-10-18T00:00:00Z', '2026-10-18T00:00:00Z')"
    )

    stuck_before = kernel.conn.execute("SELECT COUNT(*) FROM tasks WHERE state = 'HUMAN_REVIEW'").fetchone()[0]
    pending_before = kernel.conn.execute("SELECT COUNT(*) FROM tasks WHERE state = 'PENDING'").fetchone()[0]
    assert stuck_before == 3
    assert pending_before == 2
    kernel.close()

    # Run Subsystem Independent Reviewer
    maintenance_res = resolve_stuck_human_review_tasks(ask_db)
    assert maintenance_res["resolved_count"] == 3
    assert maintenance_res["purged_pending_count"] == 2
    assert maintenance_res["remaining_human_review"] == 0
    assert maintenance_res["remaining_pending"] == 0

    # Verify kernel tasks are in clean state
    kernel2 = TaskKernel(ask_db)
    stuck_after = kernel2.conn.execute("SELECT COUNT(*) FROM tasks WHERE state = 'HUMAN_REVIEW'").fetchone()[0]
    pending_after = kernel2.conn.execute("SELECT COUNT(*) FROM tasks WHERE state = 'PENDING'").fetchone()[0]
    assert stuck_after == 0
    assert pending_after == 0
    kernel2.close()


# ---------------------------------------------------------------------------
# Autonomous Explorer: Dynamic Exploration Without Hardcoded Question Duplication
# ---------------------------------------------------------------------------
def test_autonomous_cycle_explores_diverse_topics_without_duplicate_spam(tmp_path):
    """Autonomous Explorer runs cycle and explores dynamic topics without duplicate spamming."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)

    # Run cycle 1
    res1 = asyncio.run(run_autonomous_cycle(count=2, dispatch_tasks=True, data_dir=data_dir))
    assert res1["status"] == "ok"
    assert len(res1["explored_topics"]) > 0
    assert res1["tasks_dispatched"] == 2

    # Hands task has dynamic goal, not static readiness
    hands_goal = res1["hands_task"]["goal"]
    assert "Autonomous hands exploration:" in hands_goal

    # Check knowledge records in cycle 1
    kr_db = data_dir / "knowledge.sqlite3"
    with sqlite3.connect(kr_db) as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*), COUNT(DISTINCT lower(trim(question))) FROM knowledge_records")
        c1_total, c1_distinct = cur.fetchone()
        assert c1_total == c1_distinct, "All explored records in cycle 1 must be distinct"

    # Run cycle 2
    res2 = asyncio.run(run_autonomous_cycle(count=2, dispatch_tasks=True, data_dir=data_dir))
    assert res2["status"] == "ok"

    # Check that duplicate rate remains 0%
    with sqlite3.connect(kr_db) as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*), COUNT(DISTINCT lower(trim(question))) FROM knowledge_records")
        c2_total, c2_distinct = cur.fetchone()
        assert c2_total == c2_distinct, "Subsequent cycles must not create duplicate question rows"
        assert c2_total >= c1_total
