# SCP CIRCUIT: M02 — STATUS: CLOSED_WITH_KNOWN_GAP (closure: docs/evidence-summary/M02-closure.json)
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import sqlite3
import threading
import time
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from scp.core.verifier_receipt import VerifierReceipt, sign_verifier_receipt

logger = logging.getLogger(__name__)


_c3_logger = logging.getLogger("scp.ask_kernel_adapter")

try:
    from .task_kernel import InvalidTransition, KernelError, StorageIntegrityError, TaskKernel
    from .trace_ledger import TraceLedger
except ImportError:
    logger.debug('<module>: ImportError ignored', exc_info=True)
    from task_kernel import InvalidTransition, KernelError, StorageIntegrityError, TaskKernel
    from trace_ledger import TraceLedger
try:
    from scp.api_server_parts.helpers import AskResponse
except Exception:  # pragma: no cover - standalone kernel tests do not need API schema
    logger.warning('<module>: Exception not handled', exc_info=True)
    AskResponse = None


_TRACE_LOCK = threading.Lock()
_TERMINAL = {"COMPLETED", "FAILED", "CANCELLED"}
# States in which the in-process ask attempt still holds execution authority
# over the task. Anything else that is non-terminal means the watchdog,
# boot-recovery, lease expiry or a previous escalation already moved the
# task out of the happy path — finalize must route, not assume.
_ASK_LIFECYCLE_INTACT_STATES = {"RUNNING", "VERIFYING"}

# [S20 2026-09-13] Long-LLM availability. Runtime evidence (bench_final_seed99
# + GA.md B11): free-tier provider latency measured 30-260s (mean 85.6s) while
# the /ask claim lease TTL was a fixed 60s. The lease expired mid-handler, the
# watchdog moved the task, and S19's fail-closed state-route correctly
# discarded a real, correct answer (2/10 asks on seed 99). The S20 fix was the
# holder-side heartbeat (kernel.renew_lease, expiry-only, fencing-checked).
# [W1-c4 2026-10-02] Runtime audit (same SHA) still measured q05 122.4s /
# q11 89.4s withheld as lifecycle_authority_lost: the golden /ask probe at
# 57.5s sat ~2.5s under the 60s TTL, so ANY renewal gap (the c3 heartbeat
# infra-fault bug, now fixed) forfeited the lease. The default TTL is raised
# 60 -> 120 seconds — still overridable via SCP_ASK_LEASE_TTL_SECONDS — so a
# transient renewal gap no longer reaches the expiry line, while a crashed
# worker is still detected within one TTL.
DEFAULT_ASK_LEASE_TTL_SECONDS = 120

# [W1-c3 2026-10-02] Bounded retry budget for transient infra faults raised
# by kernel.renew_lease inside _lease_heartbeat (see the docstring there):
# short backoff between attempts, at most this many CONSECUTIVE failures
# before the heartbeat stops and finalize routes by the current state.
# A success resets the counter, so a long-lived worker survives repeated
# isolated faults while a hard outage can never spin the loop forever.
_LEASE_RENEW_MAX_CONSECUTIVE_INFRA_FAILURES = 3
_LEASE_RENEW_INFRA_BACKOFF_S = 0.25


def ask_lease_ttl_seconds() -> int:
    """SCP_ASK_LEASE_TTL_SECONDS as int seconds; default 120 [W1-c4].

    Parse errors / zero / negative fall back to the default — importing or
    starting the adapter must never crash on a bad env value, and a
    non-positive TTL would violate the kernel lease contract (claim raises).
    """
    raw = os.environ.get("SCP_ASK_LEASE_TTL_SECONDS", "")
    try:
        value = int(raw.strip())
    except (TypeError, ValueError):
        return DEFAULT_ASK_LEASE_TTL_SECONDS
    if value <= 0:
        return DEFAULT_ASK_LEASE_TTL_SECONDS
    return value


def ask_lease_heartbeat_enabled() -> bool:
    """Kill switch SCP_ASK_LEASE_HEARTBEAT=0|false|off|no disables renewal.

    Kept for the deterministic anti-placebo control test (T04 S20): with the
    heartbeat off and a slow provider, the old lifecycle_authority_lost
    failure must reproduce — proving the test actually exercises the bug.
    """
    raw = os.environ.get("SCP_ASK_LEASE_HEARTBEAT", "").strip().lower() or "1"
    return raw not in {"0", "false", "off", "no"}


