"""R5 · K_journal: the journal/checkpoint key is separate from the capability key.

Before R5 one SCP_CAPABILITY_SECRET minted approval tokens, signed verifier receipts and
HMAC-chained the kernel journal, so whoever held it could rewrite who approved a task and
still pass verify_journal. Checkpoint hashes used the same key and serialization as journal
events, so a caller-supplied planned_action could be turned into a valid event_hash.

Zero-mock: every test runs a real TaskKernel on a SQLite file under tmp_path; attacks are
performed with a second sqlite3 connection, exactly as a DB-write attacker would.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3

import pytest

from scp.task_kernel import KernelError, TaskKernel
from scp.task_kernel_parts.definitions import journal_event_body

_CAPABILITY = "r5-capability-secret-must-not-sign-journal-32b"
_JOURNAL = "r5-journal-secret-K_journal-distinct-value-32bytes"
_JOURNAL_PREFIX = b"scp.journal.v1\x00"


@pytest.fixture
def keys(monkeypatch):
    monkeypatch.setenv("SCP_CAPABILITY_SECRET", _CAPABILITY)
    monkeypatch.setenv("SCP_JOURNAL_SECRET", _JOURNAL)
    for name in ("SCP_PREVIOUS_CAPABILITY_SECRETS", "SCP_PREVIOUS_JOURNAL_SECRETS", "SCP_JOURNAL_SECRET_FILE"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def db(tmp_path, keys):
    return tmp_path / "r5_kernel.sqlite3"


def _approved_task(kernel: TaskKernel, task_id: str) -> None:
    kernel.create_task(task_id, "r5-owner", "goal", "R1", input_hash="sha256:" + "0" * 64)
    kernel.transition(task_id, "PLANNING", actor="planner", reason="r5")
    kernel.transition(task_id, "READY", actor="original-actor", reason="r5")


def _hmac(key: bytes, message: bytes) -> str:
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def _rechain(db_path, task_id: str, key: bytes, prefix: bytes = b"", new_actor: str | None = None) -> None:
    """Rewrite one task's journal under ``key`` (optionally changing the READY actor)."""
    side = sqlite3.connect(db_path)
    side.row_factory = sqlite3.Row
    try:
        events = [dict(e) for e in side.execute("SELECT * FROM events WHERE task_id=? ORDER BY seq", (task_id,))]
        prev = None
        for e in events:
            payload = json.loads(e["payload_json"])
            if new_actor and e["to_state"] == "READY":
                e["actor"] = new_actor
            e["prev_event_hash"] = prev
            body = journal_event_body(e, payload)
            digest = _hmac(key, prefix + json.dumps(body, sort_keys=True).encode())
            side.execute(
                "UPDATE events SET actor=?, prev_event_hash=?, event_hash=? WHERE event_id=?",
                (e["actor"], prev, digest, e["event_id"]),
            )
            prev = digest
        side.execute("UPDATE control SET journal_head_hash=? WHERE id=1", (prev,))
        side.commit()
    finally:
        side.close()


def _set_control(db_path, column: str, value: int) -> None:
    side = sqlite3.connect(db_path)
    try:
        side.execute(f"UPDATE control SET {column}=? WHERE id=1", (value,))
        side.commit()
    finally:
        side.close()


def test_kernel_refuses_to_journal_without_k_journal(tmp_path, monkeypatch):
    monkeypatch.setenv("SCP_CAPABILITY_SECRET", _CAPABILITY)
    monkeypatch.delenv("SCP_JOURNAL_SECRET", raising=False)
    monkeypatch.delenv("SCP_JOURNAL_SECRET_FILE", raising=False)
    kernel = TaskKernel(tmp_path / "no_journal_key.sqlite3")
    try:
        with pytest.raises(KernelError):
            kernel.create_task("r5-no-key", "r5-owner", "goal", "R0", input_hash="sha256:" + "0" * 64)
    finally:
        kernel.close()


def test_capability_secret_cannot_rewrite_journal_on_a_fresh_db(db):
    kernel = TaskKernel(db)
    try:
        _approved_task(kernel, "r5-rewrite")
        _rechain(db, "r5-rewrite", _CAPABILITY.encode(), new_actor="human-operator")
        assert kernel.verify_journal("r5-rewrite")["hash_chain_valid"] is False
        _rechain(db, "r5-rewrite", _CAPABILITY.encode(), prefix=_JOURNAL_PREFIX, new_actor="human-operator")
        assert kernel.verify_journal("r5-rewrite")["hash_chain_valid"] is False
    finally:
        kernel.close()


