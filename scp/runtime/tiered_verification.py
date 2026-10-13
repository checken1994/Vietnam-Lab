"""Tiered Verification Architecture (Phase B58: Core Intelligence & UX).

Provides 3-tiered verification levels for epistemic assessments without compromising safety:
- PERMISSIVE: Informational lookup / creative responses. Relaxes epistemic grounding
  thresholds to avoid false withholding, while preserving 100% security boundaries
  (Egress Deny, Sandbox isolation, Capability Token PEP).
- STANDARD: Default for code generation and analysis. Enforces syntax & safety checks.
- STRICT: Mandatory for system mutation, workspace overwrite, and kernel operations.
"""
from __future__ import annotations

import logging
import os
from enum import Enum
from typing import Any

logger = logging.getLogger("scp.tiered_verification")


class VerificationTier(str, Enum):
    """Authoritative verification tiers for SCP responses and actions."""

    PERMISSIVE = "PERMISSIVE"
    STANDARD = "STANDARD"
    STRICT = "STRICT"


class TieredVerificationPolicy:
    """Policy rules governing verification rigor per tier."""

    @staticmethod
    def resolve_tier(
        req: Any = None,
        task: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
    ) -> VerificationTier:
        """Resolve active tier from explicit request headers/fields or infer from context."""
        # 1. Explicit request/task configuration
        explicit = (
            getattr(req, "verification_tier", None)
            or (task.get("verification_tier") if isinstance(task, dict) else None)
            or (data.get("verification_tier") if isinstance(data, dict) else None)
            or os.environ.get("SCP_VERIFICATION_TIER")
        )
        if explicit:
            norm = str(explicit).strip().upper()
            if norm in VerificationTier.__members__:
                return VerificationTier(norm)

        # 2. Strict operations: system overwrite / workspace mutate / kernel
        action_name = str((data or {}).get("action") or (task or {}).get("action") or "")
        mutates_state = bool(
            (data or {}).get("mutates_state")
            or (task or {}).get("mutates_state")
            or "write" in action_name
            or "execute" in action_name
            or "patch" in action_name
            or "kernel" in action_name
        )
        if mutates_state:
            return VerificationTier.STRICT

        # 3. Informational lookup / chatbot lane: PERMISSIVE (unless evidence-backed RAG ask)
        has_contexts = bool(getattr(req, "contexts", None) or getattr(req, "retrieved_context", None))
        lane = str((data or {}).get("lane") or getattr(req, "lane", "") or "")
        intent = str((data or {}).get("intent") or getattr(req, "intent", "") or "")
        if not has_contexts and (lane == "LANE_CHATBOT" or intent == "LOOKUP" or "lookup" in action_name):
            return VerificationTier.PERMISSIVE

        # 4. Default: STANDARD
        return VerificationTier.STANDARD

    @staticmethod
    def is_epistemic_relaxed(tier: VerificationTier) -> bool:
        """Returns True if epistemic withholding should be relaxed (PERMISSIVE tier only)."""
        return tier == VerificationTier.PERMISSIVE

    @staticmethod
    def enforce_security_invariants(data: dict[str, Any]) -> bool:
        """Security boundaries are ALWAYS non-negotiable across ALL tiers."""
        evidence = data.get("evidence", {}) if isinstance(data.get("evidence"), dict) else {}
        gov = str(
            data.get("governance_decision")
            or data.get("governance")
            or evidence.get("governance_decision")
            or evidence.get("governance")
            or ""
        ).strip().upper()
        if gov in ("KILL", "BLOCK", "DENIED", "REJECTED"):
            return False

        if bool(
            data.get("egress_denied")
            or data.get("egress_blocked")
            or evidence.get("egress_denied")
            or evidence.get("egress_blocked")
            or data.get("sandbox_violation")
            or data.get("sandbox_blocked")
            or evidence.get("sandbox_violation")
            or data.get("token_revoked")
            or data.get("pep_denied")
            or data.get("kill_switch")
            or data.get("kill_switch_engaged")
        ):
            return False

        guard = data.get("v98_guard")
        if isinstance(guard, dict) and bool(guard.get("security_blocked")):
            return False
        return True


__all__ = [
    "VerificationTier",
    "TieredVerificationPolicy",
]
