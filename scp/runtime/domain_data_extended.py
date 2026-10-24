# scp/runtime/domain_data_extended.py
"""Mở rộng 9 miền tri thức tĩnh cục bộ (W19 Multi-Agent Domain Expansion).

Bao gồm: Technology, Cybersecurity, Logic, Medical, Legal, Finance, History,
Environment, Education (100% local in-memory dictionaries, 0 network calls).
Được tách từ domain_data_fork.py nhằm tuân thủ nghiêm ngặt trần file ARC-01 (<= 780 LOC).
"""

from __future__ import annotations

import logging
import re
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


def resolve_extended_and_fts_lookup(question: str, q_norm: str) -> dict[str, Any] | None:
    """Resolve queries via static local domains and dynamic SQLite FTS5 warehouse."""
    is_specific_article = bool(re.search(r"\bđiều\s+\d+\b", q_norm))
    if not is_specific_article:
        ans = resolve_extended_local_domains(q_norm)
        if ans:
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
            fts_hits = get_canonical_fts_warehouse().search(question, limit=1)
            if fts_hits:
                top_hit = fts_hits[0]
                return {
                    "text": top_hit["text"],
                    "api_name": f"CanonicalFTSWarehouse ({top_hit['domain']})",
                    "api_url": top_hit["source_url"],
                    "evidence": top_hit["text"],
                    "domain": top_hit.get("domain", ""),
                    "epistemic_tier": top_hit.get("epistemic_tier", "canonical_document"),
                    "content_sha256": top_hit.get("content_sha256"),
                }
        except (ImportError, OSError, ValueError, KeyError) as exc:
            logger.debug("[domain_fork] FTS5 warehouse lookup error: %s", exc)

    if is_specific_article:
        ans = resolve_extended_local_domains(q_norm)
        if ans:
            return ans

    return None

