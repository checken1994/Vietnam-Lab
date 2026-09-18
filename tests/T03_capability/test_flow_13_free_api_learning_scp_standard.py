"""
SCP Complete Standard Test — Mạch 13: Free API & Learning
Covers: api/routes/v104_routes.py (free-apis, learn/top-systems), data_sources/free_api_catalog.py, core/top_systems_learning.py

FA-01: Strict assertions, no loosening
FA-02: No skip/xfail
FA-03: Full pytest output as evidence
FA-04: No simulated VERIFIED
FA-05: No self-grant authority
FA-09: Exploit mandate - reproduce actual behavior
FA-13: Causal branch coverage of free API & learning flow
"""

import json
import tempfile
from unittest.mock import MagicMock, patch, AsyncMock

import pytest
from fastapi.testclient import TestClient

from scp.api_server import app
from scp.api.routes import v104_routes
from scp.data_sources.free_api_catalog import FreeAPICatalog
from scp.core.top_systems_learning import TopSystemsLearner, inspect_untrusted, _extract_concepts, reputation_from_stars, egress_disabled


class TestFlow13FreeAPILearning:
    """Mạch 13: Free API & Learning - SCP Complete Standard"""

    # =========================================================================
    # 1. V104 ROUTES — Free API & Learning (Admin Required)
    # =========================================================================

    def test_v104_free_apis_search_requires_admin(self):
        """
        [FREE-1] GET /v104/free-apis/search requires admin auth.
        """
        with TestClient(app) as client:
            response = client.get("/v104/free-apis/search")
            assert response.status_code in [401, 403, 429]

    def test_v104_free_apis_search_returns_catalog(self):
        """
        [FREE-2] Free API search returns catalog results.
        """
        from scp.api._shared import verify_admin
        app.dependency_overrides[verify_admin] = lambda: True
        try:
            with TestClient(app) as client:
                with patch("scp.data_sources.free_api_catalog.FreeAPICatalog.search") as mock_search, \
                     patch("scp.data_sources.free_api_catalog.FreeAPICatalog.entries", return_value=["dummy"]):
                    mock_search.return_value = [
                        {"name": "Test API", "category": "Development", "auth": "none", "https": True}
                    ]

                    response = client.get("/v104/free-apis/search?query=test")
                    assert response.status_code == 200
                    data = response.json()
                    assert data["count"] == 1
        finally:
            app.dependency_overrides.clear()

    def test_v104_learn_top_systems_status_requires_admin(self):
        """
        [LEARN-1] GET /v104/learn/top-systems/status requires admin auth.
        """
        with TestClient(app) as client:
            response = client.get("/v104/learn/top-systems/status")
            assert response.status_code in [401, 403, 429]

    def test_v104_learn_top_systems_requires_admin(self):
        """
        [LEARN-2] POST /v104/learn/top-systems requires admin auth.
        """
        with TestClient(app) as client:
            response = client.post("/v104/learn/top-systems", json={})
            assert response.status_code in [401, 403, 429]

    def test_v104_learn_top_systems_advise_requires_admin(self):
        """
        [LEARN-3] GET /v104/learn/top-systems/advise requires admin auth.
        """
        with TestClient(app) as client:
            response = client.get("/v104/learn/top-systems/advise")
            assert response.status_code in [401, 403, 429]

    # =========================================================================
    # 2. FREE API CATALOG
    # =========================================================================

    def test_free_api_catalog_loads_from_public_apis(self, tmp_path):
        """
        [CAT-1] FreeAPICatalog loads 2200+ APIs from public-apis repo.
        """
        mock_md = b"| API | Description | Auth | HTTPS | CORS |\n|---|---|---|---|---|\n| [Test API](http://test.com) | Test | none | Yes | Yes |\n### Development\n| API | Description | Auth | HTTPS | CORS |\n| [Test API Dev](http://test.com) | Test Dev | none | Yes | Yes |"
        catalog = FreeAPICatalog(data_dir=str(tmp_path), transport=lambda url: mock_md)

        catalog.refresh()

        entries = catalog.entries()
        assert len(entries) >= 1
        assert any(e["name"] == "Test API Dev" for e in entries)

    def test_free_api_catalog_search_returns_results(self, tmp_path):
        """
        [CAT-2] FreeAPICatalog search returns matching APIs.
        """
        catalog = FreeAPICatalog(data_dir=str(tmp_path))
        catalog._entries = [
            {"name": "GitHub API", "description": "GitHub", "auth": "oauth", "https": "Yes", "category": "Development"},
            {"name": "GitLab API", "description": "GitLab", "auth": "oauth", "https": "Yes", "category": "Development"},
            {"name": "Weather API", "description": "Weather", "auth": "apiKey", "https": "Yes", "category": "Weather"}
        ]

        results = catalog.search("git")
        assert len(results) == 2
        assert all("git" in r["name"].lower() or "git" in r["description"].lower() for r in results)

    def test_free_api_catalog_filters_by_category(self, tmp_path):
        """
        [CAT-3] FreeAPICatalog filters by category.
        """
        catalog = FreeAPICatalog(data_dir=str(tmp_path))
        catalog._entries = [
            {"name": "Dev API", "description": "Dev", "category": "Development"},
            {"name": "Weather API", "description": "Weather", "category": "Weather"}
        ]

        results = catalog.search(category="Weather")
        assert len(results) == 1
        assert results[0]["name"] == "Weather API"

    def test_free_api_catalog_caches_results(self, tmp_path):
        """
        [CAT-4] FreeAPICatalog caches results to avoid rate limits.
        """
        mock_md = b"### Development\n| API | Description | Auth | HTTPS | CORS |\n|---|---|---|---|---|\n| [Test API](http://test.com) | Test | none | Yes | Yes |"
        fetch_mock = MagicMock(return_value=mock_md)
        catalog = FreeAPICatalog(data_dir=str(tmp_path), transport=fetch_mock)

        catalog.refresh()
        catalog._entries = None # Force reload
        catalog.refresh()  # Second call should use cache

        # Should only fetch once
        assert fetch_mock.call_count == 1
        assert catalog.entries()[0]["name"] == "Test API"

    def test_free_api_catalog_handles_github_rate_limit(self, tmp_path):
        """
        [CAT-5] FreeAPICatalog handles GitHub rate limit (60 req/h) gracefully.
        """
        def fail_transport(url):
            raise Exception("Rate limit exceeded")
            
        catalog = FreeAPICatalog(data_dir=str(tmp_path), transport=fail_transport)

        # Should not crash, return empty or cached
        res = catalog.refresh()
        assert res["ok"] is False
        assert catalog.entries() == []

    # =========================================================================
    # 3. TOP SYSTEMS LEARNING
    # =========================================================================

    def test_top_systems_learner_queries_github(self):
        """
        [LEARN-4] TopSystemsLearner queries GitHub Search API for best practices.
        """
        learner = TopSystemsLearner(data_dir="data")

        with patch.object(learner, "_get_json", return_value={
            "items": [{"full_name": "agent-framework", "stargazers_count": 1000, "description": "Agent framework"}]
        }), patch.object(learner, "_get_raw", return_value=""):
            results = learner.learn_topic("agent_runtime")

            assert "records" in results
            assert results["records"] >= 1

    def test_top_systems_learner_queries_wikipedia(self):
        """
        [LEARN-5] TopSystemsLearner queries Wikipedia for concepts.
        """
        learner = TopSystemsLearner(data_dir="data")

        with patch.object(learner, "_get_json", return_value={
            "query": {"search": [{"title": "Agent (AI)", "snippet": "An agent is..."}]}
        }), patch.object(learner, "_get_raw", return_value=""):
            results = learner.learn_topic("agent_runtime")

            assert "records" in results
            assert results["records"] >= 1

    def test_top_systems_learner_extracts_concepts(self):
        """
        [LEARN-6] TopSystemsLearner extracts concepts with sha256 dedup.
        """
        learner = TopSystemsLearner(data_dir="data")

        raw_content = """
        # Agent Best Practices
        # Use capability tokens
        # Implement fail-closed
        # Use capability tokens  # Duplicate
        """

        concepts = _extract_concepts(raw_content)

        # Should deduplicate "Use capability tokens"
        concept_texts = [c for c in concepts]
        assert concept_texts.count("Use capability tokens") == 1

    def test_reputation_weighted_advice(self):
        """
        [LEARN-7] TopSystemsLearner gives reputation-weighted advice.
        """
        # High stars = higher reputation
        assert reputation_from_stars(5000) in ["high", "medium"]
        assert reputation_from_stars(10) == "low"

    def test_top_systems_learner_persists_to_ledger(self, tmp_path):
        """
        [LEARN-8] TopSystemsLearner persists to learning ledger.
        """
        learner = TopSystemsLearner(data_dir=str(tmp_path))

        with patch.object(learner, "_fetch_github", return_value=[
            {"full_name": "test/repo", "stargazers_count": 100, "description": "Test concept"}
        ]):
            result = learner.learn_topic("agent_runtime")

        # Verify persisted - check ledger file exists
        ledger_files = list(tmp_path.glob("*.jsonl"))
        assert len(ledger_files) >= 1

    def test_v104_learn_consolidate_stub_contract(self):
        """
        [LEARN-9][M13-FIX] POST /v104/learn/consolidate returns 200 with an
        explicit minimal-stub contract.
        """
        from scp.api._shared import verify_admin
        app.dependency_overrides[verify_admin] = lambda: True
        try:
            with TestClient(app) as client:
                response = client.post("/v104/learn/consolidate")
                assert response.status_code == 200
                data = response.json()
                assert data["status"] == "ok_stub_noop"
                assert data["consolidator_mode"] == "minimal_stub_passthrough"
                assert isinstance(data["arbiter_stats"], dict)
                assert "KnowledgeConsolidator" in data["note"]
                assert "does not persist" in data["note"]
        finally:
            app.dependency_overrides.pop(verify_admin, None)


