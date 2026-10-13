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
    verify_dockerfile_pins_base_image_digest,
    verify_hash_pinning,
    verify_sbom,
    verify_sbom_licenses,
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


def test_dockerfile_comment_bypass_rejected_fail_closed(tmp_path: Path) -> None:
    """DEP-03 negative test: comment containing --require-hashes must not trick the gate;
    an unhashed RUN pip install must fail closed immediately (AssertionError)."""
    import pytest

    fake_dockerfile = tmp_path / "Dockerfile"
    # Comment contains --require-hashes and requirements.hashes.txt, but execution command does not
    fake_dockerfile.write_text(
        "# RUN pip install --require-hashes -r requirements.hashes.txt\n"
        "FROM python:3.12-slim\n"
        "RUN pip install --no-cache-dir -r requirements.lock.txt\n",
        encoding="utf-8",
    )
    with pytest.raises(AssertionError, match="DEP-03 VIOLATION"):
        verify_dockerfile_enforces_hashes(tmp_path, dockerfile_path=fake_dockerfile)


def test_dockerfile_multiline_pip_install_enforces_hashes(tmp_path: Path) -> None:
    """DEP-03 multiline test: verifies line continuations '\\' with comments interspersed."""
    fake_dockerfile = tmp_path / "Dockerfile"
    fake_dockerfile.write_text(
        "FROM python:3.12-slim\n"
        "# Comment before command\n"
        "RUN python -m pip install \\\n"
        "    --no-cache-dir \\\n"
        "    --require-hashes \\\n"
        "    -r requirements.hashes.txt\n",
        encoding="utf-8",
    )
    assert verify_dockerfile_enforces_hashes(tmp_path, dockerfile_path=fake_dockerfile) is True


def test_dockerfile_exec_form_pip_install_rejected_if_unhashed(tmp_path: Path) -> None:
    """DEP-03 negative test: JSON exec-form RUN ['pip', 'install', ...] without --require-hashes fails closed."""
    import pytest

    fake_dockerfile = tmp_path / "Dockerfile"
    fake_dockerfile.write_text(
        'FROM python:3.12-slim\n'
        'RUN ["pip", "install", "unpinned-package"]\n',
        encoding="utf-8",
    )
    with pytest.raises(AssertionError, match="DEP-03 VIOLATION"):
        verify_dockerfile_enforces_hashes(tmp_path, dockerfile_path=fake_dockerfile)


def test_dockerfile_pip_with_interspersed_options_rejected_if_unhashed(tmp_path: Path) -> None:
    """DEP-03 negative test: options between pip and install (pip --no-cache-dir install) fail closed without hash."""
    import pytest

    fake_dockerfile = tmp_path / "Dockerfile"
    fake_dockerfile.write_text(
        "FROM python:3.12-slim\n"
        "RUN pip --no-cache-dir install malicious\n",
        encoding="utf-8",
    )
    with pytest.raises(AssertionError, match="DEP-03 VIOLATION"):
        verify_dockerfile_enforces_hashes(tmp_path, dockerfile_path=fake_dockerfile)


def test_dockerfile_case_insensitive_pip_install_rejected_if_unhashed(tmp_path: Path) -> None:
    """DEP-03 negative test: uppercase PIP INSTALL without --require-hashes fails closed."""
    import pytest

    fake_dockerfile = tmp_path / "Dockerfile"
    fake_dockerfile.write_text(
        "FROM python:3.12-slim\n"
        "RUN PIP INSTALL --no-cache-dir malicious\n",
        encoding="utf-8",
    )
    with pytest.raises(AssertionError, match="DEP-03 VIOLATION"):
        verify_dockerfile_enforces_hashes(tmp_path, dockerfile_path=fake_dockerfile)


def test_dockerfile_require_hashes_without_hashes_file_rejected(tmp_path: Path) -> None:
    """DEP-03 negative test: command with --require-hashes but lacking requirements.hashes.txt fails closed."""
    import pytest

    fake_dockerfile = tmp_path / "Dockerfile"
    fake_dockerfile.write_text(
        "FROM python:3.12-slim\n"
        "RUN pip install --require-hashes -r untrusted.txt\n",
        encoding="utf-8",
    )
    with pytest.raises(AssertionError, match="DEP-03 VIOLATION"):
        verify_dockerfile_enforces_hashes(tmp_path, dockerfile_path=fake_dockerfile)


def test_dockerfile_pins_base_image_digest_dep04() -> None:
    """DEP-04: Dockerfile must pin base image with @sha256:<64-hex> digest."""
    repo_root = Path(__file__).resolve().parents[2]
    assert verify_dockerfile_pins_base_image_digest(repo_root) is True


