from __future__ import annotations

import tempfile
import time
from pathlib import Path

import pytest

from scp.core.capability_token import (
    CapabilitySecurityError,
    MacaroonCapabilityToken,
)
from scp.trace_ledger import MerkleTraceLedger, TraceLedger
from scp.runtime.multi_llm_crosscheck import verify_cross_lineage_consensus
from scp.autofix.runner import ensure_runner_env


# ==============================================================================
# 1. GOOGLE MACAROONS CAPABILITY TOKENS (O(1) ATTENUATION & CAVEATS)
# ==============================================================================

def test_macaroon_mint_and_verify_success():
    secret = b"test-root-secret-for-macaroons-32b"
    token = MacaroonCapabilityToken.mint(
        secret,
        subject="agent:tester",
        epoch=5,
        initial_caveats=["op=read", "task_id=t_42"],
    )
    assert token.identifier.startswith("mac_")
    assert token.subject == "agent:tester"
    assert token.epoch == 5
    assert token.caveats == ("op=read", "task_id=t_42")
    assert token.signature

    # Valid context check
    ok = token.verify(
        secret,
        current_epoch=5,
        context={"op": "read", "task_id": "t_42"},
    )
    assert ok is True


def test_macaroon_attenuation_and_chained_hmac():
    secret = b"test-root-secret-for-macaroons-32b"
    root = MacaroonCapabilityToken.mint(
        secret,
        subject="agent:worker",
        epoch=2,
        initial_caveats=["op=write"],
    )
    # Attenuate O(1) without root secret
    attenuated = root.attenuate("task_id=t_99", "path_prefix=/tmp/safe")
    assert attenuated.caveats == ("op=write", "task_id=t_99", "path_prefix=/tmp/safe")
    assert attenuated.signature != root.signature

    # Verify attenuated token against matching context
    ok = attenuated.verify(
        secret,
        current_epoch=2,
        context={"op": "write", "task_id": "t_99", "path": "/tmp/safe/doc.txt"},
    )
    assert ok is True


def test_macaroon_negative_context_mismatch_fails_closed():
    secret = b"test-root-secret-for-macaroons-32b"
    token = MacaroonCapabilityToken.mint(
        secret,
        subject="agent:worker",
        epoch=1,
        initial_caveats=["op=write", "task_id=t_100", "path_prefix=/allowed"],
    )

    # 1. Negative: wrong operation
    with pytest.raises((CapabilitySecurityError, PermissionError), match="Caveat violation on 'op'"):
        token.verify(
            secret,
            current_epoch=1,
            context={"op": "delete", "task_id": "t_100", "path": "/allowed/file"},
        )

    # 2. Negative: wrong task_id
    with pytest.raises((CapabilitySecurityError, PermissionError), match="Caveat violation on 'task_id'"):
        token.verify(
            secret,
            current_epoch=1,
            context={"op": "write", "task_id": "t_999", "path": "/allowed/file"},
        )

    # 3. Negative: path outside allowed prefix
    with pytest.raises((CapabilitySecurityError, PermissionError), match="outside allowed prefix"):
        token.verify(
            secret,
            current_epoch=1,
            context={"op": "write", "task_id": "t_100", "path": "/etc/passwd"},
        )


def test_macaroon_negative_replay_nonce_fails_closed():
    secret = b"test-root-secret-for-macaroons-32b"
    token = MacaroonCapabilityToken.mint(secret, subject="user:alice", epoch=1)
    consumed_nonces: set[str] = set()

    # First attempt: succeeds
    assert token.verify(secret, current_epoch=1, consumed_nonces=consumed_nonces) is True
    assert token.identifier in consumed_nonces

    # Second attempt: fails closed with replay detection (GAP-12)
    with pytest.raises((CapabilitySecurityError, PermissionError), match="Replay detected"):
        token.verify(secret, current_epoch=1, consumed_nonces=consumed_nonces)


def test_macaroon_negative_tampered_signature_fails_closed():
    secret = b"test-root-secret-for-macaroons-32b"
    token = MacaroonCapabilityToken.mint(secret, subject="admin", epoch=1)
    # Flip the last character of the signature
    tampered_sig = token.signature[:-1] + ("0" if token.signature[-1] != "0" else "1")
    tampered_token = MacaroonCapabilityToken(
        identifier=token.identifier,
        subject=token.subject,
        epoch=token.epoch,
        issued_at=token.issued_at,
        caveats=token.caveats,
        signature=tampered_sig,
    )

    with pytest.raises((CapabilitySecurityError, PermissionError), match="Macaroon signature mismatch"):
        tampered_token.verify(secret, current_epoch=1)


