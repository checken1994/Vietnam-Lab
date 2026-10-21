"""T13 — Brain console contract.

Two layers of evidence, both real:

1. STATIC contract: the shipped source must keep the guarantees the service
   advertises (zero npm dependencies, read-only SQLite access, every
   documented route actually implemented, no secret values in the source).
2. LIVE contract: a real `node` process is started against a fixture data
   directory that contains deliberately broken realities, and the projection
   must REPORT them — not hide them.

The negative cases matter most: a projection that cannot see a corrupt
database or a diverged journal anchor is worse than no projection at all,
because it looks authoritative.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICE_DIR = REPO_ROOT / "mini-services" / "brain-console"


# ─── Helpers ────────────────────────────────────────────────────────────────

def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _node_bin() -> str:
    node_bin = shutil.which("node")
    assert node_bin is not None, "Node.js must be installed and in PATH"
    return node_bin


def _get(url: str, timeout: float = 20.0):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return int(response.status), response.read().decode("utf-8", errors="replace")


def _make_kernel_db(path: Path, *, events: int = 4, anchor_override: int | None = None) -> None:
    """Create a Task Kernel-shaped database whose control anchor we control."""
    secret = "t13-brain-console-secret-1234567890"
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE control (
            id INTEGER PRIMARY KEY CHECK (id=1), journal_head_hash TEXT,
            event_count INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE tasks (
            task_id TEXT PRIMARY KEY, state TEXT NOT NULL, risk_tier TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL, error TEXT, version INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE events (
            event_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, seq INTEGER NOT NULL,
            type TEXT NOT NULL, from_state TEXT, to_state TEXT, actor TEXT NOT NULL,
            reason TEXT NOT NULL, payload_json TEXT NOT NULL, policy_hash TEXT,
            prev_event_hash TEXT, event_hash TEXT NOT NULL, created_at TEXT NOT NULL
        );
        """
    )
    conn.execute("INSERT INTO control(id) VALUES (1)")
    conn.execute(
        "INSERT INTO tasks VALUES ('task-1','RUNNING','R1',0,'2026-01-01','2026-01-01',NULL,1)"
    )
    prev = None
    for seq in range(1, events + 1):
        body = {
            "event_id": f"evt-{seq}", "task_id": "task-1", "seq": seq,
            "type": "STATE_TRANSITION", "from_state": "CREATED", "to_state": "RUNNING",
            "actor": "tester", "reason": "fixture", "payload": {},
            "policy_hash": None, "prev_event_hash": prev,
        }
        digest = hmac.new(
            secret.encode(), json.dumps(body, sort_keys=True).encode(), hashlib.sha256
        ).hexdigest()
        conn.execute(
            "INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"evt-{seq}", "task-1", seq, "STATE_TRANSITION", "CREATED", "RUNNING",
             "tester", "fixture", "{}", None, prev, digest, "2026-01-01T00:00:00+00:00"),
        )
        prev = digest
    count = anchor_override if anchor_override is not None else events
    conn.execute("UPDATE control SET event_count=?, journal_head_hash=? WHERE id=1", (count, prev))
    conn.commit()
    conn.close()


def _corrupt_file(path: Path) -> None:
    path.write_bytes(bytes(range(256)) * 40)


