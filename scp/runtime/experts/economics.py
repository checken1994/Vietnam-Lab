"""
[OPT-13] Economics — DomainExpert for economics (uses FRED + WorldBank).

SCP's "SLM" means "Specialized Logic Module" (deterministic dispatcher),
NOT "Small Language Model". This SLM:
  1. Checks local knowledge (GDP/inflation/interest rate basics)
  2. Falls back to FRED (US indicators) or WorldBank (global) DataSource
  3. Returns SLMResponse with answer + confidence + source

Pattern: Base subclass with local knowledge dict + DataSource fallback.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from scp.runtime.slm_base import BaseSLM as Base
from scp.runtime.slm_base import SLMResponse

logger = logging.getLogger("scp.slms")


class Economics(Base):
    """Economics DomainExpert — routes to FRED (US) + WorldBank (global)."""

    # Local knowledge: basic economic facts (fast path, no API call)

    def __init__(self, config: dict | None = None):
        super().__init__(name="Economics", domain="economics", config=config)
        # Lazy-load DataSources (fail gracefully if API key missing)
        self._fred = None
        self._worldbank = None
        try:
            from scp.data_sources.fred import FREDDataSource
            self._fred = FREDDataSource()
        except Exception as e:
            logger.debug(f"Economics FRED init: {e}", exc_info=True)
        try:
            from scp.data_sources.worldbank import WorldBankDataSource
            self._worldbank = WorldBankDataSource()
        except Exception as e:
            logger.debug(f"Economics WorldBank init: {e}", exc_info=True)

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
                    confidence = 0.8
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

        # Fallback path: try FRED then WorldBank
        if not answer:
            # Try FRED (US-specific indicators)
            if self._fred and self._fred.enabled:
                try:
                    result = self._fred.query(q)
                    if result and result.get("value"):
                        answer = (f"{result['value']} (source: FRED, "
                                  f"series: {result.get('metadata', {}).get('label', '?')}, "
                                  f"date: {result.get('metadata', {}).get('date', '?')})")
                        confidence = 0.8
                        reasoning = f"FRED: {result.get('metadata', {}).get('series_id', '?')}"
                        evidence = result
                except Exception as e:
                    logger.debug(f"Economics FRED query: {e}", exc_info=True)

            # Try WorldBank (global indicators)
            if not answer and self._worldbank and self._worldbank.enabled:
                try:
                    result = self._worldbank.query(q)
                    if result and result.get("value"):
                        meta = result.get("metadata", {})
                        answer = (f"{result['value']} (source: WorldBank, "
                                  f"indicator: {meta.get('indicator', '?')}, "
                                  f"country: {meta.get('country', '?')}, "
                                  f"year: {meta.get('date', '?')})")
                        confidence = 0.8
                        reasoning = f"WorldBank: {meta.get('indicator_code', '?')}"
                        evidence = result
                except Exception as e:
                    logger.debug(f"Economics WorldBank query: {e}", exc_info=True)

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
            reasoning = "Economics: no evidence — no verified KnowledgeStore record and no data-source hit"

        resp = SLMResponse(
            question=question, answer=answer, confidence=confidence,
            domain="economics", reasoning=reasoning,
            evidence=evidence, slm_name=self.name,
            processing_time=time.time() - start,
        )
        self.cache_response(question, resp)
        self._end_timer(start, confidence > 0.3)
        return resp

    def get_confidence(self, question: str, answer: str) -> float:
        # Local knowledge: 0.7; DataSource hit: 0.8; none: 0.0
        if not answer:
            return 0.0
        return 0.8 if "FRED" in answer or "WorldBank" in answer else 0.7


# [OPT-7] Alias — "SLM" in SCP means "Specialized Logic Module" (DomainExpert).
EconomicsExpert = Economics
