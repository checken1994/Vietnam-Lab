"""Key C lane L2b — one-way receipt-authority mode floor (audit -> enforce), kernel-persisted.

Design keyc-1A §2.3 ("floor một chiều") + checker-keyc-merge directive 7 / condition 4:

* ``control.receipt_mode_floor`` is a §15 signed control flag (HMAC under K_journal, domain
  "control"). A missing/invalid signature reads as the STRICT value ``enforce`` (never audit).
* The commit gate reads the verified floor INSIDE the commit transaction and passes it to
  ``receipt_authority.verify_completion_receipt(mode_floor=...)``: once the floor is enforce, setting
  ``SCP_RECEIPT_AUTHORITY_MODE=audit`` no longer lets a self-signed HMAC receipt complete a task.
* One-way: the only kernel API raises audit -> enforce; nothing lowers it. A raise is also journaled
  (``__global__ RECEIPT_MODE_FLOOR_RAISED``) and the journal anchor keeps the floor at enforce even
  if the control row is rolled back to the well-known (deterministic) audit signature.
* Fresh DB -> audit (signed). Pre-L2b DB -> columns added and self-signed ONCE as audit (§15 rule).
* ``tools/receipt_authority_cutover.py`` raises the floor only after preflight (public keyring with
  an active kid; verifier reachable over its pipe or explicitly skipped) and proves it with a health
  check on a fresh kernel instance (R6 lesson: the flag must really be ON in the DB).

Zero-mock: real TaskKernel on temp SQLite files, real throwaway Ed25519 keys, real HMAC receipts
with test-only secrets set through monkeypatch; the "attacker" is a second sqlite3 connection that
writes the control table directly; the live-verifier test spawns the real daemon over a named pipe.
"""
from __future__ import annotations

import base64
import importlib
import json
import os
import secrets
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from scp.core.verifier_receipt import InvalidReceiptSignatureError, VerifierReceipt, sign_verifier_receipt
from scp.kernel_storage import SQLiteKernelStorage
from scp.task_kernel import TaskKernel
from scp.task_kernel_parts.definitions import KernelError, compute_control_flag_sig

ROOT = Path(__file__).resolve().parents[2]
PY = sys.executable
FLOOR = "receipt_mode_floor"
FLOOR_SIG = FLOOR + "_sig"
RAISED = "RECEIPT_MODE_FLOOR_RAISED"
MODE_ENV = "SCP_RECEIPT_AUTHORITY_MODE"
_CAPABILITY = "keyc-l2b-capability-secret-32bytes-distinct!"
_JOURNAL = "keyc-l2b-journal-secret-Kjournal-32bytes-val"
_HMAC_SECRET = "keyc-l2b-test-only-verifier-hmac-secret-0123456"
_WORKER = "keyc-l2b-worker"
_ENV_TO_CLEAR = (
    "SCP_PREVIOUS_CAPABILITY_SECRETS", "SCP_PREVIOUS_JOURNAL_SECRETS", "SCP_JOURNAL_SECRET_FILE",
    "SCP_VERIFIER_SECRET_FILE", "SCP_VERIFIER_PUBLIC_KEYS", "SCP_VERIFIER_PUBLIC_KEYS_FILE",
    "SCP_VERIFIER_PRIVATE_KEY", "SCP_VERIFIER_PRIVATE_KEY_FILE", MODE_ENV, f"{MODE_ENV}_FILE",
    "SCP_VERIFIER_IPC_TOKEN",
)


# ----------------------------------------------------------------------------- fixtures/helpers
def _ring(*keys, status: str = "active", not_after: float | None = None) -> str:
    entries = []
    for key in keys:
        raw = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        entry = {"pub": base64.b64encode(raw).decode("ascii"), "status": status}
        if not_after is not None:
            entry["not_after"] = not_after
        entries.append(entry)
    return json.dumps({"keys": entries})


@pytest.fixture
def key_c():
    return Ed25519PrivateKey.generate()


@pytest.fixture
def env(monkeypatch, key_c):
    for name in _ENV_TO_CLEAR:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SCP_CAPABILITY_SECRET", _CAPABILITY)
    monkeypatch.setenv("SCP_JOURNAL_SECRET", _JOURNAL)
    monkeypatch.setenv("SCP_VERIFIER_SECRET", _HMAC_SECRET)
    monkeypatch.setenv("SCP_VERIFIER_PUBLIC_KEYS", _ring(key_c))
    return monkeypatch