@pytest.fixture()
def fixture_data_dir(tmp_path):
    """A data directory containing one healthy DB, one corrupt DB, one
    kernel DB with a diverged anchor, and a zero-yield loop ledger."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    healthy = data_dir / "healthy.sqlite3"
    conn = sqlite3.connect(str(healthy))
    conn.execute("CREATE TABLE notes(a TEXT)")
    conn.execute("INSERT INTO notes VALUES ('ok')")
    conn.commit()
    conn.close()

    _corrupt_file(data_dir / "broken.sqlite3")
    _make_kernel_db(data_dir / "kernel.sqlite3", events=4, anchor_override=1)

    with (data_dir / "loop_runs.jsonl").open("w", encoding="utf-8") as handle:
        for _ in range(30):
            handle.write(json.dumps({"status": "ok", "findings_count": 0}) + "\n")

    return data_dir


@pytest.fixture()
def live_console(fixture_data_dir):
    """Start a real brain-console process against the fixture data dir."""
    node_bin = _node_bin()
    port = _free_port()
    env = dict(os.environ)
    env.update({
        "SCP_BRAIN_CONSOLE_PORT": str(port),
        "SCP_BRAIN_CONSOLE_HOST": "127.0.0.1",
        "SCP_DATA_DIR": str(fixture_data_dir),
        # Point at an unroutable backend so the service probes fail fast
        # instead of depending on a live SCP deployment.
        "SCP_BASE_URL": "http://127.0.0.1:1",
        "LOOP_SCHEDULER_URL": "http://127.0.0.1:1",
        "AGENT_DESKTOP_URL": "http://127.0.0.1:1",
        "SCP_BRAIN_HTTP_TIMEOUT_MS": "1500",
    })
    process = subprocess.Popen(
        [node_bin, "index.js"],
        cwd=str(SERVICE_DIR),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 30
    ready = False
    while time.time() < deadline:
        if process.poll() is not None:
            output = process.stdout.read() if process.stdout else ""
            raise AssertionError(f"brain-console exited early:\n{output}")
        try:
            status, _ = _get(f"{base}/healthz", timeout=2.0)
            if status == 200:
                ready = True
                break
        except (urllib.error.URLError, OSError):
            time.sleep(0.2)
    assert ready, "brain-console did not become healthy within the timeout"
    try:
        yield base
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()


# ─── Static contract ────────────────────────────────────────────────────────

def test_service_ships_zero_npm_dependencies():
    """Zero-dependency discipline must not regress."""
    package = json.loads((SERVICE_DIR / "package.json").read_text(encoding="utf-8"))
    assert package.get("dependencies") == {}
    assert package.get("devDependencies") == {}
    assert package.get("type") == "module"
    assert package["scripts"]["start"] == "node index.js"


def test_service_requires_node_with_builtin_sqlite():
    """node:sqlite is only unflagged from Node 23.4 onward."""
    package = json.loads((SERVICE_DIR / "package.json").read_text(encoding="utf-8"))
    assert package["engines"]["node"] >= "22.5.0"


def test_source_never_opens_a_database_for_writing():
    """Every DatabaseSync construction must be read-only."""
    source = (SERVICE_DIR / "core.js").read_text(encoding="utf-8")
    assert "new DatabaseSync(" in source
    assert source.count("new DatabaseSync(") == source.count("{ readOnly: true }")
    assert "readOnly: false" not in source
    assert "readOnly: true" in source


def test_source_contains_no_hardcoded_secret_values():
    """No credential may be baked into the shipped source."""
    # The markers are assembled at runtime so that this test file itself
    # does not contain a credential-shaped literal.
    markers = (
        "scp-dev-" + "capability-secret",
        "sk-or-" + "v1-",
        "gsk" + "_",
        "Bearer scp-" + "v3-",
    )
    for name in ("index.js", "core.js", "view.js"):
        text = (SERVICE_DIR / name).read_text(encoding="utf-8")
        for marker in markers:
            assert marker not in text, f"{name} must not contain a literal credential"


def test_every_documented_route_is_implemented():
    """The README's endpoint table must match the implementation."""
    index_source = (SERVICE_DIR / "index.js").read_text(encoding="utf-8")
    readme = (SERVICE_DIR / "README.md").read_text(encoding="utf-8")
    for route in ("/healthz", "/ready", "/api/brain", "/api/kernel", "/api/journal",
                  "/api/storage", "/api/services", "/api/loops", "/api/skills",
                  "/api/audit", "/api/stream"):
        assert route in readme, f"{route} is documented but missing from the README table"
        assert route in index_source, f"{route} is documented but not implemented"


