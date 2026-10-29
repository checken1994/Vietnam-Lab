"""[FU-DIGEST-ASCII 2026-10-29] Gate token/admin-key của /v3/hands: non-ASCII → 403, không 500.

Hiện trạng trước FU-DIGEST-ASCII: ``_guard`` (X-SCP-PC-TOKEN) và admin gate của
``POST /v3/hands/confirmations`` (X-SCP-ADMIN-KEY) gọi ``hmac.compare_digest`` trên
``str``. Stdlib raise ``TypeError`` khi một trong hai ``str`` chứa ký tự non-ASCII
→ request có header non-ASCII (hoặc deploy cấu hình key non-ASCII) nhận 500 thay vì
403. Vẫn fail-closed (không bypass) nhưng sai status và là một exception path.
Sửa: so sánh constant-time trên bytes UTF-8 ở CẢ HAI phía (``surrogatepass`` để
encode luôn total và injective: bytes bằng nhau ⇔ str bằng nhau), giữ nguyên các
kiểm rỗng/None fail-closed.

Server thực sự nhận gì (đã probe, starlette 1.3.1 / httpx 0.28.1):
  * httpx từ chối header ``str`` non-ASCII (UnicodeEncodeError phía client) ⇒ test
    gửi raw ``bytes``.
  * Transport của TestClient decode bytes theo ``httpx.Headers.encoding`` (ascii →
    utf-8 → latin-1) rồi re-encode UTF-8 vào ASGI scope; Starlette decode header
    theo latin-1. ⇒ route nhận ``raw_utf8.decode("latin-1")`` (mojibake, vd
    ``b"kh\\xc3\\xb3a"`` → ``"khÃ³a"``) — luôn là str non-ASCII, mỗi code point
    ≤ U+00FF. Test precondition bên dưới pin điều này để các ca 403 không pass
    rỗng nếu harness đổi hành vi (anti-placebo).
  * Uvicorn thật (h11/httptools cho phép obs-text 0x80–0xFF) đưa thẳng wire bytes
    vào scope rồi cũng decode latin-1 — KHÔNG chạy ở đây (Docker-only mandate);
    xem giới hạn phạm vi trong report của lane.

Ngữ nghĩa gate sau fix = đúng phép bằng nhau trên ``str`` (như trước với ASCII):
key non-ASCII cấu hình đúng nhưng client gửi dạng UTF-8 vẫn 403, vì route thấy
bản decode latin-1 (ca d). Đây là fail-closed, được pin có chủ đích.

Zero-mock (FA-04): FastAPI TestClient thật trên ``hands_routes.router`` thật,
``HumanConfirmationStore`` thật + request ledger thật trong tmp_path; chỉ dùng
monkeypatch.setenv/delenv/setattr để cô lập. Secret test sinh ngẫu nhiên, KHÔNG
BAO GIỜ in ra (assert message không chứa giá trị).
"""
from __future__ import annotations

import hmac
import secrets
from pathlib import Path

import pytest
from fastapi import FastAPI, Header
from fastapi.testclient import TestClient

from scp.api.routes import hands_routes
from scp.security import confirmation_store
from scp.security.confirmation_store import HumanConfirmationStore

_ENDPOINT = "/v3/hands/confirmations"
_ADMIN_DENY_MARKER = "Operator confirmation requires admin authority"
_GUARD_DENY_MARKER = "SCP Hands token is missing or invalid"
_PAYLOAD = {"action": "pc.write_file", "target": "workspace/fu_digest_ascii.py"}


def _ascii_secret() -> str:
    return secrets.token_urlsafe(24)


def _non_ascii_secret() -> str:
    # Cyrillic + tiếng Việt có dấu; phần ngẫu nhiên giữ cho mỗi lần chạy khác nhau.
    return "ключ-khóa-" + secrets.token_urlsafe(16)


