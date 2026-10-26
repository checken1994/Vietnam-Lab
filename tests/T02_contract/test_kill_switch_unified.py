"""[HC-22 + HC-13] Kill switch hợp nhất 3 kênh — anti-placebo contract tests.

Mục tiêu: scp.security.kill_switch là authority ĐỌC duy nhất cho kill switch:
BẤT KỲ kênh nào trong 3 kênh engaged → ``is_engaged()`` True, và
``engaged_source()`` phải gọi ĐÚNG TÊN kênh (bằng chứng cho audit log).

Mỗi test dưới đây engage MỘT kênh ĐỘC LẬP (negative control có: tắt hết →
False) — chống placebo kiểu "hàm luôn trả True" và kiểu "hàm luôn trả False":

  (a) file flag ``data/pc_controller/KILL_SWITCH`` (tmp_path — KHÔNG đụng data/ thật)
  (b) SQLite TaskKernel ``control.global_kill`` (TaskKernel thật trên tmp sqlite)
  (c) env ``SCP_KILL_SWITCH=1`` (monkeypatch.setenv — hermetic, FA-11)

Đồng thời pin: không import ngược ``scp.api_server`` (HC-13), không tự mở
kernel connection mới, không ghi file khi chỉ đọc, fail-closed khi trạng thái
không đọc được.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest


@pytest.fixture()
def _hermetic_env(tmp_path, monkeypatch):
    """[FA-11 hermetic isolation] Tách 3 kênh khỏi môi trường thật.

    - SCP_PC_WORKING_DIR → tmp_path: kênh file flag ĐỌC theo env tại thời điểm
      gọi (default_kill_switch_path), nên flag thật của data/ bị vô hiệu.
    - SCP_KILL_SWITCH bị xóa: kênh env off mặc định, mỗi test tự setenv khi cần.
    """
    monkeypatch.setenv("SCP_PC_WORKING_DIR", str(tmp_path))
    monkeypatch.delenv("SCP_KILL_SWITCH", raising=False)
    return tmp_path


def _engage_flag(tmp_path: Path, reason: str = "hc22 unified test") -> Path:
    from scp.pc_control.pc_controller import default_kill_switch_path

    flag = default_kill_switch_path()
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.write_text(reason, encoding="utf-8")
    return flag


def _real_kernel(tmp_path: Path):
    from scp.task_kernel import TaskKernel

    return TaskKernel(str(tmp_path / "hc22-kill.sqlite3"))


# ---------------------------------------------------------------------------
# 1. Từng kênh engage ĐỘC LẬP → True + đúng tên kênh
# ---------------------------------------------------------------------------


def test_env_channel_engages(_hermetic_env, monkeypatch):
    """Kênh (c): SCP_KILL_SWITCH=1 → engaged, source = env_scp_kill_switch."""
    from scp.security import kill_switch

    monkeypatch.setenv("SCP_KILL_SWITCH", "1")
    assert kill_switch.is_engaged() is True
    assert kill_switch.engaged_source() == kill_switch.SOURCE_ENV


def test_flag_file_channel_engages(_hermetic_env):
    """Kênh (a): file flag tồn tại → engaged, source = pc_flag_file."""
    from scp.security import kill_switch

    flag = _engage_flag(_hermetic_env)
    assert flag.exists()
    assert kill_switch.is_engaged() is True
    assert kill_switch.engaged_source() == kill_switch.SOURCE_FLAG_FILE


def test_kernel_channel_engages_with_real_taskkernel(_hermetic_env, tmp_path):
    """Kênh (b): TaskKernel thật trên tmp sqlite, global_kill=1 → engaged.

    Không mock kernel enforcement: set_global_kill(True) thật (FA-12 — đọc lại
    control row trực tiếp từ SQLite để chứng minh state thật, không phải state
    mà chính hàm under test tự báo).
    """
    import sqlite3

    from scp.security import kill_switch

    kernel = _real_kernel(tmp_path)
    epoch = kernel.set_global_kill(True, actor="hc22-test")
    assert epoch >= 1

    with sqlite3.connect(str(kernel.db_path)) as conn:
        row = conn.execute("SELECT global_kill FROM control WHERE id=1").fetchone()
    assert row == (1,), f"SQLite phải global_kill=1, thấy {row}"

    assert kill_switch.is_engaged(kernel=kernel) is True
    assert kill_switch.engaged_source(kernel=kernel) == kill_switch.SOURCE_KERNEL


def test_kernel_channel_ignored_when_kernel_none(_hermetic_env, tmp_path):
    """kernel=None → kênh DB bị bỏ qua TƯỜNG MINH (documented skip).

    Kernel engaged nhưng caller không truyền kernel → is_engaged() chỉ nhìn
    file+env (đều off) → False. Đây là hợp đồng, không phải bug: module không
    được tự mở connection mới khi caller không có kernel.
    """
    from scp.security import kill_switch

    kernel = _real_kernel(tmp_path)
    kernel.set_global_kill(True, actor="hc22-test")
    assert kill_switch.is_engaged() is False
    assert kill_switch.engaged_source() is None
    assert kill_switch.is_engaged(kernel=kernel) is True  # truyền kernel → thấy


# ---------------------------------------------------------------------------
# 2. Negative controls + precedence
# ---------------------------------------------------------------------------


def test_all_channels_clear_not_engaged(_hermetic_env, tmp_path):
    """Tắt hết 3 kênh → False + source None (chống placebo 'luôn trả True')."""
    from scp.security import kill_switch

    kernel = _real_kernel(tmp_path)  # global_kill=0 mặc định
    assert kill_switch.is_engaged(kernel=kernel) is False
    assert kill_switch.engaged_source(kernel=kernel) is None


def test_env_values_other_than_1_do_not_engage(_hermetic_env, monkeypatch):
    """Giữ nguyên ngữ nghĩa cũ của stream_routes: chỉ '1' là engaged."""
    from scp.security import kill_switch

    for value in ("0", "", "true", "on", "1 "):
        monkeypatch.setenv("SCP_KILL_SWITCH", value)
        assert kill_switch.engaged_source() is None, value


def test_flag_file_takes_precedence_over_env(_hermetic_env, monkeypatch):
    """File + env cùng engaged → source là kênh file (ưu tiên authority
    process-independent)."""
    from scp.security import kill_switch

    _engage_flag(_hermetic_env)
    monkeypatch.setenv("SCP_KILL_SWITCH", "1")
    assert kill_switch.engaged_source() == kill_switch.SOURCE_FLAG_FILE


# ---------------------------------------------------------------------------
# 3. Fail-closed khi trạng thái không đọc được
# ---------------------------------------------------------------------------


def test_flag_unreadable_fails_closed(_hermetic_env, monkeypatch):
    """Flag probe raise OSError → engaged (source _unreadable), KHÔNG False."""
    from scp.security import kill_switch

    class _BoomPath:
        def exists(self):
            raise OSError("simulated flag probe failure")

    monkeypatch.setattr(kill_switch, "_flag_path", lambda: _BoomPath())
    assert kill_switch.is_engaged() is True
    assert kill_switch.engaged_source() == kill_switch.SOURCE_FLAG_FILE_UNREADABLE


def test_kernel_unreadable_fails_closed(_hermetic_env):
    """Kernel được truyền vào nhưng control row không đọc được → fail-closed."""
    import sqlite3

    from scp.security import kill_switch

    class _BrokenKernel:
        def _control(self):
            raise sqlite3.OperationalError("database is locked")

    assert kill_switch.is_engaged(kernel=_BrokenKernel()) is True
    assert kill_switch.engaged_source(kernel=_BrokenKernel()) == kill_switch.SOURCE_KERNEL_UNREADABLE


# ---------------------------------------------------------------------------
# 4. Không side effect khi chỉ đọc (HC-22 ràng buộc thiết kế)
# ---------------------------------------------------------------------------


def test_module_never_imports_api_server_or_sqlite3(_hermetic_env):
    """[HC-13 anti-reverse-import] Module hợp nhất KHÔNG được import
    scp.api_server (vòng ngược middleware/api_server) và KHÔNG được import
    sqlite3 (không tự mở connection — kênh DB chỉ đọc qua kernel instance
    được truyền vào). Kiểm bằng AST trên source thật, không phải grep thô
    (docstring/comment không được lừa assertion)."""
    import scp.security.kill_switch as kill_switch_module

    tree = ast.parse(inspect.getsource(kill_switch_module))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    assert not any("api_server" in name for name in imported), imported
    assert not any(name == "sqlite3" or name.startswith("sqlite3.") for name in imported), imported


def test_middleware_no_longer_imports_api_server_for_kill_switch():
    """[HC-13] runtime_skill_guard hết vòng ngược: không còn import
    scp.api_server ở bất kỳ đâu trong module middleware."""
    import scp.middleware.runtime_skill_guard as guard_module

    tree = ast.parse(inspect.getsource(guard_module))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert (node.module or "") != "scp.api_server", (
                "runtime_skill_guard lại import ngược scp.api_server (HC-13 regression)"
            )


def test_middleware_safe_probe_control_off_when_all_channels_clear(_hermetic_env):
    """Control: mọi kênh off → safe probe False (không placebo 'luôn True')."""
    from scp.middleware import runtime_skill_guard
    from scp.security import kill_switch

    assert kill_switch.engaged_source() is None
    assert runtime_skill_guard._pc_kill_switch_engaged_safe() is False


def test_middleware_safe_probe_fails_closed_when_unified_module_unavailable(_hermetic_env, monkeypatch):
    """_pc_kill_switch_engaged_safe: engaged (env kênh) → True; module hợp nhất
    bỗng không import được → VẪN True (fail-closed, sửa chỗ cũ trả False
    fail-open trái docstring)."""
    import sys

    from scp.middleware import runtime_skill_guard

    monkeypatch.setenv("SCP_KILL_SWITCH", "1")
    assert runtime_skill_guard._pc_kill_switch_engaged_safe() is True

    # Fail-closed khi unified module unavailable: sys.modules[name] = None
    # khiến import tiếp theo raise ImportError. Assertion này PHẢI đứng cuối —
    # poison sys.modules còn hiệu lực đến hết test.
    monkeypatch.setitem(sys.modules, "scp.security.kill_switch", None)
    assert runtime_skill_guard._pc_kill_switch_engaged_safe() is True


# ---------------------------------------------------------------------------
# 5. Backward compat: các call-site cũ cùng một authority
# ---------------------------------------------------------------------------


def test_api_server_gate_delegates_to_unified_module(_hermetic_env, monkeypatch):
    """api_server._pc_kill_switch_engaged (tên cũ, giữ cho backward compat)
    phải phản ánh authority hợp nhất: env engaged → True (trước đây hàm này
    chỉ đọc file flag)."""
    from scp import api_server

    monkeypatch.setenv("SCP_KILL_SWITCH", "1")
    assert api_server._pc_kill_switch_engaged() is True

    monkeypatch.delenv("SCP_KILL_SWITCH", raising=False)
    assert api_server._pc_kill_switch_engaged() is False


def test_stream_route_refuses_when_any_channel_engaged(_hermetic_env, monkeypatch):
    """stream_routes (kênh (c) cũ) giờ hỏi authority hợp nhất: flag file
    engaged (không chỉ env) cũng phải 503 fail-closed."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from scp.api._shared import verify_admin
    from scp.api.routes.stream_routes import router as stream_router

    _engage_flag(_hermetic_env)  # engage kênh FILE: lane stream giờ thấy cả kênh không-env

    app = FastAPI()
    app.include_router(stream_router)
    app.dependency_overrides[verify_admin] = lambda: None
    client = TestClient(app)
    response = client.post("/v105/ask/stream", json={"question": "hc22 probe"})
    assert response.status_code == 503, response.text
    assert "kill switch" in response.text.lower()


