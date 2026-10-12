"""Response verification and safe response construction for AskKernelAdapter."""
from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from typing import Any

logger = logging.getLogger(__name__)


def json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def dump_data(obj: Any) -> dict[str, Any]:
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if hasattr(obj, "dict"):
        return obj.dict()
    if isinstance(obj, dict):
        return obj
    return dict(vars(obj))


def verification_fail_reason(response_data: dict[str, Any]) -> str:
    """[SECURITY-REASON 2026-09-26] Kernel reason for a non-verified ask."""
    guard = response_data.get("v98_guard")
    security_determined = (
        str(response_data.get("lane") or "") == "LANE_SECURITY"
        or str(response_data.get("governance_decision") or "") == "KILL"
        or (isinstance(guard, dict) and bool(guard.get("security_blocked")))
    )
    return (
        "ask_security_escalate"
        if security_determined
        else "ask_evidence_insufficient_or_contradicted"
    )


def lookup_fork_self_certified(data: dict[str, Any]) -> bool:
    """[W2-d6] Fork lookup (route=lookup_data_api) validation."""
    v98 = data.get("v98_classification") if isinstance(data.get("v98_classification"), dict) else {}
    if v98.get("route") != "lookup_data_api":
        return True
    return (data.get("relevance_gate") or {}).get("checked") is True


