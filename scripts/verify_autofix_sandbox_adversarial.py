#!/usr/bin/env python3
"""
scripts/verify_autofix_sandbox_adversarial.py
=============================================================================
EMPIRICAL ADVERSARIAL STRESS TEST BATTERY
Target: AutoFix Sandbox Rollback & Fail-Closed Defenses
        (scripts/sandbox_autofix_empirical_proof.py)

Adversarial Objectives:
  1. Stress Test Scenario 4 (Syntax Corruption):
     - Test multiple distinct syntax corruption attacks (unterminated strings,
       unmatched brackets, invalid tokens, keyword identifiers, zero indentation, incomplete expr).
     - Prove 0-byte physical file corruption across all attacks.
  2. Stress Test Scenario 5 (Security Assertion Defense & Rollback):
     - Test subtle assertion weakening (e.g. bypassing length checks).
     - Test multi-file atomic transaction rollback (all-or-nothing).
     - Verify pre-patch and post-rollback SHA-256 byte parity.
     - Test rollback idempotency.
  3. Stress Test Scenario 6 (Protected Path Traversal & Worker Root):
     - Test mixed slash and case-variant normalization.
     - Test complex traversal sequences against storage path sanitizer.
     - Test worker root escape attempts.
  4. Non-Zero Exit Code Verification:
     - Inject adversarial failure into proof runner and empirically verify
       that exit code is non-zero (proc.returncode != 0).

ARC-01: Line count strictly <= 800 LOC.
=============================================================================
"""
from __future__ import annotations

import hashlib
import io
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# Windows console encoding fix
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, io.UnsupportedOperation):
        pass

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scp.autofix.classifier import BugReport, BugTier
from scp.autofix.deterministic_worker import DeterministicWorker, WorkerConfig
from scp.autofix.engine_parts.verify_mixin import VerifyMixin
from scp.autofix.path_guard import sanitize_storage_path
from scp.autofix.runner_phases.ast_scan import _is_protected_path
from scp.autofix.shadow_snapshot import ShadowSnapshotManager
from scp.autofix.validate_patch import validate_patch
from scp.core.code_evolution_agent import CodeEvolutionAgent

# ANSI formatting
C_RESET = "\033[0m"
C_BOLD = "\033[1m"
C_GREEN = "\033[32m"
C_RED = "\033[31m"
C_CYAN = "\033[36m"


def log_probe(probe_id: str, desc: str, passed: bool, detail: str = "") -> None:
    tag = f"{C_GREEN}PASS{C_RESET}" if passed else f"{C_RED}FAIL{C_RESET}"
    print(f"[{tag}] {C_BOLD}{probe_id}{C_RESET}: {desc}")
    if detail:
        print(f"       {C_CYAN}-> {detail}{C_RESET}")


# =============================================================================
# CHALLENGE 1: SYNTAX CORRUPTION ADVERSARIAL STRESS
# =============================================================================
def challenge_syntax_corruption_rejection() -> bool:
    print(f"\n{C_BOLD}--- Challenge 1: Adversarial Syntax Corruption Variations ---{C_RESET}")
    attacks = [
        ("SYNTAX_01_unterminated_str", '    return "unclosed string literal\n'),
        ("SYNTAX_02_unmatched_bracket", '    return [1, 2, 3\n'),
        ("SYNTAX_03_invalid_token", '    return x / @@@@\n'),
        ("SYNTAX_04_zero_indentation", 'return x / y\n'),
        ("SYNTAX_05_keyword_as_ident", '    class = 42\n    return class\n'),
        ("SYNTAX_06_incomplete_expr", '    return x +\n'),
    ]

    all_passed = True
    with tempfile.TemporaryDirectory(prefix="adv_sc4_") as td:
        s_path = Path(td)
        target = s_path / "math_service.py"
        orig_code = (
            "def safe_divide(x: float, y: float) -> float:\n"
            "    if y == 0.0:\n"
            "        return 0.0\n"
            "    return x / y\n"
        )
        orig_sha = hashlib.sha256(orig_code.encode("utf-8")).hexdigest()

        for attack_id, corrupt_replace in attacks:
            # Re-initialize clean file state for each attack
            target.write_text(orig_code, encoding="utf-8")

            # 1. Test validate_patch
            val_res = validate_patch(str(target), "    return x / y\n", corrupt_replace)
            val_rejected = (val_res.valid is False) and (val_res.syntax_ok is False)

            # 2. Test CodeEvolutionAgent preflight gate
            fenced = f"```python\ndef safe_divide(x: float, y: float) -> float:\n{corrupt_replace}```"
            agent = CodeEvolutionAgent.__new__(CodeEvolutionAgent)
            ok, _critique = agent._try_patch_with_feedback(target, fenced)
            preflight_rejected = (ok is False)

            # 3. Check physical file checksum (0-byte corruption proof)
            post_content = target.read_text(encoding="utf-8")
            post_sha = hashlib.sha256(post_content.encode("utf-8")).hexdigest()
            file_intact = (orig_sha == post_sha)

            probe_ok = val_rejected and preflight_rejected and file_intact
            if not probe_ok:
                all_passed = False
            log_probe(
                attack_id,
                f"validate_patch rejected={val_rejected}, agent rejected={preflight_rejected}, file_intact={file_intact}",
                probe_ok,
                f"Sha: {orig_sha[:10]} == {post_sha[:10]}"
            )

    return all_passed