@pytest.fixture
def db(tmp_path, env):
    return tmp_path / "keyc_l2b.sqlite3"


@pytest.fixture
def kernel(db):
    k = TaskKernel(db)
    try:
        yield k
    finally:
        k.close()


def _raw(db_path, column: str):
    side = sqlite3.connect(db_path)
    try:
        return side.execute(f"SELECT {column} FROM control WHERE id=1").fetchone()[0]
    finally:
        side.close()


def _set_raw(db_path, column: str, value) -> None:
    side = sqlite3.connect(db_path)
    try:
        side.execute(f"UPDATE control SET {column}=? WHERE id=1", (value,))
        side.commit()
    finally:
        side.close()


def _control_columns(db_path) -> list[str]:
    side = sqlite3.connect(db_path)
    try:
        return [row[1] for row in side.execute('PRAGMA table_info("control")')]
    finally:
        side.close()


def _drop_floor_columns(db_path) -> None:
    """Shape of a DB written by pre-L2b code (88376fe0): §15 sig columns, no floor columns."""
    side = sqlite3.connect(db_path)
    try:
        present = {row[1] for row in side.execute('PRAGMA table_info("control")')}
        for col in (FLOOR_SIG, FLOOR):
            if col in present:
                side.execute(f'ALTER TABLE control DROP COLUMN "{col}"')
        side.commit()
    finally:
        side.close()


def _raise(k: TaskKernel, actor: str = "keyc-l2b-operator"):
    return k.approval_engine.raise_receipt_mode_floor(actor=actor)


def _to_verifying(k: TaskKernel, task_id: str):
    k.create_task(task_id, "keyc-l2b-owner", "keyc l2b goal")
    for state in ("PLANNING", "READY", "QUEUED"):
        k.transition(task_id, state, actor="keyc-l2b-scheduler", reason="keyc_l2b_setup")
    lease = k.claim(task_id, _WORKER, ttl_seconds=120)
    k.start(task_id, lease.lease_id)
    k.transition(task_id, "VERIFYING", actor=_WORKER, reason="keyc_l2b_setup")
    return lease


def _hmac(task_id: str):
    return sign_verifier_receipt(
        VerifierReceipt(task_id=task_id, verifier_id="hands-executor-self-verifier-v1", verdict="VERIFIED",
                        evidence_ref=f"evidence://self/{task_id}", issued_at=time.time()),
        _HMAC_SECRET,
    )


def _mint(key, lease) -> str:
    from scp.security.receipt_authority import mint_vr2

    return mint_vr2(key, task_id=lease.task_id, attempt_id=lease.attempt_id, lease_id=lease.lease_id,
                    fencing_token=lease.fencing_token, evidence_ref=f"evidence://keyc-l2b/{lease.task_id}",
                    profile="journal_integrity")


def _completed_payload(k: TaskKernel, task_id: str) -> dict:
    events = [e for e in k.get_events(task_id) if e["type"] == "TASK_COMPLETED"]
    assert len(events) == 1
    return json.loads(events[0]["payload_json"])


def _raised_events(k: TaskKernel) -> list[dict]:
    return [json.loads(e["payload_json"]) for e in k.get_events("__global__") if e["type"] == RAISED]


def _assert_hmac_refused(k: TaskKernel, task_id: str, lease) -> None:
    with pytest.raises(InvalidReceiptSignatureError) as excinfo:
        k.commit_verification_result(task_id, lease.lease_id, _hmac(task_id))
    assert getattr(excinfo.value, "code", None) == "HMAC_REJECTED", repr(excinfo.value)
    assert k.get_task(task_id)["state"] == "VERIFYING"
    assert [e for e in k.get_events(task_id) if e["type"] == "TASK_COMPLETED"] == []


def _floor_module():
    return importlib.import_module("scp.task_kernel_parts.receipt_floor")


