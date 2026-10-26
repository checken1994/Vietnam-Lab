#!/usr/bin/env python3
"""Publisher and Indexer for SCP Knowledge Catalog.

Traverses data/knowledge/catalog/facts/ and data/knowledge/catalog/reference/,
computes exact SHA-256 checksums, counts records, increments dataset_version,
updates manifest.json, and validates catalog integrity via validate_knowledge_catalog.py.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add project root to sys.path dynamically (Zero Hardcoded Paths)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.validate_knowledge_catalog import validate_catalog_cli


def compute_sha256(file_path: Path) -> str:
    """Compute hex SHA-256 of file bytes."""
    return hashlib.sha256(file_path.read_bytes()).hexdigest()


def count_and_inspect_records(file_path: Path) -> tuple[int, str | None, str]:
    """Count valid records and determine domain and shard_type."""
    lines = file_path.read_text(encoding="utf-8").splitlines()
    count = 0
    domain: str | None = None
    shard_type = "reference" if "reference" in file_path.parts else "fact"

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        record = json.loads(stripped)
        count += 1
        if domain is None:
            if "domain" in record:
                domain = str(record["domain"]).strip()
            elif "table_name" in record:
                domain = str(record["table_name"]).strip()

    if domain is None:
        domain = file_path.stem

    return count, domain, shard_type


def increment_dataset_version(current_version: str | None) -> str:
    """Increment dataset version string (e.g. 2026.10.26.1 -> 2026.10.26.2)."""
    now_utc = datetime.now(timezone.utc)
    date_prefix = now_utc.strftime("%Y.%m.%d")

    if not current_version:
        return f"{date_prefix}.1"

    match = re.match(r"^(\d{4}\.\d{2}\.\d{2})\.(\d+)$", current_version.strip())
    if match:
        old_date, seq = match.groups()
        if old_date == date_prefix:
            return f"{date_prefix}.{int(seq) + 1}"
        else:
            return f"{date_prefix}.1"

    return f"{date_prefix}.1"


def publish_catalog(catalog_dir: Path) -> int:
    """Publish and validate knowledge catalog in catalog_dir."""
    cat_dir = catalog_dir.resolve()
    if not cat_dir.exists() or not cat_dir.is_dir():
        print(f"[ERROR] Catalog directory does not exist: {cat_dir}", file=sys.stderr)
        return 1

    manifest_path = cat_dir / "manifest.json"
    manifest_data: dict = {
        "manifest_version": 1,
        "schema_version": 1,
        "dataset_version": "2026.10.26.1",
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "shards": [],
        "metadata": {
            "description": "Unified Knowledge Catalog",
        },
    }

    if manifest_path.exists():
        try:
            loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                manifest_data.update(loaded)
        except Exception as exc:
            print(f"[WARN] Failed to load existing manifest: {exc}", file=sys.stderr)

    old_version = manifest_data.get("dataset_version")
    new_version = increment_dataset_version(old_version)
    manifest_data["dataset_version"] = new_version
    manifest_data["created_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Discover shard files
    discovered_shards: list[dict] = []
    scan_dirs = [cat_dir / "facts", cat_dir / "reference"]

    for sub_dir in scan_dirs:
        if not sub_dir.exists():
            continue
        for shard_file in sorted(sub_dir.rglob("*.jsonl")):
            if not shard_file.is_file():
                continue
            rel_path = shard_file.relative_to(cat_dir).as_posix()
            sha256 = compute_sha256(shard_file)
            record_count, domain, shard_type = count_and_inspect_records(shard_file)

            shard_entry = {
                "path": rel_path,
                "sha256": sha256,
                "record_count": record_count,
                "shard_type": shard_type,
                "domain": domain,
            }
            discovered_shards.append(shard_entry)

    discovered_shards.sort(key=lambda s: s["path"])
    manifest_data["shards"] = discovered_shards

    # Write manifest atomically
    temp_manifest = cat_dir / f"manifest.tmp.{int(datetime.now().timestamp())}.json"
    temp_manifest.write_text(json.dumps(manifest_data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temp_manifest.replace(manifest_path)

    print(
        f"[PUBLISHED] Knowledge Catalog published at '{cat_dir}'.\n"
        f"  - New Dataset Version: {new_version}\n"
        f"  - Registered Shards: {len(discovered_shards)}\n"
        f"  - Total Records: {sum(s['record_count'] for s in discovered_shards)}",
        file=sys.stdout,
    )

    # Validate catalog
    val_exit = validate_catalog_cli(catalog_dir=cat_dir, strict=True, check_hashes=True)
    if val_exit != 0:
        print("[ERROR] Post-publication catalog validation failed!", file=sys.stderr)
        return 1

    print("[SUCCESS] Catalog published and 100% verified.", file=sys.stdout)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Publish and index SCP Knowledge Catalog.")
    parser.add_argument(
        "--catalog-dir",
        type=Path,
        default=PROJECT_ROOT / "data" / "knowledge" / "catalog",
        help="Path to catalog directory (default: data/knowledge/catalog)",
    )
    args = parser.parse_args()
    exit_code = publish_catalog(args.catalog_dir)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
