"""
[Milestone M3 Contract Test] Architectural Deprecation of 61 Empty Data Sources & 26 Dead News Feeds.
====================================================================================================
Requirement R4:
1. ConversionDataSource in conversion.py is preserved intact (functional arithmetic conversion engine).
2. The 61 empty Data Source classes in scp/data_sources/ are deprecated:
   - emit DeprecationWarning on initialization.
   - have deprecated = True attribute.
   - have their static fact dictionaries emptied to {} (Tabula Rasa).
   - safely fail closed on fetch() / query().
3. All 26 dead news feeds in data/free_api_catalog.json are marked deprecated:
   - FreeAPICatalog.search() excludes deprecated entries by default.
   - FreeAPICatalog.search(include_deprecated=True) surfaces deprecated entries.
4. Expert consumers in scp/runtime/experts/ gracefully query LiveKnowledgeFetcher / InternetSearch
   without NameError or AttributeError.
5. Negative anti-placebo assertions: fail closed on unresolvable queries.
"""
from __future__ import annotations

import json
import warnings
from pathlib import Path
import pytest

from scp.data_sources.conversion import ConversionDataSource
from scp.data_sources.weather import WeatherDataSource
from scp.data_sources.free_api_catalog import FreeAPICatalog


def test_conversion_data_source_retained_and_functional():
    """Requirement R4.1: ConversionDataSource is preserved intact and NOT deprecated."""
    with warnings.catch_warnings(record=True) as recorded_warnings:
        warnings.simplefilter("always")
        ds = ConversionDataSource()
        dep_warnings = [w for w in recorded_warnings if issubclass(w.category, DeprecationWarning)]
        assert len(dep_warnings) == 0, "ConversionDataSource must not emit DeprecationWarning"

    assert not getattr(ds, "deprecated", False), "ConversionDataSource must not be marked deprecated"

    # Functional unit conversion check
    result = ds.convert(1.0, "km", "m")
    assert result is not None and result.get("value") == 1000.0, f"Expected 1000.0, got {result}"

    ans = ds.answer_from_question("1 km bằng bao nhiêu mét?")
    assert ans is not None
    assert "1000" in ans["text"]


def test_weather_data_source_retained_and_functional():
    """Requirement R4.1: WeatherDataSource (Open-Meteo live integration) is preserved intact and NOT deprecated."""
    with warnings.catch_warnings(record=True) as recorded_warnings:
        warnings.simplefilter("always")
        ds = WeatherDataSource()
        dep_warnings = [w for w in recorded_warnings if issubclass(w.category, DeprecationWarning)]
        assert len(dep_warnings) == 0, "WeatherDataSource must not emit DeprecationWarning"

    assert not getattr(ds, "deprecated", False), "WeatherDataSource must not be marked deprecated"
    assert hasattr(ds, "fetch"), "WeatherDataSource must have fetch method"
    assert hasattr(ds, "answer_from_question"), "WeatherDataSource must have answer_from_question method"


DEPRECATED_CLASSES = [
    ("aerospace", "AerospaceDataSource"),
    ("agriculture", "AgricultureDataSource"),
    ("alphavantage", "AlphaVantageDataSource"),
    ("architecture", "ArchitectureDataSource"),
    ("arts", "ArtsDataSource"),
    ("astronomy", "AstronomyDataSource"),
    ("audiovideo", "AudioVideoDataSource"),
    ("biology", "BiologyDataSource"),
    ("blockchain", "BlockchainDataSource"),
    ("cartography", "CartographyDataSource"),
    ("chemistry", "ChemistryDataSource"),
    ("cornell_lii", "CornellLIIDataSource"),
    ("courtlistener", "CourtListenerDataSource"),
    ("crafts", "CraftsDataSource"),
    ("cybersecurity", "CybersecurityDataSource"),
    ("digitalmarketing", "DigitalMarketingDataSource"),
    ("diplomacy", "DiplomacyDataSource"),
    ("dtic", "DTICDataSource"),
    ("ecommerce", "EcommerceDataSource"),
    ("education", "EducationDataSource"),
    ("energy", "EnergyDataSource"),
    ("environment", "EnvironmentDataSource"),
    ("eric", "ERICDataSource"),
    ("finance", "FinanceDataSource"),
    ("foodtech", "FoodTechDataSource"),
    ("fred", "FREDDataSource"),
    ("genai", "GenAIDataSource"),
    ("geography", "GeographyDataSource"),
    ("geology", "GeologyDataSource"),
    ("glottolog", "GlottologDataSource"),
    ("google_factcheck", "GoogleFactCheckDataSource"),
    ("gutenberg", "GutenbergDataSource"),
    ("heritage", "HeritageDataSource"),
    ("history", "HistoryDataSource"),
    ("legal", "LegalDataSource"),
    ("logic", "LogicDataSource"),
    ("math", "MathDataSource"),
    ("medical", "MedicalDataSource"),
    ("metmuseum", "MetMuseumDataSource"),
    ("military", "MilitaryDataSource"),
    ("newsapi", "NewsAPIDataSource"),
    ("noaa", "NOAADataSource"),
    ("oceanography", "OceanographyDataSource"),
    ("physics", "PhysicsDataSource"),
    ("psychology", "PsychologyDataSource"),
    ("reality", "RealityDataSource"),
    ("religion", "ReligionDataSource"),
    ("social", "SocialDataSource"),
    ("spacemedicine", "SpaceMedicineDataSource"),
    ("sports", "SportsDataSource"),
    ("statistics", "StatisticsDataSource"),
    ("technology", "TechnologyDataSource"),
    ("tourism", "TourismDataSource"),
    ("transport", "TransportDataSource"),
    ("undata", "UNDataDataSource"),
    ("unesco", "UNESCODataSource"),
    ("usgs", "USGSDataSource"),
    ("uxui", "UXUIDataSource"),
    ("wikiart", "WikiArtDataSource"),
    ("worldbank", "WorldBankDataSource"),
]


