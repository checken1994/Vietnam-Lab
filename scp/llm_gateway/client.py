"""
SCP LLM Gateway — Unified LLM client (API-only).

Single source of truth for ALL LLM calls in SCP (inference + learning).

Provider Strategy (2026-08-31):
  Gateway chỉ gọi OpenRouter API trực tiếp (primary).
  Fallback: OpenAI-compatible provider qua env (OPENAI_API_KEY/OPENAI_BASE_URL)
  hoặc bất kỳ provider nào khai báo qua SCP_LLM_FALLBACK_PROVIDERS.
  Không còn hardcode bất kỳ provider cụ thể nào (Ollama, Groq đã bị gỡ).

Design:
  - Async-first (httpx.AsyncClient) for inference path (/ask)
  - chat_sync() wrapper for background threads (learning engines)
  - Task routing: mỗi task có một OpenRouterProvider riêng
    (primary = OPENROUTER_MODEL, free fallback per task, key round-robin)
  - Singleton get_gateway() — shared connection pool, thread-safe init
"""
from __future__ import annotations

import asyncio
import ipaddress
import itertools
import logging
import math
import os
import random
import threading
import time

import httpx

from scp.llm_gateway.circuit_breaker import (
    _DEAD_MODEL_REASONS,
    _DEAD_MODEL_REGISTRY,
    _DEAD_MODEL_REGISTRY_LOCK,
    _HEDGE_OFF_VALUES,
    _MODEL_NOT_FOUND_MARKERS,
    _RATE_LIMIT_STREAKS,
    _RATE_LIMIT_STREAKS_LOCK,
    _RATE_LIMIT_THRESHOLD_DEFAULT,
    _REASON_MODEL_NOT_FOUND,
    _REASON_PAYMENT_DEAD,
    _REASON_RATE_DEAD,
    _RECORDED_FAILURE_PREFIX,
    CircuitBreaker,
    _clear_rate_limit_streak,
    _dead_model_breaker_enabled,
    _is_dead_model,
    _is_model_not_found_response,
    _mark_dead_model,
    _rate_limit_threshold,
    _record_rate_limit_hit,
    reset_dead_model_cache,
)
from scp.llm_gateway.egress_policy import llm_egress_allowed
from scp.llm_gateway.providers import (
    _BACKOFF_JITTER_RNG,
    _PLACEHOLDER_KEYS,
    OPENROUTER_FREE_MODELS,
    EnvCompatProvider as _EnvCompatProviderBase,
    OpenRouterProvider as _OpenRouterProviderBase,
)
from scp.security.provider_keys import ProviderCredentialError, load_openrouter_keys
from scp.security.url_safety import EgressDeniedError, enforce_egress_policy  # [EE-G1]

logger = logging.getLogger("scp.llm_gateway")


class OpenRouterProvider(_OpenRouterProviderBase):
    """OpenRouter provider with round-robin keys and model fallback cascade."""


class EnvCompatProvider(_EnvCompatProviderBase):
    """Generic OpenAI-compatible provider configured via environment variables."""


# Sync wrapper hard timeout: OpenRouter client timeout is 60s inside
# _call_model; the sync wrapper adds headroom for thread-pool scheduling.
SYNC_CALL_TIMEOUT_SECONDS = 90

# ============================================================
# [S21 HEDGE] Hedged LLM race — owner directive: "khi LLM trả kết quả chậm
# khoảng 10s -> chuyển sang LLM khác hoặc API khác".
#   - SCP_LLM_ATTEMPT_TIMEOUT_SECONDS (default 10): deadline cho MỖI attempt;
#     quá hạn → bắn provider kế tiếp trong chain vào race SONG SONG (provider
#     cũ không bị hủy — ai về trước thắng).
#   - SCP_LLM_HEDGE_MAX_SECONDS (default 90): cap tổng cho toàn bộ race;
#     quá cap → fail-closed (None + error rõ), không chờ vô hạn.
#   - SCP_LLM_HEDGE=off: kill-switch → quay về failover tuần tự cũ.
# Env parse FAIL-CLOSED: giá trị lỗi/0/âm/non-finite → default.
# ============================================================
HEDGE_DEFAULT_ATTEMPT_TIMEOUT_SECONDS = 10.0
HEDGE_DEFAULT_MAX_SECONDS = 90.0

# ============================================================
# [W1-c5 2026-10-02] Sequential failover total cap.
# ============================================================
SEQ_DEFAULT_MAX_SECONDS = 90.0


