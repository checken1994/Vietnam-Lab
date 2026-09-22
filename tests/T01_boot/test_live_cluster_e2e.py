"""Live Cluster End-to-End Boot, Verification & Clean Shutdown Test.

Spawns the full live cluster:
- LLM Bridge on port 8081
- SCP API Server on port 8000
- Web Dashboard on port 3000

Verifies:
1. Health readiness on all 3 services.
2. Backward-traceable chat interaction yielding trace_id.
3. Authenticated trace retrieval with sensitive secret redaction.
4. Fail-closed HTTP 401 when queried without credentials.
5. Complete process termination with zero leftover zombie processes.
"""
from __future__ import annotations

import pytest
from tools.e2e_live_cluster_verifier import run_e2e_verification


@pytest.mark.asyncio
async def test_live_cluster_e2e_boot_verify_and_shutdown():
    """Verify live cluster lifecycle end-to-end."""
    result = await run_e2e_verification()
    assert result["success"] is True, f"Live cluster E2E verification failed: {result}"
    assert result["bridge_ready"] is True
    assert result["server_ready"] is True
    assert result["dashboard_ready"] is True
    assert result["trace_id"], "No trace_id extracted from live chat"
    assert result["trace_record_verified"] is True
    assert result["secret_redacted"] is True
    assert result["unauth_blocked_401"] is True
    assert result["ports_free"] is True, "Target ports 8000, 8081, 3000 were not cleanly released!"
