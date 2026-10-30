"""
[OPT-18] Education — DomainExpert for education (uses ERIC DataSource).

SCP's "SLM" means "Specialized Logic Module" (deterministic dispatcher).
This SLM:
  1. Checks local knowledge (pedagogy, learning theory basics)
  2. Falls back to ERIC DataSource for research papers
  3. Returns SLMResponse with answer + confidence + source

NOTE: Distinct from existing Education (_Domain) — this version uses
the new ERIC API + has explicit local knowledge for education concepts.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from scp.runtime.slm_base import BaseSLM as Base
from scp.runtime.slm_base import SLMResponse

logger = logging.getLogger("scp.slms")


class Education(Base):
    """Education DomainExpert — pedagogy + learning theory + ERIC."""

    def __init__(self, config: dict | None = None):
        super().__init__(name="Education", domain="education", config=config)
        self._eric = None
        try:
            from scp.data_sources.eric import ERICDataSource
            self._eric = ERICDataSource()
        except Exception as e:
            logger.debug(f"Education ERIC init: {e}", exc_info=True)

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

        # Path 2: ERIC fallback for research-paper-style queries
        if not answer and self._eric and self._eric.enabled:
            try:
                result = self._eric.query(q)
                if result and result.get("value"):
                    meta = result.get("metadata", {})
                    top_results = meta.get("top_results", [])
                    if top_results:
                        first = top_results[0]
                        answer = (f"Top research paper found: '{first.get('title', '')}' "
                                  f"by {first.get('author', 'unknown')} "
                                  f"({first.get('publication_year', '?')}). "
                                  f"ERIC ID: {first.get('eric_id', '?')}. "
                                  f"Total results: {meta.get('total_results', 0)}.")
                        confidence = 0.6  # research paper, not verified claim
                        reasoning = f"ERIC: searched '{meta.get('search_term', '?')[:50]}'"
                        evidence = result
            except Exception as e:
                logger.debug(f"Education ERIC query: {e}", exc_info=True)

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
            reasoning = "Education: no evidence — no verified KnowledgeStore record and no data-source hit"

        resp = SLMResponse(
            question=question, answer=answer, confidence=confidence,
            domain="education", reasoning=reasoning,
            evidence=evidence, slm_name=self.name,
            processing_time=time.time() - start,
        )
        self.cache_response(question, resp)
        self._end_timer(start, confidence > 0.3)
        return resp

    def get_confidence(self, question: str, answer: str) -> float:
        if not answer:
            return 0.0
        return 0.75 if "local_knowledge" in answer else 0.6


# [OPT-7] Alias — "SLM" in SCP means "Specialized Logic Module" (DomainExpert).
EducationExpert = Education