def test_k_journal_holder_still_verifies_and_new_db_is_already_cut_over(db):
    kernel = TaskKernel(db)
    try:
        _approved_task(kernel, "r5-clean")
        result = kernel.verify_journal("r5-clean")
        assert result["hash_chain_valid"] is True
        assert result["capability_key_events"] == 0
        assert kernel._control()["journal_key_migrated"] == 1
    finally:
        kernel.close()


def test_checkpoint_signature_cannot_be_replayed_as_journal_event_hash(db):
    kernel = TaskKernel(db)
    try:
        # Victim: an approved task whose last journal event the attacker wants to rewrite.
        victim = "r5-oracle-victim"
        _approved_task(kernel, victim)
        last = dict(kernel.get_events(victim)[-1])
        forged = dict(last, actor="human-operator")
        forged_body = journal_event_body(forged, json.loads(last["payload_json"]))
        # Oracle: a worker on another task gets the kernel to sign that body as a planned_action.
        worker_task = "r5-oracle-worker"
        kernel.create_task(worker_task, "r5-owner", "goal", "R0", input_hash="sha256:" + "1" * 64)
        for state in ("PLANNING", "READY", "QUEUED"):
            kernel.transition(worker_task, state, actor="r5-scheduler", reason="r5")
        lease = kernel.claim(worker_task, "r5-worker", ttl_seconds=120)
        kernel.start(worker_task, lease.lease_id)
        checkpoint_id = kernel.checkpoint(
            worker_task, lease.lease_id, "step-oracle", "RUNNING", forged_body,
            capability_epoch=1, idempotency_key="idem-oracle",
        )
        task_id = victim
        side = sqlite3.connect(db)
        try:
            (signed,) = side.execute(
                "SELECT planned_action_hash FROM checkpoints WHERE checkpoint_id=?", (checkpoint_id,)
            ).fetchone()
            side.execute(
                "UPDATE events SET actor=?, event_hash=? WHERE event_id=?",
                ("human-operator", signed, last["event_id"]),
            )
            side.commit()
        finally:
            side.close()
        assert kernel.verify_journal(task_id)["hash_chain_valid"] is False
    finally:
        kernel.close()


def test_pre_r5_journal_migrates_to_k_journal_with_evidence_then_window_closes(db):
    kernel = TaskKernel(db)
    try:
        _approved_task(kernel, "r5-legacy")
        # Simulate a pre-R5 database: journal chained under the capability key, window open.
        _rechain(db, "r5-legacy", _CAPABILITY.encode())
        _set_control(db, "journal_key_migrated", 0)
        before = kernel.verify_journal("r5-legacy")
        assert before["hash_chain_valid"] is True
        assert before["capability_key_events"] == before["event_count"]

        migration = kernel.reanchor_all_journals()
        assert migration["failed_tasks"] == []
        assert kernel._control()["journal_key_migrated"] == 1
        reanchored = [e for e in kernel.get_events("r5-legacy") if e["type"] == "REANCHORED"]
        assert len(reanchored) == 1
        assert reanchored[0]["reason"] == "journal_key_migration"
        after = kernel.verify_journal("r5-legacy")
        assert after["hash_chain_valid"] is True
        assert after["capability_key_events"] == 0

        # Cutover: the capability key no longer validates a journal, legacy format or not.
        _rechain(db, "r5-legacy", _CAPABILITY.encode(), new_actor="human-operator")
        assert kernel.verify_journal("r5-legacy")["hash_chain_valid"] is False
    finally:
        kernel.close()


def test_runtime_audit_checks_hmac_with_k_journal_not_capability_key(db):
    from scp.runtime_audit.probes.kernel import JournalAnchorProbe

    kernel = TaskKernel(db)
    try:
        _approved_task(kernel, "r5-audit")
    finally:
        kernel.close()

    def hmac_failed(secret: str) -> list:
        (result,) = JournalAnchorProbe([db], journal_secret=secret).run()
        return result.details["hmac_failed"]

    assert hmac_failed(_JOURNAL) == []
    assert hmac_failed(_CAPABILITY) != []


