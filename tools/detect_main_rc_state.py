#!/usr/bin/env python3
"""Detect whether a main push actually carries RC (release-candidate) state.

CONTRACT-5 (Wave 4): the RC promotion workflow used to run
manifest-provenance / main-lineage-authority / customer-handoff-verdict on
EVERY push to main, so a docs-only push went RED even though no RC handoff
was in scope. This tool classifies the push WITHOUT weakening any gate:

- rc_state=true means the main HEAD tree is byte-identical (same Git tree
  OID) to the ``captured_tree`` frozen in the committed snapshot manifest —
  i.e. the push carries the RC_DONE manifest state of the release flow. This
  only UNLOCKS the (still fail-closed) RC verification jobs; it never marks
  them successful.
- rc_state=false means an ordinary push (docs/code outside a release
  freeze); RC-only jobs may then be skipped with a neutral conclusion.
  Skipping is not a pass: the handoff verdict still requires every RC job to
  have run and succeeded, so a forged or missing RC state can never produce
  a CUSTOMER_HANDOFF_PASS.

The classifier is a pure function so it can be unit-tested
(tests/T11_release/test_detect_main_rc_state.py).
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

DEFAULT_MANIFEST = Path("reports/manifests_202608/ROOT_SCP_SNAPSHOT_MANIFEST_20260826.json")
_SHA40 = re.compile(r"[0-9a-f]{40}")


def classify_rc_state(
    manifest_bytes: bytes | None, head_tree: str
) -> tuple[bool, str]:
    """Pure classifier: manifest content + HEAD tree -> (rc_state, reason).

    ``manifest_bytes=None`` means the manifest file is absent from the tree.
    """
    if manifest_bytes is None:
        return False, "manifest_absent"
    try:
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return False, f"manifest_invalid: {type(exc).__name__}"
    if not isinstance(manifest, dict):
        return False, "manifest_invalid: not a JSON object"
    captured_tree = manifest.get("captured_tree")
    captured_commit = manifest.get("captured_commit")
    if not isinstance(captured_tree, str) or not _SHA40.fullmatch(captured_tree):
        return False, "manifest_invalid: captured_tree missing or not a 40-hex SHA"
    if not isinstance(captured_commit, str) or not _SHA40.fullmatch(captured_commit):
        return False, "manifest_invalid: captured_commit missing or not a 40-hex SHA"
    if not _SHA40.fullmatch(head_tree or ""):
        return False, "head_tree_invalid"
    if head_tree == captured_tree:
        return True, (
            f"HEAD tree equals manifest captured_tree for frozen SHA {captured_commit}"
        )
    return False, (
        f"HEAD tree {head_tree[:12]} != manifest captured_tree {captured_tree[:12]}"
    )


def detect_rc_state(repo_root: Path, manifest_path: Path) -> tuple[bool, str]:
    """Git-backed wrapper around classify_rc_state for the live checkout."""
    try:
        head_tree = subprocess.run(
            ["git", "rev-parse", "HEAD^{tree}"],
            cwd=str(repo_root), capture_output=True, text=True, check=True,
        ).stdout.strip()
    except subprocess.CalledProcessError as exc:
        return False, f"git_tree_unresolvable: {exc.stderr.strip()[:120]}"
    manifest_file = repo_root / manifest_path
    manifest_bytes = manifest_file.read_bytes() if manifest_file.is_file() else None
    return classify_rc_state(manifest_bytes, head_tree)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "manifest", nargs="?", default=str(DEFAULT_MANIFEST),
        help="committed snapshot manifest path (relative to repo root)",
    )
    args = parser.parse_args()
    repo_root = Path(subprocess.check_output(
        ["git", "rev-parse", "--show-toplevel"], text=True,
    ).strip())
    rc_state, reason = detect_rc_state(repo_root, Path(args.manifest))
    print(f"rc_state={'true' if rc_state else 'false'}")
    print(f"reason={reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
