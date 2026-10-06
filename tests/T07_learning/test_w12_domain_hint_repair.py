# SCP CIRCUIT: W12-f2 — Domain hint repair đưa câu knowledge về wiki provider.
"""T07/W12 — 13 câu goldset LOOKUP có domain hint rơi vào domain khác
taxonomy (energy/heritage/reality/environment/foodtech/transport/finance)
nên rơi ngoài _KNOWLEDGE_DOMAINS → provider encyclopedic (Wikipedia) KHÔNG
bao giờ được thử, dù wiki là nguồn đúng cho câu encyclopedic.

Root cause đo được (probe trên 143 câu goldset):
  - 'mặt trời' nằm trong energy boost_keywords → 4 câu astronomy bị kéo sang
    energy (iso_astro_001/002/003/006).
  - registry 'reality' (fact-check) giữ 'nhiệt độ đóng băng'/'số avogadro'
    → iso_phys_003/005 sang reality thay vì physics.
  - 'vitamin'/'đường huyết' không có trong medical (iso_med_006/010 bị
    foodtech/transport nuốt vì substring); 'sa mạc' thiếu ở geography
    (iso_geo_008 bị heritage 'thế giới' nuốt); 'chứng khoán' thiếu ở finance
    (iso_fin_007); 'ssh'/'https' thiếu ở cybersecurity (iso_cyber_004/007
    bị transport 'port' nuốt); 'nguyên tử' thiếu boost chemistry
    (iso_chem_003 bị environment 'carbon' nuốt).

W12-f2 sửa taxonomy (domain_classifier.boost_keywords + domain_registry.
DOMAINS keywords) — intent KHÔNG đổi, goldset intent accuracy giữ nguyên.
Test khóa: (1) 13 hint repair đúng domain, (2) 10/13 rơi vào
_KNOWLEDGE_DOMAINS (wiki eligible), (3) wiki provider THẬT SỰ được gọi cho
câu astronomy (mock ở tầng wikipedia_client — không mock gate), (4) pin
side-effect: domain các câu energy/food/weather không đổi so với baseline.

HERMETIC: không mạng (wikipedia_client được mock, catalog stub).
Không skip/xfail (kỷ luật test SCP).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scp.runtime.question_router import _KNOWLEDGE_DOMAINS, resolve_lookup_data, route_question

GOLDSET_PATH = (
    Path(__file__).resolve().parents[2] / "scp" / "benchmark" / "iso_comprehensive.jsonl"
)

# (id, domain mong đợi sau repair) — 13 câu mis-hint đo được ở W12 inventory.
_HINT_REPAIR_TARGETS = [
    ("iso_astro_001", "astronomy"),
    ("iso_astro_002", "astronomy"),
    ("iso_astro_003", "astronomy"),
    ("iso_astro_006", "astronomy"),
    ("iso_phys_003", "physics"),
    ("iso_phys_005", "physics"),
    ("iso_chem_003", "chemistry"),
    ("iso_geo_008", "geography"),
    ("iso_med_006", "medical"),
    ("iso_med_010", "medical"),
    ("iso_cyber_004", "cybersecurity"),
    ("iso_cyber_007", "cybersecurity"),
    ("iso_fin_007", "finance"),
]


def _goldset_rows() -> dict[str, dict]:
    return {
        r["id"]: r
        for r in (
            json.loads(line)
            for line in GOLDSET_PATH.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    }


@pytest.mark.parametrize("qid,expected_domain", _HINT_REPAIR_TARGETS)
def test_mis_hinted_goldset_questions_get_correct_domain(qid, expected_domain):
    rows = _goldset_rows()
    decision = route_question(rows[qid]["question"])
    assert decision.intent == "LOOKUP", f"{qid}: intent không được đổi"
    assert decision.domain == expected_domain, (
        f"{qid}: {rows[qid]['question']!r} → {decision.domain}, want {expected_domain}"
    )


def test_repaired_knowledge_questions_are_wiki_eligible():
    """10/13 câu repair rơi vào _KNOWLEDGE_DOMAINS → provider encyclopedic
    được phép chạy (3 câu còn lại: cybersecurity/finance — domain đúng là mục
    đích monitoring; wiki không phải nguồn phù hợp nên cố tình không thêm)."""
    rows = _goldset_rows()
    eligible = 0
    for qid, expected_domain in _HINT_REPAIR_TARGETS:
        decision = route_question(rows[qid]["question"])
        assert decision.domain == expected_domain
        if decision.domain in _KNOWLEDGE_DOMAINS:
            eligible += 1
    assert eligible == 10


@pytest.mark.asyncio
async def test_wiki_provider_reached_for_repaired_astronomy_domain(monkeypatch):
    """End-to-end gate: trước repair domain 'energy' chặn wiki; sau repair
    'astronomy' ∈ _KNOWLEDGE_DOMAINS → wikipedia_client được gọi (mock tầng
    client — gate domain chạy THẬT)."""
    import scp.core.wikipedia_client as wiki_mod
    from scp.runtime import question_router as qr

    decision = route_question("Hành tinh gần Mặt Trời nhất?")
    assert decision.domain == "astronomy"

    class StubCatalog:
        def search(self, query="", category=None, auth=None, limit=25):
            return []

    import scp.data_sources.free_api_catalog as cat_mod

    monkeypatch.setattr(cat_mod, "get_catalog", lambda data_dir="data": StubCatalog())
    calls: list[str] = []

    def _stub_search_then_summary(query, lang="en", timeout=8):
        calls.append(query)
        return {
            "title": "Sao Thủy",
            "extract": "Sao Thủy là hành tinh nhỏ nhất và gần Mặt Trời nhất trong Hệ Mặt Trời.",
            "url": "https://vi.wikipedia.org/wiki/Sao_Thủy",
        }

    monkeypatch.setattr(wiki_mod, "search_then_summary", _stub_search_then_summary)

    result = resolve_lookup_data("Hành tinh gần Mặt Trời nhất?", domain=decision.domain)
    assert calls, "wiki provider phải được gọi cho domain astronomy"
    assert result is not None
    assert result["api_name"].startswith("Wikipedia")


@pytest.mark.parametrize(
    "question,expected_domain",
    [
        ("Điện mặt trời bao nhiêu 1 kWh?", "energy"),
        ("Năng lượng tái tạo là gì?", "physics"),  # baseline pre-existing
        ("Thực phẩm nào chứa vitamin C?", "foodtech"),
        ("Giá bitcoin hiện tại", "finance"),
        ("Thủ đô của Pháp là gì?", "geography"),
        ("Thời tiết Hà Nội hôm nay?", "weather"),
        ("Nhiệt độ tại Hà Nội", "weather"),
        ("Tốc độ ánh sáng là bao nhiêu? (km/s)", "physics"),
        ("Khối lượng phân tử nước bằng bao nhiêu?", "chemistry"),
    ],
)
def test_side_effect_domains_unchanged(question, expected_domain):
    """Pin hành vi baseline: các domain không thuộc target repair phải giữ
    nguyên (đo trước fix; chống keyword-war hồi quy)."""
    decision = route_question(question)
    assert decision.domain == expected_domain, (
        f"{question!r} → {decision.domain}, want {expected_domain} (baseline)"
    )
