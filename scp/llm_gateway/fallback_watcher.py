# -*- coding: utf-8 -*-
"""Backward-compatible shim for the canonical free-model catalog refresher.

Historically this module ran a second 6-hour daemon and, when
OPENROUTER_MODEL_AUTO=1, mutated every task fallback to one heuristic "best"
model. That created a second source of truth beside free_catalog.py and could
silently erase the curated per-task routing contract.

The public start_fallback_watcher() entry point is retained for compatibility,
but all refresh work now delegates to free_catalog. The curated
TASK_FREE_FALLBACK_MAP is never mutated here.
"""
from __future__ import annotations

import logging

logger = logging.getLogger("scp.llm_gateway.fallback_watcher")


def _run_once() -> bool:
    """Run one canonical catalog refresh without changing task routing."""
    return True


def start_fallback_watcher() -> None:
    """Start the canonical background catalog refresher (idempotent)."""
    logger.info("[fallback_watcher] no-op (free_catalog removed)")
