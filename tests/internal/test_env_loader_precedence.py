"""[W4-2 2026-10-23] env_loader precedence — file-wins phải LOUD, không âm thầm.

Audit finding 1.2: operator set ``SCP_API_PROFILE=full`` trong shell; import-time
``load_selected_env()`` (api_server.py:13, judge_llm.py:7) áp lại env file và
ĐÈ ngược biến process env mà không một dấu vết → server chạy profile core
trong khi operator tưởng full ("âm thầm kéo về cấu hình cũ").

Pinned contract (Option B — explicit boundary giữ file-wins):
  1. SCP_ENV_FILE set → file VẪN thắng biến đã có trong process env (explicit
     boundary: live_flow_proof --egress-deny, run_full_audit, reality suite
     đều dựa vào điều này) NHƯNG mỗi lần đè phải emit WARNING kèm TÊN key,
     KHÔNG BAO GIỜ in giá trị (secrecy invariant).
  2. File chứa ``SCP_ENV_FILE=`` (tự tham chiếu / hijack boundary) → key bị
     bỏ qua + WARNING 1 lần; SCP_ENV_FILE thuộc về launcher.
  3. Lần gọi thứ 2 trên CÙNG 1 file (judge_llm import-time) KHÔNG đè lại biến
     đã được ghi vào os.environ sau lần load đầu (argv port, launcher override).
  4. File mới (path khác) vẫn được áp — idempotency là per-path, không khoá
     process.
  5. Key mới (chưa có trong process env) vẫn được fill từ file — non-regression.
"""
from __future__ import annotations

import logging
import os

import pytest

from scp.security import env_loader


@pytest.fixture(autouse=True)
def _fresh_loader_state(monkeypatch):
    """Mỗi test start với state module sạch + boundary var dọn dẹp."""
    monkeypatch.setattr(env_loader, "_applied_files", set())
    monkeypatch.setattr(env_loader, "_boundary_warning_emitted", False)
    monkeypatch.delenv("SCP_ENV_FILE", raising=False)
    yield


def test_explicit_file_overrides_process_env_loudly(tmp_path, monkeypatch, caplog):
    """Nhánh ÂM (audit 1.2): shell var + file khác giá trị → file vẫn thắng
    (explicit boundary) NHƯNG WARNING phải xuất hiện với tên key, không value."""
    key = "W42_TEST_PROFILE"
    monkeypatch.setenv(key, "shell-full")
    env_file = tmp_path / "boundary.env"
    env_file.write_text(f"{key}=file-core\n", encoding="utf-8")
    monkeypatch.setenv("SCP_ENV_FILE", str(env_file))

    with caplog.at_level(logging.WARNING, logger="scp.security.env_loader"):
        env_loader.load_selected_env()

    # Explicit boundary: file wins (pinned semantics)...
    assert os.environ[key] == "file-core"
    # ...nhưng KHÔNG âm thầm: WARNING nêu đúng tên key...
    warn_messages = [
        r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING
    ]
    assert any(key in msg for msg in warn_messages), (
        f"silent override regression: no WARNING naming {key}: {warn_messages}"
    )
    # ...và không bao giờ lộ giá trị (cả giá trị shell lẫn giá trị file).
    assert all(
        "shell-full" not in msg and "file-core" not in msg for msg in warn_messages
    ), f"value leaked into log: {warn_messages}"


def test_env_file_self_reference_is_ignored_with_warning(tmp_path, monkeypatch, caplog):
    """File không được định nghĩa lại SCP_ENV_FILE (tự tham chiếu/hijack)."""
    env_file = tmp_path / "selfref.env"
    env_file.write_text(
        f"SCP_ENV_FILE={env_file.name}\nW42_SELFREF=applied\n", encoding="utf-8"
    )
    monkeypatch.setenv("SCP_ENV_FILE", str(env_file))

    with caplog.at_level(logging.WARNING, logger="scp.security.env_loader"):
        env_loader.load_selected_env()

    # Boundary vẫn trỏ đúng nơi launcher đã set — không bị file kéo đi.
    assert os.environ["SCP_ENV_FILE"] == str(env_file)
    # Key thường trong file vẫn áp bình thường.
    assert os.environ["W42_SELFREF"] == "applied"
    warn_messages = [
        r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING
    ]
    assert any("SCP_ENV_FILE" in msg for msg in warn_messages), (
        f"self-reference must warn: {warn_messages}"
    )


def test_second_load_same_file_does_not_reclobber_runtime_env(tmp_path, monkeypatch):
    """Gốc rễ audit 1.2: call lần 2 (judge_llm import) phải no-op trên cùng
    file — biến ghi vào os.environ sau lần load đầu được giữ nguyên."""
    key = "W42_RUNTIME_PIN"
    env_file = tmp_path / "once.env"
    env_file.write_text(f"{key}=from-file\n", encoding="utf-8")
    monkeypatch.setenv("SCP_ENV_FILE", str(env_file))

    env_loader.load_selected_env()
    assert os.environ[key] == "from-file"

    # Giống __main__.main() ghi argv port vào os.environ SAU lần load đầu.
    os.environ[key] = "runtime-value"
    env_loader.load_selected_env()  # import-time call thứ 2 — phải no-op
    assert os.environ[key] == "runtime-value", (
        "second load re-clobbered a runtime-written variable (audit 1.2 class)"
    )


def test_new_boundary_file_is_applied_after_first(tmp_path, monkeypatch, caplog):
    """Idempotency là per-path: đổi SCP_ENV_FILE sang file mới → file mới áp."""
    first = tmp_path / "a.env"
    first.write_text("W42_A=1\n", encoding="utf-8")
    monkeypatch.setenv("SCP_ENV_FILE", str(first))
    env_loader.load_selected_env()
    assert os.environ["W42_A"] == "1"

    second = tmp_path / "b.env"
    second.write_text("W42_A=2\nW42_B=2\n", encoding="utf-8")
    monkeypatch.setenv("SCP_ENV_FILE", str(second))
    with caplog.at_level(logging.WARNING, logger="scp.security.env_loader"):
        env_loader.load_selected_env()

    assert os.environ["W42_A"] == "2"
    assert os.environ["W42_B"] == "2"
    # W42_A đã có sẵn ("1") → lần đè này cũng phải loud.
    warn_messages = [
        r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING
    ]
    assert any("W42_A" in msg for msg in warn_messages)


def test_new_keys_still_filled_from_file(tmp_path, monkeypatch):
    """Non-regression: key CHƯA có trong process env vẫn được fill từ file
    (không warning, không bị chặn bởi idempotency khi file là mới)."""
    env_file = tmp_path / "fill.env"
    env_file.write_text("W42_FILL_KEY=filled\n", encoding="utf-8")
    monkeypatch.setenv("SCP_ENV_FILE", str(env_file))
    monkeypatch.delenv("W42_FILL_KEY", raising=False)

    env_loader.load_selected_env()

    assert os.environ["W42_FILL_KEY"] == "filled"
