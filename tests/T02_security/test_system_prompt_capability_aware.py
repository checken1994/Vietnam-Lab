"""HC-54 — Capability-aware system prompt pins.

Pin behavior: system prompt KHÔNG được hứa năng lực mà route profile hiện tại
đã tắt. Trước đây prompt là chuỗi tĩnh hứa "full physical Computer-Use"
(kể cả khi SCP_API_PROFILE=core tắt hands/pc_controller/web_control).

Contract pinned:
  - profile `full`: mọi năng lực được liệt kê ở phần ENABLED, không phần DISABLED;
  - profile `core`: KHÔNG có phần hứa năng lực (orchestrator authority), có phần
    nói rõ năng lực đang TẮT;
  - profile `standard`: Hands bật, PC Controller/noVNC tắt;
  - env override `SCP_ENABLED_GROUPS`/`SCP_DISABLED_GROUPS` được tôn trọng;
  - profile cấu hình sai → conservative (không hứa năng lực nào ngoài core).
"""
from __future__ import annotations

from scp.api.system_prompt import build_system_prompt

# Các marker "hứa năng lực" (chỉ được xuất hiện khi group tương ứng ENABLED)
_ENABLE_MARKER_VI = "NĂNG LỰC đang BẬT"
_ENABLE_MARKER_EN = "CAPABILITIES ENABLED"
_DISABLE_MARKER_VI = "đang TẮT"
_DISABLE_MARKER_EN = "CAPABILITIES DISABLED"
_ORCHESTRATOR_VI = "thẩm quyền của bộ điều phối"
_ORCHESTRATOR_EN = "orchestrator of these capabilities"


def test_full_profile_promises_all_capabilities():
    env = {"SCP_API_PROFILE": "full"}
    vi = build_system_prompt("vi", environ=env)
    en = build_system_prompt("en", environ=env)
    assert _ENABLE_MARKER_VI in vi
    assert _ORCHESTRATOR_VI in vi
    assert "PC Controller" in vi and "noVNC" in vi
    assert _DISABLE_MARKER_VI not in vi
    assert _ENABLE_MARKER_EN in en
    assert _ORCHESTRATOR_EN in en
    assert _DISABLE_MARKER_EN not in en


def test_core_profile_does_not_promise_disabled_capabilities():
    env = {"SCP_API_PROFILE": "core"}
    vi = build_system_prompt("vi", environ=env)
    en = build_system_prompt("en", environ=env)
    # Không được hứa: không phần ENABLED, không directive orchestrator.
    assert _ENABLE_MARKER_VI not in vi
    assert _ORCHESTRATOR_VI not in vi
    assert _ENABLE_MARKER_EN not in en
    assert _ORCHESTRATOR_EN not in en
    # Phải nói tường minh các năng lực bị tắt (được phép NHẮC tên để phủ nhận).
    assert _DISABLE_MARKER_VI in vi
    assert "PC Controller" in vi and "noVNC" in vi
    assert "không hứa hẹn" in vi
    assert _DISABLE_MARKER_EN in en
    assert "Never promise" in en
    # Task Kernel thuộc core — luôn được nhắc.
    assert "Task Kernel" in vi and "Task Kernel" in en


def test_standard_profile_enables_hands_only():
    env = {"SCP_API_PROFILE": "standard"}
    vi = build_system_prompt("vi", environ=env)
    assert _ENABLE_MARKER_VI in vi
    assert _ORCHESTRATOR_VI in vi
    assert _DISABLE_MARKER_VI in vi
    # Phần ENABLED không được chứa PC Controller/noVNC (chỉ Hands).
    enabled_section = vi.split(_ENABLE_MARKER_VI)[1].split("NĂNG LỰC đang TẮT")[0]
    assert "Hands" in enabled_section
    assert "PC Controller" not in enabled_section
    assert "noVNC" not in enabled_section


def test_enabled_groups_override():
    env = {"SCP_API_PROFILE": "core", "SCP_ENABLED_GROUPS": "pc_controller"}
    vi = build_system_prompt("vi", environ=env)
    assert _ENABLE_MARKER_VI in vi
    enabled_section = vi.split(_ENABLE_MARKER_VI)[1].split("NĂNG LỰC đang TẮT")[0]
    assert "PC Controller" in enabled_section
    assert "noVNC" not in enabled_section


def test_disabled_groups_override():
    env = {"SCP_API_PROFILE": "full", "SCP_DISABLED_GROUPS": "hands"}
    vi = build_system_prompt("vi", environ=env)
    enabled_section = vi.split(_ENABLE_MARKER_VI)[1].split("NĂNG LỰC đang TẮT")[0]
    assert "Chromium CDP" not in enabled_section
    assert _DISABLE_MARKER_VI in vi


def test_invalid_profile_is_conservative():
    env = {"SCP_API_PROFILE": "khong-hop-le"}
    vi = build_system_prompt("vi", environ=env)
    en = build_system_prompt("en", environ=env)
    assert _ENABLE_MARKER_VI not in vi
    assert _ORCHESTRATOR_VI not in vi
    assert "không hợp lệ" in vi
    assert _ENABLE_MARKER_EN not in en
    assert "invalid" in en


def test_identity_block_always_present():
    env = {"SCP_API_PROFILE": "core"}
    assert "ĐỊNH DANH" in build_system_prompt("vi", environ=env)
    assert "IDENTITY" in build_system_prompt("en", environ=env)