def test_dockerfile_unpinned_base_image_rejected_fail_closed(tmp_path: Path) -> None:
    """DEP-04 negative test: unpinned mutable base image tag must fail closed immediately."""
    import pytest

    fake_dockerfile = tmp_path / "Dockerfile"
    fake_dockerfile.write_text(
        "FROM python:3.12-slim AS runtime\n"
        "RUN pip install --no-cache-dir --require-hashes -r requirements.hashes.txt\n",
        encoding="utf-8",
    )
    with pytest.raises(AssertionError, match="DEP-04 VIOLATION"):
        verify_dockerfile_pins_base_image_digest(tmp_path, dockerfile_path=fake_dockerfile)


def test_dockerfile_invalid_digest_rejected_fail_closed(tmp_path: Path) -> None:
    """DEP-04 negative test: non-sha256 or truncated digest must fail closed."""
    import pytest

    fake_dockerfile = tmp_path / "Dockerfile"
    fake_dockerfile.write_text(
        "FROM python:3.12-slim@sha256:deadbeef AS runtime\n",
        encoding="utf-8",
    )
    with pytest.raises(AssertionError, match="DEP-04 VIOLATION"):
        verify_dockerfile_pins_base_image_digest(tmp_path, dockerfile_path=fake_dockerfile)


def test_sbom_licenses_100_percent_compliance_dep05() -> None:
    """DEP-05: 100% of components in docs/sbom.json must have valid SPDX licenses and 0 UNKNOWN."""
    repo_root = Path(__file__).resolve().parents[2]
    count = verify_sbom_licenses(repo_root)
    assert count >= 32, f"Expected at least 32 compliant components, got {count}"


def test_sbom_unknown_license_rejected_fail_closed(tmp_path: Path) -> None:
    """DEP-05 negative test: any component with UNKNOWN license must fail closed."""
    import pytest

    fake_sbom = tmp_path / "sbom.json"
    fake_sbom.write_text(
        json.dumps({
            "bomFormat": "CycloneDX",
            "specVersion": "1.5",
            "components": [
                {
                    "name": "vulnerable-pkg",
                    "version": "1.0.0",
                    "licenses": [{"license": {"name": "UNKNOWN"}}],
                }
            ],
        }),
        encoding="utf-8",
    )
    with pytest.raises(AssertionError, match="DEP-05 VIOLATION"):
        verify_sbom_licenses(tmp_path, sbom_path=fake_sbom)


def test_dockerfile_trailing_garbage_digest_rejected_fail_closed(tmp_path: Path) -> None:
    """DEP-04 negative test: digest with trailing characters must fail closed."""
    import pytest

    fake_dockerfile = tmp_path / "Dockerfile"
    fake_dockerfile.write_text(
        "FROM python:3.12-slim@sha256:7f99dd92994e43c8d9e68c9bc88a5369bb9892c99a071efdf6e680a618c6ee4cEXTRA_BAD AS runtime\n",
        encoding="utf-8",
    )
    with pytest.raises(AssertionError, match="DEP-04 VIOLATION"):
        verify_dockerfile_pins_base_image_digest(tmp_path, dockerfile_path=fake_dockerfile)


def test_dockerfile_digest_with_65_hex_chars_rejected_fail_closed(tmp_path: Path) -> None:
    """DEP-04 negative test: digest with 65 hex characters (exceeding SHA256 length) must fail closed."""
    import pytest

    fake_dockerfile = tmp_path / "Dockerfile"
    fake_dockerfile.write_text(
        "FROM python:3.12-slim@sha256:7f99dd92994e43c8d9e68c9bc88a5369bb9892c99a071efdf6e680a618c6ee4c0 AS runtime\n",
        encoding="utf-8",
    )
    with pytest.raises(AssertionError, match="DEP-04 VIOLATION"):
        verify_dockerfile_pins_base_image_digest(tmp_path, dockerfile_path=fake_dockerfile)


def test_dockerfile_multi_stage_case_insensitive_alias_accepted(tmp_path: Path) -> None:
    """DEP-04 multi-stage test: case-insensitive stage aliases must be accepted without false positives."""
    fake_dockerfile = tmp_path / "Dockerfile"
    fake_dockerfile.write_text(
        "FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1 AS builder\n"
        "FROM BUILDER AS runner\n",
        encoding="utf-8",
    )
    assert verify_dockerfile_pins_base_image_digest(tmp_path, dockerfile_path=fake_dockerfile) is True


