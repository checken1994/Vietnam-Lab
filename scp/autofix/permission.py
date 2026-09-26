# SCP CIRCUIT: M07 — STATUS: CLOSED_WITH_KNOWN_GAP (closure: docs/evidence-summary/M07-closure.json)
"""
SCP Permission Gate — for Tier 3 (logic bugs that must ask human).

When SCP detects a logic bug, it does NOT auto-fix. Instead:
  1. Write a permission request to data/permission_requests.jsonl
  2. Optionally send notification (if critical)
  3. Wait for human approval (poll the request file)
  4. Only apply fix when approved

This is the "con người quyết định" boundary — SCP finds the problem,
suggests the fix, but the HUMAN decides whether to apply it for logic changes.

During Attack Mode (Tier 4), SCP bypasses this gate for RESTRAINTS only.
"""
from __future__ import annotations

import json
import logging

logger = logging.getLogger("scp.autofix.permission")
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from scp.autofix.classifier import BugReport

# [OPT-14 / Gà §11] UnderstandingChecker — verify that the human who approves
# a Tier-3 logic-bug fix can actually explain what the fix does. If the note
# is missing/trivial/copy-paste, approval is rejected — the human did not
# demonstrate understanding of what they were approving.
from scp.meta.understanding_check import UnderstandingChecker


@dataclass
class PermissionRequest:
    """A pending permission request for a logic bug."""
    request_id: str
    timestamp: float
    file: str
    line: int
    bug_type: str
    description: str
    suggested_fix: str
    # [Phase 5-A / 4-a-009] Extended status set:
    #   pending       — initial, awaiting human decision
    #   approved      — human approved, fix not yet applied (transient —
    #                    should transition to pending_apply → applied
    #                    via v105_approve_permission endpoint)
    #   pending_apply — approval committed, fix application in progress
    #                    (transactional intermediate state — operators can
    #                    see the request is mid-flight, not stuck in limbo)
    #   applied       — fix successfully applied after approval (terminal)
    #   apply_failed  — fix application raised an exception (recoverable —
    #                    operator can re-approve via the same endpoint)
    #   denied        — human denied (terminal)
    #   expired       — 24h elapsed with no decision (terminal)
    status: str = "pending"
    human_note: str = ""
    decided_at: float | None = None
    decided_by: str = ""
    # [Phase 5-A / 4-a-009] Audit trail for apply-failed transitions.
    # When apply_approved_fix raises, the error message is stored here so
    # operators can diagnose without grepping logs (DNA #8 KB accumulation).
    apply_error: str = ""
    applied_at: float | None = None


