# SCP CIRCUIT: W12-f1 — Conversion local tier cho S24 lookup fork.
"""T07/W12 — câu conversion của goldset được trả lời từ bảng hệ số local.

Bối cảnh W12 (câu hỏi owner): repo có hàng nghìn API/data source (catalog
2004 entries + 63 DataSource class) nhưng câu đơn giản dạng "1 km bằng bao
nhiêu mét?" không được trả lời từ dữ liệu. Root cause đo được: 11/143 câu
goldset (iso_conv_001..010, iso_phys_007) có source thật trong repo
(ConversionDataSource — bảng hệ số, pure) NHƯNG không được wire vào đường
lookup: catalog chỉ match rác substring, domain 'conversion' nằm ngoài
_KNOWLEDGE_DOMAINS → wiki không chạy → luôn LLM fallback.

W12-f1 wire ConversionDataSource vào resolve_lookup_data như tier local
đầu tiên (trước catalog). Test này khóa: (1) hệ số đúng theo bảng thật,
(2) expected_answer goldset nằm trong answer text (data-driven, không hardcode
đáp án — đọc từ goldset), (3) fail-closed cho đơn vị lạ/cross-category,
(4) fork e2e deliver PASS kèm relevance-gate marker.

HERMETIC: không mạng, không mock LLM — toàn bộ đường L0 + local tier.
Không skip/xfail (kỷ luật test SCP).
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scp.data_sources.conversion import ConversionDataSource
from scp.runtime.question_router import attempt_lookup_fork, resolve_lookup_data

GOLDSET_PATH = (
    Path(__file__).resolve().parents[2] / "scp" / "benchmark" / "iso_comprehensive.jsonl"
)


def _goldset_conversion_rows() -> list[dict]:
    """11 câu conversion: 10 câu category 'conversion' + iso_phys_007
    ('1 cal bằng bao nhiêu joule?' — cùng shape, category physics)."""
    rows = [
        json.loads(line)
        for line in GOLDSET_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return [r for r in rows if r["category"] == "conversion" or r["id"] == "iso_phys_007"]


def test_goldset_conversion_rows_present() -> None:
    rows = _goldset_conversion_rows()
    assert len(rows) == 11
    assert all(r.get("expected_answer") for r in rows)


def test_convert_uses_real_factor_tables() -> None:
    """Hệ số phải khớp bảng thật trong repo — chặn 'bịa' hệ số."""
    src = ConversionDataSource()
    assert src.convert(1, "mile", "km")["value"] == pytest.approx(1.609344)
    assert src.convert(1, "gallon", "lít")["value"] == pytest.approx(3.78541)
    assert src.convert(1, "km", "mét")["value"] == pytest.approx(1000.0)
    assert src.convert(1, "knot", "km/h")["value"] == pytest.approx(1.852, rel=1e-5)
    assert src.convert(100, "C", "F")["value"] == pytest.approx(212.0)


def test_convert_fail_closed_on_unknown_or_cross_category() -> None:
    src = ConversionDataSource()
    assert src.convert(1, "km", "kg") is None  # cross-category
    assert src.convert(1, "frobnicate", "widget") is None  # unknown units
    assert src.convert(None, "km", "m") is None
    assert src.answer_from_question("2 + 2 = ?") is None  # không phải conversion
    assert src.answer_from_question("Thủ đô Việt Nam?") is None


@pytest.mark.parametrize(
    "row", _goldset_conversion_rows(), ids=lambda r: r["id"]
)
def test_answer_from_question_contains_goldset_expected(row: dict) -> None:
    """Data-driven từ goldset: expected_answer phải nằm trong text trả lời
    (text compose từ bảng hệ số thật — không hardcode đáp án ở đây)."""
    src = ConversionDataSource()
    result = src.answer_from_question(row["question"])
    assert result is not None, f"conversion miss: {row['id']} {row['question']!r}"
    assert str(row["expected_answer"]) in result["text"], (
        f"{row['id']}: expected {row['expected_answer']!r} in {result['text']!r}"
    )


def test_resolve_lookup_data_prefers_local_conversion_tier() -> None:
    """Tier local phải chạy TRƯỚC catalog — chặn regression tier-order."""
    result = resolve_lookup_data("1 km bằng bao nhiêu mét?", domain="conversion")
    assert result is not None
    assert result["api_name"].startswith("ConversionDataSource")
    assert "1000" in result["text"]
    assert result["text"] == result["evidence"]


def test_resolve_lookup_data_non_conversion_still_fail_closed(monkeypatch) -> None:
    """Câu không conversion: tier local trả None → flow cũ (catalog miss →
    None với domain ngoài knowledge). Không fetch mạng nào xảy ra."""
    monkeypatch.setattr(
        "scp.runtime.question_router._catalog_candidates", lambda terms: ([], "")
    )
    result = resolve_lookup_data("Thủ đô Việt Nam?", domain="geography")
    assert result is None


@pytest.mark.asyncio
async def test_fork_delivers_conversion_answer_with_gate_marker() -> None:
    """E2E fork: /ask chuyển thành data-API answer từ bảng local, PASS kèm
    marker relevance_gate (điều kiện bắt buộc để adapter coi là
    already_judged — W2-d6)."""
    req = SimpleNamespace(
        question="1 mile bằng bao nhiêu km?",
        contexts=[],
        retrieved_context="",
        ai_answer="",
        session_id="w12-test",
    )
    response = await attempt_lookup_fork(req)
    assert response is not None, "fork phải bắt được câu conversion"
    assert response["verdict"] == "PASS"
    assert "1.609" in response["final_answer"]
    assert response["v98_classification"]["route"] == "lookup_data_api"
    assert response["relevance_gate"]["checked"] is True
    assert "ConversionDataSource" in response["final_answer"]
