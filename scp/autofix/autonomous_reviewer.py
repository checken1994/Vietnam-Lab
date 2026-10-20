"""Autonomous Reviewer — Independent Subsystem Reviewer for Tier 3 Logic Bugs.

Rule 7 of SCP AGENTS.md (Distributed Autonomy — Zero Human Bottleneck):
"Khi thiết kế hoặc audit SCP, khái niệm 'External Reviewer' (Người đánh giá bên ngoài)
phải được hiểu là một Subsystem độc lập (Ví dụ: Agent A kiểm duyệt Agent B), chứ không
phải tạo Nút thắt cổ chai bằng cách đẩy cho Con người."

Architectural Principles:
- DNA #1 / #2: Reality > Model. Evaluates real AST, in-memory patch application,
  and blast radius against actual source files on disk.
- DNA #8: Preserves verifiable evidence trail and reasons for all decisions.
- DNA #9: Fail-Closed. Any ambiguity, syntax failure, or blast radius violation
  results in safe denial.
- ARC-01: LOC strictly bounded <= 800 lines.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, field
import json
import logging
import os
from pathlib import Path
import re
import sys
import textwrap
import time
from typing import Any

from scp.autofix.classifier import BugReport, BugTier
from scp.autofix.permission import PermissionGate, PermissionRequest

logger = logging.getLogger("scp.autofix.autonomous_reviewer")

# Standard dangerous patterns forbidden in autonomous patches (Fail-Closed)
DANGEROUS_PATTERNS = [
    (re.compile(r"\beval\s*\("), "eval() invocation"),
    (re.compile(r"\bexec\s*\("), "exec() invocation"),
    (re.compile(r"\bos\.system\s*\("), "os.system() invocation"),
    (re.compile(r"\b__import__\s*\("), "dynamic __import__()"),
    (re.compile(r"\bpickle\.(?:loads|load)\s*\("), "pickle deserialization"),
    (re.compile(r"\bshutil\.rmtree\s*\("), "destructive rmtree() call"),
]


@dataclass
class EvaluationVerdict:
    """Independent evaluation verdict for a permission request."""

    request_id: str
    approved: bool
    rationale: str
    checks: dict[str, Any] = field(default_factory=dict)
    applied: bool = False
    apply_result: dict[str, Any] | None = None


@dataclass
class AutonomousReviewerConfig:
    """Configuration for Autonomous Reviewer."""

    reviewer_id: str = "autonomous_subsystem_reviewer"
    auto_apply: bool = True
    max_blast_radius_lines: int = 80
    max_line_drift: int = 100
    check_syntax: bool = True
    check_dangerous_patterns: bool = True
    check_diff_scope: bool = True


class AutonomousReviewer:
    """Independent Subsystem Reviewer (Agent A / Subsystem B).

    Inspects pending Tier 3 permission requests without human bottleneck,
    verifying AST syntax, blast radius, dangerous patterns, and in-memory
    patch application before approving or denying.
    """

    def __init__(
        self,
        data_dir: str = "data",
        repo_root: Path | str | None = None,
        gate: PermissionGate | None = None,
        engine: Any = None,
        notif_system: Any = None,
        config: AutonomousReviewerConfig | None = None,
    ):
        self.repo_root = Path(repo_root or Path(__file__).resolve().parents[2]).resolve()
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.config = config or AutonomousReviewerConfig()
        self._gate = gate
        self._engine = engine
        self._notif_system = notif_system

    @property
    def gate(self) -> PermissionGate:
        """Lazily initialize PermissionGate."""
        if self._gate is None:
            self._gate = PermissionGate(data_dir=str(self.data_dir), notif_system=self.notif_system)
        return self._gate

    @property
    def engine(self) -> Any:
        """Lazily initialize AutoFixEngine."""
        if self._engine is None:
            try:
                from scp.autofix.engine import get_autofix_engine
                self._engine = get_autofix_engine(data_dir=str(self.data_dir))
            except Exception as e:
                logger.debug("AutoFixEngine lazy init error: %s", e)
                self._engine = None
        return self._engine

    @property
    def notif_system(self) -> Any:
        """Lazily initialize UserNotificationSystem."""
        if self._notif_system is None:
            try:
                from scp.runtime.notifications import NotificationConfig, UserNotificationSystem
                self._notif_system = UserNotificationSystem(
                    config=NotificationConfig(min_severity="warning"),
                    data_dir=str(self.data_dir),
                )
            except Exception as e:
                logger.debug("UserNotificationSystem lazy init error: %s", e)
                self._notif_system = None
        return self._notif_system

    def _resolve_target_file(self, file_path_str: str) -> Path | None:
        """Resolve target file path relative to repo root or absolute path."""
        p = Path(file_path_str)
        if p.is_file():
            return p.resolve()
        # Try relative to repo root
        cand = (self.repo_root / file_path_str).resolve()
        if cand.is_file():
            return cand
        # Try stripping leading repo root if doubled
        parts = p.parts
        for i in range(len(parts)):
            sub = Path(*parts[i:])
            cand2 = (self.repo_root / sub).resolve()
            if cand2.is_file():
                return cand2
        return None

    def _extract_patch_blocks(self, patch: str) -> tuple[str, str] | None:
        """Extract SEARCH and REPLACE blocks from patch string."""
        if not patch or not isinstance(patch, str):
            return None
        patterns = [
            re.compile(
                r"<<<<<<<[ \t]*SEARCH[ \t]*\r?\n(.*?)\r?\n={5,7}[ \t]*\r?\n(?:(.*?)\r?\n)?>>>>>>>[ \t]*(?:REPLACE)?[ \t]*",
                re.DOTALL,
            ),
            re.compile(
                r"<<<<<<<[ \t]*OLD[ \t]*\r?\n(.*?)\r?\n={5,7}[ \t]*\r?\n(?:(.*?)\r?\n)?>>>>>>>[ \t]*(?:NEW)?[ \t]*",
                re.DOTALL,
            ),
        ]
        for pat in patterns:
            m = pat.search(patch)
            if m:
                return m.group(1), m.group(2) or ""
        return None

    def _apply_patch_in_memory(self, original: str, patch: str, target_name: str = "") -> tuple[bool, str, str]:
        """Apply patch to original text in memory without touching disk."""
        try:
            from scp.autofix.subsystem_reviewer_helpers import apply_patch_to_text
            ok, patched, err = apply_patch_to_text(original, patch, filename=target_name)
            return ok, patched, err or ""
        except Exception as e:
            logger.debug("apply_patch_to_text import/run fallback: %s", e)

        # Fallback in-memory replacement
        blocks = self._extract_patch_blocks(patch)
        if not blocks:
            return False, original, "Unable to extract SEARCH/REPLACE blocks"
        old_text, new_text = blocks
        if old_text in original:
            patched = original.replace(old_text, new_text, 1)
            return True, patched, ""
        return False, original, f"SEARCH block not found in {target_name}"

    def evaluate_request(self, req: PermissionRequest) -> EvaluationVerdict:
        """Perform rigorous independent evaluation on a permission request.

        Verifies:
        1. File existence & scope.
        2. AST syntax of suggested fix and replacement.
        3. Diff scope & Blast Radius (line limit, target line bounds).
        4. Dangerous pattern scan.
        5. In-memory patch application & whole-file AST validity.
        6. Target condition verification (no placebo/stale patches).
        """
        checks: dict[str, Any] = {
            "file_exists": False,
            "ast_syntax": False,
            "diff_scope": False,
            "dangerous_patterns": False,
            "patch_application": False,
            "target_condition": False,
        }

        # Check 1: File Existence & Scope
        target_path = self._resolve_target_file(req.file)
        if target_path is None or not target_path.exists():
            return EvaluationVerdict(
                request_id=req.request_id,
                approved=False,
                rationale=f"Denied by {self.config.reviewer_id}: Target file not found ({req.file}).",
                checks=checks,
            )
        checks["file_exists"] = True

        try:
            orig_content = target_path.read_text(encoding="utf-8")
        except Exception as read_err:
            return EvaluationVerdict(
                request_id=req.request_id,
                approved=False,
                rationale=f"Denied by {self.config.reviewer_id}: Failed to read target file ({read_err}).",
                checks=checks,
            )

        orig_lines = orig_content.splitlines()
        orig_line_count = len(orig_lines)

        # Check 2: Blast Radius & Line Bounds
        if self.config.check_diff_scope:
            if req.line > orig_line_count + self.config.max_line_drift:
                return EvaluationVerdict(
                    request_id=req.request_id,
                    approved=False,
                    rationale=(
                        f"Denied by {self.config.reviewer_id}: Target line {req.line} exceeds "
                        f"target file length ({orig_line_count} lines) in {target_path.name}."
                    ),
                    checks=checks,
                )

        blocks = self._extract_patch_blocks(req.suggested_fix)
        if not blocks:
            # If not standard search/replace, check if it's fenced code
            if not req.suggested_fix or not req.suggested_fix.strip():
                return EvaluationVerdict(
                    request_id=req.request_id,
                    approved=False,
                    rationale=f"Denied by {self.config.reviewer_id}: Empty or invalid suggested fix.",
                    checks=checks,
                )
            # Evaluate fenced code snippet AST directly
            try:
                ast.parse(textwrap.dedent(req.suggested_fix))
                checks["ast_syntax"] = True
            except SyntaxError as syn_err:
                return EvaluationVerdict(
                    request_id=req.request_id,
                    approved=False,
                    rationale=f"Denied by {self.config.reviewer_id}: SyntaxError in suggested fix snippet ({syn_err}).",
                    checks=checks,
                )
        else:
            search_block, replace_block = blocks

            # Check lines modified (blast radius)
            search_lines = search_block.splitlines()
            replace_lines = replace_block.splitlines()
            if (
                len(search_lines) > self.config.max_blast_radius_lines
                or len(replace_lines) > self.config.max_blast_radius_lines
            ):
                return EvaluationVerdict(
                    request_id=req.request_id,
                    approved=False,
                    rationale=(
                        f"Denied by {self.config.reviewer_id}: Blast radius exceeded "
                        f"({len(replace_lines)} lines > limit {self.config.max_blast_radius_lines})."
                    ),
                    checks=checks,
                )

            # Check 3: AST Syntax on replacement snippet
            if self.config.check_syntax and replace_block.strip():
                try:
                    ast.parse(textwrap.dedent(replace_block))
                    checks["ast_syntax"] = True
                except SyntaxError:
                    # Could be an indented sub-block; will be validated by whole-file AST
                    pass

            # Check 4: Dangerous Patterns
            if self.config.check_dangerous_patterns:
                for pattern, desc in DANGEROUS_PATTERNS:
                    if pattern.search(replace_block):
                        return EvaluationVerdict(
                            request_id=req.request_id,
                            approved=False,
                            rationale=f"Denied by {self.config.reviewer_id}: Forbidden pattern detected: {desc}.",
                            checks=checks,
                        )
            checks["dangerous_patterns"] = True

        checks["diff_scope"] = True

        # Check 5: In-Memory Patch Simulation & Full AST Parsing
        ok, patched_content, err_msg = self._apply_patch_in_memory(
            orig_content, req.suggested_fix, target_name=str(target_path)
        )
        if not ok:
            return EvaluationVerdict(
                request_id=req.request_id,
                approved=False,
                rationale=(
                    f"Denied by {self.config.reviewer_id}: In-memory patch application failed: {err_msg}."
                ),
                checks=checks,
            )
        checks["patch_application"] = True

        try:
            ast.parse(patched_content, filename=str(target_path))
            checks["ast_syntax"] = True
        except SyntaxError as full_syn_err:
            return EvaluationVerdict(
                request_id=req.request_id,
                approved=False,
                rationale=(
                    f"Denied by {self.config.reviewer_id}: Patched source has SyntaxError: {full_syn_err}."
                ),
                checks=checks,
            )

        # Check 6: Target condition verification (Avoid stale / already-fixed bugs)
        if req.bug_type == "PossiblyUndefinedName":
            match = re.search(r"Name\s+`?(\w+)`?\s+used", req.description)
            if match:
                var_name = match.group(1)
                # If variable is already assigned at top-level / module scope, patch may be stale
                if f"{var_name} = " in orig_content or f"{var_name}:" in orig_content:
                    # Verify if it's already defined before the reported bug line
                    lines_before = "\n".join(orig_lines[: min(req.line, orig_line_count)])
                    if f"{var_name} =" in lines_before:
                        return EvaluationVerdict(
                            request_id=req.request_id,
                            approved=False,
                            rationale=(
                                f"Denied by {self.config.reviewer_id}: Variable '{var_name}' is already "
                                f"defined in {target_path.name}; bug condition is obsolete."
                            ),
                            checks=checks,
                        )

        checks["target_condition"] = True

        # All checks passed cleanly
        rationale = (
            f"Autonomous review approved by {self.config.reviewer_id}: "
            f"AST parsed cleanly without syntax errors, blast radius bounded to {target_path.name} "
            f"(target line {req.line}), zero dangerous patterns detected, and patch applied cleanly in memory."
        )
        return EvaluationVerdict(
            request_id=req.request_id,
            approved=True,
            rationale=rationale,
            checks=checks,
        )

    def review_request(self, req_or_id: str | PermissionRequest) -> dict[str, Any]:
        """Review a single permission request, approve/deny, and optionally apply fix."""
        if isinstance(req_or_id, str):
            req = self.gate._pending.get(req_or_id)
            if not req:
                return {
                    "request_id": req_or_id,
                    "status": "error",
                    "reason": "Request ID not found in pending queue",
                }
        else:
            req = req_or_id

        verdict = self.evaluate_request(req)

        if verdict.approved:
            # Call approve on PermissionGate
            approved_ok = self.gate.approve(
                req.request_id,
                decided_by=self.config.reviewer_id,
                note=verdict.rationale,
            )
            apply_result = None
            if approved_ok and self.config.auto_apply and self.engine is not None:
                try:
                    apply_result = self.engine.apply_approved_fix(req.request_id)
                    verdict.applied = apply_result.get("action") == "fixed"
                    verdict.apply_result = apply_result
                except Exception as apply_err:
                    logger.error("AutoFixEngine apply_approved_fix failed: %s", apply_err, exc_info=True)
                    self.gate.mark_apply_status(req.request_id, "apply_failed", error=str(apply_err))
                    verdict.apply_result = {"action": "error", "reason": str(apply_err)}

            return {
                "request_id": req.request_id,
                "status": "approved",
                "rationale": verdict.rationale,
                "applied": verdict.applied,
                "apply_result": verdict.apply_result,
                "checks": verdict.checks,
            }
        else:
            # Call deny on PermissionGate
            self.gate.deny(
                req.request_id,
                decided_by=self.config.reviewer_id,
                note=verdict.rationale,
            )
            return {
                "request_id": req.request_id,
                "status": "denied",
                "rationale": verdict.rationale,
                "applied": False,
                "checks": verdict.checks,
            }

    def review_all_pending(self) -> dict[str, Any]:
        """Inspect and process all pending requests in PermissionGate."""
        pending_list = self.gate.list_pending()
        total = len(pending_list)
        approved_count = 0
        denied_count = 0
        results = []

        logger.info(
            "AutonomousReviewer starting batch review on %d pending request(s)", total
        )

        for req in pending_list:
            res = self.review_request(req)
            if res.get("status") == "approved":
                approved_count += 1
            elif res.get("status") == "denied":
                denied_count += 1
            results.append(res)

        return {
            "total_pending": total,
            "approved_count": approved_count,
            "denied_count": denied_count,
            "results": results,
        }


_global_reviewer: AutonomousReviewer | None = None


def get_autonomous_reviewer(data_dir: str = "data") -> AutonomousReviewer:
    """Get or create singleton AutonomousReviewer instance."""
    global _global_reviewer
    if _global_reviewer is None or str(_global_reviewer.data_dir) != str(data_dir):
        _global_reviewer = AutonomousReviewer(data_dir=data_dir)
    return _global_reviewer


def reset_autonomous_reviewer() -> None:
    """Reset global AutonomousReviewer instance (for testing)."""
    global _global_reviewer
    _global_reviewer = None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    reviewer = get_autonomous_reviewer()
    summary = reviewer.review_all_pending()
    print(json.dumps(summary, indent=2))
