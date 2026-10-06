"""R5 kernel-integrity probe (READ-ONLY): cross-check the 3 real /ask calls of
this audit against (a) the physical SQLite kernel DB and (b) the unified
hash-chained trace ledger.

Runs observed over HTTP earlier in this audit (same boot sessions):
  - live_flow_proof 8090 normal   -> verdict PASS  governance UPHOLD  (task COMPLETED expected)
  - live_flow_proof 8091 deny     -> verdict FAIL  governance ESCALATE (task HUMAN_REVIEW expected)
  - injection probe 8092          -> verdict FAIL  governance ESCALATE (task HUMAN_REVIEW expected)

No secret values are loaded into memory except via dotenv (never printed).
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env")

from scp.trace_ledger import TraceLedger  # noqa: E402

LEDGER = ROOT / "data" / "trace_ledger.jsonl"
KERNEL_DB = ROOT / "data" / "ask_task_kernel.sqlite3"
RUN_IDS = [
    "run-aa34bbf4eaaf4620a4131f9f972ded4b",  # normal golden
    "run-676aca17dab041b6bf10dbb2f375ce46",  # egress-deny
    "run-d5eb79ac32fe49b883415681bf7f6530",  # prompt-injection
]

print("== A. trace ledger hash chain (product verifier, report-only) ==")
led = TraceLedger(LEDGER, verify_on_init=False)
report = led.verify()
for k in ("entries", "active_segment_entries", "hash_chain_valid", "history_valid",
          "recovered", "boot_skipped"):
    print(f"  {k:24s} = {report.get(k)}")
errs = report.get("errors") or []
print(f"  active_segment_errors   = {errs[:3]}{'...' if len(errs) > 3 else ''}")

print("== B. ledger entries for the 3 audited run_ids ==")
found = 0
for line in LEDGER.read_text(encoding="utf-8", errors="replace").splitlines():
    if not any(r in line for r in RUN_IDS):
        continue
    try:
        e = json.loads(line)
    except Exception:
        print("  [unparsable line with run_id — parse error]")
        continue
    if e.get("type") in ("CHAIN_RECOVERY",):
        continue
    found += 1
    keys = ("seq", "type", "run_id", "task_id", "final_verdict", "final_governance",
            "final_outcome", "disposition")
    print("  " + json.dumps({k: e.get(k) for k in keys if e.get(k) is not None})[:260])
print(f"  ledger entries found: {found}")

print("== C. physical SQLite kernel ==")
db = sqlite3.connect(f"file:{KERNEL_DB.as_posix()}?mode=ro", uri=True)
try:
    print("  integrity_check:", db.execute("PRAGMA integrity_check").fetchone()[0])
    n_tasks = db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
    n_events = db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    print(f"  totals: tasks={n_tasks} events={n_events}")
    print("  -- tasks created during this audit window (2026-10-06T16:2x-16:4x UTC) --")
    rows = db.execute(
        "SELECT task_id, state, created_at FROM tasks "
        "WHERE created_at >= '2026-10-06T16:20' ORDER BY rowid").fetchall()
    cols = [c[1] for c in db.execute("PRAGMA table_info(events)")]
    id_col = "task_id" if "task_id" in cols else cols[0]
    kind_col = "event_type" if "event_type" in cols else ("type" if "type" in cols else cols[1])
    for tid, state, created in rows:
        evs = db.execute(f"SELECT {kind_col} FROM events WHERE {id_col}=? ORDER BY rowid",
                         (tid,)).fetchall()
        chain = " -> ".join(r[0] for r in evs)
        print(f"  {tid} state={state} created={created}")
        print(f"      events({len(evs)}): {chain}")
finally:
    db.close()
