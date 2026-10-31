"""Contract & Anti-Placebo Tests for dich-b Verified Knowledge Catalog Reader Tier.

Verifies the Wire-or-Kill integration of Unified Knowledge Catalog shards into
dich-b (`scp/runtime/domain_data_extended.py` -> `scp/runtime/domain_data_fork.py`
-> `scp/runtime/question_router.py`):
  1. `humanities.literature` (12 verified fact records) -> `reader_tier="catalog_verified"`, `source_indicator="KB:catalog"`
  2. `humanities.philosophy` (18 verified fact records) -> `reader_tier="catalog_verified"`, `source_indicator="KB:catalog"`
  3. `management.frameworks` (16 reference records) -> `reader_tier="catalog_verified"`, `source_indicator="KB:catalog"`
  4. `weather_codes` (28 WMO 4677 records) & `units` (23 BIPM SI records) -> `reader_tier="catalog_verified"`, `source_indicator="KB:catalog"`
  5. Fail-closed SHA-256 tamper & `review.status == "verified"` enforcement
  6. Anti-placebo negative guards (3-letter `mác` homonym, `memory management`, `NIST AI RMF`, `ad hominem` precedence)
  7. Kill-switch (`SCP_CATALOG_READER_TIER=off`) mutant verification
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from scp.runtime.domain_data_extended import (
    resolve_catalog_verified_lookup,
    resolve_extended_and_fts_lookup,
)
from scp.runtime.domain_data_fork import resolve_domain_data_lookup
from scp.runtime.question_router import LOOKUP, RouteDecision, attempt_lookup_fork


def _mock_router_for_fork(monkeypatch: pytest.MonkeyPatch, domain: str = "general") -> None:
    """Hermetic L0/L2 route + relevance gate setup for attempt_lookup_fork tests."""
    import scp.runtime.answer_relevance as ar
    import scp.runtime.question_router as qr
    from scp.runtime.answer_relevance import RELEVANT_EMBED, RelevanceAssessment

    async def _fake_route(q: str, gateway=None):
        return RouteDecision(
            intent=LOOKUP,
            domain=domain,
            confidence=0.95,
            via="l0-test-hermetic",
            reason="test_catalog_verified_tier",
            lane="factual",
            language="vi",
        )

    async def _fake_fork_gate(req, question, relevant, data, decision):
        return RelevanceAssessment(
            status=RELEVANT_EMBED,
            score=0.88,
            tau_low=0.35,
            tau_high=0.55,
            embed_state="ok",
            model_id="test-hermetic",
            reason="hermetic contract test relevance",
            path="lookup_fork",
        )

    monkeypatch.setattr(qr, "route_question_async", _fake_route)
    monkeypatch.setattr(ar, "lookup_fork_gate", _fake_fork_gate)


def test_01_literature_catalog_verified_tier_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    """`humanities.literature` verified records resolve via `catalog_verified` (`KB:catalog`)."""
    q1 = "Tác giả William Shakespeare"
    res1 = resolve_domain_data_lookup(q1)
    assert res1 is not None
    assert res1["reader_tier"] == "catalog_verified"
    assert res1["source_indicator"] == "KB:catalog"
    assert res1["domain"] == "humanities.literature"
    assert res1["record_id"] == "humanities.literature:author-shakespeare"
    assert res1["dataset_version"] == "2026.10.27.1"
    assert "Hamlet" in res1["text"]
    assert res1["api_url"].startswith("https://")

    # Lookup by notable work in catalog (`works` field)
    q2 = "Tác phẩm Les Misérables của Victor Hugo"
    res2 = resolve_domain_data_lookup(q2)
    assert res2 is not None
    assert res2["reader_tier"] == "catalog_verified"
    assert res2["source_indicator"] == "KB:catalog"
    assert res2["record_id"] == "humanities.literature:author-hugo"
    assert "Victor Hugo" in res2["text"]

    # Lookup literary movement
    q3 = "Chủ nghĩa lãng mạn Romanticism"
    res3 = resolve_domain_data_lookup(q3)
    assert res3 is not None
    assert res3["reader_tier"] == "catalog_verified"
    assert res3["source_indicator"] == "KB:catalog"
    assert res3["record_id"] == "humanities.literature:movement-romanticism"

    # E2E propagation through attempt_lookup_fork
    _mock_router_for_fork(monkeypatch, domain="general")
    req = SimpleNamespace(
        question=q1,
        contexts=[],
        retrieved_context="",
        ai_answer="",
        session_id="test-tier-literature",
    )
    fork_res = asyncio.run(attempt_lookup_fork(req))
    assert fork_res is not None
    assert fork_res["verdict"] == "PASS"
    v98 = fork_res["v98_classification"]
    assert v98["reader_tier"] == "catalog_verified"
    assert v98["source_indicator"] == "KB:catalog"
    assert "tier=catalog_verified" in fork_res["reasoning"]


def test_02_philosophy_catalog_verified_tier_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    """`humanities.philosophy` verified records resolve via `catalog_verified` (`KB:catalog`)."""
    for query, expected_id, expected_substr in [
        ("Triết gia Socrates", "humanities.philosophy:philosopher-socrates", "Socrates"),
        ("Triết gia Immanuel Kant", "humanities.philosophy:philosopher-kant", "Immanuel Kant"),
        ("Tư tưởng Khổng Tử", "humanities.philosophy:philosopher-confucius", "Confucius"),
        ("Triết gia Lão Tử", "humanities.philosophy:philosopher-laozi", "Laozi"),
        ("Chủ nghĩa khắc kỷ Stoicism", "humanities.philosophy:concept-stoicism", "Stoicism"),
        ("Nhận thức luận Epistemology", "humanities.philosophy:concept-epistemology", "Epistemology"),
        ("Logic học", "humanities.philosophy:concept-logic", "Logic"),
    ]:
        res = resolve_domain_data_lookup(query)
        assert res is not None, f"Expected catalog hit for {query!r}"
        assert res["reader_tier"] == "catalog_verified"
        assert res["source_indicator"] == "KB:catalog"
        assert res["domain"] == "humanities.philosophy"
        assert res["record_id"] == expected_id
        assert expected_substr in res["text"]

    _mock_router_for_fork(monkeypatch, domain="general")
    req = SimpleNamespace(
        question="Chủ nghĩa khắc kỷ Stoicism",
        contexts=[],
        retrieved_context="",
        ai_answer="",
        session_id="test-tier-philosophy",
    )
    fork_res = asyncio.run(attempt_lookup_fork(req))
    assert fork_res is not None
    assert fork_res["verdict"] == "PASS"
    assert fork_res["v98_classification"]["reader_tier"] == "catalog_verified"
    assert fork_res["v98_classification"]["source_indicator"] == "KB:catalog"


def test_03_management_frameworks_catalog_verified_tier_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`management.frameworks` reference records resolve with longest-alias disambiguation."""
    for query, expected_id, expected_substr in [
        ("Mô hình phân tích SWOT", "management.frameworks:swot", "SWOT"),
        ("Mô hình Porter's 5 Forces", "management.frameworks:porters_5_forces", "Porter"),
        ("Phương pháp quản trị OKR", "management.frameworks:okr", "OKR"),
        ("Phương pháp Six Sigma", "management.frameworks:six_sigma", "Six Sigma"),
        ("Quản trị thay đổi Change Management", "management.frameworks:change_management", "Kotter"),
        ("Lean Management", "management.frameworks:lean", "Lean"),
        ("Khái niệm quản trị", "management.frameworks:management", "Fayol"),
    ]:
        res = resolve_domain_data_lookup(query)
        assert res is not None, f"Expected management catalog hit for {query!r}"
        assert res["reader_tier"] == "catalog_verified"
        assert res["source_indicator"] == "KB:catalog"
        assert res["domain"] == "management"
        assert res["record_id"] == expected_id
        assert expected_substr.lower() in res["text"].lower()

    _mock_router_for_fork(monkeypatch, domain="general")
    req = SimpleNamespace(
        question="Mô hình phân tích SWOT",
        contexts=[],
        retrieved_context="",
        ai_answer="",
        session_id="test-tier-management",
    )
    fork_res = asyncio.run(attempt_lookup_fork(req))
    assert fork_res is not None
    assert fork_res["verdict"] == "PASS"
    assert fork_res["v98_classification"]["reader_tier"] == "catalog_verified"
    assert fork_res["v98_classification"]["source_indicator"] == "KB:catalog"


