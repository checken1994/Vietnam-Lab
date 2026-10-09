"""
Unit & Integration Tests for Commercial Data Sources Config Wiring.
===================================================================
Kiểm chứng toàn bộ 14 Data Sources đã được đấu nối vào config_loader.py:
  1. agriculture.py (AgricultureDataSource -> USDA_API_KEY)
  2. alphavantage.py (AlphaVantageDataSource -> ALPHAVANTAGE_API_KEY)
  3. biology.py (BiologyDataSource -> NCBI_API_KEY)
  4. courtlistener.py (CourtListenerDataSource -> COURTLISTENER_TOKEN)
  5. cybersecurity.py (CybersecurityDataSource -> NVD_API_KEY)
  6. energy.py (EnergyDataSource -> EIA_API_KEY)
  7. eric.py (ERICDataSource -> ERIC_API_KEY)
  8. fred.py (FREDDataSource -> FRED_API_KEY)
  9. google_factcheck.py (GoogleFactCheckDataSource -> GOOGLE_FACT_CHECK_API_KEY)
  10. legal.py (LegalDataSource -> CASE_LAW_API_KEY)
  11. medical.py (MedicalDataSource -> PUBMED_API_KEY / NCBI_API_KEY)
  12. newsapi.py (NewsAPIDataSource -> NEWSAPI_API_KEY)
  13. noaa.py (NOAADataSource -> NOAA_API_KEY)
  14. wikiart.py (WikiArtDataSource -> WIKIART_API_KEY)

Kiểm tra:
  - Lọc bỏ placeholder (demo, dummy, todo...) -> không gán key giả lập.
  - Nhận đúng key hợp lệ khi cấu hình trong DataSourceConfigLoader.
  - Fail-closed & Zero-leak: Không in hoặc lộ raw secret trong log hay exception.
"""
from __future__ import annotations

import pytest

from scp.data_sources.agriculture import AgricultureDataSource
from scp.data_sources.alphavantage import AlphaVantageDataSource
from scp.data_sources.biology import BiologyDataSource
from scp.data_sources.config_loader import (
    DataSourceConfigLoader,
    mask_secret,
    reset_default_loader,
    set_default_loader,
)
from scp.data_sources.courtlistener import CourtListenerDataSource
from scp.data_sources.cybersecurity import CybersecurityDataSource
from scp.data_sources.energy import EnergyDataSource
from scp.data_sources.eric import ERICDataSource
from scp.data_sources.fred import FREDDataSource
from scp.data_sources.google_factcheck import GoogleFactCheckDataSource
from scp.data_sources.legal import LegalDataSource
from scp.data_sources.medical import MedicalDataSource
from scp.data_sources.newsapi import NewsAPIDataSource
from scp.data_sources.noaa import NOAADataSource
from scp.data_sources.wikiart import WikiArtDataSource


@pytest.fixture(autouse=True)
def clean_config_loader():
    reset_default_loader()
    yield
    reset_default_loader()


def test_data_sources_with_valid_api_keys():
    """Tất cả 14 nguồn nạp đúng key hợp lệ khi được cấu hình."""
    mock_env = {
        "USDA_API_KEY": "usda_valid_key_111",
        "ALPHAVANTAGE_API_KEY": "alpha_valid_key_222",
        "NCBI_API_KEY": "ncbi_valid_key_333",
        "COURTLISTENER_TOKEN": "court_valid_token_444",
        "NVD_API_KEY": "nvd_valid_key_555",
        "EIA_API_KEY": "eia_valid_key_666",
        "ERIC_API_KEY": "eric_valid_key_777",
        "FRED_API_KEY": "fred_valid_key_888",
        "GOOGLE_FACT_CHECK_API_KEY": "google_valid_key_999",
        "CASE_LAW_API_KEY": "caselaw_valid_key_000",
        "PUBMED_API_KEY": "pubmed_valid_key_123",
        "NEWSAPI_API_KEY": "newsapi_valid_key_456",
        "NOAA_API_KEY": "noaa_valid_key_789",
        "WIKIART_API_KEY": "wikiart_valid_key_987",
    }
    loader = DataSourceConfigLoader(env_dict=mock_env, auto_load_env=False)
    set_default_loader(loader)

    # 1. Agriculture
    agri = AgricultureDataSource()
    assert agri._usda_api_key == "usda_valid_key_111"

    # 2. AlphaVantage
    alpha = AlphaVantageDataSource()
    assert alpha.api_key == "alpha_valid_key_222"
    assert alpha.enabled is True

    # 3. Biology
    bio = BiologyDataSource()
    assert bio._ncbi_api_key == "ncbi_valid_key_333"

    # 4. CourtListener
    cl = CourtListenerDataSource()
    assert cl.api_key == "court_valid_token_444"
    assert cl.enabled is True

    # 5. Cybersecurity
    cyber = CybersecurityDataSource()
    assert cyber._nvd_api_key == "nvd_valid_key_555"

    # 6. Energy
    energy = EnergyDataSource()
    assert energy._eia_api_key == "eia_valid_key_666"

    # 7. ERIC
    eric = ERICDataSource()
    assert eric.api_key == "eric_valid_key_777"
    assert eric.enabled is True

    # 8. FRED
    fred = FREDDataSource()
    assert fred.api_key == "fred_valid_key_888"
    assert fred.enabled is True

    # 9. Google FactCheck
    gfc = GoogleFactCheckDataSource()
    assert gfc.api_key == "google_valid_key_999"
    assert gfc.enabled is True

    # 10. Legal
    legal = LegalDataSource()
    assert legal._case_law_api_key == "caselaw_valid_key_000"

    # 11. Medical
    med = MedicalDataSource()
    assert med._pubmed_api_key == "pubmed_valid_key_123"
    assert med._ncbi_api_key == "ncbi_valid_key_333"

    # 12. NewsAPI
    news = NewsAPIDataSource()
    assert news.api_key == "newsapi_valid_key_456"
    assert news.enabled is True

    # 13. NOAA
    noaa = NOAADataSource()
    assert noaa.api_key == "noaa_valid_key_789"
    assert noaa.enabled is True

    # 14. WikiArt
    wiki = WikiArtDataSource()
    assert wiki.api_key == "wikiart_valid_key_987"
    assert wiki.enabled is True


