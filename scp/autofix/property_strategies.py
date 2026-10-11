"""[SCP-DNA-FIX R9 v4 IMP-19] Property-Based Fix Validation strategies & helpers."""
from __future__ import annotations

import ast
import hashlib
import logging
import random
import textwrap
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from scp.autofix.restricted_exec import safe_getattr, safe_hasattr

logger = logging.getLogger("scp.autofix.property_validator")

# [SCP-DNA-FIX R13-5] Bug #5: SAFE_BUILTINS sandbox for exec().
SAFE_BUILTINS: dict[str, Any] = {
    # --- I/O (NONE — no file/network access) ---
    # --- type constructors (safe subset) ---
    "bool": bool,
    "bytes": bytes,
    "bytearray": bytearray,
    "complex": complex,
    "dict": dict,
    "float": float,
    "frozenset": frozenset,
    "int": int,
    "list": list,
    "set": set,
    "str": str,
    "tuple": tuple,
    # --- numeric / iteration helpers ---
    "abs": abs,
    "all": all,
    "any": any,
    "ascii": ascii,
    "bin": bin,
    "chr": chr,
    "divmod": divmod,
    "enumerate": enumerate,
    "filter": filter,
    "format": format,
    "hex": hex,
    "iter": iter,
    "len": len,
    "map": map,
    "max": max,
    "min": min,
    "next": next,
    "oct": oct,
    "ord": ord,
    "pow": pow,
    "print": print,
    "range": range,
    "repr": repr,
    "reversed": reversed,
    "round": round,
    "slice": slice,
    "sorted": sorted,
    "sum": sum,
    "zip": zip,
    # --- introspection (safe subset — NO globals/locals/vars/dir) ---
    "callable": callable,
    "getattr": safe_getattr,
    "hasattr": safe_hasattr,
    "isinstance": isinstance,
    "issubclass": issubclass,
    "id": id,
    "type": type,
    # --- constants ---
    "True": True,
    "False": False,
    "None": None,
    "NotImplemented": NotImplemented,
    "Ellipsis": Ellipsis,
    # --- exception classes (so candidates can catch/raise) ---
    "Exception": Exception,
    "BaseException": BaseException,
    "ValueError": ValueError,
    "TypeError": TypeError,
    "KeyError": KeyError,
    "IndexError": IndexError,
    "AttributeError": AttributeError,
    "StopIteration": StopIteration,
    "ArithmeticError": ArithmeticError,
    "ZeroDivisionError": ZeroDivisionError,
    "OverflowError": OverflowError,
    "LookupError": LookupError,
    "RuntimeError": RuntimeError,
    "AssertionError": AssertionError,
}


# ============================================================
# Default input strategies — generate edge-case inputs.
# ============================================================

_PV_RNG = random.SystemRandom()
_strategy_source: Any = None


class _DeterministicSampler:
    """Small deterministic sampler for seeded runs (hashlib-based)."""

    def __init__(self, seed: int) -> None:
        self._seed = int(seed)
        self._counter = 0

    def _next_u64(self) -> int:
        self._counter += 1
        blob = hashlib.sha256(f"{self._seed}:{self._counter}".encode()).digest()
        return int.from_bytes(blob[:8], "big")

    def choice(self, seq: Sequence[Any]) -> Any:
        if not len(seq):
            raise IndexError("choice() from an empty sequence")
        return seq[self._next_u64() % len(seq)]


@contextmanager
def _deterministic_strategy_source(seed: int) -> Iterator[_DeterministicSampler]:
    """Temporarily make every strategy draw from a deterministic stream."""
    global _strategy_source
    _strategy_source = _DeterministicSampler(seed)
    try:
        yield _strategy_source
    finally:
        _strategy_source = None


def _draw(seq: Sequence[Any]) -> Any:
    """Draw one edge-case element."""
    source = _strategy_source if _strategy_source is not None else _PV_RNG
    return source.choice(seq)


def _edge_ints() -> Any:
    """Boundary integer inputs."""
    return _draw([
        0, 1, -1, 2, -2, 10, -10, 100, -100, 1000000, -1000000,
        2147483647, -2147483648,
        10**18, -(10**18),
    ])


def _edge_floats() -> Any:
    """Boundary float inputs (incl. NaN, inf)."""
    return _draw([
        0.0, -0.0, 1.0, -1.0, 0.5, -0.5,
        1e-10, -1e-10, 1e10, -1e10,
        float("nan"), float("inf"), float("-inf"),
        5e-324, -5e-324,
    ])