def test_census_count_of_deprecated_classes():
    """Verify exact count of 60 deprecated classes."""
    assert len(DEPRECATED_CLASSES) == 60


@pytest.mark.parametrize("module_name,class_name", DEPRECATED_CLASSES)
def test_data_sources_deprecated_attributes_and_warnings(module_name: str, class_name: str):
    """Requirement R4.1: Each of the 60 data source classes is marked deprecated and emits warning."""
    import importlib
    mod = importlib.import_module(f"scp.data_sources.{module_name}")
    cls = getattr(mod, class_name)

    assert getattr(cls, "deprecated", False) is True, f"{class_name} must have deprecated=True"

    with warnings.catch_warnings(record=True) as recorded_warnings:
        warnings.simplefilter("always")
        instance = cls()
        dep_warnings = [
            w for w in recorded_warnings
            if issubclass(w.category, DeprecationWarning) and class_name in str(w.message)
        ]
        assert len(dep_warnings) >= 1, f"{class_name} must emit DeprecationWarning on init"


def test_tabula_rasa_static_fact_dicts_emptied():
    """Requirement R4.1 & Tabula Rasa: Static fact dictionaries are emptied to {}."""
    from scp.data_sources.architecture import ArchitectureDataSource
    from scp.data_sources.blockchain import BlockchainDataSource
    from scp.data_sources.cartography import CartographyDataSource
    from scp.data_sources.arts import ArtsDataSource
    from scp.data_sources.sports import SportsDataSource

    assert ArchitectureDataSource()._styles == {}
    assert BlockchainDataSource()._concepts == {}
    assert CartographyDataSource()._projections == {}
    assert ArtsDataSource()._painters == {}
    assert SportsDataSource()._olympics == {}


def test_free_api_catalog_news_feeds_deprecated(tmp_path):
    """Requirement R4.2: All 26 news feeds in catalog are marked deprecated and filtered by default."""
    catalog_path = Path("data/free_api_catalog.json")
    assert catalog_path.exists(), "data/free_api_catalog.json must exist on disk"

    with open(catalog_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    news_entries = [e for e in data.get("entries", []) if e.get("category") == "News"]
    assert len(news_entries) == 26, f"Expected 26 news entries, got {len(news_entries)}"

    for entry in news_entries:
        assert entry.get("deprecated") is True, f"Entry {entry.get('name')} must have deprecated=True"
        assert entry.get("status") == "deprecated"
        assert "InternetSearch" in entry.get("replacement", "")

    # Test FreeAPICatalog search filtering
    catalog = FreeAPICatalog(data_dir="data")
    # Default search excludes deprecated news feeds
    results_default = catalog.search(category="News", include_deprecated=False)
    assert len(results_default) == 0, "Default search must exclude deprecated news feeds"

    # Search with include_deprecated=True returns them
    results_included = catalog.search(category="News", include_deprecated=True, limit=50)
    assert len(results_included) == 26, "include_deprecated=True must surface all 26 entries"


def test_expert_graceful_live_fallback_and_no_crash(monkeypatch):
    """Requirement R4.3: Experts handle deprecated data sources and query LiveKnowledgeFetcher gracefully."""
    from scp.runtime.experts.aerospace import Aerospace
    from scp.runtime.experts.news import News

    # Mock LiveKnowledgeFetcher to return predictable live answer
    monkeypatch.setattr(
        "scp.data_sources.live_knowledge.fetch_live",
        lambda q, domain="": {"value": "Live Apollo 11 moon landing fact", "source": "Wikipedia"}
    )

    aero = Aerospace()
    resp = aero.predict("Apollo 11 moon landing")
    assert resp is not None
    assert "Live Apollo 11" in resp.answer
    assert resp.confidence >= 0.7

    news_expert = News()
    resp_news = news_expert.predict("Tin tức công nghệ hôm nay")
    assert resp_news is not None
    assert "Live Apollo 11" in resp_news.answer


def test_anti_placebo_negative_fail_closed_on_unresolvable_query(monkeypatch):
    """Anti-Placebo Gate Pattern: Queries with no match must fail closed with low confidence."""
    from scp.runtime.experts.aerospace import Aerospace

    # Mock unresolvable query (all sources return None)
    monkeypatch.setattr("scp.data_sources.live_knowledge.fetch_live", lambda q, domain="": None)

    aero = Aerospace()
    resp = aero.predict("câu hỏi không có trong bất kỳ tài liệu nào xyz123456")
    assert resp is not None
    assert resp.confidence <= 0.1
    assert resp.answer == ""
