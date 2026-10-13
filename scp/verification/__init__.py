"""SCP Verification Domain Package (Phase B60 Domain-Driven Layout).

Provides canonical domain-level access to the SCP Verification and Reality Judging subsystem,
including tiered epistemic verification, RealityJudge, delivery rule enforcement,
and response building.

Backward compatibility:
- All symbols are identical to and backed by `scp.ask_adapter_parts.response_verifier`,
  `scp.runtime.judge`, `scp.runtime.tiered_verification`, and `scp.api_server_parts.pipeline.delivery_rules`.
"""
from __future__ import annotations

from scp.api_server_parts.pipeline.delivery_rules import DecisionCode, DeliveryRuleEngine
from scp.ask_adapter_parts.response_verifier import (
    build_kernel_blocked_response,
    build_safe_response,
    lookup_fork_self_certified,
    verification_fail_reason,
    verify_ask_response,
)
from scp.runtime.judge import RealityJudge
from scp.runtime.tiered_verification import TieredVerificationPolicy, VerificationTier

__all__ = [
    "DecisionCode",
    "DeliveryRuleEngine",
    "RealityJudge",
    "TieredVerificationPolicy",
    "VerificationTier",
    "build_kernel_blocked_response",
    "build_safe_response",
    "lookup_fork_self_certified",
    "verification_fail_reason",
    "verify_ask_response",
]
