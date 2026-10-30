# SCP CIRCUIT: Knowledge Reader Tiers Contract (CỔNG 1 đích-b).
"""Contract tests for knowledge reader tiers:
- Tier 1: KB news_fact reader for today's news queries ('tin tức hôm nay về serbia')
- Tier 2: Statutory standard for legal/statutory queries ('Điều 12 Bộ luật Lao động 2019', 'Nghị định 85/2016')
- Tier 3: Canonical document for general knowledge queries ('Thủ đô của Pháp', 'Tốc độ ánh sáng trong chân không')
- Anti-Goodhart Mutant Test: disabling news reader kills the tier (fail/fallback).
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from scp.runtime import question_router as qr
from scp.runtime.question_router import attempt_lookup_fork, resolve_lookup_data


def _run_fork(question: str) -> dict[str, Any] | None:
    req = SimpleNamespace(
        question=question,
        contexts=[],
        retrieved_context="",
        ai_answer="",
        session_id="reader-tier-contract-test",
    )
    return asyncio.run(attempt_lookup_fork(req))


def test_01_query_serbia_today_news_reads_kb_news_fact(monkeypatch) -> None:
    """Test 1: Query tin tức hôm nay về serbia -> trả về KB news_fact (reader_tier == 'kb_news_fact'),
    KHÔNG kéo Wikipedia cũ (Mai An Tiêm)."""
    # Egress deny to guarantee no live network leaks
    monkeypatch.setenv("SCP_EGRESS_MODE", "deny")

    question = "tin tức hôm nay về serbia"
    res = resolve_lookup_data(question)

    assert res is not None, "Lookup fork phải tìm thấy news_fact trong KB"
    assert res.get("reader_tier") == "kb_news_fact"
    assert res.get("source_indicator") == "KB:news_fact"
    assert res.get("api_name") == "kb_news_fact"
    assert "serbia" in res.get("text", "").lower()
    assert "mai an tiêm" not in res.get("text", "").lower()

    # Edge cases: các biến thể cấu trúc thời gian + loại-thông-tin
    for variant in ("tin mới về serbia", "tin nóng về serbia"):
        res_var = resolve_lookup_data(variant)
        assert res_var is not None, f"Phải tìm thấy news_fact cho biến thể '{variant}'"
        assert res_var.get("reader_tier") == "kb_news_fact"
        assert "serbia" in res_var.get("text", "").lower()

    # Negative check: sự kiện lịch sử không được nuốt vào news_fact
    res_hist = resolve_lookup_data("sự kiện lịch sử năm 1945")
    if res_hist is not None:
        assert res_hist.get("reader_tier") != "kb_news_fact"


def test_02_query_legal_statutory_standards(monkeypatch) -> None:
    """Test 2: Query pháp lý VN ('Điều 12 Bộ luật Lao động 2019', 'Nghị định 85/2016 Cấp độ 4 ATTT')
    -> trả về reader_tier == 'statutory_standard'."""
    monkeypatch.setenv("SCP_EGRESS_MODE", "deny")

    # Case 2a: Điều 12 Bộ luật Lao động 2019
    q_labor = "Điều 12 Bộ luật Lao động 2019"
    res_labor = resolve_lookup_data(q_labor)
    assert res_labor is not None, "Phải tìm thấy Điều 12 BLLĐ 2019"
    assert res_labor.get("reader_tier") == "statutory_standard"
    assert res_labor.get("source_indicator") == "KB:statutory"
    assert "lao động" in res_labor.get("text", "").lower()

    # Case 2b: Nghị định 85/2016 Cấp độ 4 ATTT
    q_decree = "Nghị định 85/2016 Cấp độ 4 ATTT"
    res_decree = resolve_lookup_data(q_decree)
    assert res_decree is not None, "Phải tìm thấy Nghị định 85/2016"
    assert res_decree.get("reader_tier") == "statutory_standard"
    assert res_decree.get("source_indicator") == "KB:statutory"
    assert "cấp độ 4" in res_decree.get("text", "").lower()


def test_03_query_general_knowledge_canonical_document(monkeypatch) -> None:
    """Test 3: Query tri thức chung ('Thủ đô của Pháp', 'Tốc độ ánh sáng trong chân không')
    -> trả về reader_tier == 'canonical_document', domain đúng Geography / Physics, không bị ép sang Legal."""
    monkeypatch.setenv("SCP_EGRESS_MODE", "deny")

    # Case 3a: Thủ đô của Pháp
    q_geo = "Thủ đô của Pháp"
    res_geo = resolve_lookup_data(q_geo)
    assert res_geo is not None, "Phải tìm thấy Thủ đô của Pháp"
    assert res_geo.get("reader_tier") == "canonical_document"
    assert res_geo.get("source_indicator") == "KB:canonical"
    assert str(res_geo.get("domain", "")).lower() == "geography"
    assert "paris" in res_geo.get("text", "").lower()

    # Case 3b: Tốc độ ánh sáng trong chân không
    q_physics = "Tốc độ ánh sáng trong chân không"
    res_physics = resolve_lookup_data(q_physics)
    assert res_physics is not None, "Phải tìm thấy Tốc độ ánh sáng"
    assert res_physics.get("reader_tier") == "canonical_document"
    assert res_physics.get("source_indicator") == "KB:canonical"
    assert str(res_physics.get("domain", "")).lower() == "physics"
    assert "299,792,458" in res_physics.get("text", "")

    # Case 3c: Khoa học / định luật không bị ép sang statutory_standard
    res_law = resolve_lookup_data("định luật Ohm")
    if res_law is not None:
        assert res_law.get("reader_tier") != "statutory_standard"


def test_04_anti_goodhart_news_mutant_killed(monkeypatch) -> None:
    """Test 4: Anti-Goodhart Mutant Test: khi tắt reader news (hoặc monkeypatch news_lookup = None),
    câu hỏi tin tức hôm nay chuyển sang fallback / fail -> mutant bị KILLED 100%!"""
    question = "tin tức hôm nay về serbia"

    # Step 1: Baseline verify that with news reader working, it yields kb_news_fact
    res_baseline = resolve_lookup_data(question)
    assert res_baseline is not None
    assert res_baseline.get("reader_tier") == "kb_news_fact"

    # Step 2: Inject mutation: kill news_lookup
    monkeypatch.setattr(qr, "news_lookup", lambda *args, **kwargs: None)
    monkeypatch.setattr(qr, "_wiki_lookup", lambda *args, **kwargs: None)
    monkeypatch.setattr(qr, "_bridge_lookup", lambda **kwargs: None)

    res_mutant = resolve_lookup_data(question)

    # Mutant must NOT return kb_news_fact (it falls back to None or non-news tier)
    if res_mutant is not None:
        assert res_mutant.get("reader_tier") != "kb_news_fact", "Mutant survived: news_lookup was bypassed!"
    else:
        # None is expected when all fallbacks fail-closed
        assert res_mutant is None, "Mutant correctly killed: returned None fallback"


def test_05_attempt_lookup_fork_propagates_reader_tier_and_indicator(monkeypatch) -> None:
    """Test 5: attempt_lookup_fork đưa reader_tier và source_indicator vào v98_classification và reasoning."""
    monkeypatch.setenv("SCP_EGRESS_MODE", "deny")

    async def _mock_route(q: str, **kwargs: Any) -> qr.RouteDecision:
        return qr.RouteDecision(
            intent=qr.LOOKUP,
            domain="general",
            confidence=0.85,
            via="l0-keyword",
            reason="test_reader_tier",
            lane=qr.LANE_FACTUAL,
            language="vi",
        )

    monkeypatch.setattr(qr, "route_question_async", _mock_route)

    fork_res = _run_fork("tin tức hôm nay về serbia")
    assert fork_res is not None
    assert fork_res.get("verdict") == "PASS"
    v98 = fork_res.get("v98_classification", {})
    assert v98.get("reader_tier") == "kb_news_fact"
    assert v98.get("source_indicator") == "KB:news_fact"
    assert "tier=kb_news_fact" in fork_res.get("reasoning", "")


def test_06_statutory_regex_precision_and_no_leak(monkeypatch) -> None:
    """Test 6: Kiểm tra độ chính xác regex statutory và chống leak statutory_standard sang câu hỏi phi pháp luật.
    - 'điều kiện tự nhiên của Việt Nam' không match _STATUTORY_TERMS_RE
    - 'quy luật cung cầu' không trả về statutory_standard
    """
    from scp.runtime.domain_data_extended import _STATUTORY_TERMS_RE

    # Check 6a: Regex precision - 'điều' không kèm số thứ tự không được coi là statutory
    non_statutory_samples = [
        "điều kiện tự nhiên của Việt Nam",
        "điều hòa nhiệt độ",
        "điều khiển từ xa",
        "điều trị bệnh",
    ]
    for sample in non_statutory_samples:
        assert not bool(_STATUTORY_TERMS_RE.search(sample.lower())), (
            f"Overbroad regex: '{sample}' bị match nhầm là statutory!"
        )

    # Check 6b: 'điều' kèm số thứ tự hợp lệ vẫn phải match
    assert bool(_STATUTORY_TERMS_RE.search("điều 12 bộ luật lao động".lower()))
    assert bool(_STATUTORY_TERMS_RE.search("theo điều 5 nghị định".lower()))

    # Check 6c: Anti-leak - 'quy luật cung cầu' không được trả về statutory_standard
    from scp.runtime.domain_data_extended import resolve_extended_and_fts_lookup
    res_ext = resolve_extended_and_fts_lookup("quy luật cung cầu", "quy luật cung cầu")
    assert res_ext is None, (
        "Statutory standard leak in extended/fts: 'quy luật cung cầu' trả về doc thay vì None!"
    )

    monkeypatch.setenv("SCP_EGRESS_MODE", "deny")
    res_market = resolve_lookup_data("quy luật cung cầu")
    if res_market is not None:
        assert res_market.get("reader_tier") != "statutory_standard", (
            f"Statutory standard leak: 'quy luật cung cầu' trả về {res_market.get('reader_tier')}!"
        )
        assert res_market.get("source_indicator") != "KB:statutory"


def test_07_earthquake_valdivia_canonical_reader_tier(monkeypatch) -> None:
    """Test 7: Resolver địa chấn USGS ('động đất valdivia') phải được gán đầy đủ:
    reader_tier == 'canonical_document' và source_indicator == 'KB:canonical'.
    """
    monkeypatch.setenv("SCP_EGRESS_MODE", "deny")
    res_valdivia = resolve_lookup_data("động đất valdivia")
    assert res_valdivia is not None, "Phải tìm thấy trận động đất Valdivia"
    assert res_valdivia.get("reader_tier") == "canonical_document", (
        f"Lỗi thiếu reader_tier: nhận {res_valdivia.get('reader_tier')}"
    )
    assert res_valdivia.get("source_indicator") == "KB:canonical", (
        f"Lỗi thiếu source_indicator: nhận {res_valdivia.get('source_indicator')}"
    )
    assert "valdivia" in res_valdivia.get("text", "").lower() or "chilean" in res_valdivia.get("text", "").lower()


def test_08_biology_and_geology_resolvers_canonical_reader_tier(monkeypatch) -> None:
    """Test 8: Resolver Sinh học và Địa chất cục bộ phải được gán reader_tier == 'canonical_document'
    và source_indicator == 'KB:canonical'.
    """
    from scp.runtime import domain_data_fork
    from scp.runtime.domain_data_fork import resolve_domain_data_lookup

    # Biology resolver test
    bio = domain_data_fork._get_biology()
    monkeypatch.setattr(bio, "_cells", {
        "mitochondria": {"size": "0.5-1.0 µm", "function": "Hô hấp tế bào"},
    })
    res_bio = resolve_domain_data_lookup("ty thể")
    assert res_bio is not None, "Phải tìm thấy thông tin sinh học (ty thể)"
    assert res_bio.get("reader_tier") == "canonical_document", (
        f"Biology resolver thiếu reader_tier: {res_bio.get('reader_tier')}"
    )
    assert res_bio.get("source_indicator") == "KB:canonical", (
        f"Biology resolver thiếu source_indicator: {res_bio.get('source_indicator')}"
    )

    # Geology resolver test
    monkeypatch.setattr(domain_data_fork, "_MOHS_SCALE", {
        "kim cương": (10, "Kim cương", "Khoáng vật cứng nhất"),
    })
    res_geo = resolve_domain_data_lookup("thang mohs kim cương")
    assert res_geo is not None, "Phải tìm thấy thông tin địa chất (thang mohs kim cương)"
    assert res_geo.get("reader_tier") == "canonical_document", (
        f"Geology resolver thiếu reader_tier: {res_geo.get('reader_tier')}"
    )
    assert res_geo.get("source_indicator") == "KB:canonical", (
        f"Geology resolver thiếu source_indicator: {res_geo.get('source_indicator')}"
    )

