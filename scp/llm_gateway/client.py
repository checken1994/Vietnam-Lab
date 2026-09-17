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
import json
import logging
import math
import os
import random
import threading
import time
from typing import AsyncIterator
from urllib.parse import urlparse

import httpx

from scp.llm_gateway.discovery import ModelDiscoveryStore, is_provider_model_eligible

logger = logging.getLogger("scp.llm_gateway")

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
_HEDGE_OFF_VALUES = {"off", "0", "false", "no"}


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
        logger.debug('_is_loopback_host: ValueError ignored', exc_info=True)
        return False


def _llm_egress_allowed(base_url: str) -> bool:
    """Enforce the SCP LLM egress contract before a network client is used.

    deny/offline/disabled allow loopback fixtures only. allowlist requires an
    exact host in SCP_LLM_EGRESS_ALLOWLIST and HTTPS for non-loopback traffic.
    An explicitly unknown mode fails closed; an unset mode preserves legacy
    developer behavior while production_guard remains authoritative in prod.
    """
    try:
        parsed = urlparse(base_url)
    except Exception:
        logger.warning('_llm_egress_allowed: Exception not handled', exc_info=True)
        return False
    host = (parsed.hostname or "").strip().rstrip(".").lower()
    if not host:
        return False
    if _is_loopback_host(host):
        return True

    mode = os.environ.get("SCP_EGRESS_MODE", "").strip().lower()
    if mode in {"deny", "offline", "disabled"}:
        return False
    if mode == "allowlist":
        if parsed.scheme.lower() != "https":
            return False
        allowed = {
            item.strip().rstrip(".").lower()
            for item in os.environ.get("SCP_LLM_EGRESS_ALLOWLIST", "").split(",")
            if item.strip()
        }
        return host in allowed
    if mode in {"allow", "enabled", "on"}:
        return True
    if not mode:
        return True
    return False


# ============================================================
# [OPENROUTER-FREE-FIX] REGISTRY OF ALL 17 FREE MODELS ON OPENROUTER
# ============================================================
# Verified via https://openrouter.ai/api/v1/models on 2026-08-06.
# All have pricing.prompt=0 AND pricing.completion=0 (cost $0).
# Use this list when you need to iterate over ALL free models (e.g. for
# fallback chains, model rotation, or auto-discovery).
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


