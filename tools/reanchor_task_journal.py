#!/usr/bin/env python3
"""Tool to verify and re-anchor TaskKernel event journal hash chains.

Remediates F-01: Historical tasks with legacy stable_hash, a rotated
SCP_JOURNAL_SECRET, or (R5 migration) the pre-R5 SCP_CAPABILITY_SECRET are
safely verified and re-anchored to the active K_journal so all journals verify
cleanly with 0 invalid chains. A clean pass closes the R5 dual-accept window.
"""
from __future__ import annotations

import argparse
import logging
import shutil
import sys
import time
from pathlib import Path
from typing import Any

# Ensure repo root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")

from scp.task_kernel import TaskKernel

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("reanchor_task_journal")


def reanchor_database(db_path: Path | str, backup: bool = True) -> dict[str, Any]:
    target = Path(db_path).resolve()
    if not target.exists():
        raise FileNotFoundError(f"Database file not found: {target}")

    # [R5] Target key is K_journal. Pre-R5 journals are signed with SCP_CAPABILITY_SECRET, which
    # the kernel accepts as a migration source only while control.journal_key_migrated == 0.
    from scp.security.secret_registry import resolve

    if not resolve("SCP_JOURNAL_SECRET"):  # env or SCP_JOURNAL_SECRET_FILE
        raise RuntimeError("MissingSecretError: SCP_JOURNAL_SECRET (or _FILE) must be set in environment or .env")

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
            # [R5.1] Report the kernel's real outcome: PARTIAL while the K_journal cutover flag is
            # still 0 (failed chains or live pre-window checkpoints), never a blanket PASS.
            "status": result.get("status", "PARTIAL"),
            "journal_key_migrated": bool(result.get("journal_key_migrated")),
            "journal_key_cutover_blocked_by": list(result.get("journal_key_cutover_blocked_by") or []),
            "failed_tasks": list(result.get("failed_tasks") or []),
        }
    finally:
        kernel.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Re-anchor TaskKernel event journal hash chains")
    parser.add_argument(
        "--db-path",
        type=Path,
        default=PROJECT_ROOT / "data" / "ask_task_kernel.sqlite3",
        help="Path to TaskKernel SQLite database",
    )
    parser.add_argument("--no-backup", action="store_true", help="Skip creating safety backup copy")
    args = parser.parse_args(argv)

    try:
        summary = reanchor_database(args.db_path, backup=not args.no_backup)
    except Exception as exc:
        print(f"\n[FAIL] Re-anchor failed: {exc}", file=sys.stderr)
        return 1
    if summary["status"] != "COMPLETED":
        print(f"\n[PARTIAL] Re-anchor done but the K_journal cutover has NOT happened: {summary}")
        if summary["journal_key_cutover_blocked_by"]:
            print("Blocked by live tasks with pre-window checkpoints (let them finish or cancel them, then rerun): "
                  + ", ".join(summary["journal_key_cutover_blocked_by"]))
        if summary["failed_tasks"]:
            print("Unprovable journals: " + ", ".join(summary["failed_tasks"]))
        return 2
    print(f"\n[PASS] Re-anchor successful, K_journal cutover complete: {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
