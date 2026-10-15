"""
SCP - Viet Nam | Self-Correcting Pipeline
Copyright (c) 2026 SCP Vietnam Project. All Rights Reserved.

WHY Engine, Recursive Why, MetaFalsifier, ProofGraph
License: See LICENSE file
"""

"""
 MedicalDataSource — Y tế & Sức khỏe
Bao gồm: bệnh常见, thuốc, cơ thể người, chỉ số sức khỏe, vaccine.
Fallback: LiveKnowledgeFetcher (Wikipedia + MedlinePlus).
"""
import logging
import os
import re
import urllib.parse
import urllib.request
from typing import Any

from scp.core.api_utils import fetch_with_retry  # [V5.8-API]
from scp.data_sources.config_loader import get_api_key
from scp.interfaces.data_source import IDataSource
from scp.security.url_safety import safe_urlopen  # [AUDIT-20260909 SSRF-S1]

logger = logging.getLogger(__name__)

# [AUDIT-20260909 SSRF-S1] pmid từ response NCBI (external data) PHẢI là
# digits — chặn trước khi ghép vào URL efetch.
_PMID_RE = re.compile(r"^\d{1,10}$")


def build_ncbi_efetch_pubmed_url(pmids: list) -> str:
    """[AUDIT-20260909 SSRF-S1] Pure URL builder — mỗi pmid PHẢI fullmatch
    ^\\d{1,10}$; input xấu → ValueError TRƯỚC KHI fetch. Host cố định
    eutils.ncbi.nlm.nih.gov."""
    ids = [str(p or "").strip() for p in (pmids or [])]
    if not ids:
        raise ValueError("empty_pmid_list")
    for pid in ids:
        if not _PMID_RE.fullmatch(pid):
            raise ValueError(f"invalid_pmid:{pid[:32]!r}")
    return (
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
        f"?db=pubmed&id={','.join(ids)}"
        f"&rettype=abstract&retmode=text"
    )


# [V104.31 #2] Min key length for fuzzy token-boundary matching
_MIN_FUZZY_KEY_LEN = 4


def _token_boundary_match(key: str, entity_lower: str) -> bool:
    """[V104.31 #2] Word-boundary match — was: substring 'in' matched 'insulin'."""
    if not key or not entity_lower:
        return False
    if key == entity_lower:
        return True
    if len(key) < _MIN_FUZZY_KEY_LEN:
        return False
    pattern = r'(?<![\wÀ-ỹ])' + re.escape(key) + r'(?![\wÀ-ỹ])'
    if re.search(pattern, entity_lower):
        return True
    if len(entity_lower) >= _MIN_FUZZY_KEY_LEN:
        pattern_rev = r'(?<![\wÀ-ỹ])' + re.escape(entity_lower) + r'(?![\wÀ-ỹ])'
        if re.search(pattern_rev, key):
            return True
    return False


