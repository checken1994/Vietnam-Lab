"""
[OPT-15] Literature — DomainExpert for literature (uses Gutenberg).

SCP's "SLM" means "Specialized Logic Module" (deterministic dispatcher).
This SLM:
  1. Checks local knowledge (classic authors, literary periods)
  2. Falls back to Gutenberg DataSource for book metadata
  3. Returns SLMResponse with answer + confidence + source
"""
from __future__ import annotations

import logging
import time
from typing import Any

from scp.runtime.slm_base import BaseSLM as Base
from scp.runtime.slm_base import SLMResponse

logger = logging.getLogger("scp.slms")


class Literature(Base):
    """Literature DomainExpert — books, authors, literary movements."""

    # Local knowledge: classic authors and their notable works

    def __init__(self, config: dict | None = None):
        super().__init__(name="Literature", domain="literature", config=config)
        self._gutenberg = None
        try:
            from scp.data_sources.gutenberg import GutenbergDataSource
            self._gutenberg = GutenbergDataSource()
        except Exception as e:
            logger.debug(f"Literature Gutenberg init: {e}", exc_info=True)

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
                rec = store.find_unique(domain="humanities.literature", entity_terms=[q])
                if rec is None:
                    candidates = store.find_candidates(domain="humanities.literature", entity_terms=[q])
                    if len(candidates) == 1:
                        rec = candidates[0]
                if rec is not None:
                    facts = rec.facts
                    if "works" in facts and "info" in facts:
                        answer = facts["info"] + " Notable works: " + ", ".join(facts["works"]) + "."
                    elif "description" in facts:
                        answer = str(facts["description"])
                    else:
                        answer = str(facts.get("info") or facts.get("summary") or "")
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
                logger.debug(f"Literature KnowledgeStore query: {e}", exc_info=True)
        # [DEHARDCODE LN-11] Không còn đáp án viết cứng khớp chuỗi con câu hỏi: chỉ
        # bản ghi KnowledgeStore (có version/review) hoặc nguồn dữ liệu thật bên dưới.

        # Path 3: Gutenberg fallback for specific book queries
        if not answer and self._gutenberg and self._gutenberg.enabled:
            try:
                result = self._gutenberg.query(q)
                if result and result.get("value"):
                    meta = result.get("metadata", {})
                    authors = ", ".join(meta.get("authors", []))
                    answer = (f"'{result['value']}' by {authors}. "
                              f"Downloads: {meta.get('download_count', '?')}. "
                              f"Languages: {', '.join(meta.get('languages', []))}. "
                              f"Available on Project Gutenberg (ID: {meta.get('gutenberg_id', '?')}).")
                    confidence = 0.75
                    reasoning = "GutenbergDataSource: book search"
                    evidence = result
            except Exception as e:
                logger.debug(f"Literature Gutenberg query: {e}", exc_info=True)

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
            reasoning = "Literature: no evidence — no verified KnowledgeStore record and no data-source hit"

        resp = SLMResponse(
            question=question, answer=answer, confidence=confidence,
            domain="literature", reasoning=reasoning,
            evidence=evidence, slm_name=self.name,
            processing_time=time.time() - start,
        )
        self.cache_response(question, resp)
        self._end_timer(start, confidence > 0.3)
        return resp

    def get_confidence(self, question: str, answer: str) -> float:
        if not answer:
            return 0.0
        return 0.75 if "Gutenberg" in answer else 0.85


# [OPT-7] Alias — "SLM" in SCP means "Specialized Logic Module" (DomainExpert).
LiteratureExpert = Literature