def test_macaroon_negative_epoch_and_expiration_and_clock_skew():
    secret = b"test-root-secret-for-macaroons-32b"
    now_ts = 1700000000.0

    # 1. Epoch revoked
    old_token = MacaroonCapabilityToken.mint(secret, subject="sub", epoch=3)
    with pytest.raises((CapabilitySecurityError, PermissionError), match="is revoked"):
        old_token.verify(secret, current_epoch=4)

    # 2. Token expired
    exp_token = MacaroonCapabilityToken.mint(
        secret,
        subject="sub",
        epoch=1,
        initial_caveats=[f"exp={now_ts - 10}"],
        issued_at=now_ts - 100,
    )
    with pytest.raises((CapabilitySecurityError, PermissionError), match="Token expired at"):
        exp_token.verify(secret, current_epoch=1, now=now_ts)

    # 3. Future clock skew
    future_token = MacaroonCapabilityToken.mint(
        secret,
        subject="sub",
        epoch=1,
        issued_at=now_ts + 500,
    )
    with pytest.raises((CapabilitySecurityError, PermissionError), match="Token issued in future"):
        future_token.verify(secret, current_epoch=1, max_skew_seconds=300.0, now=now_ts)


def test_macaroon_serialization_and_deserialization():
    secret = b"test-root-secret-for-macaroons-32b"
    token = MacaroonCapabilityToken.mint(
        secret,
        subject="subagent:analyzer",
        epoch=2,
        initial_caveats=["op=analyze", "scope=public"],
    )
    serialized = token.serialize()
    assert serialized.startswith("macaroon.")
    parts = serialized.split(".")
    assert len(parts) == 3

    # Roundtrip deserialization
    deserialized = MacaroonCapabilityToken.deserialize(serialized)
    assert deserialized.identifier == token.identifier
    assert deserialized.subject == token.subject
    assert deserialized.epoch == token.epoch
    assert abs(deserialized.issued_at - token.issued_at) < 1e-5
    assert deserialized.caveats == token.caveats
    assert deserialized.signature == token.signature
    assert deserialized.verify(secret, current_epoch=2, context={"op": "analyze", "scope": "public"}) is True

    # Negative: invalid prefix
    with pytest.raises((CapabilitySecurityError, PermissionError), match="must start with 'macaroon.'"):
        MacaroonCapabilityToken.deserialize("invalid.prefix.here")

    # Negative: malformed structure
    with pytest.raises((CapabilitySecurityError, PermissionError), match="Invalid macaroon string structure"):
        MacaroonCapabilityToken.deserialize("macaroon.part1")


# ==============================================================================
# 2. RFC 6962 CERTIFICATE TRANSPARENCY MERKLE TRACE LEDGER
# ==============================================================================

def test_merkle_trace_ledger_append_and_sth():
    secret = b"merkle-sth-hmac-secret-32bytes!!"
    with tempfile.TemporaryDirectory() as tmpdir:
        ledger_path = Path(tmpdir) / "audit_trace.jsonl"
        ledger = MerkleTraceLedger(ledger_path)

        entries = [
            ledger.append(event=f"step_{i}", token="test_token_redacted_value")
            for i in range(12)
        ]
        assert len(entries) == 12
        # Secret redaction check
        assert entries[0]["fields"]["token"] == "[REDACTED]"

        sth = ledger.signed_tree_head(secret)
        assert sth["tree_size"] == 12
        assert sth["merkle_root"].startswith("sha256:")
        assert sth["tail_hash"] == entries[-1]["hash"]
        assert len(sth["signature"]) == 64  # SHA-256 HMAC hex


def test_merkle_trace_ledger_inclusion_proof_and_verification():
    secret = b"merkle-sth-hmac-secret-32bytes!!"
    with tempfile.TemporaryDirectory() as tmpdir:
        ledger_path = Path(tmpdir) / "audit_trace.jsonl"
        ledger = MerkleTraceLedger(ledger_path)

        entries = [ledger.append(item=f"item_{i}") for i in range(10)]
        sth = ledger.signed_tree_head(secret)

        # Generate O(log N) inclusion proof for sequence 7 (1-based index)
        proof_7 = ledger.prove_inclusion(7)
        assert isinstance(proof_7, list)
        assert len(proof_7) <= 4  # log2(10) ceil = 4

        # Positive verification: leaf exists and matches Merkle root
        verified = MerkleTraceLedger.verify_inclusion(
            entries[6]["hash"],
            proof_7,
            sth["merkle_root"],
        )
        assert verified is True

        # Negative test 1: Tampered entry hash fails verification
        tampered_entry = "sha256:0000000000000000000000000000000000000000000000000000000000000000"
        assert MerkleTraceLedger.verify_inclusion(
            tampered_entry,
            proof_7,
            sth["merkle_root"],
        ) is False

        # Negative test 2: Wrong expected root fails verification
        wrong_root = "sha256:1111111111111111111111111111111111111111111111111111111111111111"
        assert MerkleTraceLedger.verify_inclusion(
            entries[6]["hash"],
            proof_7,
            wrong_root,
        ) is False

        # Negative test 3: Sequence out of bounds
        with pytest.raises(IndexError, match="out of bounds"):
            ledger.prove_inclusion(0)

        with pytest.raises(IndexError, match="out of bounds"):
            ledger.prove_inclusion(999)


