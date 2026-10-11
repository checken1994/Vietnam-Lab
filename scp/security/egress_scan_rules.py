"""Helper constants, dataclasses, and AST dominance rules for `scp.security.egress_static_scan`.

Extracted from `scp/security/egress_static_scan.py` for modularity (<= 780 LOC)
and re-exported from `scp.security.egress_static_scan` for backward compatibility.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

# Constructors that yield an HTTP client/session/opener object.
CLIENT_CTORS: frozenset[str] = frozenset(
    {
        "requests.Session",
        "requests.session",
        "httpx.Client",
        "httpx.AsyncClient",
        "urllib.request.build_opener",
        "urllib.request.OpenerDirector",
        "aiohttp.ClientSession",
    }
)

# Methods that, when called directly on a tracked client, perform network I/O.
CLIENT_HTTP_METHODS: frozenset[str] = frozenset(
    {
        "get", "post", "put", "delete", "patch", "head", "options",
        "request", "stream", "send", "open", "urlopen",
    }
)

# Modules excluded from BOTH gates (raw + client): the choke point itself and
# the canonical fetchers that call it.
GATE_EXCLUDED_MODULES: frozenset[str] = frozenset(
    {
        "scp/security/url_safety.py",
        "scp/core/url_fetcher.py",
        "scp/core/api_utils.py",
    }
)

EGRESS_GATE_NAME = "enforce_egress_policy"

_RAW_URLLIB_KIND = "urllib.request.urlopen"

_SCOPE_NODES = (ast.FunctionDef, ast.AsyncFunctionDef)


@dataclass
class ClientCallSite:
    """One method call on a tracked HTTP client (gated or not)."""

    file: str
    line: int
    col: int
    method: str
    receiver: str
    function: str
    gated: bool

    def as_dict(self) -> dict:
        return {
            "file": self.file,
            "line": self.line,
            "col": self.col,
            "method": self.method,
            "receiver": self.receiver,
            "function": self.function,
            "gated": self.gated,
        }


def _block_always_exits(stmts: list) -> bool:
    """True iff every execution path through the statement list leaves the
    enclosing function (final effect is return/raise, through If branches,
    or a fully-exiting try). Used to decide whether an exception raised in a
    Try body can fall through to statements after the Try."""
    if not stmts:
        return False
    last = stmts[-1]
    if isinstance(last, (ast.Return, ast.Raise)):
        return True
    if isinstance(last, ast.If):
        if not last.orelse:
            return False
        return _block_always_exits(last.body) and _block_always_exits(last.orelse)
    if isinstance(last, (ast.Try, ast.TryStar)):
        if not last.handlers:
            return False
        if not _block_always_exits(last.body):
            return False
        if not all(_block_always_exits(h.body) for h in last.handlers):
            return False
        if last.orelse and not _block_always_exits(last.orelse):
            return False
        if last.finalbody and not _block_always_exits(last.finalbody):
            return False
        return True
    return False


def _gate_dominates(gate_chain: tuple, call_chain: tuple) -> bool:
    """True iff the gate statement is guaranteed to run before the call on
    every path reaching the call, and a gate DENIAL cannot fall through to
    the call.

    Chains are tuples of (list_id, index, owner_stmt, role) from the
    function/module body down to the statement's block. Rules:
      - shared prefix levels must keep gate index <= call index;
      - gate in an enclosing block (or same list) of the call → dominates;
      - diverged subtrees: the gate still dominates when every compound on
        the gate's tail path is a Try — ``body`` with all-exiting handlers
        (denial returns/raises, never swallowed), ``finalbody`` (always
        runs) — anything conditional (If/For/... bodies, orelse, handlers)
        means the gate can be skipped → NOT dominating;
      - a call inside the ``finalbody`` of the SAME try that encloses the
        gate is never exempt (finally runs even after a denial-return)."""
    m = min(len(gate_chain), len(call_chain))
    if m == 0:
        return False
    k = 0
    while k < m and gate_chain[k][0] == call_chain[k][0]:
        k += 1
    if k == 0:
        return False
    for lvl in range(k):
        if gate_chain[lvl][1] > call_chain[lvl][1]:
            return False
    if k == len(gate_chain):
        return True
    if k < len(call_chain):
        call_owner_k = call_chain[k][2]
        gate_owner_k = gate_chain[k][2]
        if call_chain[k][3] == "finalbody" and call_owner_k is gate_owner_k:
            return False  # finally runs even after a denial-return
        if call_chain[k][3] == "handlers" and call_owner_k is gate_owner_k:
            return False  # the denial handler itself performs the fetch
        if (
            call_chain[k][3] == "orelse"
            and call_owner_k is gate_owner_k
            and gate_chain[k][3] == "body"
        ):
            # try/else: the else block runs only when the body (and gate) succeeded
            return True
    for lvl in range(k, len(gate_chain)):
        _lid, _idx, owner, role = gate_chain[lvl]
        if not isinstance(owner, (ast.Try, ast.TryStar)):
            return False  # conditional compound: gate may be skipped entirely
        if role == "finalbody":
            continue  # a finally always executes
        if role != "body":
            return False  # orelse/handler positions are conditional
        if owner.handlers and not all(
            _block_always_exits(h.body) for h in owner.handlers
        ):
            return False  # a handler can swallow the denial and fall through
    return True


def _resolve_from_module(module: str | None, level: int, package: str | None) -> str | None:
    """Resolve an ``ast.ImportFrom`` target to a dotted module name.

    ``level`` is the relative-import depth (0 = absolute); ``package`` is the
    dotted package of the file containing the import (None for standalone
    scripts, whose relative imports cannot be resolved here)."""
    if not level:
        return module
    if not package:
        return None
    parts = package.split(".")
    drop = level - 1
    if drop > len(parts):
        return None
    if drop:
        parts = parts[:-drop]
    base = ".".join(parts)
    if module:
        return f"{base}.{module}" if base else module
    return base or None


def _package_and_module_names(path: Path) -> tuple[str | None, str | None]:
    """(package, module) dotted names derived from the filesystem layout:
    walk up while ``__init__.py`` exists. Returns (None, None) for standalone
    scripts outside any package."""
    directory = path.parent
    if not (directory / "__init__.py").exists():
        return None, None
    parts: list[str] = []
    walk = directory
    while (walk / "__init__.py").exists():
        parts.append(walk.name)
        walk = walk.parent
    package = ".".join(reversed(parts))
    module = package if path.name == "__init__.py" else f"{package}.{path.stem}"
    return package, module


def _dotted_parts(node: ast.AST) -> tuple[str, ...] | None:
    """Return the dotted path of a Name/Attribute chain, else None."""
    parts: list[str] = []
    cur: ast.AST = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
        return tuple(reversed(parts))
    return None
