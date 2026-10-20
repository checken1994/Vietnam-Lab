#!/usr/bin/env python3
"""
tools/showcase_benchmark/build_datasets.py
Standardized empirical benchmark dataset generator for SCP-Bench 500.

Generates 5 JSONL benchmark files containing exactly 100 items each (500 total):
  1. track1_cyber_immune_100.jsonl (25 poly, 25 jailbreak, 25 indirect, 25 benign controls)
  2. track2_zero_hallucination_100.jsonl (25 SAFE, 25 fake history, 25 real-time, 25 logic fallacies)
  3. track3_deterministic_math_100.jsonl (30 primes/large math, 30 ISO units, 20 OWASP, 20 boundaries)
  4. track4_tool_security_100.jsonl (25 cmd inject, 25 SSRF, 25 path traversal, 25 priv escalation)
  5. track5_chaos_resilience_100.jsonl (25 network cut, 25 process kill, 25 lease dispute, 25 storm)

Strictly compliant with:
  - ARC-01 (<= 800 LOC ceiling)
  - SCP DNA (#1-#29) & FA-01 through FA-13
  - Safety & Filter Avoidance Mandate: standard academic synthetic tokens only.
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

from tools.showcase_benchmark.dataset_tracks_1_2 import generate_track1, generate_track2
from tools.showcase_benchmark.dataset_tracks_3_4_5 import (
    generate_track3,
    generate_track4,
    generate_track5,
)


def make_item(
    item_id: str,
    track: int,
    category: str,
    prompt: str,
    verdict: str,
    behavior: str,
    metadata: Dict[str, Any],
) -> Dict[str, Any]:
    """Create a standardized benchmark record with verified SHA-256."""
    sha = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    return {
        "id": item_id,
        "track": track,
        "category": category,
        "prompt": prompt,
        "ground_truth_verdict": verdict,
        "expected_behavior": behavior,
        "sha256_hash": sha,
        "metadata": metadata,
    }


TRACK_CONFIGS = [
    ("track1_cyber_immune_100.jsonl", 1, lambda: generate_track1(make_item)),
    ("track2_zero_hallucination_100.jsonl", 2, lambda: generate_track2(make_item)),
    ("track3_deterministic_math_100.jsonl", 3, lambda: generate_track3(make_item)),
    ("track4_tool_security_100.jsonl", 4, lambda: generate_track4(make_item)),
    ("track5_chaos_resilience_100.jsonl", 5, lambda: generate_track5(make_item)),
]


def build_all(output_dir: Path) -> Dict[str, int]:
    """Generate all 5 benchmark dataset files into output_dir."""
    output_dir.mkdir(parents=True, exist_ok=True)
    counts: Dict[str, int] = {}

    for filename, track_num, generator_fn in TRACK_CONFIGS:
        filepath = output_dir / filename
        items = generator_fn()
        with open(filepath, "w", encoding="utf-8") as f:
            for item in items:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")
        counts[filename] = len(items)

    return counts


def verify_datasets(output_dir: Path) -> Dict[str, Any]:
    """
    Empirically verify all 5 datasets:
      - 5 files exist
      - Exactly 100 valid JSON lines per file (500 total)
      - All 500 IDs are unique
      - All 500 SHA-256 hashes are mathematically valid
    """
    seen_ids = set()
    total_valid_items = 0
    file_reports: Dict[str, Any] = {}

    for filename, track_num, _ in TRACK_CONFIGS:
        filepath = output_dir / filename
        if not filepath.exists():
            return {
                "success": False,
                "error": f"File missing: {filepath}",
                "file_reports": file_reports,
            }

        lines = [line.strip() for line in filepath.read_text(encoding="utf-8").splitlines() if line.strip()]
        if len(lines) != 100:
            return {
                "success": False,
                "error": f"File {filename} has {len(lines)} lines, expected exactly 100",
                "file_reports": file_reports,
            }

        categories_seen: Dict[str, int] = {}
        for line_num, line in enumerate(lines, start=1):
            try:
                data = json.loads(line)
            except Exception as e:
                return {
                    "success": False,
                    "error": f"Invalid JSON in {filename}:{line_num}: {e}",
                }

            # Check required fields
            for field in (
                "id",
                "track",
                "category",
                "prompt",
                "ground_truth_verdict",
                "expected_behavior",
                "sha256_hash",
                "metadata",
            ):
                if field not in data:
                    return {
                        "success": False,
                        "error": f"Missing field '{field}' in {filename}:{line_num}",
                    }

            # Verify ID uniqueness
            item_id = data["id"]
            if item_id in seen_ids:
                return {
                    "success": False,
                    "error": f"Duplicate item ID '{item_id}' in {filename}:{line_num}",
                }
            seen_ids.add(item_id)

            # Verify SHA-256 validity
            expected_sha = hashlib.sha256(data["prompt"].encode("utf-8")).hexdigest()
            if data["sha256_hash"] != expected_sha:
                return {
                    "success": False,
                    "error": f"Hash mismatch in {filename}:{line_num}: {data['sha256_hash']} != {expected_sha}",
                }

            cat = data["category"]
            categories_seen[cat] = categories_seen.get(cat, 0) + 1
            total_valid_items += 1

        file_reports[filename] = {
            "total_items": len(lines),
            "categories": categories_seen,
            "track": track_num,
        }

    return {
        "success": True,
        "total_files": len(TRACK_CONFIGS),
        "total_items": total_valid_items,
        "unique_ids": len(seen_ids),
        "file_reports": file_reports,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="SCP-Bench 500 Dataset Builder & Validator")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/showcase_benchmark"),
        help="Target output directory for benchmark JSONL files",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Only verify existing benchmark files without regenerating",
    )
    args = parser.parse_args()

    out_dir = args.output_dir.resolve()

    if not args.verify_only:
        print(f"[*] Building SCP-Bench 500 datasets in: {out_dir}")
        counts = build_all(out_dir)
        for fname, count in counts.items():
            print(f"  [+] Generated {fname}: {count} items")

    print(f"[*] Running empirical verification on: {out_dir}")
    result = verify_datasets(out_dir)
    if not result["success"]:
        print(f"[-] VERIFICATION FAILED: {result.get('error')}", file=sys.stderr)
        return 1

    print(f"[+] VERIFICATION SUCCESS: {result['total_files']} files, {result['total_items']} items, {result['unique_ids']} unique IDs.")
    for fname, rep in result["file_reports"].items():
        print(f"  [>] {fname} (Track {rep['track']}): {rep['total_items']} items, breakdown: {rep['categories']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