def test_04_weather_codes_and_units_reference_catalog_resolution() -> None:
    """`weather_codes` (WMO 4677) and `units` (BIPM SI) reference shards resolve via `catalog_verified`."""
    wmo_95 = resolve_domain_data_lookup("Mã thời tiết WMO 95 là gì?")
    assert wmo_95 is not None
    assert wmo_95["reader_tier"] == "catalog_verified"
    assert wmo_95["source_indicator"] == "KB:catalog"
    assert wmo_95["record_id"] == "weather_codes:95"
    assert "Thunderstorm" in wmo_95["text"] or "Dông" in wmo_95["text"]

    wmo_45 = resolve_domain_data_lookup("WMO weather code 45")
    assert wmo_45 is not None
    assert wmo_45["reader_tier"] == "catalog_verified"
    assert wmo_45["record_id"] == "weather_codes:45"
    assert "Fog" in wmo_45["text"] or "Sương mù" in wmo_45["text"]

    unit_pa = resolve_domain_data_lookup("Đơn vị đo lường hệ SI pascal")
    assert unit_pa is not None
    assert unit_pa["reader_tier"] == "catalog_verified"
    assert unit_pa["source_indicator"] == "KB:catalog"
    assert unit_pa["record_id"] == "units:pa"
    assert "Pascal" in unit_pa["text"]


