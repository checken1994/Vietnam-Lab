"""Hermetic A3 tests for the Hugging Face backup external-write boundary.

The fake is installed at the third-party driver seam only.  The SCP egress
policy and the backup control flow run for real; no network socket is allowed.
"""
from __future__ import annotations

import logging
import sqlite3
import sys
import types
from pathlib import Path

import pytest

from scp.core import github_backup as backup
from scp.security.url_safety import EgressDeniedError


@pytest.fixture
def backup_fixture(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    db_path = tmp_path / "v13.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE marker (value TEXT)")
    conn.commit()
    conn.close()

    monkeypatch.setattr(backup, "DB_PATH", db_path)
    monkeypatch.setattr(backup, "BACKUP_INTERVAL_CYCLES", 10)
    monkeypatch.setattr(backup, "BACKUP_MIN_SECONDS_BETWEEN", 0)
    monkeypatch.setattr(backup, "SNAPSHOT_EVERY_N_BACKUPS", 1)
    monkeypatch.setattr(backup, "_last_backup_cycle", 0)
    monkeypatch.setattr(backup, "_last_backup_time", 0.0)
    monkeypatch.setattr(backup, "_backup_success_count", 0)
    monkeypatch.setattr(
        backup,
        "_last_backup_result",
        {
            "last_attempt_at": None,
            "last_success_at": None,
            "last_error": None,
            "consecutive_failures": 0,
        },
    )
    monkeypatch.delenv("HF_BACKUP_REPO", raising=False)
    monkeypatch.delenv("HF_TOKEN", raising=False)
    return db_path


def _install_fake_hf(monkeypatch: pytest.MonkeyPatch, api_type: type) -> None:
    fake_module = types.ModuleType("huggingface_hub")
    fake_module.HfApi = api_type
    monkeypatch.setitem(sys.modules, "huggingface_hub", fake_module)


