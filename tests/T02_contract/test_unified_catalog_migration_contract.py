"""Contract tests for WP3, WP4, WP5, and WP7 Unified Knowledge Catalog Migration.

Verifies:
1. Literature queries resolve via KnowledgeStore when enabled.
2. Philosophy queries resolve via KnowledgeStore when enabled.
3. Management queries resolve via KnowledgeStore reference records when enabled.
4. Reference table lookups for SI units (BIPM) and WMO weather codes.
5. Quarantine invariant: staging legacy_unverified records are NEVER marked as verified.
6. Canned experts do NOT advertise unverified legacy knowledge as verified when KnowledgeStore is enabled.
7. Publisher tool updates manifest with correct SHA-256 checksums and valid record counts.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scp.knowledge.store import (
    KnowledgeSnapshot,
    KnowledgeStore,
    load_catalog,
)
from tools.publish_knowledge_catalog import (
    compute_sha256,
    count_and_inspect_records,
    publish_catalog,
)
from tools.validate_knowledge_catalog import validate_catalog_cli


@pytest.fixture
def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


@pytest.fixture
def catalog_dir(repo_root: Path) -> Path:
    return repo_root / "data" / "knowledge" / "catalog"


@pytest.fixture
def staging_dir(repo_root: Path) -> Path:
    return repo_root / "data" / "knowledge" / "staging" / "legacy_unverified"


@pytest.fixture
def store(catalog_dir: Path) -> KnowledgeStore:
    return KnowledgeStore(catalog_dir=catalog_dir, strict=True, check_hashes=True)


# ==============================================================================
# 1. Literature Domain Queries via KnowledgeStore
# ==============================================================================

def test_literature_store_records(store: KnowledgeStore) -> None:
    """Verify literature author and movement records exist with verified status and evidence."""
    # Author record
    shakespeare = store.get_by_id("humanities.literature:author-shakespeare")
    assert shakespeare is not None
    assert shakespeare.domain == "humanities.literature"
    assert shakespeare.entity.canonical_name == "William Shakespeare"
    assert "Hamlet" in shakespeare.facts["works"]
    assert shakespeare.review["status"] == "verified"
    assert shakespeare.evidence is not None
    assert shakespeare.evidence["source_id"] == "wikipedia"

    # Movement record
    romanticism = store.get_by_id("humanities.literature:movement-romanticism")
    assert romanticism is not None
    assert "Wordsworth" in romanticism.facts["description"]
    assert romanticism.review["status"] == "verified"


def test_literature_expert_integration(monkeypatch: pytest.MonkeyPatch, catalog_dir: Path) -> None:
    """Verify Literature expert uses KnowledgeStore when enabled."""
    from scp.runtime.experts.literature import Literature

    expert = Literature()

    # Enabled mode
    monkeypatch.setenv("SCP_UNIFIED_KNOWLEDGE_STORE", "on")
    monkeypatch.setenv("SCP_KNOWLEDGE_CATALOG_DIR", str(catalog_dir))

    resp_author = expert.predict("Who was William Shakespeare?")
    assert resp_author.confidence >= 0.8
    assert "Hamlet" in resp_author.answer
    assert resp_author.evidence.get("source") == "knowledge_store"
    assert resp_author.evidence.get("record_id") == "humanities.literature:author-shakespeare"

    resp_movement = expert.predict("What is romanticism?")
    assert resp_movement.confidence >= 0.8
    assert "Wordsworth" in resp_movement.answer
    assert resp_movement.evidence.get("source") == "knowledge_store"
    assert resp_movement.evidence.get("record_id") == "humanities.literature:movement-romanticism"


# ==============================================================================
# 2. Philosophy Domain Queries via KnowledgeStore
# ==============================================================================

def test_philosophy_store_records(store: KnowledgeStore) -> None:
    """Verify philosophy philosopher and concept records exist with verified status."""
    # Philosopher record
    socrates = store.get_by_id("humanities.philosophy:philosopher-socrates")
    assert socrates is not None
    assert socrates.domain == "humanities.philosophy"
    assert "Socratic method" in socrates.facts["info"]
    assert socrates.review["status"] == "verified"
    assert socrates.evidence is not None
    assert socrates.evidence["source_id"] == "stanford_encyclopedia_philosophy"

    # Concept record
    ethics = store.get_by_id("humanities.philosophy:concept-ethics")
    assert ethics is not None
    assert "normative ethics" in ethics.facts["description"]
    assert ethics.review["status"] == "verified"

    # Fallacy record with bilingual aliases
    fallacy = store.get_by_id("humanities.philosophy:concept-fallacy")
    assert fallacy is not None
    assert "ngụy biện" in fallacy.facts["description"].lower()


def test_philosophy_expert_integration(monkeypatch: pytest.MonkeyPatch, catalog_dir: Path) -> None:
    """Verify Philosophy expert uses KnowledgeStore when enabled."""
    from scp.runtime.experts.philosophy import Philosophy

    expert = Philosophy()

    monkeypatch.setenv("SCP_UNIFIED_KNOWLEDGE_STORE", "on")
    monkeypatch.setenv("SCP_KNOWLEDGE_CATALOG_DIR", str(catalog_dir))

    resp_socrates = expert.predict("Who was Socrates?")
    assert resp_socrates.confidence >= 0.8
    assert "Socratic method" in resp_socrates.answer
    assert resp_socrates.evidence.get("source") == "knowledge_store"
    assert resp_socrates.evidence.get("record_id") == "humanities.philosophy:philosopher-socrates"

    resp_ethics = expert.predict("What is ethics?")
    assert resp_ethics.confidence >= 0.8
    assert "normative ethics" in resp_ethics.answer
    assert resp_ethics.evidence.get("source") == "knowledge_store"
    assert resp_ethics.evidence.get("record_id") == "humanities.philosophy:concept-ethics"


# ==============================================================================
# 3. Management Domain Queries via KnowledgeStore References
# ==============================================================================

def test_management_reference_records(store: KnowledgeStore) -> None:
    """Verify management reference records are accessible via get_reference."""
    swot = store.get_reference("management.frameworks", "swot")
    assert swot is not None
    assert swot.table_name == "management.frameworks"
    assert swot.key == "swot"
    assert "Strengths, Weaknesses" in swot.data["summary"]
    assert swot.source_id == "wikipedia"

    porters = store.get_reference("management.frameworks", "porters_5_forces")
    assert porters is not None
    assert "threat of new entrants" in porters.data["summary"]


def test_management_expert_integration(monkeypatch: pytest.MonkeyPatch, catalog_dir: Path) -> None:
    """Verify Management expert queries reference catalog when enabled."""
    from scp.runtime.experts.management import Management

    expert = Management()

    monkeypatch.setenv("SCP_UNIFIED_KNOWLEDGE_STORE", "on")
    monkeypatch.setenv("SCP_KNOWLEDGE_CATALOG_DIR", str(catalog_dir))

    resp_swot = expert.predict("What is SWOT analysis?")
    assert resp_swot.confidence >= 0.8
    assert "Strengths, Weaknesses" in resp_swot.answer
    assert resp_swot.evidence.get("source") == "knowledge_store"
    assert resp_swot.evidence.get("record_id") == "management.frameworks:swot"

    resp_okr = expert.predict("What is OKR?")
    assert resp_okr.confidence >= 0.8
    assert "Andy Grove" in resp_okr.answer
    assert resp_okr.evidence.get("source") == "knowledge_store"


# ==============================================================================
# 4. Units & Weather Codes Reference Lookups
# ==============================================================================

def test_units_reference_table(store: KnowledgeStore) -> None:
    """Verify BIPM SI unit conversions in reference table 'units'."""
    km = store.get_reference("units", "km")
    assert km is not None
    assert km.source_id == "bipm"
    assert km.data["factor_to_base"] == 1000.0
    assert km.data["base_unit"] == "m"
    assert km.data["category"] == "length"

    kg = store.get_reference("units", "kg")
    assert kg is not None
    assert kg.data["factor_to_base"] == 1.0
    assert kg.data["category"] == "mass"

    s = store.get_reference("units", "s")
    assert s is not None
    assert s.data["factor_to_base"] == 1.0
    assert s.data["category"] == "time"

    bar = store.get_reference("units", "bar")
    assert bar is not None
    assert bar.data["factor_to_base"] == 100000.0
    assert bar.data["base_unit"] == "pa"


def test_weather_codes_reference_table(store: KnowledgeStore) -> None:
    """Verify WMO 4677 weather interpretation codes in reference table 'weather_codes'."""
    code_0 = store.get_reference("weather_codes", "0")
    assert code_0 is not None
    assert code_0.source_id == "wmo"
    assert code_0.data["text_vi"] == "trời quang"
    assert code_0.data["text_en"] == "clear sky"

    code_45 = store.get_reference("weather_codes", "45")
    assert code_45 is not None
    assert code_45.data["text_vi"] == "sương mù"
    assert code_45.data["text_en"] == "fog"

    code_95 = store.get_reference("weather_codes", "95")
    assert code_95 is not None
    assert code_95.data["text_vi"] == "dông"
    assert code_95.data["text_en"] == "thunderstorm"


# ==============================================================================
# 5. Quarantine & Staging Invariants (Anti-Placebo)
# ==============================================================================

def test_staging_records_are_never_marked_verified(staging_dir: Path, repo_root: Path) -> None:
    """Every record in staging/legacy_unverified MUST be status='legacy_unverified'."""
    assert staging_dir.exists(), f"Staging directory missing at {staging_dir}"

    expected_domains = [
        "agriculture",
        "art",
        "economics",
        "education",
        "engineering",
        "law",
        "military",
    ]

    total_records = 0
    for domain in expected_domains:
        file_path = staging_dir / f"{domain}.jsonl"
        assert file_path.exists(), f"Staging shard missing: {file_path}"

        lines = [line.strip() for line in file_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        assert len(lines) > 0, f"Staging shard is empty: {file_path}"

        for line_no, line in enumerate(lines, start=1):
            record = json.loads(line)
            total_records += 1

            # Fail-closed invariant: must NEVER be marked verified
            review = record.get("review", {})
            status = review.get("status")
            assert status == "legacy_unverified", (
                f"VIOLATION: Record '{record.get('record_id')}' in {file_path}:{line_no} has status='{status}' "
                "instead of 'legacy_unverified'!"
            )
            assert review.get("method") == "code_extraction_staging"

            # Provenance invariant: origin_file must exist in repo
            origin_rel = review.get("origin_file")
            assert origin_rel is not None
            origin_abs = repo_root / origin_rel
            assert origin_abs.exists(), f"Origin file not found: {origin_abs}"

    assert total_records >= 60, f"Expected at least 60 staging records, got {total_records}"


def test_canned_experts_do_not_advertise_legacy_as_verified(monkeypatch: pytest.MonkeyPatch, catalog_dir: Path) -> None:
    """When KnowledgeStore is enabled, canned experts must NOT return legacy facts as verified."""
    from scp.runtime.experts.agriculture import Agriculture
    from scp.runtime.experts.law import Law

    monkeypatch.setenv("SCP_UNIFIED_KNOWLEDGE_STORE", "on")
    monkeypatch.setenv("SCP_KNOWLEDGE_CATALOG_DIR", str(catalog_dir))

    # Agriculture expert
    agri = Agriculture()
    resp_agri = agri.predict("what is agriculture")
    # Must NOT return legacy unverified facts with source='local_knowledge'
    assert resp_agri.evidence.get("source") != "local_knowledge"

    # Law expert
    law = Law()
    resp_law = law.predict("what is a contract")
    assert resp_law.evidence.get("source") != "local_knowledge"


# ==============================================================================
# 6. Catalog Publisher and Manifest Integrity
# ==============================================================================

def test_publisher_tool_manifest_integrity(catalog_dir: Path, tmp_path: Path) -> None:
    """Publisher tool correctly computes SHA-256 and record counts for all registered shards."""
    import shutil
    temp_catalog = tmp_path / "catalog"
    shutil.copytree(catalog_dir, temp_catalog)

    # Run publisher on temporary copy to prevent dirtying git-tracked manifest.json
    exit_code = publish_catalog(catalog_dir=temp_catalog)
    assert exit_code == 0, "publish_catalog failed"

    # Validate manifest contents
    manifest_path = temp_catalog / "manifest.json"
    manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))

    shards = manifest_data.get("shards", [])
    assert len(shards) >= 6

    for shard in shards:
        shard_path = temp_catalog / shard["path"]
        assert shard_path.exists(), f"Registered shard file does not exist: {shard_path}"

        # Recalculate hash independently
        expected_sha = compute_sha256(shard_path)
        assert shard["sha256"] == expected_sha, f"SHA-256 mismatch for shard {shard['path']}"

        # Recount records independently
        expected_count, _, _ = count_and_inspect_records(shard_path)
        assert shard["record_count"] == expected_count, f"Record count mismatch for shard {shard['path']}"

    # Verify catalog passes strict validation CLI
    val_exit = validate_catalog_cli(catalog_dir=temp_catalog, strict=True, check_hashes=True)
    assert val_exit == 0
