"""[R7 2026-10-28] Transition authority gates: edge-class kill-switch + anti-bypass.

Contract (verified REAL by probes on 2026-10-28, then pinned here):
1. Kill-switch theo LOẠI CẠNH: khi global kill active, chỉ cạnh về
   SAFE_UNDER_KILL được phép; mọi cạnh resume (RESUME_TARGETS) bị chặn —
   kể cả với _system_authority (boot recovery). Cạnh về CANCELLED/FAILED
   (containment) vẫn ĐƯỢC PHÉP dưới kill.
2. Chặn theo LỠ CẠNH (không liệt kê cặp — OWNER MANDATE @ bfb40026): mọi cạnh
   từ CONTROLLED_STATES về DISPATCH_TARGETS qua transition() generic bị chặn;
   phải qua authority gate chính thức (commit_approval /
   reconcile_unknown / auto_resolve_human_review).
3. Chặn đường vòng trung gian: RECONCILING->CHECKPOINTED->QUEUED và
   RECONCILING->RECOVERING->QUEUED không được lọt (predecessor-contained
   check trên journal cho dispatch edge từ CHECKPOINTED).
4. Boot recovery trên chuỗi sạch (kill TẮT) vẫn chạy đủ, kể cả nhánh
   CHECKPOINTED->QUEUED (pred = RUNNING, không contained).

FA-04 zero-mock: mọi test dùng TaskKernel thật trên SQLite tmp_path;
không mock, không stub, không skip.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from scp.task_kernel import (
    InvalidTransition,
    KillSwitchActive,
    TaskKernel,
)
from scp.task_kernel_parts.definitions import (
    CONTROLLED_STATES,
    DISPATCH_TARGETS,
    RESUME_TARGETS,
    SAFE_UNDER_KILL,
)


def _kernel(tmp_path: Path, name: str = "r7.sqlite3", **kw) -> TaskKernel:
    return TaskKernel(tmp_path / name, **kw)


def _setup_running(kernel: TaskKernel, task_id: str, owner: str = "r7-owner"):
    """Fresh task driven to RUNNING with a live lease (legal intake path)."""
    kernel.create_task(task_id, owner, "r7 gate probe", "R1")
    for state in ("PLANNING", "READY", "QUEUED"):
        kernel.transition(task_id, state, actor="setup")
    lease = kernel.claim(task_id, "worker-1", ttl_seconds=300)
    kernel.start(task_id, lease.lease_id)
    return lease


# ---------------------------------------------------------------------------
# Class constants sanity (chống phân phối nhầm lớp cạnh)
# ---------------------------------------------------------------------------

def test_r7_edge_class_constants_are_disjoint_and_complete():
    """SAFE_UNDER_KILL và RESUME_TARGETS không giao nhau; controlled/dispatch
    là tập con của STATES theo thiết kế R7."""
    from scp.task_kernel_parts.definitions import STATES

    assert not (SAFE_UNDER_KILL & RESUME_TARGETS), "resume targets must never be kill-safe"
    assert CONTROLLED_STATES <= STATES
    assert DISPATCH_TARGETS <= STATES
    assert DISPATCH_TARGETS <= RESUME_TARGETS, "dispatch targets are resume-class edges"
    assert CONTROLLED_STATES & DISPATCH_TARGETS == set()


# ---------------------------------------------------------------------------
# (2) Direct controlled->dispatch edges blocked without lease/authority
# ---------------------------------------------------------------------------

def test_r7_direct_pair_human_review_to_ready_blocked_without_gate(tmp_path: Path):
    kernel = _kernel(tmp_path, "r7-direct-hr.sqlite3", autonomous_mode=True)
    try:
        _setup_running(kernel, "r7-hr")
        kernel.transition("r7-hr", "HUMAN_REVIEW")
        with pytest.raises(InvalidTransition, match="controlled-state dispatch edge"):
            kernel.transition("r7-hr", "READY", actor="attacker", reason="no_gate")
        assert kernel.get_task("r7-hr")["state"] == "HUMAN_REVIEW"
        # Authority gate still works (autonomous_mode on this kernel).
        kernel.auto_resolve_human_review("r7-hr", reason="autonomous_verification_passed")
        assert kernel.get_task("r7-hr")["state"] == "READY"
    finally:
        kernel.close()


def test_r7_direct_pair_reconciling_to_queued_blocked_without_evidence(tmp_path: Path):
    kernel = _kernel(tmp_path, "r7-direct-rec.sqlite3")
    try:
        _setup_running(kernel, "r7-rec")
        kernel.transition("r7-rec", "RECOVERING")
        kernel.transition("r7-rec", "RECONCILING")
        with pytest.raises(InvalidTransition, match="controlled-state dispatch edge"):
            kernel.transition("r7-rec", "QUEUED", actor="attacker", reason="no_evidence")
        assert kernel.get_task("r7-rec")["state"] == "RECONCILING"
    finally:
        kernel.close()


def test_r7_direct_pair_recovering_to_queued_blocked(tmp_path: Path):
    kernel = _kernel(tmp_path, "r7-direct-recovering.sqlite3")
    try:
        _setup_running(kernel, "r7-rs")
        kernel.transition("r7-rs", "RECOVERING")
        with pytest.raises(InvalidTransition, match="controlled-state dispatch edge"):
            kernel.transition("r7-rs", "QUEUED")
        assert kernel.get_task("r7-rs")["state"] == "RECOVERING"
    finally:
        kernel.close()


def test_r7_waiting_approval_to_ready_raw_transition_still_blocked(tmp_path: Path):
    """Pin rule sẵn (:193 state_machine): WAITING_APPROVAL->READY qua generic
    bị cấm với thông điệp riêng — chỉ commit_approval() với token hợp lệ."""
    kernel = _kernel(tmp_path, "r7-wa.sqlite3")
    try:
        kernel.create_task("r7-wa", "own", "goal", "R3")
        kernel.transition("r7-wa", "PLANNING")
        kernel.transition("r7-wa", "WAITING_APPROVAL", reason="high_risk_tier")
        with pytest.raises(
            InvalidTransition,
            match="direct transition from WAITING_APPROVAL to READY is forbidden",
        ):
            kernel.transition("r7-wa", "READY", actor="bypasser")
        assert kernel.get_task("r7-wa")["state"] == "WAITING_APPROVAL"
    finally:
        kernel.close()


# ---------------------------------------------------------------------------
# (2b) Bypass qua trung gian bị chặn
# ---------------------------------------------------------------------------

def test_r7_bypass_reconciling_checkpointed_queued_blocked(tmp_path: Path):
    """Đường vòng 1: RECONCILING -> CHECKPOINTED -> QUEUED không được lọt."""
    kernel = _kernel(tmp_path, "r7-bypass-cp.sqlite3")
    try:
        _setup_running(kernel, "r7-bp1")
        kernel.transition("r7-bp1", "RECOVERING")
        kernel.transition("r7-bp1", "RECONCILING")
        kernel.transition("r7-bp1", "CHECKPOINTED")
        assert kernel.get_task("r7-bp1")["state"] == "CHECKPOINTED"
        with pytest.raises(InvalidTransition, match="entered CHECKPOINTED from a controlled state"):
            kernel.transition("r7-bp1", "QUEUED")
        # Cả nhánh RUNNING cũng là cạnh dispatch — cùng bị chặn.
        with pytest.raises(InvalidTransition, match="entered CHECKPOINTED from a controlled state"):
            kernel.transition("r7-bp1", "RUNNING")
        assert kernel.get_task("r7-bp1")["state"] == "CHECKPOINTED"
    finally:
        kernel.close()


def test_r7_bypass_reconciling_recovering_queued_blocked(tmp_path: Path):
    """Đường vòng 2: RECONCILING -> RECOVERING -> QUEUED không được lọt."""
    kernel = _kernel(tmp_path, "r7-bypass-rs.sqlite3")
    try:
        _setup_running(kernel, "r7-bp2")
        kernel.transition("r7-bp2", "RECOVERING")
        kernel.transition("r7-bp2", "RECONCILING")
        kernel.transition("r7-bp2", "RECOVERING")
        assert kernel.get_task("r7-bp2")["state"] == "RECOVERING"
        with pytest.raises(InvalidTransition, match="controlled-state dispatch edge"):
            kernel.transition("r7-bp2", "QUEUED")
        assert kernel.get_task("r7-bp2")["state"] == "RECOVERING"
    finally:
        kernel.close()


# ---------------------------------------------------------------------------
# (4) System authority không được miễn kill-check và edge-class gate
# ---------------------------------------------------------------------------

def test_r7_system_authority_cannot_bypass_controlled_dispatch_gate(tmp_path: Path):
    kernel = _kernel(tmp_path, "r7-sysauth.sqlite3")
    try:
        _setup_running(kernel, "r7-sys")
        kernel.transition("r7-sys", "RECOVERING")
        kernel._system_authority = True
        try:
            with pytest.raises(InvalidTransition, match="controlled-state dispatch edge"):
                kernel.transition("r7-sys", "QUEUED", actor="system_watchdog")
        finally:
            kernel._system_authority = False
        assert kernel.get_task("r7-sys")["state"] == "RECOVERING"
    finally:
        kernel.close()


def test_r7_system_authority_cannot_bypass_kill_on_resume_edge(tmp_path: Path):
    kernel = _kernel(tmp_path, "r7-syskill.sqlite3")
    try:
        lease = _setup_running(kernel, "r7-sk")
        kernel.transition("r7-sk", "CHECKPOINTED", lease_id=lease.lease_id)
        kernel.set_global_kill(True)
        kernel._system_authority = True
        try:
            with pytest.raises(KillSwitchActive):
                kernel.transition("r7-sk", "QUEUED", actor="boot_recovery")
        finally:
            kernel._system_authority = False
        assert kernel.get_task("r7-sk")["state"] == "CHECKPOINTED"
    finally:
        kernel.close()


# ---------------------------------------------------------------------------
# (1) Kill-switch theo loại cạnh
# ---------------------------------------------------------------------------

def test_r7_kill_active_blocks_resume_edge_but_allows_cancel_and_fail(tmp_path: Path):
    kernel = _kernel(tmp_path, "r7-kill.sqlite3", autonomous_mode=True)
    try:
        # Park one task in RETRY_SCHEDULED (retry worker requeue = resume edge).
        lease = _setup_running(kernel, "r7-k1")
        kernel.commit_failed("r7-k1", lease.lease_id, "worker-1", "RETRYABLE", "indict://r7")
        assert kernel.get_task("r7-k1")["state"] == "RETRY_SCHEDULED"
        # A second, non-terminal lease-less task for the CANCELLED probe, and a
        # third for the FAILED probe — both staged BEFORE the kill switch.
        kernel.create_task("r7-k2", "own", "goal")
        kernel.transition("r7-k2", "PLANNING")
        kernel.transition("r7-k2", "READY")
        kernel.create_task("r7-k3", "own", "goal")
        kernel.transition("r7-k3", "PLANNING")

        kernel.set_global_kill(True)
        # Resume edge blocked under kill.
        with pytest.raises(KillSwitchActive):
            kernel.transition("r7-k1", "QUEUED", actor="retry_policy")
        # Containment edges still allowed under kill: READY -> CANCELLED.
        cancelled = kernel.transition("r7-k2", "CANCELLED", actor="ops")
        assert cancelled["state"] == "CANCELLED"
        # FAILED via the official fail-closed path (autonomous kernel).
        failed = kernel.fail_task_fail_closed("r7-k3", reason="kill_switch_sweep")
        assert failed["state"] == "FAILED"
    finally:
        kernel.set_global_kill(False)
        kernel.close()


def test_r7_kill_active_blocks_auto_resolve_human_review(tmp_path: Path):
    """auto_resolve_human_review là HUMAN_REVIEW->READY (cạnh dispatch):
    dưới kill phải fail-closed (READY không thuộc SAFE_UNDER_KILL)."""
    kernel = _kernel(tmp_path, "r7-kill-ar.sqlite3", autonomous_mode=True)
    try:
        _setup_running(kernel, "r7-kar")
        kernel.transition("r7-kar", "HUMAN_REVIEW")
        kernel.set_global_kill(True)
        with pytest.raises(KillSwitchActive):
            kernel.auto_resolve_human_review("r7-kar")
        assert kernel.get_task("r7-kar")["state"] == "HUMAN_REVIEW"
    finally:
        kernel.set_global_kill(False)
        kernel.close()


def test_r7_kill_active_blocks_new_intake_planning_edge(tmp_path: Path):
    """PLANNING thuộc RESUME_TARGETS: dưới kill, CREATED->PLANNING bị chặn
    (không nhận việc mới); containment edge CREATED->CANCELLED vẫn hợp lệ."""
    kernel = _kernel(tmp_path, "r7-kill-plan.sqlite3")
    try:
        kernel.create_task("r7-kp", "own", "goal")
        kernel.set_global_kill(True)
        with pytest.raises(KillSwitchActive):
            kernel.transition("r7-kp", "PLANNING")
        # Containment edge from CREATED still legal under kill.
        cancelled = kernel.transition("r7-kp", "CANCELLED")
        assert cancelled["state"] == "CANCELLED"
    finally:
        kernel.set_global_kill(False)
        kernel.close()


# ---------------------------------------------------------------------------
# (5) Boot recovery: kill TẮT vẫn chạy đủ; kill BẬT contain thay vì sập
# ---------------------------------------------------------------------------

def test_r7_boot_recovery_clean_chain_kill_off_still_runs(tmp_path: Path):
    db = tmp_path / "r7-boot.sqlite3"
    k1 = TaskKernel(db)
    lease_a = _setup_running(k1, "r7-boot-a")
    k1.transition("r7-boot-a", "CHECKPOINTED", lease_id=lease_a.lease_id)
    _setup_running(k1, "r7-boot-b")  # RUNNING task, boot -> HUMAN_REVIEW
    k1.close()

    k2 = TaskKernel(db)
    try:
        report = k2.recover_on_boot()
        assert report["corrupted"] == []
        by_task = {entry["task_id"]: entry for entry in report["recovered"]}
        # Resume branch: CHECKPOINTED (pred RUNNING, not contained) -> QUEUED.
        assert by_task["r7-boot-a"]["to"] == "QUEUED"
        assert k2.get_task("r7-boot-a")["state"] == "QUEUED"
        # Safe branch: RUNNING -> HUMAN_REVIEW.
        assert by_task["r7-boot-b"]["to"] == "HUMAN_REVIEW"
        assert k2.get_task("r7-boot-b")["state"] == "HUMAN_REVIEW"
    finally:
        k2.close()


def test_r7_boot_recovery_under_kill_contains_checkpointed_task(tmp_path: Path):
    """Kill BẬT trong boot recovery: nhánh CHECKPOINTED->QUEUED bị chặn
    (KillSwitchActive), task giữ nguyên CHECKPOINTED (contained), vòng
    recovery KHÔNG sập — task khác vẫn được recover về trạng thái an toàn."""
    db = tmp_path / "r7-boot-kill.sqlite3"
    k1 = TaskKernel(db)
    lease_a = _setup_running(k1, "r7-bk-a")
    k1.transition("r7-bk-a", "CHECKPOINTED", lease_id=lease_a.lease_id)
    _setup_running(k1, "r7-bk-b")
    k1.set_global_kill(True)
    k1.close()

    k2 = TaskKernel(db)
    try:
        report = k2.recover_on_boot()
        assert report["corrupted"] == []
        recovered = {entry["task_id"]: entry for entry in report["recovered"]}
        assert "r7-bk-a" not in recovered, "resume edge must not run under kill"
        assert k2.get_task("r7-bk-a")["state"] == "CHECKPOINTED"
        left = {entry["task_id"]: entry for entry in report["left_as_is"]}
        assert left["r7-bk-a"]["reason"] == "global_kill_active"
        # Safe edge still recovered under kill: RUNNING -> HUMAN_REVIEW.
        assert recovered["r7-bk-b"]["to"] == "HUMAN_REVIEW"
    finally:
        k2.set_global_kill(False)
        k2.close()


# ---------------------------------------------------------------------------
# (3) Authority gates chính thức vẫn chạy đúng (production paths)
# ---------------------------------------------------------------------------

def test_r7_auto_resolve_gate_then_finalize_flow_requeue_path(tmp_path: Path):
    """Pin caller production ask_adapter_parts/finalize_flow.py:174-175:
    auto_resolve_human_review -> READY -> transition QUEUED (autonomous
    auto-advance) phải chạy đúng khi kill TẮT."""
    kernel = _kernel(tmp_path, "r7-finalize.sqlite3", autonomous_mode=True)
    try:
        _setup_running(kernel, "r7-fin")
        kernel.transition("r7-fin", "HUMAN_REVIEW")
        kernel.auto_resolve_human_review("r7-fin", reason="autonomous_verification_passed")
        kernel.transition("r7-fin", "QUEUED", actor="ask-kernel-adapter", reason="autonomous_auto_advance")
        new_lease = kernel.claim("r7-fin", "ask-route-worker")
        kernel.transition("r7-fin", "RUNNING", actor="ask-kernel-adapter", lease_id=new_lease.lease_id)
        kernel.transition("r7-fin", "VERIFYING", actor="ask-kernel-adapter", lease_id=new_lease.lease_id)
        assert kernel.get_task("r7-fin")["state"] == "VERIFYING"
    finally:
        kernel.close()


def test_r7_auto_resolve_refused_when_not_autonomous(tmp_path: Path):
    """Gate autonomous_mode của auto_resolve_human_review vẫn fail-closed
    trên kernel không-autonomous (giữ nguyên gate hiện có)."""
    kernel = _kernel(tmp_path, "r7-notauto.sqlite3", autonomous_mode=False)
    try:
        _setup_running(kernel, "r7-na")
        kernel.transition("r7-na", "HUMAN_REVIEW")
        with pytest.raises(InvalidTransition, match="autonomous_mode"):
            kernel.auto_resolve_human_review("r7-na")
        assert kernel.get_task("r7-na")["state"] == "HUMAN_REVIEW"
    finally:
        kernel.close()


def test_r7_reconcile_unknown_evidence_gate_still_routes_to_queued(tmp_path: Path):
    """Gate chính thức cho RECONCILING->QUEUED: reconcile_unknown với evidence
    (outcome NOT_APPLIED) — đường authority hợp lệ vẫn hoạt động."""
    kernel = _kernel(tmp_path, "r7-recon.sqlite3", autonomous_mode=True)
    try:
        lease = _setup_running(kernel, "r7-rc")
        idem_key, claimed = kernel.idempotency_claim("r7-rc", "step-1", "http.post", "https://api.test/x")
        assert claimed
        disp = kernel.record_action_dispatched(
            "r7-rc", lease.lease_id, "step-1", {"action": "x"}, 1, idem_key, "req-r7-1",
        )
        cp_id = disp["checkpoint_id"]
        # record_action_dispatched records the side-effect boundary and moves
        # the task to UNKNOWN (uncertain outcome) — same as the mission tests.
        assert kernel.get_task("r7-rc")["state"] == "UNKNOWN"
        kernel.enter_reconciling("r7-rc", cp_id)
        assert kernel.get_task("r7-rc")["state"] == "RECONCILING"
        result = kernel.reconcile_unknown(
            "r7-rc", cp_id, outcome="NOT_APPLIED",
            evidence_ref="evidence://r7/dry-run", verifier_id="verifier-r7",
        )
        assert result["state"] == "QUEUED"
    finally:
        kernel.close()


def test_r7_legit_flows_unaffected_kill_off(tmp_path: Path):
    """Chuỗi chạy việc hợp lệ (không controlled) không bị gate R7 làm chậm:
    intake, checkpoint-resume với lease, retry requeue từ RETRY_SCHEDULED."""
    kernel = _kernel(tmp_path, "r7-legit.sqlite3")
    try:
        kernel.create_task("r7-lg", "own", "goal")
        for state in ("PLANNING", "READY", "QUEUED"):
            kernel.transition("r7-lg", state)
        lease = kernel.claim("r7-lg", "w", ttl_seconds=300)
        kernel.start("r7-lg", lease.lease_id)
        kernel.transition("r7-lg", "CHECKPOINTED", lease_id=lease.lease_id)
        kernel.transition("r7-lg", "RUNNING", lease_id=lease.lease_id)
        kernel.commit_failed("r7-lg", lease.lease_id, "w", "RETRYABLE", "indict://r7")
        assert kernel.get_task("r7-lg")["state"] == "RETRY_SCHEDULED"
        kernel.transition("r7-lg", "QUEUED", actor="retry_policy", reason="retry_policy_background")
        assert kernel.get_task("r7-lg")["state"] == "QUEUED"
    finally:
        kernel.close()


def test_r7_watchdog_expire_leases_contains_checkpointed_under_kill(tmp_path: Path):
    """[R7-1] Watchdog expire_leases không được là lối vòng kill-switch:
    CHECKPOINTED + expired lease dưới global kill phải ĐỨNG YÊN CHECKPOINTED
    (chỉ xả lease), không requeue về QUEUED. Sau khi kill tắt, boot recovery
    requeue bình thường (pred của task là RUNNING — chuỗi sạch, không contained)."""
    kernel = _kernel(tmp_path, "r7-wd.sqlite3")
    try:
        lease = _setup_running(kernel, "r7-wd")
        kernel.transition("r7-wd", "CHECKPOINTED", lease_id=lease.lease_id)
        kernel.set_global_kill(True)
        expired = kernel.expire_leases(now=time.time() + 1000.0)
        assert lease.lease_id in expired, "lease must still be released under kill"
        task = kernel.get_task("r7-wd")
        assert task["state"] == "CHECKPOINTED", "resume edge must not run under kill"
        assert task["active_lease_id"] is None
    finally:
        kernel.set_global_kill(False)
        kernel.close()

    # Kill OFF: boot recovery requeues the contained-by-kill task legally.
    k2 = TaskKernel(tmp_path / "r7-wd.sqlite3")
    try:
        report = k2.recover_on_boot()
        assert report["corrupted"] == []
        assert k2.get_task("r7-wd")["state"] == "QUEUED"
    finally:
        k2.close()


# ---------------------------------------------------------------------------
# Gaps closed after the hand-over (probe A7/A8 + watchdog defence in depth)
# ---------------------------------------------------------------------------

def _setup_reconciling(kernel: TaskKernel, task_id: str):
    lease = _setup_running(kernel, task_id)
    idem_key, claimed = kernel.idempotency_claim(task_id, "step-1", "http.post", "https://api.test/x")
    assert claimed
    disp = kernel.record_action_dispatched(task_id, lease.lease_id, "step-1", {"action": "x"}, 1, idem_key, "req-r7")
    kernel.enter_reconciling(task_id, disp["checkpoint_id"])
    assert kernel.get_task(task_id)["state"] == "RECONCILING"
    return disp["checkpoint_id"], idem_key


def test_r7_reconcile_unknown_resume_outcome_blocked_under_kill(tmp_path: Path):
    """[R7-1] reconcile_unknown is an authority gate, not a kill exemption: NOT_APPLIED
    (-> QUEUED) fails closed under global kill and rolls back the idempotency update;
    an outcome that only contains the task (UNKNOWN -> HUMAN_REVIEW) stays allowed."""
    kernel = _kernel(tmp_path, "r7-recon-kill.sqlite3", autonomous_mode=True)
    try:
        cp_id, idem_key = _setup_reconciling(kernel, "r7-rk")
        kernel.set_global_kill(True)
        with pytest.raises(KillSwitchActive):
            kernel.reconcile_unknown(
                "r7-rk", cp_id, outcome="NOT_APPLIED",
                evidence_ref="evidence://r7/dry-run", verifier_id="verifier-r7",
            )
        assert kernel.get_task("r7-rk")["state"] == "RECONCILING"
        idem = kernel.conn.execute("SELECT status FROM idempotency WHERE logical_key=?", (idem_key,)).fetchone()
        assert idem["status"] == "CLAIMED", "idempotency update must roll back with the refused resume"
        contained = kernel.reconcile_unknown(
            "r7-rk", cp_id, outcome="UNKNOWN",
            evidence_ref="evidence://r7/unknown", verifier_id="verifier-r7",
        )
        assert contained["state"] == "HUMAN_REVIEW"
    finally:
        kernel.set_global_kill(False)
        kernel.close()


def test_r7_boot_recovery_finishes_and_leaves_contained_checkpointed_task(tmp_path: Path):
    """[R7-2] A CHECKPOINTED task that entered from a controlled state is left contained by
    boot recovery (reported), and the recovery pass still processes every other task."""
    kernel = _kernel(tmp_path, "r7-boot-contained.sqlite3")
    try:
        _setup_running(kernel, "r7-contained")
        kernel.transition("r7-contained", "RECOVERING")
        kernel.transition("r7-contained", "RECONCILING")
        kernel.transition("r7-contained", "CHECKPOINTED")
        _setup_running(kernel, "r7-inflight", owner="r7-owner-2")
        report = kernel.recover_on_boot()
        left = {entry["task_id"]: entry for entry in report["left_as_is"]}
        assert left["r7-contained"]["reason"] == "contained_requires_authority"
        assert kernel.get_task("r7-contained")["state"] == "CHECKPOINTED"
        recovered = {entry["task_id"]: entry for entry in report["recovered"]}
        assert recovered["r7-inflight"]["to"] == "HUMAN_REVIEW"
        assert report["corrupted"] == []
    finally:
        kernel.close()


def test_r7_watchdog_does_not_requeue_contained_checkpointed_task(tmp_path: Path):
    """[R7-2] Defence in depth: even if a contained CHECKPOINTED task somehow carries an
    expired active lease (forged at DB level here), the watchdog must not requeue it."""
    import sqlite3

    db = tmp_path / "r7-wd-contained.sqlite3"
    kernel = _kernel(tmp_path, "r7-wd-contained.sqlite3")
    try:
        lease = _setup_running(kernel, "r7-wdc")
        kernel.transition("r7-wdc", "RECOVERING")
        kernel.transition("r7-wdc", "RECONCILING")
        kernel.transition("r7-wdc", "CHECKPOINTED")
        side = sqlite3.connect(db)
        try:
            side.execute("UPDATE leases SET released=0 WHERE lease_id=?", (lease.lease_id,))
            side.execute("UPDATE tasks SET active_lease_id=? WHERE task_id='r7-wdc'", (lease.lease_id,))
            side.commit()
        finally:
            side.close()
        expired = kernel.expire_leases(now=time.time() + 1000.0)
        assert lease.lease_id in expired
        task = kernel.get_task("r7-wdc")
        assert task["state"] == "CHECKPOINTED"
        assert task["active_lease_id"] is None
        last = kernel.get_events("r7-wdc")[-1]
        assert last["reason"] == "heartbeat_expired_contained_checkpoint"
    finally:
        kernel.close()