def _allow_hf(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCP_EGRESS_MODE", "allowlist")
    monkeypatch.setenv("SCP_EGRESS_ALLOWLIST", "huggingface.co")
    monkeypatch.setenv("HF_TOKEN", "fixture-secret")
    monkeypatch.delenv("SCP_PRODUCTION_MODE", raising=False)


def _deny_hf(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCP_EGRESS_MODE", "deny")
    monkeypatch.delenv("SCP_EGRESS_ALLOWLIST", raising=False)
    monkeypatch.delenv("SCP_PRODUCTION_MODE", raising=False)


def _assert_driver_not_called(
    backup_fixture, monkeypatch: pytest.MonkeyPatch, *, mode: str | None,
    allowlist: str | None = None, expected_error: str,
):
    if mode is None:
        monkeypatch.delenv("SCP_EGRESS_MODE", raising=False)
    else:
        monkeypatch.setenv("SCP_EGRESS_MODE", mode)
    if allowlist is None:
        monkeypatch.delenv("SCP_EGRESS_ALLOWLIST", raising=False)
    else:
        monkeypatch.setenv("SCP_EGRESS_ALLOWLIST", allowlist)
    monkeypatch.setenv("HF_TOKEN", "fixture-secret")
    calls: list[str] = []

    class SentinelHfApi:
        def __init__(self, *args, **kwargs):
            calls.append("HfApi")
            raise AssertionError("HfApi must not be constructed before R2 egress approval")

    _install_fake_hf(monkeypatch, SentinelHfApi)
    assert backup.backup_to_hf(10) is False
    assert calls == []
    assert backup.get_backup_status()["last_error"] == expected_error


def test_unset_mode_blocks_before_hf_driver(backup_fixture, monkeypatch):
    _assert_driver_not_called(
        backup_fixture, monkeypatch, mode=None,
        expected_error="fail_closed_unknown",
    )


def test_unknown_mode_blocks_before_hf_driver(backup_fixture, monkeypatch):
    _assert_driver_not_called(
        backup_fixture, monkeypatch, mode="UNKNOWN_MODE",
        expected_error="fail_closed_unknown",
    )


def test_deny_mode_blocks_before_hf_driver(
    backup_fixture, monkeypatch: pytest.MonkeyPatch
):
    _assert_driver_not_called(
        backup_fixture, monkeypatch, mode="deny",
        expected_error="unconfigured_external_write",
    )


def test_offline_mode_blocks_before_hf_driver(backup_fixture, monkeypatch):
    _assert_driver_not_called(
        backup_fixture, monkeypatch, mode="offline",
        expected_error="unconfigured_external_write",
    )


def test_disabled_mode_blocks_before_hf_driver(backup_fixture, monkeypatch):
    _assert_driver_not_called(
        backup_fixture, monkeypatch, mode="disabled",
        expected_error="unconfigured_external_write",
    )


def test_allowlist_without_hf_fails_closed_before_hf_driver(
    backup_fixture, monkeypatch: pytest.MonkeyPatch
):
    _assert_driver_not_called(
        backup_fixture, monkeypatch, mode="allowlist", allowlist="example.com",
        expected_error="unconfigured_external_write",
    )


def test_unknown_repo_fails_closed_before_hf_driver(
    backup_fixture, monkeypatch: pytest.MonkeyPatch
):
    _allow_hf(monkeypatch)
    monkeypatch.setenv("HF_BACKUP_REPO", "attacker/arbitrary-dataset")
    calls: list[str] = []

    class SentinelHfApi:
        def __init__(self, *args, **kwargs):
            calls.append("HfApi")
            raise AssertionError("unknown destination must not reach HfApi")

    _install_fake_hf(monkeypatch, SentinelHfApi)

    assert backup.backup_to_hf(10) is False
    assert calls == []
    assert backup.get_backup_status()["last_error"] == "backup_destination_not_approved"
    assert not list(backup_fixture.parent.glob("*.db.tmp"))


def test_wrong_path_is_rejected_before_egress_pep(
    monkeypatch: pytest.MonkeyPatch,
):
    _allow_hf(monkeypatch)
    policy_calls: list[str] = []
    monkeypatch.setattr(backup, "enforce_egress_policy", policy_calls.append)

    with pytest.raises(EgressDeniedError) as excinfo:
        backup._guard_hf_write(
            repo_id="checken9x/scp-backup",
            repo_type="dataset",
            path_in_repo="not-approved.db",
        )

    assert excinfo.value.reason == "backup path is not approved"
    assert policy_calls == []


@pytest.mark.parametrize(
    "path_in_repo",
    [
        "../v13.db",
        "v13.db?x=1",
        "v13.db#x",
        "backups/../v13.db",
        "backups/v13_20260909.db",
        "backups/v13_20260909_0102.db",
        "backups/v13_20260909_010203.txt",
        "backups/v13_20260909_010203.db.extra",
        "backups/v13_20260909_010203.db?x=1",
        "backups/v13_20260909_010203.db#x",
        "backups/v13_20260909_010203.db/..",
    ],
)
def test_path_variants_fail_closed_before_egress_or_hf_driver(
    monkeypatch: pytest.MonkeyPatch, path_in_repo: str
):
    _allow_hf(monkeypatch)
    policy_calls: list[str] = []
    driver_calls: list[str] = []
    monkeypatch.setattr(backup, "enforce_egress_policy", policy_calls.append)

    class SentinelHfApi:
        def __init__(self, *args, **kwargs):
            driver_calls.append("HfApi")
            raise AssertionError("rejected path must not reach the HF driver")

    _install_fake_hf(monkeypatch, SentinelHfApi)

    with pytest.raises(EgressDeniedError) as excinfo:
        backup._guard_hf_write(
            repo_id="checken9x/scp-backup",
            repo_type="dataset",
            path_in_repo=path_in_repo,
        )

    assert excinfo.value.reason == "backup path is not approved"
    assert policy_calls == []
    assert driver_calls == []


def test_approved_target_uses_fake_transport_and_cleans_temp(
    backup_fixture, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    _allow_hf(monkeypatch)
    calls: list[tuple] = []
    temp_paths: list[str] = []
    secret = "fixture-secret"

    class FakeHfApi:
        def __init__(self, *, endpoint, token):
            calls.append(("init", endpoint))
            assert token == secret

        def create_repo(self, **kwargs):
            calls.append(("create_repo", kwargs))

        def upload_file(self, **kwargs):
            temp_paths.append(str(kwargs["path_or_fileobj"]))
            calls.append(("upload_file", kwargs["path_in_repo"]))

    _install_fake_hf(monkeypatch, FakeHfApi)

    with caplog.at_level(logging.DEBUG, logger="scp.github_backup"):
        assert backup.backup_to_hf(10) is True

    assert [entry[0] for entry in calls] == [
        "init",
        "create_repo",
        "upload_file",
        "upload_file",
    ]
    assert calls[1][1]["repo_id"] == "checken9x/scp-backup"
    assert calls[2][1] == "v13.db"
    assert calls[3][1].startswith("backups/v13_")
    assert calls[3][1].endswith(".db")
    assert temp_paths and all(not Path(path).exists() for path in temp_paths)
    assert backup.get_backup_status()["last_error"] is None
    assert not any(secret in record.getMessage() for record in caplog.records)


def test_token_is_read_at_attempt_time_and_missing_token_fails_closed(
    backup_fixture, monkeypatch: pytest.MonkeyPatch
):
    _allow_hf(monkeypatch)
    monkeypatch.delenv("HF_TOKEN", raising=False)
    calls: list[str] = []

    class SentinelHfApi:
        def __init__(self, *args, **kwargs):
            calls.append("HfApi")

    _install_fake_hf(monkeypatch, SentinelHfApi)
    assert backup.backup_to_hf(10) is False
    assert calls == []
    assert backup.get_backup_status()["last_error"] == "no_hf_token"


def test_explicit_allowlist_uses_current_attempt_token(
    backup_fixture, monkeypatch: pytest.MonkeyPatch
):
    _allow_hf(monkeypatch)
    calls: list[str] = []

    class FakeHfApi:
        def __init__(self, *, endpoint, token):
            calls.append(token)

        def create_repo(self, **kwargs):
            pass

        def upload_file(self, **kwargs):
            pass

    _install_fake_hf(monkeypatch, FakeHfApi)
    monkeypatch.setenv("HF_TOKEN", "attempt-secret")
    assert backup.backup_to_hf(10) is True
    assert calls == ["attempt-secret"]


def test_create_repo_failure_is_terminal_and_secret_safe(
    backup_fixture, monkeypatch: pytest.MonkeyPatch, caplog
):
    _allow_hf(monkeypatch)
    secret = "fixture-secret"
    calls: list[str] = []

    class FakeHfApi:
        def __init__(self, *, endpoint, token):
            assert token == secret

        def create_repo(self, **kwargs):
            calls.append("create_repo")
            raise RuntimeError(f"third-party failure contains {secret}")

        def upload_file(self, **kwargs):
            calls.append("upload_file")
            raise AssertionError("upload_file must not follow create_repo failure")

    _install_fake_hf(monkeypatch, FakeHfApi)

    with caplog.at_level(logging.DEBUG, logger="scp.github_backup"):
        assert backup.backup_to_hf(10) is False

    assert calls == ["create_repo"]
    status = backup.get_backup_status()
    assert status["last_error"] == "create_repo_failed"
    assert status["consecutive_failures"] == 1
    assert not any(secret in record.getMessage() for record in caplog.records)
    assert not any("third-party failure" in record.getMessage() for record in caplog.records)
    assert not list(backup_fixture.parent.glob("*.db.tmp"))


def test_each_upload_has_its_own_egress_guard(
    backup_fixture, monkeypatch: pytest.MonkeyPatch
):
    _allow_hf(monkeypatch)
    calls: list[str] = []
    policy_calls: list[str] = []

    def deny_snapshot(url: str):
        policy_calls.append(url)
        if len(policy_calls) == 3:
            raise EgressDeniedError(url, "fixture denial")

    monkeypatch.setattr(backup, "enforce_egress_policy", deny_snapshot)

    class FakeHfApi:
        def __init__(self, *, endpoint, token):
            pass

        def create_repo(self, **kwargs):
            calls.append("create_repo")

        def upload_file(self, **kwargs):
            calls.append(kwargs["path_in_repo"])

    _install_fake_hf(monkeypatch, FakeHfApi)

    assert backup.backup_to_hf(10) is True
    assert calls == ["create_repo", "v13.db"]
    assert len(policy_calls) == 3
    assert backup.get_backup_status()["last_error"] == "snapshot_upload:EgressDeniedError"