def test_dockerfile_unpinned_second_stage_rejected_fail_closed(tmp_path: Path) -> None:
    """DEP-04 negative test: second stage using unpinned external base image must fail closed even if stage 1 was pinned."""
    import pytest

    fake_dockerfile = tmp_path / "Dockerfile"
    fake_dockerfile.write_text(
        "FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1 AS builder\n"
        "FROM alpine:latest AS runner\n",
        encoding="utf-8",
    )
    with pytest.raises(AssertionError, match="DEP-04 VIOLATION"):
        verify_dockerfile_pins_base_image_digest(tmp_path, dockerfile_path=fake_dockerfile)


def test_dockerfile_flags_before_base_image_enforced(tmp_path: Path) -> None:
    """DEP-04 flag parsing test: instructions with multiple flags are parsed properly and strictly enforced."""
    import pytest

    fake_dockerfile_valid = tmp_path / "Dockerfile.valid"
    fake_dockerfile_valid.write_text(
        "FROM --platform=linux/amd64 --pull python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1 AS runtime\n",
        encoding="utf-8",
    )
    assert verify_dockerfile_pins_base_image_digest(tmp_path, dockerfile_path=fake_dockerfile_valid) is True

    fake_dockerfile_invalid = tmp_path / "Dockerfile.invalid"
    fake_dockerfile_invalid.write_text(
        "FROM --platform=linux/amd64 --pull python:3.12-slim AS runtime\n",
        encoding="utf-8",
    )
    with pytest.raises(AssertionError, match="DEP-04 VIOLATION"):
        verify_dockerfile_pins_base_image_digest(tmp_path, dockerfile_path=fake_dockerfile_invalid)


def test_sbom_placeholder_license_rejected_fail_closed(tmp_path: Path) -> None:
    """DEP-05 negative test: placeholder licenses like 'None', 'N/A', 'UNKNOWN LICENSE' must fail closed."""
    import pytest

    for placeholder in ["None", "N/A", "UNKNOWN LICENSE", "UNLICENSED", "NOASSERTION", "undefined"]:
        fake_sbom = tmp_path / f"sbom_{placeholder.replace(' ', '_').replace('/', '_')}.json"
        fake_sbom.write_text(
            json.dumps({
                "bomFormat": "CycloneDX",
                "specVersion": "1.5",
                "components": [
                    {
                        "name": "placeholder-pkg",
                        "version": "1.0.0",
                        "licenses": [{"license": {"name": placeholder}}],
                    }
                ],
            }),
            encoding="utf-8",
        )
        with pytest.raises(AssertionError, match="DEP-05 VIOLATION"):
            verify_sbom_licenses(tmp_path, sbom_path=fake_sbom)


def test_dockerfile_self_alias_bypass_rejected_fail_closed(tmp_path: Path) -> None:
    """DEP-04 negative test: 'FROM unpinned AS unpinned' must not bypass digest check via self-aliasing."""
    import pytest

    fake_dockerfile = tmp_path / "Dockerfile"
    fake_dockerfile.write_text(
        "FROM unpinned AS unpinned\n",
        encoding="utf-8",
    )
    with pytest.raises(AssertionError, match="DEP-04 VIOLATION"):
        verify_dockerfile_pins_base_image_digest(tmp_path, dockerfile_path=fake_dockerfile)


def test_dockerfile_bogus_low_entropy_and_revoked_digest_rejected_fail_closed(tmp_path: Path) -> None:
    """DEP-04 / DEP-06 negative test: low-entropy ('0000...') or revoked bogus digest ('7f99dd...') must fail closed."""
    import pytest

    for bad_digest in (
        "0" * 64,
        "a" * 64,
        "7f99dd92994e43c8d9e68c9bc88a5369bb9892c99a071efdf6e680a618c6ee4c",
    ):
        fake_dockerfile = tmp_path / f"Dockerfile.{bad_digest[:8]}"
        fake_dockerfile.write_text(
            f"FROM python:3.12-slim@sha256:{bad_digest} AS runtime\n",
            encoding="utf-8",
        )
        with pytest.raises(AssertionError, match="DEP-04"):
            verify_dockerfile_pins_base_image_digest(tmp_path, dockerfile_path=fake_dockerfile)


def test_sbom_non_spdx_arbitrary_license_rejected_fail_closed(tmp_path: Path) -> None:
    """DEP-05 negative test: arbitrary non-SPDX license strings like 'RandomFakeLicense' must fail closed."""
    import pytest

    for bogus_license in ["RandomFakeLicense", "ProprietaryBogus-1.0", "MadeUpLicense"]:
        fake_sbom = tmp_path / f"sbom_{bogus_license}.json"
        fake_sbom.write_text(
            json.dumps({
                "bomFormat": "CycloneDX",
                "specVersion": "1.5",
                "components": [
                    {
                        "name": "bogus-licensed-pkg",
                        "version": "1.0.0",
                        "licenses": [{"license": {"id": bogus_license, "name": bogus_license}}],
                    }
                ],
            }),
            encoding="utf-8",
        )
        with pytest.raises(AssertionError, match="DEP-05 VIOLATION"):
            verify_sbom_licenses(tmp_path, sbom_path=fake_sbom)


