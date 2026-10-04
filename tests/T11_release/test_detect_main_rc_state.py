"""CONTRACT-5 (Wave 4): unit contract for the main-push RC-state classifier.

The RC promotion workflow must skip (neutral) the RC-only jobs on ordinary
main pushes while keeping them fully fail-closed when an RC push arrives.
These tests pin the pure classifier in tools/detect_main_rc_state.py:
old behavior (classifier missing) fails because the workflow has no detect
function to bind to; any weakening (e.g. returning true on invalid/absent
manifest, or on tree mismatch) must fail here.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

TOOL_PATH = Path(__file__).resolve().parents[2] / "tools" / "detect_main_rc_state.py"


@pytest.fixture(scope="module")
def classify():
    spec = importlib.util.spec_from_file_location("detect_main_rc_state", TOOL_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.classify_rc_state


TREE = "c1f58d9775121f92f1fe73de1f0e9531916dc98c"
COMMIT = "9ae77540edb3b32351c2e011ccdd1f5153d832e7"
OTHER_TREE = "b" * 40


def _manifest_bytes(tree: str = TREE, commit: str = COMMIT) -> bytes:
    return json.dumps({
        "captured_commit": commit,
        "captured_tree": tree,
        "tracked_count": 1,
    }).encode("utf-8")


def test_rc_state_true_only_when_head_tree_equals_captured_tree(classify):
    rc, reason = classify(_manifest_bytes(), TREE)
    assert rc is True
    assert "captured_tree" in reason
    assert COMMIT in reason


def test_ordinary_push_tree_mismatch_is_not_rc(classify):
    rc, reason = classify(_manifest_bytes(), OTHER_TREE)
    assert rc is False
    assert "captured_tree" in reason


def test_missing_manifest_is_not_rc(classify):
    rc, reason = classify(None, TREE)
    assert rc is False
    assert reason == "manifest_absent"


def test_invalid_manifest_json_is_not_rc(classify):
    rc, reason = classify(b"{not json", TREE)
    assert rc is False
    assert reason.startswith("manifest_invalid")


def test_non_object_manifest_is_not_rc(classify):
    rc, reason = classify(b"[1, 2]", TREE)
    assert rc is False
    assert reason.startswith("manifest_invalid")


def test_manifest_with_bad_captured_tree_is_not_rc(classify):
    rc, reason = classify(
        json.dumps({"captured_commit": COMMIT, "captured_tree": "deadbeef"}).encode(),
        TREE,
    )
    assert rc is False
    assert reason.startswith("manifest_invalid")


def test_manifest_with_bad_captured_commit_is_not_rc(classify):
    rc, reason = classify(
        json.dumps({"captured_commit": "nothex", "captured_tree": TREE}).encode(),
        TREE,
    )
    assert rc is False
    assert reason.startswith("manifest_invalid")


def test_invalid_head_tree_is_not_rc(classify):
    rc, reason = classify(_manifest_bytes(), "not-a-tree")
    assert rc is False
    assert reason == "head_tree_invalid"


def _load_workflow() -> dict:
    import yaml

    workflow_path = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "scp-rc-promotion.yml"
    return yaml.safe_load(workflow_path.read_text(encoding="utf-8"))


def test_workflow_rc_jobs_are_gated_on_detected_rc_state():
    """CONTRACT-5 regression lock: RC-only jobs must skip (neutral) on main
    pushes without RC manifest state, and must still run (fail-closed) when
    rc_state=true. On the integration branch the provenance freeze flow stays
    ungated."""
    jobs = _load_workflow()["jobs"]
    detect = jobs["rc-state-detect"]
    assert detect["if"] == "${{ github.ref == 'refs/heads/main' }}"
    assert "rc_state" in detect["outputs"]

    manifest_if = jobs["manifest-provenance"]["if"]
    assert "github.ref != 'refs/heads/main' || needs.rc-state-detect.outputs.rc_state == 'true'" in manifest_if
    assert "rc-state-detect" in jobs["manifest-provenance"]["needs"]

    lineage_if = jobs["main-lineage-authority"]["if"]
    assert "needs.rc-state-detect.outputs.rc_state == 'true'" in lineage_if
    assert "rc-state-detect" in jobs["main-lineage-authority"]["needs"]

    handoff_if = jobs["customer-handoff-verdict"]["if"]
    assert "needs.rc-state-detect.outputs.rc_state == 'true'" in handoff_if
    assert "rc-state-detect" in jobs["customer-handoff-verdict"]["needs"]
    # Fail-closed preserved: the verdict still requires every RC job success.
    assert "needs.main-lineage-authority.result == 'success'" in handoff_if
    assert "needs.manifest-provenance.result == 'success'" in handoff_if
    assert "needs.manifest-provenance.outputs.ready == 'true'" in handoff_if


def test_workflow_rc_jobs_never_auto_pass_without_manifest_verification():
    """The handoff verdict must keep requiring snapshot-manifest verification;
    skipping detection can unlock nothing without the real gates passing."""
    jobs = _load_workflow()["jobs"]
    handoff_steps = jobs["customer-handoff-verdict"]["steps"]
    assert any(
        "verify_snapshot_manifest.py" in json.dumps(step) for step in handoff_steps
    ), "handoff verdict must still run the snapshot manifest verification"

