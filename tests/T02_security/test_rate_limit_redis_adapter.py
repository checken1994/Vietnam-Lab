"""[H3-W1] Test Redis rate-limit adapter — scp/api/rate_limit_redis.py.

Phủ:
- Fixed-window INCR + EXPIRE trên Redis giả (mock dict) — chặn đúng limit,
  EXPIRE chỉ gọi ở hit đầu window, tự vá key mất TTL.
- Fallback in-memory khi package redis không có (sys.modules['redis']=None)
  + WARNING (contract H3-W1: không crash, enforcement vẫn chạy).
- Downgrade vĩnh viễn sang memory khi Redis connection error ở allow().
- Factory build_rate_limiter theo SCP_RATE_LIMIT_BACKEND (default memory).
"""
from __future__ import annotations

import logging
import sys
import types

import pytest

from scp.api import rate_limit_redis as rlr


class FakeRedis:
    """Redis giả lập INCR/EXPIRE/TTL bằng dict — đủ ngữ nghĩa cho fixed window."""

    def __init__(self, *, fail_incr: bool = False) -> None:
        self.store: dict[str, int] = {}
        self.ttls: dict[str, int] = {}
        self.expire_calls: list[tuple[str, int]] = []
        self.fail_incr = fail_incr
        self.closed = False

    def incr(self, key: str) -> int:
        if self.fail_incr:
            raise ConnectionError("redis down")
        self.store[key] = self.store.get(key, 0) + 1
        return self.store[key]

    def expire(self, key: str, seconds: int) -> bool:
        self.expire_calls.append((key, seconds))
        self.ttls[key] = seconds
        return True

    def ttl(self, key: str) -> int:
        return self.ttls.get(key, -2)  # -2 = key không tồn tại (Redis semantics)

    def close(self) -> None:
        self.closed = True


# ------------------------------------------------------------------- memory --


def test_memory_limiter_blocks_after_limit():
    limiter = rlr.InMemoryRateLimiter()
    results = [limiter.allow("k1", limit=3, window_seconds=60) for _ in range(5)]
    assert results == [True, True, True, False, False]


def test_memory_limiter_invalid_config_fails_closed():
    limiter = rlr.InMemoryRateLimiter()
    assert limiter.allow("k1", limit=0, window_seconds=60) is False
    assert limiter.allow("k1", limit=10, window_seconds=0) is False


def test_memory_limiter_new_window_resets(monkeypatch):
    limiter = rlr.InMemoryRateLimiter()
    assert limiter.allow("k1", limit=1, window_seconds=60) is True
    assert limiter.allow("k1", limit=1, window_seconds=60) is False
    # Trôi qua window kế tiếp → counter reset (tương đương EXPIRE hết hạn).
    real_time = rlr.time.time
    monkeypatch.setattr(rlr.time, "time", lambda: real_time() + 120.0)
    assert limiter.allow("k1", limit=1, window_seconds=60) is True


# -------------------------------------------------------------------- redis --


def test_redis_limiter_enforces_limit_and_expires_once_per_window():
    fake = FakeRedis()
    limiter = rlr.RedisRateLimiter("scp.test", client=fake)
    results = [limiter.allow("ip:1.2.3.4", limit=3, window_seconds=60) for _ in range(5)]
    assert results == [True, True, True, False, False]
    key = "scp.test:rate:ip:1.2.3.4"
    assert fake.store[key] == 5  # INCR nguyên tử chạy cho MỌI hit
    # EXPIRE chỉ ở hit đầu window (count == 1), không reset TTL mỗi hit.
    assert fake.expire_calls == [(key, 60)]


def test_redis_limiter_repairs_key_without_ttl():
    fake = FakeRedis()
    limiter = rlr.RedisRateLimiter("scp.test", client=fake)
    assert limiter.allow("k", limit=10, window_seconds=30) is True
    assert len(fake.expire_calls) == 1
    # Mô phỏng crash giữa INCR và EXPIRE: TTL biến mất.
    fake.ttls.clear()
    assert limiter.allow("k", limit=10, window_seconds=30) is True
    assert fake.expire_calls[-1] == ("scp.test:rate:k", 30)  # đã tự vá TTL


