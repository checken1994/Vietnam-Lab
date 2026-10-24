"""T00 integrity: anti-resurrection pin — llm-bridge codegen đã bị XÓA SỔ.

Llm-bridge (``mini-services/llm-bridge/``) bị xóa 2026-10-24 (commit
d0125739; docs/ADDENDUM_BRIDGE_REMOVAL_2026-10-24.md). Codegen
``TASK_MODEL_MAP`` tồn tại chỉ để phục vụ bridge nên phải biến mất cùng
bridge. Merge 778832c1 với origin/main từng "hồi sinh" 2 file
(``scripts/generate_task_model_map.py`` + test codegen) do main vẫn giữ
chúng — test này pin sự VẮNG MẶT (pattern ``test_bun_eradicated`` /
``test_supervisor_manages_llm_bridge``) để mọi lần hồi sinh sau này văng
ngay ở T00 thay vì 4 fail ``core.ts không còn``.

Nguồn map duy nhất còn lại: ``TASK_FREE_FALLBACK_MAP`` trong
``scp/llm_gateway/providers.py`` (Python) — Brain Task Registry (PHẦN 3
§3.4) sẽ là nguồn chuẩn hóa cuối.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

FORBIDDEN_PATHS = [
    REPO / "mini-services" / "llm-bridge",
    REPO / "scripts" / "generate_task_model_map.py",
    REPO / "mini-services" / "llm-bridge" / "core.ts",
]


def test_llm_bridge_codegen_stays_eradicated() -> None:
    """Pin sự VẮNG MẶT: bridge + codegen script không được hồi sinh."""
    for path in FORBIDDEN_PATHS:
        assert not path.exists(), (
            f"{path} hồi sinh (resurrection) — llm-bridge đã bị xóa sổ "
            "2026-10-24 theo owner (commit d0125739). Xóa file vừa merge về; "
            "nếu thật sự cần codegen trở lại phải ra quyết định kiến trúc "
            "mới + xóa pin này trong cùng change, không merge lặng lẽ."
        )


def test_no_task_model_map_generated_block_left_in_repo_code() -> None:
    """Không còn marker generated TASK_MODEL_MAP trong code đang sống
    (docs/history được phép nhắc tên vì là bằng chứng thu dọn)."""
    offenders: list[str] = []
    scan_roots = [REPO / "scp", REPO / "scripts", REPO / "mini-services", REPO / "tools"]
    for root in scan_roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix not in {".py", ".ts", ".js", ".mjs", ".ps1"}:
                continue
            if "node_modules" in path.parts:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            if "BEGIN GENERATED TASK_MODEL_MAP" in text:
                offenders.append(str(path.relative_to(REPO)))
    assert not offenders, (
        f"Marker generated TASK_MODEL_MAP còn sống trong: {offenders} — "
        "codegen bridge phải ở trạng thái eradicated (anti-resurrection)."
    )
