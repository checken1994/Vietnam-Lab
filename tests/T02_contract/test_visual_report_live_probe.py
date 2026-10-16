"""
Contract test: Live probe verification in Visual Report Generator.

Enforces:
  - FA-04: KHÔNG tạo simulated/manufactured VERIFIED.
  - FA-08: KHÔNG tự tạo bằng chứng (No Forged Provenance).
  - DNA #1: Reality > Model (Thực tế > Mô hình).
  - Anti-placebo negative tests: closed ports MUST report OFFLINE;
    missing tasks MUST report NOT_FOUND (win32) or NOT_APPLICABLE (non-win32).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from tools.generate_visual_reports import PhysicalDataAggregator, VisualReportGenerator


def test_probe_http_endpoint_offline_on_closed_port():
    """Negative test: probing closed port MUST fail closed and report OFFLINE, never 200 OK."""
    # Port 9999 is closed in our local test environment
    result = PhysicalDataAggregator.probe_http_endpoint("ClosedPortTest", "http://127.0.0.1:9999/health", timeout=0.5)
    assert result["online"] is False, "Closed port must NOT report online=True"
    assert "200" not in result["status_line"], "Closed port must NEVER report HTTP 200 OK"
    assert "OFFLINE" in result["status_line"] or "ERROR" in result["status_line"]


def test_probe_task_scheduler_non_existent_task():
    """Negative test: probing non-existent task MUST fail closed, never claim Ready."""
    result = PhysicalDataAggregator.probe_task_scheduler_task("SCP-NON-EXISTENT-TASK-99999", timeout=1.5)
    if sys.platform == "win32":
        assert result["state"] == "NOT_FOUND", "Non-existent Windows task must report NOT_FOUND"
        assert "Ready" not in result["status_line"], "Missing task must NEVER report Ready"
    else:
        assert result["state"] == "NOT_APPLICABLE", "Non-Windows platform must report NOT_APPLICABLE"
        assert result["supported"] is False


def test_sqlite_metrics_live_integrity_check():
    """Verify load_sqlite_metrics runs real PRAGMA integrity_check."""
    metrics = PhysicalDataAggregator.load_sqlite_metrics()
    assert "wal_integrity" in metrics
    wal_val = metrics["wal_integrity"]
    assert wal_val != "unknown", "wal_integrity must be dynamically probed, not unknown"
    # In live repo, at least knowledge.sqlite3 exists
    assert "knowledge:" in wal_val


def test_visual_report_generator_contains_live_probe_evidence():
    """Verify generated report contains live probe section rather than hardcoded 200 OK strings."""
    gen = VisualReportGenerator(cadence="daily")
    report_path = gen.generate()
    assert report_path.exists()
    content = report_path.read_text(encoding="utf-8")
    assert "## 5. HẠ TẦNG VÀ TIẾN TRÌNH VẬN HÀNH 24/7 (ĐO ĐẠC THỜI GIAN THỰC)" in content
    assert "Live HTTP Socket/Endpoint Probe" in content
    assert "OS Scheduler Probe" in content
    # Ensure it reports actual loop success rate calculation
    assert "Tỷ lệ:" in content
