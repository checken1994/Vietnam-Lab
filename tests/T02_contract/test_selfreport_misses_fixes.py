"""
Contract & Anti-Placebo Tests for the 3 Self-Report Misses Fixes (N1, N2, N4):
  - Item 2a (N1): AST scanner cap 100/811 stratified priority + deterministic rotation + coverage metadata.
  - Item 2b (N4): All 5 Windows Scheduled Tasks probed and reported honestly (Disabled / ALL_DISABLED).
  - Item 2c (N2/N4): Daily report loop_runs window filtering & STALE detection, missing deep_audit_results.jsonl
    UNKNOWN reporting, subsystem_heartbeat.sqlite integrity check, and true UTC+7 timestamp conversion.
"""

from __future__ import annotations

import datetime
import json
import sys
from pathlib import Path

import pytest

from scp.autofix.runner_phases.ast_scan import (
    _SCP_ROOT,
    CRITICAL_SCAN_DIRS,
    _iter_python_files,
    _top_level_subpackage,
    get_scan_coverage_metadata,
)
from tools.generate_visual_reports import (
    REPORTS_DIR,
    SCP_WINDOWS_TASKS,
    TZ_UTC7,
    PhysicalDataAggregator,
    VisualReportGenerator,
)


# ==============================================================================
# ITEM 2a — Scanner cap 100/811 (N1)
# ==============================================================================


def test_ast_scan_stratified_priority_covers_critical_dirs_in_every_window():
    """Every 100-file rotation window on real scp/ MUST include all 6 critical directories."""
    cov0 = get_scan_coverage_metadata(root=_SCP_ROOT, limit=100, rotation_index=0)
    total_windows = cov0["total_windows"]
    assert cov0["total_eligible"] > 500
    assert total_windows >= 6

    for win in range(total_windows):
        selected = _iter_python_files(_SCP_ROOT, limit=100, rotation_index=win)
        assert len(selected) == 100
        # Must remain deterministically sorted by path within each window
        assert selected == sorted(selected)
        subdirs = {_top_level_subpackage(p, _SCP_ROOT) for p in selected}
        for crit_dir in CRITICAL_SCAN_DIRS:
            assert crit_dir in subdirs, (
                f"Critical directory '{crit_dir}' starved (0 files) in window {win}/{total_windows}"
            )


def test_ast_scan_rotation_covers_100_percent_of_eligible_files():
    """Rotating across all windows MUST cover 100% of eligible .py files in scp/."""
    all_eligible = _iter_python_files(_SCP_ROOT, limit=10000)
    cov0 = get_scan_coverage_metadata(root=_SCP_ROOT, limit=100, rotation_index=0)
    total_windows = cov0["total_windows"]

    visited: set[Path] = set()
    for win in range(total_windows):
        batch = _iter_python_files(_SCP_ROOT, limit=100, rotation_index=win)
        visited.update(batch)

    assert len(visited) == len(all_eligible), (
        f"Rotation across {total_windows} windows covered {len(visited)}/{len(all_eligible)} files"
    )
    assert visited == set(all_eligible)


def test_ast_scan_coverage_metadata_anti_placebo():
    """Negative test: sampled scan MUST report is_full_coverage=False and accurate ratio; limit<=0 returns 0."""
    sampled_cov = get_scan_coverage_metadata(root=_SCP_ROOT, limit=100, rotation_index=0)
    assert sampled_cov["scanned_count"] == 100
    assert sampled_cov["total_eligible"] > 500
    assert sampled_cov["is_full_coverage"] is False
    assert 0.0 < sampled_cov["coverage_ratio"] < 0.5
    assert all(cnt > 0 for cnt in sampled_cov["critical_dirs_scanned"].values())

    zero_cov = get_scan_coverage_metadata(root=_SCP_ROOT, limit=0, rotation_index=0)
    assert zero_cov["scanned_count"] == 0
    assert zero_cov["coverage_ratio"] == 0.0
    assert zero_cov["is_full_coverage"] is False


