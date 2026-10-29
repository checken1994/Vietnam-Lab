"""[FU-AUDIT-FILE 2026-10-29] runtime audit tôn trọng kênh ``*_FILE`` của admin bearer token.

Hiện trạng trước FU-AUDIT-FILE: ``scp.runtime_audit.runner._resolve_admin_token``
đọc ``SCP_ADMIN_KEY`` → ``SCP_AUTH_TOKEN_SECRET`` → ``SCP_SCHEDULER_ADMIN_TOKEN``
thẳng từ ``os.environ``, bỏ qua kênh ``{name}_FILE`` (docker secrets / K8s mount)
dù cả 3 tên đều được ``scp.security.secret_registry`` khai báo ``file_variant=True``
(HC-08). Hệ quả: deploy chỉ cấp credential qua ``_FILE`` → audit KHÔNG gửi bearer
→ endpoint bảo vệ trả 401 → claim bị báo "could not observe" dù credential có thật.
Ngược lại, khi ``{name}_FILE`` hỏng mà env ``{name}`` còn residue, base dùng residue
— đúng loại fail-open mà registry cấm. Sửa: mỗi tên đọc qua
``secret_registry.resolve(name)``, giữ nguyên thứ tự ưu tiên giữa các tên.

Contract pin (docstring scp/security/secret_registry.py, HC-08):
  * ``{name}_FILE`` set (khác rỗng) → FILE thắng env của CÙNG tên.
  * File hỏng/không tồn tại/rỗng → tên đó coi như không có (``None``, registry log
    ERROR, KHÔNG fallback env residue của cùng tên).
  * Thứ tự giữa các tên giữ nguyên: SCP_ADMIN_KEY > SCP_AUTH_TOKEN_SECRET >
    SCP_SCHEDULER_ADMIN_TOKEN; tên không resolve được thì xét tên kế tiếp (giống
    hành vi "tên chưa set" của base).
  * Không resolve được tên nào → ``""`` → audit báo "could not observe" trung thực,
    không bao giờ thành pass.

Zero-mock: ``_resolve_admin_token`` thật + ``secret_registry.resolve`` thật trên
file thật trong tmp_path; test wiring chạy ``RuntimeAuditRunner.run()`` thật với
``ServiceLivenessProbe`` gọi HTTP thật tới một ``ThreadingHTTPServer`` cục bộ
trong test (127.0.0.1:0, cùng pattern tests/T13_runtime_audit/test_runtime_audit.py),
KHÔNG chạm service sống trên host. Chỉ dùng tmp_path + monkeypatch.setenv/delenv.
Secret test sinh ngẫu nhiên mỗi lần chạy và KHÔNG BAO GIỜ được in ra (assert
message không chứa giá trị).
"""
from __future__ import annotations

import json
import logging
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from scp.runtime_audit.models import Severity
from scp.runtime_audit.runner import RuntimeAuditRunner, _resolve_admin_token

_REGISTRY_LOGGER = "scp.security.secret_registry"
_NAMES = ("SCP_ADMIN_KEY", "SCP_AUTH_TOKEN_SECRET", "SCP_SCHEDULER_ADMIN_TOKEN")
_BROKEN_KINDS = ("missing", "empty", "directory")


def _random_secret() -> str:
    return secrets.token_urlsafe(24)


def _label(resolved: str, known: dict[str, str]) -> str:
    """Đổi token resolve được thành nhãn để assertion diff KHÔNG BAO GIỜ in giá trị secret."""
    if resolved == "":
        return "<empty>"
    for label, value in known.items():
        if resolved == value:
            return label
    return "<unknown-value>"


@pytest.fixture(autouse=True)
def _clean_admin_channels(monkeypatch):
    """Dọn cả env lẫn ``_FILE`` của 3 tên — conftest gốc setdefault SCP_ADMIN_KEY
    cho cả suite và .env host có thể có giá trị thật; mỗi test tự khai báo kênh."""
    for name in _NAMES:
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(f"{name}_FILE", raising=False)
    yield


def _write_secret_file(tmp_path: Path, name: str, value: str) -> Path:
    path = tmp_path / f"{name.lower()}.secret"
    path.write_text(f"{value}\n", encoding="utf-8")
    return path


