"""Circuit breaker and dead-model/rate-limit tracking for outbound LLM calls.

Extracted from scp.llm_gateway.client for modularization and maintainability.
"""
from __future__ import annotations

import logging
import os
import sys
import threading
import time
from typing import Any

logger = logging.getLogger("scp.llm_gateway.circuit_breaker")

_HEDGE_OFF_VALUES = {"off", "0", "false", "no"}


def _monotonic() -> float:
    client_mod = sys.modules.get("scp.llm_gateway.client")
    clock = getattr(client_mod, "time", time) if client_mod is not None else time
    return clock.monotonic()


class CircuitBreaker:
    """[C5 — Gemini indictment: round-robin ngây thơ] Cầu dao tự ngắt.

    Một endpoint/model fail liên tục N lần → OPEN: mọi request tới nó bị
    chặn NGAY tại client (fast-fail, không chờ time-out), tránh nghẽn chết
    hàng đợi Task Kernel. Sau cooldown → HALF-OPEN: cho đúng 1 request thăm
    dò; thành công → CLOSE (reset), thất bại → OPEN lại. Đây cùng nguyên lý
    với scp/core/circuit_breaker.py nhưng cho đường LLM outbound."""

    def __init__(self, failure_threshold: int = 3, cooldown_seconds: float = 300.0):
        if failure_threshold < 1 or cooldown_seconds <= 0:
            raise ValueError("failure_threshold >= 1 and cooldown_seconds > 0 required")
        self.failure_threshold = int(failure_threshold)
        self.cooldown_seconds = float(cooldown_seconds)
        self._consecutive_failures = 0
        self._opened_at: float | None = None
        # [AUDIT-FIX low-7a] Docstring hứa "cho đúng 1 request thăm dò" nhưng
        # transition HALF-OPEN từng nằm trong is_open() → N request classify
        # đồng thời sau cooldown đều thấy breaker "đóng" và đều được probe.
        # Việc cấp probe duy nhất tách ra acquire_half_open_probe() (gọi ở
        # ĐIỂM THỰC SỰ thực hiện HTTP attempt — _call_model), is_open() giờ
        # là pure classifier.
        self._half_open_probe_in_flight = False
        self._lock = threading.Lock()

    def is_open(self) -> bool:
        """Pure classifier: True = OPEN (fast-fail), False = CLOSED hoặc
        cooldown đã hết (HALF-OPEN pending). KHÔNG mutate state."""
        with self._lock:
            if self._opened_at is None:
                return False
            return _monotonic() - self._opened_at < self.cooldown_seconds

    def acquire_half_open_probe(self) -> bool:
        """[AUDIT-FIX low-7a] Cấp quyền thực hiện một attempt trên breaker.

        - CLOSED → True cho mọi caller (không tiêu probe).
        - OPEN (còn trong cooldown) → False (fast-fail như cũ).
        - Cooldown vừa hết (HALF-OPEN) → True cho ĐÚNG 1 caller (probe),
          False cho mọi caller khác cho đến khi probe được record/released.
        """
        with self._lock:
            if self._half_open_probe_in_flight:
                return False
            if self._opened_at is None:
                return True
            if _monotonic() - self._opened_at < self.cooldown_seconds:
                return False
            # Cooldown hết → chuyển HALF-OPEN: giảm 1 ngưỡng để probe thất bại
            # mở lại ngay, thành công thì record_success reset về 0.
            self._consecutive_failures = self.failure_threshold - 1
            self._opened_at = None
            self._half_open_probe_in_flight = True
            return True

    def release_half_open_probe(self) -> None:
        """Nhả probe reservation (idempotent — record_* đã tự nhả)."""
        with self._lock:
            self._half_open_probe_in_flight = False

    def record_success(self) -> None:
        with self._lock:
            self._consecutive_failures = 0
            self._opened_at = None
            self._half_open_probe_in_flight = False

    def record_failure(self) -> None:
        with self._lock:
            self._consecutive_failures += 1
            if self._consecutive_failures >= self.failure_threshold:
                self._opened_at = _monotonic()
            self._half_open_probe_in_flight = False