def _edge_strs() -> Any:
    """Boundary string inputs (incl. unicode, empty, huge)."""
    return _draw([
        "", " ", "a", "ab", "hello",
        "hello\n", "  \t\n",
        "héllo", "你好", "🐶🐱", "𝕏",
        "\x00", "\x00abc", "abc\x00",
        "x" * 10_000,
        "\n".join(str(i) for i in range(100)),
        "café\t\n",
    ])


def _edge_lists() -> Any:
    """Boundary list inputs."""
    return _draw([
        [], [None], [0], [1, 2, 3], [-1, -2, -3],
        [0] * 100, list(range(100)),
        [[], []], [[1, 2], [3, 4]], [[[[1]]]],
        [1, "a", None, 3.14],
        list(range(10_000)),
        [1] * 100,
    ])


def _edge_dicts() -> Any:
    """Boundary dict inputs."""
    return _draw([
        {}, {"a": 1}, {"a": 1, "b": 2},
        {"a": None}, {"a": None, "b": None},
        {"a": {"b": {"c": 1}}},
        {"": "empty_key", "key with space": 1, "🐶": "emoji"},
        {f"k{i}": i for i in range(100)},
        {"data": "x" * 10_000},
    ])


def _edge_none() -> Any:
    """Always None."""
    return None


def _edge_bools() -> Any:
    """Boundary bool inputs."""
    return _draw([True, False, 0, 1, None])


def _edge_mixed() -> Any:
    """Random pick across all strategies — useful for general fuzz."""
    return _draw([
        _edge_ints(), _edge_floats(), _edge_strs(),
        _edge_lists(), _edge_dicts(), _edge_none(), _edge_bools(),
    ])


# Registry — extensible. Caller can register custom strategy under a name.
STRATEGY_REGISTRY: dict[str, Callable[[], Any]] = {
    "int": _edge_ints,
    "float": _edge_floats,
    "str": _edge_strs,
    "list": _edge_lists,
    "dict": _edge_dicts,
    "none": _edge_none,
    "bool": _edge_bools,
    "mixed": _edge_mixed,
}

# Public short aliases (kept stable for the manifest reference).
INT_OR_NONE_STRATEGY = "int"
STR_OR_NONE_STRATEGY = "str"
LIST_STRATEGY = "list"
DICT_STRATEGY = "dict"
MIXED_STRATEGY = "mixed"


def register_strategy(name: str, fn: Callable[[], Any]) -> None:
    """Register a custom strategy. Idempotent. Fail-open on bad input."""
    try:
        if not name or not callable(fn):
            logger.warning(f"[IMP-19] register_strategy bad args: {name!r}")
            return
        STRATEGY_REGISTRY[name] = fn
    except Exception as e:  # noqa: BLE001 — fail-open
        logger.warning(f"[IMP-19] register_strategy error: {e}")


# ============================================================
# Dataclasses.
# ============================================================

@dataclass
class BugLocation:
    """Line range of the bug being fixed (matches IMP-15 BugLocation)."""
    function_name: str = ""
    line_start: int = 0
    line_end: int = 0
    statement_kind: str = ""

    def contains_line(self, lineno: int) -> bool:
        """True if lineno is within [line_start, line_end]."""
        if not lineno:
            return False
        if self.line_start and lineno < self.line_start:
            return False
        if self.line_end and lineno > self.line_end:
            return False
        return True


@dataclass
class PropertySpec:
    """A property-based test specification."""
    invariants: list[Callable[[Any], bool]] = field(default_factory=list)
    strategy: str | Callable[[], Any] = "mixed"
    skip_if_none_input: bool = False


@dataclass
class Violation:
    """A single invariant violation observed during validation."""
    input_value: Any
    original_output: Any
    fixed_output: Any
    invariant_index: int
    reason: str
    over_broad: bool = False   # True if behavior changed outside bug_location

    def summary(self) -> str:
        return (
            f"Violation(invariant={self.invariant_index}, "
            f"reason={self.reason!r}, over_broad={self.over_broad})"
        )


