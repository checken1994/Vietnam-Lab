"""
SCP - Viet Nam | Self-Correcting Pipeline
 PhysicsDataSource - Data source cho Vật lý
"""

import logging
import warnings
import re as _re
from typing import Any

from scp.interfaces.data_source import IDataSource

logger = logging.getLogger(__name__)
# [V104.32 #2] word-boundary matching for short keys


class PhysicsDataSource(IDataSource):
    """
    Data source cho các câu hỏi Vật lý.
    Hỗ trợ: hằng số vật lý, công thức, đơn vị.
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

        # Physical constants (CODATA 2018)
        self._constants: dict[str, float] = {}
        self._prefixes: dict[str, float] = {}

    @property
    def name(self) -> str:
        return "PhysicsDataSource"

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
            return {"value": result.get("answer", ""), "source": "Physics", "metadata": result}
        return None

    def health_check(self) -> bool:
        """[V104.32] Always-healthy THEO THIẾT KẾ: source thuần local
        (dataset cứng trong code, fetch() không chạm mạng) — không có
        network endpoint nào để ping, nên không có live evidence để thu.
        Contract: KHÔNG dùng nguồn này làm bằng chứng reachable cho bất
        kỳ network capability nào."""
        return True

    def query(self, question: str) -> dict[str, Any]:
        """Query physics data."""
        q = question.lower().strip()

        # Check cache
        if q in self._cache:
            return self._cache[q]

        result = {"found": False, "answer": None, "confidence": 0.0}

        # Check constants
        for name, value in self._constants.items():
            if (len(name) >= 3 and _re.search(r'\b' + _re.escape(name) + r'\b', q)) or name == q:
                result = {
                    "found": True,
                    "answer": f"{name} = {value}",
                    "confidence": 1.0,
                    "unit": self._get_unit(name),
                    "source": "CODATA 2018"
                }
                break

        # Check SI prefixes
        for prefix, factor in self._prefixes.items():
            if prefix in q:
                result = {
                    "found": True,
                    "answer": f"1 {prefix} = {factor}",
                    "confidence": 1.0,
                    "source": "SI Standard"
                }
                break

        self._cache[q] = result
        return result

    def _get_unit(self, constant_name: str) -> str:
        """Get unit for constant."""
        units = {
            "tốc độ ánh sáng": "m/s", "speed of light": "m/s", "c": "m/s",
            "g": "m/s²", "hằng số hấp dẫn": "m³/(kg·s²)", "gravitational constant": "m³/(kg·s²)",
            "khối lượng electron": "kg", "electron mass": "kg",
            "khối lượng proton": "kg", "proton mass": "kg",
            "hằng số Planck": "J·s", "Planck constant": "J·s",
            "hằng số Boltzmann": "J/K",
            "nhiệt độ Planck": "K", "Planck temperature": "K",
        }
        return units.get(constant_name, "")

    def get_constant(self, name: str) -> float | None:
        """Get constant by name."""
        return self._constants.get(name.lower())
