"""Phase P4: SLSA v1.0 / in-toto Statement v1 DSSE Attestation & Atomic WORM Ledger (INV-01, INV-05)."""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scp.core.capability_token import get_capability_secret

INTOTO_STATEMENT_V1_TYPE = "https://in-toto.io/Statement/v1"
SLSA_PROVENANCE_V1_PREDICATE = "https://slsa.dev/provenance/v1"
DSSE_PAYLOAD_TYPE = "application/vnd.in-toto+json"


class ProvenanceVerificationError(PermissionError):
    """Raised fail-closed when DSSE signature, SLSA schema, or commit SHA fails verification."""


def _pae_encode(payload_type: str, payload_bytes: bytes) -> bytes:
    """DSSE Pre-Authentication Encoding (PAE): 'DSSEv1 <len> <type> <len> <body_bytes>'."""
    pt_bytes = payload_type.encode("utf-8")
    header = f"DSSEv1 {len(pt_bytes)} ".encode("ascii") + pt_bytes + f" {len(payload_bytes)} ".encode("ascii")
    return header + payload_bytes


def create_slsa_dsse_envelope(
    *,
    subject_name: str,
    subject_sha256: str,
    commit_sha: str,
    builder_id: str = "https://github.com/checken1994/Vietnam-Lab/actions",
    build_type: str = "https://scp.vietnam-lab.local/ReleaseGate/v1",
    key_id: str = "scp-dsse-root-key-1",
    secret: bytes | None = None,
) -> dict[str, Any]:
    """Create an in-toto Statement v1 + SLSA Provenance v1.0 wrapped in a signed DSSE envelope."""
    if not subject_name or not subject_sha256 or len(subject_sha256) != 64:
        raise ValueError("Valid subject_name and 64-hex subject_sha256 digest required")
    if not commit_sha or len(commit_sha) < 7:
        raise ValueError("Valid commit_sha required for SLSA provenance")

    signing_secret = secret if secret is not None else get_capability_secret()
    statement = {
        "_type": INTOTO_STATEMENT_V1_TYPE,
        "subject": [
            {
                "name": subject_name,
                "digest": {"sha256": subject_sha256.lower()},
            }
        ],
        "predicateType": SLSA_PROVENANCE_V1_PREDICATE,
        "predicate": {
            "buildDefinition": {
                "buildType": build_type,
                "externalParameters": {
                    "repository": "checken1994/Vietnam-Lab",
                    "ref": "refs/heads/main",
                    "commitSha": commit_sha,
                },
                "resolvedDependencies": [
                    {
                        "uri": "git+https://github.com/checken1994/Vietnam-Lab@refs/heads/main",
                        "digest": {"gitCommit": commit_sha},
                    }
                ],
            },
            "runDetails": {
                "builder": {"id": builder_id},
                "metadata": {
                    "startedOn": datetime.now(timezone.utc).isoformat(),
                },
            },
        },
    }
    raw_payload = json.dumps(statement, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    payload_b64 = base64.b64encode(raw_payload).decode("ascii")
    pae = _pae_encode(DSSE_PAYLOAD_TYPE, raw_payload)
    sig_hex = hmac.new(signing_secret, pae, hashlib.sha256).hexdigest()
    return {
        "payloadType": DSSE_PAYLOAD_TYPE,
        "payload": payload_b64,
        "signatures": [
            {
                "keyid": key_id,
                "sig": sig_hex,
            }
        ],
    }


def verify_slsa_dsse_envelope(
    envelope: dict[str, Any],
    *,
    expected_commit_sha: str,
    expected_subject_sha256: str | None = None,
    secret: bytes | None = None,
) -> dict[str, Any]:
    """Verify DSSE PAE signature, in-toto Statement v1 structure, and Same-SHA provenance."""
    if not isinstance(envelope, dict) or envelope.get("payloadType") != DSSE_PAYLOAD_TYPE:
        raise ProvenanceVerificationError("Invalid DSSE payloadType")
    sigs = envelope.get("signatures") or []
    if not isinstance(sigs, list) or not sigs:
        raise ProvenanceVerificationError("Unsigned DSSE envelope")
    try:
        raw_payload = base64.b64decode(envelope.get("payload", ""), validate=True)
    except (binascii.Error, ValueError, TypeError) as exc:
        raise ProvenanceVerificationError("Corrupt base64 DSSE payload") from exc

    signing_secret = secret if secret is not None else get_capability_secret()
    pae = _pae_encode(DSSE_PAYLOAD_TYPE, raw_payload)
    expected_sig = hmac.new(signing_secret, pae, hashlib.sha256).hexdigest()
    provided_sig = str(sigs[0].get("sig", "")).strip()
    if not provided_sig or not hmac.compare_digest(provided_sig, expected_sig):
        raise ProvenanceVerificationError("DSSE cryptographic signature verification failed")

    try:
        statement = json.loads(raw_payload.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ProvenanceVerificationError("Corrupt JSON in DSSE payload") from exc

    if statement.get("_type") != INTOTO_STATEMENT_V1_TYPE:
        raise ProvenanceVerificationError("Invalid in-toto Statement _type")
    if statement.get("predicateType") != SLSA_PROVENANCE_V1_PREDICATE:
        raise ProvenanceVerificationError("Invalid SLSA predicateType")

    ext_params = statement.get("predicate", {}).get("buildDefinition", {}).get("externalParameters", {})
    actual_sha = ext_params.get("commitSha")
    if actual_sha != expected_commit_sha:
        raise ProvenanceVerificationError(
            f"Same-SHA provenance mismatch: attestation is for '{actual_sha}', expected '{expected_commit_sha}'"
        )

    if expected_subject_sha256 is not None:
        subjects = statement.get("subject") or []
        actual_digest = subjects[0].get("digest", {}).get("sha256") if subjects else None
        if actual_digest != expected_subject_sha256.lower():
            raise ProvenanceVerificationError("Artifact SHA-256 digest mismatch")

    return statement


class WormAttestationLedger:
    """Append-only hash-chained WORM JSONL ledger with fsync durability for SLSA attestations."""

    def __init__(self, ledger_path: str | Path) -> None:
        self.ledger_path = Path(ledger_path)
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)

    def append_attestation(self, envelope: dict[str, Any], *, expected_commit_sha: str, secret: bytes | None = None) -> dict[str, Any]:
        statement = verify_slsa_dsse_envelope(envelope, expected_commit_sha=expected_commit_sha, secret=secret)
        entries = self.read_all()
        seq = len(entries) + 1
        prev_hash = entries[-1]["entry_hash"] if entries else None
        record_body = {
            "seq": seq,
            "prev_hash": prev_hash,
            "commit_sha": expected_commit_sha,
            "subject_name": statement["subject"][0]["name"],
            "subject_sha256": statement["subject"][0]["digest"]["sha256"],
            "dsse_envelope": envelope,
        }
        entry_hash = "sha256:" + hashlib.sha256(
            json.dumps(record_body, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        record = {**record_body, "entry_hash": entry_hash}
        line = json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
        with open(self.ledger_path, "a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
        return record

    def read_all(self) -> list[dict[str, Any]]:
        if not self.ledger_path.exists():
            return []
        lines = [ln.strip() for ln in self.ledger_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        return [json.loads(ln) for ln in lines]

    def verify_worm_chain(self) -> bool:
        entries = self.read_all()
        prev_hash = None
        for idx, item in enumerate(entries, 1):
            if item.get("seq") != idx or item.get("prev_hash") != prev_hash:
                return False
            body = {
                "seq": item["seq"],
                "prev_hash": item["prev_hash"],
                "commit_sha": item["commit_sha"],
                "subject_name": item["subject_name"],
                "subject_sha256": item["subject_sha256"],
                "dsse_envelope": item["dsse_envelope"],
            }
            expected_hash = "sha256:" + hashlib.sha256(
                json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            if item.get("entry_hash") != expected_hash:
                return False
            prev_hash = expected_hash
        return True
