import logging
import os
import time
from pathlib import Path

from scp.autofix.classifier import BugReport
from scp.autofix.engine_parts.autofix_helpers import AutoFixHelpersMixin

logger = logging.getLogger("scp.autofix")

# Single source of truth for the per-cycle AutoFix rate limit. The mixin is the
# only code user; engine.py re-exports this name for back-compat.
MAX_FIXES_PER_CYCLE = 200


class AutoFixMixin:

    def __init__(self, engine=None, *args, **kwargs) -> None:
        self.engine = engine
        self._helpers = AutoFixHelpersMixin()

    def _auto_fix_part1(self, ctx, engine=None) -> dict | None:
        target = engine or self.engine or self
        return AutoFixHelpersMixin._auto_fix_part1(target, ctx)

    def _auto_fix_part2(self, ctx, engine=None) -> dict | None:
        target = engine or self.engine or self
        return AutoFixHelpersMixin._auto_fix_part2(target, ctx)

    def _auto_fix(self, bug: BugReport, report: bool, attack_mode: bool = False) -> dict:
        from pathlib import Path

        from scp.autofix.engine_parts.context import FixContext
        ctx = FixContext(
            bug=bug,
            filepath=Path(bug.file),
            report=report,
            attack_mode=attack_mode
        )

        res = self._auto_fix_gates(ctx)
        if res is not None:
            return res

        try:
            res = self._auto_fix_part1(ctx)
            if res is not None:
                if res.get("action") != "fixed" and getattr(ctx, "shadow_tx_id", None) and getattr(ctx, "shadow_mgr", None):
                    if (ctx.shadow_mgr.active_dir / ctx.shadow_tx_id).is_dir():
                        try:
                            ctx.shadow_mgr.rollback(ctx.shadow_tx_id, reason=f"part1 non-fixed: {res.get('reason')}")
                        except Exception as rb_err:
                            logger.warning("[AutoFix] shadow rollback failed after part1 non-fixed — tx %s left in active dir (GC will reclaim): %s", ctx.shadow_tx_id, rb_err, exc_info=True)
                return res

            res = self._auto_fix_part2(ctx)
            if res is not None:
                if res.get("action") != "fixed" and getattr(ctx, "shadow_tx_id", None) and getattr(ctx, "shadow_mgr", None):
                    if (ctx.shadow_mgr.active_dir / ctx.shadow_tx_id).is_dir():
                        try:
                            ctx.shadow_mgr.rollback(ctx.shadow_tx_id, reason=f"part2 non-fixed: {res.get('reason')}")
                        except Exception as rb_err:
                            logger.warning("[AutoFix] shadow rollback failed after part2 non-fixed — tx %s left in active dir (GC will reclaim): %s", ctx.shadow_tx_id, rb_err, exc_info=True)
                return res

            res = self._auto_fix_part3(ctx)
            # [SANDBOX-SKIP] Surface visible realtime skip when delegated to shadow canary + post_fix_verify.
            if res is not None and getattr(ctx, "realtime_skipped", False):
                res["realtime_skipped"] = True
            if res is not None:
                if res.get("action") != "fixed" and getattr(ctx, "shadow_tx_id", None) and getattr(ctx, "shadow_mgr", None):
                    if (ctx.shadow_mgr.active_dir / ctx.shadow_tx_id).is_dir():
                        try:
                            ctx.shadow_mgr.rollback(ctx.shadow_tx_id, reason=f"part3 non-fixed: {res.get('reason')}")
                        except Exception as rb_err:
                            logger.warning("[AutoFix] shadow rollback failed after part3 non-fixed — tx %s left in active dir (GC will reclaim): %s", ctx.shadow_tx_id, rb_err, exc_info=True)
                return res

        except Exception as e:
            logger.error(f"[AutoFix] Critical error during auto-fix: {e}", exc_info=True)
            if getattr(ctx, "shadow_tx_id", None) and getattr(ctx, "shadow_mgr", None):
                try:
                    ctx.shadow_mgr.rollback(ctx.shadow_tx_id, reason=f"auto-fix crash: {e}")
                except Exception as rb_err:
                    logger.critical("[AutoFix] DOUBLE FAILURE: auto-fix crashed AND shadow rollback failed — tx %s left in active dir, manual cleanup may be required: %s", ctx.shadow_tx_id, rb_err, exc_info=True)
            return {
                "action": "skipped",
                "tier": int(ctx.bug.tier),
                "reason": f"auto-fix crash: {e}",
            }

        if getattr(ctx, "shadow_tx_id", None) and getattr(ctx, "shadow_mgr", None):
            if (ctx.shadow_mgr.active_dir / ctx.shadow_tx_id).is_dir():
                try:
                    ctx.shadow_mgr.rollback(ctx.shadow_tx_id, reason="fell through")
                except Exception as rb_err:
                    logger.warning("[AutoFix] shadow rollback failed on fell-through path — tx %s left in active dir (GC will reclaim): %s", ctx.shadow_tx_id, rb_err, exc_info=True)

        return {"action": "skipped", "reason": "fell through"}

    def _auto_fix_gates(self, ctx) -> dict | None:
        """Apply the fix autonomously. Uses code_evolution_agent._apply_fix."""
        # [GOV-IMMUTABLE-RUNTIME] In Government/Enterprise immutable production mode,
        # live self-modification of source files on disk is strictly blocked (fail-closed).
        if os.environ.get("SCP_IMMUTABLE_RUNTIME", "0").strip().lower() in {"1", "true", "yes"}:
            logger.warning("[GOV-IMMUTABLE-RUNTIME] Blocked live disk mutation in immutable runtime mode: %s", getattr(ctx.bug, "file", ""))
            return {"status": "blocked", "reason": "immutable_runtime_enforced", "action": "immutable_runtime_blocked"}
        # [G2-FIX PERM-03] Protected path check — SCP cannot modify its own permission/security source
        from scp.autofix.runner_phases.ast_scan import _is_protected_path
        ctx.filepath = getattr(ctx.bug, 'file', '') or ''
        if _is_protected_path(ctx.filepath):
            logger.error(f"[G2-FIX] PROTECTED_PATH_BLOCKED: {ctx.filepath} — SCP cannot modify its own permission/security source")
            return {"status": "blocked", "reason": f"protected path: {ctx.filepath}", "action": "protected_path_blocked"}

        # [OPT-14 / Gà §12] Capability gate
        try:
            from scp.meta.capability_levels import get_capability_manager
            _cap = get_capability_manager()
            _required_action = "apply_narrow" if ctx.attack_mode else (
                "apply_test" if ctx.report else "apply_sandbox"
            )
            if not _cap.can_do(_required_action):
                logger.warning(
                    f"[AutoFix] Gà §12 — capability denied: current "
                    f"{_cap.get_current_level().name} does not allow "
                    f"'{_required_action}'. Set SCP_CAPABILITY_LEVEL env "
                    f"var to escalate (requires human approval via "
                    f"CapabilityManager.request_escalation)."
                )
                return {
                    "action": "skipped",
                    "tier": int(ctx.bug.tier),
                    "reason": f"capability denied — current level "
                              f"{_cap.get_current_level().name} lacks "
                              f"'{_required_action}' (Gà §12)",
                }
        except Exception as _cap_err:
            # [G2-FIX PERM-04] Fail-CLOSED: if CapabilityManager crashes, BLOCK auto-fix
            logger.error(f"[AutoFix] CapabilityManager crash — BLOCKING fix (security > availability): {_cap_err}", exc_info=True)
            return {
                "action": "blocked",
                "tier": int(ctx.bug.tier),
                "reason": f"CapabilityManager error (fail-closed): {_cap_err}",
            }

        # [P1-1 FIX R16] Wire verify_chain into the apply step.
        try:
            _gate = getattr(self, "policy_gate", None) or getattr(self, "_policy_gate", None)
            if _gate is not None and hasattr(_gate, "verify_chain"):
                _chain_ok, _chain_msg = _gate.verify_chain()
                if not _chain_ok:
                    logger.error(
                        f"[P1-1 R16] Policy gate verify_chain FAILED: {_chain_msg} — "
                        f"BLOCKING fix (audit log may be tampered)"
                    )
                    return {
                        "action": "blocked",
                        "tier": int(ctx.bug.tier),
                        "reason": f"verify_chain failed (audit log tampered?): {_chain_msg}",
                        "blocked_by": "verify_chain",
                    }
                logger.debug(f"[P1-1 R16] verify_chain OK: {_chain_msg}")
        except Exception as _vc_err:
            logger.error(
                f"[P1-1 R16] verify_chain crashed (fail-open, DNA #7): {_vc_err} — "
                f"proceeding with fix, but operator must investigate policy_gate health", exc_info=True
            )

        if ctx.bug.bug_type == "BareExceptPass" and os.environ.get("SCP_SKIP_BAREEXCEPTPASS", "0") == "1":
            logger.info(
                f"[AutoFix] SKIP BareExceptPass (SCP_SKIP_BAREEXCEPTPASS=1) "
                f"({ctx.bug.file}:{ctx.bug.line})"
            )
            return {
                "action": "skipped",
                "tier": int(ctx.bug.tier),
                "reason": "BareExceptPass skipped (SCP_SKIP_BAREEXCEPTPASS=1)",
            }

        # Rate limit
        if self._fixes_this_cycle >= MAX_FIXES_PER_CYCLE:
            return {"action": "skipped", "tier": int(ctx.bug.tier),
                    "reason": "rate limit — max fixes per cycle exceeded"}

        # [V9.0-WHY-GATE] WHY gates AutoFix — PRIMARY CONTROL GATE
        try:
            from scp.meta.why_gate import get_why_gate
            _why = get_why_gate().gate(
                action_type="autofix",
                action_desc=f"Fix {ctx.bug.bug_type} at {ctx.bug.file}:{ctx.bug.line}: {(ctx.bug.description or '')[:100]}",
                context=(ctx.bug.suggested_fix or "")[:500],
            )
            if _why.blocked:
                logger.info(
                    f"[V9.0-WHY-GATE] AutoFix blocked for {ctx.bug.file}:{ctx.bug.line}: "
                    f"{_why.falsification_reason[:100]}"
                )
                return {"action": "skipped", "tier": int(ctx.bug.tier),
                        "reason": f"FALSIFICATION: WHY-Gate blocked patch: {_why.falsification_reason}",
                        "pending_fix_queued": False}
        except Exception as _why_err:
            logger.debug(f"[V9.0-WHY-GATE] WHY Gate error (non-blocking, default allow): {_why_err}", exc_info=True)

        # [R9 v4 WIRE — IMP-24] Constitutional Policy Gate (DEFAULT-DENY).
        try:
            from scp.autofix.policy_gate import (
                PolicyFix as _V4_PolicyFix,
            )
            from scp.autofix.policy_gate import (
                evaluate_fix as _v4_policy_evaluate,
            )
            _v4_pf = _V4_PolicyFix(
                fix_id=f"{ctx.bug.file}:{ctx.bug.line}:{ctx.bug.bug_type}",
                patch=ctx.bug.suggested_fix or "",
                patched_source="",
                bug_file=ctx.bug.file or "",
                bug_line=int(ctx.bug.line or 0),
                scanner_name="autofix_engine",
                extra={"bug_type": ctx.bug.bug_type, "tier": int(ctx.bug.tier)},
            )
            _v4_decision = _v4_policy_evaluate(_v4_pf)
            if not _v4_decision.allowed:
                logger.warning(
                    f"[R9 v4 IMP-24] POLICY BLOCKED fix for "
                    f"{ctx.bug.file}:{ctx.bug.line}: patterns="
                    f"{_v4_decision.blocked_patterns} "
                    f"reason={_v4_decision.reason[:120]} "
                    f"audit_id={_v4_decision.audit_id}"
                )
                return {
                    "action": "skipped",
                    "tier": int(ctx.bug.tier),
                    "reason": (
                        f"policy_gate BLOCK (DNA #4): "
                        f"{_v4_decision.reason[:160]}"
                    ),
                    "patched": False,
                    "policy_blocked": True,
                    "policy_audit_id": _v4_decision.audit_id,
                    "policy_patterns": list(_v4_decision.blocked_patterns),
                }
            logger.debug(
                f"[R9 v4 IMP-24] policy gate ALLOWED fix for "
                f"{ctx.bug.file}:{ctx.bug.line} (severity={_v4_decision.severity})"
            )
        except ImportError as _v4_p_imp:
            logger.error(
                f"[R9 v4 IMP-24] policy_gate module unavailable — "
                f"DEFAULT-DENY (DNA #4 Constitution KILL gate down): {_v4_p_imp}"
            )
            return {
                "action": "blocked",
                "tier": int(ctx.bug.tier),
                "reason": (
                    f"policy_gate ImportError — DEFAULT-DENY (DNA #4): "
                    f"{_v4_p_imp}"
                ),
                "policy_gate_down": True,
            }
        except Exception as _v4_p_err:
            logger.error(
                f"[R9 v4 IMP-24] policy_gate evaluate_fix CRASH — "
                f"DEFAULT-DENY (DNA #4): {_v4_p_err}",
                exc_info=True,
            )
            try:
                _repaired = self._attempt_meta_repair("policy_gate", _v4_p_err)
                if _repaired:
                    from scp.autofix.policy_gate import (
                        PolicyFix as _V4_PolicyFix2,
                    )
                    from scp.autofix.policy_gate import (
                        evaluate_fix as _v4_policy_evaluate2,
                    )
                    _v4_pf2 = _V4_PolicyFix2(
                        fix_id=f"{ctx.bug.file}:{ctx.bug.line}:{ctx.bug.bug_type}",
                        patch=ctx.bug.suggested_fix or "",
                        patched_source="",
                        bug_file=ctx.bug.file or "",
                        bug_line=int(ctx.bug.line or 0),
                    )
                    _v4_decision2 = _v4_policy_evaluate2(_v4_pf2)
                    if not _v4_decision2.allowed:
                        return {
                            "action": "skipped", "tier": int(ctx.bug.tier),
                            "reason": f"policy_gate BLOCK after meta-repair: {_v4_decision2.reason[:160]}",
                            "patched": False, "policy_blocked": True,
                            "policy_audit_id": _v4_decision2.audit_id,
                            "policy_patterns": list(_v4_decision2.blocked_patterns),
                            "meta_repaired": True,
                        }
                    logger.info(" policy_gate meta-repair SUCCESS — gate back UP")
                else:
                    logger.warning(" policy_gate meta-repair failed — gate stays DOWN (DEFAULT-DENY)")
            except Exception as _meta_repair_err:
                logger.warning(f" meta-repair attempt crashed (fail-open): {_meta_repair_err}", exc_info=True)
            return {
                "action": "blocked",
                "tier": int(ctx.bug.tier),
                "reason": (
                    f"policy_gate crash — DEFAULT-DENY (DNA #4): {_v4_p_err}"
                ),
                "policy_gate_down": True,
            }

    def _auto_fix_part3(self, ctx) -> dict | None:
        filepath = Path(ctx.bug.file)
        from scp.core.code_evolution_agent import CodeEvolutionAgent
        agent = CodeEvolutionAgent.__new__(CodeEvolutionAgent)
        agent.log_file = self.data_dir / "evolution_log.jsonl"
        # [SCP-DNA-FIX R12-18] Real-Time Verifier — check invariants BEFORE file write.
        try:
            from scp.autofix.realtime_verifier import verify_patch_realtime
            _rtv_patched_source = filepath.read_text(encoding="utf-8") if filepath.exists() else ""  # noqa: F841
            if not ctx.pre_fix_content or not ctx.bug.suggested_fix:
                return {
                    "action": "skipped",
                    "tier": int(ctx.bug.tier),
                    "reason": "realtime patch simulation unavailable; fix is UNVERIFIED",
                    "patched": False,
                    "realtime_blocked": True,
                }
            _rtv_simulated = ctx.pre_fix_content
            import re as _rtv_re
            _rtv_blocks = _rtv_re.findall(
                r'<<<<<<< SEARCH\n(.*?)\n=======\n(.*?)\n>>>>>>>',
                ctx.bug.suggested_fix, _rtv_re.DOTALL
            )
            if not _rtv_blocks:
                return {
                    "action": "skipped",
                    "tier": int(ctx.bug.tier),
                    "reason": "realtime patch format unsupported; fix is UNVERIFIED",
                    "patched": False,
                    "realtime_blocked": True,
                }
            from scp.autofix.validate_patch import flexible_replace
            for _rtv_old, _rtv_new in _rtv_blocks:
                _res = flexible_replace(_rtv_simulated, _rtv_old, _rtv_new)
                if _res is None:
                    return {
                        "action": "skipped",
                        "tier": int(ctx.bug.tier),
                        "reason": "realtime patch search block not found; fix is UNVERIFIED",
                        "patched": False,
                        "realtime_blocked": True,
                    }
                _rtv_simulated = _res
            if _rtv_simulated != ctx.pre_fix_content:
                _rtv_result = verify_patch_realtime(
                    orig_source=ctx.pre_fix_content,
                    patched_source=_rtv_simulated,
                    func_name=getattr(ctx.bug, "function_name", None) or getattr(ctx.bug, "method_name", None),
                )
                if not _rtv_result.ok:
                    _rtv_canary_state = getattr(ctx, "v4_shadow_result", None)
                    _rtv_canary_passed = bool(getattr(_rtv_canary_state, "passed", False))
                    if (
                        getattr(_rtv_result, "sandbox_incompatible", False)
                        and _rtv_canary_passed
                    ):
                        logger.warning(
                            f" Real-Time Verifier SKIPPED (sandbox-incompatible source) for "
                            f"{ctx.bug.file}:{ctx.bug.line}: {_rtv_result.reason} — "
                            f"verification delegated to shadow canary + post_fix_verify"
                        )
                        ctx.realtime_skipped = True
                    else:
                        logger.warning(
                            f" Real-Time Verifier BLOCKED patch for "
                            f"{ctx.bug.file}:{ctx.bug.line}: {_rtv_result.reason} — skipping file write"
                        )
                        return {
                            "action": "skipped",
                            "tier": int(ctx.bug.tier),
                            "reason": f"realtime_verifier: {_rtv_result.reason[:160]}",
                            "patched": False,
                            "realtime_blocked": True,
                            "violations": _rtv_result.violations[:3],
                        }
                else:
                    logger.info(
                        f" Real-Time Verifier OK: {_rtv_result.reason} "
                        f"(inputs={_rtv_result.inputs_tested})"
                    )
        except ImportError as _rtv_imp:
            logger.warning(
                " realtime_verifier unavailable; blocking unverifiable patch: %s",
                type(_rtv_imp).__name__,
            )
            return {
                "action": "skipped",
                "tier": int(ctx.bug.tier),
                "reason": "realtime verifier unavailable; fix is UNVERIFIED",
                "patched": False,
                "realtime_blocked": True,
            }
        except Exception as _rtv_err:
            logger.warning(
                " realtime_verifier failed; blocking unverifiable patch: %s",
                type(_rtv_err).__name__,
                exc_info=True,
            )
            return {
                "action": "skipped",
                "tier": int(ctx.bug.tier),
                "reason": "realtime verifier failed; fix is UNVERIFIED",
                "patched": False,
                "realtime_blocked": True,
            }

        patched = agent._apply_fix(filepath, ctx.bug.suggested_fix, queue_on_failure=getattr(ctx.bug, "_last_refinement_round", True))

        if patched:
            self._fixes_this_cycle += 1
            try:
                from scp.autofix.llm_fix_cache import invalidate_cache_for_file
                invalidate_cache_for_file(str(filepath))
            except Exception as _inv_err:
                logger.debug(f" cache invalidate failed (non-fatal): {_inv_err}", exc_info=True)
        else:
            if getattr(ctx, "shadow_tx_id", None) and getattr(ctx, "shadow_mgr", None):
                ctx.shadow_mgr.rollback(ctx.shadow_tx_id, reason="apply_fix_failed")
            try:
                from scp.autofix.diagnostic import diagnose_fix_failure as _diagnose
                from scp.autofix.monitor import FixAttempt as _FixAttempt
                from scp.autofix.monitor import get_monitor as _get_monitor
                ctx._diag = _diagnose(
                    bug_id=ctx._autofix_bug_id,
                    bug_type=ctx.bug.bug_type,
                    llm_output=ctx.bug.suggested_fix,
                    patch_parsed=None,
                    validation_result=None,
                    apply_result="failed",
                )
                _get_monitor().record(_FixAttempt(
                    bug_id=ctx._autofix_bug_id,
                    bug_type=ctx.bug.bug_type,
                    provider=ctx._autofix_provider,
                    diagnosis=ctx._diag.diagnosis,
                    success=False,
                ))
            except Exception as e:
                logger.warning(f"Silent except: {e}", exc_info=True)
            critique = getattr(agent, "last_critique", None) or "fix queued for human review (not auto-patchable)"
            return {
                "action": "skipped",
                "tier": int(ctx.bug.tier),
                "reason": critique,
                "patched": False,
                "pending_fix_queued": getattr(ctx.bug, "_last_refinement_round", True),
            }

        # FixVerification layer — self-verify after apply patch.
        try:
            _verify_ok, _verify_reason = self._verify_fix(filepath, [ctx.bug])
            if not _verify_ok:
                logger.warning(
                    f" Fix verification FAILED for {ctx.bug.file}:{ctx.bug.line}: "
                    f"{_verify_reason} — ROLLING BACK"
                )
                self._audit_v91("autofix_verify_fail_rollback", {
                    "file": ctx.bug.file, "line": ctx.bug.line, "bug_type": ctx.bug.bug_type,
                    "reason": _verify_reason,
                })
                if getattr(ctx, "shadow_tx_id", None) and getattr(ctx, "shadow_mgr", None):
                    ctx.shadow_mgr.rollback(ctx.shadow_tx_id, reason=f"VERIFY_FIX_FAILED: {_verify_reason}")
                elif ctx.pre_fix_content is not None:
                    try:
                        filepath.write_text(ctx.pre_fix_content, encoding="utf-8", newline="")
                        logger.info(f" Rollback OK for {ctx.bug.file}")
                    except Exception as _rb_err:
                        logger.error(f" Rollback FAILED for {ctx.bug.file}: {_rb_err}", exc_info=True)
                self._fixes_this_cycle = max(0, self._fixes_this_cycle - 1)
                try:
                    from scp.autofix.diagnostic import diagnose_fix_failure as _diagnose
                    from scp.autofix.monitor import FixAttempt as _FixAttempt
                    from scp.autofix.monitor import get_monitor as _get_monitor
                    ctx._diag = _diagnose(
                        bug_id=ctx._autofix_bug_id,
                        bug_type=ctx.bug.bug_type,
                        llm_output=ctx.bug.suggested_fix,
                        patch_parsed={"search": "(applied)", "replace": "(applied)"},
                        validation_result={"valid": True, "reason": "pre-apply OK"},
                        apply_result="rollback",
                    )
                    _get_monitor().record(_FixAttempt(
                        bug_id=ctx._autofix_bug_id,
                        bug_type=ctx.bug.bug_type,
                        provider=ctx._autofix_provider,
                        diagnosis=ctx._diag.diagnosis,
                        success=False,
                    ))
                except Exception as e:
                    logger.warning(f"Silent except: {e}", exc_info=True)
                return {
                    "action": "skipped",
                    "tier": int(ctx.bug.tier),
                    "reason": f"fix verification failed (rolled back): {_verify_reason}",
                    "patched": False,
                }
            self._audit_v91("autofix_verify_ok", {
                "file": ctx.bug.file, "line": ctx.bug.line, "bug_type": ctx.bug.bug_type,
                "reason": _verify_reason,
            })

            # [SCP-DNA-FIX R12-6] Wire R7-Full post-fix verification orchestrator.
            try:
                from scp.autofix.runner_phases.post_fix_verify import (
                    run_full_post_fix_verify,
                )
                _pfv_result = run_full_post_fix_verify(
                    bug_id=f"{ctx.bug.file}:{ctx.bug.line}:{ctx.bug.bug_type}",
                    file_path=str(filepath),
                    method_name=getattr(ctx.bug, "function_name", None) or getattr(ctx.bug, "method_name", None),
                    bug_type=ctx.bug.bug_type,
                    run_vulture=True,
                    run_import=True,
                    run_hypothesis=False,
                    run_reality_exercise=True,
                    run_completeness=True,
                    buggy_source=ctx.pre_fix_content,
                )
                if _pfv_result.get("ok") is not True:
                    _pfv_reason = _pfv_result.get("reason", "post-fix verification is UNVERIFIED")
                    _pfv_rollback = True
                    if _pfv_rollback:
                        logger.warning(
                            f" post_fix_verify ROLLBACK for {ctx.bug.file}:{ctx.bug.line}: "
                            f"{_pfv_reason} — restoring pre-fix content"
                        )
                        if getattr(ctx, "shadow_tx_id", None) and getattr(ctx, "shadow_mgr", None):
                            ctx.shadow_mgr.rollback(ctx.shadow_tx_id, reason=f"POST_FIX_VERIFY_FAILED: {_pfv_reason}")
                        elif ctx.pre_fix_content is not None:
                            try:
                                filepath.write_text(ctx.pre_fix_content, encoding="utf-8", newline="")
                                logger.info(f" Rollback OK for {ctx.bug.file}")
                            except Exception as _rb_err:
                                logger.error(f" Rollback FAILED for {ctx.bug.file}: {_rb_err}", exc_info=True)
                        self._fixes_this_cycle = max(0, self._fixes_this_cycle - 1)
                    else:
                        logger.warning(
                            f" post_fix_verify escalate_to_tier3 for "
                            f"{ctx.bug.file}:{ctx.bug.line}: {_pfv_reason}"
                        )
                    return {
                        "action": "skipped",
                        "tier": int(ctx.bug.tier),
                        "reason": (
                            f"post-fix verification rejected/unverified "
                            f"(rolled back): {_pfv_reason}"
                        ),
                        "patched": False,
                        "verification_status": "UNVERIFIED",
                        "post_fix_verification": _pfv_result,
                        "escalate_to_tier3": bool(
                            _pfv_result.get("escalate_to_tier3", True)
                        ),
                    }
                else:
                    logger.info(
                        f" post_fix_verify OK for {ctx.bug.file}:{ctx.bug.line} "
                        f"(phases: {list(_pfv_result.get('phases', {}).keys())})"
                    )
            except ImportError as _pfv_imp:
                logger.warning(
                    " post_fix_verify unavailable; rolling back unverifiable patch: %s",
                    type(_pfv_imp).__name__,
                )
                if getattr(ctx, "shadow_tx_id", None) and getattr(ctx, "shadow_mgr", None):
                    ctx.shadow_mgr.rollback(ctx.shadow_tx_id, reason=f"POST_FIX_VERIFY_IMPORT_ERROR: {_pfv_imp}")
                elif ctx.pre_fix_content is not None:
                    filepath.write_text(ctx.pre_fix_content, encoding="utf-8", newline="")
                self._fixes_this_cycle = max(0, self._fixes_this_cycle - 1)
                return {
                    "action": "skipped",
                    "tier": int(ctx.bug.tier),
                    "reason": "post-fix verifier unavailable; fix is UNVERIFIED and was rolled back",
                    "patched": False,
                }
            except Exception as _pfv_err:
                logger.warning(
                    " post_fix_verify failed; rolling back unverifiable patch: %s",
                    type(_pfv_err).__name__,
                    exc_info=True,
                )
                if getattr(ctx, "shadow_tx_id", None) and getattr(ctx, "shadow_mgr", None):
                    ctx.shadow_mgr.rollback(ctx.shadow_tx_id, reason=f"POST_FIX_VERIFY_ERROR: {_pfv_err}")
                elif ctx.pre_fix_content is not None:
                    filepath.write_text(ctx.pre_fix_content, encoding="utf-8", newline="")
                self._fixes_this_cycle = max(0, self._fixes_this_cycle - 1)
                return {
                    "action": "skipped",
                    "tier": int(ctx.bug.tier),
                    "reason": "post-fix verifier failed; fix is UNVERIFIED and was rolled back",
                    "patched": False,
                }
        except Exception as _verify_call_err:
            logger.warning(
                " verifier call failed; rolling back unverifiable patch: %s",
                type(_verify_call_err).__name__,
                exc_info=True,
            )
            if getattr(ctx, "shadow_tx_id", None) and getattr(ctx, "shadow_mgr", None):
                ctx.shadow_mgr.rollback(ctx.shadow_tx_id, reason=f"VERIFY_CALL_ERROR: {_verify_call_err}")
            elif ctx.pre_fix_content is not None:
                filepath.write_text(ctx.pre_fix_content, encoding="utf-8", newline="")
            self._fixes_this_cycle = max(0, self._fixes_this_cycle - 1)
            return {
                "action": "skipped",
                "tier": int(ctx.bug.tier),
                "reason": "verifier call failed; fix is UNVERIFIED and was rolled back",
                "patched": False,
            }

        # Record for cooldown
        bug_key = f"{ctx.bug.file}:{ctx.bug.line}:{ctx.bug.bug_type}"
        self._recent_fixes[bug_key] = time.time()

        _verify_ok_local = locals().get("_verify_ok")
        _verify_reason_local = locals().get("_verify_reason")
        try:
            from scp.autofix.audit_log import (
                compute_hashes as _compute_hashes_4b012,
            )
            from scp.autofix.audit_log import (
                make_rollback_token_backup as _rb_token_4b012,
            )
            _main_post = filepath.read_text(encoding="utf-8")
            ctx.before_hash, ctx.after_hash = _compute_hashes_4b012(
                ctx.pre_fix_content, _main_post,
            )
            if _verify_ok_local is True:
                ctx.reality_test_result = "pass"
            elif _verify_ok_local is False:
                ctx.reality_test_result = f"fail:{(_verify_reason_local or 'unknown')[:80]}"
            else:
                ctx.reality_test_result = "skipped"
            if ctx.attack_mode:
                try:
                    from scp.autofix.audit_log import (
                        make_rollback_token_git as _git_token_4b012,
                    )
                    ctx.rollback_token = _git_token_4b012(str(filepath))
                except Exception as git_token_err:
                    logger.warning("[4-b-012] git rollback token unavailable, falling back to backup-file token: %s", git_token_err, exc_info=True)
                    ctx.rollback_token = _rb_token_4b012(str(filepath))
            else:
                ctx.rollback_token = _rb_token_4b012(str(filepath))
        except Exception as _main_hash_err:
            logger.debug(
                f"[4-b-012] main audit hash/token compute failed "
                f"(non-fatal — entry gets 'n/a'): {_main_hash_err}", exc_info=True
            )
            ctx.before_hash = ctx.after_hash = "n/a"
            ctx.reality_test_result = "skipped"
            ctx.rollback_token = "n/a"

        # Log to audit trail (Tier 2+ always; Tier 1 only if report=True)
        if ctx.report or ctx.attack_mode:
            self._write_audit(
                ctx.bug, "fixed", attack_mode=ctx.attack_mode,
                before_hash=ctx.before_hash, after_hash=ctx.after_hash,
                rollback_token=ctx.rollback_token,
                reality_test_result=ctx.reality_test_result,
            )
        else:
            self._write_audit(
                ctx.bug, "fixed_silent", attack_mode=False,
                before_hash=ctx.before_hash, after_hash=ctx.after_hash,
                rollback_token=ctx.rollback_token,
                reality_test_result=ctx.reality_test_result,
            )

        # [V5.7-WHY] Evolution Engine reflect feedback (opt-in via SCP_EVOLUTION_ENABLED=1).
        try:
            if os.environ.get("SCP_EVOLUTION_ENABLED", "0") == "1":
                from scp.autofix.evolution import get_evolution_engine
                _evo = get_evolution_engine(data_dir=str(self.data_dir))
                _fix_diff = str(ctx.bug.suggested_fix) if ctx.bug.suggested_fix else ""
                _fix_verified_receipt = (
                    locals().get("_verify_ok") is True
                    and isinstance(locals().get("_pfv_result"), dict)
                    and locals().get("_pfv_result", {}).get("ok") is True
                )
                _reflect_result = _evo.reflect(
                    ctx.bug, _fix_diff, fix_verified=_fix_verified_receipt,
                )
                logger.info(
                    f"[V5.7-WHY] reflect: {ctx.bug.file}:{ctx.bug.line} "
                    f"self_falsified={getattr(_reflect_result, 'self_falsified', '?')} "
                    f"lesson={getattr(_reflect_result, 'lesson_learned', '')[:80]!r}"
                )
        except Exception as _reflect_err:
            logger.debug(f"[V5.7-WHY] reflect failed (non-fatal): {_reflect_err}", exc_info=True)

        # [OPT-27/28] Record success — patch applied + verified.
        try:
            from scp.autofix.diagnostic import diagnose_fix_failure as _diagnose
            from scp.autofix.monitor import FixAttempt as _FixAttempt
            from scp.autofix.monitor import get_monitor as _get_monitor
            ctx._diag = _diagnose(
                bug_id=ctx._autofix_bug_id,
                bug_type=ctx.bug.bug_type,
                llm_output=ctx.bug.suggested_fix,
                patch_parsed={"search": "(applied)", "replace": "(applied)"},
                validation_result={"valid": True, "reason": "pre-apply OK"},
                apply_result="success",
            )
            _get_monitor().record(_FixAttempt(
                bug_id=ctx._autofix_bug_id,
                bug_type=ctx.bug.bug_type,
                provider=ctx._autofix_provider,
                diagnosis=ctx._diag.diagnosis,
                success=True,
            ))
        except Exception as e:
            logger.warning(f"Silent except: {e}", exc_info=True)

        # [R10 v3 WIRE — IMP-17] Regression Watcher (background daemon).
        try:
            from scp.autofix.runner_phases.auto_rollback import (
                get_regression_watcher as _v4_get_watcher,
            )
            _v4_watch_token = ""
            if ctx.pre_fix_content is not None:
                try:
                    _v4_post_content = filepath.read_text(encoding="utf-8")
                    _v4_watch_token = self.register_fix_for_rollback(
                        file_path=str(filepath),
                        before_content=ctx.pre_fix_content,
                        after_content=_v4_post_content,
                        patch=ctx.bug.suggested_fix or "",
                        bug_id=ctx._autofix_bug_id,
                        bug_type=ctx.bug.bug_type,
                        tier=int(ctx.bug.tier),
                        reality_test_result=None,
                    )
                except Exception as _v4_rb_reg_err:
                    logger.debug(
                        f"[R10 v3 IMP-17] register_fix_for_rollback failed "
                        f"(non-fatal — watcher will log-only): {_v4_rb_reg_err}", exc_info=True
                    )
            if _v4_watch_token:
                _v4_watcher = _v4_get_watcher()
                _v4_watcher.register(
                    fix_id=ctx._autofix_bug_id,
                    file_path=str(filepath),
                    rollback_token=_v4_watch_token,
                    ttl=60,
                    extra={
                        "bug_type": ctx.bug.bug_type,
                        "tier": int(ctx.bug.tier),
                        "ctx.attack_mode": ctx.attack_mode,
                    },
                )
                logger.info(
                    f"[R10 v3 IMP-17] registered fix {ctx._autofix_bug_id} "
                    f"for regression watch (ttl=60s, token={_v4_watch_token[:8]}...)"
                )
        except ImportError as _v4_ar_imp:
            logger.debug(
                f"[R10 v3 IMP-17] auto_rollback unavailable (fail-open): {_v4_ar_imp}"
            )
        except Exception as _v4_ar_err:
            logger.debug(
                f"[R10 v3 IMP-17] auto_rollback wire crash (fail-open): {_v4_ar_err}", exc_info=True
            )

        _result_rollback_token = locals().get("_v4_watch_token") or getattr(ctx, "rollback_token", "n/a")
        _result_rollback_registered = bool(locals().get("_v4_watch_token"))
        if getattr(ctx, "shadow_tx_id", None) and getattr(ctx, "shadow_mgr", None):
            ctx.shadow_mgr.commit(ctx.shadow_tx_id)
        return {
            "action": "fixed",
            "tier": int(ctx.bug.tier),
            "patched": patched,
            "ctx.attack_mode": ctx.attack_mode,
            "before_hash": getattr(ctx, "before_hash", "n/a"),
            "after_hash": getattr(ctx, "after_hash", "n/a"),
            "rollback_token": _result_rollback_token,
            "rollback_registered": _result_rollback_registered,
            "reality_test_result": getattr(ctx, "reality_test_result", "skipped"),
            "post_fix_verification": locals().get("_pfv_result", {}),
        }
