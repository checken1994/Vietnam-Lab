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


# ==============================================================================
# Exceptions Hierarchy (Fail-Closed)
# ==============================================================================

class CatalogError(Exception):
    """Base exception for all knowledge catalog errors."""


class CatalogNotFoundError(CatalogError):
    """Raised when catalog directory, manifest, or shard file is not found."""


class CatalogChecksumError(CatalogError):
    """Raised when shard SHA-256 checksum does not match manifest."""


class CatalogValidationError(CatalogError):
    """Raised when catalog, manifest, or record violates schema, evidence, or integrity rules."""


class CatalogDuplicateRecordError(CatalogValidationError):
    """Raised when duplicate record_id is found across shards."""


# ==============================================================================
# Helpers: Immutability & Normalization
# ==============================================================================

def _freeze_value(val: Any) -> Any:
    """Recursively freeze dicts to MappingProxyType and lists/sets to tuples/frozensets."""
    if isinstance(val, dict):
        return MappingProxyType({k: _freeze_value(v) for k, v in val.items()})
    elif isinstance(val, (list, tuple)):
        return tuple(_freeze_value(v) for v in val)
    elif isinstance(val, (set, frozenset)):
        return frozenset(_freeze_value(v) for v in val)
    return val


def _unfreeze_value(val: Any) -> Any:
    """Recursively convert MappingProxyType/tuples back to mutable dicts/lists."""
    if isinstance(val, (dict, MappingProxyType)):
        return {k: _unfreeze_value(v) for k, v in val.items()}
    elif isinstance(val, (list, tuple)):
        return [_unfreeze_value(v) for v in val]
    elif isinstance(val, (set, frozenset)):
        return {_unfreeze_value(v) for v in val}
    return val


def normalize_term(s: str) -> str:
    """Normalize text: Unicode NFC, strip, lowercase, collapse whitespace."""
    if not s:
        return ""
    norm = unicodedata.normalize("NFC", str(s)).strip().lower()
    return re.sub(r"\s+", " ", norm)


# ==============================================================================
# Immutable Records & Data Classes
# ==============================================================================

@dataclass(frozen=True)
class EntityInfo:
    """Entity descriptor for a fact record."""
    entity_id: str
    canonical_name: str
    names: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    place: str | None = None
    event_date: str | None = None
    year: int | None = None
    extra: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> EntityInfo:
        known_keys = {"entity_id", "canonical_name", "names", "aliases", "place", "event_date", "year"}
        entity_id = str(d.get("entity_id", "")).strip()
        canonical_name = str(d.get("canonical_name", "")).strip()
        names = tuple(str(x).strip() for x in d.get("names", ()) if x)
        aliases = tuple(str(x).strip() for x in d.get("aliases", ()) if x)
        place = str(d["place"]).strip() if d.get("place") is not None else None
        event_date = str(d["event_date"]).strip() if d.get("event_date") is not None else None
        
        year_raw = d.get("year")
        year: int | None = None
        if year_raw is not None and str(year_raw).strip() != "":
            try:
                year = int(year_raw)
            except (ValueError, TypeError) as exc:
                raise CatalogValidationError(f"Invalid entity year: {year_raw!r}") from exc

        extra = MappingProxyType({
            k: _freeze_value(v) for k, v in d.items() if k not in known_keys
        })
        return cls(
            entity_id=entity_id,
            canonical_name=canonical_name,
            names=names,
            aliases=aliases,
            place=place,
            event_date=event_date,
            year=year,
            extra=extra,
        )

    def __getitem__(self, item: str) -> Any:
        try:
            return getattr(self, item)
        except AttributeError:
            if item in self.extra:
                return self.extra[item]
            raise KeyError(item)

    def get(self, item: str, default: Any = None) -> Any:
        val = getattr(self, item, None)
        if val is not None:
            return val
        return self.extra.get(item, default)

    def __contains__(self, item: str) -> bool:
        return hasattr(self, item) or item in self.extra

    def to_dict(self) -> dict[str, Any]:
        base: dict[str, Any] = {
            "entity_id": self.entity_id,
            "canonical_name": self.canonical_name,
            "names": list(self.names),
            "aliases": list(self.aliases),
            "place": self.place,
            "event_date": self.event_date,
            "year": self.year,
        }
        if self.extra:
            base.update(_unfreeze_value(self.extra))
        return base


