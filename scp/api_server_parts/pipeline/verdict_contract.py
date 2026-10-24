# [HC-52/HC-46 · 2026-10-24 · K5-W1] Verdict shape contract — PHASE 1 OBSERVE.
"""Single source of truth for the shape of judge-verdict objects flowing through
the 5-stage /ask pipeline (lookup -> generation -> security -> verification -> ledger).

Phase 1 policy: ``validate_verdict_shape`` VI PHẠM chỉ log structured WARNING
(không raise, không mutate) — quan sát trước khi siết ở phase sau. Xem
``tests/T01_pipeline/test_verdict_contract.py`` (test pin).

Tập giá trị dưới đây KHÔNG bịa: mỗi giá trị được xác minh bằng rg trên
``scp/`` + ``tests/`` (bằng chứng dòng cụ thể ghi trong test pin cùng ngày).
"""
from __future__ import annotations

import logging
from typing import Any, Final, Literal, Mapping

logger = logging.getLogger(__name__)

# --- [HC-52] Canonical value sets -------------------------------------------------

# 7 giá trị verdict chuẩn, xác minh bằng rg trên code + tests (2026-10-24):
#   PASS/ABSTAIN/UNKNOWN  — scp/runtime/judge.py:342,359,524,541; verification_stage.py:116,177,263
#   FAIL                  — security_stage.py:252; verification_stage.py:177,271
#   FLAGGED               — verification_stage.py:330; ledger_stage.py:103
#   PARTIAL               — ledger_stage.py:103; meta/simple_explainer.py:69
#   ESCALATE              — delivery_rules.py:162 (so sánh); tests/T02_contract/test_ask_w16_*.py
VerdictValue = Literal[
    "PASS",
    "FAIL",
    "UNKNOWN",
    "PARTIAL",
    "ABSTAIN",
    "FLAGGED",
    "ESCALATE",
]

VERDICTS: Final[frozenset[str]] = frozenset(
    {"PASS", "FAIL", "UNKNOWN", "PARTIAL", "ABSTAIN", "FLAGGED", "ESCALATE"}
)

# Giá trị verdict MỞ RỘNG quan sát được thật trong hệ (không bịa):
#   DEGRADED — delivery_rules.py:162; tests/T02_contract/test_ask_w3_escalate_withhold.py:129
#   BLOCKED  — scp/runtime/judge_helpers.py:81
VERDICTS_EXTENDED: Final[frozenset[str]] = frozenset({"DEGRADED", "BLOCKED"})

ALL_VERDICTS: Final[frozenset[str]] = VERDICTS | VERDICTS_EXTENDED

# Tập governance_decision, xác minh bằng rg (2026-10-24):
#   UPHOLD/ESCALATE — verification_stage.py:132,196,273
#   KILL            — server_helpers.py:76,93; delivery_rules.py:60,71
#   ABSTAIN/DENY    — delivery_rules.py:126,256
#   REJECT/DEGRADED/UNKNOWN — delivery_rules.py:241,142,77
#   ALLOW           — scp/governance/drift_guard.py:26; scp/ask_adapter_parts/response_verifier.py:162
GovernanceValue = Literal[
    "UPHOLD",
    "ALLOW",
    "ESCALATE",
    "KILL",
    "ABSTAIN",
    "DENY",
    "REJECT",
    "DEGRADED",
    "UNKNOWN",
]

GOVERNANCE: Final[frozenset[str]] = frozenset(
    {"UPHOLD", "ALLOW", "ESCALATE", "KILL", "ABSTAIN", "DENY", "REJECT", "DEGRADED", "UNKNOWN"}
)


# --- [HC-46] Shared key contracts (magic-string removal) ---------------------------


class PipelineContextKeys:
    """Hằng tên key của ``v98_context`` (dict mutable dùng chung 5 stage /ask).

    Contract từng key (ai ghi, ai đọc, ý nghĩa):
      SESSION_ID            — lookup/generation ghi id phiên; verification + ledger đọc
                              để gắn multi-turn tracking & response record.
      WEB_FALLBACK          — lookup ghi cờ đã dùng web fallback; verification đọc
                              để miễn trừ stale-fact guard (W8-e1).
      FACT_CHECK_HINT       — stage trước ghi gợi ý fact-check; verification đọc.
      DECISION_CODE         — verification ghi mã quyết định DeliveryRuleEngine
                              (decision.code) sau boundary; API boundary đọc.
      SEMANTIC_FIREWALL     — verification ghi {"blocked", "total"} thống kê
                              evidence bị cách ly bởi semantic firewall.
      V104_MULTI_TURN       — verification ghi __dict__ của MultiTurnTracker result.
      V99_VIETNAMESE_ATTACK — security/verification ghi cờ phát hiện attack tiếng Việt.
    """

    SESSION_ID: Final = "session_id"
    WEB_FALLBACK: Final = "web_fallback"
    FACT_CHECK_HINT: Final = "fact_check_hint"
    DECISION_CODE: Final = "decision_code"
    SEMANTIC_FIREWALL: Final = "semantic_firewall"
    V104_MULTI_TURN: Final = "v104_multi_turn"
    V99_VIETNAMESE_ATTACK: Final = "v99_vietnamese_attack"


