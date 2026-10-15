#!/usr/bin/env python3
"""Tool to verify and re-anchor TaskKernel event journal hash chains.

Remediates F-01: Historical tasks with legacy stable_hash or rotated
SCP_CAPABILITY_SECRET are safely verified and re-anchored to the active
secret so all journals verify cleanly with 0 invalid chains.
"""
from __future__ import annotations

import argparse
import logging
import os
import shutil
import sys
import time
from pathlib import Path

# Ensure repo root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")

from scp.task_kernel import TaskKernel

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("reanchor_task_journal")


def reanchor_database(db_path: Path | str, backup: bool = True) -> dict[str, int | str]:
    target = Path(db_path).resolve()
    if not target.exists():
        raise FileNotFoundError(f"Database file not found: {target}")

    secret = os.environ.get("SCP_CAPABILITY_SECRET", "").strip()
    if not secret:
        raise RuntimeError("MissingSecretError: SCP_CAPABILITY_SECRET must be set in environment or .env")

    if backup:
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        backup_path = target.with_name(f"{target.stem}_pre_reanchor_{timestamp}.sqlite3")
        shutil.copyfile(target, backup_path)
        logger.info(f"Created safety backup at: {backup_path}")

    kernel = TaskKernel(db_path=str(target))
    try:
        pre_integrity = kernel.verify_integrity()
        logger.info(
            f"Pre-reanchor integrity: tasks={pre_integrity['tasks']}, invalid_chains={len(pre_integrity['invalid_chains'])}"
        )

        result = kernel.reanchor_all_journals()
        logger.info(f"Reanchor outcome: {result}")

        post_integrity = kernel.verify_integrity()
        invalid_count = len(post_integrity["invalid_chains"])
        logger.info(
            f"Post-reanchor integrity: tasks={post_integrity['tasks']}, invalid_chains={invalid_count}"
        )

        if invalid_count > 0:
            logger.error(f"Integrity check failed: {post_integrity['invalid_chains'][:5]}")
            raise RuntimeError(f"Re-anchor completed with {invalid_count} remaining invalid chains!")

        return {
            "tasks_checked": post_integrity["tasks"],
            "reanchored_tasks": result.get("reanchored_tasks", 0),
            "invalid_chains": invalid_count,
            "status": "PASS",
        }
    finally:
        kernel.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Re-anchor TaskKernel event journal hash chains")
    parser.add_argument(
        "--db-path",
        type=Path,
        default=PROJECT_ROOT / "data" / "ask_task_kernel.sqlite3",
        help="Path to TaskKernel SQLite database",
    )
    parser.add_argument("--no-backup", action="store_true", help="Skip creating safety backup copy")
    args = parser.parse_args()

    try:
        summary = reanchor_database(args.db_path, backup=not args.no_backup)
        print(f"\n[PASS] Re-anchor successful: {summary}")
        sys.exit(0)
    except Exception as exc:
        print(f"\n[FAIL] Re-anchor failed: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
