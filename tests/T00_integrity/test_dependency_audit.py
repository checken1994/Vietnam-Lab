"""DEP-01: Supply-Chain Dependency Audit Tests.

Verifies:
1. All production dependencies have SHA256 hashes pinned in scp/requirements.hashes.txt.
2. Machine-readable SBOM exists in docs/sbom.json and conforms to CycloneDX format.
3. Dependency audit passes fail-closed verification.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from tools.audit_dependencies import (
    verify_dockerfile_enforces_hashes,
    verify_hash_pinning,
    verify_sbom,
)


def test_requirements_hashes_exist_and_pinned() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    hash_count = verify_hash_pinning(repo_root)
    assert hash_count >= 20, f"Expected at least 20 pinned hashes, got {hash_count}"


def test_sbom_exists_and_valid_cyclonedx() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    comp_count = verify_sbom(repo_root)
    assert comp_count >= 20, f"Expected at least 20 SBOM components, got {comp_count}"

    sbom_path = repo_root / "docs" / "sbom.json"
    data = json.loads(sbom_path.read_text(encoding="utf-8"))
    assert data.get("bomFormat") == "CycloneDX"
    assert data.get("specVersion") == "1.5"
    assert "components" in data


def test_dockerfile_enforces_require_hashes_dep03() -> None:
    """DEP-03: Dockerfile must enforce pip --require-hashes with requirements.hashes.txt."""
    repo_root = Path(__file__).resolve().parents[2]
    assert verify_dockerfile_enforces_hashes(repo_root) is True


def test_requirements_txt_is_strictly_pinned() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    req_file = repo_root / "scp" / "requirements.txt"
    lines = [
        l.strip()
        for l in req_file.read_text(encoding="utf-8").splitlines()
        if l.strip() and not l.strip().startswith("#")
    ]
    for line in lines:
        assert "==" in line, f"Dependency is not pinned with '==': {line}"
        assert ">=" not in line and "<=" not in line, f"Fuzzy version range forbidden: {line}"
