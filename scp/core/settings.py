"""EffectiveSettings — điểm ĐỌC CHUẨN cho cấu hình env của SCP (HC-03/HC-05).

[HC-03 2026-10-23] config_contract.py chỉ validate lúc boot và không lưu giá
trị; ~437 điểm đọc env rải rác không có chung một chỗ đọc. Module này KHÔNG
phải là settings object pydantic và KHÔNG cache giá trị — nó là điểm đọc
chuẩn (standard read point) trên nền ``os.environ``:

  Nguồn sự thật: process env SAU KHI env_loader nạp env file
  (``scp.security.env_loader.load_selected_env`` — scp/__main__ gọi lúc boot).
  Precedence vì vậy kế thừa contract W4-2 đã pin: SCP_ENV_FILE explicit
  boundary thắng process env (loud), .env fallback chỉ fill key còn thiếu.

  Module này chỉ thêm 3 thứ trên nền đọc đó:
    1. Chuẩn hóa: strip khoảng trắng; helper ``get_bool``/``get_int``.
    2. Redirect legacy → canonical (HC-05) + WARNING đúng 1 lần/legacy key,
       chỉ nêu TÊN key, không bao giờ log giá trị (secrecy invariant).
    3. Thống kê đọc tối giản (``stats()``) để audit điểm đọc nào đang dùng
       chuẩn; không cache giá trị vì env có thể đổi lúc runtime.

Không dùng cho secret: secret đi qua ``scp.security.secret_loader`` (env hoặc
``*_FILE``), không qua API này (HC-08 xử lý riêng).
"""
from __future__ import annotations

import logging
import os
import threading
from contextlib import contextmanager
from typing import Iterator, Mapping

logger = logging.getLogger("scp.core.settings")

# [HC-05 2026-10-23] Cặp canonical/legacy lấy từ BẰNG CHỨNG code, không phỏng
# đoán: ``scp/kernel_storage.py:make_storage`` (dòng 308-361) định nghĩa
# ``SCP_KERNEL_BACKEND`` là switch canonical (cho phép postgres qua
# SCP_KERNEL_PG_DSN, fail-closed) và ``SCP_STORAGE_BACKEND`` là switch legacy
# còn sót với contract sqlite-only (postgres ở đó vẫn raise). ``get()`` trên
# key legacy redirect sang canonical + cảnh báo 1 lần để deployment tưởng
# đang cấu hình đúng mà thực chất đang dùng tên chết.
#
# Lưu ý quan hệ ĐẶC BIỆT KHÁC (không phải rename pair, không đưa vào bảng):
# ``SCP_KERNEL_DB_PATH`` (component path) và ``SCP_DATA_DIR`` (data root) là
# quan hệ.compose — ``runtime_path`` ưu tiên component path, fallback về
# ``runtime_data_dir()/filename``. Cả hai đều canonical, giữ nguyên.
CANONICAL_TO_LEGACY: dict[str, str] = {
    "SCP_KERNEL_BACKEND": "SCP_STORAGE_BACKEND",
}
_LEGACY_TO_CANONICAL = {legacy: canon for canon, legacy in CANONICAL_TO_LEGACY.items()}

_TRUTHY = {"1", "true", "yes", "on"}
_FALSY = {"0", "false", "no", "off", ""}

_MISSING = object()  # sentinel: key KHÔNG tồn tại trong env trước override

_lock = threading.Lock()
# Chỉ cache WARNING đã emit (per legacy key / per key giá trị xấu) — KHÔNG
# cache giá trị env: os.environ có thể đổi lúc runtime, đọc phải luôn live.
_legacy_warned: set[str] = set()
_bad_value_warned: set[str] = set()
_stats: dict[str, int] = {"reads": 0, "legacy_redirects": 0, "defaults_used": 0}


