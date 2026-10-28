#!/usr/bin/env python3
"""Issuer CLI for TaskKernel approvals (R5-B).

The kernel only accepts ``approval:grant`` tokens signed with the issuer Ed25519 PRIVATE key.
Run this where that key lives (operator workstation or issuer service), never inside the agent:

    python tools/issue_approval_token.py --task-id TASK --issuer alice
    python tools/issue_approval_token.py --generate-keypair DIR

The private key is read from SCP_APPROVAL_ISSUER_PRIVATE_KEY or SCP_APPROVAL_ISSUER_PRIVATE_KEY_FILE.
``--generate-keypair`` writes issuer_private.key / issuer_public.key (base64 raw keys) into DIR;
deploy the public key to the kernel as SCP_APPROVAL_ISSUER_PUBLIC_KEY(_FILE).
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scp.security.approval_issuer import (  # noqa: E402
    DEFAULT_TTL_SECONDS,
    generate_issuer_keypair,
    issue_approval_token,
)


def _write_private(path: Path, value: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as handle:
        handle.write(value + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--task-id", help="task to approve (token is bound to exactly this task)")
    parser.add_argument("--issuer", help="identity of the approving operator/service")
    parser.add_argument("--ttl", type=float, default=DEFAULT_TTL_SECONDS, help="validity in seconds (max 3600)")
    parser.add_argument("--generate-keypair", metavar="DIR", help="write a new issuer keypair into DIR")
    args = parser.parse_args(argv)

    if args.generate_keypair:
        out = Path(args.generate_keypair)
        out.mkdir(parents=True, exist_ok=True)
        private_b64, public_b64 = generate_issuer_keypair()
        _write_private(out / "issuer_private.key", private_b64)
        (out / "issuer_public.key").write_text(public_b64 + "\n", encoding="ascii")
        print(f"wrote {out / 'issuer_private.key'} (keep with the issuer only) and {out / 'issuer_public.key'}")
        return 0
    if not args.task_id or not args.issuer:
        parser.error("--task-id and --issuer are required unless --generate-keypair is used")
    print(issue_approval_token(args.task_id, args.issuer, ttl_seconds=args.ttl))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