def test_service_has_no_mutating_routes():
    """The console is read-only: it must reject write methods."""
    index_source = (SERVICE_DIR / "index.js").read_text(encoding="utf-8")
    assert 'method !== "GET" && method !== "HEAD"' in index_source


# ─── Live contract ──────────────────────────────────────────────────────────

def test_live_healthz_identifies_the_service(live_console):
    """The health probe must name the service."""
    status, body = _get(f"{live_console}/healthz")
    assert status == 200
    payload = json.loads(body)
    assert payload["status"] == "ok"
    assert payload["service"] == "scp-brain-console"


def test_live_index_serves_the_brain_dashboard(live_console):
    """The dashboard must be served without a build step."""
    status, body = _get(f"{live_console}/")
    assert status == 200
    assert "SCP Brain Console" in body
    assert "/api/brain" in body


def test_live_brain_projection_reports_reality(live_console):
    """The headline contract: broken realities must be reported, not hidden."""
    status, body = _get(f"{live_console}/api/brain")
    assert status == 200
    snapshot = json.loads(body)
    assert snapshot["reality"]["severity"] == "FAIL"

    corrupt_paths = [entry["path"] for entry in snapshot["reality"]["corruptDatabases"]]
    assert any("broken.sqlite3" in path for path in corrupt_paths), \
        f"corrupt database was not reported: {corrupt_paths}"

    divergent = snapshot["reality"]["anchorDivergentDatabases"]
    assert any("kernel.sqlite3" in entry["path"] for entry in divergent), \
        f"journal anchor divergence was not reported: {divergent}"


def test_live_projection_classifies_a_non_database_as_corrupt(live_console):
    """A file that is not a SQLite database at all is physically corrupt."""
    status, body = _get(f"{live_console}/api/brain")
    assert status == 200
    snapshot = json.loads(body)
    reality = snapshot["reality"]

    corrupt_paths = [entry["path"] for entry in reality["corruptDatabases"]]
    unreadable_paths = [entry["path"] for entry in reality.get("unreadableDatabases", [])]
    assert any("broken.sqlite3" in path for path in corrupt_paths), \
        f"non-database file was not reported as corrupt: {corrupt_paths}"
    assert not any("broken.sqlite3" in path for path in unreadable_paths), \
        "a non-database file is corruption, not an access failure"
    for entry in reality["corruptDatabases"]:
        assert entry["integrity"], "a corrupt entry must carry the integrity verdict"


