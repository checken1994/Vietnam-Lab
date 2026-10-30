# [esc-auto slice 3 · 2026-10-30] Tách quyền MÁY vs OPERATOR; route "người" chỉ còn audit/veto.
#
# Trước fix (claude/c1-escalation 3a9f5b83 + slice 1/2): verify_admin nhận 5 loại credential
# như nhau; control_routes ghi actor="human_via_api". Container loop-scheduler (giữ
# SCP_SCHEDULER_ADMIN_TOKEN) approve được dead-man timer (hủy safe outcome) và nâng
# CapabilityLevel, audit vẫn ghi "người" (c1-escalation-done §7).
#
# Owner: "đã tự động thì ai duyệt cho bạn" ⇒ không có bước DUYỆT; người chỉ còn kill +
# veto/audit hậu kiểm. Pin ở đây:
#   * credential máy (scheduler / token-secret / admin-key / JWT admin) ⇒ 403 ở route operator;
#   * operator (SCP_AUTH_PASSWORD, credential không phân phối cho service nào) ⇒ 200 nhưng
#     CHỈ ghi audit: dead-man vẫn armed, safe outcome không bị hủy;
#   * audit ghi principal thật (operator:password / machine:scheduler_token), không "human";
#   * nâng capability bằng máy ⇒ 403, level không đổi; hạ capability (an toàn hơn) máy được
#     làm nhưng ghi đúng machine:*;
#   * cùng một giá trị secret đặt cho cả password lẫn scheduler ⇒ xếp loại máy (hạn chế nhất).
from __future__ import annotations

import types

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

SECRETS = {
    "SCP_AUTH_PASSWORD": "esc-auto-operator-password-0123456789",
    "SCP_SCHEDULER_ADMIN_TOKEN": "esc-auto-scheduler-machine-token-0123456789",
    "SCP_AUTH_TOKEN_SECRET": "esc-auto-token-secret-machine-0123456789",
    "SCP_ADMIN_KEY": "esc-auto-admin-key-machine-0123456789abcd",
}