# ------------------------------------------------------------------------------ fresh / pre-L2b DB
def test_fresh_db_floor_is_audit_signed_and_hmac_still_accepted(env, db, kernel):
    control = kernel._control()
    assert control[FLOOR] == "audit"
    assert control["control_flags_sig_valid"][FLOOR] is True
    assert _raw(db, FLOOR_SIG) == compute_control_flag_sig(FLOOR, "audit")
    floor = _floor_module()
    assert floor.read_receipt_mode_floor(kernel) == "audit"
    health = floor.receipt_floor_health(kernel)
    assert (health["receipt_mode_floor"], health["receipt_mode_floor_sig_valid"]) == ("audit", True)
    assert health["receipt_mode_floor_raise_journaled"] is False
    assert health["receipt_mode_floor_state"] == "NOT_ENFORCED"

    env.setenv(MODE_ENV, "audit")
    lease = _to_verifying(kernel, "keyc-l2b-fresh")
    assert kernel.commit_verification_result(lease.task_id, lease.lease_id, _hmac(lease.task_id))["state"] == "COMPLETED"
    payload = _completed_payload(kernel, lease.task_id)
    assert (payload["receipt_provenance"], payload["receipt_mode"]) == ("SELF_ATTESTED_HMAC", "audit")
    assert payload["receipt_mode_floor"] == "audit"


def test_pre_l2b_db_is_migrated_and_self_signed_audit_exactly_once(env, db):
    k = TaskKernel(db)
    try:
        k.set_global_kill(True, actor="keyc-l2b-operator")
        k.set_global_kill(False, actor="keyc-l2b-operator")
    finally:
        k.close()
    _drop_floor_columns(db)
    assert FLOOR not in _control_columns(db)
    others = {col: _raw(db, col) for col in _control_columns(db) if col.endswith("_sig")}

    upgraded = TaskKernel(db)
    try:
        assert _raw(db, FLOOR) == "audit"
        assert _raw(db, FLOOR_SIG) == compute_control_flag_sig(FLOOR, "audit")
        assert upgraded._control()["control_flags_sig_valid"][FLOOR] is True
        assert {col: _raw(db, col) for col in others} == others  # no other flag re-signed (no laundering)
        first_sig = _raw(db, FLOOR_SIG)
    finally:
        upgraded.close()

    again = TaskKernel(db)  # column already present -> never self-signed again
    try:
        assert _raw(db, FLOOR_SIG) == first_sig
        assert _floor_module().read_receipt_mode_floor(again) == "audit"
        env.setenv(MODE_ENV, "audit")
        lease = _to_verifying(again, "keyc-l2b-upgraded")
        assert again.commit_verification_result(lease.task_id, lease.lease_id, _hmac(lease.task_id))["state"] == "COMPLETED"
    finally:
        again.close()


def test_pre_l2b_db_opened_without_k_journal_reads_enforce_and_defers_signing(env, db):
    TaskKernel(db).close()
    _drop_floor_columns(db)
    env.delenv("SCP_JOURNAL_SECRET")
    blind = TaskKernel(db)
    try:
        control = blind._control()
        assert control[FLOOR] == "enforce"  # unsigned -> strict value
        assert control["control_flags_sig_valid"][FLOOR] is False
        # The sig column is not added unsigned: a NULL sig would pin the floor forever.
        assert FLOOR_SIG not in _control_columns(db)
    finally:
        blind.close()

    env.setenv("SCP_JOURNAL_SECRET", _JOURNAL)
    keyed = TaskKernel(db)
    try:
        assert _raw(db, FLOOR_SIG) == compute_control_flag_sig(FLOOR, "audit")
        assert keyed._control()["control_flags_sig_valid"][FLOOR] is True
        assert _floor_module().read_receipt_mode_floor(keyed) == "audit"
    finally:
        keyed.close()


# -------------------------------------------------------------------- floor honored by commit gate
def test_signed_floor_on_disk_is_honored_inside_the_commit_gate(env, key_c, db, kernel):
    """A validly signed enforce floor (written with K_journal, no new API) must beat env audit."""
    side = sqlite3.connect(db)
    try:
        present = {row[1] for row in side.execute('PRAGMA table_info("control")')}
        for col in (FLOOR, FLOOR_SIG):
            if col not in present:
                side.execute(f'ALTER TABLE control ADD COLUMN "{col}" TEXT')
        side.execute(f"UPDATE control SET {FLOOR}=?, {FLOOR_SIG}=? WHERE id=1",
                     ("enforce", compute_control_flag_sig(FLOOR, "enforce")))
        side.commit()
    finally:
        side.close()
    env.setenv(MODE_ENV, "audit")
    lease = _to_verifying(kernel, "keyc-l2b-disk")
    _assert_hmac_refused(kernel, lease.task_id, lease)
    assert kernel.commit_verification_result(lease.task_id, lease.lease_id, _mint(key_c, lease))["state"] == "COMPLETED"
    payload = _completed_payload(kernel, lease.task_id)
    assert (payload["receipt_provenance"], payload["receipt_mode"]) == ("VERIFIED_KEY_C", "enforce")


