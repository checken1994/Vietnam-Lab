"""In-memory patch application helpers for AutonomousCheckerGate.

[SEC-FIX 2026-10-27] ARC-01 tách file (move-only): autonomous_checker_gate.py
đạt 820 LOC vượt trần 800 (tripwire test_arc01_taskkernel_loc_ceiling). Năm
hàm patch-in-memory (unified diff / SEARCH-REPLACE / fenced block / composite
/ blast radius) chuyển VERBATIM sang module này; autonomous_checker_gate.py
re-export giữ nguyên import path cũ. Không đổi logic, signature hay default.
Logger dùng cùng tên "scp.task_kernel.autonomous_checker_gate" để hành vi log
không đổi."""
from __future__ import annotations

import ast
import difflib
import logging
import re
from typing import Any

logger = logging.getLogger("scp.task_kernel.autonomous_checker_gate")


def apply_unified_diff(original: str, diff_text: str) -> tuple[bool, str, str | None]:
    """Apply unified diff text to original string in memory.

    Returns:
        (success, patched_text, error_message)
    """
    orig_lines = original.splitlines(keepends=True)
    if orig_lines and not orig_lines[-1].endswith(("\n", "\r")):
        orig_lines[-1] = orig_lines[-1] + "\n"

    diff_lines = diff_text.splitlines(keepends=True)
    hunk_regex = re.compile(r"^@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@")

    hunks = []
    current_hunk: dict[str, Any] | None = None

    for line in diff_lines:
        m = hunk_regex.match(line)
        if m:
            if current_hunk is not None:
                hunks.append(current_hunk)
            old_start = int(m.group(1))
            old_count = int(m.group(2)) if m.group(2) is not None else 1
            new_start = int(m.group(3))
            new_count = int(m.group(4)) if m.group(4) is not None else 1
            current_hunk = {
                "old_start": old_start,
                "old_count": old_count,
                "new_start": new_start,
                "new_count": new_count,
                "lines": [],
            }
        elif current_hunk is not None:
            if line.startswith("\\"):
                continue  # ignore "\ No newline at end of file"
            current_hunk["lines"].append(line)

    if current_hunk is not None:
        hunks.append(current_hunk)

    if not hunks:
        return False, original, "No unified diff hunks found in patch"

    # Sort hunks in reverse order of old_start so line indices for earlier hunks don't shift
    hunks.sort(key=lambda h: h["old_start"], reverse=True)

    result_lines = list(orig_lines)
    for hunk in hunks:
        old_start = hunk["old_start"] - 1  # 1-indexed to 0-indexed
        old_count = hunk["old_count"]

        expected_old: list[str] = []
        replacement: list[str] = []
        for hline in hunk["lines"]:
            if not hline:
                continue
            marker = hline[0]
            content = hline[1:]
            if marker == " ":
                expected_old.append(content)
                replacement.append(content)
            elif marker == "-":
                expected_old.append(content)
            elif marker == "+":
                replacement.append(content)
            else:
                expected_old.append(hline)
                replacement.append(hline)

        if old_count == 0:
            slice_end = old_start
        else:
            slice_end = old_start + old_count
            if old_start < 0 or slice_end > len(result_lines):
                return False, original, f"Hunk at line {hunk['old_start']} is out of bounds (file has {len(result_lines)} lines)"

        target_slice = result_lines[old_start:slice_end]
        norm_expected = [l.rstrip("\r\n") for l in expected_old]
        norm_target = [l.rstrip("\r\n") for l in target_slice]

        if norm_expected != norm_target:
            return False, original, f"Hunk at line {hunk['old_start']} context does not match target file"

        result_lines[old_start:slice_end] = replacement

    return True, "".join(result_lines), None


