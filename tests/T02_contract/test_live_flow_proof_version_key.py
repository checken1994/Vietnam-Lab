"""[LIVE-FLOW-PROOF-KEY 2026-09-26] tools/live_flow_proof.py phải in key thật.

Live sweep: tool từng đọc service_identity['version'] — key này KHÔNG tồn tại
(service_identity: service_name/mode/host/configured_port/pid/commit) nên
luôn print rỗng. 'version' nằm TOP-LEVEL trong /health body. Test pin đúng vị
trí key mà tool dựa vào.

[SEC-FIX /health-identity 2026-09-26] 'argv' đã bị LOẠI khỏi service_identity:
/health không auth, từng echo toàn bộ command line — bất kỳ secret nào từng
được truyền qua CLI sẽ lộ. Contract mới: argv KHÔNG được xuất hiện lại.

[F-02 audit-r2 2026-10-01] 'config_hash' cũng đã bị LOẠI (sha256 của .env
trong body không auth = oracle crack offline). Pin cũ từng assert PRESENCE
của config_hash trong expected set — classify PRODUCT_FAIL (pin hành vi sai),
đã siết: key này phải VẮNG MẶT.
"""
from __future__ import annotations

import asyncio


def _health_payload() -> dict:
    """[BUYER-05 2026-10-30] Bản /health ĐẦY ĐỦ (version/service_identity đủ
    key) giờ chỉ trả cho Bearer admin; gọi trực tiếp ``api_server.health()``
    không có Request ⇒ bản rút gọn (fail-closed). Pin cũ dựa vào bản đầy đủ
    không auth — đổi sang TestClient + Bearer admin throwaway; mọi assertion
    contract (version top-level, không argv/config_hash) giữ nguyên.
    """
    import os

    os.environ.setdefault("SCP_CAPABILITY_SECRET", "test-capability-secret-for-automated-suites-only-32bytes")
    from fastapi.testclient import TestClient

    from scp import api_server
    from scp.security import auth as _auth

    admin_key = "buyer05-live-flow-proof-throwaway-admin-32b"
    old = {k: os.environ.get(k) for k in ("SCP_ADMIN_KEY", "SCP_ADMIN_KEY_FILE")}
    os.environ["SCP_ADMIN_KEY"] = admin_key
    os.environ.pop("SCP_ADMIN_KEY_FILE", None)
    try:
        with _auth._auth_failures_lock:
            _auth._auth_failures.clear()
        response = TestClient(api_server.app).get(
            "/health", headers={"Authorization": f"Bearer {admin_key}"}
        )
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    assert response.status_code == 200
    return response.json()


def test_unauth_health_has_no_version_or_full_identity():
    """[BUYER-05] Bản không auth: không version, identity không commit/pid/host."""
    body = asyncio.run(__import__("scp.api_server", fromlist=["health"]).health())
    assert "version" not in body
    assert not {"commit", "pid", "host"} & set(body["service_identity"])
    assert "argv" not in body["service_identity"]


def test_version_is_top_level_in_health_body():
    payload = _health_payload()
    assert payload.get("version"), "/health body phải có version top-level (không rỗng)"


def test_service_identity_has_no_version_key():
    """Pin nguyên nhân gốc: service_identity KHÔNG có 'version' — tool đọc
    nhầm key này từng in chuỗi rỗng.

    [F-02 audit-r2] 'config_hash' cũng phải VẮNG MẶT — identity của endpoint
    không auth không được chứa hash của file secret (.env).
    """
    payload = _health_payload()
    assert "version" not in payload["service_identity"]
    identity = payload["service_identity"]
    expected = {"service_name", "mode", "host", "configured_port", "pid", "commit"}
    assert expected.issubset(set(identity.keys()))
    assert "config_hash" not in identity, (
        "config_hash (sha256 của .env) không được quay lại identity unauth"
    )


def test_service_identity_never_exposes_argv():
    """[SEC-FIX /health-identity 2026-09-26] service_identity KHÔNG được chứa
    'argv': /health là endpoint không auth — raw command line (có thể chứa
    secret truyền qua CLI) tuyệt đối không được phản hồi về client."""
    payload = _health_payload()
    assert "argv" not in payload["service_identity"]


def test_live_flow_proof_source_reads_top_level_version():
    """Chính tool (không phải bản chép tay) phải đọc health['version'] —
    kiểm tra bằng compile + tham chiếu dòng lệnh print thực tế trong source."""
    import importlib.util
    import py_compile
    from pathlib import Path

    tool_path = Path(__file__).resolve().parents[2] / "tools" / "live_flow_proof.py"
    # [Hermetic fix 2026-10-25] py_compile mặc định ghi .pyc cạnh source
    # (tools/__pycache__) — trong audit image /app/tools là root-owned read-only
    # (runner chạy user 10001) ⇒ PermissionError không liên quan contract.
    # cfile → tmp_path giữ nguyên ý nghĩa (compile thật, fail nếu syntax hỏng),
    # bỏ side-effect ghi vào source tree.
    import tempfile

    with tempfile.TemporaryDirectory() as _tmp_compile:
        py_compile.compile(str(tool_path), cfile=str(Path(_tmp_compile) / "live_flow_proof.pyc"), doraise=True)
    spec = importlib.util.spec_from_file_location("scp_live_flow_proof_under_test", tool_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    source = __import__("inspect").getsource(module.main)
    assert 'health.get("version"' in source, "tool phải đọc version từ /health body top-level"
    assert 'sid.get("version"' not in source, "không được đọc version từ service_identity (key không tồn tại)"
