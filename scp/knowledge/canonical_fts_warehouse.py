"""Canonical SQLite FTS5 Knowledge Warehouse & Document Folder Ingestor (Wave W23).

Replaces hardcoded Python dictionary lookups with a scalable, zero-dependency
SQLite FTS5 full-text search index (`VIRTUAL TABLE kb_fts USING fts5`) backed by:
  1. Drop-in document ingestion from `data/documents/` (`.jsonl`, `.json`, `.md`, `.txt`).
  2. Cryptographically signed records promoted from `AutoWebIngestor` (`VerifierReceipt` + `SHA-256`).
  3. Strict TTL expiration (`fetched_at_epoch + ttl_seconds`): expired dynamic records
     automatically fail-closed (`STALE_EXPIRED`) rather than serving outdated facts.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("scp.knowledge.canonical_fts_warehouse")

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DOCS_DIR = ROOT / "data" / "documents"
DEFAULT_FTS_DB_PATH = ROOT / "data" / "canonical_kb_fts5.sqlite"

_WAREHOUSE_LOCK = threading.RLock()
_DEFAULT_WAREHOUSE: CanonicalFTSWarehouse | None = None

_STOPWORDS_VI_EN = {
    "là", "gì", "bao", "nhiêu", "của", "trong", "như", "thế", "nào", "ở", "đâu",
    "khi", "về", "các", "những", "một", "có", "được", "theo", "quy", "định",
    "mấy", "tại", "và", "hoặc", "tôi", "bạn", "thấy", "nghĩ", "cho", "lý", "do",
    "số", "người", "thông", "hiệu", "hình", "tử", "bản", "loại", "phần", "cách",
    "việc", "ra", "vào", "lại", "thêm",
    "what", "is", "the", "of", "in", "how", "many",
    "much", "where", "when", "who", "which", "are", "does", "do", "to", "for",
}



def _extract_fts_tokens(text: str) -> list[str]:
    cleaned = re.sub(r"[^\wÀ-ỹ0-9./\-]", " ", text.lower())
    raw_tokens = [t.strip(".-/") for t in cleaned.split() if t.strip(".-/")]
    tokens = [t for t in raw_tokens if len(t) >= 2 and t not in _STOPWORDS_VI_EN and not t.isdigit()]
    return tokens or [t for t in raw_tokens if not t.isdigit()][:5]



class CanonicalFTSWarehouse:
    """Full-Text Search (SQLite FTS5) Knowledge Warehouse with SHA-256 provenance and TTL enforcement."""

    def __init__(
        self,
        db_path: Path | str | None = None,
        documents_dir: Path | str | None = None,
        auto_ingest_dir: bool = True,
    ) -> None:
        self.db_path = Path(db_path or os.environ.get("SCP_FTS_DB_PATH") or DEFAULT_FTS_DB_PATH)
        self.documents_dir = Path(documents_dir or os.environ.get("SCP_DOCUMENTS_DIR") or DEFAULT_DOCS_DIR)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._use_unix_none: bool | None = None
        self._local = threading.local()
        self._init_schema()
        if auto_ingest_dir and self.documents_dir.is_dir():
            self.ingest_documents_directory(self.documents_dir)

    def _connect(self) -> sqlite3.Connection:
        if self._use_unix_none is None:
            try:
                conn = sqlite3.connect(str(self.db_path), timeout=30.0, check_same_thread=False)
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
                conn.execute("PRAGMA busy_timeout=5000")
                conn.execute("CREATE TABLE IF NOT EXISTS _fts_vfs_probe (id INTEGER PRIMARY KEY)")
                conn.execute("INSERT OR REPLACE INTO _fts_vfs_probe (id) VALUES (1)")
                conn.commit()
                self._use_unix_none = False
                return conn
            except sqlite3.OperationalError:
                self._use_unix_none = True

        if not self._use_unix_none:
            conn = sqlite3.connect(str(self.db_path), timeout=30.0, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout=5000")
            return conn

        posix_p = os.path.abspath(str(self.db_path)).replace("\\", "/")
        conn = sqlite3.connect(
            f"file:{posix_p}?vfs=unix-none",
            uri=True,
            timeout=30.0,
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=MEMORY")
        conn.execute("PRAGMA synchronous=OFF")
        conn.execute("PRAGMA mmap_size=0")
        return conn

    def _init_schema(self) -> None:
        if True:
            conn = getattr(self._local, "read_conn", None) if not self._use_unix_none else None
            if conn is None:
                conn = self._connect()
                if not self._use_unix_none:
                    self._local.read_conn = conn
            try:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS kb_documents (
                        doc_id TEXT PRIMARY KEY,
                        domain TEXT NOT NULL,
                        title TEXT NOT NULL,
                        content TEXT NOT NULL,
                        keywords TEXT DEFAULT '',
                        source_url TEXT NOT NULL,
                        content_sha256 TEXT NOT NULL,
                        epistemic_tier TEXT NOT NULL DEFAULT 'canonical_document',
                        fetched_at_epoch REAL NOT NULL,
                        ttl_seconds INTEGER NOT NULL DEFAULT 0,
                        verifier_receipt_json TEXT DEFAULT '',
                        document_code TEXT DEFAULT '',
                        legal_status TEXT DEFAULT 'ACTIVE',
                        effective_until TEXT DEFAULT '',
                        superseded_by TEXT DEFAULT ''
                    );
                    CREATE VIRTUAL TABLE IF NOT EXISTS kb_fts USING fts5(
                        doc_id UNINDEXED,
                        domain,
                        title,
                        content,
                        keywords
                    );
                    """
                )
                for col in ("document_code", "legal_status", "effective_until", "superseded_by"):
                    try:
                        conn.execute(f"ALTER TABLE kb_documents ADD COLUMN {col} TEXT DEFAULT ''")
                    except (sqlite3.OperationalError, sqlite3.DatabaseError) as exc:
                        logger.debug("Column %s already present or alter failed: %s", col, exc)
                conn.commit()
            finally:
                if self._use_unix_none:
                    conn.close()

    def upsert_document(
        self,
        *,
        doc_id: str,
        domain: str,
        title: str,
        content: str,
        source_url: str,
        keywords: str = "",
        epistemic_tier: str = "canonical_document",
        fetched_at_epoch: float | None = None,
        ttl_seconds: int = 0,
        verifier_receipt: dict[str, Any] | None = None,
        document_code: str = "",
        legal_status: str = "ACTIVE",
        effective_until: str = "",
        superseded_by: str = "",
    ) -> str:
        """Insert or update a verified document in kb_documents and synchronize kb_fts."""
        content_clean = content.strip()
        if not content_clean:
            raise ValueError("Document content cannot be empty")
        sha256_hex = hashlib.sha256(content_clean.encode("utf-8")).hexdigest()
        now_ts = float(fetched_at_epoch if fetched_at_epoch is not None else time.time())
        receipt_obj = getattr(verifier_receipt, "__dict__", verifier_receipt)
        receipt_str = json.dumps(receipt_obj, ensure_ascii=False, sort_keys=True, default=str) if verifier_receipt else ""

        if True:
            conn = getattr(self._local, "read_conn", None) if not self._use_unix_none else None
            if conn is None:
                conn = self._connect()
                if not self._use_unix_none:
                    self._local.read_conn = conn
            try:
                existing = conn.execute(
                    "SELECT content_sha256, fetched_at_epoch, legal_status, document_code, effective_until, superseded_by FROM kb_documents WHERE doc_id = ?",
                    (doc_id,),
                ).fetchone()
                if (
                    existing
                    and existing["content_sha256"] == sha256_hex
                    and fetched_at_epoch is None
                    and str(existing["legal_status"] or "") == str(legal_status or "ACTIVE")
                    and str(existing["document_code"] or "") == str(document_code or "")
                    and str(existing["effective_until"] or "") == str(effective_until or "")
                    and str(existing["superseded_by"] or "") == str(superseded_by or "")
                ):
                    return sha256_hex

                conn.execute(
                    """
                    INSERT OR REPLACE INTO kb_documents (
                        doc_id, domain, title, content, keywords, source_url,
                        content_sha256, epistemic_tier, fetched_at_epoch, ttl_seconds, verifier_receipt_json,
                        document_code, legal_status, effective_until, superseded_by
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        doc_id,
                        domain,
                        title,
                        content_clean,
                        keywords,
                        source_url,
                        sha256_hex,
                        epistemic_tier,
                        now_ts,
                        int(ttl_seconds),
                        receipt_str,
                        str(document_code or ""),
                        str(legal_status or "ACTIVE"),
                        str(effective_until or ""),
                        str(superseded_by or ""),
                    ),
                )
                conn.execute("DELETE FROM kb_fts WHERE doc_id = ?", (doc_id,))
                conn.execute(
                    "INSERT INTO kb_fts (doc_id, domain, title, content, keywords) VALUES (?, ?, ?, ?, ?)",
                    (doc_id, domain, title, content_clean, keywords),
                )
                conn.commit()
            finally:
                if self._use_unix_none:
                    conn.close()
        return sha256_hex

    def ingest_documents_directory(self, directory: Path | str | None = None) -> int:
        """Scan `data/documents/` for `.jsonl`, `.json`, `.md`, and `.txt` files and index into FTS5."""
        target_dir = Path(directory or self.documents_dir)
        if not target_dir.is_dir():
            return 0
        ingested = 0
        for file_path in sorted(target_dir.rglob("*")):
            if not file_path.is_file():
                continue
            suffix = file_path.suffix.lower()
            if suffix == ".jsonl":
                for idx, line in enumerate(file_path.read_text(encoding="utf-8").splitlines(), start=1):
                    line = line.strip()
                    if not line:
                        continue
                    row = json.loads(line)
                    self.upsert_document(
                        doc_id=str(row.get("doc_id") or f"{file_path.stem}_{idx:04d}"),
                        domain=str(row.get("domain") or "General"),
                        title=str(row.get("title") or file_path.stem),
                        content=str(row["content"]),
                        keywords=str(row.get("keywords") or ""),
                        source_url=str(row.get("source_url") or f"file://data/documents/{file_path.name}"),
                        epistemic_tier=str(row.get("epistemic_tier") or (row.get("verifier_receipt") and "statutory_standard") or "unverified_local_drop"),
                        ttl_seconds=int(row.get("ttl_seconds") or 0),
                        verifier_receipt=row.get("verifier_receipt"),
                        document_code=str(row.get("document_code") or ""),
                        legal_status=str(row.get("legal_status") or "ACTIVE"),
                        effective_until=str(row.get("effective_until") or ""),
                        superseded_by=str(row.get("superseded_by") or ""),
                    )
                    ingested += 1
            elif suffix == ".json":
                data = json.loads(file_path.read_text(encoding="utf-8"))
                records = data if isinstance(data, list) else [data]
                for idx, row in enumerate(records, start=1):
                    if isinstance(row, dict) and row.get("content"):
                        self.upsert_document(
                            doc_id=str(row.get("doc_id") or f"{file_path.stem}_{idx:04d}"),
                            domain=str(row.get("domain") or "General"),
                            title=str(row.get("title") or file_path.stem),
                            content=str(row["content"]),
                            keywords=str(row.get("keywords") or ""),
                            source_url=str(row.get("source_url") or f"file://data/documents/{file_path.name}"),
                            epistemic_tier=str(row.get("epistemic_tier") or (row.get("verifier_receipt") and "canonical_document") or "unverified_local_drop"),
                            ttl_seconds=int(row.get("ttl_seconds") or 0),
                            verifier_receipt=row.get("verifier_receipt"),
                            document_code=str(row.get("document_code") or ""),
                            legal_status=str(row.get("legal_status") or "ACTIVE"),
                            effective_until=str(row.get("effective_until") or ""),
                            superseded_by=str(row.get("superseded_by") or ""),
                        )
                        ingested += 1
            elif suffix in (".md", ".txt"):
                raw_txt = file_path.read_text(encoding="utf-8").strip()
                if raw_txt:
                    first_line = raw_txt.splitlines()[0].lstrip("# ").strip()
                    self.upsert_document(
                        doc_id=f"doc_{file_path.stem}",
                        domain="Document_Corpus",
                        title=first_line or file_path.stem,
                        content=raw_txt,
                        keywords=file_path.stem.replace("_", " "),
                        source_url=f"file://data/documents/{file_path.name}",
                        epistemic_tier="unverified_local_drop",
                        ttl_seconds=0,
                    )
                    ingested += 1
        return ingested

    def search(self, query: str, limit: int = 3, now_epoch: float | None = None) -> list[dict[str, Any]]:
        """Query the FTS5 index and filter out any TTL-expired dynamic records (Fail-Closed)."""
        tokens = _extract_fts_tokens(query)
        if not tokens:
            return []
        now_ts = float(now_epoch if now_epoch is not None else time.time())

        # Try strict token intersection first, then fallback to ranked OR match with >=2 token overlap
        safe_tokens = [re.sub(r'["\'()*^:]', "", t) for t in tokens if re.sub(r'["\'()*^:]', "", t)]
        if not safe_tokens:
            return []

        if True:
            conn = getattr(self._local, "read_conn", None) if not self._use_unix_none else None
            if conn is None:
                conn = self._connect()
                if not self._use_unix_none:
                    self._local.read_conn = conn
            try:
                and_expr = " AND ".join(f'"{t}"' for t in safe_tokens)
                rows = conn.execute(
                    """
                    SELECT d.*, bm25(kb_fts, 0.0, 1.5, 3.0, 1.0, 2.5) AS rank_score
                    FROM kb_fts
                    JOIN kb_documents d ON d.doc_id = kb_fts.doc_id
                    WHERE kb_fts MATCH ?
                    ORDER BY rank_score ASC
                    LIMIT ?
                    """,
                    (and_expr, limit * 2),
                ).fetchall()

                if not rows and len(safe_tokens) >= 2:
                    or_expr = " OR ".join(f'"{t}"' for t in safe_tokens)
                    candidates = conn.execute(
                        """
                        SELECT d.*, bm25(kb_fts, 0.0, 1.5, 3.0, 1.0, 2.5) AS rank_score
                        FROM kb_fts
                        JOIN kb_documents d ON d.doc_id = kb_fts.doc_id
                        WHERE kb_fts MATCH ?
                        ORDER BY rank_score ASC
                        LIMIT ?
                        """,
                        (or_expr, limit * 4),
                    ).fetchall()
                    min_overlap = max(3, int(round(len(safe_tokens) * 0.75))) if len(safe_tokens) >= 3 else len(safe_tokens)
                    filtered = []
                    for r in candidates:
                        haystack = f"{r['title']} {r['keywords']} {r['content']}".lower()
                        haystack_tokens = set(re.findall(r"[\wÀ-ỹ0-9]+", haystack))
                        hits = sum(1 for t in safe_tokens if t in haystack_tokens)
                        if hits >= min_overlap:
                            filtered.append(r)
                    rows = filtered[: limit * 2]

                results: list[dict[str, Any]] = []
                for r in rows:
                    ttl = int(r["ttl_seconds"] or 0)
                    fetched = float(r["fetched_at_epoch"] or 0.0)
                    if ttl > 0 and (now_ts - fetched) > ttl:
                        # Fail-closed on expired TTL
                        continue
                    results.append({
                        "doc_id": r["doc_id"],
                        "domain": r["domain"],
                        "title": r["title"],
                        "text": r["content"],
                        "source_url": r["source_url"],
                        "content_sha256": r["content_sha256"],
                        "epistemic_tier": r["epistemic_tier"],
                        "ttl_seconds": ttl,
                        "verifier_receipt": json.loads(r["verifier_receipt_json"]) if r["verifier_receipt_json"] else None,
                        "document_code": r["document_code"] if "document_code" in r.keys() else "",
                        "legal_status": r["legal_status"] if "legal_status" in r.keys() else "ACTIVE",
                        "effective_until": r["effective_until"] if "effective_until" in r.keys() else "",
                        "superseded_by": r["superseded_by"] if "superseded_by" in r.keys() else "",
                    })
                    if len(results) >= limit:
                        break
                return results
            except Exception as exc:
                logger.debug("FTS5 search error: %s", exc, exc_info=True)
                return []
            finally:
                if self._use_unix_none:
                    conn.close()


    def check_legal_citation_validity(self, claim_text: str) -> dict[str, Any]:
        """Check whether a legal claim cites an EXPIRED statute vs an ACTIVE statute dynamically from kb_documents."""
        q_lower = claim_text.lower()
        conn = getattr(self._local, "read_conn", None) if not self._use_unix_none else None
        if conn is None:
            conn = self._connect()
            if not self._use_unix_none:
                self._local.read_conn = conn
        try:
            expired_rows = conn.execute(
                "SELECT * FROM kb_documents WHERE UPPER(legal_status) = 'EXPIRED'"
            ).fetchall()
            for r in expired_rows:
                doc_code = str(r["document_code"] or "").lower()
                doc_title = str(r["title"] or "").lower()
                has_code_match = bool(doc_code and doc_code in q_lower)
                title_clean = re.sub(r"\(.*?\)|[-_]", " ", doc_title).strip()
                title_words = [w for w in title_clean.split() if len(w) > 2 and w not in ("luật", "đã", "hết", "hiệu", "lực")]
                has_title_match = bool(title_words and all(w in q_lower for w in title_words[:3]))

                if has_code_match or has_title_match:
                    if any(k in q_lower for k in ("đang có hiệu lực", "hiện hành", "còn hiệu lực", "áp dụng")):
                        superseded = str(r["superseded_by"] or "văn bản pháp luật hiện hành")
                        eff_until = str(r["effective_until"] or "")
                        title = str(r["title"])
                        doc_code_clean = str(r["document_code"] or r["doc_id"])
                        repaired = (
                            f"{title} ({doc_code_clean}) đã hết hiệu lực"
                            f"{f' từ ngày {eff_until}' if eff_until else ''}; "
                            f"văn bản hiện hành đang có hiệu lực là {superseded}."
                        )
                        return {
                            "valid": False,
                            "status": "EXPIRED_LEGAL_CITATION",
                            "cited_document_code": doc_code_clean,
                            "legal_status": "EXPIRED",
                            "effective_until": eff_until,
                            "superseded_by": superseded,
                            "repaired_claim": repaired,
                        }
        except (sqlite3.Error, OSError, ValueError, KeyError) as exc:
            logger.debug("Legal citation validity check error: %s", exc)
        finally:
            if self._use_unix_none:
                conn.close()

        return {
            "valid": True,
            "status": "ACTIVE_OR_NOT_APPLICABLE",
        }

    def close(self) -> None:
        """Close any thread-local or persistent SQLite connection held by this warehouse."""
        conn = getattr(self._local, "read_conn", None)
        if conn is not None:
            try:
                conn.close()
            except (sqlite3.Error, OSError) as exc:
                logger.debug("Failed closing FTS warehouse connection: %s", exc)
            self._local.read_conn = None

    def __enter__(self) -> CanonicalFTSWarehouse:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except (sqlite3.Error, OSError) as exc:
            logger.debug("Error during FTS warehouse cleanup: %s", exc)


def get_canonical_fts_warehouse() -> CanonicalFTSWarehouse:
    global _DEFAULT_WAREHOUSE
    with _WAREHOUSE_LOCK:
        if _DEFAULT_WAREHOUSE is None:
            _DEFAULT_WAREHOUSE = CanonicalFTSWarehouse()
        return _DEFAULT_WAREHOUSE