@pytest.mark.parametrize("setting", ["unset", "audit", "AUDIT", "file-audit"])
def test_env_audit_cannot_lower_an_api_raised_floor(env, key_c, db, kernel, tmp_path, setting):
    _raise(kernel)
    if setting == "file-audit":
        mode_file = tmp_path / "mode.txt"
        mode_file.write_text("audit\n", encoding="utf-8")
        env.setenv(f"{MODE_ENV}_FILE", str(mode_file))
    elif setting != "unset":
        env.setenv(MODE_ENV, setting)
    lease = _to_verifying(kernel, f"keyc-l2b-env-{setting}")
    _assert_hmac_refused(kernel, lease.task_id, lease)
    assert kernel.commit_verification_result(lease.task_id, lease.lease_id, _mint(key_c, lease))["state"] == "COMPLETED"
    payload = _completed_payload(kernel, lease.task_id)
    assert (payload["receipt_mode"], payload["receipt_mode_floor"]) == ("enforce", "enforce")


def test_floor_cannot_be_lowered_by_any_kernel_api(env, db, kernel):
    floor = _floor_module()
    health = _raise(kernel)
    assert (health["receipt_mode_floor"], health["receipt_mode_floor_sig_valid"]) == ("enforce", True)
    assert health["receipt_mode_floor_state"] == "ENFORCED"
    signed = _raw(db, FLOOR_SIG)
    assert signed == compute_control_flag_sig(FLOOR, "enforce")
    for bad in ("audit", "AUDIT", "", None, "enforce ", "off"):
        with pytest.raises(KernelError):
            kernel.approval_engine.raise_receipt_mode_floor(bad, actor="keyc-l2b-operator")
        with pytest.raises(KernelError):
            floor.raise_receipt_mode_floor(kernel, bad, actor="keyc-l2b-operator")
    # Every other control writer leaves the floor (value AND signature bytes) untouched.
    kernel.set_global_kill(True, actor="keyc-l2b-operator")
    kernel.set_global_kill(False, actor="keyc-l2b-operator")
    assert kernel.reanchor_all_journals()["status"] == "COMPLETED"
    assert (_raw(db, FLOOR), _raw(db, FLOOR_SIG)) == ("enforce", signed)
    assert kernel._control()["control_flags_sig_valid"][FLOOR] is True
    # Raising again is idempotent: same signature, still exactly one journaled raise.
    _raise(kernel)
    assert _raw(db, FLOOR_SIG) == signed
    assert len(_raised_events(kernel)) == 1
    # API surface: the only floor writer is the raise.
    surface = {name for obj in (kernel, kernel.approval_engine, floor) for name in dir(obj)
               if "floor" in name.lower() and not name.startswith("_")}
    assert surface <= {"raise_receipt_mode_floor", "read_receipt_mode_floor", "receipt_floor_health",
                       "floor_raise_journaled", "FLOOR_FLAG", "FLOOR_RAISED_EVENT"}, surface


# ---------------------------------------------------------------------------- tamper-evidence
def test_tampered_floor_value_reads_enforce_and_hmac_is_refused(env, db, kernel):
    _raise(kernel)
    _set_raw(db, FLOOR, "audit")  # attacker lowers the value without K_journal
    reopened = TaskKernel(db)
    try:
        control = reopened._control()
        assert control[FLOOR] == "enforce"  # §15 verified read -> strict default
        assert control["control_flags_sig_valid"][FLOOR] is False
        health = _floor_module().receipt_floor_health(reopened)
        assert (health["receipt_mode_floor"], health["receipt_mode_floor_sig_valid"]) == ("enforce", False)
        assert health["receipt_mode_floor_state"] == "INCONSISTENT"
        env.setenv(MODE_ENV, "audit")
        lease = _to_verifying(reopened, "keyc-l2b-tamper")
        _assert_hmac_refused(reopened, lease.task_id, lease)
    finally:
        reopened.close()


