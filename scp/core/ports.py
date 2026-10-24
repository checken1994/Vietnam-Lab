"""Canonical port registry for SCP services (HC-26).

Single Python source of truth for service ports. Cross-checked against:

- ``docs/architecture/PORTS.md``            (human-readable registry)
- ``docker-compose.full.yml``               (published service ports)

Drift guard: ``tests/T00_integrity/test_ports_registry.py``. If you change a
port here you MUST update all other sources in the same change, or the test
fails (anti-placebo: one source edited without the others -> test văng).

Do not import env parsing or heavy modules here — this file must stay
dependency-free so integrity tests can load it anywhere.

History: ``LLM_BRIDGE = 8081`` (mini-services/llm-bridge) was removed on
2026-10-24 together with the bridge directory itself (Zero-Bun deprecation).
Ports 8081/11434 must not be reused for new services.
"""

from __future__ import annotations

# Core services (published in docker-compose.full.yml)
BACKEND = 8000
DASHBOARD = 3000
LOOP_SCHEDULER = 3030
AGENT_DESKTOP = 6080
BRAIN_CONSOLE = 8090

# Agent-desktop Chromium CDP (env CHROME_DEBUG_PORT inside agent-desktop;
# loopback-internal, not published by docker-compose.full.yml)
CDP = 9222

# Ports documented in PORTS.md but intentionally NOT constant-ized here
# (owned by other config files):
#   4318   OTLP HTTP collector (docker-compose.observability.yml)
#   8055   hermetic audit backend (scripts/run_full_audit.py AUDIT_PORT)

__all__ = [
    "BACKEND",
    "DASHBOARD",
    "LOOP_SCHEDULER",
    "AGENT_DESKTOP",
    "BRAIN_CONSOLE",
    "CDP",
]
