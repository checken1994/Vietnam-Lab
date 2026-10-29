"""Key C lane L6 — offline receipt auditor (GD2 acceptance condition, checker-keyc-merge directive 8).

keyc-1A §5 risk 6: an in-process attacker holding the symmetric K_journal can re-sign the floor and the
journal, so the real guarantee is an OFFLINE pass that re-verifies every COMPLETED with only the PUBLIC
verifier keyring. These tests drive real completions through L2a's commit gate (real TaskKernel on a
temp SQLite file, real throwaway Ed25519 keys, real HMAC receipts under a test-only secret), close the
kernel, then attack the closed DB file the way a writer with DB access would (plain sqlite3 UPDATEs) and
run the auditor on it. Nothing is stubbed.
"""
from __future__ import annotations

import base64
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import scp.security.receipt_authority as ra
from scp.core.verifier_receipt import VerifierReceipt, sign_verifier_receipt
from scp.task_kernel import TaskKernel

REPO = Path(__file__).resolve().parents[2]
TOOL = REPO / "tools" / "receipt_offline_auditor.py"
_HMAC_SECRET = "keyc-l6-test-only-verifier-hmac-secret-0123456789"
_WORKER = "keyc-l6-worker"
_KEY_C_ENV = (
    "SCP_VERIFIER_PUBLIC_KEYS", "SCP_VERIFIER_PUBLIC_KEYS_FILE", "SCP_VERIFIER_PRIVATE_KEY",
    "SCP_VERIFIER_PRIVATE_KEY_FILE", "SCP_RECEIPT_AUTHORITY_MODE", "SCP_RECEIPT_AUTHORITY_MODE_FILE",
)


@pytest.fixture
def auditor():
    # Import inside the fixture: each test fails on its own while the auditor does not exist.
    import scp.runtime_audit.receipt_auditor as module

    return module


@pytest.fixture
def key_c():
    return Ed25519PrivateKey.generate()


def _pub(key) -> bytes:
    return key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def _ring(*entries) -> str:
    return json.dumps({"keys": list(entries)})


def _entry(key, status: str = "active", not_after: float | None = None) -> dict:
    return ra.keyring_entry(_pub(key), status=status, not_after=not_after)


@pytest.fixture
def env(monkeypatch, key_c):
    for name in _KEY_C_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SCP_VERIFIER_SECRET", _HMAC_SECRET)
    monkeypatch.setenv("SCP_VERIFIER_PUBLIC_KEYS", _ring(_entry(key_c)))
    return monkeypatch


@pytest.fixture
def enforce(env):
    env.setenv("SCP_RECEIPT_AUTHORITY_MODE", "enforce")
    return env


@pytest.fixture
def db(tmp_path) -> Path:
    return tmp_path / "kernel" / "keyc_l6.sqlite3"


@pytest.fixture
def kernel(db):
    k = TaskKernel(db)
    try:
        yield k
    finally:
        k.close()


@pytest.fixture
def ring_file(tmp_path, key_c) -> Path:
    path = tmp_path / "public" / "verifier_keyring.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_ring(_entry(key_c)), encoding="utf-8")
    return path


def _sidecars(db: Path) -> list[str]:
    return sorted(p.name for p in db.parent.iterdir() if p.name != db.name)


def _close(k: TaskKernel, db: Path) -> None:
    k.close()
    assert _sidecars(db) == []  # a closed kernel DB is a plain file: the auditor's precondition


def _to_verifying(k: TaskKernel, task_id: str):
    k.create_task(task_id, "keyc-l6-owner", "keyc l6 goal")
    for state in ("PLANNING", "READY", "QUEUED"):
        k.transition(task_id, state, actor="keyc-l6-scheduler", reason="keyc_l6_setup")
    lease = k.claim(task_id, _WORKER, ttl_seconds=120)
    k.start(task_id, lease.lease_id)
    k.transition(task_id, "VERIFYING", actor=_WORKER, reason="keyc_l6_setup")
    return lease


def _mint(key, lease, **overrides) -> str:
    fields = {"task_id": lease.task_id, "attempt_id": lease.attempt_id, "lease_id": lease.lease_id,
              "fencing_token": lease.fencing_token, "evidence_ref": f"evidence://keyc-l6/{lease.task_id}",
              "profile": "journal_integrity"}
    fields.update(overrides)
    return ra.mint_vr2(key, **fields)


