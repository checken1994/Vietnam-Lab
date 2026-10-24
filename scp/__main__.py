"""SCP entry point — run with: python3 -m scp [port]

Defaults to port 8000. Reads SCP_PORT env var if no arg is passed.
Reads SCP_HOST env var (default 127.0.0.1 — bind to loopback only; the
Caddy gateway on :81 reverse-proxies public traffic via ?XTransformPort=8000).

Examples:
    python3 -m scp                 # 127.0.0.1:8000 (default)
    python3 -m scp 8080            # 127.0.0.1:8080
    SCP_PORT=9000 python3 -m scp   # 127.0.0.1:9000

NOTE: `app` is the FastAPI instance defined at module level in
`scp/api_server.py` (line ~438). uvicorn imports it via the string
"scp.api_server:app" so it doesn't import this __main__ module (which would
trigger uvicorn.run() recursively).
"""
from __future__ import annotations

import os
import sys

# [HC-02] Canonical loader import: scp/__init__ + scp.core.release_identity are
# constant-only (verified light), so importing before env loading reads no env.
from scp.security.env_loader import load_selected_env


# [SCP-DNA-FIX R14-ENV] ROOT CAUSE FIX: load .env BEFORE anything else.
#
# 5-Whys analysis:
#   Symptom: User set SCP_AUTH_PASSWORD + SCP_SKIP_STARTUP_GATE in .env but
#   gate doesn't run / auth doesn't work.
#   Why 1: .env is never loaded when `python3 -m scp` starts
#   Why 2: load_dotenv() only exists in scp/autofix/runner.py
#   Why 3: runner.py is imported LAZILY (inside lifespan() function, line 92)
#   Why 4: By the time lifespan() runs, api_server.py module-level code has
#   already read env vars (CORS, FORCE_HTTPS, etc.) with EMPTY values
#   Why 5 (ROOT): .env loading is FRAGMENTED — it happens too late (inside
#         lifespan) instead of at process start. Env reads at module-level
#         and early lifespan (SCP_SKIP_STARTUP_GATE at line 87) happen
#         BEFORE runner.py imports and loads .env.
#
# ROOT FIX: load .env at the VERY START of __main__.py, before uvicorn
# imports api_server. This ensures ALL env reads (module-level, lifespan,
# verify_admin) see the real values from .env.
#
# Why this is ROOT not CASCADE:
# - Cascade fix would add load_dotenv() to api_server.py module-level too,
#   but that still runs AFTER __main__.py reads SCP_PORT/SCP_HOST.
# - This fix loads .env ONCE at process entry → all downstream code sees
#   correct env, regardless of import order.
# - Eliminates the bug CLASS (late .env loading) at origin.
#
# [HC-02 2026-10-23] ROOT FIX part 2: this loader used to be a DIVERGENT copy
# of scp/security/env_loader.py — same resolution rules but (a) the explicit
# SCP_ENV_FILE branch overrode process env SILENTLY (the exact "âm thầm kéo
# về cấu hình cũ" class W4-2 made loud in env_loader), and (b) the fallback
# branch depended on python-dotenv with a hand-rolled parser duplicate.
# Delegating to the canonical loader removes the second implementation: the
# W4-2 contract (loud override warnings, SCP_ENV_FILE launcher-owned,
# per-path idempotency so runtime-written vars are not re-clobbered by the
# later import-time call in api_server.py/judge_llm.py) now covers __main__
# too. Resolution target is identical (repo-root .env / SCP_ENV_FILE via
# Path.cwd()); the repo .env uses plain KEY=VALUE lines (verified: no
# `export` prefix, no inline comments), so dropping the dotenv dependency is
# behavior-preserving here.
def _load_env_at_startup() -> None:
    """Load the selected env file via the canonical env_loader (HC-02).
    SCP_ENV_FILE is the explicit configuration boundary for isolated runs.
    If absent, retain the historical repo-root .env fallback.
    If present but missing, fail closed by loading no env file.
    """
    load_selected_env()
_load_env_at_startup()
import logging

from scp.security.production_guard import enforce_production_safety

logger = logging.getLogger(__name__)

enforce_production_safety()
def main() -> None:
    if any(arg in {"-h", "--help"} for arg in sys.argv[1:]):
        print("usage: python -m scp [PORT]")
        print("Starts the SCP API on loopback; PORT defaults to SCP_PORT or 8000.")
        return
    port = (
        int(sys.argv[1])
        if len(sys.argv) > 1
        else int(os.environ.get("SCP_PORT", "8000"))
    )
    os.environ["SCP_PORT"] = str(port)
    host = os.environ.get("SCP_HOST", "127.0.0.1")

    import asyncio

    import uvicorn
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    # Import via string so uvicorn's reloader (when reload=True) doesn't
    # double-import this module. We keep reload=False — the SCP codebase
    # spins up background threads at startup that don't survive a reload.
    uvicorn.run(
        "scp.api_server:app",
        host=host,
        port=port,
        reload=False,
        log_level="info",
        access_log=True,
    )


if __name__ == "__main__":
    main()
