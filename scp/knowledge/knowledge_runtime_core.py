"""S06 Knowledge System runtime CORE (M8 / 26-P1) — authority + accelerator.

[ARC-01 split 2026-10-25] Trước đây toàn bộ nằm trong knowledge_runtime.py
(1319 LOC > 800 ceiling). Tách làm 2 module, import surface KHÔNG đổi:

- Module này (core): KnowledgeStore (SQLite AUTHORITY, CE-S06-01
  must_not: direct_status_mutation) + RetrievalIndex (FTS5 ACCELERATOR,
  CE-S06-03 must_not: index_as_authority) + errors/helpers/constants chung.
- scp/knowledge/knowledge_runtime.py: GoldLifecycle (promotion ladder) +
  TemporalRevalidation (volatility review schedule) — re-export tên public
  của core để caller import từ ``scp.knowledge.knowledge_runtime`` như cũ.

Fail-closed everywhere: every gate failure raises KnowledgeTransitionRejected
(REJECTED), every transition is validated by ontology.validate_transition and
appended to an immutable status-event history (UPDATE/DELETE triggers abort).
PASS on the tests below only means "no failure observed in the stated scope".
"""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

from scp.contracts.ids import new_id
from scp.contracts.time import now_utc_iso, parse_utc_iso
from scp.epistemic.evidence_store import EvidenceIntegrityError
from scp.knowledge.ontology import (
    KnowledgeObject,
    KnowledgeStatus,
    validate_object,
    validate_transition,
)

# Promotable ladder in strict order; GOLD is the top of the ladder.
_PROMOTION_LADDER: tuple[str, ...] = (
    KnowledgeStatus.RAW.value,
    KnowledgeStatus.CURATED.value,
    KnowledgeStatus.CORROBORATED.value,
    KnowledgeStatus.VERIFIED.value,
    KnowledgeStatus.GOLD.value,
)

# Statuses that may be relied on for decisions. RAW is untrusted input;
# UNDER_REVIEW / DEMOTED / RETIRED are quarantined by definition.
USABLE_FOR_DECISIONS: frozenset[str] = frozenset(
    {
        KnowledgeStatus.CURATED.value,
        KnowledgeStatus.CORROBORATED.value,
        KnowledgeStatus.VERIFIED.value,
        KnowledgeStatus.GOLD.value,
    }
)

# Statuses that a revalidation sweep may push into UNDER_REVIEW.
_REVIEWABLE_STATUSES: tuple[str, ...] = (
    KnowledgeStatus.RAW.value,
    KnowledgeStatus.CURATED.value,
    KnowledgeStatus.CORROBORATED.value,
    KnowledgeStatus.VERIFIED.value,
    KnowledgeStatus.GOLD.value,
)


class KnowledgeRuntimeError(ValueError):
    """Base error for the S06 knowledge runtime (fail-closed)."""


class KnowledgeNotFound(KnowledgeRuntimeError, KeyError):
    """Requested knowledge_id does not exist in the authority store."""


class KnowledgeTransitionRejected(KnowledgeRuntimeError):
    """A promotion/demotion/retirement was REJECTED (fail-closed).

    Carries the machine-readable reason codes and missing pieces so callers can
    audit WHY the ladder refused the move (CE-S06-01: immutable decision).
    """

    def __init__(
        self,
        message: str,
        *,
        reason_codes: Sequence[str] = (),
        missing_pieces: Sequence[str] = (),
    ) -> None:
        super().__init__(message)
        self.reason_codes = list(reason_codes)
        self.missing_pieces = list(missing_pieces)


class RetrievalIndexMissing(KnowledgeRuntimeError):
    """The FTS accelerator table is absent (dropped/corrupted).

    Search refuses to answer (fail-closed) instead of silently returning an
    empty result that callers could misread as "no knowledge exists". Rebuild
    via RetrievalIndex.rebuild().
    """


