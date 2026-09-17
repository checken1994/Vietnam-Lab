"""[A3 NEW-01 HIGH / SMTP egress guard] Email notifications go through the
operator opt-in gate BEFORE any SMTP transport is touched.

Falsification background (AUDIT-20260909): ``UserNotificationSystem._send_email``
called ``smtplib.SMTP(host, port)`` + ``starttls()`` + ``login()`` +
``send_message()`` directly — an external write (risk tier R2) with NO egress
choke, NO allowlist and NO approval. The HTTP choke ``enforce_egress_policy``
cannot cover SMTP: it returns early for non-HTTP(S) schemes
(``scp/security/url_safety.py:182-183``), so the guard must live in
``scp/runtime/notifications.py`` itself.

Contracts pinned here (all fail-closed):
  (a) approval env missing / not exactly "1" → return False, smtplib.SMTP is
      NEVER constructed (sentinel count 0), no method sequence runs.
  (b) approval present but host not in SCP_NOTIFICATION_SMTP_ALLOWLIST →
      same denial; empty/missing allowlist denies EVERYTHING (default deny).
  (c) approval + exact host match → fake SMTP runs the full sequence
      (constructor → starttls → login → send_message) and _send_email
      returns True.
  (d) allowlist matching is exact-token, case-insensitive: no substring /
      prefix / suffix match, and an embedded port or query in the host is
      NOT normalized away (fail-closed).
  (e) no-log contract: the denied path emits ONLY the two static DEBUG
      denial lines — the SMTP host, username and password never appear in
      the log stream, even at DEBUG level.

No-mock discipline (T03): the fake lives at the SMTP TRANSPORT layer only —
``smtplib.SMTP`` is replaced so no socket can ever be opened; as a belt-and-
braces hermetic guard, ``socket.socket``/``socket.create_connection`` are
patched to raise if anything in the code path attempts a real connection.
The notifications logic and the guard itself run for real (FA-02: no
skip/xfail, nothing loosened).
"""
from __future__ import annotations

import logging
import socket
import sys
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

import smtplib  # noqa: E402

from scp.runtime.notifications import (  # noqa: E402
    NotificationConfig,
    UserNotificationSystem,
)

APPROVAL_ENV = "SCP_NOTIFICATION_SMTP_APPROVED"
ALLOWLIST_ENV = "SCP_NOTIFICATION_SMTP_ALLOWLIST"


# ----------------------------------------------------------- fake transport
class _FakeSMTP:
    """SMTP transport fake — counts constructions, records call sequence."""

    # Class-level sentinel: survives across instances within one test.
    construct_calls = 0
    last_init_args: tuple | None = None
    sequences: list[list[str]] = []

    def __init__(self, host, port, *args, **kwargs):
        type(self).construct_calls += 1
        type(self).last_init_args = (host, port)
        self._calls: list[str] = []
        type(self).sequences.append(self._calls)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def starttls(self, *args, **kwargs):
        self._calls.append("starttls")

    def login(self, username, password):
        self._calls.append(f"login:{username}:{password}")

    def send_message(self, msg):
        self._calls.append("send_message")


# ----------------------------------------------------------- helpers
@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    """No real network, ever: fake the SMTP transport and hard-fail on any
    attempt to open a real socket. Also scrub the two opt-in env vars so the
    outer environment can never flip a DENY case into ALLOW (or vice versa)."""
    _FakeSMTP.construct_calls = 0
    _FakeSMTP.last_init_args = None
    _FakeSMTP.sequences = []
    monkeypatch.setattr(smtplib, "SMTP", _FakeSMTP)

    def _no_real_socket(*args, **kwargs):
        raise AssertionError(
            "hermetic violation: real socket attempted in SMTP egress test"
        )

    monkeypatch.setattr(socket, "socket", _no_real_socket)
    monkeypatch.setattr(socket, "create_connection", _no_real_socket)
    monkeypatch.delenv(APPROVAL_ENV, raising=False)
    monkeypatch.delenv(ALLOWLIST_ENV, raising=False)


@pytest.fixture
def notifier(tmp_path):
    return UserNotificationSystem(
        config=NotificationConfig(
            email_enabled=True,
            email_smtp_host="smtp.example.com",
            email_smtp_port=587,
            email_username="scp-bot@example.com",
            # Runtime-generated fake password (NOT a credential-shaped literal):
            # unique every run, so if this fake SMTP transport is ever swapped
            # for a real one, the test fails loudly instead of silently using a
            # hardcoded password.
            email_password=uuid.uuid4().hex,
            email_from="scp@example.com",
            email_to="owner@example.com",
        ),
        data_dir=str(tmp_path / "notif-data"),
    )