# ---------------------------------------------------------------------------
# 6. [SEC-FIX 2026-10-27 · F5 audit af88fd31] engaged_channel_label — withheld
# message phải ghi ĐÚNG kênh (trước đây /ask ghi cứng "(POST /v3/pc/kill)"
# cho mọi kênh — sai nguồn khi kill bật bằng file flag/env/kernel).
# ---------------------------------------------------------------------------


def test_label_env_channel(_hermetic_env, monkeypatch):
    """Kênh env engaged → nhãn ghi đúng kênh env."""
    from scp.security import kill_switch

    monkeypatch.setenv("SCP_KILL_SWITCH", "1")
    assert kill_switch.engaged_channel_label() == "env SCP_KILL_SWITCH=1"


def test_label_flag_file_channel(_hermetic_env):
    """Kênh file flag engaged → nhãn ghi đúng đường dẫn flag."""
    from scp.security import kill_switch

    _engage_flag(_hermetic_env)
    assert kill_switch.engaged_channel_label() == "file flag data/pc_controller/KILL_SWITCH"


def test_label_kernel_channel_real_taskkernel(_hermetic_env, tmp_path):
    """Kênh kernel — TaskKernel THẬT (FA-12): label theo state SQLite thật,
    đối xứng engage/release, không mock."""
    from scp.security import kill_switch

    kernel = _real_kernel(tmp_path)
    assert kill_switch.engaged_channel_label(kernel=kernel) == "no channel engaged"
    kernel.set_global_kill(True, actor="f5-label-test")
    assert kill_switch.engaged_channel_label(kernel=kernel) == "TaskKernel global kill"
    kernel.set_global_kill(False, actor="f5-label-test")
    assert kill_switch.engaged_channel_label(kernel=kernel) == "no channel engaged"


def test_label_fail_closed_file_unreadable(_hermetic_env, monkeypatch):
    """Flag không đọc được → nhãn fail-closed tường minh (không nhầm kênh khác)."""
    from scp.security import kill_switch

    class _BoomPath:
        def exists(self):
            raise OSError("simulated flag probe failure")

    monkeypatch.setattr(kill_switch, "_flag_path", lambda: _BoomPath())
    assert kill_switch.engaged_channel_label() == "file flag unreadable (fail-closed)"


def test_label_no_channel_engaged_is_explicit(_hermetic_env):
    """Không kênh nào engaged → nhãn tường minh "no channel engaged" — một
    withheld message với nhãn này là bằng chứng lỗi gate (fail-honest),
    không bao giờ hiển thị nguồn sai mặc định."""
    from scp.security import kill_switch

    assert kill_switch.engaged_channel_label() == "no channel engaged"
