"""[Tabula Rasa] Geography local cache - explicitly empty / unpopulated by default.

Part of WP5/WP7 Dead Shell Cleanup. This module contains NO static canned facts.
All geographical knowledge must be fetched dynamically via LiveKnowledgeFetcher
or loaded through the canonical KnowledgeStore. Avoiding false advertising of pre-canned facts.
"""
from __future__ import annotations

from typing import Any

GEO_LOCAL_CACHE: dict[str, dict[str, Any]] = {}
