"""[FU-ADMINKEY 2026-10-29] POST /v3/hands/confirmations tôn trọng kênh SCP_ADMIN_KEY_FILE.

Hiện trạng trước FU-ADMINKEY: ``hands_record_confirmation`` đọc admin key bằng
``os.environ.get("SCP_ADMIN_KEY", "")`` — bỏ qua kênh ``SCP_ADMIN_KEY_FILE``
(docker secrets / K8s mount) dù ``scp.security.secret_registry`` khai báo
``SCP_ADMIN_KEY`` với ``file_variant=True`` (HC-08) và liệt kê chính
``scp.api.routes.hands_routes`` trong ``consumers``. Hệ quả: deploy chỉ cấp
admin key qua ``_FILE`` → endpoint LUÔN 403 (mất khả dụng, vẫn fail-closed —
không phải bypass). Sửa: đọc qua ``secret_registry.resolve("SCP_ADMIN_KEY")``,
cùng pattern ``/auth/token`` (R17) và ``jwt_guard.verify_api_key`` (P3-06).

Contract pin (docstring scp/security/secret_registry.py, HC-08):
  * ``{name}_FILE`` set (khác rỗng) → FILE thắng env.
  * File hỏng/không tồn tại/rỗng → ``None`` (fail-closed, KHÔNG fallback env).
  * Không cấu hình kênh nào → 403 (fail-closed giữ nguyên).
  * Chỉ-env giữ hành vi cũ.

Zero-mock (FA-04): FastAPI TestClient thật trên ``hands_routes.router`` thật,
``HumanConfirmationStore`` thật trỏ vào tmp_path, request ledger thật trỏ vào
tmp_path; chỉ dùng tmp_path + monkeypatch.setenv/delenv/setattr để cô lập.
Secret test sinh ngẫu nhiên mỗi lần chạy và KHÔNG BAO GIỜ được in ra
(assert message không chứa giá trị).
"""
from __future__ import annotations

import logging
import secrets
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from scp.api.routes import hands_routes
from scp.security import confirmation_store
from scp.security.confirmation_store import HumanConfirmationStore

_ENDPOINT = "/v3/hands/confirmations"
_DENY_DETAIL_MARKER = "Operator confirmation requires admin authority"
_PAYLOAD = {"action": "pc.write_file", "target": "workspace/fu_adminkey.py"}


def _random_secret() -> str:
    # ASCII-only (hmac.compare_digest trên str yêu cầu ASCII); ≥ min_length=8 của registry.
    return secrets.token_urlsafe(24)


@pytest.fixture()
def harness(monkeypatch, tmp_path: Path):
    """App thật + store/ledger thật trong tmp_path; dọn mọi kênh admin key.

    conftest gốc setdefault SCP_ADMIN_KEY cho cả suite, và .env host có thể có
    giá trị thật → delenv cả env lẫn _FILE để mỗi test tự khai báo kênh của nó.
    """
    monkeypatch.delenv("SCP_ADMIN_KEY", raising=False)
    monkeypatch.delenv("SCP_ADMIN_KEY_FILE", raising=False)
    pc_token = _random_secret()
    monkeypatch.setenv("SCP_PC_CONTROLLER_TOKEN", pc_token)
    monkeypatch.setenv("SCP_DATA_DIR", str(tmp_path / "data"))

    # get_confirmation_store() là singleton process-wide: bind một store thật
    # trỏ vào tmp_path để side-effect (hoặc sự vắng mặt của nó) quan sát được.
    store = HumanConfirmationStore(store_path=tmp_path / "confirmations.jsonl")
    monkeypatch.setattr(confirmation_store, "_GLOBAL_CONFIRMATION_STORE", store)
    # traced_request đã capture ledger instance lúc import → trỏ path vào tmp.
    monkeypatch.setattr(hands_routes._HANDS_ROUTES_LEDGER, "path", tmp_path / "request_runs.jsonl")

    app = FastAPI()
    app.include_router(hands_routes.router)
    return TestClient(app), pc_token, store


def _post(client: TestClient, pc_token: str, admin_header: str | None):
    headers = {"X-SCP-PC-TOKEN": pc_token}
    if admin_header is not None:
        headers["X-SCP-ADMIN-KEY"] = admin_header
    return client.post(_ENDPOINT, json=_PAYLOAD, headers=headers)


def _assert_denied(resp, store: HumanConfirmationStore, case: str) -> None:
    assert resp.status_code == 403, f"[{case}] phải 403 fail-closed, got {resp.status_code}"
    assert _DENY_DETAIL_MARKER in resp.json()["detail"], f"[{case}] 403 phải đến từ admin gate"
    assert store._memory_cache == {}, f"[{case}] 403 không được ghi confirmation nào"
    assert not store.store_path.exists(), f"[{case}] 403 không được tạo file confirmation"


def _assert_accepted(resp, store: HumanConfirmationStore, case: str) -> str:
    assert resp.status_code == 200, f"[{case}] admin key đúng phải được nhận, got {resp.status_code}"
    body = resp.json()
    assert body["success"] is True
    cid = body["confirmation_id"]
    assert cid.startswith("conf-")
    # Side-effect thật: record nằm trong store và được persist xuống JSONL.
    assert store._memory_cache[cid]["action"] == _PAYLOAD["action"]
    assert store._memory_cache[cid]["user"] == "admin_operator"
    assert cid in store.store_path.read_text(encoding="utf-8")
    return cid


