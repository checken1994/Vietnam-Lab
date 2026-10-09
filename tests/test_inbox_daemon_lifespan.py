"""
Integration tests for InboxDaemon lifecycle in api_server.py Lifespan.
=======================================================================
Kiểm chứng:
  1. Khi SCP_INBOX_DAEMON_ENABLED=1 (mặc định), khởi động máy chủ qua lifespan sẽ khởi động InboxDaemon.
  2. Khi máy chủ tắt qua lifespan, InboxDaemon dừng an toàn và giải phóng tài nguyên.
  3. Khi SCP_INBOX_DAEMON_ENABLED=0, InboxDaemon không được khởi động.
  4. Khả năng chịu lỗi: Nếu InboxDaemon gặp lỗi khi khởi động, api_server vẫn khởi động bình thường không crash.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from scp.api_server import app, get_inbox_daemon, stop_inbox_daemon
from scp.knowledge.inbox_daemon import InboxDaemon


@pytest.fixture(autouse=True)
def clean_daemon():
    """Bảo đảm daemon được dọn dẹp trước và sau mỗi test."""
    stop_inbox_daemon()
    yield
    stop_inbox_daemon()


@pytest.mark.parametrize("enabled_val", ["1", "true", "yes", "True", "YES"])
def test_inbox_daemon_lifespan_startup_and_shutdown(monkeypatch, enabled_val):
    """Khi SCP_INBOX_DAEMON_ENABLED mang giá trị truthy (1, true, yes), lifespan khởi động và dừng InboxDaemon."""
    monkeypatch.setenv("SCP_INBOX_DAEMON_ENABLED", enabled_val)
    monkeypatch.setenv("SCP_SKIP_STARTUP_GATE", "1")

    with TestClient(app) as client:
        resp = client.get("/health")
        assert resp.status_code == 200

        daemon = get_inbox_daemon()
        assert daemon is not None
        assert isinstance(daemon, InboxDaemon)
        assert daemon.is_running is True
        assert getattr(app.state, "inbox_daemon", None) is daemon

    # Sau khi thoát khỏi context lifespan, daemon phải được dừng
    assert get_inbox_daemon() is None
    assert daemon.is_running is False


@pytest.mark.parametrize("disabled_val", ["0", "false", "no", "False", "NO", "disabled"])
def test_inbox_daemon_lifespan_disabled_by_env(monkeypatch, disabled_val):
    """Khi SCP_INBOX_DAEMON_ENABLED mang giá trị falsy, lifespan không khởi động InboxDaemon."""
    monkeypatch.setenv("SCP_INBOX_DAEMON_ENABLED", disabled_val)
    monkeypatch.setenv("SCP_SKIP_STARTUP_GATE", "1")

    with TestClient(app) as client:
        resp = client.get("/health")
        assert resp.status_code == 200

        daemon = get_inbox_daemon()
        assert daemon is None
        assert getattr(app.state, "inbox_daemon", None) is None


def test_inbox_daemon_lifespan_error_handled_gracefully(monkeypatch):
    """Nếu start_inbox_daemon văng lỗi môi trường, lifespan bắt try/except và không làm sập server."""
    monkeypatch.setenv("SCP_INBOX_DAEMON_ENABLED", "1")
    monkeypatch.setenv("SCP_SKIP_STARTUP_GATE", "1")

    def crashing_starter(*args, **kwargs):
        raise RuntimeError("Simulated environment permission failure in inbox daemon")

    import scp.knowledge.inbox_daemon as id_mod
    monkeypatch.setattr(id_mod, "start_inbox_daemon", crashing_starter)

    with TestClient(app) as client:
        resp = client.get("/health")
        assert resp.status_code == 200
        # Server vẫn hoạt động bình thường và state an toàn là None
        assert get_inbox_daemon() is None
        assert getattr(app.state, "inbox_daemon", None) is None
