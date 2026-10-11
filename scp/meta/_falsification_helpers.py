# SPDX-License-Identifier: AGPL-3.0-only
"""Constants, data models, and smoke test for scp.meta.falsification_engine."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any

# ============================================================
# CONSTANTS — V4 FORTRESS scope fingerprint
# ============================================================

CONFIDENCE_THRESHOLD: float = 0.90        # < 90%  -> HUMAN_DECISION_REQUIRED
MAX_CONTRADICTIONS_AUTO: int = 2          # > 2    -> HUMAN_DECISION_REQUIRED
V4_TOTAL_TEST_CASES: int = 161            # PyRIT (161) + garak probes
V4_LAYERS_ACTIVE: int = 14                # 10 sensor layers + 4 countermeasures
ERRORSTORE_TARGET_SIZE: int = 50_000      # 50K records target capacity
KB_DEFAULT_SIZE: int = 12_000             # placeholder KB size

# Severity thresholds (relative delta)
SEVERITY_LOW: float = 0.02      # < 2%   -> LOW
SEVERITY_MEDIUM: float = 0.10   # < 10%  -> MEDIUM
SEVERITY_HIGH: float = 0.25     # < 25%  -> HIGH
# >= 25% -> CRITICAL


# ============================================================
# SKEPTICAL STATUS LANGUAGE
# ============================================================


class FalsificationStatus(str, Enum):
    """Skeptical status language — NEVER absolute truth claims."""

    UNREFUTED_IN_CURRENT_SCOPE = "UNREFUTED_IN_CURRENT_SCOPE"  # was PASS
    NO_KNOWN_PATTERN_MATCHED = "NO_KNOWN_PATTERN_MATCHED"      # was SAFE
    NO_CONTRADICTION_FOUND = "NO_CONTRADICTION_FOUND"          # was GROUND_TRUTH_VALID
    CONTRADICTION_DETECTED = "CONTRADICTION_DETECTED"          # LLM vs source mismatch
    PATTERN_MATCHED = "PATTERN_MATCHED"                        # was FAIL
    HUMAN_DECISION_REQUIRED = "HUMAN_DECISION_REQUIRED"        # confidence < 90%
    REFUTED_BY_REALITY = "REFUTED_BY_REALITY"                  # ground truth proved V4 wrong
    OUT_OF_SCOPE = "OUT_OF_SCOPE"                              # V4 cannot evaluate

    def is_skeptical(self) -> bool:
        """True if the status avoids any absolute truth claim."""
        return self in {
            FalsificationStatus.UNREFUTED_IN_CURRENT_SCOPE,
            FalsificationStatus.NO_KNOWN_PATTERN_MATCHED,
            FalsificationStatus.NO_CONTRADICTION_FOUND,
            FalsificationStatus.HUMAN_DECISION_REQUIRED,
            FalsificationStatus.OUT_OF_SCOPE,
        }

    def requires_human(self) -> bool:
        """True if a human must make the final decision."""
        return self in {
            FalsificationStatus.HUMAN_DECISION_REQUIRED,
            FalsificationStatus.REFUTED_BY_REALITY,
            FalsificationStatus.OUT_OF_SCOPE,
        }


# ============================================================
# DATA MODELS
# ============================================================


@dataclass
class Deviation:
    """A single measured deviation between an LLM answer and a reference."""

    field: str
    llm_value: str
    reference_value: str
    delta: float          # relative |llm - ref| / |ref|
    severity: str         # LOW | MEDIUM | HIGH | CRITICAL
    missing_claim: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "field": self.field,
            "llm_value": self.llm_value,
            "reference_value": self.reference_value,
            "delta": round(self.delta, 4),
            "severity": self.severity,
        }


@dataclass
class Contradiction:
    """A contradiction between two sources for a single field."""

    source_a: str
    source_b: str
    field: str
    value_a: str
    value_b: str
    severity: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_a": self.source_a,
            "source_b": self.source_b,
            "field": self.field,
            "value_a": self.value_a,
            "value_b": self.value_b,
            "severity": self.severity,
        }


@dataclass
class Refutation:
    """A record of reality proving V4 wrong — V4's most valuable event."""

    refutation_id: str
    timestamp: str
    question: str
    v4_verdict: str
    reality: str
    lesson: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _smoke_test() -> None:
    """Exercise every public method of FalsificationEngine."""
    from scp.meta.falsification_engine import FalsificationEngine

    sep = "=" * 72
    print(sep)
    print("SCP V4 FORTRESS — FalsificationEngine smoke test")
    print("Philosophy: V4 is a microscope, not a judge.")
    print(sep)

    engine = FalsificationEngine()

    print("\n[1] translate_old_verdict: PASS -> UNREFUTED_IN_CURRENT_SCOPE")
    translated = engine.translate_old_verdict(
        "PASS",
        {"scope": "161 test cases", "confidence": 0.95},
    )
    print(f"    status    = {translated['status']}")
    print(f"    scope     = {translated['scope']}")
    print(f"    note      = {translated['note']}")
    print(f"    confidence= {translated['confidence']}")
    assert translated["status"] == "UNREFUTED_IN_CURRENT_SCOPE", (  # noqa: S101
        "PASS must translate to UNREFUTED_IN_CURRENT_SCOPE"
    )
    assert "Not declared safe" in translated["note"]  # noqa: S101
    print("    [OK] PASS correctly translated to skeptical status.")

    fail_t = engine.translate_old_verdict(
        "FAIL", {"pattern": "prompt_injection_jailbreak", "confidence": 0.99}
    )
    print(f"\n    FAIL   -> {fail_t['status']} (pattern={fail_t['pattern']})")
    assert fail_t["status"] == "PATTERN_MATCHED"  # noqa: S101

    unc_t = engine.translate_old_verdict(
        "UNCERTAIN", {"reason": "conflicting layers", "confidence": 0.55}
    )
    print(f"    UNCERT -> {unc_t['status']} (reason={unc_t['reason']})")
    assert unc_t["status"] == "HUMAN_DECISION_REQUIRED"  # noqa: S101

    esc_t = engine.translate_old_verdict(
        "ESCALATED", {"reason": "medical dosage — safety critical"}
    )
    print(f"    ESCAL  -> {esc_t['status']} (reason={esc_t['reason']})")
    assert esc_t["status"] == "HUMAN_DECISION_REQUIRED"  # noqa: S101

    unk_t = engine.translate_old_verdict("BOGUS_VERDICT", {})
    print(f"    BOGUS  -> {unk_t['status']}")
    assert unk_t["status"] == "OUT_OF_SCOPE"  # noqa: S101

    print("\n[2] measure_deviation: LLM '52 tỷ' vs reference '62.849 tỷ'")
    llm_answer = "FPT Corporation reported revenue of 52 tỷ VND in Q3 2026."
    reference_data = {"revenue": "62.849 tỷ"}
    dev = engine.measure_deviation(llm_answer, reference_data)
    print(f"    status            = {dev['status']}")
    print(f"    max_deviation     = {dev['max_deviation']:.4f} "
          f"({dev['max_deviation']*100:.2f}%)")
    print(f"    contradiction_cnt = {dev['contradiction_count']}")
    for d in dev["deviations"]:
        print(f"    - {d['field']}: llm={d['llm_value']} vs "
              f"ref={d['reference_value']} delta={d['delta']:.4f} "
              f"severity={d['severity']}")
    assert dev["status"] == "CONTRADICTION_DETECTED", (  # noqa: S101
        "52 tỷ vs 62.849 tỷ must be a contradiction"
    )
    assert dev["max_deviation"] > 0.10, "delta should exceed 10%"  # noqa: S101
    assert dev["deviations"][0]["severity"] in ("HIGH", "CRITICAL")  # noqa: S101
    print("    [OK] Deviation correctly measured — NOT corrected, only measured.")

    print("\n[3] generate_contradiction_report: conflicting evidence")
    question = "What was FPT's Q3 2026 revenue?"
    llm_answer_2 = "FPT revenue was 52 tỷ VND, profit 8 tỷ VND."
    evidence = [
        {"source": "Cafef", "revenue": "60 tỷ"},
        {"source": "VnExpress", "revenue": "62.849 tỷ"},
    ]
    ground_truth = {"revenue": "62.849 tỷ", "profit": "8.5 tỷ"}
    report = engine.generate_contradiction_report(
        question, llm_answer_2, evidence, ground_truth
    )
    print(f"    question              = {report['question']}")
    print(f"    evidence_summary      = {report['evidence_summary']}")
    print(f"    contradictions        = {len(report['contradictions'])}")
    for c in report["contradictions"]:
        print(f"      - {c['source_a']} vs {c['source_b']} on {c['field']}: "
              f"{c['value_a']} vs {c['value_b']} [{c['severity']}]")
    print(f"    unrefuted_claims      = {len(report['unrefuted_claims'])}")
    print(f"    confidence            = {report['confidence']:.4f}")
    print(f"    human_decision_req    = {report['human_decision_required']}")
    print(f"    v4_recommendation     = {report['v4_recommendation']}")
    assert len(report["contradictions"]) >= 1, "should find >=1 contradiction"  # noqa: S101
    assert "Review" in report["v4_recommendation"] or "No contradictions" in report["v4_recommendation"]  # noqa: S101
    print("    [OK] Report generated — no conclusion, only data for human review.")

    print("\n[4] should_escalate_human: confidence 0.85 < 0.90")
    escalate, reason = engine.should_escalate_human(0.85, [])
    print(f"    escalate = {escalate}")
    print(f"    reason   = {reason}")
    assert escalate is True, "0.85 < 0.90 must escalate"  # noqa: S101
    assert "confidence" in reason.lower()  # noqa: S101

    esc2, reason2 = engine.should_escalate_human(0.90, [])
    print(f"    boundary 0.90 -> escalate={esc2} ({reason2})")
    assert esc2 is False, "0.90 == threshold should NOT escalate"  # noqa: S101

    esc3, reason3 = engine.should_escalate_human(0.99, [1, 2, 3])
    print(f"    3 contradictions @ 0.99 -> escalate={esc3} ({reason3})")
    assert esc3 is True, "3 contradictions must escalate"  # noqa: S101
    print("    [OK] Escalation policy correctly enforced.")

    print("\n[5] record_refutation: reality proves V4 wrong")
    ref = engine.record_refutation(
        question="Is medicine X safe in pregnancy?",
        v4_verdict="UNREFUTED_IN_CURRENT_SCOPE",
        reality="CONTRAINDICATED in 1st trimester — FDA category D",
    )
    print(f"    refutation_id   = {ref['refutation_id']}")
    print(f"    recorded        = {ref['recorded']}")
    print(f"    errorstore_grew = {ref['errorstore_grew']}")
    print(f"    lesson          = {ref['lesson']}")
    assert ref["recorded"] is True  # noqa: S101
    assert ref["errorstore_grew"] is True  # noqa: S101
    assert len(engine.refutations) == 1  # noqa: S101
    print("    [OK] Refutation recorded — ErrorStore grew by 1.")

    print("\n[6] calculate_scope: what V4 tested vs what it didn't")
    scope = engine.calculate_scope()
    print(f"    total_test_cases   = {scope['total_test_cases']}")
    print(f"    layers_active      = {scope['layers_active']}")
    print(f"    errorstore_size    = {scope['errorstore_size']}")
    print(f"    kb_size            = {scope['kb_size']}")
    print(f"    coverage_gaps      = {len(scope['coverage_gaps'])} known gaps")
    for g in scope["coverage_gaps"][:3]:
        print(f"      - {g}")
    print(f"    last_falsification = {scope['last_falsification']}")
    assert scope["total_test_cases"] == V4_TOTAL_TEST_CASES  # noqa: S101
    assert scope["layers_active"] == V4_LAYERS_ACTIVE  # noqa: S101
    assert len(scope["coverage_gaps"]) > 0, "V4 must acknowledge its blind spots"  # noqa: S101
    print("    [OK] Scope fingerprint includes coverage gaps.")

    print("\n[7] get_falsification_stats: V4's limits, not its successes")
    stats = engine.get_falsification_stats()
    print(f"    total_refutations       = {stats['total_refutations']}")
    print(f"    total_contradictions    = {stats['total_contradictions']}")
    print(f"    coverage_gaps           = {len(stats['coverage_gaps'])} gaps")
    print(f"    errorstore_growth_rate  = {stats['errorstore_growth_rate']}")
    print(f"    human_escalations       = {stats['human_escalations']}")
    print(f"    last_self_falsification = {stats['last_self_falsification']}")
    print(f"    scope_label             = {stats['scope_label']}")
    print(f"    test_coverage           = {stats['test_coverage']}")
    assert stats["total_refutations"] == 1  # noqa: S101
    assert stats["total_contradictions"] >= 1  # noqa: S101
    assert "161 test cases" in stats["scope_label"]  # noqa: S101
    print("    [OK] Stats focus on limits — refutations, gaps, escalations.")

    print("\n[8] to_dict() serialization sanity")
    payload = engine.to_dict()
    json_str = json.dumps(payload, ensure_ascii=False, default=str)
    print(f"    serialized length = {len(json_str)} chars")
    print(f"    refutations       = {len(payload['refutations'])}")
    print(f"    contradictions    = {len(payload['contradictions'])}")
    assert len(payload["refutations"]) == 1  # noqa: S101
    assert len(payload["contradictions"]) >= 1  # noqa: S101
    print("    [OK] Engine state is JSON-serializable.")

    print("\n" + sep)
    print("ALL SMOKE TESTS PASSED")
    print("V4 never declared SAFE or TRUTH — only measured and reported.")
    print(sep)