from scp.ask_adapter_parts import (
    append_unified_ledger,
    build_kernel_blocked_response,
    build_safe_response,
    build_unified_ledger_fields,
    dump_data,
    effective_run_identity,
    finalize_task_checkpoint,
    json_bytes,
    lookup_fork_self_certified,
    record_task_failure,
    run_lease_heartbeat,
    unified_ledger_path,
    unreadable_task,
    verification_fail_reason,
    verify_ask_response,
)


def _dump(obj: Any) -> dict[str, Any]:
    return dump_data(obj)


def _verification_fail_reason(response_data: dict[str, Any]) -> str:
    return verification_fail_reason(response_data)


def t2_fork_enabled() -> bool:
    """[S24] Mirror of scp.runtime.question_router.t2_fork_enabled, defined
    locally so run_rag's kill-switch check works even if the router module
    fails to import (fork off = old behavior, never a crash)."""
    raw = os.environ.get("SCP_T2_ROUTER", "").strip().lower() or "1"
    return raw not in {"0", "false", "off", "no"}


class AskKernelAdapter:
    """Durable lifecycle gate around the existing context-backed /ask path.

    The adapter does not replace JudgeCore. It calls the supplied handler first,
    then independently checks the returned answer against request evidence before
    allowing the TaskKernel to complete the task.
    """

    def __init__(self, db_path: str | None = None, trace_path: str | None = None, *, autonomous_mode: bool | None = None):
        if db_path is None:
            # [SEC 2026-09-25] tempfile.mktemp is the insecure predictable-name
            # pattern (Mimosa finding): the name can be raced between guess and
            # open. tempfile.mkstemp creates the file atomically (O_EXCL, 0600)
            # and returns the fd — close it and hand the name onward; SQLite
            # treats an existing empty file as a fresh database, same semantics
            # as before (same directory family: the process temp dir).
            import tempfile
            fd, db_path = tempfile.mkstemp(suffix=".sqlite", prefix="kernel_")
            os.close(fd)
        if trace_path is None:
            import tempfile
            fd, trace_path = tempfile.mkstemp(suffix=".jsonl", prefix="trace_")
            os.close(fd)
        if autonomous_mode is not None:
            self.autonomous_mode = bool(autonomous_mode)
        else:
            self.autonomous_mode = os.environ.get("SCP_AUTONOMOUS_MODE", "").strip().lower() in {"1", "true", "yes"}
        self.kernel = TaskKernel(db_path, autonomous_mode=self.autonomous_mode)
        self.trace = TraceLedger(trace_path)
        # [C3 — Gemini indictment: SQLite SPOF] Boot-time durability:
        # integrity quick_check + online backup với retention. Lỗi maintenance
        # KHÔNG bao giờ chặn serving (chỉ log) — nhưng hỏng được ghi nhận.
        self.last_maintenance: dict[str, Any] | None = None
        try:
            integrity = self.kernel.verify_integrity()
            if integrity["quick_check"] != "ok":
                _c3_logger.error("[C3] kernel DB quick_check FAILED: %s", integrity["quick_check"])
            backup_result = self.kernel.backup(
                Path(db_path).parent / "kernel-backups",
                retain=int(os.environ.get("SCP_KERNEL_BACKUP_RETENTION", "7")),
            )
            self.last_maintenance = {"integrity": integrity, "backup": backup_result}
            _c3_logger.info(
                "[C3] kernel maintenance ok: quick_check=%s backup=%s (%s bytes, retained=%s)",
                integrity["quick_check"], Path(backup_result["backup"]).name,
                backup_result["size_bytes"], backup_result["retained"],
            )
        except Exception as exc:  # durability check phải không bao giờ chặn serving
            logger.warning('AskKernelAdapter.__init__: Exception not handled: %s', exc, exc_info=True)
            _c3_logger.warning("[C3] kernel maintenance failed (non-blocking): %s", exc)
            self.last_maintenance = None

    def _existing_task(self, task_id: str) -> dict[str, Any] | None:
        try:
            return self.kernel.get_task(task_id)
        except Exception:
            logger.warning('AskKernelAdapter._existing_task: Exception not handled', exc_info=True)
            return None

    # Lifecycle states where a racing transport retry must still be deduped:
    # the first execution has not reached a decision yet.
    _IN_FLIGHT_STATES = {
        "CREATED", "PLANNING", "READY", "QUEUED", "LEASED", "RUNNING",
        "WAITING_TOOL", "VERIFYING", "CHECKPOINTED", "UNKNOWN",
        "RECOVERING", "RECONCILING", "RETRY_SCHEDULED",
    }

    @staticmethod
    def _duplicate_is_reaskable(existing: dict[str, Any] | None) -> bool:
        """A duplicate whose lifecycle has REACHED A DECISION (terminal, or
        HUMAN_REVIEW — the answer was withheld) is a finished ask: a new
        identical request is a legitimate new ask, not a transport retry.
        Only an in-flight duplicate must be deduped against."""
        if not existing:
            return False
        return existing.get("state") not in AskKernelAdapter._IN_FLIGHT_STATES

    @staticmethod
    def canonical_input_hash(question: str, contexts: list[str], retrieved_context: str) -> str:
        raw = json_bytes(
            {
                "question": question,
                "contexts": contexts,
                "retrieved_context": retrieved_context,
            }
        )
        return "sha256:" + hashlib.sha256(raw).hexdigest()

    @classmethod
    def task_id_for(
        cls,
        question: str,
        contexts: list[str],
        retrieved_context: str,
        session_id: str | None,
        request_id: str | None,
    ) -> str:
        """Durable identity for one logical ask. Exposed as a classmethod so
        harnesses derive the SAME id instead of mirroring (and drifting from)
        the private formula.

        [S19 BUG-2 fix 2026-09-13] The canonical question/evidence hash is
        ALWAYS part of the identity. The previous derivation let a client
        idempotency key REPLACE the hash entirely
        (``identity = request_id or f"{session}|{hash}"``), so a client that
        reuses one key (per-run key, per-provider key, benchmark replay) mapped
        EVERY different question onto the FIRST ask's task; create_task then
        collided and every later request died fail-closed with
        "stable logical ask already exists" — the endpoint locked after the
        first question. Idempotency semantics: a key (or session) dedupes
        REPEATS OF THE SAME question+evidence (transport retries), it must not
        act as a global mutex over unrelated questions. The key/session stays
        the retry SCOPE discriminator; the input hash stays the PER-QUESTION
        discriminator; a retry with the same key and same body still maps to
        the same durable id.
        """
        input_hash = cls.canonical_input_hash(question, contexts, retrieved_context)
        scope = request_id or session_id or ""
        return "ask-" + hashlib.sha256(f"{scope}|{input_hash}".encode()).hexdigest()[:24]

    def _task_id(
        self,
        question: str,
        contexts: list[str],
        retrieved_context: str,
        session_id: str | None,
        request_id: str | None,
    ) -> str:
        return self.task_id_for(question, contexts, retrieved_context, session_id, request_id)

    def _input_hash(self, question: str, contexts: list[str], retrieved_context: str) -> str:
        return self.canonical_input_hash(question, contexts, retrieved_context)

    @staticmethod
    def _request_id(request: Any) -> str | None:
        headers = getattr(request, "headers", None)
        if headers is None:
            return None
        value = headers.get("X-SCP-Idempotency-Key") or headers.get("Idempotency-Key")
        return str(value)[:200] if value else None

    def begin(
        self,
        question: str,
        contexts: list[str],
        retrieved_context: str,
        session_id: str | None,
        request: Any = None,
        _retried: bool = False,
    ) -> dict[str, Any]:
        request_id = self._request_id(request)
        task_id = self._task_id(question, contexts, retrieved_context, session_id, request_id)
        if _retried:
            # [FIX 2026-08-29] A terminal duplicate older than the window is a
            # legitimate NEW ask, not a transport retry. Uniquify the durable
            # id so repeat asks are not blocked for the database's lifetime.
            task_id = task_id + "-" + uuid.uuid4().hex[:8]
        input_hash = self._input_hash(question, contexts, retrieved_context)
        # [CHAIN-AUDIT: backpressure] Admission control — chặn intake trước khi
        # kernel ngập. Vượt cap → fail-closed (caller nhận blocked response),
        # thay vì tích dồn vô hạn task rồi chậm chết cả chuỗi.
        max_inflight = int(os.environ.get("SCP_ASK_MAX_INFLIGHT", "200"))
        if max_inflight > 0 and self.kernel.in_flight_count() >= max_inflight:
            raise KernelError(
                f"backpressure: in-flight ask tasks at cap {max_inflight} — try again later"
            )
        try:
            self.kernel.create_task(task_id, "ask-route", "rag-verified /ask", "R0", input_hash=input_hash)
            for state in ("PLANNING", "READY", "QUEUED"):
                self.kernel.transition(task_id, state, actor="ask-kernel-adapter", reason="ask_lifecycle")
            lease_ttl = ask_lease_ttl_seconds()
            lease = self.kernel.claim(task_id, "ask-route-worker", ttl_seconds=lease_ttl)
            self.kernel.start(task_id, lease.lease_id)
            logical_key, claimed = self.kernel.idempotency_claim(
                task_id,
                "rag-read",
                "rag.context.read",
                input_hash,
            )
            if not claimed:
                raise KernelError("logical ask action already claimed")
            # [F-03] The exact planned_action object is kept in the returned
            # task handle: finalize needs it to re-present the same bytes to
            # kernel.finalize_checkpoint (planned_action_hash match) — no
            # reconstruction drift.
            checkpoint_planned_action = {
                "operation": "rag-verified-read",
                "risk_tier": "R0",
                "contexts_count": len(contexts),
                "retrieved_context_present": bool(retrieved_context.strip()),
            }
            checkpoint_id = self.kernel.checkpoint(
                task_id,
                lease.lease_id,
                "rag-read",
                "RUNNING",
                checkpoint_planned_action,
                0,
                logical_key,
                pre_observation_ref=f"ask://{task_id}/pre",
            )
            with _TRACE_LOCK:
                self.trace.append(
                    task_id=task_id,
                    attempt_id=lease.attempt_id,
                    step_id="rag-read",
                    lease_id=lease.lease_id,
                    checkpoint_id=checkpoint_id,
                    outcome="STARTED",
                    input_hash=input_hash,
                )
            return {
                "task_id": task_id,
                "lease_id": lease.lease_id,
                "attempt_id": lease.attempt_id,
                "fencing_token": lease.fencing_token,
                "lease_ttl_seconds": lease_ttl,
                "checkpoint_id": checkpoint_id,
                "checkpoint_planned_action": checkpoint_planned_action,
                "input_hash": input_hash,
            }
        except (sqlite3.IntegrityError, StorageIntegrityError) as exc:
            # Duplicate durable identity: within the idempotency window this is
            # a transport retry — a safe block, not a second handler execution.
            # Past the window a terminal duplicate is a NEW ask — re-ask once
            # with a uniquified id instead of blocking forever.
            existing = self._existing_task(task_id)
            if not _retried and self._duplicate_is_reaskable(existing):
                return self.begin(question, contexts, retrieved_context, session_id, request=request, _retried=True)
            try:
                current = self.kernel.get_task(task_id)
            except Exception:
                logger.warning('AskKernelAdapter.begin: Exception not handled', exc_info=True)
                current = {"state": "UNKNOWN"}
            with _TRACE_LOCK:
                self.trace.append(
                    task_id=task_id,
                    outcome="REJECTED_DUPLICATE",
                    reason="stable_idempotency_duplicate",
                    input_hash=input_hash,
                    existing_state=current.get("state"),
                )
            raise KernelError("stable logical ask already exists") from exc
        except Exception as exc:
            try:
                current = self.kernel.get_task(task_id)
                if current["state"] not in _TERMINAL:
                    self.kernel.set_task_kill(task_id, actor="ask-kernel-adapter-begin-failure")
            except Exception as cleanup_exc:
                # [R20] create_task đã fail → task có thể chưa từng tồn tại:
                # get_task văng NotFound sẽ ghi đè KernelError gốc (che giấu
                # nguyên nhân thật — thiếu K_journal, DB IO...). Nuốt CÓ LOG
                # (không silent-pass theo T00) để re-raise exception gốc bên dưới.
                logger.warning("begin() cleanup after failure could not inspect task %s: %s",
                               task_id, cleanup_exc)
            finally:
                with _TRACE_LOCK:
                    self.trace.append(
                        task_id=task_id,
                        outcome="FAILED",
                        reason="ask_begin_failed",
                        error_type=type(exc).__name__,
                        input_hash=input_hash,
                    )
            raise

    @staticmethod
    def _lookup_fork_self_certified(data: dict[str, Any]) -> bool:
        return lookup_fork_self_certified(data)

    async def verify_response(self, req: Any, response: Any, task: dict[str, Any] | None = None) -> dict[str, Any]:
        return await verify_ask_response(req, response, task)

    def _safe_response(self, response: Any, verification: dict[str, Any]) -> Any:
        return build_safe_response(response, verification)


    def _escalate_to_human_review(
        self,
        task_id: str,
        reason: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Move a task to HUMAN_REVIEW idempotently — never raise.

        [S19/W2 BUG-1 fix 2026-09-13] The W2 witness observed HTTP 500 from
        ``InvalidTransition: HUMAN_REVIEW->HUMAN_REVIEW`` when a task already
        sat in HUMAN_REVIEW (lease expiry sweeps VERIFYING->HUMAN_REVIEW, boot
        recovery moves RUNNING/VERIFYING->HUMAN_REVIEW) and finalize escalated
        again. A second escalation of the SAME decision is an idempotent
        no-op: the outcome (answer withheld, human required) is already
        recorded — re-raising would only crash the client request. A raced
        state move (concurrent watchdog, released/expired lease) is likewise
        skipped with a log; the lifecycle decision belongs to the recovery
        machinery, not to this request path.
        [R22] An unreadable task (purged, storage error) yields an explicit
        UNKNOWN result instead of an escaping NotFound.
        """
        try:
            current = self.kernel.get_task(task_id)
        except Exception as read_exc:
            return unreadable_task(task_id, read_exc)
        if current["state"] in _TERMINAL:
            return current
        if current["state"] == "HUMAN_REVIEW":
            logger.info(
                "[ask-kernel] HUMAN_REVIEW escalation for %s is already recorded "
                "(idempotent no-op): %s",
                task_id,
                reason,
            )
            return current
        try:
            return self.kernel.transition(
                task_id,
                "HUMAN_REVIEW",
                actor="ask-kernel-adapter",
                reason=reason,
                payload=payload,
            )
        except KernelError as exc:  # InvalidTransition / StaleLease / OptimisticLockError
            logger.warning(
                "[ask-kernel] HUMAN_REVIEW escalation skipped for %s in state %s "
                "(%s: %s): %s",
                task_id,
                current["state"],
                type(exc).__name__,
                exc,
                reason,
            )
            try:
                return self.kernel.get_task(task_id)
            except Exception as read_exc:  # [R22] never let NotFound replace exc
                return unreadable_task(task_id, read_exc, exc)

    def _fail_closed_autonomous(
        self,
        task_id: str,
        reason: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Move a task to FAILED idempotently in autonomous mode — never raise.

        [R22] An unreadable task (purged, storage error) yields an explicit UNKNOWN result.
        """
        try:
            current = self.kernel.get_task(task_id)
        except Exception as read_exc:
            return unreadable_task(task_id, read_exc)
        if current["state"] in _TERMINAL:
            return current
        try:
            return self.kernel.fail_task_fail_closed(
                task_id,
                reason=reason,
                error_payload=payload,
                actor="ask-kernel-adapter",
            )
        except KernelError as exc:
            logger.warning(
                "[ask-kernel] Autonomous fail-closed skipped for %s in state %s "
                "(%s: %s): %s",
                task_id,
                current["state"],
                type(exc).__name__,
                exc,
                reason,
            )
            try:
                return self.kernel.get_task(task_id)
            except Exception as read_exc:  # [R22] never let NotFound replace exc
                return unreadable_task(task_id, read_exc, exc)

    def _finalize_task_checkpoint(
        self,
        task: dict[str, Any],
        verification: dict[str, Any],
        note: str | None = None,
    ) -> None:
        finalize_task_checkpoint(self.kernel, task, verification, note)

    @staticmethod
    def _effective_run_identity(response_data: dict[str, Any], request: Any) -> tuple[str, Any]:
        return effective_run_identity(response_data, request)

    @staticmethod
    def _unified_ledger_path() -> Path:
        return unified_ledger_path()

    def _unified_ledger_fields(
        self,
        *,
        req: Any,
        request: Any,
        response: Any,
        verification: dict[str, Any],
        final_task: dict[str, Any],
        response_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build unified trace-ledger fields; final_answer is pre-safe provenance."""
        return build_unified_ledger_fields(
            req=req,
            request=request,
            response=response,
            verification=verification,
            final_task=final_task,
            response_data=response_data,
        )

    def _append_unified_ledger(self, fields: dict[str, Any]) -> None:
        append_unified_ledger(fields)


    async def finalize(self, task: dict[str, Any], response: Any, req: Any, request: Any = None) -> dict[str, Any]:
        from scp.ask_adapter_parts.finalize_flow import finalize_ask_task

        return await finalize_ask_task(
            self,
            task,
            response,
            req,
            request=request,
            terminal_states=_TERMINAL,
            intact_states=_ASK_LIFECYCLE_INTACT_STATES,
            trace_lock=_TRACE_LOCK,
            sign_receipt_fn=sign_verifier_receipt,
        )

    def fail(
        self,
        task: dict[str, Any],
        reason: str,
        failure_classification: str = "FATAL",
        indictment_ref: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        record_task_failure(
            self,
            task,
            reason,
            failure_classification=failure_classification,
            indictment_ref=indictment_ref,
            details=details,
            terminal_states=_TERMINAL,
            trace_lock=_TRACE_LOCK,
        )

    def _kernel_blocked_response(self, req: Any, exc: Exception) -> Any:
        return build_kernel_blocked_response(req, exc, AskResponse)

    async def _lease_heartbeat(
        self,
        task: dict[str, Any],
        stop: asyncio.Event,
    ) -> None:
        await run_lease_heartbeat(
            self.kernel,
            task,
            stop,
            default_ttl=DEFAULT_ASK_LEASE_TTL_SECONDS,
            max_consecutive_infra_failures=_LEASE_RENEW_MAX_CONSECUTIVE_INFRA_FAILURES,
            infra_backoff_s=_LEASE_RENEW_INFRA_BACKOFF_S,
            c3_logger=_c3_logger,
        )

    async def run_rag(
        self,
        req: Any,
        request: Any,
        handler: Callable[[Any, Any], Awaitable[Any]],
    ) -> Any:
        try:
            task = self.begin(
                req.question,
                list(req.contexts or []),
                req.retrieved_context or "",
                req.session_id,
                request=request,
            )
        except Exception as exc:
            logger.debug(f"run_rag ignored: {exc}", exc_info=True)
            return self._kernel_blocked_response(req, exc)
        # [S20] Provider calls legitimately run 30-260s (free tier); the fixed
        # TTL would expire the lease under a living worker and force S19's
        # fail-closed state-route to discard correct answers. Keep the lease
        # alive ONLY while this coroutine owns the attempt; crash detection is
        # preserved because a dead process stops renewing.
        stop = asyncio.Event()
        heartbeat: asyncio.Task | None = None
        if ask_lease_heartbeat_enabled():
            heartbeat = asyncio.create_task(self._lease_heartbeat(task, stop))
        try:
            # [S24 2026-09-13] LOOKUP→data-API fork TRƯỚC generation (owner
            # directive: "hơn 1000 API để lấy thông tin. LLM CHỈ dùng khi 1000
            # API không có" — trước S24 mọi câu bay thẳng LLM, see
            # reports/benchmark-2026-09-12/bench_combined_seed2026.json).
            # Fork trả None → generation path cũ chạy nguyên vẹn. Bất kỳ lỗi
            # nào trong fork cũng phải fallback về handler (fork không được
            # phép phá /ask). Answer fork vẫn đi qua finalize/verify bên dưới
            # — cùng verification/governance path, không có check nào bị bỏ.
            fork_response: Any = None
            if t2_fork_enabled():
                try:
                    from scp.runtime.question_router import attempt_lookup_fork

                    fork_response = await attempt_lookup_fork(req)
                except Exception as exc:
                    logger.debug(f"run_rag ignored: {exc}", exc_info=True)
                    logger.warning(
                        "[S24] lookup fork errored (%s: %s) — falling back to LLM handler",
                        type(exc).__name__, exc,
                    )
                    fork_response = None
            if fork_response is not None:
                response = fork_response
            else:
                response = await handler(req, request)
            result = await self.finalize(task, response, req, request=request)
            return result["safe_response"]
        except Exception:
            self.fail(task, "ask_rag_exception")
            raise
        finally:
            if heartbeat is not None:
                stop.set()
                try:
                    await asyncio.wait_for(heartbeat, timeout=5.0)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    heartbeat.cancel()



