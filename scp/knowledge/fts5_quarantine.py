"""Phase P4: FTS5 Dynamic Knowledge Warehouse with DNA #27 Quarantine & DNA #4 Human Gold Gate."""
from __future__ import annotations

import hashlib
import hmac
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scp.core.capability_token import get_capability_secret

_CODE_FENCE_RE = re.compile(r"(?is)```.*?```")
_HTML_SCRIPT_RE = re.compile(r"(?is)<(script|iframe|object|embed)\b[^>]*>.*?</\1>")
_INJECTION_DIRECTIVE_RE = re.compile(
    r"(?i)(?:ignore\s+(?:all\s+)?previous\s+instructions|system\s+override|exec\s*\(|eval\s*\(|__import__\s*\(|os\.system\s*\(|subprocess\.Popen)"
)


class KnowledgePoisoningError(PermissionError):
    """Raised when untrusted external knowledge fails quarantine or lacks provenance."""


@dataclass(frozen=True)
class QuarantineReport:
    doc_id: str
    source_url: str
    commit_sha: str
    sanitized_text: str
    code_blocks_stripped: int
    poison_patterns_neutralized: int
    content_sha256: str


def quarantine_external_document(
    *,
    doc_id: str,
    raw_markdown: str,
    source_url: str,
    commit_sha: str,
) -> QuarantineReport:
    """Enforce DNA #27 (Knowledge Quarantine): strip executable code and injection directives before KB ingestion."""
    if not doc_id or not source_url or not commit_sha:
        raise KnowledgePoisoningError("Provenance (doc_id, source_url, commit_sha) is mandatory for KB ingestion")

    code_blocks = len(_CODE_FENCE_RE.findall(raw_markdown or ""))
    cleaned = _CODE_FENCE_RE.sub(" [CODE_STRIPPED_BY_QUARANTINE] ", raw_markdown or "")
    cleaned = _HTML_SCRIPT_RE.sub(" [SCRIPT_STRIPPED_BY_QUARANTINE] ", cleaned)

    poison_hits = len(_INJECTION_DIRECTIVE_RE.findall(cleaned))
    cleaned = _INJECTION_DIRECTIVE_RE.sub("[POISON_DIRECTIVE_NEUTRALIZED]", cleaned)
    cleaned = " ".join(cleaned.split())
    digest = hashlib.sha256(cleaned.encode("utf-8")).hexdigest()
    return QuarantineReport(
        doc_id=doc_id,
        source_url=source_url,
        commit_sha=commit_sha,
        sanitized_text=cleaned,
        code_blocks_stripped=code_blocks,
        poison_patterns_neutralized=poison_hits,
        content_sha256=digest,
    )


class QuarantinedFTS5Warehouse:
    """SQLite FTS5 Knowledge Warehouse enforcing DNA #27 Quarantine on every write."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS kb_fts5 USING fts5(
                doc_id UNINDEXED,
                title,
                sanitized_body,
                source_url UNINDEXED,
                commit_sha UNINDEXED,
                content_sha256 UNINDEXED
            );
            """
        )

    def ingest_quarantined(self, *, doc_id: str, title: str, raw_markdown: str, source_url: str, commit_sha: str) -> QuarantineReport:
        report = quarantine_external_document(
            doc_id=doc_id,
            raw_markdown=raw_markdown,
            source_url=source_url,
            commit_sha=commit_sha,
        )
        self.conn.execute("DELETE FROM kb_fts5 WHERE doc_id = ?", (doc_id,))
        self.conn.execute(
            "INSERT INTO kb_fts5(doc_id, title, sanitized_body, source_url, commit_sha, content_sha256) VALUES (?, ?, ?, ?, ?, ?)",
            (report.doc_id, title, report.sanitized_text, report.source_url, report.commit_sha, report.content_sha256),
        )
        self.conn.commit()
        return report

    def search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        safe_tokens = [re.sub(r"[^0-9A-Za-zÀ-ỹ_]", "", tok) for tok in (query or "").split()]
        safe_tokens = [t for t in safe_tokens if t]
        if not safe_tokens:
            return []
        fts_query = " OR ".join(f'"{t}"' for t in safe_tokens)
        rows = self.conn.execute(
            "SELECT doc_id, title, sanitized_body, source_url, commit_sha, content_sha256 FROM kb_fts5 WHERE kb_fts5 MATCH ? LIMIT ?",
            (fts_query, int(limit)),
        ).fetchall()
        return [dict(r) for r in rows]

    def close(self) -> None:
        self.conn.close()


def sign_human_gold_rag_row(
    *,
    question_id: str,
    gold_answer: str,
    source_quote: str,
    source_text: str,
    reviewer_id: str,
    secret: bytes | None = None,
) -> dict[str, Any]:
    """Sign a human-reviewed Gold RAG row (DNA #4) after verifying exact quote containment."""
    if not question_id or not gold_answer or not source_quote or not reviewer_id:
        raise ValueError("All fields (question_id, gold_answer, source_quote, reviewer_id) are required")
    if source_quote not in (source_text or ""):
        raise ValueError("Quote containment check failed: source_quote is not verbatim in source_text")
    key = secret if secret is not None else get_capability_secret()
    canonical = f"{question_id}|{reviewer_id}|{gold_answer}|{source_quote}".encode("utf-8")
    sig = hmac.new(key, canonical, hashlib.sha256).hexdigest()
    return {
        "question_id": question_id,
        "gold_answer": gold_answer,
        "source_quote": source_quote,
        "reviewer_id": reviewer_id,
        "review_status": "HUMAN_VERIFIED_GOLD",
        "reviewer_signature": sig,
    }


def verify_human_gold_rag_row(row: dict[str, Any], *, source_text: str, secret: bytes | None = None) -> bool:
    """Admission gate for Ragas v2 / ARES: reject unverified or quote-mismatched rows fail-closed."""
    if not isinstance(row, dict) or row.get("review_status") != "HUMAN_VERIFIED_GOLD":
        return False
    quote = str(row.get("source_quote", ""))
    if not quote or quote not in (source_text or ""):
        return False
    key = secret if secret is not None else get_capability_secret()
    canonical = f"{row.get('question_id','')}|{row.get('reviewer_id','')}|{row.get('gold_answer','')}|{quote}".encode("utf-8")
    expected = hmac.new(key, canonical, hashlib.sha256).hexdigest()
    provided = str(row.get("reviewer_signature", ""))
    return bool(provided) and hmac.compare_digest(provided, expected)
