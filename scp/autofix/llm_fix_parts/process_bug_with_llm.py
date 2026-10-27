# Auto-extracted from llm_fix.py
from __future__ import annotations

import logging
import os
import time
from pathlib import Path

logger = logging.getLogger(__name__)


def _safe_call_generate_fix_for_bug(
    fn,
    bug,
    feedback: str | None = None,
    previous_patch: str | None = None,
    conversation_history: list[dict] | None = None,
) -> str | None:
    """Safely invoke generate_fix_for_bug with rich arguments, supporting mocks."""
    import inspect
    kwargs = {}
    try:
        sig = inspect.signature(fn)
        params = sig.parameters
        has_kwargs = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())
        if "feedback" in params or has_kwargs:
            kwargs["feedback"] = feedback
        if "previous_patch" in params or has_kwargs:
            kwargs["previous_patch"] = previous_patch
        if "conversation_history" in params or has_kwargs:
            kwargs["conversation_history"] = conversation_history
    except (ValueError, TypeError):
        kwargs = {"feedback": feedback}
    return fn(bug, **kwargs)

def process_bug_with_llm(bug, autofix_engine, allow_llm: bool=True) -> dict:
    """Process a bug: try pattern fix first, then LLM-generated fix.

    [V5.5-FIX] TẠI SAO: was always call LLM — 50 BareExceptPass bugs × 1 LLM call
    = 50 API calls, nhưng rate limit 20/giờ → 30 bugs "LLM fix generation failed"
    → STARTUP-GATE block. BareExceptPass là pattern CỨNG, fix trực tiếp không cần LLM.

    New flow:
      1. If bug has code patch already → use it
      2. If BareExceptPass → use _generate_bare_except_fix (NO LLM)
      3. Otherwise → call LLM (rate-limited)

    Args:
        bug: BugReport
        autofix_engine: AutoFixEngine instance
        allow_llm: When False, only predefined/pattern/deterministic fixes are
            allowed; unresolved bugs are returned as skipped without provider I/O.

    Returns:
        Result dict from engine.process_bug(), with 'fix_source' flag.
    """
    bug_key = f"{getattr(bug, 'file', '')}:{getattr(bug, 'line', '')}:{getattr(bug, 'bug_type', '')}"
    if hasattr(autofix_engine, "is_in_cooldown"):
        try:
            res = autofix_engine.is_in_cooldown(bug_key)
            if isinstance(res, tuple) and len(res) >= 2 and bool(res[0]):
                return {
                    'action': 'skipped',
                    'tier': int(getattr(bug, 'tier', 1)),
                    'reason': str(res[1]),
                    'llm_generated': False,
                    'fix_source': 'cooldown_skip',
                }
        except Exception as _e:
            logger.debug("[llm_fix] cooldown query bypassed: %s", _e)
    elif hasattr(autofix_engine, "_recent_fixes") and isinstance(getattr(autofix_engine, "_recent_fixes", None), dict) and bug_key in autofix_engine._recent_fixes:
        cooldown_limit = float(os.environ.get("SCP_AUTOFIX_FAILURE_COOLDOWN_SEC", "1800"))
        try:
            elapsed = time.time() - autofix_engine._recent_fixes[bug_key]
            if elapsed < cooldown_limit:
                return {
                    'action': 'skipped',
                    'tier': int(getattr(bug, 'tier', 1)),
                    'reason': f'cooldown — bug processed recently ({int(cooldown_limit - elapsed)}s remaining)',
                    'llm_generated': False,
                    'fix_source': 'cooldown_skip',
                }
        except Exception as _e:
            logger.debug("[llm_fix] _recent_fixes query bypassed: %s", _e)

    if bug.suggested_fix and ('<<<<<<< SEARCH' in bug.suggested_fix or '<<<<<<< OLD' in bug.suggested_fix):
        result = autofix_engine.process_bug(bug)
        result['fix_source'] = 'predefined'
        return result
    pattern_fix = _generate_bare_except_fix(bug)  # noqa: F821  # [hygiene-keep] _generate_bare_except_fix injected by llm_fix.py rebind/wire
    if pattern_fix:
        from scp.autofix.classifier import BugReport
        bug_with_fix = BugReport(file=bug.file, line=bug.line, bug_type=bug.bug_type, description=bug.description, suggested_fix=pattern_fix, tier=bug.tier, is_restraint=bug.is_restraint, is_reversible=bug.is_reversible, affects_logic=bug.affects_logic)
        result = autofix_engine.process_bug(bug_with_fix)
        result['fix_source'] = 'pattern_bare_except'
        result['llm_generated'] = False
        return result
    det_fix = _generate_deterministic_fix(bug)  # noqa: F821  # [hygiene-keep] _generate_deterministic_fix injected by llm_fix.py rebind/wire
    if det_fix:
        logger.info(f'[llm_fix] Deterministic fix (no LLM): {bug.bug_type} at {bug.file}:{bug.line}')
        from scp.autofix.classifier import BugReport
        bug_with_fix = BugReport(file=bug.file, line=bug.line, bug_type=bug.bug_type, description=bug.description, suggested_fix=det_fix, tier=bug.tier, is_restraint=bug.is_restraint, is_reversible=bug.is_reversible, affects_logic=bug.affects_logic)
        result = autofix_engine.process_bug(bug_with_fix)
        result['fix_source'] = 'deterministic_no_llm'
        result['llm_generated'] = False
        return result
    try:
        from scp.autofix.deterministic_patches import build_candidate as _build_det_candidate
        from scp.autofix.deterministic_patches import candidate_search_replace as _candidate_search_replace
        _det_candidate = _build_det_candidate(bug)
        if _det_candidate is not None:
            _risk_order = {'low': 0, 'medium': 1, 'high': 2}
            _max_risk = os.environ.get('SCP_AUTOFIX_DETERMINISTIC_MAX_RISK', 'low').strip().lower()
            if _max_risk not in _risk_order:
                _max_risk = 'low'
            if _risk_order.get(_det_candidate.risk, 99) > _risk_order[_max_risk]:
                return {'action': 'candidate', 'reason': f'deterministic candidate risk={_det_candidate.risk} exceeds configured max={_max_risk}; no file write', 'fix_source': f'ast_{_det_candidate.patch_id}', 'llm_generated': False, 'deterministic_patch_risk': _det_candidate.risk}
            logger.info('[llm_fix] AST deterministic candidate (no LLM): %s for %s:%s', _det_candidate.patch_id, bug.file, bug.line)
            from scp.autofix.classifier import BugReport
            _det_bug = BugReport(file=bug.file, line=bug.line, bug_type=bug.bug_type, description=bug.description, suggested_fix=_candidate_search_replace(_det_candidate), tier=bug.tier, is_restraint=bug.is_restraint, is_reversible=bug.is_reversible, affects_logic=bug.affects_logic)
            result = autofix_engine.process_bug(_det_bug)
            result['fix_source'] = f'ast_{_det_candidate.patch_id}'
            result['llm_generated'] = False
            result['deterministic_patch_risk'] = _det_candidate.risk
            return result
    except Exception as _det_err:
        logger.debug('[llm_fix] AST deterministic catalog unavailable: %s', _det_err, exc_info=True)
    if not allow_llm:
        return {'action': 'skipped', 'reason': 'llm_disabled_deterministic_only', 'fix_source': 'deterministic_only', 'llm_generated': False}
    if os.environ.get('SCP_EVOLUTION_ENABLED', '0') == '1':
        try:
            from scp.autofix.evolution import get_evolution_engine
            evo = get_evolution_engine()
            for fixer_name in ('fix_type_mismatch', 'add_null_check', 'add_lock', 'parameterize_sql', 'add_context_manager'):
                fixer_fn = getattr(evo, fixer_name, None)
                if not fixer_fn:
                    continue
                try:
                    pattern_patch = fixer_fn(bug)
                except Exception as e:
                    logger.debug(f'[llm_fix] {fixer_name} raised: {e}', exc_info=True)
                    continue
                if pattern_patch:
                    logger.info(f'[llm_fix] Pattern fix (no LLM): {fixer_name} for {bug.bug_type} at {bug.file}:{bug.line}')
                    from scp.autofix.classifier import BugReport
                    bug_with_fix = BugReport(file=bug.file, line=bug.line, bug_type=bug.bug_type, description=bug.description, suggested_fix=pattern_patch, tier=bug.tier, is_restraint=bug.is_restraint, is_reversible=bug.is_reversible, affects_logic=bug.affects_logic)
                    result = autofix_engine.process_bug(bug_with_fix)
                    result['fix_source'] = f'pattern_{fixer_name}'
                    result['llm_generated'] = False
                    return result
        except Exception as e:
            logger.debug(f'[llm_fix] evolution pattern fixers unavailable: {e}', exc_info=True)
    try:
        import hashlib as _v4_sp_hashlib

        from scp.autofix.speculative_prefixer import DEFAULT_PATTERNS as _v4_sp_default_patterns
        from scp.autofix.speculative_prefixer import lookup as _v4_sp_lookup
        from scp.autofix.speculative_prefixer import prefetch_candidates as _v4_sp_prefetch
        _v4_sp_filepath = Path(bug.file)
        if _v4_sp_filepath.exists():
            try:
                _v4_sp_source = _v4_sp_filepath.read_text(encoding='utf-8', errors='replace')
                _v4_sp_sha = _v4_sp_hashlib.sha256(_v4_sp_source.encode('utf-8', errors='replace')).hexdigest()[:16]
                _v4_sp_bug_type_map = {'BareExceptPass': 'bare_except_pass', 'BareExcept': 'bare_except_broad', 'MutableDefaultArg': 'mutable_default_arg', 'MutableDefaultList': 'mutable_default_arg', 'MutableDefaultDict': 'mutable_default_dict', 'MutableDefaultSet': 'mutable_default_set', 'MissingEncoding': 'missing_encoding_open', 'BareAssert': 'bare_assert'}
                _v4_sp_pattern_name = _v4_sp_bug_type_map.get(bug.bug_type, '')
                if _v4_sp_pattern_name:
                    _v4_sp_candidate = _v4_sp_lookup(_v4_sp_sha, _v4_sp_pattern_name)
                    if _v4_sp_candidate is not None:
                        _v4_sp_lines = _v4_sp_source.splitlines()
                        _v4_sp_ls = max(1, _v4_sp_candidate.line_start)
                        _v4_sp_le = min(len(_v4_sp_lines), _v4_sp_candidate.line_end)
                        if _v4_sp_ls <= _v4_sp_le and _v4_sp_candidate.patched_snippet:
                            _v4_sp_search = '\n'.join(_v4_sp_lines[_v4_sp_ls - 1:_v4_sp_le])
                            _v4_sp_block = f'<<<<<<< SEARCH\n{_v4_sp_search}\n=======\n{_v4_sp_candidate.patched_snippet}\n>>>>>>> REPLACE'
                            logger.info(f'[R10 v4 IMP-21] speculative cache HIT for {bug.bug_type} at {bug.file}:{bug.line} (pattern={_v4_sp_pattern_name}, sha={_v4_sp_sha[:8]}) — skipping LLM call')
                            from scp.autofix.classifier import BugReport
                            bug_with_fix = BugReport(file=bug.file, line=bug.line, bug_type=bug.bug_type, description=bug.description, suggested_fix=_v4_sp_block, tier=bug.tier, is_restraint=bug.is_restraint, is_reversible=bug.is_reversible, affects_logic=bug.affects_logic)
                            result = autofix_engine.process_bug(bug_with_fix)
                            result['fix_source'] = f'speculative_cache_{_v4_sp_pattern_name}'
                            result['llm_generated'] = False
                            result['speculative_cache_hit'] = True
                            return result
                        logger.debug(f'[R10 v4 IMP-21] cache HIT but line range {_v4_sp_ls}-{_v4_sp_le} invalid — fall through to LLM')
                    else:
                        try:
                            _v4_sp_prefetch(bug.file, patterns=_v4_sp_default_patterns, source_override=_v4_sp_source)
                        except Exception as _v4_sp_pref_err:
                            logger.debug(f'[R10 v4 IMP-21] prefetch error (non-fatal): {_v4_sp_pref_err}', exc_info=True)
            except Exception as _v4_sp_lookup_err:
                logger.debug(f'[R10 v4 IMP-21] speculative lookup crash (fail-open): {_v4_sp_lookup_err}', exc_info=True)
    except ImportError as _v4_sp_imp:
        logger.debug(f'[R10 v4 IMP-21] speculative_prefixer unavailable (fail-open): {_v4_sp_imp}')
    except Exception as _v4_sp_err:
        logger.debug(f'[R10 v4 IMP-21] speculative_prefixer wire crash (fail-open): {_v4_sp_err}', exc_info=True)
    max_rounds = int(os.environ.get("SCP_AUTOFIX_MAX_ROUNDS", "3"))
    feedback = None
    last_result = None
    last_llm_fix = None
    conversation_history: list[dict] = []
    for round_idx in range(1, max_rounds + 1):
        is_last = (round_idx == max_rounds)
        llm_fix = _safe_call_generate_fix_for_bug(
            generate_fix_for_bug,
            bug,
            feedback=feedback,
            previous_patch=last_llm_fix,
            conversation_history=conversation_history if round_idx > 1 else None,
        )  # noqa: F821  # [hygiene-keep] generate_fix_for_bug injected by llm_fix.py rebind/wire
        if not llm_fix:
            if round_idx == 1:
                if hasattr(autofix_engine, "record_cooldown"):
                    try:
                        autofix_engine.record_cooldown(bug_key, reason="LLM fix generation failed")
                    except Exception as _e:
                        logger.debug("[llm_fix] record_cooldown bypassed: %s", _e)
                elif hasattr(autofix_engine, "_recent_fixes") and isinstance(getattr(autofix_engine, "_recent_fixes", None), dict):
                    try:
                        autofix_engine._recent_fixes[bug_key] = time.time()
                    except Exception as _e:
                        logger.debug("[llm_fix] _recent_fixes write bypassed: %s", _e)
                return {'action': 'skipped', 'tier': int(getattr(bug, 'tier', 1)), 'reason': 'LLM fix generation failed (failure cooldown recorded)', 'llm_generated': False, 'fix_source': 'llm_failed'}
            break
        last_llm_fix = llm_fix
        from scp.autofix.classifier import BugReport
        bug_with_fix = BugReport(
            file=bug.file,
            line=bug.line,
            bug_type=bug.bug_type,
            description=bug.description,
            suggested_fix=llm_fix,
            tier=bug.tier,
            is_restraint=bug.is_restraint,
            is_reversible=bug.is_reversible,
            affects_logic=bug.affects_logic,
            related_files=getattr(bug, "related_files", []),
            failing_tests=getattr(bug, "failing_tests", []),
            test_output=getattr(bug, "test_output", ""),
            callgraph_context=getattr(bug, "callgraph_context", ""),
        )
        if hasattr(bug, "_initial_prompt"):
            try:
                bug_with_fix._initial_prompt = bug._initial_prompt
            except Exception:
                setattr(bug_with_fix, "_initial_prompt", getattr(bug, "_initial_prompt", None)) if hasattr(bug_with_fix, "__dict__") else None
        bug_with_fix._last_refinement_round = is_last
        bug_with_fix._refinement_round = round_idx
        result = autofix_engine.process_bug(bug_with_fix)
        result['llm_generated'] = True
        result['fix_source'] = 'llm'
        result['refinement_rounds'] = round_idx
        last_result = result
        if result.get('action') == 'fixed':
            try:
                from scp.epistemic.failure_ledger import get_failure_ledger
                # Mark any active anti-pattern for this bug as resolved
                fl = get_failure_ledger()
                for ap in fl.get_anti_patterns_for_module(bug.file):
                    if getattr(bug, 'bug_type', '') in ap.get('anti_pattern_rule', ''):
                        fl.resolve_failure(ap['failure_id'])
            except Exception as _res_err:  # noqa: BLE001
                logger.debug("[llm_fix] failed to resolve failure in failure_ledger: %s", _res_err)
            return result
        reason = result.get('reason', '')
        feedback = f"Round {round_idx} patch was rejected: {reason}"
        if round_idx == 1:
            init_prompt = getattr(bug, "_initial_prompt", None) or getattr(bug_with_fix, "_initial_prompt", None)
            if not init_prompt:
                try:
                    from scp.autofix.llm_fix import _build_fix_prompt
                    filepath = Path(bug.file)
                    ctx = ""
                    if filepath.exists():
                        src_lines = filepath.read_text(encoding="utf-8", errors="replace").splitlines()
                        b_idx = max(0, bug.line - 1)
                        ctx_lines = [f"# line {i + 1}{' >>>' if i == b_idx else '    '}\n{l}" for i, l in enumerate(src_lines)]
                        ctx = "\n".join(ctx_lines)
                    from scp.autofix.llm_fix_parts.generate_fix_for_bug import (
                        _collect_related_files_context,
                        _collect_test_context,
                    )
                    init_prompt = _build_fix_prompt(
                        bug,
                        ctx,
                        related_files_context=_collect_related_files_context(bug),
                        test_context=_collect_test_context(bug),
                        callgraph_context=getattr(bug, "callgraph_context", ""),
                    )
                except Exception:
                    init_prompt = f"Fix {bug.bug_type} in {bug.file}:{bug.line}: {bug.description}"
            conversation_history = [
                {"role": "user", "content": init_prompt},
                {"role": "assistant", "content": llm_fix or ""},
                {
                    "role": "user",
                    "content": (
                        f"Round 1 patch was rejected by safety verification: {reason}\n"
                        f"Previous attempted patch:\n{llm_fix}\n\n"
                        f"Please address this rejection critique, do not remove any safety gates or invariants, "
                        f"and generate a new corrected SEARCH/REPLACE block."
                    ),
                },
            ]
        else:
            conversation_history.append({"role": "assistant", "content": llm_fix or ""})
            conversation_history.append({
                "role": "user",
                "content": (
                    f"Round {round_idx} patch was rejected by safety verification: {reason}\n"
                    f"Previous attempted patch:\n{llm_fix}\n\n"
                    f"Please address this rejection critique, do not remove any safety gates or invariants, "
                    f"and generate a new corrected SEARCH/REPLACE block."
                ),
            })
        logger.info(f'[llm_fix] Round {round_idx} rejected: {reason}. Feeding critique back to LLM for round {round_idx + 1}/{max_rounds}...')
        try:
            from scp.epistemic.failure_ledger import get_failure_ledger
            get_failure_ledger().record_failure(
                failure_type="WHY_REJECTION",
                target_module=bug.file,
                target_symbol=getattr(bug, "description", bug.bug_type),
                verbatim_critique=reason,
                falsified_patch_diff=llm_fix or "",
                anti_pattern_rule=f"Rejected patch for {bug.bug_type}: {reason}",
            )
        except Exception as _fl_err:  # noqa: BLE001
            logger.debug("[llm_fix] failed to record failure in failure_ledger: %s", _fl_err)

    if last_llm_fix and (not last_result or last_result.get('action') != 'fixed'):
        if not (last_result and last_result.get('pending_fix_queued')):
            try:
                from scp.core.code_evolution_agent import CodeEvolutionAgent
                CodeEvolutionAgent._queue_pending_fix(Path(bug.file), last_llm_fix)
            except Exception as _q_err:
                logger.debug("Failed to queue pending fix on round 3 exhaustion: %s", _q_err)

    # Record failure cooldown so false-positive / rejected bugs do not loop endlessly
    _fail_reason = last_result.get('reason', 'refinement rounds exhausted') if last_result else 'no LLM fix generated'
    if hasattr(autofix_engine, "record_cooldown"):
        try:
            autofix_engine.record_cooldown(bug_key, reason=f"Refinement failed ({_fail_reason})")
        except Exception as _e:
            logger.debug("[llm_fix] record_cooldown exhausted bypassed: %s", _e)
    elif hasattr(autofix_engine, "_recent_fixes") and isinstance(getattr(autofix_engine, "_recent_fixes", None), dict):
        try:
            autofix_engine._recent_fixes[bug_key] = time.time()
        except Exception as _e:
            logger.debug("[llm_fix] _recent_fixes write exhausted bypassed: %s", _e)

    return last_result or {'action': 'skipped', 'reason': 'All refinement rounds failed (failure cooldown recorded)'}