def test_dependabot_config_exists_and_monitors_docker_actions_pip() -> None:
    """DEP-06: .github/dependabot.yml must exist and configure docker, github-actions, and pip."""
    import yaml

    repo_root = Path(__file__).resolve().parents[2]
    dep_file = repo_root / ".github" / "dependabot.yml"
    assert dep_file.exists(), "Missing .github/dependabot.yml"
    cfg = yaml.safe_load(dep_file.read_text(encoding="utf-8"))
    assert cfg.get("version") == 2
    ecosystems = {entry.get("package-ecosystem") for entry in cfg.get("updates", [])}
    assert {"docker", "github-actions", "pip"} <= ecosystems


def test_requirements_hashes_missing_transitive_dependency_rejected(tmp_path: Path) -> None:
    """DEP-01/DEP-03 negative test: requirements.hashes.txt missing a transitive lockfile dependency must fail closed."""
    import pytest

    scp_dir = tmp_path / "scp"
    scp_dir.mkdir(parents=True, exist_ok=True)
    (scp_dir / "requirements.txt").write_text("fastapi==0.142.2\n", encoding="utf-8")
    (scp_dir / "requirements.lock.txt").write_text(
        "fastapi==0.142.2\ntyping_extensions==4.16.0\n",
        encoding="utf-8",
    )
    hash_lines = ["fastapi==0.142.2 \\"] + [
        f"    --hash=sha256:{i:064x}" + (" \\" if i < 20 else "") for i in range(1, 21)
    ]
    (scp_dir / "requirements.hashes.txt").write_text("\n".join(hash_lines) + "\n", encoding="utf-8")

    with pytest.raises(AssertionError, match="DEP-01/DEP-03 VIOLATION"):
        verify_hash_pinning(tmp_path)


def test_spdx_expression_with_exception_and_malformed_grammar(tmp_path: Path) -> None:
    """DEP-05 test: compound SPDX with WITH exception is accepted; operator-less or trailing operator expressions fail closed."""
    import pytest

    valid_sbom = tmp_path / "sbom_valid_expr.json"
    valid_sbom.write_text(
        json.dumps({
            "bomFormat": "CycloneDX",
            "specVersion": "1.5",
            "components": [
                {
                    "name": "valid-compound-pkg",
                    "version": "1.0.0",
                    "licenses": [{"expression": "(Apache-2.0 WITH LLVM-exception OR MIT) AND BSD-3-Clause"}],
                }
            ],
        }),
        encoding="utf-8",
    )
    assert verify_sbom_licenses(tmp_path, sbom_path=valid_sbom) == 1

    for malformed in ["MIT MIT", "AND MIT", "MIT OR", "MIT WITH FakeException", "WITH LLVM-exception"]:
        bad_sbom = tmp_path / f"sbom_bad_{ abs(hash(malformed)) }.json"
        bad_sbom.write_text(
            json.dumps({
                "bomFormat": "CycloneDX",
                "specVersion": "1.5",
                "components": [
                    {
                        "name": "bad-expr-pkg",
                        "version": "1.0.0",
                        "licenses": [{"expression": malformed}],
                    }
                ],
            }),
            encoding="utf-8",
        )
        with pytest.raises(AssertionError, match="DEP-05 VIOLATION"):
            verify_sbom_licenses(tmp_path, sbom_path=bad_sbom)


def test_dockerfile_repeating_pattern_digest_rejected_fail_closed(tmp_path: Path) -> None:
    """DEP-04 negative test: repeating 8/16-char pattern digests (e.g. 0123456789abcdef * 4) must fail closed."""
    import pytest

    for pattern_digest in ("0123456789abcdef" * 4, "fedcba9876543210" * 4, "0123abcd" * 8):
        fake_dockerfile = tmp_path / f"Dockerfile.{pattern_digest[:8]}"
        fake_dockerfile.write_text(
            f"FROM python:3.12-slim@sha256:{pattern_digest} AS runtime\n",
            encoding="utf-8",
        )
        with pytest.raises(AssertionError, match="DEP-04"):
            verify_dockerfile_pins_base_image_digest(tmp_path, dockerfile_path=fake_dockerfile)

