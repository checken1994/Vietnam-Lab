"""
SCP LLM Gateway — Unified LLM client (API-only).
Single source of truth for ALL LLM calls in SCP (inference + learning).
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

# [S21 HEDGE] Hedged LLM race (parallel fallback when slow ~10s).
HEDGE_DEFAULT_ATTEMPT_TIMEOUT_SECONDS = 10.0
HEDGE_DEFAULT_MAX_SECONDS = 90.0
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


def _match_preferred_provider(provider_name: str, preferred: str) -> bool:
    """Check if provider name matches preferred provider target."""
    pn = provider_name.lower().strip()
    target = preferred.lower().strip()
    return pn == target or target in pn or pn.startswith(target) or pn.replace("_compat", "") == target


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

# [MULTI-PROVIDER] First-class AI providers supported out-of-the-box.
# Format: (PROVIDER_NAME, KEY_ENV, BASE_URL_ENV, MODEL_ENV, default_model, default_base_url)
_BUILTIN_COMPAT_PROVIDERS: tuple[tuple[str, str, str, str, str, str], ...] = (
    ("openai_compat", "OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL", "", "https://api.openai.com/v1"),
    ("gemini_compat", "GEMINI_API_KEY", "GEMINI_BASE_URL", "GEMINI_MODEL", "gemini-2.0-flash", "https://generativelanguage.googleapis.com/v1beta/openai"),
    ("deepseek", "DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "DEEPSEEK_MODEL", "deepseek-chat", "https://api.deepseek.com/v1"),
    ("groq", "GROQ_API_KEY", "GROQ_BASE_URL", "GROQ_MODEL", "llama-3.3-70b-versatile", "https://api.groq.com/openai/v1"),
    ("mistral", "MISTRAL_API_KEY", "MISTRAL_BASE_URL", "MISTRAL_MODEL", "mistral-small-latest", "https://api.mistral.ai/v1"),
)


class LLMGateway:
    """Unified LLM gateway with task routing and provider failover."""

    def __init__(self):
        self._preferred_provider: str = ""
        tasks = ("autofix", "why", "learning", "fast_learning", "judge", "chat", "route")
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

    def set_preferred_provider(self, provider_name: str) -> None:
        """Set a default preferred provider name for this gateway instance."""
        self._preferred_provider = str(provider_name or "")

    def _parse_extra_providers(self) -> None:
        spec = os.environ.get("SCP_LLM_FALLBACK_PROVIDERS", "")
        tasks = ("autofix", "why", "learning", "fast_learning", "judge", "chat", "route", "default")

        # Builtin first-class AI providers (OpenAI, Gemini, DeepSeek, Groq, Mistral)
        for name, key_env, base_env, model_env, def_model, def_base in _BUILTIN_COMPAT_PROVIDERS:
            for task in tasks:
                self._extra_providers.setdefault(task, []).append(
                    EnvCompatProvider(
                        name, task, key_env, base_env, model_env,
                        default_model=def_model, default_base_url=def_base,
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

    def _provider_chain(self, task: str, preferred_provider: str = "") -> list:
        """Chuỗi failover theo task, có hỗ trợ reorder theo preferred_provider."""
        openrouter = {
            "autofix":       self.openrouter_autofix,
            "why":           self.openrouter_why,
            "learning":      self.openrouter_learning,
            "fast_learning": self.openrouter_fast_learning,
            "judge":         self.openrouter_judge,
            "chat":          self.openrouter_chat,
            "route":         getattr(self, "openrouter_route", self.openrouter_default),
        }.get(task, self.openrouter_default)

        extra = self._extra_providers.get(task) or self._extra_providers.get("default", [])
        # [F-21] Ưu tiên phần tử enabled để tránh shadow fallback provider.
        openai_compat = (
            next((p for p in extra if p.PROVIDER_NAME == "openai_compat" and p.enabled), None)
            or next((p for p in extra if p.PROVIDER_NAME == "openai_compat"), None)
        )
        gemini_compat = (
            next((p for p in extra if p.PROVIDER_NAME == "gemini_compat" and p.enabled), None)
            or next((p for p in extra if p.PROVIDER_NAME == "gemini_compat"), None)
        )

        chain = []
        # Priority 1: Custom OpenAI API (if enabled by env vars)
        if openai_compat and openai_compat.enabled:
            chain.append(openai_compat)

        # Priority 2: OpenRouter (if enabled)
        if openrouter.enabled:
            chain.append(openrouter)

        # Priority 3: Native Google Gemini API (if enabled by env vars)
        if gemini_compat and gemini_compat.enabled:
            chain.append(gemini_compat)

        # Priority 4: Other first-class / fallback providers (DeepSeek, Groq, Mistral, custom)
        for p in extra:
            if not getattr(p, "enabled", False):
                continue
            if not any(getattr(c, "PROVIDER_NAME", None) == p.PROVIDER_NAME for c in chain):
                chain.append(p)

        pref = (preferred_provider or getattr(self, "_preferred_provider", "")).strip().lower()
        if pref and pref != "auto":
            match_idx = next(
                (i for i, p in enumerate(chain) if _match_preferred_provider(getattr(p, "PROVIDER_NAME", ""), pref)),
                None,
            )
            if match_idx is not None and match_idx > 0:
                chain.insert(0, chain.pop(match_idx))

        return chain

    def has_active_providers(self) -> bool:
        """Return True if at least one LLM provider is active and enabled."""
        return self.enabled_provider_count() > 0

    def get_active_provider_names(self) -> list[str]:
        """Return list of distinct enabled LLM provider names."""
        names: list[str] = []
        if getattr(self, "openrouter_default", None) and self.openrouter_default.enabled:
            names.append("openrouter")
        for p in self._extra_providers.get("default", []):
            if getattr(p, "enabled", False) and p.PROVIDER_NAME not in names:
                names.append(p.PROVIDER_NAME)
        return names

    def operational_mode(self) -> str:
        """Return 'connected_llm' if any provider active, else 'local_verified'."""
        return "connected_llm" if self.has_active_providers() else "local_verified"

    def enabled_provider_count(
        self,
        tasks: tuple[str, ...] = ("chat", "default", "judge", "autofix"),
    ) -> int:
        """[HC-31 · 2026-10-24] Số provider ENABLED RIÊNG BIỆT phục vụ vòng
        sinh→chấm (generation: chat/default; adjudication cascade: judge/autofix).

        Đếm theo bản sắc logic (PROVIDER_NAME, base_url): các object OpenRouter
        khác task (openrouter_chat/openrouter_judge… khác model mặc định) cùng
        endpoint + key pool tính MỘT provider. Kết quả == 1 nghĩa là TOÀN BỘ
        sinh lẫn chấm rơi về cùng một provider/model — self-certification
        (HC-31): cùng model vừa sinh vừa chấm, không có cross-model second
        opinion thật.
        """
        seen: set[tuple] = set()
        for task in tasks:
            try:
                chain = self._provider_chain(task)
            except Exception:
                continue
            for p in chain:
                if not getattr(p, "enabled", False):
                    continue
                seen.add((
                    getattr(p, "PROVIDER_NAME", id(p)),
                    str(getattr(p, "base_url", "") or ""),
                ))
        return len(seen)

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
        messages: list[dict] | None = None,
        preferred_provider: str = "",
    ) -> tuple[str | None, str]:
        """Chat with LLM — đa provider failover."""
        self._bump_stat("total_calls")  # [AUDIT-FIX low-7d]
        prioritize_free = False
        if os.environ.get("SCP_BUDGET_ROUTING", "0") == "1":
            from scp.core.budget_engine import order_tiers
            tiers = order_tiers(question + context, task)
            if tiers[0] == "free":
                prioritize_free = True

        pref = (preferred_provider or getattr(self, "_preferred_provider", "")).strip().lower()
        try:
            chain = self._provider_chain(task, preferred_provider=pref)
        except TypeError:
            chain = self._provider_chain(task)
            if pref and pref != "auto":
                match_idx = next(
                    (i for i, p in enumerate(chain) if _match_preferred_provider(getattr(p, "PROVIDER_NAME", ""), pref)),
                    None,
                )
                if match_idx is not None and match_idx > 0:
                    chain = list(chain)
                    chain.insert(0, chain.pop(match_idx))
        enabled = [p for p in chain if p.enabled]
        healthy = [p for p in enabled if not p._breaker.is_open()]
        degraded = [p for p in enabled if p._breaker.is_open()]
        pool = healthy + degraded
        if not pool:
            self._bump_stat("failures")  # [AUDIT-FIX low-7d]
            return None, "none"

        if pref and pref != "auto" and any(_match_preferred_provider(getattr(p, "PROVIDER_NAME", ""), pref) for p in pool):
            pref_match = next(p for p in pool if _match_preferred_provider(getattr(p, "PROVIDER_NAME", ""), pref))
            others = [p for p in pool if p is not pref_match]
            rotation = [pref_match] + others
        else:
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
                attempt_deadline, hedge_cap, messages=messages,
            )
        return await self._chat_sequential(
            rotation, question, context, system_prompt, prioritize_free,
            messages=messages,
        )

    @staticmethod
    async def _invoke_provider_chat(
        provider,
        question: str,
        context: str,
        system_prompt: str,
        prioritize_free: bool,
        messages: list[dict] | None = None,
    ) -> tuple[str | None, str]:
        """Call provider.chat with backward compatibility for legacy / mock providers."""
        if messages is not None:
            try:
                return await provider.chat(
                    question,
                    context,
                    system_prompt,
                    prioritize_free=prioritize_free,
                    messages=messages,
                )
            except TypeError as te:
                if "messages" in str(te):
                    return await provider.chat(
                        question,
                        context,
                        system_prompt,
                        prioritize_free=prioritize_free,
                    )
                raise
        return await provider.chat(
            question,
            context,
            system_prompt,
            prioritize_free=prioritize_free,
        )

    async def _chat_sequential(
        self,
        rotation: list,
        question: str,
        context: str,
        system_prompt: str,
        prioritize_free: bool,
        messages: list[dict] | None = None,
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
                    self._invoke_provider_chat(
                        provider,
                        question,
                        context,
                        system_prompt,
                        prioritize_free=prioritize_free,
                        messages=messages,
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
        messages: list[dict] | None = None,
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
                self._invoke_provider_chat(
                    provider,
                    question,
                    context,
                    system_prompt,
                    prioritize_free=prioritize_free,
                    messages=messages,
                )
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
        question: str = "",
        context: str = "",
        system_prompt: str = "",
        task: str = "default",
        messages: list[dict] | None = None,
        preferred_provider: str = "",
    ) -> tuple[str | None, str]:
        """Sync wrapper for background threads. Runs async chat safely."""
        coro = self.chat(
            question, context, system_prompt, task=task, messages=messages,
            preferred_provider=preferred_provider,
        )
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


def peek_gateway() -> "LLMGateway | None":
    """[HC-31] Trả singleton gateway HIỆN CÓ mà KHÔNG khởi tạo (zero side
    effect). Verification stage dùng để đếm provider lúc judge chạy; None =
    gateway chưa tồn tại (custom judge / chưa từng gọi LLM) → caller KHÔNG
    được dán nhãn self_certified thiếu căn cứ (UNKNOWN thay vì True/False)."""
    return _gateway


def chat_sync(
    question: str = "",
    context: str = "",
    system_prompt: str = "",
    task: str = "default",
    messages: list[dict] | None = None,
    preferred_provider: str = "",
) -> tuple[str | None, str]:
    """Module-level sync shortcut. Uses singleton gateway."""
    return get_gateway().chat_sync(
        question, context, system_prompt, task=task, messages=messages,
        preferred_provider=preferred_provider,
    )


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
    "peek_gateway",
    "httpx",
    "itertools",
    "load_openrouter_keys",
    "random",
    "reset_dead_model_cache",
]
