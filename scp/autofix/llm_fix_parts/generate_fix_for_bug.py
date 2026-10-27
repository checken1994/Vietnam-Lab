# Auto-extracted from llm_fix.py
from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger("scp.autofix.llm_fix")


def _collect_related_files_context(bug, max_files: int = 2) -> str:
    """Collect cross-file dependency snippets from callgraph or imports."""
    sections = []
    explicit_files = getattr(bug, "related_files", None) or []
    files_to_check = list(dict.fromkeys(f for f in explicit_files if f and str(f) != str(bug.file)))

    if len(files_to_check) < max_files:
        try:
            import ast
            src_path = Path(bug.file)
            if src_path.exists():
                tree = ast.parse(src_path.read_text(encoding="utf-8", errors="replace"))
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("scp."):
                        rel = node.module.replace(".", "/") + ".py"
                        if Path(rel).exists() and rel != str(bug.file) and rel not in files_to_check:
                            files_to_check.append(rel)
                            if len(files_to_check) >= max_files:
                                break
        except Exception as _cg_err:
            logger.debug("[llm_fix] import discovery skipped: %s", _cg_err)

    for rf in files_to_check[:max_files]:
        rf_path = Path(rf)
        if rf_path.exists():
            try:
                rf_content = rf_path.read_text(encoding="utf-8", errors="replace")
                rf_lines = rf_content.splitlines()[:80]
                snippet = "\n".join(rf_lines)
                sections.append(
                    f"[RELATED DEPENDENCY MODULE: {rf_path.name} ({rf})]\n"
                    f"# Context definitions for cross-file alignment:\n"
                    f"{snippet}"
                )
            except Exception as _read_err:
                logger.debug("[llm_fix] related file read failed: %s", _read_err)

    return "\n\n".join(sections)


def _collect_test_context(bug) -> str:
    """Collect relevant test specifications and failing assertion output."""
    sections = []
    failing_tests = getattr(bug, "failing_tests", None) or []
    test_output = getattr(bug, "test_output", "") or ""
    if failing_tests or test_output:
        sec = "[FAILING TEST DIAGNOSTICS & TRACEBACK]\n"
        if failing_tests:
            sec += f"Failing test(s): {', '.join(failing_tests)}\n"
        if test_output:
            sec += f"Assertion / Traceback Output:\n{test_output}\n"
        sections.append(sec.strip())

    try:
        stem = Path(bug.file).stem
        tests_dir = Path("tests")
        if tests_dir.is_dir():
            matching_tests = list(tests_dir.rglob(f"test_*{stem}*.py"))
            if matching_tests:
                tpath = matching_tests[0]
                tlines = tpath.read_text(encoding="utf-8", errors="replace").splitlines()[:80]
                tsnippet = "\n".join(tlines)
                sections.append(
                    f"[RELEVANT TEST SUITE SPECIFICATION: {tpath.name}]\n"
                    f"# Tests asserting expected behavior for this module:\n"
                    f"{tsnippet}"
                )
    except Exception as _t_err:
        logger.debug("[llm_fix] test discovery skipped: %s", _t_err)

    return "\n\n".join(sections)