def _notification() -> dict:
    return {
        "id": "notif_test",
        "timestamp": 0.0,
        "event_type": "governance_event",
        "severity": "critical",
        "title": "t",
        "message": "m",
        "details": {},
        "actions_taken": [],
    }


# ----------------------------------------------------------- (a) no approval
def test_denied_without_approval_env(notifier):
    monkey_free_host = "smtp.example.com"
    assert notifier._send_email(_notification()) is False
    assert _FakeSMTP.construct_calls == 0
    assert _FakeSMTP.sequences == []
    assert notifier.config.email_smtp_host == monkey_free_host  # untouched


def test_denied_when_approval_is_not_exactly_1(notifier, monkeypatch):
    # Value semantics mirror SCP_EGRESS_MODE (url_safety): env values are
    # whitespace-stripped then compared, so ONLY "1" (after strip) approves.
    # Anything else — "0", "true", "yes", empty, "1x" — is DENY (fail-closed).
    for bad_value in ("0", "true", "yes", "", "1x", "0 "):
        monkeypatch.setenv(APPROVAL_ENV, bad_value)
        monkeypatch.setenv(ALLOWLIST_ENV, "smtp.example.com")
        assert notifier._send_email(_notification()) is False, repr(bad_value)
    assert _FakeSMTP.construct_calls == 0


# ------------------------------------------------- (b) approval w/o allowlist
def test_denied_with_approval_but_missing_allowlist(notifier, monkeypatch):
    monkeypatch.setenv(APPROVAL_ENV, "1")
    monkeypatch.delenv(ALLOWLIST_ENV, raising=False)
    assert notifier._send_email(_notification()) is False
    assert _FakeSMTP.construct_calls == 0


def test_denied_with_approval_but_empty_allowlist(notifier, monkeypatch):
    monkeypatch.setenv(APPROVAL_ENV, "1")
    monkeypatch.setenv(ALLOWLIST_ENV, "")
    assert notifier._send_email(_notification()) is False
    assert _FakeSMTP.construct_calls == 0


def test_denied_with_approval_but_whitespace_only_allowlist(notifier, monkeypatch):
    monkeypatch.setenv(APPROVAL_ENV, "1")
    monkeypatch.setenv(ALLOWLIST_ENV, "  ,  ,")
    assert notifier._send_email(_notification()) is False
    assert _FakeSMTP.construct_calls == 0


def test_denied_host_not_in_allowlist(notifier, monkeypatch):
    monkeypatch.setenv(APPROVAL_ENV, "1")
    monkeypatch.setenv(ALLOWLIST_ENV, "smtp.other.org,mail.another.net")
    assert notifier._send_email(_notification()) is False
    assert _FakeSMTP.construct_calls == 0


# --------------------------------------- (c) approved + allowlisted → allow
def test_allowed_runs_full_smtp_sequence(notifier, monkeypatch):
    monkeypatch.setenv(APPROVAL_ENV, "1")
    monkeypatch.setenv(ALLOWLIST_ENV, "smtp.example.com")
    assert notifier._send_email(_notification()) is True
    assert _FakeSMTP.construct_calls == 1
    assert _FakeSMTP.last_init_args == ("smtp.example.com", 587)
    assert len(_FakeSMTP.sequences) == 1
    seq = _FakeSMTP.sequences[0]
    # Full sequence, in order: starttls → login → send_message. The login
    # record must carry exactly the fixture's runtime-generated credentials
    # (exact equality — no literal, no prefix/substring relaxation).
    assert seq == [
        "starttls",
        f"login:{notifier.config.email_username}:{notifier.config.email_password}",
        "send_message",
    ]


def test_allowed_with_whitespace_padded_approval(notifier, monkeypatch):
    """Positive control pinning strip semantics: the approval env value is
    whitespace-stripped BEFORE the "1" comparison (same value semantics as
    SCP_EGRESS_MODE in url_safety), so " 1 " — with surrounding spaces —
    approves and the full SMTP sequence runs. A future regression to strict
    equality (e.g. env != "1") flips this to DENY and fails here."""
    monkeypatch.setenv(APPROVAL_ENV, " 1 ")
    monkeypatch.setenv(ALLOWLIST_ENV, "smtp.example.com")
    assert notifier._send_email(_notification()) is True
    assert _FakeSMTP.construct_calls == 1
    assert _FakeSMTP.last_init_args == ("smtp.example.com", 587)
    assert len(_FakeSMTP.sequences) == 1
    assert _FakeSMTP.sequences[0] == [
        "starttls",
        f"login:{notifier.config.email_username}:{notifier.config.email_password}",
        "send_message",
    ]


