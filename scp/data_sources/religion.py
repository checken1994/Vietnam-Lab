"""
SCP - Viet Nam | Self-Correcting Pipeline
 ReligionDataSource - Data source cho Tôn giáo
"""

import logging
import warnings
from typing import Any

from scp.interfaces.data_source import IDataSource

logger = logging.getLogger(__name__)


class ReligionDataSource(IDataSource):
    """Data source cho Tôn giáo."""
    deprecated: bool = True

    def __init__(self):
        warnings.warn(
            f"{self.__class__.__name__} is deprecated per Requirement R4 (Milestone M3) "
            "and will be removed in a future release. Live queries are handled by LiveKnowledgeFetcher / InternetSearch.",
            DeprecationWarning,
            stacklevel=2,
        )
        self._cache: dict[str, Any] = {}
        self._religions = {}


    @property
    def name(self) -> str:
        return "ReligionDataSource"

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
            return {"value": result.get("answer", ""), "source": "Religion", "metadata": result}
        return None

    def health_check(self) -> bool:
        """[V104.32] Always-healthy THEO THIẾT KẾ: source thuần local
        (dataset cứng trong code, fetch() không chạm mạng) — không có
        network endpoint nào để ping, nên không có live evidence để thu.
        Contract: KHÔNG dùng nguồn này làm bằng chứng reachable cho bất
        kỳ network capability nào."""
        return True

    def query(self, question: str) -> dict[str, Any]:
        q = question.lower().strip()
        if q in self._cache:
            return self._cache[q]
        result = {"found": False, "answer": None, "confidence": 0.0}
        for religion, info in self._religions.items():
            if religion in q:
                result = {"found": True, "answer": f"{religion}: {info['followers']} followers, từ {info['origin']}", "confidence": 0.9, "source": "Religion Database"}
                break
        self._cache[q] = result
        return result
