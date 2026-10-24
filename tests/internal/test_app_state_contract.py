# [HC-25 2026-10-23] Anti-placebo pin cho AppStateContract.
#
# Pin 3 điều:
#   1. register(app) set ĐỦ defaults theo contract, idempotent, không ghi đè
#      giá trị đã có (limiter-style pre-set phải sống sót).
#   2. attr NGOÀI contract bị phát hiện: register → WARNING + "undeclared";
#      audit_app_state → "undeclared" (contract không được im lặng trước attr lạ).
#   3. Bảng contract không được thối rữa: không tên trùng, mọi field có
#      writer + readers + kind (audit nhìn được ai ghi ai đọc).
from __future__ import annotations

import logging

import pytest
from fastapi import FastAPI

from scp.api_server_parts import app_state as app_state_mod
from scp.api_server_parts.app_state import (
    APP_STATE_CONTRACT,
    CONTRACT_FIELDS,
    audit_app_state,
    register,
)


def _fresh_app() -> FastAPI:
    return FastAPI()


def test_register_sets_all_contract_defaults() -> None:
    app = _fresh_app()
    report = register(app)

    assert report["undeclared"] == []
    assert sorted(report["initialized"]) == sorted(f.name for f in CONTRACT_FIELDS)
    assert report["kept"] == []

    for f in CONTRACT_FIELDS:
        assert hasattr(app.state, f.name), f"missing default for {f.name}"
        assert getattr(app.state, f.name) == f.default

    # Defaults then-keypin với readiness semantics cũ (getattr defensive
    # trong api_server.py đọc đúng giá trị cũ khi chưa có writer):
    assert app.state.judge_ready is False
    assert app.state.startup_status == "starting"
    assert app.state.readiness_reason == "judge_initialization_pending"
    assert app.state.background_scheduler_started is False


def test_register_is_idempotent_and_never_overwrites_preset() -> None:
    app = _fresh_app()
    # Giả lập limiter được api_server.py set ở import time (TRƯỚC register):
    app.state.limiter = object()
    app.state.judge_ready = True  # writer đã chạy trước register (boot lại)

    first = register(app)
    assert "limiter" in first["kept"]
    assert "judge_ready" in first["kept"]
    assert app.state.judge_ready is True  # KHÔNG bị reset về False
    assert getattr(app.state, "limiter") is not None

    second = register(app)
    assert second["initialized"] == []
    assert sorted(second["kept"]) == sorted(f.name for f in CONTRACT_FIELDS)


def test_undeclared_attr_warns_and_surfaces_in_audit(caplog: pytest.LogCaptureFixture) -> None:
    app = _fresh_app()
    app.state.rogue_mystery_attr = "unregistered"  # attr ngoài contract

    with caplog.at_level(logging.WARNING, logger="scp.api"):
        report = register(app)

    assert "rogue_mystery_attr" in report["undeclared"]
    assert any("rogue_mystery_attr" in rec.message for rec in caplog.records), (
        "HC-25: attr ngoài contract phải WARNING tại register"
    )

    audit = audit_app_state(app)
    assert "rogue_mystery_attr" in audit["undeclared"]
    assert audit["undeclared"]["rogue_mystery_attr"]["value_type"] == "str"


def test_audit_reports_missing_and_value_types() -> None:
    app = _fresh_app()
    app.state.judge_ready = True

    audit = audit_app_state(app)
    # judge_ready có sẵn giá trị writer-style → present + đúng type:
    assert audit["declared"]["judge_ready"]["present"] is True
    assert audit["declared"]["judge_ready"]["value_type"] == "bool"
    # App chưa qua register → mọi field default còn thiếu:
    assert "startup_status" in audit["missing"]
    assert "inbox_daemon" in audit["missing"]
    assert audit["contract_size"] == len(CONTRACT_FIELDS)

    register(app)
    audit2 = audit_app_state(app)
    assert audit2["missing"] == []
    assert audit2["undeclared"] == {}


def test_contract_table_hygiene() -> None:
    names = [f.name for f in CONTRACT_FIELDS]
    assert len(names) == len(set(names)), "contract không được trùng tên"
    for f in CONTRACT_FIELDS:
        assert f.kind, f"{f.name}: kind bắt buộc"
        assert f.writer, f"{f.name}: writer (lifespan stage) bắt buộc"
        assert f.readers, f"{f.name}: readers bắt buộc"

    # Pin các attr từng là nợ HC-25 (tên không được đổi ngầm):
    required = {
        "judge_ready",
        "startup_status",
        "readiness_reason",
        "limiter",
        "background_scheduler_started",
        "evolution_initialized",
        "deep_audit_started",
        "deep_audit_stop",
        "attack_monitor_started",
        "attack_monitor_stop",
        "doubt_cron",
        "why_verify_stop",
        "why_verify_thread",
        "retry_policy_stop",
        "retry_policy",
        "discovery_scheduler",
        "inbox_daemon",
        "task_kernel",
    }
    assert required <= set(names), f"contract thiếu: {required - set(names)}"


def test_contract_covers_every_writer_found_in_lifespan() -> None:
    """Anti-placebo: quét static tất cả ``app.state.<x> =`` trong scp/ — mọi
    tên writer phải nằm trong contract. Nếu ai thêm attr mới mà quên khai
    báo, test này văng (không cần đợi audit runtime)."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "scp"
    pattern = re.compile(r"app\.state\.(\w+)\s*=")
    found: set[str] = set()
    for py in root.rglob("*.py"):
        text = py.read_text(encoding="utf-8", errors="replace")
        found |= set(pattern.findall(text))

    declared = set(APP_STATE_CONTRACT.names())
    leaked = sorted(found - declared)
    assert leaked == [], f"app.state writer ngoài contract (thêm vào CONTRACT_FIELDS): {leaked}"
    # và ngược lại — mọi field trong contract phải có ít nhất 1 writer được
    # quét được HOẶC được ghi chú là defensive-read-only (task_kernel):
    writer_backed = {f.name for f in CONTRACT_FIELDS if "(không có writer" not in f.writer}
    assert writer_backed <= found | {"limiter"}, (
        f"field contract không tìm thấy writer tương ứng: {writer_backed - found}"
    )


def test_register_helper_module_shape() -> None:
    assert app_state_mod.APP_STATE_CONTRACT.fields is CONTRACT_FIELDS