def test_classification_separates_access_failure_from_corruption():
    """The rule itself, exercised in the implementation that applies it.

    A database that cannot be opened because the filesystem cannot satisfy
    SQLite's locking is NOT corrupt. Blaming the data would be a false
    accusation — and on this deployment that mistake would have hidden the
    real root cause entirely.
    """
    node_bin = _node_bin()
    script = (
        "import { classifyIntegrityFailure } from './core.js';"
        "const cases = ["
        "['unable to open database file','unreadable'],"
        "['disk I/O error','unreadable'],"
        "['database is locked','unreadable'],"
        "['database disk image is malformed','corrupt'],"
        "['file is not a database','corrupt'],"
        "['wrong # of entries in index idx','corrupt']"
        "];"
        "const out = cases.map(([m,e]) => [m, classifyIntegrityFailure(m), e]);"
        "process.stdout.write(JSON.stringify(out));"
    )
    result = subprocess.run(
        [node_bin, "--input-type=module", "-e", script],
        cwd=str(SERVICE_DIR),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, f"classification probe failed: {result.stderr}"
    observed = json.loads(result.stdout)
    for message, actual, expected in observed:
        assert actual == expected, f"{message!r} classified as {actual!r}, expected {expected!r}"


def test_live_brain_projection_reports_kernel_occupancy(live_console):
    """The 17-state machine must be projected, not summarised away."""
    status, body = _get(f"{live_console}/api/kernel")
    assert status == 200
    payload = json.loads(body)
    kernels = payload["kernels"]
    assert len(kernels) >= 1
    kernel = next(k for k in kernels if "kernel.sqlite3" in k["path"])
    assert kernel["observable"] is True
    assert kernel["taskCount"] == 1
    assert len(kernel["states"]) == 17
    assert kernel["occupancy"]["RUNNING"] == 1


def test_live_journal_projection_exposes_the_anchor_delta(live_console):
    """The anchor delta must be visible, not just the chain verdict."""
    status, body = _get(f"{live_console}/api/journal")
    assert status == 200
    payload = json.loads(body)
    journal = next(j for j in payload["journals"] if "kernel.sqlite3" in j["path"])
    assert journal["observable"] is True
    assert journal["eventCount"] == 4
    assert journal["anchorEventCount"] == 1
    assert journal["anchorDelta"] == 3
    assert journal["chainBrokenCount"] == 0


def test_live_storage_projection_lists_every_database(live_console):
    """Storage census must include both the healthy and the corrupt file."""
    status, body = _get(f"{live_console}/api/storage")
    assert status == 200
    payload = json.loads(body)
    names = [entry["name"] for entry in payload["databases"]]
    assert "healthy.sqlite3" in names
    assert "broken.sqlite3" in names
    broken = next(e for e in payload["databases"] if e["name"] == "broken.sqlite3")
    assert broken["integrityOk"] is False


def test_live_loops_projection_exposes_zero_yield(live_console):
    """Loop effectiveness must be projected, including the zero-yield case."""
    status, body = _get(f"{live_console}/api/loops")
    assert status == 200
    ledgers = json.loads(body)["ledgers"]
    assert ledgers["loop"]["totalRuns"] == 30
    assert ledgers["loop"]["runsWithNonzeroFindings"] == 0
    assert ledgers["loop"]["zeroFindingRatio"] == 1.0


def test_live_services_projection_reports_unreachable_backend(live_console):
    """An unreachable backend must be reported as unreachable, not healthy."""
    status, body = _get(f"{live_console}/api/services")
    assert status == 200
    services = json.loads(body)["services"]
    backend = next(s for s in services if s["name"] == "backend-health")
    assert backend["reachable"] is False
    assert backend["status"] is None


def test_live_projection_does_not_mutate_the_audited_databases(live_console, fixture_data_dir):
    """Read-only proof: the projected files must be byte-identical afterwards."""
    before = {
        path.name: path.read_bytes()
        for path in sorted(fixture_data_dir.iterdir())
        if path.is_file()
    }
    _get(f"{live_console}/api/brain")
    _get(f"{live_console}/api/storage")
    after = {
        path.name: path.read_bytes()
        for path in sorted(fixture_data_dir.iterdir())
        if path.is_file()
    }
    assert before.keys() == after.keys()
    for name, digest in before.items():
        assert after[name] == digest, f"{name} was modified by the read-only projection"


def test_live_projection_does_not_create_files_in_the_data_dir(live_console, fixture_data_dir):
    """The projection must not drop any artefact into the audited directory."""
    before = {path.name for path in fixture_data_dir.iterdir()}
    _get(f"{live_console}/api/brain")
    after = {path.name for path in fixture_data_dir.iterdir()}
    assert after == before


def test_live_unknown_route_returns_404_json(live_console):
    """Unknown routes must fail closed with a structured error."""
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        _get(f"{live_console}/api/does-not-exist")
    assert excinfo.value.code == 404
    payload = json.loads(excinfo.value.read().decode("utf-8"))
    assert payload["error"] == "not found"


def test_live_write_method_is_rejected(live_console):
    """A mutating request must be refused: the console has no write surface."""
    request = urllib.request.Request(f"{live_console}/api/brain", method="POST", data=b"{}")
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        urllib.request.urlopen(request, timeout=10)
    assert excinfo.value.code == 405
