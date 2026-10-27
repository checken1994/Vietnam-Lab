"""Provider implementations for LLM Gateway (OpenRouter and EnvCompat).

Extracted from scp.llm_gateway.client for modularization and maintainability.
"""
from __future__ import annotations

import asyncio
import itertools
import logging
import os
import random
import sys
import threading

import httpx

# [HC-03 / B3-W1 2026-10-23] Điểm đọc env chuẩn (settings.get — live, strip,
# không cache). Secret (key_env động) cố tình GIỮ qua os.environ.get: secret
# đi qua scp.security.secret_loader, không qua API cấu hình này (HC-08).
from scp.core.settings import get

from scp.llm_gateway.circuit_breaker import (
    _DEAD_MODEL_REASONS,
    _DEAD_MODEL_REGISTRY_LOCK,
    _REASON_MODEL_NOT_FOUND,
    _REASON_PAYMENT_DEAD,
    _REASON_RATE_DEAD,
    _RECORDED_FAILURE_PREFIX,
    CircuitBreaker,
    _clear_rate_limit_streak,
    _is_dead_model,
    _is_model_not_found_response,
    _mark_dead_model,
    _record_rate_limit_hit,
)
from scp.llm_gateway.egress_policy import llm_egress_allowed
from scp.security.provider_keys import ProviderCredentialError, load_openrouter_keys
from scp.security.url_safety import (
    EgressDeniedError,
    enforce_egress_policy as _real_enforce_egress_policy,
)

logger = logging.getLogger("scp.llm_gateway.providers")


def enforce_egress_policy(url: str, **kwargs) -> None:
    """Enforce outbound URL egress policy, honoring client-level monkeypatches."""
    client_mod = sys.modules.get("scp.llm_gateway.client")
    target = (
        getattr(client_mod, "enforce_egress_policy", _real_enforce_egress_policy)
        if client_mod
        else _real_enforce_egress_policy
    )
    if target is enforce_egress_policy:
        target = _real_enforce_egress_policy
    target(url, **kwargs)


_BACKOFF_JITTER_RNG = random.SystemRandom()

# ============================================================
# [OPENROUTER-FREE-FIX] REGISTRY OF ALL 17 FREE MODELS ON OPENROUTER
# ============================================================
OPENROUTER_FREE_MODELS: list[str] = [
    # ---- 2026+ SCP Added Models ----
    "deepseek/deepseek-r1:free",                         # DeepSeek R1 reasoning
    "deepseek/deepseek-chat:free",                       # DeepSeek V3 chat
    "google/gemini-2.5-pro:free",                        # Gemini 2.5 Pro (if free)
    "google/gemini-2.5-flash:free",                      # Gemini 2.5 Flash
    "meta-llama/llama-3.3-70b-instruct:free",            # Llama 3.3 70B
    "meta-llama/llama-3.1-8b-instruct:free",             # Llama 3.1 8B
    "meta-llama/llama-3.2-3b-instruct:free",             # Llama 3.2 3B
    "qwen/qwen-2.5-72b-instruct:free",                   # Qwen 2.5 72B
    "qwen/qwen-2.5-coder-32b-instruct:free",             # Qwen 2.5 Coder
    "mistralai/mistral-nemo:free",                       # Mistral Nemo
    "microsoft/phi-3-mini-128k-instruct:free",           # Phi 3 Mini
    "google/gemma-2-27b-it:free",                        # Gemma 2 27B
    "anthropic/claude-3.5-sonnet:free",                  # Claude 3.5 Sonnet (if rotated to free)

    # ---- Existing SCP Models ----
    "nvidia/nemotron-3-ultra-550b-a55b:free",
    "nvidia/nemotron-3-super-120b-a12b:free",
    "google/gemma-4-31b-it:free",
    "google/gemma-4-26b-a4b-it:free",
    "nvidia/nemotron-3-nano-30b-a3b:free",
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
    "cohere/north-mini-code:free",
    "inclusionai/ling-3.0-tiny:free",
    "poolside/laguna-s-2.1:free",
    "poolside/laguna-xs-2.1:free",
    "openrouter/free",
    "openai/gpt-oss-20b:free",
    "nvidia/nemotron-3.5-content-safety:free",
    "nvidia/nemotron-nano-12b-v2-vl:free",
    "nvidia/nemotron-nano-9b-v2:free",
    "google/lyria-3-pro-preview",
    "google/lyria-3-clip-preview",
]