def _broken_secret_ref(tmp_path: Path, name: str, kind: str) -> Path:
    if kind == "missing":
        return tmp_path / f"{name.lower()}.does-not-exist"
    if kind == "empty":
        path = tmp_path / f"{name.lower()}.empty"
        path.write_text("  \n", encoding="utf-8")
        return path
    path = tmp_path / f"{name.lower()}.dir"
    path.mkdir()
    return path


# ─── _resolve_admin_token: từng kênh ───────────────────────────────────────

@pytest.mark.parametrize("name", _NAMES)
def test_file_only_channel_is_resolved(name, tmp_path, monkeypatch):
    """Chỉ ``{name}_FILE`` được cấp → token phải là giá trị trong file (base: ``""``)."""
    value = _random_secret()
    monkeypatch.setenv(f"{name}_FILE", str(_write_secret_file(tmp_path, name, value)))
    assert _label(_resolve_admin_token(), {"file": value}) == "file", f"{name}_FILE-only phải resolve được credential"


@pytest.mark.parametrize("name", _NAMES)
def test_env_only_channel_keeps_legacy_behavior(name, monkeypatch):
    """Control: chỉ env ``{name}`` → giữ hành vi cũ (strip whitespace)."""
    value = _random_secret()
    monkeypatch.setenv(name, f"  {value}  ")
    assert _label(_resolve_admin_token(), {"env": value}) == "env", f"{name} env-only phải giữ hành vi cũ"


@pytest.mark.parametrize("name", _NAMES)
def test_file_wins_over_env_residue_for_same_name(name, tmp_path, monkeypatch):
    """FILE thắng env residue của CÙNG tên (HC-08 FILE-wins)."""
    file_value, residue = _random_secret(), _random_secret()
    monkeypatch.setenv(name, residue)
    monkeypatch.setenv(f"{name}_FILE", str(_write_secret_file(tmp_path, name, file_value)))
    got = _label(_resolve_admin_token(), {"file": file_value, "env-residue": residue})
    assert got == "file", f"{name}: giá trị _FILE phải thắng env residue"


@pytest.mark.parametrize("kind", _BROKEN_KINDS)
@pytest.mark.parametrize("name", _NAMES)
def test_broken_file_fails_closed_without_env_residue_fallback(name, kind, tmp_path, monkeypatch, caplog):
    """``{name}_FILE`` hỏng → tên đó fail-closed: KHÔNG dùng env residue, registry log ERROR,
    và không log record nào chứa giá trị residue."""
    residue = _random_secret()
    monkeypatch.setenv(name, residue)
    monkeypatch.setenv(f"{name}_FILE", str(_broken_secret_ref(tmp_path, name, kind)))
    with caplog.at_level(logging.WARNING, logger=_REGISTRY_LOGGER):
        resolved = _resolve_admin_token()
    assert _label(resolved, {"env-residue": residue}) == "<empty>", (
        f"{name}_FILE {kind}: phải fail-closed, không fallback env residue"
    )
    errors = [r for r in caplog.records if r.name == _REGISTRY_LOGGER and r.levelno >= logging.ERROR]
    assert any(f"{name}_FILE" in r.getMessage() for r in errors), (
        f"{name}_FILE {kind}: registry phải log ERROR nêu tên biến"
    )
    assert all(residue not in r.getMessage() for r in caplog.records), "giá trị secret không được log"


# ─── _resolve_admin_token: thứ tự ưu tiên giữa các tên ─────────────────────

def test_precedence_order_is_preserved_env_only(monkeypatch):
    """Control: thứ tự cũ SCP_ADMIN_KEY > SCP_AUTH_TOKEN_SECRET > SCP_SCHEDULER_ADMIN_TOKEN."""
    values = {name: _random_secret() for name in _NAMES}
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    assert _label(_resolve_admin_token(), values) == "SCP_ADMIN_KEY"
    monkeypatch.delenv("SCP_ADMIN_KEY")
    assert _label(_resolve_admin_token(), values) == "SCP_AUTH_TOKEN_SECRET"
    monkeypatch.delenv("SCP_AUTH_TOKEN_SECRET")
    assert _label(_resolve_admin_token(), values) == "SCP_SCHEDULER_ADMIN_TOKEN"
    monkeypatch.delenv("SCP_SCHEDULER_ADMIN_TOKEN")
    assert _label(_resolve_admin_token(), values) == "<empty>"