def _seq_max_seconds() -> float:
    """Cap tổng (giây) cho failover tuần tự từ SCP_LLM_SEQ_MAX_SECONDS."""
    return _parse_positive_seconds(
        os.environ.get("SCP_LLM_SEQ_MAX_SECONDS"), SEQ_DEFAULT_MAX_SECONDS
    )


def _parse_positive_seconds(raw: str | None, default: float) -> float:
    """Parse giây từ env; giá trị lỗi/0/âm/non-finite → default (fail-closed)."""
    if raw is None:
        return default
    try:
        value = float(raw.strip())
    except (TypeError, ValueError):
        return default
    if not math.isfinite(value) or value <= 0:
        return default
    return value


def _hedge_settings() -> tuple[bool, float, float]:
    """(enabled, attempt_deadline_seconds, hedge_max_seconds) từ env."""
    enabled = os.environ.get("SCP_LLM_HEDGE", "on").strip().lower() not in _HEDGE_OFF_VALUES
    attempt_deadline = _parse_positive_seconds(
        os.environ.get("SCP_LLM_ATTEMPT_TIMEOUT_SECONDS"),
        HEDGE_DEFAULT_ATTEMPT_TIMEOUT_SECONDS,
    )
    hedge_cap = _parse_positive_seconds(
        os.environ.get("SCP_LLM_HEDGE_MAX_SECONDS"),
        HEDGE_DEFAULT_MAX_SECONDS,
    )
    return enabled, attempt_deadline, hedge_cap


def _is_loopback_host(host: str) -> bool:
    """Return True only for explicit loopback hosts/addresses."""
    normalized = host.strip().rstrip(".").lower()
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        logger.debug("_is_loopback_host: ValueError ignored", exc_info=True)
        return False


def _llm_egress_allowed(base_url: str) -> bool:
    """Enforce the SCP LLM egress contract delegating to unified egress policy."""
    return llm_egress_allowed(base_url)


# ============================================================
# GATEWAY — single entry point with task routing
# ============================================================

