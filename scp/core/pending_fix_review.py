"""Human-review guide for fixes that cannot be applied safely."""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path


def ensure_review_guide(pending_dir: str | Path) -> Path:
    """Write a truthful, atomic review guide for the currently queued items."""
    directory = Path(pending_dir)
    directory.mkdir(parents=True, exist_ok=True)
    items = sorted(
        path.name
        for path in directory.glob("*.py")
        if path.is_file()
    )

    lines = [
        "# Pending fix human review",
        "",
        "These files were not applied automatically because the proposed change could not be represented as a safe patch.",
        "",
        "For each queued item:",
        "1. Review the referenced source file and the proposed change together.",
        "2. Choose an explicit decision: approve, reject, or defer.",
        "3. If approved, apply the change manually to the source; do not execute the queued review file.",
        "4. Run the relevant tests and reality checks after the manual change.",
        "5. Remove the queued item only after the decision and resulting evidence are recorded.",
        "",
        "## Currently queued items",
    ]
    if items:
        lines.extend(f"- `{name}`" for name in items)
    else:
        lines.append("- None")
    lines.append("")

    target = directory / "README.md"
    unique_id = f"{os.getpid()}_{threading.get_ident()}_{time.time_ns()}"
    temp = directory / f".README.md.{unique_id}.tmp"
    try:
        with temp.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write("\n".join(lines))
            handle.flush()
            os.fsync(handle.fileno())
        last_exc: Exception | None = None
        for attempt in range(5):
            try:
                os.replace(temp, target)
                last_exc = None
                break
            except (PermissionError, OSError) as exc:
                last_exc = exc
                time.sleep(0.05 * (2 ** attempt))
        if last_exc is not None:
            raise last_exc
    finally:
        try:
            if temp.exists():
                temp.unlink(missing_ok=True)
        except OSError:
            pass
    return target