from scp.security.provider_keys import ProviderCredentialError, load_openrouter_keys
from scp.security.url_safety import EgressDeniedError, enforce_egress_policy  # [EE-G1]


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
        self._lock = threading.Lock()

    def is_open(self) -> bool:
        with self._lock:
            if self._opened_at is None:
                return False
            if time.monotonic() - self._opened_at >= self.cooldown_seconds:
                # HALF-OPEN: cho 1 probe — giảm 1 ngưỡng để probe thất bại
                # đóng lại ngay, thành công thì record_success reset về 0.
                self._consecutive_failures = self.failure_threshold - 1
                self._opened_at = None
                return False
            return True

    def record_success(self) -> None:
        with self._lock:
            self._consecutive_failures = 0
            self._opened_at = None

    def record_failure(self) -> None:
        with self._lock:
            self._consecutive_failures += 1
            if self._consecutive_failures >= self.failure_threshold:
                self._opened_at = time.monotonic()


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
            logger.warning('[LLM Gateway] free-catalog refresh failed: %s', e)
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
            self.model = os.environ.get("OPENROUTER_MODEL_JUDGE_PRIMARY", "anthropic/claude-3-5-sonnet")
        else:
            self.model = os.environ.get("OPENROUTER_MODEL", "deepseek/deepseek-v4-flash-0731")
        # FREE fallback for this task (used if PAID fails with 429/402/quota)
        self.free_fallback = os.environ.get(
            f"OPENROUTER_MODEL_{task.upper()}",
            self.TASK_FREE_FALLBACK_MAP.get(task, self.TASK_FREE_FALLBACK_MAP["default"]),
        )
        self.base_url = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
        # [C5] Cầu dao theo (provider, model): fail liên tục → fast-fail có
        # thời hạn, không ném request vào endpoint đang sập.
        self._breaker = CircuitBreaker(
            failure_threshold=int(os.environ.get("SCP_LLM_BREAKER_THRESHOLD", "3")),
            cooldown_seconds=float(os.environ.get("SCP_LLM_BREAKER_COOLDOWN_SEC", "300")),
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
        """True if at least one non-placeholder API key is configured."""
        self._init_keys()
        self._init_dynamic_models()
        return len(self._API_KEYS) > 0

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
        if self._breaker.is_open():
            # [C5] Fast-fail: endpoint đang bị ngắt — không đốt time-out.
            return None, "circuit_open (fast-fail)"
        last_error: str | None = None
        for attempt in range(3):
            answer, err = await self._call_model_once(model, messages, api_key)
            if answer is not None:
                self._breaker.record_success()  # thành công thật: reset chuỗi lỗi
                return answer, err
            if err == "egress_denied":
                return None, err
            if err and ("429" in err or "402" in err):
                return None, err  # quota/rate-limit: failover, không retry tại chỗ
            last_error = err
            transient = (
                err is not None
                and not any(sig in err for sig in ("429", "402", "circuit_open", "egress_denied"))
            )
            if attempt < 2 and transient:
                await asyncio.sleep(min(0.25 * (2 ** attempt) + random.uniform(0, 0.15), 2.0))
                continue
            break
        # Breaker chỉ ghi MỘT lần theo kết quả cuối — 3 retry nhanh trong 1s
        # không được phép mở breaker oan (transient blip != endpoint chết).
        if last_error:
            self._breaker.record_failure()
        return None, last_error

    async def _call_model_once(self, model: str, messages: list[dict], api_key: str) -> tuple[str | None, str | None]:
        if not _llm_egress_allowed(self.base_url):
            logger.warning(
                "[LLM Gateway] outbound blocked by SCP egress policy: provider=%s base_url=%s",
                self.PROVIDER_NAME,
                self.base_url,
            )
            return None, "egress_denied"
        # [EE-G1] Generic egress gate (idempotent, SCP_EGRESS_MODE-aware) on
        # the real request URL. Denial maps to the SAME "egress_denied"
        # sentinel as the LLM check above — _call_model returns it without
        # retry and without recording a breaker failure (fail-closed, no I/O).
        # The LLM operator allowlist (SCP_LLM_EGRESS_ALLOWLIST) is passed as
        # extra_allowed_hosts so the generic allowlist branch and the LLM
        # policy above agree on this transport instead of contradicting each
        # other; deny modes still fail closed for every non-loopback host.
        try:
            llm_allowlist = frozenset(
                item.strip().lower().rstrip(".")
                for item in os.environ.get("SCP_LLM_EGRESS_ALLOWLIST", "").split(",")
                if item.strip()
            )
            enforce_egress_policy(
                f"{self.base_url}/chat/completions", extra_allowed_hosts=llm_allowlist
            )
        except EgressDeniedError:
            return None, "egress_denied"
        try:
            # [Fix 4-a-014] Double-checked locking — only the first concurrent
            # caller creates _client; subsequent callers see it set + skip
            # the lock entirely (no contention on the hot path).
            if self._client is None:
                async with self._client_lock:
                    if self._client is None:
                        self._client = httpx.AsyncClient(timeout=60.0)
            resp = await self._client.post(
                f"{self.base_url}/chat/completions",
                json={"model": model, "messages": messages, "stream": False},
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "HTTP-Referer": "https://scp-vietnam.local",
                    "X-Title": "SCP Gateway",
                },
            )
            # 429 = rate limit, 402 = payment required (quota exhausted)
            if resp.status_code in (429, 402):
                return None, f"HTTP {resp.status_code} (quota/rate-limit)"
            resp.raise_for_status()
            data = resp.json()
            answer = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
            if answer:
                return (answer, None)
            return (None, "empty_completion")
        except Exception as e:
            return None, str(e)

    async def _call_model_stream(self, model: str, messages: list[dict], api_key: str) -> AsyncIterator[str]:
        if not _llm_egress_allowed(self.base_url):
            logger.warning(
                "[LLM Gateway] outbound blocked by SCP egress policy: provider=%s base_url=%s",
                self.PROVIDER_NAME,
                self.base_url,
            )
            return
        try:
            llm_allowlist = frozenset(
                item.strip().lower().rstrip(".")
                for item in os.environ.get("SCP_LLM_EGRESS_ALLOWLIST", "").split(",")
                if item.strip()
            )
            enforce_egress_policy(
                f"{self.base_url}/chat/completions", extra_allowed_hosts=llm_allowlist
            )
        except EgressDeniedError:
            return

        try:
            if self._client is None:
                async with self._client_lock:
                    if self._client is None:
                        self._client = httpx.AsyncClient(timeout=60.0)

            async with self._client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                json={"model": model, "messages": messages, "stream": True},
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "HTTP-Referer": "https://scp-vietnam.local",
                    "X-Title": "SCP Gateway",
                },
            ) as resp:
                if resp.status_code in (429, 402):
                    self._breaker.record_failure()
                    return
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    line = line.strip()
                    if not line or not line.startswith("data:"):
                        continue
                    data_str = line[5:].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_str)
                        choices = chunk.get("choices", [])
                        if choices:
                            delta = choices[0].get("delta", {})
                            content = delta.get("content")
                            if content:
                                yield content
                    except Exception:
                        continue
        except Exception as e:
            logger.debug(f"{self.PROVIDER_NAME} _call_model_stream error: {e}")
            return


    async def chat(self, question: str, context: str = "", system_prompt: str = "", prioritize_free: bool = False) -> tuple[str | None, str]:
        if not self.enabled:
            return None, "none"
            
        primary_model = self.model
        fallback_model = self.free_fallback
        if prioritize_free:
            primary_model, fallback_model = fallback_model, primary_model

        if not self._breaker.is_open():
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": f"{context}\n\n{question}".strip()})

            err: str | None = None
            for _attempt in range(min(3, self._key_count())):
                key = self._next_key()
                answer, err = await self._call_model(primary_model, messages, key)
                if answer:
                    self._breaker.record_success()
                    return answer, f"{self.PROVIDER_NAME}:{primary_model}"
                if err == "egress_denied":
                    return None, "none"
                if err and "quota" not in err.lower() and "rate-limit" not in err.lower() and "429" not in err and "402" not in err:
                    logger.debug(f"OpenRouter PAID ({primary_model}) failed: {err}")
                    break
                logger.debug(f"OpenRouter PAID ({primary_model}) key failed: {err}, trying next key")

            # _call_model already records final transient failures. Quota/rate
            # failures return before that point, so count those exactly once here.
            if err and any(sig in err.lower() for sig in ("quota", "rate-limit", "429", "402")):
                self._breaker.record_failure()

            if fallback_model != primary_model:
                key = self._next_key()
                answer, err = await self._call_model(fallback_model, messages, key)
                if answer:
                    return answer, f"{self.PROVIDER_NAME}:{fallback_model}"
                if err == "egress_denied":
                    return None, "none"
                logger.debug(f"{self.PROVIDER_NAME} FREE fallback ({fallback_model}) failed: {err}")

            if self.PROVIDER_NAME == "openrouter" and fallback_model != "openrouter/free" and primary_model != "openrouter/free":
                key = self._next_key()
                answer, err = await self._call_model("openrouter/free", messages, key)
                if answer:
                    logger.info("OpenRouter auto-router (openrouter/free) succeeded")
                    return answer, f"{self.PROVIDER_NAME}:openrouter/free"
                logger.debug(f"OpenRouter auto-router failed: {err}")
        else:
            logger.warning(f"[{self.PROVIDER_NAME}] Circuit Breaker is OPEN. Bypassing provider to prevent rate-limit ban.")

        return None, "none"

    async def chat_stream(
        self,
        question: str,
        context: str = "",
        system_prompt: str = "",
        prioritize_free: bool = False,
    ) -> AsyncIterator[str]:
        if not self.enabled:
            return

        primary_model = self.model
        fallback_model = self.free_fallback
        if prioritize_free:
            primary_model, fallback_model = fallback_model, primary_model

        if self._breaker.is_open():
            logger.warning(f"[{self.PROVIDER_NAME}] Circuit Breaker is OPEN. Bypassing stream.")
            return

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": f"{context}\n\n{question}".strip()})

        tokens_yielded = False
        for _attempt in range(min(3, self._key_count())):
            key = self._next_key()
            try:
                async for token in self._call_model_stream(primary_model, messages, key):
                    tokens_yielded = True
                    yield token
                if tokens_yielded:
                    self._breaker.record_success()
                    return
            except Exception as e:
                logger.debug(f"{self.PROVIDER_NAME} streaming attempt failed: {e}")
                if tokens_yielded:
                    return

        if fallback_model != primary_model:
            key = self._next_key()
            try:
                async for token in self._call_model_stream(fallback_model, messages, key):
                    tokens_yielded = True
                    yield token
                if tokens_yielded:
                    self._breaker.record_success()
                    return
            except Exception as e:
                logger.debug(f"{self.PROVIDER_NAME} fallback streaming failed: {e}")
                if tokens_yielded:
                    return

        if self.PROVIDER_NAME == "openrouter" and fallback_model != "openrouter/free" and primary_model != "openrouter/free":
            key = self._next_key()
            try:
                async for token in self._call_model_stream("openrouter/free", messages, key):
                    tokens_yielded = True
                    yield token
                if tokens_yielded:
                    self._breaker.record_success()
                    return
            except Exception as e:
                logger.debug(f"{self.PROVIDER_NAME} auto-router stream failed: {e}")

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
            failure_threshold=int(os.environ.get("SCP_LLM_BREAKER_THRESHOLD", "3")),
            cooldown_seconds=float(os.environ.get("SCP_LLM_BREAKER_COOLDOWN_SEC", "300")),
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
            logger.warning('[LLM Gateway] free-catalog refresh failed: %s', e)
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

    def __init__(self, discovery_store: ModelDiscoveryStore | None = None):
        tasks = ("autofix", "why", "learning", "fast_learning", "judge", "chat")
        self._discovery_endpoints = self._configured_discovery_endpoints()
        self._discovery_store_owned = False
        self.discovery_store = discovery_store
        if self.discovery_store is None and self._discovery_endpoints:
            # The lifecycle scheduler and gateway share this durable SQLite
            # authority.  A configured local endpoint is denied until the
            # scheduler has written an ACTIVE row; store-open failure therefore
            # remains fail-closed at _provider_eligible.
            try:
                from pathlib import Path

                data_dir = Path(os.environ.get("SCP_DATA_DIR", "data"))
                self.discovery_store = ModelDiscoveryStore(
                    data_dir / "model_discovery.sqlite3"
                )
                self._discovery_store_owned = True
            except Exception as exc:
                logger.warning(
                    "[S35] discovery authority unavailable; local providers remain blocked: %s",
                    type(exc).__name__,
                )
        # Tier 1 — OpenRouter (primary, per-task FREE fallback map).
        for task in tasks:
            provider = OpenRouterProvider(task=task)
            setattr(self, f"openrouter_{task}", provider)
        self.openrouter_default = OpenRouterProvider(task="default")
        # Backward-compat aliases — old code used `gateway.openrouter`.
        self.openrouter = self.openrouter_default
        self.openrouter_fast_learning = getattr(self, "openrouter_fast_learning", None) or self.openrouter_fast
        # Tier 3 — provider OpenAI-compatible khai báo qua env (không sửa code).
        self._extra_providers: dict[str, list[EnvCompatProvider]] = {t: [] for t in tasks + ("default",)}
        self._parse_extra_providers()
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

    @staticmethod
    def _configured_discovery_endpoints() -> list[str]:
        raw = os.environ.get("SCP_LOCAL_ENDPOINTS", "")
        return [item.strip() for item in raw.split(",") if item.strip()]

    def _provider_eligible(self, provider: Any) -> bool:
        """Apply S35 ACTIVE gating without blocking unknown cloud providers."""
        return is_provider_model_eligible(
            self.discovery_store,
            getattr(provider, "base_url", ""),
            getattr(provider, "model", ""),
            configured_local_endpoints=self._discovery_endpoints,
        )

    def close(self) -> None:
        """Close a gateway-owned discovery authority without touching callers'."""
        if self._discovery_store_owned and self.discovery_store is not None:
            self.discovery_store.close()
            self.discovery_store = None
            self._discovery_store_owned = False

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
            "autofix":       getattr(self, "openrouter_autofix"),
            "why":           getattr(self, "openrouter_why"),
            "learning":      getattr(self, "openrouter_learning"),
            "fast_learning": getattr(self, "openrouter_fast_learning"),
            "judge":         getattr(self, "openrouter_judge"),
            "chat":          getattr(self, "openrouter_chat"),
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
        self._stats["total_calls"] += 1
        prioritize_free = False
        if os.environ.get("SCP_BUDGET_ROUTING", "0") == "1":
            from scp.core.budget_engine import order_tiers
            tiers = order_tiers(question + context, task)
            if tiers[0] == "free":
                prioritize_free = True

        chain = self._provider_chain(task)
        # S35: a discovered local model is routable only while its durable
        # lifecycle state is ACTIVE. Unknown configured cloud providers remain
        # eligible because they are outside the local discovery registry.
        # [KHÔNG ƯU TIÊN MODEL] Pool brand-neutral: provider khỏe xoay vòng theo
        # lượt gọi (chia tải đều, không đặt clip nào lên trên vĩnh viễn);
        # provider breaker OPEN bị đẩy xuống cuối (chỉ dùng khi hết người khỏe).
        enabled = [p for p in chain if p.enabled and self._provider_eligible(p)]
        healthy = [p for p in enabled if not p._breaker.is_open()]
        degraded = [p for p in enabled if p._breaker.is_open()]
        pool = healthy + degraded
        if not pool:
            self._stats["failures"] += 1
            return None, "none"
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

    async def chat_stream(
        self,
        question: str,
        context: str = "",
        system_prompt: str = "",
        task: str = "default",
    ) -> AsyncIterator[str]:
        """Stream LLM response tokens — multi-provider failover."""
        self._stats["total_calls"] += 1
        prioritize_free = False
        if os.environ.get("SCP_BUDGET_ROUTING", "0") == "1":
            try:
                from scp.core.budget_engine import order_tiers
                tiers = order_tiers(question + context, task)
                if tiers[0] == "free":
                    prioritize_free = True
            except Exception as exc:
                logger.debug(f"Budget routing order_tiers failed: {exc}")

        chain = self._provider_chain(task)
        # S35 boundary: discovered local models must be ACTIVE; providers not
        # represented by the local registry retain current cloud behavior.
        enabled = [p for p in chain if p.enabled and self._provider_eligible(p)]
        healthy = [p for p in enabled if not p._breaker.is_open()]
        degraded = [p for p in enabled if p._breaker.is_open()]
        pool = healthy + degraded
        if not pool:
            self._stats["failures"] += 1
            return

        start = self._rr_counter % len(pool)
        self._rr_counter += 1
        rotation = pool[start:] + pool[:start]

        attempted = 0
        for provider in rotation:
            attempted += 1
            self._stats[f"{provider.PROVIDER_NAME}_calls"] = (
                self._stats.get(f"{provider.PROVIDER_NAME}_calls", 0) + 1
            )
            tokens_yielded = False
            try:
                async for token in provider.chat_stream(
                    question,
                    context,
                    system_prompt,
                    prioritize_free=prioritize_free,
                ):
                    tokens_yielded = True
                    yield token
                if tokens_yielded:
                    if attempted > 1:
                        self._stats["failover_count"] += 1
                    return
            except Exception as e:
                logger.debug(f"{provider.PROVIDER_NAME} chat_stream failed: {e}")
                if tokens_yielded:
                    return

        self._stats["failures"] += 1

    async def _chat_sequential(
        self,
        rotation: list,
        question: str,
        context: str,
        system_prompt: str,
        prioritize_free: bool,
    ) -> tuple[str | None, str]:
        """Failover TUẦN TỰ gốc (SCP_LLM_HEDGE=off hoặc chain 1 provider)."""
        attempted = 0
        for provider in rotation:
            attempted += 1
            self._stats[f"{provider.PROVIDER_NAME}_calls"] = self._stats.get(f"{provider.PROVIDER_NAME}_calls", 0) + 1
            answer, _provider_label = await provider.chat(
                question,
                context,
                system_prompt,
                prioritize_free=prioritize_free,
            )
            if answer:
                if attempted > 1:
                    self._stats["failover_count"] += 1
                return answer, _provider_label
            # provider trả None (quota/rate-limit/breaker) → sang provider kế

        self._stats["failures"] += 1
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
        """[S21] Hedged race — first-result-wins đa provider.

        Provider đầu tiên chạy NGAY; quá ``attempt_deadline`` mà chưa có kết
        quả thì provider KẾ TIẾP theo chain sẵn có được bắn vào race SONG
        SONG (task đang chạy KHÔNG bị hủy — "ai về trước thắng"). Answer
        thành công đầu tiên của bất kỳ provider nào được trả về ngay; các
        task còn lại chỉ bị hủy ở bước DỌN DẸP sau khi winner đã chắc chắn
        (hủy sau khi đã có winner là an toàn, không double-spend vô nghĩa).
        Provider LỖI (không chậm) vẫn failover NGAY sang kế tiếp như tuần tự.
        Mỗi provider chỉ 1 hedge attempt — retry logic cũ giữ nguyên trong
        provider.chat(). Quá ``hedge_cap``: fail-closed (None + error rõ),
        không chờ vô hạn. Mọi attempt vẫn đi qua breaker + egress guard của
        provider (không đổi).
        """
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
            self._stats[f"{provider.PROVIDER_NAME}_calls"] = (
                self._stats.get(f"{provider.PROVIDER_NAME}_calls", 0) + 1
            )
            task = loop.create_task(
                provider.chat(question, context, system_prompt, prioritize_free=prioritize_free)
            )
            pending.add(task)
            idx_by_task[task] = task_idx
            expiry[task] = time.monotonic() + attempt_deadline
            if reason == "hedge":
                self._stats["hedge_fires"] += 1
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
                self._stats["failover_count"] += 1
            self._stats["hedge_wins"] += 1
            logger.info(
                "[S21 HEDGE] race won by %s sau %.2fs (first_provider_won=%s, hedge_fires=%d)",
                label, time.monotonic() - start, idx == 0, self._stats["hedge_fires"],
            )
            return answer, label

        self._stats["failures"] += 1
        if cap_hit:
            self._stats["hedge_caps"] += 1
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
        """Sync wrapper for background threads. Runs async chat safely.

        [RUNTIME-FIX-2] Root cause (runtime log line 732, 941):
          RuntimeWarning: coroutine 'LLMGateway.chat' was never awaited
        Bug: `loop.run_until_complete(self.chat(...))` evaluates self.chat(...)
        FIRST, creating a coroutine object. If run_until_complete raises (e.g.
        'event loop already running' inside async thread, or RuntimeError on
        loop creation), the coroutine object is never awaited and never closed
        -> leaks memory + GC pressure. In production 24/7 this means thousands
        of leaked coroutines per day.

        Fix: hold the coroutine reference explicitly. On ANY exception path,
        call coro.close() to release it cleanly. Use asyncio.run() (which
        handles loop creation+close properly) instead of manual loop management.
        Detect 'event loop already running' and fall back to ThreadPoolExecutor
        so we don't nest event loops.
        """
        coro = self.chat(question, context, system_prompt, task=task)
        try:
            # Detect if we're already inside an async context (event loop running).
            # In that case, asyncio.run() would raise RuntimeError. Fall back to
            # running in a fresh thread with its own event loop.
            try:
                asyncio.get_running_loop()
                _in_async = True
            except RuntimeError:
                logger.debug('LLMGateway.chat_sync: RuntimeError ignored', exc_info=True)
                _in_async = False

            if _in_async:
                # We're inside async code (e.g. called from async def without await).
                # Run the coroutine in a separate thread to avoid 'loop already running'.
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(asyncio.run, coro)
                    return future.result(timeout=SYNC_CALL_TIMEOUT_SECONDS)
            else:
                # Normal sync context — asyncio.run handles loop lifecycle properly.
                return asyncio.run(coro)
        except Exception as e:
            # CRITICAL: ensure coroutine is closed to prevent leak.
            # RuntimeWarning 'coroutine was never awaited' happens when coro
            # is created but never awaited AND never closed.
            # [SCP-DNA-FIX R5-5] TẠI SAO: Python 3 semantic — `except Exception
            # as e:` DELETES `e` on exit of the except block (PEP 3110). The
            # inner `try/except` below calls `coro.close()`, and if coro.close()
            # raises, the inner except's exit deletes its OWN `e` — but the
            # outer `e` is ALSO already gone by the time we reach the
            # `logger.warning(f"chat_sync failed: {e}")` line below, raising
            # NameError. Breaks the chat_sync fail-open contract (returns
            # (None, "none") on error). mypy [misc] "Trying to read deleted
            # variable e" caught it. Fix: snapshot `e` into a local `_err`
            # BEFORE the inner try so it survives the inner-except deletion.
            _err = e  # save before inner try deletes e
            try:
                coro.close()
            except Exception as e:
                logger.exception("[client.py:608] silenced exception")
            logger.warning(f"chat_sync failed: {_err}")  # upgrade debug->warning for observability
            self._stats["failures"] += 1
            return None, "none"

    def stats(self) -> dict:
        extras: dict[str, dict] = {}
        for providers in self._extra_providers.values():
            for provider in providers:
                if provider.PROVIDER_NAME not in extras:
                    extras[provider.PROVIDER_NAME] = provider.stats()
        return {
            **self._stats,
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
# FRESH-2 finding: bare `if _gateway is None: _gateway = LLMGateway()` had no
# lock. Two concurrent first-callers (e.g. /ask hit while a learning thread
# boots) would both pass the None check and create duplicate LLMGateway
# instances — leaking httpx.AsyncClient connection pools and breaking the
# shared-stats invariant. Fix: double-checked locking with threading.Lock.
# ============================================================

_gateway: LLMGateway | None = None
_gateway_lock = threading.Lock()


def get_gateway() -> LLMGateway:
    """Get or create the singleton LLM gateway (thread-safe)."""
    global _gateway
    if _gateway is None:
        with _gateway_lock:
            # Re-check inside lock — another thread may have created it while
            # we were waiting.
            if _gateway is None:
                _gateway = LLMGateway()
                try:
                    from scp.llm_gateway.free_catalog import start_background_refresh
                    start_background_refresh()
                except Exception:
                    logger.warning('get_gateway: Exception not handled', exc_info=True)
    return _gateway


def chat_sync(
    question: str,
    context: str = "",
    system_prompt: str = "",
    task: str = "default",
) -> tuple[str | None, str]:
    """Module-level sync shortcut. Uses singleton gateway."""
    return get_gateway().chat_sync(question, context, system_prompt, task=task)
