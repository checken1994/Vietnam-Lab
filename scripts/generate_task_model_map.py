#!/usr/bin/env python3
"""Generate the TASK_MODEL_MAP block in mini-services/llm-bridge/core.ts.

HC-29/HC-56 (Wave K4, 2026-10-24): TASK_MODEL_MAP từng được dịch thủ công
Python -> TS (kèm comment tự tham chiếu `client.py:99-107` đã sai nguồn —
map thật là `TASK_FREE_FALLBACK_MAP` trong ``scp/llm_gateway/providers.py``,
lớp ``OpenRouterProvider``). Bản dịch tay trôi dần khỏi Python (DNA #5).

Generator này:
  1. AST-extract ``TASK_FREE_FALLBACK_MAP`` từ ``scp/llm_gateway/providers.py``
     (AST thay vì import thật: không cần runtime deps/credentials, bền hơn).
  2. Render block TS giữa hai marker
     ``// BEGIN GENERATED TASK_MODEL_MAP`` / ``// END GENERATED TASK_MODEL_MAP``
     trong ``mini-services/llm-bridge/core.ts`` và thay thế region đó.
  3. ``--check``: dry-run — render và so với file, exit 1 + unified diff nếu
     lệch, KHÔNG ghi file. Drift test
     ``tests/T00_integrity/test_task_model_map_codegen.py`` dùng chính path này.

HC-55: mọi entry sinh ra đều có dạng
``process.env.OPENROUTER_MODEL_<TASK> ?? TASK_FREE_FALLBACK_DEFAULTS["<task>"]``
— thiếu biến môi trường rơi về FREE default đúng chuẩn Python (không còn rơi
im lặng về OPENROUTER_MODEL/model cũ). Cảnh báo WARNING cho fallback không rõ
tên model nằm trong ``resolveModel()`` (hand-written, ngoài block generated).
"""

from __future__ import annotations

import argparse
import ast
import difflib
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PROVIDERS_PY = REPO / "scp" / "llm_gateway" / "providers.py"
CORE_TS = REPO / "mini-services" / "llm-bridge" / "core.ts"

GENERATED_HEADER = (
    "// [GENERATED — do not edit; source: scp/llm_gateway/providers.py "
    "TASK_FREE_FALLBACK_MAP]"
)
BEGIN_MARKER = "// BEGIN GENERATED TASK_MODEL_MAP"
END_MARKER = "// END GENERATED TASK_MODEL_MAP"


def extract_task_map(providers_path: Path = PROVIDERS_PY) -> dict[str, str]:
    """AST-extract OpenRouterProvider.TASK_FREE_FALLBACK_MAP (str -> str)."""
    tree = ast.parse(providers_path.read_text(encoding="utf-8"), str(providers_path))
    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or node.name != "OpenRouterProvider":
            continue
        for stmt in node.body:
            if isinstance(stmt, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "TASK_FREE_FALLBACK_MAP"
                for t in stmt.targets
            ):
                return _literal_str_dict(stmt.value)
            if (
                isinstance(stmt, ast.AnnAssign)
                and isinstance(stmt.target, ast.Name)
                and stmt.target.id == "TASK_FREE_FALLBACK_MAP"
                and stmt.value is not None
            ):
                return _literal_str_dict(stmt.value)
    raise SystemExit(
        f"TASK_FREE_FALLBACK_MAP (str->str dict) không tìm thấy trong lớp "
        f"OpenRouterProvider tại {providers_path} — generator fail-closed, "
        "không đoán map."
    )


def _literal_str_dict(node: ast.expr) -> dict[str, str]:
    if not isinstance(node, ast.Dict):
        raise SystemExit("TASK_FREE_FALLBACK_MAP phải là dict literal (anti-placebo).")
    result: dict[str, str] = {}
    for key_node, value_node in zip(node.keys, node.values):
        if not (
            isinstance(key_node, ast.Constant)
            and isinstance(key_node.value, str)
            and isinstance(value_node, ast.Constant)
            and isinstance(value_node.value, str)
        ):
            raise SystemExit(
                "TASK_FREE_FALLBACK_MAP chỉ chấp nhận key/value chuỗi literal — "
                f"thấy {ast.dump(key_node)}={ast.dump(value_node)}"
            )
        result[key_node.value] = value_node.value
    if "default" not in result:
        raise SystemExit(
            'TASK_FREE_FALLBACK_MAP thiếu key "default" — Python side fail-closed '
            "tại providers.py (map['default'] là fallback cuối), generator từ chối."
        )
    return result


