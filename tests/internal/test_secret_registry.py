"""[K2-W2 2026-10-23] secret_registry — precedence FILE-wins + fail-closed.

Trả nợ HC-07/HC-08: registry tĩnh ban đầu 12 secret (11 HC-07 + OPENROUTER_API_KEY provider key),
nay 18 spec sau R5 (tách khóa theo vai trò) và Key C L1 (keyring verifier) — danh sách tường minh
kèm commit gốc nằm trong ``test_registry_covers_exactly_the_12_declared_secrets``. resolve() chuẩn hóa kênh
env/{name}_FILE. Contract pinned trong docstring của
``scp/security/secret_registry.py``:

  * FILE wins khi ``{name}_FILE`` được set (kênh cấp phát orchestrator là ý
    định hiện tại của operator; env cùng lúc là residue).
  * ``{name}_FILE`` set nhưng file hỏng/rỗng → ``None`` (fail-closed), KHÔNG
    fallback về env — fallback âm thầm là fail-open.
  * Không cache giá trị.
  * Secret ngoài registry → ValueError (phải khai báo trước).
"""
from __future__ import annotations

import logging

import pytest

from scp.security import secret_registry
from scp.security.secret_registry import SECRET_PURPOSES, audit_missing, resolve

_REGISTRY_LOGGER = "scp.security.secret_registry"


@pytest.fixture(autouse=True)
def _clean_secret_env(monkeypatch):
    """Dọn kênh env cho các secret dùng trong test (không phụ thuộc .env host)."""
    for name in (
        "SCP_ADMIN_KEY",
        "SCP_ADMIN_KEY_FILE",
        "SCP_JWT_SECRET",
        "SCP_JWT_SECRET_FILE",
        "SCP_CAPABILITY_SECRET",
        "SCP_INTERNAL_SECRET",
        "SCP_INTERNAL_SECRET_FILE",
    ):
        monkeypatch.delenv(name, raising=False)
    yield


def test_registry_covers_exactly_the_12_declared_secrets():
    """Anti-drift: registry phải phủ ĐÚNG tập spec khai báo tường minh dưới đây, thiếu/thừa là fail.

    Tên test giữ số "12" vì lý do lịch sử: đổi tên = xóa nodeid (FA-02, T00
    ``check_real_test_deletion``). Số spec hiện tại là 18 (12 gốc + 4 R5 + 2 Key C L1).

    [registry-test-sync 2026-10-29] Test này đỏ từ 8c48a2d8 (R5-A) tới 9ed08a91
    vì 6 spec mới được thêm vào registry mà không cập nhật danh sách ở đây
    (tests/internal nằm ngoài broad suite tests/T*). Mỗi spec mới dưới đây ghi
    commit thêm vào + lane đã qua CHECKER GO. Danh sách PHẢI giữ tường minh —
    không so registry với chính nó (tautology): thêm spec mới vào registry
    bắt buộc phải cập nhật tập này trong cùng lane.
    """
    expected = {
        # --- 12 spec gốc: 11 HC-07 (K2-W2, 5e1d5a17) + provider key (6a5843a9, I37+R7) ---
        "OPENROUTER_API_KEY",
        "SCP_ADMIN_KEY",
        "SCP_AUTH_TOKEN_SECRET",
        "SCP_BRAIN_ADMIN_TOKEN",
        "SCP_CAPABILITY_SECRET",
        "SCP_DASHBOARD_PROXY_SECRET",
        "SCP_ENCRYPTION_KEY",
        "SCP_INTERNAL_SECRET",
        "SCP_JWT_SECRET",
        "SCP_PC_CONTROLLER_TOKEN",
        "SCP_SCHEDULER_ADMIN_TOKEN",
        "SCP_VERIFIER_SECRET",
        # --- R5 split-secrets (CHECKER GLM GO 2 vòng, ghi nhận 9a3403e8 GA.md) ---
        # R5-A 8c48a2d8: K_journal — HMAC journal hash chain + checkpoint (_FILE, min 32).
        "SCP_JOURNAL_SECRET",
        # R5-D 3f866fb9: K_ledger-audit — autonomous ledger + trace_ledger STH (_FILE, min 32).
        "SCP_LEDGER_HMAC_KEY",
        # R5-B 33215f4e: Ed25519 approval issuer — private chỉ issuer giữ (_FILE).
        "SCP_APPROVAL_ISSUER_PRIVATE_KEY",
        # R5-B 33215f4e: public key để kernel verify approval token, không mint được (_FILE).
        "SCP_APPROVAL_ISSUER_PUBLIC_KEY",
        # --- Key C L1 1db9e8fc (merge 9ed08a91, CHECKER GLM GO) ---
        # Public keyring verifier vr2 — không phải bí mật, khai báo để có kênh _FILE (HC-08).
        "SCP_VERIFIER_PUBLIC_KEYS",
        # Private key C — chỉ verifier daemon giữ (consumer scp.verifier_service đến với lane L3).
        "SCP_VERIFIER_PRIVATE_KEY",
    }
    assert set(SECRET_PURPOSES) == expected
    for name, spec in SECRET_PURPOSES.items():
        assert spec.name == name
        assert spec.purpose, f"{name} thiếu purpose"
        assert spec.consumers, f"{name} thiếu consumer"