def _llm_egress_allowed(base_url: str) -> bool:
    """Enforce the SCP LLM egress contract delegating to unified egress policy."""
    return llm_egress_allowed(base_url)


class OpenRouterProvider:
    """OpenAI-compatible base với breaker; ProviderName dùng làm nhãn fallback."""
    PROVIDER_NAME = "openrouter"
    """OpenRouter cloud LLM provider — PAID primary + FREE fallback.

    Architecture:
      1. PRIMARY: PAID model (OPENROUTER_MODEL env) — used for ALL tasks by default.
      2. FALLBACK: FREE models (cost $0) — used when PAID model fails
         (rate limit, quota exhausted, network error).
         Task-specific FREE model selected from TASK_FREE_FALLBACK_MAP.
      3. 3 API KEYS round-robin: OPENROUTER_API_KEY, _2, _3 — rotates per call
         to avoid 20 req/min rate limit per key. Effective: 60 req/min total.

    Fallback chain per call:
      1. Try PAID model with key rotation
      2. If 429/402/quota → try task-specific FREE model
      3. If still fails → try openrouter/free (auto-router, picks any free)
      4. If still fails → return None (caller sees provider "none" and must
         fail closed — no answer is fabricated)
    """

    # Task → FREE fallback model. Used ONLY when PAID model fails.
    # All defaults are FREE (cost $0), verified on 2026-08-06.
    TASK_FREE_FALLBACK_MAP: dict[str, str] = {
        "autofix":       "deepseek/deepseek-r1:free",
        "why":           "deepseek/deepseek-chat:free",
        "learning":      "qwen/qwen-2.5-72b-instruct:free",
        "fast_learning": "meta-llama/llama-3.3-70b-instruct:free",
        "judge":         "nvidia/nemotron-3-super-120b-a12b:free",
        "chat":          "google/gemini-2.5-flash:free",
        "vision":        "google/gemini-2.5-flash:free",
        "coding":        "qwen/qwen-2.5-coder-32b-instruct:free",
        "fact_check":    "deepseek/deepseek-r1:free",
        "default":       "openrouter/free",
    }

    # Class-level: 3 API keys + round-robin iterator (shared across all instances)
    _API_KEYS: list[str] = []
    _key_cycle: itertools.cycle | None = None
    _key_lock = threading.Lock()

    @classmethod
    def _init_keys(cls) -> None:
        """Load API keys from env vars (3 keys supported for round-robin)."""
        if cls._API_KEYS:
            return  # already loaded
        try:
            keys = load_openrouter_keys()
        except ProviderCredentialError as exc:
            logger.error("[LLM Gateway] provider credential configuration rejected: %s", str(exc))
            keys = []
        cls._API_KEYS = keys
        if keys:
            cls._key_cycle = itertools.cycle(keys)

    _dynamic_models_loaded = False

    @classmethod
    def _init_dynamic_models(cls) -> None:
        if cls._dynamic_models_loaded:
            return
        try:
            from scp.llm_gateway.free_catalog import refresh_free_catalog
            refresh_free_catalog()
        except Exception as e:
            logger.warning("[LLM Gateway] free-catalog refresh failed: %s", e, exc_info=True)
        cls._dynamic_models_loaded = True

    @classmethod
    def _next_key(cls) -> str:
        """Get next API key (round-robin). Returns '' if no keys configured."""
        cls._init_keys()
        cls._init_dynamic_models()
        with cls._key_lock:
            if cls._key_cycle is None:
                return ""
            return next(cls._key_cycle)

    def __init__(self, task: str = "default"):
        self.task = task
        # PRIMARY: PAID model (user's OPENROUTER_MODEL) — used FIRST for ALL tasks.
        if task == "judge":
            self.model = get("OPENROUTER_MODEL_JUDGE_PRIMARY", "anthropic/claude-3-5-sonnet")
        else:
            self.model = get("OPENROUTER_MODEL", "deepseek/deepseek-v4-flash-0731")
        # FREE fallback for this task (used if PAID fails with 429/402/quota)
        self.free_fallback = os.environ.get(
            f"OPENROUTER_MODEL_{task.upper()}",
            self.TASK_FREE_FALLBACK_MAP.get(task, self.TASK_FREE_FALLBACK_MAP["default"]),
        )
        self.base_url = get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
        # [C5] Cầu dao theo (provider, model): fail liên tục → fast-fail có
        # thời hạn, không ném request vào endpoint đang sập.
        self._breaker = CircuitBreaker(
            failure_threshold=int(get("SCP_LLM_BREAKER_THRESHOLD", "3")),
            cooldown_seconds=float(get("SCP_LLM_BREAKER_COOLDOWN_SEC", "300")),
        )
        self._client: httpx.AsyncClient | None = None
        # [Fix 4-a-014] Race condition on lazy _client init — two concurrent
        # _call_model() calls could both see _client is None, both create an
        # httpx.AsyncClient, and one would leak. asyncio.Lock + double-checked
        # init closes the race. DNA #9 (no harm — leaked httpx clients).
        self._client_lock = asyncio.Lock()

    @property
    def api_key(self) -> str:
        """Current API key (round-robin across 3 keys)."""
        return self._next_key()

    @property
    def enabled(self) -> bool:
        """True if at least one non-placeholder API key is configured and model is configured."""
        self._init_keys()
        self._init_dynamic_models()
        return len(self._API_KEYS) > 0 and bool(self.model)

    def _key_count(self) -> int:
        """Số key khả dụng — subclass có key-instance override chỗ này."""
        self._init_keys()
        self._init_dynamic_models()
        return len(self._API_KEYS)

    async def _call_model(self, model: str, messages: list[dict], api_key: str) -> tuple[str | None, str | None]:
        """Call a specific model with a specific key. Returns (answer, error).

        [#33 Resilient Transport] Lỗi TRANSIENT (network/5xx/timeout) được
        retry tối đa 2 lần với exponential backoff + jitter trước khi tính
        là failure thật — chống sập vì một gián đoạn mạng vài trăm ms.
        429/402 KHÔNG retry (rate-limit là trạng thái provider — failover
        sang tầng kế tiếp là đúng, chờ thêm chỉ lãng phí)."""
        # [AUDIT-FIX low-7a] Fast-fail khi OPEN; khi cooldown vừa hết, caller
        # này là probe HALF-OPEN DUY NHẤT được đi tiếp (các caller khác nhận
        # False → circuit_open). finally-release bảo đảm probe reservation
        # không kẹt trên các nhánh return không record (dead-model skip,
        # egress_denied, 429/402, last_error rỗng) — record_success/record_
        # failure cũng tự nhả, nên release ở finally là idempotent.
        if not self._breaker.acquire_half_open_probe():
            # [C5] Fast-fail: endpoint đang bị ngắt — không đốt time-out.
            return None, "circuit_open (fast-fail)"
        try:
            return await self._call_model_inner(model, messages, api_key)
        finally:
            self._breaker.release_half_open_probe()

    async def _call_model_inner(
        self, model: str, messages: list[dict], api_key: str
    ) -> tuple[str | None, str | None]:
        if _is_dead_model(self.PROVIDER_NAME, self.base_url, model):
            # [F-05] Dead-candidate breaker: skip ONLY the cached-dead
            # candidate — no HTTP attempt, no key rotation; the chain
            # continues to the next candidate with its normal semantics.
            with _DEAD_MODEL_REGISTRY_LOCK:
                _dead_reason = _DEAD_MODEL_REASONS.get(
                    (self.PROVIDER_NAME, self.base_url, model), _REASON_MODEL_NOT_FOUND
                )
            return None, (
                f"dead_model_skipped: {model} "
                f"({_dead_reason} cached for {self.PROVIDER_NAME})"
            )
        last_error: str | None = None
        for attempt in range(3):
            answer, err = await self._call_model_once(model, messages, api_key)
            if answer is not None:
                self._breaker.record_success()  # thành công thật: reset chuỗi lỗi
                _clear_rate_limit_streak(self.PROVIDER_NAME, self.base_url, model)
                return answer, err
            if err == "egress_denied":
                return None, err
            if err and err.startswith("model_not_found"):
                # [F-05] Permanent per-model failure (strict 404
                # model-not-found classification) — cache it kill-switch-aware
                # and fail over WITHOUT the transient retries; the provider
                # breaker still records the endpoint failure exactly once
                # ([BREAKER-ONCE]) and the error carries the
                # recorded_failure: marker so chat()'s tail never
                # double-counts it.
                _mark_dead_model(self.PROVIDER_NAME, self.base_url, model)
                self._breaker.record_failure()
                return None, f"{_RECORDED_FAILURE_PREFIX}{err}"
            if err and err.startswith("payment_required:"):
                # [F-05-EXT] 402 Payment Required = hard quota: payment state
                # does not change mid-process → cache the candidate dead
                # immediately (same registry + kill-switch as the 404 cache)
                # and fail over without transient retries. Breaker failure is
                # recorded exactly once here ([BREAKER-ONCE]) and the error is
                # returned with the recorded_failure: marker so chat()'s tail
                # does not double-count it.
                _mark_dead_model(self.PROVIDER_NAME, self.base_url, model, reason=_REASON_PAYMENT_DEAD)
                _clear_rate_limit_streak(self.PROVIDER_NAME, self.base_url, model)
                self._breaker.record_failure()
                return None, f"{_RECORDED_FAILURE_PREFIX}{err}"
            if err and err.startswith("rate_limited:"):
                # [F-05-EXT] 429 may be transient: cache the candidate dead
                # only after N consecutive 429s in-process for this candidate
                # (any non-429 outcome resets the streak). At threshold the
                # breaker failure is recorded here ([BREAKER-ONCE]) and the
                # error carries the recorded_failure: marker; below threshold
                # the historical semantics are unchanged: failover now, no
                # cache, no inner record — the error stays UNMARKED so
                # chat()'s tail counts it exactly once.
                if _record_rate_limit_hit(self.PROVIDER_NAME, self.base_url, model):
                    _mark_dead_model(self.PROVIDER_NAME, self.base_url, model, reason=_REASON_RATE_DEAD)
                    self._breaker.record_failure()
                    return None, f"{_RECORDED_FAILURE_PREFIX}{err}"
                return None, err
            if err and ("429" in err or "402" in err):
                return None, err  # quota/rate-limit: failover, không retry tại chỗ
            _clear_rate_limit_streak(self.PROVIDER_NAME, self.base_url, model)
            last_error = err
            transient = (
                err is not None
                and not any(sig in err for sig in ("429", "402", "circuit_open", "egress_denied"))
            )
            if attempt < 2 and transient:
                await asyncio.sleep(min(0.25 * (2 ** attempt) + _BACKOFF_JITTER_RNG.uniform(0, 0.15), 2.0))
                continue
            break
        # Breaker chỉ ghi MỘT lần theo kết quả cuối — 3 retry nhanh trong 1s
        # không được phép mở breaker oan (transient blip != endpoint chết).
        # [BREAKER-ONCE] Error được trả kèm recorded_failure: marker vì
        # failure này ĐÃ được record ngay tại đây.
        if last_error:
            self._breaker.record_failure()
            return None, f"{_RECORDED_FAILURE_PREFIX}{last_error}"
        return None, last_error

    async def _get_client(self) -> httpx.AsyncClient:
        """Double-checked locking accessor for HTTP client singleton."""
        if self._client is None:
            async with self._client_lock:
                if self._client is None:
                    self._client = httpx.AsyncClient(timeout=60.0)
        return self._client

    async def _call_model_once(self, model: str, messages: list[dict], api_key: str) -> tuple[str | None, str | None]:
        client_mod = sys.modules.get("scp.llm_gateway.client")
        egress_fn = getattr(client_mod, "_llm_egress_allowed", _llm_egress_allowed) if client_mod else _llm_egress_allowed
        if not egress_fn(self.base_url):
            logger.warning(
                "[LLM Gateway] outbound blocked by SCP egress policy: provider=%s base_url=%s",
                self.PROVIDER_NAME,
                self.base_url,
            )
            return None, "egress_denied"
        try:
            llm_allowlist = frozenset(
                item.strip().lower().rstrip(".")
                for item in get("SCP_LLM_EGRESS_ALLOWLIST", "").split(",")
                if item.strip()
            )
            enforce_egress_policy(
                f"{self.base_url}/chat/completions", extra_allowed_hosts=llm_allowlist
            )
        except EgressDeniedError:
            return None, "egress_denied"
        try:
            client = await self._get_client()
            resp = await client.post(
                f"{self.base_url}/chat/completions",
                json={"model": model, "messages": messages, "stream": False},
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "HTTP-Referer": "https://scp-vietnam.local",
                    "X-Title": "SCP Gateway",
                },
            )
            if resp.status_code == 402:
                return None, "payment_required: HTTP 402 (quota/rate-limit)"
            if resp.status_code == 429:
                return None, "rate_limited: HTTP 429 (quota/rate-limit)"
            if resp.status_code == 404 and _is_model_not_found_response(resp):
                return None, (
                    f"model_not_found: HTTP 404 for model '{model}' "
                    f"on {self.PROVIDER_NAME}"
                )
            resp.raise_for_status()
            data = resp.json()
            answer = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
            if answer:
                return (answer, None)
            return (None, "empty_completion")
        except Exception as e:
            logger.debug("OpenRouterProvider._call_model_once: exception ignored: %s", e, exc_info=True)
            return None, str(e)

    async def chat(
        self,
        question: str = "",
        context: str = "",
        system_prompt: str = "",
        prioritize_free: bool = False,
        messages: list[dict] | None = None,
    ) -> tuple[str | None, str]:
        if not self.enabled:
            return None, "none"

        primary_model = self.model
        fallback_model = self.free_fallback
        if prioritize_free:
            primary_model, fallback_model = fallback_model, primary_model

        if not self._breaker.is_open():
            if messages is None:
                messages_payload = []
                if system_prompt:
                    messages_payload.append({"role": "system", "content": system_prompt})
                messages_payload.append({"role": "user", "content": f"{context}\n\n{question}".strip()})
            else:
                messages_payload = messages

            err: str | None = None
            for _attempt in range(min(3, self._key_count())):
                key = self._next_key()
                answer, err = await self._call_model(primary_model, messages_payload, key)
                if answer:
                    self._breaker.record_success()
                    return answer, f"{self.PROVIDER_NAME}:{primary_model}"
                if err == "egress_denied":
                    return None, "none"
                if err and "quota" not in err.lower() and "rate-limit" not in err.lower() and "429" not in err and "402" not in err:
                    logger.debug("OpenRouter PAID (%s) failed: %s", primary_model, err)
                    break
                logger.debug("OpenRouter PAID (%s) key failed: %s, trying next key", primary_model, err)

            if (
                err
                and not err.startswith(_RECORDED_FAILURE_PREFIX)
                and any(sig in err.lower() for sig in ("quota", "rate-limit", "429", "402"))
            ):
                self._breaker.record_failure()

            if fallback_model != primary_model:
                key = self._next_key()
                answer, err = await self._call_model(fallback_model, messages_payload, key)
                if answer:
                    return answer, f"{self.PROVIDER_NAME}:{fallback_model}"
                if err == "egress_denied":
                    return None, "none"
                logger.debug("%s FREE fallback (%s) failed: %s", self.PROVIDER_NAME, fallback_model, err)

            if self.PROVIDER_NAME == "openrouter" and fallback_model != "openrouter/free" and primary_model != "openrouter/free":
                key = self._next_key()
                answer, err = await self._call_model("openrouter/free", messages_payload, key)
                if answer:
                    logger.info("OpenRouter auto-router (openrouter/free) succeeded")
                    return answer, f"{self.PROVIDER_NAME}:openrouter/free"
                logger.debug("OpenRouter auto-router failed: %s", err)
        else:
            logger.warning("[%s] Circuit Breaker is OPEN. Bypassing provider to prevent rate-limit ban.", self.PROVIDER_NAME)

        return None, "none"

    def stats(self) -> dict:
        self._init_keys()
        self._init_dynamic_models()
        return {
            "configured": self.enabled,
            "num_keys": len(self._API_KEYS),
            "primary_model": self.model,
            "free_fallback": self.free_fallback,
            "task": self.task,
            "total_free_models_available": len(OPENROUTER_FREE_MODELS),
        }