# ==============================================================================
# 3. SCP DNA #5: MULTI-LLM CROSSCHECK & CROSS-LINEAGE CONSENSUS
# ==============================================================================

def test_cross_lineage_consensus_dna_5_negative_same_lineage():
    # Even if 2 different LLM families agree on PASS, same source lineage must ABSTAIN (DNA #5)
    opinions = [
        {"model_family": "anthropic", "source_lineage": "reuters_wire_01", "verdict": "PASS"},
        {"model_family": "openai", "source_lineage": "reuters_wire_01", "verdict": "PASS"},
    ]
    res = verify_cross_lineage_consensus(opinions)
    assert res["final"] is None
    assert res["status"] == "ABSTAIN_INSUFFICIENT_INDEPENDENT_LINEAGE"
    assert res["distinct_model_families"] == 2
    assert res["distinct_source_lineages"] == 1


def test_cross_lineage_consensus_dna_5_negative_single_family():
    # 2 different data sources but single model family must ABSTAIN
    opinions = [
        {"model_family": "anthropic", "source_lineage": "source_a", "verdict": "PASS"},
        {"model_family": "anthropic", "source_lineage": "source_b", "verdict": "PASS"},
    ]
    res = verify_cross_lineage_consensus(opinions)
    assert res["final"] is None
    assert res["status"] == "ABSTAIN_INSUFFICIENT_INDEPENDENT_LINEAGE"
    assert res["distinct_model_families"] == 1
    assert res["distinct_source_lineages"] == 2


def test_cross_lineage_consensus_dna_5_negative_disagreement():
    # Distinct lineages and families, but disagreeing verdicts must ABSTAIN
    opinions = [
        {"model_family": "anthropic", "source_lineage": "source_a", "verdict": "PASS"},
        {"model_family": "google", "source_lineage": "source_b", "verdict": "FAIL"},
    ]
    res = verify_cross_lineage_consensus(opinions)
    assert res["final"] is None
    assert res["status"] == "ABSTAIN_INSUFFICIENT_INDEPENDENT_LINEAGE"


def test_cross_lineage_consensus_dna_5_positive_dual_lineage():
    # >= 2 distinct model families AND >= 2 distinct source lineages agreeing on PASS -> VERIFIED
    opinions = [
        {"model_family": "anthropic", "source_lineage": "gov_gazette_archive", "verdict": "PASS"},
        {"model_family": "openai", "source_lineage": "court_bulletin_feed", "verdict": "PASS"},
    ]
    res = verify_cross_lineage_consensus(opinions)
    assert res["final"] == "PASS"
    assert res["status"] == "VERIFIED_CROSS_LINEAGE"
    assert res["distinct_model_families"] == 2
    assert res["distinct_source_lineages"] == 2


# ==============================================================================
# 4. DECOUPLED RUNNER .ENV IMPORT TIME
# ==============================================================================

def test_runner_ensure_env_safe_invocation():
    # ensure_runner_env must be callable without throwing an exception
    ensure_runner_env()


# ==============================================================================
# 5. DEEP VERIFICATION & HARDENED EDGE CASES
# ==============================================================================

