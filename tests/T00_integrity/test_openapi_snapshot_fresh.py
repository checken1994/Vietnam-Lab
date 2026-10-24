"""T00 — OpenAPI snapshot freshness & auth-drift tripwire (K5-W2 / HC-51).

Background (vùng âm wave-4d): `dashboard/src/app/api/scp/routes/route.ts`
advertised `/ask`, `/v1/chat/completions`, `/v1/models` as `authRequired: false`
while the backend reality is `Depends(verify_jwt_token)` on all three
(probe returns 401 without a valid Bearer — proven in the security audit).

This test guards the OTHER half of that contract: `docs/spec/openapi_snapshot.json`
is a static export that can silently go stale. It must therefore:

  1. carry explicit provenance (`x-scp-snapshot-meta`) — a snapshot without a
     generation timestamp must not be treatable as fresh (DNA #22: PASS ≠ TRUE);
  2. contain the minimum path set of the full API profile it claims to be;
  3. keep the HTTPBearer security requirement on the three auth-critical routes
     so a regen cannot silently drop auth metadata (same drift the dashboard
     route table had);
  4. [C2-W2] actually be verified against the live app: verifiedAgainstLiveApp
     must be True (a stale snapshot must be regenerated, never shipped stale),
     with a parseable verifiedAt timestamp and a real verifiedCommit SHA so
     the schema is traceable to the exact source tree that produced it.

Design: pure-static (JSON + route_profile constants) — NO app boot, so this
runs fast in the audit container. The negative branch mutates a copy of the
snapshot and proves the validator FAILS (DNA #19/#25 anti-placebo).

Regeneration is intentionally NOT part of this test: booting the full app to
regen is a heavier job owned by `tools/export_openapi_snapshot.py`. If the
snapshot is stale, that tool must be run (it now stamps fresh provenance).
"""
from __future__ import annotations

import copy
import json
from datetime import datetime
from pathlib import Path

import re

REPO_ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT_PATH = REPO_ROOT / "docs" / "spec" / "openapi_snapshot.json"

# verifiedCommit must be a real git SHA (short 7+ or full 40 hex). "unknown"
# or any placeholder fails closed: an untraceable snapshot must not ship.
_COMMIT_SHA_RE = re.compile(r"[0-9a-f]{7,40}")

# Full-profile minimum: paths that MUST exist if the snapshot really was
# exported from a full-profile app. /health + / come from the trace/root
# surface; /ask from chat; the /v1 pair from openai_compat (GROUP_MINIMUM
# level "standard" ≤ "full", see scp/api/route_profile.py).
FULL_PROFILE_MINIMUM_PATHS = ("/", "/ask", "/health", "/v1/chat/completions", "/v1/models")

# The three routes whose auth metadata drifted in the dashboard route table
# (wave-4d). Their OpenAPI operations must keep a security requirement.
AUTH_CRITICAL_OPERATIONS = {
    "/ask": "post",
    "/v1/chat/completions": "post",
    "/v1/models": "get",
}

MINIMUM_FULL_PROFILE_PATH_COUNT = 180  # matches the ARC-04 tripwire in test_meta_audit.py


def _load_snapshot() -> dict:
    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


def _validate(snapshot: dict) -> list[str]:
    """Return a list of contract violations. Empty list = contract holds."""
    errors: list[str] = []

    meta = snapshot.get("x-scp-snapshot-meta")
    if not isinstance(meta, dict):
        errors.append(
            "missing top-level 'x-scp-snapshot-meta': a snapshot without provenance "
            "must not be treatable as fresh (regen via tools/export_openapi_snapshot.py)"
        )
        return errors

    generated_at = meta.get("generatedAt")
    if not isinstance(generated_at, str) or not generated_at.strip():
        errors.append("x-scp-snapshot-meta.generatedAt missing/empty")
    else:
        try:
            datetime.fromisoformat(generated_at)
        except ValueError:
            errors.append(f"x-scp-snapshot-meta.generatedAt is not ISO-8601: {generated_at!r}")

    verified = meta.get("verifiedAgainstLiveApp")
    # [C2-W2] The shipped snapshot MUST be verified against the live app. A
    # snapshot that was never verified is stale by definition: it must be
    # regenerated via tools/export_openapi_snapshot.py, not shipped with a
    # warning attached (fail-closed — no silent degradation of provenance).
    if verified is not True:
        errors.append(
            "verifiedAgainstLiveApp is not true: this snapshot was never verified "
            "against live app.openapi() — regenerate via tools/export_openapi_snapshot.py "
            "with the full-profile env before shipping"
        )

    verified_at = meta.get("verifiedAt")
    if not isinstance(verified_at, str) or not verified_at.strip():
        errors.append("x-scp-snapshot-meta.verifiedAt missing/empty (required when verifiedAgainstLiveApp=true)")
    else:
        try:
            datetime.fromisoformat(verified_at)
        except ValueError:
            errors.append(f"x-scp-snapshot-meta.verifiedAt is not ISO-8601: {verified_at!r}")

    verified_commit = str(meta.get("verifiedCommit", "") or "")
    if not _COMMIT_SHA_RE.fullmatch(verified_commit):
        errors.append(
            f"x-scp-snapshot-meta.verifiedCommit must be a real git SHA (7-40 hex chars), "
            f"got {verified_commit!r} — the schema must stay traceable to its source tree"
        )

    paths = snapshot.get("paths")
    if not isinstance(paths, dict) or not paths:
        errors.append("snapshot has no 'paths' object")
        return errors

    if str(meta.get("profile", "")).strip().lower() == "full":
        if len(paths) < MINIMUM_FULL_PROFILE_PATH_COUNT:
            errors.append(
                f"snapshot claims profile=full but only has {len(paths)} paths "
                f"(< {MINIMUM_FULL_PROFILE_PATH_COUNT})"
            )
        for required in FULL_PROFILE_MINIMUM_PATHS:
            if required not in paths:
                errors.append(f"full-profile minimum path missing from snapshot: {required}")

    for route_path, method in AUTH_CRITICAL_OPERATIONS.items():
        op = (paths.get(route_path) or {}).get(method)
        if op is None:
            errors.append(f"auth-critical operation missing: {method.upper()} {route_path}")
            continue
        security = op.get("security")
        if not security:
            errors.append(
                f"{method.upper()} {route_path} lost its security requirement in the "
                "snapshot — this is exactly the auth-drift class fixed in the dashboard "
                "route table (wave-4d); regenerate with verify_jwt_token intact"
            )

    return errors


