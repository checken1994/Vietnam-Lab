"""Unit and contract tests for CommitApprovalEngine (ARC-01 GA B44).

Verifies:
1. DI injection: TaskKernel(approval_engine=...) and property access.
2. Fail-closed unbound assertion: unbound engine raises RuntimeError.
3. Class alias: ApprovalManager is CommitApprovalEngine.
4. Direct commit_approval with capability token (compact mint_token) and operator signature.
5. Direct commit_completed with verifier receipt.
6. Direct commit_failed with retryable failure routing vs terminal failure routing.
7. Autonomous human review resolution and fail-closed termination.
8. Lease renewal, expiration sweep, cooperative release.
9. Task cancellation and global kill switch epoch toggle.
"""
from __future__ import annotations

import hashlib
import hmac
import time
from pathlib import Path

import pytest

from scp.core.capability_token import (
    InvalidTokenSignatureError,
    compute_token_signature,
    get_capability_secret,
    mint_token,
)
from scp.core.verifier_receipt import (
    InvalidReceiptSignatureError,
    VerifierReceipt,
    sign_verifier_receipt,
)
from scp.security.approval_issuer import issue_approval_token
from scp.security.capability_epoch import CapabilityToken
from scp.task_kernel import (
    ApprovalManager,
    CommitApprovalEngine,
    InvalidTransition,
    KernelError,
    NotFound,
    OptimisticLockError,
    StaleLease,
    TaskKernel,
)


def test_commit_approval_engine_unbound_raises() -> None:
    """An unbound CommitApprovalEngine raises RuntimeError fail-closed."""
    engine = CommitApprovalEngine(None)
    with pytest.raises(RuntimeError, match="has no bound TaskKernel instance"):
        _ = engine.conn

    with pytest.raises(RuntimeError, match="has no bound TaskKernel instance"):
        engine.commit_completed("task-1", "lease-1")

    with pytest.raises(RuntimeError, match="has no bound TaskKernel instance"):
        engine.commit_failed("task-1", "lease-1", "worker", "FATAL", "indictment://test")

    with pytest.raises(RuntimeError, match="has no bound TaskKernel instance"):
        engine.commit_approval("task-1", "token")

    with pytest.raises(RuntimeError, match="has no bound TaskKernel instance"):
        engine.renew_lease("task-1", "lease-1", 1)

    with pytest.raises(RuntimeError, match="has no bound TaskKernel instance"):
        engine.expire_leases()

    with pytest.raises(RuntimeError, match="has no bound TaskKernel instance"):
        engine.release("task-1", "lease-1")


def test_commit_approval_engine_di_injection_and_alias(tmp_path: Path) -> None:
    """CommitApprovalEngine can be injected via DI constructor or auto-initialized."""
    assert ApprovalManager is CommitApprovalEngine

    # Auto-initialization
    k1 = TaskKernel(tmp_path / "k1.sqlite3")
    try:
        assert isinstance(k1.approval_engine, CommitApprovalEngine)
        assert k1.approval_engine.kernel is k1
    finally:
        k1.close()

    # Injected engine
    custom_engine = CommitApprovalEngine()
    k2 = TaskKernel(tmp_path / "k2.sqlite3", approval_engine=custom_engine)
    try:
        assert k2.approval_engine is custom_engine
        assert custom_engine.kernel is k2
    finally:
        k2.close()


