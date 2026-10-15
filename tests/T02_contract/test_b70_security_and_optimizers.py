"""
Contract Tests for B70 Security, Hardening & Optimizers.
Covers:
- F-01: Task journal HMAC backward-compatibility & re-anchoring.
- F-02: Dashboard 127.0.0.1 loopback binding enforcement.
- F-03: Safe shutdown hygiene (no indiscriminate taskkill of electron.exe).
- N-01: AutoWebIngestor fail-closed on missing SCP_CAPABILITY_SECRET.
- Opt-5: Google SimHash 64-bit + Hamming distance deduplication.
- N-10: Capability token is_safe_path_prefix traversal & prefix confusion defense.
- N-08: SDK offline verification fail-closed when ungrounded.
- N-04: Canonical FTS warehouse fallback to unverified_local_drop.
- N-07: HKDF RFC 5869 Key Hierarchy for 5 core subsystems.
- Opt-1 & Opt-2: ShardedPgKernelLockAndClaim and SkipLockedPgEventBusLease contracts.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import pytest

from scp.core.capability_token import (
    MacaroonCapabilityToken,
    CapabilitySecurityError,
    is_safe_path_prefix,
)
from scp.data_sources.auto_web_ingestor import (
    AutoWebIngestor,
    compute_simhash64,
    hamming_distance64,
)
from scp.security.hkdf_key_hierarchy import HkdfKeyHierarchy, SubsystemContext
from scp.kernel_storage_pg import ShardedPgKernelLockAndClaim
from scp.event_bus_pg import SkipLockedPgEventBusLease
from scp.knowledge.canonical_fts_warehouse import CanonicalFTSWarehouse
from sdk.python.scp_sdk.protect import SCPProtect, VerificationTier


# --------------------------------------------------------------------------- #
# F-01: Journal HMAC & Re-anchoring                                           #
# --------------------------------------------------------------------------- #
def test_f01_task_journal_legacy_hash_and_reanchor_contract() -> None:
    from scp.task_kernel import TaskKernel
    from scp.task_kernel_parts.definitions import stable_hash

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test_kernel.sqlite3"
        kernel = TaskKernel(str(db_path))
        try:
            tid = "t_f01_001"
            kernel.create_task(tid, "tester", "test_goal", "R1")
            kernel.transition(tid, "PLANNING", actor="setup")

            # Verify journal initially valid
            v = kernel.verify_journal(tid)
            assert v["hash_chain_valid"] is True

            # Emulate pre-B55 legacy chain: recompute all events with stable_hash
            conn = kernel.conn
            events = kernel.get_events(tid)
            prev = None
            kernel._begin()
            for e in events:
                body = {
                    "event_id": e["event_id"],
                    "task_id": e["task_id"],
                    "seq": e["seq"],
                    "type": e["type"],
                    "from_state": e["from_state"],
                    "to_state": e["to_state"],
                    "actor": e["actor"],
                    "reason": e["reason"],
                    "payload": json.loads(e["payload_json"]),
                    "policy_hash": e["policy_hash"],
                    "prev_event_hash": prev,
                }
                legacy_hash = stable_hash(body)
                conn.execute(
                    "UPDATE events SET prev_event_hash = ?, event_hash = ? WHERE event_id = ?",
                    (prev, legacy_hash, e["event_id"]),
                )
                prev = legacy_hash
            kernel._commit()

            # Verify backward compatibility (F-01 fix: legacy hash passes)
            v_legacy = kernel.verify_journal(tid)
            assert v_legacy["hash_chain_valid"] is True, f"Legacy journal should verify cleanly: {v_legacy}"

            # Re-anchor journal to modern HMAC
            reanchor_res = kernel.reanchor_journal(tid)
            assert reanchor_res["reanchored"] >= 1
            assert reanchor_res["status"] == "REANCHORED"

            # Verify post-reanchor is strictly valid
            v_post = kernel.verify_journal(tid)
            assert v_post["hash_chain_valid"] is True
        finally:
            kernel.close()


# --------------------------------------------------------------------------- #
# F-02: Dashboard Loopback Binding Contract                                   #
# --------------------------------------------------------------------------- #
def test_f02_dashboard_binds_loopback_only() -> None:
    # 1. dashboard/package.json must bind 127.0.0.1
    pkg = json.loads(Path("dashboard/package.json").read_text(encoding="utf-8"))
    assert "-H 127.0.0.1" in pkg["scripts"]["dev"]
    assert "start-standalone.js" in pkg["scripts"]["start"]

    # 2. dashboard/scripts/start-standalone.js must force 127.0.0.1
    standalone_js = Path("dashboard/scripts/start-standalone.js").read_text(encoding="utf-8")
    assert 'process.env.HOSTNAME = "127.0.0.1"' in standalone_js
    assert 'process.env.PORT = "3000"' in standalone_js

    # 3. launch.bat must set HOSTNAME=127.0.0.1
    bat = Path("launch.bat").read_text(encoding="utf-8")
    assert "set HOSTNAME=127.0.0.1" in bat

    # 4. start_scp_silent.ps1 must set $env:HOSTNAME = "127.0.0.1"
    ps1 = Path("scripts/ops/start_scp_silent.ps1").read_text(encoding="utf-8")
    assert '$env:HOSTNAME = "127.0.0.1"' in ps1

    # 5. start-scp.sh must specify 127.0.0.1
    sh = Path("start-scp.sh").read_text(encoding="utf-8")
    assert 'DASHBOARD_HOST="${DASHBOARD_HOST:-127.0.0.1}"' in sh


# --------------------------------------------------------------------------- #
# F-03: Safe Process Termination Hygiene                                      #
# --------------------------------------------------------------------------- #
def test_f03_launch_bat_does_not_taskkill_arbitrary_electron() -> None:
    bat = Path("launch.bat").read_text(encoding="utf-8")
    assert "taskkill /f /t /im electron.exe" not in bat.lower()
    assert "taskkill /im electron.exe" not in bat.lower()


# --------------------------------------------------------------------------- #
# N-01: AutoWebIngestor Fail-Closed On Missing Secret                          #
# --------------------------------------------------------------------------- #
def test_n01_auto_web_ingestor_missing_secret_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SCP_CAPABILITY_SECRET", raising=False)
    with tempfile.TemporaryDirectory() as tmp:
        wh = CanonicalFTSWarehouse(db_path=Path(tmp) / "fts.sqlite", documents_dir=Path(tmp) / "empty")
        ingestor = AutoWebIngestor(warehouse=wh, quarantine_db_path=Path(tmp) / "q.sqlite")
        try:
            # Stage a record from a trusted domain
            ingestor.stage_from_url(
                topic_key="topic_01",
                domain="Legal",
                title="Law Title",
                url="https://vbpl.vn/law",
                raw_content="Content with number 100 and clean text",
            )
            # Promoting to warehouse without SCP_CAPABILITY_SECRET must fail-closed (N-01)
            with pytest.raises(RuntimeError, match="MissingSecretError"):
                ingestor.reconcile_and_promote("topic_01")
        finally:
            wh.close()


# --------------------------------------------------------------------------- #
# Opt-5: SimHash 64-bit Deduplication                                        #
# --------------------------------------------------------------------------- #
def test_opt5_simhash64_deduplication() -> None:
    text_a = "Nghị quyết 999 của Quốc hội quy định về thời hạn kiểm toán nhật ký AI tối thiểu 36 tháng."
    text_b = "Nghị quyết 999 của Quốc hội quy định về thời hạn kiểm toán nhật ký AI tối thiểu là 36 tháng."
    text_different = "Giá vàng thế giới hôm nay tăng vọt lên mức kỷ lục mới trong phiên giao dịch."

    hash_a = compute_simhash64(text_a)
    hash_b = compute_simhash64(text_b)
    hash_diff = compute_simhash64(text_different)

    dist_near = hamming_distance64(hash_a, hash_b)
    dist_far = hamming_distance64(hash_a, hash_diff)

    # Near-duplicates should have very small Hamming distance (<= 10)
    assert dist_near <= 10, f"Expected near distance <= 10, got {dist_near}"
    # Completely different topics should have large Hamming distance (> 15)
    assert dist_far > 15, f"Expected far distance > 15, got {dist_far}"


# --------------------------------------------------------------------------- #
# N-10: Capability Token Path Traversal & Prefix Confusion Defense             #
# --------------------------------------------------------------------------- #
def test_n10_is_safe_path_prefix_and_macaroon_caveat_defense() -> None:
    # 1. Pure helper checks
    assert is_safe_path_prefix("/allowed/dir/file.txt", "/allowed") is True
    assert is_safe_path_prefix("/allowed/dir", "/allowed/dir") is True
    # Negative: prefix confusion (/allowed_evil should not pass prefix /allowed)
    assert is_safe_path_prefix("/allowed_evil/file.txt", "/allowed") is False
    # Negative: path traversal
    assert is_safe_path_prefix("/allowed/../etc/passwd", "/allowed") is False
    assert is_safe_path_prefix("/allowed/dir/../../etc/passwd", "/allowed") is False
    # Negative: empty paths
    assert is_safe_path_prefix("", "/allowed") is False
    assert is_safe_path_prefix("/allowed/file", "") is False

    # 2. Macaroon token enforcement
    secret = b"test-secret-for-n10-path-security"
    token = MacaroonCapabilityToken.mint(
        secret,
        subject="worker:agent",
        epoch=1,
        initial_caveats=["path_prefix=/workspace/safe"],
    )

    # Valid subpath passes
    assert token.verify(secret, current_epoch=1, context={"path": "/workspace/safe/output.json"}) is True

    # Prefix confusion fails closed
    with pytest.raises(CapabilitySecurityError, match="outside allowed prefix"):
        token.verify(secret, current_epoch=1, context={"path": "/workspace/safe_malicious/payload"})

    # Directory traversal fails closed
    with pytest.raises(CapabilitySecurityError, match="outside allowed prefix"):
        token.verify(secret, current_epoch=1, context={"path": "/workspace/safe/../../etc/shadow"})


# --------------------------------------------------------------------------- #
# N-08: SDK Offline Verification Fail-Closed When Ungrounded                  #
# --------------------------------------------------------------------------- #
def test_n08_sdk_offline_verification_fail_closed() -> None:
    protector = SCPProtect()

    # In STANDARD or STRICT mode without grounding context, offline verification must FAIL-CLOSED
    res = protector.protect_output("Some ungrounded response", tier=VerificationTier.STANDARD)
    assert res["verified"] is False
    assert res["verdict"] == "INSUFFICIENT"
    assert res["offline"] is True

    # In PERMISSIVE mode, ungrounded conversational content is allowed
    res_perm = protector.protect_output("Some conversational response", tier=VerificationTier.PERMISSIVE)
    assert res_perm["verified"] is True
    assert res_perm["verdict"] == "PASS"


# --------------------------------------------------------------------------- #
# N-04: Canonical FTS Warehouse Fallback to unverified_local_drop             #
# --------------------------------------------------------------------------- #
def test_n04_canonical_fts_warehouse_unverified_fallback() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        docs_dir = Path(tmp) / "docs"
        docs_dir.mkdir(parents=True, exist_ok=True)

        # 1. Unvetted raw .jsonl without epistemic_tier or verifier_receipt
        unvetted = {
            "doc_id": "unvetted_01",
            "title": "Unvetted Claim",
            "content": "Unverified external assertion without epistemic assessment.",
        }
        (docs_dir / "unvetted.jsonl").write_text(json.dumps(unvetted) + "\n", encoding="utf-8")

        # 2. Raw .md file
        (docs_dir / "raw_note.md").write_text("# Note Title\nRaw content from external source.", encoding="utf-8")

        wh = CanonicalFTSWarehouse(db_path=Path(tmp) / "test.sqlite", documents_dir=docs_dir)
        try:
            hits_jsonl = wh.search("Unverified external assertion")
            assert len(hits_jsonl) >= 1
            assert hits_jsonl[0]["epistemic_tier"] == "unverified_local_drop"

            hits_md = wh.search("Raw content from external source")
            assert len(hits_md) >= 1
            assert hits_md[0]["epistemic_tier"] == "unverified_local_drop"
        finally:
            wh.close()


# --------------------------------------------------------------------------- #
# N-07: HKDF RFC 5869 Key Hierarchy                                          #
# --------------------------------------------------------------------------- #
def test_n07_hkdf_rfc5869_key_hierarchy() -> None:
    # 1. RFC 5869 Test Case 1 Verification (SHA-256)
    ikm = bytes([0x0B] * 22)
    salt = bytes.fromhex("000102030405060708090a0b0c")
    info = bytes.fromhex("f0f1f2f3f4f5f6f7f8f9")
    l = 42

    prk = HkdfKeyHierarchy.extract(salt, ikm)
    expected_prk = "077709362c2e32df0ddc3f0dc47bba6390b6c73bb50f9c3122ec844ad7c2b3e5"
    assert prk.hex() == expected_prk

    okm = HkdfKeyHierarchy.expand(prk, info, length=l)
    expected_okm = "3cb25f25faacd57a90434f64d0362f2a2d2d0a90cf1a5a4c5db02d56ecc4c5bf34007208d5b887185865"
    assert okm.hex() == expected_okm

    # 2. SCP 5-subsystem domain separation
    master = b"scp-master-root-secret-for-all-subsystems"
    hkdf = HkdfKeyHierarchy(master)

    k_kernel = hkdf.derive_task_kernel_key()
    k_token = hkdf.derive_capability_token_key()
    k_gateway = hkdf.derive_llm_gateway_key()
    k_knowledge = hkdf.derive_knowledge_key()
    k_audit = hkdf.derive_audit_ledger_key()

    # All keys must be 32 bytes and pairwise unique (domain separation)
    keys = [k_kernel, k_token, k_gateway, k_knowledge, k_audit]
    assert all(len(k) == 32 for k in keys)
    assert len(set(keys)) == 5

    # Fail closed on empty master secret
    with pytest.raises(ValueError, match="Master secret cannot be empty"):
        HkdfKeyHierarchy(b"")


# --------------------------------------------------------------------------- #
# Opt-1 & Opt-2: ShardedPgKernelLockAndClaim & SkipLockedPgEventBusLease      #
# --------------------------------------------------------------------------- #
def test_opt1_and_opt2_interfaces_and_exports() -> None:
    # Opt-1: Sharded lock key mapping
    class DummyStorage:
        _conn_local = type("Local", (), {"txn": False})()
        def _get_conn(self): pass
        def commit(self): pass
        def rollback(self): pass

    sharded = ShardedPgKernelLockAndClaim(DummyStorage(), num_shards=32)
    s1 = sharded.get_shard_id("task_001")
    s2 = sharded.get_shard_id("task_002")
    assert 0 <= s1 < 32
    assert 0 <= s2 < 32
    k1 = sharded.get_lock_key(s1)
    assert k1 >= ShardedPgKernelLockAndClaim.BASE_ADVISORY_KEY

    # Opt-2: SkipLockedPgEventBusLease initialization
    lease_helper = SkipLockedPgEventBusLease("postgresql://localhost/dummy")
    assert lease_helper.dsn == "postgresql://localhost/dummy"
    assert hasattr(lease_helper, "claim_next_events")
    assert hasattr(lease_helper, "claim_and_process")
