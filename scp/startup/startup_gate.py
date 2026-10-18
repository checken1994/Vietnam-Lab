"""Startup Gate Scanner Transparency Module.

Provides transparent file-counting and scan scope resolution for the startup
AST verification gate. Ensures operators and logs clearly distinguish between
sampled quick scans (e.g. 100/708 files) and full scans (708/708 files).
Supports SCP_STARTUP_SCAN_ALL=1 to enable comprehensive full-codebase audits.
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger("scp.startup.startup_gate")

# Resolve the scp package root directory dynamically
_SCP_ROOT = Path(__file__).resolve().parent.parent


def get_eligible_files(root: Path | None = None) -> list[Path]:
    """Dynamically discover all eligible Python source files in the scp package.

    Follows canonical scanner filtering:
    - Skips __pycache__ and tests
    - Skips test_*.py files
    - Skips examples, scripts, attack_payloads
    - Sorted for deterministic ordering
    """
    target_root = root if root is not None else _SCP_ROOT
    return sorted(
        path for path in target_root.rglob("*.py")
        if "__pycache__" not in path.parts
        and "tests" not in path.parts
        and not path.name.startswith("test_")
        and not any(part in ("examples", "scripts", "attack_payloads") for part in path.parts)
    )


def count_eligible_files(root: Path | None = None) -> int:
    """Return the total count of eligible Python files in the scp package."""
    return len(get_eligible_files(root))


def format_startup_scan_summary(
    bug_count: int,
    scanned_count: int,
    total_count: int,
    is_full_scan: bool,
) -> str:
    """Format transparent startup gate scan completion log message.

    Canonical formats:
      '[STARTUP-GATE] Background scan complete: 0 bugs found across 100/708 files (sampled)'
      '[STARTUP-GATE] Background scan complete: 0 bugs found across 708/708 files (full scan)'
    """
    mode = "full scan" if is_full_scan else "sampled"
    return f"[STARTUP-GATE] Background scan complete: {bug_count} bugs found across {scanned_count}/{total_count} files ({mode})"


def resolve_scan_scope(
    max_files: int | None = None,
    scan_all: bool | None = None,
    root: Path | None = None,
) -> tuple[int | None, int, int, str]:
    """Determine effective scan parameters based on environment and arguments.

    Returns:
        (effective_max_files, scanned_count, total_count, mode)

        - effective_max_files: int limit for sampling, or None when full scan is requested.
        - scanned_count: number of files that will be scanned.
        - total_count: dynamic count of all eligible files in the codebase.
        - mode: 'full scan' or 'sampled'.
    """
    total_count = count_eligible_files(root)

    if scan_all is None:
        scan_all = os.environ.get("SCP_STARTUP_SCAN_ALL", "0") == "1"

    if scan_all:
        effective_max_files = None
        scanned_count = total_count
        mode = "full scan"
    elif max_files is None and "SCP_MAX_STARTUP_FILES" not in os.environ and "SCP_STARTUP_SCAN_ALL" not in os.environ:
        # Default sampling
        configured_limit = 100
        effective_max_files = configured_limit
        scanned_count = min(configured_limit, total_count)
        mode = "full scan" if scanned_count >= total_count else "sampled"
    else:
        limit_val = max_files if max_files is not None else int(os.environ.get("SCP_MAX_STARTUP_FILES", "100"))
        effective_max_files = limit_val
        scanned_count = min(limit_val, total_count)
        mode = "full scan" if scanned_count >= total_count else "sampled"

    return effective_max_files, scanned_count, total_count, mode


async def run_startup_ast_scan(
    timeout_sec: float | None = None,
    include_enterprise: bool = False,
    max_files: int | None = None,
    scan_all: bool | None = None,
    root: Path | None = None,
) -> tuple[list[Any], int, int, str]:
    """Execute the startup AST scan in a background thread with bounded timeout.

    Returns:
        (bugs, scanned_count, total_count, mode)
    """
    from scp.autofix.runner import ast_scan_scp

    effective_max_files, scanned_count, total_count, mode = resolve_scan_scope(
        max_files=max_files,
        scan_all=scan_all,
        root=root,
    )

    if timeout_sec is None:
        timeout_sec = float(os.environ.get("SCP_STARTUP_SCAN_TIMEOUT_SEC", "15"))

    # When effective_max_files is None (full scan), pass total_count to ast_scan_scp
    # to avoid TypeError in ast_scan runner while ensuring 100% of files are scanned.
    scan_limit = total_count if effective_max_files is None else effective_max_files

    bugs = await asyncio.wait_for(
        asyncio.to_thread(
            ast_scan_scp,
            max_files=scan_limit,
            include_enterprise=include_enterprise,
        ),
        timeout=max(1.0, timeout_sec),
    )

    return bugs, scanned_count, total_count, mode