# ==============================================================================
# ITEM 2b — 5 Windows Scheduled Tasks Disabled (N4)
# ==============================================================================


def test_probe_live_infrastructure_includes_all_5_scp_windows_tasks():
    """Infrastructure probe MUST inspect all 5 SCP Windows Scheduled Tasks and report disposition."""
    expected_names = {
        "SCP-247-Supervisor",
        "SCP-247-Recovery-Watchdog",
        "SCP-247-Visual-Reports",
        "SCP-System-Reporter-247",
        "SCP-Public-Relay-Bridge",
    }
    configured_names = {task_name for _, task_name, _ in SCP_WINDOWS_TASKS}
    assert configured_names == expected_names

    infra = PhysicalDataAggregator.probe_live_infrastructure()
    assert infra["total_task_count"] == 5
    probed_names = {v["task_name"] for v in infra["tasks"].values()}
    assert probed_names == expected_names
    for task_info in infra["tasks"].values():
        assert task_info.get("disposition"), "Each task must carry a non-empty disposition explanation"

    if sys.platform == "win32":
        # On the live Windows host, all 5 legacy tasks are Disabled (or NOT_FOUND if removed)
        assert infra["active_task_count"] == 0
        assert "ALL_DISABLED" in infra["scheduler_verdict"]


def test_ops_control_and_teardown_scripts_include_all_5_tasks():
    """Verify scp_247_control.ps1 and scp_247_teardown.ps1 list all 5 SCP tasks."""
    repo_root = Path(__file__).resolve().parent.parent.parent
    control_text = (repo_root / "scripts" / "ops" / "scp_247_control.ps1").read_text(encoding="utf-8")
    teardown_text = (repo_root / "scripts" / "ops" / "scp_247_teardown.ps1").read_text(encoding="utf-8")

    for task_name in (
        "SCP-247-Supervisor",
        "SCP-247-Recovery-Watchdog",
        "SCP-247-Visual-Reports",
        "SCP-System-Reporter-247",
        "SCP-Public-Relay-Bridge",
    ):
        assert task_name in control_text, f"{task_name} missing from scp_247_control.ps1"
        assert task_name in teardown_text, f"{task_name} missing from scp_247_teardown.ps1"


# ==============================================================================
# ITEM 2c — Daily Report Honesty: deep_audit UNKNOWN, loop_runs window/STALE,
#           subsystem_heartbeat.sqlite integrity, UTC+7 timezone (N2 & N4)
# ==============================================================================


def test_load_deep_audit_results_missing_file_reports_unknown_not_zero(tmp_path):
    """Negative test (DNA #22): missing deep_audit_results.jsonl MUST return UNKNOWN, never 0 errors."""
    findings, summary = PhysicalDataAggregator.load_deep_audit_results(since_ts=0.0, data_dir=tmp_path)
    assert findings == []
    assert summary["source_exists"] is False
    assert summary["status"] == "UNKNOWN"
    assert summary["total_findings"] is None
    assert "UNKNOWN" in summary["status_reason"]

    # Generating report against empty data_dir must print UNKNOWN, not "Không có lỗi AST phát sinh | 0"
    out_dir = tmp_path / "reports"
    gen = VisualReportGenerator(cadence="daily", reports_dir=out_dir, data_dir=tmp_path)
    md_path = gen.generate()
    content = md_path.read_text(encoding="utf-8")
    assert "UNKNOWN" in content
    assert "| _Không có lỗi AST phát sinh_ | 0 |" not in content


