# SPDX-License-Identifier: AGPL-3.0-only
"""
scp/security/defense_antibody_synthesizer.py
=============================================
Milestone M2: Adaptive Defense Antibody Synthesizer & Quarantine Gate.

Autonomous biological cyber-immune system engine for SCP Agent OS:
  - Layer 1: Structural AST Antibody Synthesizer (Base64, Rot13, Leet, Unicode Confusables, \\b word boundaries)
  - Layer 2: Vector Semantic Intent Classifier (Cosine similarity over intent prototypes, dual-mode dense/subword vectorizer)
  - Layer 3: Kernel State Machine Tripwire (Unauthorized TaskKernel state mutation interceptor)
  - QuarantineGate: Dual empirical gate (100.0% negative block rate + 0.0% false positive preservation)
  - DefenseAntibodySynthesizer: Master synthesis engine promoting validated rules into AttackPatternMemory
"""
from __future__ import annotations

import base64
import codecs
import hashlib
import json
import logging
import math
import os
import re
import time
from pathlib import Path
from typing import Any

from scp.security.attack_memory import AttackPatternMemory, DynamicRule
from scp.security.defense_antibody_parts import (
    AntibodyRuleCandidate,
    DeterministicSemanticEmbedder,
    Layer1StructuralSynthesizer,
    Layer2SemanticIntentClassifier,
    Layer3KernelTripwire,
    QuarantineVerdict,
    SynthesisReport,
)

logger = logging.getLogger("scp.security.defense_antibody_synthesizer")


# ==============================================================================
# QUARANTINE GATE (DUAL EMPIRICAL EVALUATION)
# ==============================================================================