# ---------------------------------------------------------------------------
# Checker follow-up: surviving mutants and the cutover/checkpoint regression
# ---------------------------------------------------------------------------

def _running(kernel: TaskKernel, task_id: str):
    kernel.create_task(task_id, "r5-owner", "goal", "R0", input_hash="sha256:" + "0" * 64)
    for state in ("PLANNING", "READY", "QUEUED"):
        kernel.transition(task_id, state, actor="r5-scheduler", reason="r5")
    lease = kernel.claim(task_id, "r5-worker", ttl_seconds=120)
    kernel.start(task_id, lease.lease_id)
    return lease


def test_checkpoint_rewritten_with_capability_key_is_rejected_after_cutover(db):
    kernel = TaskKernel(db)
    try:
        lease = _running(kernel, "r5-cp-forge")
        planned = {"action": "click", "target": "#pay"}
        cp_id = kernel.checkpoint("r5-cp-forge", lease.lease_id, "s1", "RUNNING", planned,
                                  capability_epoch=1, idempotency_key="idem-cp-forge")
        assert kernel.validate_checkpoint(cp_id, planned)
        forged = _hmac(_CAPABILITY.encode(), json.dumps(planned, sort_keys=True).encode())
        side = sqlite3.connect(db)
        try:
            side.execute("UPDATE checkpoints SET planned_action_hash=? WHERE checkpoint_id=?", (forged, cp_id))
            side.commit()
        finally:
            side.close()
        with pytest.raises(KernelError):
            kernel.validate_checkpoint(cp_id, planned)
    finally:
        kernel.close()


def test_reanchor_refuses_a_capability_key_chain_after_cutover(db):
    kernel = TaskKernel(db)
    try:
        _approved_task(kernel, "r5-reanchor-forge")
        _rechain(db, "r5-reanchor-forge", _CAPABILITY.encode(), new_actor="human-operator")
        with pytest.raises(KernelError):
            kernel.reanchor_journal("r5-reanchor-forge")
        assert kernel.verify_journal("r5-reanchor-forge")["hash_chain_valid"] is False
    finally:
        kernel.close()


def test_pre_r5_schema_upgrade_opens_the_dual_accept_window(db):
    kernel = TaskKernel(db)
    kernel.close()
    side = sqlite3.connect(db)
    try:
        side.execute("ALTER TABLE control DROP COLUMN journal_key_migrated")  # pre-R5 control table
        side.commit()
    finally:
        side.close()
    upgraded = TaskKernel(db)
    try:
        assert upgraded._control()["journal_key_migrated"] == 0
    finally:
        upgraded.close()


def test_cutover_waits_until_no_live_task_owns_a_checkpoint(db):
    kernel = TaskKernel(db)
    try:
        lease = _running(kernel, "r5-live-cp")
        kernel.checkpoint("r5-live-cp", lease.lease_id, "s1", "RUNNING", {"action": "noop"},
                          capability_epoch=1, idempotency_key="idem-live-cp")
        _set_control(db, "journal_key_migrated", 0)
        first = kernel.reanchor_all_journals()
        assert first["journal_key_cutover_blocked_by"] == ["r5-live-cp"]
        assert first["status"] == "PARTIAL"
        assert kernel._control()["journal_key_migrated"] == 0
        kernel.set_task_kill("r5-live-cp")
        second = kernel.reanchor_all_journals()
        assert second["journal_key_cutover_blocked_by"] == []
        assert kernel._control()["journal_key_migrated"] == 1
    finally:
        kernel.close()


def test_retired_journal_keys_must_not_reuse_the_capability_key(db, monkeypatch):
    kernel = TaskKernel(db)
    try:
        _approved_task(kernel, "r5-prev-reuse")
        monkeypatch.setenv("SCP_PREVIOUS_JOURNAL_SECRETS", f"some-old-key,{_CAPABILITY}")
        _rechain(db, "r5-prev-reuse", _CAPABILITY.encode(), prefix=_JOURNAL_PREFIX, new_actor="human-operator")
        with pytest.raises(KernelError, match="must not reuse a value of SCP_CAPABILITY_SECRET or SCP_PREVIOUS_CAPABILITY_SECRETS"):
            kernel.verify_journal("r5-prev-reuse")
    finally:
        kernel.close()
