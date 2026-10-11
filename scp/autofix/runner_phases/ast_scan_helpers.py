"""AST visitor helpers for `scp.autofix.runner_phases.ast_scan`.

Extracted from `scp/autofix/runner_phases/ast_scan.py` for modularity (<= 780 LOC)
and re-exported from `scp.autofix.runner_phases.ast_scan` for backward compatibility.
"""
from __future__ import annotations

import ast


class _BareExceptPassFinder(ast.NodeVisitor):
    """Detect `except: pass` / `except Exception as e: pass` — swallows errors silently.

    These are the #1 cause of "silent failure" bugs (the audit trail catches
    exceptions but never surfaces them). Auto-fixable in most cases by
    re-raising or logging.
    """

    def __init__(self):
        self.findings: list[dict] = []

    def visit_ExceptHandler(self, node: ast.ExceptHandler):
        is_bare = node.type is None
        is_broad = (
            isinstance(node.type, ast.Name)
            and node.type.id in ("Exception", "BaseException")
        )
        if is_bare or is_broad:
            body = node.body
            if len(body) == 1 and isinstance(body[0], ast.Pass):
                self.findings.append({
                    "line": node.lineno,
                    "bug_type": "BareExceptPass",
                    "description": (
                        f"{'bare' if is_bare else 'broad'} except with bare `pass` "
                        f"at line {node.lineno} — swallows errors silently"
                    ),
                    "suggested_fix": (
                        "Replace `pass` with `logger.exception(...)` or re-raise. "
                        "Silent exception swallowing hides real bugs."
                    ),
                })
        self.generic_visit(node)


class _ModuleNameCollector(ast.NodeVisitor):
    """[V5.3-SCANNER-FIX] Pass 1 helper — collect ALL module-level name bindings.

    Walks top-level statements (caller passes `tree.body` statements one by
    one). Recurses into "container" statements (If/Try/For/With/While/
    ExceptHandler) so that names bound inside `if __name__ == "__main__":`
    or `try:` blocks at module level are also collected. Does NOT recurse
    into FunctionDef/ClassDef bodies — those introduce separate scopes.

    Why: Python executes module body top-to-bottom, binding each name before
    any function is *called*. So a function defined LATER in the file can
    still be referenced EARLIER (inside another function body that runs at
    call time). This is the "forward reference" pattern the old scanner
    falsely flagged.
    """

    def __init__(self, names: set):
        self._names = names

    # --- Bindings (do not recurse into function/class bodies) ---

    def visit_FunctionDef(self, node):
        self._names.add(node.name)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node):
        self._names.add(node.name)

    def visit_Import(self, node):
        for alias in node.names:
            self._names.add(alias.asname or alias.name.split(".")[0])

    def visit_ImportFrom(self, node):
        for alias in node.names:
            if alias.name == "*":
                continue
            self._names.add(alias.asname or alias.name)

    def visit_Assign(self, node):
        for tgt in node.targets:
            self._collect_target(tgt)
        # RHS may contain walrus (visit_NamedExpr) — visit value too.
        self.visit(node.value)

    def visit_AnnAssign(self, node):
        if isinstance(node.target, ast.Name):
            self._names.add(node.target.id)
        if node.value is not None:
            self.visit(node.value)

    def visit_AugAssign(self, node):
        # x += 1 requires x already defined — doesn't create new binding
        # but visit value in case it contains walrus.
        self.visit(node.value)

    def visit_For(self, node):
        self._collect_target(node.target)
        self.visit(node.iter)
        for stmt in node.body:
            self.visit(stmt)
        for stmt in node.orelse:
            self.visit(stmt)

    visit_AsyncFor = visit_For

    def visit_With(self, node):
        for item in node.items:
            if item.optional_vars:
                self._collect_target(item.optional_vars)
            self.visit(item.context_expr)
        for stmt in node.body:
            self.visit(stmt)

    visit_AsyncWith = visit_With

    def visit_ExceptHandler(self, node):
        if node.name:
            self._names.add(node.name)
        if node.type is not None:
            self.visit(node.type)
        for stmt in node.body:
            self.visit(stmt)

    def visit_Try(self, node):
        for stmt in node.body:
            self.visit(stmt)
        for handler in node.handlers:
            self.visit(handler)
        for stmt in node.orelse:
            self.visit(stmt)
        for stmt in node.finalbody:
            self.visit(stmt)

    def visit_If(self, node):
        self.visit(node.test)
        for stmt in node.body:
            self.visit(stmt)
        for stmt in node.orelse:
            self.visit(stmt)

    def visit_While(self, node):
        self.visit(node.test)
        for stmt in node.body:
            self.visit(stmt)
        for stmt in node.orelse:
            self.visit(stmt)

    def visit_NamedExpr(self, node):
        # Walrus at module level binds target in module scope.
        if isinstance(node.target, ast.Name):
            self._names.add(node.target.id)
        self.visit(node.value)

    def _collect_target(self, tgt):
        if isinstance(tgt, ast.Name):
            self._names.add(tgt.id)
        elif isinstance(tgt, (ast.Tuple, ast.List)):
            for elt in tgt.elts:
                self._collect_target(elt)
        elif isinstance(tgt, ast.Starred):
            self._collect_target(tgt.value)
        # Attribute/Subscript targets don't bind new names


__all__ = ["_BareExceptPassFinder", "_ModuleNameCollector"]