def _complete_key_c(k: TaskKernel, key, task_id: str, **mint_overrides) -> str:
    lease = _to_verifying(k, task_id)
    token = _mint(key, lease, **mint_overrides)
    assert k.commit_verification_result(task_id, lease.lease_id, token)["state"] == "COMPLETED"
    return token


def _complete_hmac(k: TaskKernel, task_id: str) -> None:
    lease = _to_verifying(k, task_id)
    receipt = sign_verifier_receipt(
        VerifierReceipt(task_id=task_id, verifier_id="hands-executor-self-verifier-v1", verdict="VERIFIED",
                        evidence_ref=f"evidence://self/{task_id}", issued_at=time.time()), _HMAC_SECRET)
    assert k.commit_verification_result(task_id, lease.lease_id, receipt)["state"] == "COMPLETED"


def _sql(db: Path, statement: str, params=()) -> None:
    """The attacker: a plain writer with DB file access (no kernel, no K_journal)."""
    conn = sqlite3.connect(db)
    try:
        conn.execute(statement, params)
        conn.commit()
    finally:
        conn.close()


def _rewrite_payload(db: Path, task_id: str, mutate) -> None:
    conn = sqlite3.connect(db)
    try:
        event_id, text = conn.execute(
            "SELECT event_id, payload_json FROM events WHERE task_id=? AND type='TASK_COMPLETED'", (task_id,)).fetchone()
        payload = json.loads(text)
        mutate(payload)
        conn.execute("UPDATE events SET payload_json=? WHERE event_id=?", (json.dumps(payload, sort_keys=True), event_id))
        conn.commit()
    finally:
        conn.close()


def _completed_payload(db: Path, task_id: str, column: str = "payload_json"):
    assert column in ("payload_json", "created_at")
    conn = sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro&immutable=1", uri=True)
    try:
        value = conn.execute(
            f"SELECT {column} FROM events WHERE task_id=? AND type='TASK_COMPLETED'", (task_id,)).fetchone()[0]
    finally:
        conn.close()
    return json.loads(value) if column == "payload_json" else value


def _find(report: dict, task_id: str) -> dict:
    matches = [f for f in report["findings"] if f["task_id"] == task_id]
    assert len(matches) == 1, report["findings"]
    return matches[0]


def _assert_invalid(report: dict, task_id: str, code: str, category: str) -> None:
    finding = _find(report, task_id)
    assert (finding["classification"], finding["code"], finding["category"]) == ("INVALID", code, category), finding
    assert finding["fails"] is True and task_id in report["failing_task_ids"]
    assert report["verdict"] == "FAIL"


# ============================================================================ positive control


def test_valid_key_c_completion_is_verified_and_the_report_says_pass_is_not_true(auditor, enforce, key_c, kernel, db, ring_file):
    token = _complete_key_c(kernel, key_c, "keyc-l6-ok")
    _close(kernel, db)
    report = auditor.audit_database(db, ring_file)

    finding = _find(report, "keyc-l6-ok")
    signed = ra.parse_vr2(token)
    assert (finding["classification"], finding["code"], finding["fails"]) == ("VERIFIED_KEY_C", None, False)
    assert (finding["kid"], finding["jti"], finding["mode"]) == (signed["kid"], signed["jti"], "enforce")
    assert report["verdict"] == "PASS" and report["failing_task_ids"] == []
    assert report["counts"]["VERIFIED_KEY_C"] == 1 and report["counts"]["completed_tasks"] == 1
    assert "PASS != TRUE" in report["semantics"] and "no failure was observed in this DB snapshot" in report["semantics"]
    assert auditor.main(["--db", str(db), "--keyring", str(ring_file)]) == 0


# ======================================================================= read-only guarantees


