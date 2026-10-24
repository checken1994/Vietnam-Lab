"""Anti-regression tests for the deterministic SQL-parameterize recipe.

W4-5 audit (FA-09): the AST SQL recipe used to keep the SQL string quotes that
wrapped the interpolated value, producing ``name = '?'`` while still binding
parameters. sqlite treats a quoted ``?`` as a string literal, so executing the
patched statement on a real database raised
``sqlite3.ProgrammingError: Incorrect number of bindings supplied`` while the
statement had zero real placeholders.

These tests pin the fixed behavior against a real in-memory SQLite database:
quoted values must become true bind parameters, and a patch that would leave a
placeholder inside a quoted region must be declined (fail closed) instead of
emitted.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

from scp.autofix.deterministic_patches import build_candidate

QUOTED_VALUE_SOURCE = '''import sqlite3


def find_user(cursor, name):
    cursor.execute(f"SELECT id FROM users WHERE name = '{name}'")
    return cursor.fetchone()
'''

TWO_QUOTED_VALUES_SOURCE = '''import sqlite3


def find_user(cursor, name, city):
    cursor.execute(
        f"SELECT id FROM users WHERE name = '{name}' AND city = '{city}'"
    )
    return cursor.fetchone()
'''

INSERT_QUOTED_VALUE_SOURCE = '''import sqlite3


def add_user(conn, name):
    conn.execute(f"INSERT INTO users (name) VALUES ('{name}')")
    conn.commit()
'''

UNPAIRED_QUOTE_SOURCE = '''import sqlite3


def find_user(cursor, name):
    cursor.execute(f"SELECT id FROM users WHERE name = '{name} AND x = 1")
    return cursor.fetchone()
'''


def _make_bug(tmp_path: Path, source: str, line: int) -> SimpleNamespace:
    target = tmp_path / "victim.py"
    target.write_text(source, encoding="utf-8")
    return SimpleNamespace(bug_type="SQLInjection", file=str(target), line=line)


def _candidate_for(tmp_path: Path, source: str, line: int):
    bug = _make_bug(tmp_path, source, line)
    return build_candidate(bug)


def test_quoted_value_becomes_real_bind_parameter(tmp_path: Path) -> None:
    candidate = _candidate_for(tmp_path, QUOTED_VALUE_SOURCE, line=5)
    # Anti-placebo: the recipe must still fire on this scenario; a silent skip
    # would make the runtime assertion below vacuous.
    assert candidate is not None
    assert candidate.patch_id == "sql_parameterize_ast_v1"
    # The broken form kept the quotes around the placeholder: name = '?'.
    assert "'?'" not in candidate.source_after
    assert '"?"' not in candidate.source_after

    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")
    conn.execute("INSERT INTO users (name) VALUES (?)", ("O'Brien",))
    conn.commit()
    namespace: dict[str, object] = {}
    exec(candidate.source_after, namespace)  # noqa: S102 - test-controlled source
    row = namespace["find_user"](conn.cursor(), "O'Brien")
    assert row is not None and row[0] == 1


def test_multiple_quoted_values_all_bind(tmp_path: Path) -> None:
    candidate = _candidate_for(tmp_path, TWO_QUOTED_VALUES_SOURCE, line=5)
    assert candidate is not None
    assert "'?'" not in candidate.source_after

    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, city TEXT)")
    conn.execute(
        "INSERT INTO users (name, city) VALUES (?, ?)", ("O'Brien", "Hanoi")
    )
    conn.commit()
    namespace = {}
    exec(candidate.source_after, namespace)  # noqa: S102 - test-controlled source
    row = namespace["find_user"](conn.cursor(), "O'Brien", "Hanoi")
    assert row is not None and row[0] == 1


def test_insert_quoted_value_binds(tmp_path: Path) -> None:
    candidate = _candidate_for(tmp_path, INSERT_QUOTED_VALUE_SOURCE, line=5)
    assert candidate is not None
    assert "'?'" not in candidate.source_after

    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")
    namespace = {}
    exec(candidate.source_after, namespace)  # noqa: S102 - test-controlled source
    namespace["add_user"](conn, "O'Brien")
    row = conn.execute(
        "SELECT id FROM users WHERE name = ?", ("O'Brien",)
    ).fetchone()
    assert row is not None and row[0] == 1


def test_placeholder_left_inside_quotes_is_declined(tmp_path: Path) -> None:
    # Unpaired quote around the interpolation would leave the placeholder
    # inside a quoted region even after stripping: the recipe must decline
    # (fail closed) instead of emitting SQL that cannot bind.
    candidate = _candidate_for(tmp_path, UNPAIRED_QUOTE_SOURCE, line=5)
    assert candidate is None
