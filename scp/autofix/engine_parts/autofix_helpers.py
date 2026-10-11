import logging
import os
import time
from pathlib import Path

logger = logging.getLogger("scp.autofix")


class AutoFixHelpersMixin:
    """Helper phases (_auto_fix_part1 and _auto_fix_part2) for AutoFixMixin."""

    def _auto_fix_part1(self, ctx) -> dict | None:
        from pathlib import Path as PathCls

        filepath = PathCls(ctx.bug.file)
        if not filepath.exists():
            return {"action": "skipped", "tier": int(ctx.bug.tier),
                    "reason": f"file not found: {ctx.bug.file}"}

        # R6: Durable ShadowSnapshot transaction
        from scp.autofix.shadow_snapshot import get_shadow_snapshot_manager
        ctx.shadow_mgr = get_shadow_snapshot_manager(shadow_dir=getattr(self, "data_dir", Path("data")) / "shadow")
        ctx.shadow_tx_id = ctx.shadow_mgr.begin(
            target_files=[filepath],
            bug_id=f"{ctx.bug.file}:{ctx.bug.line}:{ctx.bug.bug_type}",
        )
        ctx.pre_fix_content: str | None = None
        try:
            # Keep original line endings for byte-hash consistency across rollback.
            with filepath.open("r", encoding="utf-8", newline="") as _pre_fix_file:
                ctx.pre_fix_content = _pre_fix_file.read()
        except Exception as _bk_err:
            logger.debug(f" pre-fix backup failed (will skip verify): {_bk_err}", exc_info=True)

        # [OPT-24] XSS pattern fix — deterministic, no LLM.
        if ctx.bug.bug_type == "XSSVulnerability":
            try:
                from scp.autofix.evolution import get_evolution_engine
                _evo = get_evolution_engine(data_dir=str(self.data_dir))
                _xss_result = _evo._apply_xss_pattern_fix(ctx.bug)
                if _xss_result and _xss_result.get("fixed"):
                    try:
                        filepath.write_text(
                            _xss_result["patch"], encoding="utf-8"
                        )
                    except Exception as _write_err:
                        logger.warning(
                            f"[OPT-24] XSS pattern fix write failed: {_write_err}", exc_info=True
                        )
                    else:
                        try:
                            import ast as _ast_verify
                            _ast_verify.parse(
                                filepath.read_text(encoding="utf-8"),
                                filename=str(filepath),
                            )
                        except SyntaxError as _syn_err:
                            logger.warning(
                                f"[OPT-24] XSS pattern fix introduced "
                                f"SyntaxError post-write — ROLLING BACK: {_syn_err}"
                            )
                            if getattr(ctx, "shadow_tx_id", None) and getattr(ctx, "shadow_mgr", None):
                                ctx.shadow_mgr.rollback(ctx.shadow_tx_id, reason=f"XSS_SYNTAX_ERROR: {_syn_err}")
                                ctx.shadow_tx_id = ctx.shadow_mgr.begin(
                                    target_files=[filepath],
                                    bug_id=f"{ctx.bug.file}:{ctx.bug.line}:{ctx.bug.bug_type}",
                                )
                            elif ctx.pre_fix_content is not None:
                                filepath.write_text(
                                    ctx.pre_fix_content, encoding="utf-8", newline=""
                                )
                        else:
                            self._fixes_this_cycle += 1
                            _xss_cache_invalidated = False
                            _xss_cache_error = ""
                            try:
                                from scp.autofix.llm_fix_cache import invalidate_cache_for_file
                                invalidate_cache_for_file(str(filepath))
                                _xss_cache_invalidated = True
                            except Exception as _inv_err:
                                _xss_cache_error = str(_inv_err)[:200]
                                logger.warning(
                                    f" cache invalidate failed after deterministic fix: {_inv_err}", exc_info=True
                                )
                            bug_key = f"{ctx.bug.file}:{ctx.bug.line}:{ctx.bug.bug_type}"
                            self._recent_fixes[bug_key] = time.time()
                            _xss_rollback_registered = False
                            try:
                                from scp.autofix.audit_log import (
                                    compute_hashes as _compute_hashes_4b012,
                                )
                                _xss_post = filepath.read_text(encoding="utf-8")
                                _xss_bh, _xss_ah = _compute_hashes_4b012(
                                    ctx.pre_fix_content, _xss_post,
                                )
                                _xss_rtr = "pass"
                                _xss_token = self.register_fix_for_rollback(
                                    file_path=str(filepath),
                                    before_content=ctx.pre_fix_content or "",
                                    after_content=_xss_post,
                                    patch=_xss_result.get("patch", ""),
                                    bug_id=f"{ctx.bug.file}:{ctx.bug.line}",
                                    bug_type=ctx.bug.bug_type,
                                    tier=int(ctx.bug.tier),
                                    reality_test_result={
                                        "status": "pass",
                                        "method": "ast.parse",
                                        "fix_method": "xss_pattern",
                                    },
                                )
                                _xss_rollback_registered = True
                            except Exception as _xss_hash_err:
                                logger.warning(
                                    f"[4-b-012] XSS hash/rollback registration failed: {_xss_hash_err}", exc_info=True
                                )
                                _xss_bh = _xss_ah = "n/a"
                                _xss_rtr = "skipped"
                                _xss_token = f"backup:{filepath}"
                                _xss_rollback_registered = False
                            if ctx.report or ctx.attack_mode:
                                self._write_audit(
                                    ctx.bug, "fixed", attack_mode=ctx.attack_mode,
                                    before_hash=_xss_bh, after_hash=_xss_ah,
                                    rollback_token=_xss_token,
                                    reality_test_result=_xss_rtr,
                                )
                            else:
                                self._write_audit(
                                    ctx.bug, "fixed_silent", attack_mode=False,
                                    before_hash=_xss_bh, after_hash=_xss_ah,
                                    rollback_token=_xss_token,
                                    reality_test_result=_xss_rtr,
                                )
                            logger.info(
                                f"[OPT-24] XSS pattern fix applied for "
                                f"{ctx.bug.file}:{ctx.bug.line} "
                                f"pattern={_xss_result.get('pattern', '?')} "
                                f"matches={_xss_result.get('matches', 0)} "
                                f"(skipped LLM call — deterministic fix)"
                            )
                            try:
                                if os.environ.get("SCP_EVOLUTION_ENABLED", "0") == "1":
                                    _evo.reflect(ctx.bug, _xss_result["reason"])
                            except Exception as _reflect_err:
                                logger.debug(
                                    f"[V5.7-WHY] reflect on XSS pattern fix "
                                    f"failed (non-fatal): {_reflect_err}", exc_info=True
                                )
                            if getattr(ctx, "shadow_tx_id", None) and getattr(ctx, "shadow_mgr", None):
                                ctx.shadow_mgr.commit(ctx.shadow_tx_id)
                            return {
                                "action": "fixed",
                                "status": "fixed",
                                "tier": int(ctx.bug.tier),
                                "patched": True,
                                "ctx.attack_mode": ctx.attack_mode,
                                "method": "xss_pattern",
                                "pattern": _xss_result.get("pattern", ""),
                                "reason": _xss_result.get("reason", ""),
                                "before_hash": _xss_bh,
                                "after_hash": _xss_ah,
                                "rollback_token": _xss_token,
                                "rollback_registered": _xss_rollback_registered,
                                "reality_test_result": _xss_rtr,
                                "cache_invalidated": _xss_cache_invalidated,
                                "cache_invalidation_error": _xss_cache_error,
                            }
            except Exception as _xss_pattern_err:
                logger.debug(
                    f"[OPT-24] XSS pattern fix dispatch failed (non-fatal, "
                    f"fall through to LLM): {_xss_pattern_err}", exc_info=True
                )

        # [OPT-26/27/28] Validate patch + diagnose + record to monitor before _apply_fix.
        ctx._autofix_bug_id = f"{ctx.bug.file}:{ctx.bug.line}"
        ctx._autofix_provider = "predefined"
        try:
            from scp.autofix.llm_fix import select_provider_for_bug as _spfb
            ctx._autofix_provider = _spfb(ctx.bug.bug_type)
        except Exception as e:
            logger.warning(f"Silent except: {e}", exc_info=True)

        _autofix_validation_skipped = False  # noqa: F841
        try:
            import re as _autofix_re
            _sr_pat = _autofix_re.compile(
                r"<<<<<<<\s*SEARCH\s*\n(.*?)\n={5,7}\s*\n(.*?)\n>>>>>>>\s*(?:REPLACE)?\s*",
                _autofix_re.DOTALL,
            )
            _old_pat = _autofix_re.compile(
                r"<<<<<<<\s*OLD\s*\n(.*?)\n={5,7}\s*\n(.*?)\n>>>>>>>\s*(?:NEW)?\s*",
                _autofix_re.DOTALL,
            )
            ctx.pairs: list[tuple[str, str]] = []
            if ctx.bug.suggested_fix:
                for _m in _sr_pat.finditer(ctx.bug.suggested_fix):
                    ctx.pairs.append((_m.group(1), _m.group(2)))
                if not ctx.pairs:
                    for _m in _old_pat.finditer(ctx.bug.suggested_fix):
                        ctx.pairs.append((_m.group(1), _m.group(2)))

            if ctx.pairs:
                from scp.autofix.diagnostic import diagnose_fix_failure as _diagnose
                from scp.autofix.monitor import FixAttempt as _FixAttempt
                from scp.autofix.monitor import get_monitor as _get_monitor
                from scp.autofix.validate_patch import validate_patch as _validate_patch

                _validation_failed = False
                _validation_reason = ""
                for _search, _replace in ctx.pairs:
                    _vresult = _validate_patch(str(filepath), _search, _replace)
                    if not _vresult.valid:
                        _validation_failed = True
                        _validation_reason = _vresult.reason
                        break

                if _validation_failed:
                    logger.warning(
                        f"[AutoFix] [OPT-26] Patch validation FAILED for "
                        f"{ctx.bug.file}:{ctx.bug.line}: {_validation_reason}"
                    )
                    ctx._diag = _diagnose(
                        bug_id=ctx._autofix_bug_id,
                        bug_type=ctx.bug.bug_type,
                        llm_output=ctx.bug.suggested_fix,
                        patch_parsed={"search": ctx.pairs[0][0], "replace": ctx.pairs[0][1]},
                        validation_result={"valid": False, "reason": _validation_reason},
                        apply_result="failed",
                    )
                    _get_monitor().record(_FixAttempt(
                        bug_id=ctx._autofix_bug_id,
                        bug_type=ctx.bug.bug_type,
                        provider=ctx._autofix_provider,
                        diagnosis=ctx._diag.diagnosis,
                        success=False,
                    ))
                    return {
                        "action": "skipped",
                        "tier": int(ctx.bug.tier),
                        "reason": f"validation failed: {_validation_reason}",
                        "patched": False,
                    }
            else:
                _autofix_validation_skipped = True  # noqa: F841
        except Exception as _val_err:
            logger.debug(f"[AutoFix] [OPT-26] validation harness error (non-blocking): {_val_err}", exc_info=True)
            _autofix_validation_skipped = True  # noqa: F841

        # [R9 v4 WIRE — IMP-14 + IMP-23 shared simulation]
        _v4_pairs = getattr(ctx, "pairs", []) or []
        ctx.sim_patched: str | None = None
        if ctx.pre_fix_content is not None and _v4_pairs:
            try:
                from scp.autofix.validate_patch import flexible_replace
                _v4_sim = ctx.pre_fix_content
                _v4_applied_any = False
                for _v4_s, _v4_r in _v4_pairs:
                    _res = flexible_replace(_v4_sim, _v4_s, _v4_r)
                    if _res is not None:
                        _v4_sim = _res
                        _v4_applied_any = True
                if _v4_applied_any and _v4_sim != ctx.pre_fix_content:
                    ctx.sim_patched = _v4_sim
            except Exception as _v4_sim_err:
                logger.debug(
                    f"[R9 v4 WIRE] patched_source simulation failed "
                    f"(fail-open — IMP-14 + IMP-23 skip): {_v4_sim_err}", exc_info=True
                )

        # [R9 v4 WIRE — IMP-14] Confidence Ranker (fail-open).
        try:
            from scp.autofix.confidence_ranker import (
                best_fix as _v4_rank_best,
            )
            from scp.autofix.confidence_ranker import (
                make_fix as _v4_make_fix,
            )
            if ctx.sim_patched is not None:
                _v4_lines_changed = sum(
                    max(0, len(_r.splitlines()) - len(_s.splitlines()) + 1)
                    for _s, _r in _v4_pairs
                ) or sum(len(_r.splitlines()) for _, _r in _v4_pairs)
                _v4_candidate = _v4_make_fix(
                    fix_id=ctx._autofix_bug_id,
                    patch=ctx.bug.suggested_fix or "",
                    patched_source=ctx.sim_patched,
                    source="llm",
                    bug_type=ctx.bug.bug_type,
                    bug_file=ctx.bug.file,
                    bug_line=int(ctx.bug.line or 0),
                    lines_changed=_v4_lines_changed,
                    reality_test_result=None,
                )
                _v4_ranked = _v4_rank_best([_v4_candidate], bug_type=ctx.bug.bug_type)
                if _v4_ranked is None or _v4_ranked.disposition == "discard":
                    logger.info(
                        f"[R9 v4 IMP-14] confidence ranker DISCARDED "
                        f"fix for {ctx.bug.file}:{ctx.bug.line} "
                        f"(confidence={_v4_candidate.confidence:.3f} "
                        f"disposition={_v4_candidate.disposition})"
                    )
                    try:
                        from scp.autofix.diagnostic import diagnose_fix_failure as _diagnose
                        from scp.autofix.monitor import FixAttempt as _FixAttempt
                        from scp.autofix.monitor import get_monitor as _get_monitor
                        ctx._diag = _diagnose(
                            bug_id=ctx._autofix_bug_id,
                            bug_type=ctx.bug.bug_type,
                            llm_output=ctx.bug.suggested_fix,
                            patch_parsed={"search": "(ranked)", "replace": "(ranked)"},
                            validation_result={"valid": True, "reason": "pre-rank OK"},
                            apply_result="discarded_by_ranker",
                        )
                        _get_monitor().record(_FixAttempt(
                            bug_id=ctx._autofix_bug_id,
                            bug_type=ctx.bug.bug_type,
                            provider=ctx._autofix_provider,
                            diagnosis=ctx._diag.diagnosis,
                            success=False,
                        ))
                    except Exception as e:
                        logger.debug(f"Silent except: {e}", exc_info=True)
                    return {
                        "action": "skipped",
                        "tier": int(ctx.bug.tier),
                        "reason": (
                            f"confidence_ranker DISCARD "
                            f"(confidence={_v4_candidate.confidence:.3f})"
                        ),
                        "patched": False,
                        "confidence": _v4_candidate.confidence,
                        "disposition": "discard",
                    }
                logger.info(
                    f"[R9 v4 IMP-14] fix ranked: "
                    f"confidence={_v4_ranked.confidence:.3f} "
                    f"disposition={_v4_ranked.disposition} "
                    f"for {ctx.bug.file}:{ctx.bug.line}"
                )
        except ImportError as _v4_cr_imp:
            logger.debug(
                f"[R9 v4 IMP-14] confidence_ranker unavailable "
                f"(fail-open — apply without scoring): {_v4_cr_imp}"
            )
        except Exception as _v4_cr_err:
            logger.debug(
                f"[R9 v4 IMP-14] confidence_ranker crash "
                f"(fail-open — apply without scoring): {_v4_cr_err}", exc_info=True
            )

    def _auto_fix_part2(self, ctx) -> dict | None:
        # [R10 v4 WIRE — IMP-16 + IMP-20 PAIRED] Blast-Radius + Type-Flow Verify.
        try:
            from scp.autofix.runner_phases.blast_radius import (
                blast_radius_summary as _v4_blast_summary,
            )
            from scp.autofix.runner_phases.blast_radius import (
                compute_blast_radius as _v4_blast,
            )
            from scp.autofix.runner_phases.blast_radius import (
                should_escalate_tier as _v4_should_escalate,
            )
            from scp.autofix.runner_phases.blast_radius import (
                should_require_dry_run as _v4_should_dry_run,
            )
            _v4_target_func = (
                getattr(ctx.bug, "function_name", "")
                or getattr(ctx.bug, "method_name", "")
                or ""
            )
            if not _v4_target_func and ctx.bug.suggested_fix:
                import re as _v4_re_mod
                _v4_def_m = _v4_re_mod.search(
                    r"def\s+(\w+)\s*\(", ctx.bug.suggested_fix,
                )
                if _v4_def_m:
                    _v4_target_func = _v4_def_m.group(1)
            if _v4_target_func:
                _v4_blast_result = _v4_blast(
                    target_file=ctx.bug.file or "",
                    target_function=_v4_target_func,
                    scp_root=None,
                    scan_tests=False,
                )
                _v4_blast_sum = _v4_blast_summary(_v4_blast_result)
                logger.info(
                    f"[R10 v4 IMP-16] blast_radius for {_v4_target_func}: "
                    f"callers={_v4_blast_sum['caller_count']} "
                    f"risk={_v4_blast_sum['risk_level']} "
                    f"bounded={_v4_blast_sum['bounded']}"
                )

                try:
                    _v4_orig_tier = int(getattr(ctx.bug, "tier", 1) or 1)
                except (TypeError, ValueError) as tier_err:
                    logger.debug("[AutoFix] bug.tier not numeric, defaulting to 1: %s", tier_err, exc_info=True)
                    _v4_orig_tier = 1
                _v4_escalated_tier = _v4_should_escalate(
                    _v4_blast_result, _v4_orig_tier,
                )
                if _v4_escalated_tier > _v4_orig_tier:
                    logger.warning(
                        f" blast_radius policy escalated "
                        f"{ctx.bug.file}:{ctx.bug.line} from Tier "
                        f"{_v4_orig_tier} to Tier {_v4_escalated_tier} "
                        f"(risk={_v4_blast_sum['risk_level']}, "
                        f"callers={_v4_blast_sum['caller_count']})"
                    )
                    if hasattr(ctx.bug, "tier"):
                        try:
                            ctx.bug.tier = _v4_escalated_tier  # type: ignore[assignment]
                        except Exception:
                            logger.debug('[AUTOFIX] tier assignment failed; retaining original tier', exc_info=True)

                if _v4_should_dry_run(_v4_blast_result):
                    _dry_run_snapshot = None
                    try:
                        _dry_run_preview_fn = getattr(
                            self, "preview_fix_dry_run", None,
                        )
                        if (_dry_run_preview_fn is not None
                                and ctx.pre_fix_content is not None
                                and ctx.bug.suggested_fix):
                            import re as _v4_dr_re
                            _v4_dr_sim = ctx.pre_fix_content
                            for _old, _new in _v4_dr_re.findall(
                                r'<<<<<<< SEARCH\n(.*?)\n=======\n(.*?)\n>>>>>>>',
                                ctx.bug.suggested_fix, _v4_dr_re.DOTALL,
                            ):
                                _v4_dr_sim = _v4_dr_sim.replace(_old, _new, 1)
                            if _v4_dr_sim != ctx.pre_fix_content:
                                _dr_out = _dry_run_preview_fn(
                                    ctx.bug.file, _v4_dr_sim,
                                )
                                _dry_run_snapshot = _dr_out.get("snapshot_path")
                    except Exception as _dr_err:  # noqa: BLE001
                        logger.debug(
                            f" dry-run preview failed (fail-open): {_dr_err}"
                        )
                    logger.warning(
                        f" HIGH/CRITICAL blast radius for "
                        f"{_v4_target_func} (risk={_v4_blast_sum['risk_level']}, "
                        f"callers={_v4_blast_sum['caller_count']}) — "
                        f"dry-run{' snapshot='+str(_dry_run_snapshot) if _dry_run_snapshot else ' unavailable (fail-open)'}"
                    )

                try:
                    from scp.autofix.type_flow_verifier import (
                        Signature as _V4_TF_Sig,
                    )
                    from scp.autofix.type_flow_verifier import (
                        verify_type_flow as _v4_tflow,
                    )
                    if _v4_blast_sum["caller_count"] > 0:
                        import ast as _ast_tf

                        def _extract_sig(source_text: str, func_name: str) -> _V4_TF_Sig:
                            """Extract function signature from source via AST."""
                            try:
                                tree = _ast_tf.parse(source_text)
                                for node in _ast_tf.walk(tree):
                                    if isinstance(node, (_ast_tf.FunctionDef, _ast_tf.AsyncFunctionDef)) and node.name == func_name:
                                        args = [a.arg for a in node.args.args if hasattr(a, 'arg')]
                                        returns = ""
                                        if node.returns:
                                            try:
                                                returns = _ast_tf.unparse(node.returns)
                                            except Exception as unp_err:
                                                logger.debug("[AUTOFIX] return-annotation unparse failed, using 'Any': %s", unp_err, exc_info=True)
                                                returns = "Any"
                                        return _V4_TF_Sig(args=args, returns=returns)
                            except Exception:
                                logger.debug('[AUTOFIX] signature scan failed; using empty signature', exc_info=True)
                            return _V4_TF_Sig(args=[], returns="")

                        _v4_orig_sig = _extract_sig(ctx.pre_fix_content or "", _v4_target_func)
                        _v4_patched_src = ""
                        try:
                            _v4_patched_src = Path(ctx.bug.file).read_text(encoding="utf-8")
                        except Exception:
                            logger.debug('[AUTOFIX] patched source read failed; using empty source', exc_info=True)
                        _v4_new_sig = _extract_sig(_v4_patched_src, _v4_target_func)

                        _v4_tflow_result = _v4_tflow(
                            target_file=ctx.bug.file or "",
                            target_function=_v4_target_func,
                            orig_signature=_v4_orig_sig,
                            new_signature=_v4_new_sig,
                            scp_root=str(
                                Path(__file__).resolve().parent.parent
                            ),
                        )
                        logger.info(
                            f" type_flow for "
                            f"{_v4_target_func}: compatible="
                            f"{_v4_tflow_result.compatible} "
                            f"callers={_v4_tflow_result.caller_count} "
                            f"orig_args={_v4_orig_sig.args} "
                            f"new_args={_v4_new_sig.args} "
                            f"reason={_v4_tflow_result.reason[:80]}"
                        )
                        if (
                            not _v4_tflow_result.compatible
                            and _v4_blast_sum["risk_level"] in ("HIGH", "CRITICAL")
                        ):
                            logger.warning(
                                f" TYPE-FLOW BREAKAGE + "
                                f"HIGH risk — escalating {ctx.bug.file}:"
                                f"{ctx.bug.line} to review (Tier 3)"
                            )
                            return {
                                "action": "skipped",
                                "tier": 3,
                                "reason": (
                                    f"type_flow_verifier: "
                                    f"{len(_v4_tflow_result.incompatible_sites)} "
                                    f"breaking caller(s) + risk="
                                    f"{_v4_blast_sum['risk_level']}"
                                ),
                                "patched": False,
                                "blast_radius": _v4_blast_sum,
                                "type_flow_breaking": len(
                                    _v4_tflow_result.incompatible_sites
                                ),
                            }
                except ImportError as _v4_tf_imp:
                    logger.debug(
                        f"[R10 v4 IMP-20] type_flow_verifier unavailable "
                        f"(fail-open): {_v4_tf_imp}"
                    )
                except Exception as _v4_tf_err:
                    logger.debug(
                        f"[R10 v4 IMP-20] type_flow_verifier crash "
                        f"(fail-open): {_v4_tf_err}", exc_info=True
                    )
        except ImportError as _v4_br_imp:
            logger.debug(
                f"[R10 v4 IMP-16] blast_radius unavailable (fail-open): {_v4_br_imp}"
            )
        except Exception as _v4_br_err:
            logger.debug(
                f"[R10 v4 IMP-16] blast_radius crash (fail-open): {_v4_br_err}", exc_info=True
            )

        # [R9 v4 WIRE — IMP-23] Shadow-Apply + Canary Compare.
        ctx.v4_shadow_result = None
        try:
            from scp.autofix.runner_phases.shadow_canary import (
                ShadowFix as _V4_ShadowFix,
            )
            from scp.autofix.runner_phases.shadow_canary import (
                default_canary_suite as _v4_default_canary,
            )
            from scp.autofix.runner_phases.shadow_canary import (
                shadow_apply_and_compare as _v4_shadow_compare,
            )
            if ctx.sim_patched is not None and ctx.pre_fix_content is not None:
                _v4_shadow_fix = _V4_ShadowFix(
                    original_source=ctx.pre_fix_content,
                    patched_source=ctx.sim_patched,
                    fix_id=ctx._autofix_bug_id,
                )
                _v4_shadow_result = _v4_shadow_compare(
                    target_file=ctx.bug.file,
                    fix=_v4_shadow_fix,
                    canary_suite=_v4_default_canary(),
                )
                ctx.v4_shadow_result = _v4_shadow_result
                if not _v4_shadow_result.passed:
                    logger.warning(
                        f"[R9 v4 IMP-23] SHADOW CANARY FAILED for "
                        f"{ctx.bug.file}:{ctx.bug.line}: "
                        f"{_v4_shadow_result.reason[:120]} "
                        f"(tests_run={_v4_shadow_result.tests_run} "
                        f"diffs={len(_v4_shadow_result.diffs)})"
                    )
                    try:
                        from scp.autofix.diagnostic import diagnose_fix_failure as _diagnose
                        from scp.autofix.monitor import FixAttempt as _FixAttempt
                        from scp.autofix.monitor import get_monitor as _get_monitor
                        ctx._diag = _diagnose(
                            bug_id=ctx._autofix_bug_id,
                            bug_type=ctx.bug.bug_type,
                            llm_output=ctx.bug.suggested_fix,
                            patch_parsed={"search": "(shadowed)", "replace": "(shadowed)"},
                            validation_result={"valid": True, "reason": "pre-shadow OK"},
                            apply_result="shadow_canary_failed",
                        )
                        _get_monitor().record(_FixAttempt(
                            bug_id=ctx._autofix_bug_id,
                            bug_type=ctx.bug.bug_type,
                            provider=ctx._autofix_provider,
                            diagnosis=ctx._diag.diagnosis,
                            success=False,
                        ))
                    except Exception as e:
                        logger.debug(f"Silent except: {e}", exc_info=True)
                    return {
                        "action": "skipped",
                        "tier": int(ctx.bug.tier),
                        "reason": (
                            f"shadow_canary FAIL: "
                            f"{_v4_shadow_result.reason[:160]}"
                        ),
                        "patched": False,
                        "shadow_canary_passed": False,
                        "shadow_diffs": list(_v4_shadow_result.diffs[:5]),
                        "shadow_tests_run": _v4_shadow_result.tests_run,
                    }
                logger.info(
                    f"[R9 v4 IMP-23] shadow canary OK for "
                    f"{ctx.bug.file}:{ctx.bug.line} "
                    f"(tests_run={_v4_shadow_result.tests_run} "
                    f"flagged={_v4_shadow_result.flagged_for_review})"
                )
        except ImportError as _v4_sc_imp:
            logger.warning(
                "[R9 v4 IMP-23] shadow_canary unavailable; blocking unverifiable patch: %s",
                type(_v4_sc_imp).__name__,
            )
            return {
                "action": "skipped",
                "tier": int(ctx.bug.tier),
                "reason": "shadow_canary unavailable; fix is UNVERIFIED",
                "patched": False,
                "shadow_canary_unverified": True,
            }
        except Exception as _v4_sc_err:
            logger.warning(
                "[R9 v4 IMP-23] shadow_canary failed; blocking unverifiable patch: %s",
                type(_v4_sc_err).__name__,
                exc_info=True,
            )
            return {
                "action": "skipped",
                "tier": int(ctx.bug.tier),
                "reason": "shadow_canary failed; fix is UNVERIFIED",
                "patched": False,
                "shadow_canary_unverified": True,
            }
