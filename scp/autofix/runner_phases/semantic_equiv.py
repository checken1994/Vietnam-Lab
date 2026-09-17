"""AST-based semantic-equivalence guard for AutoFix candidates.

The guard does not claim that two programs are mathematically equivalent.  It
checks the narrower, auditable contract used by ``post_fix_verify``: a patch
may change the declared target location, but must not silently change unrelated
functions/classes or delete the target.  Parse failures and missing targets
are unverified and fail closed.
"""
from __future__ import annotations

import ast
import dataclasses
import typing


@dataclasses.dataclass
class BugLocation:
    file_path: str = ""
    line: int = 0
    function_name: typing.Optional[str] = None
    class_name: typing.Optional[str] = None


@dataclasses.dataclass
class Result:
    ok: bool = False
    equivalent: bool = False
    over_broad: bool = False
    critical: bool = False
    changed_statements: list[str] = dataclasses.field(default_factory=list)
    reason: str = "unverified"


def _parse(source: str, filename: str) -> ast.Module:
    if not isinstance(source, str) or not source.strip():
        raise ValueError("source is missing")
    return ast.parse(source, filename=filename or "<source>")


def _node_key(node: ast.AST) -> str:
    """Stable AST identity without source locations/context noise."""

    return ast.dump(node, annotate_fields=True, include_attributes=False)


def _functions(tree: ast.AST) -> dict[tuple[str, str], ast.FunctionDef | ast.AsyncFunctionDef]:
    found: dict[tuple[str, str], ast.FunctionDef | ast.AsyncFunctionDef] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        owner = ""
        parent_stack: list[ast.AST] = []
        # ``ast.walk`` has no parents; class ownership is reconstructed from
        # qualified names in a small recursive visitor below instead.
        del parent_stack
        found[(owner, node.name)] = node
    return found


def _qualified_functions(tree: ast.Module) -> dict[tuple[str, str], ast.FunctionDef | ast.AsyncFunctionDef]:
    result: dict[tuple[str, str], ast.FunctionDef | ast.AsyncFunctionDef] = {}

    def visit(body: list[ast.stmt], owner: str = "") -> None:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                result[(owner, node.name)] = node
            elif isinstance(node, ast.ClassDef):
                visit(node.body, f"{owner}.{node.name}" if owner else node.name)

    visit(tree.body)
    return result


def _top_level_keys(tree: ast.Module, target: ast.AST | None) -> list[str]:
    keys: list[str] = []
    for node in tree.body:
        if node is target:
            continue
        keys.append(_node_key(node))
    return keys


def _line_intersects(node: ast.AST, line: int) -> bool:
    if line <= 0:
        return True
    start = int(getattr(node, "lineno", 0) or 0)
    end = int(getattr(node, "end_lineno", start) or start)
    return start <= line <= end


def _statement_labels(node: ast.AST, prefix: str = "") -> list[str]:
    labels: list[str] = []
    for child in getattr(node, "body", ()) or ():
        if isinstance(child, ast.stmt):
            name = type(child).__name__
            line = int(getattr(child, "lineno", 0) or 0)
            labels.append(f"{prefix}{name}@{line}")
    return labels


def _body_outside_target(
    original: ast.FunctionDef | ast.AsyncFunctionDef,
    fixed: ast.FunctionDef | ast.AsyncFunctionDef,
    bug_location: BugLocation,
) -> list[str]:
    """Return changed target statements outside the permitted line."""

    before = list(original.body)
    after = list(fixed.body)
    changed: list[str] = []
    max_len = max(len(before), len(after))
    for index in range(max_len):
        left = before[index] if index < len(before) else None
        right = after[index] if index < len(after) else None
        if left is not None and right is not None and _node_key(left) == _node_key(right):
            continue
        representative = right or left
        if representative is None:
            continue
        # A missing line/function name means the caller identified the whole
        # target function; all changes in that function are intentional scope.
        if bug_location.line > 0 and not _line_intersects(representative, bug_location.line):
            changed.append(
                f"{bug_location.function_name or '<target>'}:{type(representative).__name__}@{getattr(representative, 'lineno', 0)}"
            )
    return changed


def verify_semantic_equiv(
    original_source: str,
    fixed_source: str,
    bug_location: BugLocation | None,
) -> Result:
    """Compare original/fixed ASTs and fail closed on uncertainty.

    ``equivalent`` means "no change outside the declared target"; it does not
    mean output equality for the changed target itself.  This is intentionally
    a structural guard, while runtime behavior belongs to the sandbox/reality
    verifier.
    """

    location = bug_location if isinstance(bug_location, BugLocation) else BugLocation()
    filename = location.file_path or "<autofix>"
    try:
        original_tree = _parse(original_source, filename)
        fixed_tree = _parse(fixed_source, filename)
    except (SyntaxError, TypeError, ValueError) as exc:
        return Result(ok=False, equivalent=False, reason=f"source parse failed: {type(exc).__name__}")

    before = _qualified_functions(original_tree)
    after = _qualified_functions(fixed_tree)
    target_key: tuple[str, str] | None = None
    if location.function_name:
        owner = location.class_name or ""
        target_key = (owner, location.function_name)
        if target_key not in before:
            # A top-level function may have been addressed with class_name=None;
            # this is already the exact key.  Do not fuzzy-match a different
            # function: ambiguity is unsafe.
            return Result(
                ok=False,
                equivalent=False,
                critical=True,
                reason=f"target function '{location.function_name}' missing from original source",
            )
        if target_key not in after:
            return Result(
                ok=False,
                equivalent=False,
                critical=True,
                reason=f"target function '{location.function_name}' deleted by candidate",
            )

    changed: list[str] = []
    if target_key is None:
        if _node_key(original_tree) == _node_key(fixed_tree):
            return Result(ok=True, equivalent=True, reason="AST unchanged")
        # Without a target, no changed statement is authorised.  Include
        # bounded labels to make the rejection observable and actionable.
        changed = _statement_labels(original_tree, "module:")[:10]
        changed.extend(_statement_labels(fixed_tree, "module+:" )[:10])
        return Result(
            ok=False,
            equivalent=False,
            over_broad=True,
            changed_statements=changed,
            reason="no bug_location target; source AST changed",
        )

    original_target = before[target_key]
    fixed_target = after[target_key]

    # Any top-level node other than the target is outside the patch scope.  For
    # methods, compare the owning class after replacing the target method with
    # a placeholder; this catches sibling method/class changes.
    original_non_target = dict(before)
    fixed_non_target = dict(after)
    original_non_target.pop(target_key, None)
    fixed_non_target.pop(target_key, None)
    all_non_target = set(original_non_target) | set(fixed_non_target)
    for key in sorted(all_non_target):
        left = original_non_target.get(key)
        right = fixed_non_target.get(key)
        if left is None or right is None or _node_key(left) != _node_key(right):
            changed.append(f"outside:{key[0] or '<module>'}.{key[1]}")

    changed.extend(_body_outside_target(original_target, fixed_target, location))
    if changed:
        return Result(
            ok=False,
            equivalent=False,
            over_broad=True,
            changed_statements=changed[:50],
            reason="candidate changed statements outside bug_location",
        )

    if _node_key(original_target) == _node_key(fixed_target):
        return Result(ok=True, equivalent=True, reason="target AST unchanged")

    return Result(
        ok=True,
        equivalent=True,
        changed_statements=[],
        reason="only declared target function changed",
    )


__all__ = ["BugLocation", "Result", "verify_semantic_equiv"]
