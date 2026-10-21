"""T13 — Runtime reality audit.

These tests are adversarial by construction: every one of them builds a
broken reality and asserts that the probe actually notices. A test that
only ever asserts "PASS on a healthy fixture" would be a placebo, so each
case here also proves the negative path (DNA #19, DNA #25, FA-01, FA-04).

What is covered:
  * SqliteRealityProbe   — physical integrity of live databases
  * JournalAnchorProbe   — per-task chains AND the global control anchor
  * ServiceLivenessProbe — 401 means alive-and-protected, 5xx means broken
  * LedgerYieldProbe     — a loop that finds nothing is not healthy
  * ClaimRealityCrossCheck — the system claiming "ok" while it is broken
  * Verdict derivation   — fail-closed, never optimistic
  * RuntimeAuditLedger   — durable, queryable audit history
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

os.environ.setdefault("SCP_CAPABILITY_SECRET", "t13-runtime-audit-secret-1234567890")

from scp.runtime_audit import (  # noqa: E402
    ClaimRealityCrossCheck,
    JournalAnchorProbe,
    LedgerYieldProbe,
    RuntimeAuditLedger,
    ServiceLivenessProbe,
    Severity,
    SqliteRealityProbe,
    Verdict,
    derive_verdict,
)
from scp.runtime_audit.models import ProbeResult, RuntimeAuditVerdict  # noqa: E402


# ─── Fixtures ───────────────────────────────────────────────────────────────

def _make_kernel_db(path: Path, *, task_ids: int = 2, events_per_task: int = 3) -> str:
    """Create a minimal Task Kernel-shaped database with a valid chain."""
    secret = os.environ["SCP_CAPABILITY_SECRET"]
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE control (
            id INTEGER PRIMARY KEY CHECK (id=1),
            global_kill INTEGER NOT NULL DEFAULT 0,
            global_kill_epoch INTEGER NOT NULL DEFAULT 0,
            journal_head_hash TEXT,
            event_count INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE tasks (
            task_id TEXT PRIMARY KEY, state TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE events (
            event_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, seq INTEGER NOT NULL,
            type TEXT NOT NULL, from_state TEXT, to_state TEXT, actor TEXT NOT NULL,
            reason TEXT NOT NULL, payload_json TEXT NOT NULL, policy_hash TEXT,
            prev_event_hash TEXT, event_hash TEXT NOT NULL, created_at TEXT NOT NULL,
            UNIQUE(task_id, seq)
        );
        """
    )
    conn.execute("INSERT INTO control(id) VALUES (1)")

    total = 0
    head = None
    for t in range(task_ids):
        task_id = f"task-{t:03d}"
        prev = None
        for seq in range(1, events_per_task + 1):
            body = {
                "event_id": f"evt-{t:03d}-{seq:03d}",
                "task_id": task_id,
                "seq": seq,
                "type": "STATE_TRANSITION",
                "from_state": "CREATED",
                "to_state": "PLANNING",
                "actor": "tester",
                "reason": "fixture",
                "payload": {},
                "policy_hash": None,
                "prev_event_hash": prev,
            }
            digest = hmac.new(
                secret.encode(), json.dumps(body, sort_keys=True).encode(), hashlib.sha256
            ).hexdigest()
            conn.execute(
                "INSERT INTO events(event_id, task_id, seq, type, from_state, to_state, actor, "
                "reason, payload_json, policy_hash, prev_event_hash, event_hash, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    body["event_id"], task_id, seq, body["type"], body["from_state"],
                    body["to_state"], body["actor"], body["reason"], "{}", None, prev, digest,
                    "2026-01-01T00:00:00+00:00",
                ),
            )
            prev = digest
            total += 1
        head = prev
    conn.execute("UPDATE control SET event_count=?, journal_head_hash=? WHERE id=1", (total, head))
    conn.commit()
    conn.close()
    return secret


def _corrupt_file(path: Path) -> None:
    """Replace a database file with bytes that are not a SQLite database."""
    path.write_bytes(bytes(range(256)) * 40)


def _healthy_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE t(a INTEGER)")
    conn.execute("INSERT INTO t VALUES (1)")
    conn.commit()
    conn.close()


# ─── SqliteRealityProbe ─────────────────────────────────────────────────────

