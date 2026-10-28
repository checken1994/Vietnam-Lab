"""Autonomous Checker Subsystem Gate for TaskKernel.

Trao quyền cho Checker Subsystem & Cưỡng chế cổng T00 Meta-Audit + VerifierReceipt
(ARC-01 GA B44 / DNA #1-#29 / FA-01-FA-13).

Encapsulates:
- Target path boundary & path traversal validation (within repo root).
- In-memory patch simulation (supporting unified diff, SEARCH/REPLACE, and fenced blocks).
- Whole-file AST syntax validity after patch application.
- Blast radius limits (max modified lines).
- Dangerous pattern scanning (eval, exec, os.system, shutil.rmtree, etc.).
- T00 Test-Integrity rules (empty test bodies, swallowed exceptions, pytest.skip/xfail, placebo assertions, zero-assert tests).
- [KEYC-L5] Typed recommendation attestation (typ=scp.checker_recommendation), never a
  completion receipt: the gate runs BEFORE execution, so it can never attest a result.
- [R5-B] Approval RECOMMENDATION only: the gate no longer mints approval:grant tokens nor
  commits WAITING_APPROVAL -> READY; an issuer-signed Ed25519 token is required for that.
"""
from __future__ import annotations

import ast
from collections import Counter
from dataclasses import dataclass, field
import difflib
import hashlib
import hmac
import logging
from pathlib import Path
import re
import time
from typing import Any

from scp.core.verifier_receipt import (
    InvalidReceiptSignatureError,
    VerifierReceipt,
    canonical_receipt_bytes,
    get_verifier_secret,
    sign_verifier_receipt,
)
from scp.task_kernel_parts.definitions import (
    InvalidTransition,
    KernelError,
    OptimisticLockError,
)
# [SEC-FIX 2026-10-27] ARC-01 tách file (move-only): năm hàm patch-in-memory
# chuyển sang checker_gate_patching.py; re-export giữ nguyên import path cũ.
from scp.task_kernel_parts.checker_gate_patching import (  # noqa: F401 - facade re-export
    apply_fenced_block,
    apply_patch_in_memory,
    apply_search_replace,
    apply_unified_diff,
    compute_blast_radius,
)

logger = logging.getLogger("scp.task_kernel.autonomous_checker_gate")

# [KEYC-L5 · B5] The gate signs BEFORE execution, so its MAC must never verify as a completion
# receipt: verdict != VERIFIED AND a typ-derived key (canonical 'a:b:c' is colon-ambiguous; a
# crafted actor like 'x:VERIFIED:y' would otherwise re-split the same bytes into a VERIFIED receipt).
CHECKER_RECOMMENDATION_TYP = "scp.checker_recommendation"
CHECKER_RECOMMENDATION_VERDICT = "RECOMMEND_APPROVE"


def _recommendation_key(secret: str | bytes | None = None) -> bytes:
    label = CHECKER_RECOMMENDATION_TYP.encode() + b"\0"  # domain label; derived key != K_verifier
    return hmac.new(get_verifier_secret(secret), label, hashlib.sha256).digest()


