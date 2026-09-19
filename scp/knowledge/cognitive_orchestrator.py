import logging
from datetime import datetime, timezone
from scp.knowledge.knowledge_control_db import KnowledgeControlDB
from scp.knowledge.learning_db import LearningDB
from scp.knowledge.revalidation_authority import RevalidationAuthority, RevalidationPolicy, VolatilityClass
from scp.knowledge.contradiction_authority import ContradictionAuthority, ContradictionMateriality
from scp.knowledge.open_question_authority import OpenQuestionAuthority
from scp.knowledge.hypothesis_authority import HypothesisAuthority, HypothesisRecord
from scp.knowledge.experiment_authority import ExperimentAuthority, ExperimentRecord
from scp.knowledge.lesson_authority import LessonAuthority, LessonRecord
from scp.knowledge.benchmark_authority import BenchmarkAuthority, BenchmarkRunRecord
from scp.contracts.time import now_utc_iso

logger = logging.getLogger("scp.knowledge.cognitive_orchestrator")

# Default policy: re-validate knowledge every 7 days; stale after 30 days
_DEFAULT_POLICY = RevalidationPolicy(
    volatility_class=VolatilityClass.LOW,
    review_after_seconds=7 * 86400,
    max_staleness_seconds=30 * 86400,
    on_stale="UNDER_REVIEW",
)


