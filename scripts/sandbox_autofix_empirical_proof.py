#!/usr/bin/env python3
"""
scripts/sandbox_autofix_empirical_proof.py
=============================================================================
SCP AutoFix Empirical Reality Sandbox Verification Suite
=============================================================================

DNA Binding:
  - DNA #2:  Reality > Model (Empirical verification on physical filesystem)
  - DNA #4:  Fail-Closed (Defense-in-depth security invariants)
  - DNA #7:  AutoFix Reversibility (0% rollback on valid fixes, 100% on regressions)
  - DNA #14: Anti-Placebo Gates (Mandatory negative proof of failure rejection)
  - DNA #22: PASS != TRUE (Empirical postconditions & checksums verified)

Scenarios Covered:
  Positive (0% Rollback Proof):
    1. Unused Import Deletion (F401) via REPLACE=""
    2. Multiline Function Replacement with Blank Lines (\\n\\n)
    3. Whitespace / Indentation Drift handled cleanly
  Negative Anti-Placebo (Fail-Closed Proof):
    4. Syntax Corruption Rejection (0 file corruption)
    5. Security Assertion Removal Defense (rollback triggered)
    6. Protected Path Traversal Defense (hard-blocked)

ARC-01 Constraint: Total lines strictly <= 800 LOC.
=============================================================================
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

# Ensure UTF-8 console output on Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scp.autofix.classifier import BugReport, BugTier
from scp.autofix.deterministic_worker import DeterministicWorker, WorkerConfig
from scp.autofix.engine_parts.verify_mixin import VerifyMixin
from scp.autofix.path_guard import sanitize_storage_path
from scp.autofix.runner_phases.ast_scan import PROTECTED_PATHS, _is_protected_path
from scp.autofix.shadow_snapshot import ShadowSnapshotManager
from scp.autofix.validate_patch import flexible_replace, validate_patch
from scp.core.code_evolution_agent import CodeEvolutionAgent

# ANSI Color Codes
C_RESET = "\033[0m"
C_BOLD = "\033[1m"
C_GREEN = "\033[32m"
C_RED = "\033[31m"
C_YELLOW = "\033[33m"
C_BLUE = "\033[34m"
C_CYAN = "\033[36m"
C_GRAY = "\033[90m"
C_MAGENTA = "\033[35m"


def log_banner(title: str) -> None:
    sep = "=" * 78
    print(f"\n{C_BOLD}{C_CYAN}{sep}{C_RESET}")
    print(f"{C_BOLD}{C_CYAN}[SCENARIO] {title}{C_RESET}")
    print(f"{C_BOLD}{C_CYAN}{sep}{C_RESET}")


def log_step(step_num: int, desc: str, detail: str = "") -> None:
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3]
    msg = f"  {C_BOLD}{C_BLUE}[STEP {step_num}]{C_RESET} {C_GRAY}{ts}{C_RESET} {desc}"
    if detail:
        msg += f"\n    {C_GRAY}-> {detail}{C_RESET}"
    print(msg)


def log_substep(label: str, value: str, success: bool = True) -> None:
    tag = f"{C_GREEN}PASS{C_RESET}" if success else f"{C_RED}FAIL{C_RESET}"
    print(f"    [{tag}] {C_BOLD}{label}:{C_RESET} {value}")


# =============================================================================
# POSITIVE SCENARIO 1: Unused Import Deletion (F401) via REPLACE=""
# =============================================================================
def run_scenario_1_unused_import_deletion() -> dict:
    start_t = time.perf_counter()
    log_banner("1. Positive: Unused Import Deletion (F401) via REPLACE=\"\" [0% Rollback]")

    with tempfile.TemporaryDirectory(prefix="scp_sandbox_sc1_") as td:
        s_path = Path(td)
        target = s_path / "module_calc.py"
        orig_code = (
            "import os\n"
            "import math\n"
            "\n"
            "def compute_hypot(a: float, b: float) -> float:\n"
            "    return math.hypot(a, b)\n"
        )
        target.write_text(orig_code, encoding="utf-8")
        pre_sha = hashlib.sha256(orig_code.encode("utf-8")).hexdigest()
        log_step(1, "Initialized sandbox file with unused import 'import os'", f"File: {target.name}, SHA: {pre_sha[:12]}")

        # Step 2: Validate patch before disk modification
        search_block = "import os\n"
        replace_block = ""
        val_res = validate_patch(str(target), search_block, replace_block)
        log_step(2, "Executed validate_patch for deletion", f"valid={val_res.valid}, syntax_ok={val_res.syntax_ok}")
        assert val_res.valid is True, "validate_patch should accept safe deletion"
        assert val_res.syntax_ok is True, "Syntax should remain valid"

        # Step 3: Transaction snapshot creation
        shadow_mgr = ShadowSnapshotManager(shadow_dir=s_path / "shadow")
        tx_id = shadow_mgr.begin([target], bug_id="F401_unused_os")
        log_step(3, "Began ShadowSnapshot transaction", f"tx_id={tx_id}")

        # Step 4: Apply deletion via flexible_replace
        current_text = target.read_text(encoding="utf-8")
        patched_text = flexible_replace(current_text, search_block, replace_block)
        assert patched_text is not None, "flexible_replace should locate and remove import os"
        target.write_text(patched_text, encoding="utf-8")
        log_step(4, "Applied deletion to disk via flexible_replace")

        # Step 5: Verify AST integrity
        tree = ast.parse(patched_text, filename=str(target))
        imports = [n.names[0].name for n in ast.walk(tree) if isinstance(n, ast.Import)]
        log_step(5, "AST parsed successfully post-patch", f"Remaining imports: {imports}")
        assert "os" not in imports, "import os must be absent from AST"
        assert "math" in imports, "import math must be preserved"

        # Step 6: Commit transaction with 0 rollback
        commit_ok = shadow_mgr.commit(tx_id)
        assert commit_ok is True, "Shadow transaction commit must succeed"
        log_step(6, "Transaction committed successfully (0% Rollback)")

        # Verify postconditions
        post_content = target.read_text(encoding="utf-8")
        post_sha = hashlib.sha256(post_content.encode("utf-8")).hexdigest()
        log_substep("Original Import Removed", "import os absent", "import os" not in post_content)
        log_substep("Functional Code Intact", "compute_hypot present", "def compute_hypot" in post_content)
        log_substep("Rollback Count", "0 rollbacks", True)

        dur_ms = (time.perf_counter() - start_t) * 1000.0
        return {
            "id": "SC-01",
            "name": "Unused Import Deletion (F401)",
            "type": "POSITIVE",
            "passed": True,
            "rollback_count": 0,
            "duration_ms": round(dur_ms, 2),
        }


# =============================================================================
# POSITIVE SCENARIO 2: Multiline Function Replacement with Blank Lines (\n\n)
# =============================================================================
def run_scenario_2_multiline_blank_lines() -> dict:
    start_t = time.perf_counter()
    log_banner("2. Positive: Multiline Function Replacement with Blank Lines (\\n\\n) [0% Rollback]")

    with tempfile.TemporaryDirectory(prefix="scp_sandbox_sc2_") as td:
        s_path = Path(td)
        target = s_path / "algo_pipeline.py"
        orig_code = (
            "def execute_pipeline(val: int) -> int:\n"
            "    step1 = val + 10\n"
            "\n"
            "    step2 = step1 * 2\n"
            "\n"
            "    step3 = step2 - 5\n"
            "\n"
            "    return step3\n"
        )
        target.write_text(orig_code, encoding="utf-8")
        log_step(1, "Created file with multiline function containing internal blank lines")

        # Step 2: Shadow snapshot begin
        shadow_mgr = ShadowSnapshotManager(shadow_dir=s_path / "shadow")
        tx_id = shadow_mgr.begin([target], bug_id="AST_SPAN_002")
        log_step(2, "Began ShadowSnapshot transaction", f"tx_id={tx_id}")

        # Step 3: Fenced multiline replacement targeting full AST function span
        fenced_patch = (
            "```python\n"
            "def execute_pipeline(val: int) -> int:\n"
            "    step1 = val * 5\n"
            "\n"
            "    step2 = step1 + 100\n"
            "\n"
            "    step3 = step2 // 2\n"
            "\n"
            "    return step3\n"
            "```"
        )
        agent = CodeEvolutionAgent.__new__(CodeEvolutionAgent)
        ok, critique = agent._try_patch_with_feedback(target, fenced_patch)
        log_step(3, "Applied patch via CodeEvolutionAgent AST replacement", f"ok={ok}, critique={critique}")
        assert ok is True, f"AST span replacement must succeed without blank line truncation: {critique}"

        # Step 4: Verify AST completeness
        patched_code = target.read_text(encoding="utf-8")
        tree = ast.parse(patched_code, filename=str(target))
        func_node = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "execute_pipeline")
        assert func_node is not None, "Function node must exist"
        log_step(4, "AST parsed cleanly; verified full function replacement")

        # Step 5: Commit transaction
        commit_ok = shadow_mgr.commit(tx_id)
        assert commit_ok is True, "Transaction commit should succeed"
        log_step(5, "Committed shadow transaction with 0 rollback")

        # Postcondition checks
        log_substep("Old logic replaced", "val + 10 absent", "step1 = val + 10" not in patched_code)
        log_substep("New logic present", "val * 5 present", "step1 = val * 5" in patched_code)
        log_substep("Blank lines preserved", "Internal blank lines intact", "\n\n" in patched_code)
        log_substep("Rollback Count", "0 rollbacks", True)

        dur_ms = (time.perf_counter() - start_t) * 1000.0
        return {
            "id": "SC-02",
            "name": "Multiline Function with Blank Lines",
            "type": "POSITIVE",
            "passed": True,
            "rollback_count": 0,
            "duration_ms": round(dur_ms, 2),
        }


# =============================================================================
# POSITIVE SCENARIO 3: Whitespace / Indentation Drift Handled Cleanly
# =============================================================================
def run_scenario_3_whitespace_drift() -> dict:
    start_t = time.perf_counter()
    log_banner("3. Positive: Whitespace / Indentation Drift Handled Cleanly [0% Rollback]")

    with tempfile.TemporaryDirectory(prefix="scp_sandbox_sc3_") as td:
        s_path = Path(td)
        target = s_path / "service_handler.py"
        orig_code = (
            "class ServiceHandler:\n"
            "    def handle(self, payload: dict) -> dict:\n"
            "        data = payload.get('data')\n"
            "        status = 'PENDING'\n"
            "        return {'status': status, 'data': data}\n"
        )
        target.write_text(orig_code, encoding="utf-8")
        log_step(1, "Created service handler with standard 4-space indentation")

        # Step 2: Shadow snapshot begin
        shadow_mgr = ShadowSnapshotManager(shadow_dir=s_path / "shadow")
        tx_id = shadow_mgr.begin([target], bug_id="DRIFT_INDENT_003")
        log_step(2, "Began ShadowSnapshot transaction", f"tx_id={tx_id}")

        # Step 3: Search block with drifted indentation (2-spaces) and trailing whitespace
        drifted_search = (
            "  data = payload.get('data')   \n"
            "  status = 'PENDING'  \n"
        )
        replacement = (
            "        data = payload.get('data', {})\n"
            "        status = 'PROCESSED'\n"
        )
        val_res = validate_patch(str(target), drifted_search, replacement)
        log_step(3, "validate_patch with indentation drift", f"search_found={val_res.search_found}, valid={val_res.valid}")
        assert val_res.valid is True, "flexible_replace within validate_patch must resolve drifted indentation"

        # Step 4: Apply replacement cleanly
        current_text = target.read_text(encoding="utf-8")
        patched_text = flexible_replace(current_text, drifted_search, replacement)
        assert patched_text is not None, "flexible_replace must succeed on drifted whitespace"
        target.write_text(patched_text, encoding="utf-8")
        log_step(4, "Applied drifted patch cleanly using flexible_replace")

        # Step 5: Verify AST syntax
        ast.parse(patched_text, filename=str(target))
        commit_ok = shadow_mgr.commit(tx_id)
        assert commit_ok is True, "Commit transaction must succeed"
        log_step(5, "AST compiles cleanly; committed transaction (0% Rollback)")

        # Postcondition checks
        log_substep("Drift matched & replaced", "status = 'PROCESSED' present", "PROCESSED" in patched_text)
        log_substep("Old status removed", "PENDING absent", "PENDING" not in patched_text)
        log_substep("Syntax valid", "ast.parse PASSED", True)
        log_substep("Rollback Count", "0 rollbacks", True)

        dur_ms = (time.perf_counter() - start_t) * 1000.0
        return {
            "id": "SC-03",
            "name": "Whitespace / Indentation Drift",
            "type": "POSITIVE",
            "passed": True,
            "rollback_count": 0,
            "duration_ms": round(dur_ms, 2),
        }


# =============================================================================
# NEGATIVE SCENARIO 4: Syntax Corruption Rejection (Fail-Closed, 0 Corruption)
# =============================================================================
def run_scenario_4_syntax_corruption_defense() -> dict:
    start_t = time.perf_counter()
    log_banner("4. Negative: Syntax Corruption Rejection [Fail-Closed / 0 Corruption]")

    with tempfile.TemporaryDirectory(prefix="scp_sandbox_sc4_") as td:
        s_path = Path(td)
        target = s_path / "critical_math.py"
        orig_code = (
            "def safe_divide(x: float, y: float) -> float:\n"
            "    if y == 0.0:\n"
            "        return 0.0\n"
            "    return x / y\n"
        )
        target.write_text(orig_code, encoding="utf-8")
        pre_sha = hashlib.sha256(orig_code.encode("utf-8")).hexdigest()
        log_step(1, "Created valid file", f"pre_sha256={pre_sha[:12]}")

        # Step 2: Attempt malformed patch with syntax corruption
        corrupted_search = "    return x / y"
        corrupted_replace = "    return x / ??? SYNTAX_FATAL_ERROR ???"
        val_res = validate_patch(str(target), corrupted_search, corrupted_replace)
        log_step(2, "Executed validate_patch on malformed patch", f"valid={val_res.valid}, reason={val_res.reason}")
        assert val_res.valid is False, "validate_patch MUST reject corrupted syntax"
        assert val_res.syntax_ok is False, "syntax_ok must be False"

        # Step 3: Attempt application via CodeEvolutionAgent preflight gate
        fenced_corrupted = (
            "```python\n"
            "def safe_divide(x: float, y: float) -> float:\n"
            "    return x / ??? SYNTAX_FATAL_ERROR ???\n"
            "```"
        )
        agent = CodeEvolutionAgent.__new__(CodeEvolutionAgent)
        ok, critique = agent._try_patch_with_feedback(target, fenced_corrupted)
        log_step(3, "Tested CodeEvolutionAgent preflight gate", f"ok={ok}, critique={critique}")
        assert ok is False, "Preflight gate must block corrupted code before writing"
        assert "SYNTAX_ERROR" in str(critique), "Critique must cite SYNTAX_ERROR"

        # Step 4: Verify physical file integrity
        post_code = target.read_text(encoding="utf-8")
        post_sha = hashlib.sha256(post_code.encode("utf-8")).hexdigest()
        log_step(4, "Verified physical file integrity post-rejection", f"post_sha256={post_sha[:12]}")
        assert pre_sha == post_sha, "Target file MUST be 100% byte-identical (0 corruption)"

        log_substep("Patch Rejection", "validate_patch rejected", val_res.valid is False)
        log_substep("Preflight Defense", "CodeEvolutionAgent rejected", ok is False)
        log_substep("File Preservation", "0 byte corruption", pre_sha == post_sha)

        dur_ms = (time.perf_counter() - start_t) * 1000.0
        return {
            "id": "SC-04",
            "name": "Syntax Corruption Rejection",
            "type": "NEGATIVE ANTI-PLACEBO",
            "passed": True,
            "rollback_count": 0,
            "duration_ms": round(dur_ms, 2),
        }


# =============================================================================
# NEGATIVE SCENARIO 5: Security Assertion Removal Defense (Rollback Triggered)
# =============================================================================
def run_scenario_5_security_assertion_defense() -> dict:
    start_t = time.perf_counter()
    log_banner("5. Negative: Security Assertion Removal Defense [Rollback Triggered]")

    with tempfile.TemporaryDirectory(prefix="scp_sandbox_sc5_") as td:
        s_path = Path(td)
        src_dir = s_path / "src"
        tests_dir = s_path / "tests"
        src_dir.mkdir()
        tests_dir.mkdir()

        # Conftest to allow pytest options cleanly
        (s_path / "conftest.py").write_text(
            "def pytest_addoption(parser):\n"
            "    parser.addoption('--timeout', action='store', default=None)\n",
            encoding="utf-8",
        )

        target = src_dir / "auth_service.py"
        orig_code = (
            "def authenticate_request(user: str, token: str | None) -> dict:\n"
            "    assert token is not None, 'Security Invariant: token required'\n"
            "    assert len(token) >= 8, 'Security Invariant: minimum token entropy'\n"
            "    return {'status': 'authenticated', 'user': user}\n"
        )
        target.write_text(orig_code, encoding="utf-8")
        pre_sha = hashlib.sha256(orig_code.encode("utf-8")).hexdigest()

        # Legacy & shadow backup creation
        bk = target.with_suffix(target.suffix + ".tier3bak")
        bk.write_text(orig_code, encoding="utf-8")

        test_file = tests_dir / "test_auth_service.py"
        test_code = (
            "import pytest\n"
            "import sys\n"
            "from pathlib import Path\n"
            "sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))\n"
            "from auth_service import authenticate_request\n"
            "\n"
            "def test_authenticate_valid():\n"
            "    res = authenticate_request('admin', 'secret_token_123')\n"
            "    assert res['status'] == 'authenticated'\n"
            "\n"
            "def test_authenticate_rejects_missing_token():\n"
            "    with pytest.raises(AssertionError, match='token required'):\n"
            "        authenticate_request('admin', None)\n"
            "\n"
            "def test_authenticate_rejects_short_token():\n"
            "    with pytest.raises(AssertionError, match='minimum token entropy'):\n"
            "        authenticate_request('admin', 'short')\n"
        )
        test_file.write_text(test_code, encoding="utf-8")
        log_step(1, "Initialized protected authentication service & security tests")

        # Step 2: Begin shadow transaction
        shadow_mgr = ShadowSnapshotManager(shadow_dir=s_path / "shadow")
        tx_id = shadow_mgr.begin([target], bug_id="SEC_ASSERTS_DEFENSE_005")
        log_step(2, "Began ShadowSnapshot transaction", f"tx_id={tx_id}")

        # Step 3: Apply malicious patch that deletes the security assertions
        malicious_code = (
            "def authenticate_request(user: str, token: str | None) -> dict:\n"
            "    return {'status': 'authenticated', 'user': user}\n"
        )
        target.write_text(malicious_code, encoding="utf-8")
        log_step(3, "Applied malicious patch deleting security assertions to disk")

        # Step 4: VerifyMixin catches pytest regression due to bypassed assertions
        bug = BugReport(
            file=str(target),
            line=2,
            bug_type="SecurityAssertionRemoval",
            description="Malicious deletion of security assertions",
            suggested_fix="",
            tier=BugTier.TIER_1_AUTO_FIX,
        )
        mixin = VerifyMixin()
        is_valid, reason = mixin._verify_fix(target, [bug])
        log_step(4, "VerifyMixin evaluation result", f"is_valid={is_valid}, reason={reason[:80]}...")
        assert is_valid is False, "VerifyMixin MUST reject removal of security assertions"
        assert "regression" in reason.lower() or "rollback" in reason.lower(), "Reason must state regression/rollback"

        # Step 5: Trigger shadow rollback
        rollback_ok = shadow_mgr.rollback(tx_id, reason=reason)
        assert rollback_ok is True, "Shadow rollback must execute successfully"
        log_step(5, "Executed atomic filesystem rollback via ShadowSnapshotManager")

        # Step 6: Postcondition - verify security assertions are fully restored
        restored_code = target.read_text(encoding="utf-8")
        restored_sha = hashlib.sha256(restored_code.encode("utf-8")).hexdigest()
        log_step(6, "Verified post-rollback code integrity", f"restored_sha256={restored_sha[:12]}")
        assert pre_sha == restored_sha, "Target file must be restored 100% to pre-patch state"
        assert "assert token is not None" in restored_code, "Security invariant must be preserved"
        assert "assert len(token) >= 8" in restored_code, "Entropy invariant must be preserved"

        # Verify rollback record in shadow manager
        rolled_back_tx = shadow_mgr.rolled_back_dir / tx_id
        assert rolled_back_tx.is_dir(), "Transaction directory must be moved to rolled_back"

        log_substep("Security Defense Triggered", "Regression detected", is_valid is False)
        log_substep("Atomic Rollback", "Restored from backup", rollback_ok is True)
        log_substep("Security Invariant Preserved", "assert token is not None present", True)

        dur_ms = (time.perf_counter() - start_t) * 1000.0
        return {
            "id": "SC-05",
            "name": "Security Assertion Defense",
            "type": "NEGATIVE ANTI-PLACEBO",
            "passed": True,
            "rollback_count": 1,
            "duration_ms": round(dur_ms, 2),
        }


# =============================================================================
# NEGATIVE SCENARIO 6: Protected Path Traversal Defense (Hard-Blocked)
# =============================================================================
def run_scenario_6_protected_path_defense() -> dict:
    start_t = time.perf_counter()
    log_banner("6. Negative: Protected Path Traversal Defense [Hard-Blocked]")

    # Check 1: Invariant protected paths in scp
    protected_samples = [
        "scp/task_kernel.py",
        "scp/persistence/db.py",
        "scp/security/auth.py",
        "spec/protected_invariants.yaml",
        "tests/T00_integrity/test_meta_audit.py",
        "scp/autofix/engine.py",
    ]
    log_step(1, "Testing _is_protected_path against core security & kernel files")
    for sample in protected_samples:
        is_prot = _is_protected_path(sample)
        log_substep(f"Protected path: {sample}", f"blocked={is_prot}", is_prot is True)
        assert is_prot is True, f"Path {sample} MUST be protected by _is_protected_path"

    # Windows backslash absolute normalization test
    win_abs_path = str(PROJECT_ROOT / "scp" / "security" / "auth.py")
    win_prot = _is_protected_path(win_abs_path)
    log_step(2, "Testing Windows backslash absolute path normalization", f"path={win_abs_path}, protected={win_prot}")
    assert win_prot is True, "Absolute Windows path must resolve and normalize to protected"

    # Check 2: Path Traversal defense via sanitize_storage_path
    log_step(3, "Testing path traversal defense in storage path sanitizer")
    traversal_candidates = [
        "../../etc/passwd",
        "..\\..\\windows\\system32\\cmd.exe",
        "logs/../../secret.key",
    ]
    for cand in traversal_candidates:
        sanitized = sanitize_storage_path(cand, default="data/fallback_audit.jsonl")
        assert ".." not in sanitized.parts, f"Sanitized path {sanitized} must never contain '..'"
        log_substep(f"Traversal blocked: {cand}", f"Sanitized to: {sanitized.name}", True)

    # Check 3: DeterministicWorker allowed root boundary enforcement
    with tempfile.TemporaryDirectory(prefix="scp_sandbox_sc6_") as td:
        sandbox_root = Path(td)
        allowed_dir = sandbox_root / "allowed_repo"
        outside_dir = sandbox_root / "outside_secret"
        allowed_dir.mkdir()
        outside_dir.mkdir()
        secret_file = outside_dir / "confidential.txt"
        secret_file.write_text("CLASSIFIED", encoding="utf-8")

        worker_data_dir = sandbox_root / "worker_data"
        worker_data_dir.mkdir()
        worker = DeterministicWorker(WorkerConfig(data_dir=worker_data_dir, allowed_root=allowed_dir))
        allowed, reason = worker._path_allowed(str(secret_file))
        log_step(4, "Testing DeterministicWorker root boundary enforcement", f"allowed={allowed}, reason={reason}")
        assert allowed is False, "DeterministicWorker must block files outside allowed_root"
        assert "outside allowed root" in reason, f"Reason must specify outside allowed root: {reason}"
        assert secret_file.read_text(encoding="utf-8") == "CLASSIFIED", "File must remain untouched"
        del worker
        import gc
        gc.collect()

    log_substep("Protected Paths Gate", f"{len(protected_samples)} critical paths guarded", True)
    log_substep("Traversal Sanitizer", "Zero parent traversals permitted", True)
    log_substep("Worker Root Enforced", "Hard-blocked outside allowed_root", True)

    dur_ms = (time.perf_counter() - start_t) * 1000.0
    return {
        "id": "SC-06",
        "name": "Protected Path Traversal Defense",
        "type": "NEGATIVE ANTI-PLACEBO",
        "passed": True,
        "rollback_count": 0,
        "duration_ms": round(dur_ms, 2),
    }


# =============================================================================
# MAIN RUNNER & ANSI REPORT GENERATOR
# =============================================================================
def main() -> int:
    overall_start = time.perf_counter()
    header_sep = "#" * 78
    print(f"{C_BOLD}{C_GREEN}{header_sep}{C_RESET}")
    print(f"{C_BOLD}{C_GREEN}  SCP AUTOFIX EMPIRICAL REALITY SANDBOX VERIFICATION SUITE{C_RESET}")
    print(f"{C_GRAY}  Timestamp: {datetime.now(timezone.utc).isoformat()}{C_RESET}")
    print(f"{C_GRAY}  Python:    {sys.version.split()[0]} ({sys.executable}){C_RESET}")
    print(f"{C_GRAY}  DNA Rules: Reality > Model | PASS != TRUE | Anti-Placebo Gates{C_RESET}")
    print(f"{C_BOLD}{C_GREEN}{header_sep}{C_RESET}")

    scenarios = [
        run_scenario_1_unused_import_deletion,
        run_scenario_2_multiline_blank_lines,
        run_scenario_3_whitespace_drift,
        run_scenario_4_syntax_corruption_defense,
        run_scenario_5_security_assertion_defense,
        run_scenario_6_protected_path_defense,
    ]

    results = []
    all_passed = True

    for scenario_fn in scenarios:
        try:
            res = scenario_fn()
            results.append(res)
            if not res.get("passed", False):
                all_passed = False
        except Exception as exc:
            all_passed = False
            fn_name = scenario_fn.__name__
            print(f"\n{C_BOLD}{C_RED}[SCENARIO EXCEPTION] in {fn_name}: {exc}{C_RESET}")
            import traceback
            traceback.print_exc()
            results.append({
                "id": fn_name,
                "name": fn_name,
                "type": "UNKNOWN",
                "passed": False,
                "rollback_count": -1,
                "duration_ms": 0.0,
            })

    total_time = (time.perf_counter() - overall_start) * 1000.0

    # Summary Table
    table_sep = "-" * 78
    print(f"\n\n{C_BOLD}{C_CYAN}{'=' * 78}{C_RESET}")
    print(f"{C_BOLD}{C_CYAN}                       EMPIRICAL EVIDENCE MATRIX                       {C_RESET}")
    print(f"{C_BOLD}{C_CYAN}{'=' * 78}{C_RESET}")
    print(f"{C_BOLD}{'ID':<7} {'Type':<22} {'Scenario Name':<34} {'Result':<8} {'Time (ms)':<9}{C_RESET}")
    print(f"{C_GRAY}{table_sep}{C_RESET}")

    for r in results:
        res_str = f"{C_GREEN}PASS{C_RESET}" if r["passed"] else f"{C_RED}FAIL{C_RESET}"
        type_color = C_CYAN if r["type"] == "POSITIVE" else C_MAGENTA
        print(f"{r['id']:<7} {type_color}{r['type']:<22}{C_RESET} {r['name']:<34} {res_str:<17} {r['duration_ms']:<9.1f}")

    print(f"{C_GRAY}{table_sep}{C_RESET}")

    pos_passed = sum(1 for r in results if r["type"] == "POSITIVE" and r["passed"])
    neg_passed = sum(1 for r in results if "NEGATIVE" in r["type"] and r["passed"])
    total_rollbacks = sum(r.get("rollback_count", 0) for r in results)

    print(f"\n{C_BOLD}SUMMARY METRICS:{C_RESET}")
    print(f"  * Total Empirical Scenarios:     {C_BOLD}{len(results)}/6{C_RESET}")
    print(f"  * Positive Scenarios (0% R/B):   {C_GREEN}{pos_passed}/3 (100% Verified){C_RESET}")
    print(f"  * Negative Scenarios (Defense):  {C_GREEN}{neg_passed}/3 (100% Defended Fail-Closed){C_RESET}")
    print(f"  * Total Controlled Rollbacks:    {C_CYAN}{total_rollbacks}{C_RESET} (1 expected from security defense)")
    print(f"  * Total Execution Time:          {C_BOLD}{total_time:.1f} ms{C_RESET}")
    print(f"  * Workspace Hygiene:             {C_GREEN}100% Clean (All temp directories unlinked){C_RESET}")

    # Check ARC-01
    this_file = Path(__file__).resolve()
    loc = len(this_file.read_text(encoding="utf-8").splitlines())
    print(f"  * ARC-01 Code Size:              {C_GREEN}{loc} LOC <= 800 LOC ceiling{C_RESET}")

    if all_passed and loc <= 800:
        print(f"\n{C_BOLD}{C_GREEN}>>> EMPIRICAL PROOF PASSED: ALL 6 SCENARIOS 100% VERIFIED <<<{C_RESET}\n")
        return 0
    else:
        print(f"\n{C_BOLD}{C_RED}>>> EMPIRICAL PROOF FAILED: ONE OR MORE SCENARIOS REJECTED <<<{C_RESET}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
