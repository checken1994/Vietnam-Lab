"""[HC-22 + HC-13] Unified kill-switch authority — ONE reader, THREE channels.

TRƯỚC khi hợp nhất, SCP có 3 kênh kill switch rời rạc, mỗi kênh được đọc bởi
một call-site khác nhau với ngữ nghĩa riêng:

  (a) File flag ``data/pc_controller/KILL_SWITCH`` — đọc qua
      ``scp.pc_control.pc_controller.default_kill_switch_path()``, gate tại
      ``scp.api_server._pc_kill_switch_engaged`` (fail-closed khi flag không
      đọc được). Middleware ``scp.middleware.runtime_skill_guard`` import NGƯỢC
      từ ``scp.api_server`` (HC-13: dependency middleware → api_server, vòng
      ngược vì api_server import middleware/router ở module level).
  (b) SQLite TaskKernel ``control.global_kill/global_kill_epoch`` — engage qua
      ``taskkernel.set_global_kill(True)`` (bridge trong
      ``pc_controller_routes._engage_kernel_global_kill``), enforce tại
      ``taskkernel._assert_not_killed`` (claim/lease bị KillSwitchActive).
  (c) Env ``SCP_KILL_SWITCH=1`` — chỉ đọc tại 1 chỗ
      (``scp/api/routes/stream_routes.py``), fail-closed theo kiểu riêng.

Vấn đề của trạng thái cũ: một kênh engaged không nhất thiết khiến các lane
khác dừng (env chỉ chặn stream, kernel chỉ chặn claim), và middleware phải
import ngược từ api_server để hỏi trạng thái kênh (a). Module này hợp nhất
việc ĐỌC (admission) — trả True nếu BẤT KỲ kênh nào engaged — để mọi lane
(/ask, /chat, openai-compat, stream, middleware) dùng MỘT authority.

Ràng buộc thiết kế:
  * KHÔNG tự mở kernel connection mới: kênh (b) chỉ được probe khi caller
    truyền vào một kernel instance ĐANG CÓ (dùng connection của kernel đó).
    ``kernel=None`` → bỏ qua kênh DB một cách TƯỜNG MINH (documented skip),
    không bao giờ khởi tạo TaskKernel hay sqlite3.connect tại đây.
  * KHÔNG ghi file nào khi chỉ đọc (pure read module).
  * Fail-closed: mọi lỗi ĐỌC trạng thái (OSError/sqlite/...) được tính là
    ENGAGED (kênh tương ứng + hậu tố ``_unreadable``) — cùng hướng với hành vi
    F-RUN-01 hiện có của ``api_server._pc_kill_switch_engaged``.
  * Không import ``scp.api_server`` ở bất kỳ đâu trong module này (hết vòng
    ngược HC-13); ``default_kill_switch_path`` được import LAZY bên trong
    accessor để tránh chu kỳ import package (pc_controller import
    ``scp.security.capability_epoch``).
"""
from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger("scp.security.kill_switch")

# --- Tên kênh (ổn định cho audit log — không đổi vội) -----------------------
SOURCE_FLAG_FILE = "pc_flag_file"
SOURCE_FLAG_FILE_UNREADABLE = "pc_flag_file_unreadable"
SOURCE_ENV = "env_scp_kill_switch"
SOURCE_KERNEL = "kernel_global_kill"
SOURCE_KERNEL_UNREADABLE = "kernel_global_kill_unreadable"

ENV_KILL_SWITCH = "SCP_KILL_SWITCH"


def _flag_path() -> Any:
    """Path của kênh file flag — accessor tường minh (drift-guard seam).

    Dùng lại ``default_kill_switch_path`` (cùng derivation với PCController và
    /ask gate — Layer 3 của test_ask_kill_switch_gate.py) thay vì tự copy logic
    dẫn đường. Import LAZY: tránh chu kỳ ``scp.security`` ↔
    ``scp.pc_control.pc_controller`` lúc import package.
    """
    from scp.pc_control.pc_controller import default_kill_switch_path

    return default_kill_switch_path()


def _flag_file_source() -> str | None:
    """Kênh (a): file flag. Engaged khi file tồn tại; không đọc được → fail-closed."""
    try:
        if _flag_path().exists():
            return SOURCE_FLAG_FILE
        return None
    except Exception as exc:
        logger.error(
            "[HC-22] kill-switch flag unreadable (%s: %s) — failing CLOSED",
            type(exc).__name__, exc,
        )
        return SOURCE_FLAG_FILE_UNREADABLE


