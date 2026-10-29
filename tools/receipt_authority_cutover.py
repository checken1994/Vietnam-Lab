#!/usr/bin/env python3
"""Key C cutover (GĐ2): raise the kernel's one-way receipt-authority mode floor audit -> enforce.

    python tools/receipt_authority_cutover.py --db-path DB \\
        (--verifier-pipe ADDRESS [--ipc-token-file FILE] | --skip-verifier-check) [--actor NAME]

Preflight — every check must pass, otherwise the status is REFUSED and the DB is not written:
  1. the kernel DB file exists (the tool never creates an empty kernel DB);
  2. K_journal (SCP_JOURNAL_SECRET[_FILE]) resolves: the floor is an HMAC-signed control flag;
  3. this process holds no verifier PRIVATE key (kernel side = public keyring only);
  4. the public keyring SCP_VERIFIER_PUBLIC_KEYS[_FILE] loads and has >= 1 active, unexpired kid;
  5. the verifier daemon answers ``pubkey`` over its pipe with a key that is an active kid of that
     keyring — or ``--skip-verifier-check`` was given explicitly (reported SKIPPED, never REACHABLE).
Then the floor is raised through the kernel API (CommitApprovalEngine.raise_receipt_mode_floor) and
proven by a health check on a FRESH kernel instance (R6 lesson: the flag must really be ON in the DB):
floor == enforce, its §15 signature valid, and the raise journaled (__global__ RECEIPT_MODE_FLOOR_RAISED).

``--check`` is the standing health check (never raises the floor): ENFORCED (exit 0) only when the
signed value is enforce AND the raise is journaled; NOT_ENFORCED (exit 2) for a consistent audit floor;
INCONSISTENT (exit 1) for a missing/invalid signature or a control row that contradicts the journal.

Output: one JSON summary line on stdout (human line on stderr). Exit 0 COMPLETED, 2 REFUSED, 1 FAILED.
No .env loading: secrets come from the process env / ``*_FILE`` channels (secret_registry, HC-08).
There is no inverse tool: the floor is one-way by design.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

EXIT_COMPLETED, EXIT_FAILED, EXIT_REFUSED = 0, 1, 2
IPC_TOKEN_ENV = "SCP_VERIFIER_IPC_TOKEN"


class CutoverRefused(Exception):
    """A preflight check failed: nothing was written."""


def _check_database(db_path: Path) -> None:
    if not db_path.is_file():
        raise CutoverRefused(f"kernel database not found: {db_path}")


def _check_k_journal() -> str:
    from scp.task_kernel_parts.definitions import KernelError, journal_secret

    try:
        journal_secret()
    except KernelError as exc:
        raise CutoverRefused(f"K_journal unavailable, the floor cannot be signed: {exc}") from exc
    return "OK"


def _check_role_separation() -> str:
    from scp.verifier_service.client import VerifierConfigError, assert_worker_holds_no_private_key

    try:
        assert_worker_holds_no_private_key()
    except VerifierConfigError as exc:
        raise CutoverRefused(f"role separation: {exc}") from exc
    return "OK"


def _active_unexpired(entry: Any, now: float) -> bool:
    return entry.status == "active" and (entry.not_after is None or now <= entry.not_after)


def _check_keyring(now: float) -> tuple[dict[str, Any], Any]:
    from scp.security import receipt_authority as ra

    try:
        ring = ra.load_keyring()
    except ra.ReceiptAuthorityError as exc:
        raise CutoverRefused(f"public verifier keyring unusable: {exc}") from exc
    active = [entry.kid for entry in ring.entries if _active_unexpired(entry, now)]
    if not active:
        raise CutoverRefused("public verifier keyring has no active, unexpired kid")
    return {"status": "OK", "digest": ring.digest, "active_kids": active}, ring


def _ipc_token(token_file: str | None) -> str:
    if token_file:
        try:
            return Path(token_file).read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise CutoverRefused(f"verifier IPC token file unreadable ({type(exc).__name__})") from exc
    return os.environ.get(IPC_TOKEN_ENV, "").strip()


def _check_verifier(args: argparse.Namespace, ring: Any, now: float) -> dict[str, Any]:
    from scp.security import receipt_authority as ra
    from scp.verifier_service.client import VerifierClientError, VerifierDaemonClient

    if args.skip_verifier_check:
        return {"status": "SKIPPED"}
    if not args.verifier_pipe:
        raise CutoverRefused(
            "verifier reachability not checked: pass --verifier-pipe ADDRESS (with --ipc-token-file "
            f"or {IPC_TOKEN_ENV}) or --skip-verifier-check explicitly"
        )
    token = _ipc_token(args.ipc_token_file)
    if not token:
        raise CutoverRefused(f"verifier IPC token missing (--ipc-token-file or {IPC_TOKEN_ENV})")
    try:
        with VerifierDaemonClient.connect_pipe(args.verifier_pipe, token) as client:
            info = client.public_key()
    except VerifierClientError as exc:
        raise CutoverRefused(f"verifier daemon unreachable at {args.verifier_pipe}: {exc}") from exc
    try:
        kid = ra.compute_kid(base64.b64decode(info["public_key"], validate=True))
    except (binascii.Error, KeyError, TypeError, ValueError) as exc:
        raise CutoverRefused("verifier daemon returned an unusable public key") from exc
    if kid != info.get("kid") or info.get("domain") != ra.RECEIPT_DOMAIN_NAME:
        raise CutoverRefused("verifier daemon kid/domain does not match its public key (kid recomputed locally)")
    entry = ring.lookup(kid)
    if entry is None or not _active_unexpired(entry, now):
        raise CutoverRefused(f"verifier daemon key {kid} is not an active key in the public keyring")
    return {"status": "REACHABLE", "kid": kid}


def _env_mode() -> str:
    from scp.security import receipt_authority as ra

    try:
        return ra.resolve_mode()
    except ra.ReceiptAuthorityError as exc:
        return exc.code


def _health(db_path: Path) -> dict[str, Any]:
    from scp.task_kernel import TaskKernel
    from scp.task_kernel_parts.receipt_floor import receipt_floor_health

    kernel = TaskKernel(db_path=str(db_path))
    try:
        return receipt_floor_health(kernel)
    finally:
        kernel.close()


def run_cutover(args: argparse.Namespace) -> dict[str, Any]:
    """Preflight, raise, then prove the floor on a fresh kernel. Never raises: status says it all."""
    db_path = Path(args.db_path).resolve()
    summary: dict[str, Any] = {"status": "REFUSED", "reason": None, "db_path": str(db_path), "preflight": {}}
    preflight = summary["preflight"]
    now = time.time()
    try:
        _check_database(db_path)
        preflight["k_journal"] = _check_k_journal()
        preflight["role_separation"] = _check_role_separation()
        preflight["keyring"], ring = _check_keyring(now)
        preflight["verifier"] = _check_verifier(args, ring, now)
    except CutoverRefused as exc:
        summary["reason"] = str(exc)
        return summary
    summary["env_mode"] = _env_mode()
    try:
        summary["health_before"] = _health(db_path)
        from scp.task_kernel import TaskKernel

        kernel = TaskKernel(db_path=str(db_path))
        try:
            kernel.approval_engine.raise_receipt_mode_floor(actor=args.actor)
        finally:
            kernel.close()
        health = _health(db_path)  # fresh instance: proves what is persisted, not in-memory state
    except Exception as exc:  # report honestly; the kernel txn rolled back on any error
        summary.update(status="FAILED", reason=f"{type(exc).__name__}: {exc}")
        return summary
    summary["health"] = health
    if health["receipt_mode_floor_state"] == "ENFORCED":
        summary.update(status="COMPLETED", reason=None)
    else:
        summary.update(status="FAILED", reason="post-cutover health check did not prove a signed, journaled enforce floor")
    return summary


def run_check(db_path_arg: Any) -> dict[str, Any]:
    """Read-only health check (no preflight, no raise): ENFORCED / NOT_ENFORCED / INCONSISTENT.

    Opening the kernel runs its schema migration (a pre-L2b DB gets the floor columns, self-signed
    'audit', exactly as any kernel open would); the floor itself is never written here.
    """
    db_path = Path(db_path_arg).resolve()
    summary: dict[str, Any] = {"mode": "check", "db_path": str(db_path)}
    if not db_path.is_file():
        summary.update(status="INCONSISTENT", reason=f"kernel database not found: {db_path}")
        return summary
    summary["env_mode"] = _env_mode()
    health = _health(db_path)
    summary.update(status=health["receipt_mode_floor_state"], health=health, reason=None)
    if summary["status"] == "INCONSISTENT":
        summary["reason"] = "floor signature invalid/missing or control row contradicts the journal (tamper evidence)"
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db-path", required=True, type=Path, help="TaskKernel SQLite database (must exist)")
    parser.add_argument("--verifier-pipe", help="verifier daemon pipe address (\\\\.\\pipe\\NAME or UDS path)")
    parser.add_argument("--ipc-token-file", help=f"file with the verifier IPC token (else env {IPC_TOKEN_ENV})")
    parser.add_argument("--skip-verifier-check", action="store_true",
                        help="explicitly skip the verifier reachability check (reported as SKIPPED)")
    parser.add_argument("--actor", default="operator:receipt_authority_cutover", help="actor recorded in the journal")
    parser.add_argument("--check", action="store_true",
                        help="health check only (never raises the floor): exit 0 ENFORCED, 2 NOT_ENFORCED, 1 INCONSISTENT")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    summary = run_check(args.db_path) if args.check else run_cutover(args)
    status = summary["status"]
    if status in {"COMPLETED", "ENFORCED"}:
        print(f"[{status}] receipt mode floor is enforce (signature valid, raise journaled)", file=sys.stderr)
    else:
        print(f"[{status}] {summary['reason'] or 'receipt mode floor is audit (cutover not done)'}", file=sys.stderr)
    print(json.dumps(summary, sort_keys=True))
    codes = {"COMPLETED": EXIT_COMPLETED, "ENFORCED": EXIT_COMPLETED, "REFUSED": EXIT_REFUSED,
             "NOT_ENFORCED": EXIT_REFUSED}
    return codes.get(status, EXIT_FAILED)


if __name__ == "__main__":
    raise SystemExit(main())
