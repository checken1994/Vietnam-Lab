"""[L0a 2026-10-31] Báo cáo CHỈ-ĐỌC: kho học thiếu provenance / mang dấu nguồn test.

Không ghi, không migrate, không xóa: SQLite được chụp (copy main + -wal + -shm) sang thư
mục tạm rồi mới mở bản sao; sha256 của file nguồn được đo trước/sau và in ra để chứng minh
nguồn không đổi. Mục đích: lineage cho lane quarantine sau (không tự quarantine).

Phân loại:
  - ``by_provenance``: giá trị cột/trường ``provenance`` có cấu trúc (L0a). Bản ghi không
    có cột/trường ⇒ ``legacy_untrusted`` (đúng ngữ nghĩa reader L0a: không bao giờ phục vụ).
  - ``heuristic_test_marker``: CHỈ THÔNG TIN, KHÔNG phải cổng — đếm bản ghi có chuỗi
    "pytest" trong trường đường dẫn (lệnh tái lập của checker, Phụ lục A thiết kế). Không
    dùng để phân loại hay lọc trong product.

Dùng:  python -X utf8 tools/learning_provenance_report.py --data-dir <dir> [--data-dir <dir2>]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
import tempfile
from collections import Counter
from pathlib import Path

SQLITE_STORES = {
    "failure_ledger.sqlite3": {"failure_records": "target_module"},
    "kb_evolve.sqlite": {"lessons": "bug_file", "evolved_patterns": None},
}
JSONL_STORES = ("error_store.jsonl", "error_store_rejected.jsonl")
LEGACY = "legacy_untrusted"


def _sha(path: Path) -> str | None:
    if not path.exists():
        return None
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _family(path: Path) -> list[Path]:
    return [p for p in (path, Path(f"{path}-wal"), Path(f"{path}-shm")) if p.exists()]


def _sqlite_report(path: Path) -> dict:
    out: dict = {"tables": {}}
    with tempfile.TemporaryDirectory(prefix="scp-prov-report-") as tmp:
        copy = Path(tmp) / path.name
        for src in _family(path):
            shutil.copyfile(src, Path(tmp) / src.name)
        conn = sqlite3.connect(str(copy))
        try:
            for table, marker_col in SQLITE_STORES[path.name].items():
                exists = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
                ).fetchone()
                if not exists:
                    out["tables"][table] = {"present": False}
                    continue
                cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
                has_prov = "provenance" in cols
                select = ["provenance" if has_prov else "NULL"]
                select.append(marker_col if marker_col else "NULL")
                rows = conn.execute(f"SELECT {', '.join(select)} FROM {table}").fetchall()
                by_prov = Counter((r[0] or LEGACY) for r in rows)
                marker = sum(1 for r in rows if r[1] and "pytest" in str(r[1]))
                out["tables"][table] = {
                    "present": True,
                    "total": len(rows),
                    "has_provenance_column": has_prov,
                    "by_provenance": dict(by_prov),
                    "missing_provenance": by_prov.get(LEGACY, 0),
                    "heuristic_test_marker": marker if marker_col else None,
                }
        finally:
            conn.close()
    return out


def _jsonl_report(path: Path) -> dict:
    total = 0
    bad = 0
    by_prov: Counter = Counter()
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                bad += 1
                continue
            total += 1
            by_prov[(rec.get("provenance") if isinstance(rec, dict) else None) or LEGACY] += 1
    return {"total": total, "unparseable": bad, "by_provenance": dict(by_prov),
            "missing_provenance": by_prov.get(LEGACY, 0)}


def report(data_dir: Path) -> dict:
    data_dir = data_dir.resolve()
    result: dict = {"data_dir": str(data_dir), "stores": {}}
    for name in list(SQLITE_STORES) + list(JSONL_STORES):
        path = data_dir / name
        if not path.exists():
            result["stores"][name] = {"present": False}
            continue
        family = _family(path) if name in SQLITE_STORES else [path]
        before = {p.name: _sha(p) for p in family}
        body = _sqlite_report(path) if name in SQLITE_STORES else _jsonl_report(path)
        after = {p.name: _sha(p) for p in family}
        body.update({"present": True, "sha256_before": before, "sha256_after": after,
                     "source_unchanged_by_report": before == after})
        result["stores"][name] = body
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data-dir", action="append", required=True, type=Path)
    args = ap.parse_args(argv)
    out = [report(d) for d in args.data_dir]
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
