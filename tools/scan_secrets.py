#!/usr/bin/env python3
"""SEC-02: Secret Scanner for Commits and Pull Requests.

Scans git diffs in commits/PRs against the base branch for hardcoded secrets,
private keys, and sensitive credentials. Fails closed if any unverified
secret token is detected in added lines.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

# High-confidence secret patterns
SECRET_PATTERNS = [
    (re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----"), "Private Key header"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AWS Access Key ID"),
    (re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{36,255}\b"), "GitHub Personal Access Token"),
    (re.compile(r"\bxox[baprs]-[0-9a-zA-Z-]{10,72}\b"), "Slack Token"),
    (re.compile(r"\bAIzaSy[A-Za-z0-9_\-]{33}\b"), "Google / Gemini API Key"),
    (re.compile(r"\bsk-(?:ant-|proj-|live-)?[a-zA-Z0-9_\-]{20,}\b"), "API / Provider Secret Key"),
    (
        re.compile(
            r"(?i)\b[A-Za-z0-9_.\-]*(?:api_key|secret_key|access_token|auth_token|token|password|passwd|secret|admin_key)[A-Za-z0-9_.\-]*\s*[:=]\s*[\"']([A-Za-z0-9_\-+/=]{8,})[\"']"
        ),
        "Literal secret assignment",
    ),
]

# Whitelisted benign placeholders / test tokens (RC-2 / CI oracles)
SAFE_PLACEHOLDER_SUBSTRINGS = [
    "ci-only-runtime-placeholder",
    "pseudo_token",
    "test-token",
    "test_token",
    "dummy_secret",
    "mock_token",
    "fixture_token",
    "fake_token",
    "placeholder_secret",
]


def _is_safe_placeholder(text: str) -> bool:
    text_lower = text.lower()
    return any(p in text_lower for p in SAFE_PLACEHOLDER_SUBSTRINGS)


def scan_diff_text(diff_text: str) -> list[str]:
    """Scan unified diff text for additions containing potential secrets."""
    findings: list[str] = []
    current_file = ""

    for line in diff_text.splitlines():
        if line.startswith("+++ b/"):
            current_file = line[6:]
            continue

        if not line.startswith("+") or line.startswith("+++"):
            continue

        added_content = line[1:].strip()
        if not added_content:
            continue

        # Skip test fixture files where redaction patterns are intentionally tested
        if current_file.startswith("tests/") and any(
            t in current_file for t in (
                "test_internet_safety",
                "test_tier2_boundary",
                "test_tier3_cross",
                "test_secret_scanner",
            )
        ):
            continue

        for pattern, desc in SECRET_PATTERNS:
            match = pattern.search(added_content)
            if match:
                matched_token = match.group(0)
                if not _is_safe_placeholder(matched_token) and not _is_safe_placeholder(added_content):
                    findings.append(f"[{desc}] in {current_file or 'diff'}: {added_content[:70]}...")
                    break

    return findings


def get_diff_against_base(root: Path) -> str:
    """Determine base commit/branch and return git diff output."""
    base_ref = os.environ.get("GITHUB_BASE_REF")
    if base_ref:
        candidates = [f"origin/{base_ref}...HEAD", f"{base_ref}...HEAD"]
    else:
        candidates = ["origin/main...HEAD", "main...HEAD", "HEAD~1...HEAD", "HEAD~1"]

    working_diff = ""
    try:
        w_res = subprocess.run(
            ["git", "diff", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            errors="replace",
        )
        if w_res.returncode == 0:
            working_diff = w_res.stdout
    except (subprocess.SubprocessError, OSError):
        pass

    for ref in candidates:
        try:
            # Check if reference is resolvable
            base = ref.split("...")[0]
            check = subprocess.run(
                ["git", "rev-parse", "--verify", base],
                cwd=root,
                capture_output=True,
                text=True,
            )
            if check.returncode == 0:
                diff_res = subprocess.run(
                    ["git", "diff", ref],
                    cwd=root,
                    capture_output=True,
                    text=True,
                    errors="replace",
                )
                if diff_res.returncode == 0:
                    combined = diff_res.stdout
                    if working_diff:
                        combined = f"{combined}\n{working_diff}"
                    return combined
        except (subprocess.SubprocessError, OSError):
            continue

    # Fallback: diff of cached / staged changes
    return working_diff


def main() -> int:
    parser = argparse.ArgumentParser(description="SEC-02: Secret Scanner")
    parser.add_argument("--diff-file", help="Path to raw diff file to scan")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent.parent

    if args.diff_file:
        diff_text = Path(args.diff_file).read_text(encoding="utf-8", errors="replace")
    else:
        diff_text = get_diff_against_base(project_root)

    print("[SEC-02] Scanning commits / PR diff for hardcoded secrets...")
    findings = scan_diff_text(diff_text)

    if findings:
        print(f"\n[SEC-02 FAIL] Detected {len(findings)} potential hardcoded secrets in diff:")
        for f in findings:
            print(f"  - {f}")
        print("\nPlease remove hardcoded secrets and use environment variables or secret vaults.")
        return 1

    print("[SEC-02 PASS] 0 hardcoded secrets detected in diff.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
