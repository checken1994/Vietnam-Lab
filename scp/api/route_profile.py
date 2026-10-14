"""Explicit API-surface profiles & 7-Subsystem Isolation for the SCP Monorepo (Wave W23).

Enables a single operator to maintain all 7 product subsystems in one repository
without CI or runtime overload by isolating:
  - `core` (Level 0): TaskKernel, AtomicVerifier, CanonicalFTSWarehouse, LLM Gateway, Security/Egress, Trace/Health
  - `standard` (Level 1): Core + `/ask`, Chat, OpenAI-Compat, Evaluation, Control, Hands
  - `full` (Level 2): All 7 subsystems including `pc_controller`, `web_control`, `forecast`, `batch_benchmark`, etc.

Also supports fine-grained opt-out via `SCP_DISABLED_GROUPS` (comma-separated list of groups to disable)
and fine-grained opt-in via `SCP_ENABLED_GROUPS`.
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

PROFILE_LEVEL = {"core": 0, "standard": 1, "full": 2}

# 7 Macro Product Pillars mapped to route groups for single-operator monorepo governance
SEVEN_PRODUCT_PILLARS: dict[str, tuple[str, ...]] = {
    "1_task_kernel_and_trace": ("trace",),
    "2_verified_qa_and_fts5_kb": ("chat", "openai_compat", "evaluation"),
    "3_hands_pc_and_web_automation": ("hands", "pc_controller", "web_control", "call"),
    "4_autofix_and_self_audit": ("audit", "control", "threat"),
    "5_forecast_and_world_state": ("prediction", "forecast", "world_state", "calibration", "risk_intelligence"),
    "6_llm_gateway_and_stream": ("agent", "stream", "webhook"),
    "7_admin_and_batch_ops": ("versioned_admin", "import", "batch_benchmark", "history"),
}

# A group is registered only when the selected profile reaches this level.
GROUP_MINIMUM = {
    "trace": "core",
    "chat": "standard",
    "openai_compat": "standard",
    "evaluation": "standard",
    "control": "standard",
    "hands": "standard",
    "agent": "standard",
    "versioned_admin": "full",
    "import": "full",
    "stream": "full",
    "threat": "full",
    "audit": "full",
    "prediction": "full",
    "webhook": "full",
    "pc_controller": "full",
    "web_control": "full",
    "batch_benchmark": "full",
    "call": "full",
    # Restored subsystems
    "risk_intelligence": "full",
    "world_state": "full",
    "calibration": "full",
    "forecast": "full",
    "history": "full",
}


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _parse_group_set(raw: str | None) -> set[str]:
    if not raw:
        return set()
    return {item.strip().lower() for item in str(raw).split(",") if item.strip()}


def resolve_api_profile(environ: Mapping[str, str] | None = None) -> str:
    """Resolve the profile, defaulting production to the least surface (`core`)."""
    env = os.environ if environ is None else environ
    configured = str(env.get("SCP_API_PROFILE", "")).strip().lower()
    if not configured:
        return "core" if _truthy(env.get("SCP_PRODUCTION_MODE")) else "full"
    if configured not in PROFILE_LEVEL:
        allowed = ", ".join(PROFILE_LEVEL)
        raise RuntimeError(f"invalid SCP_API_PROFILE={configured!r}; expected one of: {allowed}")
    return configured


def route_group_enabled(
    group: str,
    profile: str,
    environ: Mapping[str, str] | None = None,
) -> bool:
    if profile not in PROFILE_LEVEL:
        raise RuntimeError(f"unknown API profile: {profile}")
    minimum = GROUP_MINIMUM.get(group)
    if minimum is None:
        raise RuntimeError(f"unclassified API route group: {group}")
    env = os.environ if environ is None else environ
    disabled = _parse_group_set(env.get("SCP_DISABLED_GROUPS"))
    if group.lower() in disabled:
        return False
    enabled_override = _parse_group_set(env.get("SCP_ENABLED_GROUPS"))
    if group.lower() in enabled_override:
        return True
    return PROFILE_LEVEL[profile] >= PROFILE_LEVEL[minimum]


def get_active_profile_summary(environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Return a structured manifest of active vs frozen route groups across the 7 product pillars."""
    env = os.environ if environ is None else environ
    profile = resolve_api_profile(env)
    enabled_groups = sorted(g for g in GROUP_MINIMUM if route_group_enabled(g, profile, env))
    disabled_groups = sorted(g for g in GROUP_MINIMUM if not route_group_enabled(g, profile, env))
    pillars_status: dict[str, dict[str, Any]] = {}
    for pillar, groups in SEVEN_PRODUCT_PILLARS.items():
        active_in_pillar = [g for g in groups if g in enabled_groups]
        pillars_status[pillar] = {
            "active": len(active_in_pillar) > 0,
            "enabled_groups": active_in_pillar,
            "frozen_groups": [g for g in groups if g not in enabled_groups],
        }
    return {
        "profile": profile,
        "enabled_count": len(enabled_groups),
        "disabled_count": len(disabled_groups),
        "enabled_groups": enabled_groups,
        "disabled_groups": disabled_groups,
        "pillars": pillars_status,
    }