class LLMGateway:
    """Unified LLM gateway — OpenRouter API only, routed per task.

    Task routing (primary model is OPENROUTER_MODEL for every task; the
    per-task provider differs only in its FREE fallback model):
        task="autofix"       → free fallback nemotron-3-ultra-550b:free
        task="why"           → free fallback gemma-4-31b-it:free
        task="learning"      → free fallback gemma-4-31b-it:free
        task="fast_learning" → free fallback nemotron-3-nano-30b:free
        task="judge"         → free fallback nemotron-3-super-120b:free
        task="chat"          → free fallback gpt-oss-20b:free
        task="default"       → free fallback gpt-oss-20b:free

    Usage (async):
        gw = get_gateway()
        answer, provider = await gw.chat("What is 2+2?", task="default")

    Usage (sync — for background threads):
        answer, provider = chat_sync("What is 2+2?", task="learning")
    """

    def __init__(self):
        tasks = ("autofix", "why", "learning", "fast_learning", "judge", "chat")
        # Tier 1 — OpenRouter (primary, per-task FREE fallback map).
        for task in tasks:
            provider = OpenRouterProvider(task=task)
            setattr(self, f"openrouter_{task}", provider)
        self.openrouter_default = OpenRouterProvider(task="default")
        # Backward-compat alias — old code used `gateway.openrouter`.
        self.openrouter = self.openrouter_default
        # Tier 3 — provider OpenAI-compatible khai báo qua env (không sửa code).
        self._extra_providers: dict[str, list[EnvCompatProvider]] = {t: [] for t in tasks + ("default",)}
        self._parse_extra_providers()
        self._stats_lock = threading.Lock()  # [AUDIT-FIX low-7d] telemetry-only
        self._rr_counter = 0  # brand-neutral rotation: không ưu tiên model nào
        self._stats = {
            "total_calls": 0,
            "openrouter_calls": 0,
            "extra_calls": 0,
            "failover_count": 0,
            "failures": 0,
            # [S21 HEDGE] telemetry — cùng store _stats hiện có, không store mới.
            "hedge_fires": 0,
            "hedge_wins": 0,
            "hedge_caps": 0,
        }

    def _parse_extra_providers(self) -> None:
        spec = os.environ.get("SCP_LLM_FALLBACK_PROVIDERS", "")
        tasks = ("autofix", "why", "learning", "fast_learning", "judge", "chat", "default")

        # [NEW] 1. Generic OpenAI API (highest priority if configured)
        for task in tasks:
            self._extra_providers.setdefault(task, []).append(
                EnvCompatProvider(
                    "openai_compat", task,
                    "OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL",
                    default_base_url="https://api.openai.com/v1"
                )
            )

        for entry in (e.strip() for e in spec.split(",") if e.strip()):
            parts = [p.strip() for p in entry.split(":")]
            if len(parts) != 4:
                logger.warning("[FAILOVER] malformed SCP_LLM_FALLBACK_PROVIDERS entry (want name:KEY_ENV:BASEURL_ENV:MODEL_ENV): %s", entry)
                continue
            name, key_env, base_env, model_env = parts
            for task in tasks:
                self._extra_providers.setdefault(task, []).append(
                    EnvCompatProvider(name, task, key_env, base_env, model_env)
                )

    def _provider_chain(self, task: str) -> list:
        """Chuỗi failover theo task."""
        openrouter = {
            "autofix":       self.openrouter_autofix,
            "why":           self.openrouter_why,
            "learning":      self.openrouter_learning,
            "fast_learning": self.openrouter_fast_learning,
            "judge":         self.openrouter_judge,
            "chat":          self.openrouter_chat,
        }.get(task, self.openrouter_default)

        extra = self._extra_providers.get(task, [])
        openai_compat = extra[0] if extra and extra[0].PROVIDER_NAME == "openai_compat" else None

        chain = []
        # Priority 1: Custom OpenAI API (if enabled by env vars)
        if openai_compat and openai_compat.enabled:
            chain.append(openai_compat)

        # Priority 2: OpenRouter (if enabled)
        if openrouter.enabled:
            chain.append(openrouter)

        # Priority 3: Other fallback providers
        if extra:
            start_idx = 1 if openai_compat else 0
            chain.extend(extra[start_idx:])

        return chain

    def _bump_stat(self, key: str, amount: int = 1) -> None:
        """[AUDIT-FIX low-7d] Increment telemetry counter dưới lock. Telemetry
        only — không dùng làm tín hiệu điều khiển."""
        with self._stats_lock:
            self._stats[key] = self._stats.get(key, 0) + amount

    async def chat(
        self,
        question: str,
        context: str = "",
        system_prompt: str = "",
        task: str = "default",
    ) -> tuple[str | None, str]:
        """Chat with LLM — đa provider failover.

        Thứ tự: OpenRouter (primary) → provider env-declared (OPENAI_API_KEY) → SCP_LLM_FALLBACK_PROVIDERS.
        Provider bị rate-limit (429/402) hoặc breaker OPEN → chuyển NGAY sang
        provider kế tiếp, caller không thấy lỗi, không đốt time-out.
        """
        self._bump_stat("total_calls")  # [AUDIT-FIX low-7d]
        prioritize_free = False
        if os.environ.get("SCP_BUDGET_ROUTING", "0") == "1":
            from scp.core.budget_engine import order_tiers
            tiers = order_tiers(question + context, task)
            if tiers[0] == "free":
                prioritize_free = True

        chain = self._provider_chain(task)
        # [KHÔNG ƯU TIÊN MODEL] Pool brand-neutral: provider khỏe xoay vòng theo
        # lượt gọi (chia tải đều, không đặt clip nào lên trên vĩnh viễn);
        # provider breaker OPEN bị đẩy xuống cuối (chỉ dùng khi hết người khỏe).
        enabled = [p for p in chain if p.enabled]
        healthy = [p for p in enabled if not p._breaker.is_open()]
        degraded = [p for p in enabled if p._breaker.is_open()]
        pool = healthy + degraded
        if not pool:
            self._bump_stat("failures")  # [AUDIT-FIX low-7d]
            return None, "none"
        with self._stats_lock:  # [AUDIT-FIX low-7d] atomic rotate
            start = self._rr_counter % len(pool)
            self._rr_counter += 1
        rotation = pool[start:] + pool[:start]

        # [S21 HEDGE] Owner directive: LLM chậm ~10s → bắn LLM/API khác song
        # song, ai trả trước thắng. Kill-switch SCP_LLM_HEDGE=off hoặc chain
        # chỉ có 1 provider → failover tuần tự cũ (giữ nguyên hành vi).
        hedge_on, attempt_deadline, hedge_cap = _hedge_settings()
        if hedge_on and len(rotation) > 1:
            return await self._chat_hedged(
                rotation, question, context, system_prompt, prioritize_free,
                attempt_deadline, hedge_cap,
            )
        return await self._chat_sequential(
            rotation, question, context, system_prompt, prioritize_free,
        )

    async def _chat_sequential(
        self,
        rotation: list,
        question: str,
        context: str,
        system_prompt: str,
        prioritize_free: bool,
    ) -> tuple[str | None, str]:
        """Failover TUẦN TỰ gốc (SCP_LLM_HEDGE=off hoặc chain 1 provider).

        [W1-c5] Có cap tổng ``SCP_LLM_SEQ_MAX_SECONDS`` (default 90): mỗi
        provider chỉ được dùng phần budget còn lại của cap; provider đang bay
        quá hạn bị hủy và (nếu còn budget) failover sang provider kế tiếp;
        hết budget → fail-closed ``(None, "none")`` thay vì chờ vô hạn.
        Timeout do cap không record breaker failure (slow ≠ dead).
        """
        started = time.monotonic()
        total_cap = _seq_max_seconds()
        attempted = 0
        for provider in rotation:
            remaining = total_cap - (time.monotonic() - started)
            if remaining <= 0:
                logger.warning(
                    "[LLM Gateway] sequential failover cap %.1fs exhausted after "
                    "%d attempt(s) — fail-closed",
                    total_cap,
                    attempted,
                )
                self._bump_stat("seq_cap_exhausted")
                break
            attempted += 1
            self._bump_stat(f"{provider.PROVIDER_NAME}_calls")  # [AUDIT-FIX low-7d]
            try:
                answer, _provider_label = await asyncio.wait_for(
                    provider.chat(
                        question,
                        context,
                        system_prompt,
                        prioritize_free=prioritize_free,
                    ),
                    timeout=remaining,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "[LLM Gateway] %s exceeded remaining sequential budget (%.1fs) — "
                    "failover",
                    provider.PROVIDER_NAME,
                    remaining,
                )
                self._bump_stat("seq_cap_timeouts")
                answer, _provider_label = None, None
            if answer:
                if attempted > 1:
                    self._bump_stat("failover_count")  # [AUDIT-FIX low-7d]
                return answer, _provider_label
            # provider trả None (quota/rate-limit/breaker/timeout) → sang provider kế

        self._bump_stat("failures")  # [AUDIT-FIX low-7d]
        return None, "none"

    async def _chat_hedged(
        self,
        rotation: list,
        question: str,
        context: str,
        system_prompt: str,
        prioritize_free: bool,
        attempt_deadline: float,
        hedge_cap: float,
    ) -> tuple[str | None, str]:
        """[S21] Hedged race — first-result-wins đa provider."""
        loop = asyncio.get_running_loop()
        start = time.monotonic()
        cap_at = start + hedge_cap
        pending: set[asyncio.Task] = set()
        idx_by_task: dict[asyncio.Task, int] = {}
        expiry: dict[asyncio.Task, float] = {}  # hedge-fire deadline per attempt
        next_idx = 0
        errors: list[str] = []

        def _launch_next(reason: str, slow_provider: str = "") -> None:
            nonlocal next_idx
            if next_idx >= len(rotation):
                return
            provider = rotation[next_idx]
            task_idx = next_idx
            next_idx += 1
            self._bump_stat(f"{provider.PROVIDER_NAME}_calls")  # [AUDIT-FIX low-7d]
            task = loop.create_task(
                provider.chat(question, context, system_prompt, prioritize_free=prioritize_free)
            )
            pending.add(task)
            idx_by_task[task] = task_idx
            expiry[task] = time.monotonic() + attempt_deadline
            if reason == "hedge":
                self._bump_stat("hedge_fires")  # [AUDIT-FIX low-7d]
                logger.info(
                    "[S21 HEDGE] fire: %s chưa trả sau deadline %.1fs → bắn thêm %s (chain idx %d) vào race song song",
                    slow_provider, attempt_deadline, provider.PROVIDER_NAME, task_idx,
                )

        _launch_next("first")
        winner: tuple[str, str, int] | None = None
        cap_hit = False
        try:
            while pending or next_idx < len(rotation):
                now = time.monotonic()
                if now >= cap_at:
                    cap_hit = True
                    break
                if not pending:
                    # Defensive: hết task đang chạy mà còn provider → bắn tiếp.
                    _launch_next("after-failure")
                    continue
                # Thức dậy sớm nhất tại: hedge-fire deadline gần nhất (nếu còn
                # provider để bắn) hoặc hedge cap — không bao giờ chờ vô hạn.
                wake = cap_at
                if next_idx < len(rotation) and expiry:
                    wake = min(wake, min(expiry[t] for t in pending if t in expiry))
                timeout = max(0.0, wake - now)
                done, _still_pending = await asyncio.wait(
                    set(pending), timeout=timeout, return_when=asyncio.FIRST_COMPLETED
                )
                # Winner: success đầu tiên theo THỨ TỰ chain (deterministic
                # tie-break khi nhiều task về đích cùng một lượt wait).
                for task in sorted(done, key=lambda t: idx_by_task[t]):
                    pending.discard(task)
                    expiry.pop(task, None)
                    idx = idx_by_task.pop(task)
                    try:
                        answer, label = task.result()
                    except Exception as exc:  # provider.chat tự nuốt lỗi; phòng hộ fail-closed
                        logger.debug("LLMGateway._chat_hedged: exception ignored: %s", exc, exc_info=True)
                        errors.append(f"{rotation[idx].PROVIDER_NAME}: {type(exc).__name__}")
                        continue
                    if answer:
                        winner = (answer, label, idx)
                        break
                    errors.append(f"{rotation[idx].PROVIDER_NAME}: {label}")
                if winner is not None:
                    break
                if done and next_idx < len(rotation):
                    # Provider vừa LỖI → chuyển NGAY sang kế tiếp (failover
                    # tuần tự giữa các lỗi; hedge song song chỉ cho provider CHẬM).
                    _launch_next("after-failure")
                # Hedge-fire: task còn chạy quá attempt deadline → bắn kế tiếp
                # SONG SONG vào race (không hủy task đang chạy).
                now = time.monotonic()
                for task in [t for t in list(expiry) if expiry[t] <= now]:
                    expiry.pop(task, None)  # mỗi provider chỉ được fire đúng 1 lần
                    if next_idx < len(rotation):
                        _launch_next("hedge", slow_provider=rotation[idx_by_task[task]].PROVIDER_NAME)
        finally:
            # Dọn dẹp: winner đã chắc chắn (hoặc cap/toàn fail) → hủy phần còn
            # lại. Hủy DIỄN RA SAU khi winner đã được lấy kết quả — an toàn.
            leftover = [t for t in pending if not t.done()]
            for task in leftover:
                task.cancel()
            if leftover:
                await asyncio.gather(*leftover, return_exceptions=True)

        if winner is not None:
            answer, label, idx = winner
            if idx > 0:
                self._bump_stat("failover_count")  # [AUDIT-FIX low-7d]
            self._bump_stat("hedge_wins")  # [AUDIT-FIX low-7d]
            logger.info(
                "[S21 HEDGE] race won by %s sau %.2fs (first_provider_won=%s, hedge_fires=%d)",
                label, time.monotonic() - start, idx == 0, self._stats["hedge_fires"],
            )
            return answer, label

        self._bump_stat("failures")  # [AUDIT-FIX low-7d]
        if cap_hit:
            self._bump_stat("hedge_caps")  # [AUDIT-FIX low-7d]
            logger.error(
                "[S21 HEDGE] cap %.0fs vượt quá sau %.2fs — fail-closed (không answer); lỗi các attempt: %s",
                hedge_cap, time.monotonic() - start, "; ".join(errors) or "no-attempt-error",
            )
            return None, "hedge_cap_exceeded"
        logger.error(
            "[S21 HEDGE] tất cả provider trong race đều lỗi: %s",
            "; ".join(errors) or "unknown",
        )
        return None, "none"

    def chat_sync(
        self,
        question: str,
        context: str = "",
        system_prompt: str = "",
        task: str = "default",
    ) -> tuple[str | None, str]:
        """Sync wrapper for background threads. Runs async chat safely."""
        coro = self.chat(question, context, system_prompt, task=task)
        _abandoned_worker_owns_coro = False
        try:
            try:
                asyncio.get_running_loop()
                _in_async = True
            except RuntimeError:
                logger.debug("LLMGateway.chat_sync: RuntimeError ignored", exc_info=True)
                _in_async = False

            if _in_async:
                import concurrent.futures
                pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
                try:
                    future = pool.submit(asyncio.run, coro)
                    return future.result(timeout=SYNC_CALL_TIMEOUT_SECONDS)
                except concurrent.futures.TimeoutError:
                    _abandoned_worker_owns_coro = True
                    logger.warning(
                        "chat_sync: vượt SYNC_CALL_TIMEOUT %.0fs — abandon worker "
                        "thread (bounded leak: đúng 1 worker/lần timeout, worker tự "
                        "kết thúc khi coroutine hoàn tất; caller không join nữa)",
                        SYNC_CALL_TIMEOUT_SECONDS,
                    )
                    raise
                finally:
                    pool.shutdown(wait=False)
            else:
                return asyncio.run(coro)
        except Exception as e:
            _err = e  # save before inner try deletes e
            try:
                if not _abandoned_worker_owns_coro:
                    coro.close()
            except Exception:
                logger.exception("[client.py:608] silenced exception")
            logger.warning("chat_sync failed: %s", _err, exc_info=True)
            self._bump_stat("failures")  # [AUDIT-FIX low-7d]
            return None, "none"

    def stats(self) -> dict:
        extras: dict[str, dict] = {}
        for providers in self._extra_providers.values():
            for provider in providers:
                if provider.PROVIDER_NAME not in extras:
                    extras[provider.PROVIDER_NAME] = provider.stats()
        with self._stats_lock:  # [AUDIT-FIX low-7d] consistent snapshot
            stats_snapshot = dict(self._stats)
        return {
            **stats_snapshot,
            "openrouter_default":  self.openrouter_default.stats(),
            "openrouter_autofix":  self.openrouter_autofix.stats(),
            "openrouter_why":      self.openrouter_why.stats(),
            "openrouter_learning": self.openrouter_learning.stats(),
            "openrouter_fast_learning": self.openrouter_fast_learning.stats(),
            "openrouter_judge":    self.openrouter_judge.stats(),
            "extra_providers": extras,
        }