def test_precedence_order_is_preserved_across_mixed_channels(tmp_path, monkeypatch):
    """Thứ tự giữa các tên không phụ thuộc kênh: tên ưu tiên cao qua FILE vẫn thắng
    tên thấp hơn qua env (base: tên FILE bị bỏ qua → tên thấp hơn thắng sai)."""
    values = {name: _random_secret() for name in _NAMES}
    monkeypatch.setenv("SCP_ADMIN_KEY_FILE", str(_write_secret_file(tmp_path, "SCP_ADMIN_KEY", values["SCP_ADMIN_KEY"])))
    monkeypatch.setenv("SCP_AUTH_TOKEN_SECRET", values["SCP_AUTH_TOKEN_SECRET"])
    sched_file = _write_secret_file(tmp_path, "SCP_SCHEDULER_ADMIN_TOKEN", values["SCP_SCHEDULER_ADMIN_TOKEN"])
    monkeypatch.setenv("SCP_SCHEDULER_ADMIN_TOKEN_FILE", str(sched_file))

    assert _label(_resolve_admin_token(), values) == "SCP_ADMIN_KEY", "SCP_ADMIN_KEY (FILE) phải thắng"
    monkeypatch.delenv("SCP_ADMIN_KEY_FILE")
    assert _label(_resolve_admin_token(), values) == "SCP_AUTH_TOKEN_SECRET", "kế tiếp: SCP_AUTH_TOKEN_SECRET (env)"
    monkeypatch.delenv("SCP_AUTH_TOKEN_SECRET")
    assert _label(_resolve_admin_token(), values) == "SCP_SCHEDULER_ADMIN_TOKEN", "cuối: SCP_SCHEDULER_ADMIN_TOKEN (FILE)"


def test_middle_name_file_beats_lower_name_env(tmp_path, monkeypatch):
    """SCP_AUTH_TOKEN_SECRET qua FILE phải thắng SCP_SCHEDULER_ADMIN_TOKEN qua env."""
    auth_value, sched_value = _random_secret(), _random_secret()
    auth_file = _write_secret_file(tmp_path, "SCP_AUTH_TOKEN_SECRET", auth_value)
    monkeypatch.setenv("SCP_AUTH_TOKEN_SECRET_FILE", str(auth_file))
    monkeypatch.setenv("SCP_SCHEDULER_ADMIN_TOKEN", sched_value)
    got = _label(_resolve_admin_token(), {"auth-file": auth_value, "sched-env": sched_value})
    assert got == "auth-file", "SCP_AUTH_TOKEN_SECRET (FILE) phải thắng scheduler env"


def test_broken_higher_file_skips_to_next_name_never_to_its_own_residue(tmp_path, monkeypatch):
    """SCP_ADMIN_KEY_FILE hỏng + env residue SCP_ADMIN_KEY + SCP_AUTH_TOKEN_SECRET hợp lệ:
    residue của SCP_ADMIN_KEY KHÔNG được dùng; tên kế tiếp được xét như khi tên trên
    chưa cấp (giữ ngữ nghĩa chuỗi ưu tiên của base)."""
    residue, auth_value = _random_secret(), _random_secret()
    monkeypatch.setenv("SCP_ADMIN_KEY", residue)
    monkeypatch.setenv("SCP_ADMIN_KEY_FILE", str(tmp_path / "admin.does-not-exist"))
    monkeypatch.setenv("SCP_AUTH_TOKEN_SECRET", auth_value)
    got = _label(_resolve_admin_token(), {"admin-env-residue": residue, "auth-env": auth_value})
    assert got == "auth-env", "env residue của tên có _FILE hỏng không được dùng; tên kế tiếp phải được dùng"


def test_all_files_broken_with_env_residues_resolves_nothing(tmp_path, monkeypatch):
    """Cả 3 ``_FILE`` hỏng, env residue đủ cả 3 → ``""`` (không credential nào), không fallback."""
    residues = {name: _random_secret() for name in _NAMES}
    for name, residue in residues.items():
        monkeypatch.setenv(name, residue)
        monkeypatch.setenv(f"{name}_FILE", str(_broken_secret_ref(tmp_path, name, "missing")))
    assert _label(_resolve_admin_token(), residues) == "<empty>"


