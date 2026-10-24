# [HC-40 · 2026-10-23] Kill-switch boundary cho 2 đường kích hoạt audit/fix.
#
# Trước HC-40: KHÔNG đường nào trong 2 đường (backend scheduled job
# 'deep_audit_scheduler' + loop-scheduler → POST /v105/autofix/run-audit)
# kiểm kill switch — kill switch chỉ chặn /ask + hardware execution.
#
# Sau HC-40 (fail-closed, 2 lớp):
#   Lớp 1 (điểm chung): scp/autofix/runner.py::run_once — mọi apply-mode cycle
#   của CẢ 2 đường hội tụ ở đây; kill switch engaged → summary rỗng
#   source='kill_switch_blocked', không scan, không fix.
#   Lớp 2 (endpoint): POST /v105/autofix/run-audit → 503 kill_switch_engaged
#   (kể cả observe/deterministic-worker mode).
#
# Doc boundary: docs/architecture/AUTOFIX_TRIGGERS.md
from __future__ import annotations

import os

os.environ.setdefault(
    "SCP_JWT_SECRET", "test-only-jwt-secret-0123456789abcdef-not-for-production"
)
os.environ.setdefault("SCP_ADMIN_KEY", "test-only-admin-key-0123456789abcdef")

from fastapi.testclient import TestClient

from scp.api_server import app
from scp.autofix.runner import run_once


def test_run_once_blocked_when_kill_switch_env_engaged(monkeypatch):
    """Lớp 1 (điểm chung T1+T2): env SCP_KILL_SWITCH=1 → run_once REFUSED.

    Fail-closed: trả summary rỗng (contract dict của caller vẫn đọc được),
    KHÔNG scan (không chạm AST scanner), KHÔNG fix.
    """
    monkeypatch.setenv("SCP_KILL_SWITCH", "1")
    result = run_once(bugs=[], max_bugs=1)
    assert result["source"] == "kill_switch_blocked", result
    assert result["processed"] == 0
    assert result["fixed"] == 0
    assert result["details"] == []
    assert result["skipped"] == 0


def test_run_once_not_blocked_without_kill_switch(monkeypatch):
    """Negative control: kill switch TẮT → run_once chạy bình thường (không
    over-fire). bugs=[] explicit → source='explicit', 0 processed, không bị
    gắn nhãn kill_switch_blocked."""
    monkeypatch.delenv("SCP_KILL_SWITCH", raising=False)
    result = run_once(bugs=[], max_bugs=1)
    assert result["source"] == "explicit", result
    assert result["processed"] == 0
    assert "kill_switch_blocked" != result["source"]


def test_run_once_blocked_via_file_flag_channel(monkeypatch, tmp_path):
    """Kênh file flag của kill switch cũng phải chặn — cùng authority
    scp.security.kill_switch (is_engaged đọc qua default_kill_switch_path)."""
    from scp.security import kill_switch as ks_mod

    flag = tmp_path / "KILL_SWITCH"
    flag.write_text("engaged", encoding="utf-8")
    monkeypatch.setattr(ks_mod, "_flag_path", lambda: flag)
    monkeypatch.delenv("SCP_KILL_SWITCH", raising=False)
    result = run_once(bugs=[], max_bugs=1)
    assert result["source"] == "kill_switch_blocked", result
    assert result["processed"] == 0


def test_run_audit_endpoint_503_when_kill_switch_engaged(monkeypatch):
    """Lớp 2 (endpoint T2 — đường loop-scheduler): kill switch engaged →
    POST /v105/autofix/run-audit trả 503 kill_switch_engaged, TRƯỚC khi bất kỳ
    scan/audit nào bắt đầu."""
    monkeypatch.setenv("SCP_KILL_SWITCH", "1")
    admin_token = os.environ["SCP_ADMIN_KEY"]
    headers = {"Authorization": f"Bearer {admin_token}"}
    with TestClient(app) as client:
        resp = client.post(
            "/v105/autofix/run-audit",
            json={"mode": "observe", "max_bugs": 1},
            headers=headers,
        )
        assert resp.status_code == 503, (
            f"kill switch engaged phải chặn endpoint, got {resp.status_code}: {resp.text[:200]}"
        )
        assert resp.json()["detail"] == "kill_switch_engaged"