def test_merkle_trace_ledger_tamper_detection_on_disk():
    """Disk tampering without line count change must immediately change STH root."""
    import json
    secret = b"test-sth-secret-32bytes-for-tamper"
    with tempfile.TemporaryDirectory() as tmpdir:
        ledger_path = Path(tmpdir) / "tamper_trace.jsonl"
        ledger = MerkleTraceLedger(ledger_path)
        ledger.append(step="step_1", status="ok")
        ledger.append(step="step_2", status="ok")
        sth_before = ledger.signed_tree_head(secret)

        # Direct disk mutation of entry 1 without changing line count
        lines = [line for line in ledger_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        entry_0 = json.loads(lines[0])
        entry_0["hash"] = "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
        lines[0] = json.dumps(entry_0)
        ledger_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        sth_after = ledger.signed_tree_head(secret)
        # STH root MUST change reflecting the disk alteration (Reality > Model)
        assert sth_before["merkle_root"] != sth_after["merkle_root"]


def test_macaroon_deep_attenuation_chaining():
    """Verify deep attenuation chaining across 12 nested subagent layers in O(1) steps."""
    secret = b"deep-attenuation-secret-32b-seed"
    token = MacaroonCapabilityToken.mint(
        secret,
        subject="agent:orchestrator",
        epoch=1,
        initial_caveats=["path_prefix=/workspace", "org=scp"],
    )
    # Chaining 12 successive attenuations with increasingly restrictive path prefixes
    current_path = "/workspace"
    for i in range(1, 13):
        current_path += f"/sub_{i}"
        token = token.attenuate(f"path_prefix={current_path}", f"agent_layer_{i}=authorized")

    assert len(token.caveats) == 26  # 2 initial + 12*2 attenuated
    # Context matching the deepest restrictions satisfies all 26 caveats
    context = {"path": current_path + "/output.dat", "org": "scp"}
    for i in range(1, 13):
        context[f"agent_layer_{i}"] = "authorized"

    assert token.verify(secret, current_epoch=1, context=context) is True


def test_macaroon_clock_skew_exact_boundaries():
    """Test clock skew edge cases at exactly 300.0s vs 300.001s."""
    secret = b"clock-skew-secret-32bytes-exact!!"
    now_ts = 1700000000.0

    # 1. Exactly on skew boundary (300.000s future) -> allowed
    token_boundary = MacaroonCapabilityToken.mint(
        secret,
        subject="sub",
        epoch=1,
        issued_at=now_ts + 300.0,
    )
    assert token_boundary.verify(secret, current_epoch=1, max_skew_seconds=300.0, now=now_ts) is True

    # 2. Exceeding boundary by 1 millisecond (300.001s future) -> rejected fail-closed
    token_exceeded = MacaroonCapabilityToken.mint(
        secret,
        subject="sub",
        epoch=1,
        issued_at=now_ts + 300.001,
    )
    with pytest.raises((CapabilitySecurityError, PermissionError), match="Token issued in future"):
        token_exceeded.verify(secret, current_epoch=1, max_skew_seconds=300.0, now=now_ts)


def test_macaroon_negative_empty_root_secret_fails_closed():
    """Verifying with empty root_secret must fail closed (GAP-09)."""
    token = MacaroonCapabilityToken.mint(b"valid-secret-32b", "user", 1)
    with pytest.raises((CapabilitySecurityError, PermissionError), match="Root secret cannot be empty"):
        token.verify(b"")


def test_macaroon_negative_malformed_deserialize_fails_closed():
    """Malformed payload or missing required fields in deserialization must fail closed."""
    import base64
    # Missing 'id' field in payload
    bad_payload = base64.urlsafe_b64encode(b'{"sub": "user", "epoch": 1, "iat": 100.0}').decode().rstrip("=")
    with pytest.raises((CapabilitySecurityError, PermissionError), match="Malformed macaroon payload"):
        MacaroonCapabilityToken.deserialize(f"macaroon.{bad_payload}.fake_sig")

    # Non-dict JSON payload
    array_payload = base64.urlsafe_b64encode(b'[1, 2, 3]').decode().rstrip("=")
    with pytest.raises((CapabilitySecurityError, PermissionError), match="Malformed macaroon payload"):
        MacaroonCapabilityToken.deserialize(f"macaroon.{array_payload}.fake_sig")


def test_macaroon_nonce_not_consumed_on_failed_caveat():
    """If caveat verification fails, the nonce must NOT be burned."""
    secret = b"test-root-secret-for-macaroons-32b"
    nonces: set[str] = set()
    token = MacaroonCapabilityToken.mint(secret, "user", 1, ["op=read"])

    # Attempt with wrong context
    with pytest.raises((CapabilitySecurityError, PermissionError)):
        token.verify(secret, context={"op": "write"}, consumed_nonces=nonces)

    # Nonce should not be in consumed_nonces
    assert token.identifier not in nonces

    # Subsequent attempt with correct context should succeed and consume nonce
    assert token.verify(secret, context={"op": "read"}, consumed_nonces=nonces) is True
    assert token.identifier in nonces


def test_macaroon_negative_malformed_exp_caveat_fails_closed():
    """Non-numeric exp caveat must raise CapabilitySecurityError, not unhandled ValueError."""
    secret = b"test-root-secret-for-macaroons-32b"
    token = MacaroonCapabilityToken.mint(secret, "user", 1, ["exp=not_a_valid_number"])
    with pytest.raises((CapabilitySecurityError, PermissionError), match="Invalid timestamp in caveat"):
        token.verify(secret)

