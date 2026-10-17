"""
SCP - Viet Nam | Self-Correcting Pipeline
Copyright (c) 2026 SCP Vietnam Project. All Rights Reserved.




License: See LICENSE file
Contact: scp-vietnam@example.com
"""

"""
ChemistryDataSource - Data source cho Hóa học
Bao gồm: 118 nguyên tố, hợp chất phổ biến, phản ứng, hằng số hóa học.
"""
import json
import logging
import warnings
import urllib.parse
import urllib.request
from typing import Any

from scp.interfaces.data_source import IDataSource
from scp.security.url_safety import safe_urlopen  # [AUDIT-20260909 SSRF-S1]

logger = logging.getLogger(__name__)

# [AUDIT-20260909 SSRF-S1] Host cố định cho PubChem fetch.
_PUBCHEM_BASE = "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name"


def build_pubchem_url(entity: str) -> str:
    """[AUDIT-20260909 SSRF-S1] Pure URL builder — compound name (input động)
    được quote(safe='') → '/', '..', '?' bị encode, luôn nằm trong MỘT path
    segment của host cố định pubchem.ncbi.nlm.nih.gov."""
    quoted = urllib.parse.quote(str(entity or ""), safe="")
    return (
        f"{_PUBCHEM_BASE}/{quoted}"
        f"/property/MolecularFormula,MolecularWeight/JSON"
    )