def test_auditor_does_not_write_the_db_sha256_identical_and_no_sidecar_created(auditor, enforce, key_c, kernel, db, ring_file, capsys):
    _complete_key_c(kernel, key_c, "keyc-l6-ro-1")
    _complete_key_c(kernel, key_c, "keyc-l6-ro-2")
    _close(kernel, db)
    before_sha, before_mtime = auditor.file_sha256(db), db.stat().st_mtime_ns

    assert auditor.main(["--db", str(db), "--keyring", str(ring_file)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert auditor.file_sha256(db) == before_sha and db.stat().st_mtime_ns == before_mtime
    assert _sidecars(db) == []  # no -wal, -shm or -journal was created
    assert (report["db"]["sha256_before"], report["db"]["sha256_after"]) == (before_sha, before_sha)
    assert report["db"]["snapshot_unchanged"] is True and report["db"]["open"] == "sqlite uri mode=ro&immutable=1"


def test_snapshot_connection_is_read_only_and_creates_no_sidecar(auditor, enforce, key_c, kernel, db):
    _complete_key_c(kernel, key_c, "keyc-l6-ro-conn")
    _close(kernel, db)
    conn = auditor.open_snapshot(db)
    try:
        assert conn.execute("SELECT count(*) FROM tasks").fetchone()[0] == 1
        assert _sidecars(db) == []  # even while the connection is open
        with pytest.raises(sqlite3.OperationalError, match="readonly|read-only|query_only"):
            conn.execute("UPDATE tasks SET state='FAILED'")
    finally:
        conn.close()
    assert _sidecars(db) == []


def test_live_db_with_a_wal_sidecar_is_refused_not_silently_read(auditor, enforce, key_c, kernel, db, ring_file, capsys):
    _complete_key_c(kernel, key_c, "keyc-l6-live")
    assert Path(f"{db}-wal").exists()  # the kernel is still open: WAL frames may not be in the main file
    before = auditor.file_sha256(db)
    with pytest.raises(auditor.AuditInputError) as excinfo:
        auditor.audit_database(db, ring_file)
    assert excinfo.value.code == "DB_LIVE_SIDECAR"
    assert auditor.main(["--db", str(db), "--keyring", str(ring_file)]) == 2
    assert json.loads(capsys.readouterr().out)["verdict"] == "UNKNOWN"
    assert auditor.file_sha256(db) == before


# ============================================================================= INVALID: signature


@pytest.mark.parametrize("tamper", ["body-edit", "spliced-signature"])
def test_tampered_token_in_the_journal_row_is_invalid_signature(auditor, enforce, key_c, kernel, db, ring_file, tamper):
    token = _complete_key_c(kernel, key_c, "keyc-l6-sig")
    other = _complete_key_c(kernel, key_c, "keyc-l6-sig-other")
    _close(kernel, db)
    body, sig = token[len("vr2."):].split(".")
    if tamper == "body-edit":
        signed = json.loads(ra.b64url_decode(body))
        signed["evidence_ref"] = "evidence://forged"
        forged = f"vr2.{ra.b64url_encode(ra.canonical_payload_bytes(signed))}.{sig}"
    else:
        forged = f"vr2.{body}.{other.split('.')[2]}"
    _rewrite_payload(db, "keyc-l6-sig", lambda p: p.update(receipt_token=forged))

    report = auditor.audit_database(db, ring_file)
    _assert_invalid(report, "keyc-l6-sig", "BAD_SIGNATURE", "signature")
    assert _find(report, "keyc-l6-sig-other")["classification"] == "VERIFIED_KEY_C"
    assert auditor.main(["--db", str(db), "--keyring", str(ring_file)]) == 1


# =============================================================================== INVALID: binding


@pytest.mark.parametrize("field", ["task-transplant", "lease", "attempt", "fencing"])
def test_journal_row_binding_tamper_is_invalid_binding(auditor, enforce, key_c, kernel, db, ring_file, field):
    _complete_key_c(kernel, key_c, "keyc-l6-donor")
    _complete_key_c(kernel, key_c, "keyc-l6-victim")
    _close(kernel, db)
    donor = _completed_payload(db, "keyc-l6-donor")
    receipt_fields = ("receipt_token", "receipt_kid", "receipt_jti", "receipt_signature_sha256", "lease_id",
                      "attempt_id", "fencing_token", "verifier_id", "evidence_ref", "issued_at")
    mutate = {
        # A valid receipt of another task, copied whole into this task's row: only task_id differs.
        "task-transplant": lambda p: p.update({name: donor[name] for name in receipt_fields}),
        "lease": lambda p: p.update(lease_id=donor["lease_id"]),
        "attempt": lambda p: p.update(attempt_id="attempt_forged"),
        "fencing": lambda p: p.update(fencing_token=p["fencing_token"] + 1),
    }[field]
    _rewrite_payload(db, "keyc-l6-victim", mutate)
    code = {"task-transplant": "TASK_MISMATCH", "lease": "LEASE_MISMATCH", "attempt": "ATTEMPT_MISMATCH",
            "fencing": "FENCING_MISMATCH"}[field]

    report = auditor.audit_database(db, ring_file)
    _assert_invalid(report, "keyc-l6-victim", code, "binding")
    assert _find(report, "keyc-l6-donor")["classification"] == "VERIFIED_KEY_C"


@pytest.mark.parametrize("column", ["task_id", "attempt_id", "fencing_token", "deleted"])
def test_lease_table_tamper_is_invalid_binding(auditor, enforce, key_c, kernel, db, ring_file, column):
    token = _complete_key_c(kernel, key_c, "keyc-l6-lease")
    _close(kernel, db)
    lease_id = ra.parse_vr2(token)["lease_id"]
    statement = {
        "task_id": "UPDATE leases SET task_id='keyc-l6-other' WHERE lease_id=?",
        "attempt_id": "UPDATE leases SET attempt_id='attempt_forged' WHERE lease_id=?",
        "fencing_token": "UPDATE leases SET fencing_token=fencing_token+1 WHERE lease_id=?",
        "deleted": "DELETE FROM leases WHERE lease_id=?",
    }[column]
    _sql(db, statement, (lease_id,))
    code = {"task_id": "LEASE_MISMATCH", "attempt_id": "ATTEMPT_MISMATCH", "fencing_token": "FENCING_MISMATCH",
            "deleted": "LEASE_ROW_MISSING"}[column]
    _assert_invalid(auditor.audit_database(db, ring_file), "keyc-l6-lease", code, "binding")


# ============================================================================== INVALID: key/time


@pytest.mark.parametrize("case", ["revoked", "unknown"])
def test_revoked_or_unknown_kid_in_the_keyring_is_invalid(auditor, enforce, key_c, kernel, db, tmp_path, case):
    _complete_key_c(kernel, key_c, "keyc-l6-kid")
    _close(kernel, db)
    rotated = Ed25519PrivateKey.generate()
    entries = [_entry(key_c, status="revoked"), _entry(rotated)] if case == "revoked" else [_entry(rotated)]
    ring = tmp_path / "archive_keyring.json"
    ring.write_text(_ring(*entries), encoding="utf-8")
    code = {"revoked": "KID_REVOKED", "unknown": "UNKNOWN_KID"}[case]
    _assert_invalid(auditor.audit_database(db, ring), "keyc-l6-kid", code, "key")


def test_key_and_receipt_validity_are_judged_at_completion_time_not_audit_time(auditor, enforce, key_c, kernel, db, tmp_path):
    # Short (3 s) but not razor-thin TTL: the kernel itself must still accept it under machine load.
    token = _complete_key_c(kernel, key_c, "keyc-l6-time", ttl_seconds=3.0)
    _close(kernel, db)
    exp = float(ra.parse_vr2(token)["exp"])
    while time.time() <= exp + 0.2:  # the receipt is expired NOW; it was valid when the kernel committed it
        time.sleep(0.05)
    completed_at = auditor.parse_timestamp(_find(auditor.audit_database(db, _write(tmp_path, "a.json", _ring(_entry(key_c)))),
                                                 "keyc-l6-time")["completed_at"])
    assert completed_at is not None and completed_at <= exp
    rotated = _entry(Ed25519PrivateKey.generate())
    retired_after = _write(tmp_path, "b.json", _ring(_entry(key_c, "retiring", completed_at + 30.0), rotated))
    retired_before = _write(tmp_path, "c.json", _ring(_entry(key_c, "retiring", completed_at - 30.0), rotated))

    assert _find(auditor.audit_database(db, retired_after), "keyc-l6-time")["classification"] == "VERIFIED_KEY_C"
    _assert_invalid(auditor.audit_database(db, retired_before), "keyc-l6-time", "KID_EXPIRED", "key")


def _write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


# ===================================================================== SELF_ATTESTED / floor


def test_audit_mode_hmac_completion_is_self_attested_and_passes_without_a_floor(auditor, env, kernel, db, ring_file):
    _complete_hmac(kernel, "keyc-l6-hmac")
    _close(kernel, db)
    report = auditor.audit_database(db, ring_file)
    finding = _find(report, "keyc-l6-hmac")
    assert (finding["classification"], finding["after_floor"], finding["fails"]) == ("SELF_ATTESTED_HMAC", False, False)
    assert report["counts"]["SELF_ATTESTED_HMAC"] == 1 and report["floor"]["source"] == "none"
    assert report["verdict"] == "PASS"


@pytest.mark.parametrize("case", ["since-before", "since-zero", "since-exact", "since-after", "unreadable-time"])
def test_self_attested_at_or_after_the_cli_floor_fails(auditor, env, kernel, db, ring_file, case):
    start = time.time()
    _complete_hmac(kernel, "keyc-l6-floor")
    _close(kernel, db)
    completed_at = _completed_payload(db, "keyc-l6-floor", column="created_at")
    if case == "unreadable-time":
        _sql(db, "UPDATE events SET created_at='not-a-time' WHERE task_id=? AND type='TASK_COMPLETED'", ("keyc-l6-floor",))
    since = {"since-before": start - 1.0, "since-zero": "0", "since-exact": completed_at,  # "at" the floor fails
             "since-after": time.time() + 60.0, "unreadable-time": "2000-01-01T00:00:00+00:00"}[case]
    report = auditor.audit_database(db, ring_file, since=since)
    finding = _find(report, "keyc-l6-floor")
    assert finding["classification"] == "SELF_ATTESTED_HMAC" and report["floor"]["source"] == "cli"
    if case == "since-after":
        assert (finding["fails"], report["verdict"]) == (False, "PASS")
    else:  # an unreadable completion time is treated as after the floor (fail-closed)
        assert (finding["fails"], finding["code"], finding["category"]) == (True, "SELF_ATTESTED_AFTER_FLOOR", "floor")
        assert report["verdict"] == "FAIL"


def test_journal_floor_catches_an_env_downgrade_after_enforce(auditor, env, key_c, kernel, db, ring_file):
    _complete_hmac(kernel, "keyc-l6-before")              # GD1 audit history: allowed
    env.setenv("SCP_RECEIPT_AUTHORITY_MODE", "enforce")
    _complete_key_c(kernel, key_c, "keyc-l6-enforced")      # the enforce point, journaled by L2a
    env.setenv("SCP_RECEIPT_AUTHORITY_MODE", "audit")       # env downgrade (no DB floor before L2b)
    _complete_hmac(kernel, "keyc-l6-downgraded")
    _close(kernel, db)

    report = auditor.audit_database(db, ring_file)
    before, enforced, after = (_find(report, t) for t in ("keyc-l6-before", "keyc-l6-enforced", "keyc-l6-downgraded"))
    floor = report["floor"]
    assert floor["source"] == "journal" and floor["journal_evidence_event_ids"]
    t_before, t_enforced = (auditor.parse_timestamp(f["completed_at"]) for f in (before, enforced))
    assert t_before < floor["effective"] <= t_enforced
    assert (before["classification"], before["fails"]) == ("SELF_ATTESTED_HMAC", False)
    assert (enforced["classification"], enforced["fails"]) == ("VERIFIED_KEY_C", False)
    assert (after["classification"], after["code"], after["fails"]) == ("SELF_ATTESTED_HMAC", "SELF_ATTESTED_AFTER_FLOOR", True)
    assert report["failing_task_ids"] == ["keyc-l6-downgraded"] and report["verdict"] == "FAIL"


def test_an_enforce_marker_with_an_unreadable_time_makes_the_floor_unbounded(auditor, env, key_c, kernel, db, ring_file, capsys):
    _complete_hmac(kernel, "keyc-l6-early")
    env.setenv("SCP_RECEIPT_AUTHORITY_MODE", "enforce")
    _complete_key_c(kernel, key_c, "keyc-l6-marker")
    _close(kernel, db)
    _sql(db, "UPDATE events SET created_at='not-a-time' WHERE task_id='__global__' AND type='RECEIPT_KEYRING_LOADED'")

    report = auditor.audit_database(db, ring_file)
    assert (report["floor"]["unbounded"], report["floor"]["effective"]) == (True, None)
    early = _find(report, "keyc-l6-early")
    assert (early["classification"], early["code"], early["fails"]) == ("SELF_ATTESTED_HMAC", "SELF_ATTESTED_AFTER_FLOOR", True)
    assert _find(report, "keyc-l6-marker")["classification"] == "VERIFIED_KEY_C"
    assert auditor.main(["--db", str(db), "--keyring", str(ring_file)]) == 1

    def refuse_constant(name):
        raise AssertionError(f"non-standard JSON constant {name} in the CLI report")

    assert json.loads(capsys.readouterr().out, parse_constant=refuse_constant)["floor"]["unbounded"] is True


# ===================================================================================== MISSING


_PRE_L2A_FIELDS = ("evidence_ref", "verifier_verdict", "lease_id", "verifier_id", "signature_digest", "issued_at")


def test_pre_l2a_completion_is_missing_receipt_and_fails_only_after_the_floor(auditor, env, kernel, db, ring_file):
    _complete_hmac(kernel, "keyc-l6-legacy")
    _close(kernel, db)

    def to_pre_l2a_shape(payload):  # the exact payload keys bb8435b9 (pre-L2a) wrote
        for name in [n for n in payload if n not in _PRE_L2A_FIELDS]:
            del payload[name]

    _rewrite_payload(db, "keyc-l6-legacy", to_pre_l2a_shape)
    assert set(_completed_payload(db, "keyc-l6-legacy")) == set(_PRE_L2A_FIELDS)

    open_report = auditor.audit_database(db, ring_file)
    finding = _find(open_report, "keyc-l6-legacy")
    assert (finding["classification"], finding["code"], finding["fails"]) == ("MISSING_RECEIPT", "NO_RECEIPT_PAYLOAD", False)
    assert open_report["verdict"] == "PASS" and open_report["counts"]["MISSING_RECEIPT"] == 1

    floored = auditor.audit_database(db, ring_file, since=0)
    finding = _find(floored, "keyc-l6-legacy")
    assert (finding["classification"], finding["code"], finding["fails"]) == ("MISSING_RECEIPT", "MISSING_RECEIPT_AFTER_FLOOR", True)
    assert floored["verdict"] == "FAIL"
    assert auditor.main(["--db", str(db), "--keyring", str(ring_file), "--since", "0"]) == 1


# ========================================================================== INVALID: journal/payload


@pytest.mark.parametrize("case", ["no-event", "duplicate-event", "state-reverted"])
def test_journal_inconsistency_around_completed_is_invalid(auditor, enforce, key_c, kernel, db, ring_file, case):
    if case == "no-event":
        _to_verifying(kernel, "keyc-l6-journal")
        _close(kernel, db)
        _sql(db, "UPDATE tasks SET state='COMPLETED' WHERE task_id=?", ("keyc-l6-journal",))  # direct SQL write (P31)
    else:
        _complete_key_c(kernel, key_c, "keyc-l6-journal")
        _close(kernel, db)
        if case == "duplicate-event":
            _sql(db, "INSERT INTO events SELECT 'evt_keyc_l6_dup', task_id, seq+100, type, from_state, to_state, actor, "
                     "reason, payload_json, policy_hash, prev_event_hash, event_hash, created_at FROM events "
                     "WHERE task_id=? AND type='TASK_COMPLETED'", ("keyc-l6-journal",))
        else:
            _sql(db, "UPDATE tasks SET state='VERIFYING' WHERE task_id=?", ("keyc-l6-journal",))
    code = {"no-event": "COMPLETION_EVENT_MISSING", "duplicate-event": "DUPLICATE_COMPLETION_EVENT",
            "state-reverted": "TASK_STATE_MISMATCH"}[case]
    _assert_invalid(auditor.audit_database(db, ring_file), "keyc-l6-journal", code, "journal")


@pytest.mark.parametrize("case", ["hmac-claims-enforce", "hmac-relabelled-key-c", "key-c-relabelled-hmac",
                                  "key-c-missing-jti", "key-c-unknown-provenance", "key-c-unreadable-time"])
def test_relabelled_or_stripped_receipt_payload_is_invalid(auditor, env, key_c, kernel, db, ring_file, case):
    task_id = "keyc-l6-relabel"
    if case.startswith("hmac"):
        _complete_hmac(kernel, task_id)
    else:
        env.setenv("SCP_RECEIPT_AUTHORITY_MODE", "enforce")
        _complete_key_c(kernel, key_c, task_id)
    _close(kernel, db)
    if case == "key-c-unreadable-time":
        _sql(db, "UPDATE events SET created_at='not-a-time' WHERE task_id=? AND type='TASK_COMPLETED'", (task_id,))
    else:
        mutate = {
            "hmac-claims-enforce": lambda p: p.update(receipt_mode="enforce"),
            "hmac-relabelled-key-c": lambda p: p.update(receipt_provenance="VERIFIED_KEY_C"),
            "key-c-relabelled-hmac": lambda p: p.update(receipt_provenance="SELF_ATTESTED_HMAC"),
            "key-c-missing-jti": lambda p: p.pop("receipt_jti"),
            "key-c-unknown-provenance": lambda p: p.update(receipt_provenance="TRUSTED"),
        }[case]
        _rewrite_payload(db, task_id, mutate)
    code = {"hmac-claims-enforce": "SELF_ATTESTED_IN_ENFORCE", "hmac-relabelled-key-c": "MISSING_PAYLOAD_FIELD",
            "key-c-relabelled-hmac": "PROVENANCE_MISMATCH", "key-c-missing-jti": "MISSING_PAYLOAD_FIELD",
            "key-c-unknown-provenance": "UNKNOWN_PROVENANCE", "key-c-unreadable-time": "MALFORMED_TIMESTAMP"}[case]
    _assert_invalid(auditor.audit_database(db, ring_file), task_id, code, "payload")


@pytest.mark.parametrize("field", ["receipt_kid", "receipt_jti", "evidence_ref", "verifier_id", "verifier_verdict",
                                   "issued_at", "receipt_signature_sha256"])
def test_journal_claims_must_equal_the_signed_receipt(auditor, enforce, key_c, kernel, db, ring_file, field):
    _complete_key_c(kernel, key_c, "keyc-l6-claims")
    _close(kernel, db)
    forged = {"receipt_kid": "0123456789abcdef", "receipt_jti": "f" * 32, "evidence_ref": "evidence://forged",
              "verifier_id": "forged-verifier", "verifier_verdict": "VERIFIED_BY_ME", "issued_at": 1.0,
              "receipt_signature_sha256": "sha256:" + "0" * 64}[field]
    _rewrite_payload(db, "keyc-l6-claims", lambda p: p.update({field: forged}))
    _assert_invalid(auditor.audit_database(db, ring_file), "keyc-l6-claims", "CARRIER_MISMATCH", "payload")


def test_purged_task_completion_is_still_audited(auditor, enforce, key_c, kernel, db, ring_file):
    _complete_key_c(kernel, key_c, "keyc-l6-purged")
    assert kernel.purge_tasks("keyc-l6-purged", actor="keyc-l6-operator")["deleted_count"] == 1
    _close(kernel, db)
    report = auditor.audit_database(db, ring_file)
    finding = _find(report, "keyc-l6-purged")
    assert (finding["task_state"], finding["classification"]) == (None, "VERIFIED_KEY_C")
    assert report["counts"]["completed_tasks"] == 0 and report["counts"]["completion_events"] == 1


# ================================================================================= input refusal


def _private_inputs(tmp_path: Path) -> dict[str, tuple[Path, str, str]]:
    key = Ed25519PrivateKey.generate()
    seed_b64 = base64.b64encode(key.private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())).decode("ascii")
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()).decode("ascii")
    with_private = json.loads(_ring(_entry(key)))
    with_private["keys"][0]["priv"] = seed_b64
    cases = {
        "keygen-private-file": ("verifier_c_private.key", seed_b64 + "\n", seed_b64),
        "private-seed-renamed": ("keyring.json", seed_b64 + "\n", seed_b64),
        "pem-pkcs8": ("keyring.pem", pem, pem.splitlines()[1]),
        "json-with-private-field": ("keyring_with_field.json", json.dumps(with_private), seed_b64),
        "dotenv": (".env", f"SCP_VERIFIER_PRIVATE_KEY={seed_b64}\n", seed_b64),
    }
    out = {}
    for case, (name, text, secret) in cases.items():
        path = tmp_path / "inputs" / case / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        out[case] = (path, "ENV_INPUT_REFUSED" if case == "dotenv" else "PRIVATE_KEY_INPUT_REFUSED", secret)
    return out


