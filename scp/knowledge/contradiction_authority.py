import uuid
import sqlite3
import json
from pathlib import Path
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import List, Dict, Any, Optional

from scp.contracts.time import now_utc_iso
from scp.knowledge.knowledge_control_db import KnowledgeControlDB

class ContradictionRelation(str, Enum):
    DIRECT_CONTRADICTION = "DIRECT_CONTRADICTION"
    TEMPORAL_CHANGE = "TEMPORAL_CHANGE"
    SCOPE_MISMATCH = "SCOPE_MISMATCH"
    POSSIBLE_CONTRADICTION = "POSSIBLE_CONTRADICTION"
    RESOLVED = "RESOLVED"

class ContradictionMateriality(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"

@dataclass
class ContradictionRecord:
    claim_a: str
    claim_b: str
    evidence_a: List[str]
    evidence_b: List[str]
    relation: ContradictionRelation | str
    materiality: ContradictionMateriality | str
    contradiction_id: str = ""
    resolution_status: str = "OPEN"
    resolution_evidence_refs: List[str] = field(default_factory=list)
    created_at: str = ""
    resolved_at: Optional[str] = None

    def __post_init__(self):
        if not self.contradiction_id:
            self.contradiction_id = f"ctd_{uuid.uuid4().hex}"
        if not self.created_at:
            self.created_at = now_utc_iso()
        if isinstance(self.relation, str):
            self.relation = ContradictionRelation(self.relation.upper())
        if isinstance(self.materiality, str):
            self.materiality = ContradictionMateriality(self.materiality.upper())

class ContradictionAuthority:
    """
    P1-04 Contradiction Authority.
    Evaluates conflicting claims/evidence and determines if they represent a true contradiction,
    a temporal progression, or a scope mismatch.
    """
    def __init__(self, db: KnowledgeControlDB):
        self.db = db
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db.db_path) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS contradictions (
                    contradiction_id TEXT PRIMARY KEY,
                    claim_a TEXT,
                    claim_b TEXT,
                    evidence_a_json TEXT,
                    evidence_b_json TEXT,
                    relation TEXT,
                    materiality TEXT,
                    resolution_status TEXT,
                    resolution_evidence_refs_json TEXT,
                    created_at TEXT,
                    resolved_at TEXT
                );
            """)

    def assess_conflict(
        self,
        claim_a: dict,
        claim_b: dict,
        evidence_meta_a: dict,
        evidence_meta_b: dict
    ) -> ContradictionRecord:
        """
        Heuristic assessment of a conflict.
        Uses proper datetime parsing for temporal ordering (not string length comparison).
        """
        from datetime import datetime, timezone

        time_a = evidence_meta_a.get("observed_at", "")
        time_b = evidence_meta_b.get("observed_at", "")

        dt_a: datetime | None = None
        dt_b: datetime | None = None
        for ts, slot in [(time_a, "a"), (time_b, "b")]:
            if ts:
                try:
                    dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    if slot == "a":
                        dt_a = dt
                    else:
                        dt_b = dt
                except (ValueError, TypeError):
                    pass  # unparseable → treat as no-timestamp

        if dt_a is not None and dt_b is not None:
            if dt_b > dt_a and evidence_meta_b.get("is_update"):
                relation = ContradictionRelation.TEMPORAL_CHANGE
                materiality = ContradictionMateriality.LOW
            else:
                relation = ContradictionRelation.DIRECT_CONTRADICTION
                materiality = ContradictionMateriality.HIGH
        else:
            relation = ContradictionRelation.POSSIBLE_CONTRADICTION
            materiality = ContradictionMateriality.MEDIUM

        # Scope mismatch check
        scope_a = claim_a.get("scope", {})
        scope_b = claim_b.get("scope", {})
        if scope_a != scope_b:
            relation = ContradictionRelation.SCOPE_MISMATCH
            materiality = ContradictionMateriality.LOW

        record = ContradictionRecord(
            claim_a=claim_a.get("id", "unknown_a"),
            claim_b=claim_b.get("id", "unknown_b"),
            evidence_a=[evidence_meta_a.get("evidence_id", "")],
            evidence_b=[evidence_meta_b.get("evidence_id", "")],
            relation=relation,
            materiality=materiality
        )
        return record

    def register(self, record: ContradictionRecord) -> str:
        """Persist the contradiction."""
        with sqlite3.connect(self.db.db_path) as conn:
            conn.execute("""
                INSERT INTO contradictions (
                    contradiction_id, claim_a, claim_b, evidence_a_json, evidence_b_json,
                    relation, materiality, resolution_status, resolution_evidence_refs_json,
                    created_at, resolved_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                record.contradiction_id,
                record.claim_a,
                record.claim_b,
                json.dumps(record.evidence_a),
                json.dumps(record.evidence_b),
                record.relation.value,
                record.materiality.value,
                record.resolution_status,
                json.dumps(record.resolution_evidence_refs),
                record.created_at,
                record.resolved_at
            ))
        
        # If materiality is HIGH or CRITICAL, we must trigger UNDER_REVIEW for VERIFIED/GOLD
        if record.materiality in (ContradictionMateriality.HIGH, ContradictionMateriality.CRITICAL):
            for claim_id in [record.claim_a, record.claim_b]:
                self.db.record_status_event({
                    "knowledge_id": claim_id,
                    "to_status": "UNDER_REVIEW",
                    "reason_codes": [f"CONTRADICTION_{record.materiality.value}"],
                    "evidence_refs": [record.contradiction_id]
                })
        
        return record.contradiction_id
