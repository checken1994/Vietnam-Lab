"""
SCP - Viet Nam | Self-Correcting Pipeline
Copyright (c) 2026 SCP Vietnam Project. All Rights Reserved.

WHY Engine, Recursive Why, MetaFalsifier, ProofGraph
License: See LICENSE file
"""

"""
 SportsDataSource — Thể thao & Giải trí
Bao gồm: Olympic, FIFA World Cup, athletes, sports rules.
Fallback: LiveKnowledgeFetcher (Wikipedia).
"""
import logging
import warnings
from typing import Any

from scp.interfaces.data_source import IDataSource

logger = logging.getLogger(__name__)


class SportsDataSource(IDataSource):
    """Data source cho Thể thao & Giải trí."""
    deprecated: bool = True

    def __init__(self):
        warnings.warn(
            f"{self.__class__.__name__} is deprecated per Requirement R4 (Milestone M3) "
            "and will be removed in a future release. Live queries are handled by LiveKnowledgeFetcher / InternetSearch.",
            DeprecationWarning,
            stacklevel=2,
        )
        self._cache: dict[str, Any] = {}

        # Olympic Games
        self._olympics = {}

        # FIFA World Cup
        self._world_cup = {}

        # Famous athletes
        self._athletes = {}

        # Sports rules - basic info
        self._sports_rules = {}

    @property
    def name(self) -> str:
        return "SportsDataSource"

    @property
    def priority(self) -> int:
        return 2

    @property
    def ttl(self) -> int:
        return 604800  # 1 week

    def get_supported_intents(self) -> list[str]:
        return ['sports_olympic', 'sports_world_cup', 'sports_athlete',
                'sports_rules', 'sports_info']

    def can_handle(self, intent: str, entity: str | None = None) -> bool:
        if intent in self.get_supported_intents():
            return True
        if entity:
            entity_lower = entity.lower().strip()
            for table in (self._olympics, self._world_cup, self._athletes, self._sports_rules):
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

        # Olympics
        if entity_lower in self._olympics:
            data = self._olympics[entity_lower]
            return {
                'value': str(data.get('year', '')),
                'source': 'Local Sports Database',
                'metadata': data
            }
        for key, data in self._olympics.items():
            if key in entity_lower or entity_lower in key:
                return {
                    'value': str(data.get('year', '')),
                    'source': 'Local Sports Database',
                    'metadata': data
                }

        # World Cup
        if entity_lower in self._world_cup:
            data = self._world_cup[entity_lower]
            return {
                'value': data.get('winner', ''),
                'source': 'Local Sports Database',
                'metadata': data
            }
        for key, data in self._world_cup.items():
            if key in entity_lower or entity_lower in key:
                return {
                    'value': data.get('winner', ''),
                    'source': 'Local Sports Database',
                    'metadata': data
                }

        # Athletes
        if entity_lower in self._athletes:
            data = self._athletes[entity_lower]
            return {
                'value': data.get('sport', ''),
                'source': 'Local Sports Database',
                'metadata': data
            }
        for key, data in self._athletes.items():
            if key in entity_lower or entity_lower in key:
                return {
                    'value': data.get('sport', ''),
                    'source': 'Local Sports Database',
                    'metadata': data
                }

        # Sports rules
        if entity_lower in self._sports_rules:
            data = self._sports_rules[entity_lower]
            return {
                'value': str(data.get('players', '')),
                'source': 'Local Sports Database',
                'metadata': data
            }
        for key, data in self._sports_rules.items():
            if key in entity_lower or entity_lower in key:
                return {
                    'value': str(data.get('players', '')),
                    'source': 'Local Sports Database',
                    'metadata': data
                }

        return None

    def health_check(self) -> bool:
        """[V104.32] Always-healthy THEO THIẾT KẾ: source thuần local
        (dataset cứng trong code, fetch() không chạm mạng) — không có
        network endpoint nào để ping, nên không có live evidence để thu.
        Contract: KHÔNG dùng nguồn này làm bằng chứng reachable cho bất
        kỳ network capability nào."""
        return True
