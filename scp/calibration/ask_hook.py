"""[PREDICTION-P0 · 2026-10-31] Hook calibration của /ask — ghi dự đoán ĐÓNG ĐƯỢC + lỗi HIỆN RA.

TẠI SAO (audit ``prediction-system-audit.md``): hook cũ ghi 661 dự đoán, 0 đối chiếu —
confidence NULL 661/661, không ``subject_claim_id``/``input_hash``/``tested_sha``, domain luôn
"general" ⇒ về cấu trúc không resolver nào đóng được. Lỗi ghi bị ``except Exception →
warning`` nuốt ⇒ SCP không biết mình mù.

Hợp đồng (chỉ dùng API ``CalibrationLedger`` sẵn có, không đổi schema/chữ ký) — khớp
``prediction-loop-design.md`` §2 (đơn vị dự đoán, khoá K = claim_key + input_hash + tested_sha +
policy_hash):

- Ghi cho MỌI verdict /ask (kể cả PASS giao đáp án) — nếu chỉ ghi nhánh không chắc thì
  ``metrics()`` (chỉ chấm Brier khi ``semantic_verdict_at_prediction == VERIFIED``) mãi ``None``.
- ``semantic_verdict`` ánh xạ 7→4 (§2.2): PASS + ``DELIVER_VERIFIED_PASS`` ⇒ VERIFIED
  (π-deliver); FAIL ⇒ CONTRADICTED, PARTIAL ⇒ INSUFFICIENT, UNKNOWN/ABSTAIN/DEGRADED ⇒ UNKNOWN
  (π-withhold); FLAGGED/ESCALATE/BLOCKED/an ninh/PASS không có mã giao-đã-kiểm ⇒ UNKNOWN (π-none).
- ``subject_claim_id`` = ``claim_key(câu hỏi)`` sẵn có (``scp.core.evidence_resolution``: NFKC +
  casefold + gộp khoảng trắng — trung lập ngôn ngữ, KHÔNG mẫu câu/từ khoá — luật 9).
- ``input_hash`` = sha256(JSON chuẩn {question, draft, contexts}) — câu hỏi + bản nháp + ngữ cảnh.
- ``tested_sha`` = ``SCP_GIT_SHA`` (build) hoặc ``git rev-parse HEAD``; không có ⇒ ``None``.
- ``policy_hash`` = sha256 các thành phần chính sách (lock L1 router, calibration relevance,
  mã math-verify, phiên bản hook, resolver=none) — thành phần ghi trong ``prediction.policy``.
- ``confidence`` = số của judge sau mọi override trong [0,1]; thiếu/ngoài khoảng ⇒ KHÔNG bịa,
  không ghi dòng không-đóng-được, lỗi hiện ra.
- ``domain`` = domain semantic router → judge → "general" (+ ``domain_source``).
- ``prediction_json`` chỉ chứa HASH câu hỏi/đáp án/bản nháp (riêng tư); nguyên văn lấy qua
  ``run_id`` (``evidence_refs``).

Lỗi ghi ⇒ (1) log ERROR có cấu trúc (``scp_event=calibration_hook_failed``), (2) bộ đếm
``hook_health()`` (route ``/v105/calibration/stats`` đọc được), (3) 1 bài học HYPOTHESIS qua
ErrorStore đã duyệt (``FalsificationEngine._get_error_store()`` — ``scp.brain`` là vùng cô lập,
không import thẳng), có trần tần suất + chống trùng theo loại lỗi. /ask KHÔNG hỏng: lỗi ledger
không đổi verdict/câu trả lời (fail-closed cho ledger, không fail-open cho câu trả lời).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("scp.calibration.ask_hook")

HOOK_VERSION = "prediction-p0/v1"
SUBJECT_SCHEME = "claim_key/v1"
DELIVER_VERIFIED_CODE = "DELIVER_VERIFIED_PASS"
#: Ánh xạ verdict pipeline → (semantic canonical, kind) — design §2.2. PASS xử lý riêng (cần mã giao).
_VERDICT_MAP: dict[str, tuple[str, str]] = {
    "FAIL": ("CONTRADICTED", "withhold"),
    "PARTIAL": ("INSUFFICIENT", "withhold"),
    "UNKNOWN": ("UNKNOWN", "withhold"),
    "ABSTAIN": ("UNKNOWN", "withhold"),
    "DEGRADED": ("UNKNOWN", "withhold"),
    "FLAGGED": ("UNKNOWN", "none"),
    "ESCALATE": ("UNKNOWN", "none"),
    "BLOCKED": ("UNKNOWN", "none"),
}
_SECURITY_CODES = frozenset({"ERR_SECURITY_VIOLATION"})
_REPO_ROOT = Path(__file__).resolve().parents[2]
_POLICY_FILES = {
    "semantic_router_lock_sha256": _REPO_ROOT / "config" / "gd2_models.lock.json",
    "answer_relevance_calibration_sha256": _REPO_ROOT / "config" / "answer_relevance.calibration.json",
    "math_verify_code_sha256": _REPO_ROOT / "scp" / "runtime" / "math_claims.py",
}
PREDICTOR_TYPE = "judge"
PREDICTOR_ID = "judge_v3"

#: Seam cho test (hermetic): hàm không đối số trả object có ``add(...)``. ``None`` ⇒ ErrorStore
#: thật qua FalsificationEngine.
STORE_FACTORY: Callable[[], Any] | None = None

_WINDOW_S = 60.0
_lock = threading.Lock()
_store_cache: dict[str, Any] = {}
_lesson_times: list[float] = []
_lesson_seen: dict[str, float] = {}
_counters: dict[str, int] = {"recorded": 0, "failed": 0, "lessons_recorded": 0, "lessons_suppressed": 0}
_last_failure: dict[str, Any] = {}
_sha_cache: dict[str, str | None] = {}
_policy_cache: dict[str, Any] = {}


class CalibrationHookError(RuntimeError):
    """Dự đoán không thể ghi thành bản ghi đóng được (không bịa giá trị)."""

    def __init__(self, error_type: str, message: str) -> None:
        super().__init__(message)
        self.error_type = error_type


def _reset_for_tests() -> None:
    with _lock:
        _lesson_times.clear()
        _lesson_seen.clear()
        _last_failure.clear()
        for k in _counters:
            _counters[k] = 0


def hook_health() -> dict[str, Any]:
    """Bộ đếm in-process của hook (từ lúc process khởi động) — để SCP biết mình có đang mù."""
    with _lock:
        return {**_counters, "last_failure": dict(_last_failure) or None, "scope": "process_lifetime"}


# ─────────────────────────── khoá chủ thể ───────────────────────────


def subject_claim_id(question: str) -> str:
    from scp.core.evidence_resolution import claim_key

    return claim_key(question)


def input_hash(question: str, draft: str = "", contexts: list[str] | tuple[str, ...] = ()) -> str:
    payload = {"question": str(question or ""), "draft": str(draft or ""), "contexts": [str(c) for c in contexts]}
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8", "replace")).hexdigest()


def policy() -> tuple[str, dict[str, Any]]:
    """(policy_hash, thành phần). Cache theo process: chính sách đổi ⇒ deploy/restart mới."""
    with _lock:
        if "v" in _policy_cache:
            return _policy_cache["v"]
    parts: dict[str, Any] = {"hook_version": HOOK_VERSION, "resolver_policy": "none"}
    for key, path in _POLICY_FILES.items():
        try:
            parts[key] = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            parts[key] = None  # thiếu file ⇒ ghi None (trung thực), vẫn vào hash
    digest = hashlib.sha256(json.dumps(parts, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    with _lock:
        _policy_cache["v"] = (digest, parts)
    return digest, parts


def classify(verdict_label: str, decision_code: str) -> tuple[str, str]:
    """(semantic_verdict canonical, kind ∈ deliver|withhold|none) — design §2.2."""
    label = str(verdict_label or "").strip().upper()
    if decision_code in _SECURITY_CODES:
        return "UNKNOWN", "none"
    if label == "PASS":
        return ("VERIFIED", "deliver") if decision_code == DELIVER_VERIFIED_CODE else ("UNKNOWN", "none")
    return _VERDICT_MAP.get(label, ("UNKNOWN", "none"))


def tested_sha() -> str | None:
    env = os.environ.get("SCP_GIT_SHA", "").strip()
    if env and env.lower() != "unknown":
        return env
    with _lock:
        if "head" in _sha_cache:
            return _sha_cache["head"]
    sha: str | None = None
    try:
        from scp.core.safe_process import safe_run

        res = safe_run(["git", "-C", str(Path(__file__).resolve().parents[2]), "rev-parse", "HEAD"],
                       timeout=2, check=True)
        out = (res.stdout or "").strip()
        sha = out if len(out) == 40 and all(c in "0123456789abcdef" for c in out.lower()) else None
    except Exception:  # ranh giới: không có git ⇒ tested_sha None (trung thực), không làm hỏng hook
        logger.info("calibration ask hook: tested_sha unavailable (no SCP_GIT_SHA, git rev-parse failed)",
                    exc_info=True)
    with _lock:
        _sha_cache["head"] = sha
    return sha


def resolve_domain(route_decision: Any, verdict: Any) -> tuple[str, str]:
    """(domain, nguồn). Semantic router trước (tầng ngữ nghĩa đã có), rồi judge, rồi general."""
    for source, obj in (("semantic_router", route_decision), ("judge", verdict)):
        dom = str(getattr(obj, "domain", "") or "").strip()
        if dom and dom.lower() != "general":
            return dom, source
    return "general", "none"


def _redact(text: str, limit: int) -> str:
    """Redaction đã review (math_lessons) — chỉ dùng cho nội dung bài học, không cho ledger."""
    from scp.runtime.math_lessons import _redact as redact

    return redact(text, limit)


def _confidence(raw: Any) -> float:
    if raw is None or isinstance(raw, bool):
        raise CalibrationHookError("confidence_missing", "judge confidence missing — not fabricated")
    try:
        conf = float(raw)
    except (TypeError, ValueError) as exc:
        raise CalibrationHookError("confidence_not_numeric", f"judge confidence not numeric: {type(raw).__name__}") from exc
    if not 0.0 <= conf <= 1.0:  # NaN cũng rơi vào đây
        raise CalibrationHookError("confidence_out_of_range", "judge confidence outside [0,1]")
    return conf


def build_prediction(*, question: str, answer: str, verdict: Any, route_decision: Any, run_id: str,
                     trace_id: str, draft: str = "", contexts: list[str] | tuple[str, ...] = (),
                     decision_code: str = "", generator: str = "") -> dict[str, Any]:
    """Tham số cho ``CalibrationLedger.record_prediction`` — mọi khoá cần để resolver đóng sau này."""
    verdict_label = str(getattr(verdict, "verdict", "") or "")
    code = str(decision_code or "")
    conf = _confidence(getattr(verdict, "confidence", None))
    semantic, kind = classify(verdict_label, code)
    domain, domain_source = resolve_domain(route_decision, verdict)
    p_hash, p_parts = policy()
    refs = [f"run:{run_id}"] if run_id else []
    if not refs and trace_id:
        refs = [f"trace:{trace_id}"]
    prediction = {
        "kind": kind,
        "verdict": verdict_label,
        "confidence": conf,
        "confidence_semantics": "judge_confidence_after_override",
        "decision_code": code or None,
        "answer_sha256": _sha256_text(answer),
        "draft_sha256": _sha256_text(draft) if draft else None,
        "generator": str(generator or "")[:80] or None,
        "domain_source": domain_source,
        "subject_scheme": SUBJECT_SCHEME,
        "policy": p_parts,
        "route": {
            "lane": str(getattr(route_decision, "lane", "") or "")[:24],
            "intent": str(getattr(route_decision, "intent", "") or "")[:24],
            "via": str(getattr(route_decision, "via", "") or "")[:48],
        } if route_decision is not None else None,
    }
    return {
        "domain": domain,
        "task_class": "ask",
        "predictor_type": PREDICTOR_TYPE,
        "predictor_id": PREDICTOR_ID,
        "predictor_version": HOOK_VERSION,
        "prediction": prediction,
        "semantic_verdict": semantic,
        "confidence": conf,
        "subject_claim_id": subject_claim_id(question),
        "input_hash": input_hash(question, draft, contexts),
        "tested_sha": tested_sha(),
        "policy_hash": p_hash,
        "evidence_refs": refs,
    }


def record_ask_prediction(data_dir: Path, **kw: Any) -> dict[str, Any]:
    """Ghi 1 dự đoán. Raise khi không ghi được (người gọi báo lỗi qua ``report_failure``)."""
    from scp.calibration.ledger import CalibrationLedger

    args = build_prediction(**kw)
    led = CalibrationLedger(db_path=str(Path(data_dir) / "calibration.sqlite"))
    try:
        row = led.record_prediction(**args)
    finally:
        led.close()
    with _lock:
        _counters["recorded"] += 1
    return row


# ─────────────────────────── lỗi hiện ra ───────────────────────────


def _error_type(exc: BaseException) -> str:
    return exc.error_type if isinstance(exc, CalibrationHookError) else type(exc).__name__


def _store() -> Any:
    if STORE_FACTORY is not None:
        return STORE_FACTORY()
    with _lock:
        if "default" not in _store_cache:
            from scp.meta.falsification_engine import FalsificationEngine

            _store_cache["default"] = FalsificationEngine()._get_error_store()
        return _store_cache["default"]


def _record_lesson(error_type: str, exc: BaseException, question: str, verdict_label: str) -> bool:
    now = time.monotonic()
    try:
        cap = max(0, int(os.environ.get("SCP_CALIBRATION_LESSON_MAX_PER_MIN", "10") or 10))
    except ValueError:
        cap = 10
    with _lock:
        _lesson_times[:] = [t for t in _lesson_times if now - t < _WINDOW_S]
        for k in [k for k, t in _lesson_seen.items() if now - t >= _WINDOW_S]:
            del _lesson_seen[k]
        if error_type in _lesson_seen or len(_lesson_times) >= cap:
            _counters["lessons_suppressed"] += 1
            return False
        _lesson_times.append(now)
        _lesson_seen[error_type] = now
    try:
        details = {
            "epistemic_status": "HYPOTHESIS", "verified": False, "promotion": "never_automatic",
            "status": "calibration_hook_failed", "error_type": error_type,
            "hypothesis": (
                "the /ask calibration prediction was NOT recorded: SCP cannot later compare this "
                "answer with Reality (blind spot) — cause is the ledger write path or a missing "
                "judge field, not the answer itself"
            ),
            "error": _redact(str(exc), 300),
            "judge_verdict": verdict_label[:24],
            "observed_at": datetime.now(timezone.utc).isoformat(),
        }
        rec = _store().add(
            question=f"calibration_hook_failed {error_type}: {_redact(question, 300)}",
            answer="",
            verdict="UNVERIFIED", domain="calibration", error_type=f"calibration_hook_{error_type.lower()}",
            details=details,
        )
        if isinstance(rec, dict) and rec.get("rejected"):
            raise RuntimeError(f"ErrorStore rejected lesson: {rec.get('reject_reason')}")
    except Exception:  # ranh giới: bài học là best-effort; lỗi của nó cũng phải hiện ra
        with _lock:
            _lesson_seen.pop(error_type, None)
        logger.error("calibration_hook_lesson_failed error_type=%s", error_type, exc_info=True,
                     extra={"scp_event": "calibration_hook_lesson_failed", "error_type": error_type})
        return False
    with _lock:
        _counters["lessons_recorded"] += 1
    return True


def report_failure(exc: BaseException, *, question: str, verdict_label: str) -> dict[str, Any]:
    """Làm lỗi ghi ledger HIỆN RA (log có cấu trúc + bộ đếm + bài học HYPOTHESIS). Không raise."""
    error_type = _error_type(exc)
    failure = {"error_type": error_type, "at": datetime.now(timezone.utc).isoformat(),
               "judge_verdict": verdict_label[:24]}
    with _lock:
        _counters["failed"] += 1
        _last_failure.clear()
        _last_failure.update(failure)
    logger.error(
        "calibration_hook_failed error_type=%s verdict=%s — prediction NOT recorded (answer unaffected)",
        error_type, verdict_label[:24], exc_info=not isinstance(exc, CalibrationHookError),
        extra={"scp_event": "calibration_hook_failed", "error_type": error_type},
    )
    failure["lesson_recorded"] = _record_lesson(error_type, exc, question, verdict_label)
    return failure


__all__ = [
    "DELIVER_VERIFIED_CODE", "HOOK_VERSION", "SUBJECT_SCHEME", "CalibrationHookError", "build_prediction",
    "classify", "hook_health", "input_hash", "policy", "record_ask_prediction", "report_failure",
    "resolve_domain", "subject_claim_id", "tested_sha",
]