def generate_fix_for_bug(
    bug,
    feedback: str | None = None,
    previous_patch: str | None = None,
    conversation_history: list[dict] | None = None,
) -> str | None:
    """Generate a code fix for a BugReport using LLM.

    Args:
        bug: BugReport with file, line, bug_type, description, suggested_fix
        feedback: Optional critique from a previous failed round to refine
        previous_patch: Optional patch from previous round that was rejected
        conversation_history: Optional multi-turn conversation messages

    Returns:
        Search-replace block string (ready for AutoFix._apply_fix), or None.
    """
    has_refinement = bool(feedback or previous_patch or conversation_history)
    if not has_refinement:
        try:
            from scp.autofix.llm_fix_cache import compute_cache_key, get_llm_fix_cache, is_cache_enabled
            if is_cache_enabled():
                _cache = get_llm_fix_cache()
                _cache_key = compute_cache_key(bug)
                _cached = _cache.get(_cache_key)
                if _cached is not None:
                    logger.info(f"[IMP-8] LLM fix cache HIT for {getattr(bug, 'bug_type', '?')} at {getattr(bug, 'file', '?')}:{getattr(bug, 'line', '?')} (key={_cache_key[:8]}) — skipping LLM call")
                    return _cached
        except ImportError:
            logger.debug('[IMP-8] llm_fix_cache module unavailable — no caching')
        except Exception as _cache_err:
            logger.debug(f'[IMP-8] cache lookup failed (non-fatal): {_cache_err}', exc_info=True)

    if not _check_rate_limit():  # noqa: F821  # [hygiene-keep] _check_rate_limit injected by llm_fix.py rebind/wire
        logger.warning('[llm_fix] Rate limit reached — skipping LLM fix generation')
        return None

    filepath = Path(bug.file)
    if not filepath.exists():
        logger.warning(f'[llm_fix] File not found: {filepath}')
        return None

    try:
        source = filepath.read_text(encoding='utf-8', errors='replace')
        lines = source.splitlines()
        bug_line_idx = max(0, bug.line - 1)
        max_lines = int(os.environ.get('SCP_LLM_CONTEXT_LINES', '2000'))
        if len(lines) <= max_lines:
            context_lines = []
            for i, line in enumerate(lines):
                marker = ' >>>' if i == bug_line_idx else '    '
                context_lines.append(f'# line {i + 1}{marker}\n{line}')
            context = '\n'.join(context_lines)
        else:
            start = max(0, bug_line_idx - 250)
            end = min(len(lines), bug_line_idx + 250)
            context_lines = [f'# NOTE: Showing lines {start + 1}-{end} of {len(lines)} (file truncated)']
            for i in range(start, end):
                marker = ' >>>' if i == bug_line_idx else '    '
                context_lines.append(f'# line {i + 1}{marker}\n{lines[i]}')
            context = '\n'.join(context_lines)
    except Exception as e:
        logger.warning(f'[llm_fix] Could not read {filepath}: {e}', exc_info=True)
        return None

    related_files_context = _collect_related_files_context(bug)
    test_context = _collect_test_context(bug)

    if conversation_history:
        llm_response = _call_smart_llm(  # noqa: F821
            bug_type=bug.bug_type,
            max_tokens=4000,
            messages=conversation_history,
        )
    else:
        prompt = _build_fix_prompt(  # noqa: F821  # [hygiene-keep] _build_fix_prompt injected by llm_fix.py rebind/wire
            bug,
            context,
            feedback=feedback,
            previous_patch=previous_patch,
            related_files_context=related_files_context,
            test_context=test_context,
        )
        llm_response = _call_smart_llm(prompt, bug.bug_type, max_tokens=4000)  # noqa: F821  # [hygiene-keep] _call_smart_llm injected by llm_fix.py rebind/wire

    if not llm_response:
        return None

    fix_block = _extract_search_replace_block(llm_response)  # noqa: F821  # [hygiene-keep] _extract_search_replace_block injected by llm_fix.py rebind/wire
    if fix_block:
        logger.info(f'[llm_fix] Generated fix for {filepath.name}:{bug.line}')
        if not has_refinement:
            try:
                from scp.autofix.llm_fix_cache import compute_cache_key, get_llm_fix_cache, is_cache_enabled
                if is_cache_enabled():
                    _cache = get_llm_fix_cache()
                    _cache_key = compute_cache_key(bug)
                    _cache.set(_cache_key, fix_block, bug=bug)
            except ImportError as cache_import_err:
                logger.debug('[IMP-8] llm_fix_cache module unavailable — no caching: %s', cache_import_err, exc_info=True)
            except Exception as _cache_set_err:
                logger.debug(f'[IMP-8] cache set failed (non-fatal): {_cache_set_err}', exc_info=True)
        return fix_block
    else:
        logger.info('[llm_fix] No search-replace block found, returning raw LLM response')
        return llm_response