def _as_utc_datetime(value: str | datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise KnowledgeRuntimeError("naive datetime rejected: provenance requires tz-aware UTC")
        return value.astimezone(timezone.utc)
    return parse_utc_iso(value)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


# ============================================================================
# 1. KNOWLEDGE STORE — the SQLite AUTHORITY
# ============================================================================

_SCHEMA = """
CREATE TABLE IF NOT EXISTS knowledge_objects (
    knowledge_id         TEXT PRIMARY KEY,
    type                 TEXT NOT NULL,
    status               TEXT NOT NULL,
    title                TEXT NOT NULL,
    data_class           TEXT NOT NULL,
    payload_json         TEXT NOT NULL,
    independent_lineages INTEGER NOT NULL DEFAULT 0,
    evidence_count       INTEGER NOT NULL DEFAULT 0,
    volatility           TEXT,
    last_validated_at    TEXT,
    review_after         TEXT,
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL
);

-- CE-S06-01 must_not direct_status_mutation: a status-changing UPDATE only
-- succeeds when a matching append-only status event (OLD.status -> NEW.status)
-- already exists in the SAME transaction. The lifecycle writes the event first;
-- any direct UPDATE of status without its history event is aborted.
CREATE TRIGGER IF NOT EXISTS trg_kobjects_no_direct_status_mutation
BEFORE UPDATE ON knowledge_objects
WHEN OLD.status <> NEW.status
     AND NOT EXISTS (
         SELECT 1 FROM knowledge_status_events
         WHERE knowledge_id = NEW.knowledge_id
           AND from_status = OLD.status
           AND to_status = NEW.status
     )
BEGIN
    SELECT RAISE(ABORT, 'direct status mutation forbidden: use GoldLifecycle (CE-S06-01)');
END;

-- CE-S06-02 must_not history_overwrite: status history is append-only.
CREATE TABLE IF NOT EXISTS knowledge_status_events (
    event_id            TEXT PRIMARY KEY,
    knowledge_id        TEXT NOT NULL,
    from_status         TEXT,
    to_status           TEXT NOT NULL,
    decision            TEXT NOT NULL,
    reason_codes_json   TEXT NOT NULL DEFAULT '[]',
    evidence_refs_json  TEXT NOT NULL DEFAULT '[]',
    actor               TEXT NOT NULL,
    timestamp           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_kse_knowledge ON knowledge_status_events(knowledge_id, timestamp);
CREATE TRIGGER IF NOT EXISTS trg_kse_no_update
BEFORE UPDATE ON knowledge_status_events
BEGIN SELECT RAISE(ABORT, 'knowledge status history is append-only'); END;
CREATE TRIGGER IF NOT EXISTS trg_kse_no_delete
BEFORE DELETE ON knowledge_status_events
BEGIN SELECT RAISE(ABORT, 'knowledge status history is append-only'); END;

-- Material contradictions: OPEN while unresolved; once RESOLVED, immutable.
CREATE TABLE IF NOT EXISTS knowledge_contradictions (
    contradiction_id TEXT PRIMARY KEY,
    knowledge_id     TEXT NOT NULL,
    evidence_ref     TEXT,
    description      TEXT NOT NULL,
    resolution       TEXT NOT NULL DEFAULT 'OPEN' CHECK (resolution IN ('OPEN', 'RESOLVED')),
    created_at       TEXT NOT NULL,
    resolved_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_kc_knowledge ON knowledge_contradictions(knowledge_id, resolution);
CREATE TRIGGER IF NOT EXISTS trg_kc_no_update_when_resolved
BEFORE UPDATE ON knowledge_contradictions
WHEN OLD.resolution = 'RESOLVED'
BEGIN SELECT RAISE(ABORT, 'resolved contradiction is immutable'); END;
CREATE TRIGGER IF NOT EXISTS trg_kc_no_delete
BEFORE DELETE ON knowledge_contradictions
BEGIN SELECT RAISE(ABORT, 'contradiction record is append-only evidence'); END;
"""


@dataclass
class LifecycleResult:
    """Outcome of a lifecycle action (promotion/demotion/retirement)."""

    knowledge_id: str
    action: str
    from_status: str
    to_status: str
    reason_codes: list[str] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    event_id: str = ""
    timestamp: str = ""


class KnowledgeStore:
    """SQLite authority for knowledge objects.

    Owns the canonical payload; the retrieval index is a derived accelerator
    kept in the same connection so store writes + index sync are atomic.
    """

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._index: "RetrievalIndex | None" = None
        self._conn.commit()

    # -- index wiring --------------------------------------------------------
    def attach_index(self, index: "RetrievalIndex") -> "RetrievalIndex":
        self._index = index
        return index

    @property
    def connection(self) -> sqlite3.Connection:
        return self._conn

    # -- CRUD ----------------------------------------------------------------
    def upsert(self, obj: KnowledgeObject) -> KnowledgeObject:
        """Insert a new object or update a NON-status field set.

        Fail-closed: changing the stored status through this door is rejected
        (status changes must walk GoldLifecycle so an immutable event exists).
        """
        validate_object(obj)
        existing = self._conn.execute(
            "SELECT status FROM knowledge_objects WHERE knowledge_id = ?",
            (obj.knowledge_id,),
        ).fetchone()
        if existing is not None and existing["status"] != parse_status_value(obj.status):
            raise KnowledgeTransitionRejected(
                f"upsert would mutate status {existing['status']} -> {obj.status} "
                "outside GoldLifecycle (direct_status_mutation forbidden)",
                reason_codes=["DIRECT_STATUS_MUTATION_FORBIDDEN"],
            )
        self._persist(obj)
        self._conn.commit()
        return obj

    def get(self, knowledge_id: str) -> KnowledgeObject:
        row = self._conn.execute(
            "SELECT payload_json FROM knowledge_objects WHERE knowledge_id = ?",
            (knowledge_id,),
        ).fetchone()
        if row is None:
            raise KnowledgeNotFound(f"knowledge_id not found: {knowledge_id!r}")
        return _object_from_payload(row["payload_json"])

    def find(self, knowledge_id: str) -> KnowledgeObject | None:
        try:
            return self.get(knowledge_id)
        except KnowledgeNotFound:
            return None

    def list_all(self, status: str | KnowledgeStatus | None = None) -> list[KnowledgeObject]:
        query = "SELECT payload_json FROM knowledge_objects"
        params: tuple[Any, ...] = ()
        if status is not None:
            query += " WHERE status = ?"
            params = (parse_status_value(status),)
        query += " ORDER BY created_at ASC, knowledge_id ASC"
        return [_object_from_payload(r["payload_json"]) for r in self._conn.execute(query, params)]

    def count(self, status: str | KnowledgeStatus | None = None) -> int:
        if status is None:
            row = self._conn.execute("SELECT COUNT(*) AS n FROM knowledge_objects").fetchone()
        else:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM knowledge_objects WHERE status = ?",
                (parse_status_value(status),),
            ).fetchone()
        return int(row["n"])

    def update(self, knowledge_id: str, mutator: Callable[[KnowledgeObject], None]) -> KnowledgeObject:
        """Apply a mutator to the stored object; status must remain unchanged."""
        obj = self.get(knowledge_id)
        before = parse_status_value(obj.status)
        mutator(obj)
        validate_object(obj)
        if parse_status_value(obj.status) != before:
            raise KnowledgeTransitionRejected(
                "update() may not change status; use GoldLifecycle",
                reason_codes=["DIRECT_STATUS_MUTATION_FORBIDDEN"],
            )
        self._persist(obj)
        self._conn.commit()
        return obj

    def record_evidence(self, knowledge_id: str, evidence_refs: Sequence[str]) -> KnowledgeObject:
        refs = [str(r) for r in evidence_refs if str(r).strip()]
        if not refs:
            raise KnowledgeRuntimeError("record_evidence requires at least one evidence ref")

        def _add(obj: KnowledgeObject) -> None:
            for ref in refs:
                if ref not in obj.evidence_refs:
                    obj.evidence_refs.append(ref)

        return self.update(knowledge_id, _add)

    # -- lifecycle-only transition channel ------------------------------------
    def transition(
        self,
        obj: KnowledgeObject,
        to_status: str | KnowledgeStatus,
        *,
        decision: str,
        reason_codes: Sequence[str],
        evidence_refs: Sequence[str] = (),
        actor: str = "system",
    ) -> LifecycleResult:
        """Validate + persist a status change and append the immutable event.

        Only GoldLifecycle/TemporalRevalidation call this; the DB trigger makes
        the status UPDATE impossible without the lifecycle session flag.
        """
        from_status = parse_status_value(obj.status)
        target = parse_status_value(to_status)
        try:
            validate_transition(from_status, target)  # ontology authority, fail-closed
        except ValueError as exc:
            raise KnowledgeTransitionRejected(
                f"transition {from_status} -> {target} rejected by ontology: {exc}",
                reason_codes=["INVALID_TRANSITION"],
            ) from exc
        obj.status = target
        obj.updated_at = now_utc_iso()
        try:
            validate_object(obj)  # e.g. GOLD without evidence_refs -> hard reject
        except ValueError as exc:
            obj.status = from_status  # leave the in-memory object as it was
            raise KnowledgeTransitionRejected(
                f"transition {from_status} -> {target} rejected by ontology validation: {exc}",
                reason_codes=["ONTOLOGY_VALIDATION_FAILED"],
            ) from exc

        refs = [str(r) for r in evidence_refs]
        event_id = new_id("kse")
        ts = obj.updated_at
        try:
            # Event FIRST: the status-guard trigger only lets an UPDATE through
            # when its append-only history event already exists (same tx).
            self._conn.execute(
                """
                INSERT INTO knowledge_status_events (
                    event_id, knowledge_id, from_status, to_status, decision,
                    reason_codes_json, evidence_refs_json, actor, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    obj.knowledge_id,
                    from_status,
                    target,
                    decision,
                    json.dumps(list(reason_codes), ensure_ascii=False),
                    json.dumps(refs, ensure_ascii=False),
                    actor,
                    ts,
                ),
            )
            self._persist(obj)
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        return LifecycleResult(
            knowledge_id=obj.knowledge_id,
            action=decision,
            from_status=from_status,
            to_status=target,
            reason_codes=list(reason_codes),
            evidence_refs=refs,
            event_id=event_id,
            timestamp=ts,
        )

    # -- history / contradictions ---------------------------------------------
    def history(self, knowledge_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM knowledge_status_events WHERE knowledge_id = ? ORDER BY timestamp ASC, event_id ASC",
            (knowledge_id,),
        ).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item["reason_codes"] = json.loads(item.pop("reason_codes_json"))
            item["evidence_refs"] = json.loads(item.pop("evidence_refs_json"))
            out.append(item)
        return out

    def contradictions(self, knowledge_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM knowledge_contradictions WHERE knowledge_id = ? ORDER BY created_at ASC",
            (knowledge_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def record_contradiction(
        self,
        knowledge_id: str,
        *,
        evidence_ref: str,
        description: str,
        now: str | datetime | None = None,
    ) -> str:
        """Append an OPEN material contradiction (append-only evidence)."""
        contradiction_id = new_id("kct")
        ts = _iso(_as_utc_datetime(now))
        self._conn.execute(
            "INSERT INTO knowledge_contradictions "
            "(contradiction_id, knowledge_id, evidence_ref, description, resolution, created_at) "
            "VALUES (?, ?, ?, ?, 'OPEN', ?)",
            (contradiction_id, knowledge_id, str(evidence_ref), description, ts),
        )
        self._conn.commit()
        return contradiction_id

    def usable_for_decisions(self, obj: KnowledgeObject, *, now: str | datetime | None = None) -> bool:
        """Stale / quarantined knowledge must never drive decisions."""
        if parse_status_value(obj.status) not in USABLE_FOR_DECISIONS:
            return False
        review_after = (obj.validity or {}).get("review_after")
        if review_after and parse_utc_iso(str(review_after)) <= _as_utc_datetime(now):
            return False  # expired: not re-checked yet -> not decision-grade
        return True

    def open_contradiction_count(self, knowledge_id: str) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM knowledge_contradictions "
            "WHERE knowledge_id = ? AND resolution = 'OPEN'",
            (knowledge_id,),
        ).fetchone()
        return int(row["n"])

    # -- internals -------------------------------------------------------------
    def _persist(self, obj: KnowledgeObject) -> None:
        validity = obj.validity if isinstance(obj.validity, dict) else {}
        self._conn.execute(
            """
            INSERT OR REPLACE INTO knowledge_objects (
                knowledge_id, type, status, title, data_class, payload_json,
                independent_lineages, evidence_count, volatility,
                last_validated_at, review_after, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                obj.knowledge_id,
                parse_type_value(obj.type),
                parse_status_value(obj.status),
                obj.title,
                parse_data_class_value(obj.data_class),
                to_payload_json(obj),
                int(obj.independent_lineages),
                len(obj.evidence_refs),
                validity.get("volatility"),
                validity.get("last_validated_at"),
                validity.get("review_after"),
                obj.created_at,
                obj.updated_at,
            ),
        )
        # Keep the accelerator in sync INSIDE the same transaction. If the FTS
        # table was dropped, the authority write MUST still succeed (index is
        # not the authority) — the index marks itself degraded instead.
        if self._index is not None:
            self._index.sync_object(obj)

    def close(self) -> None:
        self._conn.close()