def test_resolve_from_env(monkeypatch):
    monkeypatch.setenv("SCP_INTERNAL_SECRET", "  env-hmac-secret  ")
    assert resolve("SCP_INTERNAL_SECRET") == "env-hmac-secret"


def test_resolve_openrouter_api_key_from_env(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "  sk-or-test-123  ")
    assert resolve("OPENROUTER_API_KEY") == "sk-or-test-123"
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert resolve("OPENROUTER_API_KEY") is None
    # no file channel for the provider key by declaration (HC-08)
    assert SECRET_PURPOSES["OPENROUTER_API_KEY"].file_variant is False


def test_resolve_returns_none_when_unset():
    assert resolve("SCP_INTERNAL_SECRET") is None


def test_resolve_from_file_strips_whitespace(tmp_path, monkeypatch):
    secret_file = tmp_path / "admin-key"
    secret_file.write_text("file-admin-key-abc\n", encoding="utf-8")
    monkeypatch.setenv("SCP_ADMIN_KEY_FILE", str(secret_file))
    assert resolve("SCP_ADMIN_KEY") == "file-admin-key-abc"


def test_file_wins_over_env_when_both_set(tmp_path, monkeypatch):
    """PRECEDENCE PINNED: FILE thắng env khi cả hai được set.

    Lý do (đã pin trong docstring registry): *_FILE là kênh cấp phát do
    orchestrator kiểm soát (docker secrets/K8s mount) — khi được mount, đó là
    ý định hiện tại của operator; giá trị env chỉ là residue của
    shell/bootstrap (vd. default trong compose.test.yml).
    """
    secret_file = tmp_path / "admin-key"
    secret_file.write_text("file-admin-key-abc\n", encoding="utf-8")
    monkeypatch.setenv("SCP_ADMIN_KEY", "env-admin-key-residue")
    monkeypatch.setenv("SCP_ADMIN_KEY_FILE", str(secret_file))
    assert resolve("SCP_ADMIN_KEY") == "file-admin-key-abc"


def test_file_unreadable_fails_closed_without_env_fallback(tmp_path, monkeypatch, caplog):
    """Nhánh Âm (anti-placebo): file hỏng → None kể cả khi env đang có giá trị.

    Fallback về env ở đây sẽ là fail-open: operator tin kênh file hiệu lực
    trong khi env residue bị dùng ngầm.
    """
    monkeypatch.setenv("SCP_ADMIN_KEY", "env-admin-key-residue")
    monkeypatch.setenv("SCP_ADMIN_KEY_FILE", str(tmp_path / "does-not-exist"))
    with caplog.at_level(logging.ERROR, logger=_REGISTRY_LOGGER):
        assert resolve("SCP_ADMIN_KEY") is None
    errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert any("SCP_ADMIN_KEY_FILE" in msg for msg in errors)
    # secrecy invariant: không bao giờ log giá trị
    assert all("env-admin-key-residue" not in msg for msg in errors)


def test_empty_file_fails_closed(tmp_path, monkeypatch):
    secret_file = tmp_path / "admin-key"
    secret_file.write_text("   \n", encoding="utf-8")
    monkeypatch.setenv("SCP_ADMIN_KEY_FILE", str(secret_file))
    assert resolve("SCP_ADMIN_KEY") is None


