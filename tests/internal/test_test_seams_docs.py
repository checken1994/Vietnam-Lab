# [HC-61 2026-10-23] Anti-placebo pin cho docs/architecture/TEST_SEAMS.md.
#
# Mọi symbol trong bảng seam chính thức phải còn tồn tại (import được). Nếu
# production xóa/dời symbol mà quên cập nhật TEST_SEAMS.md → test này văng.
# Ngược lại, thêm seam mới vào docs mà symbol chưa tồn tại → cũng văng.
from __future__ import annotations

import importlib
import re
from dataclasses import dataclass
from pathlib import Path

DOCS_PATH = Path(__file__).resolve().parents[2] / "docs" / "architecture" / "TEST_SEAMS.md"

_ROW_RE = re.compile(r"^\|\s*`([^`|]+)`\s*\|", re.MULTILINE)


@dataclass(frozen=True)
class SeamRef:
    module: str
    attr: str


def _parse_seam_symbols() -> list[SeamRef]:
    """Parse cột Symbol của bảng seam: các ô `` `module.attr` `` (có thể chứa
    nhiều attr cùng module cách nhau bằng '/') thành danh sách SeamRef."""
    text = DOCS_PATH.read_text(encoding="utf-8")
    refs: list[SeamRef] = []
    last_module = ""
    for raw in text.splitlines():
        if not raw.strip().startswith("|"):
            continue
        cells = [c.strip() for c in raw.strip().strip("|").split("|")]
        if not cells or not cells[0].startswith("`"):
            continue  # header/separator hoặc cột không phải symbol
        symbol_cell = cells[0].strip("`").strip()
        for part in symbol_cell.split("/"):
            part = part.strip().strip("`").strip()
            if not part:
                continue
            if "." in part:
                module, _, attr = part.rpartition(".")
                last_module = module
                refs.append(SeamRef(module=module, attr=attr))
            else:
                # attr bare kế tiếp module đầy đủ ngay trước nó trong cùng ô
                if not last_module:
                    raise AssertionError(f"Ô symbol không có module đầy đủ: {raw!r}")
                refs.append(SeamRef(module=last_module, attr=part))
    return refs


def test_seams_doc_exists_and_parseable() -> None:
    assert DOCS_PATH.exists(), "TEST_SEAMS.md phải tồn tại (HC-61)"
    refs = _parse_seam_symbols()
    assert len(refs) >= 10, f"bảng seam quá ít symbol ({len(refs)}) — parse hỏng hoặc bảng bị rỗng"
    assert any(r.attr == "get_judge" for r in refs)
    assert any(r.attr == "_pc_kill_switch_engaged" for r in refs)


def test_every_documented_seam_symbol_still_importable() -> None:
    refs = _parse_seam_symbols()
    missing: list[str] = []
    for ref in refs:
        try:
            module = importlib.import_module(ref.module)
        except Exception as exc:  # noqa: BLE001 — báo đúng symbol hỏng
            missing.append(f"{ref.module} (import lỗi: {exc!r})")
            continue
        if not hasattr(module, ref.attr):
            missing.append(f"{ref.module}.{ref.attr}")
    assert missing == [], (
        "TEST_SEAMS.md ghi seam không còn tồn tại — cập nhật docs CÙNG code: "
        + ", ".join(missing)
    )


def test_seams_doc_names_call_site_rule() -> None:
    text = DOCS_PATH.read_text(encoding="utf-8")
    # Quy tắc namespace phải nằm trong docs — chống ai đó xoá mất hướng dẫn:
    assert "call-site" in text
    assert "Import runtime" in text
