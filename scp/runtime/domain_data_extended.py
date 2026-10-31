# scp/runtime/domain_data_extended.py
"""Mở rộng 9 miền tri thức tĩnh cục bộ (W19 Multi-Agent Domain Expansion).

Bao gồm: Technology, Cybersecurity, Logic, Medical, Legal, Finance, History,
Environment, Education (100% local in-memory dictionaries, 0 network calls).
Được tách từ domain_data_fork.py nhằm tuân thủ nghiêm ngặt trần file ARC-01 (<= 780 LOC).
"""

from __future__ import annotations

import logging
import os
import re
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_EXTENDED_SOURCES_CACHE: dict[str, Any] = {}


def _get_extended_source(mod_name: str, cls_name: str) -> Any:
    if mod_name not in _EXTENDED_SOURCES_CACHE:
        import importlib
        mod = importlib.import_module(f"scp.data_sources.{mod_name}")
        cls = getattr(mod, cls_name)
        _EXTENDED_SOURCES_CACHE[mod_name] = cls()
    return _EXTENDED_SOURCES_CACHE[mod_name]


def resolve_extended_local_domains(question_lower: str) -> dict[str, Any] | None:
    """Resolve queries deterministically against static dictionaries in 9 specialized DataSources."""
    try:
        # 1. Technology (Programming languages, AI concepts, hardware, protocols)
        tech = _get_extended_source("technology", "TechnologyDataSource")
        for lang, info in getattr(tech, "_languages", {}).items():
            if re.search(rf"\b{re.escape(lang)}\b", question_lower):
                if any(k in question_lower for k in ("năm nào", "ra đời", "ai tạo", "tác giả", "creator", "created", "year", "paradigm", "ngôn ngữ")):
                    text = (
                        f"Ngôn ngữ lập trình {lang.title()} ra đời năm {info.get('year')}, "
                        f"do {info.get('creator')} phát triển (mô hình: {info.get('paradigm')}, kiểu: {info.get('typing')})."
                    )
                    return {
                        "text": text,
                        "api_name": "TechnologyDataSource (Local Static)",
                        "api_url": "local:scp/data_sources/technology.py",
                        "evidence": text,
                    }
        for proto, info in getattr(tech, "_protocols", {}).items():
            if re.search(rf"\b{re.escape(proto)}\b", question_lower) and any(
                k in question_lower for k in ("port", "cổng", "giao thức", "protocol", "là gì", "what is")
            ):
                text = f"Giao thức {proto.upper()} ({info.get('desc')}) hoạt động ở tầng {info.get('layer')}, cổng mặc định là {info.get('port')}."
                return {
                    "text": text,
                    "api_name": "TechnologyDataSource (Local Static)",
                    "api_url": "local:scp/data_sources/technology.py",
                    "evidence": text,
                }

        # 2. Cybersecurity (Attacks, encryption standards)
        cyber = _get_extended_source("cybersecurity", "CybersecurityDataSource")
        for atk, desc in getattr(cyber, "_attacks", {}).items():
            if re.search(rf"\b{re.escape(atk.lower())}\b", question_lower) and any(
                k in question_lower for k in ("là gì", "tấn công", "attack", "what is", "bảo mật")
            ):
                text = f"{atk}: {desc}."
                return {
                    "text": text,
                    "api_name": "CybersecurityDataSource (Local Static)",
                    "api_url": "local:scp/data_sources/cybersecurity.py",
                    "evidence": text,
                }
        for enc, desc in getattr(cyber, "_encryption", {}).items():
            if re.search(rf"\b{re.escape(enc.lower())}\b", question_lower):
                text = f"Chuẩn mã hóa/băm {enc}: {desc}."
                return {
                    "text": text,
                    "api_name": "CybersecurityDataSource (Local Static)",
                    "api_url": "local:scp/data_sources/cybersecurity.py",
                    "evidence": text,
                }

        # 3. Logic (Logical fallacies, syllogisms, boolean algebra)
        logic_src = _get_extended_source("logic", "LogicDataSource")
        for fallacy, desc in getattr(logic_src, "_fallacies", {}).items():
            if fallacy.lower() in question_lower:
                text = f"Ngụy biện {fallacy} (Logical fallacy): {desc}."
                return {
                    "text": text,
                    "api_name": "LogicDataSource (Local Static)",
                    "api_url": "local:scp/data_sources/logic.py",
                    "evidence": text,
                }
        for syl_key, info in getattr(logic_src, "_syllogisms", {}).items():
            syl_name = syl_key.replace("_", " ")
            if syl_name in question_lower:
                text = f"Quy tắc suy luận {syl_name.title()}: {info.get('pattern')} Ví dụ: {info.get('example')}"
                return {
                    "text": text,
                    "api_name": "LogicDataSource (Local Static)",
                    "api_url": "local:scp/data_sources/logic.py",
                    "evidence": text,
                }

        # 4. Legal (Vietnamese laws, state organs, human rights)
        legal_src = _get_extended_source("legal", "LegalDataSource")
        for law_name, info in getattr(legal_src, "_vn_laws", {}).items():
            if law_name.lower() in question_lower:
                text = f"{info.get('desc')} được ban hành năm {info.get('year')}, gồm {info.get('articles')} điều."
                return {
                    "text": text,
                    "api_name": "LegalDataSource (Local Static)",
                    "api_url": "local:scp/data_sources/legal.py",
                    "evidence": text,
                }

        # 5. History (26 Historical Vietnamese & World milestones)
        hist_src = _get_extended_source("history", "HistoryDataSource")
        for yr_key, info in getattr(hist_src, "_events", {}).items():
            ev_name = str(info.get("event") or info.get("name") or "").lower()
            yr_match = re.search(rf"\b(?:năm\s+)?{re.escape(str(yr_key))}\b", question_lower) and any(
                k in question_lower for k in ("sự kiện", "lịch sử", "năm", "chiến thắng", "xảy ra", "history", "event")
            )
            ev_match = bool(ev_name and len(ev_name) >= 5 and ev_name in question_lower)
            if yr_match or ev_match:
                if "event" in info:
                    loc = f" tại {info.get('location')}" if info.get("location") else ""
                    text = f"Sự kiện lịch sử năm {info.get('year')}{loc}: {info.get('event')}."
                else:
                    text = f"Nhân vật lịch sử {info.get('name')} ({info.get('born')}–{info.get('died')}): {info.get('title')}."
                return {
                    "text": text,
                    "api_name": "HistoryDataSource (Local Static)",
                    "api_url": "local:scp/data_sources/history.py",
                    "evidence": text,
                }

        # 6. Medical (Human body constants, vitals)
        med_src = _get_extended_source("medical", "MedicalDataSource")
        for body_key, val in getattr(med_src, "_body", {}).items():
            if body_key.lower() in question_lower:
                text = f"Chỉ số cơ thể người ({body_key}): {val}."
                return {
                    "text": text,
                    "api_name": "MedicalDataSource (Local Static)",
                    "api_url": "local:scp/data_sources/medical.py",
                    "evidence": text,
                }
        for vital_key, info in getattr(med_src, "_vitals", {}).items():
            if vital_key.lower() in question_lower:
                text = f"Chỉ số sinh hiệu {vital_key}: mức bình thường là {info.get('normal')}."
                return {
                    "text": text,
                    "api_name": "MedicalDataSource (Local Static)",
                    "api_url": "local:scp/data_sources/medical.py",
                    "evidence": text,
                }

        # 7. Education (Learning methods, universities, levels)
        edu_src = _get_extended_source("education", "EducationDataSource")
        for method_key, desc in getattr(edu_src, "_methods", {}).items():
            if method_key.lower() in question_lower:
                text = f"Phương pháp học tập {method_key}: {desc}."
                return {
                    "text": text,
                    "api_name": "EducationDataSource (Local Static)",
                    "api_url": "local:scp/data_sources/education.py",
                    "evidence": text,
                }
        for uni_key, info in getattr(edu_src, "_universities", {}).items():
            if re.search(rf"\b{re.escape(uni_key.lower())}\b", question_lower):
                text = f"Đại học {uni_key}: {info}."
                return {
                    "text": text,
                    "api_name": "EducationDataSource (Local Static)",
                    "api_url": "local:scp/data_sources/education.py",
                    "evidence": text,
                }

        # 8. Finance (Currencies, precious metals)
        fin_src = _get_extended_source("finance", "FinanceDataSource")
        for cur_code, info in getattr(fin_src, "_currencies", {}).items():
            vi_name = str(info.get("vi_name") or "").lower()
            if re.search(rf"\b{re.escape(cur_code.lower())}\b", question_lower) or (vi_name and vi_name in question_lower):
                if any(k in question_lower for k in ("tiền tệ", "đồng tiền", "ký hiệu", "currency", "symbol", "nước nào", "quốc gia")):
                    text = f"Đồng tiền {cur_code.upper()} ({info.get('vi_name')} / {info.get('name')}), ký hiệu {info.get('symbol')}, của {info.get('country')}."
                    return {
                        "text": text,
                        "api_name": "FinanceDataSource (Local Static)",
                        "api_url": "local:scp/data_sources/finance.py",
                        "evidence": text,
                    }

        # 9. Environment (Climate & atmospheric constants)
        env_src = _get_extended_source("environment", "EnvironmentDataSource")
        for clim_key, val in getattr(env_src, "_climate", {}).items():
            if clim_key.lower() in question_lower:
                text = f"Chỉ số khí hậu / môi trường ({clim_key}): {val}."
                return {
                    "text": text,
                    "api_name": "EnvironmentDataSource (Local Static)",
                    "api_url": "local:scp/data_sources/environment.py",
                    "evidence": text,
                }

        # [G-W1 · 2026-10-25] Đã XÓA dict _gov_standards hardcode trong Python.
        # Dữ liệu tiêu chuẩn Chính phủ/ISO (NĐ 85/2016, TT 12/2022, ISO 16290,
        # ISO/IEC 15408, NIST AI RMF...) nằm trong data/documents/*.jsonl và được
        # resolve qua CanonicalFTSWarehouse ở bước dưới (tra cứu, không mã hóa).
    except (RuntimeError, ValueError, OSError, ImportError, AttributeError, KeyError) as exc:
        logger.debug("[domain_fork] extended local domain lookup error: %s", exc, exc_info=True)
    return None