def verify_checker_recommendation(receipt: Any, *, task_id: str, secret: str | bytes | None = None) -> bool:
    """Verify a checker recommendation attestation for the exact task_id. It authorizes nothing:
    COMPLETED still needs a VERIFIED receipt signed after execution (commit_approval)."""
    if not isinstance(receipt, VerifierReceipt) or receipt.task_id != task_id:
        raise InvalidReceiptSignatureError("checker attestation does not bind the exact task_id")
    if receipt.verdict != CHECKER_RECOMMENDATION_VERDICT or not receipt.evidence_ref.startswith(
        f"{CHECKER_RECOMMENDATION_TYP}://"
    ):
        raise InvalidReceiptSignatureError(f"not a {CHECKER_RECOMMENDATION_TYP} attestation")
    expected = hmac.new(_recommendation_key(secret), canonical_receipt_bytes(receipt), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(str(receipt.signature), expected):
        raise InvalidReceiptSignatureError("checker attestation signature verification failed")
    return True


DANGEROUS_PATTERNS = [
    (re.compile(r"\beval\s*\("), "eval() invocation"),
    (re.compile(r"\bexec\s*\("), "exec() invocation"),
    (re.compile(r"\bos\.system\s*\("), "os.system() invocation"),
    (re.compile(r"\bos\.popen\s*\("), "os.popen() invocation"),
    (re.compile(r"\bshutil\.rmtree\s*\("), "destructive rmtree() call"),
    (re.compile(r"\b__import__\s*\("), "dynamic __import__() call"),
    (re.compile(r"\bpickle\.(?:loads|load)\s*\("), "pickle deserialization call"),
    (re.compile(r"\bsubprocess\.(?:Popen|call|run|check_call|check_output)\s*\("), "subprocess execution"),
]


class CheckerRejectionError(KernelError, PermissionError):
    """Raised when AutonomousCheckerGate rejects a candidate patch (Fail-Closed)."""
    pass


@dataclass(frozen=True)
class CheckerEvaluationResult:
    """Independent evaluation result from AutonomousCheckerGate."""

    approved: bool
    task_id: str
    verdict: str
    rationale: str
    receipt: VerifierReceipt | None = None  # [KEYC-L5] recommendation attestation, never completion
    token: str | None = None
    task: dict[str, Any] | None = None
    target_file: str = ""
    checks: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "approved": self.approved,
            "task_id": self.task_id,
            "verdict": self.verdict,
            "rationale": self.rationale,
            "receipt": self.receipt.to_dict() if self.receipt else None,
            "token": self.token,
            "task": self.task,
            "target_file": self.target_file,
            "checks": self.checks,
        }




class DangerousCallVisitor(ast.NodeVisitor):
    """AST visitor to detect dangerous or destructive function and method invocations."""

    def __init__(self) -> None:
        self.dangerous_calls: list[str] = []

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Name):
            if func.id in ("eval", "exec", "__import__"):
                self.dangerous_calls.append(f"{func.id}() invocation")
        elif isinstance(func, ast.Attribute):
            if isinstance(func.value, ast.Name):
                val_id = func.value.id
                attr = func.attr
                if val_id == "os" and attr in ("system", "popen"):
                    self.dangerous_calls.append(f"os.{attr}() invocation")
                elif val_id == "shutil" and attr == "rmtree":
                    self.dangerous_calls.append("shutil.rmtree() invocation")
                elif val_id == "pickle" and attr in ("loads", "load"):
                    self.dangerous_calls.append(f"pickle.{attr}() invocation")
                elif val_id == "subprocess" and attr in ("Popen", "call", "run", "check_call", "check_output"):
                    self.dangerous_calls.append(f"subprocess.{attr}() invocation")
        self.generic_visit(node)