def apply_search_replace(original: str, patch: str) -> tuple[bool, str, str | None]:
    """Apply SEARCH/REPLACE blocks (<<<<<<< SEARCH ... ======= ... >>>>>>>) to original text."""
    search_patterns = (
        re.compile(r"<<<<<<<[ \t]*SEARCH[ \t]*\r?\n(.*?)\r?\n={5,7}[ \t]*\r?\n(?:(.*?)\r?\n)?>>>>>>>[ \t]*(?:REPLACE)?[ \t]*", re.DOTALL),
        re.compile(r"<<<<<<<[ \t]*OLD[ \t]*\r?\n(.*?)\r?\n={5,7}[ \t]*\r?\n(?:(.*?)\r?\n)?>>>>>>>[ \t]*(?:NEW)?[ \t]*", re.DOTALL),
    )
    matches = []
    for pattern in search_patterns:
        matches = list(pattern.finditer(patch))
        if matches:
            break

    if not matches:
        return False, original, "No SEARCH/REPLACE or OLD/NEW blocks found"

    patched = original
    for match in matches:
        old_text = match.group(1)
        new_text = match.group(2) or ""

        if old_text in patched:
            patched = patched.replace(old_text, new_text, 1)
        elif old_text.replace("\r\n", "\n") in patched.replace("\r\n", "\n"):
            patched = patched.replace("\r\n", "\n").replace(
                old_text.replace("\r\n", "\n"),
                new_text.replace("\r\n", "\n"),
                1,
            )
        else:
            return False, original, f"SEARCH block text not found in target: {old_text[:60]!r}"

    return True, patched, None


def apply_fenced_block(original: str, patch: str) -> tuple[bool, str, str | None]:
    """Apply fenced python block (```python ... ```) by replacing the target function or class."""
    fenced = re.search(r"```(?:python)?[ \t]*\r?\n(.*?)\r?\n```", patch, re.DOTALL)
    if not fenced:
        return False, original, "No fenced code block found"

    code_block = fenced.group(1).strip()
    first_line = code_block.split("\n", 1)[0].strip()
    name_match = re.match(r"(?:async\s+)?(?:def|class)\s+(\w+)", first_line)
    if not name_match:
        return False, original, "Fenced block does not start with def or class"

    def_name = name_match.group(1)
    try:
        tree = ast.parse(original)
        target_node = None
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == def_name:
                target_node = node
                break
        if target_node is not None and getattr(target_node, "lineno", None) and getattr(target_node, "end_lineno", None):
            orig_lines = original.splitlines(keepends=True)
            start_idx = target_node.lineno - 1
            end_idx = target_node.end_lineno
            replacement = code_block + "\n"
            patched = "".join(orig_lines[:start_idx]) + replacement + "".join(orig_lines[end_idx:])
            return True, patched, None
    except SyntaxError as exc:
        logger.debug("Failed to parse original code for fenced block replacement: %s", exc)

    return False, original, f"Target definition {def_name} not found in original AST"


def apply_patch_in_memory(
    original: str,
    patch: str,
    target_name: str = "",
    filename: str = "",
) -> tuple[bool, str, str | None]:
    """Composite in-memory patch applier.

    Tries:
    1. SEARCH/REPLACE or OLD/NEW blocks
    2. Unified diff hunks (@@ -... +... @@)
    3. Fenced python code blocks
    4. Full replacement if original is empty
    """
    file_label = filename or target_name or ""
    if patch is None or not isinstance(patch, str) or not patch.strip():
        return False, original, "Patch text must be a non-empty string"

    if "<<<<<<< SEARCH" in patch or "<<<<<<< OLD" in patch:
        ok, res, err = apply_search_replace(original, patch)
        if ok:
            return True, res, None
        return False, original, err

    if re.search(r"^@@\s+-\d+", patch, re.MULTILINE):
        ok, res, err = apply_unified_diff(original, patch)
        if ok:
            return True, res, None
        return False, original, err

    if "```" in patch:
        ok, res, err = apply_fenced_block(original, patch)
        if ok:
            return True, res, None
        return False, original, err

    if not original.strip():
        return True, patch, None

    return False, original, f"No recognized patch format in {file_label or 'patch'}"


def compute_blast_radius(original: str, patched: str) -> int:
    """Compute number of modified (added + removed) lines between original and patched."""
    orig_lines = original.splitlines()
    patch_lines = patched.splitlines()
    diff = list(difflib.unified_diff(orig_lines, patch_lines, lineterm=""))
    return sum(
        1 for line in diff
        if (line.startswith("+") or line.startswith("-"))
        and not line.startswith("+++")
        and not line.startswith("---")
    )
