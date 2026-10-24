"""T00 integrity: TASK_MODEL_MAP codegen drift guard (HC-29/HC-56 + HC-55).

Wave K4, 2026-10-24. Block ``TASK_MODEL_MAP`` trong
``mini-services/llm-bridge/core.ts`` phải được SINH RA từ
``TASK_FREE_FALLBACK_MAP`` trong ``scp/llm_gateway/providers.py`` qua
``scripts/generate_task_model_map.py`` — không chấp nhận bản dịch tay trôi
khỏi nguồn Python (DNA #5: ảo giác đồng thuận giữa 2 ngôn ngữ).

Anti-placebo: sửa map Python mà không regenerate, hoặc sửa block TS thủ công
→ ``--check`` render khác file → test văng. Xóa generator/marker → fail-closed.

HC-55 pin: mọi entry TASK_MODEL_MAP phải có explicit FREE default
(``?? TASK_FREE_FALLBACK_DEFAULTS[...]``) và ``resolveModel`` phải log WARNING
``[HC-55]`` khi fallback về OPENROUTER_MODEL — cấm fallback im lặng về model
cũ/trả phí khi thiếu biến môi trường.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
GENERATOR = REPO / "scripts" / "generate_task_model_map.py"
PROVIDERS_PY = REPO / "scp" / "llm_gateway" / "providers.py"
CORE_TS = REPO / "mini-services" / "llm-bridge" / "core.ts"

BEGIN_MARKER = "// BEGIN GENERATED TASK_MODEL_MAP"
END_MARKER = "// END GENERATED TASK_MODEL_MAP"
GENERATED_HEADER = (
    "// [GENERATED — do not edit; source: scp/llm_gateway/providers.py "
    "TASK_FREE_FALLBACK_MAP]"
)


def _assert_exists(path: Path, what: str) -> None:
    assert path.is_file(), (
        f"Thiếu {what}: {path} — codegen drift guard fail-closed, không được "
        "xóa để 'gọn repo' (anti-placebo)."
    )


def _load_generator():
    _assert_exists(GENERATOR, "generator scripts/generate_task_model_map.py")
    spec = importlib.util.spec_from_file_location("generate_task_model_map", GENERATOR)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _core_ts_text() -> str:
    _assert_exists(CORE_TS, "mini-services/llm-bridge/core.ts")
    return CORE_TS.read_text(encoding="utf-8")


def _extracted_python_map() -> dict[str, str]:
    _assert_exists(PROVIDERS_PY, "nguồn Python scp/llm_gateway/providers.py")
    return _load_generator().extract_task_map(PROVIDERS_PY)


def test_generated_block_matches_python_source() -> None:
    """Core anti-drift: block TS hiện tại == render từ providers.py."""
    gen = _load_generator()
    text = _core_ts_text()
    current = gen.current_block(text)
    assert current is not None, (
        f"core.ts mất marker {BEGIN_MARKER!r}/{END_MARKER!r} — block generated "
        "bị xóa (anti-placebo)."
    )
    assert GENERATED_HEADER in current, (
        "Block generated thiếu dòng header [GENERATED — do not edit; source: "
        "scp/llm_gateway/providers.py TASK_FREE_FALLBACK_MAP] — provenance "
        "mất, phải regenerate."
    )
    expected = gen.render_block(_extracted_python_map())
    assert current == expected, (
        "TASK_MODEL_MAP trong core.ts KHÔNG khớp providers.py — chạy "
        "`python scripts/generate_task_model_map.py` để regenerate. Diff:\n"
        + "\n".join(
            __import__("difflib").unified_diff(
                current.splitlines(),
                expected.splitlines(),
                fromfile="core.ts (current)",
                tofile="providers.py (generated)",
                lineterm="",
            )
        )
    )


def test_generator_check_cli_exits_zero() -> None:
    """CLI ``--check`` phải exit 0 (hợp đồng dùng bởi CI/audit)."""
    proc = subprocess.run(
        [sys.executable, str(GENERATOR), "--check"],
        capture_output=True,
        text=True,
        cwd=str(REPO),
        timeout=120,
    )
    assert proc.returncode == 0, (
        f"generator --check exit {proc.returncode}\nstdout: {proc.stdout}\n"
        f"stderr: {proc.stderr}"
    )


def test_generated_map_covers_all_python_tasks_with_defaults() -> None:
    """Mọi task trong providers.py phải có entry env-override + FREE default
    trong block TS (HC-55: thiếu env -> default rõ, không rơi im lặng)."""
    task_map = _extracted_python_map()
    block = _core_ts_text()
    begin = block.find(BEGIN_MARKER)
    end = block.find(END_MARKER)
    assert begin != -1 and end != -1 and end > begin, (
        "core.ts mất generated markers (anti-placebo)."
    )
    generated = block[begin:end]

    bare_entries = re.findall(
        r'"[a-z_]+":\s*process\.env\.OPENROUTER_MODEL_[A-Z_]+,\s*$',
        generated,
        re.MULTILINE,
    )
    assert not bare_entries, (
        f"{len(bare_entries)} entry TASK_MODEL_MAP không có explicit default "
        f"(bare process.env.X): {bare_entries} — vi phạm HC-55 (thiếu env rơi "
        "im lặng về OPENROUTER_MODEL)."
    )
    for task, model in task_map.items():
        assert f'"{task}":' in generated, (
            f"task '{task}' (default {model}) không có trong TASK_MODEL_MAP "
            "generated — map TS lệch nguồn Python (HC-29)."
        )
        assert f'"{model}"' in generated, (
            f"FREE default '{model}' của task '{task}' không có trong "
            "TASK_FREE_FALLBACK_DEFAULTS — default lệch nguồn (HC-55)."
        )


def test_resolve_model_fallback_warns_hc55() -> None:
    """resolveModel phải WARNING [HC-55] trên mọi đường fallback về
    OPENROUTER_MODEL — cấm fallback im lặng về model cũ/trả phí."""
    src = _core_ts_text()
    match = re.search(
        r"function\s+resolveModel\s*\([^)]*\)\s*:\s*string\s*\{", src
    )
    assert match, "core.ts mất hàm resolveModel (regression 4-d-005)."
    body_start = match.end()
    body_end = src.find("\n}", body_start)
    body = src[body_start:body_end]
    assert "[HC-55]" in body, (
        "resolveModel fallback không còn WARNING [HC-55] — HC-55 bị phản áp "
        "(anti-placebo)."
    )
    assert body.count("console.warn") >= 2, (
        "resolveModel phải WARNING cho CẢ 2 đường fallback (no-model và "
        "unknown-model)."
    )
    assert "TASK_MODEL_MAP[" in body, (
        "resolveModel không còn tra TASK_MODEL_MAP (regression 4-d-005)."
    )
