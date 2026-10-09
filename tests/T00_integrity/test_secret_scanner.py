"""SEC-02: Secret Scanner Unit Tests.

Verifies that the secret scanner catches hardcoded secrets (private keys,
tokens, API keys) in diffs, while permitting safe placeholders and test
redaction assertions.
"""
from __future__ import annotations

from tools.scan_secrets import scan_diff_text


def test_secret_scanner_detects_private_key_in_diff() -> None:
    diff = """\
--- a/scp/config.py
+++ b/scp/config.py
@@ -10,1 +10,2 @@
+PRIVATE_KEY = \"\"\"-----BEGIN RSA PRIVATE KEY-----
+MIIEowIBAAKCAQEA0...
+\"\"\"
"""
    findings = scan_diff_text(diff)
    assert len(findings) == 1
    assert "Private Key header" in findings[0]


def test_secret_scanner_detects_aws_key_in_diff() -> None:
    diff = """\
--- a/scp/cloud.py
+++ b/scp/cloud.py
@@ -5,1 +5,2 @@
+AWS_KEY = "AKIAIOSFODNN7EXAMPLE"
"""
    findings = scan_diff_text(diff)
    assert len(findings) == 1
    assert "AWS Access Key ID" in findings[0]


def test_secret_scanner_detects_github_pat_in_diff() -> None:
    diff = """\
--- a/scp/github_client.py
+++ b/scp/github_client.py
@@ -1,1 +1,2 @@
+GITHUB_TOKEN = "ghp_1234567890abcdefghijklmnopqrstuvwxyz1234"
"""
    findings = scan_diff_text(diff)
    assert len(findings) == 1
    assert "GitHub Personal Access Token" in findings[0]


def test_secret_scanner_permits_safe_placeholders() -> None:
    diff = """\
--- a/scp/config.py
+++ b/scp/config.py
@@ -1,1 +1,3 @@
+TOKEN = "ci-only-runtime-placeholder"
+DUMMY_KEY = "dummy_secret_value"
+MOCK_TOKEN = "test-token"
"""
    findings = scan_diff_text(diff)
    assert len(findings) == 0


def test_secret_scanner_ignores_unmodified_lines() -> None:
    diff = """\
--- a/scp/config.py
+++ b/scp/config.py
@@ -1,3 +1,3 @@
-OLD_TOKEN = "ghp_1234567890abcdefghijklmnopqrstuvwxyz1234"
 UNCHANGED = "ghp_1234567890abcdefghijklmnopqrstuvwxyz1234"
+NEW_TOKEN = "ci-only-runtime-placeholder"
"""
    findings = scan_diff_text(diff)
    assert len(findings) == 0


def test_secret_scanner_detects_google_gemini_api_key_in_diff() -> None:
    diff = """\
--- a/scp/gemini_provider.py
+++ b/scp/gemini_provider.py
@@ -1,1 +1,2 @@
+GEMINI_KEY = "AIzaSyAbCdEfGhIjKlMnOpQrStUvWxYz1234567"
"""
    findings = scan_diff_text(diff)
    assert len(findings) == 1
    assert "Google / Gemini API Key" in findings[0]


def test_secret_scanner_detects_anthropic_api_key_in_diff() -> None:
    diff = """\
--- a/scp/anthropic_provider.py
+++ b/scp/anthropic_provider.py
@@ -1,1 +1,2 @@
+CLAUDE_KEY = "sk-ant-api03-abcdef1234567890abcdef1234567890"
"""
    findings = scan_diff_text(diff)
    assert len(findings) == 1
    assert "API / Provider Secret Key" in findings[0]


def test_secret_scanner_detects_prefixed_and_suffixed_secret_variables_sec09() -> None:
    diff = """\
--- a/scp/security/auth.py
+++ b/scp/security/auth.py
@@ -10,1 +10,6 @@
+SCP_JWT_SECRET = "super_secret_jwt_token_12345"
+db_password = "production_database_password_98765"
+SCP_ADMIN_KEY = "admin_master_key_123"
+JWT_SECRET = "another_secret_token_12"
+MY_API_KEY = "custom_third_party_api_key_888"
"""
    findings = scan_diff_text(diff)
    assert len(findings) == 5
    for f in findings:
        assert "Literal secret assignment" in f

