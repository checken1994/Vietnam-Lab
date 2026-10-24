"""W4-5 audit probe: reproduce the AutoFix SQL-parameterize recipe binding bug.

External audit report (section 1.6) claims: the deterministic SQL recipe keeps
the SQL string quotes that wrapped the interpolated value, producing
``name = '?'`` while still binding parameters, so a real SQLite execution
raises ``ProgrammingError: Incorrect number of bindings supplied``.

This probe runs the real recipe engine (``build_candidate``) against a source
file with a quoted value containing an apostrophe (``O'Brien``) and executes
the patched SQL against a real in-memory SQLite database. No LLM, no network.
"""
from __future__ import annotations

# [CHECKER h2 · 2026-10-24] Tự bootstrap sys.path để chạy trực tiếp được:
#   python tests/diagnostics/probe_sql_recipe_bindings_w45.py
# (trước đây phải nhớ PYTHONPATH=/app trong container).
import os
import sys as _sys

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO_ROOT not in _sys.path:
    _sys.path.insert(0, _REPO_ROOT)

import json
import sqlite3
import sys
import tempfile
import traceback
from pathlib import Path
from types import SimpleNamespace

from scp.autofix.deterministic_patches import build_candidate

SOURCE = '''import sqlite3


def find_user(cursor, name):
    cursor.execute(f"SELECT id FROM users WHERE name = '{name}'")
    return cursor.fetchone()
'''


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "victim.py"
        target.write_text(SOURCE, encoding="utf-8")
        bug = SimpleNamespace(bug_type="SQLInjection", file=str(target), line=5)
        candidate = build_candidate(bug)

    if candidate is None:
        print(json.dumps({"status": "DISPROVEN", "reason": "recipe produced no candidate"}))
        return 1

    print("=== patched source ===")
    print(candidate.source_after)

    # Execute the patched call on a real in-memory SQLite database.
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")
    conn.execute("INSERT INTO users (name) VALUES (?)", ("O'Brien",))
    conn.commit()
    cursor = conn.cursor()
    namespace: dict[str, object] = {"sqlite3": sqlite3}
    exec(candidate.source_after, namespace)  # noqa: S102 - audit probe, local file
    try:
        row = namespace["find_user"](cursor, "O'Brien")  # type: ignore[operator]
        result = {
            "status": "DISPROVEN",
            "reason": "patched SQL executed without error",
            "row": row,
        }
        print(json.dumps(result))
        return 1
    except Exception as exc:  # noqa: BLE001 - probe must print the real failure
        print("=== real failure ===")
        traceback.print_exc()
        result = {
            "status": "PROVEN",
            "exception_type": type(exc).__name__,
            "exception_message": str(exc),
            "patch_id": candidate.patch_id,
        }
        print(json.dumps(result))
        return 0


if __name__ == "__main__":
    sys.exit(main())