def test_sqlite_probe_passes_on_healthy_database(tmp_path):
    """A healthy database must be reported healthy (positive control)."""
    _healthy_db(tmp_path / "healthy.sqlite3")
    results = SqliteRealityProbe(tmp_path).run()
    assert len(results) == 1
    assert results[0].severity == Severity.PASS
    assert results[0].details["database_count"] == 1


def test_sqlite_probe_fails_closed_on_corrupt_database(tmp_path):
    """A physically unreadable database must FAIL, never PASS (anti-placebo)."""
    _corrupt_file(tmp_path / "broken.sqlite3")
    results = SqliteRealityProbe(tmp_path).run()
    assert len(results) == 1
    assert results[0].severity == Severity.FAIL
    assert "broken.sqlite3" in results[0].details["corrupt"]
    assert results[0].details["corrupt"] or results[0].details["unreadable"]


def test_sqlite_probe_reports_unknown_when_data_dir_missing(tmp_path):
    """An unobservable scope is UNKNOWN, not PASS (fail-closed)."""
    results = SqliteRealityProbe(tmp_path / "does-not-exist").run()
    assert len(results) == 1
    assert results[0].severity == Severity.UNKNOWN


def test_sqlite_probe_does_not_write_to_audited_database(tmp_path):
    """The probe must be observation-only: the file must be byte-identical."""
    db_path = tmp_path / "observed.sqlite3"
    _healthy_db(db_path)
    before = db_path.read_bytes()
    SqliteRealityProbe(tmp_path).run()
    assert db_path.read_bytes() == before


def test_sqlite_probe_excludes_its_own_audit_ledger(tmp_path):
    """The audit must not grade its own homework."""
    RuntimeAuditLedger(tmp_path).record(
        RuntimeAuditVerdict(
            verdict=Verdict.PASS,
            findings=[],
            target="t",
            data_dir=str(tmp_path),
            started_at="2026-01-01T00:00:00+00:00",
            completed_at="2026-01-01T00:00:01+00:00",
            elapsed_sec=1.0,
        )
    )
    results = SqliteRealityProbe(tmp_path).run()
    assert results[0].details["database_count"] == 0
    assert results[0].severity == Severity.UNKNOWN


# ─── JournalAnchorProbe ─────────────────────────────────────────────────────

def test_journal_anchor_passes_on_consistent_kernel(tmp_path):
    """A kernel whose counter, head hash and chains agree must PASS."""
    db_path = tmp_path / "kernel.sqlite3"
    _make_kernel_db(db_path)
    results = JournalAnchorProbe([db_path], capability_secret=os.environ["SCP_CAPABILITY_SECRET"]).run()
    assert len(results) == 1
    assert results[0].severity == Severity.PASS
    assert results[0].details["anchor_divergent"] == []
    assert results[0].details["hmac_failed"] == []


