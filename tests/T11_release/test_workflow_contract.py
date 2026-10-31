"""Contract tests for tools/check_workflow_contract.py (ci-release-gate, 2026-10-31).

Why: scp-release-gate.yml was rejected by GitHub ("workflow file issue") on every
push from 24/10 to 31/10 because one checkout step had two ``with:`` keys.
``yaml.safe_load`` keeps the last duplicate silently, so every local check said
"fine". These tests prove the gate (a) passes on the real repository, and
(b) turns red for each fault class that actually broke CI (anti-placebo mutants),
each mutant applied to a COPY of the real workflow files, never a synthetic toy.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOL = REPO_ROOT / "tools" / "check_workflow_contract.py"
sys.path.insert(0, str(REPO_ROOT / "tools"))

import check_workflow_contract as contract  # noqa: E402


def _copy_repo_subset(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    shutil.copytree(REPO_ROOT / ".github" / "workflows", root / ".github" / "workflows")
    shutil.copy2(REPO_ROOT / "Dockerfile", root / "Dockerfile")
    return root


def _run_tool(root: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(TOOL), "--root", str(root)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(REPO_ROOT),
    )


def _mutate(root: Path, workflow: str, old: str, new: str) -> None:
    path = root / ".github" / "workflows" / workflow
    text = path.read_text(encoding="utf-8")
    assert text.count(old) >= 1, f"mutant anchor not found in {workflow}: {old!r}"
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def test_real_repository_workflows_satisfy_contract() -> None:
    proc = _run_tool(REPO_ROOT)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "status=PASS_WITHIN_SCOPE" in proc.stdout
    assert "workflows=8" in proc.stdout


def test_strict_loader_rejects_duplicate_key_that_safe_load_hides() -> None:
    text = "steps:\n  - uses: actions/checkout@x\n    with:\n      fetch-depth: 0\n    with:\n      ref: abc\n"
    # PyYAML last-wins: the very blind spot that hid the release-gate fault.
    assert yaml.safe_load(text)["steps"][0]["with"] == {"ref": "abc"}
    with pytest.raises(contract.DuplicateKeyError, match="duplicate key 'with' at line 5"):
        contract.strict_load(text)


MUTANTS = {
    # M1: re-introduce e12283ca (second `with:` on the release-gate checkout).
    "m1_duplicate_with_key": (
        "scp-release-gate.yml",
        "        with:\n          ref: ${{ github.event.pull_request.head.sha || github.sha }}\n",
        "        with:\n          fetch-depth: 0\n        with:\n          ref: ${{ github.event.pull_request.head.sha || github.sha }}\n",
        "duplicate key 'with'",
    ),
    # M2: put bun back on PATH (violates the zero-bun eradication invariant tests).
    "m2_setup_bun_back": (
        "scp-system-audit.yml",
        "      - name: Generate Ephemeral CI Secrets",
        "      - uses: oven-sh/setup-bun@0c5077e51419868618aeaa5fe8019c62421857d6 # v2\n"
        "        with:\n          bun-version: '1.3.14'\n      - name: Generate Ephemeral CI Secrets",
        "installs bun",
    ),
    # M3: Node 20 again (no --experimental-strip-types; drift from Dockerfile node:24).
    "m3_node_20": (
        "scp-rc-promotion.yml",
        "node-version: '24'",
        "node-version: '20'",
        "node-version 20 < 22",
    ),
    # M4: publish the Dockerfile's last (LOCAL-ONLY audit) stage again.
    "m4_publish_without_target": (
        "container-publish.yml",
        "            --target runtime \\\n",
        "",
        "without --target",
    ),
    # M5: shallow checkout for a full-suite job (T00 trusted base missing).
    "m5_shallow_full_suite": (
        "scp-system-audit.yml",
        "          fetch-depth: 0\n",
        "          fetch-depth: 1\n",
        "fetch-depth=1;",
    ),
    # M6: verify step pins a different Node than setup-node provides.
    "m6_verify_step_mismatch": (
        "scp-release-gate.yml",
        "process.versions.node.split('.')[0] !== '24'",
        "process.versions.node.split('.')[0] !== '22'",
        "Node verify step pins 22",
    ),
    # M7: publish the audit stage explicitly.
    "m7_publish_audit_target": (
        "container-publish.yml",
        "            --target runtime \\\n",
        "            --target audit \\\n",
        "LOCAL-ONLY stage 'audit'",
    ),
}


@pytest.mark.parametrize("mutant_id", sorted(MUTANTS))
def test_mutant_turns_gate_red(tmp_path: Path, mutant_id: str) -> None:
    workflow, old, new, expected = MUTANTS[mutant_id]
    root = _copy_repo_subset(tmp_path)
    clean = _run_tool(root)
    assert clean.returncode == 0, f"copy must start green: {clean.stdout}"
    _mutate(root, workflow, old, new)
    proc = _run_tool(root)
    assert proc.returncode == 1, f"{mutant_id} survived: {proc.stdout}{proc.stderr}"
    assert expected in proc.stdout, proc.stdout


def test_missing_workflow_dir_is_tool_error_not_pass(tmp_path: Path) -> None:
    (tmp_path / "Dockerfile").write_text("FROM node:24 AS n\n", encoding="utf-8")
    proc = _run_tool(tmp_path)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "TOOL ERROR (fail-closed)" in proc.stderr


def test_dockerfile_without_node_stage_fails_closed(tmp_path: Path) -> None:
    root = _copy_repo_subset(tmp_path)
    (root / "Dockerfile").write_text("FROM python:3.12-slim AS runtime\n", encoding="utf-8")
    proc = _run_tool(root)
    assert proc.returncode == 1
    assert "no 'FROM node:<major>' stage" in proc.stdout
