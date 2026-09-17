# SCP CIRCUIT: M02 — STATUS: CLOSED_WITH_KNOWN_GAP (closure: reports/circuit-closures/M02-closure.json)
from __future__ import annotations

import asyncio
import hashlib
import logging
import math
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
# discarded a real, correct answer (2/10 asks on seed 99). The fix is NOT a
# bigger TTL (that just slows real crash detection): the holder renews the
# lease while it is alive (kernel.renew_lease, expiry-only, fencing-checked),
# so an expired lease again proves the worker is *dead*, not merely *slow*.
DEFAULT_ASK_LEASE_TTL_SECONDS = 60


def ask_lease_ttl_seconds() -> int:
    """SCP_ASK_LEASE_TTL_SECONDS as int seconds; default 60 preserved.

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


# [Q07 2026-09-15] Controlled self-correction (Reflection) wiring.
# Budget là best-effort: mỗi bước (regenerate, canonical re-verify) bị chặn
# riêng bởi giá trị này; khi vượt, withhold fail-closed. Free-tier provider có
# p50 generate 5-30s nhưng p95 tới hàng trăm giây; phương án 25s từng được cân
# nhắc khi thiết kế sẽ khiến vòng self-refine timeout thầm lặng trên provider
# trung bình-chậm -> feature gần như tắt ngầm, F_self_correction không tăng.
# Default ship = 40s phản ánh latency generate
# thực tế đo được, vẫn <= lease TTL 60s (heartbeat S20 giữ lease sống trong lúc
# regen) và <= MAX; provider cực chậm vẫn timeout->withhold (đúng, chỉ không tăng
# được recall). Muốn rộng hơn cho air-gapped model nhanh: chỉnh qua env.
DEFAULT_ASK_REFLECTION_TIMEOUT_SECONDS = 40.0
MAX_ASK_REFLECTION_TIMEOUT_SECONDS = 60.0


def ask_reflection_enabled() -> bool:
    """Kill switch SCP_ASK_REFLECTION=0|false|off|no tắt vòng self-refine.

    Mặc định BẬT. Khi tắt, finalize giữ NGUYÊN hành vi cũ (FAIL/UNKNOWN ->
    withhold -> HUMAN_REVIEW) — không có LLM call thêm nào. Đây là van an toàn
    để tắt gấp nếu vòng regenerate làm chậm/pipeline lỗi ở production."""
    raw = os.environ.get("SCP_ASK_REFLECTION", "").strip().lower() or "1"
    return raw not in {"0", "false", "off", "no"}


def ask_reflection_timeout_seconds() -> float:
    """Budget tường minh cho MỘT vòng critique->regenerate + canonical re-verify.

    Invalid / non-finite / non-positive / quá lớn -> fail-closed về default
    (giống lookup_timeout_seconds): một giá trị env rác không được phép mở
    đường cho vòng regenerate chạy vô hạn và giữ lease quá lâu. Trần cứng
    MAX_ASK_REFLECTION_TIMEOUT_SECONDS để tổng thời gian finalize vẫn nằm
    trong cửa sổ HTTP /ask (benchmark timeout=120s)."""
    raw = os.environ.get("SCP_ASK_REFLECTION_TIMEOUT_SECONDS", "").strip()
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_ASK_REFLECTION_TIMEOUT_SECONDS
    if not math.isfinite(value) or value <= 0 or value > MAX_ASK_REFLECTION_TIMEOUT_SECONDS:
        return DEFAULT_ASK_REFLECTION_TIMEOUT_SECONDS
    return value


def _record_correction_attempt() -> None:
    """[Q07] KPI seam fail-safe — reflection metrics là observability, không
    được phép làm hỏng đường /ask. Import lỗi -> debug log, continue."""
    try:
        from scp.runtime.question_router import record_correction_attempt

        record_correction_attempt()
    except Exception as exc:
        logger.debug("[Q07] correction-attempt KPI unavailable: %s", exc)


def _record_correction_result(ok: bool, *, timed_out: bool = False) -> None:
    try:
        from scp.runtime.question_router import record_correction_result

        record_correction_result(ok, timed_out=timed_out)
    except Exception as exc:
        logger.debug("[Q07] correction-result KPI unavailable: %s", exc)


def _dump(obj: Any) -> dict[str, Any]:
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if hasattr(obj, "dict"):
        return obj.dict()
    if isinstance(obj, dict):
        return obj
    return dict(vars(obj))


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

    def __init__(self, db_path: str, trace_path: str):
        self.kernel = TaskKernel(db_path)
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
            logger.warning('AskKernelAdapter.__init__: Exception not handled: %s', exc)
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
            checkpoint_id = self.kernel.checkpoint(
                task_id,
                lease.lease_id,
                "rag-read",
                "RUNNING",
                {
                    "operation": "rag-verified-read",
                    "risk_tier": "R0",
                    "contexts_count": len(contexts),
                    "retrieved_context_present": bool(retrieved_context.strip()),
                },
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

    async def verify_response(self, req: Any, response: Any, task: dict[str, Any]) -> dict[str, Any]:
        data = _dump(response)
        contexts = [str(value) for value in (getattr(req, "contexts", None) or []) if str(value).strip()]
        retrieved_context = str(getattr(req, "retrieved_context", "") or "").strip()
        if retrieved_context:
            contexts.append(retrieved_context)
        answer = str(data.get("final_answer") or "")
        verdict = str(data.get("verdict") or "")
        governance = str(data.get("governance_decision") or "")
        # [S24] LOOKUP fork evidence: với answer compose từ data-API, payload
        # thô của API CHÍNH LÀ input context mà answer được tạo từ — đưa vào
        # cùng grounding/judge check (THÊM bằng chứng; không bỏ/nới check nào).
        fork_evidence = str(data.get("data_api_evidence") or "").strip()
        if fork_evidence:
            contexts.append(fork_evidence)
        provenance_value = (data.get("v98_classification") or {}).get("provenance")
        provenance = str(provenance_value or "")
        evidence_ref = f"ask://{task['task_id']}/response/{data.get('trace_id') or 'no-trace'}"
        if not answer or answer.startswith("[SCP:"):
            return {
                "verdict": "INSUFFICIENT",
                "verifier_id": "scp-ask-rag-verifier-v2",
                "evidence_ref": evidence_ref,
                "failures": ["missing_answer"],
                "checked": [],
            }

        if not contexts:
            grounded_ratio = 0.0
        else:
            import re
            ans_words = set(re.findall(r"[\w\xc0-\u1ef9]{2,}", answer.lower()))
            if not ans_words:
                grounded_ratio = 0.0
            else:
                ctx_text = " ".join(contexts).lower()
                overlap = sum(1 for w in ans_words if w in ctx_text)
                grounded_ratio = overlap / len(ans_words)

        # --- Wire RealityJudge into production (Q1: A) ---
        # [ROOT FIX] Real LLM Semantic Judge is used. Context is passed to judge factual grounding.
        try:
            from scp.runtime.judge import RealityJudge
            judge = RealityJudge()
            judge_res = await judge.judge_async(
                question=str(getattr(req, "question", "")),
                ai_answer=answer,
                context=" ".join(contexts)
            )
            try:
                from scp.runtime.question_router import record_verifier_call

                record_verifier_call(ok=True)
            except Exception as exc:
                logger.debug("[KPI] verifier outcome counter unavailable: %s", exc)
            judge_pass = (judge_res["verdict"] == "PASS")
        except Exception:
            try:
                from scp.runtime.question_router import record_verifier_call

                record_verifier_call(ok=False)
            except Exception as exc:
                logger.debug("[KPI] verifier failure counter unavailable: %s", exc)
            logger.warning('AskKernelAdapter.verify_response: Exception not handled', exc_info=True)
            judge_pass = False

        # Contract (2026-08-29), split explicitly:
        #   - Context-backed (RAG) ask: the request carried evidence, so the
        #     answer is held to the grounding contract; grounded_ratio and the
        #     evidence context hash are recorded for audit.
        #   - General chat ask (no contexts): grounding is not applicable —
        #     the judge + governance pipeline is the verifier. This matches
        #     api_server's documented intent ("Task Kernel integration is
        #     deliberately scoped to context-backed/RAG asks; normal chat
        #     keeps the JudgeCore path"): the kernel lifecycle still wraps
        #     chat for durability, but grounding cannot be demanded from a
        #     request that carries no evidence.
        is_rag_ask = bool(contexts)
        checks = {
            "verdict_pass": verdict == "PASS",
            "judge_pass": judge_pass,
            "governance_uphold": governance == "UPHOLD",
            "web_fallback_not_used": not bool(data.get("web_fallback_used")),
            # Empty provenance is tolerated for old GA-LAB responses; if the
            # route supplies one, it must explicitly be input-context-only.
            "provenance_compatible": provenance in {"", "input_context_only"},
        }
        if is_rag_ask:
            checks["rag_evidence_bound"] = True
        failures = [name for name, ok in checks.items() if not ok]
        return {
            "verdict": "VERIFIED" if not failures else "CONTRADICTED",
            "verifier_id": "scp-ask-rag-verifier-v2",
            "evidence_ref": evidence_ref,
            "grounded_ratio": round(grounded_ratio, 6),
            "evidence_context_count": len(contexts),
            "evidence_context_hash": hashlib.sha256(
                json_bytes(contexts)
            ).hexdigest(),
            "checked": checks,
            "failures": failures,
        }

    def _safe_response(self, response: Any, verification: dict[str, Any]) -> Any:
        if verification.get("verdict") == "VERIFIED":
            return response
        data = _dump(response)
        fail_reasons = ', '.join(verification.get('failures', []))
        if not str(data.get("final_answer", "")).startswith("[SCP:"):
                    data["final_answer"] = f"[SCP: Answer withheld — evidence not verified: {fail_reasons}]"
        data["verdict"] = "FAIL"
        data["governance_decision"] = "KILL" if data.get("governance_decision") == "KILL" else "ESCALATE"
        data["confidence"] = 0.0
        if hasattr(response, "model_copy"):
            return response.model_copy(update=data)
        if isinstance(response, dict):
            return data
        if hasattr(response, "copy"):
            try:
                return response.copy(update=data)
            except TypeError:
                logger.debug('AskKernelAdapter._safe_response: TypeError ignored', exc_info=True)
                return data
        return data

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
        """
        current = self.kernel.get_task(task_id)
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
            return self.kernel.get_task(task_id)

    # ------------------------------------------------------------------
    # [Q07 2026-09-15] Controlled self-correction (Reflection) — epistemic hold
    # ------------------------------------------------------------------
    def _clone_for_reflection(self, req: Any, response: Any, verification: dict[str, Any]) -> Any | None:
        """Shape a regenerate request for the primary handler.

        critique->regenerate: câu trả lời thất bại + lý do từ canonical verifier
        (KHÔNG phải claim đúng/sai của Reflection) được nhét vào conversation
        history làm CONTEXT; ai_answer được XÓA để handler sinh lại answer mới.
        question/contexts/retrieved_context giữ NGUYÊN để round-2 đi qua đúng
        cùng gate. Trả về None nếu không clone được an toàn (-> skip reflection,
        withhold như cũ — fail-closed, không bao giờ phá request)."""
        try:
            from scp.ai_patterns import Reflection

            data = _dump(response)
            failed_answer = str(data.get("final_answer") or "")
            question = str(getattr(req, "question", "") or "")
            critique = Reflection.critique_turns(question, failed_answer, verification)
            history = list(getattr(req, "conversation_history", None) or [])
            merged = (history + critique)[-8:]  # AskRequest.conversation_history max_length=8
        except Exception as exc:  # pragma: no cover - defensive: never break /ask
            logger.debug("[Q07] critique shaping failed, skipping reflection: %s", exc)
            return None
        try:
            if hasattr(req, "model_copy"):  # pydantic AskRequest
                return req.model_copy(update={"ai_answer": "", "conversation_history": merged})
        except Exception as exc:
            logger.debug("[Q07] pydantic clone failed, trying attribute copy: %s", exc)
        try:
            import copy as _copy

            clone = _copy.copy(req)  # shallow: question/contexts giữ nguyên tham chiếu
            for name, value in (("ai_answer", ""), ("conversation_history", merged)):
                try:
                    setattr(clone, name, value)
                except Exception:
                    object.__setattr__(clone, name, value)
            return clone
        except Exception as exc:
            logger.debug("[Q07] generic clone failed, skipping reflection: %s", exc)
            return None

    async def _self_refine_once(
        self,
        task: dict[str, Any],
        response: Any,
        req: Any,
        verification: dict[str, Any],
        handler: Any,
        request: Any,
    ) -> tuple[Any, dict[str, Any], dict[str, Any]] | None:
        """Chạy TỐI ĐA MỘT vòng self-refine khi canonical verify FAIL/UNKNOWN.

        Bất biến an toàn (Q07):
          * Reflection KHÔNG tự approve. Answer mới chỉ được nhận nếu nó qua
            LẠI đúng canonical ``verify_response`` (cùng 5 check, không nới).
          * Budget=1: hàm này được finalize gọi đúng một lần trong nhánh
            epistemic-hold; ``Reflection.MAX_REFLECTIONS`` = 1 là hợp đồng.
          * Timeout: regenerate + re-verify bọc trong wait_for; quá hạn ->
            skip, withhold như cũ (fail-closed). Không có vòng lặp vô hạn.
          * Handler bắt buộc (primary pipeline chạy LẠI đầy đủ governance/
            attack/multimodal cho answer mới) — nếu thiếu handler thì KHÔNG
            reflection, giữ nguyên hành vi cũ (call-site legacy/test).
        Trả về (refined_response, verification2, meta) khi round-2 VERIFIED,
        ngược lại None (finalize sẽ escalate như trước)."""
        meta = {"attempted": False, "accepted": False, "outcome": "not_attempted"}
        if handler is None:
            return None
        try:
            from scp.ai_patterns import Reflection

            if not Reflection.should_reflect(verification):
                return None
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("[Q07] reflection predicate unavailable: %s", exc)
            return None
        if not ask_reflection_enabled():
            meta["outcome"] = "disabled"
            return None

        meta["attempted"] = True
        _record_correction_attempt()
        retry_req = self._clone_for_reflection(req, response, verification)
        if retry_req is None:
            meta["outcome"] = "clone_failed"
            _record_correction_result(False)
            return None

        budget = ask_reflection_timeout_seconds()
        try:
            refined = await asyncio.wait_for(handler(retry_req, request), timeout=budget)
            verification2 = await asyncio.wait_for(
                self.verify_response(req, refined, task), timeout=budget
            )
        except asyncio.TimeoutError:
            meta["outcome"] = "timeout"
            _record_correction_result(False, timed_out=True)
            logger.warning(
                "[Q07] self-refine exceeded %.1fs for task %s — withholding (fail-closed, no gate change)",
                budget, task.get("task_id"),
            )
            return None
        except Exception as exc:
            meta["outcome"] = f"error:{type(exc).__name__}"
            _record_correction_result(False)
            logger.warning(
                "[Q07] self-refine errored (%s: %s) — withholding (fail-closed)",
                type(exc).__name__, exc,
            )
            return None

        # Canonical re-verification là duy nhất có quyền nhận answer mới.
        if verification2.get("verdict") == "VERIFIED":
            meta["accepted"] = True
            meta["outcome"] = "accepted"
            _record_correction_result(True)
            return refined, verification2, meta
        meta["outcome"] = "rejected"
        _record_correction_result(False)
        return None

    async def finalize(
        self,
        task: dict[str, Any],
        response: Any,
        req: Any,
        handler: Any = None,
        request: Any = None,
    ) -> dict[str, Any]:
        task_id, lease_id = task["task_id"], task["lease_id"]
        reflection_meta: dict[str, Any] = {"attempted": False, "accepted": False, "outcome": "not_attempted"}

        def _terminal_result(current_task: dict[str, Any]) -> dict[str, Any]:
            verification = {
                "verdict": "INSUFFICIENT",
                "verifier_id": "scp-ask-kernel-terminal-v1",
                "evidence_ref": f"ask://{task_id}/terminal/{str(current_task['state']).lower()}",
                "failures": ["task_terminal_before_verification"],
                "checked": {"kernel_task_non_terminal": False},
            }
            response_data = _dump(response)
            with _TRACE_LOCK:
                self.trace.append(
                    task_id=task_id,
                    attempt_id=task.get("attempt_id"),
                    step_id="rag-read",
                    lease_id=lease_id,
                    checkpoint_id=task.get("checkpoint_id"),
                    verifier_id=verification["verifier_id"],
                    evidence_ref=verification["evidence_ref"],
                    run_id=response_data.get("run_id"),
                    trace_id=response_data.get("trace_id"),
                    outcome=current_task["state"],
                    verdict=verification["verdict"],
                    reason="task_terminal_before_verification",
                    response_elapsed_ms=response_data.get("elapsed_ms"),
                )
            return {
                "task": current_task,
                "verification": verification,
                "safe_response": self._safe_response(response, verification),
            }

        current_task = self.kernel.get_task(task_id)
        if current_task["state"] in _TERMINAL:
            return _terminal_result(current_task)

        def _stale_lifecycle_result(stale_task: dict[str, Any], reason: str) -> dict[str, Any]:
            """[S19 BUG-1 fix 2026-09-13] The response was observed after the
            kernel had already moved this task out of the happy path
            (RECONCILING via boot-replay/watchdog — the runtime crash
            ``InvalidTransition: RECONCILING->VERIFYING`` at ask_response_
            observed; also RECOVERING/UNKNOWN/HUMAN_REVIEW and friends).

            Design decision (b): route by current state instead of widening
            ALLOWED_TRANSITIONS with RECONCILING->VERIFYING. Adding that edge
            would let a stale attempt auto-complete work whose execution
            authority was already revoked: enter_reconciling/auto_reconcile
            release the lease (fencing), and the reconcile contract
            (recovery_decision 'human_review_if_unknown', reconcile_unknown
            'without auto-completing') deliberately exits RECONCILING to
            HUMAN_REVIEW, not to VERIFYING. Fail-closed honesty wins: the
            client gets an explicit withheld result (HTTP 200, no 500), the
            task lands in HUMAN_REVIEW via a legal edge for the reconcile/
            human flow, and the journal records that a response WAS observed.
            """
            verification = {
                "verdict": "INSUFFICIENT",
                "verifier_id": "scp-ask-kernel-lifecycle-race-v1",
                "evidence_ref": f"ask://{task_id}/lifecycle/{str(stale_task['state']).lower()}",
                "failures": [reason],
                "checked": {"kernel_execution_authority": False},
            }
            response_data = _dump(response)
            final_task = self._escalate_to_human_review(
                task_id,
                reason=reason,
                payload={"verification": verification},
            )
            with _TRACE_LOCK:
                self.trace.append(
                    task_id=task_id,
                    attempt_id=task.get("attempt_id"),
                    step_id="rag-read",
                    lease_id=lease_id,
                    checkpoint_id=task.get("checkpoint_id"),
                    verifier_id=verification["verifier_id"],
                    evidence_ref=verification["evidence_ref"],
                    run_id=response_data.get("run_id"),
                    trace_id=response_data.get("trace_id"),
                    outcome=final_task["state"],
                    verdict=verification["verdict"],
                    reason=reason,
                    response_elapsed_ms=response_data.get("elapsed_ms"),
                )
            return {
                "task": final_task,
                "verification": verification,
                "safe_response": self._safe_response(response, verification),
            }

        if current_task["state"] not in _ASK_LIFECYCLE_INTACT_STATES:
            return _stale_lifecycle_result(
                current_task,
                f"lifecycle_authority_lost:{current_task['state']}",
            )

        try:
            if current_task["state"] == "RUNNING":
                self.kernel.transition(task_id, "VERIFYING", actor="ask-kernel-adapter", reason="ask_response_observed")
        except InvalidTransition:
            current_task = self.kernel.get_task(task_id)
            if current_task["state"] in _TERMINAL:
                return _terminal_result(current_task)
            return _stale_lifecycle_result(
                current_task,
                f"verifying_transition_raced:{current_task['state']}",
            )

        verification = await self.verify_response(req, response, task)
        # [Q07] Epistemic hold (canonical verify FAIL/UNKNOWN): thử ĐÚNG MỘT
        # vòng self-refine (critique->regenerate) với budget + timeout, rồi verify
        # LẠI. Chỉ nhận answer nếu round-2 qua CÙNG canonical verify_response —
        # gate không đổi, Reflection không tự approve. Vẫn fail -> withhold như cũ.
        if verification["verdict"] != "VERIFIED" and handler is not None and ask_reflection_enabled():
            refine_outcome = await self._self_refine_once(
                task, response, req, verification, handler, request
            )
            if refine_outcome is not None:
                response, verification, reflection_meta = refine_outcome
        if verification["verdict"] == "VERIFIED":
            receipt = sign_verifier_receipt(
                VerifierReceipt(
                    task_id=task_id,
                    verifier_id=str(verification.get("verifier_id", "scp-ask-rag-verifier-v2")),
                    verdict="VERIFIED",
                    evidence_ref=str(verification.get("evidence_ref", "")),
                    issued_at=time.time(),
                    attempt_id=task.get("attempt_id"),
                )
            )
            verification["task_id"] = task_id
            verification["issued_at"] = receipt.issued_at
            verification["signature"] = receipt.signature
            try:
                final_task = self.kernel.commit_verification_result(task_id, lease_id, receipt)
            except KernelError:
                # [S19/W2 family] A slow verification can outlive the 60s ask
                # lease: expire_leases sweeps VERIFYING->HUMAN_REVIEW while the
                # judge runs, so commit hits a stale lease or an illegal
                # source state. That must not 500 the request either — the
                # evidence was verified but the execution authority is gone:
                # route through the same lifecycle-race path.
                current_task = self.kernel.get_task(task_id)
                if current_task["state"] in _TERMINAL:
                    return _terminal_result(current_task)
                return _stale_lifecycle_result(current_task, "commit_raced_lease_or_state")
        else:
            final_task = self._escalate_to_human_review(
                task_id,
                reason="ask_evidence_insufficient_or_contradicted",
                payload={"verification": verification},
            )
        response_data = _dump(response)
        if reflection_meta.get("attempted"):
            # [Q07] Ghi riêng một bước reflection vào ledger để audit được
            # attempts/success mà không lẫn vào outcome cuối của rag-read.
            with _TRACE_LOCK:
                self.trace.append(
                    task_id=task_id,
                    attempt_id=task.get("attempt_id"),
                    step_id="reflection",
                    lease_id=lease_id,
                    checkpoint_id=task.get("checkpoint_id"),
                    outcome=reflection_meta.get("outcome"),
                    accepted=bool(reflection_meta.get("accepted")),
                )
        with _TRACE_LOCK:
            self.trace.append(
                task_id=task_id,
                attempt_id=task.get("attempt_id"),
                step_id="rag-read",
                lease_id=lease_id,
                checkpoint_id=task.get("checkpoint_id"),
                verifier_id=verification.get("verifier_id"),
                evidence_ref=verification.get("evidence_ref"),
                run_id=response_data.get("run_id"),
                trace_id=response_data.get("trace_id"),
                outcome=final_task.get("state"),
                verdict=verification.get("verdict"),
                grounded_ratio=verification.get("grounded_ratio"),
                response_elapsed_ms=response_data.get("elapsed_ms"),
            )
        return {
            "task": final_task,
            "verification": verification,
            "reflection": reflection_meta,
            "safe_response": self._safe_response(response, verification),
        }

    def fail(
        self,
        task: dict[str, Any],
        reason: str,
        failure_classification: str = "FATAL",
        indictment_ref: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        try:
            current = self.kernel.get_task(task["task_id"])
            if current["state"] not in _TERMINAL:
                lease_id = task.get("lease_id") or current.get("active_lease_id")
                if lease_id:
                    self.kernel.commit_failed(
                        task_id=task["task_id"],
                        lease_id=lease_id,
                        actor=task.get("worker_id") or "ask-route-worker",
                        failure_classification=failure_classification,
                        indictment_ref=indictment_ref or f"ask://{task['task_id']}/failure/{reason}",
                        details=details or {"reason": reason},
                    )
                else:
                    self.kernel.set_task_kill(task["task_id"], actor="ask-kernel-adapter")
            with _TRACE_LOCK:
                self.trace.append(
                    task_id=task["task_id"],
                    attempt_id=task.get("attempt_id"),
                    lease_id=task.get("lease_id"),
                    checkpoint_id=task.get("checkpoint_id"),
                    outcome="FAILED",
                    reason=reason,
                )
        except Exception as exc:  # non-fatal audit fallback; original error wins
            logger.warning('AskKernelAdapter.fail: Exception not handled: %s (%s)', exc, type(exc).__name__)

    def _kernel_blocked_response(self, req: Any, exc: Exception) -> Any:
        session = getattr(req, "session_id", None) or "ask-kernel-blocked"
        trace_id = "trace-kernel-blocked-" + uuid.uuid4().hex
        msg = f"[SCP: Answer withheld — Kernel gate blocked: {type(exc).__name__} - {str(exc)}]"
        if AskResponse is None:
            return {
                "verdict": "FAIL",
                "final_answer": msg,
                "confidence": 0.0,
                "domain": getattr(req, "domain_override", "") or getattr(req, "domain", "") or "general",
                "run_status": "REJECTED",
                "trace_id": trace_id,
                "governance_decision": "KILL"
            }
        return AskResponse(
            verdict="FAIL",
            final_answer=msg,
            confidence=0.0,
            domain=getattr(req, "domain_override", "") or getattr(req, "domain", "") or "general",
            falsification_status="KERNEL_GATE",
            governance_decision="KILL",
            v98_guard={
                "mode": "rag-verified",
                "readOnly": True,
                "security_blocked": True,
                "kernel_error": type(exc).__name__,
            },
            v98_classification={"provenance": "kernel_gate", "evidence_count": 0},
            elapsed_ms=0.0,
            session_id=session,
            run_id="run-kernel-blocked-" + uuid.uuid4().hex,
            trace_id=trace_id,
            run_status="REJECTED",
            ledger_status="BLOCKED",
        )

    async def _lease_heartbeat(
        self,
        task: dict[str, Any],
        stop: asyncio.Event,
    ) -> None:
        """[S20] Renew the ask lease every ttl/3 while the in-process attempt
        still holds it, so a slow (but alive) provider no longer forfeits a
        correct answer to lease expiry.

        Invariant-safe: renew_lease is expiry-only (no state change) and
        fencing-checked, so this can only extend the CURRENT attempt. When it
        returns False the lease is already gone (released/expired/fenced-off/
        killed) — log, stop, and let finalize's state-route fail-closed
        (S19 path) decide the outcome. NEVER raise from here: a heartbeat
        must not kill the request it is trying to protect.
        """
        ttl = float(task.get("lease_ttl_seconds") or DEFAULT_ASK_LEASE_TTL_SECONDS)
        interval = max(ttl / 3.0, 0.05)
        task_id, lease_id = task["task_id"], task["lease_id"]
        while True:
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
                return  # requested stop (handler + finalize finished)
            except asyncio.TimeoutError:
                pass
            try:
                renewed = self.kernel.renew_lease(
                    task_id, lease_id, task["fencing_token"], ttl_seconds=ttl
                )
            except Exception as exc:  # infra fault: stop, do not mask request path
                logger.warning(
                    "[ask-kernel] lease heartbeat error for %s (lease %s): %s — stopping heartbeat",
                    task_id, lease_id, type(exc).__name__,
                )
                return
            if not renewed:
                _c3_logger.warning(
                    "[S20] lease renew refused for %s (lease %s, attempt %s) — "
                    "heartbeat stopped; finalize will route by current state (fail-closed)",
                    task_id, lease_id, task.get("attempt_id"),
                )
                return

    async def _attach_canonical_evidence(self, req: Any, request: Any) -> None:
        """[F-2 2026-09-15] Wire CanonicalRetriever vào /ask (seam duy nhất).

        Khi một ask KHÔNG mang bằng chứng từ client (contexts/retrieved_context
        rỗng) mà L0 phân loại là LOOKUP, retrieve BM25 trên canonical corpus và
        nạp vào ``req.contexts``. Mutate trên chính req để CÙNG một bằng chứng
        đi vào cả (a) judge grounding trong handler ``_ask_impl`` (tier1
        REJECT_GROUNDING + LLM-judge context) lẫn (b) canonical
        ``verify_response`` bên dưới — verifier và generator chấm trên cùng
        nguồn bằng chứng thật, không ai tự sinh bằng chứng cho mình:
        hit được chọn theo QUESTION (BM25), không theo candidate answer.

        Bất biến fail-closed (mọi nhánh lỗi đều trả về mà KHÔNG sửa req):
          * client đã gửi contexts/retrieved_context → không retrieve (không
            double-retrieval, evidence của client thắng);
          * S24 fork thắng → path này không chạy (fork tự có
            data_api_evidence), xem call site;
          * retriever empty / mọi hit dưới min-score / lỗi / timeout →
            hành vi y hệt trước khi nối;
          * corpus prod absent trong checkout này → retrieve() trả [] → không
            đổi gì (đúng vì vậy e2e không thể tăng khi chưa có corpus — đã
            ghi nhận Q08 F-3, report F2 nêu giới hạn).

        Bằng chứng có cấu trúc được stash vào ``request.state`` để
        ``_ask_impl`` surface vào ``slm_trace`` (provenance audit được; metric
        evidence_recall của benchmark đọc đúng field đó). Đây là TRINH BÀY lại
        bằng chứng judge đã chấm, không phải input mới cho bất kỳ quyết định
        nào — chống vòng lặp tự-duyệt.
        """
        if list(getattr(req, "contexts", None) or []) or str(
            getattr(req, "retrieved_context", "") or ""
        ).strip():
            return  # client-supplied evidence wins — no auto-retrieval, no double
        question = str(getattr(req, "question", "") or "")
        if not question.strip():
            return
        try:
            from scp.runtime.question_router import (
                ask_retrieval_timeout_seconds,
                attempt_canonical_retrieval,
                format_canonical_context,
            )
        except Exception as exc:  # pragma: no cover - router must always import
            logger.debug("[F-2] question_router unavailable: %s", exc)
            return
        budget = ask_retrieval_timeout_seconds()
        try:
            # BM25 là sync (cold corpus load + score); chạy trong to_thread với
            # wait_for để không block event loop / không giữ lease vô hạn
            # (scp-safe-latency-optimizer: đo được ~1.75ms/query fixture, nhưng
            # corpus prod chưa đo — trần cứng 10s, quá hạn fail-closed).
            hits = await asyncio.wait_for(
                asyncio.to_thread(attempt_canonical_retrieval, question),
                timeout=budget,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "[F-2] canonical auto-retrieval exceeded %.1fs — ask continues WITHOUT evidence "
                "(fail-closed, identical to pre-wiring behavior)",
                budget,
            )
            return
        except Exception as exc:
            logger.warning(
                "[F-2] canonical auto-retrieval errored (%s: %s) — ask continues WITHOUT evidence",
                type(exc).__name__, exc,
            )
            return
        if not hits:
            return
        try:
            req.contexts = [format_canonical_context(hit) for hit in hits]
        except Exception as exc:
            logger.warning(
                "[F-2] could not attach retrieved contexts (%s: %s) — continuing without evidence",
                type(exc).__name__, exc,
            )
            return
        state = getattr(request, "state", None)
        if state is not None:
            try:
                state.scp_ask_auto_evidence = [
                    {
                        "chunk_id": hit.get("chunk_id"),
                        "document_id": hit.get("document_id"),
                        "source_url": hit.get("source_url"),
                        "source_title": hit.get("source_title"),
                        "retrieval_score": hit.get("retrieval_score"),
                        "term_coverage": hit.get("term_coverage"),
                        "matched_bigrams": hit.get("matched_bigrams"),
                        "text": hit.get("text", ""),
                    }
                    for hit in hits
                ]
            except Exception as exc:  # state marker là best-effort observability
                logger.debug("[F-2] request.state marker unavailable: %s", exc)
        logger.info(
            "[F-2] LOOKUP ask auto-grounded with %d canonical chunk(s) (first=%s)",
            len(hits),
            str(hits[0].get("source_url") or hits[0].get("chunk_id"))[:120],
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
                    logger.warning(
                        "[S24] lookup fork errored (%s: %s) — falling back to LLM handler",
                        type(exc).__name__, exc,
                    )
                    fork_response = None
            if fork_response is not None:
                response = fork_response
            else:
                # [F-2 2026-09-15] LOOKUP ask thiếu-context → auto-retrieve
                # canonical BM25 evidence vào req.contexts TRƯỚC handler, sao
                # cho cùng bằng chứng đi vào cả grounding judge của _ask_impl
                # lẫn canonical verify_response bên dưới. Đặt SAU fork: fork
                # thắng đã có data_api_evidence riêng (data-API-first per S24
                # owner directive) — không bao giờ double-retrieve; đặt SAU
                # begin: durable identity/input_hash giữ đúng danh tính ask do
                # client gửi lên, evidence tự nạp không đổi task id.
                # Mọi lỗi/empty/timeout = hành vi cũ (fail-closed).
                await self._attach_canonical_evidence(req, request)
                # KPI boundary: this is the actual generation-handler call,
                # distinct from classifier calls and lookup fallback intent.
                try:
                    from scp.runtime.question_router import record_generation_call

                    record_generation_call()
                except Exception as exc:
                    logger.debug("[S24] generation KPI unavailable: %s", exc)
                response = await handler(req, request)
            # [Q07] Truyền handler + request để finalize có thể chạy TỐI ĐA MỘT
            # vòng self-refine khi canonical verify FAIL/UNKNOWN. Reflection
            # regenerate qua chính primary pipeline này rồi bắt answer mới đi qua
            # LẠI verify_response; không có handler thì finalize giữ hành vi cũ.
            result = await self.finalize(task, response, req, handler=handler, request=request)
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


def json_bytes(value: Any) -> bytes:
    import json

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