# ============================================================
# SINGLETON (thread-safe — EXEC-2 R3)
# ============================================================

_gateway: LLMGateway | None = None
_gateway_lock = threading.Lock()


def get_gateway() -> LLMGateway:
    """Get or create the singleton LLM gateway (thread-safe)."""
    global _gateway
    if _gateway is None:
        with _gateway_lock:
            if _gateway is None:
                _gateway = LLMGateway()
                try:
                    from scp.llm_gateway.free_catalog import start_background_refresh
                    start_background_refresh()
                except Exception:
                    logger.warning("get_gateway: Exception not handled", exc_info=True)
    return _gateway


def chat_sync(
    question: str,
    context: str = "",
    system_prompt: str = "",
    task: str = "default",
) -> tuple[str | None, str]:
    """Module-level sync shortcut. Uses singleton gateway."""
    return get_gateway().chat_sync(question, context, system_prompt, task=task)


__all__ = [
    "CircuitBreaker",
    "EgressDeniedError",
    "EnvCompatProvider",
    "HEDGE_DEFAULT_ATTEMPT_TIMEOUT_SECONDS",
    "HEDGE_DEFAULT_MAX_SECONDS",
    "LLMGateway",
    "OPENROUTER_FREE_MODELS",
    "OpenRouterProvider",
    "ProviderCredentialError",
    "SEQ_DEFAULT_MAX_SECONDS",
    "SYNC_CALL_TIMEOUT_SECONDS",
    "_BACKOFF_JITTER_RNG",
    "_DEAD_MODEL_REASONS",
    "_DEAD_MODEL_REGISTRY",
    "_DEAD_MODEL_REGISTRY_LOCK",
    "_HEDGE_OFF_VALUES",
    "_MODEL_NOT_FOUND_MARKERS",
    "_PLACEHOLDER_KEYS",
    "_RATE_LIMIT_STREAKS",
    "_RATE_LIMIT_STREAKS_LOCK",
    "_RATE_LIMIT_THRESHOLD_DEFAULT",
    "_REASON_MODEL_NOT_FOUND",
    "_REASON_PAYMENT_DEAD",
    "_REASON_RATE_DEAD",
    "_RECORDED_FAILURE_PREFIX",
    "_clear_rate_limit_streak",
    "_dead_model_breaker_enabled",
    "_hedge_settings",
    "_is_dead_model",
    "_is_loopback_host",
    "_is_model_not_found_response",
    "_llm_egress_allowed",
    "_mark_dead_model",
    "_parse_positive_seconds",
    "_rate_limit_threshold",
    "_record_rate_limit_hit",
    "_seq_max_seconds",
    "chat_sync",
    "enforce_egress_policy",
    "get_gateway",
    "httpx",
    "itertools",
    "load_openrouter_keys",
    "random",
    "reset_dead_model_cache",
]