@dataclass
class PropertyResult:
    """Outcome of validate_fix()."""
    ok: bool = True
    violations: list[Violation] = field(default_factory=list)
    inputs_tested: int = 0
    coverage: dict[str, int] = field(default_factory=dict)
    reason: str = ""
    invariants_held_both: int = 0
    invariants_held_fixed_only: int = 0
    invariants_held_neither: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "violations_count": len(self.violations),
            "inputs_tested": self.inputs_tested,
            "coverage": dict(self.coverage),
            "reason": self.reason,
            "invariants_held_both": self.invariants_held_both,
            "invariants_held_fixed_only": self.invariants_held_fixed_only,
            "invariants_held_neither": self.invariants_held_neither,
            "violations": [
                {
                    "invariant_index": v.invariant_index,
                    "reason": v.reason,
                    "over_broad": v.over_broad,
                    "input_repr": repr(v.input_value)[:200],
                    "original_repr": repr(v.original_output)[:200],
                    "fixed_repr": repr(v.fixed_output)[:200],
                }
                for v in self.violations
            ],
        }


# ============================================================
# Core logic.
# ============================================================

def _resolve_strategy(spec: PropertySpec) -> Callable[[], Any] | None:
    """Resolve a declared strategy; unknown strategies are not verification."""
    try:
        s = spec.strategy
        if callable(s):
            return s
        if isinstance(s, str):
            fn = STRATEGY_REGISTRY.get(s)
            if fn is None:
                logger.warning(f"[IMP-19] unknown strategy {s!r}; verification unavailable")
                return None
            return fn
        logger.warning(f"[IMP-19] bad strategy type {type(s).__name__}")
        return None
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[IMP-19] strategy resolution error: {e}")
        return None


def _compile_function(source: str, expected_name: str | None = None) -> Any:
    """Compile a single-function module source and return the function object."""
    try:
        if not source or not source.strip():
            return None
        src = textwrap.dedent(source)
        tree = ast.parse(src)
        funcs = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
        if not funcs:
            return None
        target = funcs[0]
        if expected_name and target.name != expected_name:
            for f in funcs:
                if f.name == expected_name:
                    target = f
                    break
        from scp.autofix.restricted_exec import compile_restricted_function

        return compile_restricted_function(
            src,
            expected_name=target.name,
            safe_builtins=SAFE_BUILTINS,
            filename="<property_validator>",
        )
    except SyntaxError as e:
        logger.debug(f"[IMP-19] syntax error compiling function: {e}")
        return None
    except Exception as e:  # noqa: BLE001 — fail-open
        logger.debug(f"[IMP-19] compile error: {e}")
        return None


def _safe_call(fn: Any, arg: Any) -> tuple[bool, Any, str]:
    """Call fn(arg). Returns (ok, result, reason)."""
    try:
        return True, fn(arg), ""
    except TypeError as e:
        try:
            if isinstance(arg, (tuple, list)):
                return True, fn(*arg), ""
            return False, None, f"TypeError: {e}"
        except Exception as e2:  # noqa: BLE001
            return False, None, f"TypeError-retry: {e2}"
    except Exception as e:  # noqa: BLE001 — function crashes are evidence
        return False, None, f"{type(e).__name__}: {e}"


def _check_invariants(
    output: Any,
    invariants: Sequence[Callable[[Any], bool]],
) -> list[tuple[int, str]]:
    """Return list of (invariant_index, reason) for each violated invariant."""
    out: list[tuple[int, str]] = []
    if not output[0]:
        return out
    val = output[1]
    for i, inv in enumerate(invariants):
        try:
            ok = bool(inv(val))
            if not ok:
                out.append((i, f"invariant[{i}] returned False for {val!r}"))
        except Exception as e:  # noqa: BLE001
            out.append((i, f"invariant[{i}] raised: {type(e).__name__}: {e}"))
    return out


def _type_tag(value: Any) -> str:
    """Return a short type tag for coverage tracking."""
    if value is None:
        return "NoneType"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "dict"
    if isinstance(value, tuple):
        return "tuple"
    return type(value).__name__


def _outputs_differ(orig_out: Any, fixed_out: Any) -> bool:
    """Compare outputs — return True if they differ materially."""
    try:
        if orig_out is fixed_out:
            return False
        if isinstance(orig_out, float) and isinstance(fixed_out, float):
            if orig_out != orig_out and fixed_out != fixed_out:
                return False
        return orig_out != fixed_out
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[property_validator] output comparison error: {e}")
        return True