class PermissionGate:
    """Manages permission requests for Tier 3 logic bugs.

    Requests are stored in data/permission_requests.jsonl (append-only).
    Human approves/denies by editing the file or via API endpoint.
    SCP polls the file to check status.
    """

    def __init__(self, data_dir: str = "data"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.requests_file = self.data_dir / "permission_requests.jsonl"
        self._cooldown_seconds = 300  # Don't re-request same bug within 5 min
        self._pending: dict[str, PermissionRequest] = {}
        # [EXEC-3] TẠI SAO: _pending dict + JSONL appends were unprotected —
        # concurrent request_permission() calls could collide on dict mutation
        # and interleave partial JSONL lines. Lock serializes both dict + file.
        self._lock = threading.Lock()
        # [OPT-14 / Gà §11] Per-instance checker (so tests can override
        # thresholds if needed). Human must explain what the fix does —
        # trivial/empty/copy-paste explanations are rejected.
        self.understanding_checker = UnderstandingChecker()
        # SCP_AUTO_APPROVE_TIER3=1 mode bypasses understanding check (the
        # "human" in that mode is the env-var setting; a separate audit log
        # captures these auto-approvals — see engine.py TIER3_AUTO_AUDIT_LOG).
        import os as _os
        self._bypass_understanding = _os.environ.get("SCP_AUTO_APPROVE_TIER3", "0") == "1"
        self._load_pending()

    def _load_pending(self):
        """Load pending requests from file (on startup)."""
        if not self.requests_file.exists():
            return
        try:
            with open(self.requests_file, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        req = PermissionRequest(**json.loads(line))
                        if req.status == "pending":
                            self._pending[req.request_id] = req
                    except Exception:
                        logger.exception("[permission.py:78] silenced exception")
        except Exception as e:
            logger.warning(f"Silent except: {e}", exc_info=True)

    def request_permission(self, bug: BugReport) -> str:
        """Submit a permission request for a Tier 3 bug.
        Returns request_id. If same bug already pending, returns existing ID
        (don't spam human with duplicate requests)."""
        # [EXEC-3] Hold lock across cooldown-check + create + file-append so
        # two threads can't both observe "no pending" and double-submit.
        with self._lock:
            # Check if same bug already pending (cooldown)
            for req in self._pending.values():
                if (req.file == bug.file
                    and req.line == bug.line
                    and req.bug_type == bug.bug_type
                    and req.status == "pending"):
                    # Same bug already requested — don't spam
                    return req.request_id

            # Create new request
            request_id = f"perm_{int(time.time()*1000)}_{hash((bug.file, bug.line)) & 0xFFFF:04x}"
            req = PermissionRequest(
                request_id=request_id,
                timestamp=time.time(),
                file=bug.file,
                line=bug.line,
                bug_type=bug.bug_type,
                description=bug.description,
                suggested_fix=bug.suggested_fix,
                status="pending",
            )
            self._pending[request_id] = req

            # Append to file (audit trail)
            try:
                with open(self.requests_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps(asdict(req), ensure_ascii=False) + "\n")
            except Exception as e:
                logger.warning(f"Silent except: {e}", exc_info=True)

            return request_id

    def check_permission(self, request_id: str) -> str:
        """Check status of a permission request.
        Returns: 'pending' / 'approved' / 'denied' / 'expired' / 'unknown'"""
        # [EXEC-3] Lock around _pending read + file re-read + status mutation.
        with self._lock:
            # Re-read file to get latest status (human may have edited)
            if request_id not in self._pending:
                return "unknown"

            req = self._pending[request_id]
            # Check if human updated the file
            try:
                with open(self.requests_file, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            data = json.loads(line)
                            if data.get("request_id") == request_id:
                                req.status = data.get("status", "pending")
                                req.human_note = data.get("human_note", "")
                                req.decided_at = data.get("decided_at")
                                req.decided_by = data.get("decided_by", "")
                        except Exception as e:
                            logger.warning(f"Silent except: {e}", exc_info=True)
            except Exception as e:
                logger.warning(f"Silent except: {e}", exc_info=True)

            # Expire old requests (24h)
            if req.status == "pending" and time.time() - req.timestamp > 86400:
                req.status = "expired"

            return req.status

    def list_pending(self) -> list[PermissionRequest]:
        """List all pending permission requests (for human review UI)."""
        with self._lock:
            return [r for r in self._pending.values() if r.status == "pending"]

    def approve(self, request_id: str, decided_by: str = "human", note: str = ""):
        """Human approves a request (called from admin API).

        [OPT-14 / Gà §11] Human must demonstrate understanding of what they're
        approving. The `note` field is checked against `suggested_fix` — if the
        note is too short, has low concept overlap, or is a verbatim copy-paste
        of the suggested_fix, approval is REJECTED (returns False). The human
        must re-approve with a real explanation in their own words.

        Exception: when SCP_AUTO_APPROVE_TIER3=1 (Gà-approved auto-approve
        mode for testing), the understanding check is bypassed — those
        approvals are audited separately in TIER3_AUTO_AUDIT_LOG.
        """
        with self._lock:
            if request_id not in self._pending:
                return False
            req = self._pending[request_id]

            # [OPT-14 / Gà §11] Understanding gate (unless bypassed)
            if not self._bypass_understanding:
                verification = self.understanding_checker.verify(
                    proposal=req.suggested_fix or req.description or "",
                    human_summary=note,
                )
                if not verification["passed"]:
                    logger.warning(
                        f"[permission.py] Gà §11 — approval REJECTED for {request_id}: "
                        f"human note does not demonstrate understanding "
                        f"(overlap={verification.get('concept_overlap', 0)}, "
                        f"summary_len={verification.get('summary_length', 0)}). "
                        f"Note must explain the fix in the human's own words."
                    )
                    return False

            req.status = "approved"
            req.decided_at = time.time()
            req.decided_by = decided_by
            req.human_note = note
            # Append approval to file
            try:
                with open(self.requests_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps(asdict(req), ensure_ascii=False) + "\n")
            except Exception as e:
                logger.warning(f"Silent except: {e}", exc_info=True)
            return True

    def deny(self, request_id: str, decided_by: str = "human", note: str = ""):
        """Human denies a request (called from admin API)."""
        with self._lock:
            if request_id not in self._pending:
                return False
            req = self._pending[request_id]
            req.status = "denied"
            req.decided_at = time.time()
            req.decided_by = decided_by
            req.human_note = note
            try:
                with open(self.requests_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps(asdict(req), ensure_ascii=False) + "\n")
            except Exception as e:
                logger.warning(f"Silent except: {e}", exc_info=True)
            return True

    # [Phase 5-A / 4-a-009] Transactional status transitions for the apply
    # phase. The v105_approve_permission endpoint must:
    #   1. approve(request_id, ...) → status="approved"
    #   2. mark_apply_status(request_id, "pending_apply") BEFORE calling
    #      apply_approved_fix (so operators can see mid-flight state)
    #   3. apply_approved_fix(request_id)
    #   4a. On success: mark_apply_status(request_id, "applied")
    #   4b. On exception: mark_apply_status(request_id, "apply_failed",
    #       error=str(e)) — request is recoverable (re-approve via same
    #       endpoint; approve() will re-set status to "approved" and the
    #       apply attempt can be retried).
    # This eliminates the "approved-but-not-applied" limbo that the old
    # code fell into when apply_approved_fix raised (DNA #8 KB accumulation,
    # #9 No harm — partial transaction is recoverable, not stuck).
    _ALLOWED_TRANSITIONS = frozenset({
        "pending_apply", "applied", "apply_failed",
    })

    def mark_apply_status(
        self,
        request_id: str,
        new_status: str,
        *,
        error: str = "",
    ) -> bool:
        """Transition a permission request's status in the apply phase.

        Args:
            request_id: The request to update.
            new_status: One of {"pending_apply", "applied", "apply_failed"}.
                Other statuses (pending/approved/denied/expired) are managed
                by request_permission / approve / deny / check_permission.
            error: When new_status="apply_failed", the exception message to
                record for operator diagnosis (DNA #8 KB accumulation).

        Returns:
            True if transition succeeded; False if request_id not found or
            new_status is not in the allowed transition set.
        """
        if new_status not in self._ALLOWED_TRANSITIONS:
            logger.warning(
                f"[permission.py] mark_apply_status rejected "
                f"new_status={new_status!r} for {request_id} — not in "
                f"_ALLOWED_TRANSITIONS={self._ALLOWED_TRANSITIONS}"
            )
            return False
        with self._lock:
            if request_id not in self._pending:
                return False
            req = self._pending[request_id]
            old_status = req.status
            req.status = new_status
            if new_status == "applied":
                req.applied_at = time.time()
            if new_status == "apply_failed":
                req.apply_error = error[:500]  # cap to prevent log bloat
            try:
                with open(self.requests_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps(asdict(req), ensure_ascii=False) + "\n")
            except Exception as e:
                logger.warning(f"Silent except: {e}", exc_info=True)
            logger.info(
                f"[permission.py] mark_apply_status: {request_id} "
                f"{old_status!r} → {new_status!r}"
                + (f" (error: {error[:200]})" if error else "")
            )
            return True

    def revert_to_pending(self, request_id: str, reason: str = "") -> bool:
        """Revert an approved/apply_failed request back to pending.

        Used by the v105_approve_permission endpoint's failure path: if
        apply_approved_fix raises, the request can be reverted to "pending"
        so the operator can re-approve it via the same endpoint (instead of
        being stuck in "approved-but-not-applied" limbo).

        Returns True if revert succeeded, False if request not found.
        """
        with self._lock:
            if request_id not in self._pending:
                return False
            req = self._pending[request_id]
            old_status = req.status
            req.status = "pending"
            req.decided_at = None
            req.decided_by = ""
            if reason:
                req.apply_error = reason[:500]
            try:
                with open(self.requests_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps(asdict(req), ensure_ascii=False) + "\n")
            except Exception as e:
                logger.warning(f"Silent except: {e}", exc_info=True)
            logger.info(
                f"[permission.py] revert_to_pending: {request_id} "
                f"{old_status!r} → 'pending' (reason: {reason[:200] if reason else 'n/a'})"
            )
            return True
