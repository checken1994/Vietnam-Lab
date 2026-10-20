"""Standalone helper functions for Subsystem Independent Reviewer."""
from __future__ import annotations

import ast
from collections import defaultdict
import logging
from pathlib import Path
import re
from typing import Any

from scp.core.pending_fix_review import ensure_review_guide

logger = logging.getLogger("scp.autofix.subsystem_reviewer_helpers")

SCP_ROOT = Path(__file__).resolve().parents[2]


def apply_patch_to_text(original: str, patch: str, filename: str = "") -> tuple[bool, str, str | None]:
    """Apply search/replace blocks or fenced python blocks to original text in-memory.

    Returns:
        (ok, patched_text, error_message)
    """
    if patch is None or not isinstance(patch, str):
        return False, original, "Patch text must be a non-empty string"

    if not patch.strip():
        return False, original, "Patch text must be a non-empty string"

    search_patterns = (
        re.compile(r"<<<<<<<[ \t]*SEARCH[ \t]*\r?\n(.*?)\r?\n={5,7}[ \t]*\r?\n(?:(.*?)\r?\n)?>>>>>>>[ \t]*(?:REPLACE)?[ \t]*", re.DOTALL),
        re.compile(r"<<<<<<<[ \t]*OLD[ \t]*\r?\n(.*?)\r?\n={5,7}[ \t]*\r?\n(?:(.*?)\r?\n)?>>>>>>>[ \t]*(?:NEW)?[ \t]*", re.DOTALL),
    )
    matches = []
    for pattern in search_patterns:
        matches = list(pattern.finditer(patch))
        if matches:
            break

    patched = original
    changed = False

    if matches:
        from scp.autofix.validate_patch import flexible_replace
        for match in matches:
            old_text = match.group(1)
            new_text = match.group(2) or ""
            if old_text in patched and (new_text or old_text.endswith(("\n", "\r"))):
                patched = patched.replace(old_text, new_text, 1)
                changed = True
            else:
                flex_res = flexible_replace(patched, old_text, new_text)
                if flex_res is not None:
                    patched = flex_res
                    changed = True
                else:
                    return False, original, f"SEARCH block not found in {filename or 'source'}: {old_text[:60]!r}"
    else:
        fenced = re.search(r"```(?:python)?[ \t]*\r?\n(.*?)\r?\n```", patch, re.DOTALL)
        if fenced:
            import textwrap
            code_block = fenced.group(1).strip()
            first_line = code_block.split("\n", 1)[0].strip()
            if first_line.startswith(("def ", "class ", "async def ")):
                name_match = re.match(r"(?:async\s+)?(?:def|class)\s+(\w+)", first_line)
                if name_match:
                    def_name = name_match.group(1)
                    ast_replaced = False
                    try:
                        tree = ast.parse(original)
                        target_node = None
                        for node in ast.walk(tree):
                            if (
                                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                                and node.name == def_name
                            ):
                                target_node = node
                                break
                        if (
                            target_node is not None
                            and getattr(target_node, "lineno", None)
                            and getattr(target_node, "end_lineno", None)
                        ):
                            orig_lines = original.splitlines(keepends=True)
                            start_idx = target_node.lineno - 1
                            end_idx = target_node.end_lineno
                            if 0 <= start_idx < end_idx <= len(orig_lines):
                                orig_first = orig_lines[start_idx]
                                indent = len(orig_first) - len(orig_first.lstrip(" \t"))
                                dedented = textwrap.dedent(code_block).splitlines()
                                indented_lines = [
                                    (" " * indent + ln) if ln.strip() else ""
                                    for ln in dedented
                                ]
                                line_sep = "\r\n" if orig_first.endswith("\r\n") else "\n"
                                replacement = line_sep.join(indented_lines) + line_sep
                                patched = "".join(orig_lines[:start_idx]) + replacement + "".join(orig_lines[end_idx:])
                                changed = True
                                ast_replaced = True
                    except SyntaxError:
                        ast_replaced = False

                    if not ast_replaced:
                        existing_pattern = re.compile(
                            rf"((?:async\s+)?(?:def|class)\s+{re.escape(def_name)}\b[^\n]*\n(?:(?:[ \t]+[^\n]*\n|\s*\n)*))",
                            re.MULTILINE,
                        )
                        existing = existing_pattern.search(original)
                        if existing:
                            orig_first = existing.group(1).split("\n", 1)[0]
                            indent = len(orig_first) - len(orig_first.lstrip())
                            dedented = textwrap.dedent(code_block).splitlines()
                            indented = "\n".join(
                                (" " * indent + line) if line.strip() else ""
                                for line in dedented
                            )
                            patched = original[: existing.start()] + indented + "\n" + original[existing.end() :]
                            changed = True
                        else:
                            return False, original, f"Target function/class {def_name} not found in {filename or 'source'}"
                else:
                    return False, original, "Could not determine function or class name in fenced patch"
            else:
                return False, original, "Fenced block does not start with def or class"
        else:
            return False, original, "No valid SEARCH/REPLACE block or fenced Python block found in patch"

    if not changed or patched == original:
        return False, original, "Patch produced no changes to the target file"

    try:
        ast.parse(patched, filename=filename or "patched_module.py")
    except SyntaxError as exc:
        return False, original, f"SYNTAX_ERROR: Patched {filename or 'source'} has syntax error: {exc}"

    return True, patched, None