@pytest.mark.parametrize("case", ["keygen-private-file", "private-seed-renamed", "pem-pkcs8",
                                  "json-with-private-field", "dotenv"])
def test_auditor_refuses_private_key_and_env_inputs(auditor, enforce, key_c, kernel, db, tmp_path, capsys, case):
    _complete_key_c(kernel, key_c, "keyc-l6-refuse")
    _close(kernel, db)
    path, code, secret = _private_inputs(tmp_path)[case]
    with pytest.raises(auditor.AuditInputError) as excinfo:
        auditor.audit_database(db, path)
    assert excinfo.value.code == code
    assert auditor.main(["--db", str(db), "--keyring", str(path)]) == 2
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert (report["verdict"], report["verdict_code"]) == ("UNKNOWN", code)
    assert secret not in captured.out and secret not in captured.err  # the material is never echoed


def test_auditor_trusts_only_the_keyring_file_never_the_process_env(auditor, enforce, key_c, kernel, db, ring_file, tmp_path):
    _complete_key_c(kernel, key_c, "keyc-l6-env")
    _close(kernel, db)
    stranger = Ed25519PrivateKey.generate()
    # The process env says "trust only the stranger, mode garbage": the auditor must not care.
    enforce.setenv("SCP_VERIFIER_PUBLIC_KEYS", _ring(_entry(stranger)))
    enforce.setenv("SCP_RECEIPT_AUTHORITY_MODE", "not-a-mode")
    assert _find(auditor.audit_database(db, ring_file), "keyc-l6-env")["classification"] == "VERIFIED_KEY_C"
    # ...and an env holding the right key cannot rescue a keyring file that lacks it.
    enforce.setenv("SCP_VERIFIER_PUBLIC_KEYS", _ring(_entry(key_c)))
    stranger_ring = _write(tmp_path, "stranger_ring.json", _ring(_entry(stranger)))
    _assert_invalid(auditor.audit_database(db, stranger_ring), "keyc-l6-env", "UNKNOWN_KID", "key")