def _env_source() -> str | None:
    """Kênh (c): env ``SCP_KILL_SWITCH``. Giữ nguyên ngữ nghĩa cũ của
    stream_routes: chỉ giá trị ``"1"`` tính là engaged (không mở rộng tập giá
    trị trong bước hợp nhất — tránh thay đổi ý nghĩa env đang chạy)."""
    try:
        if os.environ.get(ENV_KILL_SWITCH, "0") == "1":
            return SOURCE_ENV
        return None
    except Exception as exc:  # pragma: no cover — os.environ.get không lỗi thực tế
        logger.error("[HC-22] kill-switch env unreadable (%s) — failing CLOSED", exc)
        return SOURCE_ENV


def _kernel_source(kernel: Any) -> str | None:
    """Kênh (b): TaskKernel ``control.global_kill`` — CHỈ khi có kernel instance.

    Đọc QUA kernel instance được truyền vào (``kernel._control()`` dùng
    thread-local connection của kernel — không mở connection mới, không chạm
    SQLite ngoài lifecycle của kernel). ``kernel=None`` → None (bỏ qua tường
    minh: caller không có kernel thì không có kênh DB để hỏi; gate file/env
    vẫn phủ).
    """
    if kernel is None:
        return None
    try:
        control = None
        if hasattr(kernel, "_control"):
            control = kernel._control()
        else:
            conn = getattr(kernel, "conn", None)
            if conn is not None:
                # [§15] fallback không có _control: vẫn phải verify chữ ký cờ
                # control — global_kill sai chữ ký đọc là kill=ON (fail-closed).
                from scp.task_kernel_parts.definitions import verified_control_row

                row = conn.execute("SELECT * FROM control WHERE id=1").fetchone()
                control = verified_control_row(row) if row is not None else None
        if control is not None and int(control["global_kill"]):
            return SOURCE_KERNEL
        return None
    except Exception as exc:
        # Fail-closed: kernel được truyền vào nhưng trạng thái KHÔNG ĐỌC ĐƯỢC
        # → tính là engaged (cùng hướng fail-closed của kênh file).
        logger.error(
            "[HC-22] kernel global_kill unreadable (%s: %s) — failing CLOSED",
            type(exc).__name__, exc,
        )
        return SOURCE_KERNEL_UNREADABLE


def engaged_source(kernel: Any = None) -> str | None:
    """Tên kênh đang engaged (ưu tiên: file → env → kernel) hoặc None.

    Dùng cho admission gate + audit log: một giá trị None nghĩa là KHÔNG kênh
    nào engaged; giá trị khác None là bằng chứng kênh nào đã kích hoạt kill.
    """
    return (
        _flag_file_source()
        or _env_source()
        or _kernel_source(kernel)
    )


def is_engaged(kernel: Any = None) -> bool:
    """True nếu BẤT KỲ kênh kill switch nào đang engaged (file, env, kernel).

    ``kernel`` là instance TaskKernel/adapter-kernel ĐANG CÓ của caller; None
    → kênh DB bị bỏ qua tường minh (xem ``_kernel_source``). Hàm này KHÔNG
    ghi file, KHÔNG mở connection, KHÔNG raise (mỗi kênh tự fail-closed).
    """
    return engaged_source(kernel) is not None


# [SEC-FIX 2026-10-27 · F5 audit af88fd31] Nhãn kênh đọc được của nguồn engage.
# Trước đây withheld message của /ask ghi CỨNG "(POST /v3/pc/kill)" — sai nguồn
# khi kill được bật qua kênh file flag hoặc env (POST /v3/pc/kill chỉ là MỘT
# trong các cách engage). Nhãn ánh xạ 1-1 với các SOURCE_* ở trên; nguồn lạ
# (không nằm trong bảng) được trả nguyên văn thay vì bị che — fail-honest.
_CHANNEL_LABELS: dict[str, str] = {
    SOURCE_FLAG_FILE: "file flag data/pc_controller/KILL_SWITCH",
    SOURCE_FLAG_FILE_UNREADABLE: "file flag unreadable (fail-closed)",
    SOURCE_ENV: "env SCP_KILL_SWITCH=1",
    SOURCE_KERNEL: "TaskKernel global kill",
    SOURCE_KERNEL_UNREADABLE: "TaskKernel state unreadable (fail-closed)",
}


def engaged_channel_label(kernel: Any = None) -> str:
    """Nhãn mô tả kênh đang engaged — dùng cho withheld message + audit log.

    Không bao giờ raise (kế thừa fail-closed của ``engaged_source``); khi
    KHÔNG kênh nào engaged trả về nhãn tường minh để caller không bao giờ
    hiển thị nguồn sai — một withheld message với nhãn "no channel engaged"
    là bằng chứng lỗi gate, dễ bị test soi.
    """
    source = engaged_source(kernel)
    if source is None:
        return "no channel engaged"
    return _CHANNEL_LABELS.get(source, source)
