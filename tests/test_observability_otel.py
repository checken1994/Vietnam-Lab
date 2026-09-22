import os
from fastapi import FastAPI
from scp.observability.otel import configure_fastapi_otel

def test_otel_disabled_by_default():
    os.environ["SCP_OTEL_ENABLED"] = "0"
    app = FastAPI()
    result = configure_fastapi_otel(app)
    assert result["enabled"] is False
    assert result["reason"] == "SCP_OTEL_ENABLED=0"

def test_otel_enabled_without_endpoint_fails_safe():
    os.environ["SCP_OTEL_ENABLED"] = "1"
    os.environ.pop("OTEL_EXPORTER_OTLP_ENDPOINT", None)
    app = FastAPI()
    result = configure_fastapi_otel(app)
    assert result["enabled"] is False
    assert "missing" in result["reason"].lower()
