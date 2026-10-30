# [esc-auto slice 4 · 2026-10-30] Danh tính client cho rate limit: tin X-Forwarded-For
# CHỈ khi SCP khai báo proxy tin cậy; người lạ không khoá được admin.
#
# Trước fix (72288551): `python -m scp` gọi uvicorn.run không có proxy_headers ⇒ uvicorn 0.44
# mặc định proxy_headers=True + forwarded_allow_ips="127.0.0.1" (tin cậy NGẦM). Hệ quả đo được:
#   * mọi peer loopback đổi được request.client bằng XFF ⇒ xoay XFF mỗi request là né bộ
#     chặn brute-force 5 lỗi/60s của verify_admin (đỏ: lần thứ 6 vẫn 401, không 429);
#   * per-client sau Caddy (TLS overlay) chỉ đúng nhờ default ngầm đó, không ai khai báo.
# Pin: mặc định KHÔNG tin XFF; SCP_TRUSTED_PROXY_IPS khai báo proxy (wildcard bị bỏ);
# sau proxy khai báo, người lạ (kể cả chèn hop giả) không khoá được admin ở verify_admin
# lẫn bucket IP của tier ask.
#
# Test chạy uvicorn THẬT (thread, cổng ngẫu nhiên, loopback) với đúng kwargs mà entrypoint
# production (`scp.__main__.main`) truyền cho uvicorn.run.
from __future__ import annotations

import asyncio
import random
import socket
import sys
import threading
import time
from contextlib import contextmanager

import httpx
import pytest
from fastapi import Depends, FastAPI, Request  # module-level: string annotations resolve here

SECRETS = {
    "SCP_AUTH_PASSWORD": "esc-auto-s4-operator-password-0123456789",
    "SCP_SCHEDULER_ADMIN_TOKEN": "esc-auto-s4-scheduler-machine-token-012345",
    "SCP_AUTH_TOKEN_SECRET": "esc-auto-s4-token-secret-machine-01234567",
    "SCP_ADMIN_KEY": "esc-auto-s4-admin-key-machine-0123456789ab",
}
ADMIN = SECRETS["SCP_AUTH_PASSWORD"]


@pytest.fixture()
def auth_env(monkeypatch):
    from scp.security import auth

    for key in SECRETS:
        monkeypatch.delenv(f"{key}_FILE", raising=False)
    for key, value in SECRETS.items():
        monkeypatch.setenv(key, value)
    with auth._auth_failures_lock:
        auth._auth_failures.clear()
    yield
    with auth._auth_failures_lock:
        auth._auth_failures.clear()


def _production_launch_kwargs(monkeypatch, tmp_path, trusted: str | None, forwarded_env: str | None = None) -> dict:
    """Run the real production entrypoint with uvicorn.run captured."""
    import uvicorn

    monkeypatch.setenv("SCP_ENV_FILE", str(tmp_path / "absent.env"))  # never load a real .env
    if trusted is None:
        monkeypatch.delenv("SCP_TRUSTED_PROXY_IPS", raising=False)
    else:
        monkeypatch.setenv("SCP_TRUSTED_PROXY_IPS", trusted)
    if forwarded_env is None:
        monkeypatch.delenv("FORWARDED_ALLOW_IPS", raising=False)
    else:
        monkeypatch.setenv("FORWARDED_ALLOW_IPS", forwarded_env)
    monkeypatch.setenv("SCP_PORT", "18999")
    monkeypatch.setattr(sys, "argv", ["scp"])
    captured: dict = {}
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: captured.update(k))
    monkeypatch.setattr(asyncio, "set_event_loop_policy", lambda *_a: None)
    import scp.__main__ as entry

    entry.main()
    assert captured, "entrypoint did not call uvicorn.run"
    return captured


def _proxy_kwargs(captured: dict) -> dict:
    return {k: captured[k] for k in ("proxy_headers", "forwarded_allow_ips") if k in captured}


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@contextmanager
def _serve(app, proxy_kwargs: dict):
    import uvicorn

    config = uvicorn.Config(app, host="127.0.0.1", port=_free_port(), log_level="warning",
                            lifespan="off", access_log=False, **proxy_kwargs)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 15
    while not server.started and time.time() < deadline:
        time.sleep(0.02)
    assert server.started, "uvicorn did not start"
    try:
        yield f"http://127.0.0.1:{config.port}"
    finally:
        server.should_exit = True
        thread.join(10)


def _admin_app():
    from scp.security.auth import verify_admin

    app = FastAPI()

    @app.get("/whoami")
    def whoami(request: Request):
        return {"client": request.client.host if request.client else None}

    @app.get("/admin", dependencies=[Depends(verify_admin)])
    def admin():
        return {"ok": True}

    return app


