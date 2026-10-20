"""Adversarial Verification & Stress Test Harness for Blind-Spot Remediation.

This test suite empirically challenges:
1. ARC-01 Headroom (LOC <= 800, autofix_mixin < 730).
2. SC-07 Multi-File Atomic Rollback in scripts/sandbox_autofix_empirical_proof.py:
   - Byte-for-byte SHA-256 restore across multiple files.
   - Zero orphan temporary files (.*.rb_*).
   - Manifest state ROLLED_BACK.
   - Fallback copy try...finally cleanup under simulated disk/copy failures.
3. Benchmark 500 Mode Stamping in tools/showcase_benchmark/run_benchmark_500.py:
   - execution_mode in root and summary.
   - ANSI header output.
   - Fail-closed handling for --sample 0, negative samples, and invalid track IDs.
4. AutoFixMixin and AutoFixHelpersMixin contract integrity.
"""

import ast
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scp.autofix.engine_parts.autofix_helpers import AutoFixHelpersMixin
from scp.autofix.engine_parts.autofix_mixin import AutoFixMixin
from scp.autofix.shadow_snapshot import ShadowSnapshotManager
from scripts.sandbox_autofix_empirical_proof import (
    run_scenario_7_multi_file_atomic_rollback,
)


class TestRemediationAdversarial(unittest.TestCase):
    """Empirical challenge suite for blind-spot remediation."""

    def test_01_arc01_loc_headroom(self):
        """Verify strict line count limits on all touched files."""
        limits = {
            "scp/autofix/runner_phases/ast_scan.py": 800,
            "scp/autofix/shadow_snapshot.py": 800,
            "scripts/sandbox_autofix_empirical_proof.py": 800,
            "tools/showcase_benchmark/run_benchmark_500.py": 800,
            "scp/autofix/engine_parts/autofix_mixin.py": 729,  # strictly < 730
            "scp/autofix/engine_parts/autofix_helpers.py": 800,
        }
        for rel_path, max_lines in limits.items():
            abs_path = REPO_ROOT / rel_path
            self.assertTrue(abs_path.exists(), f"File does not exist: {rel_path}")
            lines = len(abs_path.read_text(encoding="utf-8").splitlines())
            self.assertLessEqual(
                lines,
                max_lines,
                f"ARC-01 violation: {rel_path} has {lines} LOC (limit: {max_lines})",
            )
            print(f"[ARC-01] {rel_path}: {lines} LOC (<= {max_lines}) — PASS")

    def test_02_sc07_scenario_direct_execution(self):
        """Empirically run SC-07 scenario function directly."""
        res = run_scenario_7_multi_file_atomic_rollback()
        self.assertTrue(res.get("passed"), "SC-07 scenario failed")
        self.assertEqual(res.get("id"), "SC-07")
        self.assertEqual(res.get("rollback_count"), 1)
        self.assertGreater(res.get("duration_ms", 0), 0)
        print(f"[SC-07 Direct] {res['name']} passed in {res['duration_ms']} ms")

    def test_03_deep_multi_file_atomic_rollback_stress(self):
        """Stress test multi-file atomic rollback with 3 files and delete operation."""
        with tempfile.TemporaryDirectory(prefix="scp_adv_stress_") as td:
            base_dir = Path(td)
            f1 = base_dir / "service_a.py"
            f2 = base_dir / "service_b.py"
            f3 = base_dir / "service_c.py"

            content1 = "def a(): return 100\n"
            content2 = "def b(): return 200\n"
            content3 = "def c(): return 300\n"

            f1.write_text(content1, encoding="utf-8")
            f2.write_text(content2, encoding="utf-8")
            f3.write_text(content3, encoding="utf-8")

            sha1 = hashlib.sha256(f1.read_bytes()).hexdigest()
            sha2 = hashlib.sha256(f2.read_bytes()).hexdigest()
            sha3 = hashlib.sha256(f3.read_bytes()).hexdigest()

            shadow_mgr = ShadowSnapshotManager(shadow_dir=base_dir / "shadow")
            tx_id = shadow_mgr.begin([f1, f2, f3], bug_id="STRESS_TX_001")

            # Mutate f1, corrupt f2, delete f3
            f1.write_text("def a(): return 999\n", encoding="utf-8")
            f2.write_text("def b(): return ???\n", encoding="utf-8")
            f3.unlink()

            self.assertNotEqual(f1.read_bytes(), content1.encode("utf-8"))
            self.assertNotEqual(f2.read_bytes(), content2.encode("utf-8"))
            self.assertFalse(f3.exists())

            # Trigger atomic rollback
            rb_ok = shadow_mgr.rollback(tx_id, reason="Stress test rollback triggered")
            self.assertTrue(rb_ok, "Rollback must succeed")

            # Verify byte-for-byte SHA256 restore for ALL 3 files
            self.assertEqual(hashlib.sha256(f1.read_bytes()).hexdigest(), sha1)
            self.assertEqual(hashlib.sha256(f2.read_bytes()).hexdigest(), sha2)
            self.assertEqual(hashlib.sha256(f3.read_bytes()).hexdigest(), sha3)

            # Verify zero orphan files (.rb_*)
            orphans = [
                os.path.join(r, f)
                for r, _, files in os.walk(base_dir)
                for f in files
                if ".rb_" in f
            ]
            self.assertEqual(len(orphans), 0, f"Found orphan temp files: {orphans}")

            # Verify transaction state in manifest
            rolled_back_tx = shadow_mgr.rolled_back_dir / tx_id
            self.assertTrue(rolled_back_tx.is_dir())
            manifest = json.loads((rolled_back_tx / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["status"], "ROLLED_BACK")
            print("[SC-07 Stress] 3-file atomic restore & 0 orphan files verified")

    def test_04_shadow_snapshot_orphan_cleanup_under_failure(self):
        """Verify try...finally deletes tmp_restore even if fallback copy fails."""
        with tempfile.TemporaryDirectory(prefix="scp_adv_leak_") as td:
            base_dir = Path(td)
            target = base_dir / "locked_file.py"
            target.write_text("def locked(): pass\n", encoding="utf-8")

            shadow_mgr = ShadowSnapshotManager(shadow_dir=base_dir / "shadow")
            tx_id = shadow_mgr.begin([target], bug_id="LEAK_TEST_001")

            # Mutate target
            target.write_text("def modified(): pass\n", encoding="utf-8")

            # Simulate os.replace failure AND shutil.copy2 failure during restore
            def fake_replace(src, dst):
                raise OSError("Simulated WinError 32: The process cannot access the file")

            def fake_copy2(src, dst):
                raise PermissionError("Simulated WinError 5: Access is denied")

            with patch("os.replace", side_effect=fake_replace), patch("shutil.copy2", side_effect=fake_copy2):
                rb_ok = shadow_mgr.rollback(tx_id, reason="Simulated locked file rollback")
                self.assertFalse(rb_ok, "Rollback must fail-closed when restore fails")

            # Crucial invariant: tmp_restore must be unlinked in finally block, NO orphans!
            orphans = [
                os.path.join(r, f)
                for r, _, files in os.walk(base_dir)
                for f in files
                if ".rb_" in f
            ]
            self.assertEqual(
                len(orphans),
                0,
                f"Orphan file leak detected under copy failure: {orphans}",
            )
            print("[Leak Defense] Verified zero orphan files left on copy failure")

    def test_05_benchmark_mode_stamping_and_schema(self):
        """Verify execution_mode in JSON root and summary, and ANSI display."""
        with tempfile.TemporaryDirectory(prefix="scp_adv_bench_") as td:
            out_json = Path(td) / "bench_result.json"
            cmd = [
                sys.executable,
                str(REPO_ROOT / "tools/showcase_benchmark/run_benchmark_500.py"),
                "--sample", "1",
                "--track", "1",
                "--fallback",
                "--output", str(out_json),
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(REPO_ROOT))
            self.assertEqual(proc.returncode, 0, f"Benchmark runner failed: {proc.stderr}")
            self.assertIn("Execution Mode:", proc.stdout)
            self.assertIn("SIMULATED_BENCHMARK", proc.stdout)

            # Test --no-ansi plain text output
            cmd_no_ansi = cmd + ["--no-ansi"]
            proc_plain = subprocess.run(cmd_no_ansi, capture_output=True, text=True, cwd=str(REPO_ROOT))
            self.assertEqual(proc_plain.returncode, 0)
            self.assertIn("Execution Mode: SIMULATED_BENCHMARK (IN-PROCESS (Fail-Closed Engine))", proc_plain.stdout)

            self.assertTrue(out_json.exists(), "Benchmark output JSON was not written")
            data = json.loads(out_json.read_text(encoding="utf-8"))

            # Schema invariants
            self.assertEqual(data.get("execution_mode"), "SIMULATED_BENCHMARK", "Root execution_mode missing/wrong")
            self.assertIn("summary", data, "Summary missing")
            self.assertEqual(data["summary"].get("execution_mode"), "SIMULATED_BENCHMARK", "Summary execution_mode missing/wrong")
            self.assertEqual(data["summary"].get("total_probes"), 1)
            print("[Benchmark Mode] execution_mode properly stamped at root & summary")

    def test_06_benchmark_cli_fail_closed(self):
        """Verify benchmark runner rejects invalid arguments fail-closed."""
        runner_script = str(REPO_ROOT / "tools/showcase_benchmark/run_benchmark_500.py")
        test_cases = [
            (["--sample", "0", "--fallback"], "Error: --sample must be a positive integer"),
            (["--sample", "-5", "--fallback"], "Error: --sample must be a positive integer"),
            (["--track", "0", "--fallback"], "Error: Invalid track 0"),
            (["--track", "6", "--fallback"], "Error: Invalid track 6"),
            (["--track", "99", "--fallback"], "Error: Invalid track 99"),
        ]
        for args, expected_err in test_cases:
            cmd = [sys.executable, runner_script] + args
            proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(REPO_ROOT))
            self.assertEqual(
                proc.returncode,
                1,
                f"Expected exit code 1 for args {args}, got {proc.returncode}",
            )
            self.assertIn(
                expected_err,
                proc.stderr,
                f"Expected error '{expected_err}' not in stderr: {proc.stderr}",
            )
            print(f"[Benchmark Fail-Closed] Correctly rejected args: {args}")

    def test_07_autofix_mixin_helpers_contract(self):
        """Verify AutoFixMixin and AutoFixHelpersMixin contract integrity."""
        # 1. Check helper instantiation & delegation
        mixin = AutoFixMixin()
        self.assertTrue(hasattr(mixin, "_helpers"), "AutoFixMixin must have _helpers")
        self.assertIsInstance(mixin._helpers, AutoFixHelpersMixin)

        # 2. Check _verify_realtime_patch staticmethod exists
        self.assertTrue(
            hasattr(AutoFixHelpersMixin, "_verify_realtime_patch"),
            "AutoFixHelpersMixin must have _verify_realtime_patch",
        )

        # 3. Call _verify_realtime_patch with empty context to test fail-closed handling
        class MockCtx:
            pre_fix_content = ""
            bug = None
        mock_ctx = MockCtx()
        mock_ctx.bug = type("MockBug", (), {"tier": 1, "suggested_fix": ""})()

        res = AutoFixHelpersMixin._verify_realtime_patch(mock_ctx, "dummy.py")
        self.assertIsNotNone(res)
        self.assertEqual(res.get("action"), "skipped")
        self.assertTrue(res.get("realtime_blocked"))
        print("[AutoFix Contract] AutoFixHelpersMixin._verify_realtime_patch contract verified")

    def test_08_full_empirical_proof_script_exit_code(self):
        """Verify scripts/sandbox_autofix_empirical_proof.py runs cleanly with exit code 0."""
        cmd = [
            sys.executable,
            str(REPO_ROOT / "scripts/sandbox_autofix_empirical_proof.py"),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(REPO_ROOT))
        self.assertEqual(
            proc.returncode,
            0,
            f"scripts/sandbox_autofix_empirical_proof.py exited with {proc.returncode}:\n{proc.stderr}",
        )
        self.assertIn("EMPIRICAL PROOF PASSED: ALL 7 SCENARIOS 100% VERIFIED", proc.stdout)
        print("[Empirical Suite] All 7 scenarios 100% verified with exit code 0")


if __name__ == "__main__":
    unittest.main()
