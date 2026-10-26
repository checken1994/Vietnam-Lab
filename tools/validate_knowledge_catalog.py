#!/usr/bin/env python3
"""CLI Validator for SCP Knowledge Catalog (Schema v1).

Validates manifest, shard hashes, schemas, evidence rules, and duplicate IDs.
Exits 0 on valid catalog, exits 1 on error with descriptive stderr messages.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Add project root to sys.path dynamically (Zero Hardcoded Paths)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scp.knowledge.store import (
    CatalogError,
    load_catalog,
)


def validate_catalog_cli(
    catalog_dir: Path,
    strict: bool = True,
    check_hashes: bool = True,
) -> int:
    """Validate a catalog directory, returning 0 on success, 1 on failure."""
    try:
        snapshot = load_catalog(
            catalog_dir=catalog_dir,
            strict=strict,
            check_hashes=check_hashes,
        )
        manifest = snapshot.manifest
        record_count = len(snapshot.records_by_id)
        ref_count = sum(len(tbl) for tbl in snapshot.references.values())
        print(
            f"[VALID] Knowledge catalog at '{catalog_dir}' is valid.\n"
            f"  - Dataset version: {manifest.dataset_version}\n"
            f"  - Manifest version: {manifest.manifest_version}\n"
            f"  - Shards: {len(manifest.shards)}\n"
            f"  - Fact records: {record_count}\n"
            f"  - Reference records: {ref_count}\n"
            f"  - Domains: {', '.join(snapshot.domains) if snapshot.domains else '(none)'}",
            file=sys.stdout,
        )
        return 0
    except CatalogError as exc:
        print(f"[ERROR] Knowledge catalog validation failed: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"[UNEXPECTED ERROR] Catalog validation crashed: {exc}", file=sys.stderr)
        return 1


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate SCP Knowledge Catalog according to Schema v1."
    )
    parser.add_argument(
        "--catalog-dir",
        type=Path,
        default=PROJECT_ROOT / "data" / "knowledge" / "catalog",
        help="Path to catalog directory containing manifest.json (default: data/knowledge/catalog)",
    )
    parser.add_argument(
        "--strict",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Enforce strict validation (record counts, schema compliance)",
    )
    parser.add_argument(
        "--check-hashes",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Verify SHA-256 hashes of all shards against manifest",
    )

    args = parser.parse_args()
    exit_code = validate_catalog_cli(
        catalog_dir=args.catalog_dir,
        strict=args.strict,
        check_hashes=args.check_hashes,
    )
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
