"""Independent Integrity Test Suite for T00 2.0 Semantic Enforcement.

Enforces Requirement R2 (The Checker):
- Clean AST fixture from repository code (tests/T01_boot/test_deep_audit_boot_gate.py).
- 4 deterministic ast.NodeTransformer mutation operators:
  * EmptyTestBodyTransformer
  * PlaceboAssertTransformer
  * SwallowedExceptionTransformer
  * ZeroAssertTransformer
- 100% detection rate verification against tools.t00_meta_audit.get_fa01_signatures().
- Counter Delta mechanism verification (debt vs new violation).
- Dual input type support (str | ast.AST).
- Negative CLI subprocess verification (exit code 1).
"""
from __future__ import annotations

import ast
import copy
import subprocess
import sys
from pathlib import Path
from typing import List

import pytest

from tools.t00_meta_audit import audit_content, get_fa01_signatures

# [Wave-C C3-W2 2026-10-23] Env-layer guard: các CLI subprocess test chạy
# tools/t00_meta_audit.py thật, tool fail-closed khi không resolve được trusted
# base 'main' qua git — audit container (python:3.12-slim) không có git binary
# ⇒ CLI in "[T00 FAIL-CLOSED] Trusted base 'main' is invalid or missing" thay
# cho kết quả per-file. Skip CHỈ khi git vắng mặt; runner có git chạy full.
_T00_CLI_REASON = (
    "git binary không có trong runner (audit container python:3.12-slim) — "
    "t00 CLI fail-closed ở trusted-base trước khi quét file; env-layer, "
    "xem ledger Wave-C C3-W2"
)

REPO_ROOT = Path(__file__).resolve().parents[2]
CLEAN_FIXTURE_PATH = REPO_ROOT / "tests" / "T01_boot" / "test_deep_audit_boot_gate.py"


# ---------------------------------------------------------------------------
# AST Mutation Operators (The Checkers)
# ---------------------------------------------------------------------------

class EmptyTestBodyTransformer(ast.NodeTransformer):
    """Mutates test functions so their body is hollow (pass, ellipsis, docstring)."""

    def __init__(self, mode: str = "pass"):
        self.mode = mode

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.AST:
        if node.name.startswith("test_") or node.name.startswith("test"):
            if self.mode == "pass":
                node.body = [ast.Pass()]
            elif self.mode == "ellipsis":
                node.body = [ast.Expr(value=ast.Constant(value=...))]
            elif self.mode == "docstring":
                node.body = [ast.Expr(value=ast.Constant(value="empty docstring"))]
            elif self.mode == "docstring_and_pass":
                node.body = [ast.Expr(value=ast.Constant(value="docstring")), ast.Pass()]
        return self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> ast.AST:
        return self.visit_FunctionDef(node)  # type: ignore[arg-type]


class PlaceboAssertTransformer(ast.NodeTransformer):
    """Mutates assertions into unconditional truthy statements."""

    def __init__(self, variant: str = "assert_true"):
        self.variant = variant

    def visit_Assert(self, node: ast.Assert) -> ast.AST:
        if self.variant == "assert_true":
            return ast.Assert(test=ast.Constant(value=True), msg=None)
        elif self.variant == "assert_1":
            return ast.Assert(test=ast.Constant(value=1), msg=None)
        elif self.variant == "assert_not_false":
            return ast.Assert(
                test=ast.UnaryOp(op=ast.Not(), operand=ast.Constant(value=False)),
                msg=None,
            )
        elif self.variant == "assert_1_eq_1":
            return ast.Assert(
                test=ast.Compare(
                    left=ast.Constant(value=1),
                    ops=[ast.Eq()],
                    comparators=[ast.Constant(value=1)],
                ),
                msg=None,
            )
        elif self.variant == "assert_tautology":
            return ast.Assert(
                test=ast.Compare(
                    left=ast.Name(id="_sym", ctx=ast.Load()),
                    ops=[ast.Eq()],
                    comparators=[ast.Name(id="_sym", ctx=ast.Load())],
                ),
                msg=None,
            )
        elif self.variant == "assert_tuple":
            return ast.Assert(
                test=ast.Tuple(
                    elts=[ast.Constant(value=True), ast.Constant(value="msg")],
                    ctx=ast.Load(),
                ),
                msg=None,
            )
        return node