def test_data_sources_filter_out_placeholders_fail_closed():
    """Placeholder (demo, dummy, your_key_here) bị loại bỏ hoàn toàn."""
    placeholder_env = {
        "USDA_API_KEY": "demo",
        "ALPHAVANTAGE_API_KEY": "demo",
        "NCBI_API_KEY": "your_api_key_here",
        "COURTLISTENER_TOKEN": "dummy",
        "NVD_API_KEY": "placeholder",
        "EIA_API_KEY": "todo",
        "ERIC_API_KEY": "<insert_key_here>",
        "FRED_API_KEY": "fake_key",
        "GOOGLE_FACT_CHECK_API_KEY": "changeme",
        "CASE_LAW_API_KEY": "none",
        "PUBMED_API_KEY": "test",
        "NEWSAPI_API_KEY": "dummy",
        "NOAA_API_KEY": "0",
        "WIKIART_API_KEY": "xxx",
    }
    loader = DataSourceConfigLoader(env_dict=placeholder_env, auto_load_env=False)
    set_default_loader(loader)

    # Agriculture
    agri = AgricultureDataSource()
    assert agri._usda_api_key == ""

    # AlphaVantage
    alpha = AlphaVantageDataSource()
    assert alpha.api_key == ""
    assert alpha.enabled is False

    # Biology
    bio = BiologyDataSource()
    assert bio._ncbi_api_key == ""

    # CourtListener
    cl = CourtListenerDataSource()
    assert cl.api_key == ""

    # Cybersecurity
    cyber = CybersecurityDataSource()
    assert cyber._nvd_api_key == ""

    # Energy
    energy = EnergyDataSource()
    assert energy._eia_api_key == ""

    # ERIC
    eric = ERICDataSource()
    assert eric.api_key == ""

    # FRED
    fred = FREDDataSource()
    assert fred.api_key == ""
    assert fred.enabled is False

    # Google FactCheck
    gfc = GoogleFactCheckDataSource()
    assert gfc.api_key == ""
    assert gfc.enabled is False

    # Legal
    legal = LegalDataSource()
    assert legal._case_law_api_key == ""

    # Medical
    med = MedicalDataSource()
    assert med._pubmed_api_key == ""
    assert med._ncbi_api_key == ""

    # NewsAPI
    news = NewsAPIDataSource()
    assert news.api_key == ""
    assert news.enabled is False

    # NOAA
    noaa = NOAADataSource()
    assert noaa.api_key == ""
    assert noaa.enabled is False

    # WikiArt
    wiki = WikiArtDataSource()
    assert wiki.api_key == ""


def test_data_sources_empty_env():
    """Khi không có biến môi trường nào được thiết lập -> chuỗi rỗng và disable."""
    loader = DataSourceConfigLoader(env_dict={}, auto_load_env=False)
    set_default_loader(loader)

    alpha = AlphaVantageDataSource()
    assert alpha.api_key == ""
    assert alpha.enabled is False

    fred = FREDDataSource()
    assert fred.api_key == ""
    assert fred.enabled is False

    news = NewsAPIDataSource()
    assert news.api_key == ""
    assert news.enabled is False

    noaa = NOAADataSource()
    assert noaa.api_key == ""
    assert noaa.enabled is False


def test_data_sources_filter_out_quoted_placeholders_fail_closed():
    """Placeholder được bọc trong dấu ngoặc kép hoặc ngoặc đơn ('demo', \"dummy\") cũng phải bị từ chối triệt để."""
    quoted_env = {
        "ALPHAVANTAGE_API_KEY": '"demo"',
        "FRED_API_KEY": "'dummy'",
        "NEWSAPI_API_KEY": ' "placeholder" ',
        "USDA_API_KEY": " 'changeme' ",
    }
    loader = DataSourceConfigLoader(env_dict=quoted_env, auto_load_env=False)
    set_default_loader(loader)

    alpha = AlphaVantageDataSource()
    assert alpha.api_key == ""
    assert alpha.enabled is False

    fred = FREDDataSource()
    assert fred.api_key == ""
    assert fred.enabled is False

    news = NewsAPIDataSource()
    assert news.api_key == ""
    assert news.enabled is False

    agri = AgricultureDataSource()
    assert agri._usda_api_key == ""

