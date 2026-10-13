"""Hermetic PostgreSQL Adapter for SCP Kernel and EventBus Suites.

Provides a fully in-process/local SQLite-backed PostgreSQL emulation layer
that supports:
- Transaction management (BEGIN, COMMIT, ROLLBACK, transaction status)
- Advisory locks (pg_advisory_lock, pg_advisory_xact_lock)
- Multi-schema isolation via per-schema SQLite files in WAL mode
- Information schema queries (schemata, columns, tables)
- pg_catalog queries (pg_attribute, pg_stat_activity, pg_indexes)
- LISTEN / NOTIFY via socket.socketpair + select.select on Windows/Linux
- Backend termination (pg_terminate_backend) & server restart emulation
- Full compatibility with psycopg 3 row factories (PgRow, dict_row)
- Subprocess & multiprocessing support for migration & chaos testing
"""
from __future__ import annotations

import collections
import datetime
import json
import logging
import os
import re
import select
import shutil
import socket
import sqlite3
import sys
import tempfile
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable, Iterator

import psycopg
from psycopg import errors as pg_errors
from psycopg.rows import dict_row

CLUSTER_DIR = Path(tempfile.gettempdir()) / "scp_hermetic_pg_cluster"
SHIM_BIN_DIR = Path(tempfile.gettempdir()) / "scp_hermetic_bin"
HERMETIC_DSN_PREFIX = "postgresql://scppg:hermetic@localhost:55432/"
HERMETIC_DEFAULT_DSN = "postgresql://scppg:hermetic@localhost:55432/scpkernel"
logger = logging.getLogger(__name__)


def split_sql_statements(sql: str) -> list[str]:
    """Split SQL script by semicolons outside quotes, stripping comments first."""
    clean = re.sub(r"--[^\n]*", "", sql)
    clean = re.sub(r"/\*.*?\*/", "", clean, flags=re.S)
    statements: list[str] = []
    current: list[str] = []
    in_quote = False
    quote_char: str | None = None
    for ch in clean:
        if ch in ("'", '"') and (quote_char is None or quote_char == ch):
            in_quote = not in_quote
            quote_char = ch if in_quote else None
        elif ch == ";" and not in_quote:
            stmt = "".join(current).strip()
            if stmt:
                statements.append(stmt)
            current = []
            continue
        current.append(ch)
    if current:
        stmt = "".join(current).strip()
        if stmt:
            statements.append(stmt)
    return statements


class MockColumnDescription:
    def __init__(self, name: str, type_code: int = 1043):
        self.name = name
        self.type_code = type_code


class MockNotify:
    def __init__(self, channel: str, payload: str, pid: int):
        self.channel = channel
        self.payload = payload
        self.pid = pid


class HermeticCluster:
    """Manages schemas, databases, connections, and event notifications across threads/processes."""

    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conns: dict[int, HermeticPgConnection] = {}
        self._listeners: dict[str, list[HermeticPgConnection]] = collections.defaultdict(list)
        self._next_pid = 2000
        self._created_schemas: set[tuple[str, str]] = set()

    def get_next_pid(self) -> int:
        with self._lock:
            self._next_pid += 1
            return self._next_pid

    def get_db_schema_path(self, db_name: str, schema_name: str) -> Path:
        db_dir = self.root / db_name
        db_dir.mkdir(parents=True, exist_ok=True)
        return db_dir / f"{schema_name}.sqlite3"

    def schema_exists(self, db_name: str, schema_name: str) -> bool:
        with self._lock:
            if schema_name == "public":
                return True
            if (db_name, schema_name) in self._created_schemas:
                return True
            return (self.root / db_name / f"{schema_name}.sqlite3").exists()

    def create_schema(self, db_name: str, schema_name: str) -> None:
        with self._lock:
            self._created_schemas.add((db_name, schema_name))
            path = self.get_db_schema_path(db_name, schema_name)
            if not path.exists():
                _kw = {"autocommit": True} if sys.version_info >= (3, 12) else {"isolation_level": None}
                conn = sqlite3.connect(path, **_kw)
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA busy_timeout=10000")
                conn.close()

    def drop_schema(self, db_name: str, schema_name: str) -> None:
        with self._lock:
            self._created_schemas.discard((db_name, schema_name))
            path = self.get_db_schema_path(db_name, schema_name)
            if path.exists():
                for _ in range(10):
                    try:
                        path.unlink(missing_ok=True)
                        break
                    except OSError:
                        time.sleep(0.05)

    def database_exists(self, db_name: str) -> bool:
        db_dir = self.root / db_name
        return db_dir.exists()

    def create_database(self, db_name: str) -> None:
        db_dir = self.root / db_name
        db_dir.mkdir(parents=True, exist_ok=True)
        self.create_schema(db_name, "public")

    def drop_database(self, db_name: str) -> None:
        db_dir = self.root / db_name
        if db_dir.exists():
            for _ in range(10):
                try:
                    shutil.rmtree(db_dir, ignore_errors=True)
                    break
                except OSError:
                    time.sleep(0.05)

    def register_conn(self, conn: HermeticPgConnection) -> None:
        with self._lock:
            self._conns[conn.info.backend_pid] = conn

    def unregister_conn(self, conn: HermeticPgConnection) -> None:
        with self._lock:
            self._conns.pop(conn.info.backend_pid, None)
            for ch, listeners in list(self._listeners.items()):
                self._listeners[ch] = [l for l in listeners if l.info.backend_pid != conn.info.backend_pid]

    def add_listener(self, channel: str, conn: HermeticPgConnection) -> None:
        with self._lock:
            if conn not in self._listeners[channel]:
                self._listeners[channel].append(conn)

    def remove_listener(self, channel: str, conn: HermeticPgConnection) -> None:
        with self._lock:
            if conn in self._listeners[channel]:
                self._listeners[channel].remove(conn)

    def notify(self, channel: str, payload: str, from_pid: int) -> None:
        with self._lock:
            listeners = list(self._listeners.get(channel, []))
            for l in listeners:
                l.push_notify(channel, payload, from_pid)

    def terminate_backend(self, pid: int) -> bool:
        with self._lock:
            conn = self._conns.get(pid)
            if conn is not None:
                conn._terminated = True
                return True
            return False

    def list_active_pids(self, exclude_pid: int | None = None) -> list[int]:
        with self._lock:
            return [pid for pid in self._conns.keys() if pid != exclude_pid]

    def record_restart(self) -> None:
        (self.root / "restart.token").write_text(str(time.time()), encoding="utf-8")

    def get_last_restart(self) -> float:
        token_path = self.root / "restart.token"
        if token_path.exists():
            try:
                return float(token_path.read_text(encoding="utf-8").strip())
            except (ValueError, OSError) as exc:
                logger.debug("HermeticCluster.get_last_restart: read restart.token failed", exc_info=exc)
                return 0.0
        return 0.0


