import pytest

from scp.task_kernel import KernelError, TaskKernel

# ==============================================================================
# T04 - KERNEL CRASH CONSISTENCY (Bước 0.9 residue: crash injection)
# ==============================================================================
# Simulates the two most dangerous crash windows of the event-sourcing kernel:
#   1. event appended to the journal, process died BEFORE the projection update
#      -> journal is authoritative, rebuild_projection must repair the state;
#   2. garbage/tampered event row in the journal
#      -> fail-closed: rebuild refuses, recovery reports corruption.
# The journal (not the tasks table) is the source of truth.
# ==============================================================================


def _fresh_kernel(tmp_path):
    return TaskKernel(str(tmp_path / "kernel.sqlite3"))


def test_crash_between_event_and_projection_is_repaired_by_rebuild(tmp_path):
    """Event lands, projection update never happens -> rebuild repairs, journal intact."""
    kernel = _fresh_kernel(tmp_path)
    kernel.create_task("crash-1", "owner", "crash window probe", "R1")

    # Simulate the crash window: the event is appended to the journal but the
    # projection UPDATE (inside transition()) never runs because the process
    # dies in between.
    kernel._append_event(
        "crash-1", "TRANSITION", "CREATED", "PLANNING",
        actor="crash-simulator", reason="died_between_event_and_projection",
    )

    reopened = _fresh_kernel(tmp_path)
    task_before = reopened.get_task("crash-1")
    assert task_before["state"] == "CREATED", "Projection must still show the stale state before rebuild"
    assert reopened.verify_journal("crash-1")["hash_chain_valid"] is True

    repaired = reopened.rebuild_projection("crash-1")
    assert repaired["state"] == "PLANNING", (
        "Rebuild must derive the authoritative state from the journal"
    )
    assert reopened.verify_journal("crash-1")["hash_chain_valid"] is True
    # Kernel stays usable after repair: a legal transition from the repaired state works.
    reopened.transition("crash-1", "READY", actor="crash-test", reason="post_repair")
    assert reopened.get_task("crash-1")["state"] == "READY"


def test_tampered_journal_is_fail_closed(tmp_path):
    """A tampered/garbage event breaks the hash chain -> rebuild refuses, fail-closed."""
    kernel = _fresh_kernel(tmp_path)
    kernel.create_task("tamper-1", "owner", "tamper probe", "R1")
    kernel._append_event(
        "tamper-1", "TRANSITION", "CREATED", "PLANNING",
        actor="crash-simulator", reason="legit_event_then_tamper",
    )

    # Forge history: rewrite an event payload directly (bypassing the kernel API).
    # No manual commit - the storage wrapper owns transactions and the same
    # thread-local connection must observe the tampered row.
    kernel.conn.execute(
        "UPDATE events SET payload_json=? WHERE task_id=?",
        ('{"tampered": true}', "tamper-1"),
    )

    journal = kernel.verify_journal("tamper-1")
    assert journal["hash_chain_valid"] is False, "Tampered journal must fail verification"

    with pytest.raises(KernelError):
        kernel.rebuild_projection("tamper-1")

    report = kernel.recover_on_boot(actor="crash-parent")
    assert any(entry["task_id"] == "tamper-1" for entry in report["corrupted"]), (
        f"Recovery must report the corrupted journal instead of silently repairing it: {report}"
    )


def test_hmac_secret_refuses_unkeyed_stable_hash_journal(tmp_path, monkeypatch):
    """M-11 / Anti-Placebo: When SCP_CAPABILITY_SECRET is configured, forging event_hash with unkeyed stable_hash fails-closed."""
    from scp.task_kernel_parts.definitions import stable_hash
    monkeypatch.setenv("SCP_CAPABILITY_SECRET", "super-secret-capability-key-12345")

    kernel = _fresh_kernel(tmp_path)
    kernel.create_task("hmac-probe-1", "owner", "hmac tamper probe", "R1")

    # Fetch event row
    row = kernel.conn.execute("SELECT * FROM events WHERE task_id='hmac-probe-1' AND seq=1").fetchone()
    assert row is not None

    body = {
        'event_id': row['event_id'],
        'task_id': row['task_id'],
        'seq': row['seq'],
        'type': row['type'],
        'from_state': row['from_state'],
        'to_state': row['to_state'],
        'actor': row['actor'],
        'reason': row['reason'],
        'payload': {},
        'policy_hash': row['policy_hash'],
        'prev_event_hash': row['prev_event_hash'],
    }
    # Unkeyed legacy hash
    unkeyed_hash = stable_hash(body)

    # Overwrite the HMAC hash with the unkeyed hash in SQLite
    kernel.conn.execute(
        "UPDATE events SET event_hash=? WHERE task_id='hmac-probe-1' AND seq=1",
        (unkeyed_hash,),
    )

    # Must fail-closed because SCP_CAPABILITY_SECRET is set
    journal = kernel.verify_journal("hmac-probe-1")
    assert journal["hash_chain_valid"] is False, "Unkeyed hash must be rejected when SCP_CAPABILITY_SECRET is set"
    assert "event_hash:1" in journal["errors"]