class T00IntegrityVisitor(ast.NodeVisitor):
    """AST visitor enforcing SCP T00 Test-Integrity rules (FA-01 / FA-02).

    Checks:
    - Empty test bodies (`def test_foo(): pass`)
    - Swallowed exceptions (`try ... except: pass`)
    - Skips and xfails (`@pytest.mark.skip`, `pytest.skip()`, etc.)
    - Placebo assertions (`assert True`, `assert 1 == 1`, `assert not False`)
    - Zero assert tests
    """

    def __init__(self) -> None:
        self.findings: list[str] = []
        self.current_func: str = "<module>"
        self.current_class: str | None = None
        self.func_depth: int = 0
        self.in_test_func: bool = False

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        old_class = self.current_class
        self.current_class = node.name
        self.generic_visit(node)
        self.current_class = old_class

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._handle_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._handle_function(node)

    def _is_fixture(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
        for dec in node.decorator_list:
            name = None
            if isinstance(dec, ast.Name):
                name = dec.id
            elif isinstance(dec, ast.Attribute):
                name = dec.attr
            elif isinstance(dec, ast.Call):
                if isinstance(dec.func, ast.Name):
                    name = dec.func.id
                elif isinstance(dec.func, ast.Attribute):
                    name = dec.func.attr
            if name and "fixture" in name.lower():
                return True
        return False

    def _is_test_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
        if self._is_fixture(node):
            return False
        if node.name.startswith("test_") or node.name.startswith("test"):
            if self.func_depth == 1:
                if self.current_class is None or self.current_class.startswith("Test"):
                    return True
        return False

    def _is_empty_body(self, body: list[ast.stmt]) -> bool:
        if not body:
            return True
        for stmt in body:
            if isinstance(stmt, ast.Pass):
                continue
            if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
                val = stmt.value.value
                if isinstance(val, str) or val is ... or val is None:
                    continue
            return False
        return True

    def _has_any_assertion(self, func_node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
        stack = list(ast.iter_child_nodes(func_node))
        while stack:
            current = stack.pop()
            if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                continue
            if isinstance(current, ast.Assert):
                return True
            if isinstance(current, ast.With):
                for item in current.items:
                    if self._is_verification_call(item.context_expr):
                        return True
            if isinstance(current, ast.Call):
                if self._is_verification_call(current):
                    return True
            stack.extend(ast.iter_child_nodes(current))
        return False

    def _is_verification_call(self, call_node: ast.AST) -> bool:
        if not isinstance(call_node, ast.Call):
            return False
        name = ""
        if isinstance(call_node.func, ast.Name):
            name = call_node.func.id
        elif isinstance(call_node.func, ast.Attribute):
            name = call_node.func.attr
            if isinstance(call_node.func.value, ast.Name) and call_node.func.value.id == "pytest":
                if name in ("raises", "warns", "fail"):
                    return True

        name_lower = name.lower()
        if (
            name_lower.startswith("assert")
            or name_lower.startswith("verify")
            or name_lower.endswith("_verify")
            or name_lower.startswith("check")
            or name_lower.endswith("_check")
            or name_lower.startswith("expect")
            or name_lower.startswith("test_")
            or "assert" in name_lower
        ):
            return True
        return False

    def _is_placebo_assert(self, test_node: ast.AST) -> bool:
        # Constant truthy: assert True, assert 1
        if isinstance(test_node, ast.Constant):
            if bool(test_node.value) is True:
                return True

        # Unary not falsy: assert not False, assert not None
        if isinstance(test_node, ast.UnaryOp) and isinstance(test_node.op, ast.Not):
            if isinstance(test_node.operand, ast.Constant):
                if bool(test_node.operand.value) is False:
                    return True

        # Constant comparisons: assert 1 == 1, assert "a" == "a"
        if isinstance(test_node, ast.Compare):
            if isinstance(test_node.left, ast.Constant) and all(isinstance(c, ast.Constant) for c in test_node.comparators):
                try:
                    cur_val = test_node.left.value
                    is_true = True
                    for op, comp in zip(test_node.ops, test_node.comparators):
                        v2 = comp.value
                        res = False
                        if isinstance(op, ast.Eq):
                            res = (cur_val == v2)
                        elif isinstance(op, ast.NotEq):
                            res = (cur_val != v2)
                        elif isinstance(op, ast.Is):
                            res = (cur_val is v2)
                        elif isinstance(op, ast.IsNot):
                            res = (cur_val is not v2)
                        elif isinstance(op, ast.Lt):
                            res = (cur_val < v2)
                        elif isinstance(op, ast.LtE):
                            res = (cur_val <= v2)
                        elif isinstance(op, ast.Gt):
                            res = (cur_val > v2)
                        elif isinstance(op, ast.GtE):
                            res = (cur_val >= v2)
                        else:
                            is_true = False
                            break
                        if not res:
                            is_true = False
                            break
                        cur_val = v2
                    if is_true:
                        return True
                except Exception as exc:
                    logger.debug("Non-comparable values in placebo assertion check: %s", exc)

            # Tautological comparison: assert x == x
            if isinstance(test_node.left, ast.Name) and len(test_node.comparators) == 1:
                comp = test_node.comparators[0]
                op = test_node.ops[0]
                if isinstance(comp, ast.Name) and test_node.left.id == comp.id:
                    if isinstance(op, (ast.Eq, ast.Is)):
                        return True

        if isinstance(test_node, ast.Tuple) and len(test_node.elts) > 0:
            return True

        return False

    def _is_swallowed_body(self, body: list[ast.stmt]) -> bool:
        if not body:
            return True
        for stmt in body:
            if isinstance(stmt, ast.Pass):
                continue
            if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
                continue
            return False
        return True

    def _handle_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        old_func = self.current_func
        self.current_func = node.name
        self.func_depth += 1
        old_in_test = self.in_test_func

        is_test = self._is_test_function(node)
        self.in_test_func = is_test

        for dec in node.decorator_list:
            attr_name = None
            if isinstance(dec, ast.Attribute):
                attr_name = dec.attr
            elif isinstance(dec, ast.Call):
                if isinstance(dec.func, ast.Attribute):
                    attr_name = dec.func.attr
                elif isinstance(dec.func, ast.Name):
                    attr_name = dec.func.id
            if attr_name in ("skip", "xfail", "skipif"):
                self.findings.append(f"pytest decorator '{attr_name}' in {self.current_func}")

        if is_test:
            if self._is_empty_body(node.body):
                self.findings.append(f"empty test body in {self.current_func}")
            if not self._has_any_assertion(node):
                self.findings.append(f"zero assert test in {self.current_func}")

        self.generic_visit(node)
        self.func_depth -= 1
        self.current_func = old_func
        self.in_test_func = old_in_test

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Attribute):
            if getattr(node.func.value, "id", "") == "pytest" and node.func.attr in ("skip", "xfail", "importorskip"):
                self.findings.append(f"pytest.{node.func.attr}() call in {self.current_func}")
        self.generic_visit(node)

    def visit_Assert(self, node: ast.Assert) -> None:
        if self._is_placebo_assert(node.test):
            self.findings.append(f"placebo assertion in {self.current_func}")
        self.generic_visit(node)

    def visit_Try(self, node: ast.Try) -> None:
        if self.in_test_func:
            for handler in node.handlers:
                if self._is_swallowed_body(handler.body):
                    self.findings.append(f"swallowed exception in handler in {self.current_func}")
        self.generic_visit(node)

    def _check_pytestmark(self, value: ast.AST | None) -> None:
        if value is None:
            return
        for node in ast.walk(value):
            attr_node = node.func if isinstance(node, ast.Call) else node
            if (
                isinstance(attr_node, ast.Attribute)
                and attr_node.attr in ("skip", "xfail", "skipif")
                and isinstance(attr_node.value, ast.Attribute)
                and attr_node.value.attr == "mark"
                and isinstance(attr_node.value.value, ast.Name)
                and attr_node.value.value.id == "pytest"
            ):
                self.findings.append(f"pytestmark = pytest.mark.{attr_node.attr}")

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "pytestmark":
                self._check_pytestmark(node.value)
        self.generic_visit(node)


def is_test_target(target_path: str, tree: ast.AST) -> bool:
    """Determine whether the target file is a test file or defines test functions/classes."""
    norm = target_path.replace("\\", "/").lower()
    if "/tests/" in norm or norm.startswith("tests/") or "test_" in norm or norm.endswith("_test.py"):
        return True
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("test_") or node.name.startswith("test"):
                return True
        elif isinstance(node, ast.ClassDef):
            if node.name.startswith("Test"):
                return True
    return False


class AutonomousCheckerGate:
    """Autonomous Checker Subsystem Gate for TaskKernel.

    Evaluates candidate patches against strict syntactic, structural, security,
    and T00 test-integrity policies and returns a signed recommendation attestation
    (R5-B: no approval token, no commit; KEYC-L5: no completion receipt).
    """

    def __init__(
        self,
        kernel: Any = None,
        repo_root: Path | str | None = None,
        max_blast_radius_lines: int = 80,
        secret: str | bytes | None = None,
    ) -> None:
        self.kernel = kernel
        self.repo_root = Path(repo_root or Path(__file__).resolve().parents[2]).resolve()
        self.max_blast_radius_lines = max_blast_radius_lines
        self.secret = secret

    def evaluate_and_approve(
        self,
        task_id: str,
        candidate_patch: str,
        target_file: str,
        actor: str = "autonomous_checker",
        expected_version: int | None = None,
        max_blast_radius_lines: int | None = None,
        raise_on_rejection: bool = True,
        repo_root: Path | str | None = None,
    ) -> CheckerEvaluationResult:
        """Evaluate candidate patch fail-closed and return an approval recommendation (R5-B: never self-approves)."""
        try:
            return self._evaluate_and_approve_impl(
                task_id=task_id,
                candidate_patch=candidate_patch,
                target_file=target_file,
                actor=actor,
                expected_version=expected_version,
                max_blast_radius_lines=max_blast_radius_lines,
                repo_root=repo_root,
            )
        except Exception as exc:
            if isinstance(exc, (CheckerRejectionError, OptimisticLockError, KernelError)):
                if raise_on_rejection:
                    raise
                return CheckerEvaluationResult(
                    approved=False,
                    task_id=task_id,
                    verdict="REJECTED",
                    rationale=str(exc),
                    target_file=target_file,
                )
            raise

    def _evaluate_and_approve_impl(
        self,
        task_id: str,
        candidate_patch: str,
        target_file: str,
        actor: str,
        expected_version: int | None = None,
        max_blast_radius_lines: int | None = None,
        repo_root: Path | str | None = None,
    ) -> CheckerEvaluationResult:
        if self.kernel is None:
            raise RuntimeError("AutonomousCheckerGate has no bound TaskKernel instance")
        if not task_id or not str(task_id).strip():
            raise CheckerRejectionError("task_id is required")
        if candidate_patch is None or not isinstance(candidate_patch, str) or not candidate_patch.strip():
            raise CheckerRejectionError("candidate_patch is required and must be non-empty")
        if not target_file or not str(target_file).strip():
            raise CheckerRejectionError("target_file is required")

        root = Path(repo_root or self.repo_root).resolve()
        target_p = Path(target_file)

        # 1. Target Path Boundary & Traversal Validation
        if target_p.is_absolute():
            try:
                rel_path = target_p.resolve().relative_to(root)
                resolved_target = target_p.resolve()
            except ValueError:
                raise CheckerRejectionError(
                    f"Path traversal detected: target path {target_file} is outside repository root {root}"
                )
        else:
            resolved_target = (root / target_p).resolve()
            try:
                rel_path = resolved_target.relative_to(root)
            except ValueError:
                raise CheckerRejectionError(
                    f"Path traversal detected: target path {target_file} resolves outside repository root {root}"
                )

        if not resolved_target.is_file():
            raise CheckerRejectionError(f"Target file does not exist: {target_file}")

        try:
            original_code = resolved_target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            original_code = resolved_target.read_text(encoding="utf-8", errors="replace")

        checks: dict[str, Any] = {
            "path_boundary": True,
            "target_file": str(rel_path),
        }

        # 2. In-Memory Patch Simulation
        ok, patched_code, patch_err = apply_patch_in_memory(original_code, candidate_patch, filename=str(rel_path))
        if not ok:
            raise CheckerRejectionError(f"In-memory patch application failed: {patch_err}")

        if patched_code == original_code:
            raise CheckerRejectionError("Patch produced no changes to the target file")
        checks["patch_applied"] = True

        # 3. Whole-File AST Syntax Validity
        try:
            patched_tree = ast.parse(patched_code, filename=str(rel_path))
        except SyntaxError as syn_err:
            raise CheckerRejectionError(
                f"Syntax error in patched file {rel_path}: {syn_err}"
            ) from syn_err
        checks["ast_syntax_valid"] = True

        # 4. Blast Radius Check
        limit = max_blast_radius_lines if max_blast_radius_lines is not None else self.max_blast_radius_lines
        modified_lines = compute_blast_radius(original_code, patched_code)
        checks["modified_lines"] = modified_lines
        checks["max_blast_radius_lines"] = limit
        if modified_lines > limit:
            raise CheckerRejectionError(
                f"Blast radius limit exceeded: {modified_lines} modified lines exceeds limit of {limit}"
            )
        checks["blast_radius_passed"] = True

        # 5. Dangerous Pattern Scanning (Regex + AST)
        for pattern_regex, desc in DANGEROUS_PATTERNS:
            if pattern_regex.search(candidate_patch):
                raise CheckerRejectionError(f"Dangerous pattern detected in patch: {desc}")

        call_visitor = DangerousCallVisitor()
        call_visitor.visit(patched_tree)
        if call_visitor.dangerous_calls:
            raise CheckerRejectionError(
                f"Dangerous call detected in patched AST: {', '.join(call_visitor.dangerous_calls)}"
            )
        checks["dangerous_patterns_passed"] = True

        # 6. T00 Test-Integrity Rule Enforcement
        if is_test_target(str(rel_path), patched_tree):
            patched_visitor = T00IntegrityVisitor()
            patched_visitor.visit(patched_tree)

            orig_findings: list[str] = []
            if original_code.strip():
                try:
                    orig_tree = ast.parse(original_code, filename=str(rel_path))
                    orig_vis = T00IntegrityVisitor()
                    orig_vis.visit(orig_tree)
                    orig_findings = orig_vis.findings
                except SyntaxError as exc:
                    logger.debug("Original file had syntax error, cannot extract baseline T00 findings: %s", exc)

            orig_counts = Counter(orig_findings)
            patched_counts = Counter(patched_visitor.findings)

            new_violations: list[str] = []
            for finding, count in patched_counts.items():
                if count > orig_counts.get(finding, 0):
                    new_violations.append(finding)

            if new_violations:
                raise CheckerRejectionError(
                    f"T00 Test-Integrity rule violation introduced: {', '.join(new_violations)}"
                )
            checks["t00_test_integrity_passed"] = True

        # 7. [KEYC-L5 · B5] Sign a typed recommendation attestation (pre-execution, never VERIFIED)
        receipt_issued_at = time.time()
        receipt = sign_verifier_receipt(
            {
                "task_id": task_id,
                "verifier_id": actor,
                "verdict": CHECKER_RECOMMENDATION_VERDICT,
                "evidence_ref": f"{CHECKER_RECOMMENDATION_TYP}://ast_and_t00_pass/{task_id}",
                "issued_at": receipt_issued_at,
            },
            secret=_recommendation_key(self.secret),
        )
        checks["attestation_typ"] = CHECKER_RECOMMENDATION_TYP
        checks["recommendation_attestation_signed"] = True

        # 8. [R5-B] No self-approval: the gate no longer mints an approval:grant token nor commits
        # WAITING_APPROVAL -> READY. It returns a recommendation; only the holder of the issuer
        # PRIVATE key (operator / issuer service) can sign the approval the kernel accepts.
        # The state/OCC preconditions commit_approval used to enforce still gate the recommendation.
        task = self.kernel.get_task(task_id)
        if task["state"] != "WAITING_APPROVAL":
            raise InvalidTransition(
                f"task {task_id} in state '{task['state']}' cannot be approved; task must be in WAITING_APPROVAL"
            )
        if expected_version is not None and int(task["version"]) != expected_version:
            raise OptimisticLockError(
                f"concurrency conflict approving task {task_id}: expected version {expected_version}, found {task['version']}",
                table="tasks",
                entity_id=task_id,
                expected_version=expected_version,
            )
        checks["approval_recommended"] = True
        return CheckerEvaluationResult(
            approved=False,
            task_id=task_id,
            verdict="RECOMMEND_APPROVE",
            rationale=(
                "All checks passed: path validation, AST syntax, blast radius, dangerous patterns, "
                "T00 test integrity; approval requires an issuer-signed token (R5-B)"
            ),
            receipt=receipt,
            token=None,
            task=task,
            target_file=str(rel_path),
            checks=checks,
        )