def _bearer(value: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {value}"}


@pytest.fixture()
def control_app(monkeypatch, tmp_path):
    from scp.api.routes import control_routes
    from scp.api_server_parts import helpers
    from scp.meta import capability_levels
    from scp.security.escalation import EscalationManager

    for key in ("SCP_AUTH_PASSWORD_FILE", "SCP_SCHEDULER_ADMIN_TOKEN_FILE", "SCP_AUTH_TOKEN_SECRET_FILE", "SCP_ADMIN_KEY_FILE"):
        monkeypatch.delenv(key, raising=False)
    for key, value in SECRETS.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("SCP_CAPABILITY_LEVEL", "SANDBOX")
    monkeypatch.setattr(capability_levels, "_capability_manager", None)
    manager = EscalationManager(data_dir=str(tmp_path))
    monkeypatch.setattr(helpers, "_judge", types.SimpleNamespace(escalation_manager=manager))
    app = FastAPI()
    app.include_router(control_routes.router)
    yield TestClient(app), manager
    manager.shutdown()


MACHINE_CREDENTIALS = ["SCP_SCHEDULER_ADMIN_TOKEN", "SCP_AUTH_TOKEN_SECRET", "SCP_ADMIN_KEY"]


@pytest.mark.parametrize("env_name", MACHINE_CREDENTIALS)
def test_machine_credentials_cannot_act_as_operator(control_app, env_name):
    client, manager = control_app
    manager.on_threat_detected({"id": "t-machine", "type": "probe", "severity": "critical"})
    headers = _bearer(SECRETS[env_name])
    # Đọc trạng thái (quan sát) vẫn được — không phải hành động "người".
    assert client.get("/v105/escalation/status", headers=headers).status_code == 200
    for path in ("/v105/escalation/t-machine/approve", "/v105/escalation/t-machine/reject"):
        assert client.post(path, json={"action": "x"}, headers=headers).status_code == 403
    escalate = client.post("/v105/capability/escalate", json={"level": 5, "reason": "machine tries"}, headers=headers)
    assert escalate.status_code == 403
    from scp.meta.capability_levels import CapabilityLevel, get_capability_manager

    assert get_capability_manager().get_current_level() == CapabilityLevel.SANDBOX
    assert "t-machine" in manager._timers, "machine must not be able to touch the dead-man switch"
    assert not any(h.get("action") in ("approved", "operator_ack", "operator_dispute") for h in manager.get_escalation_history())


def test_jwt_admin_is_machine_too(control_app):
    from scp.security.jwt_guard import create_access_token

    client, manager = control_app
    manager.on_threat_detected({"id": "t-jwt", "type": "probe", "severity": "critical"})
    token = create_access_token(data={"sub": "admin"})
    assert client.get("/v105/escalation/status", headers=_bearer(token)).status_code == 200
    assert client.post("/v105/escalation/t-jwt/approve", json={}, headers=_bearer(token)).status_code == 403


def test_operator_ack_is_audit_only_and_records_true_principal(control_app, tmp_path):
    client, manager = control_app
    manager.on_threat_detected({"id": "t-op", "type": "probe", "severity": "critical"})
    headers = _bearer(SECRETS["SCP_AUTH_PASSWORD"])
    ack = client.post("/v105/escalation/t-op/approve", json={"action": "seen"}, headers=headers)
    assert ack.status_code == 200, ack.text
    body = ack.json()
    assert body["effect"] == "audit_only" and body["actor"] == "operator:password"
    dispute = client.post("/v105/escalation/t-op/reject", headers=headers)
    assert dispute.status_code == 200
    # Safe outcome KHÔNG bị hủy: deadline vẫn armed, status vẫn armed.
    assert "t-op" in manager._timers
    entry = next(e for e in manager.get_active_escalations() if e["threat_id"] == "t-op")
    assert entry["status"] == "armed"
    actions = [(h["action"], h.get("actor")) for h in manager.get_escalation_history()]
    assert ("operator_ack", "operator:password") in actions
    assert ("operator_dispute", "operator:password") in actions
    assert "human" not in (tmp_path / "escalation_state.json").read_text(encoding="utf-8").replace("human_review", "")


def test_capability_audit_never_says_human_and_deescalate_records_machine(control_app):
    from scp.meta.capability_levels import CapabilityLevel, get_capability_manager

    client, _ = control_app
    up = client.post("/v105/capability/escalate", json={"level": 4, "reason": "operator raises"},
                     headers=_bearer(SECRETS["SCP_AUTH_PASSWORD"]))
    assert up.status_code == 200, up.text
    # Ghi chú FA-11 (ngoài phạm vi, báo cáo riêng): get_capability_manager() reset level về
    # env ở MỖI lần gọi ⇒ nâng qua API bị hoàn tác ở lần đọc sau; vì vậy kiểm audit trail.
    trail = get_capability_manager().get_audit_trail(limit=5)
    assert any(e["actor"] == "operator:password" and e["new"] == int(CapabilityLevel.NARROW_PRODUCTION) for e in trail)
    down = client.post("/v105/capability/de-escalate", json={"reason": "scheduler lowers"},
                       headers=_bearer(SECRETS["SCP_SCHEDULER_ADMIN_TOKEN"]))
    assert down.status_code == 200 and down.json()["changed"] is True
    actors = [e["actor"] for e in get_capability_manager().get_audit_trail(limit=5)]
    assert "operator:password" in actors and "machine:scheduler_token" in actors
    assert not any("human" in a for a in actors), actors


def test_shared_secret_value_is_classified_most_restrictive(control_app, monkeypatch):
    client, manager = control_app
    monkeypatch.setenv("SCP_SCHEDULER_ADMIN_TOKEN", SECRETS["SCP_AUTH_PASSWORD"])
    manager.on_threat_detected({"id": "t-shared", "type": "probe", "severity": "critical"})
    resp = client.post("/v105/escalation/t-shared/approve", json={}, headers=_bearer(SECRETS["SCP_AUTH_PASSWORD"]))
    assert resp.status_code == 403


def test_capability_manager_denies_machine_actor_directly():
    from scp.meta.capability_levels import CapabilityLevel, CapabilityManager

    manager = CapabilityManager(CapabilityLevel.SANDBOX)
    assert manager.request_escalation(CapabilityLevel.FULL_PRODUCTION, "x", actor="machine:scheduler_token") is False
    assert manager.get_current_level() == CapabilityLevel.SANDBOX
