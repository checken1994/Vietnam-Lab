#!/usr/bin/env python3
"""
Hourly 24/7 SCP System Verification & Synthesis Tool.
Checks ports 8000, 3030, 8081, 3000, SQLite WAL integrity, loop runs count, and schtasks.
"""
import urllib.request
import json
import sqlite3
import os
import subprocess
import sys

def main():
    print("=== SCP 24/7 HOURLY SYSTEM AUDIT ===")
    
    # 1. Check Ports (4 Normative Services + Verify Port 8081 Closed)
    endpoints = [
        ("Port 8000 (Backend /ready)", "http://127.0.0.1:8000/ready"),
        ("Port 3030 (Loop Scheduler /healthz)", "http://127.0.0.1:3030/healthz"),
        ("Port 6080 (Agent Desktop /)", "http://127.0.0.1:6080/"),
        ("Port 3000 (Web Dashboard /)", "http://127.0.0.1:3000/"),
    ]
    for name, url in endpoints:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "SCP-Audit/1.0"})
            with urllib.request.urlopen(req, timeout=4) as resp:
                data = resp.read(250).decode("utf-8", errors="ignore")
                print(f"[OK] {name}: HTTP {resp.status} -> {data.strip()[:80]}")
        except Exception as e:
            print(f"[FAIL] {name}: {e}")

    # Port 8081 must be CLOSED (Zero-Bun compliance check)
    try:
        req8081 = urllib.request.Request("http://127.0.0.1:8081/health", headers={"User-Agent": "SCP-Audit/1.0"})
        with urllib.request.urlopen(req8081, timeout=2) as resp:
            print(f"[WARN] Port 8081 is unexpectedly OPEN (status {resp.status})")
    except Exception:
        print("[OK] Port 8081 (Decommissioned LLM Bridge): CLOSED as expected (Zero-Bun compliance)")


    # 2. Check Loop runs count
    loop_path = r"d:\scp\data\loop_runs.jsonl"
    if os.path.exists(loop_path):
        with open(loop_path, "r", encoding="utf-8") as f:
            runs = sum(1 for line in f if line.strip())
        print(f"\n[LOOP RUNS] {loop_path}: {runs} total cycles logged.")
    else:
        print(f"\n[LOOP RUNS] {loop_path} not found.")

    # 3. Check SQLite WAL and integrity
    dbs = [
        r"d:\scp\data\knowledge.sqlite3",
        r"d:\scp\data\ask_task_kernel.sqlite3",
        r"d:\scp\data\hands\task_kernel.sqlite3"
    ]
    print("\n[SQLITE WAL INTEGRITY]")
    for db in dbs:
        if os.path.exists(db):
            conn = sqlite3.connect(db)
            cur = conn.cursor()
            mode = cur.execute("PRAGMA journal_mode;").fetchone()[0]
            integrity = cur.execute("PRAGMA integrity_check;").fetchone()[0]
            # Count rows
            tables = cur.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()
            row_counts = {}
            for t in tables:
                tname = t[0]
                if not tname.startswith("sqlite_"):
                    cnt = cur.execute(f"SELECT COUNT(*) FROM \"{tname}\";").fetchone()[0]
                    row_counts[tname] = cnt
            size = os.path.getsize(db)
            print(f"- {os.path.basename(db)}: size={size}B, journal_mode={mode}, integrity={integrity}, tables={row_counts}")
            conn.close()
        else:
            print(f"- {db}: NOT FOUND")

    # 4. Check Windows Task Scheduler
    print("\n[WINDOWS TASK SCHEDULER]")
    for tname in ["SCP-247-Supervisor", "SCP-247-Recovery-Watchdog"]:
        try:
            out = subprocess.check_output(f'schtasks /query /tn "{tname}" /fo list', shell=True).decode("utf-8", errors="ignore")
            for line in out.splitlines():
                if any(k in line for k in ["TaskName:", "Status:", "Next Run Time:"]):
                    print(f"  {line.strip()}")
        except Exception as e:
            print(f"  {tname}: ERROR {e}")

if __name__ == "__main__":
    main()