def test_null_floor_signature_reads_enforce_and_is_never_resigned(env, db, kernel):
    _set_raw(db, FLOOR_SIG, None)  # never-raised DB: no journal anchor, only the fail-closed default
    reopened = TaskKernel(db)
    try:
        control = reopened._control()
        assert control[FLOOR] == "enforce"
        assert control["control_flags_sig_valid"][FLOOR] is False
        assert _raw(db, FLOOR_SIG) is None  # column existed -> open does NOT re-sign
        assert _raised_events(reopened) == []
        env.setenv(MODE_ENV, "audit")
        lease = _to_verifying(reopened, "keyc-l2b-null")
        _assert_hmac_refused(reopened, lease.task_id, lease)
    finally:
        reopened.close()


def test_replayed_audit_signature_after_raise_is_refused_by_journal_anchor(env, db, kernel):
    audit_sig = _raw(db, FLOOR_SIG)  # deterministic: identical in every DB under this K_journal
    _raise(kernel)
    _set_raw(db, FLOOR, "audit")
    _set_raw(db, FLOOR_SIG, audit_sig)
    reopened = TaskKernel(db)
    try:
        control = reopened._control()
        assert (control[FLOOR], control["control_flags_sig_valid"][FLOOR]) == ("audit", True)  # §15 alone is blind
        assert _floor_module().read_receipt_mode_floor(reopened) == "enforce"
        assert _floor_module().receipt_floor_health(reopened)["receipt_mode_floor_state"] == "INCONSISTENT"
        env.setenv(MODE_ENV, "audit")
        lease = _to_verifying(reopened, "keyc-l2b-replay")
        _assert_hmac_refused(reopened, lease.task_id, lease)
    finally:
        reopened.close()


def test_dropping_floor_columns_after_raise_cannot_downgrade(env, db):
    k = TaskKernel(db)
    try:
        _raise(k)
    finally:
        k.close()
    _drop_floor_columns(db)  # attacker hopes the §15 "newly added column" self-sign resets to audit
    reopened = TaskKernel(db)
    try:
        assert _raw(db, FLOOR_SIG) == compute_control_flag_sig(FLOOR, _raw(db, FLOOR))
        assert _floor_module().read_receipt_mode_floor(reopened) == "enforce"
        env.setenv(MODE_ENV, "audit")
        lease = _to_verifying(reopened, "keyc-l2b-drop")
        _assert_hmac_refused(reopened, lease.task_id, lease)
    finally:
        reopened.close()


# ------------------------------------------------------------- TOCTOU: floor read inside the txn
class _InterleavingStorage(SQLiteKernelStorage):
    """Real SQLite storage; the next BEGIN IMMEDIATE first lets one concurrent writer commit."""

    def __init__(self, db_path) -> None:
        super().__init__(db_path)
        self.before_next_begin = None
        self.interleaved = False

    def begin(self) -> None:
        hook, self.before_next_begin = self.before_next_begin, None
        if hook is not None:
            hook()
            self.interleaved = True
        super().begin()


@pytest.fixture
def racing(tmp_path, env):
    db_path = tmp_path / "keyc_l2b_toctou.sqlite3"
    storage = _InterleavingStorage(db_path)
    main, other = TaskKernel(storage=storage), TaskKernel(db_path)
    try:
        yield storage, main, other
    finally:
        main.close()
        other.close()


def test_toctou_positive_control_hmac_completes_without_a_raise(env, racing):
    storage, main, _other = racing
    env.setenv(MODE_ENV, "audit")
    lease = _to_verifying(main, "keyc-l2b-race-ok")
    storage.before_next_begin = lambda: None
    assert main.commit_verification_result(lease.task_id, lease.lease_id, _hmac(lease.task_id))["state"] == "COMPLETED"
    assert storage.interleaved


def test_toctou_floor_raised_between_precheck_and_txn_is_enforced(env, racing):
    storage, main, other = racing
    env.setenv(MODE_ENV, "audit")
    lease = _to_verifying(main, "keyc-l2b-race-raise")
    storage.before_next_begin = lambda: _raise(other)
    _assert_hmac_refused(main, lease.task_id, lease)
    assert storage.interleaved  # the pre-check passed in audit; only the in-txn floor read refused it