def test_snapshot_exists_and_is_valid_json():
    assert SNAPSHOT_PATH.is_file(), f"{SNAPSHOT_PATH} must exist"
    snapshot = _load_snapshot()
    assert isinstance(snapshot, dict)
    assert "openapi" in snapshot, "snapshot must be an OpenAPI document"


def test_snapshot_carries_provenance_meta_and_contract_holds():
    """Positive branch: the shipped snapshot satisfies every freshness/auth rule."""
    snapshot = _load_snapshot()
    errors = _validate(snapshot)
    assert not errors, "OpenAPI snapshot contract violated:\n- " + "\n- ".join(errors)


def test_validator_actually_fails_on_stale_or_auth_drifted_snapshot():
    """Negative/anti-placebo branch: each drift class below must be DETECTED.

    A validator that only ever returns [] on any input is a placebo (DNA #25),
    so we prove it fires on: (a) missing provenance meta, (b) a snapshot
    claiming verifiedAgainstLiveApp=false, (c) missing/garbled verifiedAt,
    (d) missing/placeholder verifiedCommit, (e) missing auth security on
    /v1/models.
    """
    snapshot = _load_snapshot()

    no_meta = copy.deepcopy(snapshot)
    no_meta.pop("x-scp-snapshot-meta", None)
    assert any("x-scp-snapshot-meta" in e for e in _validate(no_meta)), (
        "validator must fail when provenance meta is stripped"
    )

    unverified = copy.deepcopy(snapshot)
    unverified["x-scp-snapshot-meta"]["verifiedAgainstLiveApp"] = False
    assert any("verifiedAgainstLiveApp" in e for e in _validate(unverified)), (
        "validator must fail when the snapshot claims it was never verified against the live app"
    )

    bad_verified_at = copy.deepcopy(snapshot)
    bad_verified_at["x-scp-snapshot-meta"]["verifiedAt"] = "not-a-timestamp"
    assert any("verifiedAt" in e for e in _validate(bad_verified_at)), (
        "validator must fail when verifiedAt is not a parseable timestamp"
    )

    no_verified_at = copy.deepcopy(snapshot)
    no_verified_at["x-scp-snapshot-meta"].pop("verifiedAt", None)
    assert any("verifiedAt" in e for e in _validate(no_verified_at)), (
        "validator must fail when verifiedAt is missing"
    )

    for bad_commit in ("", "unknown", "HEAD", "zzzz-not-a-sha"):
        bad_commit_snap = copy.deepcopy(snapshot)
        bad_commit_snap["x-scp-snapshot-meta"]["verifiedCommit"] = bad_commit
        assert any("verifiedCommit" in e for e in _validate(bad_commit_snap)), (
            f"validator must fail on non-SHA verifiedCommit={bad_commit!r}"
        )

    auth_drift = copy.deepcopy(snapshot)
    auth_drift["paths"].pop("/v1/models", None)
    assert any("/v1/models" in e for e in _validate(auth_drift)), (
        "validator must fail when /v1/models disappears from the snapshot"
    )

    auth_removed = copy.deepcopy(snapshot)
    auth_removed["paths"]["/v1/models"]["get"].pop("security", None)
    assert any("security" in e for e in _validate(auth_removed)), (
        "validator must fail when /v1/models loses its security requirement"
    )


def test_snapshot_profile_claim_matches_full_profile_group_minimum():
    """The snapshot claims profile=full; every GROUP_MINIMUM group must therefore
    be represented by at least one registered surface. openai_compat (minimum
    'standard' ≤ full) must expose the /v1 pair; this cross-checks the snapshot
    against scp/api/route_profile.py so the two cannot drift apart silently."""
    from scp.api.route_profile import GROUP_MINIMUM, PROFILE_LEVEL

    snapshot = _load_snapshot()
    paths = snapshot.get("paths", {})
    meta_profile = str((snapshot.get("x-scp-snapshot-meta") or {}).get("profile", "")).strip().lower()

    assert meta_profile == "full", (
        f"snapshot meta claims profile={meta_profile!r}; this tripwire is written for "
        "the full profile — update FULL_PROFILE_MINIMUM_PATHS if the export profile changes"
    )
    # sanity: GROUP_MINIMUM must place openai_compat at a level reachable by full
    assert PROFILE_LEVEL["full"] >= PROFILE_LEVEL[GROUP_MINIMUM["openai_compat"]]
    assert "/v1/chat/completions" in paths and "/v1/models" in paths, (
        "full profile must include the openai_compat surface (/v1/chat/completions, /v1/models)"
    )