# =============================================================================
# CHALLENGE 2: SECURITY ASSERTION DEFENSE & MULTI-FILE ROLLBACK
# =============================================================================
def challenge_security_defense_and_rollback() -> bool:
    print(f"\n{C_BOLD}--- Challenge 2: Adversarial Security Assertion & Multi-File Rollback ---{C_RESET}")
    all_passed = True

    with tempfile.TemporaryDirectory(prefix="adv_sc5_") as td:
        s_path = Path(td)
        src_dir = s_path / "src"
        tests_dir = s_path / "tests"
        src_dir.mkdir()
        tests_dir.mkdir()

        (s_path / "conftest.py").write_text(
            "def pytest_addoption(parser):\n"
            "    parser.addoption('--timeout', action='store', default=None)\n",
            encoding="utf-8",
        )

        file_a = src_dir / "auth.py"
        file_b = src_dir / "tokens.py"
        code_a = (
            "def verify_auth(user: str, token: str) -> bool:\n"
            "    assert token is not None, 'token must be provided'\n"
            "    assert len(token) >= 8, 'token too short'\n"
            "    return True\n"
        )
        code_b = (
            "def format_token(prefix: str, token: str) -> str:\n"
            "    return f'{prefix}_{token}'\n"
        )
        file_a.write_text(code_a, encoding="utf-8")
        file_b.write_text(code_b, encoding="utf-8")
        sha_a_orig = hashlib.sha256(code_a.encode("utf-8")).hexdigest()
        sha_b_orig = hashlib.sha256(code_b.encode("utf-8")).hexdigest()

        # Legacy backups
        file_a.with_suffix(".py.tier3bak").write_text(code_a, encoding="utf-8")
        file_b.with_suffix(".py.tier3bak").write_text(code_b, encoding="utf-8")

        test_file = tests_dir / "test_auth.py"
        test_file.write_text(
            "import pytest\n"
            "import sys\n"
            "from pathlib import Path\n"
            "sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))\n"
            "from auth import verify_auth\n"
            "\n"
            "def test_valid():\n"
            "    assert verify_auth('user', 'long_enough_token') is True\n"
            "\n"
            "def test_reject_short():\n"
            "    with pytest.raises(AssertionError, match='token too short'):\n"
            "        verify_auth('user', 'short')\n",
            encoding="utf-8",
        )

        # Attack 2a: Weakened assertion (replace length 8 with length 2)
        shadow_mgr = ShadowSnapshotManager(shadow_dir=s_path / "shadow")
        tx_id = shadow_mgr.begin([file_a, file_b], bug_id="ADV_WEAKEN_ASSERTION")

        # Mutate file_a with weakened assertion (bypasses security invariant)
        weakened_code_a = (
            "def verify_auth(user: str, token: str) -> bool:\n"
            "    assert token is not None, 'token must be provided'\n"
            "    assert len(token) >= 2, 'token too short'\n"
            "    return True\n"
        )
        file_a.write_text(weakened_code_a, encoding="utf-8")

        # Mutate file_b with benign change
        mutated_code_b = (
            "def format_token(prefix: str, token: str) -> str:\n"
            "    return f'{prefix.upper()}_{token}'\n"
        )
        file_b.write_text(mutated_code_b, encoding="utf-8")

        # Run VerifyMixin
        bug = BugReport(
            file=str(file_a),
            line=3,
            bug_type="WeakenedAssertion",
            description="Weakened assertion from len>=8 to len>=2",
            suggested_fix="",
            tier=BugTier.TIER_1_AUTO_FIX,
        )
        mixin = VerifyMixin()
        is_valid, reason = mixin._verify_fix(file_a, [bug])

        # Clean ascii reason for printing
        safe_reason = reason.replace("\u2192", "->")

        # Verify regression caught
        regression_caught = (is_valid is False) and ("regression" in reason.lower() or "rollback" in reason.lower())
        log_probe(
            "SEC_01_weakened_assertion_regression",
            f"VerifyMixin caught regression: is_valid={is_valid}",
            regression_caught,
            f"Reason: {safe_reason[:70]}..."
        )
        if not regression_caught:
            all_passed = False

        # Execute rollback and verify BOTH files restored (atomic all-or-nothing)
        rb_ok = shadow_mgr.rollback(tx_id, reason=reason)
        sha_a_post = hashlib.sha256(file_a.read_text(encoding="utf-8").encode("utf-8")).hexdigest()
        sha_b_post = hashlib.sha256(file_b.read_text(encoding="utf-8").encode("utf-8")).hexdigest()
        both_restored = (sha_a_orig == sha_a_post) and (sha_b_orig == sha_b_post)

        log_probe(
            "SEC_02_atomic_multi_file_rollback",
            f"Rollback status={rb_ok}, both files restored to exact pre-patch SHA-256",
            both_restored,
            f"A: {sha_a_orig[:10]}=={sha_a_post[:10]}, B: {sha_b_orig[:10]}=={sha_b_post[:10]}"
        )
        if not both_restored:
            all_passed = False

        # Attack 2b: Rollback idempotency (calling rollback again on rolled-back tx)
        rb_second = shadow_mgr.rollback(tx_id, reason="Second rollback attempt")
        # Should not crash and file should remain intact
        sha_a_after_second = hashlib.sha256(file_a.read_text(encoding="utf-8").encode("utf-8")).hexdigest()
        idempotent = (sha_a_orig == sha_a_after_second)
        log_probe(
            "SEC_03_rollback_idempotency",
            f"Second rollback executed cleanly without corruption, returned={rb_second}",
            idempotent,
            f"File A SHA intact: {sha_a_orig[:10]} == {sha_a_after_second[:10]}"
        )
        if not idempotent:
            all_passed = False

    return all_passed


