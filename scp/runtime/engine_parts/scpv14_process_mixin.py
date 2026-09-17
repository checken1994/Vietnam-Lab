"""
SCP - Viet Nam | Self-Correcting Pipeline
Copyright (c) 2026 SCP Vietnam Project. All Rights Reserved.




License: See LICENSE file
Contact: scp-vietnam@example.com
"""

#!/usr/bin/env python3
"""
SCP V14 — Multi-SLM + Reality Judge + Self-Healing Engine.

Nâng cấp từ V13:
  V13 = Reality Engine + Generic Pipeline + Self-Healing (single engine)
  V14 = Multi-SLM (Math/Biology/Finance) + Reality Judge (cross-check) + Enhanced Self-Healing

Kiến trúc V14:
                           ???????????????????????
                           ?    SCP V14 Gateway   ?
                           ?  (SCPV14 entry point) ?
                           ???????????????????????
                                      ?
                    ?????????????????????????????????????
                    ?                 ?                 ?
                    ?                 ?                 ?
              ????????????     ????????????     ????????????
              ? MathSLM  ?     ? BioSLM   ?     ? FinSLM   ?
              ? (toán)   ?     ? (sinh)   ?     ? (tài)    ?
              ????????????     ????????????     ????????????
                   ?                ?                ?
                   ???????????????????????????????????
                                    ?
                         ???????????????????????
                         ?   Reality Judge     ?
                         ?  - Cross-check SLMs ?
                         ?  - Verify với V13   ?
                         ?  - Confidence score  ?
                         ???????????????????????
                                    ?
                                    ?
                         ???????????????????????
                         ? Self-Healing Engine ?
                         ?  - 5 healing strategies ?
                         ?  - Monitor + heal    ?
                         ?  - ErrorHistory       ?
                         ???????????????????????

Usage:
    from v14_engine import SCPV14
    engine = SCPV14()
    result = engine.process("Tính 2+3", "2 + 3 = 5")
    print(result.verdict)  # PASS / FAIL / CONFLICT / PARTIAL
"""

import os
import sys
from datetime import datetime

# Import V13 components (base layer)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

import logging

from scp.core.db_manager import (
    DB_PATH,
    db_exec,
)

logger = logging.getLogger("scp.v14")

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class JudgeVerdict:
    """Verdict từ Reality Judge."""
    question: str = ""
    slm_responses: list[dict] = field(default_factory=list)
    final_answer: str = ""
    confidence: float = 0.0
    verdict: str = "UNKNOWN"          # PASS / FAIL / CONFLICT / PARTIAL / UNKNOWN
    reasoning: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)
    domain: str = "unknown"
    cross_validation: dict[str, Any] = field(default_factory=dict)
    slm_scores: dict[str, Any] = field(default_factory=dict)
    reality_check: dict[str, Any] = field(default_factory=dict)
    timestamp: str = ""
    similar_errors: list[dict] = field(default_factory=list)
    skeptical: bool = False

    def to_dict(self):
        return asdict(self)

# [Task 10-B Modularity Refactor B] Re-export extracted helpers — backward compat.
# DirectAPIVerifier + _run_periodic_cleanup moved to engine_parts/antibody_adapter.
# Cache/process_batch/entity extractors moved to engine_parts/slm_coordinator.
# engine_parts split reverted — DirectAPIVerifier stays in engine.py

# Detail constants
DETAIL_NONE = ""
DETAIL_NO_CHECKER = "NO_CHECKER"
DETAIL_NO_DATA = "NO_DATA"
DETAIL_API_ERROR = "API_ERROR"
DETAIL_TIMEOUT = "TIMEOUT"
DETAIL_NO_NUMBER = "NO_NUMBER"

# Import Brain components

# Import Meta-Cognition


# ============================================================
# [Task 10-B] DirectAPIVerifier + _run_periodic_cleanup extracted to
# engine_parts/antibody_adapter.py — re-exported above for backward compat.
# ============================================================

# ============================================================
# SCP V14 — ENTRY POINT
# ============================================================


