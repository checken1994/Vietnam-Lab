import os
os.environ.setdefault("SCP_API_PROFILE", "full")
os.environ.setdefault("SCP_CAPABILITY_SECRET", "dummy-secret-for-tests-123")
os.environ.setdefault("SCP_STORAGE_BACKEND", "sqlite")
os.environ.setdefault("SCP_TOP_SYSTEMS_EGRESS", "0")

from scp.data_sources.free_api_catalog import get_catalog, ALLOWED_HOSTS


def test_subsystem_data_sources_importable():
    """Free API catalog: verify offline status, catalog entries, and search indexing."""
    cat = get_catalog()
    status = cat.status()
    assert isinstance(status, dict)
    assert status.get("cached") is True
    assert status.get("count", 0) > 0
    
    # Functional search
    results = cat.search("weather")
    assert isinstance(results, list)
    assert len(results) > 0
    assert "name" in results[0]
    assert "url" in results[0]
    assert "category" in results[0]
