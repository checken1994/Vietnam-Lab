"""Redis-backed rate-limit adapter (H3-W1) — bổ sung cho scp/api/rate_limiting.py.

TẠI SAO: ``rate_limiting.py`` dùng slowapi Limiter với storage in-process —
mỗi replica uvicorn có bộ đếm riêng, nên quota "60/phút" thực tế là
"60/phút/replica" khi scale ngang. Adapter này cung cấp bộ đếm dùng chung
qua Redis (fixed window: INCR + EXPIRE per window) cho deployment multi-replica.

Contract (H3-W1):
- ``RedisRateLimiter(namespace)``: LAZY import package ``redis`` — nếu package
  không cài sẵn → fallback sang ``InMemoryRateLimiter`` + log WARNING (không
  crash boot; rate limiting vẫn hoạt động per-process).
- Atomic INCR + EXPIRE per window: ``INCR key`` (nguyên tử phía Redis); khi
  count == 1 (hit đầu window) mới ``EXPIRE key window`` — đúng thuật toán
  fixed-window chuẩn của Redis docs. Key không TTL (crash giữa INCR/EXPIRE)
  được tự vá ở lần hit sau qua kiểm tra TTL.
- Env ``SCP_RATE_LIMIT_BACKEND`` = ``memory`` | ``redis`` (default ``memory``)
  — đọc qua điểm đọc chuẩn HC-03 (``scp.core.settings.get``), đọc LIVE mỗi lần
  gọi factory (không cache import-time).
- Mất kết nối Redis giữa chừng → downgrade 1 lần sang in-memory + WARNING
  (fail-closed về mặt enforcement: bộ đếm memory vẫn chặn, KHÔNG fail-open
  như nhánh lỗi storage của rate_limiting.py).

API: ``allow(key, limit=..., window_seconds=...) -> bool``. Thread-safe.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

# [HC-03] Điểm đọc env chuẩn — get() đọc live + strip, không cache import-time.
from scp.core.settings import get

logger = logging.getLogger("scp.rate_limiting")

__all__ = [
    "InMemoryRateLimiter",
    "RedisRateLimiter",
    "build_rate_limiter",
    "RATE_LIMIT_BACKEND_ENV",
    "REDIS_URL_ENV",
]

RATE_LIMIT_BACKEND_ENV = "SCP_RATE_LIMIT_BACKEND"
REDIS_URL_ENV = "SCP_REDIS_URL"

# Ngưỡng dọn key hết hạn trong fallback in-memory (chống rò rỉ bộ nhớ khi
# keyspace phình vì IP độc quét nhiều token). Dọn khi vượt ngưỡng, O(n) hiếm.
_INMEMORY_PRUNE_THRESHOLD = 10_000


class InMemoryRateLimiter:
    """Fixed-window bộ đếm trong-process (fallback của RedisRateLimiter).

    Đúng ngữ nghĩa INCR+EXPIRE per window: mỗi (key, window_index) có 1 counter,
    hit đầu window reset count = 1, hit sau chỉ cộng. Thread-safe dưới lock.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # key -> (window_index, count)
        self._counters: dict[str, tuple[int, int]] = {}

    def _prune_expired(self, now_index: int) -> None:
        # Gọi khi GIỮ lock. Chỉ dọn window cũ (window hiện tại vẫn cần).
        if len(self._counters) < _INMEMORY_PRUNE_THRESHOLD:
            return
        expired = [k for k, (widx, _) in self._counters.items() if widx < now_index]
        for k in expired:
            self._counters.pop(k, None)

    def allow(self, key: str, *, limit: int, window_seconds: int) -> bool:
        if limit <= 0 or window_seconds <= 0:
            # Config sai → fail-closed về an toàn: không cấp quota vô hạn.
            logger.warning("[RateLimit] config không hợp lệ limit=%s window=%s", limit, window_seconds)
            return False
        now_index = int(time.time()) // window_seconds
        with self._lock:
            self._prune_expired(now_index)
            widx, count = self._counters.get(key, (now_index, 0))
            if widx != now_index:
                # Window mới — tương đương key cũ EXPIRE xong, INCR lại từ 1.
                widx, count = now_index, 0
            count += 1
            self._counters[key] = (widx, count)
            return count <= limit

    def close(self) -> None:  # parity với RedisRateLimiter.close()
        with self._lock:
            self._counters.clear()