@dataclass(frozen=True)
class FactRecord:
    """Canonical Schema v1 Fact Record."""
    schema_version: int
    record_id: str
    domain: str
    entity: EntityInfo
    facts: Mapping[str, Any]
    review: Mapping[str, Any]
    evidence: Mapping[str, Any] | None = None
    freshness: Mapping[str, Any] | None = None
    metadata: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> FactRecord:
        schema_version = d.get("schema_version", 1)
        if not isinstance(schema_version, int):
            raise CatalogValidationError(f"schema_version must be int, got {type(schema_version)}")

        record_id = d.get("record_id")
        if not record_id or not isinstance(record_id, str) or not record_id.strip():
            raise CatalogValidationError("Record missing valid non-empty 'record_id'")
        record_id = record_id.strip()

        domain = d.get("domain")
        if not domain or not isinstance(domain, str) or not domain.strip():
            raise CatalogValidationError(f"Record '{record_id}' missing valid non-empty 'domain'")
        domain = domain.strip()

        entity_raw = d.get("entity")
        if not isinstance(entity_raw, dict):
            raise CatalogValidationError(f"Record '{record_id}' missing valid 'entity' object")
        entity = EntityInfo.from_dict(entity_raw)
        if not entity.canonical_name and not entity.entity_id:
            raise CatalogValidationError(f"Record '{record_id}' entity requires canonical_name or entity_id")

        facts_raw = d.get("facts")
        if not isinstance(facts_raw, dict):
            raise CatalogValidationError(f"Record '{record_id}' missing valid 'facts' dict")
        facts = _freeze_value(facts_raw)

        review_raw = d.get("review")
        if not isinstance(review_raw, dict):
            raise CatalogValidationError(f"Record '{record_id}' missing valid 'review' dict")
        status = review_raw.get("status")
        valid_statuses = {"legacy_unverified", "source_fetched", "verified", "rejected", "stale"}
        if not status or status not in valid_statuses:
            raise CatalogValidationError(
                f"Record '{record_id}' review.status must be one of {valid_statuses}, got {status!r}"
            )
        review = _freeze_value(review_raw)

        # Fail-closed evidence rule for verified records
        evidence_raw = d.get("evidence")
        if status == "verified":
            if not evidence_raw or not isinstance(evidence_raw, dict):
                raise CatalogValidationError(
                    f"Record '{record_id}' has review.status='verified' but missing evidence object"
                )
            for req_field in ("text", "source_id", "source_url"):
                val = evidence_raw.get(req_field)
                if not val or not isinstance(val, str) or not val.strip():
                    raise CatalogValidationError(
                        f"Record '{record_id}' has review.status='verified' but evidence missing required field '{req_field}'"
                    )
        evidence = _freeze_value(evidence_raw) if evidence_raw else None

        freshness_raw = d.get("freshness")
        freshness = _freeze_value(freshness_raw) if freshness_raw else None

        known_keys = {"schema_version", "record_id", "domain", "entity", "facts", "review", "evidence", "freshness"}
        extra_meta = {k: _freeze_value(v) for k, v in d.items() if k not in known_keys}
        metadata = MappingProxyType(extra_meta)

        return cls(
            schema_version=schema_version,
            record_id=record_id,
            domain=domain,
            entity=entity,
            facts=facts,
            review=review,
            evidence=evidence,
            freshness=freshness,
            metadata=metadata,
        )

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "schema_version": self.schema_version,
            "record_id": self.record_id,
            "domain": self.domain,
            "entity": self.entity.to_dict(),
            "facts": _unfreeze_value(self.facts),
            "review": _unfreeze_value(self.review),
        }
        if self.evidence is not None:
            d["evidence"] = _unfreeze_value(self.evidence)
        if self.freshness is not None:
            d["freshness"] = _unfreeze_value(self.freshness)
        if self.metadata:
            d.update(_unfreeze_value(self.metadata))
        return d


