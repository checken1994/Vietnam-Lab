"""[DomainDataFork] Unified Local Data Source Resolver for SCP Question Router.

Kết nối toàn bộ các nguồn dữ liệu khoa học / thực tế cục bộ (Offline Local Sources)
trong `scp/data_sources/` vào luồng Lookup Fork của QuestionRouter:
  - AstronomyDataSource: hành tinh, sao, thiên hà, hằng số thiên văn (NASA/IAU).
  - ChemistryDataSource: nguyên tố, hợp chất, phân tử khối, hóa trị (IUPAC).
  - PhysicsDataSource: hằng số vật lý (tốc độ ánh sáng, Planck, gia tốc trọng trường...).
  - GeographyDataSource: thủ đô, dân số, diện tích, quốc gia.
  - MathDataSource: hằng số toán học (pi, e, phi, căn bậc hai...).

Fail-closed contract:
  - Trả về None khi không chắc chắn về entity/intent -> câu hỏi trượt xuống LLM fallback.
  - Không bao giờ bịa đặt số liệu. Toàn bộ giá trị đến từ bảng dữ liệu cục bộ trong repo.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger("scp.runtime.domain_data_fork")

# Lazy singletons
_astronomy_source: Any = None
_chemistry_source: Any = None
_physics_source: Any = None
_geography_source: Any = None
_math_source: Any = None


def _get_astronomy() -> Any:
    global _astronomy_source
    if _astronomy_source is None:
        try:
            from scp.data_sources.astronomy import AstronomyDataSource
            _astronomy_source = AstronomyDataSource()
        except Exception as exc:
            logger.debug("[domain_fork] AstronomyDataSource load error: %s", exc)
            _astronomy_source = None
    return _astronomy_source


def _get_chemistry() -> Any:
    global _chemistry_source
    if _chemistry_source is None:
        try:
            from scp.data_sources.chemistry import ChemistryDataSource
            _chemistry_source = ChemistryDataSource()
        except Exception as exc:
            logger.debug("[domain_fork] ChemistryDataSource load error: %s", exc)
            _chemistry_source = None
    return _chemistry_source


def _get_physics() -> Any:
    global _physics_source
    if _physics_source is None:
        try:
            from scp.data_sources.physics import PhysicsDataSource
            _physics_source = PhysicsDataSource()
        except Exception as exc:
            logger.debug("[domain_fork] PhysicsDataSource load error: %s", exc)
            _physics_source = None
    return _physics_source


def _get_geography() -> Any:
    global _geography_source
    if _geography_source is None:
        try:
            from scp.data_sources.geography import GeographyDataSource
            _geography_source = GeographyDataSource()
        except Exception as exc:
            logger.debug("[domain_fork] GeographyDataSource load error: %s", exc)
            _geography_source = None
    return _geography_source


def _get_math() -> Any:
    global _math_source
    if _math_source is None:
        try:
            from scp.data_sources.math import MathDataSource
            _math_source = MathDataSource()
        except Exception as exc:
            logger.debug("[domain_fork] MathDataSource load error: %s", exc)
            _math_source = None
    return _math_source


# ---------------------------------------------------------------------------
# 1. Thiên văn học (Astronomy)
# ---------------------------------------------------------------------------
_PLANET_NAMES_VI_EN: dict[str, str] = {}



def _resolve_astronomy(question_lower: str) -> dict[str, Any] | None:
    if any(k in question_lower for k in ("nhiệt độ trung bình trái đất", "mực nước biển tăng", "co2 hiện tại", "ozone layer")):
        return None
    try:
        astro = _get_astronomy()
        matched_planets = {
            en_name for vi_name, en_name in _PLANET_NAMES_VI_EN.items()
            if vi_name in question_lower and en_name not in ("sun", "moon")
        }
        if len(matched_planets) > 1:
            return None

        # Hành tinh
        for vi_name, en_name in _PLANET_NAMES_VI_EN.items():
            if vi_name in question_lower:
                if vi_name == "mặt trời" and any(k in question_lower for k in ["gần mặt trời", "quanh mặt trời", "từ mặt trời"]):
                    continue
                if vi_name in ("mặt trăng", "moon") and any(
                    p in question_lower for p in [
                        "sao thủy", "sao kim", "trái đất", "sao hỏa", "sao mộc", "sao thổ",
                        "sao thiên vương", "sao hải vương", "mercury", "venus", "earth",
                        "mars", "jupiter", "saturn", "uranus", "neptune"
                    ]
                ):
                    continue
                data = astro.fetch("planet_info", en_name)
                if data and "metadata" in data:
                    meta = data["metadata"]
                    vi_title = meta.get("vi_name") or vi_name.title()
                    # Hỏi về số lượng mặt trăng / vệ tinh
                    if any(k in question_lower for k in ["mặt trăng", "vệ tinh", "moon", "satellite"]):
                        moons = meta.get("moons", 0)
                        if en_name == "earth":
                            text = f"{vi_title} có 1 vệ tinh tự nhiên (Mặt Trăng)."
                        else:
                            text = f"{vi_title} có {moons} vệ tinh tự nhiên (mặt trăng)."
                    elif any(k in question_lower for k in ["khối lượng", "mass", "nặng"]):
                        mass = meta.get("mass_kg")
                        if mass is not None:
                            text = f"Khối lượng của {vi_title} là {mass:.3e} kg."
                        else:
                            text = f"Khối lượng của {vi_title} không có sẵn."
                    elif any(k in question_lower for k in ["bán kính", "radius", "kích thước"]):
                        radius = meta.get("radius_m")
                        if radius is not None:
                            r_km = radius / 1000.0
                            text = f"Bán kính trung bình của {vi_title} là khoảng {r_km:,.0f} km."
                        else:
                            text = f"Bán kính của {vi_title} không có sẵn."
                    elif any(k in question_lower for k in ["chu kỳ", "quỹ đạo", "năm", "orbital period"]):
                        period = meta.get("orbital_period_s")
                        if period is not None:
                            period_days = period / 86400.0
                            text = f"Chu kỳ quỹ đạo quanh Mặt Trời của {vi_title} là khoảng {period_days:.1f} ngày Trái Đất."
                        else:
                            text = f"Chu kỳ quỹ đạo của {vi_title} không có sẵn."
                    elif any(k in question_lower for k in ["khoảng cách", "distance", "cách mặt trời"]):
                        axis = meta.get("semi_major_axis_m")
                        if axis is not None:
                            axis_mkm = axis / 1.0e9
                            text = f"Khoảng cách trung bình từ {vi_title} đến Mặt Trời là khoảng {axis_mkm:,.1f} triệu km ({axis:.3e} m)."
                        else:
                            text = f"Khoảng cách quỹ đạo của {vi_title} không có sẵn."
                    else:
                        pos = meta.get("position")
                        pos_str = f"Vị trí thứ {pos} từ Mặt Trời, " if pos is not None else ""
                        mass_val = meta.get("mass_kg")
                        mass_str = f"{mass_val:.2e} kg" if mass_val is not None else "N/A"
                        text = f"{vi_title} ({en_name.title()}): {pos_str}khối lượng {mass_str}, {meta.get('moons', 0)} vệ tinh."
                    return {
                        "text": text,
                        "api_name": "AstronomyDataSource (Local NASA/IAU)",
                        "api_url": "local:scp/data_sources/astronomy.py",
                        "evidence": text,
                    }

        # [G-W1 · 2026-10-25] Đã XÓA các fact hardcode inline (tuổi vũ trụ 13.8 tỷ năm,
        # hằng số Hubble 67.4). Dữ liệu thiên văn nằm trong CanonicalFTSWarehouse
        # (data/documents/*.jsonl seed từ Internet) và được tra cứu ở bước 9.
    except Exception as exc:
        logger.debug("[domain_fork] astronomy lookup error: %s", exc, exc_info=True)
    return None


# ---------------------------------------------------------------------------
# 2. Hóa học (Chemistry)
# ---------------------------------------------------------------------------
_COMMON_COMPOUNDS: dict[str, str] = {}
_COMMON_ELEMENTS: dict[str, str] = {}



def _resolve_chemistry(question_lower: str) -> dict[str, Any] | None:
    try:
        chem = _get_chemistry()
        # Phân tử khối hợp chất
        if any(k in question_lower for k in ["phân tử khối", "molar mass", "molecular weight", "khối lượng phân tử"]):
            for name, formula in _COMMON_COMPOUNDS.items():
                if name == "nước" and re.search(r"\bnước\s+(nào|ngoài|ta)\b", question_lower):
                    continue
                if re.search(rf"\b{re.escape(name)}\b", question_lower):
                    data = chem.fetch("molar_mass", formula)
                    if data:
                        val = data.get("value")
                        text = f"Phân tử khối của {formula} là {val} g/mol."
                        return {
                            "text": text,
                            "api_name": "ChemistryDataSource (Local IUPAC)",
                            "api_url": "local:scp/data_sources/chemistry.py",
                            "evidence": text,
                        }

        # Nguyên tố hóa học
        if any(k in question_lower for k in ["nguyên tử khối", "nguyên tử số", "số hiệu nguyên tử", "atomic number", "atomic mass"]):
            for name, symbol in _COMMON_ELEMENTS.items():
                if symbol in ("C", "K") and re.search(r"\bvitamin\s+[ck]\b", question_lower):
                    continue
                # Dùng word-boundary tránh 'h' match trong 'hiệu'
                if re.search(rf"\b{re.escape(name)}\b", question_lower):
                    data = chem.fetch("chemical_element", symbol)
                    if data and "metadata" in data:
                        meta = data["metadata"]
                        elem_name = meta.get("name_vi") or meta.get("name_en")
                        z = meta.get("atomic_number")
                        m = meta.get("atomic_mass")
                        text = f"Nguyên tố {elem_name} ({symbol}): Số hiệu nguyên tử Z = {z}, nguyên tử khối A ≈ {m} g/mol."
                        return {
                            "text": text,
                            "api_name": "ChemistryDataSource (Local IUPAC)",
                            "api_url": "local:scp/data_sources/chemistry.py",
                            "evidence": text,
                        }

    except Exception as exc:
        logger.debug("[domain_fork] chemistry lookup error: %s", exc, exc_info=True)
    return None


# ---------------------------------------------------------------------------
# 3. Vật lý (Physics)
# ---------------------------------------------------------------------------
_PHYSICS_CONSTANTS: dict[str, tuple[str, str]] = {}



def _resolve_physics(question_lower: str) -> dict[str, Any] | None:
    try:
        for key, (label, val_str) in _PHYSICS_CONSTANTS.items():
            if key in question_lower:
                text = f"{label} có giá trị là {val_str}."
                return {
                    "text": text,
                    "api_name": "PhysicsDataSource (Local CODATA)",
                    "api_url": "local:scp/data_sources/physics.py",
                    "evidence": text,
                }
    except Exception as exc:
        logger.debug("[domain_fork] physics lookup error: %s", exc, exc_info=True)
    return None


# ---------------------------------------------------------------------------
# 4. Địa lý Quốc gia & Thủ đô (Geography)
# ---------------------------------------------------------------------------
_CAPITALS_TABLE: dict[str, tuple[str, str]] = {}

# [G-W1 · 2026-10-25] Guard đồng âm cho chủ thể "thủ đô của X" — quy tắc NGÔN
# NGỮ (không phải dữ liệu fact): các cụm X đồng âm với tên quốc gia không được
# hiểu là quốc gia. Dùng chung cho resolver địa lý và tầng FTS warehouse.
_CAPITAL_HOMONYM_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(r"\bý\s+(nghĩa|kiến|định|niệm|thức|chí|tưởng|đồ|muốn|nguyện|tứ|dân|trời|học|vị|nguyên|hợp|bảo|tình)"),
    re.compile(r"\bnga\b.*\b(thiên nga|hằng nga)\b|\b(thiên nga|hằng nga)\b"),
    re.compile(r"\banh\s+(trai|em|chị|họ|rể|bạn|ấy|ta)"),
    re.compile(r"(\bđạo\s+đức\b|\bđức\s+(tính|tin|hạnh|độ)\b|\b(phúc|công)\s+đức\b)"),
    re.compile(r"(\bthẩm\s+mỹ\b|\bmỹ\s+(thuật|phẩm|nhân|cảm|vị|miều|lệ|học))"),
    re.compile(r"\b(thuốc|gió|dép)\s+lào\b"),
)


def is_capital_homonym_context(question_lower: str, country_key: str | None = None) -> bool:
    """True khi câu hỏi 'thủ đô của ...' đang dùng từ đồng âm, KHÔNG phải quốc gia.
    country_key=None: kiểm tra mọi pattern (dùng ở tầng FTS); ngược lại chỉ pattern
    của quốc gia tương ứng."""
    if country_key is None:
        return any(patt.search(question_lower) for patt in _CAPITAL_HOMONYM_PATTERNS)
    key_to_idx = {"ý": 0, "nga": 1, "anh": 2, "đức": 3, "mỹ": 4, "lào": 5}
    idx = key_to_idx.get(country_key)
    return idx is not None and bool(_CAPITAL_HOMONYM_PATTERNS[idx].search(question_lower))


def _resolve_geography(question_lower: str) -> dict[str, Any] | None:
    try:
        # Câu hỏi về thủ đô (yêu cầu 'thủ đô của ' tường minh để không nuốt test catalog/non-conversion)
        if "thủ đô của " in question_lower:
            if any(k in question_lower for k in ["nước nào", "quốc gia nào"]):
                return None
            for country_key, (capital, country_name) in _CAPITALS_TABLE.items():
                if is_capital_homonym_context(question_lower, country_key):
                    continue
                if re.search(rf"\b{re.escape(country_key)}\b", question_lower):
                    text = f"Thủ đô của {country_name} là {capital}."
                    return {
                        "text": text,
                        "api_name": "GeographyDataSource (Local UN/ISO)",
                        "api_url": "local:scp/data_sources/geography.py",
                        "evidence": text,
                    }
    except Exception as exc:
        logger.debug("[domain_fork] geography lookup error: %s", exc, exc_info=True)
    return None


# ---------------------------------------------------------------------------
# 5. Toán học Hằng số (Math Constants)
# ---------------------------------------------------------------------------
_MATH_CONSTANTS: dict[str, tuple[str, str]] = {}



def _resolve_math(question_lower: str) -> dict[str, Any] | None:
    try:
        for key, (label, val_str) in _MATH_CONSTANTS.items():
            if key == "số pi":
                matched = bool(re.search(r"\bsố\s+pi\b(?!-)", question_lower))
            elif key == "số e":
                matched = bool(re.search(r"\bsố\s+e\b(?!-)", question_lower))
            else:
                matched = bool(re.search(rf"\b{re.escape(key)}\b", question_lower))
            if matched:
                text = f"Giá trị của {label} xấp xỉ là {val_str}."
                return {
                    "text": text,
                    "api_name": "MathDataSource (Local)",
                    "api_url": "local:scp/data_sources/math.py",
                    "evidence": text,
                }
    except Exception as exc:
        logger.debug("[domain_fork] math lookup error: %s", exc, exc_info=True)
    return None


# ---------------------------------------------------------------------------
# 6. Địa chấn & Động đất USGS (Earthquake)
# ---------------------------------------------------------------------------
_usgs_source: Any = None


def _get_usgs() -> Any:
    global _usgs_source
    if _usgs_source is None:
        from scp.data_sources.usgs import USGSDataSource
        _usgs_source = USGSDataSource()
    return _usgs_source


def _resolve_usgs(question_lower: str) -> dict[str, Any] | None:
    if not any(k in question_lower for k in ["động đất", "earthquake", "địa chấn", "richter"]):
        return None
    try:
        # [G-W1 · 2026-10-25] Historical catalog đã chuyển sang DATA FILE
        # data/knowledge/usgs_historical_earthquakes.jsonl (seed từ Internet bởi
        # scripts/seed_knowledge_from_internet.py). Fail-closed: thiếu file -> bỏ qua.
        catalog_path = Path(__file__).resolve().parents[2] / "data" / "knowledge" / "usgs_historical_earthquakes.jsonl"
        if catalog_path.is_file():
            for line in catalog_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                tokens = [str(t).lower() for t in (row.get("match_tokens") or [])]
                alt_tokens = [str(t).lower() for t in (row.get("alt_match_tokens") or [])]
                text = str(row.get("text") or "")
                if not text:
                    continue
                if (tokens and all(t in question_lower for t in tokens)) or (
                    alt_tokens and all(t in question_lower for t in alt_tokens)
                ):
                    return {
                        "text": text,
                        "api_name": "USGSDataSource (Historical Catalog)",
                        "api_url": str(row.get("api_url") or "https://earthquake.usgs.gov/earthquakes/browse/largest-world.php"),
                        "evidence": text,
                    }

        usgs = _get_usgs()
        data = usgs.query(question_lower)
        if data and "metadata" in data:
            meta = data["metadata"]
            if "top_events" in meta and meta["top_events"]:
                top = meta["top_events"][0]
                mag = top.get("magnitude")
                place = top.get("place")
                text = f"Sự kiện động đất ghi nhận gần đây bởi USGS: Độ lớn M{mag} tại {place} (tổng cộng {meta.get('result_count', 1)} trận trong {meta.get('time_window_days', 7)} ngày)."
            else:
                mag = meta.get("magnitude")
                place = meta.get("place")
                text = f"Sự kiện động đất ghi nhận gần đây bởi USGS: Độ lớn M{mag} tại {place}."
            return {
                "text": text,
                "api_name": "USGSDataSource (USGS Hazards API)",
                "api_url": "https://earthquake.usgs.gov/fdsnws/event/1/query",
                "evidence": text,
            }
    except Exception as exc:
        logger.debug("[domain_fork] usgs lookup error: %s", exc, exc_info=True)
    return None


# ---------------------------------------------------------------------------
# 7. Sinh học (Biology)
# ---------------------------------------------------------------------------
_biology_source: Any = None


def _get_biology() -> Any:
    global _biology_source
    if _biology_source is None:
        from scp.data_sources.biology import BiologyDataSource
        _biology_source = BiologyDataSource()
    return _biology_source


_AMINO_ACID_NAMES_VI: dict[str, tuple[str, str]] = {}



def _resolve_biology(question_lower: str) -> dict[str, Any] | None:
    try:
        bio = _get_biology()
        # Genetic code / Codon
        if any(k in question_lower for k in ["mã di truyền", "codon", "mã hóa cho", "triplet", "bộ ba"]):
            for codon, aa in bio._genetic_code.items():
                if re.search(rf"\b{re.escape(codon.lower())}\b", question_lower):
                    if "Stop codon" in str(aa):
                        text = f"Mã di truyền (codon) {codon.upper()} là {aa}."
                    elif "Codon mở đầu" in str(aa):
                        text = f"Mã di truyền (codon) {codon.upper()} mã hóa cho {aa}."
                    else:
                        en_name, vi_name = _AMINO_ACID_NAMES_VI.get(aa, (aa, aa))
                        text = f"Mã di truyền (codon) {codon.upper()} mã hóa cho axit amin {aa} ({en_name} / {vi_name})."
                    return {
                        "text": text,
                        "api_name": "BiologyDataSource (Standard Genetic Code)",
                        "api_url": "local:scp/data_sources/biology.py",
                        "evidence": text,
                    }

        # Amino acids
        if any(k in question_lower for k in ["axit amin", "amino acid"]):
            for code, (en_name, vi_name) in _AMINO_ACID_NAMES_VI.items():
                if re.search(rf"\b{re.escape(code.lower())}\b|\b{re.escape(en_name.lower())}\b|\b{re.escape(vi_name.lower())}\b", question_lower):
                    text = f"Axit amin {code} ({en_name} / {vi_name}) là một trong 20 axit amin tiêu chuẩn cấu tạo nên protein."
                    return {
                        "text": text,
                        "api_name": "BiologyDataSource (Amino Acid DB)",
                        "api_url": "local:scp/data_sources/biology.py",
                        "evidence": text,
                    }

        # Organelles / Tế bào
        organelle_map = {
            "ty thể": "mitochondria", "mitochondria": "mitochondria",
            "ribosome": "ribosome",
            "nhân tế bào": "nucleus", "nucleus": "nucleus",
            "lục lạp": "chloroplast", "chloroplast": "chloroplast",
            "lysosome": "lysosome", "tiêu thể": "lysosome",
        }
        for vi_org, en_org in organelle_map.items():
            if vi_org in question_lower:
                info = bio._cells.get(en_org)
                if info:
                    text = f"Bào quan {vi_org.title()} ({en_org}): Kích thước khoảng {info['size']}, chức năng chính là {info['function']}."
                    return {
                        "text": text,
                        "api_name": "BiologyDataSource (Cell Biology DB)",
                        "api_url": "local:scp/data_sources/biology.py",
                        "evidence": text,
                    }

        # [G-W1 · 2026-10-25] Đã XÓA fact hardcode inline (46 nhiễm sắc thể, bộ gen
        # 3.2 tỷ base). Dữ liệu sinh học con người nằm trong CanonicalFTSWarehouse
        # (data/documents/*.jsonl seed từ Internet) — tra cứu ở bước 9.

        # NCBI taxonomy lookup fallback
        if re.search(r"\b(loài|species|genus|phân loại học|chi sinh học)\b", question_lower):
            res = bio.query(question_lower)
            if res and res.get("found"):
                ans_text = res.get("answer")
                return {
                    "text": ans_text,
                    "api_name": "BiologyDataSource (NCBI Taxonomy)",
                    "api_url": "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi",
                    "evidence": ans_text,
                }
    except Exception as exc:
        logger.debug("[domain_fork] biology lookup error: %s", exc, exc_info=True)
    return None


# ---------------------------------------------------------------------------
# 8. Địa chất (Geology)
# ---------------------------------------------------------------------------
_geology_source: Any = None


def _get_geology() -> Any:
    global _geology_source
    if _geology_source is None:
        from scp.data_sources.geology import GeologyDataSource
        _geology_source = GeologyDataSource()
    return _geology_source


# Emptied for 100% Tabula Rasa — live queries routed through LiveKnowledgeFetcher
_MOHS_SCALE: dict[str, tuple[int, str, str]] = {}
_EARTH_LAYERS: dict[str, str] = {}


def _resolve_geology(question_lower: str) -> dict[str, Any] | None:
    try:
        # Mohs scale / Độ cứng khoáng vật
        if any(k in question_lower for k in ["độ cứng", "thang mohs", "mohs", "khoáng vật", "khoáng sản", "mineral"]):
            for key, (mohs_val, name_display, desc) in _MOHS_SCALE.items():
                if key in question_lower:
                    text = f"Khoáng vật {name_display}: Độ cứng theo thang Mohs là {mohs_val}. Đặc điểm: {desc}."
                    return {
                        "text": text,
                        "api_name": "GeologyDataSource (Mohs Scale / Local DB)",
                        "api_url": "local:scp/data_sources/geology.py",
                        "evidence": text,
                    }

        # Cấu tạo Trái Đất
        for layer_key, layer_text in _EARTH_LAYERS.items():
            if layer_key in question_lower:
                return {
                    "text": layer_text,
                    "api_name": "GeologyDataSource (Earth Structure DB)",
                    "api_url": "local:scp/data_sources/geology.py",
                    "evidence": layer_text,
                }

        # [G-W1 · 2026-10-25] Đã XÓA các đoạn mô tả đá hardcode inline (magma/
        # trầm tích/biến chất). Dữ liệu địa chất nằm trong CanonicalFTSWarehouse
        # (data/documents/*.jsonl seed từ Internet) — tra cứu ở bước 9.
    except Exception as exc:
        logger.debug("[domain_fork] geology lookup error: %s", exc, exc_info=True)
    return None



# ---------------------------------------------------------------------------
# Master Dispatcher
# ---------------------------------------------------------------------------
def resolve_domain_data_lookup(question: str) -> dict[str, Any] | None:
    """Điều phối tra cứu toàn bộ các nguồn dữ liệu chuyên ngành cục bộ (Local).

    Thứ tự ưu tiên:
      1. Địa chấn USGS (Earthquake)
      2. Sinh học (Biology)
      3. Địa chất (Geology)
      4. Thiên văn (Astronomy)
      5. Hóa học (Chemistry)
      6. Vật lý (Physics)
      7. Địa lý (Geography)
      8. Toán học (Math)

    Fail-closed: Trả về None nếu không tìm thấy, để câu hỏi rơi xuống Catalog/Wikipedia/LLM.
    """
    q_norm = question.strip().lower()
    if not q_norm:
        return None

    # 1. Địa chấn USGS
    ans = _resolve_usgs(q_norm)
    if ans:
        return ans

    # 2. Sinh học
    ans = _resolve_biology(q_norm)
    if ans:
        return ans

    # 3. Địa chất
    ans = _resolve_geology(q_norm)
    if ans:
        return ans

    # 4. Thiên văn
    ans = _resolve_astronomy(q_norm)
    if ans:
        return ans

    # 5. Hóa học
    ans = _resolve_chemistry(q_norm)
    if ans:
        return ans

    # 6. Vật lý
    ans = _resolve_physics(q_norm)
    if ans:
        return ans

    # 7. Địa lý
    ans = _resolve_geography(q_norm)
    if ans:
        return ans

    # 8. Toán học
    ans = _resolve_math(q_norm)
    if ans:
        return ans

    # 9. Extended local domains and Canonical FTS5 Warehouse (data/documents/ + Web Ingestions)
    try:
        from scp.runtime.domain_data_extended import resolve_extended_and_fts_lookup
        return resolve_extended_and_fts_lookup(question, q_norm)
    except (RuntimeError, ValueError, OSError, ImportError, AttributeError, KeyError) as _ext_err:
        logger.debug("[domain_fork] extended/fts lookup failed: %s", _ext_err)

    return None