def test_allowed_with_multiple_allowlist_entries(notifier, monkeypatch):
    monkeypatch.setenv(APPROVAL_ENV, "1")
    monkeypatch.setenv(ALLOWLIST_ENV, "smtp.other.org, smtp.example.com ,mail.another.net")
    assert notifier._send_email(_notification()) is True
    assert _FakeSMTP.construct_calls == 1


# ------------------------------------ (d) exact-token matching, no bleed
def test_allowlist_match_is_case_insensitive(notifier, monkeypatch):
    monkeypatch.setenv(APPROVAL_ENV, "1")
    monkeypatch.setenv(ALLOWLIST_ENV, "SMTP.Example.COM")
    assert notifier._send_email(_notification()) is True


def test_allowlist_no_substring_or_affix_match(notifier, monkeypatch):
    monkeypatch.setenv(APPROVAL_ENV, "1")
    # "example.com" is a substring of the host; "smtp.example.com.evil.com"
    # extends it on the right; "evil-smtp.example.com" extends it on the
    # left. Exact-token matching must DENY all three.
    for hostile_allowlist in (
        "example.com",
        "smtp.example.com.evil.com",
        "evil-smtp.example.com",
    ):
        monkeypatch.setenv(ALLOWLIST_ENV, hostile_allowlist)
        assert notifier._send_email(_notification()) is False, hostile_allowlist
    assert _FakeSMTP.construct_calls == 0


def test_allowlist_host_port_and_query_not_normalized_away(notifier, monkeypatch):
    monkeypatch.setenv(APPROVAL_ENV, "1")
    monkeypatch.setenv(ALLOWLIST_ENV, "smtp.example.com")
    # A host string carrying a port or query is a DIFFERENT token: the guard
    # never parses port/query out of the host, so it fails closed.
    assert notifier.config.email_smtp_host == "smtp.example.com"
    for variant_host in ("smtp.example.com:587", "smtp.example.com?x=1"):
        notifier.config.email_smtp_host = variant_host
        assert notifier._send_email(_notification()) is False, variant_host
    assert _FakeSMTP.construct_calls == 0


def test_denied_empty_host_config(notifier, monkeypatch):
    monkeypatch.setenv(APPROVAL_ENV, "1")
    monkeypatch.setenv(ALLOWLIST_ENV, "smtp.example.com")
    notifier.config.email_smtp_host = ""
    assert notifier._send_email(_notification()) is False
    assert _FakeSMTP.construct_calls == 0


# ------------------------------------------------- (e) no-log contract
def test_denied_path_never_logs_host_or_password(notifier, monkeypatch, caplog):
    """No-log contract: on the denied path the guard may emit ONLY the two
    static DEBUG denial lines (missing approval / host not allowlisted) —
    the SMTP host, username or password must never reach the log stream,
    even at DEBUG level. Both denial branches are exercised so each of the
    module's two static denial lines is covered."""
    with caplog.at_level(logging.DEBUG, logger="scp.runtime.notifications"):
        caplog.clear()
        # Denial branch 1 — approval missing (the "no approval" denied path).
        monkeypatch.delenv(APPROVAL_ENV, raising=False)
        monkeypatch.delenv(ALLOWLIST_ENV, raising=False)
        assert notifier._send_email(_notification()) is False
        # Denial branch 2 — approval present, host not allowlisted.
        monkeypatch.setenv(APPROVAL_ENV, "1")
        monkeypatch.setenv(ALLOWLIST_ENV, "smtp.other.org")
        assert notifier._send_email(_notification()) is False
    # Exactly the module's two static denial lines — nothing else, DEBUG only.
    assert len(caplog.records) == 2
    assert all(record.levelname == "DEBUG" for record in caplog.records)
    joined = "\n".join(record.getMessage() for record in caplog.records)
    assert "smtp.example.com" not in joined
    assert notifier.config.email_username not in joined
    assert notifier.config.email_password not in joined
    # The env-var references pin the records as the module's fixed denial
    # text (static constants), not dynamically built log content.
    assert "SCP_NOTIFICATION_SMTP_APPROVED" in joined
    assert "SCP_NOTIFICATION_SMTP_ALLOWLIST" in joined
