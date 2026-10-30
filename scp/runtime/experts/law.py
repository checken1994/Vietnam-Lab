"""
[OPT-17] Law — DomainExpert for law (uses existing LegalDataSource).

SCP's "SLM" means "Specialized Logic Module" (deterministic dispatcher).
This SLM:
  1. Checks local knowledge (Vietnamese + international law basics)
  2. Falls back to LegalDataSource.fetch() for specific entities
  3. Returns SLMResponse with answer + confidence + source

NOTE: Distinct from existing Legal (_Domain) — this version has explicit
local knowledge and is meant as the "DomainExpert" pattern reference.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from scp.runtime.slm_base import BaseSLM as Base
from scp.runtime.slm_base import SLMResponse

logger = logging.getLogger("scp.slms")


class Law(Base):
    """Law DomainExpert — Vietnamese + international law."""

    def __init__(self, config: dict | None = None):
        super().__init__(name="Law", domain="law", config=config)
        self._ds = None
        try:
            from scp.data_sources.legal import LegalDataSource
            self._ds = LegalDataSource()
        except Exception as e:
            logger.debug(f"Law LegalDataSource init: {e}", exc_info=True)

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
        # [DEHARDCODE LN-11] Không còn đáp án viết cứng khớp chuỗi con câu hỏi: chỉ
        # bản ghi KnowledgeStore (có version/review) hoặc nguồn dữ liệu thật bên dưới.
        if KnowledgeStore.is_enabled():
            try:
                store = KnowledgeStore()
                rec = store.find_unique(domain=self.domain, entity_terms=[q])
                if rec and rec.review.get("status") == "verified":
                    answer = str(rec.facts.get("text") or rec.facts.get("info") or "")
                    confidence = 0.75
                    reasoning = f"KnowledgeStore: {rec.record_id}"
                    evidence = {
                        "value": answer,
                        "source": "knowledge_store",
                        "record_id": rec.record_id,
                        "dataset_version": store.dataset_version,
                        "status": "verified",
                    }
            except Exception as e:
                logger.debug(f"{self.name} KnowledgeStore query: {e}", exc_info=True)

        # Path 2: LegalDataSource fallback for specific entities
        if not answer and self._ds:
            try:
                entity = self._extract_entity(q)
                if entity:
                    for intent in self._ds.get_supported_intents():
                        if self._ds.can_handle(intent, entity):
                            result = self._ds.fetch(intent, entity)
                            if result and result.get("value"):
                                answer = str(result["value"])[:500]
                                confidence = 0.7
                                reasoning = f"LegalDataSource: {entity[:50]}"
                                evidence = {"source": result.get("source", ""),
                                            "entity": entity,
                                            "value": result.get("value", ""),
                                            **result.get("metadata", {})}
                                break
                    if answer:
                        pass
            except Exception as e:
                logger.debug(f"Law DataSource query: {e}", exc_info=True)

        if not answer:

            try:

                from scp.data_sources.live_knowledge import fetch_live

                live_res = fetch_live(q, domain=self.domain)

                if live_res and live_res.get('value'):

                    answer = str(live_res['value'])[:500]

                    confidence = float(live_res.get('confidence', 0.75))

                    reasoning = f"LiveKnowledgeFetcher: {live_res.get('source', '')}"

                    evidence = {

                        'source': live_res.get('source', 'LiveKnowledgeFetcher'),

                        'value': live_res.get('value', ''),

                        **live_res.get('metadata', {}),

                    }

            except Exception as e:

                logger.debug(f"{self.name} live query error: {e}", exc_info=True)

        

        if not answer:
            confidence = 0.0
            reasoning = "Law: no evidence — no verified KnowledgeStore record and no data-source hit"

        resp = SLMResponse(
            question=question, answer=answer, confidence=confidence,
            domain="law", reasoning=reasoning,
            evidence=evidence, slm_name=self.name,
            processing_time=time.time() - start,
        )
        self.cache_response(question, resp)
        self._end_timer(start, confidence > 0.3)
        return resp

    def _extract_entity(self, question: str) -> str:
        """Extract entity from question (basic heuristic)."""
        import re
        q = question.strip()
        q = re.sub(r'^(?:what\s+is|what\s+are|cho\s+biết|tìm\s+hiểu)\s+', '', q, flags=re.IGNORECASE)
        q = re.sub(r'^(.+?)\s+là\s+gì\??$', r'\1', q, flags=re.IGNORECASE)
        return q.rstrip('?').strip()

    def get_confidence(self, question: str, answer: str) -> float:
        if not answer:
            return 0.0
        return 0.75 if "local_knowledge" in answer else 0.7


# [OPT-7] Alias — "SLM" in SCP means "Specialized Logic Module" (DomainExpert).
LawExpert = Law
