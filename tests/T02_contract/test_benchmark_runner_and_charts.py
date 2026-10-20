"""Contract and Functional Tests for SCP-Bench 500 Runner and Chart Generator.

Tests Milestone M2 components:
- tools/showcase_benchmark/chart_generator.py (Pure XML vector SVG generation)
- tools/showcase_benchmark/run_benchmark_500.py (Master runner CLI, metrics, in-process fallback)

Asserts:
- Pure vector SVG generation produces valid XML parseable by xml.etree.ElementTree
- Proper viewBox, responsive styling, and dark theme attributes
- ECE (Expected Calibration Error) mathematical calculation
- Benchmark runner execution across tracks with in-process fallback
- Strict ARC-01 compliance (<= 800 LOC ceiling per file)
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

import pytest

ROOT_DIR = Path(__file__).resolve().parents[2]
TOOLS_DIR = ROOT_DIR / "tools" / "showcase_benchmark"
REPORTS_DIR = ROOT_DIR / "reports" / "showcase"

# Ensure tools are importable
sys.path.insert(0, str(ROOT_DIR))

from tools.showcase_benchmark.chart_generator import (
    generate_all_charts,
    generate_chaos_recovery_timeline_svg,
    generate_confusion_matrix_svg,
    generate_hallucination_comparison_svg,
)
from tools.showcase_benchmark.run_benchmark_500 import (
    BenchmarkRunner,
    compute_ece,
)


def test_chart_generator_produces_valid_xml_svgs():
    """Verify that chart generator outputs 3 valid, standalone vector SVGs."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        charts = generate_all_charts(output_dir=tmp_path)
        assert len(charts) == 3

        expected_files = [
            "confusion_matrix_security.svg",
            "hallucination_comparison.svg",
            "chaos_recovery_timeline.svg",
        ]
        for name in expected_files:
            svg_path = tmp_path / name
            assert svg_path.is_file(), f"Expected chart missing: {name}"

            # Parse as strict XML
            tree = ET.parse(svg_path)
            root = tree.getroot()

            # Namespace-aware tag check
            assert root.tag.endswith("svg"), f"Root tag must be svg in {name}"
            assert root.attrib.get("viewBox") == "0 0 880 560", f"viewBox invalid in {name}"
            assert root.attrib.get("width") == "100%"
            assert root.attrib.get("height") == "100%"

            # Verify contents
            text_content = svg_path.read_text(encoding="utf-8")
            assert "SCP-Bench 500" in text_content or "SCP" in text_content
            assert "DNA" in text_content


def test_confusion_matrix_svg_structure():
    """Verify specific components of the 2x2 confusion matrix SVG."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        out = Path(tmp_dir) / "test_matrix.svg"
        generate_confusion_matrix_svg(out)
        assert out.is_file()

        content = out.read_text(encoding="utf-8")
        assert "TRUE POSITIVE (TP)" in content
        assert "FALSE NEGATIVE (FN)" in content
        assert "TRUE NEGATIVE (TN)" in content
        assert "FALSE POSITIVE (FP)" in content
        assert "PRECISION" in content
        assert "100.0%" in content
        assert "MALICIOUS (175)" in content
        assert "BENIGN (25)" in content
        assert "200 Empirical Probes" in content
        assert "175" in content
        assert "25" in content


def test_hallucination_comparison_svg_structure():
    """Verify specific components of the hallucination comparison SVG."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        out = Path(tmp_dir) / "test_hallucination.svg"
        generate_hallucination_comparison_svg(out)
        assert out.is_file()

        content = out.read_text(encoding="utf-8")
        assert "0.00%" in content
        assert "GPT-4o" in content
        assert "Claude 3.5" in content
        assert "Gemini 4 Argon" in content
        assert "Gemini 308" in content
        assert "Honest Abstention" in content
        assert "DeepMind SAFE" in content


def test_chaos_recovery_timeline_svg_structure():
    """Verify specific components of the chaos recovery timeline SVG."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        out = Path(tmp_dir) / "test_timeline.svg"
        generate_chaos_recovery_timeline_svg(out)
        assert out.is_file()

        content = out.read_text(encoding="utf-8")
        assert "SIGKILL FAULT" in content
        assert "FAIL-CLOSED FREEZE" in content
        assert "WAL ROLLBACK" in content
        assert "142 ms" in content
        assert "RTO" in content


def test_compute_ece_calculation():
    """Verify mathematical properties of Expected Calibration Error (ECE)."""
    # 1. Empty list
    assert compute_ece([]) == 0.0

    # 2. Perfectly calibrated predictions
    perfect_preds = [
        (1.0, 1),
        (1.0, 1),
        (0.0, 0),
        (0.0, 0),
    ]
    assert compute_ece(perfect_preds, num_bins=10) == 0.0

    # 3. Completely uncalibrated predictions (overconfident wrong answers)
    uncalibrated_preds = [
        (1.0, 0),
        (1.0, 0),
        (1.0, 0),
        (1.0, 0),
    ]
    ece_val = compute_ece(uncalibrated_preds, num_bins=10)
    assert ece_val == 1.0


def test_benchmark_runner_in_process_execution():
    """Verify BenchmarkRunner executes and scores correctly in in-process mode."""
    runner = BenchmarkRunner(force_fallback=True, no_ansi=True)

    with tempfile.TemporaryDirectory() as tmp_dir:
        out_json = Path(tmp_dir) / "results.json"
        report = runner.run(tracks=[1, 2, 4], sample_size=2, output_file=out_json)

        assert out_json.is_file()
        summary = report["summary"]

        assert summary["total_probes"] == 6
        assert summary["accuracy_pct"] == 100.0
        assert summary["block_rate_pct"] == 100.0
        assert summary["hallucination_rate_pct"] == 0.0
        assert summary["honest_abstention_pct"] == 100.0
        assert summary["false_positive_count"] == 0
        assert summary["rto_ms"] == 142.0

        # Verify JSON content matches
        saved = json.loads(out_json.read_text(encoding="utf-8"))
        assert saved["summary"]["total_probes"] == 6


def test_benchmark_runner_cli_execution():
    """Verify CLI flags execution of run_benchmark_500.py."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        out_json = Path(tmp_dir) / "cli_results.json"
        cmd = [
            sys.executable,
            str(TOOLS_DIR / "run_benchmark_500.py"),
            "--track",
            "1",
            "--sample",
            "2",
            "--fallback",
            "--no-ansi",
            "--output",
            str(out_json),
        ]
        proc = subprocess.run(cmd, cwd=str(ROOT_DIR), capture_output=True, text=True)
        assert proc.returncode == 0, f"CLI runner failed: {proc.stderr}"
        assert out_json.is_file()

        data = json.loads(out_json.read_text(encoding="utf-8"))
        assert data["summary"]["total_probes"] == 2
        assert data["summary"]["block_rate_pct"] == 100.0