def _unencodable_secret() -> str:
    # Lone surrogate: dạng os.environ (POSIX, surrogateescape) trả về khi env chứa
    # byte không phải UTF-8. ``str.encode("utf-8")`` strict raise UnicodeEncodeError.
    return "k\udcff" + secrets.token_urlsafe(16)


@pytest.fixture()
def harness(monkeypatch, tmp_path: Path):
    """App thật + store/ledger thật trong tmp_path; mặc định cả hai key ASCII hợp lệ.

    ``raise_server_exceptions=False``: exception phía server hiện ra đúng như client
    thật thấy (HTTP 500) thay vì nổ trong test — RED trên base đọc được là "got 500".
    """
    monkeypatch.delenv("SCP_ADMIN_KEY", raising=False)
    monkeypatch.delenv("SCP_ADMIN_KEY_FILE", raising=False)
    pc_token = _ascii_secret()
    admin_key = _ascii_secret()
    monkeypatch.setenv("SCP_PC_CONTROLLER_TOKEN", pc_token)
    monkeypatch.setenv("SCP_ADMIN_KEY", admin_key)
    monkeypatch.setenv("SCP_DATA_DIR", str(tmp_path / "data"))

    store = HumanConfirmationStore(store_path=tmp_path / "confirmations.jsonl")
    monkeypatch.setattr(confirmation_store, "_GLOBAL_CONFIRMATION_STORE", store)
    monkeypatch.setattr(hands_routes._HANDS_ROUTES_LEDGER, "path", tmp_path / "request_runs.jsonl")

    app = FastAPI()
    app.include_router(hands_routes.router)
    client = TestClient(app, raise_server_exceptions=False)
    return client, pc_token, admin_key, store


def _post(client: TestClient, pc_header: str | bytes | None, admin_header: str | bytes | None):
    headers: dict[str, str | bytes] = {}
    if pc_header is not None:
        headers["X-SCP-PC-TOKEN"] = pc_header
    if admin_header is not None:
        headers["X-SCP-ADMIN-KEY"] = admin_header
    return client.post(_ENDPOINT, json=_PAYLOAD, headers=headers)


def _assert_denied(resp, store: HumanConfirmationStore, marker: str, case: str) -> None:
    assert resp.status_code == 403, f"[{case}] phải 403 fail-closed, got {resp.status_code}"
    assert marker in resp.json()["detail"], f"[{case}] 403 phải đến từ đúng gate"
    assert store._memory_cache == {}, f"[{case}] 403 không được ghi confirmation nào"
    assert not store.store_path.exists(), f"[{case}] 403 không được tạo file confirmation"


def _assert_admin_denied(resp, store, case: str) -> None:
    _assert_denied(resp, store, _ADMIN_DENY_MARKER, case)


def _assert_guard_denied(resp, store, case: str) -> None:
    _assert_denied(resp, store, _GUARD_DENY_MARKER, case)


def _assert_accepted(resp, store: HumanConfirmationStore, case: str) -> None:
    assert resp.status_code == 200, f"[{case}] key đúng phải được nhận, got {resp.status_code}"
    body = resp.json()
    assert body["success"] is True
    cid = body["confirmation_id"]
    assert store._memory_cache[cid]["action"] == _PAYLOAD["action"]
    assert cid in store.store_path.read_text(encoding="utf-8")


class TestHarnessPrecondition:
    def test_non_ascii_header_bytes_reach_route_as_non_ascii_str(self):
        """Anti-placebo: raw bytes non-ASCII thực sự tới route dưới dạng str non-ASCII
        (latin-1 decode của UTF-8 bytes), và đó chính là input làm compare_digest(str)
        raise TypeError trên base."""
        app = FastAPI()

        @app.get("/echo")
        async def echo(x_probe: str | None = Header(default=None)) -> dict[str, list[int]]:
            return {"codepoints": [ord(c) for c in (x_probe or "")]}

        raw_utf8 = "khóa-ключ".encode("utf-8")
        seen_cps = TestClient(app).get("/echo", headers={"X-Probe": raw_utf8}).json()["codepoints"]
        seen = "".join(chr(cp) for cp in seen_cps)
        assert seen == raw_utf8.decode("latin-1"), "route phải nhận latin-1 decode của UTF-8 bytes"
        assert not seen.isascii(), "precondition: giá trị route nhận phải là non-ASCII"
        with pytest.raises(TypeError):
            hmac.compare_digest(seen, "ascii-only")