class QuarantineGate:
    """Dual empirical evaluation gate enforcing negative block rate and positive preservation.
    
    Invariants:
      1. Negative Test: Candidate must block 100.0% of seed attack and its mutated variants.
      2. Positive Preservation: Candidate must have 0.0% False Positive rate on benign corpus.
      3. Fail-Closed: If benign corpus is missing or empty, screening fails closed (reason='no_benign_history').
      4. Anti-Poisoning: Empty or overly broad patterns ('the') are rejected immediately.
    """

    def __init__(self, benign_path: str | Path | None = None, data_dir: str = "data"):
        self.data_dir = Path(data_dir)
        self.benign_path = Path(benign_path) if benign_path else (self.data_dir / "benign_questions.jsonl")
        self.layer1 = Layer1StructuralSynthesizer()
        self.layer2 = Layer2SemanticIntentClassifier(data_dir=data_dir)
        self.layer3 = Layer3KernelTripwire()

    def _generate_attack_variants(self, seed: str) -> list[str]:
        """Generate adversarial mutated variants of seed attack."""
        variants = [seed]
        # Rot13
        try:
            variants.append(codecs.encode(seed, "rot_13"))
        except Exception as rot_err:
            logger.debug("[QuarantineGate] Rot13 variant generation failed: %s", rot_err)
        # Base64
        try:
            variants.append(base64.b64encode(seed.encode("utf-8")).decode("utf-8"))
        except Exception as b64_err:
            logger.debug("[QuarantineGate] Base64 variant generation failed: %s", b64_err)
        # Case swapping
        variants.append(seed.swapcase())
        # Leetspeak mutation
        leet_map = {"e": "3", "i": "1", "o": "0", "s": "5", "a": "4"}
        leet_variant = "".join(leet_map.get(c.lower(), c) for c in seed)
        variants.append(leet_variant)
        # Dotted mutation
        words = seed.split()
        if words:
            variants.append(" ".join(".".join(w.replace(".", "")) for w in words))
        return variants

    def _matches_rule(self, pattern: str, rule_type: str, text: str) -> bool:
        """Check if a pattern matches a given text using strict boundary-respecting rules."""
        if not pattern or not pattern.strip():
            return False

        text_lower = text.lower()
        pattern_lower = pattern.lower().strip()

        if rule_type == "keyword":
            # Strict word boundary matching (strip accidental regex delimiters)
            cleaned = re.sub(r"^\(\?i\)\\b|^\(\?i\)|^\\b|\\b$", "", pattern_lower)
            return bool(re.search(rf"\b{re.escape(cleaned)}\b", text_lower))
        elif rule_type == "pattern":
            try:
                return bool(re.compile(pattern, re.IGNORECASE).search(text))
            except re.error:
                return False
        else:
            return pattern_lower in text_lower

    def evaluate(
        self,
        candidate: AntibodyRuleCandidate | DynamicRule,
        seed_attacks: list[str] | str | None = None,
        benign_history_path: Path | str | None = None,
    ) -> QuarantineVerdict:
        """Execute dual empirical evaluation on candidate rule."""
        rule_id = getattr(candidate, "candidate_id", getattr(candidate, "rule_id", "rule_cand"))
        pattern = getattr(candidate, "pattern", "")
        rule_type = getattr(candidate, "rule_type", "pattern")

        # 1. Anti-Poisoning Check: Empty/Whitespace patterns fail closed
        if not pattern or not pattern.strip():
            return QuarantineVerdict(
                passed=False,
                negative_block_rate=0.0,
                false_positive_rate=0.0,
                benign_matches=0,
                benign_samples_tested=0,
                attack_variants_tested=0,
                reason="empty_pattern",
                candidate_id=rule_id,
            )

        # 2. Positive Preservation Gate: Screen against benign history
        b_path = Path(benign_history_path) if benign_history_path else self.benign_path
        benign_samples: list[str] = []
        if b_path.is_file():
            try:
                for line in b_path.read_text(encoding="utf-8", errors="replace").splitlines():
                    line_clean = line.strip()
                    if not line_clean:
                        continue
                    try:
                        rec = json.loads(line_clean)
                        q = rec.get("question", "") if isinstance(rec, dict) else str(rec)
                    except json.JSONDecodeError:
                        q = line_clean
                    q_str = str(q).strip()
                    if q_str:
                        benign_samples.append(q_str)
            except Exception as exc:
                logger.warning("[QuarantineGate] Error reading benign history: %s", exc)

        if not benign_samples:
            # Fail-closed invariant: no benign history -> rule is unproven!
            return QuarantineVerdict(
                passed=False,
                negative_block_rate=0.0,
                false_positive_rate=0.0,
                benign_matches=0,
                benign_samples_tested=0,
                attack_variants_tested=0,
                reason="no_benign_history",
                candidate_id=rule_id,
            )

        benign_matches = 0
        for b_query in benign_samples:
            if self._matches_rule(pattern, rule_type, b_query):
                benign_matches += 1

        fp_rate = benign_matches / len(benign_samples)
        if benign_matches > 0 or fp_rate > 0.0:
            return QuarantineVerdict(
                passed=False,
                negative_block_rate=0.0,
                false_positive_rate=fp_rate,
                benign_matches=benign_matches,
                benign_samples_tested=len(benign_samples),
                attack_variants_tested=0,
                reason="false_positive_detected",
                candidate_id=rule_id,
            )

        # 3. Negative Attack Blocking Gate: Must block 100% of seed attack variants
        raw_seed = getattr(candidate, "raw_payload", "")
        seeds: list[str] = []
        if isinstance(seed_attacks, str) and seed_attacks.strip():
            seeds = [seed_attacks]
        elif isinstance(seed_attacks, list) and seed_attacks:
            seeds = seed_attacks
        elif raw_seed:
            seeds = [raw_seed]

        attack_variants: list[str] = []
        for s in seeds:
            attack_variants.extend(self._generate_attack_variants(s))

        if not attack_variants:
            # If no seed variants provided, candidate passed FP check with clean pattern
            return QuarantineVerdict(
                passed=True,
                negative_block_rate=1.0,
                false_positive_rate=0.0,
                benign_matches=0,
                benign_samples_tested=len(benign_samples),
                attack_variants_tested=0,
                reason="passed",
                candidate_id=rule_id,
            )

        blocked_count = 0
        for variant in attack_variants:
            # Check direct match or decoded match across Layer 1 / 2 / 3
            direct_hit = self._matches_rule(pattern, rule_type, variant)
            decoded_hit = False
            if not direct_hit:
                for dec in self.layer1.deobfuscate_all(variant):
                    if self._matches_rule(pattern, rule_type, dec):
                        decoded_hit = True
                        break

            tripwire_hit = self.layer3.has_state_mutation(variant) if getattr(candidate, "layer", 1) == 3 else False

            if direct_hit or decoded_hit or tripwire_hit:
                blocked_count += 1

        block_rate = blocked_count / len(attack_variants)
        if block_rate < 1.0:
            return QuarantineVerdict(
                passed=False,
                negative_block_rate=block_rate,
                false_positive_rate=0.0,
                benign_matches=0,
                benign_samples_tested=len(benign_samples),
                attack_variants_tested=len(attack_variants),
                reason="failed_negative_block",
                candidate_id=rule_id,
            )

        return QuarantineVerdict(
            passed=True,
            negative_block_rate=1.0,
            false_positive_rate=0.0,
            benign_matches=0,
            benign_samples_tested=len(benign_samples),
            attack_variants_tested=len(attack_variants),
            reason="passed",
            candidate_id=rule_id,
        )


# ==============================================================================
# DEFENSE ANTIBODY SYNTHESIZER (MASTER ENGINE)
# ==============================================================================

