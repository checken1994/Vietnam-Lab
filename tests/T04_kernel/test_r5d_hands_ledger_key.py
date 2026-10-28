"""R5-D residual · HandsExecutor signs its audit ledger with K_ledger resolved by the registry.

R5-D (3f866fb9) declared ``scp.hands.hands_executor`` a consumer of ``SCP_LEDGER_HMAC_KEY`` but the
executor kept reading ``os.environ`` directly and, when that env var was absent, derived the ledger
key from the capability secret. Consequences pinned here:

* A  ``SCP_LEDGER_HMAC_KEY_FILE`` only      -> hands must sign with the file value (was: derived key).
* B  K_ledger reuses capability/journal key -> refused with the registry ValueError (was: accepted).
* C  env and file differ                    -> the file wins, as in the registry (was: env residue).
* D  ``_FILE`` set but unreadable/empty     -> no key; ``require_hmac`` fails closed (was: derived).
* cross  an independent auditor that resolves K_ledger through the registry verifies hands entries.
* E  no channel configured                  -> legacy derived key kept, but a WARNING is logged.
* F  env-only K_ledger (control)            -> unchanged.
* G  injected ledger (control)              -> used verbatim; the executor resolves no key for it.

Zero-mock: real HandsExecutor / PCController / CapabilityAuthority / AutonomousAuditLedger /
secret_registry on files under tmp_path. Key values are random per test and only compared, never
printed.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import secrets
from pathlib import Path

import pytest

from scp.core.autonomous_ledger import AutonomousAuditLedger
from scp.hands.hands_executor import HandsExecutor
from scp.pc_control.pc_controller import PCController
from scp.security.capability_epoch import CapabilityAuthority

_LEGACY_LABEL = b"scp.autonomous_audit_ledger.v1"
_LEDGER_LOGGER = "scp.hands.hands_executor"
_CLEARED_ENV = (
    "SCP_LEDGER_HMAC_KEY",
    "SCP_LEDGER_HMAC_KEY_FILE",
    "SCP_REQUIRE_LEDGER_HMAC",
    "SCP_JOURNAL_SECRET_FILE",
    "SCP_CAPABILITY_SECRET_FILE",
    "SCP_PC_WORKING_DIR",
    "SCP_HANDS_DATA_DIR",
    "SCP_SECURITY_DATA_DIR",
)


def _random(prefix: str) -> str:
    return f"{prefix}-{secrets.token_hex(24)}"


class _Roles:
    def __init__(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        self.monkeypatch = monkeypatch
        self.tmp_path = tmp_path
        self.capability = _random("cap")
        self.journal = _random("jrn")
        monkeypatch.setenv("SCP_CAPABILITY_SECRET", self.capability)
        monkeypatch.setenv("SCP_JOURNAL_SECRET", self.journal)

    def value_of(self, role: str) -> str:
        return {"SCP_CAPABILITY_SECRET": self.capability, "SCP_JOURNAL_SECRET": self.journal}[role]

    @property
    def legacy_derived(self) -> bytes:
        return hmac.new(self.capability.encode("utf-8"), _LEGACY_LABEL, hashlib.sha256).digest()

    def set_env_key(self, value: str) -> None:
        self.monkeypatch.setenv("SCP_LEDGER_HMAC_KEY", value)

    def set_file_key(self, value: str | None, name: str = "kledger.secret") -> Path:
        """Point SCP_LEDGER_HMAC_KEY_FILE at a file; ``None`` leaves the file absent."""
        path = self.tmp_path / name
        if value is not None:
            path.write_text(value, encoding="utf-8")
        self.monkeypatch.setenv("SCP_LEDGER_HMAC_KEY_FILE", str(path))
        return path

    @property
    def ledger_path(self) -> Path:
        return self.tmp_path / "hands" / "autonomous_ledger.jsonl"

    def executor(self, audit_ledger: AutonomousAuditLedger | None = None) -> HandsExecutor:
        workspace = self.tmp_path / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)
        authority = CapabilityAuthority(self.tmp_path / "capability_state.json")
        return HandsExecutor(
            controller=PCController(working_dir=workspace),
            capability_authority=authority,
            data_dir=self.tmp_path / "hands",
            audit_ledger=audit_ledger,
        )


@pytest.fixture
def roles(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> _Roles:
    for name in _CLEARED_ENV:
        monkeypatch.delenv(name, raising=False)
    return _Roles(monkeypatch, tmp_path)


def _run_status(executor: HandsExecutor) -> dict:
    token = executor.capability_authority.issue("hands:pc.status")
    return asyncio.run(executor.execute("pc.status", capability_token=token))


def _autonomous_records(path: Path) -> list[dict]:
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            fields = json.loads(line).get("fields", {})
            if fields.get("event") in ("AUTONOMOUS_TOOL_INTENT", "AUTONOMOUS_TOOL_RESULT"):
                records.append(fields)
    return records


# --- A ------------------------------------------------------------------------------------------
def test_a_file_only_ledger_key_is_used(roles: _Roles) -> None:
    file_key = _random("kl")
    roles.set_file_key(file_key)

    executor = roles.executor()

    assert executor.audit_ledger.hmac_key == file_key.encode("utf-8")
    assert executor.audit_ledger.hmac_key != roles.legacy_derived
    assert executor.audit_ledger.require_hmac is True


# --- B ------------------------------------------------------------------------------------------
@pytest.mark.parametrize("channel", ["env", "file"])
@pytest.mark.parametrize("reused_role", ["SCP_CAPABILITY_SECRET", "SCP_JOURNAL_SECRET"])
def test_b_ledger_key_reusing_another_role_is_refused(roles: _Roles, channel: str, reused_role: str) -> None:
    reused = roles.value_of(reused_role)
    if channel == "env":
        roles.set_env_key(reused)
    else:
        roles.set_file_key(reused)

    with pytest.raises(ValueError, match=f"SCP_LEDGER_HMAC_KEY must not reuse the value of {reused_role}"):
        roles.executor()


# --- C ------------------------------------------------------------------------------------------
def test_c_file_channel_wins_over_env_residue(roles: _Roles) -> None:
    file_key = _random("kl")
    env_residue = _random("kl2")
    roles.set_env_key(env_residue)
    roles.set_file_key(file_key)

    executor = roles.executor()

    assert executor.audit_ledger.hmac_key == file_key.encode("utf-8")
    assert executor.audit_ledger.hmac_key != env_residue.encode("utf-8")


# --- D ------------------------------------------------------------------------------------------
@pytest.mark.parametrize("file_state", ["missing", "empty"])
def test_d_unreadable_file_channel_fails_closed(roles: _Roles, file_state: str) -> None:
    roles.set_file_key(None if file_state == "missing" else "")

    executor = roles.executor()

    assert executor.audit_ledger.hmac_key is None
    assert executor.audit_ledger.require_hmac is True
    with pytest.raises(RuntimeError, match="HMAC key is required but missing"):
        executor.audit_ledger.commit_intent(
            task_id="t-d", step_id="s-d", tool_name="pc.status", params={},
            capability_token={"token_id": "tok-d", "signature": "sig-d"},
        )
    # The real dispatch path is refused before the action runs: nothing reaches the ledger.
    with pytest.raises(RuntimeError, match="HMAC key is required but missing"):
        _run_status(executor)
    assert _autonomous_records(roles.ledger_path) == []


# --- cross: an independent auditor using the registry key ------------------------------------------
@pytest.mark.parametrize("channel", ["file_only", "env_and_file", "env_only"])
def test_cross_auditor_with_registry_key_verifies_hands_entries(roles: _Roles, channel: str) -> None:
    ledger_key = _random("kl")
    if channel == "file_only":
        roles.set_file_key(ledger_key)
    elif channel == "env_and_file":
        roles.set_env_key(_random("kl2"))
        roles.set_file_key(ledger_key)
    else:
        roles.set_env_key(ledger_key)

    result = _run_status(roles.executor())
    assert result["ledgerSeq"] == 2

    auditor = AutonomousAuditLedger(ledger_path=roles.ledger_path, require_hmac=True)
    verification = auditor.verify_provenance()

    assert verification["errors"] == []
    assert verification["hash_chain_valid"] is True
    assert verification["entries"] == 2
    assert len(_autonomous_records(roles.ledger_path)) == 2


# --- E ------------------------------------------------------------------------------------------
def test_e_unconfigured_ledger_key_keeps_legacy_derivation(roles: _Roles) -> None:
    executor = roles.executor()

    assert executor.audit_ledger.hmac_key == roles.legacy_derived
    assert executor.audit_ledger.hmac_key != roles.capability.encode("utf-8")
    assert executor.audit_ledger.require_hmac is True


def test_e_unconfigured_ledger_key_warns_about_legacy_derivation(
    roles: _Roles, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger=_LEDGER_LOGGER):
        roles.executor()

    warnings = [
        record for record in caplog.records
        if record.name == _LEDGER_LOGGER and record.levelno == logging.WARNING
        and "SCP_LEDGER_HMAC_KEY" in record.getMessage()
    ]
    assert len(warnings) == 1
    assert roles.capability not in warnings[0].getMessage()


def test_configured_ledger_key_does_not_warn(roles: _Roles, caplog: pytest.LogCaptureFixture) -> None:
    roles.set_env_key(_random("kl"))
    with caplog.at_level(logging.WARNING, logger=_LEDGER_LOGGER):
        roles.executor()

    assert [r for r in caplog.records if r.name == _LEDGER_LOGGER and r.levelno >= logging.WARNING] == []


# --- F (control) --------------------------------------------------------------------------------
def test_f_env_only_ledger_key_is_used(roles: _Roles) -> None:
    env_key = _random("kl")
    roles.set_env_key(env_key)

    executor = roles.executor()

    assert executor.audit_ledger.hmac_key == env_key.encode("utf-8")
    assert executor.audit_ledger.hmac_key != roles.legacy_derived


# --- G (control) --------------------------------------------------------------------------------
def test_g_injected_ledger_is_used_verbatim(roles: _Roles) -> None:
    injected_key = _random("inj")
    injected = AutonomousAuditLedger(
        ledger_path=roles.tmp_path / "injected.jsonl", hmac_key=injected_key, require_hmac=True
    )
    # A reused env value is the auditor's problem when it builds its own ledger; hands neither
    # reads nor needs K_ledger when the caller hands it a ledger.
    roles.set_env_key(roles.capability)

    executor = roles.executor(audit_ledger=injected)

    assert executor.audit_ledger is injected
    assert executor.audit_ledger.hmac_key == injected_key.encode("utf-8")
