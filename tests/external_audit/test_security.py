"""Security regression tests — OWASP API5 BFLA (Broken Function Level Authorization).

External trust audit tests — independent check of system security invariants.
INVARIANT: These tests intentionally do NOT import SCP modules directly to avoid circular trust.
They inspect source code directly with regex / AST or shell out to external tools via subprocess.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

# Repository root: tests/external_audit/ -> tests/ -> root
SCP_ROOT = Path(__file__).resolve().parent.parent.parent
SCP_PACKAGE = SCP_ROOT / "scp"
API_SERVER = SCP_PACKAGE / "api_server.py"
API_LIFESPAN = SCP_PACKAGE / "api_server_parts" / "lifespan.py"
ROUTES_DIR = SCP_PACKAGE / "api" / "routes"
CANONICAL_AUTH = SCP_PACKAGE / "security" / "auth.py"

# Routes that are PUBLIC by design — exempt from BFLA check.
PUBLIC_ROUTES = {
    "/",            # root info
    "/health",      # liveness probe (must be open for k8s/docker healthcheck)
    "/metrics",     # prometheus metrics (read-only, no secrets)
    "/dashboard",   # HTML dashboard (token required via URL ?token=... in JS)
    "/ask",         # main Q&A endpoint (user-facing, has rate limiting)
    "/v1/chat/completions",  # OpenAI-compatible (PyRIT/garak need open access)
    "/v1/models",           # OpenAI-compatible model list (public metadata)
}


def _extract_routes(src: str) -> list[tuple[int, str, str]]:
    """Return [(line_number, method, path)] for every FastAPI app/router decorator."""
    routes = []
    for i, line in enumerate(src.splitlines(), start=1):
        m = re.match(
            r'\s*@(app|router)\.(get|post|put|delete)\(\s*"([^"]+)"',
            line,
        )
        if m:
            routes.append((i, m.group(2), m.group(3)))
    return routes


def _route_has_auth(src: str, decorator_line: int) -> bool:
    """Check whether the route on `decorator_line` has verify_admin auth.

    Looks at the decorator line itself AND the next 8 lines (function signature).
    """
    lines = src.splitlines()
    chunk = lines[decorator_line - 1]
    for j in range(decorator_line, min(decorator_line + 8, len(lines))):
        chunk += "\n" + lines[j]
    return "Depends(verify_admin)" in chunk


def test_admin_routes_have_auth():
    """RC-2 BFLA check: every /v9*, /v10*, /v105* admin route must have verify_admin."""
    missing_auth = []
    assert API_SERVER.exists(), f"canonical api_server.py missing at {API_SERVER}"
    assert ROUTES_DIR.is_dir(), f"canonical api/routes dir missing at {ROUTES_DIR}"
    sources = [API_SERVER] + sorted(ROUTES_DIR.glob("*.py"))
    for source_path in sources:
        if not source_path.exists():
            continue
        src = source_path.read_text(encoding="utf-8")
        for lineno, method, path in _extract_routes(src):
            if path in PUBLIC_ROUTES:
                continue
            if not re.match(r"^/v(9|10)\d+", path) and not path.startswith("/v105/"):
                continue
            if not _route_has_auth(src, lineno):
                missing_auth.append(f"  {source_path.name}:L{lineno}: {method.upper()} {path}")

    assert not missing_auth, (
        "BFLA regression — admin routes without verify_admin (RC-2):\n"
        + "\n".join(missing_auth)
    )


def test_verify_admin_no_dev_mode_bypass():
    """RC-2: verify_admin function must NOT contain the SCP_DEV_MODE bypass."""
    assert CANONICAL_AUTH.exists(), "canonical security/auth.py is missing"
    src = CANONICAL_AUTH.read_text(encoding="utf-8")
    m = re.search(
        r"^def verify_admin\([^)]*\)[^:]*:(?:.|\n)*?^(?:def |class |\Z)",
        src, re.MULTILINE,
    )
    assert m, "verify_admin function not found in canonical security/auth.py"
    body = re.sub(r"\n(?:def |class ).*$", "", m.group(0), flags=re.MULTILINE)
    bypass_pattern = re.compile(
        r'SCP_DEV_MODE[^"\n]*"1"[^:\n]*:[^\n]*\n\s*return\s+True',
        re.MULTILINE,
    )
    assert not bypass_pattern.search(body), (
        "RC-2 regression: SCP_DEV_MODE bypass (return True) present in verify_admin body"
    )


def test_no_hardcoded_token_in_source():
    """RC-2 CODE-AUDIT-001: no production token hardcoded in source."""
    token = os.environ.get("SCP_AUTH_TOKEN_SECRET", "prod_token_sec_never_commit_xyz")
    offenders = []
    for py_file in SCP_ROOT.rglob("*.py"):
        rel_path = py_file.relative_to(SCP_ROOT).as_posix()
        if "__pycache__" in rel_path:
            continue
        if (
            "tests/" in rel_path
            or "/test_" in f"/{rel_path}"
            or rel_path.startswith("test_")
        ):
            continue
        try:
            src = py_file.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if token in src:
            offenders.append(str(py_file))
    benchmark_dir = SCP_ROOT / "benchmark"
    if benchmark_dir.exists():
        for md_file in benchmark_dir.rglob("*.md"):
            try:
                src = md_file.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            if token in src:
                offenders.append(str(md_file))
    assert not offenders, (
        "RC-2 regression: hardcoded production token found in:\n  "
        + "\n  ".join(offenders)
    )


def test_bandit_no_new_high_severity_via_bandit():
    """RC-10 external audit: bandit HIGH-severity count must not increase."""
    cmd = [sys.executable, "-m", "bandit", "-r", str(SCP_PACKAGE), "-f", "json", "-q"]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    assert proc.returncode in (0, 1), f"bandit failed to run: {proc.stderr[:200]}"
    data = json.loads(proc.stdout)
    high_issues = [
        issue for issue in data.get("results", [])
        if issue.get("issue_severity") == "HIGH"
    ]
    new_categories = set()
    for issue in high_issues:
        if issue.get("test_id") != "B324":
            new_categories.add((issue.get("test_id"), issue.get("filename")))
    new_categories = {
        (tid, fn) for (tid, fn) in new_categories
        if "/tests/external_audit/" not in fn and "\\tests\\external_audit\\" not in fn
    }
    assert not new_categories, (
        "RC-10 regression: new HIGH-severity bandit issues appeared:\n  "
        + "\n  ".join(f"{tid} in {fn}" for tid, fn in new_categories)
    )


def test_active_lifespan_does_not_autostart_ungated_external_producers():
    """Direct-request producers stay opt-in until a shared egress gate exists."""
    assert API_LIFESPAN.exists(), f"canonical lifespan.py missing at {API_LIFESPAN}"
    lifespan_src = API_LIFESPAN.read_text(encoding="utf-8")
    assert "async def lifespan" in lifespan_src
    for start_call in ("start_audit_fetcher", "start_scanner", "start_detector"):
        assert start_call not in lifespan_src


def test_stream_route_contract_is_live_and_offloads_sync_judge():
    """The registered stream route must not advertise the old dead-route state."""
    stream_file = ROUTES_DIR / "stream_routes.py"
    assert stream_file.exists(), f"stream_routes.py missing at {stream_file}"
    stream_src = stream_file.read_text(encoding="utf-8")
    assert "LIVE ROUTE" in stream_src
    assert "DEAD ROUTE" not in stream_src
    assert "await asyncio.to_thread(" in stream_src
    assert '"/v105/ask/stream"' in stream_src


def test_pc_read_only_allowlist_rejects_command_chains(tmp_path):
    """Subprocess verification: read-only prefix must not permit command chains."""
    script = f"""