def parse_pending_fix_file(pending_file: Path | str, repo_root: Path | None = None) -> tuple[Path | None, str | None]:
    """Parse target file and suggested patch from a pending fix file.

    Returns:
        (target_path_or_None, patch_text_or_None)
    """
    path = Path(pending_file)
    if not path.is_file():
        return None, None

    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return None, None
    except Exception as exc:
        logger.debug("Failed to read pending fix file %s: %s", path, exc)
        return None, None

    lines = text.splitlines()
    if not lines:
        return None, None

    # Header & target parsing
    target_str: str | None = None
    for line in lines:
        tf_match = re.search(r"#\s*Target file:\s*([^\r\n#]+)", line)
        if tf_match:
            target_str = tf_match.group(1).strip()
            break

    if not target_str:
        hdr_match = re.search(r"#\s*Pending fix for\s+([A-Za-z0-9_.\-/]+)", lines[0])
        if hdr_match:
            target_str = hdr_match.group(1).strip()
        else:
            token_match = re.search(r"#\s*Pending fix for\s+(\S+)", lines[0])
            if token_match:
                target_str = token_match.group(1).strip().rstrip("—–-")

    # Suggested fix block parsing
    fix_lines: list[str] = []
    in_fix = False
    for line in lines:
        if in_fix:
            if line.startswith("# "):
                fix_lines.append(line[2:])
            elif line.startswith("#"):
                fix_lines.append(line[1:])
            else:
                fix_lines.append(line)
        elif "# Suggested fix:" in line:
            in_fix = True

    patch = "\n".join(fix_lines).strip() if fix_lines else None
    if not target_str:
        return None, patch

    if repo_root is None:
        repo_root = SCP_ROOT

    target_path = _resolve_target_path(target_str, repo_root)
    return target_path, patch


def parse_proposal_file(proposal_file: Path | str, repo_root: Path | None = None) -> tuple[Path | None, str | None]:
    """Parse a governance proposal markdown file into target path and suggested patch.

    Returns:
        (resolved_target_path, patch_string) or (None, None) if invalid or already resolved.
    """
    path = Path(proposal_file)
    if not path.is_file():
        return None, None

    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return None, None
    except Exception as exc:
        logger.debug("Failed to read proposal file %s: %s", path, exc)
        return None, None

    # Skip proposals already resolved or decided
    if "RESOLVED_BY_MAKER_CHECKER" in text or "Status: `RESOLVED" in text:
        return None, None

    # Target path extraction
    target_str: str | None = None
    target_match = re.search(r"#\s*Meta-repair proposal:\s*([^\r\n#]+)", text, re.IGNORECASE)
    if not target_match:
        target_match = re.search(r"#\s*Proposal for\s+([A-Za-z0-9_.\-/]+)", text, re.IGNORECASE)
    if not target_match:
        target_match = re.search(r"-\s*\*\*Target\*\*:\s*`?([^\r\n`]+)`?", text, re.IGNORECASE)
    if not target_match:
        target_match = re.search(r"#\s*Target file:\s*([^\r\n#]+)", text, re.IGNORECASE)

    if target_match:
        target_str = target_match.group(1).strip()

    # Patch extraction (SEARCH/REPLACE blocks or diff fences)
    patch: str | None = None
    patch_match = re.search(r"##\s*Proposed patch[^\n]*\n+```(?:diff)?\n(.*?)\n```", text, re.DOTALL | re.IGNORECASE)
    if patch_match:
        patch = patch_match.group(1).strip()
    elif "<<<<<<< SEARCH" in text and "=======" in text and ">>>>>>> REPLACE" in text:
        block_match = re.search(r"(<<<<<<< SEARCH.*?>>>>>>> REPLACE)", text, re.DOTALL)
        if block_match:
            patch = block_match.group(1).strip()

    if not target_str or not patch:
        return None, None

    if repo_root is None:
        repo_root = SCP_ROOT

    target_path = _resolve_target_path(target_str, repo_root)
    return target_path, patch


