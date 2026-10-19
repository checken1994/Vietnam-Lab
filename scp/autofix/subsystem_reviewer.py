"""Subsystem Independent Reviewer for AutoFix Autonomy.

Fulfills Requirement R1 and Non-Negotiable Rule #7 (Distributed Autonomy):
- Replaces manual "HUMAN review required" bottlenecks with autonomous subsystem review.
- Under CapabilityLevel.FULL_PRODUCTION, autonomously inspects pending fixes from pending_fixes/.
- Parses target files and suggested patches.
- Executes shadow tests via shadow_apply_and_compare.
- Validates safety against DriftGuard and WHY-Gate.
- If verification passes: automatically merges the patch, removes the pending file,
  and logs ACTION: AUTO_MERGED_BY_SUBSYSTEM_REVIEWER.
- If verification fails: feeds the rejection critique into the multi-turn iterative
  refinement loop (up to 3 rounds) or quarantines if all rounds are exhausted.
- Provides queue deduplication and queue pruning facilities.
"""
from __future__ import annotations

import ast
import hashlib
import logging
import os
import re
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from scp.autofix.runner_phases.shadow_canary import (
    CanarySuite,
    ShadowFix,
    default_canary_suite,
    shadow_apply_and_compare,
)
from scp.autofix.subsystem_reviewer_helpers import (
    SCP_ROOT,
    _resolve_target_path,
    apply_patch_to_text,
    is_duplicate_pending_fix,
    parse_pending_fix_file,
    parse_proposal_file,
    prune_pending_fixes,
)
from scp.core.pending_fix_review import ensure_review_guide
from scp.governance import DriftDecision, DriftGuard
from scp.meta.capability_levels import CapabilityLevel, get_capability_manager
from scp.meta.why_gate import get_why_gate

logger = logging.getLogger("scp.autofix.subsystem_reviewer")


@dataclass
class ReviewResult:
    """Result of an autonomous review on a patch candidate."""
    target_file: Path | str
    pending_file: Path | str | None
    approved: bool
    action: str  # "AUTO_MERGED_BY_SUBSYSTEM_REVIEWER", "QUARANTINED", "REJECTED", "SKIPPED_NOT_FULL_PRODUCTION"
    reasons: list[str] = field(default_factory=list)
    critique: str = ""
    refinement_rounds: int = 0


@dataclass
class ReviewSummary:
    """Summary of batch review on pending fixes."""
    total_inspected: int = 0
    approved_count: int = 0
    rejected_count: int = 0
    quarantined_count: int = 0
    pruned_count: int = 0
    results: list[ReviewResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_inspected": self.total_inspected,
            "approved_count": self.approved_count,
            "rejected_count": self.rejected_count,
            "quarantined_count": self.quarantined_count,
            "pruned_count": self.pruned_count,
            "results": [
                {
                    "target_file": str(r.target_file),
                    "pending_file": str(r.pending_file) if r.pending_file else None,
                    "approved": r.approved,
                    "action": r.action,
                    "reasons": r.reasons,
                    "critique": r.critique,
                    "refinement_rounds": r.refinement_rounds,
                }
                for r in self.results
            ],
        }