def test_load_loop_runs_stats_filters_by_window_and_flags_stale(tmp_path):
    """Stale entries from 5 days ago MUST report 0 cycles in today's 24h window and status=STALE."""
    loop_file = tmp_path / "loop_runs.jsonl"
    stale_lines = [
        json.dumps({
            "ts": f"2026-10-26T19:{m:02d}:20.709Z",
            "scp_online": False,
            "status": "scp_offline",
            "triggered_by": "cron",
            "duration_ms": 3000,
        })
        for m in (0, 5, 13, 18, 23, 28)
    ]
    loop_file.write_text("\n".join(stale_lines) + "\n", encoding="utf-8")

    now_dt = datetime.datetime(2026, 10, 31, 12, 0, 0, tzinfo=TZ_UTC7)
    since_dt = now_dt - datetime.timedelta(days=1)

    stats = PhysicalDataAggregator.load_loop_runs_stats(
        since_ts=since_dt.timestamp(),
        now_ts=now_dt.timestamp(),
        data_dir=tmp_path,
    )
    assert stats["total_runs"] == 0, "Stale runs from 5 days ago must NOT be counted in today's 24h window"
    assert stats["ok_runs"] == 0
    assert stats["error_runs"] == 0
    assert stats["success_rate_pct"] == 0.0, "0 runs must NEVER report 100.0% success"
    assert stats["all_time_total_runs"] == 6
    assert stats["all_time_error_runs"] == 6
    assert stats["last_cycle_at"] == "2026-10-26T19:28:20.709Z"
    assert stats["is_stale"] is True
    assert stats["status"] == "STALE"

    # Positive check: add 4 fresh runs inside the 24h window (3 ok, 1 error)
    fresh_lines = stale_lines + [
        json.dumps({"ts": "2026-10-31T01:00:00Z", "status": "ok", "duration_ms": 120}),
        json.dumps({"ts": "2026-10-31T02:00:00Z", "status": "ok", "duration_ms": 110}),
        json.dumps({"ts": "2026-10-31T03:00:00Z", "status": "scp_offline", "duration_ms": 3000}),
        json.dumps({"ts": "2026-10-31T04:00:00Z", "status": "ok", "duration_ms": 130}),
    ]
    loop_file.write_text("\n".join(fresh_lines) + "\n", encoding="utf-8")

    fresh_stats = PhysicalDataAggregator.load_loop_runs_stats(
        since_ts=since_dt.timestamp(),
        now_ts=now_dt.timestamp(),
        data_dir=tmp_path,
    )
    assert fresh_stats["total_runs"] == 4
    assert fresh_stats["ok_runs"] == 3
    assert fresh_stats["error_runs"] == 1
    assert fresh_stats["success_rate_pct"] == 75.0
    assert fresh_stats["all_time_total_runs"] == 10
    assert fresh_stats["is_stale"] is False
    assert fresh_stats["status"] == "ACTIVE"


def test_load_sqlite_metrics_detects_malformed_heartbeat_db(tmp_path):
    """Negative test: corrupted subsystem_heartbeat.sqlite MUST be flagged FAIL/MALFORMED, never ignored."""
    bad_hb = tmp_path / "subsystem_heartbeat.sqlite"
    bad_hb.write_bytes(b"SQLite format 3\x00CORRUPTED_HEADER_BYTES_NOT_A_VALID_DB")

    metrics = PhysicalDataAggregator.load_sqlite_metrics(data_dir=tmp_path)
    assert "subsystem_heartbeat:FAIL/MALFORMED" in metrics["wal_integrity"]
    assert metrics["heartbeat_db_status"].startswith("FAIL/MALFORMED")
    assert metrics["all_sqlite_ok"] is False


def test_visual_report_utc7_timezone_conversion_and_pytest_isolation(tmp_path):
    """UTC timestamp 14:07:46Z MUST be rendered as 21:07:46 (UTC+7) in both MD and KPI SVG, and default pytest run is isolated."""
    utc_time = datetime.datetime(2026, 10, 30, 14, 7, 46, tzinfo=datetime.timezone.utc)
    out_dir = tmp_path / "out"
    gen = VisualReportGenerator(
        cadence="daily",
        reports_dir=out_dir,
        data_dir=tmp_path,
        now=utc_time,
    )
    md_path = gen.generate()
    content = md_path.read_text(encoding="utf-8")
    assert "2026-10-30 21:07:46 (UTC+7)" in content
    assert "2026-10-30 14:07:46 (UTC+7)" not in content

    kpi_svg_path = out_dir / "assets" / "kpi_daily_2026-10-30.svg"
    assert kpi_svg_path.exists()
    svg_text = kpi_svg_path.read_text(encoding="utf-8")
    assert "2026-10-30 21:07:46 (UTC+7)" in svg_text

    # Verify default VisualReportGenerator under pytest does NOT write into live REPORTS_DIR
    default_gen = VisualReportGenerator(cadence="daily")
    assert default_gen.reports_dir != REPORTS_DIR
    assert default_gen.origin == "pytest"


