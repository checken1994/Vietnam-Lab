# SPDX-License-Identifier: AGPL-3.0-only
"""
SCP V4 FORTRESS — AI Defense Citadel
Copyright (c) 2026 Minh / SCP V4 Project. AGPL-3.0-only.

File: falsification_engine.py
Module: scp_v4.falsification_engine
Purpose: Continuous Falsification Engine — measures deviation, never declares truth.
"""

import json
import logging
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from scp.meta._falsification_helpers import (
    CONFIDENCE_THRESHOLD,
    ERRORSTORE_TARGET_SIZE,
    KB_DEFAULT_SIZE,
    MAX_CONTRADICTIONS_AUTO,
    SEVERITY_HIGH,
    SEVERITY_LOW,
    SEVERITY_MEDIUM,
    V4_LAYERS_ACTIVE,
    V4_TOTAL_TEST_CASES,
    Contradiction,
    Deviation,
    FalsificationStatus,
    Refutation,
    _smoke_test,
)
from scp.meta._number_utils import (
    _NUM_UNIT_RE,
    _UNIT_MULTIPLIERS,
    _normalize_number,
)

logger = logging.getLogger(__name__)


# ============================================================
# FALSIFICATION ENGINE
# ============================================================


class FalsificationEngine:
    """Measures deviation, contradiction, and uncertainty.

    Never declares truth — only reports what was tested and what was found.

    Philosophy: V4 is a microscope, not a judge.
    """

    def __init__(self, error_store: Any | None = None) -> None:
        # All contradictions ever found by this engine instance.
        self.contradictions: list[Contradiction] = []
        # All refutations by reality — V4's "scars".
        self.refutations: list[Refutation] = []
        # [A3 F-M2] Real ErrorStore backing refutation records.
        self._error_store: Any | None = error_store
        # [A3 F-M2] Count of refutation records ACTUALLY persisted to ErrorStore.
        self._errorstore_persisted: int = 0
        # What V4 tested vs what it didn't (mutable scope fingerprint).
        self.scope_limits: dict[str, Any] = {
            "last_self_falsification": "never",
            "domains_covered": set(),
            "domains_uncovered": set(),
        }
        # Per-test-type counters.
        self.test_coverage: dict[str, int] = defaultdict(int)

    # --------------------------------------------------------
    # VERDICT TRANSLATION (old absolute -> new skeptical)
    # --------------------------------------------------------

    def translate_old_verdict(self, old_verdict: str, context: dict) -> dict:
        """Translate old absolute verdicts to skeptical status."""
        context = context or {}
        old = (old_verdict or "").upper().strip()
        confidence = float(context.get("confidence", 0.0) or 0.0)
        scope = str(context.get("scope", f"{V4_TOTAL_TEST_CASES} test cases"))
        contradictions = list(context.get("contradictions", []) or [])

        if old == "PASS":
            return {
                "status": FalsificationStatus.UNREFUTED_IN_CURRENT_SCOPE.value,
                "old_verdict": old_verdict,
                "scope": scope,
                "note": "Not declared safe — only unrefuted within test scope",
                "contradictions": contradictions,
                "confidence": confidence if confidence else 0.95,
            }

        if old == "FAIL":
            return {
                "status": FalsificationStatus.PATTERN_MATCHED.value,
                "old_verdict": old_verdict,
                "scope": "pattern-matching layer (antibodies + KB)",
                "note": "Known malicious pattern detected",
                "pattern": context.get("pattern", "unknown"),
                "contradictions": contradictions,
                "confidence": confidence if confidence else 0.99,
            }

        if old == "UNCERTAIN":
            reason = context.get("reason", "conflicting signals across layers")
            return {
                "status": FalsificationStatus.HUMAN_DECISION_REQUIRED.value,
                "old_verdict": old_verdict,
                "scope": "insufficient — human review required",
                "note": "V4 could not reach threshold confidence",
                "reason": reason,
                "contradictions": contradictions,
                "confidence": confidence if confidence else 0.50,
            }

        if old == "ESCALATED":
            reason = context.get("reason", "medical/safety escalation")
            return {
                "status": FalsificationStatus.HUMAN_DECISION_REQUIRED.value,
                "old_verdict": old_verdict,
                "scope": "medical/safety domain — human owns final decision",
                "note": "Human decision required — safety-critical domain",
                "reason": reason,
                "contradictions": contradictions,
                "confidence": confidence,
            }

        # Unknown verdict type — V4 honestly admits it cannot translate.
        return {
            "status": FalsificationStatus.OUT_OF_SCOPE.value,
            "old_verdict": old_verdict,
            "scope": "unknown verdict type",
            "note": f"V4 cannot translate verdict '{old_verdict}' — out of scope",
            "contradictions": contradictions,
            "confidence": 0.0,
        }

    # --------------------------------------------------------
    # DEVIATION MEASUREMENT
    # --------------------------------------------------------

    @staticmethod
    def _extract_numbers(text: str) -> list[dict[str, Any]]:
        """Extract all (value, raw, unit) triples from free text.

        Vietnamese-safe: preserves diacritics, recognises 'tỷ', 'triệu', etc.
        """
        if not text:
            return []
        results: list[dict[str, Any]] = []
        seen_spans = set()
        for m in _NUM_UNIT_RE.finditer(str(text)):
            span = m.span()
            if span in seen_spans:
                continue
            seen_spans.add(span)
            raw_num, unit = m.group(1), m.group(2)
            try:
                value = float(raw_num.replace(",", ""))
            except ValueError as exc:
                logger.debug("falsification_engine: comma-strip parse failed, trying dot-swap: %s", exc, exc_info=True)
                try:
                    value = float(raw_num.replace(",", "."))
                except ValueError as exc:
                    logger.debug("falsification_engine: number parse failed, skipping token: %s", exc, exc_info=True)
                    continue
            if unit:
                mult = _UNIT_MULTIPLIERS.get(unit.lower())
                if mult is not None:
                    value *= mult
            results.append({
                "value": value,
                "raw": m.group(0).strip(),
                "unit": unit or "",
            })
        return results

    @staticmethod
    def _classify_severity(delta: float) -> str:
        """Classify a relative delta into a severity bucket."""
        if delta < SEVERITY_LOW:
            return "LOW"
        if delta < SEVERITY_MEDIUM:
            return "MEDIUM"
        if delta < SEVERITY_HIGH:
            return "HIGH"
        return "CRITICAL"

    def measure_deviation(self, llm_answer: str, reference_data: dict) -> dict:
        """Measure deviation between LLM answer and reference data.

        Does NOT correct — only MEASURES.
        """
        deviations: list[Deviation] = []
        llm_numbers = self._extract_numbers(llm_answer)

        for field_name, ref_value in (reference_data or {}).items():
            ref_num = _normalize_number(ref_value)
            if ref_num is None:
                # Reference itself is non-numeric — skip (cannot measure deviation).
                continue

            best_match: dict[str, Any] | None = None
            best_delta: float = float("inf")
            for ln in llm_numbers:
                if ref_num == 0.0:
                    delta = abs(ln["value"])
                else:
                    delta = abs(ln["value"] - ref_num) / abs(ref_num)
                if delta < best_delta:
                    best_delta = delta
                    best_match = ln

            if best_match is None:
                # [SCP-DNA-FIX R5-6] missing_claim=True excludes sentinel delta=1.0 from max_dev.
                deviations.append(Deviation(
                    field=field_name,
                    llm_value="<no number found>",
                    reference_value=str(ref_value),
                    delta=1.0,
                    severity="CRITICAL",
                    missing_claim=True,
                ))
                continue

            severity = self._classify_severity(best_delta)
            deviations.append(Deviation(
                field=field_name,
                llm_value=best_match["raw"],
                reference_value=str(ref_value),
                delta=best_delta,
                severity=severity,
            ))

        numeric_deltas = [d.delta for d in deviations if not d.missing_claim]
        max_dev = max(numeric_deltas, default=0.0)
        contradiction_count = sum(
            1 for d in deviations if d.severity in ("HIGH", "CRITICAL")
        )
        status = (
            FalsificationStatus.CONTRADICTION_DETECTED
            if contradiction_count > 0
            else FalsificationStatus.NO_CONTRADICTION_FOUND
        )

        # Track coverage for scope accounting.
        self.test_coverage["measure_deviation"] += 1

        return {
            "deviations": [d.to_dict() for d in deviations],
            "max_deviation": round(max_dev, 4),
            "contradiction_count": contradiction_count,
            "status": status.value,
        }

    # --------------------------------------------------------
    # CONTRADICTION REPORT (replaces closed verdict)
    # --------------------------------------------------------

    def generate_contradiction_report(
        self,
        question: str,
        llm_answer: str,
        evidence: list,
        ground_truth: dict,
    ) -> dict:
        """Generate a Contradiction Report (replaces the old closed verdict)."""
        contradictions: list[dict[str, Any]] = []
        unrefuted_claims: list[dict[str, Any]] = []

        # 1. Measure LLM answer against ground truth.
        llm_vs_gt = self.measure_deviation(llm_answer, ground_truth)
        for dev in llm_vs_gt["deviations"]:
            entry = {
                "source_a": "llm_answer",
                "source_b": "ground_truth",
                "field": dev["field"],
                "value_a": dev["llm_value"],
                "value_b": dev["reference_value"],
                "delta": dev["delta"],
                "severity": dev["severity"],
            }
            if dev["severity"] in ("HIGH", "CRITICAL"):
                contradictions.append(entry)
                self.contradictions.append(Contradiction(
                    source_a="llm_answer",
                    source_b="ground_truth",
                    field=dev["field"],
                    value_a=dev["llm_value"],
                    value_b=dev["reference_value"],
                    severity=dev["severity"],
                ))
            else:
                unrefuted_claims.append(entry)

        # 2. Measure each piece of evidence against ground truth.
        for idx, ev in enumerate(evidence or []):
            if not isinstance(ev, dict):
                ev_dev = self.measure_deviation(str(ev), ground_truth)
                for dev in ev_dev["deviations"]:
                    if dev["severity"] in ("HIGH", "CRITICAL"):
                        contradictions.append({
                            "source_a": f"evidence[{idx}]",
                            "source_b": "ground_truth",
                            "field": dev["field"],
                            "value_a": dev["llm_value"],
                            "value_b": dev["reference_value"],
                            "delta": dev["delta"],
                            "severity": dev["severity"],
                        })
                continue
            overlap = {k: v for k, v in ev.items() if k in ground_truth}
            if not overlap:
                continue
            ev_dev = self.measure_deviation(json.dumps(overlap, ensure_ascii=False), overlap)
            for dev in ev_dev["deviations"]:
                if dev["severity"] in ("HIGH", "CRITICAL"):
                    contradictions.append({
                        "source_a": f"evidence[{idx}]",
                        "source_b": "ground_truth",
                        "field": dev["field"],
                        "value_a": dev["llm_value"],
                        "value_b": dev["reference_value"],
                        "delta": dev["delta"],
                        "severity": dev["severity"],
                    })

        # 3. Confidence: ratio of unrefuted claims to total measured claims.
        total_claims = len(llm_vs_gt["deviations"])
        contradicted = sum(
            1 for c in contradictions if c["source_a"] == "llm_answer"
        )
        if total_claims > 0:
            confidence = max(0.0, 1.0 - (contradicted / total_claims))
        else:
            confidence = 0.5  # nothing to measure — uncertain by default

        # 4. Test scope fingerprint.
        test_scope = {
            "tests_run": V4_TOTAL_TEST_CASES,
            "tests_passed": V4_TOTAL_TEST_CASES - len(self.refutations),
            "tests_failed": len(self.refutations),
            "coverage_percent": round(
                100.0 * (1.0 - len(self.refutations) / max(V4_TOTAL_TEST_CASES, 1)), 2
            ),
        }

        # 5. Should a human decide?
        escalate, escalate_reason = self.should_escalate_human(
            confidence, contradictions
        )

        # 6. V4 recommendation is NEVER "approve/reject" — only a review hint.
        if contradictions:
            recommendation = (
                f"Review {len(contradictions)} contradiction(s) before deciding — "
                f"V4 will not conclude."
            )
        else:
            recommendation = (
                "No contradictions detected within test scope — "
                "human still owns the final decision."
            )

        self.test_coverage["contradiction_reports"] += 1

        return {
            "question": question,
            "llm_answer": llm_answer,
            "evidence_summary": f"{len(evidence or [])} source(s) checked",
            "contradictions": contradictions,
            "unrefuted_claims": unrefuted_claims,
            "test_scope": test_scope,
            "confidence": round(confidence, 4),
            "v4_recommendation": recommendation,
            "human_decision_required": escalate,
            "escalation_reason": escalate_reason,
        }

    # --------------------------------------------------------
    # SCOPE CALCULATION
    # --------------------------------------------------------

    def calculate_scope(self) -> dict:
        """Calculate what V4 has tested vs what it hasn't."""
        coverage_gaps = [
            "multilingual attacks <5% coverage (vi/en/fr/ja/zh)",
            "zero-day prompt patterns untested",
            "multimodal (image/audio) partial — text-only verified",
            "agentic tool-use attacks emerging — coverage TBD",
            "long-context (>32k tokens) injection untested",
            "cross-modal latent injection untested",
        ]
        return {
            "total_test_cases": V4_TOTAL_TEST_CASES,
            "layers_active": V4_LAYERS_ACTIVE,
            "errorstore_size": self._errorstore_persisted,
            "errorstore_target_capacity": ERRORSTORE_TARGET_SIZE,
            "kb_size": KB_DEFAULT_SIZE,
            "coverage_gaps": coverage_gaps,
            "last_falsification": self.scope_limits.get(
                "last_self_falsification", "never"
            ),
        }

    # --------------------------------------------------------
    # REFUTATION RECORDING — V4's most valuable event
    # --------------------------------------------------------

    def _get_error_store(self) -> Any:
        """Return the real ErrorStore backing refutation records (lazy)."""
        if self._error_store is None:
            from scp.brain.error_store import ErrorStore

            self._error_store = ErrorStore()
        return self._error_store

    def record_refutation(
        self, question: str, v4_verdict: str, reality: str
    ) -> dict:
        """Record when Reality proves V4 wrong."""
        refutation = Refutation(
            refutation_id=f"ref_{uuid.uuid4().hex[:12]}",
            timestamp=datetime.now(timezone.utc).isoformat(),
            question=question,
            v4_verdict=v4_verdict,
            reality=reality,
            lesson=(
                f"V4 said '{v4_verdict}' but reality was '{reality}' — "
                f"scope limit exposed and recorded in ErrorStore."
            ),
        )
        self.refutations.append(refutation)
        self.scope_limits["last_self_falsification"] = refutation.timestamp
        self.test_coverage["refutations"] += 1

        logger.info(
            "REFUTATION_RECORDED id=%s question=%r v4=%r reality=%r",
            refutation.refutation_id, question[:80], v4_verdict, reality[:80],
        )

        # [A3 F-M2] Persist to the REAL ErrorStore — best-effort but HONEST.
        errorstore_grew = False
        errorstore_id: str | None = None
        errorstore_note: str | None = None
        try:
            record = self._get_error_store().add(
                question=question,
                answer=refutation.lesson,
                verdict="REFUTED",
                domain="falsification",
                error_type="v4_refutation",
                details={
                    "refutation_id": refutation.refutation_id,
                    "v4_verdict": v4_verdict,
                    "reality": reality,
                },
            )
            if isinstance(record, dict) and record.get("rejected"):
                errorstore_note = (
                    "errorstore_rejected:"
                    + str(record.get("reject_reason", "unknown"))
                )
                logger.warning(
                    "Refutation %s NOT persisted — ErrorStore rejected it (%s)",
                    refutation.refutation_id,
                    record.get("reject_reason", "unknown"),
                )
            else:
                errorstore_grew = True
                errorstore_id = (
                    str(record.get("id")) if isinstance(record, dict) else None
                )
                self._errorstore_persisted += 1
        except Exception as exc:
            errorstore_note = (
                f"errorstore_unavailable:{type(exc).__name__}:{exc}"
            )
            logger.warning(
                "Refutation %s could NOT be persisted to ErrorStore: %s",
                refutation.refutation_id,
                errorstore_note,
                exc_info=True,
            )

        return {
            "refutation_id": refutation.refutation_id,
            "recorded": True,
            "errorstore_grew": errorstore_grew,
            "errorstore_id": errorstore_id,
            "errorstore_note": errorstore_note,
            "lesson": refutation.lesson,
            "timestamp": refutation.timestamp,
        }

    # --------------------------------------------------------
    # HUMAN ESCALATION POLICY
    # --------------------------------------------------------

    def should_escalate_human(
        self, confidence: float, contradictions: list
    ) -> tuple[bool, str]:
        """Determine if a human decision is required.

        Rule: confidence < 90%  OR  contradictions > 2  ->  escalate.
        """
        reasons: list[str] = []
        try:
            conf = float(confidence)
        except (TypeError, ValueError) as exc:
            logger.debug("falsification_engine: confidence unparseable, treating as 0: %s", exc, exc_info=True)
            conf = 0.0
            reasons.append("confidence unparseable — treating as 0%")
        if conf < CONFIDENCE_THRESHOLD:
            reasons.append(
                f"confidence {conf:.1%} < {CONFIDENCE_THRESHOLD:.0%} threshold"
            )
        n_contradictions = len(contradictions or [])
        if n_contradictions > MAX_CONTRADICTIONS_AUTO:
            reasons.append(
                f"{n_contradictions} contradictions > {MAX_CONTRADICTIONS_AUTO} "
                f"auto-decide limit"
            )
        if reasons:
            return True, "; ".join(reasons)
        return False, (
            f"within auto-decide scope (confidence {conf:.1%}, "
            f"{n_contradictions} contradictions)"
        )

    # --------------------------------------------------------
    # FALSIFICATION STATS — focused on LIMITS, not successes
    # --------------------------------------------------------

    def get_falsification_stats(self) -> dict:
        """Stats focused on V4's LIMITS, not its successes."""
        human_escalations = sum(
            1 for r in self.refutations
            if "human" in (r.v4_verdict or "").lower()
            or "escalat" in (r.v4_verdict or "").lower()
        )
        scope = self.calculate_scope()
        growth_rate = float(len(self.refutations))

        return {
            "total_refutations": len(self.refutations),
            "total_contradictions": len(self.contradictions),
            "coverage_gaps": scope["coverage_gaps"],
            "errorstore_growth_rate": round(growth_rate, 4),
            "human_escalations": human_escalations,
            "last_self_falsification": scope["last_falsification"],
            "scope_label": (
                f"{V4_TOTAL_TEST_CASES} test cases, "
                f"{V4_LAYERS_ACTIVE} layers, "
                f"{ERRORSTORE_TARGET_SIZE // 1000}K error store"
            ),
            "test_coverage": dict(self.test_coverage),
        }

    # --------------------------------------------------------
    # SERIALIZATION (for persistence / debugging)
    # --------------------------------------------------------

    def to_dict(self) -> dict:
        """Serialize the engine state to a plain dict (JSON-safe)."""
        return {
            "contradictions": [c.to_dict() for c in self.contradictions],
            "refutations": [r.to_dict() for r in self.refutations],
            "scope_limits": {
                k: (list(v) if isinstance(v, set) else v)
                for k, v in self.scope_limits.items()
            },
            "test_coverage": dict(self.test_coverage),
            "stats": self.get_falsification_stats(),
        }

    def __repr__(self) -> str:
        return (
            f"<FalsificationEngine contradictions={len(self.contradictions)} "
            f"refutations={len(self.refutations)}>"
        )


__all__ = [
    "CONFIDENCE_THRESHOLD",
    "MAX_CONTRADICTIONS_AUTO",
    "V4_TOTAL_TEST_CASES",
    "V4_LAYERS_ACTIVE",
    "ERRORSTORE_TARGET_SIZE",
    "KB_DEFAULT_SIZE",
    "SEVERITY_LOW",
    "SEVERITY_MEDIUM",
    "SEVERITY_HIGH",
    "FalsificationStatus",
    "Deviation",
    "Contradiction",
    "Refutation",
    "FalsificationEngine",
    "_NUM_UNIT_RE",
    "_UNIT_MULTIPLIERS",
    "_normalize_number",
    "_smoke_test",
]

if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    _smoke_test()
