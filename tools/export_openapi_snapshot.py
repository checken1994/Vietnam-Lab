#!/usr/bin/env python3
"""ARC-04: Export OpenAPI Schema Snapshot Tool.

Exports and validates the OpenAPI schema snapshot from the canonical
SCP FastAPI application (scp.api_server:app).
Saves the authoritative schema to docs/spec/openapi_snapshot.json.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def export_openapi_schema(root: Path) -> dict:
    """Generate OpenAPI schema from FastAPI app."""
    # Ensure project root is on sys.path
    root_str = str(root)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)

    from scp.api_server import app

    schema = app.openapi()
    return schema


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    spec_dir = root / "docs" / "spec"
    spec_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = spec_dir / "openapi_snapshot.json"

    print("[ARC-04] Generating OpenAPI schema snapshot from scp.api_server:app...")
    try:
        schema = export_openapi_schema(root)
    except Exception as exc:
        print(f"[ARC-04 FAIL] Failed to generate OpenAPI schema: {exc}")
        return 1

    paths_count = len(schema.get("paths", {}))
    print(f"[ARC-04] Extracted schema: title='{schema.get('info', {}).get('title')}', routes count={paths_count}")

    # [K5-W2 · HC-51] Stamp provenance metadata so the snapshot can never
    # masquerade as fresh without evidence (DNA #22: PASS ≠ TRUE). This makes
    # the file differ from a raw app.openapi() dump — the meta key
    # `x-scp-snapshot-meta` is the only injected key; everything else is raw.
    # Consumers/tests (tests/T00_integrity/test_openapi_snapshot_fresh.py)
    # rely on this block being present.
    from datetime import datetime, timezone

    import os as _os

    schema["x-scp-snapshot-meta"] = {
        "generatedAt": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "generatedAtProvenance": "export_openapi_snapshot.py run against live scp.api_server:app",
        "profile": _os.environ.get("SCP_API_PROFILE", "full (default when SCP_PRODUCTION_MODE unset)"),
        "verifiedAgainstLiveApp": True,
        "staleWarning": "",
    }

    # Write formatted JSON snapshot
    formatted = json.dumps(schema, indent=2, sort_keys=True)
    snapshot_path.write_text(formatted, encoding="utf-8")
    print(f"[ARC-04 PASS] Snapshot saved to {snapshot_path} ({len(formatted)} bytes).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
