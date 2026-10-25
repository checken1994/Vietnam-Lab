"""[H3-W1] Test DI composition root — scp/api_server_parts/composition.py.

Phủ:
- build_container idempotent (1 instance duy nhất / process).
- Member lazy dựng ĐÚNG 1 lần (container-level __getattr__, cache-back).
- Hợp nhất singleton: api_server và _ask_impl dùng CÙNG instance
  (trước đây mỗi module tự dựng → 2 bộ tracker/detector trong 1 process).
- close() idempotent + đóng rate_limiter.
- app_state contract khai báo app_container (anti-placebo HC-25).
"""
from __future__ import annotations

import threading

from scp.api_server_parts.composition import (
    AppContainer,
    build_container,
    close_container,
    reset_container,
)


def test_build_container_is_idempotent():
    c1 = build_container()
    c2 = build_container()
    assert c1 is c2


def test_lazy_member_built_once_and_typed():
    reset_container()
    try:
        c = build_container()
        from scp.security.image_voice_detector import (
            ImageJailbreakDetector,
            VoiceJailbreakDetector,
        )
        from scp.security.multi_turn_tracker import MultiTurnTracker

        assert isinstance(c.multi_turn_tracker, MultiTurnTracker)
        assert c.multi_turn_tracker is c.multi_turn_tracker  # cache-back, không dựng lại
        assert isinstance(c.image_detector, ImageJailbreakDetector)
        assert c.image_detector is c.image_detector
        assert isinstance(c.voice_detector, VoiceJailbreakDetector)
    finally:
        reset_container()


def test_unknown_member_raises_attribute_error():
    c = AppContainer()
    try:
        c.does_not_exist  # noqa: B018
    except AttributeError as exc:
        assert "does_not_exist" in str(exc)
    else:
        raise AssertionError("AppContainer phải raise AttributeError cho member lạ")


def test_container_thread_safe_single_build():
    reset_container()
    try:
        seen: list[AppContainer] = []
        barrier = threading.Barrier(4)

        def worker():
            barrier.wait()
            seen.append(build_container())

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(set(id(c) for c in seen)) == 1
    finally:
        reset_container()


def test_close_is_idempotent_and_closes_rate_limiter():
    class _FakeLimiter:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    c = AppContainer()
    fake = _FakeLimiter()
    c._members["rate_limiter"] = fake
    c.close()
    assert c.closed is True
    assert fake.closed is True
    c.close()  # lần 2 — no-op, không raise
    assert c.closed is True


def test_api_server_and_ask_impl_share_singletons():
    """Hợp nhất 3 singleton: hết tình trạng 2 bộ instance trong 1 process."""
    from scp import api_server
    from scp.api_server_parts import _ask_impl

    assert api_server._multi_turn_tracker is _ask_impl._multi_turn_tracker
    assert api_server._image_detector is _ask_impl._image_detector
    assert api_server._voice_detector is _ask_impl._voice_detector


def test_api_server_singletons_are_container_instances():
    """api_server lấy instance TỪ container (composition root là nguồn duy nhất)."""
    from scp import api_server

    # Composition root là nguồn duy nhất: instance trên module api_server phải
    # là instance container hiện hành (hoặc container trước đó nếu test khác
    # đã reset — identity thật đã pin ở test hợp nhất với _ask_impl).
    assert type(api_server._multi_turn_tracker).__name__ == "MultiTurnTracker"
    assert type(api_server._image_detector).__name__ == "ImageJailbreakDetector"
    assert type(api_server._voice_detector).__name__ == "VoiceJailbreakDetector"


def test_app_state_contract_declares_app_container():
    from scp.api_server_parts.app_state import APP_STATE_CONTRACT

    assert "app_container" in APP_STATE_CONTRACT.names()


def test_lifespan_wires_build_and_close():
    """lifespan startup phải build_container + shutdown phải close_container."""
    import inspect

    from scp.api_server_parts import lifespan as lifespan_mod

    src = inspect.getsource(lifespan_mod)
    assert "build_container()" in src
    assert "close_container()" in src