# ------------------------------------------------------------------------------ cutover tool
def _cutover(capsys, *argv: str) -> tuple[int, dict]:
    tool = importlib.import_module("tools.receipt_authority_cutover")
    rc = tool.main(list(argv))
    lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    return rc, json.loads(lines[-1])


def _floor_state(db_path) -> tuple[str, bool, int]:
    k = TaskKernel(db_path)
    try:
        health = _floor_module().receipt_floor_health(k)
        return health["receipt_mode_floor"], health["receipt_mode_floor_sig_valid"], len(_raised_events(k))
    finally:
        k.close()


def test_cutover_refuses_without_keyring(env, db, capsys):
    TaskKernel(db).close()
    env.delenv("SCP_VERIFIER_PUBLIC_KEYS")
    rc, summary = _cutover(capsys, "--db-path", str(db), "--skip-verifier-check")
    assert (rc, summary["status"]) == (2, "REFUSED")
    assert "keyring" in summary["reason"].lower()
    assert _floor_state(db) == ("audit", True, 0)


def test_cutover_refuses_keyring_without_a_usable_active_key(env, db, key_c, capsys):
    TaskKernel(db).close()
    env.setenv("SCP_VERIFIER_PUBLIC_KEYS", _ring(key_c, not_after=time.time() - 3600))  # active but expired
    rc, summary = _cutover(capsys, "--db-path", str(db), "--skip-verifier-check")
    assert (rc, summary["status"]) == (2, "REFUSED")
    assert "active" in summary["reason"].lower()
    assert _floor_state(db) == ("audit", True, 0)


def test_cutover_refuses_without_verifier_check_or_explicit_skip(env, db, capsys):
    TaskKernel(db).close()
    rc, summary = _cutover(capsys, "--db-path", str(db))
    assert (rc, summary["status"]) == (2, "REFUSED")
    assert "verifier" in summary["reason"].lower()
    assert _floor_state(db) == ("audit", True, 0)


def test_cutover_refuses_unreachable_verifier(env, db, tmp_path, capsys):
    TaskKernel(db).close()
    token_file = tmp_path / "ipc.token"
    token_file.write_text(secrets.token_hex(24), encoding="ascii")
    address = (f"\\\\.\\pipe\\scp-l2b-absent-{secrets.token_hex(6)}" if os.name == "nt"
               else str(tmp_path / "absent.sock"))
    rc, summary = _cutover(capsys, "--db-path", str(db), "--verifier-pipe", address, "--ipc-token-file", str(token_file))
    assert (rc, summary["status"]) == (2, "REFUSED")
    assert "unreachable" in summary["reason"].lower()
    assert _floor_state(db) == ("audit", True, 0)


def test_cutover_refuses_missing_database(env, tmp_path, capsys):
    missing = tmp_path / "nope.sqlite3"
    rc, summary = _cutover(capsys, "--db-path", str(missing), "--skip-verifier-check")
    assert (rc, summary["status"]) == (2, "REFUSED")
    assert not missing.exists()  # the tool never creates an empty kernel DB


def test_cutover_with_keyring_and_explicit_skip_completes_and_is_idempotent(env, db, capsys):
    TaskKernel(db).close()
    rc, summary = _cutover(capsys, "--db-path", str(db), "--skip-verifier-check", "--actor", "keyc-l2b-owner")
    assert (rc, summary["status"]) == (0, "COMPLETED"), summary
    assert summary["preflight"]["verifier"]["status"] == "SKIPPED"
    assert summary["preflight"]["keyring"]["active_kids"]
    after = summary["health"]
    assert (after["receipt_mode_floor"], after["receipt_mode_floor_sig_valid"]) == ("enforce", True)
    assert after["receipt_mode_floor_raise_journaled"] is True
    assert after["receipt_mode_floor_state"] == "ENFORCED"
    assert _floor_state(db) == ("enforce", True, 1)

    rc2, summary2 = _cutover(capsys, "--db-path", str(db), "--skip-verifier-check")
    assert (rc2, summary2["status"]) == (0, "COMPLETED")
    assert _floor_state(db) == ("enforce", True, 1)

    env.setenv(MODE_ENV, "audit")
    k = TaskKernel(db)
    try:
        lease = _to_verifying(k, "keyc-l2b-after-cutover")
        _assert_hmac_refused(k, lease.task_id, lease)
    finally:
        k.close()