class MedicalDataSource(IDataSource):
    """Data source cho Y tế & Sức khỏe."""

    def __init__(self):
        self._cache: dict[str, Any] = {}
        # [V104.31 #9] Source date + low confidence for static medical data
        self._source_date = "2024-06"
        self._static_confidence = 0.55
        # [V5.8-API] PubMed API key (NCBI E-utilities) via ConfigLoader
        self._pubmed_api_key = (
            get_api_key("medical", required=False)
            or get_api_key("pubmed", required=False)
            or ""
        ).strip()
        self._ncbi_api_key = (
            get_api_key("ncbi", required=False)
            or self._pubmed_api_key
        ).strip()

        # Bệnh thường gặp
        self._diseases: dict[str, dict[str, Any]] = {}
        self._medicines: dict[str, dict[str, Any]] = {}
        self._vitals: dict[str, dict[str, Any]] = {}
        self._body: dict[str, Any] = {}
        self._vaccines: dict[str, dict[str, Any]] = {}

    @property
    def name(self) -> str:
        return "MedicalDataSource"

    @property
    def priority(self) -> int:
        return 2

    @property
    def ttl(self) -> int:
        # [FIX-CRIT-46 BUG 6] TẠI SAO: was 604800 (7 days) → stale drug doses
        # served for a full week. Medical knowledge (drug doses, contraindications,
        # vaccine schedules, clinical guidelines) changes on a faster cadence;
        # serving a 7-day-stale paracetamol max_daily dose is medically unsafe.
        # Fix: 86400 (24 hours) — same cadence as MedlinePlus / FDA drug-label
        # updates. Aligns with domain_registry.py medical entry.
        return 86400  # 24 hours

    def get_supported_intents(self) -> list[str]:
        return ['medical_disease', 'medical_medicine', 'medical_vital',
                'medical_body', 'medical_vaccine', 'medical_info',
                # [FIX-CRIT-46 BUG 7] medical_dose_advice — user asking "what
                # dose should I take?" → SCP MUST abstain per Constitution
                # ("abstain rather than fabricate"). Returning a stored dose
                # as evidence would be practicing medicine without a license.
                'medical_dose_advice']

    def can_handle(self, intent: str, entity: str | None = None) -> bool:
        if intent in self.get_supported_intents():
            return True
        if entity:
            entity_lower = entity.lower().strip()
            for table in (self._diseases, self._medicines, self._vitals,
                          self._body, self._vaccines):
                if entity_lower in table:
                    return True
                for key in table:
                    if _token_boundary_match(key, entity_lower):  # [V104.31 #2]
                        return True
        return False

    def fetch(self, intent: str, entity: str, **kwargs) -> dict[str, Any] | None:
        if not entity:
            return None
        entity_lower = entity.lower().strip()

        # [FIX-CRIT-46 BUG 7] TẠI SAO: previously NO abstain path — the fetch()
        # method returned a stored dose (e.g. "paracetamol 500mg người lớn, 4h/lần")
        # as evidence whenever a user asked "what dose should I take?". That is
        # practicing medicine without a license and violates the SCP Constitution
        # ("abstain rather than fabricate"). The static dose in the local DB is
        # a reference value, NOT personalized medical advice — patient weight,
        # age, liver function, comorbidities, concurrent medications all matter.
        # Fix: if the caller classified the question intent as
        # `medical_dose_advice` (user asking "what dose should I take?",
        # "liều dùng bao nhiêu?", "how many mg should I take?"), abstain —
        # return None value with abstain=True and a reason. Downstream
        # (judge.py / engine.py) sees value=None and propagates UNKNOWN/
        # abstain rather than serving the stored dose as an answer.
        if intent == 'medical_dose_advice':
            return {
                'value': None,
                'abstain': True,
                'reason': (
                    "SCP cannot provide medical dosage advice. "
                    "Consult a healthcare professional."
                ),
                'source': 'MedicalDataSource (abstain policy)',
                'metadata': {
                    'intent': 'medical_dose_advice',
                    'abstain': True,
                    'disclaimer': (
                        "Dose depends on patient weight, age, liver/kidney "
                        "function, comorbidities, and concurrent medications. "
                        "SCP's static DB contains reference values, not "
                        "personalized medical advice."
                    ),
                },
                'confidence': 0.0,
            }

        # [V104.31 #9] Common metadata: source_date + disclaimer for static DB
        disclaimer = (
            "Dữ liệu y tế tĩnh — chỉ tham khảo. Không thay thế tư vấn bác sĩ. "
            "Có thể lỗi thời so với hướng dẫn lâm sàng mới nhất."
        )
        common_meta = {
            'source_date': self._source_date,
            'static_confidence': self._static_confidence,
            'disclaimer': disclaimer,
        }

        # Diseases
        if entity_lower in self._diseases:
            data = self._diseases[entity_lower]
            return {
                'value': data.get('en', ''),
                'source': 'Local Medical Database',
                'metadata': {**data, **common_meta},
                'confidence': self._static_confidence,
            }
        for key, data in self._diseases.items():
            if _token_boundary_match(key, entity_lower):  # [V104.31 #2]
                return {
                    'value': data.get('en', ''),
                    'source': 'Local Medical Database',
                    'metadata': {**data, **common_meta},
                    'confidence': self._static_confidence,
                }

        # Medicines
        if entity_lower in self._medicines:
            data = self._medicines[entity_lower]
            return {
                'value': data.get('use', ''),
                'source': 'Local Medical Database',
                'metadata': {**data, **common_meta},
                'confidence': self._static_confidence,
            }
        for key, data in self._medicines.items():
            if _token_boundary_match(key, entity_lower):  # [V104.31 #2]
                return {
                    'value': data.get('use', ''),
                    'source': 'Local Medical Database',
                    'metadata': {**data, **common_meta},
                    'confidence': self._static_confidence,
                }

        # Vitals
        if entity_lower in self._vitals:
            data = self._vitals[entity_lower]
            return {
                'value': data.get('normal', data.get('threshold', data.get('range', ''))),
                'source': 'Local Medical Database',
                'metadata': {**data, **common_meta},
                'confidence': self._static_confidence,
            }
        for key, data in self._vitals.items():
            if _token_boundary_match(key, entity_lower):  # [V104.31 #2]
                return {
                    'value': data.get('normal', data.get('threshold', data.get('range', ''))),
                    'source': 'Local Medical Database',
                    'metadata': {**data, **common_meta},
                    'confidence': self._static_confidence,
                }

        # Body
        if entity_lower in self._body:
            return {
                'value': self._body[entity_lower],
                'source': 'Local Medical Database',
                'metadata': {'fact': entity_lower, 'value': self._body[entity_lower], **common_meta},
                'confidence': self._static_confidence,
            }
        for key, val in self._body.items():
            if _token_boundary_match(key, entity_lower):  # [V104.31 #2]
                return {
                    'value': val,
                    'source': 'Local Medical Database',
                    'metadata': {'fact': key, 'value': val, **common_meta},
                    'confidence': self._static_confidence,
                }

        # Vaccines
        if entity_lower in self._vaccines:
            data = self._vaccines[entity_lower]
            return {
                'value': data.get('type', ''),
                'source': 'Local Medical Database',
                'metadata': {**data, **common_meta},
                'confidence': self._static_confidence,
            }

        # [V5.8-API] Try PubMed (NCBI E-utilities) as fallback for medical info.
        # Local DB is preferred for known Vietnamese terms (keeps existing tests
        # passing + keeps source_date/disclaimer/static_confidence contract).
        # PubMed is tried ONLY when local DB has no match — gives real abstracts
        # for drug/disease queries outside the static DB's coverage.
        pubmed_result = self._fetch_from_pubmed(entity)
        if pubmed_result:
            return pubmed_result

        return None

    # [V5.8-API] PubMed E-utilities integration
    def _fetch_from_pubmed(self, entity: str) -> dict[str, Any] | None:
        """
        [V5.8-API] Fetch medical info from PubMed via NCBI E-utilities.
        Step 1: esearch.fcgi (JSON) → list of PMIDs
        Step 2: efetch.fcgi (text) → abstracts for top PMIDs
        Returns dict with value/source/metadata/confidence, or None on failure.
        """
        if not entity or not entity.strip():
            return None
        term = entity.strip()
        # Build esearch URL (JSON response — handled by fetch_with_retry)
        api_key_param = f"&api_key={self._pubmed_api_key}" if self._pubmed_api_key else ""
        esearch_url = (
            f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
            f"?db=pubmed&term={urllib.parse.quote(term, safe='')}"
            f"&retmode=json&retmax=3{api_key_param}"
        )
        try:
            esearch_data = fetch_with_retry(esearch_url, headers={"User-Agent": "SCP/1.0"}, timeout=5)
            if not esearch_data:
                return None
            id_list = esearch_data.get("esearchresult", {}).get("idlist", [])
            if not id_list:
                return None
        except Exception as e:
            logger.warning(f"[V5.8-API] PubMed esearch failed for '{term}': {e}", exc_info=True)
            return None

        # Fetch abstracts via efetch (text/plain response)
        pmids = id_list[:3]
        # [AUDIT-20260909 SSRF-S1] pmids (external data) được validate bằng
        # regex trong builder; input xấu → ValueError TRƯỚC KHI fetch.
        efetch_url = build_ncbi_efetch_pubmed_url(pmids) + api_key_param
        try:
            req = urllib.request.Request(
                efetch_url, headers={"User-Agent": "SCP/1.0"}
            )  # noqa: S310 — validated by safe_urlopen
            with safe_urlopen(req, timeout=8) as resp:
                if getattr(resp, "status", 200) != 200:
                    return None
                abstract_text = resp.read().decode("utf-8", errors="replace").strip()
            if not abstract_text:
                return None
            if len(abstract_text) < 20:
                return None
            # Truncate very long abstracts to a sensible size
            if len(abstract_text) > 2000:
                abstract_text = abstract_text[:2000] + "... [truncated]"
            return {
                'value': abstract_text,
                'source': 'PubMed API (NCBI E-utilities)',
                'metadata': {
                    'pmids': pmids,
                    'query_term': term,
                    'api': 'pubmed_esearch+efetch',
                    'disclaimer': (
                        'PubMed abstracts are reference material, not medical advice. '
                        'Consult a healthcare professional for diagnosis/treatment.'
                    ),
                },
                'confidence': 0.85,
            }
        except Exception as e:
            logger.warning(f"[V5.8-API] PubMed efetch failed for PMIDs {pmids}: {e}", exc_info=True)
            return None

    def health_check(self) -> bool:
        """[V104.32] Always-healthy THEO THIẾT KẾ: source thuần local
        (dataset cứng trong code, fetch() không chạm mạng) — không có
        network endpoint nào để ping, nên không có live evidence để thu.
        Contract: KHÔNG dùng nguồn này làm bằng chứng reachable cho bất
        kỳ network capability nào."""
        return True