class SwallowedExceptionTransformer(ast.NodeTransformer):
    """Wraps test bodies in try: ... except: pass blocks."""

    def __init__(self, mode: str = "except_pass"):
        self.mode = mode

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.AST:
        if node.name.startswith("test_") or node.name.startswith("test"):
            if self.mode == "except_pass":
                handler = ast.ExceptHandler(type=None, name=None, body=[ast.Pass()])
            elif self.mode == "except_exception_pass":
                handler = ast.ExceptHandler(
                    type=ast.Name(id="Exception", ctx=ast.Load()),
                    name=None,
                    body=[ast.Pass()],
                )
            elif self.mode == "except_docstring":
                handler = ast.ExceptHandler(
                    type=ast.Name(id="Exception", ctx=ast.Load()),
                    name=None,
                    body=[ast.Expr(value=ast.Constant(value="swallowed"))],
                )
            elif self.mode == "except_ellipsis":
                handler = ast.ExceptHandler(
                    type=ast.Name(id="Exception", ctx=ast.Load()),
                    name=None,
                    body=[ast.Expr(value=ast.Constant(value=...))],
                )
            else:
                handler = ast.ExceptHandler(type=None, name=None, body=[ast.Pass()])

            try_stmt = ast.Try(
                body=node.body,
                handlers=[handler],
                orelse=[],
                finalbody=[],
            )
            node.body = [try_stmt]
        return self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> ast.AST:
        return self.visit_FunctionDef(node)  # type: ignore[arg-type]


class ZeroAssertTransformer(ast.NodeTransformer):
    """Strips all assertions and verification calls from test functions without emptying the body."""

    def visit_Assert(self, node: ast.Assert):
        return None

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.AST:
        self.generic_visit(node)
        has_stmt = any(not isinstance(stmt, ast.Pass) for stmt in node.body)
        if not has_stmt:
            node.body = [
                ast.Assign(
                    targets=[ast.Name(id="_computed_val", ctx=ast.Store())],
                    value=ast.BinOp(
                        left=ast.Constant(value=40),
                        op=ast.Add(),
                        right=ast.Constant(value=2),
                    ),
                )
            ]
        return node

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> ast.AST:
        return self.visit_FunctionDef(node)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Helper Functions
# ---------------------------------------------------------------------------

def _load_clean_fixture() -> tuple[str, ast.AST, List[str]]:
    assert CLEAN_FIXTURE_PATH.is_file(), f"Clean fixture not found: {CLEAN_FIXTURE_PATH}"
    code = CLEAN_FIXTURE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(code, filename=str(CLEAN_FIXTURE_PATH))
    test_funcs = [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and (node.name.startswith("test_") or node.name.startswith("test"))
    ]
    assert len(test_funcs) >= 3, f"Expected at least 3 test functions, found {len(test_funcs)}"
    return code, tree, test_funcs


# ---------------------------------------------------------------------------
# Unit & Mutation Integrity Tests
# ---------------------------------------------------------------------------

def test_clean_fixture_has_zero_fa01_violations():
    """Verify that the baseline clean repository AST fixture produces zero violations."""
    code, tree, test_funcs = _load_clean_fixture()
    signatures_from_code = get_fa01_signatures(code)
    signatures_from_ast = get_fa01_signatures(tree)

    assert len(signatures_from_code) == 0, f"Clean fixture had violations: {signatures_from_code}"
    assert len(signatures_from_ast) == 0, f"Clean AST had violations: {signatures_from_ast}"


def test_dual_input_type_support_str_and_ast():
    """Verify that get_fa01_signatures accepts either str or ast.AST with identical output."""
    sample_code = "def test_a(): pass\ndef test_b(): assert True\n"
    res_str = get_fa01_signatures(sample_code)
    res_ast = get_fa01_signatures(ast.parse(sample_code))

    assert res_str == res_ast
    assert "empty_test_body in test_a" in res_str
    assert "placebo_assert in test_b" in res_str


@pytest.mark.parametrize("mode", ["pass", "ellipsis", "docstring", "docstring_and_pass"])
def test_empty_test_body_transformer_100_percent_caught(mode: str):
    """EmptyTestBodyTransformer must achieve 100% detection rate across all variants."""
    clean_code, clean_tree, test_funcs = _load_clean_fixture()

    mutated_tree = EmptyTestBodyTransformer(mode=mode).visit(copy.deepcopy(clean_tree))
    ast.fix_missing_locations(mutated_tree)

    # 1. Direct AST invocation
    signatures = get_fa01_signatures(mutated_tree)
    for func in test_funcs:
        assert f"empty_test_body in {func}" in signatures, (
            f"Failed to detect empty_test_body ({mode}) in {func}. Signatures: {signatures}"
        )

    # 2. Counter Delta verification via audit_content
    mutated_code = ast.unparse(mutated_tree)
    new_v, debts = audit_content(mutated_code, clean_code, "tests/T01_boot/test_deep_audit_boot_gate.py")
    assert len(new_v) >= len(test_funcs)
    assert len(debts) == 0