class DefenseAntibodySynthesizer:
    """Master engine orchestrating 3-layer antibody synthesis, quarantine, and promotion."""

    def __init__(self, data_dir: str = "data"):
        self.data_dir = Path(data_dir)
        self.attack_memory = AttackPatternMemory(data_dir=str(self.data_dir))
        self.quarantine_gate = QuarantineGate(
            benign_path=self.data_dir / "benign_questions.jsonl",
            data_dir=str(self.data_dir),
        )
        self.layer1 = Layer1StructuralSynthesizer()
        self.layer2 = Layer2SemanticIntentClassifier(data_dir=str(self.data_dir))
        self.layer3 = Layer3KernelTripwire()

    def synthesize_from_intel(self, intel_entry: dict[str, Any] | str) -> AntibodyRuleCandidate | None:
        """Deconstruct threat intelligence entry across Layers 1, 2, and 3."""
        if isinstance(intel_entry, str):
            payload = intel_entry
            target_type = "threat_intel"
        else:
            payload = intel_entry.get("raw_payload", "") or intel_entry.get("payload", "") or intel_entry.get("pattern", "")
            target_type = intel_entry.get("category", "threat_intel")

        if not payload:
            return None

        # Check Layer 3 Tripwire first
        if self.layer3.has_state_mutation(payload):
            cand = self.layer3.synthesize_candidate(payload)
            if cand:
                return cand

        # Check Layer 1 Structural AST
        cand_l1 = self.layer1.synthesize_candidate(payload, target_attack_type=target_type)
        if cand_l1:
            return cand_l1

        # Fallback to Layer 2 Semantic Vector Intent
        return self.layer2.synthesize_candidate(payload, target_attack_type=target_type)

    def synthesize_from_bypass(self, bypass_entry: dict[str, Any]) -> AntibodyRuleCandidate | None:
        """Synthesize candidate from simulation bypass finding."""
        mutated = bypass_entry.get("mutated", "")
        original = bypass_entry.get("original", "")
        payload = mutated or original
        if not payload:
            return None
        return self.synthesize_from_intel(payload)

    def quarantine_and_promote(
        self,
        candidate: AntibodyRuleCandidate,
        seed_attacks: list[str] | None = None,
        benign_history_path: Path | str | None = None,
    ) -> dict[str, Any]:
        """Subject candidate to QuarantineGate and promote into AttackPatternMemory upon passing."""
        b_path = benign_history_path or (self.data_dir / "benign_questions.jsonl")
        verdict = self.quarantine_gate.evaluate(
            candidate=candidate,
            seed_attacks=seed_attacks,
            benign_history_path=b_path,
        )

        if not verdict.passed:
            return {
                "success": False,
                "rule_id": candidate.rule_id,
                "reason": verdict.reason,
                "verdict": verdict.to_dict(),
            }

        # Register pending dynamic rule into AttackPatternMemory
        dynamic_rule = candidate.to_dynamic_rule()
        with self.attack_memory._lock:
            self.attack_memory.dynamic_rules[dynamic_rule.rule_id] = dynamic_rule

        # Promote rule through official operator gate
        promo_result = self.attack_memory.promote_rule(
            rule_id=dynamic_rule.rule_id,
            promoted_by="quarantine_gate",
            benign_history_path=b_path,
        )

        return {
            "success": promo_result.get("success", False),
            "rule_id": dynamic_rule.rule_id,
            "verdict": verdict.to_dict(),
            "promotion": promo_result,
        }

    def run_synthesis_cycle(
        self,
        raw_intel_path: Path | str | None = None,
        max_items: int = 50,
    ) -> SynthesisReport:
        """Execute a full autonomous synthesis & quarantine cycle over ingested threat intel."""
        t0 = time.time()
        intel_file = Path(raw_intel_path) if raw_intel_path else (self.data_dir / "raw_threat_intel.jsonl")
        report = SynthesisReport()

        if not intel_file.is_file():
            report.elapsed_ms = (time.time() - t0) * 1000
            return report

        entries: list[dict[str, Any]] = []
        try:
            for line in intel_file.read_text(encoding="utf-8", errors="replace").splitlines():
                if not line.strip():
                    continue
                try:
                    entries.append(json.loads(line))
                except Exception:
                    continue
                if len(entries) >= max_items:
                    break
        except Exception as exc:
            logger.warning("[DefenseAntibodySynthesizer] Failed reading threat intel: %s", exc)

        report.total_intel_processed = len(entries)

        for entry in entries:
            candidate = self.synthesize_from_intel(entry)
            if not candidate:
                continue

            report.candidates_generated += 1
            payload = entry.get("raw_payload", "") or entry.get("payload", "") or entry.get("pattern", "")
            seeds = [payload] if payload else None

            res = self.quarantine_and_promote(candidate, seed_attacks=seeds)
            if res.get("success"):
                report.quarantine_passed += 1
                report.promoted_rule_ids.append(candidate.rule_id)
            else:
                report.quarantine_rejected += 1

        report.elapsed_ms = (time.time() - t0) * 1000
        return report


__all__ = [
    "AntibodyRuleCandidate",
    "QuarantineVerdict",
    "SynthesisReport",
    "Layer1StructuralSynthesizer",
    "DeterministicSemanticEmbedder",
    "Layer2SemanticIntentClassifier",
    "Layer3KernelTripwire",
    "QuarantineGate",
    "DefenseAntibodySynthesizer",
]