# =============================================================================
# CHALLENGE 3: PROTECTED PATH TRAVERSAL & ESCAPES
# =============================================================================
def challenge_protected_paths_and_traversal() -> bool:
    print(f"\n{C_BOLD}--- Challenge 3: Adversarial Path Traversal & Normalization ---{C_RESET}")
    all_passed = True

    # Sub-challenge 3a: Slash & case variations
    path_variants = [
        ("scp/task_kernel.py", True),
        ("SCP/TASK_KERNEL.PY", True),
        ("scp\\task_kernel.py", True),
        ("./scp/task_kernel.py", True),
        ("scp/persistence/db.py", True),
        ("SCP/SECURITY/AUTH.PY", True),
        ("spec/protected_invariants.yaml", True),
        ("tests/T00_integrity/test_meta_audit.py", True),
        ("scp/autofix/engine.py", True),
        ("scp/unprotected_dummy_file.py", False),
    ]

    for variant, expected in path_variants:
        is_prot = _is_protected_path(variant)
        ok = (is_prot == expected)
        if not ok:
            all_passed = False
        log_probe(f"PATH_NORM_{variant}", f"expected={expected}, actual={is_prot}", ok)

    # Sub-challenge 3b: Path traversal in sanitize_storage_path
    traversals = [
        ("../../../../../windows/system32/calc.exe", False),
        ("..\\..\\..\\windows\\system32\\cmd.exe", False),
        ("data/subdir/../../../../secret.key", False),
        ("normal/file/path.jsonl", True),
    ]
    for cand, _is_clean in traversals:
        sanitized = sanitize_storage_path(cand, default="data/fallback_audit.jsonl")
        contains_parent = (".." in sanitized.parts)
        ok = (not contains_parent)
        if not ok:
            all_passed = False
        log_probe(
            f"TRAVERSAL_{cand[:25]}",
            f"sanitized={sanitized.name}, contains_parent={contains_parent}",
            ok
        )

    # Sub-challenge 3c: Worker root boundary escapes
    with tempfile.TemporaryDirectory(prefix="adv_worker_") as td:
        root = Path(td)
        allowed = root / "allowed"
        allowed.mkdir()
        outside = root / "outside"
        outside.mkdir()
        forbidden_file = outside / "secret.txt"
        forbidden_file.write_text("TOP_SECRET", encoding="utf-8")

        worker_data = root / "wdata"
        worker_data.mkdir()
        worker = DeterministicWorker(WorkerConfig(data_dir=worker_data, allowed_root=allowed))

        # Test escape via relative traversal
        escape_path = str(allowed / ".." / "outside" / "secret.txt")
        allowed_res, reason = worker._path_allowed(escape_path)
        escape_blocked = (allowed_res is False) and ("outside allowed root" in reason)
        if not escape_blocked:
            all_passed = False
        log_probe(
            "WORKER_ESCAPE_traversal",
            f"Worker blocked relative traversal escape: allowed={allowed_res}, reason={reason}",
            escape_blocked
        )

        del worker
        import gc
        gc.collect()

    return all_passed