def test_cutover_check_mode_reports_health_and_tamper_without_writing(env, db, capsys):
    TaskKernel(db).close()
    rc, summary = _cutover(capsys, "--db-path", str(db), "--check")
    assert (rc, summary["status"]) == (2, "NOT_ENFORCED")
    assert _floor_state(db) == ("audit", True, 0)  # check mode never raises the floor

    assert _cutover(capsys, "--db-path", str(db), "--skip-verifier-check")[0] == 0
    rc, summary = _cutover(capsys, "--db-path", str(db), "--check")
    assert (rc, summary["status"]) == (0, "ENFORCED")

    # Replayed (validly signed) audit row: the floor stays enforce via the journal, but health says tamper.
    _set_raw(db, FLOOR, "audit")
    _set_raw(db, FLOOR_SIG, compute_control_flag_sig(FLOOR, "audit"))
    rc, summary = _cutover(capsys, "--db-path", str(db), "--check")
    assert (rc, summary["status"]) == (1, "INCONSISTENT")
    assert summary["health"]["receipt_mode_floor"] == "enforce"
    # Value restored without a valid signature: still flagged.
    _set_raw(db, FLOOR, "enforce")
    rc, summary = _cutover(capsys, "--db-path", str(db), "--check")
    assert (rc, summary["status"]) == (1, "INCONSISTENT")
    assert summary["health"]["receipt_mode_floor_sig_valid"] is False


def _keygen(out: Path) -> dict:
    proc = subprocess.run([PY, str(ROOT / "tools" / "verifier_keygen.py"), "--out-dir", str(out / "daemon_only"),
                           "--keyring-out", str(out / "keyring_public.json")],
                          capture_output=True, text=True, cwd=str(ROOT), timeout=120)
    assert proc.returncode == 0, proc.stderr
    info = json.loads(proc.stdout)
    info["keyring_text"] = (out / "keyring_public.json").read_text(encoding="utf-8")
    return info


def test_cutover_with_live_verifier_daemon_over_pipe(env, db, tmp_path, capsys):
    TaskKernel(db).close()
    keys = _keygen(tmp_path / "keys")
    token = secrets.token_hex(24)
    token_file = tmp_path / "ipc.token"
    token_file.write_text(token, encoding="ascii")
    address = (f"\\\\.\\pipe\\scp-l2b-verifier-{secrets.token_hex(6)}" if os.name == "nt"
               else str(tmp_path / "verifier.sock"))
    ready = tmp_path / "ready"
    child_env = {k: v for k, v in os.environ.items() if k.upper() in {"SYSTEMROOT", "PATH", "TEMP", "TMP"}}
    child_env.update({"PYTHONPATH": str(ROOT), "SCP_JOURNAL_SECRET": _JOURNAL,
                      "SCP_VERIFIER_PRIVATE_KEY_FILE": keys["private_key_file"]})
    proc = subprocess.Popen([PY, "-m", "scp.verifier_service", "--transport", "pipe", "--db", str(db),
                             "--audit-log", str(tmp_path / "audit.jsonl"), "--ipc-token-file", str(token_file),
                             "--pipe-address", address, "--ready-file", str(ready)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, env=child_env, cwd=str(ROOT))
    try:
        deadline = time.monotonic() + 60
        while not ready.exists() and proc.poll() is None and time.monotonic() < deadline:
            time.sleep(0.1)
        assert ready.exists(), proc.stderr.read().decode("utf-8", "replace") if proc.poll() is not None else ""
        args = ("--db-path", str(db), "--verifier-pipe", address, "--ipc-token-file", str(token_file))

        env.setenv("SCP_VERIFIER_PUBLIC_KEYS", _ring(Ed25519PrivateKey.generate()))  # daemon key NOT trusted
        rc, summary = _cutover(capsys, *args)
        assert (rc, summary["status"]) == (2, "REFUSED")
        assert keys["kid"] in summary["reason"]
        assert _floor_state(db) == ("audit", True, 0)

        env.setenv("SCP_VERIFIER_PUBLIC_KEYS", keys["keyring_text"])
        rc, summary = _cutover(capsys, *args)
        assert (rc, summary["status"]) == (0, "COMPLETED"), summary
        assert summary["preflight"]["verifier"] == {"status": "REACHABLE", "kid": keys["kid"]}
        assert _floor_state(db) == ("enforce", True, 1)
    finally:
        proc.kill()
        proc.wait(timeout=10)