_STATUTORY_TERMS_RE = re.compile(
    r"(?<!định\s)(?<!quy\s)\b(luật|bộ luật|nghị định|thông tư|hiến pháp|law|decree|standard|tiêu chuẩn|iso|nist)\b|\bđiều\s+\d+\b",
    re.IGNORECASE,
)

_CATALOG_LOCK = threading.RLock()
_DEFAULT_CATALOG_STORE: Any = None
_DEFAULT_CATALOG_DIR_ENV: str = ""

_GENERIC_SINGLE_TOKEN_EXCLUSIONS = frozenset({"mác", "logic", "management"})
_GENERIC_MANAGEMENT_EXCLUSIONS = frozenset({"management", "quản lý"})


def _get_catalog_store(catalog_dir: str | Path | None = None) -> Any:
    """Load and cache SHA-256 verified KnowledgeStore (Fail-Closed on tamper/error)."""
    global _DEFAULT_CATALOG_STORE, _DEFAULT_CATALOG_DIR_ENV
    from scp.knowledge.store import CatalogError, KnowledgeStore

    if catalog_dir is not None:
        try:
            return KnowledgeStore(catalog_dir=catalog_dir, strict=True, check_hashes=True)
        except (CatalogError, OSError, ValueError, RuntimeError) as exc:
            logger.warning("[domain_fork] catalog integrity check failed (%s): %s", catalog_dir, exc)
            return None

    env_dir = os.environ.get("SCP_KNOWLEDGE_CATALOG_DIR", "").strip()
    with _CATALOG_LOCK:
        if _DEFAULT_CATALOG_STORE is None or env_dir != _DEFAULT_CATALOG_DIR_ENV:
            try:
                _DEFAULT_CATALOG_STORE = KnowledgeStore(strict=True, check_hashes=True)
                _DEFAULT_CATALOG_DIR_ENV = env_dir
            except (CatalogError, OSError, ValueError, RuntimeError) as exc:
                logger.warning("[domain_fork] default catalog load failed: %s", exc)
                return None
        return _DEFAULT_CATALOG_STORE