# =============================================================================
# CHALLENGE 4: PROOF RUNNER NON-ZERO EXIT CODE ON ASSERTION FAILURE
# =============================================================================
def challenge_exit_code_on_failure() -> bool:
    print(f"\n{C_BOLD}--- Challenge 4: Proof Runner Non-Zero Exit Code on Failure ---{C_RESET}")

    # Test 4a: Normal execution exits 0
    proc_normal = subprocess.run(
        [sys.executable, "scripts/sandbox_autofix_empirical_proof.py"],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
        check=False,
    )
    normal_zero = (proc_normal.returncode == 0)
    log_probe(
        "EXIT_01_intact_suite_returns_0",
        f"Normal run exit code={proc_normal.returncode}",
        normal_zero,
        f"Summary present: {'EMPIRICAL PROOF PASSED' in proc_normal.stdout}"
    )

    # Test 4b: Injected failure in a scenario must force exit code 1
    injection_code = (
        "import sys\n"
        "from pathlib import Path\n"
        "sys.path.insert(0, str(Path('.').resolve()))\n"
        "import scripts.sandbox_autofix_empirical_proof as proof\n"
        "\n"
        "def failing_scenario_1():\n"
        "    raise AssertionError('ADVERSARIAL_INJECTED_FAILURE_IN_SCENARIO_1')\n"
        "\n"
        "proof.run_scenario_1_unused_import_deletion = failing_scenario_1\n"
        "sys.exit(proof.main())\n"
    )

    proc_injected = subprocess.run(
        [sys.executable, "-c", injection_code],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
        check=False,
    )
    injected_nonzero = (proc_injected.returncode != 0)
    failure_logged = "SCENARIO EXCEPTION" in proc_injected.stdout or "EMPIRICAL PROOF FAILED" in proc_injected.stdout

    log_probe(
        "EXIT_02_injected_failure_returns_nonzero",
        f"Injected failure exit code={proc_injected.returncode} (expected != 0)",
        injected_nonzero and failure_logged,
        f"Stdout excerpt: {'EMPIRICAL PROOF FAILED' in proc_injected.stdout}"
    )

    return normal_zero and injected_nonzero and failure_logged


# =============================================================================
# MAIN ORCHESTRATION
# =============================================================================
def main() -> int:
    t0 = time.perf_counter()
    header = "=" * 78
    print(f"\n{C_BOLD}{C_CYAN}{header}{C_RESET}")
    print(f"{C_BOLD}{C_CYAN}  ADVERSARIAL STRESS TEST HARNESS -- AUTOFIX SANDBOX & FAIL-CLOSED DEFENSES{C_RESET}")
    print(f"{C_BOLD}{C_CYAN}{header}{C_RESET}")

    c1 = challenge_syntax_corruption_rejection()
    c2 = challenge_security_defense_and_rollback()
    c3 = challenge_protected_paths_and_traversal()
    c4 = challenge_exit_code_on_failure()

    elapsed = (time.perf_counter() - t0) * 1000.0

    print(f"\n{C_BOLD}STRESS HARNESS SUMMARY:{C_RESET}")
    print(f"  * Challenge 1 (Syntax Corruption Rejection):    {'PASS' if c1 else 'FAIL'}")
    print(f"  * Challenge 2 (Security Defense & Rollback):     {'PASS' if c2 else 'FAIL'}")
    print(f"  * Challenge 3 (Protected Path Traversal):       {'PASS' if c3 else 'FAIL'}")
    print(f"  * Challenge 4 (Exit Code on Failure):           {'PASS' if c4 else 'FAIL'}")
    print(f"  * Total Execution Time:                         {elapsed:.1f} ms")

    # LOC check
    this_file = Path(__file__).resolve()
    loc = len(this_file.read_text(encoding="utf-8").splitlines())
    print(f"  * Harness LOC:                                  {loc} LOC (<= 800 limit)")

    if c1 and c2 and c3 and c4 and loc <= 800:
        print(f"\n{C_BOLD}{C_GREEN}>>> ALL ADVERSARIAL CHALLENGES EMPIRICALLY PASSED <<< {C_RESET}\n")
        return 0
    else:
        print(f"\n{C_BOLD}{C_RED}>>> ONE OR MORE ADVERSARIAL CHALLENGES FAILED <<< {C_RESET}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