def test_undeclared_file_variant_is_ignored_loudly(monkeypatch, caplog):
    """Secret khai báo file_variant=False (SCP_INTERNAL_SECRET) → *_FILE bị bỏ qua (loud).

    [P3-06 2026-10-28] SCP_JWT_SECRET chuyển sang file_variant=True (kênh
    SCP_JWT_SECRET_FILE được hỗ trợ — contract mới pin tại
    tests/internal/test_jwt_guard_secret_file_channel.py). Nhánh "kênh file
    chưa khai báo phải bị bỏ qua LOUD" được pin lại bằng SCP_INTERNAL_SECRET
    (vẫn file_variant=False). Strictness giữ nguyên: không hạ assertion nào.
    """
    monkeypatch.setenv("SCP_INTERNAL_SECRET", "env-hmac-secret")
    monkeypatch.setenv("SCP_INTERNAL_SECRET_FILE", "/tmp/should-be-ignored")
    with caplog.at_level(logging.ERROR, logger=_REGISTRY_LOGGER):
        assert resolve("SCP_INTERNAL_SECRET") == "env-hmac-secret"
    assert any(
        "SCP_INTERNAL_SECRET_FILE" in r.getMessage()
        for r in caplog.records
        if r.levelno >= logging.ERROR
    )


def test_blank_file_var_treated_as_unset(monkeypatch, caplog):
    monkeypatch.setenv("SCP_INTERNAL_SECRET", "env-hmac-secret")
    monkeypatch.setenv("SCP_INTERNAL_SECRET_FILE", "   ")
    with caplog.at_level(logging.WARNING, logger=_REGISTRY_LOGGER):
        assert resolve("SCP_INTERNAL_SECRET") == "env-hmac-secret"
    assert any(
        "SCP_INTERNAL_SECRET_FILE" in r.getMessage()
        for r in caplog.records
        if r.levelno >= logging.WARNING
    )


def test_no_value_caching_between_calls(tmp_path, monkeypatch):
    """Không cache: đổi env giữa 2 lần resolve phải thấy kết quả mới."""
    secret_file = tmp_path / "admin-key"
    monkeypatch.setenv("SCP_ADMIN_KEY_FILE", str(secret_file))
    secret_file.write_text("rotated-key-v2\n", encoding="utf-8")
    assert resolve("SCP_ADMIN_KEY") == "rotated-key-v2"
    secret_file.write_text("rotated-key-v3\n", encoding="utf-8")
    assert resolve("SCP_ADMIN_KEY") == "rotated-key-v3"


def test_unknown_secret_name_raises():
    with pytest.raises(ValueError, match="SECRET_PURPOSES"):
        resolve("SCP_NOT_A_SECRET")


def test_audit_missing_reports_exactly_the_missing_names_in_order(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("SCP_INTERNAL_SECRET", "present-via-env")
    secret_file = tmp_path / "admin-key"
    secret_file.write_text("present-via-file\n", encoding="utf-8")
    monkeypatch.setenv("SCP_ADMIN_KEY_FILE", str(secret_file))
    missing = audit_missing(
        ["SCP_INTERNAL_SECRET", "SCP_ADMIN_KEY", "SCP_JWT_SECRET"]
    )
    assert missing == ["SCP_JWT_SECRET"]


def test_audit_missing_fail_closed_on_file_error(tmp_path, monkeypatch):
    monkeypatch.setenv("SCP_ADMIN_KEY", "env-residue")
    monkeypatch.setenv("SCP_ADMIN_KEY_FILE", str(tmp_path / "gone"))
    assert audit_missing(["SCP_ADMIN_KEY"]) == ["SCP_ADMIN_KEY"]


def test_audit_missing_unknown_name_raises():
    with pytest.raises(ValueError, match="SECRET_PURPOSES"):
        audit_missing(["SCP_NOT_A_SECRET"])


def test_jwt_guard_uses_registry_resolve(monkeypatch):
    """Điểm tích hợp 1: jwt_guard đọc qua resolve, fail-closed giữ nguyên."""
    from scp.security import jwt_guard

    monkeypatch.setenv("SCP_JWT_SECRET", "jwt-via-registry")
    assert jwt_guard._get_jwt_secret() == "jwt-via-registry"
    monkeypatch.delenv("SCP_JWT_SECRET", raising=False)
    with pytest.raises(RuntimeError, match="SCP_JWT_SECRET is not set"):
        jwt_guard._get_jwt_secret()


def test_capability_token_uses_registry_resolve(monkeypatch):
    """Điểm tích hợp 2: capability_token đọc qua resolve, GAP-09 giữ nguyên."""
    from scp.core import capability_token

    monkeypatch.setenv("SCP_CAPABILITY_SECRET", "cap-via-registry")
    assert capability_token.get_capability_secret() == b"cap-via-registry"
    monkeypatch.delenv("SCP_CAPABILITY_SECRET", raising=False)
    with pytest.raises(capability_token.MissingSecretError, match="GAP-09"):
        capability_token.get_capability_secret()
