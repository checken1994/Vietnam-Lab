# [H3-W1 · 2026-10-25] DI Composition Root cho API server.
#
# VẤN ĐỀ (tại sao cần container): các object dùng chung (multi-turn tracker,
# image/voice jailbreak detectors) được DỰNG TRÙNG LẶP ở 2 nơi —
# scp/api_server.py:250-252 và scp/api_server_parts/_ask_impl.py:34,41-42 —
# mỗi nơi 1 bộ instance riêng. Cùng một process có thể chạy 2 tracker/detector
# khác nhau tuỳ code-path (/health/detailed đọc bộ api_server, /ask pipeline
# đọc bộ _ask_impl) → stats lệch, state không nhất quán. Composition root gom
# việc dựng object về MỘT nơi (container), mọi module lấy instance TỪ container.
#
# RÀNG BUỘC B4-W1 (docs/architecture/SINGLETON_LIFECYCLE.md): 3 singleton này
# KHÔNG THỂ lazify ở module level — rebind machinery F-18 (_rebind_part_function)
# tạo lại hàm part với __globals__ = api_server.globals(), hàm rebind đọc tên
# bằng LOAD_GLOBAL mà LOAD_GLOBAL KHÔNG đi qua module __getattr__ (PEP 562 chỉ
# chặn attribute access). Vì vậy: container LAZY dựng member ở lần đọc đầu
# (pattern __getattr__ của B4-W1, áp ở TẦNG CONTAINER), còn api_server giữ
# assignment import-time nhưng NGUỒN là container (identity giữ nguyên cho
# LOAD_GLOBAL). build_container() được gọi tường minh ở lifespan startup
# (idempotent — lần build đầu thắng) và container.close() ở shutdown.
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("scp.api")

__all__ = ["AppContainer", "build_container", "get_container", "close_container", "reset_container"]


@dataclass
class AppContainer:
    """Tất cả object dùng chung của API server, dựng 1 lần tại composition root.

    Member nặng được dựng LAZY ở lần đọc đầu (container-level __getattr__ —
    cùng pattern cache-back của B4-W1) và cache ngược thành attribute thường,
    nên các lần đọc sau là attribute hit thuần và ``monkeypatch.setattr`` trên
    container hoạt động bình thường.
    """

    settings: Any = None  # EffectiveSettings / dict cấu hình — pass-through
    kernel: Any = None  # TaskKernel adapter (nếu có) — default None an toàn
    built_at: float = field(default_factory=time.time)
    closed: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)
    _members: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    # ------------------------------------------------------- lazy members --
    # Tên member → factory (import trong factory: giữ import của composition.py
    # rẻ và không tạo cycle import với scp.security / scp.api).
    def __getattr__(self, name: str) -> Any:
        factories = {
            "multi_turn_tracker": ("scp.security.multi_turn_tracker", "MultiTurnTracker"),
            "image_detector": ("scp.security.image_voice_detector", "ImageJailbreakDetector"),
            "voice_detector": ("scp.security.image_voice_detector", "VoiceJailbreakDetector"),
            "rate_limiter": ("scp.api.rate_limit_redis", "build_rate_limiter"),
        }
        if name not in factories:
            raise AttributeError(f"AppContainer has no attribute {name!r}")
        with self._lock:
            if name in self._members:  # double-checked sau khi chờ lock
                return self._members[name]
            module_name, attr = factories[name]
            import importlib

            factory = getattr(importlib.import_module(module_name), attr)
            instance = factory("scp.api") if name == "rate_limiter" else factory()
            self._members[name] = instance
            # Cache ngược thành attribute thường: lần đọc sau không re-enter
            # __getattr__ (pari với cache-back-into-globals của B4-W1).
            object.__setattr__(self, name, instance)
            logger.debug("[H3-W1] AppContainer built lazy member %s", name)
            return instance

    # ------------------------------------------------------------ shutdown --
    def close(self) -> None:
        """Shutdown sạch: đóng resource của member có close() (idempotent).

        Tracker/detector không có close() (process-lifetime, chỉ giữ state bộ
        nhớ) — bỏ qua. rate_limiter (nếu đã dựng) đóng Redis client / clear
        counters. KHÔNG xoá identity member đã cache: module api_server vẫn
        giữ reference cũ tới hết process (giống status quo import-time).
        """
        with self._lock:
            if self.closed:
                return
            self.closed = True
        rate_limiter = self._members.get("rate_limiter")
        if rate_limiter is not None and hasattr(rate_limiter, "close"):
            try:
                rate_limiter.close()
                logger.info("[H3-W1] AppContainer closed (rate_limiter released)")
            except Exception as exc:  # non-fatal — shutdown không được crash
                logger.warning("[H3-W1] rate_limiter close error: %s", exc)


# ------------------------------------------------------- module singleton ---

_container_lock = threading.Lock()
_container: AppContainer | None = None


def build_container(settings: Any = None, kernel: Any = None) -> AppContainer:
    """Tạo container DUY NHẤT của process (idempotent: lần build đầu thắng).

    Gọi tường minh ở lifespan startup (điểm composition root theo contract
    H3-W1). Nếu đã tồn tại container CLOSED → dựng container mới thay thế
    (close-only seam cho test boot/stop lặp lại cùng process).
    """
    global _container
    with _container_lock:
        if _container is not None and not _container.closed:
            return _container
        _container = AppContainer(settings=settings, kernel=kernel)
        logger.info(
            "[H3-W1] AppContainer built (settings=%s, kernel=%s)",
            type(settings).__name__ if settings is not None else None,
            type(kernel).__name__ if kernel is not None else None,
        )
        return _container


def get_container() -> AppContainer:
    """Lấy container hiện tại; chưa build thì build (import-time seam)."""
    return build_container()


def close_container() -> None:
    """Đóng container hiện tại (nếu có) — gọi ở lifespan shutdown."""
    global _container
    with _container_lock:
        if _container is not None:
            _container.close()


def reset_container() -> None:
    """Seam test: bỏ singleton (không close) — lần build sau tạo instance mới."""
    global _container
    with _container_lock:
        _container = None
