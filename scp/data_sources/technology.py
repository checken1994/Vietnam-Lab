"""
SCP - Viet Nam | Self-Correcting Pipeline
Copyright (c) 2026 SCP Vietnam Project. All Rights Reserved.

WHY Engine, Recursive Why, MetaFalsifier, ProofGraph
License: See LICENSE file
"""

"""
 TechnologyDataSource — Công nghệ & Kỹ thuật
Bao gồm: programming languages, AI/ML concepts, hardware, internet protocols.
Fallback: LiveKnowledgeFetcher (arXiv + Wikipedia).
"""
import logging
import warnings
from typing import Any

from scp.interfaces.data_source import IDataSource

logger = logging.getLogger(__name__)


class TechnologyDataSource(IDataSource):
    """Data source cho Công nghệ & Kỹ thuật."""
    deprecated: bool = True

    def __init__(self):
        warnings.warn(
            f"{self.__class__.__name__} is deprecated per Requirement R4 (Milestone M3) "
            "and will be removed in a future release. Live queries are handled by LiveKnowledgeFetcher / InternetSearch.",
            DeprecationWarning,
            stacklevel=2,
        )
        self._cache: dict[str, Any] = {}

        # Programming languages
        self._languages = {}

        # AI/ML concepts
        self._ai_concepts = {}

        # Hardware specs
        self._hardware = {}

        # Internet protocols
        self._protocols = {}

        # Tech companies (founding year)
        self._companies = {}

    @property
    def name(self) -> str:
        return "TechnologyDataSource"

    @property
    def priority(self) -> int:
        return 2

    @property
    def ttl(self) -> int:
        return 2592000  # 30 days

    def get_supported_intents(self) -> list[str]:
        return ['tech_language', 'tech_ai', 'tech_hardware',
                'tech_protocol', 'tech_company', 'tech_info']

    def can_handle(self, intent: str, entity: str | None = None) -> bool:
        if intent in self.get_supported_intents():
            return True
        if entity:
            entity_lower = entity.lower().strip()
            for table in (self._languages, self._ai_concepts, self._hardware,
                          self._protocols, self._companies):
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

        # [V63 FIX] First pass: EXACT match only (prevents "go" matching "google")
        for table_name, table in [
            ('language', self._languages),
            ('ai_concept', self._ai_concepts),
            ('hardware', self._hardware),
            ('protocol', self._protocols),
            ('company', self._companies),
        ]:
            if entity_lower in table:
                data = table[entity_lower]
                # [V63.1] Build value from ALL available fields, not just desc
                parts = []
                for key in ['desc', 'vi', 'year', 'founded', 'creator', 'founders',
                            'paradigm', 'typing', 'use_cases', 'port', 'layer',
                            'specialty', 'period', 'origin']:
                    if key in data:
                        val = data[key]
                        if isinstance(val, list):
                            val = ', '.join(str(v) for v in val)
                        parts.append(f"{key}={val}")
                value = '; '.join(parts) if parts else ''
                return {
                    'value': value,
                    'source': 'Local Technology Database',
                    'metadata': {**data, 'category': table_name}
                }

        #  Second pass: partial match — but ONLY if entity is longer than key
        # This prevents "go" (2 chars) matching "google" (6 chars)
        for table_name, table in [
            ('language', self._languages),
            ('ai_concept', self._ai_concepts),
            ('hardware', self._hardware),
            ('protocol', self._protocols),
            ('company', self._companies),
        ]:
            for key, data in table.items():
                #  Only match if key is substantial (>= 4 chars) and entity contains key
                # OR entity is contained in key (entity="python" in key="python language")
                if len(key) >= 4 and key in entity_lower:
                    return {
                        'value': str(data.get('desc', data.get('vi', data.get('year', '')))),
                        'source': 'Local Technology Database',
                        'metadata': {**data, 'category': table_name, 'matched_key': key}
                    }
                if entity_lower in key and len(entity_lower) >= 4:
                    return {
                        'value': str(data.get('desc', data.get('vi', data.get('year', '')))),
                        'source': 'Local Technology Database',
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
