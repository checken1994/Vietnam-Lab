"""Contract tests for Schema v1 KnowledgeStore and Catalog Validator.

Verifies:
- Valid catalog loading (baseline and dynamic).
- Missing directory / manifest raises CatalogNotFoundError.
- Malformed JSONL line raises CatalogValidationError.
- Duplicate record_id raises CatalogDuplicateRecordError.
- SHA-256 checksum mismatch raises CatalogChecksumError.
- Verified record without evidence raises CatalogValidationError (Fail-Closed).
- Snapshot immutability (frozen dataclass and frozen containers).
- Atomic reload failure preserves previous valid snapshot.
- Search APIs: find_candidates, find_unique, get_reference, list_reference.
- Feature flag default is 'off'.
- CLI validator exits 0 on valid catalog, exits 1 on invalid catalog.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from types import MappingProxyType

import pytest

from scp.knowledge.store import (
    CatalogChecksumError,
    CatalogDuplicateRecordError,
    CatalogError,
    CatalogManifest,
    CatalogNotFoundError,
    CatalogShard,
    CatalogValidationError,
    EntityInfo,
    FactRecord,
    KnowledgeSnapshot,
    KnowledgeStore,
    ReferenceRecord,
    load_catalog,
)


def _compute_sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _create_test_catalog(
    catalog_dir: Path,
    dataset_version: str = "2026.10.26.1",
    shards: list[tuple[str, str, str, int]] | None = None,
) -> None:
    """Helper to build a catalog with given shard files and compute manifest sha256."""
    catalog_dir.mkdir(parents=True, exist_ok=True)
    manifest_shards = []

    if shards:
        for rel_path, shard_type, content, count in shards:
            file_path = catalog_dir / rel_path
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_bytes(content.encode("utf-8"))
            sha256 = hashlib.sha256(file_path.read_bytes()).hexdigest()
            manifest_shards.append({
                "path": rel_path,
                "shard_type": shard_type,
                "sha256": sha256,
                "record_count": count,
            })

    manifest = {
        "manifest_version": 1,
        "schema_version": 1,
        "dataset_version": dataset_version,
        "created_at": "2026-10-26T00:00:00Z",
        "shards": manifest_shards,
        "metadata": {"test": True},
    }
    (catalog_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


# ==============================================================================
# 1. Baseline & Valid Catalog Loading
# ==============================================================================

def test_baseline_catalog_loads_successfully() -> None:
    """Baseline catalog at data/knowledge/catalog/ must load cleanly with live shards."""
    repo_root = Path(__file__).resolve().parent.parent.parent
    baseline_dir = repo_root / "data" / "knowledge" / "catalog"
    assert baseline_dir.exists(), f"Baseline catalog directory missing at {baseline_dir}"

    store = KnowledgeStore(catalog_dir=baseline_dir)
    assert bool(store.dataset_version)
    health = store.health()
    assert health["status"] == "healthy"
    assert health["record_count"] >= 2
    assert "geology.earthquake" in health["domains"]
    assert store.get_by_id("geology.earthquake:valdivia-1960") is not None
    assert store.get_by_id("geology.earthquake:none") is None


def test_valid_custom_catalog_loading(tmp_path: Path) -> None:
    """Verify loading a multi-shard catalog with facts and references."""
    valdivia_json = json.dumps({
        "schema_version": 1,
        "record_id": "geology.earthquake:valdivia-1960",
        "domain": "geology.earthquake",
        "entity": {
            "entity_id": "valdivia-1960",
            "canonical_name": "Valdivia earthquake",
            "names": ["Valdivia", "Great Chilean earthquake"],
            "aliases": ["động đất Valdivia", "động đất Chile 1960"],
            "place": "Chile",
            "event_date": "1960-05-22",
            "year": 1960,
        },
        "facts": {
            "magnitude": {"value": 9.5, "unit": "Mw"},
        },
        "evidence": {
            "text": "The 1960 Valdivia earthquake is the most powerful earthquake recorded.",
            "source_id": "usgs",
            "source_url": "https://earthquake.usgs.gov/",
            "retrieved_at": "2026-10-26T00:00:00Z",
        },
        "review": {
            "status": "verified",
            "method": "source_fetch_and_field_check",
        },
    })
    unit_json = json.dumps({
        "schema_version": 1,
        "table_name": "units",
        "key": "meter",
        "data": {
            "name": "meter",
            "symbol": "m",
            "category": "length",
            "si_base_factor": 1.0,
        },
        "source_id": "bipm",
        "description": "Base unit of length in SI",
    })

    shards = [
        ("facts/earthquakes.jsonl", "fact", valdivia_json + "\n", 1),
        ("reference/units.jsonl", "reference", unit_json + "\n", 1),
    ]
    _create_test_catalog(tmp_path, shards=shards)

    store = KnowledgeStore(catalog_dir=tmp_path)
    assert store.dataset_version == "2026.10.26.1"
    assert store.health()["record_count"] == 1
    assert store.health()["reference_table_count"] == 1

    rec = store.get_by_id("geology.earthquake:valdivia-1960")
    assert rec is not None
    assert rec.entity.canonical_name == "Valdivia earthquake"
    assert rec.facts["magnitude"]["value"] == 9.5

    ref = store.get_reference("units", "meter")
    assert ref is not None
    assert ref.data["symbol"] == "m"


# ==============================================================================
# 2. Missing Directory & Manifest (CatalogNotFoundError)
# ==============================================================================

def test_missing_directory_raises_not_found(tmp_path: Path) -> None:
    non_existent = tmp_path / "non_existent_dir"
    with pytest.raises(CatalogNotFoundError) as exc_info:
        load_catalog(non_existent)
    assert "directory does not exist" in str(exc_info.value)


def test_missing_manifest_raises_not_found(tmp_path: Path) -> None:
    empty_dir = tmp_path / "empty_catalog"
    empty_dir.mkdir()
    with pytest.raises(CatalogNotFoundError) as exc_info:
        load_catalog(empty_dir)
    assert "manifest not found" in str(exc_info.value)


def test_missing_shard_file_raises_not_found(tmp_path: Path) -> None:
    manifest = {
        "manifest_version": 1,
        "schema_version": 1,
        "dataset_version": "2026.10.26.1",
        "shards": [
            {"path": "facts/missing.jsonl", "sha256": "abc", "record_count": 1}
        ],
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(CatalogNotFoundError) as exc_info:
        load_catalog(tmp_path)
    assert "Shard file not found" in str(exc_info.value)


# ==============================================================================
# 3. Malformed JSON Lines & Manifest (CatalogValidationError)
# ==============================================================================

def test_malformed_manifest_json_raises_validation_error(tmp_path: Path) -> None:
    (tmp_path / "manifest.json").write_text("{ incomplete json ...", encoding="utf-8")
    with pytest.raises(CatalogValidationError) as exc_info:
        load_catalog(tmp_path)
    assert "Invalid JSON in manifest" in str(exc_info.value)


def test_malformed_jsonl_line_raises_validation_error(tmp_path: Path) -> None:
    bad_line = '{"record_id": "test", broken_json\n'
    shards = [("facts/bad.jsonl", "fact", bad_line, 1)]
    _create_test_catalog(tmp_path, shards=shards)

    with pytest.raises(CatalogValidationError) as exc_info:
        load_catalog(tmp_path)
    assert "Malformed JSON in shard" in str(exc_info.value)


# ==============================================================================
# 4. Duplicate Record IDs (CatalogDuplicateRecordError)
# ==============================================================================

def test_duplicate_record_id_raises_duplicate_error(tmp_path: Path) -> None:
    rec1 = json.dumps({
        "schema_version": 1,
        "record_id": "dup:id-1",
        "domain": "test.dup",
        "entity": {"canonical_name": "Entity 1"},
        "facts": {"val": 1},
        "review": {"status": "legacy_unverified"},
    })
    rec2 = json.dumps({
        "schema_version": 1,
        "record_id": "dup:id-1",
        "domain": "test.dup",
        "entity": {"canonical_name": "Entity 2"},
        "facts": {"val": 2},
        "review": {"status": "legacy_unverified"},
    })
    content = f"{rec1}\n{rec2}\n"
    shards = [("facts/dup.jsonl", "fact", content, 2)]
    _create_test_catalog(tmp_path, shards=shards)

    with pytest.raises(CatalogDuplicateRecordError) as exc_info:
        load_catalog(tmp_path)
    assert "Duplicate record_id 'dup:id-1'" in str(exc_info.value)
    # Must also be catchable as CatalogValidationError and CatalogError
    assert issubclass(CatalogDuplicateRecordError, CatalogValidationError)
    assert issubclass(CatalogDuplicateRecordError, CatalogError)


# ==============================================================================
# 5. SHA-256 Checksum Mismatch (CatalogChecksumError)
# ==============================================================================

def test_sha256_checksum_mismatch_raises_checksum_error(tmp_path: Path) -> None:
    rec = json.dumps({
        "schema_version": 1,
        "record_id": "test:chk",
        "domain": "test.chk",
        "entity": {"canonical_name": "Test"},
        "facts": {},
        "review": {"status": "legacy_unverified"},
    })
    content = rec + "\n"
    _create_test_catalog(tmp_path, shards=[("facts/chk.jsonl", "fact", content, 1)])

    # Corrupt manifest hash
    manifest_file = tmp_path / "manifest.json"
    manifest_data = json.loads(manifest_file.read_text(encoding="utf-8"))
    manifest_data["shards"][0]["sha256"] = "0" * 64
    manifest_file.write_text(json.dumps(manifest_data), encoding="utf-8")

    with pytest.raises(CatalogChecksumError) as exc_info:
        load_catalog(tmp_path, check_hashes=True)
    assert "Checksum mismatch for shard" in str(exc_info.value)


# ==============================================================================
# 6. Verified Record Missing Evidence (CatalogValidationError - Fail-Closed)
# ==============================================================================

def test_verified_record_without_evidence_raises_validation_error(tmp_path: Path) -> None:
    """A record marked 'verified' MUST provide text, source_id, and source_url."""
    # Case A: completely missing evidence dict
    rec_no_evidence = json.dumps({
        "schema_version": 1,
        "record_id": "test:no_evidence",
        "domain": "test.evidence",
        "entity": {"canonical_name": "No Evidence"},
        "facts": {},
        "review": {"status": "verified"},
    })
    _create_test_catalog(tmp_path, shards=[("facts/v1.jsonl", "fact", rec_no_evidence + "\n", 1)])

    with pytest.raises(CatalogValidationError) as exc_info:
        load_catalog(tmp_path)
    assert "review.status='verified' but missing evidence object" in str(exc_info.value)

    # Case B: evidence missing source_url
    rec_incomplete_evidence = json.dumps({
        "schema_version": 1,
        "record_id": "test:incomplete",
        "domain": "test.evidence",
        "entity": {"canonical_name": "Incomplete"},
        "facts": {},
        "review": {"status": "verified"},
        "evidence": {
            "text": "Some text",
            "source_id": "usgs",
            # missing source_url
        },
    })
    _create_test_catalog(tmp_path, shards=[("facts/v2.jsonl", "fact", rec_incomplete_evidence + "\n", 1)])

    with pytest.raises(CatalogValidationError) as exc_info:
        load_catalog(tmp_path)
    assert "evidence missing required field 'source_url'" in str(exc_info.value)

    # Case C: legacy_unverified does NOT require evidence
    rec_legacy = json.dumps({
        "schema_version": 1,
        "record_id": "test:legacy",
        "domain": "test.evidence",
        "entity": {"canonical_name": "Legacy"},
        "facts": {},
        "review": {"status": "legacy_unverified"},
    })
    _create_test_catalog(tmp_path, shards=[("facts/v3.jsonl", "fact", rec_legacy + "\n", 1)])
    snapshot = load_catalog(tmp_path)
    assert snapshot.get_by_id("test:legacy") is not None


# ==============================================================================
# 7. Snapshot Immutability (Frozen Dataclass & Containers)
# ==============================================================================

def test_snapshot_immutability(tmp_path: Path) -> None:
    rec_json = json.dumps({
        "schema_version": 1,
        "record_id": "test:immutable",
        "domain": "test.immutable",
        "entity": {
            "canonical_name": "Immutable Entity",
            "names": ["Name 1"],
            "aliases": ["Alias 1"],
        },
        "facts": {"key": {"sub": "val"}},
        "review": {"status": "legacy_unverified"},
    })
    _create_test_catalog(tmp_path, shards=[("facts/imm.jsonl", "fact", rec_json + "\n", 1)])

    store = KnowledgeStore(catalog_dir=tmp_path)
    record = store.get_by_id("test:immutable")
    assert record is not None

    # Modifying field on frozen dataclass must raise FrozenInstanceError
    with pytest.raises(dataclasses.FrozenInstanceError):
        record.domain = "mutated.domain"  # type: ignore

    # Modifying facts dictionary must raise TypeError (MappingProxyType)
    with pytest.raises(TypeError):
        record.facts["key"] = 123  # type: ignore

    with pytest.raises(TypeError):
        record.facts["key"]["sub"] = "mutated"  # type: ignore

    # Mutating returned candidates list must not mutate internal store index
    candidates = store.find_candidates("test.immutable", "immutable")
    assert len(candidates) == 1
    candidates.clear()
    assert len(candidates) == 0
    # Store internal snapshot must still have the record
    assert len(store.find_candidates("test.immutable", "immutable")) == 1


# ==============================================================================
# 8. Atomic Reload Failure Preserves Previous Valid Snapshot
# ==============================================================================

def test_atomic_reload_failure_preserves_previous_snapshot(tmp_path: Path) -> None:
    rec_json = json.dumps({
        "schema_version": 1,
        "record_id": "test:atomic",
        "domain": "test.atomic",
        "entity": {"canonical_name": "Atomic Original"},
        "facts": {"version": 1},
        "review": {"status": "legacy_unverified"},
    })
    _create_test_catalog(tmp_path, dataset_version="2026.10.26.A", shards=[("facts/a.jsonl", "fact", rec_json + "\n", 1)])

    store = KnowledgeStore(catalog_dir=tmp_path)
    assert store.dataset_version == "2026.10.26.A"
    assert store.get_by_id("test:atomic") is not None

    # Now corrupt the shard file on disk
    shard_file = tmp_path / "facts" / "a.jsonl"
    shard_file.write_text("CORRUPTED_JSON_NOT_VALID", encoding="utf-8")

    # reload() must fail with CatalogValidationError or CatalogChecksumError
    with pytest.raises(CatalogError):
        store.reload()

    # The previous snapshot must remain serving!
    assert store.dataset_version == "2026.10.26.A"
    prev_record = store.get_by_id("test:atomic")
    assert prev_record is not None
    assert prev_record.entity.canonical_name == "Atomic Original"


# ==============================================================================
# 9. Search APIs: find_candidates, find_unique, get_reference, list_reference
# ==============================================================================

def test_search_apis_contract(tmp_path: Path) -> None:
    valdivia = json.dumps({
        "schema_version": 1,
        "record_id": "geology.earthquake:valdivia-1960",
        "domain": "geology.earthquake",
        "entity": {
            "entity_id": "valdivia-1960",
            "canonical_name": "Valdivia earthquake",
            "names": ["Valdivia", "Great Chilean earthquake"],
            "aliases": ["động đất Valdivia", "động đất Chile 1960"],
            "place": "Chile",
            "year": 1960,
        },
        "facts": {"magnitude": {"value": 9.5, "unit": "Mw"}},
        "review": {"status": "legacy_unverified"},
    })
    sumatra = json.dumps({
        "schema_version": 1,
        "record_id": "geology.earthquake:sumatra-2004",
        "domain": "geology.earthquake",
        "entity": {
            "entity_id": "sumatra-2004",
            "canonical_name": "Indian Ocean earthquake and tsunami",
            "names": ["Sumatra-Andaman earthquake", "2004 Indian Ocean earthquake"],
            "aliases": ["động đất Sumatra 2004"],
            "place": "Indonesia",
            "year": 2004,
        },
        "facts": {"magnitude": {"value": 9.1, "unit": "Mw"}},
        "review": {"status": "legacy_unverified"},
    })
    unit_m = json.dumps({
        "schema_version": 1,
        "table_name": "units",
        "key": "m",
        "data": {"name": "meter", "si_factor": 1.0},
    })
    unit_km = json.dumps({
        "schema_version": 1,
        "table_name": "units",
        "key": "km",
        "data": {"name": "kilometer", "si_factor": 1000.0},
    })

    shards = [
        ("facts/eq.jsonl", "fact", f"{valdivia}\n{sumatra}\n", 2),
        ("reference/units.jsonl", "reference", f"{unit_m}\n{unit_km}\n", 2),
    ]
    _create_test_catalog(tmp_path, shards=shards)
    store = KnowledgeStore(catalog_dir=tmp_path)

    # find_candidates by entity term
    cands_valdivia = store.find_candidates("geology.earthquake", "valdivia")
    assert len(cands_valdivia) == 1
    assert cands_valdivia[0].record_id == "geology.earthquake:valdivia-1960"

    # find_candidates with year filter
    cands_year_ok = store.find_candidates("geology.earthquake", "valdivia", year=1960)
    assert len(cands_year_ok) == 1
    cands_year_mismatch = store.find_candidates("geology.earthquake", "valdivia", year=2004)
    assert len(cands_year_mismatch) == 0

    # find_candidates with multiple terms (valdivia + chile)
    cands_multi = store.find_candidates("geology.earthquake", ["valdivia", "chile"])
    assert len(cands_multi) == 1

    # find_unique: exactly 1 match
    unique_val = store.find_unique("geology.earthquake", "valdivia")
    assert unique_val is not None
    assert unique_val.record_id == "geology.earthquake:valdivia-1960"

    # find_unique with attribute filter
    unique_with_attr = store.find_unique("geology.earthquake", "valdivia", attribute="magnitude")
    assert unique_with_attr is not None
    assert store.find_unique("geology.earthquake", "valdivia", attribute="nonexistent_attr") is None

    # find_unique: ambiguity / multiple matches must return None (Fail-Closed)
    cands_both = store.find_candidates("geology.earthquake", "earthquake")
    assert len(cands_both) == 2
    assert store.find_unique("geology.earthquake", "earthquake") is None

    # Reference APIs
    ref_m = store.get_reference("units", "m")
    assert ref_m is not None
    assert ref_m.data["name"] == "meter"
    assert store.get_reference("units", "nonexistent") is None

    all_units = store.list_reference("units")
    assert len(all_units) == 2
    keys = {u.key for u in all_units}
    assert keys == {"m", "km"}
    assert store.list_reference("missing_table") == []


# ==============================================================================
# 10. Feature Flag Behavior
# ==============================================================================

def test_feature_flag_is_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SCP_UNIFIED_KNOWLEDGE_STORE", raising=False)
    assert KnowledgeStore.is_enabled() is False

    monkeypatch.setenv("SCP_UNIFIED_KNOWLEDGE_STORE", "off")
    assert KnowledgeStore.is_enabled() is False

    monkeypatch.setenv("SCP_UNIFIED_KNOWLEDGE_STORE", "0")
    assert KnowledgeStore.is_enabled() is False

    monkeypatch.setenv("SCP_UNIFIED_KNOWLEDGE_STORE", "1")
    assert KnowledgeStore.is_enabled() is True

    monkeypatch.setenv("SCP_UNIFIED_KNOWLEDGE_STORE", "true")
    assert KnowledgeStore.is_enabled() is True

    monkeypatch.setenv("SCP_UNIFIED_KNOWLEDGE_STORE", "on")
    assert KnowledgeStore.is_enabled() is True


# ==============================================================================
# 11. CLI Validator Exit Codes
# ==============================================================================

def test_cli_validator_exit_codes(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parent.parent.parent
    cli_tool = repo_root / "tools" / "validate_knowledge_catalog.py"
    python_exe = sys.executable

    # Case A: Valid baseline catalog -> Exit 0
    baseline_catalog = repo_root / "data" / "knowledge" / "catalog"
    proc_ok = subprocess.run(
        [python_exe, str(cli_tool), "--catalog-dir", str(baseline_catalog)],
        capture_output=True,
        text=True,
    )
    assert proc_ok.returncode == 0
    assert "[VALID]" in proc_ok.stdout

    # Case B: Missing catalog directory -> Exit 1 with stderr
    missing_dir = tmp_path / "does_not_exist"
    proc_missing = subprocess.run(
        [python_exe, str(cli_tool), "--catalog-dir", str(missing_dir)],
        capture_output=True,
        text=True,
    )
    assert proc_missing.returncode == 1
    assert "[ERROR]" in proc_missing.stderr
    assert "does not exist" in proc_missing.stderr

    # Case C: Checksum mismatch -> Exit 1 with stderr
    rec = json.dumps({
        "schema_version": 1,
        "record_id": "test:cli_chk",
        "domain": "test.cli",
        "entity": {"canonical_name": "Test"},
        "facts": {},
        "review": {"status": "legacy_unverified"},
    })
    _create_test_catalog(tmp_path, shards=[("facts/cli.jsonl", "fact", rec + "\n", 1)])
    manifest_file = tmp_path / "manifest.json"
    manifest_data = json.loads(manifest_file.read_text(encoding="utf-8"))
    manifest_data["shards"][0]["sha256"] = "f" * 64
    manifest_file.write_text(json.dumps(manifest_data), encoding="utf-8")

    proc_chk = subprocess.run(
        [python_exe, str(cli_tool), "--catalog-dir", str(tmp_path)],
        capture_output=True,
        text=True,
    )
    assert proc_chk.returncode == 1
    assert "Checksum mismatch" in proc_chk.stderr