class TestAdminKeyGateNonAscii:
    def test_a_non_ascii_header_vs_ascii_key_is_403(self, harness):
        """(a) Header non-ASCII (UTF-8 bytes và latin-1 bytes) vs key ASCII → 403."""
        client, pc_token, _admin_key, store = harness
        _assert_admin_denied(_post(client, pc_token, _non_ascii_secret().encode("utf-8")), store, "a/utf8")
        _assert_admin_denied(_post(client, pc_token, "khóa-é".encode("latin-1")), store, "a/latin1")

    def test_b_valid_key_with_non_ascii_affix_is_403(self, harness):
        """(b) Key đúng + ký tự non-ASCII chèn trước/sau → 403 (non-ASCII không được
        bị bỏ qua/strip khi so sánh — chặn bypass kiểu encode(errors="ignore"))."""
        client, pc_token, admin_key, store = harness
        _assert_admin_denied(_post(client, pc_token, (admin_key + "é").encode("utf-8")), store, "b/suffix")
        _assert_admin_denied(_post(client, pc_token, ("ключ" + admin_key).encode("utf-8")), store, "b/prefix")

    def test_c_ascii_header_vs_non_ascii_configured_key_is_403(self, harness, monkeypatch, tmp_path):
        """(c) Key cấu hình non-ASCII (env và _FILE UTF-8) vs header ASCII → 403,
        kể cả header = phần ASCII của key (non-ASCII phía key không được bị bỏ qua)."""
        client, pc_token, _admin_key, store = harness
        configured = _non_ascii_secret()
        ascii_part = configured.encode("ascii", "ignore").decode("ascii")

        monkeypatch.setenv("SCP_ADMIN_KEY", configured)
        _assert_admin_denied(_post(client, pc_token, _ascii_secret()), store, "c/env+random")
        _assert_admin_denied(_post(client, pc_token, ascii_part), store, "c/env+ascii-part")

        key_file = tmp_path / "admin-key"
        key_file.write_text(configured + "\n", encoding="utf-8")
        monkeypatch.setenv("SCP_ADMIN_KEY_FILE", str(key_file))
        _assert_admin_denied(_post(client, pc_token, _ascii_secret()), store, "c/file+random")
        _assert_admin_denied(_post(client, pc_token, ascii_part), store, "c/file+ascii-part")

    def test_d_non_ascii_key_sent_as_utf8_is_403_not_500(self, harness, monkeypatch):
        """(d) Key non-ASCII cấu hình đúng, client gửi đúng UTF-8 bytes của nó → route
        thấy bản decode latin-1 ≠ key ⇒ 403 (ngữ nghĩa str-equality, fail-closed).
        Trên base: TypeError → 500."""
        client, pc_token, _admin_key, store = harness
        configured = _non_ascii_secret()
        monkeypatch.setenv("SCP_ADMIN_KEY", configured)
        _assert_admin_denied(_post(client, pc_token, configured.encode("utf-8")), store, "d/utf8-of-key")

    def test_e_unencodable_configured_key_is_403(self, harness, monkeypatch):
        """(e) Key cấu hình chứa lone surrogate (env POSIX có byte không-UTF-8) → 403,
        không phải UnicodeEncodeError → 500."""
        client, pc_token, _admin_key, store = harness
        monkeypatch.setenv("SCP_ADMIN_KEY", _unencodable_secret())
        _assert_admin_denied(_post(client, pc_token, _ascii_secret()), store, "e/ascii-header")
        _assert_admin_denied(_post(client, pc_token, "khóa".encode("utf-8")), store, "e/non-ascii-header")

    def test_f_correct_ascii_key_still_accepted(self, harness):
        """(f) Control: token + admin key ASCII đúng → 200 + side-effect thật."""
        client, pc_token, admin_key, store = harness
        _assert_accepted(_post(client, pc_token, admin_key), store, "f/correct")

    def test_g_empty_or_missing_admin_header_still_refused(self, harness, monkeypatch):
        """(g) Header rỗng/thiếu → 403 với key ASCII, key non-ASCII và khi chưa cấu
        hình key (bỏ kiểm rỗng thì compare_digest(b"", b"") == True → bypass)."""
        client, pc_token, _admin_key, store = harness
        _assert_admin_denied(_post(client, pc_token, None), store, "g/ascii-key+missing")
        _assert_admin_denied(_post(client, pc_token, ""), store, "g/ascii-key+empty")

        monkeypatch.setenv("SCP_ADMIN_KEY", _non_ascii_secret())
        _assert_admin_denied(_post(client, pc_token, None), store, "g/non-ascii-key+missing")
        _assert_admin_denied(_post(client, pc_token, ""), store, "g/non-ascii-key+empty")

        monkeypatch.delenv("SCP_ADMIN_KEY")
        _assert_admin_denied(_post(client, pc_token, None), store, "g/unconfigured+missing")
        _assert_admin_denied(_post(client, pc_token, ""), store, "g/unconfigured+empty")


