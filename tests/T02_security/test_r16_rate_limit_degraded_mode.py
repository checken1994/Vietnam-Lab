"""[R16 2026-10-28] Rate-limit middleware DEGRADED-MODE — scp/api/rate_limiting.py.

Hành vi cũ được verify REAL bằng probe TRƯỚC patch (R16 probe):
  - storage hit() raise 1 trong 4 loại tuple cũ (KeyError/ValueError/
    RuntimeError/TypeError) → nhánh except set hit_allowed=True → FAIL-OPEN
    cho qua toàn bộ (không còn kiểm đếm).
  - Exception NGOÀI tuple (ConnectionError/OSError) → propagate ra middleware
    → 500 mọi request.
  - scp/api/rate_limit_redis.py (220 dòng) 0 caller.

Contract sau fix (zero-mock: storage stub là OBJECT THẬT bơm thẳng vào seam
``limiter._limiter`` — cùng kiểu seam "client giả được bơm thẳng" của
rate_limit_redis.py; không dùng unittest.mock):
  - MỌI exception từ storage (5 loại cũ + ConnectionError/OSError + exception
    tự định nghĩa) → request vẫn được kiểm đếm bởi degraded counter in-process
    với ceiling cứng = giá trị tier cấu hình hiện hành (burst vượt ceiling →
    429), KHÔNG cho-qua tự do, KHÔNG 500.
  - Downgrade sticky đúng 1 lần theo mẫu _downgrade_to_memory
    (rate_limit_redis.py:154-162): log ERROR (không WARNING), các hit sau
    không còn chạm storage.
  - env SCP_RATE_LIMIT_STORAGE_URI rỗng/không đặt → wiring cũ memory://
    (slowapi storage_uri mặc định), hành vi memory giữ nguyên.
"""
from __future__ import annotations

import logging
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from limits.storage import MemoryStorage
from slowapi import Limiter
from slowapi.util import get_remote_address

from scp.api import rate_limit_redis as rlr
from scp.api import rate_limiting as rl


class ExplodingStrategy:
    """Storage strategy giả chết: mọi hit() raise đúng loại được bơm (seam thật)."""

    def __init__(self, exc_type: type[Exception]) -> None:
        self.exc_type = exc_type
        self.calls = 0

    def hit(self, *args: object, **kwargs: object) -> bool:
        self.calls += 1
        raise self.exc_type("R16 probe: storage chet")


class WeirdStorageError(Exception):
    """Exception NGOÀI tuple cũ hoàn toàn — phải vào degraded-mode, không 500."""


def _make_app() -> FastAPI:
    app = FastAPI()
    app.middleware("http")(rl.rate_limit_middleware)

    @app.get("/t/ping")
    async def ping() -> dict[str, bool]:
        return {"ok": True}

    return app


@pytest.fixture(autouse=True)
def _isolated_rate_limit_state(monkeypatch):
    """Mỗi test: bật rate limiting, public tier 3/phút, storage URI default,
    reset degraded state; teardown trả lại strategy thật cho ``limiter``."""
    monkeypatch.setenv("SCP_RATE_LIMIT_ENABLED", "1")
    monkeypatch.setenv("SCP_RATE_LIMIT_PUBLIC", "3/minute")
    monkeypatch.delenv(rl.STORAGE_URI_ENV, raising=False)
    original_strategy = rl.limiter._limiter
    rl._reset_degraded_mode()
    yield
    rl.limiter._limiter = original_strategy
    rl._reset_degraded_mode()


# ------------------------------------------------- degraded-mode enforcement --


@pytest.mark.parametrize(
    "exc_type",
    [RuntimeError, KeyError, ValueError, TypeError, ConnectionError, OSError, WeirdStorageError],
    ids=lambda t: t.__name__,
)
def test_storage_exception_degrades_with_hard_ceiling(exc_type: type[Exception], caplog) -> None:
    """Mọi loại exception storage → request VẪN bị kiểm đếm: 3 lượt đầu 200,
    vượt ceiling 3/phút → 429. KHÔNG 500, KHÔNG cho-qua tự do (fail-open cũ)."""
    stub = ExplodingStrategy(exc_type)
    rl.limiter._limiter = stub
    client = TestClient(_make_app())

    with caplog.at_level(logging.ERROR, logger="scp.rate_limiting"):
        statuses = [client.get("/t/ping").status_code for _ in range(5)]

    assert statuses == [200, 200, 200, 429, 429]
    assert rl.is_degraded_mode_active() is True
    assert rl.degraded_hit_count() == 5  # mọi hit đều qua degraded counter
    assert stub.calls == 1  # sticky: chỉ request đầu tiên chạm storage (xuống cấp 1 lần)
    degraded_errors = [r for r in caplog.records if "[RateLimit][DEGRADED]" in r.message]
    assert degraded_errors, "phải có log ERROR dấu hiệu degraded-mode"
    assert degraded_errors[0].levelno == logging.ERROR  # ERROR, không WARNING
    assert "downgrade" in degraded_errors[0].message.lower()
    assert exc_type.__name__ in degraded_errors[0].message