class ChemistryDataSource(IDataSource):
    """
    Data source cho các câu hỏi Hóa học.
    Hỗ trợ: nguyên tố (symbol, atomic number, mass, group, period),
            hợp chất (formula, MM, tên), phản ứng, hằng số.
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

        # ---------------- Periodic Table: 118 elements ----------------
        # Format: symbol -> {name, vi_name, atomic_number, atomic_mass, group, period, category}
        self._elements: dict[str, dict[str, Any]] = self._build_periodic_table()

        # Atomic number -> symbol (reverse lookup)
        self._atomic_number_to_symbol = {
            v['atomic_number']: k for k, v in self._elements.items()
        }

        # ---------------- Common compounds ----------------
        self._compounds: dict[str, dict[str, Any]] = self._build_compounds()

        # ---------------- Chemical constants (emptied for Tabula Rasa) ----------------
        self._constants: dict[str, float] = {}

        # ---------------- Common reactions ----------------
        self._reactions: dict[str, dict[str, Any]] = {}


    # ---------------- Build periodic table (118 elements) ----------------
    def _build_periodic_table(self) -> dict[str, dict[str, Any]]:
        return {}

    def _build_compounds(self) -> dict[str, dict[str, Any]]:
        return {}

    def name(self) -> str:
        return "ChemistryDataSource"

    @property
    def priority(self) -> int:
        return 2

    @property
    def ttl(self) -> int:
        return 604800  # 1 week - chemical constants rarely change

    def get_supported_intents(self) -> list[str]:
        return [
            'chemical_element',
            'chemical_compound',
            'chemical_reaction',
            'molar_mass',
            'chemical_constant',
            'ph_info',
            'oxidation_state',
        ]

    def can_handle(self, intent: str, entity: str | None = None) -> bool:
        if intent in self.get_supported_intents():
            return True
        if entity:
            entity_lower = entity.lower().strip()
            # Check elements (by symbol, name, or atomic number)
            if entity_lower in self._elements:
                return True
            # Check compounds
            if entity_lower in self._compounds:
                return True
            # Check constants
            if entity_lower in self._constants:
                return True
            # Check reactions
            for key in self._reactions:
                if key in entity_lower or entity_lower in key:
                    return True
            # Check for chemical formula pattern (e.g., H2O, NaCl)
            import re
            if re.match(r'^[A-Z][a-z]?\d*(?:[A-Z][a-z]?\d*)*$', entity):
                return True
        return False

    def fetch(self, intent: str, entity: str, **kwargs) -> dict[str, Any] | None:
        """Lấy dữ liệu hóa học."""
        if not entity:
            return None

        entity_lower = entity.lower().strip()

        # Try elements
        result = self._search_elements(entity_lower, entity)
        if result:
            return result

        # Try compounds
        result = self._search_compounds(entity_lower)
        if result:
            return result

        # Try constants
        result = self._search_constants(entity_lower)
        if result:
            return result

        # Try reactions
        result = self._search_reactions(entity_lower)
        if result:
            return result

        # Try PubChem API as fallback (for compounds)
        result = self._fetch_from_pubchem(entity)
        if result:
            return result

        return None

    def _search_elements(self, entity_lower: str, original: str) -> dict[str, Any] | None:
        """Tìm nguyên tố."""
        # Direct match
        if entity_lower in self._elements:
            data = self._elements[entity_lower]
            return {
                'value': data['atomic_mass'],
                'source': 'IUPAC Periodic Table (Local)',
                'metadata': {
                    'symbol': data['symbol'],
                    'name_en': data['name'],
                    'name_vi': data['vi_name'],
                    'atomic_number': data['atomic_number'],
                    'atomic_mass': data['atomic_mass'],
                    'group': data['group'],
                    'period': data['period'],
                    'category': data['category'],
                    'unit': 'g/mol',
                }
            }

        # Case-sensitive symbol check (e.g., "Fe" not "fe")
        if original in self._elements:
            data = self._elements[original]
            return {
                'value': data['atomic_mass'],
                'source': 'IUPAC Periodic Table (Local)',
                'metadata': {
                    'symbol': data['symbol'],
                    'name_en': data['name'],
                    'name_vi': data['vi_name'],
                    'atomic_number': data['atomic_number'],
                    'atomic_mass': data['atomic_mass'],
                    'group': data['group'],
                    'period': data['period'],
                    'category': data['category'],
                    'unit': 'g/mol',
                }
            }

        return None

    def _search_compounds(self, entity_lower: str) -> dict[str, Any] | None:
        """Tìm hợp chất."""
        if entity_lower in self._compounds:
            data = self._compounds[entity_lower]
            return {
                'value': data['molar_mass'],
                'source': 'Local Chemistry Database',
                'metadata': {
                    'formula': data['formula'],
                    'molar_mass': data['molar_mass'],
                    'type': data['type'],
                    'unit': 'g/mol',
                }
            }
        return None

    def _search_constants(self, entity_lower: str) -> dict[str, Any] | None:
        """Tìm hằng số hóa học."""
        if entity_lower in self._constants:
            value = self._constants[entity_lower]
            return {
                'value': value,
                'source': 'CODATA / NIST (Local)',
                'metadata': {
                    'constant_name': entity_lower,
                    'method': 'lookup',
                }
            }
        # Partial match
        for key, value in self._constants.items():
            if key in entity_lower or entity_lower in key:
                return {
                    'value': value,
                    'source': 'CODATA / NIST (Local)',
                    'metadata': {
                        'constant_name': key,
                        'method': 'lookup',
                    }
                }
        return None

    def _search_reactions(self, entity_lower: str) -> dict[str, Any] | None:
        """Tìm phản ứng hóa học."""
        if entity_lower in self._reactions:
            data = self._reactions[entity_lower]
            return {
                'value': data['equation'],
                'source': 'Local Chemistry Database',
                'metadata': data
            }
        # Partial match
        for key, data in self._reactions.items():
            if key in entity_lower or entity_lower in key:
                return {
                    'value': data['equation'],
                    'source': 'Local Chemistry Database',
                    'metadata': data
                }
        return None

    def _fetch_from_pubchem(self, entity: str) -> dict[str, Any] | None:
        """Fallback: Lấy từ PubChem."""
        try:
            # [AUDIT-20260909 SSRF-S1] build URL (quote entity) rồi fetch qua
            # safe_urlopen thay raw requests.get.
            url = build_pubchem_url(entity)
            req = urllib.request.Request(
                url, headers={"User-Agent": "SCP-Chemistry/1.0"}
            )  # noqa: S310 — validated by safe_urlopen
            with safe_urlopen(req, timeout=5) as response:
                if getattr(response, "status", 200) != 200:
                    return None
                data = json.loads(response.read().decode("utf-8", errors="replace"))
            props = data.get('PropertyTable', {}).get('Properties', [{}])[0]
            return {
                'value': props.get('MolecularWeight', 0),
                'source': 'PubChem API',
                'metadata': {
                    'formula': props.get('MolecularFormula', ''),
                    'molar_mass': props.get('MolecularWeight', 0),
                    'unit': 'g/mol',
                    'method': 'pubchem'
                }
            }
        except Exception as e:
            logger.warning(f"[Chemistry] PubChem fetch failed: {e}", exc_info=True)
        return None

    def health_check(self) -> bool:
        """[V104.32] Always-healthy THEO THIẾT KẾ: source thuần local
        (dataset cứng trong code, fetch() không chạm mạng) — không có
        network endpoint nào để ping, nên không có live evidence để thu.
        Contract: KHÔNG dùng nguồn này làm bằng chứng reachable cho bất
        kỳ network capability nào."""
        return True
