"""Autofix Shadow Snapshot Crash Recovery Integration Tests (CE-S09-03 / D-Level).

Proves:
- Process crash mid-patch leaves abandoned transaction in data/shadow/active.
- On reboot / engine startup, recover_abandoned_transactions restores files to exact pre-patch SHA256.
- Transaction moves to data/shadow/rolled_back with durable failure reason.
- Corrupt manifest entries are quarantined without leaking active state.
- No duplicate commit or file leakage occurs.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from scp.autofix.shadow_snapshot import ShadowSnapshotManager, get_shadow_snapshot_manager


def test_shadow_snapshot_crash_recovery_restores_pre_patch_files(tmp_path: Path) -> None:
    """CE-S09-03: Process crash during self-change -> startup rollback restores exact pre-patch files."""
    shadow_dir = tmp_path / "shadow"
    work_dir = tmp_path / "workspace"
    work_dir.mkdir(parents=True, exist_ok=True)

    file_a = work_dir / "target_a.py"
    file_b = work_dir / "target_b.py"
    content_a = b"def original_a(): return 42\n"
    content_b = b"class OriginalB:\n    value = 'safe'\n"
    file_a.write_bytes(content_a)
    file_b.write_bytes(content_b)

    sha_a = hashlib.sha256(content_a).hexdigest()
    sha_b = hashlib.sha256(content_b).hexdigest()

    # Step 1: Simulated worker begins shadow transaction
    mgr_pre = ShadowSnapshotManager(shadow_dir=shadow_dir)
    tx_id = mgr_pre.begin([file_a, file_b], bug_id="bug_simulated_crash_1")

    # Step 2: In-flight patch modifies files
    file_a.write_bytes(b"def mutated_a(): raise RuntimeError('corrupted')\n")
    file_b.write_bytes(b"class MutatedB: broken = True\n")
    assert file_a.read_bytes() != content_a
    assert file_b.read_bytes() != content_b

    # Step 3: Worker process crashes abruptly (simulated by non-existent PID in manifest)
    manifest_path = shadow_dir / "active" / tx_id / "manifest.json"
    manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_data["pid"] = 99999999  # Guaranteed non-existent PID
    manifest_path.write_text(json.dumps(manifest_data), encoding="utf-8")

    # Step 4: System restarts and runs abandoned transaction recovery
    mgr_post = ShadowSnapshotManager(shadow_dir=shadow_dir)
    recovered = mgr_post.recover_abandoned_transactions()

    # Assertions
    assert tx_id in recovered
    assert file_a.read_bytes() == content_a
    assert file_b.read_bytes() == content_b
    assert hashlib.sha256(file_a.read_bytes()).hexdigest() == sha_a
    assert hashlib.sha256(file_b.read_bytes()).hexdigest() == sha_b

    # Transaction moved to rolled_back
    assert not (shadow_dir / "active" / tx_id).exists()
    dest_tx = shadow_dir / "rolled_back" / tx_id
    assert dest_tx.is_dir()
    rolled_manifest = json.loads((dest_tx / "manifest.json").read_text(encoding="utf-8"))
    assert rolled_manifest["status"] == "ROLLED_BACK"
    assert rolled_manifest["rollback_reason"] == "CRASH_RECOVERY_ABANDONED_TRANSACTION"
    assert (dest_tx / "failure_reason.txt").is_file()


def test_shadow_snapshot_quarantines_corrupt_manifest_without_leaking(tmp_path: Path) -> None:
    """CE-S09-03: Corrupt or truncated manifest from crash is safely quarantined to rolled_back."""
    shadow_dir = tmp_path / "shadow_corrupt"
    mgr = ShadowSnapshotManager(shadow_dir=shadow_dir)

    # Fabricate a corrupt active transaction (e.g. power loss during write)
    corrupt_tx_dir = shadow_dir / "active" / "tx_corrupt_crash_999"
    corrupt_tx_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = corrupt_tx_dir / "manifest.json"
    manifest_path.write_text("{ incomplete_json: truncated...", encoding="utf-8")

    recovered = mgr.recover_abandoned_transactions()

    # Corrupt transaction should be quarantined out of active_dir
    assert not corrupt_tx_dir.exists()
    dest_dir = shadow_dir / "rolled_back" / "tx_corrupt_crash_999"
    assert dest_dir.is_dir()
    assert (dest_dir / "failure_reason.txt").is_file()
    assert "CORRUPT_MANIFEST" in (dest_dir / "failure_reason.txt").read_text(encoding="utf-8")


def test_shadow_snapshot_committed_transactions_remain_durable_across_crash(tmp_path: Path) -> None:
    """CE-S09-03: Committed transactions in completed/ are never modified by recovery sweeps."""
    shadow_dir = tmp_path / "shadow_commit"
    work_dir = tmp_path / "workspace_c"
    work_dir.mkdir(parents=True, exist_ok=True)
    target = work_dir / "committed_file.py"
    target.write_bytes(b"# clean file\n")

    mgr = ShadowSnapshotManager(shadow_dir=shadow_dir)
    tx_id = mgr.begin([target], bug_id="bug_legit_fix")
    target.write_bytes(b"# improved file\n")
    committed = mgr.commit(tx_id)
    assert committed is True
    assert (shadow_dir / "completed" / tx_id).is_dir()

    # Sweep recovery
    recovered = mgr.recover_abandoned_transactions()
    assert tx_id not in recovered
    assert (shadow_dir / "completed" / tx_id).is_dir()
    assert target.read_bytes() == b"# improved file\n"


def test_shadow_snapshot_quarantines_missing_manifest_transaction_without_leaking(tmp_path: Path) -> None:
    """CE-S09-03: Active transaction directory without manifest (crashed during begin) is quarantined to rolled_back."""
    shadow_dir = tmp_path / "shadow_missing"
    mgr = ShadowSnapshotManager(shadow_dir=shadow_dir)

    # Simulate crash before manifest.json was written
    orphaned_tx = shadow_dir / "active" / "tx_missing_manifest_crash_1"
    orphaned_tx.mkdir(parents=True, exist_ok=True)
    (orphaned_tx / "files").mkdir(parents=True, exist_ok=True)
    (orphaned_tx / "files" / "abandoned_payload.bak").write_bytes(b"abandoned data")

    assert orphaned_tx.is_dir()

    # Recovery sweep must quarantine orphaned directory out of active/
    recovered = mgr.recover_abandoned_transactions()
    assert "tx_missing_manifest_crash_1" not in recovered
    assert not orphaned_tx.exists()

    # Quarantined into rolled_back with failure reason
    quarantined = shadow_dir / "rolled_back" / "tx_missing_manifest_crash_1"
    assert quarantined.is_dir()
    assert (quarantined / "failure_reason.txt").is_file()
    assert "MISSING_MANIFEST_CRASH_RECOVERY" in (quarantined / "failure_reason.txt").read_text(encoding="utf-8")

