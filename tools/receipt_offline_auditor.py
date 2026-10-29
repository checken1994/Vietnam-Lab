#!/usr/bin/env python3
"""Offline Key C receipt auditor (lane L6, GĐ2 acceptance condition).

Re-verifies every COMPLETED task of a kernel SQLite file with only the PUBLIC verifier keyring:

    python tools/receipt_offline_auditor.py --db KERNEL_COPY.sqlite3 --keyring verifier_keyring.json [--since 0]

* Input is a checkpointed COPY of the kernel DB (a DB with a -wal/-journal sidecar is refused) and the
  public keyring JSON. A private key, K_journal or .env is refused; the process environment is never read.
* The DB is opened with the SQLite URI ``mode=ro&immutable=1``; TaskKernel is never instantiated.
* Exit 0 = PASS (no failure observed in this snapshot; PASS != TRUE), 1 = FAIL, 2 = input refused / UNKNOWN.

The logic lives in ``scp/runtime_audit/receipt_auditor.py`` (importable by tests and by the runtime-audit
runner); this file is only the operator entry point.
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scp.runtime_audit.receipt_auditor import main

if __name__ == "__main__":
    sys.exit(main())
