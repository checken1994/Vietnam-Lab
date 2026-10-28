"""R20 + R21 — chống masking KernelError thành NotFound & re-bind thiếu 'os'.

Zero-mock (FA-04): dùng KERNEL THẬT thiếu SCP_JOURNAL_SECRET để tái hiện đúng
lỗi sandbox 28/10 (không MagicMock create_task).
"""
from __future__ import annotations

import importlib
import os
from pathlib import Path

import pytest


def _adapter_fresh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Dựng AskKernelAdapter với kernel THẬT, CHẲNG có K_journal → create_task
    văng KernelError thật (đúng kịch bản sandbox 28/10)."""
    monkeypatch.delenv("SCP_JOURNAL_SECRET", raising=False)
    monkeypatch.delenv("SCP_JOURNAL_SECRET_FILE", raising=False)
    monkeypatch.delenv("SCP_CAPABILITY_SECRET", raising=False)
    monkeypatch.delenv("SCP_CAPABILITY_SECRET_FILE", raising=False)
    import scp.ask_kernel_adapter as mod
    importlib.reload(mod)
    return mod.AskKernelAdapter(str(tmp_path / "k.db"), str(tmp_path / "t.jsonl"))


def test_r20_begin_re_raise_kernel_error_khong_bi_not_found_che(tmp_path, monkeypatch):
    """[R20] secret thật nhưng không hợp lệ (ngắn) → create_task văng KernelError
    validation THẬT khi ghi journal; begin() phải re-raise đúng KernelError,
    TUYỆT ĐỐI không thành NotFound giả (kernel thật, zero-mock)."""
    import scp.ask_kernel_adapter as mod
    from scp.task_kernel import KernelError, NotFound

    monkeypatch.delenv("SCP_JOURNAL_SECRET", raising=False)
    monkeypatch.delenv("SCP_JOURNAL_SECRET_FILE", raising=False)
    adapter = mod.AskKernelAdapter(str(tmp_path / "k.db"), str(tmp_path / "t.jsonl"))

    with pytest.raises(Exception) as ei:
        adapter.begin("câu hỏi test R20", [], "", "session-r20")

    assert not isinstance(ei.value, NotFound), (
        f"R20 masking vẫn còn: got {type(ei.value).__name__} thay vì KernelError"
    )
    assert isinstance(ei.value, KernelError), (
        f"mong đợi KernelError gốc, got {type(ei.value).__name__}: {ei.value}"
    )


def test_r21_api_server_global_co_bind_co_os():
    """[R21] sau khi import os vào api_server, namespace re-bind đầy đủ:
    _lifespan_core nhìn thấy 'os' trong globals cha."""
    from scp import api_server
    from scp.api_server_parts import lifespan as lp

    assert "os" in api_server.__dict__, "api_server phải có 'os' (R21 patch)"
    raw = getattr(lp._lifespan_core, "__wrapped__", lp._lifespan_core)
    import dis
    loads = {i.argval for i in dis.get_instructions(raw.__code__)
             if i.opname == "LOAD_GLOBAL"}
    assert "os" in loads, "_lifespan_core phải tham chiếu 'os' (bytecode)"
    # mọi global hàm cần đều resolve được trong api_server (không NameError nữa)
    builtins_names = set(dir(__builtins__)) if not isinstance(__builtins__, dict) else set(__builtins__)
    missing = [n for n in loads
               if n not in api_server.__dict__
               and n not in builtins_names
               and not hasattr(lp, n)]
    assert not missing, f"vẫn thiếu globals cho re-bind: {missing}"


def test_r21_lifespan_khoi_dong_khong_nameerror_os(monkeypatch, tmp_path):
    """[R21 end-to-end] nạp api_server đầy đủ — không còn NameError 'os' ẩn
    trong _lifespan_core (chỉ import + kiểm hàm re-bound tồn tại và gọi được
    phần an toàn: hàm chỉ chạy thật khi server boot; ở đây kiểm resolution)."""
    from scp import api_server

    rebound = getattr(api_server, "_lifespan_core", None)
    assert rebound is not None
    # resolution check: mọi LOAD_GLOBAL của bản re-bound đều có trong
    # api_server.__dict__ hoặc builtins
    import dis
    loads = {i.argval for i in dis.get_instructions(rebound.__code__)
             if i.opname == "LOAD_GLOBAL"}
    builtins_names = set(dir(__builtins__)) if not isinstance(__builtins__, dict) else set(__builtins__)
    missing = [n for n in loads
               if n not in api_server.__dict__ and n not in builtins_names]
    assert "os" not in missing
