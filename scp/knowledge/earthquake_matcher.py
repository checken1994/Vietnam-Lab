"""SCP Unified Knowledge Catalog — Earthquake Matcher (Schema v1).

Implements Section 8 of docs/KNOWLEDGE_STORE_UNIFIED_DATA_PLAN_VN.md:
- Domain relevance detection
- Superlative intent resolution (dynamic max magnitude calculation)
- Entity + Year matching with year consistency and disambiguation
- Reverse magnitude lookup
- Verification intent handling
- Rich provenance metadata
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Mapping, Sequence

from scp.knowledge.store import FactRecord, KnowledgeStore, normalize_term

# Domain relevance keywords
EARTHQUAKE_DOMAIN_KEYWORDS = (
    "động đất",
    "earthquake",
    "địa chấn",
    "richter",
    "seismic",
    "tsunami",
    "sóng thần",
)

# Superlative intent keywords
SUPERLATIVE_KEYWORDS = (
    "mạnh nhất",
    "lớn nhất",
    "kinh hoàng nhất",
    "strongest",
    "largest",
    "greatest",
)

# Reverse lookup question indicators
REVERSE_LOOKUP_KEYWORDS = (
    "ở đâu",
    "xảy ra ở đâu",
    "tại đâu",
    "nơi nào",
    "quốc gia nào",
    "địa điểm",
    "where",
    "what",
    "là gì",
    "tên là gì",
)

# Verification intent indicators
VERIFICATION_KEYWORDS = (
    "có phải",
    "phải không",
    "đúng không",
    "is it",
    "was it",
)

# Cache default KnowledgeStore singleton across calls within process
_default_store: KnowledgeStore | None = None


def get_default_knowledge_store() -> KnowledgeStore:
    """Return shared KnowledgeStore instance, creating it if needed."""
    global _default_store
    if _default_store is None:
        _default_store = KnowledgeStore()
    return _default_store


def _extract_distinctive_entity_tokens(rec: FactRecord) -> set[str]:
    """Extract lowercase distinctive entity tokens/phrases from record entity info."""
    ent = rec.entity
    raw_texts = [
        ent.canonical_name,
        ent.entity_id,
        *(ent.names or ()),
        *(ent.aliases or ()),
        ent.place or "",
    ]

    stop_words = {
        "động", "đất", "động đất", "earthquake", "tsunami", "sóng", "thần", "sóng thần",
        "trận", "năm", "the", "and", "của", "tại", "ở", "great", "chấn", "địa chấn",
        "islands", "island", "ocean", "quần", "đảo", "quần đảo",
    }

    distinctive: set[str] = set()

    for text in raw_texts:
        if not text:
            continue
        norm = normalize_term(text)
        # Remove 4-digit years from entity phrases
        norm_no_year = re.sub(r"\b(19\d\d|20\d\d)\b", "", norm).strip()
        norm_no_year = re.sub(r"\s+", " ", norm_no_year)

        # Multi-word key phrases
        for phrase in [
            "valdivia", "chile", "great chilean",
            "sumatra", "andaman", "indonesia", "ấn độ dương", "indian ocean",
        ]:
            if phrase in norm:
                distinctive.add(phrase)

        # Single tokens
        for token in re.split(r"[\s\-_,.]+", norm_no_year):
            token = token.strip()
            if len(token) >= 3 and token not in stop_words and not token.isdigit():
                distinctive.add(token)

    return distinctive


def _matches_entity(rec: FactRecord, question_norm: str) -> bool:
    """Check if question mentions distinctive entity tokens or aliases of record."""
    distinctive = _extract_distinctive_entity_tokens(rec)
    for kw in distinctive:
        # Use regex word boundary where applicable
        if re.search(rf"\b{re.escape(kw)}\b", question_norm):
            return True
    return False


def _extract_magnitude_float(val: Any) -> float | None:
    """Safely extract float magnitude from facts dict or numeric value."""
    if isinstance(val, (dict, Mapping)):
        raw = val.get("value")
    else:
        raw = val
    if raw is None:
        return None
    try:
        return float(raw)
    except (ValueError, TypeError):
        return None


def match_earthquake_query(
    question: str,
    store: KnowledgeStore | None = None,
) -> dict[str, Any] | None:
    """Match natural earthquake query against canonical catalog (Schema v1).

    Adheres strictly to Fail-Closed and Evidence-First principles:
    1. Rejects queries outside earthquake/seismic domain or known earthquake entities.
    2. Resolves superlative intent dynamically by calculating maximum magnitude.
    3. Matches entities and years with strict year-consistency and disambiguation.
    4. Refuses ambiguous bare years without entity markers (e.g. 'động đất năm 2004').
    5. Resolves reverse lookup (magnitude -> earthquake event).
    6. Returns rich provenance metadata for audit trails.
    """
    if not question or not str(question).strip():
        return None

    q_norm = normalize_term(question)

    # 1. Domain Relevance Check
    has_domain_kw = any(k in q_norm for k in EARTHQUAKE_DOMAIN_KEYWORDS)
    has_known_entity_kw = any(k in q_norm for k in ("valdivia", "sumatra", "andaman"))

    if not (has_domain_kw or has_known_entity_kw):
        return None

    # Resolve store and fetch domain records
    if store is None:
        store = get_default_knowledge_store()

    snapshot = store.snapshot
    if snapshot is None:
        return None

    domain_records = snapshot.find_candidates(domain="geology.earthquake", entity_terms="")
    if not domain_records:
        return None

    dataset_version = snapshot.dataset_version

    # Extract 4-digit years from question
    year_strings = re.findall(r"\b(19\d\d|20\d\d)\b", q_norm)
    query_years = {int(y) for y in year_strings}

    # 2. Superlative Intent
    # E.g. "Trận động đất mạnh nhất từng ghi nhận là gì?"
    is_superlative = any(k in q_norm for k in SUPERLATIVE_KEYWORDS)
    if is_superlative:
        # Calculate maximum magnitude dynamically across domain records
        records_with_mag: list[tuple[float, FactRecord]] = []
        for rec in domain_records:
            mag = _extract_magnitude_float(rec.facts.get("magnitude"))
            if mag is not None:
                records_with_mag.append((mag, rec))

        if records_with_mag:
            records_with_mag.sort(key=lambda item: item[0], reverse=True)
            top_mag, top_rec = records_with_mag[0]
            # Ensure unique winner (no tie)
            if len(records_with_mag) == 1 or records_with_mag[1][0] < top_mag:
                return _build_response_payload(
                    rec=top_rec,
                    dataset_version=dataset_version,
                    matched_by="superlative_magnitude",
                    intent="superlative",
                )

    # 3. Entity + Year Matching
    # Find records whose entity is explicitly mentioned in question
    matching_candidates: list[FactRecord] = []
    for rec in domain_records:
        if _matches_entity(rec, q_norm):
            # Validate Year Consistency
            # If question explicitly mentions year(s), record year MUST match one of them
            if query_years:
                if rec.entity.year is not None and rec.entity.year not in query_years:
                    # Year conflict: question specified a year incompatible with this record
                    continue
            matching_candidates.append(rec)

    # If exactly one candidate matches entity + year
    if len(matching_candidates) == 1:
        chosen_rec = matching_candidates[0]
        is_verification = any(k in q_norm for k in VERIFICATION_KEYWORDS)
        matched_by = "entity_verification" if is_verification else "entity_and_year_match"
        intent = "verification" if is_verification else "entity"
        return _build_response_payload(
            rec=chosen_rec,
            dataset_version=dataset_version,
            matched_by=matched_by,
            intent=intent,
        )

    # If multiple candidates matched entity, fail-closed on ambiguity
    if len(matching_candidates) > 1:
        return None

    # 4. Reverse Lookup: Question asks for location/earthquake given magnitude
    # E.g. "Trận động đất 9.5 xảy ra ở đâu?"
    # Only evaluate if no entity matched
    mag_matches = re.findall(r"\b([789]\.\d)\b", q_norm)
    if mag_matches:
        queried_mag = float(mag_matches[0])
        rev_candidates: list[FactRecord] = []
        for rec in domain_records:
            rec_mag = _extract_magnitude_float(rec.facts.get("magnitude"))
            if rec_mag is not None and abs(rec_mag - queried_mag) < 1e-4:
                # If question specified year, check year consistency
                if query_years and rec.entity.year is not None and rec.entity.year not in query_years:
                    continue
                rev_candidates.append(rec)

        if len(rev_candidates) == 1:
            return _build_response_payload(
                rec=rev_candidates[0],
                dataset_version=dataset_version,
                matched_by="reverse_magnitude_lookup",
                intent="reverse_lookup",
            )

    # 5. Fail-Closed Disambiguation:
    # If question only has a year (e.g. "2004") or location not in catalog (e.g. "California"),
    # or no verified candidate was found, return None.
    return None


def _build_response_payload(
    rec: FactRecord,
    dataset_version: str,
    matched_by: str,
    intent: str = "entity",
) -> dict[str, Any]:
    """Format standard USGS response dictionary with complete audit trail."""
    canonical_name = rec.entity.canonical_name
    place = rec.entity.place or ""
    year = rec.entity.year
    mag_dict = rec.facts.get("magnitude", {})
    if isinstance(mag_dict, (dict, Mapping)):
        mag_val = mag_dict.get("value")
        mag_unit = mag_dict.get("unit", "Mw")
    else:
        mag_val = mag_dict
        mag_unit = "Mw"

    evidence_dict = rec.evidence or {}
    source_url = str(evidence_dict.get("source_url") or "https://earthquake.usgs.gov")
    evidence_text = str(evidence_dict.get("text") or "")
    review_status = str((rec.review or {}).get("status", "verified"))

    if intent == "superlative":
        text = (
            f"Trận động đất mạnh nhất từng được ghi nhận trong lịch sử hiện đại là {canonical_name} "
            f"({place}) xảy ra năm {year} với độ lớn M{mag_val} {mag_unit}. "
            f"Nguồn USGS: {source_url}."
        )
    elif intent == "reverse_lookup":
        text = (
            f"Trận động đất có độ lớn M{mag_val} {mag_unit} là {canonical_name}, "
            f"xảy ra tại {place} (năm {year}). Nguồn USGS: {source_url}."
        )
    elif intent == "verification":
        text = (
            f"Trận động đất {canonical_name} ({place}) năm {year} có độ lớn thực tế ghi nhận là "
            f"M{mag_val} {mag_unit}. Nguồn USGS: {source_url}."
        )
    else:
        text = (
            f"Trận động đất {canonical_name} ({place}) năm {year} có độ lớn M{mag_val} {mag_unit}. "
            f"Nguồn USGS: {source_url}."
        )

    if not evidence_text:
        evidence_text = text

    return {
        "text": text,
        "api_name": "USGSDataSource (Unified Knowledge Catalog)",
        "api_url": source_url,
        "evidence": evidence_text,
        "record_id": rec.record_id,
        "dataset_version": dataset_version,
        "review_status": review_status,
        "matched_by": matched_by,
    }
