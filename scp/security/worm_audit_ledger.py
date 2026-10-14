"""WORM (Write-Once-Read-Many) Merkle Hash-Chain Audit Ledger with Ed25519 Checkpoints.

Implements TCVN 11930:2017 (Level 4 Audit Protection) and NIST SP 800-53 Rev. 5
controls AU-9 (Protection of Audit Information) and AU-10 (Non-repudiation):
  1. Every audit record `n` commits to `prev_entry_sha256` of record `n - 1`
     (with `GENESIS_HASH = '0' * 64` for `seq = 1`).
  2. `entry_sha256 = SHA256(f"{seq}|{timestamp_iso}|{actor_id}|{actor_role}|{action}|{payload_sha256}|{prev_entry_sha256}")`.
  3. Checkpoints are signed with asymmetric Ed25519 private keys so that any
     post-facto deletion, insertion, or modification of any SQLite row breaks
     both the Merkle hash chain and the Ed25519 checkpoint signature.
"""
from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from scp.persistence.db import connect_hardened_sqlite

GENESIS_PREV_HASH = "0" * 64
_LEDGER_LOCK = threading.RLock()


class WormChainTamperError(RuntimeError):
    """Raised when the WORM Merkle audit chain or Ed25519 checkpoint is tampered with."""


def _compute_entry_hash(
    *,
    seq: int,
    timestamp_iso: str,
    actor_id: str,
    actor_role: str,
    action: str,
    payload_sha256: str,
    prev_entry_sha256: str,
) -> str:
    canonical = (
        f"{seq}|{timestamp_iso}|{actor_id}|{actor_role}|"
        f"{action}|{payload_sha256}|{prev_entry_sha256}"
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def generate_ed25519_keypair_b64() -> tuple[str, str]:
    """Generate an Ed25519 (private_key_b64, public_key_b64) pair for WORM checkpoints."""
    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key()
    priv_bytes = priv.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_bytes = pub.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return (
        base64.b64encode(priv_bytes).decode("ascii"),
        base64.b64encode(pub_bytes).decode("ascii"),
    )


class WormMerkleAuditLedger:
    """Append-only Merkle hash-chained audit ledger with Ed25519 signed checkpoints."""

    def __init__(
        self,
        db_path: str | Path | None = None,
        *,
        private_key_b64: str | None = None,
        public_key_b64: str | None = None,
    ) -> None:
        root_dir = Path(__file__).resolve().parents[2]
        self.db_path = Path(db_path) if db_path else (root_dir / "data" / "worm_audit_ledger.db")
        if private_key_b64 and public_key_b64:
            self._priv_b64 = private_key_b64
            self._pub_b64 = public_key_b64
        else:
            self._priv_b64, self._pub_b64 = generate_ed25519_keypair_b64()
        self._init_schema()

    @property
    def public_key_b64(self) -> str:
        return self._pub_b64

    def _connect(self) -> sqlite3.Connection:
        return connect_hardened_sqlite(self.db_path)

    def _init_schema(self) -> None:
        with _LEDGER_LOCK:
            conn = self._connect()
            try:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS worm_audit_entries (
                        seq INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp_iso TEXT NOT NULL,
                        actor_id TEXT NOT NULL,
                        actor_role TEXT NOT NULL,
                        action TEXT NOT NULL,
                        payload_json TEXT NOT NULL,
                        payload_sha256 TEXT NOT NULL,
                        prev_entry_sha256 TEXT NOT NULL,
                        entry_sha256 TEXT NOT NULL UNIQUE
                    );
                    CREATE TABLE IF NOT EXISTS worm_audit_checkpoints (
                        checkpoint_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        up_to_seq INTEGER NOT NULL,
                        head_entry_sha256 TEXT NOT NULL,
                        signed_at_iso TEXT NOT NULL,
                        public_key_b64 TEXT NOT NULL,
                        signature_b64 TEXT NOT NULL
                    );
                    """
                )
                conn.commit()
            finally:
                conn.close()

    def append_event(
        self,
        *,
        actor_id: str,
        actor_role: str,
        action: str,
        payload: dict[str, Any],
        auto_checkpoint: bool = True,
    ) -> dict[str, Any]:
        """Append an immutable audit record and optionally sign an Ed25519 checkpoint."""
        payload_str = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        payload_sha = hashlib.sha256(payload_str.encode("utf-8")).hexdigest()
        ts_iso = datetime.now(timezone.utc).isoformat()

        with _LEDGER_LOCK:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                last_row = conn.execute(
                    "SELECT seq, entry_sha256 FROM worm_audit_entries ORDER BY seq DESC LIMIT 1"
                ).fetchone()
                next_seq = 1 if last_row is None else int(last_row["seq"]) + 1
                prev_hash = GENESIS_PREV_HASH if last_row is None else str(last_row["entry_sha256"])

                entry_hash = _compute_entry_hash(
                    seq=next_seq,
                    timestamp_iso=ts_iso,
                    actor_id=actor_id,
                    actor_role=actor_role,
                    action=action,
                    payload_sha256=payload_sha,
                    prev_entry_sha256=prev_hash,
                )
                conn.execute(
                    """
                    INSERT INTO worm_audit_entries (
                        seq, timestamp_iso, actor_id, actor_role, action,
                        payload_json, payload_sha256, prev_entry_sha256, entry_sha256
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        next_seq,
                        ts_iso,
                        actor_id,
                        actor_role,
                        action,
                        payload_str,
                        payload_sha,
                        prev_hash,
                        entry_hash,
                    ),
                )
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
            finally:
                conn.close()

        checkpoint = None
        if auto_checkpoint:
            checkpoint = self.sign_checkpoint()

        return {
            "seq": next_seq,
            "timestamp_iso": ts_iso,
            "actor_id": actor_id,
            "actor_role": actor_role,
            "action": action,
            "payload_sha256": payload_sha,
            "prev_entry_sha256": prev_hash,
            "entry_sha256": entry_hash,
            "checkpoint": checkpoint,
        }

    def sign_checkpoint(self) -> dict[str, Any]:
        """Sign the current head of the Merkle audit chain using Ed25519."""
        with _LEDGER_LOCK:
            conn = self._connect()
            try:
                head = conn.execute(
                    "SELECT seq, entry_sha256 FROM worm_audit_entries ORDER BY seq DESC LIMIT 1"
                ).fetchone()
                if head is None:
                    raise WormChainTamperError("Cannot sign checkpoint on an empty audit ledger")
                up_to_seq = int(head["seq"])
                head_sha = str(head["entry_sha256"])
                signed_at = datetime.now(timezone.utc).isoformat()
                message = f"WORM_CHECKPOINT_V1|{up_to_seq}|{head_sha}".encode("utf-8")

                priv_key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(self._priv_b64))
                sig_b64 = base64.b64encode(priv_key.sign(message)).decode("ascii")

                conn.execute(
                    """
                    INSERT INTO worm_audit_checkpoints (
                        up_to_seq, head_entry_sha256, signed_at_iso, public_key_b64, signature_b64
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (up_to_seq, head_sha, signed_at, self._pub_b64, sig_b64),
                )
                conn.commit()
                return {
                    "up_to_seq": up_to_seq,
                    "head_entry_sha256": head_sha,
                    "signed_at_iso": signed_at,
                    "public_key_b64": self._pub_b64,
                    "signature_b64": sig_b64,
                }
            finally:
                conn.close()

    def verify_chain_integrity(self) -> dict[str, Any]:
        """Verify every Merkle link from seq=1..N and verify all Ed25519 checkpoints (FAIL-CLOSED)."""
        with _LEDGER_LOCK:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT * FROM worm_audit_entries ORDER BY seq ASC"
                ).fetchall()
                checkpoints = conn.execute(
                    "SELECT * FROM worm_audit_checkpoints ORDER BY checkpoint_id ASC"
                ).fetchall()
            finally:
                conn.close()

        expected_prev = GENESIS_PREV_HASH
        expected_seq = 1
        seq_to_hash: dict[int, str] = {}

        for row in rows:
            seq = int(row["seq"])
            if seq != expected_seq:
                raise WormChainTamperError(
                    f"WORM_CHAIN_GAP_DETECTED: expected seq={expected_seq}, found seq={seq}"
                )
            if str(row["prev_entry_sha256"]) != expected_prev:
                raise WormChainTamperError(
                    f"WORM_PREV_HASH_MISMATCH at seq={seq}: expected {expected_prev}, found {row['prev_entry_sha256']}"
                )
            actual_payload_sha = hashlib.sha256(str(row["payload_json"]).encode("utf-8")).hexdigest()
            if actual_payload_sha != str(row["payload_sha256"]):
                raise WormChainTamperError(
                    f"WORM_PAYLOAD_TAMPERED at seq={seq}: payload_json does not match payload_sha256"
                )
            recomputed = _compute_entry_hash(
                seq=seq,
                timestamp_iso=str(row["timestamp_iso"]),
                actor_id=str(row["actor_id"]),
                actor_role=str(row["actor_role"]),
                action=str(row["action"]),
                payload_sha256=actual_payload_sha,
                prev_entry_sha256=expected_prev,
            )
            if recomputed != str(row["entry_sha256"]):
                raise WormChainTamperError(
                    f"WORM_ENTRY_HASH_MISMATCH at seq={seq}: recomputed {recomputed} != stored {row['entry_sha256']}"
                )
            seq_to_hash[seq] = recomputed
            expected_prev = recomputed
            expected_seq += 1

        verified_checkpoints = 0
        for cp in checkpoints:
            cp_seq = int(cp["up_to_seq"])
            cp_head = str(cp["head_entry_sha256"])
            if seq_to_hash.get(cp_seq) != cp_head:
                raise WormChainTamperError(
                    f"WORM_CHECKPOINT_HEAD_MISMATCH at up_to_seq={cp_seq}: chain hash {seq_to_hash.get(cp_seq)} != checkpoint {cp_head}"
                )
            pub_key = Ed25519PublicKey.from_public_bytes(base64.b64decode(str(cp["public_key_b64"])))
            msg = f"WORM_CHECKPOINT_V1|{cp_seq}|{cp_head}".encode("utf-8")
            try:
                pub_key.verify(base64.b64decode(str(cp["signature_b64"])), msg)
            except InvalidSignature as exc:
                raise WormChainTamperError(
                    f"WORM_CHECKPOINT_SIGNATURE_INVALID at checkpoint_id={cp['checkpoint_id']}"
                ) from exc
            verified_checkpoints += 1

        return {
            "status": "VERIFIED",
            "total_entries": len(rows),
            "verified_checkpoints": verified_checkpoints,
            "head_entry_sha256": expected_prev if rows else GENESIS_PREV_HASH,
        }
