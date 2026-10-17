"""
SCP - Viet Nam | Self-Correcting Pipeline
 BlockchainDataSource - Data source cho Blockchain & Crypto
"""

import logging
import warnings
import re as _re
from typing import Any

from scp.interfaces.data_source import IDataSource

logger = logging.getLogger(__name__)
# [V104.32 #6] word-boundary matching for short keys


class BlockchainDataSource(IDataSource):
    """
    Data source cho các câu hỏi Blockchain.
    Hỗ trợ: blockchain, cryptocurrency, consensus.
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

        # Blockchain concepts
        self._concepts = {}

        # Major cryptocurrencies (emptied for Tabula Rasa)
        self._cryptos = {}


    @property
    def name(self) -> str:
        return "BlockchainDataSource"

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
            return {"value": result.get("answer", ""), "source": "Blockchain", "metadata": result}
        return None

    def health_check(self) -> bool:
        """[V104.32] Always-healthy THEO THIẾT KẾ: source thuần local
        (dataset cứng trong code, fetch() không chạm mạng) — không có
        network endpoint nào để ping, nên không có live evidence để thu.
        Contract: KHÔNG dùng nguồn này làm bằng chứng reachable cho bất
        kỳ network capability nào."""
        return True

    def query(self, question: str) -> dict[str, Any]:
        """Query blockchain data."""
        q = question.lower().strip()

        if q in self._cache:
            return self._cache[q]

        result = {"found": False, "answer": None, "confidence": 0.0}

        # Check concepts
        for concept, description in self._concepts.items():
            if concept in q:
                result = {
                    "found": True,
                    "answer": f"{concept}: {description}",
                    "confidence": 1.0,
                    "source": "Blockchain Database"
                }
                break

        # Check cryptos
        for ticker, info in self._cryptos.items():
            if _re.search(r'\b' + _re.escape(ticker.lower()) + r'\b', q) or _re.search(r'\b' + _re.escape(info['name'].lower()) + r'\b', q):
                result = {
                    "found": True,
                    "answer": f"{info['name']} ({ticker}): max supply {info['max_supply']}, consensus: {info['consensus']}",
                    "confidence": 1.0,
                    "source": "Crypto Database"
                }
                break

        self._cache[q] = result
        return result