# ============================================================
# [F-05 DEAD-MODEL BREAKER 2026-09-25] Per-process dead-model cache.
# Runtime audit RUNTIME-AUDIT-20260925-0411 F-05: every LLM-lane ask burned
# 3x HTTP 404 on stale OpenRouter model names in the fallback chain before a
# live model answered (known debt GA.md B11 — the owner's .env lists dead
# model names; .env belongs to the owner, so the PRODUCT caches the
# provider's own verdict instead of re-asking every request).
# Contract:
#   * only a 404 whose body ACTUALLY says model-not-found (strict body
#     classification) marks (provider, base_url, model) dead — any other 404
#     is a normal failure and is NEVER cached;
#   * cached-dead candidates are skipped on later attempts; ONLY that
#     candidate is skipped — the chain/failover semantics are unchanged and
#     nothing that was previously allowed becomes disallowed;
#   * the cache starts EMPTY on every process start, so new owner config is
#     honored after a restart (no persisted state);
#   * kill-switch SCP_LLM_DEAD_MODEL_BREAKER=off disables the cache
#     (same env convention as SCP_LLM_HEDGE; default ON).
#
# [F-05-EXT QUOTA-DEAD 2026-09-26] Live sweep extension: dead providers burned
# latency on EVERY ask cycle with hard-quota errors (Cerebras/SambaNova 402
# Payment Required, Gemini 429) because only the 404 model-not-found verdict
# was cached. Contract for quota classes:
#   * 402 Payment Required = hard quota: payment state does not change
#     mid-process → the candidate is cached dead IMMEDIATELY (strict status
#     classification — the sentinel is raised from the real HTTP status, never
#     from body text or substrings);
#   * 429 = rate limit that may be transient → the candidate is cached dead
#     only after N CONSECUTIVE 429s within the process for the same
#     (provider, base_url, model) — threshold from
#     SCP_LLM_RATE_LIMIT_BREAKER_THRESHOLD (default 3, matches the
#     scp-gateway-resilience breaker rule); any non-429 outcome for that
#     candidate resets the streak;
#   * strict classification: 404-shaped, 5xx, egress_denied and generic
#     errors NEVER enter the cache through this path (the 404 model-not-found
#     cache above is the ONLY 404 path, unchanged);
#   * kill-switch semantics identical to the 404 cache
#     (SCP_LLM_DEAD_MODEL_BREAKER=off disables both).
# ============================================================
_DEAD_MODEL_REGISTRY: set[tuple[str, str, str]] = set()
_DEAD_MODEL_REGISTRY_LOCK = threading.Lock()
# Why a candidate is dead — keeps the skip message honest without embedding
# status digits that downstream quota-signal substring matching would
# misread ("payment-dead"/"rate-dead" deliberately avoid 402/429/quota/
# rate-limit substrings).
_DEAD_MODEL_REASONS: dict[tuple[str, str, str], str] = {}
_REASON_MODEL_NOT_FOUND = "404 model-not-found"
_REASON_PAYMENT_DEAD = "payment-dead"
_REASON_RATE_DEAD = "rate-dead"

# [BREAKER-ONCE 2026-09-30] Marker máy đọc được mà inner layer gắn lên MỌI
# error whose breaker failure nó ĐÃ record (404 model-not-found, 402 hard
# quota, 429 threshold-trip, final transient). Hợp đồng exactly-once: một
# real HTTP failure phải được record_failure() ĐÚNG MỘT LẦN — chat()'s
# key-rotation tail PHẢI skip các error đã đánh dấu; các quota error CHƯA
# đánh dấu (429 dưới streak threshold, legacy provider 429/402 error strings)
# vẫn được tail count đúng một lần mỗi ask như cũ.
_RECORDED_FAILURE_PREFIX = "recorded_failure:"