def _has_phrase_boundary(text_lower: str, phrase_lower: str) -> bool:
    """Token-boundary check supporting Vietnamese diacritics without regex keyword lists."""
    if not phrase_lower:
        return False
    pattern = rf"(?<![\wÀ-ỹ]){re.escape(phrase_lower)}(?![\wÀ-ỹ])"
    return bool(re.search(pattern, text_lower))


def _resolve_catalog_fact_shards(store: Any, q_norm: str, dataset_version: str) -> dict[str, Any] | None:
    """Match verified FactRecords in humanities.literature & humanities.philosophy."""
    best_per_record: dict[str, tuple[int, int, str, Any]] = {}
    for dom in ("humanities.literature", "humanities.philosophy"):
        for rec in store.find_candidates(dom, ""):
            if str(rec.review.get("status", "")).lower() != "verified":
                continue
            ev_text = str(rec.evidence.get("text") or "").strip()
            ev_url = str(rec.evidence.get("source_url") or "").strip()
            ev_src = str(rec.evidence.get("source_id") or "").strip()
            if not (ev_text and ev_url and ev_src):
                continue

            raw_surfaces: list[str] = [rec.entity.canonical_name]
            if isinstance(rec.entity.names, dict):
                raw_surfaces.extend(str(v) for v in rec.entity.names.values() if v)
            elif rec.entity.names:
                raw_surfaces.extend(str(v) for v in rec.entity.names if v and len(str(v)) > 2)
            if rec.entity.aliases:
                raw_surfaces.extend(str(a) for a in rec.entity.aliases if a)
            works = rec.facts.get("works")
            if isinstance(works, (list, tuple)):
                raw_surfaces.extend(str(w) for w in works if w)

            best_hit: tuple[int, int, str] | None = None
            canon_lower = rec.entity.canonical_name.strip().lower()
            for surf in raw_surfaces:
                cand = re.sub(r"\s+", " ", str(surf or "").strip().lower())
                if not cand or cand in _GENERIC_SINGLE_TOKEN_EXCLUSIONS:
                    continue
                tokens = cand.split()
                if len(tokens) == 1 and len(cand) < 4 and cand != canon_lower:
                    continue
                if _has_phrase_boundary(q_norm, cand):
                    score = (len(tokens), len(cand), str(surf).strip())
                    if best_hit is None or score[:2] > best_hit[:2]:
                        best_hit = score
            if best_hit is not None:
                best_per_record[rec.record_id] = (best_hit[0], best_hit[1], best_hit[2], rec)

    if not best_per_record:
        return None

    ranked = sorted(best_per_record.values(), key=lambda item: (item[0], item[1]), reverse=True)
    if len(ranked) > 1 and (ranked[0][0], ranked[0][1]) == (ranked[1][0], ranked[1][1]):
        return None  # Fail-closed on ambiguous tie

    _, _, matched_surf, rec = ranked[0]
    ev_clean = re.sub(r"\.\s+", " — ", str(rec.evidence["text"]).strip().rstrip("."))
    fact_info = str(rec.facts.get("info") or rec.facts.get("description") or "").strip().rstrip(".")
    fact_clean = re.sub(r"\.\s+", " — ", fact_info)
    works_list = rec.facts.get("works")
    works_str = f" — Works / Tác phẩm: {', '.join(str(w) for w in works_list)}" if isinstance(works_list, (list, tuple)) and works_list else ""
    canon_name = rec.entity.canonical_name
    ent_id = str(rec.entity.entity_id or "").lower()
    etype = str((rec.entity.extra or {}).get("entity_type") or ent_id.split("-", 1)[0]).lower()
    if etype == "author":
        prefix = f"Tác giả / Nhà văn {canon_name} ({matched_surf})"
    elif etype == "movement":
        prefix = f"Trào lưu văn học {canon_name} ({matched_surf})"
    elif etype == "philosopher":
        prefix = f"Triết gia / Tư tưởng triết học {canon_name} ({matched_surf})"
    else:
        prefix = f"Khái niệm / Trường phái triết học {canon_name} ({matched_surf})"
    body = f"{fact_clean}{works_str} — {ev_clean}." if fact_clean else f"{ev_clean}{works_str}."
    text = f"{prefix}: {body}"
    return {
        "text": text,
        "api_name": f"UnifiedKnowledgeCatalog ({rec.domain})",
        "api_url": str(rec.evidence["source_url"]),
        "evidence": text,
        "domain": rec.domain,
        "record_id": rec.record_id,
        "dataset_version": dataset_version,
        "reader_tier": "catalog_verified",
        "source_indicator": "KB:catalog",
    }


