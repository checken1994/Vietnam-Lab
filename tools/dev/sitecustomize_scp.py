"""SCP dev ``sitecustomize`` template: worktree-aware import root.

This committed file is the source of truth for the NOT-committed, machine-local
``<venv>/Lib/site-packages/sitecustomize.py`` of the SCP dev venv. The venv file
is a byte-identical copy of this file (``__name__ == "sitecustomize"`` there,
which is the only case in which the side effects below run).

Why it exists
-------------
The previous venv file always inserted the main checkout (a hardcoded drive
path) at ``sys.path[0]`` and imported ``tests.pg_hermetic_adapter`` from it.
A script run BY PATH inside a git worktree
(``<main>/.claude/worktrees/<x>/tools/foo.py``) therefore imported ``scp`` and
the PG hermetic adapter of the MAIN tree, i.e. it verified the wrong code.

Behaviour
---------
1. root = nearest SCP checkout that contains the script (``sys.argv[0]`` is
   populated at site time on CPython 3.12, probed 2026-10-29); argv0 values that
   are not paths (``-c``, ``-m``, ``-``, empty) and launchers that live inside
   the interpreter prefix (``<venv>/Scripts/pytest.exe``) are ignored;
   otherwise the nearest checkout that contains the cwd; otherwise the main
   root = ``$SCP_DEV_MAIN_ROOT`` or, by default, the checkout that contains
   ``sys.prefix`` (venv ``<main>/.venv`` -> ``<main>``; no hardcoded path).
2. Insert root at ``sys.path[0]`` unless it is already on ``sys.path``.
3. Install the PG hermetic adapter of THAT root (``install_if_needed``) exactly
   as before, so every interpreter in the venv (migration/chaos subprocesses
   included) keeps the hermetic ``psycopg.connect``. A checkout without
   ``tests/pg_hermetic_adapter.py`` installs nothing; any other failure prints
   one line to stderr and never aborts interpreter startup.

An SCP checkout is a directory holding ``scp/__init__.py`` and ``tests/``.

Recipe:   copy this file to ``<venv>/Lib/site-packages/sitecustomize.py``.
Rollback: copy the saved ``sitecustomize.py.orig-<date>`` back over it.
Guard:    ``tests/internal/test_sitecustomize_worktree_guard.py``.
"""
from __future__ import annotations

import os
import sys

_NON_PATH_ARGV0 = frozenset({"", "-c", "-m", "-"})


def _is_checkout(path: str) -> bool:
    return os.path.isfile(os.path.join(path, "scp", "__init__.py")) and os.path.isdir(
        os.path.join(path, "tests")
    )


def _find_checkout(start: str) -> str | None:
    """Nearest ancestor of ``start`` (inclusive) that is an SCP checkout."""
    current = os.path.abspath(start)
    while True:
        if _is_checkout(current):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent


def _is_within(path: str, prefix: str) -> bool:
    child = os.path.normcase(os.path.abspath(path))
    base = os.path.normcase(os.path.abspath(prefix))
    try:
        return os.path.commonpath([child, base]) == base
    except ValueError:  # different drives on Windows: cannot be within
        return False


def _choose_root(
    argv0: str | None,
    cwd: str | None,
    main_root: str | None,
    exclude_prefixes: tuple[str, ...] = (),
) -> str | None:
    """Pick the checkout whose ``scp``/``tests`` this interpreter must import."""
    if argv0 and argv0 not in _NON_PATH_ARGV0:
        script = argv0 if os.path.isabs(argv0) or not cwd else os.path.join(cwd, argv0)
        if not any(_is_within(script, prefix) for prefix in exclude_prefixes if prefix):
            start = script if os.path.isdir(script) else os.path.dirname(script)
            found = _find_checkout(start)
            if found is not None:
                return found
    if cwd:
        found = _find_checkout(cwd)
        if found is not None:
            return found
    return main_root


def _main_root(environ, prefix: str | None) -> str | None:
    override = environ.get("SCP_DEV_MAIN_ROOT")
    if override:
        return os.path.abspath(override)
    return _find_checkout(prefix) if prefix else None


def _current_dir() -> str | None:
    try:
        return os.getcwd()
    except OSError:  # cwd was deleted: fall through to argv0/main root only
        return None


def _install() -> str | None:
    argv = getattr(sys, "argv", None) or [""]
    prefixes = tuple({p for p in (sys.prefix, getattr(sys, "base_prefix", None)) if p})
    root = _choose_root(argv[0], _current_dir(), _main_root(os.environ, sys.prefix), prefixes)
    if root is None:
        return None
    wanted = os.path.normcase(os.path.abspath(root))
    on_path = any(
        isinstance(entry, str) and entry and os.path.normcase(os.path.abspath(entry)) == wanted
        for entry in sys.path
    )
    if not on_path:
        sys.path.insert(0, root)
    if not os.path.isfile(os.path.join(root, "tests", "pg_hermetic_adapter.py")):
        return root
    try:
        from tests.pg_hermetic_adapter import install_if_needed

        install_if_needed()
    except Exception as exc:  # startup must survive; the failure must not be silent
        if sys.stderr is not None:
            sys.stderr.write(
                f"[scp-sitecustomize] PG hermetic adapter not installed from {root}: "
                f"{type(exc).__name__}: {exc}\n"
            )
    return root


if __name__ == "sitecustomize":
    _install()
