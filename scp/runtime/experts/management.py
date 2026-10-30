"""
[OPT-16] Management — DomainExpert for management theory (local knowledge).

SCP's "SLM" means "Specialized Logic Module" (deterministic dispatcher).
Covers: strategy, HR, marketing theory, operations, organizational behavior.
No external DataSource needed — management theory is stable knowledge.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from scp.runtime.slm_base import BaseSLM as Base
from scp.runtime.slm_base import SLMResponse

logger = logging.getLogger("scp.slms")


class Management(Base):
    """Management DomainExpert — strategy/HR/marketing/operations theory."""

    def __init__(self, config: dict | None = None):
        super().__init__(name="Management", domain="management", config=config)

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
                refs = store.list_reference("management.frameworks")
                for ref in refs:
                    aliases = [ref.key]
                    if "aliases" in ref.data:
                        aliases.extend(list(ref.data["aliases"]))
                    elif "name" in ref.data:
                        aliases.append(str(ref.data["name"]))
                    if any(alias.lower() in q_lower for alias in aliases):
                        answer = str(ref.data.get("summary") or ref.description or "")
                        confidence = 0.8
                        reasoning = f"KnowledgeStore: {ref.record_id}"
                        evidence = {
                            "value": answer,
                            "source": "knowledge_store",
                            "record_id": ref.record_id,
                            "dataset_version": store.dataset_version,
                            "framework": ref.key,
                        }
                        break
            except Exception as e:
                logger.debug(f"Management KnowledgeStore query: {e}", exc_info=True)
        # [DEHARDCODE LN-11] Không còn đáp án viết cứng khớp chuỗi con câu hỏi: chỉ
        # bản ghi KnowledgeStore (có version/review) hoặc nguồn dữ liệu thật bên dưới.

        if not answer:
            confidence = 0.0
            reasoning = "Management: no evidence — no verified KnowledgeStore record and no data-source hit"

        resp = SLMResponse(
            question=question, answer=answer, confidence=confidence,
            domain="management", reasoning=reasoning,
            evidence=evidence, slm_name=self.name,
            processing_time=time.time() - start,
        )
        self.cache_response(question, resp)
        self._end_timer(start, confidence > 0.3)
        return resp

    def get_confidence(self, question: str, answer: str) -> float:
        return 0.8 if answer else 0.0


# [OPT-7] Alias — "SLM" in SCP means "Specialized Logic Module" (DomainExpert).
ManagementExpert = Management