@pytest.mark.parametrize(
    "variant",
    [
        "assert_true",
        "assert_1",
        "assert_not_false",
        "assert_1_eq_1",
        "assert_tautology",
        "assert_tuple",
    ],
)
def test_placebo_assert_transformer_100_percent_caught(variant: str):
    """PlaceboAssertTransformer must achieve 100% detection rate across all variants."""
    clean_code, clean_tree, test_funcs = _load_clean_fixture()

    mutated_tree = PlaceboAssertTransformer(variant=variant).visit(copy.deepcopy(clean_tree))
    ast.fix_missing_locations(mutated_tree)

    # 1. Direct AST invocation
    signatures = get_fa01_signatures(mutated_tree)
    for func in test_funcs:
        assert f"placebo_assert in {func}" in signatures, (
            f"Failed to detect placebo_assert ({variant}) in {func}. Signatures: {signatures}"
        )

    # 2. Counter Delta verification
    mutated_code = ast.unparse(mutated_tree)
    new_v, debts = audit_content(mutated_code, clean_code, "tests/T01_boot/test_deep_audit_boot_gate.py")
    assert len(new_v) >= len(test_funcs)
    assert len(debts) == 0


@pytest.mark.parametrize(
    "mode",
    ["except_pass", "except_exception_pass", "except_docstring", "except_ellipsis"],
)
def test_swallowed_exception_transformer_100_percent_caught(mode: str):
    """SwallowedExceptionTransformer must achieve 100% detection rate across all variants."""
    clean_code, clean_tree, test_funcs = _load_clean_fixture()

    mutated_tree = SwallowedExceptionTransformer(mode=mode).visit(copy.deepcopy(clean_tree))
    ast.fix_missing_locations(mutated_tree)

    # 1. Direct AST invocation
    signatures = get_fa01_signatures(mutated_tree)
    for func in test_funcs:
        assert f"swallowed_exception in {func}" in signatures, (
            f"Failed to detect swallowed_exception ({mode}) in {func}. Signatures: {signatures}"
        )

    # 2. Counter Delta verification
    mutated_code = ast.unparse(mutated_tree)
    new_v, debts = audit_content(mutated_code, clean_code, "tests/T01_boot/test_deep_audit_boot_gate.py")
    assert len(new_v) >= len(test_funcs)
    assert len(debts) == 0


def test_zero_assert_transformer_100_percent_caught():
    """ZeroAssertTransformer must achieve 100% detection rate on stripped test functions."""
    clean_code, clean_tree, test_funcs = _load_clean_fixture()

    mutated_tree = ZeroAssertTransformer().visit(copy.deepcopy(clean_tree))
    ast.fix_missing_locations(mutated_tree)

    # 1. Direct AST invocation
    signatures = get_fa01_signatures(mutated_tree)
    for func in test_funcs:
        assert f"zero_assert_test in {func}" in signatures, (
            f"Failed to detect zero_assert_test in {func}. Signatures: {signatures}"
        )

    # 2. Counter Delta verification
    mutated_code = ast.unparse(mutated_tree)
    new_v, debts = audit_content(mutated_code, clean_code, "tests/T01_boot/test_deep_audit_boot_gate.py")
    assert len(new_v) >= len(test_funcs)
    assert len(debts) == 0


def test_counter_delta_mechanism_baseline_debt_preservation():
    """Verify that delta = c_fa01 - b_fa01 preserves historical debt and blocks new debt."""
    hist_code = """
def test_legacy():
    try:
        assert 1 == 1
    except Exception:
        pass
"""
    # 1. Unchanged historical code passes as debt (0 new violations)
    new_v, debts = audit_content(hist_code, hist_code, "tests/test_legacy.py")
    assert len(new_v) == 0, f"Expected 0 new violations for historical code, got {new_v}"
    assert len(debts) >= 1
    assert any("swallowed_exception in test_legacy" in d for d in debts)

    # 2. Candidate adding a new violation fails closed
    new_code = hist_code + "\ndef test_new_hollow():\n    pass\n"
    new_v, debts = audit_content(new_code, hist_code, "tests/test_legacy.py")
    assert any("empty_test_body in test_new_hollow" in v for v in new_v)
    assert any("swallowed_exception in test_legacy" in d for d in debts)

    # 3. Clearing historical debt passes cleanly
    fixed_code = "def test_legacy():\n    assert 2 + 2 == 4\n"
    new_v, debts = audit_content(fixed_code, hist_code, "tests/test_legacy.py")
    assert len(new_v) == 0
    assert len(debts) == 0


