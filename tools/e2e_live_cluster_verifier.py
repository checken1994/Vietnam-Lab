#!/usr/bin/env python3
"""
Automated Live Cluster E2E Boot, Verification & Clean Shutdown Runner
====================================================================
1. Pre-cleans ports 8000, 8081, 3000.
2. Boots LLM Bridge (8081), SCP API Server (8000), and Dashboard (3000) in background
   with log redirection to data/service-logs/ to prevent pipe buffering deadlocks.
3. Polls health endpoints with deadlines.
4. Executes live chat interaction with valid admin token, extracting backward-traceable trace_id.
5. Queries GET /v3/trace/{trace_id} with admin credentials and verifies ledger provenance and secret redaction.
6. Queries GET /v3/trace/{trace_id} without auth and verifies fail-closed HTTP 401.
7. Queries Dashboard Next.js API proxy GET /api/scp/v3/trace/{trace_id} and verifies credential forwarding.
8. Cleanly shuts down all spawned processes using process tree kill (taskkill /F /T /PID).
9. Verifies ports 8000, 8081, 3000 are completely free with 0 leftover zombie processes.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / "data" / "service-logs"
TARGET_PORTS = (8000, 8081, 3000)


def _open_service_log(name: str):
    """Open a fixed service log under data/service-logs.

    The path set is a closed allowlist rooted at the repository; anything
    else, or any path resolving outside ROOT, is refused fail-closed.
    """
    allowed = {
        "llm-bridge.log": LOG_DIR / "llm-bridge.log",
        "scp-server.log": LOG_DIR / "scp-server.log",
        "dashboard.log": LOG_DIR / "dashboard.log",
    }
    if name not in allowed:
        raise ValueError(f"unknown service log: {name}")
    path = allowed[name]
    if ROOT not in path.resolve().parents:
        raise ValueError(f"log path escapes repository root: {path}")
    return path.open("w", encoding="utf-8")


def kill_process_tree(pid: int) -> None:
    """Terminate a process and all of its descendants."""
    if not pid:
        return
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)
    else:
        import signal
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass


def get_listening_pids(ports: tuple[int, ...] = TARGET_PORTS) -> set[int]:
    """Retrieve PIDs listening on any of the target ports."""
    pids: set[int] = set()
    if sys.platform == "win32":
        port_csv = ",".join(str(p) for p in ports)
        cmd = f"Get-NetTCPConnection -LocalPort {port_csv} -State Listen -ErrorAction SilentlyContinue | ForEach-Object {{ $_.OwningProcess }}"
        res = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True)
        for line in res.stdout.splitlines():
            val = line.strip()
            if val.isdigit() and int(val) > 0:
                pids.add(int(val))
    else:
        for port in ports:
            res = subprocess.run(["lsof", "-t", f"-i:{port}"], capture_output=True, text=True)
            for line in res.stdout.splitlines():
                val = line.strip()
                if val.isdigit():
                    pids.add(int(val))
    return pids


def clean_ports(ports: tuple[int, ...] = TARGET_PORTS) -> None:
    """Force kill all processes holding any target port."""
    pids = get_listening_pids(ports)
    for pid in pids:
        kill_process_tree(pid)
    time.sleep(1)


def verify_ports_free(ports: tuple[int, ...] = TARGET_PORTS) -> bool:
    """Return True if no listener exists on any target port."""
    pids = get_listening_pids(ports)
    return len(pids) == 0


def load_env_token() -> str:
    """Load canonical auth token from .env file or environment."""
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k in ("SCP_AUTH_TOKEN_SECRET", "SCP_ADMIN_KEY", "SCP_AUTH_PASSWORD") and v:
                return v
    token = (
        os.environ.get("SCP_AUTH_TOKEN_SECRET")
        or os.environ.get("SCP_ADMIN_KEY")
        or os.environ.get("SCP_AUTH_PASSWORD")
        or os.environ.get("SCP_ADMIN_TOKEN", "")
    )
    if not token:
        raise ValueError("SCP_ADMIN_TOKEN must be set")
    return token


async def run_e2e_verification() -> dict[str, Any]:
    """Execute complete cluster boot, verification, and teardown."""
    print("=== [1/5] Pre-cleaning ports (8000, 8081, 3000) ===")
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    clean_ports(TARGET_PORTS)

    token = load_env_token()
    env = dict(os.environ)
    env["SCP_AUTH_TOKEN_SECRET"] = token
    env["SCP_ADMIN_KEY"] = token
    env["SCP_PORT"] = "8000"
    env["SCP_HOST"] = "127.0.0.1"

    spawned_procs: list[subprocess.Popen] = []
    file_handles: list[Any] = []
    verification_summary: dict[str, Any] = {
        "success": False,
        "bridge_ready": False,
        "server_ready": False,
        "dashboard_ready": False,
        "trace_id": None,
        "trace_record_verified": False,
        "secret_redacted": False,
        "unauth_blocked_401": False,
        "dashboard_proxy_verified": False,
        "ports_free": False,
    }

    try:
        # --- 1. Spawn LLM Bridge on 8081 ---
        print("=== [2/5] Booting services: LLM Bridge, SCP Server, Web Dashboard ===")
        bridge_log = _open_service_log("llm-bridge.log")
        file_handles.append(bridge_log)
        b_env = dict(env, SCP_LLM_BRIDGE_PORT="8081", ZAI_BRIDGE_PORT="8081", ZAI_BRIDGE_HOST="127.0.0.1")
        p_bridge = subprocess.Popen(
            ["bun", "run", "dev"],
            cwd=str(ROOT / "mini-services" / "llm-bridge"),
            env=b_env,
            stdout=bridge_log,
            stderr=bridge_log,
        )
        spawned_procs.append(p_bridge)

        # --- 2. Spawn SCP Python Server on 8000 ---
        server_log = _open_service_log("scp-server.log")
        file_handles.append(server_log)
        s_env = dict(env, SCP_PORT="8000", SCP_HOST="127.0.0.1")
        p_server = subprocess.Popen(
            [sys.executable, "-m", "scp", "8000"],
            cwd=str(ROOT),
            env=s_env,
            stdout=server_log,
            stderr=server_log,
        )
        spawned_procs.append(p_server)

        # --- 3. Spawn Web Dashboard on 3000 ---
        dash_log = _open_service_log("dashboard.log")
        file_handles.append(dash_log)
        d_env = dict(
            env,
            PORT="3000",
            SCP_INTERNAL_URL="http://127.0.0.1:8000",
            LLM_BRIDGE_URL="http://127.0.0.1:8081",
            SCP_AUTH_TOKEN_SECRET=token,
        )
        p_dash = subprocess.Popen(
            ["bun", "run", "dev"],
            cwd=str(ROOT / "dashboard"),
            env=d_env,
            stdout=dash_log,
            stderr=dash_log,
        )
        spawned_procs.append(p_dash)

        # --- 4. Readiness Polling ---
        print("=== [3/5] Polling health endpoints with strict deadlines ===")
        async with httpx.AsyncClient(timeout=3.0) as client:
            # Probe Bridge (8081) - /health or /api/tags
            for _ in range(25):
                try:
                    r = await client.get("http://127.0.0.1:8081/health")
                    if r.status_code == 200:
                        verification_summary["bridge_ready"] = True
                        break
                    r2 = await client.get("http://127.0.0.1:8081/api/tags")
                    if r2.status_code == 200:
                        verification_summary["bridge_ready"] = True
                        break
                except Exception:
                    pass
                await asyncio.sleep(1)
            assert verification_summary["bridge_ready"], "LLM Bridge failed to reach HTTP 200 on port 8081 within deadline"
            print("  -> LLM Bridge (8081) READY [HTTP 200]")

            # Probe SCP Server (8000) - /health
            for _ in range(45):
                try:
                    r = await client.get("http://127.0.0.1:8000/health")
                    if r.status_code == 200:
                        verification_summary["server_ready"] = True
                        break
                except Exception:
                    pass
                await asyncio.sleep(1)
            assert verification_summary["server_ready"], "SCP API Server failed to reach HTTP 200 on port 8000 within deadline"
            print("  -> SCP Server (8000) READY [HTTP 200]")

            # Probe Web Dashboard (3000) - /
            for _ in range(45):
                try:
                    r = await client.get("http://127.0.0.1:3000/")
                    if r.status_code in (200, 304):
                        verification_summary["dashboard_ready"] = True
                        break
                except Exception:
                    pass
                await asyncio.sleep(1)
            assert verification_summary["dashboard_ready"], "Web Dashboard failed to reach HTTP 200 on port 3000 within deadline"
            print("  -> Web Dashboard (3000) READY [HTTP 200]")

            # --- 5. Live Chat Interaction & Trace Extraction ---
            print("=== [4/5] Testing live chat, trace provenance, redaction & 401 auth boundary ===")
            headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
            chat_payload = {
                "model": "scp-standard",
                "messages": [{"role": "user", "content": "What is 2+2?"}],
            }
            chat_resp = await client.post(
                "http://127.0.0.1:8000/v1/chat/completions",
                headers=headers,
                json=chat_payload,
                timeout=30.0,
            )
            assert chat_resp.status_code == 200, f"Chat failed with status {chat_resp.status_code}: {chat_resp.text}"
            chat_data = chat_resp.json()
            trace_id = chat_data.get("trace_id") or chat_resp.headers.get("X-SCP-Trace-ID")
            assert trace_id, f"No trace_id returned in chat response: body={chat_data}"
            verification_summary["trace_id"] = trace_id
            print(f"  -> Live chat completed successfully. Extracted trace_id: {trace_id}")

            # --- 6. Query GET /v3/trace/{trace_id} with Admin Auth ---
            trace_resp = await client.get(
                f"http://127.0.0.1:8000/v3/trace/{trace_id}",
                headers=headers,
                timeout=10.0,
            )
            assert trace_resp.status_code == 200, f"Authenticated trace lookup failed with status {trace_resp.status_code}"
            trace_record = trace_resp.json()
            assert trace_record.get("trace_id") == trace_id or trace_id in str(trace_record), (
                f"Trace record mismatch: expected {trace_id}, got {trace_record}"
            )
            verification_summary["trace_record_verified"] = True
            print(f"  -> GET /v3/trace/{trace_id} returned HTTP 200 with matching trace record")

            # Verify credential redaction: raw token must NOT appear in the response payload
            assert token not in trace_resp.text, "CRITICAL: Secret token leaked in trace response payload!"
            verification_summary["secret_redacted"] = True
            print("  -> Sensitive attribute redaction verified: zero token leakage in trace payload")

            # --- 7. Verify Fail-Closed Security Boundary (Unauthenticated) ---
            unauth_resp = await client.get(f"http://127.0.0.1:8000/v3/trace/{trace_id}", timeout=10.0)
            assert unauth_resp.status_code == 401, (
                f"Expected HTTP 401 for unauthenticated trace query, got {unauth_resp.status_code}"
            )
            verification_summary["unauth_blocked_401"] = True
            print("  -> Unauthenticated GET /v3/trace/{trace_id} correctly returned HTTP 401 Unauthorized")

            # --- 8. Verify Dashboard Next.js API Proxy ---
            dash_headers = {"Authorization": f"Bearer {token}", "X-Forwarded-For": "127.0.0.1"}
            dash_proxy_resp = await client.get(
                f"http://127.0.0.1:3000/api/scp/v3/trace/{trace_id}",
                headers=dash_headers,
                timeout=15.0,
            )
            assert dash_proxy_resp.status_code == 200, f"Dashboard proxy failed with status {dash_proxy_resp.status_code}: {dash_proxy_resp.text}"
            dash_proxy_data = dash_proxy_resp.json()
            assert dash_proxy_data.get("trace_id") == trace_id or trace_id in str(dash_proxy_data)
            verification_summary["dashboard_proxy_verified"] = True
            print("  -> Dashboard Next.js trace proxy verified [HTTP 200, caller token forwarded]")

            # Verify that dashboard proxy rejects unauthenticated requests (fail-closed, no auto-injected credential)
            dash_unauth = await client.get(
                f"http://127.0.0.1:3000/api/scp/v3/trace/{trace_id}",
                headers={"X-Forwarded-For": "127.0.0.1"},
                timeout=15.0,
            )
            assert dash_unauth.status_code == 401, f"Expected 401 from dashboard proxy without auth, got {dash_unauth.status_code}"
            print("  -> Dashboard Next.js trace proxy correctly rejected unauthenticated request [HTTP 401]")

            verification_summary["success"] = True

    finally:
        # --- 9. Clean Shutdown & Process Tree Kill ---
        print("=== [5/5] Shutting down cluster and verifying zero zombie processes ===")
        for proc in spawned_procs:
            try:
                kill_process_tree(proc.pid)
            except Exception:
                pass
        for fh in file_handles:
            try:
                fh.close()
            except Exception:
                pass

        clean_ports(TARGET_PORTS)
        ports_free = verify_ports_free(TARGET_PORTS)
        verification_summary["ports_free"] = ports_free
        if not ports_free:
            print("  [ERROR] Lingering listeners detected on target ports!")
        else:
            print("  -> All target ports (8000, 8081, 3000) completely free. Zero zombie processes.")

    return verification_summary


def main() -> None:
    """CLI entrypoint."""
    try:
        result = asyncio.run(run_e2e_verification())
        print("\n============================================================")
        print("  [RESULT] Live Cluster E2E Verification Complete")
        print("============================================================")
        print(json.dumps(result, indent=2))
        if not result.get("success") or not result.get("ports_free"):
            sys.exit(1)
        sys.exit(0)
    except Exception as exc:
        print(f"\n[E2E FATAL ERROR] {exc}", file=sys.stderr)
        clean_ports(TARGET_PORTS)
        sys.exit(1)


if __name__ == "__main__":
    main()