def test_redis_limiter_downgrades_to_memory_on_connection_error(caplog):
    fake = FakeRedis(fail_incr=True)
    limiter = rlr.RedisRateLimiter("scp.test", client=fake)
    with caplog.at_level(logging.WARNING, logger="scp.rate_limiting"):
        # Fallback memory vẫn ENFORCE (fail-closed), không fail-open.
        results = [limiter.allow("k", limit=2, window_seconds=60) for _ in range(4)]
    assert results == [True, True, False, False]
    assert any("downgrade" in rec.message.lower() for rec in caplog.records)


def test_redis_limiter_closed_rejects_hits():
    limiter = rlr.RedisRateLimiter("scp.test", client=FakeRedis())
    limiter.close()
    assert limiter.allow("k", limit=10, window_seconds=60) is False


# ------------------------------------------------------- fallback / factory --


def test_missing_redis_package_falls_back_to_memory_with_warning(monkeypatch, caplog):
    # sys.modules['redis'] = None → `import redis` raise ImportError.
    monkeypatch.setitem(sys.modules, "redis", None)
    with caplog.at_level(logging.WARNING, logger="scp.rate_limiting"):
        limiter = rlr.RedisRateLimiter("scp.fallback")
        results = [limiter.allow("k", limit=2, window_seconds=60) for _ in range(3)]
    assert results == [True, True, False]  # fallback memory vẫn chặn đúng
    assert any("fallback" in rec.message.lower() for rec in caplog.records)
    assert isinstance(limiter._fallback, rlr.InMemoryRateLimiter)


def test_build_rate_limiter_default_is_memory(monkeypatch):
    monkeypatch.delenv("SCP_RATE_LIMIT_BACKEND", raising=False)
    assert isinstance(rlr.build_rate_limiter(), rlr.InMemoryRateLimiter)


def test_build_rate_limiter_redis_backend_without_package_falls_back(monkeypatch, caplog):
    monkeypatch.setenv("SCP_RATE_LIMIT_BACKEND", "redis")
    monkeypatch.setitem(sys.modules, "redis", None)
    with caplog.at_level(logging.WARNING, logger="scp.rate_limiting"):
        limiter = rlr.build_rate_limiter("scp.factory")
    assert isinstance(limiter, rlr.RedisRateLimiter)  # đúng lớp theo backend...
    assert limiter._fallback is not None  # ...nhưng bên trong đã fallback memory
    assert any("fallback" in rec.message.lower() for rec in caplog.records)


def test_build_rate_limiter_unknown_backend_warns_and_uses_memory(monkeypatch, caplog):
    monkeypatch.setenv("SCP_RATE_LIMIT_BACKEND", "memcached")
    with caplog.at_level(logging.WARNING, logger="scp.rate_limiting"):
        limiter = rlr.build_rate_limiter()
    assert isinstance(limiter, rlr.InMemoryRateLimiter)
    assert any("không nhận diện" in rec.message for rec in caplog.records)


def test_settings_module_is_the_env_source(monkeypatch):
    # HC-03: env đọc qua scp.core.settings.get (live) — chứng minh bằng
    # temporary_override thay vì os.environ trực tiếp.
    from scp.core.settings import temporary_override

    with temporary_override({"SCP_RATE_LIMIT_BACKEND": "redis"}):
        limiter = rlr.build_rate_limiter("scp.probe")
    assert isinstance(limiter, rlr.RedisRateLimiter)
    assert not isinstance(
        rlr.build_rate_limiter(), rlr.RedisRateLimiter
    )  # ngoài override → memory


@pytest.mark.parametrize("ns", ["", "  ", "a:b:"])
def test_namespace_is_sanitized(ns):
    limiter = rlr.RedisRateLimiter(ns, client=FakeRedis())
    assert limiter.namespace  # không rỗng
    assert not limiter.namespace.endswith(":")
