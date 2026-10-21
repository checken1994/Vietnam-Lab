"""Task Kernel event journal, including the global control anchor.
This module is part of :mod:`scp.runtime_audit.probes`.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import sqlite3
from pathlib import Path
from typing import Any, Sequence

from scp.runtime_audit.models import ProbeResult, Severity, utc_now_iso
from scp.runtime_audit.probes._shared import is_backup_path, open_readonly

logger = logging.getLogger(__name__)


class JournalAnchorProbe:
    """Verify the Task Kernel event journal, including the global anchor.

    Why this probe exists
    ---------------------
    ``TaskKernel.verify_journal(task_id)`` validates ONE task's hash chain.
    It consults the global ``control`` anchor only when that task has zero
    events. The global anchor (``control.event_count`` and
    ``control.journal_head_hash``) is therefore never cross-checked against
    the physical event table, so a divergence between the counter and the
    rows it claims to count is invisible to the in-process verifier.

    This probe closes that blind spot by comparing, for every kernel
    database:
      * ``control.event_count``            vs ``COUNT(*) FROM events``
      * ``control.journal_head_hash``      vs the newest physical event hash
      * per-task ``seq`` continuity and ``prev_event_hash`` linkage
      * (optional) the HMAC of every event, when the capability secret is
        available.
    """

    probe_id = "kernel.journal_anchor"

    def __init__(
        self,
        kernel_db_paths: Sequence[Path],
        *,
        capability_secret: str | None = None,
        max_hmac_events: int = 50_000,
        data_dir: Path | None = None,
    ) -> None:
        self.kernel_db_paths = [Path(p) for p in kernel_db_paths]
        self.capability_secret = capability_secret or None
        self.max_hmac_events = max_hmac_events
        if data_dir is not None:
            self.data_dir: Path | None = Path(data_dir)
        else:
            self.data_dir = self._infer_data_dir()

    # ── discovery ────────────────────────────────────────────────────────
    @staticmethod
    def looks_like_kernel_db(db_path: Path) -> bool:
        """True when the file exposes the Task Kernel schema."""
        try:
            conn = open_readonly(db_path)
        except (sqlite3.DatabaseError, OSError):
            return False
        try:
            names = {
                str(r[0])
                for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            }
        except sqlite3.DatabaseError:
            return False
        finally:
            try:
                conn.close()
            except sqlite3.Error as exc:
                logger.debug("sqlite close failed for %s: %s", db_path, exc)
        return {"control", "tasks", "events"}.issubset(names)

    # ── observation ──────────────────────────────────────────────────────
    def _infer_data_dir(self) -> Path | None:
        """Derive a common root so labels stay short and comparable."""
        if not self.kernel_db_paths:
            return None
        try:
            resolved = [p.resolve() for p in self.kernel_db_paths]
            common = Path(os.path.commonpath([str(p) for p in resolved]))
        except (ValueError, OSError):
            return None
        return common if common.is_dir() else common.parent

    def _label(self, db_path: Path) -> str:
        """Data-dir-relative label when possible, else the absolute path."""
        if self.data_dir is not None:
            try:
                return str(db_path.resolve().relative_to(self.data_dir.resolve())).replace("\\", "/")
            except ValueError as exc:
                # The path lives outside the data root; the absolute form is
                # the correct label, so this is not an error condition.
                logger.debug("kernel db %s is outside data dir: %s", db_path, exc)
        return str(db_path).replace("\\", "/")

    def inspect_kernel_db(self, db_path: Path) -> dict[str, Any]:
        """Observe one kernel database. Never raises."""
        record: dict[str, Any] = {"path": self._label(db_path), "is_backup": is_backup_path(db_path)}
        try:
            conn = open_readonly(db_path)
        except (sqlite3.DatabaseError, OSError) as exc:
            record.update({
                "openable": False,
                "error": f"{type(exc).__name__}: {exc}",
                "event_count_actual": None,
                "event_count_anchor": None,
                "event_count_delta": None,
                "head_matches": None,
                "chain_broken_tasks": [],
                "hmac_mismatch": None,
            })
            return record

        try:
            control_row = conn.execute(
                "SELECT journal_head_hash, event_count FROM control WHERE id=1"
            ).fetchone()
            anchor_count = int(control_row["event_count"]) if control_row is not None else None
            anchor_head = str(control_row["journal_head_hash"]) if control_row is not None and control_row["journal_head_hash"] else None

            actual_count = int(conn.execute("SELECT COUNT(*) FROM events").fetchone()[0])

            newest = conn.execute(
                "SELECT event_hash FROM events ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
            newest_hash = str(newest[0]) if newest else None

            broken: list[dict[str, Any]] = []
            hmac_mismatch = 0
            hmac_checked = 0
            task_count = 0

            rows = conn.execute(
                "SELECT event_id, task_id, seq, type, from_state, to_state, actor, reason, "
                "payload_json, policy_hash, prev_event_hash, event_hash "
                "FROM events ORDER BY task_id, seq"
            ).fetchall()

            current_task: str | None = None
            expected_seq = 0
            expected_prev: str | None = None
            for row in rows:
                task_id = str(row["task_id"])
                if task_id != current_task:
                    current_task = task_id
                    expected_seq = 0
                    expected_prev = None
                    task_count += 1
                expected_seq += 1
                seq = int(row["seq"])
                problems: list[str] = []
                if seq != expected_seq:
                    problems.append(f"seq_gap:expected={expected_seq}:actual={seq}")
                if row["prev_event_hash"] != expected_prev:
                    problems.append(f"prev_hash_break:expected={expected_prev}:actual={row['prev_event_hash']}")
                if not row["event_hash"]:
                    problems.append("missing_event_hash")
                if problems:
                    broken.append({"task_id": task_id, "seq": seq, "problems": problems})
                expected_prev = str(row["event_hash"]) if row["event_hash"] else None

                if self.capability_secret and hmac_checked < self.max_hmac_events:
                    hmac_checked += 1
                    try:
                        body = {
                            "event_id": row["event_id"],
                            "task_id": task_id,
                            "seq": seq,
                            "type": row["type"],
                            "from_state": row["from_state"],
                            "to_state": row["to_state"],
                            "actor": row["actor"],
                            "reason": row["reason"],
                            "payload": json.loads(row["payload_json"] or "{}"),
                            "policy_hash": row["policy_hash"],
                            "prev_event_hash": row["prev_event_hash"],
                        }
                        expected = hmac.new(
                            self.capability_secret.encode(),
                            json.dumps(body, sort_keys=True).encode(),
                            hashlib.sha256,
                        ).hexdigest()
                        if expected != row["event_hash"]:
                            hmac_mismatch += 1
                    except (TypeError, ValueError):
                        hmac_mismatch += 1

            record.update({
                "openable": True,
                "event_count_actual": actual_count,
                "event_count_anchor": anchor_count,
                "event_count_delta": (actual_count - anchor_count) if anchor_count is not None else None,
                "anchor_head_hash": anchor_head,
                "newest_event_hash": newest_hash,
                "head_matches": (anchor_head == newest_hash) if anchor_head else None,
                "task_count": task_count,
                "chain_broken_tasks": broken[:25],
                "chain_broken_count": len(broken),
                "hmac_checked": hmac_checked if self.capability_secret else 0,
                "hmac_mismatch": hmac_mismatch if self.capability_secret else None,
            })
        except sqlite3.DatabaseError as exc:
            # A schema variant (e.g. a control table without the anchor
            # columns) means the anchor could not be observed. That is
            # UNKNOWN, not FAIL — but it must still be reported.
            record.update({
                "openable": True,
                "anchor_observable": False,
                "error": f"{type(exc).__name__}: {exc}",
                "event_count_actual": None,
                "event_count_anchor": None,
                "event_count_delta": None,
                "head_matches": None,
                "chain_broken_tasks": [],
                "hmac_mismatch": None,
            })
        else:
            record.setdefault("anchor_observable", True)
        finally:
            try:
                conn.close()
            except sqlite3.Error as exc:
                logger.debug("sqlite close failed for %s: %s", db_path, exc)
        return record

    # ── probe protocol ──────────────────────────────────────────────────
    def run(self) -> list[ProbeResult]:
        results: list[ProbeResult] = []
        if not self.kernel_db_paths:
            results.append(ProbeResult(
                probe_id=self.probe_id,
                severity=Severity.UNKNOWN,
                title="No Task Kernel database was observable",
                summary="No kernel database could be located; journal integrity was not observed.",
                details={"candidates": []},
            ))
            return results

        observations = [self.inspect_kernel_db(p) for p in self.kernel_db_paths]

        anchor_divergent: list[str] = []
        head_mismatched: list[str] = []
        chain_broken: list[str] = []
        hmac_failed: list[str] = []
        unreadable: list[str] = []
        inconclusive: list[str] = []

        for obs in observations:
            label = obs["path"]
            if obs.get("is_backup"):
                # A historical backup is inventoried but never graded.
                continue
            if not obs.get("openable"):
                unreadable.append(label)
                continue
            if not obs.get("anchor_observable", True):
                inconclusive.append(label)
                continue
            delta = obs.get("event_count_delta")
            if delta is not None and delta != 0:
                anchor_divergent.append(label)
            if obs.get("head_matches") is False:
                head_mismatched.append(label)
            if obs.get("chain_broken_count"):
                chain_broken.append(label)
            if obs.get("hmac_mismatch"):
                hmac_failed.append(label)

        problems = anchor_divergent + head_mismatched + chain_broken + hmac_failed + unreadable
        if problems:
            severity = Severity.FAIL
            title = "Task Kernel journal does not match its global anchor"
            summary_parts: list[str] = []
            if anchor_divergent:
                summary_parts.append(f"event_count divergence in {len(anchor_divergent)} database(s)")
            if head_mismatched:
                summary_parts.append(f"journal_head_hash mismatch in {len(head_mismatched)} database(s)")
            if chain_broken:
                summary_parts.append(f"broken per-task hash chain in {len(chain_broken)} database(s)")
            if hmac_failed:
                summary_parts.append(f"HMAC mismatch in {len(hmac_failed)} database(s)")
            if unreadable:
                summary_parts.append(f"{len(unreadable)} kernel database(s) unreadable")
            summary = "; ".join(summary_parts)
        elif inconclusive:
            severity = Severity.UNKNOWN
            title = "Task Kernel journal anchor could not be fully observed"
            summary = (
                f"{len(inconclusive)} database(s) use a control schema without the global anchor columns; "
                "the anchor could not be cross-checked there."
            )
        else:
            severity = Severity.PASS
            title = "Task Kernel journal is internally consistent"
            summary = (
                f"{len(observations)} kernel database(s): event counter, head hash, per-task chains"
                + (" and HMAC signatures" if self.capability_secret else "")
                + " all agree."
            )

        results.append(ProbeResult(
            probe_id=self.probe_id,
            severity=severity,
            title=title,
            summary=summary,
            details={
                "anchor_divergent": anchor_divergent,
                "head_mismatched": head_mismatched,
                "chain_broken": chain_broken,
                "hmac_failed": hmac_failed,
                "unreadable": unreadable,
                "inconclusive": inconclusive,
                "hmac_enabled": bool(self.capability_secret),
            },
            evidence={"observations": observations},
            observed_at=utc_now_iso(),
        ))
        return results
