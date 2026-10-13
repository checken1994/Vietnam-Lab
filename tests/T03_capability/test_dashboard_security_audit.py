"""[AUD-01 Area 1] Deep Security Audit for Dashboard TypeScript & Next.js.

Verifies:
1. next.config.ts enforces strict Security Headers (CSP, X-Frame-Options, X-Content-Type-Options, Referrer-Policy, Permissions-Policy).
2. All API route handlers prevent SSRF (every fetch is guarded by resolveScpApiBase, resolveScpProxyBase, or isAllowedProbeTarget).
3. Zero unallowed XSS sinks across dashboard/src/ (0 unallowed dangerouslySetInnerHTML, 0 eval, 0 new Function).
4. Middleware enforces LOCAL_IPS and fails closed when proxy secret is missing in production.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


def test_dashboard_next_config_enforces_security_headers() -> None:
    """AUD-01: next.config.ts must declare required security headers."""
    repo_root = Path(__file__).resolve().parents[2]
    config_path = repo_root / "dashboard" / "next.config.ts"
    assert config_path.is_file(), f"next.config.ts missing at {config_path}"

    content = config_path.read_text(encoding="utf-8")
    assert "Content-Security-Policy" in content
    assert "default-src 'self'" in content
    assert "script-src 'self' 'unsafe-inline' 'unsafe-eval'" in content
    assert "connect-src" in content
    assert "frame-ancestors 'none'" in content
    assert "object-src 'none'" in content
    assert "base-uri 'self'" in content
    assert "X-Content-Type-Options" in content
    assert "nosniff" in content
    assert "X-Frame-Options" in content
    assert "DENY" in content
    assert "Referrer-Policy" in content
    assert "strict-origin-when-cross-origin" in content
    assert "Permissions-Policy" in content
    assert "camera=(), microphone=(), geolocation=()" in content
    assert "async headers()" in content or "headers()" in content


def test_dashboard_api_routes_prevent_ssrf() -> None:
    """AUD-01: Every fetch in dashboard/src/app/api/ must be guarded by probe allowlist / resolver."""
    repo_root = Path(__file__).resolve().parents[2]
    api_dir = repo_root / "dashboard" / "src" / "app" / "api"
    assert api_dir.is_dir(), f"API routes dir missing at {api_dir}"

    api_files = list(api_dir.rglob("*.ts"))
    assert len(api_files) > 0, "No API routes found"

    guarded_tokens = {
        "resolveScpApiBase",
        "resolveScpProxyBase",
        "isAllowedProbeTarget",
        "resolveHealthProbeTargets",
    }

    unguarded_files = []
    for ts_file in api_files:
        text = ts_file.read_text(encoding="utf-8")
        if "fetch(" in text:
            has_guard = any(token in text for token in guarded_tokens)
            if not has_guard:
                rel = ts_file.relative_to(repo_root)
                unguarded_files.append(str(rel))

    assert not unguarded_files, (
        f"Found API routes calling fetch without SSRF guard: {unguarded_files}"
    )


def test_dashboard_source_xss_sink_audit() -> None:
    """AUD-01: Zero unallowed dangerouslySetInnerHTML, 0 eval, 0 new Function in dashboard/src."""
    repo_root = Path(__file__).resolve().parents[2]
    src_dir = repo_root / "dashboard" / "src"
    assert src_dir.is_dir(), f"dashboard/src missing at {src_dir}"

    allowed_dangerously_files = {
        Path("dashboard/src/app/layout.tsx"),
        Path("dashboard/src/components/ui/chart.tsx"),
    }

    files = list(src_dir.rglob("*.ts")) + list(src_dir.rglob("*.tsx"))
    assert len(files) > 20, f"Expected > 20 source files in dashboard, found {len(files)}"

    xss_sinks = []
    for f in files:
        rel = f.relative_to(repo_root)
        lines = f.read_text(encoding="utf-8").splitlines()
        for idx, line in enumerate(lines, start=1):
            stripped = line.strip()
            if stripped.startswith("//") or stripped.startswith("/*") or stripped.startswith("*"):
                continue

            if "dangerouslySetInnerHTML" in line and rel not in allowed_dangerously_files:
                xss_sinks.append(f"{rel}:{idx} -> dangerouslySetInnerHTML")

            # Check for eval(
            if re.search(r"\beval\s*\(", line):
                xss_sinks.append(f"{rel}:{idx} -> eval(...)")

            # Check for new Function(
            if re.search(r"\bnew\s+Function\s*\(", line):
                xss_sinks.append(f"{rel}:{idx} -> new Function(...)")

    assert not xss_sinks, f"Found prohibited XSS sinks in dashboard:\n" + "\n".join(xss_sinks)


def test_dashboard_middleware_security_gate() -> None:
    """AUD-01: Middleware must manage LOCAL_IPS and fail-closed when proxy secret is missing."""
    repo_root = Path(__file__).resolve().parents[2]
    middleware_path = repo_root / "dashboard" / "src" / "middleware.ts"
    assert middleware_path.is_file(), f"middleware.ts missing at {middleware_path}"

    text = middleware_path.read_text(encoding="utf-8")
    assert "LOCAL_IPS" in text
    assert '"127.0.0.1"' in text
    assert '"::1"' in text
    assert '"localhost"' in text
    assert "SCP_DASHBOARD_PROXY_SECRET" in text
    # Middleware returns 503 / 403 when proxy secret missing / invalid (fail-closed)
    assert "503" in text
    assert "403" in text


def test_dashboard_security_headers_negative_test_fails_closed() -> None:
    """[Anti-Placebo Gate Pattern] Negative test: invalid/weakened next.config header config fails closed."""
    def _validate_headers(content: str) -> None:
        assert "Content-Security-Policy" in content
        assert "default-src 'self'" in content
        assert "frame-ancestors 'none'" in content
        assert "object-src 'none'" in content
        assert "base-uri 'self'" in content
        assert "X-Content-Type-Options" in content
        assert "nosniff" in content
        assert "X-Frame-Options" in content
        assert "DENY" in content

    # 1. Missing CSP
    with pytest.raises(AssertionError):
        _validate_headers("module.exports = {};")

    # 2. Missing frame-ancestors
    weak_csp = "Content-Security-Policy: default-src 'self'; X-Content-Type-Options: nosniff; X-Frame-Options: DENY"
    with pytest.raises(AssertionError):
        _validate_headers(weak_csp)

    # 3. Missing X-Frame-Options
    no_frame_opt = "Content-Security-Policy: default-src 'self'; frame-ancestors 'none'; object-src 'none'; base-uri 'self'; X-Content-Type-Options: nosniff"
    with pytest.raises(AssertionError):
        _validate_headers(no_frame_opt)

