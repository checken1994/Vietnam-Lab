# SCP CIRCUIT: M07 — STATUS: CLOSED_WITH_KNOWN_GAP (closure: docs/evidence-summary/M07-closure.json)
"""SCP Auto-Fix Engine — autonomous bug fixing with tiered autonomy."""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path

from scp.autofix.classifier import BugClassifier, BugReport, BugTier
from scp.autofix.engine_parts.autofix_mixin import MAX_FIXES_PER_CYCLE, AutoFixMixin
from scp.autofix.engine_parts.tier3_mixin import (
    COOLDOWN_SAME_BUG_SECONDS,
    MAX_TIER3_AUTO_PER_HOUR,
    TIER3_AUTO_AUDIT_LOG,
    TIER3_AUTO_TIMEOUT_SECONDS,
    Tier3AutoConfig,
    Tier3Mixin,
    _tier3_config,
    get_tier3_config,
)
from scp.autofix.engine_parts.verify_mixin import VerifyMixin
from scp.autofix.permission import PermissionGate

logger = logging.getLogger("scp.autofix")

MAX_TIER4_PER_HOUR = 20           # Max attack-mode fixes per hour
CYCLE_RESET_SECONDS = 3600        # Reset _fixes_this_cycle every 1 hour


class AutoFixEngine:
    """Autonomous bug fixing engine with tiered autonomy."""

    def __init__(self, data_dir: str = "data", in_attack_mode: bool = False):
        self._verify_component = VerifyMixin()
        self._autofix_component = AutoFixMixin(engine=self)
        self._tier3_component = Tier3Mixin()

        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.audit_log = self.data_dir / "autofix_audit.jsonl"
        self.classifier = BugClassifier()
        self.permission_gate = PermissionGate(data_dir=data_dir)

        get_tier3_config().log_startup_permission()

        if not in_attack_mode:
            in_attack_mode = os.environ.get("SCP_ATTACK_MODE", "0") == "1"
        self.in_attack_mode = in_attack_mode
        if self.in_attack_mode:
            logger.warning(
                "[TIER4-ATTACK] ⚠️  Attack mode ENABLED via .env (SCP_ATTACK_MODE=1)\n"
                "  SCP will auto-apply restraints (tighten security) without human approval.\n"
                "  Safety guards: 20/hour limit, reversible only, audit log, no relaxation.\n"
                "  To disable: set SCP_ATTACK_MODE=0 in .env + restart."
            )
        self._fixes_this_cycle = 0
        self._tier4_timestamps: list[float] = []
        self._recent_fixes: dict[str, float] = {}  # (file, line, type) → timestamp
        self._cycle_start_time: float = time.time()
        self._tier3_auto_timestamps: list[float] = []
        self._tier3_auto_enabled_at: float = 0.0  # 0 = disabled
        self.tier3_auto_audit_log = self.data_dir / TIER3_AUTO_AUDIT_LOG

        # R6: Recover any abandoned transactions from prior crashes on startup
        try:
            from scp.autofix.shadow_snapshot import get_shadow_snapshot_manager
            self.shadow_snapshot_mgr = get_shadow_snapshot_manager(shadow_dir=self.data_dir / "shadow")
            _recovered = self.shadow_snapshot_mgr.recover_abandoned_transactions()
            if _recovered:
                logger.warning(f"[AutoFix] Recovered {len(_recovered)} abandoned transaction(s) on startup: {_recovered}")
        except Exception as _rec_err:
            logger.error(f"[AutoFix] Failed to recover abandoned transactions on startup: {_rec_err}", exc_info=True)

    def _verify_fix(self, *args, **kwargs):
        return VerifyMixin._verify_fix(self, *args, **kwargs)

    def _auto_fix(self, *args, **kwargs):
        return AutoFixMixin._auto_fix(self, *args, **kwargs)

    def _auto_fix_gates(self, *args, **kwargs):
        return AutoFixMixin._auto_fix_gates(self, *args, **kwargs)

    def _auto_fix_part1(self, *args, **kwargs):
        return self._autofix_component._auto_fix_part1(args[0] if args else kwargs.get("ctx"), engine=self)

    def _auto_fix_part2(self, *args, **kwargs):
        return self._autofix_component._auto_fix_part2(args[0] if args else kwargs.get("ctx"), engine=self)

    def _auto_fix_part3(self, *args, **kwargs):
        return AutoFixMixin._auto_fix_part3(self, *args, **kwargs)

    def _attempt_meta_repair(self, *args, **kwargs):
        return Tier3Mixin._attempt_meta_repair(self, *args, **kwargs)

    def _should_auto_approve_tier3(self, *args, **kwargs):
        return Tier3Mixin._should_auto_approve_tier3(self, *args, **kwargs)

    def _auto_approve_tier3(self, *args, **kwargs):
        return Tier3Mixin._auto_approve_tier3(self, *args, **kwargs)

    def _write_tier3_auto_audit(self, *args, **kwargs):
        return Tier3Mixin._write_tier3_auto_audit(self, *args, **kwargs)

    def __getattr__(self, name: str):
        for comp in (
            getattr(self, "_verify_component", None),
            getattr(self, "_autofix_component", None),
            getattr(self, "_tier3_component", None),
        ):
            if comp is not None and hasattr(comp, name):
                attr = getattr(comp, name)
                if callable(attr):
                    return attr.__get__(self, type(self))
                return attr
        raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")

    def _check_cycle_reset(self) -> None:
        """Reset per-cycle counters when CYCLE_RESET_SECONDS elapsed."""
        if time.time() - self._cycle_start_time > CYCLE_RESET_SECONDS:
            self._fixes_this_cycle = 0
            self._cycle_start_time = time.time()
            logger.info(
                f"[AutoFix] Cycle reset — _fixes_this_cycle cleared after "
                f"{CYCLE_RESET_SECONDS}s"
            )

    def record_cooldown(self, bug_or_key: Any, reason: str = "fixed", cooldown_seconds: float | None = None) -> None:
        """Record a bug in cooldown to prevent repeated attempts on the same failure or fix."""
        if isinstance(bug_or_key, str):
            bug_key = bug_or_key
        else:
            bug_key = f"{getattr(bug_or_key, 'file', '')}:{getattr(bug_or_key, 'line', '')}:{getattr(bug_or_key, 'bug_type', '')}"
        now = time.time()
        self._recent_fixes[bug_key] = now
        logger.info("[AutoFix] Recorded cooldown for %s (reason: %s)", bug_key, reason)

    def is_in_cooldown(self, bug_or_key: Any, cooldown_seconds: float | None = None) -> tuple[bool, str]:
        """Check if a bug is within its cooldown window (for both success and failure backoff)."""
        if isinstance(bug_or_key, str):
            bug_key = bug_or_key
        else:
            bug_key = f"{getattr(bug_or_key, 'file', '')}:{getattr(bug_or_key, 'line', '')}:{getattr(bug_or_key, 'bug_type', '')}"
        now = time.time()
        limit = cooldown_seconds or float(os.environ.get("SCP_AUTOFIX_FAILURE_COOLDOWN_SEC", str(COOLDOWN_SAME_BUG_SECONDS)))
        if bug_key in self._recent_fixes:
            elapsed = now - self._recent_fixes[bug_key]
            if elapsed < limit:
                return True, f"cooldown — bug processed recently ({int(limit - elapsed)}s remaining)"
        return False, ""

    def process_bug(self, bug: BugReport) -> dict:
        """Process a detected bug. Returns action taken."""
        self._check_cycle_reset()

        classified = self.classifier.classify(
            file=bug.file, line=bug.line,
            bug_type=bug.bug_type,
            description=bug.description,
            suggested_fix=bug.suggested_fix,
            in_attack_mode=self.in_attack_mode,
            tier_hint=bug.tier if bug.tier != BugTier.TIER_1_AUTO_FIX else None,
        )
        for attr in ("_last_refinement_round", "_refinement_round"):
            if hasattr(bug, attr):
                setattr(classified, attr, getattr(bug, attr))

        # [Idea 5 — VERIFY-THEN-PROMOTE] Adjust tier based on historical streak evidence.
        try:
            from scp.autofix.monitor import get_monitor as _get_monitor
            _adj = _get_monitor().get_tier_adjustment(bug.bug_type)
            if _adj != 0 and classified.tier in (BugTier.TIER_1_AUTO_FIX, BugTier.TIER_2_AUTO_FIX_LOG):
                _new_tier_val = max(1, min(2, int(classified.tier) + _adj))
                _new_tier = BugTier(_new_tier_val)
                if _new_tier != classified.tier:
                    logger.info(
                        f"[Idea5] tier adjusted {bug.bug_type}: "
                        f"Tier {int(classified.tier)} → Tier {int(_new_tier)} "
                        f"(streak evidence, adj={_adj:+d})"
                    )
                    classified.tier = _new_tier
        except Exception as _idea5_err:
            logger.debug(f"[Idea5] tier adjustment skipped (fail-open): {_idea5_err}", exc_info=True)

        # Check cooldown (don't re-fix same bug)
        in_cd, cd_reason = self.is_in_cooldown(bug)
        if in_cd:
            return {
                "action": "skipped",
                "tier": int(classified.tier),
                "reason": cd_reason,
            }

        # Act based on tier
        if classified.tier == BugTier.TIER_1_AUTO_FIX:
            return self._auto_fix(classified, report=False)

        elif classified.tier == BugTier.TIER_2_AUTO_FIX_LOG:
            return self._auto_fix(classified, report=True)

        elif classified.tier == BugTier.TIER_3_PERMISSION:
            return self._request_permission(classified)

        elif classified.tier == BugTier.TIER_4_ATTACK_MODE:
            self._tier4_timestamps = [t for t in self._tier4_timestamps if now - t < 3600]
            if len(self._tier4_timestamps) >= MAX_TIER4_PER_HOUR:
                return {
                    "action": "skipped",
                    "tier": 4,
                    "reason": f"rate limit — {MAX_TIER4_PER_HOUR} Tier-4 fixes/hour exceeded",
                }
            self._tier4_timestamps.append(now)
            return self._auto_fix(classified, report=True, attack_mode=True)

        return {"action": "skipped", "tier": 0, "reason": "unknown tier"}

    def _audit_v91(self, event: str, payload: dict) -> None:
        try:
            _entry = {
                "ts": time.time(),
                "engine": "autofix",
                "event": event,
                "payload": payload,
            }
            _audit_path = self.data_dir / "v91_upgrade_audit.jsonl"
            with open(_audit_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(_entry, ensure_ascii=False) + "\n")
        except Exception as _audit_err:
            logger.debug(f" audit log error (fail-open): {_audit_err}", exc_info=True)

    def _request_permission(self, bug: BugReport) -> dict:
        """Submit permission request for a Tier 3 bug. Does NOT fix."""
        if self._should_auto_approve_tier3(bug):
            logger.info(
                f"[TIER3-AUTO] Auto-approving Tier-3 bug {bug.file}:{bug.line} "
                f"({bug.bug_type}) -- SCP_AUTO_APPROVE_TIER3=1"
            )
            return self._auto_approve_tier3(bug)

        request_id = self.permission_gate.request_permission(bug)
        self._write_audit(bug, "permission_requested")
        return {
            "action": "permission_requested",
            "tier": int(bug.tier),
            "request_id": request_id,
            "reason": "logic bug — human approval required",
        }

    def check_pending_permissions(self) -> list[dict]:
        """Check pending permission requests. Returns list of approved ones ready to fix."""
        ready = []
        for req in self.permission_gate.list_pending():
            status = self.permission_gate.check_permission(req.request_id)
            if status == "approved":
                ready.append({
                    "request_id": req.request_id,
                    "file": req.file,
                    "line": req.line,
                    "fix": req.suggested_fix,
                    "approved_by": req.decided_by,
                })
        return ready

    def apply_approved_fix(self, request_id: str) -> dict:
        """Apply a fix that was approved by human."""
        req = self.permission_gate._pending.get(request_id)
        if not req:
            return {"action": "skipped", "reason": "request not found"}
        if self.permission_gate.check_permission(request_id) != "approved":
            return {"action": "skipped", "reason": "not approved"}

        bug = BugReport(
            file=req.file, line=req.line,
            bug_type=req.bug_type,
            description=req.description,
            suggested_fix=req.suggested_fix,
            tier=BugTier.TIER_3_PERMISSION,
            affects_logic=True,
        )
        result = self._auto_fix(bug, report=True)
        if result.get("action") == "fixed":
            self._write_audit(bug, "fixed_after_permission",
                              extra={"request_id": request_id, "approved_by": req.decided_by})
        return result

    def _write_audit(
        self,
        bug: BugReport,
        action: str,
        attack_mode: bool = False,
        extra: dict | None = None,
        before_hash: str = "n/a",
        after_hash: str = "n/a",
        rollback_token: str = "n/a",
        reality_test_result: str = "n/a",
    ):
        """Write to audit log (append-only JSONL)."""
        try:
            from scp.autofix.audit_log import write_audit_entry as _write_entry
        except ImportError as _audit_imp_err:
            logger.warning(
                f"[4-b-012] audit_log module unavailable, falling back to "
                f"legacy write (no DNA #8 schema enforcement): {_audit_imp_err}"
            )
            entry = {
                "timestamp": time.time(),
                "file": bug.file,
                "line": bug.line,
                "bug_type": bug.bug_type,
                "tier": int(bug.tier),
                "action": action,
                "attack_mode": attack_mode,
                "description": bug.description[:200],
                "before_hash": before_hash or "n/a",
                "after_hash": after_hash or "n/a",
                "rollback_token": rollback_token or "n/a",
                "reality_test_result": reality_test_result or "n/a",
            }
            if extra:
                entry.update(extra)
            try:
                with open(self.audit_log, "a", encoding="utf-8") as f:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            except Exception as e:
                logger.warning(f"Silent except: {e}", exc_info=True)
            return

        finding_id = f"{bug.file}:{bug.line}"
        message = (extra or {}).get("description") or bug.description[:200]
        ok = _write_entry(
            log_path=self.audit_log,
            finding_id=finding_id,
            tier=int(bug.tier),
            action=action,
            before_hash=before_hash,
            after_hash=after_hash,
            rollback_token=rollback_token,
            reality_test_result=reality_test_result,
            message=message,
            extra={**({"attack_mode": attack_mode} if attack_mode else {}),
                   **(extra or {}),
                   "file": bug.file, "line": bug.line,
                   "bug_type": bug.bug_type},
        )
        if not ok:
            logger.warning(
                f"[4-b-012] audit log write REJECTED for {finding_id} "
                f"tier={int(bug.tier)} action={action!r} — see prior ERROR log"
            )

    def set_attack_mode(self, enabled: bool):
        """Toggle attack mode. When enabled, Tier 4 restraints auto-apply."""
        self.in_attack_mode = enabled
        logger.info(f"[AutoFix] Attack mode {'ENABLED' if enabled else 'DISABLED'}")

    def stats(self) -> dict:
        """Return stats for monitoring."""
        now = time.time()
        tier3_auto_remaining = max(
            0, MAX_TIER3_AUTO_PER_HOUR - len(self._tier3_auto_timestamps)
        )
        tier3_auto_expires_in = 0
        if self._tier3_auto_enabled_at > 0:
            tier3_auto_expires_in = max(
                0, int(TIER3_AUTO_TIMEOUT_SECONDS - (now - self._tier3_auto_enabled_at))
            )
        return {
            "in_attack_mode": self.in_attack_mode,
            "fixes_this_cycle": self._fixes_this_cycle,
            "tier4_last_hour": len(self._tier4_timestamps),
            "pending_permissions": len(self.permission_gate.list_pending()),
            "recent_fixes": len(self._recent_fixes),
            "cycle_started_at": self._cycle_start_time,
            "cycle_resets_in": max(
                0, int(CYCLE_RESET_SECONDS - (time.time() - self._cycle_start_time))
            ),
            "tier3_auto_enabled": os.environ.get("SCP_AUTO_APPROVE_TIER3", "0") == "1",
            "tier3_auto_used_this_hour": len(self._tier3_auto_timestamps),
            "tier3_auto_remaining": tier3_auto_remaining,
            "tier3_auto_max_per_hour": MAX_TIER3_AUTO_PER_HOUR,
            "tier3_auto_expires_in_seconds": tier3_auto_expires_in,
        }