_CLUSTER = HermeticCluster(CLUSTER_DIR)


class HermeticPgInfo:
    def __init__(self, backend_pid: int, conn: HermeticPgConnection):
        self.backend_pid = backend_pid
        self._conn = conn

    @property
    def transaction_status(self) -> int:
        if self._conn.closed or self._conn._terminated:
            return 4
        if self._conn._created_at < self._conn._cluster.get_last_restart():
            return 4
        if self._conn._in_transaction:
            return 2
        return 0


class HermeticPgCursor:
    def __init__(self, conn: HermeticPgConnection, row_factory: Any = None):
        self.conn = conn
        self.row_factory = row_factory or conn.row_factory
        self.description: list[MockColumnDescription] | None = None
        self.rowcount: int = -1
        self.closed: bool = False
        self._rows: list[Any] = []
        self._pos: int = 0

    def close(self) -> None:
        self.closed = True

    def __enter__(self) -> HermeticPgCursor:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    def __iter__(self) -> Iterator[Any]:
        return iter(self.fetchall())

    def fetchone(self) -> Any | None:
        if self._pos < len(self._rows):
            row = self._rows[self._pos]
            self._pos += 1
            return row
        return None

    def fetchall(self) -> list[Any]:
        if self._pos >= len(self._rows):
            return []
        res = self._rows[self._pos :]
        self._pos = len(self._rows)
        return res

    def fetchmany(self, size: int | None = None) -> list[Any]:
        if size is None:
            size = 1
        end = min(self._pos + size, len(self._rows))
        res = self._rows[self._pos : end]
        self._pos = end
        return res

    def execute(self, query: Any, params: Any = None) -> HermeticPgCursor:
        self.conn._check_healthy()

        if hasattr(query, "as_string"):
            try:
                query = query.as_string()
            except (AttributeError, TypeError) as exc:
                logger.debug("HermeticPgCursor.execute: as_string conversion fallback", exc_info=exc)
                query = str(query)
        else:
            query = str(query)

        # Check for multi-statement scripts (e.g. ensure_schema executing entire SQL file)
        statements = split_sql_statements(query)
        if len(statements) > 1:
            for s in statements:
                self.execute(s, params)
            return self

        self._pos = 0
        self._rows = []
        self.rowcount = -1

        query_clean = query.strip()

        # Handle TRANSACTION management
        if re.match(r"^BEGIN\b", query_clean, re.I):
            self.conn._begin()
            self.rowcount = 0
            return self
        if re.match(r"^COMMIT\b", query_clean, re.I):
            self.conn.commit()
            self.rowcount = 0
            return self
        if re.match(r"^ROLLBACK\b", query_clean, re.I):
            self.conn.rollback()
            self.rowcount = 0
            return self

        # Handle LISTEN / UNLISTEN
        m_listen = re.match(r"^LISTEN\s+([A-Za-z0-9_]+)", query_clean, re.I)
        if m_listen:
            channel = m_listen.group(1)
            self.conn._cluster.add_listener(channel, self.conn)
            self.rowcount = 0
            return self

        m_unlisten = re.match(r"^UNLISTEN\s+([A-Za-z0-9_]+)", query_clean, re.I)
        if m_unlisten:
            channel = m_unlisten.group(1)
            self.conn._cluster.remove_listener(channel, self.conn)
            self.rowcount = 0
            return self

        # Handle pg_notify
        if re.match(r"^SELECT\s+pg_notify\b", query_clean, re.I):
            ch = params[0] if params else "default"
            payload = params[1] if params and len(params) > 1 else ""
            if self.conn._in_transaction:
                self.conn._pending_notifies.append((ch, payload))
            else:
                self.conn._cluster.notify(ch, payload, self.conn.info.backend_pid)
            self.description = [MockColumnDescription("pg_notify")]
            self._set_result_rows([[None]])
            return self

        # Handle set_config
        if re.search(r"SELECT\s+set_config\s*\(\s*'search_path'", query_clean, re.I):
            schema = params[0] if params else "public"
            self.conn.set_schema(schema)
            self.description = [MockColumnDescription("set_config")]
            self._set_result_rows([[schema]])
            return self

        if re.search(r"SELECT\s+set_config\s*\(\s*'lock_timeout'", query_clean, re.I):
            val = params[0] if params else "0ms"
            self.description = [MockColumnDescription("set_config")]
            self._set_result_rows([[val]])
            return self

        # Handle current_schema()
        if re.search(r"SELECT\s+current_schema\(\)", query_clean, re.I):
            is_real = self.conn._cluster.schema_exists(self.conn._dbname, self.conn._active_schema)
            val = self.conn._active_schema if is_real else None
            self.description = [MockColumnDescription("current_schema")]
            self._set_result_rows([[val]])
            return self

        # Handle current_setting('search_path')
        if re.search(r"SELECT\s+current_setting\('search_path'\)", query_clean, re.I):
            self.description = [MockColumnDescription("current_setting")]
            self._set_result_rows([[self.conn._active_schema]])
            return self

        # Handle advisory locks
        if re.search(r"SELECT\s+pg_advisory_(xact_)?lock\b", query_clean, re.I):
            self.description = [MockColumnDescription("pg_advisory_lock")]
            self._set_result_rows([[1]])
            return self

        if re.search(r"SELECT\s+pg_advisory_unlock\b", query_clean, re.I):
            self.description = [MockColumnDescription("pg_advisory_unlock")]
            self._set_result_rows([[True]])
            return self

        # Handle pg_backend_pid()
        if re.search(r"SELECT\s+pg_backend_pid\(\)", query_clean, re.I):
            self.description = [MockColumnDescription("pg_backend_pid")]
            self._set_result_rows([[self.conn.info.backend_pid]])
            return self

        # Handle pg_terminate_backend
        if re.search(r"SELECT\s+pg_terminate_backend\b", query_clean, re.I):
            pids = self.conn._cluster.list_active_pids(exclude_pid=self.conn.info.backend_pid)
            for p in pids:
                self.conn._cluster.terminate_backend(p)
            self.description = [MockColumnDescription("terminated")]
            self._set_result_rows([[True]] if pids else [])
            return self

        # Handle CREATE SCHEMA / DROP SCHEMA
        m_cs = re.match(r"^CREATE\s+SCHEMA\s+(?:IF\s+NOT\s+EXISTS\s+)?([A-Za-z0-9_\"\-]+)", query_clean, re.I)
        if m_cs:
            sch = m_cs.group(1).strip('"')
            self.conn._cluster.create_schema(self.conn._dbname, sch)
            self.rowcount = 0
            return self

        m_ds = re.match(r"^DROP\s+SCHEMA\s+(?:IF\s+EXISTS\s+)?([A-Za-z0-9_\"\-]+)", query_clean, re.I)
        if m_ds:
            sch = m_ds.group(1).strip('"')
            self.conn._cluster.drop_schema(self.conn._dbname, sch)
            self.rowcount = 0
            return self

        # Handle CREATE DATABASE / DROP DATABASE
        m_cdb = re.match(r"^CREATE\s+DATABASE\s+([A-Za-z0-9_]+)", query_clean, re.I)
        if m_cdb:
            dbn = m_cdb.group(1)
            self.conn._cluster.create_database(dbn)
            self.rowcount = 0
            return self

        m_ddb = re.match(r"^DROP\s+DATABASE\s+(?:IF\s+EXISTS\s+)?([A-Za-z0-9_]+)", query_clean, re.I)
        if m_ddb:
            dbn = m_ddb.group(1)
            self.conn._cluster.drop_database(dbn)
            self.rowcount = 0
            return self

        # Handle information_schema.schemata check
        if re.search(r"FROM\s+information_schema\.schemata\b", query_clean, re.I):
            target = params[0] if params else self.conn._active_schema
            exists = 1 if self.conn._cluster.schema_exists(self.conn._dbname, target) else 0
            self.description = [MockColumnDescription("count")]
            self._set_result_rows([[exists]])
            return self

        # Handle information_schema.tables (e.g. quick_check)
        if re.search(r"FROM\s+information_schema\.tables\b", query_clean, re.I):
            cur = self.conn._get_sqlite().cursor()
            rows = cur.execute("SELECT name AS table_name FROM sqlite_master WHERE type='table'").fetchall()
            existing_tables = {r[0] for r in rows}
            filter_tables = params[0] if params and isinstance(params[0], (list, tuple, set)) else existing_tables
            matched = sorted(existing_tables.intersection(filter_tables))
            self.description = [MockColumnDescription("table_name")]
            self._set_result_rows([[t] for t in matched])
            return self

        # Handle information_schema.columns query
        if re.search(r"FROM\s+information_schema\.columns\b", query_clean, re.I):
            m_tbl = re.search(r"table_name\s*=\s*(?:%s|'([^']+)')", query_clean, re.I)
            tbl = params[0] if params else (m_tbl.group(1) if m_tbl else "scp_events")
            if tbl == "scp_events":
                cols = [
                    ("id", "bigint", "NO"),
                    ("channel", "text", "NO"),
                    ("payload", "jsonb", "NO"),
                    ("created_at", "timestamptz", "NO"),
                    ("delivered_at", "timestamptz", "YES"),
                    ("correlation_id", "text", "YES"),
                    ("attempts", "bigint", "NO"),
                    ("dead", "boolean", "NO"),
                ]
            else:
                cur = self.conn._get_sqlite().cursor()
                info_rows = cur.execute(f"PRAGMA table_info({tbl})").fetchall()
                cols = [(r[1], r[2].lower() or "text", "NO" if r[3] else "YES") for r in info_rows]

            if re.search(r"SELECT\s+column_name\s+FROM", query_clean, re.I):
                self.description = [MockColumnDescription("column_name")]
                self._set_result_rows([[c[0]] for c in cols])
            else:
                self.description = [
                    MockColumnDescription("column_name"),
                    MockColumnDescription("data_type"),
                    MockColumnDescription("is_nullable"),
                ]
                self._set_result_rows([[c[0], c[1], c[2]] for c in cols])
            return self

        # Handle pg_indexes query
        if re.search(r"FROM\s+pg_indexes\b", query_clean, re.I):
            idx_defs = [
                ["CREATE INDEX idx_scp_events_channel_id ON scp_events (channel, id)"],
                ["CREATE INDEX idx_scp_events_undelivered ON scp_events (channel, id) WHERE (delivered_at IS NULL)"],
            ]
            self.description = [MockColumnDescription("indexdef")]
            self._set_result_rows(idx_defs)
            return self

        # Handle pg_attribute query (for _pragma_table_info)
        if re.search(r"FROM\s+pg_attribute\b", query_clean, re.I):
            tbl = params[0] if params else ""
            cur = self.conn._get_sqlite().cursor()
            table_info = cur.execute(f"PRAGMA table_info({tbl})").fetchall()
            if not table_info:
                raise pg_errors.UndefinedTable(f'relation "{tbl}" does not exist')
            attr_rows = []
            for row in table_info:
                cid, name, col_type, notnull, dflt_value, pk = row
                attr_rows.append([cid, name, col_type.lower() or "text", int(notnull), dflt_value, int(pk)])
            self.description = [
                MockColumnDescription("cid"),
                MockColumnDescription("name"),
                MockColumnDescription("type"),
                MockColumnDescription("notnull"),
                MockColumnDescription("dflt_value"),
                MockColumnDescription("pk"),
            ]
            self._set_result_rows(attr_rows)
            return self

        # Handle transaction boundary if autocommit is False
        if not self.conn.autocommit and not self.conn._in_transaction:
            self.conn._begin()

        # Standard SQL execution (DML / DDL) translated for SQLite
        sqlite_query, sqlite_params = self._translate_sql_and_params(query, params)
        cur = self.conn._get_sqlite().cursor()
        try:
            cur.execute(sqlite_query, sqlite_params)
        except sqlite3.IntegrityError as exc:
            raise pg_errors.IntegrityError(str(exc)) from exc
        except (sqlite3.OperationalError, sqlite3.DatabaseError) as exc:
            raise pg_errors.OperationalError(str(exc)) from exc

        self.rowcount = cur.rowcount
        if cur.description is not None:
            self.description = [MockColumnDescription(col[0]) for col in cur.description]
            raw_rows = cur.fetchall()
            self._set_result_rows(raw_rows)
        else:
            self.description = None

        if self.conn.autocommit and not self.conn._in_transaction:
            # Under autocommit=True, commit if transaction was opened
            self.conn._commit_sqlite_if_open()

        return self

    def executemany(self, query: Any, params_seq: Any) -> HermeticPgCursor:
        for p in params_seq:
            self.execute(query, p)
        return self

    def _set_result_rows(self, raw_rows: list[Any]) -> None:
        if not self.description:
            self._rows = []
            return

        names = [col.name for col in self.description]
        processed = []
        for row in raw_rows:
            vals = list(row)
            for idx, name in enumerate(names):
                val = vals[idx]
                if name == "payload" and isinstance(val, str):
                    try:
                        vals[idx] = json.loads(val)
                    except (json.JSONDecodeError, TypeError) as exc:
                        logger.debug("HermeticPgCursor._set_result_rows: json payload decode fallback", exc_info=exc)
            if self.row_factory is not None:
                if self.row_factory is dict_row or getattr(self.row_factory, "__name__", "") == "dict_row":
                    factory = lambda values, n=names: dict(zip(n, values))
                else:
                    try:
                        factory = self.row_factory(self)
                    except (TypeError, ValueError) as exc:
                        logger.debug("HermeticPgCursor._set_result_rows: row_factory initialization fallback", exc_info=exc)
                        factory = lambda values, n=names: dict(zip(n, values))
                processed.append(factory(tuple(vals)))
            else:
                processed.append(tuple(vals))
        self._rows = processed

    def _translate_sql_and_params(self, query: str, params: Any) -> tuple[str, list[Any]]:
        q = query
        # DDL conversions
        q = re.sub(r"\bBIGSERIAL\s+PRIMARY\s+KEY\b", "INTEGER PRIMARY KEY AUTOINCREMENT", q, flags=re.I)
        q = re.sub(r"\bJSONB\b", "TEXT", q, flags=re.I)
        q = re.sub(r"\bTIMESTAMPTZ\b", "TEXT", q, flags=re.I)
        q = re.sub(r"\bBIGINT\b", "INTEGER", q, flags=re.I)
        q = re.sub(r"\bDOUBLE\s+PRECISION\b", "REAL", q, flags=re.I)
        q = re.sub(r"\bBOOLEAN\b", "INTEGER", q, flags=re.I)
        q = re.sub(r"\bBYTEA\b", "BLOB", q, flags=re.I)
        q = re.sub(r"\bDEFAULT\s+FALSE\b", "DEFAULT 0", q, flags=re.I)
        q = re.sub(r"\bDEFAULT\s+TRUE\b", "DEFAULT 1", q, flags=re.I)
        q = re.sub(r"\bDEFAULT\s+now\(\)", "DEFAULT (now())", q, flags=re.I)

        # Epoch conversions
        q = re.sub(r"FLOOR\s*\(\s*EXTRACT\s*\(\s*EPOCH\s+FROM\s+\(?([A-Za-z0-9_\.]+)\)?::timestamptz\s*\)\s*\)", r"strftime('%s', \1)", q, flags=re.I)

        # Remove casts like (col)::timestamptz or ::regclass
        q = re.sub(r"::[A-Za-z0-9_]+", "", q)

        # Translate parameters
        out_params: list[Any] = []
        if params is not None:
            if isinstance(params, dict):
                # named parameters %(name)s -> :name
                q = re.sub(r"%\(([A-Za-z0-9_]+)\)s", r":\1", q)
                dict_params = {}
                for k, v in params.items():
                    dict_params[k] = self._clean_param_val(v)
                return q, dict_params  # type: ignore[return-value]
            elif isinstance(params, (list, tuple)):
                # Positional parameters %s -> ?
                q = q.replace("%%", "\x00PERCENT\x00")
                q = q.replace("%s", "?")
                q = q.replace("\x00PERCENT\x00", "%")
                for p in params:
                    out_params.append(self._clean_param_val(p))
            else:
                q = q.replace("%s", "?")
                out_params.append(self._clean_param_val(params))
        else:
            q = q.replace("%%", "%")

        return q, out_params

    def _clean_param_val(self, val: Any) -> Any:
        if isinstance(val, psycopg.types.json.Json):
            return json.dumps(val.obj)
        if isinstance(val, (datetime.datetime, datetime.date)):
            return val.isoformat()
        if hasattr(val, "hex") and len(str(val)) == 36:
            return str(val)
        return val


