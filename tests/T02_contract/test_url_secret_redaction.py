"""[AUDIT-FIX med-1 + low-8] Contract test — API key KHÔNG được leak vào logs.

Root cause (external audit med-1):
  * scp/core/api_utils.py fetch_with_retry log FULL URL (chứa ?api_key=SECRET)
    ở 3 nhánh except — probe đã chứng minh key vào WARNING/ERROR logs.
  * scp/policy/egress.py EgressDeniedError embed full URL vào message.
Fix:
  * redact_query_secrets() helper (canonical, scp.core.api_utils) — mọi log/raise
    URL phải qua đây; host+path giữ nguyên, query value nhạy cảm → [REDACTED].
  * EgressDeniedError message construction redact trước khi nhúng message.
  * scp/meta/why_sources/nasa.py redact exception text trước khi log (low-8).

Regression contract: log/exception chứa '[REDACTED]' và KHÔNG chứa secret.
"""
import logging

import pytest

from scp.core.api_utils import fetch_with_retry, redact_query_secrets
from scp.policy.egress import EgressDeniedError

SECRET = "SK-ULTRA-SECRET-123"
SECRET_URL = f"https://api.nasa.gov/planetary/apod?api_key={SECRET}&year=2026"


# ---------------------------------------------------------------------------
# redact_query_secrets — helper unit contract
# ---------------------------------------------------------------------------
def test_redact_masks_secret_and_keeps_host_path_and_normal_params():
    out = redact_query_secrets(SECRET_URL)
    assert SECRET not in out
    assert "[REDACTED]" in out
    assert "api.nasa.gov" in out
    assert "/planetary/apod" in out
    assert "year=2026" in out  # param thường giữ NGUYÊN text (không re-encode)


@pytest.mark.parametrize(
    "name",
    [
        "api_key", "apiKey", "api-key", "API_KEY",
        "key", "token", "access_key", "accessKey",
        "session_token", "client_secret", "password", "signature",
    ],
)
def test_redact_covers_secret_param_variants(name):
    url = f"https://h.example/p?{name}={SECRET}&x=1"
    out = redact_query_secrets(url)
    assert SECRET not in out, f"param {name!r} không bị redact: {out}"
    assert "[REDACTED]" in out


def test_redact_leaves_non_secret_urls_untouched():
    url = "https://h.example/p?year=2026&q=abc&fields=capital"
    assert redact_query_secrets(url) == url
    assert redact_query_secrets("https://h.example/p") == "https://h.example/p"
    assert redact_query_secrets("") == ""


# ---------------------------------------------------------------------------
# EgressDeniedError — message contract
# ---------------------------------------------------------------------------
def test_egress_denied_error_message_redacted_but_host_visible():
    err = EgressDeniedError("api.nasa.gov", "not allowlisted", url=SECRET_URL)
    msg = str(err)
    assert SECRET not in msg, f"secret leak trong EgressDeniedError message: {msg}"
    assert "[REDACTED]" in msg
    assert "api.nasa.gov" in msg  # host+path vẫn visible để debug


def test_egress_denied_error_without_url_still_constructs():
    err = EgressDeniedError("example.test", "reason")
    assert "example.test" in str(err)


# ---------------------------------------------------------------------------
# fetch_with_retry — logging contract (caplog trên logger thật)
# ---------------------------------------------------------------------------
def test_fetch_with_retry_policy_violation_log_is_redacted(caplog, monkeypatch):
    monkeypatch.setenv("SCP_EGRESS_MODE", "deny")  # offline-deterministic
    with caplog.at_level(logging.WARNING, logger="scp.api_utils"):
        result = fetch_with_retry(SECRET_URL)
    assert result is None  # policy violation → no-retry, return None (contract cũ)
    joined = "\n".join(record.getMessage() for record in caplog.records)
    assert joined, "fetch_with_retry phải ghi log policy violation"
    assert SECRET not in joined
    assert "[REDACTED]" in joined


def test_fetch_with_retry_json_error_log_is_redacted(caplog, monkeypatch):
    import scp.core.url_fetcher as url_fetcher

    def _fake_fetch(target):
        return b"not-json{"

    monkeypatch.setenv("SCP_EGRESS_MODE", "open")
    monkeypatch.setattr(url_fetcher, "_safe_fetch_url", _fake_fetch)
    with caplog.at_level(logging.WARNING, logger="scp.api_utils"):
        result = fetch_with_retry(SECRET_URL)
    assert result is None
    joined = "\n".join(record.getMessage() for record in caplog.records)
    assert SECRET not in joined
    assert "[REDACTED]" in joined


# ---------------------------------------------------------------------------
# [AUDIT-FIX low-8] nasa.py — exception carrying URL không được vào log thô
# ---------------------------------------------------------------------------
def test_query_nasa_logs_redacted_exception(caplog, monkeypatch):
    import scp.meta.why_sources.nasa as nasa

    def _boom(*args, **kwargs):
        raise ValueError(f"unparseable URL {SECRET_URL}")

    monkeypatch.setattr(nasa, "safe_urlopen", _boom)
    with caplog.at_level(logging.WARNING, logger="scp.why.sources.nasa"):
        out = nasa.query_nasa("black hole")
    assert out is None
    joined = "\n".join(record.getMessage() for record in caplog.records)
    assert joined
    assert SECRET not in joined
    assert "[REDACTED]" in joined
