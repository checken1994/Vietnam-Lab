"""Contract tests for Phase B60 Domain-Driven Package Layout.

Verifies that scp.kernel, scp.pipeline, scp.verification, and scp.autofix
export their canonical domain symbols and maintain 100% parity with underlying parts.
"""
from __future__ import annotations

import pytest


def test_kernel_domain_package_exports():
    """Verify scp.kernel exposes all TaskKernel domain symbols."""
    import scp.kernel as kernel

    assert hasattr(kernel, "TaskKernel")
    assert hasattr(kernel, "KernelStorage")
    assert hasattr(kernel, "KernelError")
    assert hasattr(kernel, "InvalidTransition")
    assert hasattr(kernel, "Lease")
    assert hasattr(kernel, "ALLOWED_TRANSITIONS")
    assert hasattr(kernel, "TERMINAL")
    assert hasattr(kernel, "claim_impl")
    assert hasattr(kernel, "heartbeat_impl")
    assert hasattr(kernel, "RecoveryEngine")
    assert hasattr(kernel, "RecoveryManager")
    assert hasattr(kernel, "CommitApprovalEngine")
    assert hasattr(kernel, "IdempotencyEngine")
    assert hasattr(kernel, "CheckpointEngine")
    assert hasattr(kernel, "StateMachine")
    assert hasattr(kernel, "SoftFeedbackRemediationLoop")
    assert hasattr(kernel, "FeedbackContext")
    assert hasattr(kernel, "RemediationResult")

    # Test lease renewal self-transition
    assert "VERIFYING" in kernel.ALLOWED_TRANSITIONS["VERIFYING"]


def test_pipeline_domain_package_exports():
    """Verify scp.pipeline exposes all Ask & Execution Pipeline domain symbols."""
    import scp.pipeline as pipeline

    assert hasattr(pipeline, "AskPipelineRunner")
    assert hasattr(pipeline, "AskPipelineContext")
    assert hasattr(pipeline, "PipelineStage")
    assert hasattr(pipeline, "SecurityStage")
    assert hasattr(pipeline, "LookupStage")
    assert hasattr(pipeline, "GenerationStage")
    assert hasattr(pipeline, "VerificationStage")
    assert hasattr(pipeline, "LedgerStage")
    assert hasattr(pipeline, "DeliveryRuleEngine")
    assert hasattr(pipeline, "DecisionCode")
    assert hasattr(pipeline, "AskRequest")
    assert hasattr(pipeline, "AskResponse")


def test_verification_domain_package_exports():
    """Verify scp.verification exposes all Verification and Reality Judging domain symbols."""
    import scp.verification as verification

    assert hasattr(verification, "verify_ask_response")
    assert hasattr(verification, "RealityJudge")
    assert hasattr(verification, "VerificationTier")
    assert hasattr(verification, "TieredVerificationPolicy")
    assert hasattr(verification, "DeliveryRuleEngine")
    assert hasattr(verification, "DecisionCode")
    assert hasattr(verification, "build_safe_response")
    assert hasattr(verification, "build_kernel_blocked_response")


def test_autofix_domain_package_exports():
    """Verify scp.autofix exposes all AutoFix domain symbols."""
    import scp.autofix as autofix

    assert hasattr(autofix, "AutoFixEngine")
    assert hasattr(autofix, "BugClassifier")
    assert hasattr(autofix, "BugTier")
    assert hasattr(autofix, "PermissionGate")