def _r5_sign_raw(payload: dict) -> str:
    """[R5-B] apv1 token over an arbitrary payload, signed with the conftest TEST issuer key."""
    import base64
    import json
    import os

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(os.environ["SCP_APPROVAL_ISSUER_PRIVATE_KEY"]))
    signature = key.sign(b"scp.approval.v1\x00" + body)

    def enc(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    return f"apv1.{enc(body)}.{enc(signature)}"


def test_commit_approval_direct_and_operator_sig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test approval commit via CommitApprovalEngine directly.

    [R5-B] Approval needs an issuer-signed token; a compact HMAC mint token or an operator HMAC
    signature (both producible by anyone holding SCP_CAPABILITY_SECRET) is rejected.
    """
    secret_str = "test-secret-key-32-bytes-long!!!"
    monkeypatch.setenv("SCP_CAPABILITY_SECRET", secret_str)

    kernel = TaskKernel(tmp_path / "appr.sqlite3")
    try:
        task_id = "task-appr-test"
        kernel.create_task(task_id, "test_owner", "test_goal")
        kernel.transition(task_id, "PLANNING")
        kernel.transition(task_id, "WAITING_APPROVAL")

        # 1. Compact HMAC mint token is no longer an approval authority
        legacy = mint_token(issuer="operator", scope=f"approval:grant:{task_id}", capability_level=2, ttl_seconds=300)
        with pytest.raises(InvalidTokenSignatureError):
            kernel.approval_engine.commit_approval(task_id, approval_token=legacy, actor="board_approver")

        token = issue_approval_token(task_id, issuer="board_approver")
        res = kernel.approval_engine.commit_approval(task_id, approval_token=token, actor="board_approver")
        assert res["state"] == "READY"

        events = kernel.get_events(task_id)
        approved_events = [e for e in events if e["type"] == "TASK_APPROVED"]
        assert len(approved_events) == 1
        assert approved_events[0]["actor"] == "board_approver"

        # 2. Operator approval: the HMAC signature dict is rejected; the issuer signs for the operator
        task_id_op = "task-appr-op"
        kernel.create_task(task_id_op, "test_owner_op", "test_goal_op")
        kernel.transition(task_id_op, "PLANNING")
        kernel.transition(task_id_op, "WAITING_APPROVAL")

        op_actor = "sysadmin_lead"
        now_ts = time.time()
        canonical_op = f"operator_approval:{task_id_op}:{op_actor}:{now_ts:.6f}".encode()
        op_sig = hmac.new(secret_str.encode("utf-8"), canonical_op, hashlib.sha256).hexdigest()
        op_token = {
            "actor": op_actor,
            "timestamp": now_ts,
            "signature": op_sig,
            "task_id": task_id_op,
        }
        with pytest.raises(InvalidTokenSignatureError):
            kernel.approval_engine.commit_approval(task_id_op, approval_token=op_token, actor=op_actor)
        assert kernel.get_task(task_id_op)["state"] == "WAITING_APPROVAL"

        res_op = kernel.approval_engine.commit_approval(
            task_id_op, approval_token=issue_approval_token(task_id_op, issuer=op_actor), actor=op_actor
        )
        assert res_op["state"] == "READY"
    finally:
        kernel.close()


def test_commit_completed_direct_with_receipt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test commit_completed via CommitApprovalEngine directly with signed receipt."""
    secret_str = "verifier-secret-key-32-bytes!!!"
    monkeypatch.setenv("SCP_VERIFIER_SECRET", secret_str)

    kernel = TaskKernel(tmp_path / "comp.sqlite3")
    try:
        task_id = "task-comp-test"
        kernel.create_task(task_id, "owner_comp", "goal_comp")
        for s in ("PLANNING", "READY", "QUEUED"):
            kernel.transition(task_id, s)
        lease = kernel.claim(task_id, "worker_1", ttl_seconds=30)
        kernel.start(task_id, lease.lease_id)
        kernel.transition(task_id, "VERIFYING")

        unsigned = VerifierReceipt(
            task_id=task_id,
            verifier_id="test_verifier",
            verdict="VERIFIED",
            evidence_ref="test://evidence/proof-1",
            issued_at=time.time(),
        )
        receipt = sign_verifier_receipt(unsigned, secret=secret_str)

        # Call through approval_engine
        res = kernel.approval_engine.commit_completed(
            task_id,
            lease.lease_id,
            receipt=receipt,
        )
        assert res["state"] == "COMPLETED"
        assert res["active_lease_id"] is None
    finally:
        kernel.close()


def test_commit_failed_routing(tmp_path: Path) -> None:
    """Test retryable failure vs terminal failure routing via CommitApprovalEngine."""
    kernel = TaskKernel(tmp_path / "fail.sqlite3")
    try:
        task_id = "task-fail-test"
        kernel.create_task(task_id, "owner_fail", "goal_fail", max_attempts=2)
        for s in ("PLANNING", "READY", "QUEUED"):
            kernel.transition(task_id, s)
        lease1 = kernel.claim(task_id, "worker_fail", ttl_seconds=30)
        kernel.start(task_id, lease1.lease_id)

        # First failure: retryable -> RETRY_SCHEDULED
        res1 = kernel.approval_engine.commit_failed(
            task_id,
            lease1.lease_id,
            actor="worker_fail",
            failure_classification="TRANSIENT",
            indictment_ref="log://timeout-error",
        )
        assert res1["state"] == "RETRY_SCHEDULED"
        assert res1["attempts"] == 1

        # Second attempt: re-queue (RETRY_SCHEDULED -> QUEUED) and fail terminally
        kernel.transition(task_id, "QUEUED")
        lease2 = kernel.claim(task_id, "worker_fail", ttl_seconds=30)
        kernel.start(task_id, lease2.lease_id)

        res2 = kernel.approval_engine.commit_failed(
            task_id,
            lease2.lease_id,
            actor="worker_fail",
            failure_classification="TRANSIENT",
            indictment_ref="log://second-timeout",
        )
        assert res2["state"] == "FAILED"
        assert res2["attempts"] == 2
    finally:
        kernel.close()


def test_autonomous_mode_approval_engine_helpers(tmp_path: Path) -> None:
    """Test auto_resolve_human_review and fail_task_fail_closed in autonomous mode."""
    kernel = TaskKernel(tmp_path / "auto.sqlite3", autonomous_mode=True)
    try:
        task_id_1 = "task-auto-hr"
        kernel.create_task(task_id_1, "owner_auto", "goal_auto")
        for s in ("PLANNING", "READY", "QUEUED"):
            kernel.transition(task_id_1, s)
        lease1 = kernel.claim(task_id_1, "worker_auto", ttl_seconds=30)
        kernel.start(task_id_1, lease1.lease_id)
        kernel.transition(task_id_1, "HUMAN_REVIEW")

        # Resolve via approval_engine
        res1 = kernel.approval_engine.auto_resolve_human_review(task_id_1, reason="autonomous_check_passed")
        assert res1["state"] == "READY"

        # Fail-closed via approval_engine from HUMAN_REVIEW on task 2
        task_id_2 = "task-auto-fail"
        kernel.create_task(task_id_2, "owner_auto", "goal_auto_2")
        for s in ("PLANNING", "READY", "QUEUED"):
            kernel.transition(task_id_2, s)
        lease2 = kernel.claim(task_id_2, "worker_auto", ttl_seconds=30)
        kernel.start(task_id_2, lease2.lease_id)
        kernel.transition(task_id_2, "HUMAN_REVIEW")

        res_failed = kernel.approval_engine.fail_task_fail_closed(task_id_2, reason="autonomous_violation")
        assert res_failed["state"] == "FAILED"
    finally:
        kernel.close()


def test_lease_lifecycle_transitions(tmp_path: Path) -> None:
    """Test renew_lease, expire_leases, release, set_task_kill, and set_global_kill on CommitApprovalEngine."""
    kernel = TaskKernel(tmp_path / "lease.sqlite3")
    try:
        task_id = "task-lease-test"
        kernel.create_task(task_id, "owner_l", "goal_l")
        for s in ("PLANNING", "READY", "QUEUED"):
            kernel.transition(task_id, s)
        lease = kernel.claim(task_id, "worker_l", ttl_seconds=5)

        # Renew lease via approval_engine
        renewed = kernel.approval_engine.renew_lease(task_id, lease.lease_id, lease.fencing_token, ttl_seconds=10)
        assert renewed is True

        # Cooperative release via approval_engine
        kernel.approval_engine.release(task_id, lease.lease_id)
        task_after_release = kernel.get_task(task_id)
        assert task_after_release["active_lease_id"] is None

        # Expiration sweep on task_2
        task_id_exp = "task-lease-expire"
        kernel.create_task(task_id_exp, "owner_exp", "goal_exp")
        for s in ("PLANNING", "READY", "QUEUED"):
            kernel.transition(task_id_exp, s)
        lease2 = kernel.claim(task_id_exp, "worker_l", ttl_seconds=1)
        kernel.start(task_id_exp, lease2.lease_id)

        expired_ids = kernel.approval_engine.expire_leases(now=time.time() + 10)
        assert lease2.lease_id in expired_ids
        task_after_expire = kernel.get_task(task_id_exp)
        assert task_after_expire["state"] == "RECOVERING"

        # Kill task via approval_engine
        killed_task = kernel.approval_engine.set_task_kill(task_id_exp, actor="operator")
        assert killed_task["state"] in {"CANCELLED", "FAILED"}

        # Global kill epoch via approval_engine
        epoch = kernel.approval_engine.set_global_kill(True, actor="operator")
        assert epoch >= 1
    finally:
        kernel.close()


def test_commit_approval_error_branches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test error and invariant branches for commit_approval."""
    secret_str = "test-secret-key-32-bytes-long!!!"
    monkeypatch.setenv("SCP_CAPABILITY_SECRET", secret_str)

    kernel = TaskKernel(tmp_path / "appr_err.sqlite3")
    try:
        task_id = "task-appr-err"
        kernel.create_task(task_id, "owner_err", "goal_err")

        # 1. Missing / empty token
        with pytest.raises(InvalidTokenSignatureError, match="approval_token is required"):
            kernel.approval_engine.commit_approval(task_id, approval_token="")

        with pytest.raises(InvalidTokenSignatureError, match="approval_token is required"):
            kernel.approval_engine.commit_approval(task_id, approval_token=None)

        # 2. Empty actor
        token = issue_approval_token(task_id, issuer="operator")
        with pytest.raises(KernelError, match="actor is required"):
            kernel.approval_engine.commit_approval(task_id, approval_token=token, actor="   ")

        # 3. Task not in WAITING_APPROVAL (currently CREATED)
        with pytest.raises(InvalidTransition, match="cannot be approved; task must be in WAITING_APPROVAL"):
            kernel.approval_engine.commit_approval(task_id, approval_token=token, actor="operator")

        # 4. Transition to WAITING_APPROVAL and test optimistic lock mismatch
        kernel.transition(task_id, "PLANNING")
        kernel.transition(task_id, "WAITING_APPROVAL")
        task_cur = kernel.get_task(task_id)

        with pytest.raises(OptimisticLockError, match="concurrency conflict approving task"):
            kernel.approval_engine.commit_approval(
                task_id,
                approval_token=token,
                actor="operator",
                expected_version=task_cur["version"] + 999,
            )

        # 5. Invalid token scope (validly issuer-signed, wrong scope)
        now_ts = time.time()
        wrong_scope_token = _r5_sign_raw({
            "v": 1, "typ": "scp.approval", "task_id": task_id, "scope": "other:unauthorized",
            "iss": "operator", "iat": now_ts, "exp": now_ts + 300, "jti": "wrong-scope",
        })
        with pytest.raises(InvalidTransition, match="does not authorize 'approval:grant'"):
            kernel.approval_engine.commit_approval(task_id, approval_token=wrong_scope_token, actor="operator")

        # 6. [R5-B] CapabilityToken epoch HMAC branch is no longer an approval authority
        cap_issued = time.time()
        cap_sig = compute_token_signature(
            secret_str.encode("utf-8"),
            f"approval:grant:{task_id}",
            0,
            "tok-epoch-1",
            cap_issued,
        )
        cap_tok = CapabilityToken(
            subject=f"approval:grant:{task_id}",
            epoch=0,
            token_id="tok-epoch-1",
            issued_at=cap_issued,
            signature=cap_sig,
        )
        with pytest.raises(InvalidTokenSignatureError):
            kernel.approval_engine.commit_approval(task_id, approval_token=cap_tok, actor="epoch_authority")
        res_cap = kernel.approval_engine.commit_approval(task_id, approval_token=token, actor="epoch_authority")
        assert res_cap["state"] == "READY"

        # 7. Terminal task cannot be approved
        kernel.transition(task_id, "CANCELLED")
        with pytest.raises(InvalidTransition, match="terminal task is immutable"):
            kernel.approval_engine.commit_approval(task_id, approval_token=token, actor="operator")
    finally:
        kernel.close()


def test_commit_completed_error_branches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test error branches and contract invariants for commit_completed."""
    secret_str = "verifier-secret-key-32-bytes!!!"
    monkeypatch.setenv("SCP_VERIFIER_SECRET", secret_str)

    kernel = TaskKernel(tmp_path / "comp_err.sqlite3")
    try:
        task_id = "task-comp-err"
        kernel.create_task(task_id, "owner_comp", "goal_comp")
        for s in ("PLANNING", "READY", "QUEUED"):
            kernel.transition(task_id, s)
        lease = kernel.claim(task_id, "worker_1", ttl_seconds=30)

        # 1. Missing receipt and verdict without evidence raises KernelError
        with pytest.raises(KernelError, match="completion requires independent VERIFIED verdict and evidence"):
            kernel.approval_engine.commit_completed(task_id, lease.lease_id, receipt=None)

        # 2. Empty lease_id or mismatched task_id
        unsigned = VerifierReceipt(
            task_id=task_id,
            verifier_id="test_verifier",
            verdict="VERIFIED",
            evidence_ref="test://evidence/proof-1",
            issued_at=time.time(),
        )
        receipt = sign_verifier_receipt(unsigned, secret=secret_str)

        with pytest.raises(InvalidReceiptSignatureError, match="does not match expected task_id"):
            kernel.approval_engine.commit_completed("other_task", lease.lease_id, receipt=receipt)

        with pytest.raises((KernelError, StaleLease)):
            kernel.approval_engine.commit_completed(task_id, "   ", receipt=receipt)

        # 3. Task in LEASED, not in VERIFYING
        with pytest.raises(InvalidTransition, match="LEASED->COMPLETED"):
            kernel.approval_engine.commit_completed(task_id, lease.lease_id, receipt=receipt)

        # 4. Transition to VERIFYING
        kernel.start(task_id, lease.lease_id)
        kernel.transition(task_id, "VERIFYING")

        # 5. Wrong / stale lease_id
        with pytest.raises(StaleLease):
            kernel.approval_engine.commit_completed(task_id, "wrong_lease_id", receipt=receipt)

        # 6. Verdict not VERIFIED
        bad_unsigned = VerifierReceipt(
            task_id=task_id,
            verifier_id="test_verifier",
            verdict="REJECTED",
            evidence_ref="test://evidence/fail",
            issued_at=time.time(),
        )
        bad_receipt = sign_verifier_receipt(bad_unsigned, secret=secret_str)
        with pytest.raises(InvalidReceiptSignatureError, match="Completion requires verifier verdict 'VERIFIED'"):
            kernel.approval_engine.commit_completed(task_id, lease.lease_id, receipt=bad_receipt)
    finally:
        kernel.close()


def test_commit_failed_error_branches(tmp_path: Path) -> None:
    """Test error branches and contract invariants for commit_failed."""
    kernel = TaskKernel(tmp_path / "fail_err.sqlite3")
    try:
        task_id = "task-fail-err"
        kernel.create_task(task_id, "owner_fail", "goal_fail")

        # 1. Missing fields
        with pytest.raises(KernelError, match="task_id is required"):
            kernel.approval_engine.commit_failed("", "lease-1", "worker", "FATAL", "indictment://1")

        with pytest.raises(KernelError, match="actor is required"):
            kernel.approval_engine.commit_failed(task_id, "lease-1", "", "FATAL", "indictment://1")

        with pytest.raises(KernelError, match="failure_classification is required"):
            kernel.approval_engine.commit_failed(task_id, "lease-1", "worker", "", "indictment://1")

        with pytest.raises(KernelError, match="indictment_ref is required"):
            kernel.approval_engine.commit_failed(task_id, "lease-1", "worker", "FATAL", "")

        # 2. Non-existent lease raises StaleLease
        with pytest.raises(StaleLease):
            kernel.approval_engine.commit_failed(task_id, "lease-1", "worker", "FATAL", "indictment://1")

        # 3. Transition to RUNNING, test mismatched lease
        for s in ("PLANNING", "READY", "QUEUED"):
            kernel.transition(task_id, s)
        lease = kernel.claim(task_id, "worker_f", ttl_seconds=30)
        kernel.start(task_id, lease.lease_id)

        with pytest.raises(StaleLease):
            kernel.approval_engine.commit_failed(task_id, "mismatched_lease", "worker_f", "FATAL", "indictment://1")
    finally:
        kernel.close()


def test_autonomous_mode_error_branches(tmp_path: Path) -> None:
    """Test error branches for autonomous mode methods."""
    # 1. Non-autonomous kernel raises InvalidTransition on auto_resolve_human_review
    kernel_std = TaskKernel(tmp_path / "auto_err.sqlite3", autonomous_mode=False)
    try:
        with pytest.raises(InvalidTransition, match="can only be called when autonomous_mode is enabled"):
            kernel_std.approval_engine.auto_resolve_human_review("any-task")
    finally:
        kernel_std.close()

    # 2. Autonomous kernel with task not in HUMAN_REVIEW
    kernel_auto = TaskKernel(tmp_path / "auto_err_2.sqlite3", autonomous_mode=True)
    try:
        t_id = "task-not-hr"
        kernel_auto.create_task(t_id, "owner_a", "goal_a")
        with pytest.raises(InvalidTransition, match="expected HUMAN_REVIEW"):
            kernel_auto.approval_engine.auto_resolve_human_review(t_id)

        # 3. fail_task_fail_closed on terminal task returns task unchanged
        kernel_auto.transition(t_id, "CANCELLED")
        task_term = kernel_auto.approval_engine.fail_task_fail_closed(t_id)
        assert task_term["state"] == "CANCELLED"
    finally:
        kernel_auto.close()


def test_lease_management_error_branches(tmp_path: Path) -> None:
    """Test edge cases and error handling in lease renewal and release."""
    kernel = TaskKernel(tmp_path / "lease_err.sqlite3")
    try:
        task_id = "task-lease-err"
        kernel.create_task(task_id, "owner_l", "goal_l")

        # 1. Empty arguments return False
        assert kernel.approval_engine.renew_lease("", "lease-1", 1) is False
        assert kernel.approval_engine.renew_lease(task_id, "", 1) is False

        # 2. Non-existent lease returns False
        assert kernel.approval_engine.renew_lease(task_id, "non_existent_lease", 1) is False

        # 3. Cooperative release with empty task_id raises StaleLease or KernelError
        with pytest.raises((KernelError, StaleLease)):
            kernel.approval_engine.release("", "lease-1")

        with pytest.raises((KernelError, StaleLease)):
            kernel.approval_engine.release(task_id, "")
    finally:
        kernel.close()


def test_task_cancel_and_state_machine_methods(tmp_path: Path) -> None:
    """Test task cancellation and StateMachineEngine properties and helpers."""
    kernel = TaskKernel(tmp_path / "sm_test.sqlite3")
    try:
        # 1. StateMachineEngine properties
        sm = kernel.state_machine
        assert "CREATED" in sm.states
        assert "COMPLETED" in sm.terminal_states
        assert sm.allowed_transitions is not None
        assert sm.get_valid_next_states("CREATED") == {"PLANNING", "CANCELLED"}
        assert sm._can_transition("CREATED", "PLANNING") is True
        assert sm._can_transition("WAITING_APPROVAL", "READY") is False

        # 2. legal_or_nearest reroutes
        target, note = sm.legal_or_nearest("VERIFYING", "CANCELLED", "test_note")
        assert target == "FAILED"
        assert "kill_reroute" in note

        target2, note2 = sm.legal_or_nearest("LEASED", "UNKNOWN", "test_note2")
        assert target2 == "RECOVERING"
        assert "transition_reroute" in note2

        target3, note3 = sm.legal_or_nearest("UNKNOWN", "RETRY_SCHEDULED", "test_note3")
        assert target3 == "FAILED"
        assert "planned_retry" in note3

        # 3. Cancellation on different states
        t1 = "task-can-1"
        kernel.create_task(t1, "owner_c", "goal_c")
        c1 = kernel.cancel(t1, actor="user_stop")
        assert c1["state"] == "CANCELLED"

        # Cancel on already cancelled task raises InvalidTransition fail-closed
        with pytest.raises(InvalidTransition, match="terminal task is immutable"):
            kernel.cancel(t1, actor="user_stop_again")

        # Cancel non-existent task
        with pytest.raises((KernelError, NotFound)):
            kernel.cancel("non_existent_task")
    finally:
        kernel.close()
