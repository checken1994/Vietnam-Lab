"""Contract and integrity tests for AutonomousCheckerGate.

Trao quyền cho Checker Subsystem & Cưỡng chế cổng T00 Meta-Audit + VerifierReceipt
(ARC-01 GA B44 / DNA #1-#29 / FA-01-FA-13).

Verifies:
1. Positive verification: Valid patch on task in WAITING_APPROVAL passes checks and
   receives signed VerifierReceipt; [R5-B] the gate only RECOMMENDS approval (no self-minted
   token) and an issuer-signed token moves the task to READY.
2. Negative anti-placebo: Syntax error in patch -> REJECTED fail-closed, no token.
3. Negative anti-placebo: Dangerous patterns (eval, os.system, shutil.rmtree) -> REJECTED.
4. Negative anti-placebo: T00 audit violations (empty test body, pytest.skip, swallowed exception) -> REJECTED.
5. Blast radius violation: Exceeding max modified lines -> REJECTED.
6. Concurrency / OCC protection: Attempting approval on stale task version raises OptimisticLockError.
7. Integration with commit_verification_result: [KEYC-L5 · B5] the gate's pre-execution
   attestation is refused at VERIFYING; a receipt signed after execution completes the task.
8. Unified diff support: Unified diff format is parsed and applied cleanly in-memory.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import time

import pytest

from scp.core.verifier_receipt import (
    InvalidReceiptSignatureError,
    VerifierReceipt,
    sign_verifier_receipt,
    verify_verifier_receipt,
)
from scp.security.approval_issuer import issue_approval_token
from scp.task_kernel_parts.autonomous_checker_gate import verify_checker_recommendation
from scp.task_kernel import (
    AutonomousCheckerGate,
    CheckerEvaluationResult,
    CheckerRejectionError,
    OptimisticLockError,
    TaskKernel,
)

FIXTURE_SECRET = "test-checker-gate-secret-32-bytes-long!!!"


@pytest.fixture(autouse=True)
def setup_test_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure cryptographic secrets are configured for token and receipt operations."""
    monkeypatch.setenv("SCP_CAPABILITY_SECRET", FIXTURE_SECRET)
    monkeypatch.setenv("SCP_VERIFIER_SECRET", FIXTURE_SECRET)


