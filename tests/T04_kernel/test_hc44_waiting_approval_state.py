"""HC-44 Anti-Placebo: WAITING_APPROVAL must be a first-class kernel state.

Split-brain proven before this fix:
  - ALLOWED_TRANSITIONS contained "WAITING_APPROVAL" (PLANNING -> WA -> READY/CANCELLED)
  - STATES (the 17-element schema set) did NOT contain it
  - state_machine.py papered over the gap with a carve-out:
    `to_state not in STATES and to_state != "WAITING_APPROVAL"`

This module pins the repaired reality (18 states) with positive, negative and
schema-consistency invariants, using only in-memory/tmp_path TaskKernel
instances. It never touches live data/ directories.

Run scope: tests/T04_kernel (same container profile as the rest of the suite).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from scp.task_kernel import (
    ALLOWED_TRANSITIONS,
    STATES,
    TERMINAL,
    InvalidTransition,
    TaskKernel,
)

HC44_STATE = "WAITING_APPROVAL"
LEGACY_17_STATES = sorted(STATES - {HC44_STATE})  # the pre-HC-44 schema set


# ---------------------------------------------------------------------------
# (a) Schema-level invariants
# ---------------------------------------------------------------------------

def test_hc44_waiting_approval_is_a_real_state() -> None:
    assert HC44_STATE in STATES
    assert len(STATES) == 18
    assert HC44_STATE not in TERMINAL


def test_hc44_legacy_17_states_unchanged() -> None:
    """Backward-compat: every pre-HC-44 state must survive the fix untouched."""
    assert len(LEGACY_17_STATES) == 17
    assert set(LEGACY_17_STATES) == {
        "CREATED", "PLANNING", "READY", "QUEUED", "LEASED", "RUNNING",
        "WAITING_TOOL", "VERIFYING", "CHECKPOINTED", "UNKNOWN", "RECOVERING",
        "RECONCILING", "HUMAN_REVIEW", "RETRY_SCHEDULED", "COMPLETED",
        "FAILED", "CANCELLED",
    }


def test_hc44_no_more_split_brain_between_states_and_transition_map() -> None:
    """Every ALLOWED_TRANSITIONS key AND target must be a member of STATES."""
    for from_state, targets in ALLOWED_TRANSITIONS.items():
        assert from_state in STATES, f"transition-map key {from_state!r} not in STATES (split-brain)"
        for target in targets:
            assert target in STATES, f"transition {from_state}->{target} targets a state outside STATES (split-brain)"


def test_hc44_only_planning_enters_waiting_approval() -> None:
    """WA inbound edges: PLANNING only. Outbound edges: READY + CANCELLED only."""
    for from_state, targets in ALLOWED_TRANSITIONS.items():
        if from_state != "PLANNING":
            assert HC44_STATE not in targets, (
                f"{from_state}->{HC44_STATE} is not authorized by the HC-44 contract"
            )
    assert ALLOWED_TRANSITIONS["PLANNING"] >= {HC44_STATE}
    assert ALLOWED_TRANSITIONS[HC44_STATE] == {"READY", "CANCELLED"}


# ---------------------------------------------------------------------------
# (b) Positive: PLANNING -> WAITING_APPROVAL via the real kernel API
# ---------------------------------------------------------------------------

def _make_kernel(tmp_path: Path, name: str) -> TaskKernel:
    return TaskKernel(tmp_path / f"hc44_{name}.sqlite3")


def test_hc44_planning_to_waiting_approval_succeeds_on_real_kernel(tmp_path: Path) -> None:
    kernel = _make_kernel(tmp_path, "positive")
    try:
        kernel.create_task("hc44-pos-1", owner="hc44-tester", goal="gated high-risk action", risk_tier="R2")
        kernel.transition("hc44-pos-1", "PLANNING", actor="planner")
        task = kernel.transition(
            "hc44-pos-1", HC44_STATE, actor="risk_policy", reason="hc44_high_risk_gate"
        )
        assert task["state"] == HC44_STATE

        # Physical SQLite check (FA-08): the row really carries the state
        conn = sqlite3.connect(str(tmp_path / "hc44_positive.sqlite3"))
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT state, version FROM tasks WHERE task_id=?", ("hc44-pos-1",)
            ).fetchone()
            assert row is not None and row["state"] == HC44_STATE
        finally:
            conn.close()

        # Journal recorded the gated transition
        events = kernel.get_events("hc44-pos-1")
        wa_events = [e for e in events if e["to_state"] == HC44_STATE]
        assert len(wa_events) == 1
        assert wa_events[0]["from_state"] == "PLANNING"
        assert wa_events[0]["actor"] == "risk_policy"
    finally:
        kernel.close()


def test_hc44_waiting_approval_to_cancelled_is_legal(tmp_path: Path) -> None:
    """The other WA outbound edge (WA -> CANCELLED) must work for real."""
    kernel = _make_kernel(tmp_path, "cancel")
    try:
        kernel.create_task("hc44-cancel-1", owner="hc44-tester", goal="cancel from gate", risk_tier="R2")
        kernel.transition("hc44-cancel-1", "PLANNING", actor="planner")
        kernel.transition("hc44-cancel-1", HC44_STATE, actor="risk_policy", reason="hc44_high_risk_gate")
        task = kernel.transition("hc44-cancel-1", "CANCELLED", actor="operator", reason="user_revoked")
        assert task["state"] == "CANCELLED"
    finally:
        kernel.close()


# ---------------------------------------------------------------------------
# (c) Anti-placebo: terminal immutability + raw-bypass + unknown states
# ---------------------------------------------------------------------------

def _drive_to_terminal(kernel: TaskKernel, terminal_state: str, task_id: str) -> None:
    """Reach a terminal state through the real commit/transition APIs only."""
    if terminal_state == "CANCELLED":
        kernel.create_task(task_id, owner="hc44-tester", goal="terminal guard", risk_tier="R2")
        kernel.transition(task_id, "PLANNING", actor="planner")
        kernel.transition(task_id, "CANCELLED", actor="operator", reason="manual_cancel")
        return
    kernel.create_task(task_id, owner="hc44-tester", goal="terminal guard", risk_tier="R2", max_attempts=1)
    kernel.transition(task_id, "PLANNING", actor="planner")
    kernel.transition(task_id, "READY", actor="planner")
    kernel.transition(task_id, "QUEUED", actor="dispatcher")
    lease = kernel.claim(task_id, "hc44-worker")
    kernel.start(task_id, lease.lease_id)
    if terminal_state == "FAILED":
        kernel.commit_failed(task_id, lease.lease_id, "hc44-worker", "FATAL", "ref://fatal-err", {"err": "fatal"})
        return
    assert terminal_state == "COMPLETED"
    kernel.transition(task_id, "VERIFYING", lease_id=lease.lease_id)
    kernel.commit_completed(task_id, lease.lease_id, "VERIFIED", "ev://hc44_audit_passed")


@pytest.mark.parametrize("terminal_state", ["CANCELLED", "FAILED", "COMPLETED"])
def test_hc44_terminal_states_can_never_reenter_waiting_approval(tmp_path: Path, terminal_state: str) -> None:
    kernel = _make_kernel(tmp_path, f"terminal_{terminal_state.lower()}")
    try:
        task_id = f"hc44-term-{terminal_state.lower()}"
        _drive_to_terminal(kernel, terminal_state, task_id)
        assert kernel.get_task(task_id)["state"] == terminal_state
        before = kernel.get_task(task_id)["version"]

        with pytest.raises(InvalidTransition) as exc_info:
            kernel.transition(task_id, HC44_STATE, actor="attacker", reason="resurrect")

        msg = str(exc_info.value)
        assert (
            f"{terminal_state}->{HC44_STATE}" in msg or "terminal task is immutable" in msg
        ), f"unexpected rejection reason: {msg}"
        assert kernel.get_task(task_id)["state"] == terminal_state
        assert kernel.get_task(task_id)["version"] == before  # zero mutation
    finally:
        kernel.close()


def test_hc44_raw_waiting_approval_to_ready_still_blocked(tmp_path: Path) -> None:
    """GAP-13 regression: adding WA to STATES must NOT reopen the raw bypass."""
    kernel = _make_kernel(tmp_path, "raw_bypass")
    try:
        kernel.create_task("hc44-raw-1", owner="hc44-tester", goal="bypass attempt", risk_tier="R2")
        kernel.transition("hc44-raw-1", "PLANNING", actor="planner")
        kernel.transition("hc44-raw-1", HC44_STATE, actor="risk_policy", reason="hc44_high_risk_gate")

        with pytest.raises(InvalidTransition) as exc_info:
            kernel.transition("hc44-raw-1", "READY", actor="unauthenticated_attacker")
        assert "direct transition from WAITING_APPROVAL to READY is forbidden" in str(exc_info.value)
        assert kernel.get_task("hc44-raw-1")["state"] == HC44_STATE
    finally:
        kernel.close()


def test_hc44_illegal_wa_and_unknown_targets_still_rejected(tmp_path: Path) -> None:
    kernel = _make_kernel(tmp_path, "illegal_targets")
    try:
        kernel.create_task("hc44-illegal-1", owner="hc44-tester", goal="illegal targets", risk_tier="R2")
        kernel.transition("hc44-illegal-1", "PLANNING", actor="planner")
        kernel.transition("hc44-illegal-1", HC44_STATE, actor="risk_policy", reason="hc44_high_risk_gate")

        # WA may not self-loop, hop to QUEUED, or accept look-alike strings
        for bad_target in [HC44_STATE, "QUEUED", "RUNNING", "APPROVED", "WAITING_APPROVAL ", "waiting_approval"]:
            with pytest.raises(InvalidTransition):
                kernel.transition("hc44-illegal-1", bad_target, actor="attacker")
        assert kernel.get_task("hc44-illegal-1")["state"] == HC44_STATE
    finally:
        kernel.close()


def test_hc44_predicate_view_matches_map(tmp_path: Path) -> None:
    kernel = _make_kernel(tmp_path, "predicate")
    try:
        assert kernel._can_transition("PLANNING", HC44_STATE) is True
        assert kernel._can_transition(HC44_STATE, "CANCELLED") is True
        assert kernel._can_transition(HC44_STATE, "READY") is False  # commit_approval() only
        assert kernel._can_transition("CREATED", HC44_STATE) is False
        assert kernel._can_transition("CANCELLED", HC44_STATE) is False
        assert kernel.get_valid_next_states(HC44_STATE) == {"READY", "CANCELLED"}
    finally:
        kernel.close()