def test_degraded_ceiling_follows_tier_config(monkeypatch) -> None:
    """Ceiling cứng = GIÁ TRỊ CẤU HÌNH tier hiện hành (không ∞): hạ config xuống
    2/phút → đúng lượt thứ 3 bị 429."""
    monkeypatch.setenv("SCP_RATE_LIMIT_PUBLIC", "2/minute")
    rl.limiter._limiter = ExplodingStrategy(ConnectionError)
    client = TestClient(_make_app())

    statuses = [client.get("/t/ping").status_code for _ in range(4)]

    assert statuses == [200, 200, 429, 429]


def test_degraded_counter_resets_on_new_window(monkeypatch) -> None:
    """Bộ đếm degraded là fixed-window như storage thật: hết window → đếm lại
    từ 1. Mô phỏng window hết hạn bằng cách ghi đè window-index của CHÍNH key
    middleware vừa dùng (seam cùng kiểu FakeRedis của test adapter H3-W1)."""
    monkeypatch.setenv("SCP_RATE_LIMIT_PUBLIC", "1/minute")
    rl.limiter._limiter = ExplodingStrategy(RuntimeError)
    client = TestClient(_make_app())

    assert client.get("/t/ping").status_code == 200
    assert client.get("/t/ping").status_code == 429

    with rl._degraded_lock:
        counter = rl._degraded_counter
    assert counter is not None
    assert isinstance(counter, rlr.InMemoryRateLimiter)  # tái dùng adapter H3-W1
    (key, (widx, count)), = counter._counters.items()
    assert count == 2  # 2 hit đã được kiểm đếm bởi degraded counter
    counter._counters[key] = (widx - 1, 99)  # window cũ → hết hạn
    assert client.get("/t/ping").status_code == 200  # window mới: đếm lại


def test_degraded_mode_not_triggered_when_storage_healthy() -> None:
    """Storage khỏe → không downgrade, hit đi qua strategy thật của slowapi."""
    client = TestClient(_make_app())

    statuses = [client.get("/t/ping").status_code for _ in range(2)]

    assert statuses == [200, 200]
    assert rl.is_degraded_mode_active() is False
    assert rl.degraded_hit_count() == 0


# -------------------------------------------------------- storage-uri wiring --


def test_storage_uri_env_empty_resolves_to_memory(monkeypatch) -> None:
    """env='' → wiring cũ memory:// (yêu cầu R16 mục 3)."""
    monkeypatch.setenv(rl.STORAGE_URI_ENV, "")
    assert rl._resolve_storage_uri() == "memory://"


def test_storage_uri_env_unset_resolves_to_memory(monkeypatch) -> None:
    monkeypatch.delenv(rl.STORAGE_URI_ENV, raising=False)
    assert rl._resolve_storage_uri() == "memory://"


def test_storage_uri_env_value_passes_through(monkeypatch) -> None:
    monkeypatch.setenv(rl.STORAGE_URI_ENV, "redis://127.0.0.1:6379/0")
    assert rl._resolve_storage_uri() == "redis://127.0.0.1:6379/0"


def test_module_limiter_built_with_memory_storage(monkeypatch) -> None:
    """Limiter dựng ở import-time (env không đặt trong test process) dùng
    memory storage như hành vi cũ — và dựng lại theo cùng công thức với env=''
    cho ra MemoryStorage thật (không thêm dependency)."""
    monkeypatch.delenv(rl.STORAGE_URI_ENV, raising=False)
    assert isinstance(rl.limiter._storage, MemoryStorage)

    monkeypatch.setenv(rl.STORAGE_URI_ENV, "")
    rebuilt = Limiter(key_func=get_remote_address, storage_uri=rl._resolve_storage_uri())
    assert isinstance(rebuilt._storage, MemoryStorage)


def test_memory_behavior_unchanged_end_to_end(monkeypatch) -> None:
    """Hành vi cũ memory nguyên vẹn (không stub, không degraded): bucket auth
    riêng theo x-api-key duy nhất mỗi lần chạy → 3 lượt đầu 200, vượt → 429."""
    monkeypatch.setenv("SCP_RATE_LIMIT_AUTH", "3/minute")
    client = TestClient(_make_app())
    headers = {"x-api-key": f"r16-probe-{uuid.uuid4().hex}"}

    statuses = [client.get("/t/ping", headers=headers).status_code for _ in range(5)]

    assert statuses == [200, 200, 200, 429, 429]
    assert rl.is_degraded_mode_active() is False
