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
from tools import e2e_live_cluster_verifier
from tools.e2e_live_cluster_verifier import OccupiedPortError, clean_ports, port_clean_enabled, run_e2e_verification


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


class TestPortCleanGate:
    """SCP_SMOKE_PORT_CLEAN gate decision logic (no real processes touched)."""

    @pytest.mark.parametrize("raw,expected_enabled", [
        (None, True),          # default: historical force-clean preserved
        ("1", True),
        ("", True),            # empty -> default behavior
        ("true", True),
        ("0", False),          # refuse to kill
        ("false", False),
        ("off", False),
        ("no", False),
    ])
    def test_gate_decision(self, monkeypatch, raw, expected_enabled):
        if raw is None:
            monkeypatch.delenv("SCP_SMOKE_PORT_CLEAN", raising=False)
        else:
            monkeypatch.setenv("SCP_SMOKE_PORT_CLEAN", raw)
        assert port_clean_enabled() is expected_enabled

    def test_gate_enabled_keeps_killing_occupied_ports(self, monkeypatch):
        monkeypatch.setenv("SCP_SMOKE_PORT_CLEAN", "1")
        killed: list[int] = []
        monkeypatch.setattr(e2e_live_cluster_verifier, "get_listening_pids", lambda ports=None: {4242})
        monkeypatch.setattr(e2e_live_cluster_verifier, "kill_process_tree", lambda pid: killed.append(pid))
        monkeypatch.setattr(e2e_live_cluster_verifier.time, "sleep", lambda _s: None)
        clean_ports((8000, 8081, 3000))
        assert killed == [4242], "default gate must preserve the historical kill behavior"

    def test_gate_disabled_refuses_to_kill_and_names_pid(self, monkeypatch):
        monkeypatch.setenv("SCP_SMOKE_PORT_CLEAN", "0")
        killed: list[int] = []
        monkeypatch.setattr(e2e_live_cluster_verifier, "get_listening_pids", lambda ports=None: {4242, 5353})
        monkeypatch.setattr(e2e_live_cluster_verifier, "kill_process_tree", lambda pid: killed.append(pid))
        with pytest.raises(OccupiedPortError) as exc_info:
            clean_ports((8000, 8081, 3000))
        assert killed == [], "gate=0 must never kill a co-located listener"
        message = str(exc_info.value)
        assert "SCP_SMOKE_PORT_CLEAN=0" in message
        for pid in (4242, 5353):
            assert str(pid) in message, f"owning PID {pid} must appear in the error"

    def test_gate_disabled_is_noop_when_ports_free(self, monkeypatch):
        monkeypatch.setenv("SCP_SMOKE_PORT_CLEAN", "0")
        monkeypatch.setattr(e2e_live_cluster_verifier, "get_listening_pids", lambda ports=None: set())
        monkeypatch.setattr(
            e2e_live_cluster_verifier, "kill_process_tree",
            lambda pid: pytest.fail("kill must not be called when ports are free"),
        )
        clean_ports((8000, 8081, 3000))  # must not raise
