#!/usr/bin/env python3
"""W0 truth check: fail when a CURRENT doc or code comment calls the WHY Gate a hard gate.

Why this exists: the owner-approved WHY contract (W1) is not implemented yet. Today's
scp/meta/why_gate.py is a regex ADVISORY gate whose "can't decide" path (UPHOLD) still
allows the action, and the recursive WHY engine ("Neo") was unwired by commit 86fef978.
Calling it a "chốt" / primary control gate / fail-closed gate would be a false claim
(DNA: PASS != TRUE). Lines that intentionally keep a historical claim must carry the
marker ``[W0-historical]``.

Usage: python tools/check_why_claims.py [repo_root]   (exit 0 = no claim found, 1 = claims found)
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

CLAIM_PATTERNS = [
    re.compile(r"WHY\s*=\s*CH[ỐO]T", re.IGNORECASE),
    re.compile(r"primary\s+control\s+gate", re.IGNORECASE),
    re.compile(r"WHY[- ]?gate\s*\(fail-closed\)", re.IGNORECASE),
    re.compile(r"WHY\s+is\s+now\s+PRIMARY", re.IGNORECASE),
]
ALLOW_MARKER = "[W0-historical]"
SCAN_FILES = ["GA.md", "AGENTS.md"]
SCAN_GLOBS = ["docs/architecture/**/*.md", ".agents/**/*.md", "scp/**/*.py"]


def find_claims(root: Path) -> list[tuple[str, int, str]]:
    paths: set[Path] = {root / f for f in SCAN_FILES if (root / f).is_file()}
    for pattern in SCAN_GLOBS:
        paths.update(p for p in root.glob(pattern) if p.is_file())
    hits: list[tuple[str, int, str]] = []
    for path in sorted(paths):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            hits.append((str(path.relative_to(root)), 0, f"unreadable: {exc}"))
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if ALLOW_MARKER in line:
                continue
            if any(p.search(line) for p in CLAIM_PATTERNS):
                hits.append((path.relative_to(root).as_posix(), lineno, line.strip()[:160]))
    return hits


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else Path(__file__).resolve().parents[1]
    hits = find_claims(root)
    for rel, lineno, line in hits:
        print(f"{rel}:{lineno}: {line}")
    print(f"why_claims_found={len(hits)}")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
