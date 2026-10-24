"""T00 integrity: HIDDEN_COUPLINGS.md ledger drift guard (Wave 0, 2026-10-24).

Anti-placebo mandate: ledger này là checklist kiến trúc bắt buộc (docs-truth,
kỷ luật 5). Test chặn 3 kiểu drift:
1. Ledger bị xóa/đổi chỗ → fail (không được "gọn repo" bằng cách xóa ledger).
2. Thiếu/mừ bất kỳ HC-01..HC-64 nào, hoặc trạng thái không thuộc
   RESOLVED/ACCEPTED/OPEN → fail (mục mở phải có wave nhận).
3. OPEN mà không khai wave (K1..K5) → fail (mục mở phải có chủ).
"""

from __future__ import annotations

import re
from pathlib import Path

LEDGER_PATH = (
    Path(__file__).resolve().parents[2] / "docs" / "architecture" / "HIDDEN_COUPLINGS.md"
)
VALID_STATUSES = {"RESOLVED", "ACCEPTED", "OPEN"}
VALID_WAVES = {"K1", "K2", "K3", "K4", "K5"}


def _ledger_text() -> str:
    assert LEDGER_PATH.is_file(), (
        f"HIDDEN_COUPLINGS ledger missing at {LEDGER_PATH} — kiến trúc ledger "
        "là checklist bắt buộc, không được xóa để 'gọn repo' (anti-placebo)."
    )
    return LEDGER_PATH.read_text(encoding="utf-8")


def test_ledger_exists_and_covers_all_64() -> None:
    text = _ledger_text()
    found_ids = set(re.findall(r"\bHC-(\d{2})\b\s*\|", text))
    expected = {f"{i:02d}" for i in range(1, 65)}
    missing = sorted(expected - found_ids)
    extra = sorted(found_ids - expected)
    assert not missing, f"Ledger thiếu các mục: {missing}"
    assert not extra, f"Ledger chứa ID ngoài phạm vi 64 mục: {extra}"


def test_every_entry_has_valid_status() -> None:
    text = _ledger_text()
    rows = re.findall(
        r"^\|\s*HC-\d{2}\s*\|.*?\|\s*\*\*(RESOLVED|ACCEPTED|OPEN)[^*]*\*\*", text, re.M
    )
    assert len(rows) == 64, (
        f"Mỗi mục phải có đúng 1 trạng thái RESOLVED/ACCEPTED/OPEN; "
        f"thấy {len(rows)}/64 — trạng thái sai định dạng hoặc thiếu."
    )


def test_open_entries_declare_a_wave() -> None:
    text = _ledger_text()
    open_rows = [
        r for r in re.findall(r"^\|\s*HC-\d{2}\s*\|.*$", text, re.M) if "**OPEN" in r
    ]
    # [B4 · 2026-10-24] Khi ledger đạt đích (0 OPEN) test này rỗng-và-pass;
    # khi còn OPEN, mỗi mục buộc khai wave nhận (K1..K5).
    for row in open_rows:
        hc_id = row.split("|")[1].strip()
        # Wave nhận có thể nằm ở cột trạng thái "OPEN (K2)" hoặc cột ghi chú.
        assert re.search(r"\*\*OPEN\s*\(K[1-5]\)\*\*", row) or re.search(r"\bK[1-5]\b", row), (
            f"{hc_id} đang OPEN nhưng không khai wave nhận (K1..K5) ở trạng thái lẫn ghi chú."
        )


def test_summary_bullets_match_table_counts() -> None:
    """[Sửa theo phát hiện của báo cáo audit ngoài 2026-10-24: bullet tóm tắt
    từng ghi stale 'RESOLVED: 10' trong khi bảng có 27 — drift test cũ chỉ
    phủ bảng, không phủ tóm tắt. Bất invariant mới: số trong bullet tóm tắt
    PHẢI khớp đếm độc lập từ bảng.]"""
    text = _ledger_text()
    table_rows = re.findall(r"^\|\s*HC-\d{2}\s*\|.*$", text, re.M)
    r = sum(1 for row in table_rows if "**RESOLVED" in row)
    a = sum(1 for row in table_rows if "**ACCEPTED" in row)
    o = sum(1 for row in table_rows if "**OPEN" in row)
    assert r + a + o == 64, f"Bảng phải đủ 64 mục: {r}/{a}/{o}"
    for label, count in (("RESOLVED", r), ("ACCEPTED", a), ("OPEN", o)):
        m = re.search(rf"^\- \*\*{label}: (\d+)\*\*", text, re.M)
        assert m, f"Tóm tắt thiếu bullet '{label}'"
        assert int(m.group(1)) == count, (
            f"Tóm tắt {label}={m.group(1)} lệch bảng thật {count} — "
            "đây đúng là loại drift mà hệ đang diệt (bài học 2026-10-24)."
        )


def test_resolved_entries_cite_evidence() -> None:
    text = _ledger_text()
    resolved_rows = [
        r for r in re.findall(r"^\|\s*HC-\d{2}\s*\|.*$", text, re.M) if "**RESOLVED" in r
    ]
    assert resolved_rows, "Phải có mục RESOLVED (8 wave fix trước đó là sự thật)."
    for row in resolved_rows:
        hc_id = row.split("|")[1].strip()
        note = row.rsplit("|", 2)[-2]
        # Bằng chứng chấp nhận: git commit (hex ≥7) hoặc tham chiếu probe/audit
        # có định danh (AUD-Sx Eyy, battery, test id) — không chấp nhận lời suông.
        has_evidence = (
            re.search(r"\b[0-9a-f]{7,9}\b", note)
            or re.search(r"AUD-S\d|probe|battery|tests/", note, re.I)
            or "đã có" in note
        )
        assert has_evidence, (
            f"{hc_id} RESOLVED nhưng ghi chú không dẫn commit/bằng chứng truy vết: "
            f"'{note.strip()}'"
        )