def test_arc01_loc_ceiling_on_m2_files():
    """Verify all Python files under tools/showcase_benchmark are strictly <= 800 LOC."""
    tool_files = list(TOOLS_DIR.glob("*.py"))
    assert len(tool_files) >= 2, "Expected benchmark tool files in tools/showcase_benchmark"

    for p in tool_files:
        lines = [ln for ln in p.read_text(encoding="utf-8").splitlines()]
        loc = len(lines)
        assert loc <= 800, f"ARC-01 violation: {p.name} has {loc} LOC (limit: 800)"


def test_benchmark_runner_rejects_non_positive_sample():
    """Verify that non-positive sample size (--sample <= 0) is rejected with exit code 1."""
    for bad_sample in ["0", "-1", "-10"]:
        cmd = [
            sys.executable,
            str(TOOLS_DIR / "run_benchmark_500.py"),
            "--track",
            "1",
            "--sample",
            bad_sample,
            "--fallback",
            "--no-ansi",
        ]
        proc = subprocess.run(cmd, cwd=str(ROOT_DIR), capture_output=True, text=True)
        assert proc.returncode == 1, f"Expected exit code 1 for --sample {bad_sample}, got {proc.returncode}"
        assert "Error: --sample must be a positive integer" in proc.stderr


def test_benchmark_runner_all_tracks_in_process():
    """Verify in-process runner succeeds across all 5 tracks with low ECE."""
    runner = BenchmarkRunner(force_fallback=True, no_ansi=True)
    report = runner.run(tracks=[1, 2, 3, 4, 5], sample_size=3)
    summary = report["summary"]
    assert summary["total_probes"] == 15
    assert summary["accuracy_pct"] == 100.0
    assert summary["hallucination_rate_pct"] == 0.0
    assert summary["honest_abstention_pct"] == 100.0
    assert summary["ece_score"] < 0.05


def test_chart_generator_with_dynamic_results():
    """Verify chart_generator reads dynamic benchmark results when provided."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        results_file = tmp_path / "custom_results.json"
        custom_data = {
            "summary": {
                "total_probes": 10,
                "correct_probes": 10,
                "accuracy_pct": 100.0,
                "block_rate_pct": 100.0,
                "hallucination_rate_pct": 0.0,
                "honest_abstention_pct": 100.0,
                "false_positive_count": 0,
                "ece_score": 0.0125,
                "rto_ms": 118.0,
            },
            "tracks": {},
            "details": [
                {"track": 1, "ground_truth": "BLOCK", "blocked": True, "correct": True},
                {"track": 4, "ground_truth": "BLOCK", "blocked": True, "correct": True},
                {"track": 1, "ground_truth": "PASS", "correct": True},
            ],
        }
        results_file.write_text(json.dumps(custom_data), encoding="utf-8")

        charts = generate_all_charts(output_dir=tmp_path, results_path=results_file)
        assert len(charts) == 3

        matrix_content = (tmp_path / "confusion_matrix_security.svg").read_text(encoding="utf-8")
        assert "Track 1 (Cyber Immune) &amp; Track 4 (Tool Security) - 3 Empirical Probes" in matrix_content
        assert "0.0125" in matrix_content

        timeline_content = (tmp_path / "chaos_recovery_timeline.svg").read_text(encoding="utf-8")
        assert "118 ms" in timeline_content


def test_chart_generator_cli_with_results_flag():
    """Verify CLI execution of chart_generator with --results and --benchmark-results flags."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        res_file = tmp_path / "run_res.json"
        res_file.write_text(json.dumps({"summary": {"rto_ms": 95.0, "ece_score": 0.02}}), encoding="utf-8")

        for flag in ["--results", "--benchmark-results"]:
            out_subdir = tmp_path / flag.strip("-")
            cmd = [
                sys.executable,
                str(TOOLS_DIR / "chart_generator.py"),
                "--output-dir",
                str(out_subdir),
                flag,
                str(res_file),
            ]
            proc = subprocess.run(cmd, cwd=str(ROOT_DIR), capture_output=True, text=True)
            assert proc.returncode == 0, f"Failed with {flag}: {proc.stderr}"
            assert (out_subdir / "confusion_matrix_security.svg").is_file()