class RedisRateLimiter:
    """Fixed-window rate limiter dùng Redis làm storage dùng chung multi-replica.

    - LAZY import ``redis`` ở __init__: package thiếu → fallback memory + WARNING.
    - LAZY connect ở lần allow() đầu (không chặn boot khi Redis chưa lên).
    - Lỗi connection ở allow() → downgrade vĩnh viễn (1 lần) sang memory + WARNING.
    """

    def __init__(
        self,
        namespace: str = "scp",
        *,
        redis_url: str | None = None,
        client: Any = None,
    ) -> None:
        self.namespace = str(namespace).strip().rstrip(":") or "scp"
        self._redis_url = redis_url or str(get(REDIS_URL_ENV, "redis://127.0.0.1:6379/0"))
        self._client = client  # seam test: client giả được bơm thẳng
        self._client_ready = client is not None
        self._lock = threading.Lock()
        self._fallback: InMemoryRateLimiter | None = None
        self._closed = False
        if client is None:
            try:
                import redis  # noqa: F401 — lazy import theo contract H3-W1
            except ImportError as exc:
                logger.warning(
                    "[RateLimit] package 'redis' chưa cài — RedisRateLimiter(namespace=%s) "
                    "fallback sang in-memory: %s",
                    self.namespace,
                    exc,
                )
                self._fallback = InMemoryRateLimiter()

    # ---------------------------------------------------------------- util --
    def _full_key(self, key: str) -> str:
        return f"{self.namespace}:rate:{key}"

    def _get_client(self) -> Any:
        """Trả về Redis client (lazy connect); None nếu đã fallback."""
        if self._fallback is not None:
            return None
        with self._lock:
            if self._client_ready:
                return self._client
            try:
                import redis

                self._client = redis.Redis.from_url(self._redis_url, socket_connect_timeout=1.0)
                self._client_ready = True
                return self._client
            except Exception as exc:  # ImportError hoặc lỗi tạo client
                logger.warning(
                    "[RateLimit] Redis không dùng được (url=%s) — fallback in-memory: %s",
                    self._redis_url,
                    exc,
                )
                self._fallback = InMemoryRateLimiter()
                return None

    def _downgrade_to_memory(self, exc: Exception, full_key: str) -> None:
        with self._lock:
            if self._fallback is None:
                logger.warning(
                    "[RateLimit] Redis error ở key=%s — downgrade vĩnh viễn sang in-memory: %s",
                    full_key,
                    exc,
                )
                self._fallback = InMemoryRateLimiter()

    # ----------------------------------------------------------------- api --
    def allow(self, key: str, *, limit: int, window_seconds: int) -> bool:
        if self._closed:
            logger.warning("[RateLimit] RedisRateLimiter đã close — từ chối hit (fail-closed)")
            return False
        if limit <= 0 or window_seconds <= 0:
            logger.warning("[RateLimit] config không hợp lệ limit=%s window=%s", limit, window_seconds)
            return False
        client = self._get_client()
        if client is None:
            fb = self._fallback
            assert fb is not None  # _get_client chỉ trả None khi fallback sẵn
            return fb.allow(key, limit=limit, window_seconds=window_seconds)

        full_key = self._full_key(key)
        try:
            # Thuật toán fixed-window chuẩn Redis (atomic INCR + EXPIRE per
            # window): INCR nguyên tử; chỉ hit ĐẦU window mới gắn TTL.
            count = int(client.incr(full_key))
            if count == 1:
                client.expire(full_key, int(window_seconds))
            elif int(client.ttl(full_key)) < 0:
                # Key tồn tại mà không TTL (crash giữa INCR và EXPIRE) → vá.
                client.expire(full_key, int(window_seconds))
            return count <= limit
        except Exception as exc:  # redis.RedisError + bất kỳ lỗi transport nào
            self._downgrade_to_memory(exc, full_key)
            fb = self._fallback
            assert fb is not None
            return fb.allow(key, limit=limit, window_seconds=window_seconds)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            if self._client is not None:
                try:
                    self._client.close()
                except Exception as exc:  # pragma: no cover — client đóng 2 lần
                    logger.debug("[RateLimit] Redis client close error: %s", exc)


def build_rate_limiter(namespace: str = "scp") -> InMemoryRateLimiter | RedisRateLimiter:
    """Factory theo env ``SCP_RATE_LIMIT_BACKEND`` (memory|redis, default memory).

    Đọc LIVE mỗi lần gọi (HC-03) — đổi env trong test rồi gọi lại factory là có
    hiệu lực. backend=redis nhưng package thiếu → ``RedisRateLimiter`` TỰ fallback
    memory + WARNING (contract H3-W1), factory không raise.
    """
    backend = str(get(RATE_LIMIT_BACKEND_ENV, "memory") or "memory").strip().lower()
    if backend == "redis":
        return RedisRateLimiter(namespace)
    if backend not in ("memory", ""):
        logger.warning(
            "[RateLimit] SCP_RATE_LIMIT_BACKEND=%s không nhận diện (memory|redis) — dùng memory",
            backend,
        )
    return InMemoryRateLimiter()