async def verify_ask_response(req: Any, response: Any, task: dict[str, Any] | None = None) -> dict[str, Any]:
    if task is None:
        task = {"task_id": "ask-task-default"}
    data = dump_data(response)
    contexts = [str(value) for value in (getattr(req, "contexts", None) or []) if str(value).strip()]
    retrieved_context = str(getattr(req, "retrieved_context", "") or "").strip()
    if retrieved_context:
        contexts.append(retrieved_context)
    answer = str(data.get("final_answer") or "")
    verdict = str(data.get("verdict") or "")
    governance = str(data.get("governance_decision") or "")

    fork_evidence = str(data.get("data_api_evidence") or "").strip()
    if fork_evidence:
        contexts.append(fork_evidence)
    provenance_value = (data.get("v98_classification") or {}).get("provenance")
    provenance = str(provenance_value or "")
    is_rag_ask = bool(contexts)
    if contexts:
        evidence_context_hash = hashlib.sha256(json_bytes(contexts)).hexdigest()
        evidence_ref = f"evidence://rag/sha256:{evidence_context_hash}"
    else:
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
        ans_words = set(re.findall(r"[\w\xc0-\u1ef9]{2,}", answer.lower()))
        if not ans_words:
            grounded_ratio = 0.0
        else:
            ctx_text = " ".join(contexts).lower()
            overlap = sum(1 for w in ans_words if w in ctx_text)
            grounded_ratio = overlap / len(ans_words)

    is_chatbot_lane = False
    try:
        from scp.runtime.question_router import LANE_CHATBOT, route_question
        q_text = str(getattr(req, "question", "") or "")
        if q_text:
            decision = route_question(q_text)
            if decision.lane == LANE_CHATBOT:
                is_chatbot_lane = True
    except Exception as _cb_err:
        logger.debug("Chatbot lane check failed: %s", _cb_err, exc_info=True)
    if not is_chatbot_lane and (data.get("lane") == "LANE_CHATBOT" or getattr(req, "lane", None) == "LANE_CHATBOT"):
        is_chatbot_lane = True

    if is_rag_ask:
        is_chatbot_lane = False

    already_judged = bool(
        data.get("evidence", {}).get("judge_evaluated")
        or data.get("judge_evaluated")
        or (isinstance(data.get("v98_classification"), dict) and data["v98_classification"].get("judge_evaluated"))
        or (isinstance(data.get("v98_guard"), dict) and data["v98_guard"].get("judge_evaluated"))
    )
    is_lookup_fork = (
        isinstance(data.get("v98_classification"), dict)
        and data["v98_classification"].get("route") == "lookup_data_api"
    )
    if is_lookup_fork and lookup_fork_self_certified(data):
        already_judged = True
    elif not lookup_fork_self_certified(data):
        already_judged = False

    _CHATBOT_CLEARANCES = {"UPHOLD", "ALLOW"}
    _abstain_delivery = (
        verdict == "ABSTAIN"
        and str(governance or "").strip().upper() == "ABSTAIN"
        and answer.startswith("[unverified — abstain]")
    )
    judge_pass = True
    if is_chatbot_lane:
        _gov = str(governance or "").strip().upper()
        judge_pass = (
            verdict not in ("FAIL", "FLAGGED")
            and (_gov in _CHATBOT_CLEARANCES or _abstain_delivery)
        )
    elif already_judged:
        judge_pass = (verdict == "PASS" or _abstain_delivery)
    else:
        try:
            from scp.api_server_parts.helpers import get_judge
            judge = get_judge()
            if judge is None:
                from scp.runtime.judge import RealityJudge
                judge = RealityJudge()
            judge_res = await judge.judge_async(
                question=str(getattr(req, "question", "")),
                ai_answer=answer,
                context=" ".join(contexts),
            )
            judge_pass = (judge_res["verdict"] == "PASS")
        except Exception:
            logger.warning("verify_ask_response: RealityJudge failed", exc_info=True)
            judge_pass = False

    checks = {
        "verdict_pass": (verdict != "FAIL") if is_chatbot_lane else (verdict == "PASS" or _abstain_delivery),
        "judge_pass": judge_pass,
        "governance_uphold": (
            ((str(governance or "").strip().upper() in _CHATBOT_CLEARANCES) or _abstain_delivery)
            if is_chatbot_lane
            else (governance == "UPHOLD" or _abstain_delivery)
        ),
        "provenance_compatible": (
            (
                provenance in {"input_context_only", "external_rag", "knowledge_base", "web_search", "lookup_data_api"}
                or (bool(data.get("web_fallback_used")) and bool(data.get("web_fallback") or data.get("data_api_evidence") or data.get("web_evidence")))
            )
            if is_rag_ask
            else (
                provenance in {"", "input_context_only", "general_knowledge", "chat"}
                or bool(data.get("web_fallback_used"))
            )
        ),
    }
    if is_rag_ask and not is_chatbot_lane:
        checks["rag_evidence_bound"] = (grounded_ratio > 0.0)
    failures = [name for name, ok in checks.items() if not ok]
    is_labeled_abstain = _abstain_delivery and not failures
    return {
        "verdict": "VERIFIED" if not failures else "CONTRADICTED",
        "verifier_id": "scp-ask-rag-verifier-v2",
        "verifier_type": "heuristic_rag_gateway",
        "epistemic_level": "HEURISTIC_CHECKLIST" if not is_labeled_abstain else "UNVERIFIED_LABELED_ABSTAIN",
        "delivery_disposition": "LABELED_ABSTAIN" if is_labeled_abstain else ("VERIFIED_GATE" if not failures else "BLOCKED"),
        "is_labeled_abstain": is_labeled_abstain,
        "evidence_ref": evidence_ref,
        "response_ref": f"ask://{task['task_id']}/response/{data.get('trace_id') or 'no-trace'}",
        "grounded_ratio": round(grounded_ratio, 6),
        "evidence_context_count": len(contexts),
        "evidence_context_hash": hashlib.sha256(json_bytes(contexts)).hexdigest(),
        "checked": checks,
        "failures": failures,
        "is_chatbot_lane": is_chatbot_lane,
    }


def build_safe_response(response: Any, verification: dict[str, Any]) -> Any:
    if verification.get("verdict") == "VERIFIED":
        return response
    data = dump_data(response)
    if isinstance(response, dict) and data is response:
        data = dict(data)
    fail_reasons = ", ".join(verification.get("failures", []))
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
            logger.debug("build_safe_response: TypeError ignored", exc_info=True)
            return data
    return data


def build_kernel_blocked_response(req: Any, exc: Exception, ask_response_cls: Any = None) -> Any:
    session = getattr(req, "session_id", None) or "ask-kernel-blocked"
    trace_id = "trace-kernel-blocked-" + uuid.uuid4().hex
    msg = f"[SCP: Answer withheld — Kernel gate blocked: {type(exc).__name__} - {str(exc)}]"
    if ask_response_cls is None:
        return {
            "verdict": "FAIL",
            "final_answer": msg,
            "confidence": 0.0,
            "domain": getattr(req, "domain_override", "") or getattr(req, "domain", "") or "general",
            "run_status": "REJECTED",
            "trace_id": trace_id,
            "governance_decision": "KILL",
        }
    return ask_response_cls(
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

