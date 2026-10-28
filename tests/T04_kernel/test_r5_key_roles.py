"""R5 · key roles: each SCP signing role reads its own key, and keys must differ in value.

Splitting variable names is not enough: an operator copying one value into every variable
recreates the single-key hole R5 closes. These tests pin that the kernel journal and the audit
ledger refuse to run on a reused key, and that the ledger tree head is signed with K_ledger.

Zero-mock: real TaskKernel / TraceLedger / AutonomousAuditLedger on files under tmp_path.
"""
from __future__ import annotations

import hashlib
import hmac

import pytest

from scp.task_kernel import KernelError, TaskKernel
from scp.trace_ledger import TraceLedger

_CAPABILITY = "r5-roles-capability-key-0123456789abcdef"
_JOURNAL = "r5-roles-journal-key-0123456789abcdef!!"
_LEDGER = "r5-roles-ledger-key-0123456789abcdef!!!"


@pytest.fixture
def roles(monkeypatch):
    monkeypatch.setenv("SCP_CAPABILITY_SECRET", _CAPABILITY)
    monkeypatch.setenv("SCP_JOURNAL_SECRET", _JOURNAL)
    monkeypatch.setenv("SCP_LEDGER_HMAC_KEY", _LEDGER)
    for name in ("SCP_JOURNAL_SECRET_FILE", "SCP_LEDGER_HMAC_KEY_FILE"):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def _ledger_with_entries(path) -> TraceLedger:
    ledger = TraceLedger(path)
    for i in range(3):
        ledger.append(event=f"r5-step-{i}")
    return ledger


def _sth_payload(sth: dict) -> bytes:
    root_hex = sth["merkle_root"].split(":", 1)[1]
    return f"sth:{sth['tree_size']}:{root_hex}:{sth['tail_hash'] or ''}".encode()


def test_kernel_refuses_a_journal_key_that_reuses_the_capability_key(tmp_path, roles):
    roles.setenv("SCP_JOURNAL_SECRET", _CAPABILITY)
    kernel = TaskKernel(tmp_path / "reused.sqlite3")
    try:
        with pytest.raises(KernelError, match="must not reuse the value of SCP_CAPABILITY_SECRET"):
            kernel.create_task("r5-reused", "r5-owner", "goal", "R0", input_hash="sha256:" + "0" * 64)
    finally:
        kernel.close()


def test_tree_head_is_signed_with_k_ledger_by_default(tmp_path, roles):
    ledger = _ledger_with_entries(tmp_path / "trace.jsonl")
    sth = ledger.signed_tree_head()
    payload = _sth_payload(sth)
    assert sth["signature"] == hmac.new(_LEDGER.encode(), payload, hashlib.sha256).hexdigest()
    assert sth["signature"] != hmac.new(_CAPABILITY.encode(), payload, hashlib.sha256).hexdigest()
    assert sth["signature"] != hmac.new(_JOURNAL.encode(), payload, hashlib.sha256).hexdigest()


def test_tree_head_without_k_ledger_fails_closed(tmp_path, roles):
    roles.delenv("SCP_LEDGER_HMAC_KEY")
    ledger = _ledger_with_entries(tmp_path / "trace.jsonl")
    with pytest.raises(ValueError, match="SCP_LEDGER_HMAC_KEY"):
        ledger.signed_tree_head()


@pytest.mark.parametrize("reused", [_CAPABILITY, _JOURNAL])
def test_ledger_key_reusing_another_role_is_refused(tmp_path, roles, reused):
    from scp.core.autonomous_ledger import AutonomousAuditLedger

    roles.setenv("SCP_LEDGER_HMAC_KEY", reused)
    ledger = _ledger_with_entries(tmp_path / "trace.jsonl")
    with pytest.raises(ValueError, match="must not reuse the value"):
        ledger.signed_tree_head()
    with pytest.raises(ValueError, match="must not reuse the value"):
        AutonomousAuditLedger(ledger_path=tmp_path / "audit.jsonl")


def test_autonomous_ledger_reads_k_ledger_from_the_file_channel(tmp_path, roles):
    from scp.core.autonomous_ledger import AutonomousAuditLedger

    key_file = tmp_path / "ledger.key"
    key_file.write_text("r5-roles-ledger-key-from-file-0123456789\n", encoding="utf-8")
    roles.delenv("SCP_LEDGER_HMAC_KEY")
    roles.setenv("SCP_LEDGER_HMAC_KEY_FILE", str(key_file))
    audit = AutonomousAuditLedger(ledger_path=tmp_path / "audit.jsonl")
    assert audit.hmac_key == b"r5-roles-ledger-key-from-file-0123456789"


def test_registry_declares_one_key_per_signing_role():
    from scp.security.secret_registry import SECRET_PURPOSES

    roles = ("SCP_CAPABILITY_SECRET", "SCP_JOURNAL_SECRET", "SCP_LEDGER_HMAC_KEY", "SCP_VERIFIER_SECRET")
    specs = [SECRET_PURPOSES[name] for name in roles]
    assert len({spec.purpose for spec in specs}) == len(roles)
    assert SECRET_PURPOSES["SCP_JOURNAL_SECRET"].file_variant is True
    assert SECRET_PURPOSES["SCP_LEDGER_HMAC_KEY"].file_variant is True
    assert "scp.task_kernel_parts.lease_manager" in SECRET_PURPOSES["SCP_JOURNAL_SECRET"].consumers
    assert "scp.task_kernel_parts.checkpoint" in SECRET_PURPOSES["SCP_JOURNAL_SECRET"].consumers
