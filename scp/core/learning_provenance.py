"""[L0a 2026-10-31] Provenance có cấu trúc cho mọi bản ghi HỌC + cách ly kho runtime.

TẠI SAO module này tồn tại?
  Reality (đo 31/10, checker khác lineage VERIFIED): kho học SỐNG ``data/`` (bind-mount
  vào container production ``/app/data``) chứa 235/235 bản ghi failure_ledger và 30/30
  lesson kb_evolve sinh ra từ pytest/fixture, 0 bản ghi runtime. Gốc: writer tự tính
  đường dẫn (``PROJECT_ROOT/data``, ``"data"`` theo cwd) và không biết bản ghi đến từ
  tiến trình nào ⇒ SCP "học" từ test của chính nó (Goodhart + tự đầu độc).

Cơ chế (tổng quát, KHÔNG đoán từ nội dung bản ghi, KHÔNG regex đường dẫn):
  1. ``current_provenance()`` xác định nguồn gốc từ NGỮ CẢNH TIẾN TRÌNH:
       - tín hiệu harness: pytest tự đặt ``PYTEST_CURRENT_TEST`` cho mỗi phase test, và
         nạp ``_pytest`` vào ``sys.modules``; tiến trình con của test thừa kế biến này;
       - khai báo tường minh ``SCP_PROVENANCE`` ∈ {runtime, test, probe, fixture}.
     Harness thắng khai báo: một tiến trình pytest KHÔNG thể tự xưng ``runtime``.
     Giá trị khai báo lạ ⇒ ``unknown`` (không phải runtime ⇒ fail-closed).
  2. Kho runtime = ``<repo>/data`` (đích bind-mount) + ``SCP_DATA_DIR`` được cấu hình
     LÚC TIẾN TRÌNH KHỞI ĐỘNG (``_boot_env``). Bản ghi không phải runtime BỊ TỪ CHỐI
     ghi vào đó (``LearningIsolationError``); writer mặc định đưa chúng sang kho cách ly
     riêng (``SCP_ISOLATED_DATA_DIR`` hoặc thư mục temp hệ thống), không bao giờ nằm
     trong gốc runtime.
  3. Reader chỉ trả bản ghi cùng provenance với tiến trình đọc; ``legacy_untrusted``
     (bản ghi trước L0a, không có provenance) không bao giờ được trả cho consumer học.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

RUNTIME = "runtime"
TEST = "test"
PROBE = "probe"
FIXTURE = "fixture"
UNKNOWN = "unknown"
LEGACY_UNTRUSTED = "legacy_untrusted"

DECLARABLE = frozenset({RUNTIME, TEST, PROBE, FIXTURE})
_REPO_ROOT = Path(__file__).resolve().parents[2]
_BOOT_PID = os.getpid()


class LearningIsolationError(PermissionError):
    """Fail-closed: bản ghi học không phải runtime nhắm vào kho runtime."""


def _harness_signal() -> str | None:
    """Tín hiệu do harness đặt (không đọc nội dung bản ghi)."""
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return TEST
    if "_pytest" in sys.modules:
        return TEST
    return None


def current_provenance() -> str:
    harness = _harness_signal()
    declared = os.environ.get("SCP_PROVENANCE", "").strip().lower()
    if declared:
        if declared not in DECLARABLE:
            return UNKNOWN
        if declared == RUNTIME and harness:
            return harness
        return declared
    return harness or RUNTIME


def _boot_env(key: str) -> str:
    """Giá trị biến môi trường LÚC TIẾN TRÌNH KHỞI ĐỘNG (cấu hình triển khai).

    ``nt.environ``/``posix.environ`` là bản chụp lúc start, không bị ``os.environ[...] =``
    (vd ``monkeypatch.setenv``) cập nhật; tiến trình con thừa kế env đã đổi làm boot env
    của nó. Nhờ vậy gốc runtime là thứ được CẤU HÌNH khi khởi chạy, không phải giá trị
    một test tự đặt về tmp_path giữa chừng."""
    try:
        if os.name == "nt":
            import nt

            wanted = key.upper()
            for k, v in nt.environ.items():
                if k.upper() == wanted:
                    return str(v).strip()
            return ""
        import posix

        raw = posix.environ.get(os.fsencode(key))
        return os.fsdecode(raw).strip() if raw is not None else ""
    except Exception:  # noqa: BLE001 - fall back to the live env (stricter: more roots)
        return os.environ.get(key, "").strip()


def runtime_roots() -> tuple[Path, ...]:
    """Gốc kho runtime: ``<repo>/data`` (đích bind-mount) + ``SCP_DATA_DIR`` lúc boot."""
    roots: list[Path] = [(_REPO_ROOT / "data").resolve()]
    boot = _boot_env("SCP_DATA_DIR")
    if boot:
        root = Path(boot).expanduser().resolve()
        if root not in roots:
            roots.append(root)
    return tuple(roots)


def _resolve(path: Path | str) -> Path:
    return Path(path).expanduser().resolve()


def is_runtime_location(path: Path | str) -> bool:
    target = _resolve(path)
    for root in runtime_roots():
        if target == root or root in target.parents:
            return True
    return False


def isolated_data_dir(provenance: str) -> Path:
    configured = os.environ.get("SCP_ISOLATED_DATA_DIR", "").strip()
    if configured:
        target = _resolve(configured) / provenance
    else:
        # Mặc định: một thư mục RIÊNG cho mỗi tiến trình — trạng thái (vd capability bị
        # revoke) của lần chạy test này không rò sang lần chạy sau.
        target = (Path(tempfile.gettempdir()).resolve() / "scp-isolated-learning"
                  / provenance / f"pid-{_BOOT_PID}")
    if is_runtime_location(target):
        raise LearningIsolationError(
            f"isolated learning dir {target} lies inside a runtime data root — refusing"
        )
    return target


def learning_store_path(filename: str, provenance: str | None = None) -> Path:
    """Đường dẫn MẶC ĐỊNH của một kho học cho tiến trình hiện tại."""
    prov = provenance or current_provenance()
    if prov == RUNTIME:
        from scp.core.runtime_paths import runtime_data_dir

        return runtime_data_dir() / filename
    return isolated_data_dir(prov) / filename


def default_data_dir(provenance: str | None = None) -> Path:
    """Thay cho mặc định ``"data"`` theo cwd: runtime -> ``runtime_data_dir()``, khác -> cách ly."""
    prov = provenance or current_provenance()
    if prov == RUNTIME:
        from scp.core.runtime_paths import runtime_data_dir

        return runtime_data_dir()
    return isolated_data_dir(prov)


def default_state_dir(relative: str, provenance: str | None = None) -> Path:
    """Thư mục TRẠNG THÁI SỐNG mặc định ``<repo>/data/<relative>`` (vd ``security``, ``hands``).

    Runtime giữ nguyên đường cũ (không đổi ngữ nghĩa production); tiến trình test/probe/
    fixture nhận thư mục cách ly ⇒ một test không thể khóa/mở quyền Hands production."""
    prov = provenance or current_provenance()
    if prov == RUNTIME:
        return _REPO_ROOT / "data" / relative
    return isolated_data_dir(prov) / relative


def guard_write(path: Path | str, provenance: str | None = None) -> str:
    """Trả provenance được phép ghi vào ``path``; raise nếu vi phạm cách ly."""
    prov = provenance or current_provenance()
    if prov != RUNTIME and is_runtime_location(path):
        raise LearningIsolationError(
            f"provenance={prov} must not write runtime learning store {_resolve(path)}"
        )
    return prov


__all__ = [
    "DECLARABLE", "FIXTURE", "LEGACY_UNTRUSTED", "LearningIsolationError", "PROBE", "RUNTIME",
    "TEST", "UNKNOWN", "current_provenance", "default_data_dir", "default_state_dir", "guard_write",
    "is_runtime_location",
    "isolated_data_dir", "learning_store_path", "runtime_roots",
]
