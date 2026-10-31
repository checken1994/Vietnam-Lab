"""Contract tests for SCP Lesson Ledger (Law 12 & Law 15).

Invariants verified:
1. Append-only storage format (JSONL).
2. Default status is HYPOTHESIS.
3. Strict fail-closed boundary: status APPLIED rejected without explicit owner approval.
4. Querying and filtering by status and source.
5. Anti-placebo: Corrupted lines handled gracefully without crashing the query pipeline.
"""
import json
import pytest
from pathlib import Path
from scp.learning.lesson_ledger import (
    record_lesson,
    query_lessons,
    summarize_lessons,
    VALID_STATUSES,
)


def test_lesson_ledger_default_hypothesis(tmp_path: Path):
    ledger = tmp_path / "lessons.jsonl"
    rec = record_lesson(
        observation="Test observation",
        hypothesis="Test hypothesis",
        ledger_path=ledger,
    )
    assert rec["status"] == "HYPOTHESIS"
    assert rec["kill_switch_ref"] == "owner-on-top"
    assert len(rec["why_chain"]) == 5
    assert ledger.exists()

    lines = ledger.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    loaded = json.loads(lines[0])
    assert loaded["lesson_id"] == rec["lesson_id"]
    assert loaded["status"] == "HYPOTHESIS"


def test_lesson_ledger_invalid_status_rejected(tmp_path: Path):
    ledger = tmp_path / "lessons.jsonl"
    with pytest.raises(ValueError, match="Invalid status"):
        record_lesson(
            observation="Obs",
            hypothesis="Hyp",
            status="INVALID_STATUS",
            ledger_path=ledger,
        )


def test_lesson_ledger_applied_requires_owner_approval(tmp_path: Path):
    """Anti-Placebo: Law 12 kill-switch prevents self-promotion to APPLIED."""
    ledger = tmp_path / "lessons.jsonl"
    # Attempting to apply without owner approval must fail closed
    with pytest.raises(PermissionError, match="KILL-SWITCH"):
        record_lesson(
            observation="Obs",
            hypothesis="Hyp",
            status="APPLIED",
            owner_approved=False,
            ledger_path=ledger,
        )

    # Attempting with owner approval but without independent check verdict=PASS must fail closed
    with pytest.raises(ValueError, match="FAIL-CLOSED"):
        record_lesson(
            observation="Obs",
            hypothesis="Hyp",
            status="APPLIED",
            owner_approved=True,
            independent_check={"by": "checker", "verdict": "FAIL"},
            ledger_path=ledger,
        )

    # With both owner approval and independent check PASS, it succeeds
    rec = record_lesson(
        observation="Obs",
        hypothesis="Hyp",
        status="APPLIED",
        owner_approved=True,
        independent_check={"by": "checker", "verdict": "PASS", "at": "2026-10-31T00:00:00Z"},
        ledger_path=ledger,
    )
    assert rec["status"] == "APPLIED"


def test_lesson_ledger_query_and_summary(tmp_path: Path):
    ledger = tmp_path / "lessons.jsonl"
    record_lesson(observation="Obs 1", hypothesis="Hyp 1", status="HYPOTHESIS", trigger={"source": "e2e"}, ledger_path=ledger)
    record_lesson(observation="Obs 2", hypothesis="Hyp 2", status="SHADOW", trigger={"source": "audit"}, ledger_path=ledger)
    record_lesson(observation="Obs 3", hypothesis="Hyp 3", status="REALITY-CHECKED", trigger={"source": "e2e"}, ledger_path=ledger)

    all_items = query_lessons(ledger_path=ledger)
    assert len(all_items) == 3

    hyp_items = query_lessons(status="HYPOTHESIS", ledger_path=ledger)
    assert len(hyp_items) == 1
    assert hyp_items[0]["hypothesis"] == "Hyp 1"

    e2e_items = query_lessons(source="e2e", ledger_path=ledger)
    assert len(e2e_items) == 2

    summary = summarize_lessons(ledger_path=ledger)
    assert summary["total_lessons"] == 3
    assert summary["status_counts"]["HYPOTHESIS"] == 1
    assert summary["status_counts"]["SHADOW"] == 1
    assert summary["status_counts"]["REALITY-CHECKED"] == 1
    assert summary["status_counts"]["APPLIED"] == 0


def test_lesson_ledger_resilient_to_corrupt_lines(tmp_path: Path):
    """Anti-Placebo: Corrupt lines in JSONL do not crash query or summary."""
    ledger = tmp_path / "lessons.jsonl"
    record_lesson(observation="Valid 1", hypothesis="Hyp 1", ledger_path=ledger)
    # Inject corrupt line
    with open(ledger, "a", encoding="utf-8") as f:
        f.write("{corrupt-json-line}\n")
    record_lesson(observation="Valid 2", hypothesis="Hyp 2", ledger_path=ledger)

    items = query_lessons(ledger_path=ledger)
    assert len(items) == 2
    summary = summarize_lessons(ledger_path=ledger)
    assert summary["total_lessons"] == 2


def test_lesson_ledger_invalid_verdict_rejected(tmp_path: Path):
    """Schema adherence: independent_check.verdict must be PENDING|PASS|FAIL|UNKNOWN."""
    ledger = tmp_path / "lessons.jsonl"
    with pytest.raises(ValueError, match="Invalid independent_check verdict"):
        record_lesson(
            observation="Obs",
            hypothesis="Hyp",
            independent_check={"by": "checker", "verdict": "FLAGGED"},
            ledger_path=ledger,
        )


def test_seed_lessons_idempotent(tmp_path: Path):
    """Bug fix verification: Re-running seed_lessons must not duplicate ledger records."""
    from tools.seed_lesson import seed_lessons

    ledger = tmp_path / "lessons.jsonl"
    added_first = seed_lessons(ledger_path=ledger)
    assert added_first == 10

    # Second run must add 0 lessons
    added_second = seed_lessons(ledger_path=ledger)
    assert added_second == 0

    lines = [ln for ln in ledger.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 10