class VerdictEvidenceKeys:
    """Hằng tên key của dict ``evidence`` trong judge-verdict object.

      GOVERNANCE_DECISION    — quyết định governance (UPHOLD/ESCALATE/KILL/...).
      JUDGE_EVALUATED        — bool: RealityJudge có thực sự chạy không (F1-S3).
      DETERMINISTIC_VERIFIED — bool: đã được verifier deterministic cục bộ xác nhận.
      ADJUDICATED_BY         — str: chủ thể ra phán quyết (reality_judge/
                               atomic_deterministic_local/...).
      PROVENANCE             — str: nguồn căn cứ (web_search/external_rag/...).
      JUDGE_VERDICT_OVERRIDDEN — bool: verdict của judge đã bị nâng sau judge (F2-S3).
      JUDGE_ORIGINAL_VERDICT — str: verdict gốc của judge trước khi override.
      V98_CLASSIFICATION     — dict phân loại route/provenance.
      EVIDENCE_REFS          — list[str] tham chiếu evidence (calibration ledger dùng).
    """

    GOVERNANCE_DECISION: Final = "governance_decision"
    JUDGE_EVALUATED: Final = "judge_evaluated"
    DETERMINISTIC_VERIFIED: Final = "deterministic_verified"
    ADJUDICATED_BY: Final = "adjudicated_by"
    PROVENANCE: Final = "provenance"
    JUDGE_VERDICT_OVERRIDDEN: Final = "judge_verdict_overridden"
    JUDGE_ORIGINAL_VERDICT: Final = "judge_original_verdict"
    V98_CLASSIFICATION: Final = "v98_classification"
    EVIDENCE_REFS: Final = "evidence_refs"


# --- [HC-52] Shape validator -------------------------------------------------------

_EVIDENCE_BOOL_KEYS: Final[frozenset[str]] = frozenset(
    {
        VerdictEvidenceKeys.JUDGE_EVALUATED,
        VerdictEvidenceKeys.DETERMINISTIC_VERIFIED,
        VerdictEvidenceKeys.JUDGE_VERDICT_OVERRIDDEN,
    }
)
_EVIDENCE_STR_KEYS: Final[frozenset[str]] = frozenset(
    {
        VerdictEvidenceKeys.ADJUDICATED_BY,
        VerdictEvidenceKeys.PROVENANCE,
        VerdictEvidenceKeys.JUDGE_ORIGINAL_VERDICT,
    }
)


def validate_verdict_shape(verdict_obj: Any) -> list[str]:
    """Kiểm shape của judge-verdict object; trả về danh sách lỗi (rỗng = đạt).

    Không raise, không mutate — caller (phase 1 observe) chỉ log WARNING.
    Kiểm:
      - verdict: bắt buộc, str, thuộc ALL_VERDICTS.
      - confidence: nếu có phải số (int/float) hoặc None.
      - final_answer / reasoning: nếu có phải str hoặc None.
      - evidence: nếu có phải Mapping; các field F1-S3 đúng kiểu
        (judge_evaluated/deterministic_verified/judge_verdict_overridden: bool;
        adjudicated_by/provenance/judge_original_verdict: str; evidence_refs: list/tuple).
    """
    errors: list[str] = []
    if not isinstance(verdict_obj, Mapping):
        return [f"verdict object is not a Mapping (got {type(verdict_obj).__name__})"]

    verdict_value = verdict_obj.get("verdict")
    if verdict_value is None:
        errors.append("verdict: missing")
    elif not isinstance(verdict_value, str):
        errors.append(f"verdict: not a str (got {type(verdict_value).__name__})")
    elif verdict_value not in ALL_VERDICTS:
        errors.append(f"verdict: {verdict_value!r} not in ALL_VERDICTS")

    confidence = verdict_obj.get("confidence")
    if confidence is not None and not isinstance(confidence, (int, float)):
        errors.append(f"confidence: not numeric (got {type(confidence).__name__})")

    for _text_key in ("final_answer", "reasoning"):
        _text_val = verdict_obj.get(_text_key)
        if _text_val is not None and not isinstance(_text_val, str):
            errors.append(f"{_text_key}: not a str (got {type(_text_val).__name__})")

    evidence = verdict_obj.get("evidence")
    if evidence is not None and not isinstance(evidence, Mapping):
        errors.append(f"evidence: not a Mapping (got {type(evidence).__name__})")
        return errors  # không thể soi tiếp bên trong

    if isinstance(evidence, Mapping):
        for _bool_key in sorted(_EVIDENCE_BOOL_KEYS):
            _bool_val = evidence.get(_bool_key)
            if _bool_val is not None and not isinstance(_bool_val, bool):
                errors.append(f"evidence.{_bool_key}: not a bool (got {type(_bool_val).__name__})")
        for _str_key in sorted(_EVIDENCE_STR_KEYS):
            _str_val = evidence.get(_str_key)
            if _str_val is not None and not isinstance(_str_val, str):
                errors.append(f"evidence.{_str_key}: not a str (got {type(_str_val).__name__})")
        _refs = evidence.get(VerdictEvidenceKeys.EVIDENCE_REFS)
        if _refs is not None and not isinstance(_refs, (list, tuple)):
            errors.append(
                f"evidence.{VerdictEvidenceKeys.EVIDENCE_REFS}: not a list/tuple "
                f"(got {type(_refs).__name__})"
            )

    return errors