def _resolve_catalog_reference_shards(store: Any, q_norm: str, dataset_version: str) -> dict[str, Any] | None:
    """Match verified ReferenceRecords in management.frameworks, weather_codes, and units."""
    # 1. management.frameworks (16 records)
    best_mgmt: dict[str, tuple[int, int, str, Any]] = {}
    for ref in store.list_reference("management.frameworks"):
        name = str(ref.data.get("name") or ref.key).strip()
        summary = str(ref.data.get("summary") or ref.description or "").strip()
        if not summary:
            continue
        surfaces: list[str] = [name, ref.key.replace("_", " ")]
        aliases = ref.data.get("aliases")
        if isinstance(aliases, (list, tuple)):
            surfaces.extend(str(a) for a in aliases if a)

        best_hit: tuple[int, int, str] | None = None
        for surf in surfaces:
            cand = re.sub(r"\s+", " ", str(surf or "").strip().lower())
            if not cand:
                continue
            if ref.key == "management" and cand in _GENERIC_MANAGEMENT_EXCLUSIONS:
                continue
            tokens = cand.split()
            if _has_phrase_boundary(q_norm, cand):
                score = (len(tokens), len(cand), cand)
                if best_hit is None or score[:2] > best_hit[:2]:
                    best_hit = score
        if best_hit is not None:
            best_mgmt[ref.record_id] = (best_hit[0], best_hit[1], best_hit[2], ref)

    if best_mgmt:
        if len(best_mgmt) > 1 and "management.frameworks:management" in best_mgmt:
            best_mgmt.pop("management.frameworks:management", None)
        ranked = sorted(best_mgmt.values(), key=lambda item: (item[0], item[1]), reverse=True)
        if len(ranked) > 1 and (ranked[0][0], ranked[0][1]) == (ranked[1][0], ranked[1][1]):
            return None
        _, _, matched_surf, ref = ranked[0]
        name = str(ref.data.get("name") or ref.key).strip()
        summary_clean = re.sub(r"\.\s+", " — ", str(ref.data.get("summary") or ref.description).strip().rstrip(".")) + "."
        text = f"Mô hình / Phương pháp phân tích quản trị {name} ({matched_surf}) — {summary_clean}"
        return {
            "text": text,
            "api_name": "UnifiedKnowledgeCatalog (management.frameworks)",
            "api_url": f"catalog:reference/management.frameworks#{ref.key}",
            "evidence": text,
            "domain": "management",
            "record_id": ref.record_id,
            "dataset_version": dataset_version,
            "reader_tier": "catalog_verified",
            "source_indicator": "KB:catalog",
        }

    # 2. weather_codes (WMO 4677, 28 records)
    if any(k in q_norm for k in ("wmo", "weather code", "mã thời tiết", "mã khí tượng")):
        codes = re.findall(r"\b(\d{1,2})\b", q_norm)
        matched_refs = [store.get_reference("weather_codes", c) for c in dict.fromkeys(codes)]
        valid_refs = [r for r in matched_refs if r is not None]
        if len(valid_refs) == 1:
            ref = valid_refs[0]
            t_en = str(ref.data.get("text_en") or "")
            t_vi = str(ref.data.get("text_vi") or "")
            text = f"Mã thời tiết WMO weather code {ref.key} ({t_en} / {t_vi}) — {ref.description}."
            return {
                "text": text,
                "api_name": "UnifiedKnowledgeCatalog (weather_codes)",
                "api_url": f"catalog:reference/weather_codes#{ref.key}",
                "evidence": text,
                "domain": "weather",
                "record_id": ref.record_id,
                "dataset_version": dataset_version,
                "reader_tier": "catalog_verified",
                "source_indicator": "KB:catalog",
            }

    # 3. units (BIPM SI, 23 records — non-numeric unit definition lookups)
    if not re.search(r"\d", q_norm) and any(k in q_norm for k in ("đơn vị", "unit", "bipm", "hệ si")):
        best_unit: tuple[int, int, str, Any] | None = None
        for ref in store.list_reference("units"):
            u_name = str(ref.data.get("name") or ref.key).strip()
            surfaces = [u_name]
            u_aliases = ref.data.get("aliases")
            if isinstance(u_aliases, (list, tuple)):
                surfaces.extend(str(a) for a in u_aliases if a and len(str(a).strip()) >= 3)
            for surf in surfaces:
                cand = re.sub(r"\s+", " ", str(surf or "").strip().lower())
                if cand and _has_phrase_boundary(q_norm, cand):
                    score = (len(cand.split()), len(cand), cand, ref)
                    if best_unit is None or score[:2] > best_unit[:2]:
                        best_unit = score
        if best_unit is not None:
            _, _, matched_surf, ref = best_unit
            sym = str(ref.data.get("symbol") or ref.key)
            u_name = str(ref.data.get("name") or ref.key)
            base_u = str(ref.data.get("base_unit") or "")
            factor = ref.data.get("factor_to_base")
            text = (
                f"Đơn vị đo lường hệ SI / BIPM unit {u_name} ({sym}, {matched_surf}) — "
                f"{ref.description} (1 {sym} = {factor} {base_u})."
            )
            return {
                "text": text,
                "api_name": "UnifiedKnowledgeCatalog (units)",
                "api_url": f"catalog:reference/units#{ref.key}",
                "evidence": text,
                "domain": "physics",
                "record_id": ref.record_id,
                "dataset_version": dataset_version,
                "reader_tier": "catalog_verified",
                "source_indicator": "KB:catalog",
            }

    return None


