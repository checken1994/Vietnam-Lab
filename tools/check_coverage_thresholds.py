#!/usr/bin/env python3
"""TST-03: Code Coverage Threshold Enforcement Tool.

Enforces minimum code coverage thresholds for critical SCP packages.
- scp/task_kernel_parts: minimum 75% coverage
- scp/core: minimum 70% coverage
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

DEFAULT_THRESHOLDS = {
    "scp/task_kernel_parts": 75.0,
    "scp/core": 70.0,
}


def run_coverage_check(
    package: str,
    test_path: str | list[str],
    threshold: float,
    root: Path,
) -> tuple[bool, float, str]:
    """Run pytest with pytest-cov on target package and assert threshold."""
    if isinstance(test_path, str):
        test_paths = test_path.split()
    else:
        test_paths = list(test_path)
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        *test_paths,
        f"--cov={package}",
        "--cov-report=json",
        "-q",
        "--tb=short",
    ]
    cov_file = root / "coverage.json"
    if cov_file.exists():
        try:
            cov_file.unlink()
        except OSError:
            pass
    try:
        res = subprocess.run(cmd, cwd=root, capture_output=True, text=True, errors="replace")
        if not cov_file.exists():
            return False, 0.0, f"Coverage output file not found. stderr: {res.stderr}"

        data = json.loads(cov_file.read_text(encoding="utf-8"))
        # Clean up coverage.json after reading
        try:
            cov_file.unlink()
        except OSError:
            pass

        totals = data.get("totals", {})
        percent = totals.get("percent_covered", 0.0)
        if res.returncode != 0:
            return False, percent, f"Underlying test suite failed with exit code {res.returncode}.\nstdout:\n{res.stdout}\nstderr:\n{res.stderr}"

        passed = percent >= threshold
        return passed, percent, res.stdout
    except Exception as exc:
        return False, 0.0, str(exc)


def main() -> int:
    parser = argparse.ArgumentParser(description="TST-03: Coverage Threshold Check")
    parser.add_argument("--package", choices=list(DEFAULT_THRESHOLDS.keys()), help="Target package")
    parser.add_argument("--verify-config", action="store_true", help="Verify .coveragerc configuration")
    args = parser.parse_args()

    root = Path(__file__).resolve().parent.parent

    if args.verify_config:
        rc_path = root / ".coveragerc"
        if not rc_path.exists():
            print("[TST-03 FAIL] .coveragerc not found at project root")
            return 1
        content = rc_path.read_text(encoding="utf-8")
        if "scp/task_kernel_parts" not in content:
            print("[TST-03 FAIL] scp/task_kernel_parts not configured in .coveragerc")
            return 1
        if "scp/core" not in content:
            print("[TST-03 FAIL] scp/core not configured in .coveragerc")
            return 1
        print("[TST-03 PASS] .coveragerc properly configured with critical packages.")
        return 0

    print("[TST-03] Checking coverage thresholds for SCP critical components...")
    all_ok = True

    targets = [args.package] if args.package else ["scp/task_kernel_parts"]

    for pkg in targets:
        target_thresh = DEFAULT_THRESHOLDS[pkg]
        if pkg == "scp/task_kernel_parts":
            test_path = ["tests/T04_kernel/", "tests/test_subsystem_task_kernel.py"]
        else:
            print(f"  [TST-03 FAIL] Package '{pkg}' coverage measurement requires full system integration test suite (2,700+ tests).")
            all_ok = False
            continue

        print(f"  Measuring {pkg} against threshold {target_thresh}%...")
        ok, percent, out = run_coverage_check(pkg, test_path, target_thresh, root)
        if ok:
            print(f"  [PASS] {pkg}: {percent:.1f}% >= {target_thresh}%")
        else:
            print(f"  [FAIL] {pkg}: {percent:.1f}% < {target_thresh}%\n{out}")
            all_ok = False

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
