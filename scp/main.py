"""Canonical SCP Main Entrypoint — Exposing the canonical FastAPI app.

Allows uvicorn to launch via both `uvicorn scp.main:app` and `uvicorn scp.api_server:app`.
"""
from __future__ import annotations

from scp.api_server import app

__all__ = ["app"]
