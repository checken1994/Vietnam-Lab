"""
tests/T02_contract/test_showcase_benchmark_datasets.py
Contract tests verifying empirical validity and integrity of SCP-Bench 500 datasets.

Asserts:
  - 5 benchmark files exist in data/showcase_benchmark/
  - Exactly 100 valid JSON records per file (500 items total)
  - 100% unique IDs across all 500 test cases
  - 100% mathematically valid SHA-256 hashes matching prompt utf-8 encoding
  - Category distributions adhere to milestone specifications
  - Academic synthetic safety tokens only (no real exploits or /etc/shadow)
  - ARC-01 compliance for tools/showcase_benchmark/*.py (<= 800 LOC ceiling)
"""

import hashlib
import json
from pathlib import Path

import pytest

ROOT_DIR = Path(__file__).resolve().parents[2]
BENCHMARK_DIR = ROOT_DIR / "data" / "showcase_benchmark"
TOOLS_DIR = ROOT_DIR / "tools" / "showcase_benchmark"

TRACK_METRICS = [
    (
        "track1_cyber_immune_100.jsonl",
        1,
        {"polymorphic_mutation": 25, "jailbreak": 25, "indirect_injection": 25, "benign_control": 25},
    ),
    (
        "track2_zero_hallucination_100.jsonl",
        2,
        {"deepmind_safe": 25, "fake_history": 25, "freshness_trap": 25, "logic_fallacy": 25},
    ),
    (
        "track3_deterministic_math_100.jsonl",
        3,
        {"large_math_primes": 30, "iso_unit_conversion": 30, "owasp_llm09": 20, "arithmetic_boundary": 20},
    ),
    (
        "track4_tool_security_100.jsonl",
        4,
        {"command_injection": 25, "ssrf": 25, "path_traversal": 25, "privilege_escalation": 25},
    ),
    (
        "track5_chaos_resilience_100.jsonl",
        5,
        {"network_cut": 25, "process_kill": 25, "lease_dispute": 25, "concurrency_storm": 25},
    ),
]


def test_showcase_benchmark_files_exist_and_count():
    """Verify that all 5 benchmark files exist and contain exactly 100 lines each."""
    assert BENCHMARK_DIR.is_dir(), f"Benchmark directory missing: {BENCHMARK_DIR}"
    for filename, track_num, expected_cats in TRACK_METRICS:
        filepath = BENCHMARK_DIR / filename
        assert filepath.is_file(), f"Benchmark file missing: {filepath}"
        lines = [line.strip() for line in filepath.read_text(encoding="utf-8").splitlines() if line.strip()]
        assert len(lines) == 100, f"{filename} contains {len(lines)} lines; expected exactly 100"


def test_showcase_benchmark_schema_and_sha256_integrity():
    """Verify schema, ID uniqueness, SHA-256 mathematical accuracy, and category distribution."""
    all_ids = set()
    total_count = 0

    for filename, track_num, expected_cats in TRACK_METRICS:
        filepath = BENCHMARK_DIR / filename
        lines = [line.strip() for line in filepath.read_text(encoding="utf-8").splitlines() if line.strip()]
        cat_counts = {}

        for idx, line in enumerate(lines, start=1):
            record = json.loads(line)
            # Required schema keys
            for key in (
                "id",
                "track",
                "category",
                "prompt",
                "ground_truth_verdict",
                "expected_behavior",
                "sha256_hash",
                "metadata",
            ):
                assert key in record, f"Missing key '{key}' in {filename}:{idx}"

            assert record["track"] == track_num, f"Track mismatch in {filename}:{idx}"

            # ID uniqueness across all tracks
            item_id = record["id"]
            assert item_id not in all_ids, f"Duplicate ID '{item_id}' in {filename}:{idx}"
            all_ids.add(item_id)

            # Mathematical SHA-256 integrity
            computed_sha = hashlib.sha256(record["prompt"].encode("utf-8")).hexdigest()
            assert record["sha256_hash"] == computed_sha, (
                f"SHA-256 hash mismatch in {filename}:{idx}: {record['sha256_hash']} != {computed_sha}"
            )

            # Safety and filter avoidance: no /etc/shadow or real exploit code
            assert "/etc/shadow" not in record["prompt"], f"Unsafe token detected in {filename}:{idx}"

            cat = record["category"]
            cat_counts[cat] = cat_counts.get(cat, 0) + 1
            total_count += 1

        assert cat_counts == expected_cats, f"Category count mismatch in {filename}: {cat_counts} != {expected_cats}"

    assert len(all_ids) == 500, f"Expected 500 unique IDs, found {len(all_ids)}"
    assert total_count == 500, f"Expected 500 total records, processed {total_count}"


def test_showcase_benchmark_builder_arc01_loc_ceiling():
    """Verify that all Python scripts in tools/showcase_benchmark/ adhere to ARC-01 (<= 800 LOC)."""
    assert TOOLS_DIR.is_dir(), f"Tools directory missing: {TOOLS_DIR}"
    py_files = list(TOOLS_DIR.glob("*.py"))
    assert len(py_files) >= 1, "Expected at least 1 Python file in tools/showcase_benchmark/"

    for py_file in py_files:
        loc = len(py_file.read_text(encoding="utf-8", errors="replace").splitlines())
        assert loc <= 800, f"ARC-01 VIOLATION: {py_file.name} is {loc} LOC, exceeding 800 LOC ceiling"
