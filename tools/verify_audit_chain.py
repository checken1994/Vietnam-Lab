#!/usr/bin/env python3
"""CLI Verifier for the WORM Merkle Audit Chain (TCVN 11930:2017 / NIST AU-9, AU-10)."""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scp.security.worm_audit_ledger import WormMerkleAuditLedger


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify WORM Merkle Audit Ledger integrity.")
    parser.add_argument("--db", type=Path, default=None, help="Path to worm_audit_ledger.db")
    parser.add_argument("--self-test", action="store_true", help="Run a deterministic self-test")
    args = parser.parse_args()

    if args.self_test or args.db is None or not args.db.exists():
        with tempfile.TemporaryDirectory() as tmp:
            ledger = WormMerkleAuditLedger(Path(tmp) / "self_test_worm.db")
            ledger.append_event(
                actor_id="auditor@gov.vn",
                actor_role="SECURITY_AUDITOR",
                action="AUDIT_SELF_TEST",
                payload={"gate": "TCVN_11930_AU9", "result": "PASS"},
            )
            res = ledger.verify_chain_integrity()
            print(json.dumps(res, indent=2))
            return 0 if res["status"] == "VERIFIED" else 1

    ledger = WormMerkleAuditLedger(args.db)
    res = ledger.verify_chain_integrity()
    print(json.dumps(res, indent=2))
    return 0 if res["status"] == "VERIFIED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