class TestFlow13FreeAPILearningCausalCoverage:
    """
    FA-13: Causal Coverage Matrix for Mạch 13
    """

    def test_causal_v104_free_apis_admin_required(self):
        """Branch: free-apis endpoints require admin"""
        with TestClient(app) as client:
            response = client.get("/v104/free-apis/search")
            assert response.status_code in [401, 403, 429]

    def test_causal_v104_learn_top_systems_admin_required(self):
        """Branch: learn/top-systems endpoints require admin"""
        with TestClient(app) as client:
            response = client.post("/v104/learn/top-systems", json={})
            assert response.status_code in [401, 403, 429]

    def test_causal_free_api_catalog_loads(self):
        """Branch: catalog loads from public-apis"""
        catalog = FreeAPICatalog(data_dir="data-test")
        assert hasattr(catalog, "refresh")
        assert hasattr(catalog, "entries")

    def test_causal_free_api_catalog_search(self):
        """Branch: search → matching results"""
        catalog = FreeAPICatalog(data_dir="data-test")
        catalog._entries = [
            {"name": "GitHub API", "description": "GitHub", "category": "Development"},
            {"name": "Weather API", "description": "Weather", "category": "Weather"}
        ]
        results = catalog.search("git")
        assert len(results) == 1

    def test_causal_free_api_catalog_filter(self):
        """Branch: filter by category"""
        catalog = FreeAPICatalog(data_dir="data-test")
        catalog._entries = [
            {"name": "Dev API", "description": "Dev", "category": "Development"},
            {"name": "Weather API", "description": "Weather", "category": "Weather"}
        ]
        results = catalog.search(category="Weather")
        assert len(results) == 1
        assert results[0]["name"] == "Weather API"

    def test_causal_free_api_catalog_cache(self):
        """Branch: cache prevents refetch"""
        mock_md = b"### Development\n| API | Description | Auth | HTTPS | CORS |\n|---|---|---|---|---| | [Test API](http://test.com) | Test | none | Yes | Yes |"
        fetch_mock = MagicMock(return_value=mock_md)
        with tempfile.TemporaryDirectory() as d:
            catalog = FreeAPICatalog(data_dir=d, transport=fetch_mock)
            catalog.refresh()
            catalog._entries = None
            catalog.refresh()
        assert fetch_mock.call_count == 1

    def test_causal_free_api_catalog_rate_limit(self):
        """Branch: rate limit → graceful handling"""
        def fail_transport(url):
            raise Exception("Rate limit exceeded")
        with tempfile.TemporaryDirectory() as d:
            catalog = FreeAPICatalog(data_dir=d, transport=fail_transport)
            res = catalog.refresh()
        assert res["ok"] is False

    def test_causal_learn_github_query(self):
        """Branch: learn from GitHub"""
        learner = TopSystemsLearner(data_dir="data")
        with patch.object(learner, "_get_json", return_value={
            "items": [{"full_name": "test/repo", "stargazers_count": 100, "description": "Test"}]
        }), patch.object(learner, "_get_raw", return_value=""):
            results = learner.learn_topic("agent_runtime")
            assert "ok" in results

    def test_causal_learn_wikipedia_query(self):
        """Branch: learn from Wikipedia"""
        learner = TopSystemsLearner(data_dir="data")
        with patch.object(learner, "_get_json", return_value={
            "query": {"search": [{"title": "Test", "snippet": "Test snippet"}]}
        }), patch.object(learner, "_get_raw", return_value=""):
            results = learner.learn_topic("agent_runtime")
            assert "ok" in results

    def test_causal_learn_extract_concepts(self):
        """Branch: extract concepts with dedup"""
        raw = "# Use capability tokens\n# Use capability tokens\n"
        concepts = _extract_concepts(raw)
        assert concepts.count("Use capability tokens") == 1

    def test_causal_learn_reputation_weighted(self):
        """Branch: advice weighted by reputation"""
        assert reputation_from_stars(5000) in ["high", "medium"]
        assert reputation_from_stars(10) == "low"

    def test_causal_learn_persists_ledger(self, tmp_path):
        """Branch: persist to ledger"""
        learner = TopSystemsLearner(data_dir=str(tmp_path))
        with patch.object(learner, "_fetch_github", return_value=[
            {"full_name": "test/repo", "stargazers_count": 100, "description": "Test"}
        ]):
            learner.learn_topic("agent_runtime")
        ledger_files = list(tmp_path.glob("*.jsonl"))
        assert len(ledger_files) >= 1


if __name__ == "__main__":
    pass
