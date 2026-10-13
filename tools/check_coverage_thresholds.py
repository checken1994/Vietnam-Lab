#!/usr/bin/env python3
"""TST-03: Code Coverage Threshold Enforcement Tool.

Enforces minimum code coverage thresholds for critical SCP packages:
- scp/task_kernel_parts: minimum 75.0% coverage
- scp/llm_gateway: minimum 75.0% coverage
- scp/core: minimum 75.0% coverage (core security & kernel primitives)
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

DEFAULT_THRESHOLDS = {
    "scp/task_kernel_parts": 75.0,
    "scp/llm_gateway": 75.0,
    "scp/core": 75.0,
}

PACKAGE_CONFIG: dict[str, dict[str, list[str]]] = {
    "scp/task_kernel_parts": {
        "cov_targets": ["scp/task_kernel_parts"],
        "test_paths": [
            "tests/T04_kernel/",
            "tests/test_subsystem_task_kernel.py",
            "tests/T03_capability/test_b58_tiered_verification_and_feedback_loop.py",
            "tests/T03_capability/test_redteam_b56_remediation.py",
        ],
    },
    "scp/llm_gateway": {
        "cov_targets": ["scp/llm_gateway"],
        "test_paths": ["tests/T05_gateway/"],
    },
    "scp/core": {
        "cov_targets": [
            "scp.core.safe_process",
            "scp.core.capability_token",
            "scp.core.verifier_receipt",
            "scp.core.release_identity",
        ],
        "test_paths": [
            "tests/T03_capability/test_safe_process_hardening.py",
            "tests/T03_capability/test_capability_secret_fail_closed.py",
            "tests/T03_capability/test_capability_token_hmac_signing.py",
            "tests/T03_capability/test_capability_token_mutation_contract.py",
            "tests/T04_kernel/test_verifier_receipt_branches.py",
            "tests/T04_kernel/test_verifier_receipt_provenance.py",
            "tests/test_subsystem_core.py",
        ],
    },
}


def run_coverage_check(
    package: str,
    test_path: str | list[str],
    threshold: float,
    root: Path,
    cov_targets: list[str] | None = None,
) -> tuple[bool, float, str]:
    """Run pytest with pytest-cov on target package and assert threshold."""
    if isinstance(test_path, str):
        test_paths = test_path.split()
    else:
        test_paths = list(test_path)
    targets = cov_targets if cov_targets else [package]
    cov_flags = [f"--cov={t}" for t in targets]
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        *test_paths,
        *cov_flags,
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
    env = dict(os.environ)
    env.setdefault("SCP_CAPABILITY_SECRET", "tst03-ephemeral-coverage-secret-key-999")
    try:
        res = subprocess.run(cmd, cwd=root, env=env, capture_output=True, text=True, errors="replace")
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
            return (
                False,
                percent,
                f"Underlying test suite failed with exit code {res.returncode}.\nstdout:\n{res.stdout}\nstderr:\n{res.stderr}",
            )

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
        for required_pkg in DEFAULT_THRESHOLDS:
            if required_pkg not in content:
                print(f"[TST-03 FAIL] {required_pkg} not configured in .coveragerc")
                return 1
        print("[TST-03 PASS] .coveragerc properly configured with critical packages.")
        return 0

    print("[TST-03] Checking coverage thresholds for SCP critical components...")
    all_ok = True

    targets = [args.package] if args.package else list(DEFAULT_THRESHOLDS.keys())

    for pkg in targets:
        target_thresh = DEFAULT_THRESHOLDS[pkg]
        cfg = PACKAGE_CONFIG.get(pkg)
        if not cfg:
            print(f"  [TST-03 FAIL] Package '{pkg}' has no configured test paths.")
            all_ok = False
            continue

        print(f"  Measuring {pkg} against threshold {target_thresh}%...")
        ok, percent, out = run_coverage_check(
            pkg,
            cfg["test_paths"],
            target_thresh,
            root,
            cov_targets=cfg["cov_targets"],
        )
        if ok:
            print(f"  [PASS] {pkg}: {percent:.1f}% >= {target_thresh}%")
        else:
            print(f"  [FAIL] {pkg}: {percent:.1f}% < {target_thresh}%\n{out}")
            all_ok = False

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())