class SubsystemIndependentReviewer:
    """Autonomously inspects, verifies, and merges pending fixes without human bottleneck."""

    def __init__(
        self,
        repo_root: Path | str | None = None,
        pending_dir: Path | str | None = None,
        proposals_dir: Path | str | None = None,
        why_gate: Any = None,
    ):
        self.repo_root = Path(repo_root or SCP_ROOT).resolve()
        self.pending_dir = Path(pending_dir or (self.repo_root / "pending_fixes")).resolve()
        self.proposals_dir = Path(proposals_dir or (self.repo_root / "data" / "governance" / "proposals")).resolve()
        self._drift_guard: DriftGuard | None = None
        self.why_gate = why_gate

    def _get_why_gate(self):
        if self.why_gate is not None:
            return self.why_gate
        from scp.meta.why_gate import get_why_gate

        return get_why_gate(data_dir=str(self.repo_root / "data"))

    def _get_drift_guard(self) -> DriftGuard:
        if self._drift_guard is None:
            spec_file = self.repo_root / "spec" / "protected_invariants.yaml"
            if not spec_file.is_file():
                # Fallback to repository root spec if working in isolated test repo
                fallback = SCP_ROOT / "spec" / "protected_invariants.yaml"
                spec_file = fallback if fallback.is_file() else spec_file
            self._drift_guard = DriftGuard(spec_file)
        return self._drift_guard

    def _relative_repo_path(self, path: Path) -> str:
        try:
            return str(path.resolve().relative_to(self.repo_root.resolve())).replace("\\", "/")
        except ValueError:
            return str(path).replace("\\", "/")

    def _get_capability_level(self) -> CapabilityLevel:
        cap_mgr = get_capability_manager()
        env_val = os.environ.get("SCP_CAPABILITY_LEVEL", "").strip().upper()
        if env_val:
            try:
                return CapabilityLevel[env_val]
            except KeyError:
                return CapabilityLevel.ANALYSIS_ONLY
        return cap_mgr.get_current_level()

    def validate_patch_syntax_and_apply(
        self,
        target_path: Path,
        patch: str,
    ) -> tuple[bool, str, str | None]:
        """Validate patch and simulate in-memory application on target file."""
        if not target_path.is_file():
            return False, "", f"Target file does not exist: {target_path}"
        try:
            original = target_path.read_text(encoding="utf-8")
        except Exception as exc:
            return False, "", f"Cannot read target file {target_path.name}: {exc}"

        return apply_patch_to_text(original, patch, filename=target_path.name)

    def verify_safety_and_shadow(
        self,
        target_path: Path,
        patch: str,
        original_code: str,
        patched_code: str,
        canary_suite: CanarySuite | None = None,
    ) -> tuple[bool, str | None]:
        """Validate safety against WHY-Gate, DriftGuard, and Shadow Canary test."""
        # Step 1: Check WHY-Gate
        try:
            why_gate = self._get_why_gate()
            why_res = why_gate.gate(
                action_type="subsystem_review",
                action_desc=f"Subsystem review patch for {target_path.name}",
                context=patch[:500],
            )
            if why_res.blocked:
                return False, f"WHY_GATE_BLOCKED: {why_res.falsification_reason}"
        except Exception as exc:
            logger.debug("why_gate check in subsystem reviewer: %s", exc)

        # Step 2: Check DriftGuard
        try:
            guard = self._get_drift_guard()
            rel_path = self._relative_repo_path(target_path)
            drift_res = guard.inspect_change(
                path=rel_path,
                old_text=original_code,
                new_text=patched_code,
                governance_authorized=False,
            )
            if drift_res.decision is not DriftDecision.ALLOW:
                return False, f"DRIFT_GUARD_BLOCKED: {'; '.join(drift_res.reasons)}"
        except Exception as exc:
            logger.warning("DriftGuard check in subsystem reviewer failed: %s", exc, exc_info=True)
            return False, f"DRIFT_GUARD_ERROR: {exc}"

        # Step 3: Run Shadow Canary test
        try:
            suite = canary_suite or default_canary_suite()
            fix_obj = ShadowFix(
                original_source=original_code,
                patched_source=patched_code,
                fix_id=f"subsystem_{target_path.name}",
            )
            canary_res = shadow_apply_and_compare(str(target_path), fix_obj, suite)
            if not canary_res.passed:
                reason = canary_res.reason or "; ".join(canary_res.diffs)
                return False, f"SHADOW_CANARY_FAILED: {reason}"
        except Exception as exc:
            logger.warning("shadow_apply_and_compare check in subsystem reviewer failed: %s", exc, exc_info=True)
            return False, f"SHADOW_CANARY_ERROR: {exc}"

        return True, None

    def _apply_to_disk(
        self,
        target_path: Path,
        original_code: str,
        patched_code: str,
    ) -> tuple[bool, str | None]:
        """Atomically commit verified patch to disk with postflight DriftGuard check."""
        shadow_mgr = None
        tx_id = None
        try:
            from scp.autofix.shadow_snapshot import get_shadow_snapshot_manager
            shadow_mgr = get_shadow_snapshot_manager(shadow_dir=self.repo_root / "data" / "shadow")
            tx_id = shadow_mgr.begin([target_path], bug_id=f"subsystem_review_{target_path.name}")
        except Exception as exc:
            logger.debug("shadow_mgr begin skipped: %s", exc)

        try:
            target_path.write_text(patched_code, encoding="utf-8")
            landed = target_path.read_text(encoding="utf-8")

            # Postflight DriftGuard check
            post = self._get_drift_guard().inspect_change(
                path=self._relative_repo_path(target_path),
                old_text=original_code,
                new_text=landed,
                governance_authorized=False,
            )
            if post.decision is not DriftDecision.ALLOW:
                target_path.write_text(original_code, encoding="utf-8")
                if tx_id and shadow_mgr:
                    shadow_mgr.rollback(tx_id, reason="postflight drift")
                return False, f"DRIFT_GUARD_POSTFLIGHT_BLOCKED: {'; '.join(post.reasons)}"

            if tx_id and shadow_mgr:
                shadow_mgr.commit(tx_id)
            return True, None
        except Exception as exc:
            target_path.write_text(original_code, encoding="utf-8")
            if tx_id and shadow_mgr:
                shadow_mgr.rollback(tx_id, reason=str(exc))
            return False, str(exc)

    def review_fix(
        self,
        target_path: Path | str,
        patch_text: str,
        pending_file: Path | str | None = None,
        capability_level: CapabilityLevel | None = None,
        refiner_callback: Callable[[Path, str, str], str | None] | None = None,
        max_refinement_rounds: int = 3,
        canary_suite: CanarySuite | None = None,
    ) -> ReviewResult:
        """Autonomously review a patch, optionally looping through iterative refinement."""
        root = self.repo_root.resolve()
        try:
            target = Path(target_path).resolve()
        except (TypeError, ValueError, OSError):
            return ReviewResult(
                target_file=target_path,
                pending_file=pending_file,
                approved=False,
                action="QUARANTINED",
                critique="Invalid target path",
                reasons=["Invalid target path"],
            )

        if any(ch in str(target_path) for ch in ("*", "?", "[", "]")):
            return ReviewResult(
                target_file=target_path,
                pending_file=pending_file,
                approved=False,
                action="QUARANTINED",
                critique="Wildcard pattern not allowed in target path",
                reasons=["Wildcard pattern not allowed in target path"],
            )

        from scp.autofix.path_guard import ensure_within

        safe_target = ensure_within(root, target)
        if safe_target is None or safe_target == root:
            return ReviewResult(
                target_file=target,
                pending_file=pending_file,
                approved=False,
                action="QUARANTINED",
                critique="Path outside repository root: confinement violation",
                reasons=["Path outside repository root: confinement violation"],
            )

        if not safe_target.is_file():
            return ReviewResult(
                target_file=safe_target,
                pending_file=pending_file,
                approved=False,
                action="REJECTED",
                critique="Target file does not exist",
                reasons=["Target file does not exist"],
            )

        if capability_level is None:
            capability_level = self._get_capability_level()
        elif isinstance(capability_level, str):
            try:
                capability_level = CapabilityLevel[capability_level.strip().upper()]
            except KeyError:
                capability_level = CapabilityLevel.ANALYSIS_ONLY

        if capability_level is not CapabilityLevel.FULL_PRODUCTION and capability_level != CapabilityLevel.FULL_PRODUCTION:
            return ReviewResult(
                target_file=safe_target,
                pending_file=pending_file,
                approved=False,
                action="SKIPPED_NOT_FULL_PRODUCTION",
                critique="Subsystem review auto-merge is restricted to FULL_PRODUCTION capability level",
                reasons=["Capability level is not FULL_PRODUCTION"],
            )

        if patch_text is None or not isinstance(patch_text, str) or not patch_text.strip():
            return ReviewResult(
                target_file=safe_target,
                pending_file=pending_file,
                approved=False,
                action="QUARANTINED",
                critique="Patch text must be a non-empty string",
                reasons=["Patch text must be a non-empty string"],
            )

        current_patch = patch_text
        critique = ""
        original_code = safe_target.read_text(encoding="utf-8")

        for round_idx in range(1, max_refinement_rounds + 1):
            ok, patched_code, syntax_err = self.validate_patch_syntax_and_apply(safe_target, current_patch)
            if not ok:
                critique = syntax_err or "Syntax or patch parsing failed"
            else:
                ok, safety_err = self.verify_safety_and_shadow(
                    safe_target,
                    current_patch,
                    original_code,
                    patched_code,
                    canary_suite=canary_suite,
                )
                if not ok:
                    critique = safety_err or "Safety verification failed"

            if ok:
                merge_ok, merge_err = self._apply_to_disk(safe_target, original_code, patched_code)
                if merge_ok:
                    if pending_file and Path(pending_file).is_file():
                        p_path = Path(pending_file)
                        if p_path.suffix == ".md":
                            try:
                                content = p_path.read_text(encoding="utf-8", errors="replace")
                                res_text = (
                                    f"\n\n## Resolution (Autonomous Subsystem Reviewer)\n"
                                    f"- **Status:** RESOLVED_BY_MAKER_CHECKER\n"
                                    f"- **Action:** AUTO_MERGED_BY_SUBSYSTEM_REVIEWER\n"
                                    f"- **Timestamp:** {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n"
                                )
                                p_path.write_text(content + res_text, encoding="utf-8")
                            except Exception as e:
                                logger.debug("Failed to update proposal file %s: %s", pending_file, e)
                        else:
                            try:
                                p_path.unlink()
                            except OSError as e:
                                logger.debug("Failed to unlink merged pending file %s: %s", pending_file, e)
                    logger.info("ACTION: AUTO_MERGED_BY_SUBSYSTEM_REVIEWER: %s", safe_target.name)
                    return ReviewResult(
                        target_file=safe_target,
                        pending_file=pending_file,
                        approved=True,
                        action="AUTO_MERGED_BY_SUBSYSTEM_REVIEWER",
                        refinement_rounds=round_idx,
                    )
                critique = f"MERGE_FAILED: {merge_err}"

            logger.info(
                "SubsystemReviewer round %d rejected patch for %s: %s",
                round_idx,
                safe_target.name,
                critique,
            )

            if refiner_callback and round_idx < max_refinement_rounds:
                try:
                    refined_patch = refiner_callback(safe_target, current_patch, critique)
                    if refined_patch:
                        current_patch = refined_patch
                        continue
                except Exception as exc:
                    logger.warning(
                        "Refiner callback raised exception on round %d for %s: %s",
                        round_idx,
                        safe_target.name,
                        exc,
                        exc_info=True,
                    )
                    critique = f"REFINER_CALLBACK_ERROR: {exc}"
                    return ReviewResult(
                        target_file=safe_target,
                        pending_file=pending_file,
                        approved=False,
                        action="QUARANTINED",
                        critique=critique,
                        reasons=[critique],
                        refinement_rounds=round_idx,
                    )
            break

        # Record rejection resolution on proposal file if applicable
        if pending_file and Path(pending_file).is_file() and Path(pending_file).suffix == ".md":
            try:
                p_path = Path(pending_file)
                content = p_path.read_text(encoding="utf-8", errors="replace")
                if "## Resolution" not in content:
                    rej_text = (
                        f"\n\n## Resolution (Autonomous Subsystem Reviewer)\n"
                        f"- **Status:** REJECTED_BY_CHECKER\n"
                        f"- **Critique:** {critique}\n"
                        f"- **Timestamp:** {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n"
                    )
                    p_path.write_text(content + rej_text, encoding="utf-8")
            except Exception as e:
                logger.debug("Failed to record rejection in proposal %s: %s", pending_file, e)

        # Record failure into FailureLedger (Epistemic Negative Knowledge)
        try:
            from scp.epistemic.failure_ledger import get_failure_ledger
            get_failure_ledger().record_failure(
                failure_type="MAKER_CHECKER_REJECTION",
                target_module=self._relative_repo_path(safe_target),
                verbatim_critique=critique or "Safety verification failed in SubsystemIndependentReviewer",
                falsified_patch_diff=patch_text,
            )
        except Exception as _fl_err:
            logger.debug("Could not record rejection in FailureLedger: %s", _fl_err)

        return ReviewResult(
            target_file=safe_target,
            pending_file=pending_file,
            approved=False,
            action="QUARANTINED",
            critique=critique or "Safety verification failed",
            reasons=[critique] if critique else [],
            refinement_rounds=round_idx,
        )

    # API Alias: review_patch = review_fix
    review_patch = review_fix

    def prune_queue(self, pending_dir: Path | str | None = None) -> int:
        """Prune duplicate files in pending_fixes/ queue."""
        p_dir = Path(pending_dir or self.pending_dir)
        return prune_pending_fixes(p_dir)

    def review_pending_fixes(
        self,
        pending_dir: Path | str | None = None,
        proposals_dir: Path | str | None = None,
        capability_level: CapabilityLevel | None = None,
        auto_prune: bool = True,
        refiner_callback: Callable[[Path, str, str], str | None] | None = None,
    ) -> ReviewSummary:
        """Autonomously inspect pending fixes in pending_dir and governance proposals in proposals_dir."""
        p_dir = Path(pending_dir or self.pending_dir).resolve()
        prop_dir = Path(proposals_dir or self.proposals_dir).resolve()
        summary = ReviewSummary()

        if auto_prune and p_dir.is_dir():
            summary.pruned_count = self.prune_queue(p_dir)

        # 1. Process pending_fixes/*.py
        if p_dir.is_dir():
            pending_files = sorted(p_dir.glob("*.py"))
            for p_file in pending_files:
                target_path, patch = parse_pending_fix_file(p_file, repo_root=self.repo_root)
                if not target_path or not patch:
                    summary.total_inspected += 1
                    summary.rejected_count += 1
                    summary.results.append(
                        ReviewResult(
                            target_file=str(target_path or p_file),
                            pending_file=p_file,
                            approved=False,
                            action="REJECTED",
                            critique="Unparseable pending fix file or missing target",
                            reasons=["Unparseable pending fix file or missing target"],
                        )
                    )
                    continue

                summary.total_inspected += 1
                result = self.review_fix(
                    target_path=target_path,
                    patch_text=patch,
                    pending_file=p_file,
                    capability_level=capability_level,
                    refiner_callback=refiner_callback,
                )
                summary.results.append(result)
                if result.approved:
                    summary.approved_count += 1
                elif result.action == "QUARANTINED":
                    summary.quarantined_count += 1
                else:
                    summary.rejected_count += 1

        # 2. Process data/governance/proposals/*.md (meta_repair_*.md or un-resolved proposals)
        if prop_dir.is_dir():
            proposal_files = sorted(prop_dir.glob("meta_repair_*.md")) + sorted(prop_dir.glob("PROP-*.md"))
            for prop_file in proposal_files:
                target_path, patch = parse_proposal_file(prop_file, repo_root=self.repo_root)
                if not target_path or not patch:
                    continue  # Skip proposals without direct patches or already resolved

                summary.total_inspected += 1
                result = self.review_fix(
                    target_path=target_path,
                    patch_text=patch,
                    pending_file=prop_file,
                    capability_level=capability_level,
                    refiner_callback=refiner_callback,
                )
                summary.results.append(result)
                if result.approved:
                    summary.approved_count += 1
                elif result.action == "QUARANTINED":
                    summary.quarantined_count += 1
                else:
                    summary.rejected_count += 1

        return summary

    # API Alias: batch_review = review_pending_fixes
    batch_review = review_pending_fixes


_DEFAULT_REVIEWER: SubsystemIndependentReviewer | None = None


def get_subsystem_reviewer(repo_root: Path | str | None = None) -> SubsystemIndependentReviewer:
    """Get singleton or fresh instance of SubsystemIndependentReviewer."""
    global _DEFAULT_REVIEWER
    if repo_root is not None:
        return SubsystemIndependentReviewer(repo_root=repo_root)
    if _DEFAULT_REVIEWER is None:
        _DEFAULT_REVIEWER = SubsystemIndependentReviewer()
    return _DEFAULT_REVIEWER


__all__ = [
    "ReviewResult",
    "ReviewSummary",
    "SubsystemIndependentReviewer",
    "_resolve_target_path",
    "apply_patch_to_text",
    "get_subsystem_reviewer",
    "is_duplicate_pending_fix",
    "parse_pending_fix_file",
    "parse_proposal_file",
    "prune_pending_fixes",
]

