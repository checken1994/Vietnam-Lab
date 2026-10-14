"""Government-Grade 4-Role RBAC/ABAC Separation of Duties Policy.

Implements Circular 12/2022/TT-BTTTT, TCVN 11930:2017 (Access Control Level 3-4),
and NIST SP 800-53 Rev. 5 control AC-5 (Separation of Duties):
  1. PUBLIC_READER: Can query `/ask` and `/health`, cannot ingest data, change config, or view audit logs.
  2. KNOWLEDGE_INGESTOR: Can stage documents into Quarantine (`kb:quarantine_stage`), cannot promote without verifier receipt or alter system config.
  3. SECURITY_AUDITOR: Can read/verify WORM audit logs and run blind benchmarks (`audit:read`, `audit:verify_chain`, `benchmark:run`), cannot mutate config or ingest data.
  4. SYSTEM_OPERATOR: Can configure runtime profiles and promote verified knowledge (`system:config`, `kb:promote`), cannot delete or tamper with WORM audit logs (`audit:delete` is forbidden for ALL roles).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Final

ROLE_PUBLIC_READER: Final[str] = "PUBLIC_READER"
ROLE_KNOWLEDGE_INGESTOR: Final[str] = "KNOWLEDGE_INGESTOR"
ROLE_SECURITY_AUDITOR: Final[str] = "SECURITY_AUDITOR"
ROLE_SYSTEM_OPERATOR: Final[str] = "SYSTEM_OPERATOR"

ROLE_PERMISSIONS: Final[dict[str, frozenset[str]]] = {
    ROLE_PUBLIC_READER: frozenset({"ask:query", "health:read"}),
    ROLE_KNOWLEDGE_INGESTOR: frozenset({"ask:query", "health:read", "kb:quarantine_stage", "kb:read"}),
    ROLE_SECURITY_AUDITOR: frozenset(
        {"health:read", "audit:read", "audit:verify_chain", "benchmark:run", "kb:read"}
    ),
    ROLE_SYSTEM_OPERATOR: frozenset(
        {"ask:query", "health:read", "system:config", "kb:promote", "kb:read"}
    ),
}

# Actions that are strictly forbidden for ALL roles under WORM non-repudiation
FORBIDDEN_IMMUTABLE_ACTIONS: Final[frozenset[str]] = frozenset(
    {"audit:delete", "audit:truncate", "audit:rewrite", "exam:modify"}
)


class RBACPermissionDeniedError(PermissionError):
    """Raised when a role attempts an action outside its Separation-of-Duties boundary."""


@dataclass(frozen=True)
class ActorPrincipal:
    actor_id: str
    role: str
    clearance_level: str = "PUBLIC"


class GovernmentRBACPolicy:
    """Deterministic Separation-of-Duties (AC-5) authorization enforcer."""

    def check_permission(self, principal: ActorPrincipal, action: str) -> bool:
        norm_action = action.strip().lower()
        if norm_action in FORBIDDEN_IMMUTABLE_ACTIONS:
            raise RBACPermissionDeniedError(
                f"AC-5/AU-9 VIOLATION: Action {norm_action!r} is permanently forbidden for all roles (including {principal.role})."
            )
        allowed = ROLE_PERMISSIONS.get(principal.role)
        if allowed is None:
            raise RBACPermissionDeniedError(f"Unknown RBAC role: {principal.role!r}")
        if norm_action not in allowed:
            raise RBACPermissionDeniedError(
                f"RBAC_DENIED: Principal {principal.actor_id!r} with role {principal.role!r} "
                f"is not permitted to execute {norm_action!r}."
            )
        return True
