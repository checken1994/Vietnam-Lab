#!/usr/bin/env python3
"""Run the SCP runtime reality audit and emit tamper-evident evidence.

The audit observes the system that is RUNNING, not the repository that
describes it. Every number in the report comes from an observation made
during this process: SQLite files opened read-only, HTTP endpoints actually
requested, ledgers actually read.

Usage
-----
    python tools/run_runtime_audit.py
    python tools/run_runtime_audit.py --data-dir D:\\scp\\data --output reports/runtime
    python tools/run_runtime_audit.py --json-only

Exit codes
----------
    0  PASS or WARN   — no failure observed inside the probed scope
    1  FAIL           — a failure was observed (this is the interesting case)
    2  UNKNOWN        — part of the scope could not be observed
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scp.core.runtime_paths import runtime_data_dir  # noqa: E402
from scp.runtime_audit import RuntimeAuditLedger, RuntimeAuditRunner  # noqa: E402
from scp.runtime_audit.models import Verdict  # noqa: E402

DEFAULT_OUTPUT_DIR = ROOT / "reports" / "runtime"

_SEVERITY_MARK = {
    "PASS": "OK  ",
    "WARN": "WARN",
    "FAIL": "FAIL",
    "UNKNOWN": "UNK ",
}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fail-closed runtime reality audit for a live SCP deployment.",
    )
    parser.add_argument(
        "--data-dir",
        default="",
        help="Live data directory to audit. Defaults to SCP_DATA_DIR or <repo>/data.",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Directory that receives the JSON evidence envelope.",
    )
    parser.add_argument(
        "--backend-url",
        default="http://127.0.0.1:8000",
        help="Base URL of the running SCP backend.",
    )
    parser.add_argument(
        "--loop-url",
        default="http://127.0.0.1:3030",
        help="Base URL of the running loop scheduler.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=5.0,
        help="Per-request timeout in seconds.",
    )
    parser.add_argument(
        "--no-hmac",
        action="store_true",
        help="Skip HMAC verification of the kernel journal (structural checks still run).",
    )
    parser.add_argument(
        "--admin-token",
        default="",
        help=(
            "Bearer token for protected backend endpoints so the audit can "
            "cross-check claims that require authentication. Defaults to "
            "SCP_ADMIN_KEY / SCP_AUTH_TOKEN_SECRET / SCP_SCHEDULER_ADMIN_TOKEN."
        ),
    )
    parser.add_argument(
        "--no-ledger",
        action="store_true",
        help="Do not persist the verdict into the runtime audit ledger.",
    )
    parser.add_argument(
        "--json-only",
        action="store_true",
        help="Print only the JSON report on stdout.",
    )
    return parser


def _service_targets(backend_url: str, loop_url: str) -> list[dict[str, Any]]:
    base = backend_url.rstrip("/")
    loop = loop_url.rstrip("/")
    return [
        {"name": "backend-health", "url": f"{base}/health"},
        {"name": "backend-ready", "url": f"{base}/ready"},
        {"name": "backend-audit-stats", "url": f"{base}/v105/audit/stats"},
        {"name": "backend-runtime-subsystems", "url": f"{base}/v105/runtime/subsystems"},
        {"name": "loop-scheduler", "url": f"{loop}/healthz"},
    ]


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    data_dir = Path(args.data_dir).expanduser() if args.data_dir.strip() else runtime_data_dir()
    data_dir = data_dir.resolve()

    journal_secret = "" if args.no_hmac else os.environ.get("SCP_JOURNAL_SECRET", "")  # [R5] K_journal

    runner = RuntimeAuditRunner(
        data_dir=data_dir,
        service_targets=_service_targets(args.backend_url, args.loop_url),
        journal_secret=journal_secret,
        admin_token=args.admin_token.strip() or None,
        http_timeout_sec=args.timeout,
    )

    verdict = runner.run()
    payload = verdict.to_dict()

    run_id = ""
    if not args.no_ledger:
        try:
            run_id = RuntimeAuditLedger(data_dir).record(verdict)
            payload["run_id"] = run_id
        except Exception as exc:  # noqa: BLE001 - a ledger failure must be visible, not silent
            payload["ledger_error"] = f"{type(exc).__name__}: {exc}"

    output_dir = Path(args.output).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    evidence_path = output_dir / f"runtime-audit-{stamp}.json"
    evidence_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    payload["evidence_path"] = str(evidence_path)

    if args.json_only:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print("=" * 78)
        print(f"SCP RUNTIME REALITY AUDIT - verdict: {verdict.verdict.value}")
        print("=" * 78)
        print(f"target   : {verdict.target}")
        print(f"data dir : {verdict.data_dir}")
        print(f"elapsed  : {verdict.elapsed_sec}s")
        counts = verdict.counts()
        print(f"findings : PASS={counts['PASS']} WARN={counts['WARN']} FAIL={counts['FAIL']} UNKNOWN={counts['UNKNOWN']}")
        print("-" * 78)
        for finding in verdict.findings:
            mark = _SEVERITY_MARK.get(finding.severity.value, "????")
            print(f"[{mark}] {finding.probe_id}")
            print(f"         {finding.title}")
            print(f"         {finding.summary}")
        print("-" * 78)
        print(f"evidence : {evidence_path}")
        if run_id:
            print(f"run id   : {run_id}")
        print("note     : PASS means no defect was OBSERVED inside the probed scope.")
        print("           It does not mean the system is correct.")

    if verdict.verdict == Verdict.FAIL:
        return 1
    if verdict.verdict == Verdict.UNKNOWN:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