class SCPV14ProcessMixin:
    """Mixin for SCPV14 — provides the process method."""

    def process(self, question: str, ai_answer: str = "", source: str = "") -> JudgeVerdict:
        """
        Process (question, ai_answer) qua V14 pipeline:
            Question -> Antibody -> SLMs -> Judge -> Reality verify -> Self-Healing
                     -> RealityAnchor -> KnowledgeStore -> HypothesisZone -> Experience

         Added `source` parameter for accurate QuestionTracker labeling:
            - "real_fetcher" — from RealQuestionFetcher (V67+, real web questions)
            - "curiosity"    — from CuriosityAsker (V68+, only pre-existing meta_curiosity)
            - "external"     — user manually asking (cycle_count == 0)
            - "generator"    — legacy synthetic (should not exist in V67+)
            - ""             — auto-detect: cycle_count > 0 → "generator", else "external"

        Returns: JudgeVerdict
        """
        self.cycle_count += 1

        # [FIX LEAK] GC every 10 cycles + WAL checkpoint every 200 cycles + cache flush every 100
        if self.cycle_count % 2000 == 0:  # [V90 OPT] — ran 40x per batch!
            import gc
            gc.collect()
        if self.cycle_count % 2000 == 0:  #  was %100 — ran 4x per batch!
            # Clear SLM response caches to free RAM
            for slm in self.judge.slms.values():
                slm.response_cache.clear()
        if self.cycle_count % 200 == 0:
            try:
                from scp.core.db_manager import checkpoint_wal
                checkpoint_wal()
            except Exception:
                logger.exception("[engine.py:647] silenced exception")

        #  Auto-cleanup history tables every 10 cycles — prevent 1GB/day growth
        if self.cycle_count % 2000 == 0:  # [V90 OPT] — ran 40x per batch!
            try:
                # [Z.ai-P0-FIX #28] TẠI SAO: import db_exec KHÔNG alias trong if block
                # → Python coi db_exec là local variable cho TOÀN HÀM process()
                # → line 869 db_exec(...) → UnboundLocalError (nếu if không chạy)
                # Hậu quả: PASS verdict KHÔNG BAO GIỜ lưu vào memory table (1999/2000 lần)
                # Fix: dùng alias _dbx_cleanup để không shadow global db_exec
                from scp.core.db_manager import db_exec as _dbx_cleanup
                cleanup_tables = [
                    ("calibration_history", 1000),
                    ("why_verification_plans", 500),
                    ("verdict_cache", 500),
                    ("smart_cache_disk", 200),
                    ("predictions", 200),
                    ("error_history", 5000),  #  was 500 — SCP forgot old errors too fast
                    ("falsification_history", 200),
                    ("experiences", 500),  # [V41.2] Add experiences — was missing, caused 25MB DB
                    ("reverify_queue", 100),
                    ("meta_curiosity", 500),  # [V41.2] Cap curiosity questions
                    ("meta_world_model", 500),  # [V41.2] Cap world model
                ]
                for table, keep in cleanup_tables:
                    try:
                        _dbx_cleanup(f"DELETE FROM {table} WHERE rowid NOT IN (SELECT rowid FROM {table} ORDER BY rowid DESC LIMIT {keep})")  # nosec B608 — input validated by SCP whitelist  # noqa: S608
                    except Exception:
                        logger.exception("[engine.py:670] silenced exception")
                # Delete completed reverify items
                try:
                    _dbx_cleanup("DELETE FROM reverify_queue WHERE status IN ('done', 'error')")
                except Exception:
                    logger.exception("[engine.py:675] silenced exception")
            except Exception:
                logger.exception("[engine.py:677] silenced exception")

        #  VACUUM every 500 cycles to reclaim disk space (not too frequent — VACUUM is expensive)
        if self.cycle_count % 500 == 0:
            try:
                import sqlite3
                conn = sqlite3.connect(DB_PATH, isolation_level=None)
                conn.execute("VACUUM")
                conn.close()
            except Exception:
                logger.exception("[engine.py:687] silenced exception")

        # [QUALITY] Parse AI answer để normalize trước khi process
        if ai_answer and self._parser:
            parsed_answer = self._parser.auto_parse(question, ai_answer)
            if parsed_answer != ai_answer:
                logger.debug(f"[PARSER] '{ai_answer[:30]}...' -> '{parsed_answer}'")
                ai_answer = parsed_answer

        # [PERF] SQLite CACHE - Check cache first (0 RAM)
        cache_key = f"{question}|{ai_answer}"
        cached_verdict = self._get_sqlite_cache(cache_key)
        if cached_verdict is not None:
            self._cache_hits += 1
            return cached_verdict
        self._cache_misses += 1

        # [EXEC-2 R2] KB short-circuit REMOVED — was a CRITICAL security bypass.
        #
        # Previously this block queried the knowledge table and returned early with
        # `reality_check={"is_correct": True/False}`, which bypassed the ENTIRE
        # security pipeline: MemoryPoisoningGuard, ThreatDetector, AttackPatternMemory,
        # Governance, Antibodies. A poisoned KB row (or an attacker-controlled
        # `times_verified` count) would have been trusted unconditionally.
        #
        # The block also wrote the unverified verdict to `_set_sqlite_cache`, so
        # subsequent identical requests would hit the cache at line ~695 and skip
        # security AGAIN — compounding the bypass.
        #
        # judge.py already has its OWN knowledge lookup at ~line 1334 (inside the
        # `with _v100_timer.phase("knowledge"):` block) which runs AFTER the V100
        # security steps (Step 0a-0c: unified detector / threat / memory poison).
        # That path treats KB hits as EVIDENCE (is_correct=None), never as final
        # verdict. So this outer short-circuit was redundant AND dangerous.
        #
        # See worklog.md EXEC-2 / FRESH-2 finding for details.

        # Step 0: Antibody check (early exit if closure words detected)
        if self.antibody is not None:
            try:
                antibody_result = self.antibody.analyze_claim(ai_answer)
                # [FIX-CRIT-27 BUG 4] was `.get("closure_detected")` — but the
                # lambda at line ~379 returns `{"is_closure": ..., "matched_word": ...}`.
                # Key mismatch → antibody FAIL path NEVER fired → closure-word
                # attacks passed through undetected.
                if antibody_result.get("is_closure"):
                    # AI is using closure words → mark as FAIL immediately
                    matched_word = antibody_result.get("matched_word") or "closure"
                    return JudgeVerdict(
                        question=question, slm_responses=[], final_answer=ai_answer,
                        confidence=0.0, verdict="FAIL",
                        reasoning=f"Antibody detected closure word: {matched_word}",
                        evidence={"antibody": antibody_result}, domain="language",
                        cross_validation={}, slm_scores={}, reality_check={},
                        timestamp=datetime.now().isoformat(),
                    )
            except Exception as e:
                logger.warning(f"Antibody check failed: {e}")

        # Step 1: Judge (includes SLM routing + cross-check + reality verify)
        #  Pass source through to judge() for accurate QuestionTracker labeling
        verdict = self.judge.judge(question, ai_answer, cycle_count=self.cycle_count,
                                    source=source)
        if isinstance(verdict, dict):
            verdict = JudgeVerdict(
                question=question,
                final_answer=ai_answer,
                verdict=verdict.get("verdict", "UNKNOWN"),
                confidence=float(verdict.get("confidence", 0.0) or 0.0),
                reasoning=verdict.get("reasoning", ""),
                evidence=verdict.get("evidence", {}),
                domain=verdict.get("domain", "unknown"),
                slm_responses=verdict.get("slm_responses", []),
                timestamp=verdict.get("timestamp", datetime.now().isoformat()),
            )

        # Step 2: [P2] Monitor + Self-Heal (if issues) - Enhanced with more triggers
        system_state = {
            "slm_latency": max((r.get("processing_time", 0) or 0) for r in verdict.slm_responses) if verdict.slm_responses else 0,
            "error_rate": 0.2 if verdict.verdict in ("FAIL", "CONFLICT") else 0.0,
            "verdict": verdict.verdict,
            "confidence": verdict.confidence or 0.5,
            "domain": verdict.domain or "unknown",
            "slm_responses": verdict.slm_responses or [],
        }

        # [V90 OPT] Skip healing for PASS with high confidence — 80% of questions
        monitor_result = {"has_issues": False, "issues": []}
        if verdict.verdict != "PASS" or verdict.confidence < 0.85:
            monitor_result = self.healing.monitor(system_state)
        if monitor_result["has_issues"]:
            for issue in monitor_result["issues"]:
                heal_result = self.healing.heal(issue)
                strategy_name = heal_result.get('strategy', 'none')
                success = heal_result.get('success', False)
                logger.info(f"Self-healing: {strategy_name} -> {'OK' if success else 'FAIL'}")
                #  Create meta_goal for high_error_rate issues
                if issue["type"] == "high_error_rate" and success:
                    try:
                        from scp.meta.meta import GoalMemory
                        goal_mgr = GoalMemory()
                        goal_mgr.add_goal(
                            description=f"Reduce error rate in {issue.get('details', 'system')}",
                            goal_type="accuracy_improvement",
                            priority=2,
                        )
                    except Exception:
                        logger.exception("[engine.py:782] silenced exception")

        # [V90 OPT] Skip health_state for UNKNOWN — no useful signal
        if verdict.verdict != "UNKNOWN":
            try:
                self.health_state.record(verdict.domain or "unknown", verdict.verdict)
            except Exception as e:
                # [ROOT-FIX 5] Was `except Exception as e: pass` — swallowed health_state
                # errors silently → error-rate tracking silently broken.
                logger.warning(f"[engine] health_state.record failed: {e}")

        # [V90 OPT] On FAIL only — create recovery issue + knowledge memory entry
        if verdict.verdict == "FAIL" and ai_answer:
            try:
                self.recovery_queue.create_issue(
                    domain=verdict.domain or "unknown",
                    question=question,
                    ai_answer=ai_answer,
                    error_type="slm_reality_mismatch",
                    cause=verdict.reasoning[:200] if verdict.reasoning else "SLM answer incorrect",
                    fix_action="reverify",
                )
                self.knowledge_memory.add(
                    question=question,
                    ai_answer=ai_answer,
                    domain=verdict.domain or "unknown",
                    error_type="slm_reality_mismatch",
                    cause=verdict.reasoning[:200] if verdict.reasoning else "SLM answer incorrect",
                    fix_action="reverify",
                    fix_artifact="pending",
                    evidence=str(verdict.reality_check.get("real_value", ""))[:200],
                    confidence=verdict.confidence or 0.5,
                )
            except Exception as e:
                # [ROOT-FIX 5] Was `except Exception as e: pass` — swallowed recovery_queue +
                # knowledge_memory write errors silently → FAIL questions not tracked for recovery.
                logger.warning(f"[engine] recovery_queue/knowledge_memory add failed: {e}")

        # [V89 FIX] Knowledge Correction — actually FIX the wrong knowledge entry
        # Instead of just logging the error, update the knowledge table with the correct value
        if verdict.verdict == "FAIL" and ai_answer:
            real_value = verdict.reality_check.get("real_value")
            if real_value is not None and str(real_value).strip():
                try:
                    from scp.core.db_manager import db_exec as _dbx
                    from scp.core.db_manager import db_query_one as _dbq1
                    # Check if knowledge entry exists for this question
                    existing = _dbq1("SELECT entity, value, confidence FROM knowledge WHERE entity = ? ORDER BY timestamp DESC LIMIT 1",
                                     (question[:50].lower(),))
                    if existing:
                        # UPDATE the existing knowledge with the correct reality_check value
                        _dbx("UPDATE knowledge SET value = ?, confidence = 0.9, source = 'reality_engine_correction', timestamp = ?, times_verified = times_verified + 1 WHERE entity = ?",
                             (str(real_value)[:500], datetime.now().isoformat(), question[:50].lower()))
                        logger.info(f"[HEALING] Knowledge CORRECTED: '{question[:40]}' → '{str(real_value)[:40]}'")
                    else:
                        # INSERT new knowledge with the correct value (using correct schema: entity/attribute/value)
                        _dbx("INSERT OR REPLACE INTO knowledge (entity, attribute, value, value_type, confidence, source, timestamp, times_verified) VALUES (?, 'value', ?, ?, 0.9, ?, ?, 1)",
                             (question[:50].lower(), str(real_value)[:500],
                              'float' if isinstance(real_value, (int, float)) else 'str',
                              'reality_engine_correction', datetime.now().isoformat()))
                        logger.info(f"[HEALING] Knowledge ADDED: '{question[:40]}' → '{str(real_value)[:40]}'")
                except Exception as e:
                    logger.warning(f"[HEALING] Knowledge correction failed: {e}")

        # Step 3: Record to V13 error_history (for self-learning)
        if verdict.verdict == "FAIL" and ai_answer:
            try:
                if hasattr(self.v13.v13, 'error_history'):
                    self.v13.v13.error_history.record(
                        question=question, ai_answer=ai_answer, frame=verdict.domain,
                        v13_verdict=verdict.reality_check.get("verdict", ""),
                        final_verdict=verdict.verdict, verdict_detail="",
                        error_type="slm_reality_mismatch",
                        source=verdict.reality_check.get("source", ""),
                        real_value=verdict.reality_check.get("real_value"),
                        ai_value=None, reason=verdict.reasoning,
                    )
            except Exception as e:
                logger.warning(f"V13 error_history record failed: {e}")

        #  Save PASS verdicts to memory table for future recovery reference
        if verdict.verdict == "PASS" and ai_answer:
            try:
                db_exec("""INSERT INTO memory (timestamp, question, ai_answer, frame, verdict, reason, status)
                          VALUES (?, ?, ?, ?, ?, ?, 'active')""",
                        (datetime.now().isoformat(), question[:500], ai_answer[:500],
                         verdict.domain or "unknown", "PASS",
                         str(verdict.reality_check.get("real_value", ""))[:200]))
            except Exception as e:
                # [ROOT-FIX 5] Was `except Exception as e: pass` — swallowed INSERT failures
                # silently → PASS verdicts not recorded for future recovery reference.
                logger.warning(f"[engine] memory table INSERT failed: {e}")

        # [V90 LEARN] Wikipedia fallback for UNKNOWN — retrieve as EVIDENCE only.
        # [FIX-CRIT-27 BUG 5] Previously: fetched Wikipedia extract → upgraded
        # verdict UNKNOWN→PARTIAL with conf=0.75 → cached to knowledge table as
        # confidence=0.75 (then 0.9 on subsequent reads). This treated RETRIEVAL
        # as VERIFICATION — violating Evidence-First ("SLM ≠ reality"). Now:
        # keep verdict=UNKNOWN; surface Wikipedia extract as a candidate_answer
        # in evidence (no confidence boost, no DB write, no reality_check upgrade).
        if verdict.verdict == "UNKNOWN" and not verdict.reality_check.get("real_value"):
            try:
                import urllib.parse

                from scp.core.api_utils import fetch_with_retry
                # Try Wikipedia API (works for both EN and VI)
                q_encoded = urllib.parse.quote(question[:100])
                # Try English Wikipedia first (more articles)
                wiki_url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{q_encoded}"
                # [SCP-DNA-FIX] fetch_with_retry(url, headers, timeout=10, ...) —
                # `headers` is required positional. Previous call missed it ->
                # TypeError -> silent except -> wikipedia_candidate never populated.
                # headers=None is handled inside fetch_with_retry (defaults to
                # {"User-Agent": "SCP/1.0"}).
                resp = fetch_with_retry(wiki_url, headers=None, timeout=5)
                if resp and isinstance(resp, dict):
                    extract = resp.get("extract", "")
                    title = resp.get("title", "")
                    if extract and len(extract) > 20:
                        # Wikipedia = EVIDENCE, not VERIFICATION. Do NOT upgrade
                        # verdict, do NOT write to knowledge table, do NOT set
                        # reality_check.real_value. Surface as candidate only.
                        wiki_answer = extract[:300]
                        verdict.evidence["wikipedia_candidate"] = {
                            "title": title,
                            "extract": wiki_answer,
                            "source": "wikipedia_en",
                            "verified": False,  # retrieval ≠ verification
                        }
                        logger.info(f"[LEARN] Wikipedia candidate (evidence-only, verdict stays UNKNOWN): '{question[:40]}' -> '{wiki_answer[:40]}'")
            except Exception as e:
                logger.debug(f"Wikipedia fallback error: {e}")

        # [V89 OPT] Skip steps 4-8 for UNKNOWN verdicts — nothing to learn/save
        if verdict.verdict == "UNKNOWN" and not verdict.reality_check.get("real_value"):
            # Cache and return — skip KnowledgeStore, HypothesisZone, RealityAnchor, Phase0
            self._set_sqlite_cache(cache_key, verdict)
            return verdict

        # [V90 LEARN] Store PASS verdicts as knowledge — learn from SUCCESS too
        if verdict.verdict == "PASS" and verdict.confidence >= 0.85:
            try:
                from scp.core.db_manager import db_exec as _dbx
                from scp.core.db_manager import db_query_one as _dbq1
                q_key = question[:200].lower().strip()
                real_val = verdict.reality_check.get("real_value") or verdict.final_answer
                if real_val and str(real_val).strip():
                    existing = _dbq1("SELECT entity, times_verified FROM knowledge WHERE entity = ?", (q_key,))
                    if existing:
                        _dbx("UPDATE knowledge SET value = ?, confidence = MIN(0.99, confidence + 0.02), times_verified = times_verified + 1, timestamp = ? WHERE entity = ?",
                             (str(real_val)[:500], datetime.now().isoformat(), q_key))
                    else:
                        _dbx("INSERT OR REPLACE INTO knowledge (entity, attribute, value, value_type, confidence, source, timestamp, times_verified) VALUES (?, 'verified_value', ?, ?, 0.9, 'slm_consensus', ?, 1)",
                             (q_key, str(real_val)[:500],
                              'float' if isinstance(real_val, (int, float)) else 'str',
                              datetime.now().isoformat()))
            except Exception as e:
                logger.debug(f"PASS knowledge store failed: {e}")

        # Step 4: [P0] FIX: Wire KnowledgeStore -- LEARN from each verdict
        # [FIX] Extract real_value from SLM responses if reality_check doesn't have it
        if self.knowledge_store is not None:
            try:
                real_value = verdict.reality_check.get("real_value")
                source = verdict.reality_check.get("source", "v14_judge")

                # [P0 FIX] If reality_check doesn't have real_value, try SLM responses
                if real_value is None and verdict.slm_responses:
                    for resp in verdict.slm_responses:
                        evidence = resp.get("evidence", {})
                        # Try to extract numeric value from SLM evidence
                        if "result" in evidence and isinstance(evidence["result"], (int, float)):
                            real_value = evidence["result"]
                            source = f"slm_{resp.get('slm_name', 'unknown')}"
                            break

                # Learn if we have real_value
                if real_value is not None and verdict.domain:
                    entity = self._extract_entity(question, verdict.domain)
                    if entity:
                        attribute = self._domain_to_attribute(verdict.domain, question)
                        if attribute:
                            try:
                                value_float = float(real_value)
                                self.knowledge_store.learn(
                                    entity=entity, attribute=attribute, value=value_float,
                                    value_type="float", source=source,
                                    confidence=0.9 if verdict.verdict == "PASS" else 0.5
                                )
                            except (ValueError, TypeError):
                                logger.exception("[engine.py:967] silenced exception")
            except Exception as e:
                logger.warning(f"KnowledgeStore learn failed: {e}")

        # Step 5: [OK] FIX: Route PARTIAL/UNKNOWN to HypothesisZone (DB2)
        # Use HypothesisStore API (new version)
        if self.hypothesis_zone is not None:
            try:
                real_value = verdict.reality_check.get("real_value")
                source = verdict.reality_check.get("source", "")
                entity = self._extract_entity(question, verdict.domain)
                attribute = self._domain_to_attribute(verdict.domain, question)

                if verdict.verdict in ("PARTIAL", "UNKNOWN") and entity and attribute:
                    # PARTIAL/UNKNOWN → add to hypothesis_zone as pending
                    if self.hypothesis_store is not None:
                        self.hypothesis_store.add_partial(
                            question=question,
                            answer=ai_answer,
                            entity=entity,
                            attribute=attribute,
                            value=str(real_value) if real_value is not None else "",
                            confidence=verdict.confidence,
                            source=source,
                        )
                elif verdict.verdict == "PASS" and real_value is not None and entity and attribute:
                    # PASS → check against pending PARTIALs (confirm/reject)
                    if self.hypothesis_store is not None:
                        self.hypothesis_store.resolve_partial(
                            entity=entity,
                            attribute=attribute,
                            pass_value=real_value,
                            pass_answer=ai_answer,
                            question=question,
                        )
                elif verdict.verdict == "FAIL" and real_value is not None and entity and attribute:
                    # FAIL with real_value → resolve partials with rejection
                    if self.hypothesis_store is not None:
                        self.hypothesis_store.resolve_partial(
                            entity=entity,
                            attribute=attribute,
                            pass_value=real_value,  # This is the correct answer
                            pass_answer="REJECTED",  # Mark as rejected  # noqa: S105,S106  # nosec B106 — rejection status string, not a password
                            question=question,
                        )
            except Exception as e:
                logger.warning(f"HypothesisZone routing failed: {e}")

        # Step 6: RealityAnchor verification (SHA-256 ground truth) — only when real_value exists
        # Skip if no anchor for this entity (fast path)
        if self.reality_anchor is not None and verdict.reality_check.get("real_value") is not None and verdict.verdict == "PASS":
            try:
                entity = self._extract_entity(question, verdict.domain)
                attribute = self._domain_to_attribute(verdict.domain, question)
                if entity and attribute:
                    anchor_check = self.reality_anchor.verify(entity, attribute, verdict.reality_check["real_value"])
                    if anchor_check.get("match"):
                        if hasattr(verdict, 'confidence'):
                            verdict.confidence = min(1.0, (verdict.confidence or 0.5) + 0.1)
            except Exception as e:
                logger.warning(f"RealityAnchor check failed: {e}")

        # Step 7: [P2] ExperienceEngine — learn lessons from EVERY verdict (every cycle)
        # [OPT] Changed from %5 to %1 for more frequent learning
        if self.experience is not None and self.cycle_count % 20 == 0:  # [V90 OPT] was every 1
            try:
                # Extract data from verdict
                real_value = verdict.reality_check.get("real_value")
                if real_value is None and verdict.slm_responses:
                    for resp in verdict.slm_responses:
                        evidence = resp.get("evidence", {})
                        if "result" in evidence and isinstance(evidence["result"], (int, float)):
                            real_value = evidence["result"]
                            break

                # [FIX] Include all required fields for ExperienceEngine.learn()
                # [V79 FIX] Skip — RealityJudge.judge() already calls experience.learn()
                # with source="reality_judge". This duplicate call created 2 records/question.
                # Now: only RealityJudge logs to experiences (1 record/question)
                pass
            except Exception as e:
                logger.warning(f"ExperienceEngine learn failed: {e}")

        # Step 8: Phase 0 — record audit trail (every 5 cycles to save time)
        if self.phase0 is not None and self.cycle_count % 50 == 0:  # [V90 OPT] was every 5
            try:
                # Record conclusion
                conclusion_id = self.phase0.add_conclusion(
                    question=question, ai_answer=ai_answer,
                    verdict=verdict.verdict, confidence=verdict.confidence,
                    domain=verdict.domain or "unknown",
                    reasoning=(verdict.reasoning or "")[:500],
                    cycle_count=self.cycle_count,
                )
                # Record SLM responses as evidences
                if verdict.slm_responses:
                    for resp in verdict.slm_responses:
                        if isinstance(resp, dict) and resp.get("answer"):
                            eid = self.phase0.add_evidence(
                                evidence_type="slm_response",
                                source=resp.get("slm_name", resp.get("domain", "unknown")),
                                entity=self._extract_entity(question, verdict.domain),
                                attribute=resp.get("domain", ""),
                                value=resp.get("answer", ""),
                                confidence=resp.get("confidence", 0.5),
                                raw_data=resp,
                            )
                            if conclusion_id and eid:
                                self.phase0.link_evidence(conclusion_id, eid, weight=resp.get("confidence", 0.5), role="slm")
                # Record reality_check as evidence
                if verdict.reality_check and verdict.reality_check.get("real_value") is not None:
                    eid = self.phase0.add_evidence(
                        evidence_type="reality_check",
                        source=verdict.reality_check.get("source", "v13"),
                        entity=self._extract_entity(question, verdict.domain),
                        attribute=self._domain_to_attribute(verdict.domain, question),
                        value=verdict.reality_check.get("real_value"),
                        confidence=0.9,
                        raw_data=verdict.reality_check,
                    )
                    if conclusion_id and eid:
                        self.phase0.link_evidence(conclusion_id, eid, weight=0.9, role="reality")
                # Record decision
                if conclusion_id:
                    action = f"verdict_{verdict.verdict.lower()}"
                    self.phase0.add_decision(conclusion_id, action, notes=f"cycle={self.cycle_count}")
            except Exception as e:
                logger.warning(f"Phase 0 record failed: {e}")

        # [PERF] Run Meta-Cognition cycle every 50 cycles (was 5 - too frequent with API calls)
        if self.meta is not None and self.cycle_count % 500 == 0:  # [V90 OPT] was every 50
            try:
                self.meta.run_meta_cycle()
            except Exception as e:
                logger.warning(f"MetaCognition cycle failed: {e}")

        # [PERF] Run KnowledgeConsolidator every 100 cycles — ASYNC (don't block pipeline)
        if self.consolidator is not None and self.cycle_count % 1000 == 0:  # [V90 OPT] was every 100
            import threading
            def _run_consolidator():
                try:
                    summary = self.consolidator.consolidate_all()
                    logger.info(f"KnowledgeConsolidator: {len(summary.get('summaries', []))} summaries")
                except Exception as e:
                    logger.warning(f"KnowledgeConsolidator failed: {e}")
            threading.Thread(target=_run_consolidator, daemon=True).start()

        # [PERF] Run PredictiveOrchestrator every 100 cycles — ASYNC (don't block pipeline)
        if self.predictive is not None and self.cycle_count % 1000 == 0:  # [V90 OPT] was every 100
            import threading
            def _run_predictive():
                try:
                    predictions = self.predictive.run_cycle()
                    logger.info(f"PredictiveOrchestrator: {len(predictions.get('predictions', []))} predictions")
                except Exception as e:
                    logger.warning(f"PredictiveOrchestrator failed: {e}")
            threading.Thread(target=_run_predictive, daemon=True).start()

        # [PERF] SQLite CACHE - Save result (0 RAM)
        if verdict.verdict not in ['UNKNOWN', 'ERROR']:
            self._set_sqlite_cache(cache_key, verdict)

        # [V39.1] Kham Pha Logger — ghi log nén GZIP theo lĩnh vực + sub-domain
        try:
            from scp.core.kham_pha_logger import get_kham_pha_logger
            get_kham_pha_logger().log({
                "timestamp": datetime.now().astimezone().isoformat(),
                "question": question,
                "ai_answer": ai_answer,
                "frame": verdict.domain,
                "final_verdict": verdict.verdict,
                "real_value": verdict.reality_check.get("real_value") if verdict.reality_check else None,
                "source": verdict.reality_check.get("source") if verdict.reality_check else None,
                "confidence": verdict.confidence,
                "cycle": self.cycle_count,
            })
        except Exception as e:
            # [ROOT-FIX 5] Was `except Exception as e: pass` — swallowed kham_pha_logger
            # errors silently → compressed audit log gaps invisible to operators.
            logger.warning(f"[engine] kham_pha_logger.log failed: {e}")

        return verdict
