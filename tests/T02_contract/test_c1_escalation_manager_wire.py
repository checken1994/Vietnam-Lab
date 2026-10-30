# [CỔNG 1 · c1-escalation · 2026-10-30] Wire EscalationManager vào judge production.
#
# Trước fix (main a2282b10): `EscalationManager(` không được khởi tạo ở đâu trong
# scp/ ⇒ helpers.get_judge() trả judge KHÔNG có `escalation_manager` ⇒ 3 route
# /v105/escalation/* luôn 503 "Escalation manager is unavailable" (standard/full)
# và 404 ở core (nhóm `control` không mount) — kênh human approve/reject
# dead-man timer chết ở MỌI profile.
#
# Pin ở đây:
#   * singleton production (helpers.get_judge — đúng hàm lifespan gọi lúc
#     startup) mang EscalationManager thật, state dưới SCP_DATA_DIR;
#   * route 200 với admin auth, 401 khi thiếu/sai token;
#   * posture production (SCP_PRODUCTION_MODE=1, không SCP_API_PROFILE ⇒ core):
#     KHÔNG có SCP_ENABLED_GROUPS ⇒ route vẫn 404 (không mở bề mặt lặng lẽ);
#     operator đặt SCP_ENABLED_GROUPS=control ⇒ route mount, 401 không token,
#     200 có token;
#   * docker-compose.full.yml backend passthrough SCP_ENABLED_GROUPS với default
#     RỖNG (không tự bật nhóm nào) và KHÔNG passthrough SCP_API_PROFILE.
#
# Zero-mock: EscalationManager thật, state file thật trên tmp; posture core chạy
# trong subprocess riêng vì route được mount lúc import scp.api_server.
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = ROOT / "docker-compose.full.yml"
_ESC_STATUS = "/v105/escalation/status"


@pytest.fixture
def live_judge(tmp_path, monkeypatch):
    """Singleton production mới (helpers.get_judge) với data dir cô lập."""
    from scp.api_server_parts import helpers

    monkeypatch.setenv("SCP_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(helpers, "_judge", None)
    judge = helpers.get_judge()
    yield judge
    manager = getattr(judge, "escalation_manager", None)
    for timer in list(getattr(manager, "_timers", {}).values()):
        timer.cancel()


def _admin_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {os.environ['SCP_ADMIN_KEY']}"}


def test_production_singleton_carries_real_escalation_manager(live_judge, tmp_path):
    from scp.security.escalation import EscalationManager

    manager = getattr(live_judge, "escalation_manager", None)
    assert isinstance(manager, EscalationManager), type(manager)
    # State nằm dưới SCP_DATA_DIR đã cấu hình, không phải "data/" cứng.
    assert manager.escalation_state_path.parent == tmp_path
    assert manager.escalation_log_path.parent == tmp_path


def test_escalation_routes_live_with_admin_and_closed_without(live_judge):
    from fastapi.testclient import TestClient

    from scp.api_server import app

    client = TestClient(app)
    assert client.get(_ESC_STATUS).status_code == 401
    assert client.get(_ESC_STATUS, headers={"Authorization": "Bearer wrong-token"}).status_code == 401
    response = client.get(_ESC_STATUS, headers=_admin_headers())
    assert response.status_code == 200, response.text
    assert response.json()["active"] == []


_OPERATOR_PASSWORD = "c1-esc-operator-password-0123456789abcdef"


def _operator_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {_OPERATOR_PASSWORD}"}


def test_threat_on_live_manager_visible_and_operator_ack_never_cancels_safe_outcome(live_judge, tmp_path, monkeypatch):
    # [esc-auto slice 3 · owner 2026-10-30 "đã tự động thì ai duyệt cho bạn"] Hợp đồng c1 cũ
    # ("admin approve ⇒ hủy timer") bị thay: KHÔNG còn bước duyệt; SCP_ADMIN_KEY là credential
    # MÁY (brain-console/dashboard giữ) ⇒ 403; operator chỉ ghi audit, safe outcome giữ nguyên.
    from fastapi.testclient import TestClient

    from scp.api_server import app

    monkeypatch.delenv("SCP_AUTH_PASSWORD_FILE", raising=False)
    monkeypatch.setenv("SCP_AUTH_PASSWORD", _OPERATOR_PASSWORD)
    manager = live_judge.escalation_manager
    threat_id = "c1-esc-approve"
    manager.on_threat_detected({"id": threat_id, "type": "probe", "severity": "critical"})
    assert threat_id in manager._timers  # dead-man switch tự armed
    client = TestClient(app)
    status = client.get(_ESC_STATUS, headers=_admin_headers()).json()
    assert threat_id in {str(item.get("threat_id")) for item in status["active"]}
    # Không token ⇒ 401; credential máy ⇒ 403; cả hai không chạm được deadline.
    denied = client.post(f"/v105/escalation/{threat_id}/approve", json={"action": "default"})
    assert denied.status_code == 401
    machine = client.post(f"/v105/escalation/{threat_id}/approve", json={"action": "default"}, headers=_admin_headers())
    assert machine.status_code == 403
    assert threat_id in manager._timers
    acked = client.post(
        f"/v105/escalation/{threat_id}/approve",
        json={"action": "default"},
        headers=_operator_headers(),
    )
    assert acked.status_code == 200, acked.text
    assert acked.json()["status"] == "recorded" and acked.json()["effect"] == "audit_only"
    assert threat_id in manager._timers, "operator ack must never cancel the dead-man safe outcome"
    # Ghi chú audit của operator được persist vào state file thật, principal thật.
    persisted = json.loads((tmp_path / "escalation_state.json").read_text(encoding="utf-8"))
    assert persisted["active"][threat_id]["status"] == "armed"
    assert any(h.get("action") == "operator_ack" and h.get("actor") == "operator:password" for h in persisted["history"])


def test_reject_unknown_threat_is_404_not_503(live_judge, monkeypatch):
    from fastapi.testclient import TestClient

    from scp.api_server import app

    monkeypatch.delenv("SCP_AUTH_PASSWORD_FILE", raising=False)
    monkeypatch.setenv("SCP_AUTH_PASSWORD", _OPERATOR_PASSWORD)
    client = TestClient(app)
    # [esc-auto slice 3] credential máy ⇒ 403 trước khi lộ thông tin tồn tại; operator ⇒ 404.
    assert client.post("/v105/escalation/does-not-exist/reject", headers=_admin_headers()).status_code == 403
    response = client.post("/v105/escalation/does-not-exist/reject", headers=_operator_headers())
    assert response.status_code == 404, response.text


_CORE_PROBE = r"""
import json, os
from scp.api_server import app, _API_PROFILE
def _paths(routes):
    out = set()
    for r in routes:
        p = getattr(r, 'path', None)
        if isinstance(p, str): out.add(p)
        nested = getattr(r, 'original_router', None)
        if nested is not None: out |= _paths(getattr(nested, 'routes', []))
    return out
result = {'profile': _API_PROFILE, 'mounted': '/v105/escalation/status' in _paths(app.routes)}
if result['mounted']:
    from fastapi.testclient import TestClient
    from scp.api_server_parts import helpers
    client = TestClient(app)
    result['no_auth'] = client.get('/v105/escalation/status').status_code
    helpers.get_judge()
    hdr = {'Authorization': 'Bearer ' + os.environ['SCP_ADMIN_KEY']}
    resp = client.get('/v105/escalation/status', headers=hdr)
    result['with_auth'] = resp.status_code
    result['manager'] = type(getattr(helpers._judge, 'escalation_manager', None)).__name__
print('C1ESC_JSON=' + json.dumps(result), flush=True)
os._exit(0)
"""


def _core_posture(tmp_path: Path, enabled_groups: str | None) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in {"SCP_API_PROFILE", "SCP_ENABLED_GROUPS", "SCP_DISABLED_GROUPS"}}
    env.update(
        {
            "SCP_PRODUCTION_MODE": "1",
            "SCP_SKIP_STARTUP_GATE": "1",
            "SCP_EGRESS_MODE": "deny",
            "SCP_DATA_DIR": str(tmp_path),
            "SCP_JWT_SECRET": "c1-escalation-test-only-jwt-secret-0123456789",
            "SCP_ADMIN_KEY": "c1-escalation-test-only-admin-key-0123456789",
            "PYTHONUTF8": "1",
        }
    )
    if enabled_groups is not None:
        env["SCP_ENABLED_GROUPS"] = enabled_groups
    completed = subprocess.run(
        [sys.executable, "-c", _CORE_PROBE],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=240,
        check=False,
    )
    marker = next((ln for ln in completed.stdout.splitlines() if ln.startswith("C1ESC_JSON=")), None)
    assert marker is not None, (completed.returncode, completed.stdout[-3000:], completed.stderr[-3000:])
    return json.loads(marker.split("=", 1)[1])