def test_autonomous_checker_positive_verification(tmp_path: Path) -> None:
    """Test 1: Valid patch passes all checks and signs a VerifierReceipt; the gate recommends,
    the issuer approves (R5-B), and the task moves to READY with the receipt journaled."""
    target_file = tmp_path / "service.py"
    target_file.write_text(
        "def compute_status():\n"
        "    code = 100\n"
        "    return code\n",
        encoding="utf-8",
    )

    db_path = tmp_path / "kernel_pos.sqlite3"
    kernel = TaskKernel(db_path)
    try:
        task_id = "task-checker-pos-1"
        kernel.create_task(task_id, owner="checker_test", goal="approve safe patch")
        kernel.transition(task_id, "PLANNING")
        kernel.transition(task_id, "WAITING_APPROVAL")

        patch = (
            "<<<<<<< SEARCH\n"
            "    code = 100\n"
            "    return code\n"
            "=======\n"
            "    code = 200\n"
            "    return code\n"
            ">>>>>>> REPLACE\n"
        )

        result: CheckerEvaluationResult = kernel.autonomous_review_and_approve(
            task_id=task_id,
            candidate_patch=patch,
            target_file=str(target_file),
            actor="autonomous_checker",
            repo_root=tmp_path,
        )

        assert result.approved is False
        assert result.verdict == "RECOMMEND_APPROVE"
        assert result.task is not None
        assert result.task["state"] == "WAITING_APPROVAL"
        assert result.target_file == "service.py"

        # Check the recommendation attestation [KEYC-L5 · B5]: signed and task-bound, but the gate
        # runs before execution, so it is never a completion receipt.
        assert result.receipt is not None
        assert isinstance(result.receipt, VerifierReceipt)
        assert result.receipt.task_id == task_id
        assert result.receipt.verifier_id == "autonomous_checker"
        assert result.receipt.verdict == "RECOMMEND_APPROVE"
        assert len(result.receipt.signature) == 64
        assert verify_checker_recommendation(result.receipt, task_id=task_id) is True
        with pytest.raises(InvalidReceiptSignatureError):
            verify_verifier_receipt(result.receipt, task_id=task_id)

        # [R5-B] The gate mints nothing; the issuer signs the approval it recommended.
        assert result.token is None
        kernel.commit_approval(
            task_id,
            issue_approval_token(task_id, issuer="release_manager"),
            actor="release_manager",
            details={"verifier_receipt": result.receipt.to_dict(), "checks": result.checks},
        )

        # Check physical persistence in SQLite (version: 1 created, 2 planning, 3 WA, 4 ready)
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute("SELECT state, version FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            assert row is not None
            assert row["state"] == "READY"
            assert row["version"] == 4
        finally:
            conn.close()

        # Check append-only event journal
        events = kernel.get_events(task_id)
        appr_events = [e for e in events if e["type"] == "TASK_APPROVED"]
        assert len(appr_events) == 1
        assert appr_events[0]["actor"] == "release_manager"
        payload = json.loads(appr_events[0]["payload_json"])
        assert "verifier_receipt" in payload["details"]
        assert payload["details"]["verifier_receipt"]["signature"] == result.receipt.signature
    finally:
        kernel.close()


def test_autonomous_checker_syntax_error_rejected(tmp_path: Path) -> None:
    """Test 2: Syntax error in patch -> REJECTED fail-closed, no token, state remains WAITING_APPROVAL."""
    target_file = tmp_path / "math_mod.py"
    target_file.write_text(
        "def add(a, b):\n"
        "    return a + b\n",
        encoding="utf-8",
    )

    kernel = TaskKernel(tmp_path / "kernel_syn.sqlite3")
    try:
        task_id = "task-checker-syn-1"
        kernel.create_task(task_id, owner="checker_test", goal="reject syntax error")
        kernel.transition(task_id, "PLANNING")
        kernel.transition(task_id, "WAITING_APPROVAL")

        broken_patch = (
            "<<<<<<< SEARCH\n"
            "def add(a, b):\n"
            "    return a + b\n"
            "=======\n"
            "def add(a, b:\n"
            "    return a + b\n"
            ">>>>>>> REPLACE\n"
        )

        with pytest.raises(CheckerRejectionError, match="Syntax error in patched file"):
            kernel.autonomous_review_and_approve(
                task_id=task_id,
                candidate_patch=broken_patch,
                target_file=str(target_file),
                repo_root=tmp_path,
            )

        # State must remain WAITING_APPROVAL fail-closed
        task = kernel.get_task(task_id)
        assert task["state"] == "WAITING_APPROVAL"

        # No approval events in journal
        events = kernel.get_events(task_id)
        assert not any(e["type"] == "TASK_APPROVED" for e in events)
    finally:
        kernel.close()


def test_autonomous_checker_dangerous_patterns_rejected(tmp_path: Path) -> None:
    """Test 3: Dangerous patterns (eval, os.system, shutil.rmtree) -> REJECTED fail-closed."""
    target_file = tmp_path / "handler.py"
    target_file.write_text(
        "def handle(val):\n"
        "    return val * 2\n",
        encoding="utf-8",
    )

    kernel = TaskKernel(tmp_path / "kernel_danger.sqlite3")
    try:
        task_id = "task-checker-danger-1"
        kernel.create_task(task_id, owner="checker_test", goal="reject dangerous patch")
        kernel.transition(task_id, "PLANNING")
        kernel.transition(task_id, "WAITING_APPROVAL")

        # 1. os.system injection attempt
        os_patch = (
            "<<<<<<< SEARCH\n"
            "def handle(val):\n"
            "    return val * 2\n"
            "=======\n"
            "def handle(val):\n"
            "    import os\n"
            "    os.system('id')\n"
            "    return val * 2\n"
            ">>>>>>> REPLACE\n"
        )
        with pytest.raises(CheckerRejectionError, match="Dangerous pattern detected.*os\\.system"):
            kernel.autonomous_review_and_approve(
                task_id=task_id,
                candidate_patch=os_patch,
                target_file=str(target_file),
                repo_root=tmp_path,
            )

        # 2. eval invocation attempt
        eval_patch = (
            "<<<<<<< SEARCH\n"
            "def handle(val):\n"
            "    return val * 2\n"
            "=======\n"
            "def handle(val):\n"
            "    return eval(str(val))\n"
            ">>>>>>> REPLACE\n"
        )
        with pytest.raises(CheckerRejectionError, match="Dangerous pattern detected.*eval"):
            kernel.autonomous_review_and_approve(
                task_id=task_id,
                candidate_patch=eval_patch,
                target_file=str(target_file),
                repo_root=tmp_path,
            )

        # 3. shutil.rmtree destructive attempt
        rmtree_patch = (
            "<<<<<<< SEARCH\n"
            "def handle(val):\n"
            "    return val * 2\n"
            "=======\n"
            "def handle(val):\n"
            "    import shutil\n"
            "    shutil.rmtree('/tmp')\n"
            "    return val * 2\n"
            ">>>>>>> REPLACE\n"
        )
        with pytest.raises(CheckerRejectionError, match="Dangerous pattern detected.*rmtree"):
            kernel.autonomous_review_and_approve(
                task_id=task_id,
                candidate_patch=rmtree_patch,
                target_file=str(target_file),
                repo_root=tmp_path,
            )

        # Task remains safely in WAITING_APPROVAL
        assert kernel.get_task(task_id)["state"] == "WAITING_APPROVAL"
    finally:
        kernel.close()


def test_autonomous_checker_t00_audit_violations_rejected(tmp_path: Path) -> None:
    """Test 4: T00 audit violations (empty test body, pytest.skip, swallowed exception) -> REJECTED."""
    test_dir = tmp_path / "tests"
    test_dir.mkdir(parents=True, exist_ok=True)
    target_test_file = test_dir / "test_example.py"
    target_test_file.write_text(
        "def test_original():\n"
        "    val = 42\n"
        "    assert val == 42\n",
        encoding="utf-8",
    )

    kernel = TaskKernel(tmp_path / "kernel_t00.sqlite3")
    try:
        task_id = "task-checker-t00-1"
        kernel.create_task(task_id, owner="checker_test", goal="reject t00 violation")
        kernel.transition(task_id, "PLANNING")
        kernel.transition(task_id, "WAITING_APPROVAL")

        # 1. Empty test body violation
        empty_body_patch = (
            "<<<<<<< SEARCH\n"
            "def test_original():\n"
            "    val = 42\n"
            "    assert val == 42\n"
            "=======\n"
            "def test_original():\n"
            "    pass\n"
            ">>>>>>> REPLACE\n"
        )
        with pytest.raises(CheckerRejectionError, match="T00 Test-Integrity rule violation.*empty test body"):
            kernel.autonomous_review_and_approve(
                task_id=task_id,
                candidate_patch=empty_body_patch,
                target_file=str(target_test_file),
                repo_root=tmp_path,
            )

        # 2. Pytest skip decorator violation
        skip_dec_patch = (
            "<<<<<<< SEARCH\n"
            "def test_original():\n"
            "    val = 42\n"
            "    assert val == 42\n"
            "=======\n"
            "import pytest\n\n"
            "@pytest.mark.skip(reason='bypass')\n"
            "def test_original():\n"
            "    val = 42\n"
            "    assert val == 42\n"
            ">>>>>>> REPLACE\n"
        )
        with pytest.raises(CheckerRejectionError, match="T00 Test-Integrity rule violation.*pytest decorator 'skip'"):
            kernel.autonomous_review_and_approve(
                task_id=task_id,
                candidate_patch=skip_dec_patch,
                target_file=str(target_test_file),
                repo_root=tmp_path,
            )

        # 3. Pytest skip call violation
        skip_call_patch = (
            "<<<<<<< SEARCH\n"
            "def test_original():\n"
            "    val = 42\n"
            "    assert val == 42\n"
            "=======\n"
            "import pytest\n\n"
            "def test_original():\n"
            "    pytest.skip('skip call')\n"
            "    assert 42 == 42\n"
            ">>>>>>> REPLACE\n"
        )
        with pytest.raises(CheckerRejectionError, match="T00 Test-Integrity rule violation.*pytest\\.skip\\(\\) call"):
            kernel.autonomous_review_and_approve(
                task_id=task_id,
                candidate_patch=skip_call_patch,
                target_file=str(target_test_file),
                repo_root=tmp_path,
            )

        # 4. Swallowed exception in test handler
        swallowed_patch = (
            "<<<<<<< SEARCH\n"
            "def test_original():\n"
            "    val = 42\n"
            "    assert val == 42\n"
            "=======\n"
            "def test_original():\n"
            "    try:\n"
            "        assert 42 == 42\n"
            "    except Exception:\n"
            "        pass\n"
            ">>>>>>> REPLACE\n"
        )
        with pytest.raises(CheckerRejectionError, match="T00 Test-Integrity rule violation.*swallowed exception"):
            kernel.autonomous_review_and_approve(
                task_id=task_id,
                candidate_patch=swallowed_patch,
                target_file=str(target_test_file),
                repo_root=tmp_path,
            )

        assert kernel.get_task(task_id)["state"] == "WAITING_APPROVAL"
    finally:
        kernel.close()


def test_autonomous_checker_blast_radius_rejected(tmp_path: Path) -> None:
    """Test 5: Blast radius limit exceeded -> REJECTED fail-closed."""
    target_file = tmp_path / "long_module.py"
    target_file.write_text(
        "def step_one():\n"
        "    return 1\n",
        encoding="utf-8",
    )

    kernel = TaskKernel(tmp_path / "kernel_blast.sqlite3")
    try:
        task_id = "task-checker-blast-1"
        kernel.create_task(task_id, owner="checker_test", goal="reject large patch")
        kernel.transition(task_id, "PLANNING")
        kernel.transition(task_id, "WAITING_APPROVAL")

        # Generates a patch with ~12 lines added
        new_lines = "\n".join(f"    x_{i} = {i}" for i in range(10))
        large_patch = (
            "<<<<<<< SEARCH\n"
            "def step_one():\n"
            "    return 1\n"
            "=======\n"
            "def step_one():\n"
            f"{new_lines}\n"
            "    return 1\n"
            ">>>>>>> REPLACE\n"
        )

        with pytest.raises(CheckerRejectionError, match="Blast radius limit exceeded"):
            kernel.autonomous_review_and_approve(
                task_id=task_id,
                candidate_patch=large_patch,
                target_file=str(target_file),
                max_blast_radius_lines=5,  # Exceeds max 5 lines
                repo_root=tmp_path,
            )

        assert kernel.get_task(task_id)["state"] == "WAITING_APPROVAL"
    finally:
        kernel.close()


def test_autonomous_checker_occ_concurrency_protection(tmp_path: Path) -> None:
    """Test 6: Concurrency conflict on stale task version raises OptimisticLockError."""
    target_file = tmp_path / "safe.py"
    target_file.write_text("def ping(): return 'pong'\n", encoding="utf-8")

    kernel = TaskKernel(tmp_path / "kernel_occ.sqlite3")
    try:
        task_id = "task-checker-occ-1"
        kernel.create_task(task_id, owner="checker_test", goal="occ conflict test")
        kernel.transition(task_id, "PLANNING")
        task = kernel.transition(task_id, "WAITING_APPROVAL")

        current_ver = task["version"]

        patch = (
            "<<<<<<< SEARCH\n"
            "def ping(): return 'pong'\n"
            "=======\n"
            "def ping(): return 'pong_v2'\n"
            ">>>>>>> REPLACE\n"
        )

        # Stale expected_version conflict
        with pytest.raises(OptimisticLockError, match="concurrency conflict approving task"):
            kernel.autonomous_review_and_approve(
                task_id=task_id,
                candidate_patch=patch,
                target_file=str(target_file),
                expected_version=current_ver + 99,
                repo_root=tmp_path,
            )

        # Task remains in WAITING_APPROVAL
        assert kernel.get_task(task_id)["state"] == "WAITING_APPROVAL"
    finally:
        kernel.close()


def test_autonomous_checker_integration_with_commit_verification_result(tmp_path: Path) -> None:
    """Test 7: [KEYC-L5 · B5] The gate's pre-execution attestation cannot complete the task; only a
    VerifierReceipt signed after execution (postcondition observed) reaches COMPLETED."""
    target_file = tmp_path / "calculator.py"
    target_file.write_text("def mul(a, b): return a * b\n", encoding="utf-8")

    kernel = TaskKernel(tmp_path / "kernel_e2e.sqlite3")
    try:
        task_id = "task-checker-e2e-1"
        kernel.create_task(task_id, owner="checker_test", goal="e2e lifecycle test")
        kernel.transition(task_id, "PLANNING")
        kernel.transition(task_id, "WAITING_APPROVAL")

        patch = (
            "<<<<<<< SEARCH\n"
            "def mul(a, b): return a * b\n"
            "=======\n"
            "def mul(a, b): return int(a * b)\n"
            ">>>>>>> REPLACE\n"
        )

        result = kernel.autonomous_review_and_approve(
            task_id=task_id,
            candidate_patch=patch,
            target_file=str(target_file),
            actor="autonomous_checker",
            repo_root=tmp_path,
        )

        assert result.approved is False
        assert result.receipt is not None
        receipt = result.receipt
        # [R5-B] The recommendation is approved by the issuer, not by the gate itself.
        assert kernel.get_task(task_id)["state"] == "WAITING_APPROVAL"
        kernel.commit_approval(task_id, issue_approval_token(task_id, issuer="release_manager"), actor="release_manager")

        # 1. Transition WAITING_APPROVAL -> READY was committed
        assert kernel.get_task(task_id)["state"] == "READY"

        # 2. Advance to QUEUED
        kernel.transition(task_id, "QUEUED")

        # 3. Worker claims lease and starts task
        lease = kernel.claim(task_id, "worker_lead", ttl_seconds=30)
        kernel.start(task_id, lease.lease_id)
        assert kernel.get_task(task_id)["state"] == "RUNNING"

        # 4. Worker executes the approved patch, then transitions RUNNING -> VERIFYING
        patched_bytes = b"def mul(a, b): return int(a * b)\n"
        target_file.write_bytes(patched_bytes)
        kernel.transition(task_id, "VERIFYING", lease_id=lease.lease_id)
        assert kernel.get_task(task_id)["state"] == "VERIFYING"

        # 5. [KEYC-L5 · B5] The checker attestation was signed before execution: refused, no completion
        with pytest.raises(InvalidReceiptSignatureError):
            kernel.commit_verification_result(
                task_id=task_id,
                lease_id=lease.lease_id,
                verification_result=receipt,
            )
        assert kernel.get_task(task_id)["state"] == "VERIFYING"
        assert [e for e in kernel.get_events(task_id) if e["type"] == "TASK_COMPLETED"] == []

        # 6. A verifier observes the executed postcondition and signs AFTER execution -> COMPLETED
        observed = hashlib.sha256(target_file.read_bytes()).hexdigest()
        assert observed == hashlib.sha256(patched_bytes).hexdigest()
        post_receipt = sign_verifier_receipt({
            "task_id": task_id,
            "verifier_id": "post_execution_verifier",
            "verdict": "VERIFIED",
            "evidence_ref": f"file-sha256://calculator.py/{observed}",
            "issued_at": time.time(),
        })
        final_task = kernel.commit_verification_result(
            task_id=task_id,
            lease_id=lease.lease_id,
            verification_result=post_receipt,
        )

        assert final_task["state"] == "COMPLETED"
        assert final_task["active_lease_id"] is None

        # Verify journal recorded TASK_COMPLETED with the post-execution verifier evidence
        events = kernel.get_events(task_id)
        comp_events = [e for e in events if e["type"] == "TASK_COMPLETED"]
        assert len(comp_events) == 1
        assert comp_events[0]["actor"] == "post_execution_verifier"
        payload = json.loads(comp_events[0]["payload_json"])
        assert payload["verifier_id"] == "post_execution_verifier"
        assert payload["verifier_verdict"] == "VERIFIED"
        assert payload["evidence_ref"] == post_receipt.evidence_ref
        assert payload["signature_digest"].startswith(f"sha256:{post_receipt.signature[:16]}")
    finally:
        kernel.close()


def test_autonomous_checker_unified_diff_format_support(tmp_path: Path) -> None:
    """Test 8: Unified diff format (@@ -... +... @@) is parsed and applied cleanly in-memory."""
    target_file = tmp_path / "diff_target.py"
    target_file.write_text(
        "def greet(name):\n"
        "    msg = 'hello'\n"
        "    return f'{msg} {name}'\n",
        encoding="utf-8",
    )

    kernel = TaskKernel(tmp_path / "kernel_diff.sqlite3")
    try:
        task_id = "task-checker-diff-1"
        kernel.create_task(task_id, owner="checker_test", goal="unified diff test")
        kernel.transition(task_id, "PLANNING")
        kernel.transition(task_id, "WAITING_APPROVAL")

        unified_diff = (
            "--- a/diff_target.py\n"
            "+++ b/diff_target.py\n"
            "@@ -1,3 +1,3 @@\n"
            " def greet(name):\n"
            "-    msg = 'hello'\n"
            "+    msg = 'greetings'\n"
            "     return f'{msg} {name}'\n"
        )

        result = kernel.autonomous_review_and_approve(
            task_id=task_id,
            candidate_patch=unified_diff,
            target_file=str(target_file),
            actor="autonomous_checker",
            repo_root=tmp_path,
        )

        assert result.approved is False
        assert result.verdict == "RECOMMEND_APPROVE"
        assert result.task["state"] == "WAITING_APPROVAL"
        assert result.receipt is not None
        assert result.receipt.verdict == "RECOMMEND_APPROVE"  # [KEYC-L5 · B5] never VERIFIED pre-execution
        assert verify_checker_recommendation(result.receipt, task_id=task_id) is True
    finally:
        kernel.close()