# ============================================================================ CLI end-to-end


def _scrubbed_env() -> dict[str, str]:
    keep = ("PATH", "SYSTEMROOT", "SYSTEMDRIVE", "TEMP", "TMP", "WINDIR")
    return {name: os.environ[name] for name in keep if name in os.environ}


def _run(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, *args], cwd=cwd, env=_scrubbed_env(), capture_output=True,
                          text=True, timeout=120, check=False)


def test_cli_end_to_end_with_keygen_keys_and_a_scrubbed_env(auditor, env, kernel, db, tmp_path):
    daemon_dir, keyring = tmp_path / "daemon_only", tmp_path / "public" / "keyring.json"
    keyring.parent.mkdir(parents=True, exist_ok=True)
    keygen = _run([str(REPO / "tools" / "verifier_keygen.py"), "--out-dir", str(daemon_dir), "--keyring-out", str(keyring)], tmp_path)
    assert keygen.returncode == 0, keygen.stderr
    private_file = daemon_dir / "verifier_c_private.key"
    key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(private_file.read_text(encoding="ascii").strip()))
    env.delenv("SCP_VERIFIER_PUBLIC_KEYS", raising=False)
    env.setenv("SCP_VERIFIER_PUBLIC_KEYS_FILE", str(keyring))  # the kernel trusts the same public keyring
    env.setenv("SCP_RECEIPT_AUTHORITY_MODE", "enforce")
    _complete_key_c(kernel, key, "keyc-l6-cli")
    _close(kernel, db)

    passed = _run([str(TOOL), "--db", str(db), "--keyring", str(keyring), "--since", "0"], tmp_path)
    assert passed.returncode == 0, passed.stdout + passed.stderr
    report = json.loads(passed.stdout)
    assert report["verdict"] == "PASS" and _find(report, "keyc-l6-cli")["classification"] == "VERIFIED_KEY_C"
    assert "PASS != TRUE" in report["semantics"]

    refused = _run([str(TOOL), "--db", str(db), "--keyring", str(private_file)], tmp_path)
    assert refused.returncode == 2 and json.loads(refused.stdout)["verdict_code"] == "PRIVATE_KEY_INPUT_REFUSED"
    assert private_file.read_text(encoding="ascii").strip() not in refused.stdout + refused.stderr

    _rewrite_payload(db, "keyc-l6-cli", lambda p: p.update(fencing_token=p["fencing_token"] + 1))
    failed = _run([str(TOOL), "--db", str(db), "--keyring", str(keyring), "--out", str(tmp_path / "report.json")], tmp_path)
    assert failed.returncode == 1
    on_disk = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert on_disk["failing_task_ids"] == ["keyc-l6-cli"] and on_disk["verdict"] == "FAIL"

    probe = _run(["-c", ("import sys, scp.runtime_audit.receipt_auditor; "
                         "print('scp.task_kernel_parts.taskkernel' in sys.modules)")], REPO)
    assert probe.stdout.strip() == "False", probe.stderr  # the auditor never pulls in TaskKernel


def test_auditor_event_names_match_the_l2a_kernel_writer(auditor):
    from scp.task_kernel_parts import commit_approval

    assert auditor.KEYRING_LOADED_EVENT == commit_approval.KEYRING_LOADED_EVENT
    assert (auditor.VERIFIED_KEY_C, auditor.SELF_ATTESTED_HMAC) == (ra.PROVENANCE_KEY_C, ra.PROVENANCE_SELF_ATTESTED)
    assert auditor.COMPLETED_EVENT == "TASK_COMPLETED"
    assert '"TASK_COMPLETED"' in Path(commit_approval.__file__).read_text(encoding="utf-8")
