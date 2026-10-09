# SCP CIRCUIT: M02 — Pipeline Pattern Refactor
from __future__ import annotations

import asyncio
import logging
import os
import secrets
import uuid
from pathlib import Path
from typing import Any

from scp.api_server_parts.helpers import AskResponse
from scp.api_server_parts.pipeline.base import PipelineStage
from scp.api_server_parts.pipeline.context import AskPipelineContext
from scp.core.request_run_ledger import stage_request

logger = logging.getLogger(__name__)


class LedgerStage(PipelineStage):
    """Stage 5: Subsystems hooks (Risk, History, World state, Calibration, Forecast),

    multi-turn chat memory persistence, backward trace recording, and AskResponse assembly.
    """

    name: str = "ledger"

    async def execute(self, ctx: AskPipelineContext) -> None:
        if ctx.judge_verdict is None:
            return
        v = ctx.judge_verdict
        _data_dir = Path(os.environ.get("SCP_DATA_DIR", "data"))

        # 1. Subsystem Hook: Risk Intelligence
        try:
            from scp.risk_intelligence import RiskClassifier, RiskSignal

            _classifier = RiskClassifier()
            _signals = [RiskSignal(source_id="judge", kind="independent", observed_directly=True)]
            _risk = _classifier.classify(_signals, desired_level="PR2", hazard_severity="low")
            if _risk and _risk.level:
                v.evidence["risk_level"] = _risk.level.value
        except Exception as _hook_exc:
            logger.warning(f"[RESTORED-SYSTEMS] risk hook failed: {_hook_exc}", exc_info=True)

        # 2. Subsystem Hook: History Evidence Ledger
        try:
            from scp.history.evidence_ledger import append_record

            hist_fn = ctx.resolve("_history_evidence_record", None)
            _record = (
                hist_fn(
                    verdict=str(v.verdict or ""),
                    session_id=str(ctx.req.session_id or ""),
                    question=str(ctx.req.question or ""),
                )
                if hist_fn
                else None
            )
            if _record is not None:
                await asyncio.to_thread(append_record, _data_dir / "history_evidence.jsonl", _record)
        except Exception as _hook_exc:
            logger.warning(f"[RESTORED-SYSTEMS] history hook failed: {_hook_exc}", exc_info=True)

        # 3. Subsystem Hook: World State (if PASS, record an event)
        try:
            if v.verdict == "PASS":
                _ws_run = getattr(getattr(ctx.request, "state", None), "scp_run", None)
                _ask_run_id = str(getattr(_ws_run, "run_id", "") or "")
                if _ask_run_id:
                    from scp.contracts.time import now_utc_iso
                    from scp.world_state import EntityEventAuthority, TemporalAuthority

                    _ws_confidence = v.confidence
                    _ws_question_head = ctx.req.question[:100]

                    def _record_world_state_event() -> None:
                        _temporal = TemporalAuthority(db_path=str(_data_dir / "world_state.sqlite"))
                        try:
                            _eea = EntityEventAuthority(_temporal)
                            _eea.record_event(
                                entity_id="ask_session",
                                event_kind="pass_verdict",
                                payload={"confidence": _ws_confidence, "question": _ws_question_head},
                                valid_time=now_utc_iso(),
                                evidence_refs=[_ask_run_id],
                                actor_id="scp-judge",
                            )
                        finally:
                            _temporal.close()

                    await asyncio.to_thread(_record_world_state_event)
                else:
                    logger.warning(
                        "[RESTORED-SYSTEMS] world_state hook: judge PASS without request run_id - "
                        "world write skipped (unaudited world writes are forbidden)"
                    )
        except Exception as _hook_exc:
            logger.warning(f"[RESTORED-SYSTEMS] world_state hook failed: {_hook_exc}", exc_info=True)

        # 4. Subsystem Hook: Calibration (record prediction for UNKNOWN/PARTIAL/FLAGGED/ABSTAIN)
        try:
            if v.verdict in ("UNKNOWN", "PARTIAL", "FLAGGED", "ABSTAIN"):
                from scp.calibration.ledger import CalibrationLedger

                _cal_domain = v.domain or "general"
                _cal_verdict = v.verdict
                _cal_confidence = v.confidence

                def _record_calibration_prediction() -> None:
                    _cal = CalibrationLedger(db_path=str(_data_dir / "calibration.sqlite"))
                    try:
                        _cal.record_prediction(
                            domain=_cal_domain,
                            task_class="ask",
                            predictor_type="judge",
                            predictor_id="judge_v3",
                            prediction={"verdict": _cal_verdict, "confidence": _cal_confidence},
                        )
                    finally:
                        _cal.close()

                await asyncio.to_thread(_record_calibration_prediction)
        except Exception as _hook_exc:
            logger.warning(f"[RESTORED-SYSTEMS] calibration hook failed: {_hook_exc}", exc_info=True)

        # 5. Subsystem Hook: Forecast (if future intent detected)
        try:
            if any(w in ctx.req.question.lower() for w in ["sẽ", "dự đoán", "tương lai", "will", "predict"]):
                from scp.contracts.time import now_utc_iso as _now_utc_iso
                from scp.forecast.ledger import ForecastLedger

                _fc = ForecastLedger(db_path=str(_data_dir / "forecast.sqlite"))
                try:
                    _fc.record_case(
                        {
                            "id": str(uuid.uuid4())[:8],
                            "domain": v.domain or "general",
                            "claimant": "user",
                            "claim": ctx.req.question[:200],
                            "date": _now_utc_iso(),
                            "verdict": v.verdict,
                            "confidence": v.confidence,
                            "outcome_code": 9,
                        }
                    )
                finally:
                    _fc.close()
        except Exception as _hook_exc:
            logger.warning(f"[RESTORED-SYSTEMS] forecast hook failed: {_hook_exc}", exc_info=True)

        # 6. Multi-turn Chat Memory Persistence
        if ctx.req.session_id:
            try:
                from scp.core.chat_memory import get_chat_memory_store

                _mem_store = get_chat_memory_store()
                _mem_session = ctx.req.session_id
                _mem_user_content = ctx.req.question
                _mem_assistant_content = str(ctx.api_final_answer or "")
                _mem_metadata = {
                    "verdict": v.verdict,
                    "confidence": v.confidence,
                    "domain": v.domain or "general",
                    "governance": ctx.gov_decision,
                }
                await asyncio.to_thread(
                    _mem_store.append, session_id=_mem_session, role="user", content=_mem_user_content
                )
                await asyncio.to_thread(
                    _mem_store.append,
                    session_id=_mem_session,
                    role="assistant",
                    content=_mem_assistant_content,
                    metadata=_mem_metadata,
                )
            except Exception as _mem_save_exc:
                logger.warning("[_ask_impl] Failed to persist chat memory turn: %s", _mem_save_exc, exc_info=True)

        # 7. Backward Traceability Recording
        _ws_run = getattr(getattr(ctx.request, "state", None), "scp_run", None)
        _trace_id = getattr(_ws_run, "trace_id", None) or getattr(getattr(ctx.request, "state", None), "trace_id", None)
        if not _trace_id:
            _trace_id = f"trace_{secrets.token_hex(8)}"
        _ask_run_id = str(getattr(_ws_run, "run_id", "") or "")

        stage_req = ctx.resolve("stage_request", stage_request)
        stage_req(ctx.request, "response_boundary", verdict=v.verdict, governance_decision=ctx.gov_decision)

        if ctx.web_fallback_used:
            ctx.slm_trace.append(
                {
                    "domain": v.domain or "" or "",
                    "slm_name": "public_web_search",
                    "answer": "retrieved public snippets",
                    "time_ms": None,
                    "source": "public-search",
                    "evidence": ctx.web_fallback,
                }
            )

        ctx.trace_id = _trace_id
        ctx.run_id = _ask_run_id

        # 8. Assemble AskResponse
        ctx.final_response = AskResponse(
            trace_id=_trace_id,
            run_id=_ask_run_id or None,
            verdict=v.verdict,
            final_answer=ctx.api_final_answer,
            confidence=v.confidence,
            domain=v.domain or (ctx.route_decision.domain if ctx.route_decision else "") or "",
            falsification_status=ctx.api_falsification_status,
            governance_decision=ctx.gov_decision or v.evidence.get("governance_decision"),
            lane=ctx.route_decision.lane if ctx.route_decision else "LANE_CHATBOT",
            routing=ctx.route_decision.to_dict() if ctx.route_decision else {},
            v98_guard=ctx.api_v98_guard,
            v98_classification=ctx.api_v98_classification,
            v98_attack_policy=ctx.api_v98_attack_policy,
            v98_counter_executed=ctx.api_v98_counter_executed,
            v98_canary_token=ctx.api_v98_canary_token,
            v98_bypass_recorded=ctx.api_v98_bypass_recorded,
            elapsed_ms=round(ctx.elapsed_ms, 1),
            session_id=ctx.v98_context.get("session_id", ""),
            slm_trace=ctx.slm_trace,
            phase_timings=ctx.phase_timings,
            reasoning=ctx.api_reasoning,
            slm_responses=ctx.slm_responses,
            v100_claims=ctx.api_v100_claims,
            v103_antibodies=ctx.api_v103_antibodies,
            speculative_mode=ctx.api_speculative_mode,
            web_fallback_used=ctx.web_fallback_used,
            web_fallback=ctx.web_fallback or None,
            detector_degraded=ctx.detector_degraded,
            detector_note=("; ".join(ctx.detector_notes) if ctx.detector_notes else None),
            fact_check_degraded=ctx.fact_check_degraded or None,
            fact_check_note=(ctx.fact_check_note or None),
            verified_facts=ctx.verified_facts,
            llm_reasoning=ctx.llm_reasoning,
            confidence_badge=ctx.confidence_badge,
        )