def get(key: str, default=None):
    """Đọc một biến cấu hình từ process env (live, không cache).

    Precedence với key legacy (HC-05 — đối chiếu contract thật của
    ``kernel_storage.make_storage``): khi caller dùng TÊN legacy, giá trị của
    key canonical thắng nếu canonical đã có trong env; nếu canonical vắng mặt
    thì giá trị legacy vẫn được honor (fallback); khi cả hai vắng mặt →
    ``default``. Đọc bằng TÊN canonical thì chỉ đọc canonical (điểm đọc mới
    không bao giờ lọt xuống legacy).

    Giá trị được strip khoảng trắng; ``default`` trả nguyên vẹn khi thiếu.
    """
    key = key.strip()
    with _lock:
        _stats["reads"] += 1
    canonical = _LEGACY_TO_CANONICAL.get(key)
    if canonical is None:
        value = os.environ.get(key)
        if value is None:
            with _lock:
                _stats["defaults_used"] += 1
            return default
        return value.strip()

    with _lock:
        first_time = key not in _legacy_warned
        if first_time:
            _legacy_warned.add(key)
            _stats["legacy_redirects"] += 1
    if first_time:
        # Fail-loud đúng 1 lần/legacy key; nêu TÊN key, KHÔNG log giá trị.
        logger.warning(
            "settings: env key '%s' is a legacy alias — use canonical '%s' "
            "(canonical value wins when both are set; value never logged)",
            key, canonical,
        )
    value = os.environ.get(canonical)
    if value is None:
        value = os.environ.get(key)
    if value is None:
        with _lock:
            _stats["defaults_used"] += 1
        return default
    return value.strip()


def get_bool(key: str, default: bool = False) -> bool:
    """Đọc flag boolean. Truthy: 1/true/yes/on (case-insensitive).

    Giá trị rỗng hoặc không nhận diện được → False + WARNING 1 lần/key
    (fail-safe cho flag dạng dangerous-default-off; không bao giờ log giá trị).
    """
    value = get(key)
    if value is None:
        return default
    lowered = value.lower()
    if lowered in _TRUTHY:
        return True
    if lowered in _FALSY:
        return False
    with _lock:
        first_time = key not in _bad_value_warned
        if first_time:
            _bad_value_warned.add(key)
    if first_time:
        logger.warning(
            "settings: non-boolean value for %s treated as False (value never logged)",
            key,
        )
    return False


def get_int(key: str, default: int) -> int:
    """Đọc số nguyên; không parse được (kể cả rỗng) → ``default`` + WARN 1 lần."""
    value = get(key)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        with _lock:
            first_time = key not in _bad_value_warned
            if first_time:
                _bad_value_warned.add(key)
        if first_time:
            logger.warning(
                "settings: non-integer value for %s treated as default (value never logged)",
                key,
            )
        return default


@contextmanager
def temporary_override(updates: Mapping[str, str]) -> Iterator[None]:
    """Tạm đặt giá trị env trong scope, khôi phục CHÍNH XÁC trong ``finally``.

    [HC-04 2026-10-23] Thay cho pattern save-đầu-hàm/restore-cuối-hàm trải
    dài hàng trăm dòng trong lifespan (exception trước ``yield`` hoặc ném vào
    ``yield`` làm restore bị bỏ qua → env ô nhiễm vĩnh viễn trong process).
    ``finally`` ở đây đảm bảo khôi phục cả khi body raise, kể cả khôi phục
    trạng thái "key chưa từng tồn tại" (pop thay vì set rỗng).

    Trung thực về phạm vi: ``os.environ`` là process-global — override ảnh
    hưởng MỌI thread trong khoảng scope (không có isolation ảo per-thread).
    Dùng cho khoảng ngắn, đơn luồng hoặc khi mọi thread cùng chấp nhận giá
    trị override (đúng use-case boot window của lifespan).
    """
    with _lock:
        saved = {key: os.environ.get(key, _MISSING) for key in updates}
        for key, value in updates.items():
            os.environ[key] = str(value)
    try:
        yield
    finally:
        with _lock:
            for key, previous in saved.items():
                if previous is _MISSING:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = previous


def stats() -> dict[str, int]:
    """Thống kê đọc tối giản (bản sao) — dùng cho audit/health introspection."""
    with _lock:
        return dict(_stats)


__all__ = [
    "CANONICAL_TO_LEGACY",
    "get",
    "get_bool",
    "get_int",
    "temporary_override",
    "stats",
]