class TestPcTokenGuardNonAscii:
    """``_guard`` chạy trước admin gate; mọi ca gửi admin key ĐÚNG để nếu ``_guard``
    bị vượt thì request đi tới 200 (lỗi lộ ra), không bị admin gate che."""

    def test_h_non_ascii_pc_header_vs_ascii_token_is_403(self, harness):
        """(h) X-SCP-PC-TOKEN non-ASCII vs token ASCII → 403 từ _guard."""
        client, pc_token, admin_key, store = harness
        _assert_guard_denied(_post(client, _non_ascii_secret().encode("utf-8"), admin_key), store, "h/utf8")
        _assert_guard_denied(_post(client, (pc_token + "é").encode("utf-8"), admin_key), store, "h/suffix")

    def test_i_ascii_pc_header_vs_non_ascii_configured_token_is_403(self, harness, monkeypatch):
        """(i) Token cấu hình non-ASCII vs header ASCII (random và phần ASCII) → 403."""
        client, _pc_token, admin_key, store = harness
        configured = _non_ascii_secret()
        monkeypatch.setenv("SCP_PC_CONTROLLER_TOKEN", configured)
        ascii_part = configured.encode("ascii", "ignore").decode("ascii")
        _assert_guard_denied(_post(client, _ascii_secret(), admin_key), store, "i/random")
        _assert_guard_denied(_post(client, ascii_part, admin_key), store, "i/ascii-part")

    def test_j_unencodable_configured_pc_token_is_403(self, harness, monkeypatch):
        """(j) Token cấu hình chứa lone surrogate → 403, không 500."""
        client, _pc_token, admin_key, store = harness
        monkeypatch.setenv("SCP_PC_CONTROLLER_TOKEN", _unencodable_secret())
        _assert_guard_denied(_post(client, _ascii_secret(), admin_key), store, "j/ascii-header")

    def test_k_empty_or_missing_pc_header_still_refused(self, harness, monkeypatch):
        """(k) Header rỗng/thiếu → 403; token chưa cấu hình → 403 với mọi header."""
        client, _pc_token, admin_key, store = harness
        _assert_guard_denied(_post(client, None, admin_key), store, "k/missing")
        _assert_guard_denied(_post(client, "", admin_key), store, "k/empty")

        monkeypatch.delenv("SCP_PC_CONTROLLER_TOKEN")
        _assert_guard_denied(_post(client, "", admin_key), store, "k/unconfigured+empty")
        _assert_guard_denied(_post(client, _ascii_secret(), admin_key), store, "k/unconfigured+random")
