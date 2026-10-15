"""
[Tabula Rasa] Dynamic local geography cache extracted from runtime/experts/humanities.py.
Initialized empty: populated dynamically via LiveKnowledgeFetcher and SQLite warehouse.
"""
from __future__ import annotations

from typing import Any

GEO_LOCAL_CACHE: dict[str, dict[str, Any]] = {}