def _write_key_file(tmp_path: Path, name: str, value: str) -> Path:
    path = tmp_path / name
    path.write_text(value + "\n", encoding="utf-8")
    return path


class TestHandsConfirmationAdminKeyFileChannel:
    def test_a_file_only_admin_key_with_correct_header_is_accepted(self, harness, tmp_path, monkeypatch):
        """(a) Chỉ SCP_ADMIN_KEY_FILE + header đúng → 200 (trước fix: luôn 403)."""
        client, pc_token, store = harness
        file_key = _random_secret()
        monkeypatch.setenv("SCP_ADMIN_KEY_FILE", str(_write_key_file(tmp_path, "admin-key", file_key)))

        resp = _post(client, pc_token, file_key)
        _assert_accepted(resp, store, "a/file-only+correct")
        assert file_key not in resp.text, "Secrecy: response không được chứa admin key"

    def test_b_file_only_admin_key_with_wrong_or_missing_header_is_403(self, harness, tmp_path, monkeypatch):
        """(b) Chỉ _FILE + header sai/thiếu/rỗng → 403, không ghi confirmation."""
        client, pc_token, store = harness
        file_key = _random_secret()
        monkeypatch.setenv("SCP_ADMIN_KEY_FILE", str(_write_key_file(tmp_path, "admin-key", file_key)))

        _assert_denied(_post(client, pc_token, _random_secret()), store, "b/wrong-header")
        _assert_denied(_post(client, pc_token, None), store, "b/missing-header")
        _assert_denied(_post(client, pc_token, ""), store, "b/empty-header")

    def test_c_unconfigured_admin_key_is_403_fail_closed(self, harness):
        """(c) Không cấu hình kênh nào → 403 với mọi header (fail-closed giữ nguyên).

        Header rỗng là ca biên quan trọng: nếu bỏ kiểm rỗng thì
        compare_digest("", "") == True → bypass.
        """
        client, pc_token, store = harness

        _assert_denied(_post(client, pc_token, None), store, "c/missing-header")
        _assert_denied(_post(client, pc_token, ""), store, "c/empty-header")
        _assert_denied(_post(client, pc_token, _random_secret()), store, "c/random-header")

    def test_d_env_only_admin_key_behaves_as_before(self, harness, monkeypatch):
        """(d) Control: chỉ-env + header đúng → 200; header sai → 403 (như trước fix)."""
        client, pc_token, store = harness
        env_key = _random_secret()
        monkeypatch.setenv("SCP_ADMIN_KEY", env_key)

        _assert_denied(_post(client, pc_token, _random_secret()), store, "d/env-only+wrong")
        _assert_accepted(_post(client, pc_token, env_key), store, "d/env-only+correct")

    def test_e_file_wins_over_env_residue(self, harness, tmp_path, monkeypatch):
        """(e) env và _FILE khác nhau → FILE thắng (registry HC-08): key file được
        nhận, key env (residue) bị 403."""
        client, pc_token, store = harness
        env_key = _random_secret()
        file_key = _random_secret()
        assert env_key != file_key
        monkeypatch.setenv("SCP_ADMIN_KEY", env_key)
        monkeypatch.setenv("SCP_ADMIN_KEY_FILE", str(_write_key_file(tmp_path, "admin-key", file_key)))

        _assert_denied(_post(client, pc_token, env_key), store, "e/env-residue-must-lose")
        _assert_accepted(_post(client, pc_token, file_key), store, "e/file-wins")

    def test_f_broken_file_fails_closed_without_env_fallback(self, harness, tmp_path, monkeypatch, caplog):
        """(f) _FILE trỏ file không tồn tại / rỗng → 403 dù env có key hợp lệ
        (KHÔNG fallback env lặng lẽ); không bao giờ log giá trị key."""
        client, pc_token, store = harness
        env_key = _random_secret()
        monkeypatch.setenv("SCP_ADMIN_KEY", env_key)

        monkeypatch.setenv("SCP_ADMIN_KEY_FILE", str(tmp_path / "does-not-exist"))
        with caplog.at_level(logging.DEBUG):
            _assert_denied(_post(client, pc_token, env_key), store, "f/missing-file")

        empty_file = tmp_path / "empty-admin-key"
        empty_file.write_text("   \n", encoding="utf-8")
        monkeypatch.setenv("SCP_ADMIN_KEY_FILE", str(empty_file))
        with caplog.at_level(logging.DEBUG):
            _assert_denied(_post(client, pc_token, env_key), store, "f/empty-file")

        registry_errors = [
            r.getMessage()
            for r in caplog.records
            if r.name == "scp.security.secret_registry" and r.levelno >= logging.ERROR
        ]
        assert any("SCP_ADMIN_KEY_FILE" in msg for msg in registry_errors), (
            "Registry phải log ERROR nêu rõ kênh FILE hỏng (fail rõ, không lặng lẽ)"
        )
        assert all(env_key not in r.getMessage() for r in caplog.records), "Secrecy: không log giá trị key"