def test_journal_anchor_detects_event_count_divergence(tmp_path):
    """THE BLIND SPOT: a diverged global counter must be detected.

    `TaskKernel.verify_journal()` never cross-checks `control.event_count`
    against the physical row count. This probe must catch it.
    """
    db_path = tmp_path / "kernel.sqlite3"
    _make_kernel_db(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.execute("UPDATE control SET event_count = event_count - 2 WHERE id = 1")
    conn.commit()
    conn.close()

    results = JournalAnchorProbe([db_path], capability_secret=os.environ["SCP_CAPABILITY_SECRET"]).run()
    assert results[0].severity == Severity.FAIL
    assert "kernel.sqlite3" in results[0].details["anchor_divergent"]
    observation = results[0].evidence["observations"][0]
    assert observation["event_count_delta"] == 2


def test_journal_anchor_detects_head_hash_mismatch(tmp_path):
    """A head hash that points at no physical event must FAIL."""
    db_path = tmp_path / "kernel.sqlite3"
    _make_kernel_db(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.execute("UPDATE control SET journal_head_hash = ? WHERE id = 1", ("deadbeef" * 8,))
    conn.commit()
    conn.close()

    results = JournalAnchorProbe([db_path], capability_secret=os.environ["SCP_CAPABILITY_SECRET"]).run()
    assert results[0].severity == Severity.FAIL
    assert "kernel.sqlite3" in results[0].details["head_mismatched"]


def test_journal_anchor_detects_deleted_event_in_chain(tmp_path):
    """Deleting a middle event breaks the per-task hash chain."""
    db_path = tmp_path / "kernel.sqlite3"
    _make_kernel_db(db_path, task_ids=1, events_per_task=5)
    conn = sqlite3.connect(str(db_path))
    conn.execute("DELETE FROM events WHERE seq = 3")
    conn.commit()
    conn.close()

    results = JournalAnchorProbe([db_path], capability_secret=os.environ["SCP_CAPABILITY_SECRET"]).run()
    assert results[0].severity == Severity.FAIL
    assert "kernel.sqlite3" in results[0].details["chain_broken"]


def test_journal_anchor_detects_hmac_mismatch(tmp_path):
    """An event whose signature does not verify must FAIL."""
    db_path = tmp_path / "kernel.sqlite3"
    _make_kernel_db(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.execute("UPDATE events SET event_hash = ? WHERE seq = 1", ("0" * 64,))
    conn.commit()
    conn.close()

    results = JournalAnchorProbe([db_path], capability_secret=os.environ["SCP_CAPABILITY_SECRET"]).run()
    assert results[0].severity == Severity.FAIL
    assert "kernel.sqlite3" in results[0].details["hmac_failed"]


def test_journal_anchor_reports_unknown_for_missing_control_schema(tmp_path):
    """A schema variant is UNKNOWN, not FAIL and not PASS."""
    db_path = tmp_path / "kernel.sqlite3"
    _make_kernel_db(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.execute("DROP TABLE control")
    conn.execute(
        "CREATE TABLE control (id INTEGER PRIMARY KEY, global_kill INTEGER NOT NULL DEFAULT 0)"
    )
    conn.commit()
    conn.close()

    results = JournalAnchorProbe([db_path], capability_secret=os.environ["SCP_CAPABILITY_SECRET"]).run()
    assert results[0].severity == Severity.UNKNOWN
    assert results[0].details["inconclusive"]


def test_journal_anchor_ignores_backup_databases(tmp_path):
    """A corrupt historical backup is inventoried but never graded."""
    backup_dir = tmp_path / "kernel-backups"
    backup_dir.mkdir()
    db_path = backup_dir / "old.sqlite3"
    _make_kernel_db(db_path)
    _corrupt_file(db_path)

    results = JournalAnchorProbe([db_path], capability_secret=os.environ["SCP_CAPABILITY_SECRET"]).run()
    assert results[0].severity == Severity.PASS
    assert results[0].details["unreadable"] == []


# ─── ServiceLivenessProbe ───────────────────────────────────────────────────

class _Handler(BaseHTTPRequestHandler):
    status_code = 200
    payload = b'{"status": "ok"}'

    def do_GET(self):  # noqa: N802 - http.server API
        self.send_response(self.status_code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(self.payload)

    def log_message(self, *_args):  # silence the test server
        return


@pytest.fixture()
def http_server():
    """Start a local HTTP server whose status code the test controls."""
    servers = []

    def _start(status: int, payload: bytes = b'{"status": "ok"}'):
        handler = type("H", (_Handler,), {"status_code": status, "payload": payload})
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        servers.append(server)
        return f"http://127.0.0.1:{server.server_address[1]}"

    yield _start

    for server in servers:
        server.shutdown()
        server.server_close()


def test_liveness_probe_passes_on_200(http_server):
    """A healthy endpoint must PASS (positive control)."""
    url = http_server(200)
    results = ServiceLivenessProbe([{"name": "ok", "url": url}]).run()
    assert results[0].severity == Severity.PASS
    assert results[0].details["healthy"] == ["ok"]


def test_liveness_probe_treats_401_as_protected_not_broken(http_server):
    """401 proves the process is alive AND the guard is enforcing auth.

    It must not be reported as a liveness failure (the endpoint answered)
    and must not be silently treated as healthy either.
    """
    url = http_server(401, b'{"detail": "Unauthorized"}')
    results = ServiceLivenessProbe([{"name": "guarded", "url": url}]).run()
    assert results[0].severity == Severity.PASS
    assert results[0].details["protected"] == ["guarded"]
    assert results[0].details["server_error"] == []
    assert results[0].details["unreachable"] == []


def test_liveness_probe_fails_on_500(http_server):
    """A 5xx endpoint is broken and must FAIL (anti-placebo)."""
    url = http_server(500, b'{"detail": "boom"}')
    results = ServiceLivenessProbe([{"name": "broken", "url": url}]).run()
    assert results[0].severity == Severity.FAIL
    assert results[0].details["server_error"] == ["broken"]


def test_liveness_probe_fails_on_unreachable_endpoint():
    """A closed port must FAIL, not be reported as healthy."""
    results = ServiceLivenessProbe(
        [{"name": "dead", "url": "http://127.0.0.1:1/health"}], timeout_sec=2.0
    ).run()
    assert results[0].severity == Severity.FAIL
    assert results[0].details["unreachable"] == ["dead"]


def test_liveness_probe_is_unknown_without_targets():
    """No targets means nothing observed -> UNKNOWN, never PASS."""
    results = ServiceLivenessProbe([]).run()
    assert results[0].severity == Severity.UNKNOWN


# ─── LedgerYieldProbe ───────────────────────────────────────────────────────

def test_ledger_probe_flags_zero_yield_loop(tmp_path):
    """A loop that always reports ok and never finds anything is NOT healthy."""
    with (tmp_path / "loop_runs.jsonl").open("w", encoding="utf-8") as handle:
        for _ in range(50):
            handle.write(json.dumps({"status": "ok", "findings_count": 0}) + "\n")
    results = LedgerYieldProbe(tmp_path).run()
    assert results[0].severity == Severity.FAIL
    assert "produced findings" in results[0].summary


def test_ledger_probe_flags_overwhelming_skip_rate(tmp_path):
    """A deep audit that skips everything is not doing its job."""
    with (tmp_path / "deep_audit_results.jsonl").open("w", encoding="utf-8") as handle:
        for i in range(40):
            handle.write(
                json.dumps({"result": {"action": "skipped", "reason": f"r{i}"}}) + "\n"
            )
    results = LedgerYieldProbe(tmp_path).run()
    assert results[0].severity == Severity.FAIL
    assert "skipped" in results[0].summary


def test_ledger_probe_passes_when_loop_produces_findings(tmp_path):
    """A loop with real yield must PASS (positive control)."""
    with (tmp_path / "loop_runs.jsonl").open("w", encoding="utf-8") as handle:
        for i in range(50):
            handle.write(json.dumps({"status": "ok", "findings_count": i % 5}) + "\n")
    with (tmp_path / "deep_audit_results.jsonl").open("w", encoding="utf-8") as handle:
        for i in range(10):
            handle.write(json.dumps({"result": {"action": "fixed"}}) + "\n")
    results = LedgerYieldProbe(tmp_path).run()
    assert results[0].severity == Severity.PASS


def test_ledger_probe_is_unknown_without_ledgers(tmp_path):
    """Absent ledgers mean unobservable, not healthy."""
    results = LedgerYieldProbe(tmp_path).run()
    assert results[0].severity == Severity.UNKNOWN


# ─── ClaimRealityCrossCheck ─────────────────────────────────────────────────

def test_cross_check_detects_healthy_claim_over_corrupt_storage():
    """The core contradiction: /health says ok, storage says corrupt."""
    claims = {"backend_health": {"status": 200, "json": {"status": "ok"}}}
    physical = {"corrupt_databases": ["subsystem_heartbeat.sqlite"]}
    results = ClaimRealityCrossCheck(claims=claims, physical=physical).run()
    assert results[0].severity == Severity.FAIL
    assert results[0].details["contradictions"]


def test_cross_check_detects_empty_audit_claims_over_populated_ledger():
    """'total_findings=0' while the ledger holds records is a contradiction."""
    claims = {"backend_audit_stats": {"status": 200, "json": {"total_findings": 0}}}
    physical = {"deep_audit_records": 2812}
    results = ClaimRealityCrossCheck(claims=claims, physical=physical).run()
    assert results[0].severity == Severity.FAIL


def test_cross_check_detects_broken_subsystem_surface():
    """A 5xx on the subsystem brain view is a contradiction."""
    claims = {"backend_runtime_subsystems": {"status": 500}}
    results = ClaimRealityCrossCheck(claims=claims, physical={}).run()
    assert results[0].severity == Severity.FAIL


def test_cross_check_passes_when_claims_agree():
    """Consistent claims must PASS (positive control)."""
    claims = {
        "backend_health": {"status": 200, "json": {"status": "ok"}},
        "backend_audit_stats": {"status": 200, "json": {"total_findings": 3}},
    }
    physical = {"corrupt_databases": [], "deep_audit_records": 2812}
    results = ClaimRealityCrossCheck(claims=claims, physical=physical).run()
    assert results[0].severity == Severity.PASS
    assert results[0].details["contradictions"] == []


def test_cross_check_is_unknown_without_claims():
    """Nothing to cross-check -> UNKNOWN."""
    results = ClaimRealityCrossCheck(claims={}, physical={}).run()
    assert results[0].severity == Severity.UNKNOWN


# ─── Verdict derivation ─────────────────────────────────────────────────────

def test_derive_verdict_is_fail_closed_on_empty_scope():
    """No observation at all must never be PASS."""
    assert derive_verdict([]) == Verdict.UNKNOWN


def test_derive_verdict_fail_dominates_unknown_and_warn():
    """A single failure must dominate the whole verdict."""
    findings = [
        ProbeResult("a", Severity.PASS, "a", "a"),
        ProbeResult("b", Severity.WARN, "b", "b"),
        ProbeResult("c", Severity.UNKNOWN, "c", "c"),
        ProbeResult("d", Severity.FAIL, "d", "d"),
    ]
    assert derive_verdict(findings) == Verdict.FAIL


def test_derive_verdict_unknown_dominates_warn():
    """Unobservable scope outranks a warning."""
    findings = [
        ProbeResult("a", Severity.PASS, "a", "a"),
        ProbeResult("b", Severity.WARN, "b", "b"),
        ProbeResult("c", Severity.UNKNOWN, "c", "c"),
    ]
    assert derive_verdict(findings) == Verdict.UNKNOWN


def test_derive_verdict_pass_only_when_all_pass():
    """PASS requires every observation to be a PASS."""
    findings = [ProbeResult("a", Severity.PASS, "a", "a")]
    assert derive_verdict(findings) == Verdict.PASS


# ─── RuntimeAuditLedger ─────────────────────────────────────────────────────

def test_ledger_records_and_returns_history(tmp_path):
    """The verdict must be durable and queryable."""
    ledger = RuntimeAuditLedger(tmp_path)
    verdict = RuntimeAuditVerdict(
        verdict=Verdict.FAIL,
        findings=[ProbeResult("p1", Severity.FAIL, "broken", "it is broken")],
        target="t",
        data_dir=str(tmp_path),
        started_at="2026-01-01T00:00:00+00:00",
        completed_at="2026-01-01T00:00:01+00:00",
        elapsed_sec=1.0,
    )
    run_id = ledger.record(verdict)
    assert run_id.startswith("rt-")
    history = ledger.history(limit=10)
    assert len(history) == 1
    assert history[0]["verdict"] == "FAIL"
    assert history[0]["finding_count"] == 1


def test_ledger_latest_round_trips_the_full_verdict(tmp_path):
    """The projection must be able to read back what the authority wrote."""
    ledger = RuntimeAuditLedger(tmp_path)
    verdict = RuntimeAuditVerdict(
        verdict=Verdict.FAIL,
        findings=[ProbeResult("p1", Severity.FAIL, "broken", "it is broken")],
        target="t",
        data_dir=str(tmp_path),
        started_at="2026-01-01T00:00:00+00:00",
        completed_at="2026-01-01T00:00:01+00:00",
        elapsed_sec=1.0,
    )
    ledger.record(verdict)
    latest = ledger.latest()
    assert latest is not None
    assert latest["verdict"] == "FAIL"
    assert latest["findings"][0]["probe_id"] == "p1"


def test_ledger_history_is_empty_before_any_run(tmp_path):
    """An empty history is empty, not a fabricated PASS."""
    assert RuntimeAuditLedger(tmp_path).history() == []
    assert RuntimeAuditLedger(tmp_path).latest() is None