def resolve_catalog_verified_lookup(
    question: str,
    q_norm: str | None = None,
    *,
    catalog_dir: str | Path | None = None,
) -> dict[str, Any] | None:
    """Resolve queries against SHA-256 verified Unified Knowledge Catalog (`KB:catalog`)."""
    if os.environ.get("SCP_CATALOG_READER_TIER", "1").strip().lower() in {"0", "false", "off", "no"}:
        return None
    norm = (q_norm if q_norm is not None else str(question or "")).strip().lower()
    if not norm:
        return None

    store = _get_catalog_store(catalog_dir=catalog_dir)
    if store is None or store.snapshot is None:
        return None

    dataset_version = store.dataset_version
    fact_hit = _resolve_catalog_fact_shards(store, norm, dataset_version)
    if fact_hit is not None:
        return fact_hit
    return _resolve_catalog_reference_shards(store, norm, dataset_version)


def resolve_extended_and_fts_lookup(question: str, q_norm: str) -> dict[str, Any] | None:
    """Resolve queries via static local domains, dynamic SQLite FTS5 warehouse, and verified catalog."""
    is_statutory = bool(_STATUTORY_TERMS_RE.search(q_norm))
    if not is_statutory:
        ans = resolve_extended_local_domains(q_norm)
        if ans:
            ans.setdefault("reader_tier", "extended_local")
            ans.setdefault("source_indicator", "KB:local_static")
            return ans

    # Dynamic SQLite FTS5 Warehouse lookup from data/documents/ and verified dynamic ingestions
    # [G-W1 · 2026-10-25] fail-closed đồng âm: câu "Thủ đô của X" với X là từ đồng âm
    # (đạo đức, anh trai, ý nghĩa...) KHÔNG được kho FTS trả thủ đô quốc gia.
    _skip_fts_capital_homonym = False
    if "thủ đô của " in q_norm:
        try:
            from scp.runtime.domain_data_fork import is_capital_homonym_context
            _skip_fts_capital_homonym = is_capital_homonym_context(q_norm)
        except ImportError:
            _skip_fts_capital_homonym = False
    if not _skip_fts_capital_homonym:
        try:
            from scp.knowledge.canonical_fts_warehouse import get_canonical_fts_warehouse
            fts_hits = get_canonical_fts_warehouse().search(question, limit=5)
            if fts_hits:
                picked_hit = None
                tier_name = "canonical_document"
                source_ind = "KB:canonical"
                if is_statutory:
                    stat_hits = [h for h in fts_hits if h.get("epistemic_tier") == "statutory_standard"]
                    if stat_hits:
                        picked_hit = stat_hits[0]
                        tier_name = "statutory_standard"
                        source_ind = "KB:statutory"
                if picked_hit is None:
                    canon_hits = [h for h in fts_hits if h.get("epistemic_tier") == "canonical_document"]
                    if canon_hits:
                        picked_hit = canon_hits[0]
                    elif is_statutory and fts_hits:
                        picked_hit = fts_hits[0]
                    else:
                        picked_hit = None

                if picked_hit is not None:
                    tier_name = picked_hit.get("epistemic_tier") or "canonical_document"
                    source_ind = "KB:statutory" if tier_name == "statutory_standard" else "KB:canonical"
                    return {
                        "text": picked_hit["text"],
                        "api_name": f"CanonicalFTSWarehouse ({picked_hit['domain']})",
                        "api_url": picked_hit["source_url"],
                        "evidence": picked_hit["text"],
                        "domain": picked_hit.get("domain", ""),
                        "epistemic_tier": tier_name,
                        "content_sha256": picked_hit.get("content_sha256"),
                        "reader_tier": tier_name,
                        "source_indicator": source_ind,
                    }
        except (ImportError, OSError, ValueError, KeyError) as exc:
            logger.debug("[domain_fork] FTS5 warehouse lookup error: %s", exc)

    if is_statutory:
        ans = resolve_extended_local_domains(q_norm)
        if ans:
            ans.setdefault("reader_tier", "extended_local")
            ans.setdefault("source_indicator", "KB:local_static")
            return ans

    cat_ans = resolve_catalog_verified_lookup(question, q_norm)
    if cat_ans is not None:
        return cat_ans

    return None


