"""SCP Unified KnowledgeStore and Catalog Engine (Schema v1).

Implements Schema v1 validation, frozen immutable records, thread-safe atomic
snapshot swaps, and query APIs according to docs/KNOWLEDGE_STORE_UNIFIED_DATA_PLAN_VN.md.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Sequence


# [SEC-FIX 2026-10-27] ARC-01 tách file (move-only): exceptions + helpers +
# immutable record dataclasses chuyển sang store_records.py; re-export dưới
# đây giữ nguyên mọi import path công khai của scp.knowledge.store.
from scp.knowledge.store_records import (  # noqa: F401 - facade re-export
    CatalogChecksumError,
    CatalogDuplicateRecordError,
    CatalogError,
    CatalogNotFoundError,
    CatalogValidationError,
    EntityInfo,
    FactRecord,
    ReferenceRecord,
    CatalogShard,
    CatalogManifest,
    _freeze_value,
    _unfreeze_value,
    normalize_term,
)


# ==============================================================================
# In-Memory Immutable Query Engine: KnowledgeSnapshot
# ==============================================================================

class KnowledgeSnapshot:
    """In-memory immutable query engine holding catalog records, indexes, and tables."""

    def __init__(
        self,
        manifest: CatalogManifest,
        records_by_id: dict[str, FactRecord],
        records_by_domain: dict[str, list[FactRecord]],
        references: dict[str, dict[str, ReferenceRecord]],
    ) -> None:
        self._manifest = manifest
        self._records_by_id: Mapping[str, FactRecord] = MappingProxyType(dict(records_by_id))
        
        # Domain mapping
        self._records_by_domain: Mapping[str, tuple[FactRecord, ...]] = MappingProxyType({
            dom: tuple(recs) for dom, recs in records_by_domain.items()
        })
        
        # Reference tables mapping: table_name -> Mapping[key, ReferenceRecord]
        self._references: Mapping[str, Mapping[str, ReferenceRecord]] = MappingProxyType({
            tbl: MappingProxyType(dict(tbl_recs)) for tbl, tbl_recs in references.items()
        })

        # Precompute entity search index for fast and precise resolution
        # Map: record_id -> (norm_strings: tuple[str, ...], norm_tokens: frozenset[str])
        search_index: dict[str, tuple[tuple[str, ...], frozenset[str]]] = {}
        for r_id, rec in self._records_by_id.items():
            ent = rec.entity
            raw_strings = [
                ent.canonical_name,
                ent.entity_id,
                *(ent.names or ()),
                *(ent.aliases or ()),
                ent.place or "",
            ]
            norm_strings = tuple(normalize_term(s) for s in raw_strings if s and normalize_term(s))
            tokens: set[str] = set()
            for ns in norm_strings:
                for tok in re.split(r"[\s\-_,.]+", ns):
                    if tok:
                        tokens.add(tok)
            search_index[r_id] = (norm_strings, frozenset(tokens))

        self._search_index = MappingProxyType(search_index)

    @property
    def manifest(self) -> CatalogManifest:
        return self._manifest

    @property
    def dataset_version(self) -> str:
        return self._manifest.dataset_version

    @property
    def records_by_id(self) -> Mapping[str, FactRecord]:
        return self._records_by_id

    @property
    def domains(self) -> tuple[str, ...]:
        return tuple(sorted(self._records_by_domain.keys()))

    @property
    def references(self) -> Mapping[str, Mapping[str, ReferenceRecord]]:
        return self._references

    def get_by_id(self, record_id: str) -> FactRecord | None:
        """Fetch a FactRecord by exact record_id."""
        return self._records_by_id.get(record_id)

    def find_candidates(
        self,
        domain: str,
        entity_terms: Sequence[str] | set[str] | frozenset[str] | str,
        year: int | None = None,
    ) -> list[FactRecord]:
        """Find candidate FactRecords for a domain matching entity terms and optional year."""
        domain_recs = self._records_by_domain.get(domain, ())
        if not domain_recs:
            return []

        if isinstance(entity_terms, str):
            terms = [entity_terms]
        else:
            terms = list(entity_terms)

        normalized_q = [normalize_term(t) for t in terms if t and normalize_term(t)]

        candidates: list[FactRecord] = []
        for rec in domain_recs:
            # Year constraint (if specified, record must match year)
            if year is not None and rec.entity.year != year:
                continue

            if not normalized_q:
                candidates.append(rec)
                continue

            norm_strings, norm_tokens = self._search_index.get(rec.record_id, ((), frozenset()))

            all_matched = True
            for qt in normalized_q:
                matched = False
                if qt in norm_tokens:
                    matched = True
                else:
                    for s in norm_strings:
                        if qt in s or s in qt:
                            matched = True
                            break
                if not matched:
                    all_matched = False
                    break

            if all_matched:
                candidates.append(rec)

        return candidates

    def find_unique(
        self,
        domain: str,
        entity_terms: Sequence[str] | set[str] | frozenset[str] | str,
        attribute: str | None = None,
        year: int | None = None,
    ) -> FactRecord | None:
        """Find a unique candidate matching domain, terms, year, and optional attribute.
        
        Returns the FactRecord if and only if exactly 1 record matches.
        Returns None if 0 or >1 candidates match (Fail-Closed on ambiguity).
        """
        candidates = self.find_candidates(domain=domain, entity_terms=entity_terms, year=year)
        if attribute is not None:
            candidates = [c for c in candidates if attribute in c.facts]

        if len(candidates) == 1:
            return candidates[0]
        return None

    def get_reference(self, table_name: str, key: str) -> ReferenceRecord | None:
        """Get a single ReferenceRecord from a reference table by key."""
        table = self._references.get(table_name)
        if not table:
            return None
        return table.get(key)

    def list_reference(self, table_name: str) -> list[ReferenceRecord]:
        """List all ReferenceRecords from a reference table."""
        table = self._references.get(table_name)
        if not table:
            return []
        return list(table.values())


# ==============================================================================
# Catalog Loader & Validator (Fail-Closed)
# ==============================================================================

def load_catalog(
    catalog_dir: Path | str,
    strict: bool = True,
    check_hashes: bool = True,
) -> KnowledgeSnapshot:
    """Validate and load knowledge catalog into an immutable KnowledgeSnapshot."""
    cat_path = Path(catalog_dir).resolve()
    if not cat_path.exists() or not cat_path.is_dir():
        raise CatalogNotFoundError(f"Catalog directory does not exist or is not a directory: {cat_path}")

    manifest_file = cat_path / "manifest.json"
    if not manifest_file.exists() or not manifest_file.is_file():
        raise CatalogNotFoundError(f"Catalog manifest not found: {manifest_file}")

    try:
        manifest_raw = json.loads(manifest_file.read_text(encoding="utf-8"))
    except Exception as exc:
        raise CatalogValidationError(f"Invalid JSON in manifest '{manifest_file}': {exc}") from exc

    if not isinstance(manifest_raw, dict):
        raise CatalogValidationError(f"Manifest root must be a JSON object: {manifest_file}")

    manifest = CatalogManifest.from_dict(manifest_raw)

    seen_record_ids: set[str] = set()
    records_by_id: dict[str, FactRecord] = {}
    records_by_domain: dict[str, list[FactRecord]] = {}
    references: dict[str, dict[str, ReferenceRecord]] = {}

    for shard in manifest.shards:
        shard_path_rel = shard.path
        shard_file = (cat_path / shard_path_rel).resolve()

        # Path traversal guard
        try:
            shard_file.relative_to(cat_path)
        except ValueError:
            raise CatalogValidationError(f"Shard path escapes catalog directory: {shard_path_rel}")

        if not shard_file.exists() or not shard_file.is_file():
            raise CatalogNotFoundError(f"Shard file not found: {shard_file}")

        # Checksum validation
        shard_bytes = shard_file.read_bytes()
        if check_hashes:
            computed_sha256 = hashlib.sha256(shard_bytes).hexdigest()
            if computed_sha256.lower() != shard.sha256.lower():
                raise CatalogChecksumError(
                    f"Checksum mismatch for shard '{shard_path_rel}': expected {shard.sha256}, got {computed_sha256}"
                )

        # Parse JSONL lines
        lines = shard_bytes.decode("utf-8").splitlines()
        parsed_in_shard = 0

        for line_num, line_str in enumerate(lines, start=1):
            line_str = line_str.strip()
            if not line_str:
                continue

            try:
                record_dict = json.loads(line_str)
            except Exception as exc:
                raise CatalogValidationError(
                    f"Malformed JSON in shard '{shard_path_rel}' line {line_num}: {exc}"
                ) from exc

            if not isinstance(record_dict, dict):
                raise CatalogValidationError(
                    f"Line {line_num} in shard '{shard_path_rel}' is not a JSON object"
                )

            # Determine record type
            is_reference = (
                shard.shard_type == "reference"
                or shard_path_rel.startswith("reference/")
                or "table_name" in record_dict
            )

            if is_reference:
                default_table = shard.domain or Path(shard_path_rel).stem
                ref_record = ReferenceRecord.from_dict(record_dict, default_table=default_table)
                r_id = ref_record.record_id
                if r_id in seen_record_ids:
                    raise CatalogDuplicateRecordError(
                        f"Duplicate record_id '{r_id}' in shard '{shard_path_rel}' line {line_num}"
                    )
                seen_record_ids.add(r_id)

                if ref_record.table_name not in references:
                    references[ref_record.table_name] = {}
                references[ref_record.table_name][ref_record.key] = ref_record
                parsed_in_shard += 1
            else:
                fact_record = FactRecord.from_dict(record_dict)
                r_id = fact_record.record_id
                if r_id in seen_record_ids:
                    raise CatalogDuplicateRecordError(
                        f"Duplicate record_id '{r_id}' in shard '{shard_path_rel}' line {line_num}"
                    )
                seen_record_ids.add(r_id)

                records_by_id[r_id] = fact_record
                if fact_record.domain not in records_by_domain:
                    records_by_domain[fact_record.domain] = []
                records_by_domain[fact_record.domain].append(fact_record)
                parsed_in_shard += 1

        if strict and shard.record_count > 0 and parsed_in_shard != shard.record_count:
            raise CatalogValidationError(
                f"Record count mismatch in shard '{shard_path_rel}': manifest declared {shard.record_count}, found {parsed_in_shard}"
            )

    return KnowledgeSnapshot(
        manifest=manifest,
        records_by_id=records_by_id,
        records_by_domain=records_by_domain,
        references=references,
    )


# ==============================================================================
# Thread-Safe KnowledgeStore Container
# ==============================================================================

class KnowledgeStore:
    """Thread-safe container managing atomic knowledge snapshot swaps."""

    def __init__(
        self,
        catalog_dir: Path | str | None = None,
        auto_load: bool = True,
        strict: bool = True,
        check_hashes: bool = True,
    ) -> None:
        if catalog_dir is None:
            env_dir = os.getenv("SCP_KNOWLEDGE_CATALOG_DIR")
            if env_dir:
                catalog_dir = Path(env_dir)
            else:
                # Dynamic resolution: repo_root / data / knowledge / catalog
                catalog_dir = Path(__file__).resolve().parent.parent.parent / "data" / "knowledge" / "catalog"

        self._catalog_dir = Path(catalog_dir).resolve()
        self._strict = strict
        self._check_hashes = check_hashes
        self._lock = threading.RLock()
        self._snapshot: KnowledgeSnapshot | None = None

        if auto_load:
            self.reload()

    @property
    def catalog_dir(self) -> Path:
        return self._catalog_dir

    @property
    def dataset_version(self) -> str:
        with self._lock:
            if self._snapshot is not None:
                return self._snapshot.dataset_version
            return ""

    @property
    def snapshot(self) -> KnowledgeSnapshot | None:
        with self._lock:
            return self._snapshot

    @classmethod
    def is_enabled(cls) -> bool:
        """Check whether unified knowledge store is enabled via env var."""
        val = os.getenv("SCP_UNIFIED_KNOWLEDGE_STORE", "off").strip().lower()
        return val in ("1", "true", "on", "yes", "enabled")

    def reload(self) -> KnowledgeSnapshot:
        """Atomically reload catalog from disk.
        
        On failure, existing valid snapshot remains serving (atomic swap).
        """
        with self._lock:
            new_snapshot = load_catalog(
                catalog_dir=self._catalog_dir,
                strict=self._strict,
                check_hashes=self._check_hashes,
            )
            self._snapshot = new_snapshot
            return self._snapshot

    def get_by_id(self, record_id: str) -> FactRecord | None:
        with self._lock:
            if self._snapshot is None:
                return None
            return self._snapshot.get_by_id(record_id)

    def find_candidates(
        self,
        domain: str,
        entity_terms: Sequence[str] | set[str] | frozenset[str] | str,
        year: int | None = None,
    ) -> list[FactRecord]:
        with self._lock:
            if self._snapshot is None:
                return []
            return self._snapshot.find_candidates(domain=domain, entity_terms=entity_terms, year=year)

    def find_unique(
        self,
        domain: str,
        entity_terms: Sequence[str] | set[str] | frozenset[str] | str,
        attribute: str | None = None,
        year: int | None = None,
    ) -> FactRecord | None:
        with self._lock:
            if self._snapshot is None:
                return None
            return self._snapshot.find_unique(
                domain=domain,
                entity_terms=entity_terms,
                attribute=attribute,
                year=year,
            )

    def get_reference(self, table_name: str, key: str) -> ReferenceRecord | None:
        with self._lock:
            if self._snapshot is None:
                return None
            return self._snapshot.get_reference(table_name=table_name, key=key)

    def list_reference(self, table_name: str) -> list[ReferenceRecord]:
        with self._lock:
            if self._snapshot is None:
                return []
            return self._snapshot.list_reference(table_name=table_name)

    def health(self) -> dict[str, Any]:
        with self._lock:
            snap = self._snapshot
            return {
                "status": "healthy" if snap is not None else "uninitialized",
                "enabled": self.is_enabled(),
                "catalog_dir": str(self._catalog_dir),
                "dataset_version": snap.dataset_version if snap else None,
                "record_count": len(snap.records_by_id) if snap else 0,
                "reference_table_count": len(snap.references) if snap else 0,
                "domains": list(snap.domains) if snap else [],
            }