def render_block(task_map: dict[str, str]) -> str:
    """Render toàn bộ region generated (từ header [GENERATED] đến END marker)."""
    keys = sorted(task_map)
    width = max(len(k) for k in keys)
    defaults_lines = [
        f'  "{k}": "{task_map[k]}",' for k in keys
    ]
    map_lines = [
        f'  "{k}":'.ljust(len(k) + 6)
        + f' process.env.OPENROUTER_MODEL_{k.upper()}'
        + f' ?? TASK_FREE_FALLBACK_DEFAULTS["{k}"],'
        for k in keys
    ]
    return "\n".join(
        [
            GENERATED_HEADER,
            "// Regenerate: python scripts/generate_task_model_map.py "
            "(--check = dry-run diff)",
            "// Drift guard: tests/T00_integrity/test_task_model_map_codegen.py "
            "(HC-29/HC-56)",
            f"{BEGIN_MARKER} (tasks={len(task_map)})",
            "const TASK_FREE_FALLBACK_DEFAULTS: Record<string, string> = {",
            *defaults_lines,
            "};",
            "",
            "const TASK_MODEL_MAP: Record<string, string | undefined> = {",
            "  // [HC-55] env override truoc (providers.py: os.environ.get(",
            '  // f"OPENROUTER_MODEL_{task.upper()}", TASK_FREE_FALLBACK_MAP[...])) —',
            "  // thieu env thi ro rang FREE default cung nguon Python, KHONG con",
            "  // roi im lang ve OPENROUTER_MODEL (model cu/tra phi).",
            *map_lines,
            "};",
            END_MARKER,
        ]
    )


def current_block(core_ts_text: str) -> str | None:
    """Trả về region generated hiện có trong core.ts (None nếu thiếu marker)."""
    begin = core_ts_text.find(BEGIN_MARKER)
    end = core_ts_text.find(END_MARKER)
    if begin == -1 or end == -1 or end < begin:
        return None
    # Region bắt đầu từ dòng [GENERATED] ngay trên BEGIN (nếu có).
    header_at = core_ts_text.rfind(GENERATED_HEADER, 0, begin)
    start = header_at if header_at != -1 else begin
    return core_ts_text[start : end + len(END_MARKER)]


def apply_block(core_ts_text: str, block: str) -> str:
    current = current_block(core_ts_text)
    if current is None:
        raise SystemExit(
            f"core.ts thiếu marker {BEGIN_MARKER!r}/{END_MARKER!r} — hãy chèn "
            "block generated thủ công một lần rồi chạy generator (fail-closed, "
            "không tự đo chỗ chèn)."
        )
    return core_ts_text.replace(current, block, 1)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="Dry-run: so block rendered với core.ts, exit 1 nếu lệch, không ghi.",
    )
    args = parser.parse_args(argv)

    task_map = extract_task_map()
    block = render_block(task_map)
    core_ts_text = CORE_TS.read_text(encoding="utf-8")
    current = current_block(core_ts_text)

    if args.check:
        if current is None:
            print(f"FAIL: core.ts thiếu generated markers ({CORE_TS})", file=sys.stderr)
            return 1
        if current == block:
            print(
                f"OK: TASK_MODEL_MAP in core.ts khớp providers.py "
                f"({len(task_map)} tasks, gồm 'default')."
            )
            return 0
        diff = difflib.unified_diff(
            current.splitlines(),
            block.splitlines(),
            fromfile="core.ts (current)",
            tofile="providers.py (generated)",
            lineterm="",
        )
        print("\n".join(diff), file=sys.stderr)
        print("FAIL: TASK_MODEL_MAP drift giữa core.ts và providers.py", file=sys.stderr)
        return 1

    updated = apply_block(core_ts_text, block)
    CORE_TS.write_text(updated, encoding="utf-8", newline="\n")
    print(f"Wrote generated TASK_MODEL_MAP block ({len(task_map)} tasks) -> {CORE_TS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