_autofix_engine: AutoFixEngine | None = None
_autofix_lock = threading.Lock()


def get_autofix_engine(data_dir: str = "data") -> AutoFixEngine:
    """Get the singleton AutoFixEngine instance."""
    global _autofix_engine
    if _autofix_engine is None:
        with _autofix_lock:
            if _autofix_engine is None:
                _autofix_engine = AutoFixEngine(data_dir=data_dir)
                logger.info("[AutoFix] Singleton engine initialized")
    return _autofix_engine


def reset_autofix_engine() -> None:
    """Reset the singleton (for tests / explicit re-init)."""
    global _autofix_engine
    with _autofix_lock:
        _autofix_engine = None


try:
    from scp.autofix.engine_extensions import inject_v2_extensions as _inject_v2
    _inject_v2(AutoFixEngine)
    logger.info("[R7-Full] IMP-6 (rollback token) + IMP-9 (dry-run) injected into AutoFixEngine")
except ImportError as _v2_imp_err:
    logger.warning(
        f"[R7-Full] engine_extensions.py unavailable — IMP-6/IMP-9 disabled: {_v2_imp_err}"
    )
except Exception as _v2_inj_err:  # noqa: BLE001
    logger.warning(
        f"[R7-Full] v2 extension injection failed (non-fatal): {_v2_inj_err}"
    )

__all__ = [
    "BugClassifier",
    "BugReport",
    "BugTier",
    "PermissionGate",
    "MAX_FIXES_PER_CYCLE",
    "MAX_TIER4_PER_HOUR",
    "COOLDOWN_SAME_BUG_SECONDS",
    "CYCLE_RESET_SECONDS",
    "MAX_TIER3_AUTO_PER_HOUR",
    "TIER3_AUTO_TIMEOUT_SECONDS",
    "TIER3_AUTO_AUDIT_LOG",
    "Tier3AutoConfig",
    "Tier3Mixin",
    "_tier3_config",
    "get_tier3_config",
    "AutoFixMixin",
    "VerifyMixin",
    "AutoFixEngine",
    "get_autofix_engine",
    "reset_autofix_engine",
]
