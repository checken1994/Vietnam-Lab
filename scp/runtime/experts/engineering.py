"""
[OPT-22] Engineering — DomainExpert for engineering
(uses Aerospace + Architecture + Technology DataSources).

SCP's "SLM" means "Specialized Logic Module" (deterministic dispatcher).
This SLM:
  1. Checks local knowledge (engineering disciplines, principles)
  2. Falls back to multiple DataSources (aerospace, architecture, technology)
  3. Returns SLMResponse with answer + confidence + source
"""
from __future__ import annotations

import logging
import time
from typing import Any

from scp.data_sources.aerospace import AerospaceDataSource
from scp.data_sources.architecture import ArchitectureDataSource
from scp.data_sources.technology import TechnologyDataSource
from scp.runtime.slm_base import BaseSLM as Base
from scp.runtime.slm_base import SLMResponse

logger = logging.getLogger("scp.slms")


class Engineering(Base):
    """Engineering DomainExpert — aerospace, architecture, technology."""

    def __init__(self, config: dict | None = None):
        super().__init__(name="Engineering", domain="engineering", config=config)
        # Lazy-load multiple DataSources
        self._data_sources = []
        for ds_class in [
            AerospaceDataSource,
            ArchitectureDataSource,
            TechnologyDataSource,
        ]:
            try:
                self._data_sources.append(ds_class())
            except Exception as e:
                logger.debug(f"Engineering {ds_class.__name__} init: {e}", exc_info=True)

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

        # Path 2: Try each DataSource (aerospace, architecture, technology)
        if not answer and self._data_sources:
            try:
                entity = self._extract_entity(q)
                if entity:
                    for ds in self._data_sources:
                        ds_name = getattr(ds, "name", type(ds).__name__)
                        try:
                            for intent in ds.get_supported_intents():
                                if ds.can_handle(intent, entity):
                                    result = ds.fetch(intent, entity)
                                    if result and result.get("value"):
                                        answer = str(result["value"])[:500]
                                        confidence = 0.7
                                        reasoning = f"{ds_name}DataSource: {entity[:50]}"
                                        evidence = {"source": result.get("source", ""),
                                                    "entity": entity,
                                                    "datasource": ds_name,
                                                    "value": result.get("value", ""),
                                                    **result.get("metadata", {})}
                                        break
                            if answer:
                                break
                        except Exception as e:
                            logger.debug(f"Engineering {ds_name} fetch: {e}", exc_info=True)
            except Exception as e:
                logger.debug(f"Engineering entity extraction: {e}", exc_info=True)

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
            reasoning = "Engineering: no evidence — no verified KnowledgeStore record and no data-source hit"

        resp = SLMResponse(
            question=question, answer=answer, confidence=confidence,
            domain="engineering", reasoning=reasoning,
            evidence=evidence, slm_name=self.name,
            processing_time=time.time() - start,
        )
        self.cache_response(question, resp)
        self._end_timer(start, confidence > 0.3)
        return resp

    def _extract_entity(self, question: str) -> str:
        import re
        q = question.strip()
        q = re.sub(r'^(?:what\s+is|what\s+are|cho\s+biết|tìm\s+hiểu)\s+', '', q, flags=re.IGNORECASE)
        q = re.sub(r'^(.+?)\s+là\s+gì\??$', r'\1', q, flags=re.IGNORECASE)
        return q.rstrip('?').strip()

    def get_confidence(self, question: str, answer: str) -> float:
        if not answer:
            return 0.0
        return 0.8 if "local_knowledge" in answer else 0.7


# [OPT-7] Alias — "SLM" in SCP means "Specialized Logic Module" (DomainExpert).
EngineeringExpert = Engineering