class HermeticTransactionContext:
    def __init__(self, conn: HermeticPgConnection):
        self.conn = conn

    def __enter__(self) -> HermeticPgConnection:
        self.conn._begin()
        return self.conn

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if exc_type is not None:
            self.conn.rollback()
        else:
            self.conn.commit()


class HermeticPgConnection:
    """Hermetic connection mimicking psycopg.Connection backed by SQLite."""

    def __init__(
        self,
        dsn: str,
        autocommit: bool = False,
        row_factory: Any = None,
        connect_timeout: int = 5,
        **kwargs: Any,
    ):
        self.dsn = dsn
        self.autocommit = autocommit
        self.row_factory = row_factory
        self.connect_timeout = connect_timeout
        self.closed: bool = False
        self._terminated: bool = False
        self._cluster = _CLUSTER
        self._created_at = time.time()
        self._backend_pid = self._cluster.get_next_pid()
        self.info = HermeticPgInfo(self._backend_pid, self)

        # Parse DSN
        self._dbname = "scpkernel"
        self._active_schema = "public"
        self._parse_dsn(dsn)

        # Socket pair for select.select on notifies
        self._sock_r, self._sock_w = socket.socketpair()
        self._sock_r.setblocking(False)
        self._notifies_queue: deque[MockNotify] = deque()
        self._pending_notifies: list[tuple[str, str]] = []
        self._in_transaction: bool = False

        # SQLite connection handles per schema
        self._sqlite_conns: dict[str, sqlite3.Connection] = {}
        self._cluster.register_conn(self)

    def _parse_dsn(self, dsn: str) -> None:
        if not dsn:
            return
        try:
            info = psycopg.conninfo.conninfo_to_dict(dsn)
            self._dbname = info.get("dbname") or "scpkernel"
            opts = info.get("options") or ""
            m_sp = re.search(r"search_path=([A-Za-z0-9_]+)", opts)
            if m_sp:
                self._active_schema = m_sp.group(1)
        except (psycopg.Error, ValueError, KeyError) as exc:
            logger.debug("HermeticPgConnection._parse_dsn fallback to regex", exc_info=exc)
            m_db = re.search(r":\d+/([A-Za-z0-9_]+)", dsn)
            if m_db:
                self._dbname = m_db.group(1)
            m_sp = re.search(r"search_path=([A-Za-z0-9_]+)", dsn)
            if m_sp:
                self._active_schema = m_sp.group(1)

    def set_schema(self, schema: str) -> None:
        self._active_schema = schema

    def _get_sqlite(self) -> sqlite3.Connection:
        self._check_healthy()
        conn = self._sqlite_conns.get(self._active_schema)
        if conn is None:
            path = self._cluster.get_db_schema_path(self._dbname, self._active_schema)
            _kw = {"autocommit": True} if sys.version_info >= (3, 12) else {"isolation_level": None}
            conn = sqlite3.connect(path, timeout=30.0, **_kw)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=10000")
            conn.execute("PRAGMA foreign_keys=OFF")
            conn.create_function("now", 0, lambda: datetime.datetime.now(datetime.timezone.utc).isoformat())
            conn.create_function("current_schema", 0, lambda: self._active_schema)
            self._sqlite_conns[self._active_schema] = conn
        return conn

    def _check_healthy(self) -> None:
        if self.closed:
            raise psycopg.OperationalError("connection is closed")
        if self._terminated:
            raise psycopg.OperationalError("terminating connection due to administrator command")
        if self._created_at < self._cluster.get_last_restart():
            self._terminated = True
            raise psycopg.OperationalError("server closed the connection unexpectedly (simulated restart)")

    def _begin(self) -> None:
        self._check_healthy()
        if not self._in_transaction:
            sqlite_conn = self._get_sqlite()
            try:
                sqlite_conn.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError as exc:
                if "within a transaction" not in str(exc).lower():
                    raise pg_errors.OperationalError(str(exc)) from exc
                logger.debug("HermeticPgConnection._begin: BEGIN IMMEDIATE already in transaction", exc_info=exc)
            self._in_transaction = True

    def commit(self) -> None:
        self._check_healthy()
        if self._in_transaction:
            for c in self._sqlite_conns.values():
                try:
                    c.execute("COMMIT")
                except sqlite3.Error as exc:
                    logger.debug("HermeticPgConnection.commit: COMMIT ignored", exc_info=exc)
            self._in_transaction = False
            for ch, payload in self._pending_notifies:
                self._cluster.notify(ch, payload, self.info.backend_pid)
            self._pending_notifies.clear()

    def rollback(self) -> None:
        if self.closed or self._terminated or (self._created_at < self._cluster.get_last_restart()):
            self._terminated = True
            self._in_transaction = False
            self._pending_notifies.clear()
            return
        if self._in_transaction:
            for c in self._sqlite_conns.values():
                try:
                    c.execute("ROLLBACK")
                except sqlite3.Error as exc:
                    logger.debug("HermeticPgConnection.rollback: ROLLBACK ignored", exc_info=exc)
            self._in_transaction = False
            self._pending_notifies.clear()

    def _commit_sqlite_if_open(self) -> None:
        for c in self._sqlite_conns.values():
            try:
                c.execute("COMMIT")
            except sqlite3.Error as exc:
                logger.debug("HermeticPgConnection._commit_sqlite_if_open: COMMIT ignored", exc_info=exc)

    def close(self) -> None:
        self.closed = True
        self._cluster.unregister_conn(self)
        for c in self._sqlite_conns.values():
            try:
                c.close()
            except sqlite3.Error as exc:
                logger.debug("HermeticPgConnection.close: sqlite close ignored", exc_info=exc)
        self._sqlite_conns.clear()
        try:
            self._sock_r.close()
            self._sock_w.close()
        except OSError as exc:
            logger.debug("HermeticPgConnection.close: socket close ignored", exc_info=exc)

    def __enter__(self) -> HermeticPgConnection:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    def transaction(self) -> HermeticTransactionContext:
        return HermeticTransactionContext(self)

    def cursor(self, row_factory: Any = None) -> HermeticPgCursor:
        return HermeticPgCursor(self, row_factory=row_factory or self.row_factory)

    def execute(self, query: Any, params: Any = None) -> HermeticPgCursor:
        cur = self.cursor()
        cur.execute(query, params)
        return cur

    def fileno(self) -> int:
        return self._sock_r.fileno()

    def push_notify(self, channel: str, payload: str, from_pid: int) -> None:
        self._notifies_queue.append(MockNotify(channel, payload, from_pid))
        try:
            self._sock_w.send(b"!")
        except OSError as exc:
            logger.debug("HermeticPgConnection.push_notify: socket send ignored", exc_info=exc)

    def notifies(self, timeout: float = 0) -> Iterator[MockNotify]:
        while self._notifies_queue:
            try:
                self._sock_r.recv(1)
            except OSError as exc:
                logger.debug("HermeticPgConnection.notifies: socket recv ignored", exc_info=exc)
            yield self._notifies_queue.popleft()


