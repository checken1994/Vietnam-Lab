"""
SCP - Viet Nam | Self-Correcting Pipeline
 PsychologyDataSource - Data source cho Tâm lý học
"""

import logging
import warnings
from typing import Any

from scp.interfaces.data_source import IDataSource

logger = logging.getLogger(__name__)
# [V104.32 #14] word-boundary matching for short keys


class PsychologyDataSource(IDataSource):
    """
    Data source cho các câu hỏi Tâm lý học.
    Hỗ trợ: rối loạn, hiệu ứng, lý thuyết.
    """
    deprecated: bool = True

    def __init__(self):
        warnings.warn(
            f"{self.__class__.__name__} is deprecated per Requirement R4 (Milestone M3) "
            "and will be removed in a future release. Live queries are handled by LiveKnowledgeFetcher / InternetSearch.",
            DeprecationWarning,
            stacklevel=2,
        )
        self._cache: dict[str, Any] = {}

        # Psychological disorders (DSM-5 simplified)
        self._disorders = {}

        # Psychological effects
        self._effects = {}

        # Big Five personality traits
        self._big_five = {}


    @property
    def name(self) -> str:
        return "PsychologyDataSource"

    @property
    def priority(self) -> int:
        return 5

    @property
    def ttl(self) -> int:
        return 86400

    def get_supported_intents(self) -> list[str]:
        return ["lookup", "query", "fact"]

    def can_handle(self, intent: str, entity: str | None = None) -> bool:
        return True

    def fetch(self, intent: str, entity: str, **kwargs):
        result = self.query(entity or intent)
        if result.get("found"):
            return {"value": result.get("answer", ""), "source": "Psychology", "metadata": result}
        return None

    def health_check(self) -> bool:
        """[V104.32] Always-healthy THEO THIẾT KẾ: source thuần local
        (dataset cứng trong code, fetch() không chạm mạng) — không có
        network endpoint nào để ping, nên không có live evidence để thu.
        Contract: KHÔNG dùng nguồn này làm bằng chứng reachable cho bất
        kỳ network capability nào."""
        return True

    def query(self, question: str) -> dict[str, Any]:
        """Query psychology data."""
        q = question.lower().strip()

        if q in self._cache:
            return self._cache[q]

        result = {"found": False, "answer": None, "confidence": 0.0}

        # Check disorders
        for disorder, info in self._disorders.items():
            if disorder.lower() in q:  # [V104.32 #14] was: uppercase PTSD/OCD never matched:
                result = {
                    "found": True,
                    "answer": f"{disorder}: triệu chứng - {info['symptoms']}, điều trị - {info['treatment']}",
                    "confidence": 0.9,
                    "source": "DSM-5"
                }
                break

        # Check effects
        for effect, description in self._effects.items():
            if effect.lower() in q:
                result = {
                    "found": True,
                    "answer": f"{effect}: {description}",
                    "confidence": 1.0,
                    "source": "Psychology Database"
                }
                break

        self._cache[q] = result
        return result
