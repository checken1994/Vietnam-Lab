"""
SCP - Viet Nam | Self-Correcting Pipeline
 LogicDataSource - Data source cho Logic và Toán rời rạc
"""

import logging
import warnings
import re as _re
from typing import Any

from scp.interfaces.data_source import IDataSource

logger = logging.getLogger(__name__)
# [V104.32 #8] word-boundary matching for short keys


class LogicDataSource(IDataSource):
    """
    Data source cho các câu hỏi Logic.
    Hỗ trợ: modus ponens, syllogisms, logical fallacies, boolean algebra.
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

        # Logical fallacies
        self._fallacies = {}

        # Syllogism patterns
        self._syllogisms = {}

        # Boolean operations
        self._boolean = {}


    @property
    def name(self) -> str:
        return "LogicDataSource"

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
            return {"value": result.get("answer", ""), "source": "Logic", "metadata": result}
        return None

    def health_check(self) -> bool:
        """[V104.32] Always-healthy THEO THIẾT KẾ: source thuần local
        (dataset cứng trong code, fetch() không chạm mạng) — không có
        network endpoint nào để ping, nên không có live evidence để thu.
        Contract: KHÔNG dùng nguồn này làm bằng chứng reachable cho bất
        kỳ network capability nào."""
        return True

    def query(self, question: str) -> dict[str, Any]:
        """Query logic data."""
        q = question.lower().strip()

        if q in self._cache:
            return self._cache[q]

        result = {"found": False, "answer": None, "confidence": 0.0}

        # Check fallacies
        for fallacy, description in self._fallacies.items():
            # [ROOT-FIX 4] Was `fallacy in q or description[:20] in q` — substring
            # match. fallacies are multi-word (low risk) but description[:20] can
            # match arbitrary prefixes. Use word-boundary for safety.
            if _re.search(r'(?<![\wÀ-ỹ])' + _re.escape(fallacy) + r'(?![\wÀ-ỹ])', q) or \
               _re.search(r'(?<![\wÀ-ỹ])' + _re.escape(description[:20]) + r'(?![\wÀ-ỹ])', q):
                result = {
                    "found": True,
                    "answer": f"{fallacy}: {description}",
                    "confidence": 1.0,
                    "type": "logical_fallacy",
                    "source": "Logic Database"
                }
                break

        # Check syllogisms
        for name, data in self._syllogisms.items():
            # [ROOT-FIX 4] Was `name.replace("_", " ") in q or "syllogism" in q or
            # "modus" in q` — substring match. 'modus' (5 chars) could match
            # 'modustule' (rare). Use word-boundary.
            name_spaced = name.replace("_", " ")
            if _re.search(r'(?<![\wÀ-ỹ])' + _re.escape(name_spaced) + r'(?![\wÀ-ỹ])', q) or \
               _re.search(r'\bsyllogism\b', q) or \
               _re.search(r'\bmodus\b', q):
                result = {
                    "found": True,
                    "answer": f"{name}: {data['pattern']}\nVí dụ: {data['example']}",
                    "confidence": 1.0,
                    "type": "syllogism",
                    "source": "Logic Database"
                }
                break

        # Check boolean
        for op, meaning in self._boolean.items():
            if _re.search(r'\b' + _re.escape(op.lower()) + r'\b', q):
                result = {
                    "found": True,
                    "answer": f"{op}: {meaning}",
                    "confidence": 1.0,
                    "type": "boolean_operation",
                    "source": "Boolean Algebra"
                }
                break

        self._cache[q] = result
        return result