def _ip(prefix: str) -> str:
    return f"{prefix}.{random.randint(1, 254)}"


def test_default_launch_never_trusts_forwarded_for(monkeypatch, tmp_path):
    captured = _production_launch_kwargs(monkeypatch, tmp_path, trusted=None, forwarded_env="*")
    assert captured.get("proxy_headers", True) is False, captured
    # uvicorn đọc FORWARDED_ALLOW_IPS khi forwarded_allow_ips=None ⇒ phải luôn tường minh.
    assert captured.get("forwarded_allow_ips") == "", captured


def test_declared_trust_is_explicit_and_wildcards_are_refused(monkeypatch, tmp_path):
    captured = _production_launch_kwargs(monkeypatch, tmp_path, "127.0.0.1, 10.0.0.0/8 ,bogus,*,0.0.0.0/0,::/0")
    assert captured["proxy_headers"] is True
    assert captured["forwarded_allow_ips"] == "127.0.0.1,10.0.0.0/8"
    only_wild = _production_launch_kwargs(monkeypatch, tmp_path, "*, ::/0, 0.0.0.0/0")
    assert only_wild["proxy_headers"] is False and only_wild["forwarded_allow_ips"] == ""


def test_spoofed_forwarded_for_cannot_rotate_identity_by_default(monkeypatch, tmp_path, auth_env):
    proxy = _proxy_kwargs(_production_launch_kwargs(monkeypatch, tmp_path, trusted=None))
    with _serve(_admin_app(), proxy) as base, httpx.Client(base_url=base, timeout=10) as client:
        assert client.get("/whoami", headers={"X-Forwarded-For": "203.0.113.9"}).json()["client"] == "127.0.0.1"
        codes = [
            client.get("/admin", headers={"Authorization": "Bearer wrong", "X-Forwarded-For": f"203.0.113.{i}"}).status_code
            for i in range(1, 7)
        ]
    assert codes[:5] == [401] * 5 and codes[5] == 429, codes


def test_behind_declared_proxy_stranger_cannot_lock_out_admin(monkeypatch, tmp_path, auth_env):
    proxy = _proxy_kwargs(_production_launch_kwargs(monkeypatch, tmp_path, trusted="127.0.0.1"))
    stranger, admin_ip = _ip("203.0.113"), _ip("198.51.100")
    with _serve(_admin_app(), proxy) as base, httpx.Client(base_url=base, timeout=10) as client:
        bad = [client.get("/admin", headers={"Authorization": "Bearer guess", "X-Forwarded-For": stranger}).status_code
               for _ in range(6)]
        assert bad[5] == 429, bad
        ok = client.get("/admin", headers={"Authorization": f"Bearer {ADMIN}", "X-Forwarded-For": admin_ip})
        assert ok.status_code == 200, ok.text
        # Người lạ chèn hop giả (IP admin) phía trái: danh tính = hop không tin cậy ngoài cùng phải.
        forged = f"{admin_ip}, {stranger}"
        assert client.get("/whoami", headers={"X-Forwarded-For": forged}).json()["client"] == stranger
        still = client.get("/admin", headers={"Authorization": "Bearer guess", "X-Forwarded-For": forged})
        assert still.status_code == 429
        assert client.get("/admin", headers={"Authorization": f"Bearer {ADMIN}", "X-Forwarded-For": admin_ip}).status_code == 200


def test_ask_tier_ip_bucket_is_per_client_behind_declared_proxy(monkeypatch, tmp_path):
    from scp.api.rate_limiting import rate_limit_middleware

    monkeypatch.setenv("SCP_RATE_LIMIT_ENABLED", "1")
    monkeypatch.setenv("SCP_RATE_LIMIT_ASK", "3/minute")
    app = FastAPI()
    app.middleware("http")(rate_limit_middleware)

    @app.post("/ask")
    def ask():
        return {"ok": True}

    proxy = _proxy_kwargs(_production_launch_kwargs(monkeypatch, tmp_path, trusted="127.0.0.1"))
    stranger, admin_ip = _ip("203.0.113"), _ip("198.51.100")
    with _serve(app, proxy) as base, httpx.Client(base_url=base, timeout=10) as client:
        flood = [client.post("/ask", headers={"Authorization": f"Bearer rot-{i}", "X-Forwarded-For": stranger}).status_code
                 for i in range(4)]
        assert flood[3] == 429, flood
        ok = client.post("/ask", headers={"Authorization": f"Bearer {ADMIN}", "X-Forwarded-For": admin_ip})
        assert ok.status_code == 200, ok.text