def test_fixture_functions_are_excluded_from_empty_and_zero_assert_checks():
    """@pytest.fixture functions must not be flagged as empty_test_body or zero_assert_test."""
    fixture_code = """
import pytest

@pytest.fixture
def test_client_fixture():
    pass

@pytest.fixture()
def test_db_fixture():
    ...
"""
    signatures = get_fa01_signatures(fixture_code)
    assert len(signatures) == 0, f"Fixtures should not be flagged: {signatures}"


# ---------------------------------------------------------------------------
# Negative & Positive CLI Subprocess Tests
# ---------------------------------------------------------------------------

def test_negative_cli_subprocess_empty_test_body(tmp_path: Path):
    """CLI subprocess must exit code 1 when an empty test body is audited."""
    test_file = tmp_path / "test_empty_body.py"
    test_file.write_text("def test_empty():\n    pass\n", encoding="utf-8")

    tool_path = REPO_ROOT / "tools" / "t00_meta_audit.py"
    proc = subprocess.run(
        [sys.executable, str(tool_path), "--file", str(test_file)],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert proc.returncode == 1
    assert "T00 META-AUDIT FAILED - NEW REGRESSIONS DETECTED" in proc.stdout
    assert "empty_test_body in test_empty" in proc.stdout


def test_negative_cli_subprocess_placebo_assert(tmp_path: Path):
    """CLI subprocess must exit code 1 when a placebo assertion is audited."""
    test_file = tmp_path / "test_placebo.py"
    test_file.write_text("def test_placebo():\n    assert True\n", encoding="utf-8")

    tool_path = REPO_ROOT / "tools" / "t00_meta_audit.py"
    proc = subprocess.run(
        [sys.executable, str(tool_path), "--file", str(test_file)],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert proc.returncode == 1
    assert "T00 META-AUDIT FAILED - NEW REGRESSIONS DETECTED" in proc.stdout
    assert "placebo_assert in test_placebo" in proc.stdout


def test_negative_cli_subprocess_swallowed_exception(tmp_path: Path):
    """CLI subprocess must exit code 1 when a swallowed exception is audited."""
    test_file = tmp_path / "test_swallow.py"
    test_file.write_text(
        "def test_swallow():\n    try:\n        assert 1 == 2\n    except Exception:\n        pass\n",
        encoding="utf-8",
    )

    tool_path = REPO_ROOT / "tools" / "t00_meta_audit.py"
    proc = subprocess.run(
        [sys.executable, str(tool_path), "--file", str(test_file)],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert proc.returncode == 1
    assert "T00 META-AUDIT FAILED - NEW REGRESSIONS DETECTED" in proc.stdout
    assert "swallowed_exception in test_swallow" in proc.stdout


def test_negative_cli_subprocess_zero_assert_test(tmp_path: Path):
    """CLI subprocess must exit code 1 when a zero-assert test is audited."""
    test_file = tmp_path / "test_zero_assert.py"
    test_file.write_text("def test_zero():\n    x = 100\n", encoding="utf-8")

    tool_path = REPO_ROOT / "tools" / "t00_meta_audit.py"
    proc = subprocess.run(
        [sys.executable, str(tool_path), "--file", str(test_file)],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert proc.returncode == 1
    assert "T00 META-AUDIT FAILED - NEW REGRESSIONS DETECTED" in proc.stdout
    assert "zero_assert_test in test_zero" in proc.stdout


def test_positive_cli_subprocess_clean_file(tmp_path: Path):
    """CLI subprocess must exit code 0 when a clean test file is audited."""
    test_file = tmp_path / "test_clean_suite.py"
    test_file.write_text("def test_success():\n    assert 1 + 1 == 2\n", encoding="utf-8")

    tool_path = REPO_ROOT / "tools" / "t00_meta_audit.py"
    proc = subprocess.run(
        [sys.executable, str(tool_path), "--file", str(test_file)],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert proc.returncode == 0
    assert "0 new regressions" in proc.stdout