def parse_status_value(value: object) -> str:
    return value.value if isinstance(value, KnowledgeStatus) else str(value).strip().upper()


def parse_type_value(value: object) -> str:
    return value.value if hasattr(value, "value") else str(value).strip().upper()


def parse_data_class_value(value: object) -> str:
    return value.value if hasattr(value, "value") else str(value).strip().upper()


def to_payload_json(obj: KnowledgeObject) -> str:
    from scp.knowledge.ontology import to_json

    return to_json(obj)


def _object_from_payload(payload_json: str | bytes) -> KnowledgeObject:
    from scp.knowledge.ontology import from_json

    return from_json(payload_json)


# ============================================================================
# 2. RETRIEVAL INDEX — FTS5 ACCELERATOR (never the authority)
# ============================================================================

_FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
    knowledge_id UNINDEXED,
    title,
    subject,
    predicate,
    object_text,
    content_text
);
"""

# bm25 weights follow the column order above; title dominates, raw content is
# the weakest signal. Lower bm25 = better; we expose -rank as `score`.
_FTS_BM25_WEIGHTS = "bm25(knowledge_fts, 0.0, 6.0, 4.0, 4.0, 3.0, 1.0)"

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)

_FTS_FIELDS = ("title", "subject", "predicate", "object_text", "content_text")


@dataclass
class SearchHit:
    """A ranked retrieval candidate WITH provenance from the authority."""

    knowledge_id: str
    score: float
    title: str
    status: str
    type: str
    knowledge: KnowledgeObject


class RetrievalIndex:
    """FTS5 full-text search over knowledge objects.

    Invariants (CE-S06-03):
    - The store is the authority. Every hit is re-read from the store by
      knowledge_id; an index-only ghost row can never surface as knowledge.
    - The retrieval score is an accelerator ranking, never a truth signal.
    - Dropping the index leaves the authority intact and rebuild() restores
      the index from the authority alone.
    """

    def __init__(self, store: KnowledgeStore) -> None:
        self.store = store
        self._conn = store.connection
        self._degraded = False
        self.ensure_tables()

    def ensure_tables(self) -> None:
        self._conn.executescript(_FTS_SCHEMA)
        self._conn.commit()
        self._degraded = False

    # -- sync (called by the store inside its write transaction) --------------
    def sync_object(self, obj: KnowledgeObject) -> None:
        if self._degraded:
            return  # authority write proceeds; index waits for rebuild()
        try:
            self._index_row(obj)
        except sqlite3.OperationalError:
            # FTS table dropped/corrupted: degrade instead of breaking writes.
            self._degraded = True

    def _index_row(self, obj: KnowledgeObject) -> None:
        content = obj.content if isinstance(obj.content, dict) else {}
        subject = str(content.get("subject", "") or "")
        predicate = str(content.get("predicate", "") or "")
        object_text = str(content.get("object", "") or "")
        content_text = json.dumps(content, ensure_ascii=False, sort_keys=True, default=str)
        self._conn.execute("DELETE FROM knowledge_fts WHERE knowledge_id = ?", (obj.knowledge_id,))
        self._conn.execute(
            "INSERT INTO knowledge_fts (knowledge_id, title, subject, predicate, object_text, content_text) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (obj.knowledge_id, obj.title, subject, predicate, object_text, content_text),
        )

    # -- lifecycle -------------------------------------------------------------
    def drop(self) -> None:
        """Destroy the accelerator entirely. The authority must not care."""
        self._conn.execute("DROP TABLE IF EXISTS knowledge_fts")
        self._conn.commit()
        self._degraded = True

    @property
    def degraded(self) -> bool:
        return self._degraded

    def rebuild(self) -> int:
        """Recreate the index FROM THE AUTHORITY STORE alone; returns row count."""
        self._degraded = False
        self._conn.executescript(_FTS_SCHEMA)
        self._conn.execute("DELETE FROM knowledge_fts")
        count = 0
        for obj in self.store.list_all():
            self._index_row(obj)
            count += 1
        self._conn.commit()
        return count

    # -- search -----------------------------------------------------------------
    def search(
        self,
        *,
        subject: str | None = None,
        predicate: str | None = None,
        object: str | None = None,  # noqa: A002 - mirror the ontology field name
        content: str | None = None,
        title: str | None = None,
        limit: int = 20,
        decision_only: bool = False,
        now: str | datetime | None = None,
    ) -> list[SearchHit]:
        """Ranked full-text search. `decision_only=True` additionally filters
        out knowledge that is stale/under-review/demoted/retired (stale
        knowledge is never used for decisions)."""
        match_query = self._build_match_query(
            subject=subject, predicate=predicate, object=object, content=content, title=title
        )
        if not self._table_exists():
            raise RetrievalIndexMissing(
                "retrieval index is missing; call RetrievalIndex.rebuild() "
                "(search refuses to answer from a missing accelerator)"
            )
        if self._degraded:
            raise RetrievalIndexMissing(
                "retrieval index is degraded (dropped or out of sync); call rebuild()"
            )
        fetch = max(int(limit) * 5, 50) if decision_only else max(int(limit), 1)
        rows = self._conn.execute(
            f"SELECT knowledge_id, {_FTS_BM25_WEIGHTS} AS rank_score "
            "FROM knowledge_fts WHERE knowledge_fts MATCH ? "
            "ORDER BY rank_score ASC LIMIT ?",
            (match_query, fetch),
        ).fetchall()

        hits: list[SearchHit] = []
        seen: set[str] = set()
        for row in rows:
            knowledge_id = row["knowledge_id"]
            if knowledge_id in seen:
                continue
            seen.add(knowledge_id)
            obj = self.store.find(knowledge_id)  # AUTHORITY re-read, never index
            if obj is None:
                continue  # index ghost: the store no longer holds this object
            if decision_only and not self.store.usable_for_decisions(obj, now=now):
                continue
            hits.append(
                SearchHit(
                    knowledge_id=knowledge_id,
                    score=round(-float(row["rank_score"]), 6),  # higher = more relevant
                    title=obj.title,
                    status=parse_status_value(obj.status),
                    type=parse_type_value(obj.type),
                    knowledge=obj,
                )
            )
            if len(hits) >= int(limit):
                break
        return hits

    def _table_exists(self) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='knowledge_fts'"
        ).fetchone()
        return row is not None

    @staticmethod
    def _build_match_query(
        *,
        subject: str | None,
        predicate: str | None,
        object: str | None,  # noqa: A002
        content: str | None,
        title: str | None,
    ) -> str:
        field_inputs: dict[str, str | None] = {
            "subject": subject,
            "predicate": predicate,
            "object_text": object,
            "content_text": content,
            "title": title,
        }
        groups: list[str] = []
        for column, text in field_inputs.items():
            tokens = _TOKEN_RE.findall(str(text)) if text is not None else []
            if not tokens:
                continue
            # Quote every token -> FTS syntax injection impossible; AND within
            # the field (all terms must hit), OR across requested fields.
            clause = " AND ".join(f'{column}:"{token}"' for token in tokens)
            groups.append(f"({clause})")
        if not groups:
            raise KnowledgeRuntimeError(
                "empty retrieval query: provide at least one of subject/predicate/object/content/title"
            )
        return " OR ".join(groups)


