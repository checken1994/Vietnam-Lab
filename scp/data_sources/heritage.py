"""
SCP - Viet Nam | Self-Correcting Pipeline
 HeritageDataSource - Data source cho Di sản văn hóa
"""

import logging
import warnings
from typing import Any

from scp.interfaces.data_source import IDataSource

logger = logging.getLogger(__name__)


class HeritageDataSource(IDataSource):
    """Data source cho Di sản văn hóa."""
    deprecated: bool = True

    def __init__(self):
        warnings.warn(
            f"{self.__class__.__name__} is deprecated per Requirement R4 (Milestone M3) "
            "and will be removed in a future release. Live queries are handled by LiveKnowledgeFetcher / InternetSearch.",
            DeprecationWarning,
            stacklevel=2,
        )
        self._cache: dict[str, Any] = {}

        # UNESCO World Heritage Sites
        self._sites = {}

        # Vietnamese intangible heritage
        self._intangible = {}

    def query(self, question: str) -> dict[str, Any]:
        q = question.lower().strip()
        if q in self._cache:
            return self._cache[q]

        result = {"found": False, "answer": None, "confidence": 0.0}

        for site, info in self._sites.items():
            if site in q:
                result = {
                    "found": True,
                    "answer": f"{site}: {info['location']}, UNESCO {info['year']}, {info['type']} - {info['description']}",
                    "confidence": 1.0,
                    "source": "UNESCO"
                }
                break

        if not result["found"]:
            for heritage, desc in self._intangible.items():
                if heritage in q:
                    result = {
                        "found": True,
                        "answer": f"{heritage}: {desc}",
                        "confidence": 0.9,
                        "source": "Vietnamese Heritage"
                    }
                    break

        self._cache[q] = result
        return result

    @property
    def name(self) -> str:
        return "HeritageDataSource"

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
            return {"value": result.get("answer", ""), "source": "Heritage", "metadata": result}
        return None

    def health_check(self) -> bool:
        """[V104.32] Always-healthy THEO THIẾT KẾ: source thuần local
        (dataset cứng trong code, fetch() không chạm mạng) — không có
        network endpoint nào để ping, nên không có live evidence để thu.
        Contract: KHÔNG dùng nguồn này làm bằng chứng reachable cho bất
        kỳ network capability nào."""
        return True