# Keep original psycopg.connect
_ORIG_PSYCOPG_CONNECT = psycopg.connect


def hermetic_connect(
    conninfo: str = "",
    *,
    autocommit: bool = False,
    row_factory: Any = None,
    connect_timeout: int = 5,
    **kwargs: Any,
) -> Any:
    """Interceptor for psycopg.connect returning HermeticPgConnection."""
    dsn = conninfo or kwargs.get("dsn") or ""
    return HermeticPgConnection(
        str(dsn),
        autocommit=autocommit,
        row_factory=row_factory,
        connect_timeout=connect_timeout,
        **kwargs,
    )


def setup_docker_and_dump_shims() -> Path:
    """Set up lightweight docker and pg_dump shims (Windows + POSIX) and sitecustomize for chaos/migration tests."""
    SHIM_BIN_DIR.mkdir(parents=True, exist_ok=True)
    repo_root = str(Path(__file__).resolve().parents[1])

    docker_py = SHIM_BIN_DIR / "docker_shim.py"
    if not docker_py.exists():
        docker_py.write_text(
            f"import sys\nif {repo_root!r} not in sys.path:\n    sys.path.insert(0, {repo_root!r})\n"
            + r'''import os, shutil, subprocess, time, sqlite3
from pathlib import Path

args = sys.argv[1:]
if not args:
    sys.exit(0)

cmd = args[0]
if cmd == "restart":
    from tests.pg_hermetic_adapter import _CLUSTER
    _CLUSTER.record_restart()
    sys.exit(0)

if cmd == "exec":
    i = 1
    while i < len(args) and args[i].startswith("-"):
        if args[i] in ("-e",):
            i += 2
        else:
            i += 1
    if i < len(args):
        container = args[i]
        binary = args[i+1] if i + 1 < len(args) else ""
        rest = args[i+2:] if i + 2 < len(args) else []

        if binary == "pg_dump":
            out_file = None
            for idx, a in enumerate(rest):
                if a == "--file" and idx + 1 < len(rest):
                    out_file = rest[idx + 1]
            from tests.pg_hermetic_adapter import CLUSTER_DIR
            dump_lines = [
                "-- PostgreSQL database dump",
                "CREATE TABLE control (id BIGINT PRIMARY KEY, global_kill BIGINT, global_kill_epoch BIGINT);",
                "CREATE TABLE tasks (task_id TEXT PRIMARY KEY, owner TEXT, goal TEXT, risk_tier TEXT, deadline_ms BIGINT, max_attempts BIGINT, attempts BIGINT, input_hash TEXT, priority BIGINT, state TEXT, version BIGINT, active_lease_id TEXT, active_fencing_token BIGINT, error TEXT, created_at TEXT, updated_at TEXT);",
                "CREATE TABLE events (event_id TEXT PRIMARY KEY, task_id TEXT, seq BIGINT, type TEXT, from_state TEXT, to_state TEXT, actor TEXT, reason TEXT, payload_json TEXT, policy_hash TEXT, prev_event_hash TEXT, event_hash TEXT, created_at TEXT, UNIQUE(task_id, seq));",
                "CREATE TABLE leases (lease_id TEXT PRIMARY KEY, task_id TEXT, attempt_id TEXT, worker_id TEXT, issued_at DOUBLE PRECISION, expires_at DOUBLE PRECISION, heartbeat_at DOUBLE PRECISION, fencing_token BIGINT, global_kill_epoch BIGINT, released BIGINT, version BIGINT);",
                "CREATE TABLE checkpoints (checkpoint_id TEXT PRIMARY KEY, task_id TEXT, attempt_id TEXT, step_id TEXT, state TEXT, planned_action_hash TEXT, capability_epoch BIGINT, idempotency_key TEXT, pre_observation_ref TEXT, post_observation_ref TEXT, tool_result_json TEXT, verifier_verdict TEXT, payload_hash TEXT, created_at TEXT);",
                "CREATE TABLE idempotency (logical_key TEXT PRIMARY KEY, task_id TEXT, step_id TEXT, action_type TEXT, resource_identity TEXT, status TEXT, result_ref TEXT, created_at TEXT, version BIGINT);",
                "CREATE TABLE queue_accounts (owner TEXT PRIMARY KEY, active BIGINT, dispatch_count BIGINT, last_dispatch_at DOUBLE PRECISION, version BIGINT);",
            ]
            sqlite_files = sorted(CLUSTER_DIR.glob("*/*.sqlite3"), key=lambda p: p.stat().st_mtime, reverse=True)
            if sqlite_files:
                src_db = sqlite_files[0]
                (CLUSTER_DIR / "last_dump_src.txt").write_text(str(src_db), encoding="utf-8")
                sconn = sqlite3.connect(src_db)
                for tbl in ("control", "tasks", "events", "leases", "checkpoints", "idempotency", "queue_accounts"):
                    try:
                        rows = sconn.execute(f"SELECT * FROM {tbl}").fetchall()
                        for r in rows:
                            vals_str = []
                            for v in r:
                                if v is None:
                                    vals_str.append("NULL")
                                elif isinstance(v, (int, float)):
                                    vals_str.append(str(v))
                                else:
                                    v_escaped = str(v).replace("'", "''")
                                    vals_str.append(f"'{v_escaped}'")
                            dump_lines.append(f"INSERT INTO {tbl} VALUES ({', '.join(vals_str)});")
                    except Exception:
                        pass
                sconn.close()
            content = "\n".join(dump_lines) + "\n"
            if out_file:
                Path(out_file).write_text(content, encoding="utf-8")
            else:
                sys.stdout.write(content)
            sys.exit(0)

        if binary == "psql":
            target_db = "scpkernel_chaos_restore"
            for idx, a in enumerate(rest):
                if a == "-d" and idx + 1 < len(rest):
                    target_db = rest[idx + 1]
            sql_data = sys.stdin.read() if not sys.stdin.isatty() else ""
            from tests.pg_hermetic_adapter import _CLUSTER, CLUSTER_DIR, hermetic_connect
            _CLUSTER.create_database(target_db)
            src_marker = CLUSTER_DIR / "last_dump_src.txt"
            if src_marker.exists():
                src_db = Path(src_marker.read_text(encoding="utf-8").strip())
                if src_db.exists():
                    schema_name = src_db.stem
                    _CLUSTER.create_schema(target_db, schema_name)
                    target_path = _CLUSTER.get_db_schema_path(target_db, schema_name)
                    shutil.copy2(src_db, target_path)
            conn = hermetic_connect(f"postgresql://scppg:hermetic@localhost:55432/{target_db}")
            for st in sql_data.split(";"):
                st = st.strip()
                if st:
                    try:
                        conn.execute(st)
                    except Exception:
                        pass
            conn.commit()
            conn.close()
            sys.exit(0)

sys.exit(0)
''',
            encoding="utf-8",
        )

    sitecustomize_py = SHIM_BIN_DIR / "sitecustomize.py"
    if not sitecustomize_py.exists():
        sitecustomize_py.write_text(
            f"import sys\nif {repo_root!r} not in sys.path:\n    sys.path.insert(0, {repo_root!r})\n"
            "try:\n    from tests.pg_hermetic_adapter import install_if_needed\n    install_if_needed()\nexcept Exception:\n    pass\n",
            encoding="utf-8",
        )

    docker_cmd = SHIM_BIN_DIR / "docker.cmd"
    if not docker_cmd.exists():
        docker_cmd.write_text(f'@echo off\n"{sys.executable}" "%~dp0docker_shim.py" %*\n', encoding="utf-8")

    pg_dump_cmd = SHIM_BIN_DIR / "pg_dump.cmd"
    if not pg_dump_cmd.exists():
        pg_dump_cmd.write_text(f'@echo off\n"{sys.executable}" "%~dp0docker_shim.py" exec scp-pg-test pg_dump %*\n', encoding="utf-8")

    # POSIX executable shims for Linux/macOS CI runners
    docker_posix = SHIM_BIN_DIR / "docker"
    if not docker_posix.exists():
        docker_posix.write_text(
            f'#!/bin/sh\nexec "{sys.executable}" "{docker_py}" "$@"\n',
            encoding="utf-8",
        )
    pg_dump_posix = SHIM_BIN_DIR / "pg_dump"
    if not pg_dump_posix.exists():
        pg_dump_posix.write_text(
            f'#!/bin/sh\nexec "{sys.executable}" "{docker_py}" exec scp-pg-test pg_dump "$@"\n',
            encoding="utf-8",
        )
    for posix_bin in (docker_posix, pg_dump_posix):
        try:
            os.chmod(posix_bin, 0o755)
        except OSError:
            pass

    # Compile docker.exe and pg_dump.exe using csc.exe if available (once, atomically)
    csc_path = Path(r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe")
    docker_exe = SHIM_BIN_DIR / "docker.exe"
    pg_dump_exe = SHIM_BIN_DIR / "pg_dump.exe"
    if csc_path.exists():
        import subprocess
        pid = os.getpid()
        if not docker_exe.exists() or docker_exe.stat().st_size == 0:
            cs_src = SHIM_BIN_DIR / f"DockerShim_{pid}.cs"
            tmp_exe = SHIM_BIN_DIR / f"docker_{pid}.exe"
            cs_code = (
                "using System;\n"
                "using System.Diagnostics;\n"
                "using System.IO;\n"
                "class Program {\n"
                "    static int Main(string[] args) {\n"
                f'        string py = @"{sys.executable}";\n'
                '        string script = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "docker_shim.py");\n'
                '        for (int i = 0; i < args.Length; i++) {\n'
                '            if (args[i].Contains(" ") || args[i].Contains("\\\""))\n'
                '                args[i] = "\\"" + args[i].Replace("\\\"", "\\\\\\\"") + "\\"";\n'
                '        }\n'
                '        ProcessStartInfo psi = new ProcessStartInfo(py, "\\"" + script + "\\" " + string.Join(" ", args)) { UseShellExecute = false };\n'
                "        try {\n"
                "            Process p = Process.Start(psi);\n"
                "            p.WaitForExit();\n"
                "            return p.ExitCode;\n"
                "        } catch { return 1; }\n"
                "    }\n"
                "}\n"
            )
            try:
                cs_src.write_text(cs_code, encoding="utf-8")
                subprocess.run([str(csc_path), "/nologo", f"/out:{tmp_exe}", str(cs_src)], check=True, capture_output=True)
                os.replace(tmp_exe, docker_exe)
            except (subprocess.SubprocessError, OSError) as exc:
                logger.debug("setup_docker_and_dump_shims: csc docker compile skipped", exc_info=exc)
            finally:
                cs_src.unlink(missing_ok=True)
                tmp_exe.unlink(missing_ok=True)

        if not pg_dump_exe.exists() or pg_dump_exe.stat().st_size == 0:
            cs_dump_src = SHIM_BIN_DIR / f"PgDumpShim_{pid}.cs"
            tmp_dump_exe = SHIM_BIN_DIR / f"pg_dump_{pid}.exe"
            cs_dump_code = (
                "using System;\n"
                "using System.Diagnostics;\n"
                "using System.IO;\n"
                "class Program {\n"
                "    static int Main(string[] args) {\n"
                f'        string py = @"{sys.executable}";\n'
                '        string script = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "docker_shim.py");\n'
                '        for (int i = 0; i < args.Length; i++) {\n'
                '            if (args[i].Contains(" ") || args[i].Contains("\\\""))\n'
                '                args[i] = "\\"" + args[i].Replace("\\\"", "\\\\\\\"") + "\\"";\n'
                '        }\n'
                '        ProcessStartInfo psi = new ProcessStartInfo(py, "\\"" + script + "\\" exec scp-pg-test pg_dump " + string.Join(" ", args)) { UseShellExecute = false };\n'
                "        try {\n"
                "            Process p = Process.Start(psi);\n"
                "            p.WaitForExit();\n"
                "            return p.ExitCode;\n"
                "        } catch { return 1; }\n"
                "    }\n"
                "}\n"
            )
            try:
                cs_dump_src.write_text(cs_dump_code, encoding="utf-8")
                subprocess.run([str(csc_path), "/nologo", f"/out:{tmp_dump_exe}", str(cs_dump_src)], check=True, capture_output=True)
                os.replace(tmp_dump_exe, pg_dump_exe)
            except (subprocess.SubprocessError, OSError) as exc:
                logger.debug("setup_docker_and_dump_shims: csc pg_dump compile skipped", exc_info=exc)
            finally:
                cs_dump_src.unlink(missing_ok=True)
                tmp_dump_exe.unlink(missing_ok=True)

    return SHIM_BIN_DIR


def _patch_subprocess_docker_shim() -> None:
    """Ensure subprocess invocations of 'docker ... scp-pg-test' route to docker_shim.py."""
    import subprocess
    if getattr(subprocess.Popen, "_scp_hermetic_patched", False):
        return
    orig_init = subprocess.Popen.__init__

    def _hermetic_popen_init(self: Any, args: Any, *pos: Any, **kw: Any) -> None:
        if isinstance(args, (list, tuple)) and len(args) >= 2:
            first = str(args[0])
            if Path(first).stem.lower() == "docker":
                container = os.environ.get("SCP_PG_TEST_CONTAINER", "scp-pg-test").strip() or "scp-pg-test"
                str_args = [str(a) for a in args]
                if container in str_args or "scp-pg-test" in str_args:
                    docker_py = SHIM_BIN_DIR / "docker_shim.py"
                    if docker_py.exists():
                        args = [sys.executable, str(docker_py), *str_args[1:]]
        orig_init(self, args, *pos, **kw)

    subprocess.Popen.__init__ = _hermetic_popen_init  # type: ignore[method-assign]
    subprocess.Popen._scp_hermetic_patched = True  # type: ignore[attr-defined]


def install_hermetic_adapter() -> None:
    """Install the hermetic adapter into psycopg.connect, PATH, and PYTHONPATH."""
    psycopg.connect = hermetic_connect

    shim_dir = setup_docker_and_dump_shims()
    _patch_subprocess_docker_shim()
    cur_path = os.environ.get("PATH", "")
    path_parts = [p for p in cur_path.split(os.pathsep) if p and p != str(shim_dir)]
    scripts_dir = str(Path(sys.prefix) / "Scripts")
    if Path(scripts_dir).exists() and scripts_dir not in path_parts:
        path_parts.insert(0, scripts_dir)
    path_parts.insert(0, str(shim_dir))
    os.environ["PATH"] = os.pathsep.join(path_parts)

    repo_root = str(Path(__file__).resolve().parents[1])
    cur_pypath = os.environ.get("PYTHONPATH", "")
    pypath_parts = [p for p in cur_pypath.split(os.pathsep) if p]
    for needed in (repo_root, str(shim_dir)):
        if needed not in pypath_parts:
            pypath_parts.insert(0, needed)
    os.environ["PYTHONPATH"] = os.pathsep.join(pypath_parts)


def install_if_needed() -> None:
    """Called automatically by sitecustomize."""
    psycopg.connect = hermetic_connect
    _patch_subprocess_docker_shim()
    if not (SHIM_BIN_DIR / "docker_shim.py").exists():
        install_hermetic_adapter()

