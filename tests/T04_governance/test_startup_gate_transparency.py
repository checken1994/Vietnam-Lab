"""Tests for Issue 6: Startup Gate Scan Transparency & Full Scan Support.

Verifies dynamic eligible file counting, transparent sampling ratio formatting,
and support for SCP_STARTUP_SCAN_ALL=1 allowing full scans (max_files=None).
Includes anti-placebo negative tests preventing false-green assertions.
"""
from __future__ import annotations

import ast
import tempfile
from pathlib import Path

import pytest

from scp.startup.startup_gate import (
    count_eligible_files,
    format_startup_scan_summary,
    get_eligible_files,
    resolve_scan_scope,
    run_startup_ast_scan,
)


def test_count_eligible_files_dynamic():
    """Verify that file count is dynamic, positive, and discovers scp package files."""
    files = get_eligible_files()
    count = count_eligible_files()
    assert count > 500, f"Expected > 500 python files in scp package, found {count}"
    assert count == len(files)
    # Check that test files and pycache are excluded
    for f in files:
        assert "__pycache__" not in f.parts
        assert "tests" not in f.parts
        assert not f.name.startswith("test_")


def test_format_startup_scan_summary_sampled():
    """Verify exact formatting for sampled scan results."""
    msg = format_startup_scan_summary(bug_count=0, scanned_count=100, total_count=708, is_full_scan=False)
    assert msg == "[STARTUP-GATE] Background scan complete: 0 bugs found across 100/708 files (sampled)"


def test_format_startup_scan_summary_full_scan():
    """Verify exact formatting for full scan results."""
    msg = format_startup_scan_summary(bug_count=0, scanned_count=708, total_count=708, is_full_scan=True)
    assert msg == "[STARTUP-GATE] Background scan complete: 0 bugs found across 708/708 files (full scan)"


def test_resolve_scan_scope_default_sampling(monkeypatch):
    """Default configuration must sample 100 files when no env vars are set."""
    monkeypatch.delenv("SCP_STARTUP_SCAN_ALL", raising=False)
    monkeypatch.delenv("SCP_MAX_STARTUP_FILES", raising=False)

    effective_max, scanned_count, total_count, mode = resolve_scan_scope()

    assert effective_max == 100
    assert scanned_count == 100
    assert total_count > 100
    assert mode == "sampled"


def test_resolve_scan_scope_scan_all_env(monkeypatch):
    """SCP_STARTUP_SCAN_ALL=1 enables full scan with max_files=None."""
    monkeypatch.setenv("SCP_STARTUP_SCAN_ALL", "1")

    effective_max, scanned_count, total_count, mode = resolve_scan_scope()

    assert effective_max is None, "Expected max_files=None for full scan"
    assert scanned_count == total_count
    assert mode == "full scan"


def test_resolve_scan_scope_explicit_parameters():
    """Explicit keyword arguments override environment variables."""
    # Explicit scan_all=True
    effective_max, scanned, total, mode = resolve_scan_scope(scan_all=True)
    assert effective_max is None
    assert scanned == total
    assert mode == "full scan"

    # Explicit max_files=42
    effective_max, scanned, total, mode = resolve_scan_scope(max_files=42, scan_all=False)
    assert effective_max == 42
    assert scanned == 42
    assert mode == "sampled"


def test_resolve_scan_scope_exceeds_total():
    """When requested limit exceeds total files, mode becomes 'full scan'."""
    effective_max, scanned, total, mode = resolve_scan_scope(max_files=999999, scan_all=False)
    assert scanned == total
    assert mode == "full scan"


# =========================================================================
# ANTI-PLACEBO NEGATIVE TESTS (FA-01 / FA-11 / FA-13 / DNA #11)
# =========================================================================


def test_format_startup_scan_summary_anti_placebo_boundaries():
    """Negative tests: verify format distinguishes sampled from full scan strictly."""
    sampled_msg = format_startup_scan_summary(2, 50, 700, False)
    assert "(sampled)" in sampled_msg
    assert "(full scan)" not in sampled_msg
    assert "2 bugs found" in sampled_msg
    assert "50/700 files" in sampled_msg

    full_msg = format_startup_scan_summary(0, 700, 700, True)
    assert "(full scan)" in full_msg
    assert "(sampled)" not in full_msg
    assert "0 bugs found" in full_msg
    assert "700/700 files" in full_msg


def test_scan_all_falsy_values_fail_closed_to_sampling(monkeypatch):
    """Negative test: Falsy values of SCP_STARTUP_SCAN_ALL must not enable full scan."""
    for falsy_val in ("0", "false", "no", "False", "2", ""):
        monkeypatch.setenv("SCP_STARTUP_SCAN_ALL", falsy_val)
        effective_max, scanned, total, mode = resolve_scan_scope()
        assert mode == "sampled", f"Expected sampled for SCP_STARTUP_SCAN_ALL='{falsy_val}', got {mode}"
        assert effective_max is not None, f"Expected non-None max_files for '{falsy_val}'"


def test_custom_root_file_discovery():
    """Negative test: Verify file counting correctly handles an isolated small tree."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        (tmp_path / "valid1.py").write_text("# code\n", encoding="utf-8")
        (tmp_path / "valid2.py").write_text("# code\n", encoding="utf-8")
        (tmp_path / "test_ignored.py").write_text("# test\n", encoding="utf-8")
        (tmp_path / "__pycache__").mkdir()
        (tmp_path / "__pycache__" / "cached.py").write_text("# cache\n", encoding="utf-8")
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "ignored.py").write_text("# test\n", encoding="utf-8")

        eligible = get_eligible_files(tmp_path)
        assert len(eligible) == 2
        assert [p.name for p in eligible] == ["valid1.py", "valid2.py"]

        eff_max, scanned, total, mode = resolve_scan_scope(max_files=1, root=tmp_path)
        assert total == 2
        assert scanned == 1
        assert mode == "sampled"


@pytest.mark.asyncio
async def test_run_startup_ast_scan_execution(monkeypatch):
    """Verify run_startup_ast_scan dispatches properly with runner."""
    dummy_bugs = [{"bug_type": "BareExceptPass", "line": 10}]
    call_args_list = []

    async def fake_ast_scan_scp(**kwargs):
        call_args_list.append(kwargs)
        return dummy_bugs

    import scp.startup.startup_gate as sg
    monkeypatch.setattr(sg, "ast_scan_scp", fake_ast_scan_scp)

    bugs, scanned, total, mode = await run_startup_ast_scan(
        max_files=50,
        scan_all=False,
        include_enterprise=True,
    )

    assert bugs == dummy_bugs
    assert scanned == 50
    assert mode == "sampled"
    assert len(call_args_list) == 1
    assert call_args_list[0]["max_files"] == 50
    assert call_args_list[0]["include_enterprise"] is True


def test_lifespan_integration_contains_transparent_startup_gate():
    """Verify that scp/api_server_parts/lifespan.py calls startup_gate transparently."""
    repo_root = Path(__file__).resolve().parent.parent.parent
    lifespan_file = repo_root / "scp" / "api_server_parts" / "lifespan.py"

    assert lifespan_file.exists()
    content = lifespan_file.read_text(encoding="utf-8")

    assert "from scp.startup.startup_gate import run_startup_ast_scan, format_startup_scan_summary" in content
    assert "format_startup_scan_summary(len(bugs), scanned_count, total_count, mode == \"full scan\")" in content