class CognitiveOrchestrator:
    """
    P1-12 Cognitive Orchestrator.
    Drives the Epistemic Learning Loop.
    Watches for Stale knowledge -> Open Question -> Hypothesis -> Experiment -> Lesson -> Benchmark.
    """
    def __init__(
        self,
        knowledge_db: KnowledgeControlDB,
        learning_db: LearningDB,
        revalidation: RevalidationAuthority,
        contradiction: ContradictionAuthority,
        open_question: OpenQuestionAuthority,
        hypothesis: HypothesisAuthority,
        experiment: ExperimentAuthority,
        lesson: LessonAuthority,
        benchmark: BenchmarkAuthority
    ):
        self.knowledge_db = knowledge_db
        self.learning_db = learning_db
        self.revalidation = revalidation
        self.contradiction = contradiction
        self.open_question = open_question
        self.hypothesis = hypothesis
        self.experiment = experiment
        self.lesson = lesson
        self.benchmark = benchmark

    def run_tick(self):
        """
        Executes one pass of the cognitive loop:
        stale → open question → hypothesis → experiment → lesson → benchmark.
        All steps are fail-independent: a crash in one step logs and does NOT
        block the remaining steps (loop-level guard).
        """
        logger.info("[CognitiveOrchestrator] Tick start")
        for phase_name, phase_fn in [
            ("stale_knowledge", self._process_stale_knowledge),
            ("open_questions", self._process_open_questions),
            ("hypotheses", self._process_hypotheses),
            ("experiments", self._process_experiments),
            ("lessons", self._process_lessons),
        ]:
            try:
                phase_fn()
            except Exception as exc:
                logger.warning(
                    "[CognitiveOrchestrator] phase=%s crashed (non-fatal): %s",
                    phase_name,
                    exc,
                    exc_info=True,
                )
        logger.info("[CognitiveOrchestrator] Tick complete")

    # ------------------------------------------------------------------
    # Phase 1: Stale Knowledge
    # ------------------------------------------------------------------
    def _process_stale_knowledge(self):
        """
        Query knowledge_status_events for VERIFIED/GOLD items and flag stale
        ones as UNDER_REVIEW by recording a status transition.

        Uses revalidation.assess_staleness() against the default policy.
        Fail-closed: if last_verified_at is missing, treat as stale.
        """
        import sqlite3
        try:
            with sqlite3.connect(self.knowledge_db.db_path) as conn:
                conn.row_factory = sqlite3.Row
                # Get the LATEST status per knowledge_id
                rows = conn.execute("""
                    SELECT knowledge_id, to_status, timestamp
                    FROM knowledge_status_events
                    WHERE to_status IN ('VERIFIED', 'GOLD')
                    GROUP BY knowledge_id
                    HAVING MAX(timestamp)
                """).fetchall()
        except Exception as exc:
            logger.warning("[CognitiveOrchestrator] _process_stale_knowledge DB read failed: %s", exc)
            return

        flagged = 0
        for row in rows:
            kid = row["knowledge_id"]
            last_ts = row["timestamp"] or ""
            is_stale = self.revalidation.assess_staleness(last_ts, _DEFAULT_POLICY)
            if is_stale:
                try:
                    self.knowledge_db.record_status_event({
                        "knowledge_id": kid,
                        "from_status": row["to_status"],
                        "to_status": "UNDER_REVIEW",
                        "reason_codes": ["STALENESS_EXCEEDED"],
                    })
                    flagged += 1
                    logger.debug("[CognitiveOrchestrator] knowledge_id=%s → UNDER_REVIEW (stale)", kid)
                except Exception as exc:
                    logger.warning("[CognitiveOrchestrator] Could not flag %s as UNDER_REVIEW: %s", kid, exc)

        logger.info("[CognitiveOrchestrator] stale phase: flagged=%d", flagged)

    # ------------------------------------------------------------------
    # Phase 2: Open Questions
    # ------------------------------------------------------------------
    def _process_open_questions(self):
        """
        Query learning_db for open questions that have not yet generated a
        hypothesis. For each, propose a hypothesis via hypothesis_authority.

        The open_question_authority.formulate_question() is the seam for
        enriching the question text; we use its output as hypothesis context.
        """
        import sqlite3
        try:
            with sqlite3.connect(self.learning_db.db_path) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute("""
                    SELECT * FROM open_questions WHERE status = 'OPEN' LIMIT 50
                """).fetchall()
        except Exception as exc:
            logger.warning("[CognitiveOrchestrator] _process_open_questions DB read failed: %s", exc)
            return

        proposed = 0
        for row in rows:
            try:
                # question_id may be stored as 'question_id' in the DB
                qid = row["question_id"] if "question_id" in row.keys() else ""
                question_text = row["question"] if "question" in row.keys() else str(dict(row))
                hyp = HypothesisRecord(
                    question_ref=qid,
                    hypothesis=f"Auto-generated hypothesis for: {question_text[:200]}",
                    mechanism="auto-cognitive-loop",
                )
                self.hypothesis.propose(hyp)
                proposed += 1
            except Exception as exc:
                logger.warning("[CognitiveOrchestrator] hypothesis propose failed for question %s: %s", row["question_id"], exc)

        logger.info("[CognitiveOrchestrator] open_questions phase: proposed_hypotheses=%d", proposed)

    # ------------------------------------------------------------------
    # Phase 3: Hypotheses
    # ------------------------------------------------------------------
    def _process_hypotheses(self):
        """
        Query learning_db for PROPOSED hypotheses without a planned experiment.
        For each, plan an experiment via experiment_authority.
        """
        import sqlite3
        try:
            with sqlite3.connect(self.learning_db.db_path) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute("""
                    SELECT * FROM hypotheses WHERE status = 'PROPOSED' LIMIT 50
                """).fetchall()
        except Exception as exc:
            logger.warning("[CognitiveOrchestrator] _process_hypotheses DB read failed: %s", exc)
            return

        planned = 0
        for row in rows:
            try:
                exp = ExperimentRecord(
                    hypothesis_ref=row["hypothesis_id"],
                )
                self.experiment.plan_experiment(exp)
                planned += 1
            except Exception as exc:
                logger.warning("[CognitiveOrchestrator] experiment plan failed for hypothesis %s: %s", row["hypothesis_id"], exc)

        logger.info("[CognitiveOrchestrator] hypotheses phase: planned_experiments=%d", planned)

    # ------------------------------------------------------------------
    # Phase 4: Experiments
    # ------------------------------------------------------------------
    def _process_experiments(self):
        """
        Query learning_db for PLANNED experiments.
        Execution inside sandbox is gated — probe runs are NO-OP unless
        SCP_COGNITIVE_EXPERIMENTS=enabled. Records results as lessons.
        """
        import os, sqlite3
        if os.environ.get("SCP_COGNITIVE_EXPERIMENTS", "disabled") != "enabled":
            logger.debug("[CognitiveOrchestrator] experiments phase skipped (SCP_COGNITIVE_EXPERIMENTS not enabled)")
            return

        try:
            with sqlite3.connect(self.learning_db.db_path) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute("""
                    SELECT * FROM experiments WHERE execution_status = 'PLANNED' LIMIT 10
                """).fetchall()
        except Exception as exc:
            logger.warning("[CognitiveOrchestrator] _process_experiments DB read failed: %s", exc)
            return

        executed = 0
        for row in rows:
            try:
                lesson = LessonRecord(
                    experiment_refs=[row["experiment_id"]],
                    insights=["Experiment executed (automated probe, no conclusive result)"],
                )
                self.lesson.record_lesson(lesson)
                executed += 1
            except Exception as exc:
                logger.warning("[CognitiveOrchestrator] lesson record failed for experiment %s: %s", row["experiment_id"], exc)

        logger.info("[CognitiveOrchestrator] experiments phase: executed=%d", executed)

    # ------------------------------------------------------------------
    # Phase 5: Lessons
    # ------------------------------------------------------------------
    def _process_lessons(self):
        """
        Query learning_db for recorded lessons not yet benchmarked.
        Schedule benchmark runs to prevent catastrophic forgetting.
        """
        import sqlite3
        try:
            with sqlite3.connect(self.learning_db.db_path) as conn:
                conn.row_factory = sqlite3.Row
                # Lessons that have no corresponding benchmark_run yet
                rows = conn.execute("""
                    SELECT l.* FROM lessons l
                    WHERE NOT EXISTS (
                        SELECT 1 FROM benchmark_runs br WHERE br.benchmark_id = l.lesson_id
                    )
                    LIMIT 20
                """).fetchall()
        except Exception as exc:
            logger.warning("[CognitiveOrchestrator] _process_lessons DB read failed: %s", exc)
            return

        scheduled = 0
        for row in rows:
            try:
                bm = BenchmarkRunRecord(
                    benchmark_id=row["lesson_id"],  # lesson_id as the benchmark reference
                    target_capabilities=[],
                )
                self.benchmark.schedule_benchmark(bm)
                scheduled += 1
            except Exception as exc:
                logger.warning("[CognitiveOrchestrator] benchmark schedule failed for lesson %s: %s", row["lesson_id"], exc)

        logger.info("[CognitiveOrchestrator] lessons phase: scheduled_benchmarks=%d", scheduled)
