"""Tier-3 auto-approve configuration, safety guards, and meta-repair mixin for AutoFixEngine."""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

from scp.autofix.classifier import BugReport

logger = logging.getLogger("scp.autofix")

COOLDOWN_SAME_BUG_SECONDS = 3600  # Don't re-fix same bug within 1 hour
MAX_TIER3_AUTO_PER_HOUR = 5       # Hard cap: 5 logic-bug auto-fixes/hour
TIER3_AUTO_TIMEOUT_SECONDS = 3600
TIER3_AUTO_AUDIT_LOG = "tier3_auto_audit.jsonl"


class Tier3AutoConfig:
    """Centralized config for Tier-3 auto-approve — SCP understands the permission."""

    def __init__(self):
        self._audit_log = Path("data/tier3_auto_audit.jsonl")
        self._audit_log.parent.mkdir(parents=True, exist_ok=True)

    def is_enabled(self) -> bool:
        """Check if Tier-3 auto-approve is enabled (with logging)."""
        val = os.environ.get("SCP_AUTO_APPROVE_TIER3", "0")
        enabled = val == "1"
        return enabled

    def get_permission_source(self) -> str:
        """Identify WHERE the permission came from."""
        if "SCP_AUTO_APPROVE_TIER3" not in os.environ:
            return "default (not set — human approval required)"
        val = os.environ.get("SCP_AUTO_APPROVE_TIER3", "0")
        if val == "1":
            return ".env file (SCP_AUTO_APPROVE_TIER3=1 — auto-approve GRANTED)"
        return ".env file (SCP_AUTO_APPROVE_TIER3=0 — human approval required)"

    def log_startup_permission(self):
        """Log at startup: SCP understands whether permission is granted."""
        source = self.get_permission_source()
        if self.is_enabled():
            logger.warning(
                f"[TIER3-AUTO] ⚠️  Permission GRANTED — Tier-3 auto-approve ENABLED\n"
                f"  Source: {source}\n"
                f"  Safety guards active: 1h timeout, 5/hour limit, no relaxation,\n"
                f"  no BareExceptPass, cooldown 1h per bug\n"
                f"  Audit log: {self._audit_log}"
            )
        else:
            logger.info(
                f"[TIER3-AUTO] Permission NOT granted — human approval required\n"
                f"  Source: {source}\n"
                f"  To enable: set SCP_AUTO_APPROVE_TIER3=1 in .env + restart\n"
                f"  Or runtime: POST /v105/autofix/tier3-auto/1 (no restart needed)"
            )

    def audit_auto_approve(self, bug: BugReport, reason: str):
        """Audit trail: record every auto-approve decision."""
        entry = {
            "timestamp": time.time(),
            "action": "auto_approve",
            "file": bug.file,
            "line": bug.line,
            "bug_type": bug.bug_type,
            "reason": reason,
            "permission_source": self.get_permission_source(),
        }
        try:
            with open(self._audit_log, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.debug(f"[TIER3-AUTO] audit log write failed: {e}", exc_info=True)


_tier3_config: Tier3AutoConfig | None = None


def get_tier3_config() -> Tier3AutoConfig:
    """Get singleton Tier3AutoConfig."""
    global _tier3_config
    if _tier3_config is None:
        _tier3_config = Tier3AutoConfig()
    return _tier3_config


class Tier3Mixin:
    """Meta-repair and Tier-3 auto-approve methods for AutoFixEngine."""

    _meta_repair_attempted: bool = False  # class-level guard, reset per cycle in _auto_fix

    def _attempt_meta_repair(self, module_name: str, error: Exception) -> bool:
        """Attempt to auto-repair a crashed safety module via LLM. Returns True if repaired."""
        if self._meta_repair_attempted:
            logger.debug(" meta-repair already attempted this cycle — skip")
            return False
        self._meta_repair_attempted = True
        try:
            import traceback as _tb
            _error_str = f"{type(error).__name__}: {error}\n{_tb.format_exc()[:500]}"
            logger.warning(f" attempting meta-repair for {module_name}: {_error_str[:200]}")

            _module_path_map = {
                "policy_gate": "scp/autofix/policy_gate.py",
                "property_validator": "scp/autofix/property_validator.py",
                "evidence_replay": "scp/autofix/evidence_replay.py",
            }
            _module_path = _module_path_map.get(module_name)
            if not _module_path:
                logger.warning(f" unknown module for meta-repair: {module_name}")
                return False

            from pathlib import Path as _P
            _p = _P(_module_path)
            if not _p.exists():
                return False
            _source = _p.read_text(encoding="utf-8")

            try:
                from scp.autofix.llm_fix import _call_smart_llm
                _prompt = (
                    f"The following Python module crashed with this error:\n\n"
                    f"--- ERROR ---\n{_error_str}\n\n"
                    f"--- MODULE SOURCE ({_module_path}) ---\n{_source[:3000]}\n\n"
                    f"Generate a minimal search-replace patch to fix the crash. "
                    f"Format:\n<<<<<<< SEARCH\nold code\n=======\nnew code\n>>>>>>> REPLACE\n"
                    f"Only fix the crash — do NOT change behavior. DNA #9 (No harm)."
                )
                _patch = _call_smart_llm(_prompt, bug_type="meta_repair", max_tokens=2000)
                if not _patch or "<<<<<<< SEARCH" not in _patch:
                    logger.warning(f" LLM returned no valid patch for {module_name}")
                    return False
                import re as _re
                _blocks = _re.findall(r'<<<<<<< SEARCH\n(.*?)\n=======\n(.*?)\n>>>>>>>', _patch, _re.DOTALL)
                _new_source = _source
                for _old, _new in _blocks:
                    _new_source = _new_source.replace(_old, _new, 1)
                if _new_source == _source:
                    logger.warning(f" patch did not change source for {module_name}")
                    return False
                import ast as _ast
                _ast.parse(_new_source)

                from scp.interfaces.evolution import (
                    get_relative_repo_path as _relative_repo_path,
                )
                try:
                    _rel = _relative_repo_path(_p)  # noqa: F841
                except Exception as _rel_err:
                    logger.debug(" meta-repair: relative path compute failed, using module path: %s", _rel_err, exc_info=True)
                    _rel = _module_path  # noqa: F841
                _proposals = Path("data") / "governance" / "proposals"
                _proposals.mkdir(parents=True, exist_ok=True)
                _proposal_file = _proposals / f"meta_repair_{module_name}_{int(time.time())}.md"
                _proposal_file.write_text(
                    f"# Meta-repair proposal: {_module_path}\n\n"
                    f"## Error\n```\n{_error_str}\n```\n\n"
                    f"## Proposed patch (SEARCH/REPLACE)\n```diff\n{_patch}\n```\n\n"
                    f"## Human action required\nReview, apply manually or via "
                    f"governance-approved change (protected_invariants.yaml).",
                    encoding="utf-8",
                )
                logger.warning(
                    f" meta-repair PROPOSAL queued (not applied — protected path): "
                    f"{_proposal_file} — human review required"
                )
                return False
            except ImportError:
                logger.debug(" llm_fix unavailable — cannot meta-repair")
                return False
            except Exception as _llm_err:
                logger.warning(f" LLM meta-repair failed: {_llm_err}", exc_info=True)
                return False
        except Exception as _meta_err:
            logger.warning(f" meta-repair outer crash: {_meta_err}", exc_info=True)
            return False

    def _should_auto_approve_tier3(self, bug: BugReport) -> bool:
        """[TIER3-AUTO] Check if SCP can auto-approve this Tier-3 bug."""
        _config = get_tier3_config()

        if not _config.is_enabled():
            return False

        _permission_source = _config.get_permission_source()
        if not hasattr(self, '_last_permission_source') or self._last_permission_source != _permission_source:
            logger.info(
                f"[TIER3-AUTO] Decision authority granted — "
                f"SCP có quyền phán quyết auto-approve Tier-3"
                f" (source: {_permission_source[:60]})"
            )
            self._last_permission_source = _permission_source

        if bug.bug_type == "BareExceptPass":
            logger.info(
                f"[TIER3-AUTO] SKIP auto-approve for BareExceptPass "
                f"({bug.file}:{bug.line}) -- requires human review "
                f"(runtime log: 63% rollback rate)"
            )
            return False

        now = time.time()
        _env_now = os.environ.get("SCP_AUTO_APPROVE_TIER3", "0")
        _last_env = getattr(self, "_tier3_env_last_seen", "0")
        if _env_now == "1" and _last_env != "1":
            self._tier3_auto_expired = False
            self._tier3_auto_enabled_at = 0.0
            logger.info("[TIER3-AUTO] Re-arm permitted — env var transition 0→1 detected")
        self._tier3_env_last_seen = _env_now

        if not getattr(self, "_tier3_auto_expired", False):
            if self._tier3_auto_enabled_at == 0.0:
                self._tier3_auto_enabled_at = now
            elif now - self._tier3_auto_enabled_at > TIER3_AUTO_TIMEOUT_SECONDS:
                logger.warning(
                    f"[TIER3-AUTO] Timed out after {TIER3_AUTO_TIMEOUT_SECONDS}s -- "
                    f"re-enable by UNSETTING + re-SETTING SCP_AUTO_APPROVE_TIER3=1 "
                    f"(R8-2: previous behavior re-armed silently on next bug)"
                )
                self._tier3_auto_expired = True
                return False
        else:
            return False

        self._tier3_auto_timestamps = [t for t in self._tier3_auto_timestamps if now - t < 3600]
        if len(self._tier3_auto_timestamps) >= MAX_TIER3_AUTO_PER_HOUR:
            logger.warning(
                f"[TIER3-AUTO] Rate limit hit -- {MAX_TIER3_AUTO_PER_HOUR}/hour exceeded"
            )
            return False

        if getattr(bug, "is_relaxation", False):
            logger.warning(
                f"[TIER3-AUTO] HARD LIMIT -- bug {bug.file}:{bug.line} is relaxation "
                f"(loosens security) -> still requires human approval"
            )
            return False

        bug_key = f"{bug.file}:{bug.line}:{bug.bug_type}"
        if bug_key in self._recent_fixes:
            if now - self._recent_fixes[bug_key] < COOLDOWN_SAME_BUG_SECONDS:
                return False

        return True

    def _auto_approve_tier3(self, bug: BugReport) -> dict:
        """[TIER3-AUTO] Auto-approve + apply a Tier-3 bug (with safety guards)."""
        get_tier3_config().audit_auto_approve(
            bug,
            reason=f"All 6 safety guards passed (bug_type={bug.bug_type})"
        )

        _before_hash = ""
        try:
            from pathlib import Path as PathCls
            filepath = PathCls(bug.file)
            if filepath.exists():
                import hashlib as _hashlib
                _before_hash = _hashlib.sha256(
                    filepath.read_bytes()
                ).hexdigest()
        except Exception as e:
            logger.debug(f" before_hash compute failed for {bug.file}: {e}", exc_info=True)

        _rollback_token = ""
        try:
            import uuid as _uuid
            _rollback_token = str(_uuid.uuid4())
        except Exception:
            logger.warning('[AUTOFIX] UUID rollback token generation failed; using legacy backup fallback', exc_info=True)

        tier3_tx_id = ""
        shadow_mgr = None
        try:
            from scp.autofix.shadow_snapshot import get_shadow_snapshot_manager
            shadow_mgr = get_shadow_snapshot_manager(shadow_dir=self.data_dir / "shadow")
            from pathlib import Path as PathCls
            filepath = PathCls(bug.file)
            if filepath.exists():
                tier3_tx_id = shadow_mgr.begin([filepath], bug_id=f"tier3_{bug.file}:{bug.line}")
        except Exception as e:
            logger.warning(f"[TIER3-AUTO] Shadow snapshot failed for {bug.file}: {e}", exc_info=True)

        result = self._auto_fix(bug, report=True, attack_mode=False)
        if result.get("action") == "fixed":
            result["tier"] = 3
            result["tier3_auto_approved"] = True
            self._tier3_auto_timestamps.append(time.time())
            _after_hash = ""
            _reality_test_result = "SKIPPED"
            try:
                from pathlib import Path as PathCls
                filepath = PathCls(bug.file)
                if filepath.exists():
                    import hashlib as _hashlib
                    _after_hash = _hashlib.sha256(
                        filepath.read_bytes()
                    ).hexdigest()
                    import ast as _ast
                    try:
                        _ast.parse(filepath.read_text(encoding="utf-8"))
                        _reality_test_result = "PASS"
                    except SyntaxError as _se:
                        _reality_test_result = f"FAIL:SyntaxError:{(_se.msg or '')[:80]}"
                    except Exception as _ee:
                        logger.debug(f"AutoFixEngine._auto_approve_tier3: exception ignored: {_ee}", exc_info=True)
                        _reality_test_result = f"FAIL:{type(_ee).__name__}:{str(_ee)[:80]}"

                    if _reality_test_result == "PASS":
                        from scp.sandbox_evaluator.evaluator import (
                            build_patch_target as _build_sandbox_target,
                        )
                        from scp.sandbox_evaluator.evaluator import (
                            evaluate as _sandbox_evaluation,
                        )
                        from scp.sandbox_evaluator.evaluator import (
                            sandbox_enabled as _sandbox_opt_in,
                        )
                        if _sandbox_opt_in():
                            _sandbox_test_paths = [
                                _tp.strip()
                                for _tp in os.environ.get(
                                    "SCP_SANDBOX_EVALUATOR_TESTS", ""
                                ).replace(";", ",").split(",")
                                if _tp.strip()
                            ]
                            if not _sandbox_test_paths:
                                _reality_test_result = "FAIL:sandbox:no_tests_configured"
                            else:
                                _sandbox_res = _sandbox_evaluation(
                                    _build_sandbox_target(
                                        str(filepath),
                                        filepath.read_text(encoding="utf-8"),
                                        test_paths=_sandbox_test_paths,
                                    )
                                )
                                result["sandbox_eval"] = _sandbox_res.to_dict_bounded()
                                _reality_test_result = (
                                    "PASS"
                                    if _sandbox_res.verdict == "PASS"
                                    else f"FAIL:sandbox:{_sandbox_res.reason}"
                                )
            except Exception as e:
                logger.debug(f" after_hash / reality_test compute failed: {e}", exc_info=True)
                _reality_test_result = f"FAIL:hash_compute:{str(e)[:80]}"

            if _reality_test_result != "PASS":
                logger.error(
                    f"[TIER3-AUTO] Reality test FAILED ({_reality_test_result}) — ROLLING BACK (fail-closed)"
                )
                if tier3_tx_id and shadow_mgr:
                    shadow_mgr.rollback(tier3_tx_id, reason=f"tier3_reality_test_fail: {_reality_test_result}")
                self._write_tier3_auto_audit(
                    bug, "auto_approve_failed_reality_test_rolled_back",
                    before_hash=_before_hash,
                    after_hash=_after_hash,
                    reality_test_result=_reality_test_result,
                    rollback_token=_rollback_token,
                )
                result["action"] = "skipped"
                result["patched"] = False
                result["reason"] = f"Reality test failed: {_reality_test_result} (rolled back)"
                result["reality_test_result"] = _reality_test_result
                result["rollback_token"] = _rollback_token
                return result

            if tier3_tx_id and shadow_mgr:
                shadow_mgr.commit(tier3_tx_id)

            self._write_tier3_auto_audit(
                bug, "auto_approved_and_fixed",
                before_hash=_before_hash,
                after_hash=_after_hash,
                reality_test_result=_reality_test_result,
                rollback_token=_rollback_token,
            )
            result["rollback_token"] = _rollback_token
            result["after_hash"] = _after_hash
            result["reality_test_result"] = _reality_test_result
        else:
            if tier3_tx_id and shadow_mgr:
                shadow_mgr.rollback(tier3_tx_id, reason="tier3_autofix_not_fixed")
        return result

    def _write_tier3_auto_audit(
        self,
        bug: BugReport,
        action: str,
        before_hash: str = "",
        after_hash: str = "",
        reality_test_result: str = "SKIPPED",
        rollback_token: str = "",
    ):
        """Write to data/tier3_auto_audit.jsonl (separate from normal audit)."""
        entry = {
            "timestamp": time.time(),
            "file": bug.file,
            "line": bug.line,
            "bug_type": bug.bug_type,
            "description": bug.description[:300],
            "suggested_fix": (bug.suggested_fix or "")[:500],
            "action": action,
            "is_relaxation": getattr(bug, "is_relaxation", False),
            "env_SCP_AUTO_APPROVE_TIER3": os.environ.get("SCP_AUTO_APPROVE_TIER3", "0"),
            "before_hash": before_hash,
            "after_hash": after_hash,
            "reality_test_result": reality_test_result,
            "rollback_token": rollback_token,
        }
        try:
            with open(self.tier3_auto_audit_log, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.warning(f"[TIER3-AUTO] Failed to write audit log: {e}", exc_info=True)
