"""T03_capability session fixtures — SQLite isolation for the whole T03 suite.

[N2-AUDIT / AUDIT-20260909] TẠI SAO (causal chain, proven by EXP-2):
  1. ``scp.core.db_manager`` hardcodes ``DB_PATH = <repo>/data/v13.db`` — the
     SAME file the production server (``python -m scp 8000``) writes to with
     its background loops (learning, doubt-cron, why-verify, batch flush).
  2. ``_db_lock`` only serialises writers INSIDE one process. Between
     processes the only guard is SQLite WAL + ``busy_timeout=30000``.
  3. When any external process holds a write transaction on that file for
     more than 30s, every test write — including the autouse teardown
     ``db_exec("DELETE FROM predictions ...")`` in
     ``test_flow_06_prediction_scp_standard.py:114`` — dies with
     ``sqlite3.OperationalError: database is locked``
     (``scp/core/db_manager_parts/db_exec.py:27``).
     Deterministic proof: a second process doing
     ``BEGIN IMMEDIATE`` + hold 45s made an identical-connection probe fail
     with the exact error after 32.9s — on a DB copy, outside pytest.
  4. So the failure is NOT a per-test lifecycle bug (db_exec already
     commits/rolls back under _db_lock); it is the shared-file design. The
     principled fix is isolation, not retry/timeout widening: unit tests must
     never write the production data file in the first place.

FIX: at ``pytest_configure`` (before any test module imports ``scp.*`` and
before ``db_manager.get_db()`` opens the persistent connection) we take a
consistent copy of ``data/v13.db`` via the SQLite backup API (WAL-safe, works
while the server writes — verified on this machine) into a private temp dir
and repoint ``db_manager.DB_PATH`` at it. All ``get_db()``/``db_exec`` calls
that use the default path — i.e. every prediction/audit write in T03 — then
hit the private copy: no cross-process contention, and fixture writes/deletes
stop polluting the production DB.

KILL-SWITCH (dev/debug only): ``SCP_T03_DB_ISOLATION=off`` skips the redirect
and runs against the shared file (the pre-N2 behavior).

LIMITS / deliberate non-scope:
  - Modules that pass an EXPLICIT relative ``db_path="data/v13.db"``
    (``RealLearningEngine``/``FastLearningEngine`` constructed in
    ``scp/api_server.py`` import-time) still resolve to the production file.
    flow_06 already suppresses their background threads; any T03 test that
    triggers engine writes must be handled in a follow-up — recorded as a
    NEW FINDING in reports/expert-panel/N2-sqlite-locked-teardown.md, not
    silently papered over here.
  - The copy preserves the exact production schema, so tests that depend on
    table existence keep working; production DATA is visible to T03 (read
    path unchanged), it is no longer WRITABLE from T03. Tests asserting on
    rows they seed themselves are unaffected.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
import time
from pathlib import Path

import pytest

# <repo>/data/v13.db — resolved from this file (tests/T03_capability/conftest.py)
_REPO_ROOT = Path(__file__).resolve().parents[2]
_PROD_DB = _REPO_ROOT / "data" / "v13.db"

# Filled in pytest_configure; read by the session teardown.
_ISOLATED_DIR: str | None = None
_ISOLATED_DB: str | None = None


def _copy_prod_db(dst: str) -> None:
    """Consistent copy of the production DB into `dst` via the backup API.

    WAL readers are not blocked by WAL writers, but a concurrent
    wal_checkpoint(TRUNCATE) from the live server can still surface a
    transient SQLITE_BUSY to the source read — hence bounded retries. If the
    copy cannot be made we fail LOUDLY: falling back to the shared file would
    re-introduce the exact contention this fixture exists to remove.
    """
    last_exc: Exception | None = None
    for _attempt in range(8):
        try:
            src = sqlite3.connect(str(_PROD_DB), timeout=15.0)
            try:
                out = sqlite3.connect(dst)
                try:
                    src.backup(out)
                finally:
                    out.close()
            finally:
                src.close()
            return
        except sqlite3.OperationalError as exc:  # transient lock on the copy read
            last_exc = exc
            time.sleep(1.5)
    raise RuntimeError(
        f"[T03-DB-ISOLATION] cannot snapshot {_PROD_DB} into {dst} after retries: {last_exc}"
    )


def pytest_configure(config: pytest.Config) -> None:
    """Repoint db_manager at a private DB copy before any scp import opens it.

    conftest modules are imported and configured BEFORE test-module collection,
    so ``scp.core.db_manager`` has not been imported yet by any T03 test; we
    import it here (stdlib-only module — safe at configure time), take the
    copy, and patch the single authoritative global.
    """
    global _ISOLATED_DIR, _ISOLATED_DB
    if os.environ.get("SCP_T03_DB_ISOLATION", "on").lower() == "off":
        config.issue_config_time_warning(
            pytest.PytestConfigWarning(
                "[T03-DB-ISOLATION] disabled via SCP_T03_DB_ISOLATION=off — "
                "tests will contend with any live server on data/v13.db"
            ),
            stacklevel=1,
        )
        return

    from scp.core import db_manager

    if db_manager._persistent_conn is not None:
        raise RuntimeError(
            "[T03-DB-ISOLATION] db_manager._persistent_conn already opened "
            f"against {db_manager.DB_PATH} — the redirect ran too late; a "
            "plugin/conftest imported a DB-touching scp module at collection "
            "time. Investigate before trusting any T03 result."
        )

    _ISOLATED_DIR = tempfile.mkdtemp(prefix="scp-t03-db-")
    _ISOLATED_DB = os.path.join(_ISOLATED_DIR, "v13.db")
    if _PROD_DB.exists():
        _copy_prod_db(_ISOLATED_DB)
    else:
        # Fresh checkout: start from an empty DB; product init paths create
        # their tables exactly as they do on first boot.
        sqlite3.connect(_ISOLATED_DB).close()

    db_manager.DB_PATH = _ISOLATED_DB
    # Sync the part-module global copies as well (defensive — the public,
    # rebound functions already read db_manager's globals).
    db_manager._wire_parts()


@pytest.fixture(scope="session", autouse=True)
def _close_isolated_db_connections():
    """Session teardown: close connections we redirected so the temp dir is
    deletable and no test inherits a half-open WAL file. Order after the last
    test's own teardowns is guaranteed by session scope."""
    yield
    try:
        from scp.core import db_manager
    except Exception:  # pragma: no cover - isolation was never enabled
        return
    conn = getattr(db_manager, "_persistent_conn", None)
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass
        db_manager._persistent_conn = None
    for path, pconn in list(getattr(db_manager, "_path_conns", {}).items()):
        if path == _ISOLATED_DB:
            try:
                pconn.close()
            except Exception:
                pass
            db_manager._path_conns.pop(path, None)
    if _ISOLATED_DIR:
        shutil.rmtree(_ISOLATED_DIR, ignore_errors=True)
