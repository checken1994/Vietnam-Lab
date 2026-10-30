"""
[OPT-14] Philosophy — DomainExpert for philosophy (local knowledge).

SCP's "SLM" means "Specialized Logic Module" (deterministic dispatcher).
This SLM uses local knowledge only — no external DataSource needed for
basic philosophical concepts, schools of thought, and logical fallacies.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from scp.runtime.slm_base import BaseSLM as Base
from scp.runtime.slm_base import SLMResponse

logger = logging.getLogger("scp.slms")


class Philosophy(Base):
    """Philosophy DomainExpert — Greek/Eastern/local philosophy knowledge."""

    # Local knowledge: key philosophical concepts and schools

    def __init__(self, config: dict | None = None):
        super().__init__(name="Philosophy", domain="philosophy", config=config)

    def predict(self, question: str) -> SLMResponse:
        start = self._start_timer()
        cached = self.get_cached(question)
        if cached:
            self._end_timer(start, True)
            return cached

        q = question.strip()
        q_lower = q.lower()
        answer = ""
        confidence = 0.0
        reasoning = ""
        evidence: dict[str, Any] = {}

        from scp.knowledge.store import KnowledgeStore
        if KnowledgeStore.is_enabled():
            try:
                store = KnowledgeStore()
                rec = store.find_unique(domain="humanities.philosophy", entity_terms=[q])
                if rec is None:
                    candidates = store.find_candidates(domain="humanities.philosophy", entity_terms=[q])
                    if len(candidates) == 1:
                        rec = candidates[0]
                if rec is not None:
                    facts = rec.facts
                    answer = str(facts.get("info") or facts.get("description") or facts.get("summary") or "")
                    confidence = 0.85
                    reasoning = f"KnowledgeStore: {rec.record_id}"
                    evidence = {
                        "value": answer,
                        "source": "knowledge_store",
                        "record_id": rec.record_id,
                        "dataset_version": store.dataset_version,
                        "status": rec.review.get("status"),
                    }
            except Exception as e:
                logger.debug(f"Philosophy KnowledgeStore query: {e}", exc_info=True)
        # [DEHARDCODE LN-11] Không còn đáp án viết cứng khớp chuỗi con câu hỏi: chỉ
        # bản ghi KnowledgeStore (có version/review) hoặc nguồn dữ liệu thật bên dưới.

        if not answer:
            confidence = 0.0
            reasoning = "Philosophy: no evidence — no verified KnowledgeStore record and no data-source hit"

        resp = SLMResponse(
            question=question, answer=answer, confidence=confidence,
            domain="philosophy", reasoning=reasoning,
            evidence=evidence, slm_name=self.name,
            processing_time=time.time() - start,
        )
        self.cache_response(question, resp)
        self._end_timer(start, confidence > 0.3)
        return resp

    def get_confidence(self, question: str, answer: str) -> float:
        return 0.85 if answer else 0.0


# [OPT-7] Alias — "SLM" in SCP means "Specialized Logic Module" (DomainExpert).
PhilosophyExpert = Philosophy