# ============================================================
# [FAILOVER] Provider dự phòng ngoài OpenRouter.
# Khi 1 API bị rate-limit/quota/sập, gateway tự chuyển sang provider kế
# tiếp trong chuỗi. Các provider OpenAI-compatible khác khai báo qua
# SCP_LLM_FALLBACK_PROVIDERS mà không cần sửa code:
# "name:KEY_ENV:BASEURL_ENV:MODEL_ENV,name2:..."
# ============================================================
_PLACEHOLDER_KEYS = {"", "changeme", "your-key", "your_api_key", "placeholder", "xxx", "sk-xxx", "none"}


class EnvCompatProvider(OpenRouterProvider):
    """Provider OpenAI-compatible khai báo qua env (instance-keyed)."""

    def __init__(self, name: str, task: str, key_env: str, base_url_env: str, model_env: str, default_model: str = "", default_base_url: str = ""):
        self.PROVIDER_NAME = name
        self.task = task
        key = os.environ.get(key_env, "").strip()
        self._instance_keys = [key] if key and key.lower() not in _PLACEHOLDER_KEYS else []
        self._instance_key_cycle = itertools.cycle(self._instance_keys) if self._instance_keys else None
        self.model = os.environ.get(model_env, default_model)
        self.free_fallback = self.model
        self.base_url = os.environ.get(base_url_env, default_base_url).rstrip("/")
        self._breaker = CircuitBreaker(
            failure_threshold=int(get("SCP_LLM_BREAKER_THRESHOLD", "3")),
            cooldown_seconds=float(get("SCP_LLM_BREAKER_COOLDOWN_SEC", "300")),
        )
        self._client: httpx.AsyncClient | None = None
        self._client_lock = asyncio.Lock()

    @classmethod
    def _init_keys(cls) -> None:
        return None  # instance-level keys

    _dynamic_models_loaded = False

    @classmethod
    def _init_dynamic_models(cls) -> None:
        if cls._dynamic_models_loaded:
            return
        try:
            from scp.llm_gateway.free_catalog import refresh_free_catalog
            refresh_free_catalog()
        except Exception as e:
            logger.warning("[LLM Gateway] free-catalog refresh failed: %s", e, exc_info=True)
        cls._dynamic_models_loaded = True

    @property
    def enabled(self) -> bool:
        return bool(self._instance_keys) and bool(self.model) and bool(self.base_url)

    def _key_count(self) -> int:
        return len(self._instance_keys)

    def _next_key(self) -> str:
        return next(self._instance_key_cycle) if self._instance_key_cycle else ""

    def stats(self) -> dict:
        return {
            "configured": self.enabled,
            "num_keys": len(self._instance_keys),
            "primary_model": self.model,
            "base_url": self.base_url,
            "task": self.task,
        }