def test_production_core_posture_without_opt_in_keeps_escalation_unmounted(tmp_path):
    result = _core_posture(tmp_path, None)
    assert result["profile"] == "core"
    assert result["mounted"] is False, result


def test_production_core_posture_with_control_opt_in_serves_live_manager(tmp_path):
    result = _core_posture(tmp_path, "control")
    assert result["profile"] == "core"
    assert result["mounted"] is True, result
    assert result["no_auth"] == 401, result
    assert result["with_auth"] == 200, result
    assert result["manager"] == "EscalationManager", result


def test_compose_backend_passthrough_route_opt_in_defaults_empty():
    import yaml

    compose = yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))
    backend_env = compose["services"]["backend"]["environment"]
    # Operator bật được nhóm `control` qua .env, nhưng mặc định KHÔNG bật gì.
    assert backend_env.get("SCP_ENABLED_GROUPS") == "${SCP_ENABLED_GROUPS:-}", backend_env.get("SCP_ENABLED_GROUPS")
    # Cố ý KHÔNG pass-through SCP_API_PROFILE: một giá trị cũ trong .env operator
    # (vd. `full`) sẽ lặng lẽ mở toàn bộ bề mặt admin ở production. Muốn đổi điều
    # này là quyết định owner — sửa test có chủ đích, không lách.
    assert "SCP_API_PROFILE" not in backend_env, backend_env.get("SCP_API_PROFILE")


def test_control_group_is_not_silently_in_core_minimum():
    from scp.api.route_profile import GROUP_MINIMUM, route_group_enabled

    # Đưa escalation vào core là quyết định của owner — không phải của lane này.
    assert GROUP_MINIMUM["control"] == "standard"
    assert route_group_enabled("control", "core", {}) is False
    assert route_group_enabled("control", "core", {"SCP_ENABLED_GROUPS": "control"}) is True