@dataclass(frozen=True)
class ReferenceRecord:
    """Canonical Schema v1 Reference Record (e.g. unit conversion, lookup code)."""
    schema_version: int
    table_name: str
    key: str
    data: Mapping[str, Any]
    record_id: str = ""
    source_id: str | None = None
    description: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        if not self.record_id:
            object.__setattr__(self, "record_id", f"{self.table_name}:{self.key}")

    @classmethod
    def from_dict(cls, d: dict[str, Any], default_table: str | None = None) -> ReferenceRecord:
        schema_version = d.get("schema_version", 1)
        table_name = d.get("table_name") or default_table
        if not table_name or not isinstance(table_name, str):
            raise CatalogValidationError("Reference record missing valid 'table_name'")
        table_name = table_name.strip()

        key = d.get("key") or d.get("id") or d.get("code")
        if not key or not isinstance(key, (str, int)):
            raise CatalogValidationError(f"Reference record in table '{table_name}' missing valid 'key'")
        key = str(key).strip()

        record_id = str(d.get("record_id") or f"{table_name}:{key}").strip()

        data_raw = d.get("data")
        known_top_keys = {"schema_version", "table_name", "key", "id", "code", "record_id", "source_id", "description", "data"}
        if data_raw is not None and isinstance(data_raw, dict):
            data = _freeze_value(data_raw)
            extra_meta = {k: _freeze_value(v) for k, v in d.items() if k not in known_top_keys}
        else:
            # If no dedicated 'data' subdict, wrap all non-metadata fields into data
            data_fields = {k: _freeze_value(v) for k, v in d.items() if k not in known_top_keys}
            data = MappingProxyType(data_fields)
            extra_meta = {}

        source_id = str(d["source_id"]).strip() if d.get("source_id") else None
        description = str(d["description"]).strip() if d.get("description") else None
        metadata = MappingProxyType(extra_meta)

        return cls(
            schema_version=schema_version,
            table_name=table_name,
            key=key,
            data=data,
            record_id=record_id,
            source_id=source_id,
            description=description,
            metadata=metadata,
        )

    def __getitem__(self, item: str) -> Any:
        if item in self.data:
            return self.data[item]
        try:
            return getattr(self, item)
        except AttributeError:
            raise KeyError(item)

    def get(self, item: str, default: Any = None) -> Any:
        if item in self.data:
            return self.data[item]
        return getattr(self, item, default)

    def __contains__(self, item: str) -> bool:
        return item in self.data or hasattr(self, item)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "schema_version": self.schema_version,
            "table_name": self.table_name,
            "key": self.key,
            "record_id": self.record_id,
            "data": _unfreeze_value(self.data),
        }
        if self.source_id:
            d["source_id"] = self.source_id
        if self.description:
            d["description"] = self.description
        if self.metadata:
            d.update(_unfreeze_value(self.metadata))
        return d


@dataclass(frozen=True)
class CatalogShard:
    """Catalog shard manifest item."""
    path: str
    sha256: str
    record_count: int = 0
    shard_type: str = "fact"
    domain: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CatalogShard:
        path = d.get("path")
        if not path or not isinstance(path, str) or not path.strip():
            raise CatalogValidationError("Shard missing required 'path'")
        path = path.strip()

        sha256 = d.get("sha256")
        if not sha256 or not isinstance(sha256, str) or not sha256.strip():
            raise CatalogValidationError(f"Shard '{path}' missing required 'sha256'")
        sha256 = sha256.strip().lower()

        record_count = int(d.get("record_count", 0))
        shard_type = str(d.get("shard_type", "fact")).strip().lower()
        if shard_type not in ("fact", "reference"):
            if "reference" in path.lower():
                shard_type = "reference"
            else:
                shard_type = "fact"

        domain = str(d["domain"]).strip() if d.get("domain") else None
        known_keys = {"path", "sha256", "record_count", "shard_type", "domain"}
        extra = {k: _freeze_value(v) for k, v in d.items() if k not in known_keys}

        return cls(
            path=path,
            sha256=sha256,
            record_count=record_count,
            shard_type=shard_type,
            domain=domain,
            metadata=MappingProxyType(extra),
        )

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "path": self.path,
            "sha256": self.sha256,
            "record_count": self.record_count,
            "shard_type": self.shard_type,
        }
        if self.domain:
            d["domain"] = self.domain
        if self.metadata:
            d.update(_unfreeze_value(self.metadata))
        return d


@dataclass(frozen=True)
class CatalogManifest:
    """Dataset manifest declaration."""
    manifest_version: int
    schema_version: int
    dataset_version: str
    created_at: str
    shards: tuple[CatalogShard, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CatalogManifest:
        manifest_version = d.get("manifest_version", 1)
        schema_version = d.get("schema_version", 1)
        dataset_version = d.get("dataset_version")
        if not dataset_version or not isinstance(dataset_version, str) or not dataset_version.strip():
            raise CatalogValidationError("Manifest missing required non-empty 'dataset_version'")
        dataset_version = dataset_version.strip()

        created_at = str(d.get("created_at", "")).strip()

        shards_raw = d.get("shards", ())
        if not isinstance(shards_raw, (list, tuple)):
            raise CatalogValidationError("Manifest 'shards' must be a list")
        shards = tuple(CatalogShard.from_dict(s) for s in shards_raw)

        known_keys = {"manifest_version", "schema_version", "dataset_version", "created_at", "shards"}
        extra = {k: _freeze_value(v) for k, v in d.items() if k not in known_keys}

        return cls(
            manifest_version=manifest_version,
            schema_version=schema_version,
            dataset_version=dataset_version,
            created_at=created_at,
            shards=shards,
            metadata=MappingProxyType(extra),
        )

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "manifest_version": self.manifest_version,
            "schema_version": self.schema_version,
            "dataset_version": self.dataset_version,
            "created_at": self.created_at,
            "shards": [s.to_dict() for s in self.shards],
        }
        if self.metadata:
            d.update(_unfreeze_value(self.metadata))
        return d


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
