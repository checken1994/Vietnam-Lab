"""Autonomous evidence resolution for /ask governance ESCALATE (esc-auto slice 2).

TẠI SAO module này tồn tại (owner 30/10: "đã tự động thì ai duyệt cho bạn"):
  Trước đây ESCALATE do thiếu chứng cứ chỉ withhold câu trả lời và đẩy task
  ask-kernel vào HUMAN_REVIEW — chờ một người không bao giờ tới. Sau đó
  ``autonomous_cycle`` "giải phóng" task với nhãn ``*_audited_and_contained``
  dù KHÔNG có ai/cái gì audit (c1-escalation-done §5).

Hợp đồng (fail-closed, nói thật, không bao giờ "tự duyệt khi hết giờ"):
  * SCP tự xếp một phiếu chứng cứ (bounded, dedup theo hash claim) và tự chạy
    các cổng chứng cứ tự động. KHÔNG có bước người duyệt nào.
  * RESOLVED_CITED chỉ khi một bản ghi gắn ĐÚNG claim này có receipt HMAC của
    SCP verify được + hash nội dung khớp + còn TTL. Người dùng nhận nguyên văn
    nguồn (DELIVER_CITED_SOURCE), KHÔNG nhận bản nháp AI đã bị giữ.
  * Còn lại ⇒ UNKNOWN_WITH_REASON với lý do thật: ERR_NO_EVIDENCE /
    ERR_EVIDENCE_UNAVAILABLE / ERR_EVIDENCE_CONFLICT. Hết lượt thử = UNKNOWN,
    không bao giờ là "deliver".
  * Câu hỏi gốc KHÔNG được persist (chỉ hash); bản nháp AI luôn "withheld".
  * Không có mẫu câu hỏi viết cứng: claim key = sha256(NFKC + casefold +
    gộp khoảng trắng) — trung lập mọi ngôn ngữ.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import sqlite3
import threading
import time
import unicodedata
import urllib.parse
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from scp.security.deadline_scheduler import BoundedDeadlineScheduler

logger = logging.getLogger("scp.core.evidence_resolution")

PENDING = "PENDING"
RESOLVED_CITED = "RESOLVED_CITED"
UNKNOWN_WITH_REASON = "UNKNOWN_WITH_REASON"
DELIVER_CITED_SOURCE = "DELIVER_CITED_SOURCE"
ERR_NO_EVIDENCE = "ERR_NO_EVIDENCE"
ERR_EVIDENCE_UNAVAILABLE = "ERR_EVIDENCE_UNAVAILABLE"
ERR_EVIDENCE_CONFLICT = "ERR_EVIDENCE_CONFLICT"
EVIDENCE_ESCALATION_REASON = "ask_evidence_insufficient_or_contradicted"
KERNEL_REASON_PREFIX = "evidence_resolution:"
_CODE_PRIORITY = {ERR_EVIDENCE_CONFLICT: 3, ERR_EVIDENCE_UNAVAILABLE: 2, ERR_NO_EVIDENCE: 1}
_USER_MESSAGES = {
    PENDING: "SCP has not verified this answer and is searching for evidence automatically. The AI draft stays withheld.",
    RESOLVED_CITED: "The AI draft stays withheld. SCP found a source whose integrity it verified; the citation below is the source text verbatim.",
    UNKNOWN_WITH_REASON: "SCP could not verify this claim (see code/reason). The AI draft stays withheld.",
}


def claim_key(question: str) -> str:
    """Language-neutral claim identity (no phrase lists, no regex on meaning)."""
    normalized = " ".join(unicodedata.normalize("NFKC", str(question or "")).casefold().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class EvidenceOutcome:
    status: str
    code: str
    reason: str
    citations: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    gate_id: str = ""
    final: bool = False  # True ⇒ retrying cannot change the answer (e.g. conflict)


Gate = Callable[[str], EvidenceOutcome]


def _unknown(code: str, reason: str, gate_id: str, *, final: bool = False) -> EvidenceOutcome:
    return EvidenceOutcome(UNKNOWN_WITH_REASON, code, reason, (), gate_id, final)


def _read_doc(db_path: Path, doc_id: str) -> dict[str, Any] | None:
    uri = "file:" + urllib.parse.quote(db_path.resolve().as_posix()) + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=5.0)
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM kb_documents WHERE doc_id = ?", (doc_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def receipt_bound_gate(db_path_fn: Callable[[], Path] | None = None) -> Gate:
    """Tier-B read-only gate: a record promoted FOR THIS claim with a valid SCP receipt."""

    def _db_path() -> Path:
        if db_path_fn is not None:
            return Path(db_path_fn())
        from scp.knowledge.canonical_fts_warehouse import get_canonical_fts_warehouse

        return Path(get_canonical_fts_warehouse().db_path)

    def gate(key: str) -> EvidenceOutcome:
        gid = "receipt_bound_gate"
        try:
            db_path = _db_path()
            if not db_path.exists():
                return _unknown(ERR_EVIDENCE_UNAVAILABLE, "evidence store not present", gid)
            row = _read_doc(db_path, f"web_{key}")
        except (sqlite3.Error, OSError, RuntimeError, ImportError) as exc:
            return _unknown(ERR_EVIDENCE_UNAVAILABLE, f"evidence store unreadable: {type(exc).__name__}", gid)
        if row is None:
            return _unknown(ERR_NO_EVIDENCE, "no verified record bound to this claim yet", gid)
        ttl = int(row.get("ttl_seconds") or 0)
        fetched = float(row.get("fetched_at_epoch") or 0.0)
        if ttl > 0 and time.time() - fetched > ttl:
            return _unknown(ERR_NO_EVIDENCE, "bound record expired (TTL)", gid)
        content = str(row.get("content") or "").strip()
        if hashlib.sha256(content.encode("utf-8")).hexdigest() != row.get("content_sha256"):
            return _unknown(ERR_EVIDENCE_UNAVAILABLE, "integrity mismatch: content hash differs", gid, final=True)
        try:
            receipt = json.loads(row.get("verifier_receipt_json") or "null")
        except json.JSONDecodeError:
            receipt = None
        if not isinstance(receipt, dict):
            return _unknown(ERR_NO_EVIDENCE, "bound record has no SCP verifier receipt", gid)
        try:
            from scp.core.verifier_receipt import verify_verifier_receipt

            verify_verifier_receipt(receipt, task_id=f"ingest:{key}", max_skew_seconds=0)
        except Exception as exc:  # noqa: BLE001 — any receipt failure is fail-closed
            return _unknown(ERR_EVIDENCE_UNAVAILABLE, f"receipt invalid: {type(exc).__name__}", gid, final=True)
        hosts = sorted(h for h in str(receipt.get("evidence_ref", "")).split(",") if h)
        source_host = (urllib.parse.urlparse(str(row.get("source_url") or "")).hostname or "").lower()
        # VERIFIED gap (esc-auto): AutoWebIngestor's stored receipt does NOT carry
        # content_sha256 (VerifierReceipt drops it; canonical bytes never bound it).
        # If a future receipt carries it, it MUST match; otherwise the citation is
        # labelled content_bound_by_receipt=False (row self-consistency only).
        bound = "content_sha256" in receipt
        if (bound and receipt.get("content_sha256") != row.get("content_sha256")) or source_host not in hosts:
            return _unknown(ERR_EVIDENCE_UNAVAILABLE, "receipt does not bind this record", gid, final=True)
        citation = {
            "doc_id": row.get("doc_id"),
            "source_url": row.get("source_url"),
            "source_host": source_host,
            "corroborating_hosts": hosts,
            "content_sha256": row.get("content_sha256"),
            "epistemic_tier": row.get("epistemic_tier"),
            "fetched_at_epoch": fetched,
            "receipt_verified": True,
            "content_bound_by_receipt": bound,
            "excerpt": content[:2000],
        }
        return EvidenceOutcome(RESOLVED_CITED, DELIVER_CITED_SOURCE, "receipt-verified source bound to claim", (citation,), gid)

    return gate


def staged_reconcile_gate(ingestor_factory: Callable[[], Any] | None = None, verify_gate: Gate | None = None) -> Gate:
    """Tier-3 promotion over sources already staged for this claim (>=2 independent hosts)."""
    verify = verify_gate or receipt_bound_gate()

    def gate(key: str) -> EvidenceOutcome:
        gid = "staged_reconcile_gate"
        try:
            if ingestor_factory is not None:
                ingestor = ingestor_factory()
            else:
                from scp.data_sources.auto_web_ingestor import AutoWebIngestor

                ingestor = AutoWebIngestor()
            result = ingestor.reconcile_and_promote(key)
        except Exception as exc:  # noqa: BLE001 — unavailable store/secret ⇒ truthful UNAVAILABLE
            return _unknown(ERR_EVIDENCE_UNAVAILABLE, f"promotion gate unavailable: {type(exc).__name__}", gid)
        status = str((result or {}).get("status", ""))
        if status == "PROMOTED_TO_FTS5":
            return verify(key)
        if status == "BLOCKED_SOURCE_CONFLICT":
            return _unknown(ERR_EVIDENCE_CONFLICT, "independent sources disagree", gid, final=True)
        if status == "WAITING_SECOND_INDEPENDENT_SOURCE":
            return _unknown(ERR_NO_EVIDENCE, "only one independent source so far", gid)
        return _unknown(ERR_NO_EVIDENCE, "no source staged for this claim", gid)

    return gate


def default_gates() -> list[Gate]:
    verify = receipt_bound_gate()
    return [verify, staged_reconcile_gate(verify_gate=verify)]


def _parse_schedule(raw: str | None) -> tuple[float, ...] | None:
    if not raw:
        return None
    try:
        values = tuple(float(part) for part in raw.split(",") if part.strip())
    except ValueError:
        return None
    if not values or len(values) > 8 or any(v < 0 or v > 86400 for v in values):
        return None
    return tuple(sorted(values))


class EvidenceResolutionQueue:
    """Bounded, dedup-by-claim, single-worker autonomous evidence resolver."""

    MAX_PENDING = 512
    TERMINAL_KEEP = 2048
    MAX_TASKS_PER_TICKET = 16
    REOPEN_AFTER_S = 600.0
    RETRY_SCHEDULE_S: tuple[float, ...] = (0.0, 60.0, 300.0, 1800.0)

    def __init__(
        self,
        data_dir: str | Path,
        *,
        gates: Sequence[Gate] | None = None,
        retry_schedule: Sequence[float] | None = None,
        max_pending: int | None = None,
        on_terminal: Callable[[str, str, dict[str, Any]], str] | None = None,
    ) -> None:
        self._dir = Path(data_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._journal_path = self._dir / "evidence_resolution_journal.jsonl"
        self._state_path = self._dir / "evidence_resolution_state.json"
        self._gates = list(gates) if gates is not None else default_gates()
        self._schedule = tuple(retry_schedule) if retry_schedule else self.RETRY_SCHEDULE_S
        self._max_pending = int(max_pending or self.MAX_PENDING)
        self._on_terminal = on_terminal or terminalize_ask_task
        self._lock = threading.Lock()
        self._io_lock = threading.Lock()
        self._tickets: dict[str, dict[str, Any]] = {}
        self._by_claim: dict[str, str] = {}
        self._counters = {k: 0 for k in (
            "enqueued", "deduped", "rejected_capacity", "attempts", "resolved_cited",
            "unknown_final", "gate_errors", "terminalized_tasks", "terminalize_errors",
        )}
        self._scheduler = BoundedDeadlineScheduler(self._on_due, max_pending=self._max_pending, name="scp-evidence-resolver")
        self._restore()

    # ---- request path (cheap: no gate runs here) ---------------------------
    def enqueue(self, question: str, *, task_id: str | None = None, ask_db_path: str | None = None) -> dict[str, Any]:
        key = claim_key(question)
        now = time.time()
        task_ref = [str(task_id), str(ask_db_path)] if task_id and ask_db_path else None
        terminal_ticket: dict[str, Any] | None = None
        with self._lock:
            existing = self._tickets.get(self._by_claim.get(key, ""))
            if existing is not None and (existing["status"] == PENDING or now - existing.get("resolved_at", now) < self.REOPEN_AFTER_S):
                self._counters["deduped"] += 1
                if task_ref and task_ref not in existing["task_refs"] and len(existing["task_refs"]) < self.MAX_TASKS_PER_TICKET:
                    existing["task_refs"].append(task_ref)
                if existing["status"] != PENDING:
                    terminal_ticket = dict(existing)
                view = self._view(existing)
                schedule_at = None
            elif sum(1 for t in self._tickets.values() if t["status"] == PENDING) >= self._max_pending:
                self._counters["rejected_capacity"] += 1
                view = {"ticket_id": None, "status": UNKNOWN_WITH_REASON, "code": ERR_EVIDENCE_UNAVAILABLE,
                        "reason": "evidence queue at capacity", "draft_answer": "withheld",
                        "user_message": _USER_MESSAGES[UNKNOWN_WITH_REASON], "citations": []}
                schedule_at = None
            else:
                ticket_id = "ev-" + secrets.token_urlsafe(16)
                ticket = {"ticket_id": ticket_id, "claim_key": key, "status": PENDING, "code": "", "reason": "",
                          "created_at": now, "attempts": 0, "next_attempt_at": now + self._schedule[0],
                          "task_refs": [task_ref] if task_ref else [], "citations": [], "gate_id": ""}
                self._tickets[ticket_id] = ticket
                self._by_claim[key] = ticket_id
                self._counters["enqueued"] += 1
                view = self._view(ticket)
                schedule_at = ticket["next_attempt_at"]
        if view.get("ticket_id") is None:
            self._journal("REJECTED_CAPACITY", claim_key=key[:16])
        elif schedule_at is not None:
            self._journal("ENQUEUED", ticket_id=view["ticket_id"], claim_key=key[:16], has_task=bool(task_ref))
            self._persist()
            self._scheduler.schedule(view["ticket_id"], schedule_at)
        else:
            self._persist()
        if terminal_ticket is not None and task_ref:
            self._terminalize(terminal_ticket, [task_ref])
        return view

    def get(self, ticket_id: str) -> dict[str, Any] | None:
        with self._lock:
            ticket = self._tickets.get(str(ticket_id))
            return self._view(ticket) if ticket else None

    def find_by_task(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            for ticket in self._tickets.values():
                if any(ref[0] == task_id for ref in ticket["task_refs"]):
                    return self._view(ticket)
        return None

    def stats(self) -> dict[str, Any]:
        with self._lock:
            pending = sum(1 for t in self._tickets.values() if t["status"] == PENDING)
            return {**self._counters, "pending": pending, "tickets": len(self._tickets),
                    "max_pending": self._max_pending, "scheduler": self._scheduler.stats()}

    def shutdown(self) -> None:
        self._scheduler.shutdown()

    # ---- worker path --------------------------------------------------------
    def _run_gates(self, key: str) -> EvidenceOutcome:
        unknowns: list[EvidenceOutcome] = []
        for gate in self._gates:
            try:
                outcome = gate(key)
            except Exception as exc:  # noqa: BLE001 — a crashing gate is UNAVAILABLE, never PASS
                with self._lock:
                    self._counters["gate_errors"] += 1
                logger.warning("[evidence-resolver] gate crashed: %s", exc, exc_info=True)
                outcome = _unknown(ERR_EVIDENCE_UNAVAILABLE, f"gate error: {type(exc).__name__}", getattr(gate, "__name__", "gate"))
            if outcome.status == RESOLVED_CITED and outcome.citations:
                return outcome
            unknowns.append(outcome)
        if not unknowns:
            return _unknown(ERR_EVIDENCE_UNAVAILABLE, "no evidence gate configured", "resolver")
        return max(unknowns, key=lambda o: (o.final, _CODE_PRIORITY.get(o.code, 0)))

    def _on_due(self, ticket_id: str, cause: str) -> None:
        with self._lock:
            ticket = self._tickets.get(ticket_id)
            if ticket is None or ticket["status"] != PENDING:
                return
            key = ticket["claim_key"]
        if cause == "capacity_evicted":
            outcome = _unknown(ERR_EVIDENCE_UNAVAILABLE, "resolver capacity exceeded", "resolver", final=True)
        else:
            outcome = self._run_gates(key)
        with self._lock:
            self._counters["attempts"] += 1
            ticket["attempts"] += 1
            ticket["code"], ticket["reason"], ticket["gate_id"] = outcome.code, outcome.reason, outcome.gate_id
            done = outcome.status == RESOLVED_CITED or outcome.final or ticket["attempts"] >= len(self._schedule)
            if done:
                ticket["status"] = RESOLVED_CITED if outcome.status == RESOLVED_CITED else UNKNOWN_WITH_REASON
                ticket["citations"] = list(outcome.citations)
                ticket["resolved_at"] = time.time()
                ticket["next_attempt_at"] = None
                self._counters["resolved_cited" if ticket["status"] == RESOLVED_CITED else "unknown_final"] += 1
                refs = [list(r) for r in ticket["task_refs"]]
                snapshot = dict(ticket)
            else:
                ticket["next_attempt_at"] = ticket["created_at"] + self._schedule[ticket["attempts"]]
                next_at = ticket["next_attempt_at"]
        self._journal("ATTEMPT", ticket_id=ticket_id, attempt=ticket["attempts"], code=outcome.code,
                      gate=outcome.gate_id, final=done)
        if done:
            self._journal("RESOLVED", ticket_id=ticket_id, status=snapshot["status"], code=snapshot["code"],
                          reason=snapshot["reason"],
                          citations=[{k: c.get(k) for k in ("source_url", "content_sha256", "corroborating_hosts")}
                                     for c in snapshot["citations"]])
            self._prune()
            self._persist()
            self._terminalize(snapshot, refs)
        else:
            self._persist()
            self._scheduler.schedule(ticket_id, next_at)

    def _terminalize(self, ticket: dict[str, Any], refs: list[list[str]]) -> None:
        view = self._view(ticket)
        for task_id, db_path in refs:
            try:
                result = self._on_terminal(task_id, db_path, view)
                with self._lock:
                    self._counters["terminalized_tasks"] += 1
                self._journal("TASK_TERMINALIZED", ticket_id=view["ticket_id"], task_id=task_id, result=str(result)[:120])
            except Exception as exc:  # noqa: BLE001 — logged + counted; resolve_stuck retries from the ticket
                with self._lock:
                    self._counters["terminalize_errors"] += 1
                logger.warning("[evidence-resolver] could not terminalize %s: %s", task_id, exc, exc_info=True)
                self._journal("TASK_TERMINALIZE_FAILED", ticket_id=view["ticket_id"], task_id=task_id, error=type(exc).__name__)

    # ---- persistence --------------------------------------------------------
    @staticmethod
    def _view(ticket: dict[str, Any]) -> dict[str, Any]:
        return {
            "ticket_id": ticket["ticket_id"], "status": ticket["status"], "code": ticket.get("code", ""),
            "reason": ticket.get("reason", ""), "attempts": ticket.get("attempts", 0),
            "created_at": ticket.get("created_at"), "next_attempt_at": ticket.get("next_attempt_at"),
            "resolved_at": ticket.get("resolved_at"), "citations": list(ticket.get("citations") or []),
            "draft_answer": "withheld", "user_message": _USER_MESSAGES.get(ticket["status"], ""),
        }

    def _prune(self) -> None:
        with self._lock:
            terminal = [t for t in self._tickets.values() if t["status"] != PENDING]
            excess = len(terminal) - self.TERMINAL_KEEP
            for ticket in sorted(terminal, key=lambda t: t.get("resolved_at") or 0)[: max(0, excess)]:
                self._tickets.pop(ticket["ticket_id"], None)
                if self._by_claim.get(ticket["claim_key"]) == ticket["ticket_id"]:
                    self._by_claim.pop(ticket["claim_key"], None)

    def _journal(self, event: str, **fields: Any) -> None:
        row = json.dumps({"ts": time.time(), "event": event, **fields}, ensure_ascii=False, default=str)
        with self._io_lock:
            with self._journal_path.open("a", encoding="utf-8") as handle:
                handle.write(row + "\n")

    def _persist(self) -> None:
        with self._lock:
            payload = json.dumps({"schema_version": 1, "tickets": self._tickets, "counters": self._counters}, default=str)
        tmp = self._state_path.with_name(f"{self._state_path.name}.{secrets.token_hex(4)}.tmp")
        with self._io_lock:
            try:
                tmp.write_text(payload, encoding="utf-8")
                os.replace(tmp, self._state_path)
            except OSError as exc:
                logger.warning("[evidence-resolver] state persist failed: %s", exc)
            finally:
                if tmp.exists():
                    tmp.unlink(missing_ok=True)

    def _restore(self) -> None:
        try:
            payload = json.loads(self._state_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("[evidence-resolver] state restore skipped: %s", exc)
            return
        tickets = payload.get("tickets") if isinstance(payload, dict) else None
        if not isinstance(tickets, dict):
            return
        pending: list[tuple[str, float]] = []
        for ticket_id, ticket in tickets.items():
            if not isinstance(ticket, dict) or "claim_key" not in ticket or "status" not in ticket:
                continue
            ticket.setdefault("task_refs", [])
            self._tickets[str(ticket_id)] = ticket
            self._by_claim[ticket["claim_key"]] = str(ticket_id)
            if ticket["status"] == PENDING:
                pending.append((str(ticket_id), float(ticket.get("next_attempt_at") or time.time())))
        for name, value in (payload.get("counters") or {}).items():
            if name in self._counters and isinstance(value, int):
                self._counters[name] = value
        for ticket_id, due in pending:
            self._scheduler.schedule(ticket_id, due)


def kernel_reason(view: dict[str, Any]) -> str:
    return f"{KERNEL_REASON_PREFIX}{view.get('status')}:{view.get('code') or ERR_EVIDENCE_UNAVAILABLE}"


def terminalize_ask_task(task_id: str, ask_db_path: str, view: dict[str, Any]) -> str:
    """Close the resolver-owned HUMAN_REVIEW task with the TRUE outcome.

    The AI draft stays withheld in every case (FAILED = draft not delivered);
    the reason names the evidence outcome instead of a fake "audited" label.
    """
    from scp.task_kernel import TaskKernel

    kernel = TaskKernel(ask_db_path)
    try:
        task = kernel.get_task(task_id)
        if task["state"] != "HUMAN_REVIEW":
            return f"skipped:{task['state']}"
        kernel.fail_task_fail_closed(
            task_id,
            reason=kernel_reason(view),
            actor="scp-evidence-resolver",
            error_payload={"evidence_ticket": view.get("ticket_id"), "status": view.get("status"),
                           "code": view.get("code"), "reason": view.get("reason"), "draft_answer": "withheld"},
        )
        return kernel_reason(view)
    finally:
        kernel.close()


# ---- process singleton (production wiring) ---------------------------------
_QUEUE: EvidenceResolutionQueue | None = None
_QUEUE_LOCK = threading.Lock()


def attach_default_queue(data_dir: str | Path) -> EvidenceResolutionQueue | None:
    global _QUEUE
    with _QUEUE_LOCK:
        if _QUEUE is None:
            try:
                _QUEUE = EvidenceResolutionQueue(
                    data_dir, retry_schedule=_parse_schedule(os.environ.get("SCP_EVIDENCE_RETRY_SCHEDULE_S"))
                )
            except Exception as exc:  # noqa: BLE001 — absent queue ⇒ legacy fail-closed path (withheld)
                logger.error("[evidence-resolver] init failed; ESCALATE stays withheld without autonomous resolution: %s",
                             exc, exc_info=True)
        return _QUEUE


def get_evidence_queue() -> EvidenceResolutionQueue | None:
    return _QUEUE


def on_ask_escalation(req: Any, request: Any, task_id: str, ask_db_path: str | None, fail_reason: str) -> dict[str, Any] | None:
    """finalize_flow hook: evidence-insufficient ESCALATE ⇒ autonomous ticket."""
    queue = _QUEUE
    if queue is None or fail_reason != EVIDENCE_ESCALATION_REASON:
        return None
    try:
        view = queue.enqueue(str(getattr(req, "question", "") or ""), task_id=task_id, ask_db_path=ask_db_path)
    except Exception as exc:  # noqa: BLE001 — never break the (already withheld) response
        logger.warning("[evidence-resolver] enqueue failed for %s: %s", task_id, exc, exc_info=True)
        return None
    state = getattr(request, "state", None)
    if state is not None:
        try:
            state.scp_evidence_ticket = view
        except AttributeError:
            logger.debug("request.state not writable; evidence ticket header skipped")
    return view


__all__ = [
    "PENDING", "RESOLVED_CITED", "UNKNOWN_WITH_REASON", "DELIVER_CITED_SOURCE",
    "ERR_NO_EVIDENCE", "ERR_EVIDENCE_UNAVAILABLE", "ERR_EVIDENCE_CONFLICT",
    "EvidenceOutcome", "EvidenceResolutionQueue", "claim_key", "receipt_bound_gate",
    "staged_reconcile_gate", "default_gates", "terminalize_ask_task", "attach_default_queue",
    "get_evidence_queue", "on_ask_escalation", "kernel_reason",
]
