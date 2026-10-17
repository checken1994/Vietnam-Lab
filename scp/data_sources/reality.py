"""
SCP - Viet Nam | Self-Correcting Pipeline
Copyright (c) 2026 SCP Vietnam Project. All Rights Reserved.

WHY Engine, Recursive Why, MetaFalsifier, ProofGraph
License: See LICENSE file
"""

"""
RealityDataSource - Data source cho "Thực tế" kiểm tra
Bao gồm: physical impossibilities, common facts, world records.
"""
import logging
import warnings
from typing import Any

from scp.interfaces.data_source import IDataSource

logger = logging.getLogger(__name__)


class RealityDataSource(IDataSource):
    """Data source cho reality checks (kiểm tra tính hợp lý)."""
    deprecated: bool = True

    def __init__(self):
        warnings.warn(
            f"{self.__class__.__name__} is deprecated per Requirement R4 (Milestone M3) "
            "and will be removed in a future release. Live queries are handled by LiveKnowledgeFetcher / InternetSearch.",
            DeprecationWarning,
            stacklevel=2,
        )
        self._cache: dict[str, Any] = {}

        # Physical limits (anything beyond this is suspicious)
        self._physical_limits = {}

        # Common world facts
        self._world_facts = {}

        # Impossible claims (always flagged as false)
        self._impossible_claims = {}

        # Common sense checks
        self._common_sense = {}

    @property
    def name(self) -> str:
        return "RealityDataSource"

    @property
    def priority(self) -> int:
        return 1  # High priority for reality checks

    @property
    def ttl(self) -> int:
        return 604800  # 1 week

    def get_supported_intents(self) -> list[str]:
        return [
            'reality_check',
            'physical_limit',
            'world_fact',
            'impossible_claim',
            'common_sense',
        ]

    def can_handle(self, intent: str, entity: str | None = None) -> bool:
        if intent in self.get_supported_intents():
            return True
        if entity:
            entity_lower = entity.lower().strip()
            for table in (self._physical_limits, self._world_facts,
                          self._impossible_claims, self._common_sense):
                if entity_lower in table:
                    return True
                for key in table:
                    if key in entity_lower or entity_lower in key:
                        return True
        return False

    def fetch(self, intent: str, entity: str, **kwargs) -> dict[str, Any] | None:
        if not entity:
            return None

        entity_lower = entity.lower().strip()

        # Physical limits
        if entity_lower in self._physical_limits:
            return {
                'value': self._physical_limits[entity_lower],
                'source': 'Local Reality Database',
                'metadata': {'category': 'physical_limit', 'name': entity_lower}
            }
        for key, val in self._physical_limits.items():
            if key in entity_lower or entity_lower in key:
                return {
                    'value': val,
                    'source': 'Local Reality Database',
                    'metadata': {'category': 'physical_limit', 'name': key}
                }

        # World facts
        if entity_lower in self._world_facts:
            return {
                'value': self._world_facts[entity_lower],
                'source': 'Local Reality Database',
                'metadata': {'category': 'world_fact', 'name': entity_lower}
            }
        for key, val in self._world_facts.items():
            if key in entity_lower or entity_lower in key:
                return {
                    'value': val,
                    'source': 'Local Reality Database',
                    'metadata': {'category': 'world_fact', 'name': key}
                }

        # Impossible claims
        if entity_lower in self._impossible_claims:
            return {
                'value': False,
                'source': 'Local Reality Database',
                'metadata': {
                    'category': 'impossible_claim',
                    'name': entity_lower,
                    'reason': self._impossible_claims[entity_lower]
                }
            }
        for key, reason in self._impossible_claims.items():
            if key in entity_lower or entity_lower in key:
                return {
                    'value': False,
                    'source': 'Local Reality Database',
                    'metadata': {
                        'category': 'impossible_claim',
                        'name': key,
                        'reason': reason
                    }
                }

        # Common sense
        if entity_lower in self._common_sense:
            return {
                'value': self._common_sense[entity_lower],
                'source': 'Local Reality Database',
                'metadata': {'category': 'common_sense', 'name': entity_lower}
            }
        for key, val in self._common_sense.items():
            if key in entity_lower or entity_lower in key:
                return {
                    'value': val,
                    'source': 'Local Reality Database',
                    'metadata': {'category': 'common_sense', 'name': key}
                }

        return None

    def health_check(self) -> bool:
        """[V104.32] Always-healthy THEO THIẾT KẾ: source thuần local
        (dataset cứng trong code, fetch() không chạm mạng) — không có
        network endpoint nào để ping, nên không có live evidence để thu.
        Contract: KHÔNG dùng nguồn này làm bằng chứng reachable cho bất
        kỳ network capability nào."""
        return True
