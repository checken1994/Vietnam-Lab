"""
SCP - Viet Nam | Self-Correcting Pipeline
Copyright (c) 2026 SCP Vietnam Project. All Rights Reserved.

WHY Engine, Recursive Why, MetaFalsifier, ProofGraph
License: See LICENSE file
"""

"""
 ArtsDataSource — Nghệ thuật & Thiết kế
Bao gồm: painters, art movements, music composers, films, design principles.
Fallback: LiveKnowledgeFetcher (Wikipedia).
"""
import logging
import warnings
from typing import Any

from scp.data_sources._matching import _token_boundary_match
from scp.interfaces.data_source import IDataSource

logger = logging.getLogger(__name__)
# [V104.32 #10] word-boundary matching for short keys


class ArtsDataSource(IDataSource):
    """Data source cho Nghệ thuật & Thiết kế."""
    deprecated: bool = True

    def __init__(self):
        warnings.warn(
            f"{self.__class__.__name__} is deprecated per Requirement R4 (Milestone M3) "
            "and will be removed in a future release. Live queries are handled by LiveKnowledgeFetcher / InternetSearch.",
            DeprecationWarning,
            stacklevel=2,
        )
        self._cache: dict[str, Any] = {}

        # Famous painters
        self._painters = {}

        # Art movements
        self._movements = {}

        # Music composers
        self._composers = {}

        # Films & cinema
        self._films = {}

        # Design principles
        self._design = {}

    @property
    def name(self) -> str:
        return "ArtsDataSource"

    @property
    def priority(self) -> int:
        return 2

    @property
    def ttl(self) -> int:
        return 604800

    def get_supported_intents(self) -> list[str]:
        return ['arts_painter', 'arts_movement', 'arts_composer',
                'arts_film', 'arts_design', 'arts_info']

    def can_handle(self, intent: str, entity: str | None = None) -> bool:
        if intent in self.get_supported_intents():
            return True
        if entity:
            entity_lower = entity.lower().strip()
            for table in (self._painters, self._movements, self._composers,
                          self._films, self._design):
                if entity_lower in table:
                    return True
                for key in table:
                    if _token_boundary_match(key, entity_lower):
                        return True
        return False

    def fetch(self, intent: str, entity: str, **kwargs) -> dict[str, Any] | None:
        if not entity:
            return None
        entity_lower = entity.lower().strip()

        for table_name, table in [
            ('painter', self._painters),
            ('movement', self._movements),
            ('composer', self._composers),
            ('film', self._films),
            ('design', self._design),
        ]:
            if entity_lower in table:
                data = table[entity_lower]
                return {
                    'value': str(data.get('desc', data.get('vi', data.get('year', '')))),
                    'source': 'Local Arts Database',
                    'metadata': {**data, 'category': table_name}
                }
            for key, data in table.items():
                if _token_boundary_match(key, entity_lower):
                    return {
                        'value': str(data.get('desc', data.get('vi', data.get('year', '')))),
                        'source': 'Local Arts Database',
                        'metadata': {**data, 'category': table_name, 'matched_key': key}
                    }

        return None

    def health_check(self) -> bool:
        """[V104.32] Always-healthy THEO THIẾT KẾ: source thuần local
        (dataset cứng trong code, fetch() không chạm mạng) — không có
        network endpoint nào để ping, nên không có live evidence để thu.
        Contract: KHÔNG dùng nguồn này làm bằng chứng reachable cho bất
        kỳ network capability nào."""
        return True