def test_nothing_configured_resolves_empty():
    assert _label(_resolve_admin_token(), {}) == "<empty>"


# ─── RuntimeAuditRunner wiring: HTTP thật tới server cục bộ trong test ─────

class _BearerGatedHandler(BaseHTTPRequestHandler):
    """200 + claim JSON chỉ khi bearer khớp; còn lại 401 (giống guard của backend)."""

    expected_bearer = ""
    payload = json.dumps({"total_findings": 3}).encode("utf-8")

    def do_GET(self):  # noqa: N802 - http.server API
        authorized = self.headers.get("Authorization", "") == f"Bearer {self.expected_bearer}"
        body = self.payload if authorized else b'{"detail": "Unauthorized"}'
        self.send_response(200 if authorized else 401)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):  # silence the test server
        return


@pytest.fixture()
def gated_audit_stats_url():
    """Server cục bộ 127.0.0.1:0 cho endpoint audit-stats được bảo vệ bằng bearer."""
    servers: list[ThreadingHTTPServer] = []

    def _start(expected_bearer: str) -> str:
        handler = type("H", (_BearerGatedHandler,), {"expected_bearer": expected_bearer})
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return f"http://127.0.0.1:{server.server_address[1]}/v105/audit/stats"

    yield _start

    for server in servers:
        server.shutdown()
        server.server_close()


def _run_audit(tmp_path: Path, url: str):
    runner = RuntimeAuditRunner(
        data_dir=tmp_path / "data",
        service_targets=[{"name": "backend-audit-stats", "url": url}],
        journal_secret="",
        http_timeout_sec=5.0,
    )
    audit = runner.run()
    by_probe = {f.probe_id: f for f in audit.findings}
    return runner, audit, by_probe["services.liveness"], by_probe["audit.claim_vs_reality"]


def test_runner_authenticates_with_file_only_credential(tmp_path, monkeypatch, gated_audit_stats_url):
    """Chỉ SCP_ADMIN_KEY_FILE → audit gửi đúng bearer, claim audit-stats được quan sát
    và cross-check (base: 401 → "could not observe")."""
    token = _random_secret()
    url = gated_audit_stats_url(token)
    monkeypatch.setenv("SCP_ADMIN_KEY_FILE", str(_write_secret_file(tmp_path, "SCP_ADMIN_KEY", token)))

    runner, audit, liveness, cross = _run_audit(tmp_path, url)

    assert _label(runner.admin_token, {"file": token}) == "file", "runner phải lấy credential từ _FILE"
    assert audit.scope["authenticated_claims"] is True
    assert liveness.details["authenticated"] is True
    assert liveness.details["healthy"] == ["backend-audit-stats"]
    assert liveness.details["protected"] == []
    assert cross.severity == Severity.PASS
    assert cross.details["observations"] == [{
        "claim": "backend /v105/audit/stats total_findings",
        "observed_status": 3,
        "http_status": 200,
    }]


def test_runner_with_broken_file_reports_could_not_observe_not_pass(tmp_path, monkeypatch, gated_audit_stats_url):
    """SCP_ADMIN_KEY_FILE hỏng trong khi env residue SCP_ADMIN_KEY trùng đúng token server:
    audit KHÔNG được dùng residue để đăng nhập — claim phải báo "could not observe"
    (protected + cross-check UNKNOWN), không bao giờ thành pass."""
    token = _random_secret()
    url = gated_audit_stats_url(token)
    monkeypatch.setenv("SCP_ADMIN_KEY", token)
    monkeypatch.setenv("SCP_ADMIN_KEY_FILE", str(tmp_path / "admin.does-not-exist"))

    runner, audit, liveness, cross = _run_audit(tmp_path, url)

    assert _label(runner.admin_token, {"env-residue": token}) == "<empty>", "runner không được fallback env residue"
    assert audit.scope["authenticated_claims"] is False
    assert liveness.details["authenticated"] is False
    assert liveness.details["protected"] == ["backend-audit-stats"]
    assert liveness.details["healthy"] == []
    assert cross.severity == Severity.UNKNOWN
    assert cross.details["observations"] == []
