"""SCP Lesson Ledger — Sổ bài học tự lưu của SCP (Luật 12 Owner).

Nguyên tắc:
- Append-only JSONL tại `data/lessons/lessons.jsonl`.
- KHÔNG wire trực tiếp vào đường dẫn /ask (kho lưu trữ nhận thức độc lập).
- Mọi bài học mới mang trạng thái HYPOTHESIS cho tới khi có independent check.
- Kill-switch: Chủ quyền Owner duyệt trước khi chuyển status sang APPLIED.
- Chống tự đầu độc: Không tự ý sinh mã vá sản phẩm từ bài học chưa kiểm chứng.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("scp.learning.lesson_ledger")

VALID_STATUSES = ("HYPOTHESIS", "REALITY-CHECKED", "SHADOW", "APPLIED", "REJECTED")
VALID_NEXT_ACTIONS = ("shadow", "autofix-lane", "watch", "none")
VALID_VERDICTS = ("PENDING", "PASS", "FAIL", "UNKNOWN")


def _resolve_data_dir() -> Path:
    """Resolve data directory dynamically without hardcoded paths."""
    env_dir = os.environ.get("SCP_DATA_DIR")
    if env_dir:
        p = Path(env_dir).resolve()
        if p.exists() or p.parent.exists():
            return p
    # Default to <repo_root>/data
    repo_root = Path(__file__).resolve().parent.parent.parent
    return repo_root / "data"


def get_ledger_file_path() -> Path:
    """Return the absolute path to the lessons.jsonl ledger file."""
    data_dir = _resolve_data_dir()
    lessons_dir = data_dir / "lessons"
    lessons_dir.mkdir(parents=True, exist_ok=True)
    return lessons_dir / "lessons.jsonl"


def _generate_slug(text: str, max_words: int = 4) -> str:
    """Generate a clean slug from text."""
    clean = re.sub(r"[^a-zA-Z0-9\s_-]", "", text)
    words = clean.strip().split()[:max_words]
    slug = "-".join(words).lower()
    return slug or "unnamed"


def record_lesson(
    observation: str,
    hypothesis: str,
    trigger: dict[str, Any] | None = None,
    evidence: list[dict[str, Any]] | None = None,
    lesson_id: str | None = None,
    status: str = "HYPOTHESIS",
    independent_check: dict[str, Any] | None = None,
    why_chain: list[str] | None = None,
    next_action: str = "watch",
    kill_switch_ref: str = "owner-on-top",
    owner_approved: bool = False,
    ledger_path: Path | None = None,
) -> dict[str, Any]:
    """Record a single lesson to the append-only ledger.

    Invariants:
    1. Default status is HYPOTHESIS.
    2. Status APPLIED strictly requires owner_approved=True and independent_check.
    3. Status must be one of VALID_STATUSES.
    4. Append-only write.
    """
    status = status.upper().strip()
    if status not in VALID_STATUSES:
        raise ValueError(f"Invalid status '{status}'. Must be one of {VALID_STATUSES}")

    check = dict(independent_check) if independent_check else {
        "by": "none",
        "verdict": "PENDING",
        "at": None,
    }
    check_verdict = str(check.get("verdict", "PENDING")).upper().strip()
    if check_verdict not in VALID_VERDICTS:
        raise ValueError(f"Invalid independent_check verdict '{check_verdict}'. Must be one of {VALID_VERDICTS}")
    check["verdict"] = check_verdict

    if status == "APPLIED":
        if not owner_approved:
            raise PermissionError(
                "[KILL-SWITCH] Cannot record or promote lesson to APPLIED without explicit owner approval (Law 12)."
            )
        if check.get("verdict") != "PASS":
            raise ValueError(
                "[FAIL-CLOSED] Cannot promote lesson to APPLIED without independent check verdict=PASS."
            )

    now = int(time.time())
    if not lesson_id:
        slug = _generate_slug(hypothesis or observation)
        lesson_id = f"L-{now}-{slug}"

    record: dict[str, Any] = {
        "lesson_id": lesson_id,
        "timestamp": now,
        "trigger": trigger or {
            "source": "runtime",
            "sha": os.environ.get("SCP_GIT_SHA", "unknown"),
            "file": "unknown",
        },
        "observation": observation,
        "hypothesis": hypothesis,
        "evidence": evidence or [],
        "status": status,
        "independent_check": check,
        "why_chain": why_chain or [
            "Gốc lỗi sai: Chưa xác định",
            "Vì sao sửa đúng Reality: Đang chờ thực nghiệm",
            "Vì sao tin bằng chứng lineage: Cần đối chiếu độc lập",
            "Điều gì làm đổi quyết định anti-Goodhart: Chưa ghi nhận",
            "Chỗ UNKNOWN: Đang ở trạng thái giả thuyết",
        ],
        "next_action": next_action if next_action in VALID_NEXT_ACTIONS else "watch",
        "kill_switch_ref": kill_switch_ref,
    }

    target_path = ledger_path or get_ledger_file_path()
    target_path.parent.mkdir(parents=True, exist_ok=True)

    line = json.dumps(record, ensure_ascii=False) + "\n"
    with open(target_path, "a", encoding="utf-8") as f:
        f.write(line)

    logger.info("Recorded lesson [%s] with status [%s]", lesson_id, status)
    return record


def query_lessons(
    status: str | None = None,
    source: str | None = None,
    ledger_path: Path | None = None,
) -> list[dict[str, Any]]:
    """Query lessons from the ledger with optional filters."""
    target_path = ledger_path or get_ledger_file_path()
    if not target_path.exists():
        return []

    results: list[dict[str, Any]] = []
    with open(target_path, "r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            line_str = line.strip()
            if not line_str:
                continue
            try:
                record = json.loads(line_str)
            except Exception as e:
                logger.warning("Corrupt JSON at line %d in %s: %s", line_num, target_path, e)
                continue

            if status and record.get("status") != status.upper().strip():
                continue
            if source and (record.get("trigger") or {}).get("source") != source:
                continue
            results.append(record)

    return results


def summarize_lessons(ledger_path: Path | None = None) -> dict[str, Any]:
    """Compute summary metrics over the lesson ledger."""
    all_lessons = query_lessons(ledger_path=ledger_path)
    counts: dict[str, int] = {st: 0 for st in VALID_STATUSES}
    for l in all_lessons:
        st = l.get("status", "HYPOTHESIS")
        counts[st] = counts.get(st, 0) + 1

    return {
        "total_lessons": len(all_lessons),
        "status_counts": counts,
        "lessons": all_lessons,
    }


def print_summary(ledger_path: Path | None = None) -> None:
    """Print a clean CLI summary table of the lesson ledger."""
    summary = summarize_lessons(ledger_path=ledger_path)
    total = summary["total_lessons"]
    counts = summary["status_counts"]
    lessons = summary["lessons"]

    print("=" * 76)
    print("                SCP LESSON LEDGER — BẢNG TỔNG HỢP NHẬN THỨC                ")
    print("=" * 76)
    print(f"Tổng số bài học đã lưu : {total}")
    print("-" * 76)
    print(f"  • HYPOTHESIS       (Giả thuyết sơ khởi)        : {counts.get('HYPOTHESIS', 0):3d}")
    print(f"  • REALITY-CHECKED  (Đã kiểm chứng độc lập)    : {counts.get('REALITY-CHECKED', 0):3d}")
    print(f"  • SHADOW           (Chạy thử nghiệm song song) : {counts.get('SHADOW', 0):3d}")
    print(f"  • APPLIED          (Đã áp dụng vào hệ thống)   : {counts.get('APPLIED', 0):3d}")
    print(f"  • REJECTED         (Đã bác bỏ do phản chứng)  : {counts.get('REJECTED', 0):3d}")
    print("-" * 76)
    print("DANH SÁCH BÀI HỌC GẦN NHẤT:")
    print(f"{'MÃ BÀI HỌC':<32} | {'TRẠNG THÁI':<15} | {'HÀNH ĐỘNG':<10} | QUAN SÁT THỰC TẾ")
    print("-" * 76)
    for l in lessons[-15:]:
        lid = l.get("lesson_id", "")[:32]
        st = l.get("status", "")[:15]
        act = l.get("next_action", "")[:10]
        obs = l.get("observation", "").replace("\n", " ")[:40]
        print(f"{lid:<32} | {st:<15} | {act:<10} | {obs}")
    print("=" * 76)


def main() -> None:
    parser = argparse.ArgumentParser(description="SCP Lesson Ledger CLI")
    parser.add_argument("--summary", action="store_true", help="Print summary table of all lessons")
    parser.add_argument("--status", type=str, default=None, help="Filter query by status")
    parser.add_argument("--source", type=str, default=None, help="Filter query by trigger source")
    args = parser.parse_args()

    if args.summary or (not args.status and not args.source):
        print_summary()
    else:
        results = query_lessons(status=args.status, source=args.source)
        print(f"Found {len(results)} lesson(s) matching criteria:")
        print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