def _resolve_target_path(target_str: str, repo_root: Path) -> Path | None:
    """Resolve a target filename or relative path against repo_root strictly."""
    if not target_str or not isinstance(target_str, str):
        return None

    # Block wildcard patterns (*, ?, [, ])
    if any(ch in target_str for ch in ("*", "?", "[", "]")):
        return None

    # Block parent traversal components
    clean_parts = target_str.replace("\\", "/").split("/")
    if ".." in clean_parts:
        return None

    from scp.autofix.path_guard import ensure_within

    root = repo_root.resolve()

    # If target_str is absolute
    as_path = Path(target_str)
    if as_path.is_absolute():
        safe_path = ensure_within(root, as_path)
        if safe_path is not None and safe_path != root and safe_path.is_file():
            return safe_path
        return None

    # Direct relative path check
    direct = ensure_within(root, repo_root / target_str)
    if direct is not None and direct != root and direct.is_file():
        return direct

    # Search scp/ directory first
    scp_dir = repo_root / "scp"
    if scp_dir.is_dir():
        matches = [p for p in scp_dir.glob(f"**/{target_str}") if p.is_file()]
        valid_matches = [
            m.resolve() for m in matches
            if (safe := ensure_within(root, m)) is not None and safe != root
        ]
        if valid_matches:
            # Prefer matching within autofix if target originated from autofix parts
            for m in valid_matches:
                if "autofix" in str(m):
                    return m
            return valid_matches[0]

    # Search whole repo excluding virtualenvs, git, and data
    for p in repo_root.glob(f"**/{target_str}"):
        s = str(p)
        if ".venv" not in s and ".git" not in s and "data" not in s and "pending_fixes" not in s:
            if p.is_file():
                safe = ensure_within(root, p)
                if safe is not None and safe != root:
                    return safe

    return None


def is_duplicate_pending_fix(
    pending_dir: Path | str,
    target_name: str | Path,
    fix: str,
    repo_root: Path | None = None,
) -> bool:
    """Check if an identical pending fix for target_name already exists in pending_dir."""
    p_dir = Path(pending_dir)
    if not p_dir.is_dir():
        return False

    if repo_root is None:
        repo_root = SCP_ROOT

    norm_fix = fix.replace("\r\n", "\n").strip()
    target_path = Path(target_name)
    target_stem = target_path.stem
    target_str = str(target_name).replace("\\", "/")

    # Resolve target if possible
    resolved_target: Path | None = None
    if target_path.is_absolute():
        resolved_target = target_path.resolve()
    else:
        resolved_candidate = (repo_root / target_path).resolve()
        if resolved_candidate.is_file():
            resolved_target = resolved_candidate

    for candidate in p_dir.glob("*.py"):
        if target_stem not in candidate.name:
            continue
        try:
            cand_target, existing_patch = parse_pending_fix_file(candidate, repo_root=repo_root)
            if existing_patch:
                norm_existing = existing_patch.replace("\r\n", "\n").strip()
                if norm_existing == norm_fix:
                    if resolved_target is not None and cand_target is not None:
                        if resolved_target == cand_target.resolve():
                            return True
                    else:
                        cand_target_str = str(cand_target or "")
                        if target_str in cand_target_str or target_path.name == Path(cand_target_str).name:
                            return True
        except Exception:
            continue
    return False


def prune_pending_fixes(pending_dir: Path | str) -> int:
    """Deduplicate files in pending_fixes/, keeping only the newest file in each duplicate group.

    Returns:
        Number of duplicate files unlinked.
    """
    p_dir = Path(pending_dir)
    if not p_dir.is_dir():
        return 0

    py_files = sorted(p_dir.glob("*.py"))
    if not py_files:
        return 0

    groups: dict[str, list[Path]] = defaultdict(list)
    for f in py_files:
        try:
            content = f.read_text(encoding="utf-8")
            groups[content].append(f)
        except Exception as exc:
            logger.debug("Failed to read %s during prune: %s", f, exc)

    pruned = 0
    for _content, file_list in groups.items():
        if len(file_list) <= 1:
            continue
        # Sort by mtime descending (newest first)
        file_list.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        # Keep newest (index 0), unlink the rest
        for redundant in file_list[1:]:
            try:
                redundant.unlink()
                pruned += 1
            except OSError as err:
                logger.debug("Could not unlink duplicate %s: %s", redundant, err)

    if pruned > 0:
        ensure_review_guide(p_dir)
        logger.info("[SubsystemReviewer] Pruned %d duplicate pending fix files from %s", pruned, p_dir)

    return pruned


__all__ = [
    "SCP_ROOT",
    "_resolve_target_path",
    "apply_patch_to_text",
    "is_duplicate_pending_fix",
    "parse_pending_fix_file",
    "parse_proposal_file",
    "prune_pending_fixes",
]
