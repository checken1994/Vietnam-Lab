"""
SCP - Viet Nam | Self-Correcting Pipeline
Copyright (c) 2026 SCP Vietnam Project. All Rights Reserved.

WHY Engine, Recursive Why, MetaFalsifier, ProofGraph
License: See LICENSE file
"""

"""
 LegalDataSource — Luật & Hành chính
Bao gồm: Vietnamese law basics, international law, rights, contracts.
Fallback: LiveKnowledgeFetcher (Wikipedia).
"""
import logging
import warnings
import os
from typing import Any

from scp.core.api_utils import fetch_with_retry  # [V5.8-API]
from scp.data_sources._matching import _token_boundary_match
from scp.data_sources.config_loader import get_api_key
from scp.interfaces.data_source import IDataSource

logger = logging.getLogger(__name__)
# [V104.32 #12] word-boundary matching for short keys


class LegalDataSource(IDataSource):
    """Data source cho Luật & Hành chính."""
    deprecated: bool = True

    def __init__(self):
        warnings.warn(
            f"{self.__class__.__name__} is deprecated per Requirement R4 (Milestone M3) "
            "and will be removed in a future release. Live queries are handled by LiveKnowledgeFetcher / InternetSearch.",
            DeprecationWarning,
            stacklevel=2,
        )
        self._cache: dict[str, Any] = {}
        # [V5.8-API] Case.law API key via ConfigLoader (optional — anonymous access allowed,
        # but rate-limited; registration at https://case.law/api/)
        self._case_law_api_key = (
            get_api_key("legal", required=False)
            or get_api_key("caselaw", required=False)
            or ""
        ).strip()

        # Vietnamese legal documents
        self._vn_laws = {}

        # International law concepts
        self._intl_law = {}

        # Rights & freedoms
        self._rights = {}

        # Contract law basics
        self._contract = {}

        # Government structure VN
        self._vn_govt = {}

    @property
    def name(self) -> str:
        return "LegalDataSource"

    @property
    def priority(self) -> int:
        return 2

    @property
    def ttl(self) -> int:
        return 604800

    def get_supported_intents(self) -> list[str]:
        return ['legal_vn_law', 'legal_intl', 'legal_rights',
                'legal_contract', 'legal_govt', 'legal_info']

    # [V89 FIX] English→Vietnamese alias map for cross-language matching
    _EN_ALIASES = {
        'constitution': 'hiến pháp',
        'civil code': 'bộ luật dân sự',
        'criminal code': 'bộ luật hình sự',
        'labor law': 'bộ luật lao động',
        'land law': 'luật đất đai',
        'enterprise law': 'luật doanh nghiệp',
    }

    def _resolve_alias(self, entity: str) -> str:
        """Convert English legal terms to Vietnamese DB keys."""
        # [FIX #5] TẠI SAO: was `import re as _re` (creating name `_re`) but next
        # line called `re.search(...)` -> NameError on every legal question with a
        # year (e.g. "civil code 2020"). Swallowed by outer except -> silent fail.
        # Reality > Model: verified `import re` at top of file (line 14) works.
        import re
        e = entity.lower().strip()
        for en, vi in self._EN_ALIASES.items():
            if en in e:
                # Try with year if present
                year_m = re.search(r'(20\d{2}|19\d{2})', e)
                if year_m:
                    key = f"{vi} {year_m.group(1)}"
                    if key in self._vn_laws:
                        return key
                # Try without year
                for k in self._vn_laws:
                    if vi in k:
                        return k
        return entity

    def can_handle(self, intent: str, entity: str | None = None) -> bool:
        if intent in self.get_supported_intents():
            return True
        if entity:
            entity_lower = entity.lower().strip()
            for table in (self._vn_laws, self._intl_law, self._rights,
                          self._contract, self._vn_govt):
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
            ('vn_law', self._vn_laws),
            ('intl_law', self._intl_law),
            ('rights', self._rights),
            ('contract', self._contract),
            ('govt', self._vn_govt),
        ]:
            if entity_lower in table:
                data = table[entity_lower]
                return {
                    'value': str(data.get('desc', data.get('vi', data.get('year', '')))),
                    'source': 'Local Legal Database',
                    'metadata': {**data, 'category': table_name}
                }
            for key, data in table.items():
                if _token_boundary_match(key, entity_lower):
                    return {
                        'value': str(data.get('desc', data.get('vi', data.get('year', '')))),
                        'source': 'Local Legal Database',
                        'metadata': {**data, 'category': table_name, 'matched_key': key}
                    }

        # [V5.8-API] Local DB miss → try Case.law API for US case law.
        # (Vietnamese law API is not publicly available for free; the local
        #  DB remains the canonical source for VN law. Case.law covers
        #  international/US case-law lookups.)
        case_law_result = self._fetch_from_case_law(entity)
        if case_law_result:
            return case_law_result

        return None

    # [V5.8-API] Case.law (Caselaw Access Project) integration
    def _fetch_from_case_law(self, entity: str) -> dict[str, Any] | None:
        """
        [V5.8-API] Query the Caselaw Access Project API for case-law matches.
        Endpoint: https://api.case.law/v1/cases/?search={query}
        Returns dict or None.
        """
        if not entity or not entity.strip():
            return None
        from urllib.parse import quote
        term = entity.strip()
        # Skip very short queries / pure Vietnamese year-only queries
        if len(term) < 4:
            return None
        url = (
            f"https://api.case.law/v1/cases/?search={quote(term)}"
            f"&page_size=3"
        )
        headers = {"User-Agent": "SCP/1.0"}
        if self._case_law_api_key:
            headers["Authorization"] = f"Token {self._case_law_api_key}"
        try:
            data = fetch_with_retry(url, headers=headers, timeout=8)
            if not data:
                return None
            results = data.get('results', [])
            if not results:
                return None
            case_entries = []
            for c in results[:3]:
                name = c.get('name_abbreviation') or c.get('name') or ''
                citation = (c.get('citations') or [{}])[0].get('cite', '') if c.get('citations') else ''
                jurisdiction = (c.get('jurisdiction') or {}).get('name', '')
                decision_date = c.get('decision_date', '')
                parts = [name]
                if citation:
                    parts.append(citation)
                if jurisdiction:
                    parts.append(f"[{jurisdiction}]")
                if decision_date:
                    parts.append(f"({decision_date})")
                if name:
                    case_entries.append(" ".join(parts))
            if not case_entries:
                return None
            return {
                'value': "; ".join(case_entries),
                'source': 'Caselaw Access Project API',
                'metadata': {
                    'query_term': term,
                    'count': data.get('count', 0),
                    'api': 'case_law_v1',
                    'disclaimer': (
                        'US case-law results. For Vietnamese law, see local DB. '
                        'Always verify with primary legal sources.'
                    ),
                },
                'confidence': 0.7,
            }
        except Exception as e:
            logger.warning(f"[V5.8-API] Case.law API failed for '{term}': {e}", exc_info=True)
            return None

    def health_check(self) -> bool:
        """[V104.32] Always-healthy THEO THIẾT KẾ: source thuần local
        (dataset cứng trong code, fetch() không chạm mạng) — không có
        network endpoint nào để ping, nên không có live evidence để thu.
        Contract: KHÔNG dùng nguồn này làm bằng chứng reachable cho bất
        kỳ network capability nào."""
        return True