# [F-05-EXT] Consecutive-429 streaks per candidate (per process).
_RATE_LIMIT_STREAKS: dict[tuple[str, str, str], int] = {}
_RATE_LIMIT_STREAKS_LOCK = threading.Lock()

_RATE_LIMIT_THRESHOLD_DEFAULT = 3

_MODEL_NOT_FOUND_MARKERS = (
    "no endpoints",
    "model_not_found",
    "model not found",
    "not a valid model",
    "unknown model",
    "no allowed providers",
    "no provider available",
)


def _dead_model_breaker_enabled() -> bool:
    """Kill-switch read; values off/0/false/no disable the dead-model cache."""
    return os.environ.get("SCP_LLM_DEAD_MODEL_BREAKER", "on").strip().lower() not in _HEDGE_OFF_VALUES


def _is_model_not_found_response(resp: Any) -> bool:
    """Strict classifier: HTTP 404 AND the response body says the model does
    not exist. Any other status — or a 404 whose body matches none of the
    markers (wrong path, permissions, HTML error pages) — is NOT classified
    as a dead model and stays a normal failure."""
    if getattr(resp, "status_code", None) != 404:
        return False
    try:
        body = resp.text
    except Exception:
        logger.debug("_is_model_not_found_response: body read failed", exc_info=True)
        return False
    if not body:
        return False
    lowered = str(body).lower()
    return any(marker in lowered for marker in _MODEL_NOT_FOUND_MARKERS)


def _mark_dead_model(provider_name: str, base_url: str, model: str, reason: str = _REASON_MODEL_NOT_FOUND) -> bool:
    """Cache (provider, base_url, model) as dead for the process lifetime.
    No-op (returns False) when the kill-switch is off or model is empty."""
    if not model or not _dead_model_breaker_enabled():
        return False
    key = (str(provider_name), str(base_url), str(model))
    with _DEAD_MODEL_REGISTRY_LOCK:
        _DEAD_MODEL_REGISTRY.add(key)
        _DEAD_MODEL_REASONS[key] = reason
    return True


def _is_dead_model(provider_name: str, base_url: str, model: str) -> bool:
    if not model:
        return False
    with _DEAD_MODEL_REGISTRY_LOCK:
        return (str(provider_name), str(base_url), str(model)) in _DEAD_MODEL_REGISTRY


def reset_dead_model_cache() -> None:
    """Empty the dead-model cache (test isolation hook; also usable by ops
    after a config reload without a process restart)."""
    with _DEAD_MODEL_REGISTRY_LOCK:
        _DEAD_MODEL_REGISTRY.clear()
        _DEAD_MODEL_REASONS.clear()
    with _RATE_LIMIT_STREAKS_LOCK:
        _RATE_LIMIT_STREAKS.clear()


def _rate_limit_threshold() -> int:
    """Consecutive-429 threshold; invalid/non-positive env → default 3."""
    try:
        value = int(os.environ.get("SCP_LLM_RATE_LIMIT_BREAKER_THRESHOLD", ""))
    except (TypeError, ValueError):
        return _RATE_LIMIT_THRESHOLD_DEFAULT
    return value if value >= 1 else _RATE_LIMIT_THRESHOLD_DEFAULT


def _record_rate_limit_hit(provider_name: str, base_url: str, model: str) -> bool:
    """Count one consecutive 429 for the candidate. Returns True when the
    streak reached the kill threshold (candidate must be cached dead)."""
    key = (str(provider_name), str(base_url), str(model))
    with _RATE_LIMIT_STREAKS_LOCK:
        _RATE_LIMIT_STREAKS[key] = _RATE_LIMIT_STREAKS.get(key, 0) + 1
        return _RATE_LIMIT_STREAKS[key] >= _rate_limit_threshold()


def _clear_rate_limit_streak(provider_name: str, base_url: str, model: str) -> None:
    """Any non-429 outcome for the candidate resets its consecutive streak."""
    key = (str(provider_name), str(base_url), str(model))
    with _RATE_LIMIT_STREAKS_LOCK:
        _RATE_LIMIT_STREAKS.pop(key, None)
