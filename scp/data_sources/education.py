"""
SCP - Viet Nam | Self-Correcting Pipeline
 EducationDataSource - Data source cho Giáo dục
"""

import logging
import warnings
import re as _re
from typing import Any

from scp.interfaces.data_source import IDataSource

logger = logging.getLogger(__name__)
# [V104.32 #9] word-boundary matching for short keys


class EducationDataSource(IDataSource):
    """
    Data source cho các câu hỏi Giáo dục.
    Hỗ trợ: học thuật, trường học, bằng cấp, phương pháp học.
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

        # Education levels
        self._levels = {}

        # Top universities (QS 2024)
        self._universities = {}

        # Learning methods
        self._methods = {}


    @property
    def name(self) -> str:
        return "EducationDataSource"

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
            return {"value": result.get("answer", ""), "source": "Education", "metadata": result}
        return None

    def health_check(self) -> bool:
        """[V104.32] Always-healthy THEO THIẾT KẾ: source thuần local
        (dataset cứng trong code, fetch() không chạm mạng) — không có
        network endpoint nào để ping, nên không có live evidence để thu.
        Contract: KHÔNG dùng nguồn này làm bằng chứng reachable cho bất
        kỳ network capability nào."""
        return True

    def query(self, question: str) -> dict[str, Any]:
        """Query education data."""
        q = question.lower().strip()

        if q in self._cache:
            return self._cache[q]

        result = {"found": False, "answer": None, "confidence": 0.0}

        # Check education levels
        for level, info in self._levels.items():
            if level in q:
                result = {
                    "found": True,
                    "answer": f"{level}: tuổi {info['age']}, {info['years']} năm",
                    "confidence": 1.0,
                    "source": "Education System Database"
                }
                break

        # Check universities
        for uni, info in self._universities.items():
            if _re.search(r'\b' + _re.escape(uni.lower()) + r'\b', q):
                result = {
                    "found": True,
                    "answer": f"{uni}: #{info['rank']} ({info['country']}), mạnh về {info['field']}",
                    "confidence": 1.0,
                    "source": "QS Rankings 2024"
                }
                break

        # Check methods
        for method, description in self._methods.items():
            if method in q:
                result = {
                    "found": True,
                    "answer": f"{method}: {description}",
                    "confidence": 1.0,
                    "source": "Learning Science"
                }
                break

        self._cache[q] = result
        return result