def test_05_fail_closed_on_sha256_tamper_and_unverified_records(tmp_path: Path) -> None:
    """Tampered SHA-256 shard or `review.status != 'verified'` MUST fail-closed (return None)."""
    repo_root = Path(__file__).resolve().parents[2]
    src_catalog = repo_root / "data" / "knowledge" / "catalog"

    # Case A: SHA-256 checksum mismatch -> fail-closed
    tampered_dir = tmp_path / "catalog_tampered"
    shutil.copytree(src_catalog, tampered_dir)
    phil_shard = tampered_dir / "facts" / "humanities.philosophy.jsonl"
    phil_shard.write_text(phil_shard.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    res_tampered = resolve_catalog_verified_lookup(
        "Triết gia Socrates",
        "triết gia socrates",
        catalog_dir=tampered_dir,
    )
    assert res_tampered is None, "Tampered catalog shard must fail-closed (return None)"

    # Case B: Valid SHA-256 manifest, but record has review.status = "legacy_unverified" -> fail-closed
    unverified_dir = tmp_path / "catalog_unverified"
    shutil.copytree(src_catalog, unverified_dir)
    phil_shard_2 = unverified_dir / "facts" / "humanities.philosophy.jsonl"
    lines = []
    for raw_line in phil_shard_2.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip():
            continue
        obj = json.loads(raw_line)
        if obj.get("record_id") == "humanities.philosophy:philosopher-socrates":
            obj["review"]["status"] = "legacy_unverified"
        lines.append(json.dumps(obj, ensure_ascii=False))
    new_bytes = ("\n".join(lines) + "\n").encode("utf-8")
    phil_shard_2.write_bytes(new_bytes)

    manifest_path = unverified_dir / "manifest.json"
    manifest_obj = json.loads(manifest_path.read_text(encoding="utf-8"))
    for shard_entry in manifest_obj["shards"]:
        if shard_entry["path"] == "facts/humanities.philosophy.jsonl":
            shard_entry["sha256"] = hashlib.sha256(new_bytes).hexdigest()
    manifest_path.write_text(json.dumps(manifest_obj, indent=2), encoding="utf-8")

    res_unverified = resolve_catalog_verified_lookup(
        "Triết gia Socrates",
        "triết gia socrates",
        catalog_dir=unverified_dir,
    )
    assert res_unverified is None, "Unverified record (status='legacy_unverified') must fail-closed"


def test_06_negative_anti_placebo_and_no_collision_guards() -> None:
    """Negative anti-placebo checks: no false positives on homonyms or other tiers."""
    # 1. Economic concept must not leak
    assert resolve_extended_and_fts_lookup("quy luật cung cầu", "quy luật cung cầu") is None

    # 2. 3-letter 'mác' in 'nhãn mác' must NOT match Karl Marx
    assert resolve_catalog_verified_lookup("nhãn mác quần áo là gì", "nhãn mác quần áo là gì") is None

    # 3. Technical 'memory management' / 'database management' must NOT match management.frameworks:management
    assert resolve_catalog_verified_lookup("memory management trong hệ điều hành", "memory management trong hệ điều hành") is None

    # 4. Specific logical fallacy 'ad hominem' stays on canonical_document (CanonicalFTSWarehouse), not hijacked by catalog
    ad_hom = resolve_domain_data_lookup("Ngụy biện ad hominem là gì?")
    assert ad_hom is not None
    assert ad_hom["reader_tier"] == "canonical_document"
    assert ad_hom["source_indicator"] == "KB:canonical"

    # General philosophical fallacy concept (not in FTS or extended_local) resolves via catalog_verified
    phil_fallacy = resolve_domain_data_lookup("Khái niệm ngụy biện fallacy")
    assert phil_fallacy is not None
    assert phil_fallacy["reader_tier"] == "catalog_verified"
    assert phil_fallacy["record_id"] == "humanities.philosophy:concept-fallacy"

    # 5. NIST AI RMF stays on statutory_standard (CanonicalFTSWarehouse), not hijacked by management.frameworks
    nist = resolve_domain_data_lookup("NIST AI Risk Management Framework")
    assert nist is not None
    assert nist["reader_tier"] in ("statutory_standard", "canonical_document")


def test_07_kill_switch_disables_catalog_reader_tier(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mutant / kill-switch check: SCP_CATALOG_READER_TIER=off disables catalog_verified tier."""
    monkeypatch.setenv("SCP_CATALOG_READER_TIER", "off")
    assert resolve_catalog_verified_lookup("Triết gia Socrates", "triết gia socrates") is None
    assert resolve_domain_data_lookup("Triết gia Socrates") is None
    assert resolve_domain_data_lookup("Tác giả William Shakespeare") is None
    assert resolve_domain_data_lookup("Mô hình phân tích SWOT") is None


def test_08_all_catalog_shards_and_records_reachable_in_dichb() -> None:
    """Exhaustive check: every record in humanities.literature (12), humanities.philosophy (18),
    management.frameworks (16), weather_codes (28), and units (23) is reachable via resolve_catalog_verified_lookup.
    """
    from scp.knowledge.store import KnowledgeStore

    store = KnowledgeStore()
    lit_recs = store.find_candidates("humanities.literature", "")
    phil_recs = store.find_candidates("humanities.philosophy", "")
    mgmt_refs = store.list_reference("management.frameworks")
    wmo_refs = store.list_reference("weather_codes")
    unit_refs = store.list_reference("units")

    assert len(lit_recs) == 12
    assert len(phil_recs) == 18
    assert len(mgmt_refs) == 16
    assert len(wmo_refs) == 28
    assert len(unit_refs) == 23

    for rec in lit_recs + phil_recs:
        q = rec.entity.canonical_name
        if q.lower() == "logic":
            q = "Logic học"
        hit = resolve_catalog_verified_lookup(q)
        assert hit is not None, f"Unreachable fact record: {rec.record_id} ({q})"
        assert hit["record_id"] == rec.record_id
        assert hit["reader_tier"] == "catalog_verified"
        assert hit["source_indicator"] == "KB:catalog"

    for ref in mgmt_refs:
        q = str(ref.data.get("name") or ref.key)
        if ref.key == "management":
            q = "What is management"
        hit = resolve_catalog_verified_lookup(q)
        assert hit is not None, f"Unreachable management record: {ref.record_id} ({q})"
        assert hit["record_id"] == ref.record_id
        assert hit["reader_tier"] == "catalog_verified"
        assert hit["source_indicator"] == "KB:catalog"

    for ref in wmo_refs:
        q = f"Mã thời tiết WMO {ref.key}"
        hit = resolve_catalog_verified_lookup(q)
        assert hit is not None, f"Unreachable weather_codes record: {ref.record_id}"
        assert hit["record_id"] == ref.record_id
        assert hit["reader_tier"] == "catalog_verified"
        assert hit["source_indicator"] == "KB:catalog"

    for ref in unit_refs:
        u_name = str(ref.data.get("name") or ref.key)
        q = f"Đơn vị đo lường hệ SI {u_name}"
        hit = resolve_catalog_verified_lookup(q)
        assert hit is not None, f"Unreachable units record: {ref.record_id} ({q})"
        assert hit["record_id"] == ref.record_id
        assert hit["reader_tier"] == "catalog_verified"
        assert hit["source_indicator"] == "KB:catalog"

