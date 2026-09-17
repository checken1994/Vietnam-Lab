"""
tests/test_m2_release_evidence_challenger.py
=============================================
Adversarial Empirical Challenge Suite for Worker 1's fix on
`scp/api/routes/admin_v100.py:release_evidence`:

Probes:
  Probe A: Unauthenticated / Invalid Auth Token access (must be 401/403/429, never 200 or 500).
  Probe B: Simulated git error or invalid commit ref (must fail-closed with HTTP 503, never 500 crash).
  Probe C: Valid authenticated request (HTTP 200, exact 40-char SHA matching git HEAD,
           valid SHA256 evidence digest, artifact_hashes dictionary, cryptographic verification).
  Probe D: Physical disk creation of `release_evidence.json` (exists, non-empty, valid JSON syntax,
           matches response payload, respects custom SCP_DATA_DIR).

Adheres strictly to:
  FA-01: Strict assertions (no loosening, exact status codes, regex validation).
  FA-02: No skip/xfail.
  FA-03: Full terminal output evidence.
  FA-04: No simulated VERIFIED; validates real git object tree.
  FA-05: Enforces token authority boundary.
  FA-08: No forged provenance.
  FA-09: Real execution against live FastAPI application endpoints.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Generator

import pytest
from fastapi.testclient import TestClient

from scp.api_server import app
from scp.release.evidence_authority import EvidenceAuthority
import scp.security.auth as auth_mod


_TEST_TOKEN = "challenger_admin_token_m2_r3_test_secret"
_REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def isolate_challenger_env(monkeypatch) -> Generator[None, None, None]:
    """Ensure clean hermetic environment and reset auth rate-limit table per test."""
    # Reset auth failure table to avoid cross-test rate-limiting
    monkeypatch.setattr(auth_mod, "_auth_failures", {})

    # Default profile and admin secrets for testing
    monkeypatch.setenv("SCP_API_PROFILE", "full")
    monkeypatch.setenv("SCP_AUTH_TOKEN_SECRET", _TEST_TOKEN)
    monkeypatch.setenv("SCP_AUTH_PASSWORD", _TEST_TOKEN)

    # Ensure clean git sha env by default
    if "SCP_GIT_SHA" in os.environ:
        monkeypatch.delenv("SCP_GIT_SHA", raising=False)

    yield

    # Teardown reset
    monkeypatch.setattr(auth_mod, "_auth_failures", {})


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


# =============================================================================
# PROBE A: Unauthenticated / Invalid Auth Token Access (Security Boundary)
# =============================================================================

class TestProbeAAuthenticationBoundary:
    """Probe A: Verify that unauthorized requests are rejected with 401/403, never 200 or 500."""

    def test_probe_a01_missing_auth_header_rejected(self, client: TestClient):
        """Request without Authorization header must return HTTP 401."""
        resp = client.get("/v100/release/evidence")
        assert resp.status_code == 401, f"Expected 401, got {resp.status_code}: {resp.text}"
        assert resp.status_code not in (200, 500)
        assert "Missing auth token" in resp.text or "detail" in resp.json()

    def test_probe_a02_empty_auth_header_rejected(self, client: TestClient):
        """Request with empty Authorization header must return HTTP 401."""
        resp = client.get("/v100/release/evidence", headers={"Authorization": ""})
        assert resp.status_code == 401, f"Expected 401, got {resp.status_code}: {resp.text}"
        assert resp.status_code not in (200, 500)

    def test_probe_a03_empty_bearer_token_rejected(self, client: TestClient):
        """Request with empty Bearer token must return HTTP 401."""
        resp = client.get("/v100/release/evidence", headers={"Authorization": "Bearer "})
        assert resp.status_code == 401, f"Expected 401, got {resp.status_code}: {resp.text}"
        assert resp.status_code not in (200, 500)

    def test_probe_a04_invalid_token_rejected(self, client: TestClient):
        """Request with invalid Bearer token must return HTTP 401."""
        resp = client.get(
            "/v100/release/evidence",
            headers={"Authorization": "Bearer completely_bogus_adversarial_token_999"},
        )
        assert resp.status_code == 401, f"Expected 401, got {resp.status_code}: {resp.text}"
        assert resp.status_code not in (200, 500)
        assert "Invalid auth token" in resp.json().get("detail", "")

    def test_probe_a05_wrong_auth_scheme_rejected(self, client: TestClient):
        """Request with non-Bearer auth scheme with mismatched token must return HTTP 401."""
        resp = client.get(
            "/v100/release/evidence",
            headers={"Authorization": "Basic dXNlcjpwYXNzd29yZA=="},
        )
        assert resp.status_code == 401, f"Expected 401, got {resp.status_code}: {resp.text}"
        assert resp.status_code not in (200, 500)

    def test_probe_a06_unconfigured_auth_deny_by_default(self, client: TestClient, monkeypatch):
        """When server auth is not configured, deny-by-default must return HTTP 401."""
        monkeypatch.setenv("SCP_AUTH_TOKEN_SECRET", "")
        monkeypatch.setenv("SCP_AUTH_PASSWORD", "")
        monkeypatch.setenv("SCP_JWT_SECRET", "")

        resp = client.get(
            "/v100/release/evidence",
            headers={"Authorization": f"Bearer {_TEST_TOKEN}"},
        )
        assert resp.status_code == 401, f"Expected 401 on unconfigured server, got {resp.status_code}"
        assert resp.status_code not in (200, 500)

    def test_probe_a07_rate_limit_lockout_after_repeated_failures(self, client: TestClient):
        """After 5 consecutive auth failures, subsequent attempts must return HTTP 429."""
        # Trigger 5 auth failures
        for i in range(5):
            fail_resp = client.get(
                "/v100/release/evidence",
                headers={"Authorization": f"Bearer bad_token_{i}"},
            )
            assert fail_resp.status_code == 401

        # 6th attempt should be rate limited (429)
        lockout_resp = client.get(
            "/v100/release/evidence",
            headers={"Authorization": f"Bearer {_TEST_TOKEN}"},
        )
        assert lockout_resp.status_code == 429, f"Expected 429, got {lockout_resp.status_code}"
        assert lockout_resp.status_code not in (200, 500)


# =============================================================================
# PROBE B: Fail-Closed Behavior on Git / Filesystem Errors (HTTP 503)
# =============================================================================

class TestProbeBFailClosedGitErrors:
    """Probe B: Simulated git error or invalid commit ref must return HTTP 503 (not crash with 500)."""

    def test_probe_b01_nonexistent_40char_git_sha_returns_503(
        self, client: TestClient, monkeypatch
    ):
        """An invalid/non-existent 40-character SHA in SCP_GIT_SHA must fail-closed with HTTP 503."""
        monkeypatch.setenv("SCP_GIT_SHA", "0000000000000000000000000000000000000000")
        resp = client.get(
            "/v100/release/evidence",
            headers={"Authorization": f"Bearer {_TEST_TOKEN}"},
        )
        assert resp.status_code == 503, f"Expected 503, got {resp.status_code}: {resp.text}"
        assert resp.status_code != 500, "Must not crash with unhandled HTTP 500"
        data = resp.json()
        assert "Evidence generation unavailable" in data.get("detail", "")

    def test_probe_b02_synthetic_fake_commit_sha_returns_503(
        self, client: TestClient, monkeypatch
    ):
        """Another non-existent 40-char SHA (all 'f's) must fail-closed with HTTP 503."""
        monkeypatch.setenv("SCP_GIT_SHA", "ffffffffffffffffffffffffffffffffffffffff")
        resp = client.get(
            "/v100/release/evidence",
            headers={"Authorization": f"Bearer {_TEST_TOKEN}"},
        )
        assert resp.status_code == 503, f"Expected 503, got {resp.status_code}: {resp.text}"
        assert resp.status_code != 500

    def test_probe_b03_simulated_git_subprocess_error_returns_503(
        self, client: TestClient, monkeypatch
    ):
        """When git command fails with SubprocessError, endpoint must return HTTP 503."""
        orig_run = subprocess.run

        def mock_git_fail(cmd, *args, **kwargs):
            if isinstance(cmd, list) and len(cmd) > 0 and cmd[0] == "git":
                raise subprocess.CalledProcessError(
                    128, cmd, stderr="fatal: mock git corruption / unavailable"
                )
            return orig_run(cmd, *args, **kwargs)

        monkeypatch.setattr(subprocess, "run", mock_git_fail)

        resp = client.get(
            "/v100/release/evidence",
            headers={"Authorization": f"Bearer {_TEST_TOKEN}"},
        )
        assert resp.status_code == 503, f"Expected 503, got {resp.status_code}: {resp.text}"
        assert resp.status_code != 500
        assert "Evidence generation unavailable" in resp.json().get("detail", "")

    def test_probe_b04_simulated_oserror_returns_503(
        self, client: TestClient, monkeypatch
    ):
        """When filesystem write fails with OSError (e.g. read-only fs), return HTTP 503."""
        orig_write_text = Path.write_text

        def mock_write_error(self_path, *args, **kwargs):
            if "release_evidence" in str(self_path):
                raise PermissionError("EACCES: permission denied writing release_evidence.json")
            return orig_write_text(self_path, *args, **kwargs)

        monkeypatch.setattr(Path, "write_text", mock_write_error)

        resp = client.get(
            "/v100/release/evidence",
            headers={"Authorization": f"Bearer {_TEST_TOKEN}"},
        )
        assert resp.status_code == 503, f"Expected 503, got {resp.status_code}: {resp.text}"
        assert resp.status_code != 500
        assert "Evidence generation unavailable" in resp.json().get("detail", "")

    def test_probe_b05_simulated_value_error_returns_503(
        self, client: TestClient, monkeypatch
    ):
        """When evidence generation raises ValueError, return HTTP 503."""
        def mock_generate_val_error(*args, **kwargs):
            raise ValueError("Corrupted commit hash resolution")

        monkeypatch.setattr(
            EvidenceAuthority, "generate_evidence", mock_generate_val_error
        )

        resp = client.get(
            "/v100/release/evidence",
            headers={"Authorization": f"Bearer {_TEST_TOKEN}"},
        )
        assert resp.status_code == 503, f"Expected 503, got {resp.status_code}: {resp.text}"
        assert resp.status_code != 500
        assert "Evidence generation unavailable" in resp.json().get("detail", "")


# =============================================================================
# PROBE C: Valid Authenticated Request (HTTP 200 & Cryptographic Integrity)
# =============================================================================

class TestProbeCValidEvidenceContract:
    """Probe C: Valid authenticated request returns 200, 40-char SHA matching git HEAD, and valid hashes."""

    def test_probe_c01_valid_request_returns_200_and_exact_head_sha(
        self, client: TestClient
    ):
        """Valid request returns HTTP 200 with tested_sha matching live git rev-parse HEAD."""
        # Resolve real git HEAD commit SHA
        expected_head = subprocess.check_output(
            ["git", "-C", str(_REPO_ROOT), "rev-parse", "HEAD"],
            text=True,
        ).strip()

        resp = client.get(
            "/v100/release/evidence",
            headers={"Authorization": f"Bearer {_TEST_TOKEN}"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

        data = resp.json()
        assert "evidence" in data, "Response root must contain 'evidence' key"
        evidence = data["evidence"]

        # Schema version check
        assert evidence.get("schema_version") == "scp-evidence-authority-v1"

        # Commit SHA checks
        tested_sha = evidence.get("tested_sha")
        assert isinstance(tested_sha, str)
        assert len(tested_sha) == 40, f"tested_sha length must be 40, got {len(tested_sha)}"
        assert re.match(r"^[0-9a-f]{40}$", tested_sha), f"tested_sha {tested_sha} not lowercase hex"
        assert tested_sha == expected_head, (
            f"tested_sha {tested_sha} did not match git HEAD {expected_head}"
        )

        # Evidence digest checks
        digest = evidence.get("evidence_digest")
        assert isinstance(digest, str)
        assert len(digest) == 64, f"evidence_digest must be 64-char sha256 hex, got {len(digest)}"
        assert re.match(r"^[0-9a-f]{64}$", digest)

        # Snapshot digest checks
        snapshot = evidence.get("snapshot_digest")
        assert isinstance(snapshot, str)
        assert len(snapshot) == 64
        assert re.match(r"^[0-9a-f]{64}$", snapshot)

        # Artifact hashes dictionary checks
        hashes = evidence.get("artifact_hashes")
        assert isinstance(hashes, dict), "artifact_hashes must be a dictionary"
        assert "repository_tree_sha256" in hashes
        assert re.match(r"^[0-9a-f]{64}$", hashes["repository_tree_sha256"])

        # Subsections inside artifact_hashes
        for section in ("dna", "skills", "test_profile", "config", "manifest"):
            assert section in hashes, f"Missing section '{section}' in artifact_hashes"
            assert isinstance(hashes[section], dict), f"Section '{section}' must be a dict"
            assert len(hashes[section]) > 0, f"Section '{section}' must not be empty"

    def test_probe_c02_cryptographic_evidence_verification(
        self, client: TestClient
    ):
        """Validate that the generated evidence passes EvidenceAuthority.validate_evidence."""
        resp = client.get(
            "/v100/release/evidence",
            headers={"Authorization": f"Bearer {_TEST_TOKEN}"},
        )
        assert resp.status_code == 200
        tested_sha = resp.json()["evidence"]["tested_sha"]

        evidence_file = _REPO_ROOT / "data" / "release_evidence.json"
        assert evidence_file.exists()

        authority = EvidenceAuthority(_REPO_ROOT)
        is_valid = authority.validate_evidence(evidence_file, current_head=tested_sha)
        assert is_valid is True, "EvidenceAuthority.validate_evidence returned False for generated evidence"

    def test_probe_c03_explicit_valid_sha_in_environment(
        self, client: TestClient, monkeypatch
    ):
        """When SCP_GIT_SHA is set to a valid 40-char commit, it resolves that commit."""
        expected_head = subprocess.check_output(
            ["git", "-C", str(_REPO_ROOT), "rev-parse", "HEAD"],
            text=True,
        ).strip()
        monkeypatch.setenv("SCP_GIT_SHA", expected_head)

        resp = client.get(
            "/v100/release/evidence",
            headers={"Authorization": f"Bearer {_TEST_TOKEN}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["evidence"]["tested_sha"] == expected_head


# =============================================================================
# PROBE D: Physical Creation of `release_evidence.json` on Disk
# =============================================================================

class TestProbeDPhysicalFileCreation:
    """Probe D: Verify that `release_evidence.json` is physically created on disk with matching content."""

    def test_probe_d01_physical_file_created_and_valid_json(
        self, client: TestClient
    ):
        """Verify release_evidence.json exists on disk, size > 0, and has valid JSON syntax."""
        evidence_file = _REPO_ROOT / "data" / "release_evidence.json"

        # If file existed from earlier runs, record mtime before request
        mtime_before = evidence_file.stat().st_mtime if evidence_file.exists() else 0

        resp = client.get(
            "/v100/release/evidence",
            headers={"Authorization": f"Bearer {_TEST_TOKEN}"},
        )
        assert resp.status_code == 200
        response_evidence = resp.json()["evidence"]

        # Physical presence checks
        assert evidence_file.exists(), f"Physical file {evidence_file} was not created on disk"
        assert evidence_file.is_file(), f"{evidence_file} is not a regular file"
        file_size = evidence_file.stat().st_size
        assert file_size > 0, f"File {evidence_file} is empty (0 bytes)"

        # Physical content checks
        raw_content = evidence_file.read_text(encoding="utf-8")
        parsed_on_disk = json.loads(raw_content)

        # Deep equality between HTTP response payload and physical file on disk
        assert parsed_on_disk == response_evidence, (
            "Physical file contents on disk do not match HTTP response payload"
        )
        assert parsed_on_disk["tested_sha"] == response_evidence["tested_sha"]
        assert parsed_on_disk["evidence_digest"] == response_evidence["evidence_digest"]

    def test_probe_d02_custom_data_dir_respected(
        self, client: TestClient, monkeypatch
    ):
        """Verify endpoint respects custom SCP_DATA_DIR and creates release_evidence.json there."""
        custom_dir_name = "data_challenger_probe_d_test"
        custom_dir_path = _REPO_ROOT / custom_dir_name
        custom_file_path = custom_dir_path / "release_evidence.json"

        # Ensure directory does not exist prior to test
        if custom_dir_path.exists():
            shutil.rmtree(custom_dir_path, ignore_errors=True)

        try:
            monkeypatch.setenv("SCP_DATA_DIR", custom_dir_name)

            resp = client.get(
                "/v100/release/evidence",
                headers={"Authorization": f"Bearer {_TEST_TOKEN}"},
            )
            assert resp.status_code == 200

            # File must be physically created in the custom directory
            assert custom_file_path.exists(), (
                f"File not created at custom directory: {custom_file_path}"
            )
            assert custom_file_path.stat().st_size > 0
            disk_content = json.loads(custom_file_path.read_text(encoding="utf-8"))
            assert disk_content["tested_sha"] == resp.json()["evidence"]["tested_sha"]
        finally:
            # Clean up workspace
            if custom_dir_path.exists():
                shutil.rmtree(custom_dir_path, ignore_errors=True)