import sys
from pathlib import Path
from scp.pc_control.pc_controller import PCController

controller = PCController(working_dir=Path(r"{tmp_path}"))
commands = [
    "whoami; Start-Process calc",
    "whoami ; Start-Process calc",
    "whoami && Start-Process calc",
    "whoami | Out-File probe.txt",
    "whoami $(Start-Process calc)",
]
for cmd in commands:
    decision = controller.evaluate(cmd, capability_level=0)
    assert decision.allowed is False, f"Expected forbidden for '{{cmd}}'"
    assert "chain" in decision.reason.lower(), f"Expected chain in reason: {{decision.reason}}"
sys.exit(0)
"""
    res = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=30)
    assert res.returncode == 0, f"PCController command chain check failed:\n{res.stderr}\n{res.stdout}"


def test_browser_validator_rejects_private_network_targets():
    """Subprocess verification: public browsing rejects private network IPs."""
    script = """
import sys
from scp.web_control.browser_session import BrowserSession

targets = [
    "http://127.0.0.1:9222/json/list",
    "http://169.254.169.254/latest/meta-data/",
    "http://[::1]/",
]
for target in targets:
    try:
        BrowserSession.validate_url(target)
        print(f"FAILED: Expected ValueError for {target}", file=sys.stderr)
        sys.exit(1)
    except ValueError as e:
        if "internal/private" not in str(e):
            print(f"FAILED: Wrong error message for {target}: {e}", file=sys.stderr)
            sys.exit(2)
sys.exit(0)
"""
    res = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=30)
    assert res.returncode == 0, f"BrowserSession private IP check failed:\n{res.stderr}"


def test_detail_routes_share_producer_data_path_constants():
    """Source inspection: detail routes must read the same SCP_DATA_DIR-derived constants."""
    threat_route_src = (ROUTES_DIR / "threat_routes.py").read_text(encoding="utf-8")
    assert "from scp.core.ai_threat_scanner import THREATS_DB" in threat_route_src
    assert "from scp.core.harm_detector import HARM_DB" in threat_route_src


def test_no_scp_import_in_external_security_audit():
    """Anti-Placebo & External Trust Root Invariant:
    Neither this test file nor conftest.py may directly import scp modules.
    Verified via AST parsing so import statements are strictly prohibited.
    """
    import ast

    for target_path in (Path(__file__), Path(__file__).parent / "conftest.py"):
        tree = ast.parse(target_path.read_text(encoding="utf-8"), filename=str(target_path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("scp"), (
                        f"VIOLATION: {target_path.name} directly imports '{alias.name}'! External audit must remain decoupled."
                    )
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                assert not module.startswith("scp"), (
                    f"VIOLATION: {target_path.name} directly imports from '{module}'! External audit must remain decoupled."
                )