def test_ast_scan_small_window_limit_50_still_includes_all_critical_dirs_and_full_union():
    """Adversarial check: even when total_windows (17) exceeds len(hands)=9, every window includes all 6 critical dirs."""
    all_eligible = _iter_python_files(_SCP_ROOT, limit=10000)
    cov0 = get_scan_coverage_metadata(root=_SCP_ROOT, limit=50, rotation_index=0)
    total_windows = cov0["total_windows"]
    assert total_windows >= 17

    visited: set[Path] = set()
    for win in range(total_windows):
        selected = _iter_python_files(_SCP_ROOT, limit=50, rotation_index=win)
        assert len(selected) == 50
        assert len(set(selected)) == 50, "Window must not contain duplicate paths"
        subdirs = {_top_level_subpackage(p, _SCP_ROOT) for p in selected}
        for crit_dir in CRITICAL_SCAN_DIRS:
            assert crit_dir in subdirs, (
                f"Critical directory '{crit_dir}' missing at limit=50 in window {win}/{total_windows}"
            )
        visited.update(selected)

    assert visited == set(all_eligible)


def test_enterprise_scanner_respects_rotation_and_priority(monkeypatch):
    """Verify scan_scp_enterprise uses _iter_python_files with rotation_index rather than unrotated rglob."""
    import scp.autofix.enterprise_scanners as ent

    recorded_paths: list[Path] = []
    monkeypatch.setattr(ent, "scan_file_enterprise", lambda p: (recorded_paths.append(p) or []))

    ent.scan_scp_enterprise(max_files=50, rotation_index=0)
    win0 = list(recorded_paths)
    recorded_paths.clear()

    ent.scan_scp_enterprise(max_files=50, rotation_index=5)
    win5 = list(recorded_paths)

    assert len(win0) == 50
    assert len(win5) == 50
    assert win0 != win5, "Enterprise scanner must rotate file selection across windows"


def test_load_loop_runs_stats_upper_bound_and_corrupt_timestamp_rejection(tmp_path):
    """Negative test: future entries after now_ts and corrupt timestamps MUST NOT leak into window counts."""
    loop_file = tmp_path / "loop_runs.jsonl"
    lines = [
        json.dumps({"ts": "2026-10-27T04:00:00Z", "status": "ok", "duration_ms": 100}),
        json.dumps({"ts": "2026-10-31T04:00:00Z", "status": "ok", "duration_ms": 100}),
        json.dumps({"ts": "CORRUPT-NOT-ISO-TIMESTAMP", "status": "ok", "duration_ms": 100}),
    ]
    loop_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    oct27_now = datetime.datetime(2026, 10, 27, 12, 0, 0, tzinfo=datetime.timezone.utc)
    oct27_since = oct27_now - datetime.timedelta(days=1)

    stats = PhysicalDataAggregator.load_loop_runs_stats(
        since_ts=oct27_since.timestamp(),
        now_ts=oct27_now.timestamp(),
        data_dir=tmp_path,
    )
    assert stats["total_runs"] == 1, "Future entry (Oct 31) and corrupt timestamp must be excluded from Oct 27 window"
    assert stats["ok_runs"] == 1
    assert stats["last_cycle_at"] == "2026-10-27T04:00:00Z"

    # Even when since_ts=0.0, a corrupt non-empty timestamp string must fail closed (excluded)
    stats_zero_since = PhysicalDataAggregator.load_loop_runs_stats(
        since_ts=0.0,
        now_ts=oct27_now.timestamp(),
        data_dir=tmp_path,
    )
    assert stats_zero_since["total_runs"] == 1

