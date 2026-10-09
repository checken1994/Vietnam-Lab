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

import logging
import re
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
        from scp.data_sources.astronomy import AstronomyDataSource
        _astronomy_source = AstronomyDataSource()
    return _astronomy_source


def _get_chemistry() -> Any:
    global _chemistry_source
    if _chemistry_source is None:
        from scp.data_sources.chemistry import ChemistryDataSource
        _chemistry_source = ChemistryDataSource()
    return _chemistry_source


def _get_physics() -> Any:
    global _physics_source
    if _physics_source is None:
        from scp.data_sources.physics import PhysicsDataSource
        _physics_source = PhysicsDataSource()
    return _physics_source


def _get_geography() -> Any:
    global _geography_source
    if _geography_source is None:
        from scp.data_sources.geography import GeographyDataSource
        _geography_source = GeographyDataSource()
    return _geography_source


def _get_math() -> Any:
    global _math_source
    if _math_source is None:
        from scp.data_sources.math import MathDataSource
        _math_source = MathDataSource()
    return _math_source


# ---------------------------------------------------------------------------
# 1. Thiên văn học (Astronomy)
# ---------------------------------------------------------------------------
_PLANET_NAMES_VI_EN = {
    "sao thủy": "mercury", "mercury": "mercury",
    "sao kim": "venus", "venus": "venus",
    "trái đất": "earth", "earth": "earth",
    "sao hỏa": "mars", "mars": "mars",
    "sao mộc": "jupiter", "jupiter": "jupiter",
    "sao thổ": "saturn", "saturn": "saturn",
    "sao thiên vương": "uranus", "uranus": "uranus",
    "sao hải vương": "neptune", "neptune": "neptune",
    "mặt trời": "sun", "sun": "sun",
    "mặt trăng": "moon", "moon": "moon",
}


def _resolve_astronomy(question_lower: str) -> dict[str, Any] | None:
    try:
        astro = _get_astronomy()
        # Multi-planet queries cannot be resolved by single-planet static lookup -> fail-closed
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

        # Hằng số thiên văn
        if ("tuổi" in question_lower and "vũ trụ" in question_lower) or any(k in question_lower for k in ["age of universe", "age of the universe"]):
            text = "Tuổi của vũ trụ được ước tính là khoảng 13.8 tỷ năm (Planck 2018)."
            return {
                "text": text,
                "api_name": "AstronomyDataSource (Local Planck 2018)",
                "api_url": "local:scp/data_sources/astronomy.py",
                "evidence": text,
            }
        if any(k in question_lower for k in ["hằng số hubble", "hubble constant"]):
            text = "Hằng số Hubble (H0) hiện tại có giá trị xấp xỉ 67.4 km/s/Mpc theo dữ liệu đo đạc của Planck."
            return {
                "text": text,
                "api_name": "AstronomyDataSource (Local)",
                "api_url": "local:scp/data_sources/astronomy.py",
                "evidence": text,
            }
    except Exception as exc:
        logger.debug("[domain_fork] astronomy lookup error: %s", exc, exc_info=True)
    return None


# ---------------------------------------------------------------------------
# 2. Hóa học (Chemistry)
# ---------------------------------------------------------------------------
_COMMON_COMPOUNDS = {
    "h2o": "H2O", "nước": "H2O", "water": "H2O",
    "co2": "CO2", "khí cacbonic": "CO2", "carbon dioxide": "CO2",
    "o2": "O2", "oxy": "O2", "oxygen": "O2",
    "h2so4": "H2SO4", "axit sunfuric": "H2SO4", "sulfuric acid": "H2SO4",
    "hcl": "HCl", "axit clohidric": "HCl", "hydrochloric acid": "HCl",
    "nacl": "NaCl", "muối ăn": "NaCl", "sodium chloride": "NaCl",
    "c6h12o6": "C6H12O6", "glucose": "C6H12O6",
    "ch4": "CH4", "metan": "CH4", "methane": "CH4",
    "c2h5oh": "C2H5OH", "ethanol": "C2H5OH", "rượu": "C2H5OH",
    "naoh": "NaOH", "xút": "NaOH", "sodium hydroxide": "NaOH",
    "caco3": "CaCO3", "đá vôi": "CaCO3", "calcium carbonate": "CaCO3",
    "nh3": "NH3", "amoniac": "NH3", "ammonia": "NH3",
}

_COMMON_ELEMENTS = {
    "fe": "Fe", "sắt": "Fe", "iron": "Fe",
    "cu": "Cu", "đồng": "Cu", "copper": "Cu",
    "au": "Au", "vàng": "Au", "gold": "Au",
    "ag": "Ag", "bạc": "Ag", "silver": "Ag",
    "al": "Al", "nhôm": "Al", "aluminum": "Al",
    "na": "Na", "natri": "Na", "sodium": "Na",
    "ca": "Ca", "canxi": "Ca", "calcium": "Ca",
    "k": "K", "kali": "K", "potassium": "K",
    "cl": "Cl", "clo": "Cl", "chlorine": "Cl",
    "he": "He", "heli": "He", "helium": "He",
    "c": "C", "cacbon": "C", "carbon": "C",
    "n": "N", "nitơ": "N", "nitrogen": "N",
    "o": "O", "oxi": "O",
    "h": "H", "hiđro": "H", "hydro": "H", "hydrogen": "H",
}


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
_PHYSICS_CONSTANTS = {
    "tốc độ ánh sáng": ("Tốc độ ánh sáng trong chân không (c)", "299,792,458 m/s"),
    "speed of light": ("Speed of light in vacuum (c)", "299,792,458 m/s"),
    "hằng số planck": ("Hằng số Planck (h)", "6.62607015 × 10^-34 J·s"),
    "planck constant": ("Planck constant (h)", "6.62607015 × 10^-34 J·s"),
    "số avogadro": ("Hằng số Avogadro (N_A)", "6.02214076 × 10^23 mol^-1"),
    "avogadro constant": ("Avogadro constant (N_A)", "6.02214076 × 10^23 mol^-1"),
    "hằng số hấp dẫn": ("Hằng số hấp dẫn Newton (G)", "6.67430 × 10^-11 m^3·kg^-1·s^-2"),
    "gravitational constant": ("Gravitational constant (G)", "6.67430 × 10^-11 m^3·kg^-1·s^-2"),
    "gia tốc trọng trường": ("Gia tốc trọng trường tiêu chuẩn (g)", "9.80665 m/s^2"),
    "standard gravity": ("Standard gravity (g)", "9.80665 m/s^2"),
    "điện tích nguyên tố": ("Điện tích nguyên tố (e)", "1.602176634 × 10^-19 C"),
    "elementary charge": ("Elementary charge (e)", "1.602176634 × 10^-19 C"),
}


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
_CAPITALS_TABLE = {
    "việt nam": ("Hà Nội", "Việt Nam"),
    "vietnam": ("Hà Nội", "Việt Nam"),
    "pháp": ("Paris", "Pháp"),
    "france": ("Paris", "France"),
    "nhật bản": ("Tokyo", "Nhật Bản"),
    "japan": ("Tokyo", "Japan"),
    "hàn quốc": ("Seoul", "Hàn Quốc"),
    "south korea": ("Seoul", "South Korea"),
    "mỹ": ("Washington, D.C.", "Hoa Kỳ"),
    "hoa kỳ": ("Washington, D.C.", "Hoa Kỳ"),
    "united states": ("Washington, D.C.", "United States"),
    "usa": ("Washington, D.C.", "United States"),
    "anh": ("London", "Vương quốc Anh"),
    "united kingdom": ("London", "United Kingdom"),
    "đức": ("Berlin", "Đức"),
    "germany": ("Berlin", "Germany"),
    "ý": ("Roma", "Ý"),
    "italy": ("Rome", "Italy"),
    "nga": ("Moskva (Moscow)", "Nga"),
    "russia": ("Moscow", "Russia"),
    "trung quốc": ("Bắc Kinh", "Trung Quốc"),
    "china": ("Beijing", "China"),
    "úc": ("Canberra", "Úc"),
    "australia": ("Canberra", "Australia"),
    "thái lan": ("Bangkok", "Thái Lan"),
    "thailand": ("Bangkok", "Thailand"),
    "singapore": ("Singapore", "Singapore"),
    "lào": ("Viêng Chăn", "Lào"),
    "campuchia": ("Phnôm Pênh", "Campuchia"),
    "cambodia": ("Phnom Penh", "Cambodia"),
    "indonesia": ("Jakarta (Nusantara)", "Indonesia"),
    "malaysia": ("Kuala Lumpur", "Malaysia"),
    "philippines": ("Manila", "Philippines"),
    "canada": ("Ottawa", "Canada"),
}


def _resolve_geography(question_lower: str) -> dict[str, Any] | None:
    try:
        # Câu hỏi về thủ đô (yêu cầu 'thủ đô của ' tường minh để không nuốt test catalog/non-conversion)
        if "thủ đô của " in question_lower:
            if any(k in question_lower for k in ["nước nào", "quốc gia nào"]):
                return None
            for country_key, (capital, country_name) in _CAPITALS_TABLE.items():
                if country_key == "ý" and re.search(r"\bý\s+(nghĩa|kiến|định|niệm|thức|chí|tưởng|đồ|muốn|nguyện|tứ|dân|trời|học|vị|nguyên|hợp|bảo|tình)\b", question_lower):
                    continue
                if country_key == "nga" and any(k in question_lower for k in ["thiên nga", "hằng nga"]):
                    continue
                if country_key == "anh" and re.search(r"\banh\s+(trai|em|chị|họ|rể|bạn|ấy|ta)\b", question_lower):
                    continue
                if country_key == "đức" and re.search(r"(\bđạo\s+đức\b|\bđức\s+(tính|tin|hạnh|độ)\b|\b(phúc|công)\s+đức\b)", question_lower):
                    continue
                if country_key == "mỹ" and re.search(r"(\bthẩm\s+mỹ\b|\bmỹ\s+(thuật|phẩm|nhân|cảm|vị|miều|lệ|học)\b)", question_lower):
                    continue
                if country_key == "lào" and re.search(r"(\b(thuốc|gió|dép)\s+lào\b)", question_lower):
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
_MATH_CONSTANTS = {
    "số pi": ("Số Pi (π)", "3.141592653589793"),
    "pi value": ("Số Pi (π)", "3.141592653589793"),
    "số e": ("Số Euler (e)", "2.718281828459045"),
    "euler number": ("Số Euler (e)", "2.718281828459045"),
    "tỷ lệ vàng": ("Tỷ lệ vàng (Phi - φ)", "1.618033988749895"),
    "golden ratio": ("Tỷ lệ vàng (Phi - φ)", "1.618033988749895"),
}


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
        # Check historical notable quakes offline first
        if "valdivia" in question_lower or ("động đất" in question_lower and "mạnh nhất" in question_lower):
            text = "Trận động đất mạnh nhất lịch sử ghi nhận được là trận động đất Valdivia (Chile) năm 1960 với độ lớn M9.5."
            return {
                "text": text,
                "api_name": "USGSDataSource (Historical Catalog)",
                "api_url": "https://earthquake.usgs.gov/earthquakes/browse/largest-world.php",
                "evidence": text,
            }
        if "sumatra" in question_lower or ("động đất" in question_lower and "2004" in question_lower):
            text = "Trận động đất và sóng thần Ấn Độ Dương (Sumatra) năm 2004 có độ lớn M9.1-9.3."
            return {
                "text": text,
                "api_name": "USGSDataSource (Historical Catalog)",
                "api_url": "https://earthquake.usgs.gov/",
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


_AMINO_ACID_NAMES_VI = {
    "Ala": ("Alanine", "Alanin"),
    "Arg": ("Arginine", "Arginin"),
    "Asn": ("Asparagine", "Asparagin"),
    "Asp": ("Aspartic acid", "Axit aspartic"),
    "Cys": ("Cysteine", "Cystein"),
    "Gln": ("Glutamine", "Glutamin"),
    "Glu": ("Glutamic acid", "Axit glutamic"),
    "Gly": ("Glycine", "Glycin"),
    "His": ("Histidine", "Histidin"),
    "Ile": ("Isoleucine", "Isoleucin"),
    "Leu": ("Leucine", "Leucin"),
    "Lys": ("Lysine", "Lysin"),
    "Met": ("Methionine", "Methionin"),
    "Phe": ("Phenylalanine", "Phenylalanin"),
    "Pro": ("Proline", "Prolin"),
    "Ser": ("Serine", "Serin"),
    "Thr": ("Threonine", "Threonin"),
    "Trp": ("Tryptophan", "Tryptophan"),
    "Tyr": ("Tyrosine", "Tyrosin"),
    "Val": ("Valine", "Valin"),
}


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

        # Hằng số sinh học
        if any(k in question_lower for k in ["nhiễm sắc thể", "chromosome"]) and any(k in question_lower for k in ["người", "human"]):
            text = "Bộ nhiễm sắc thể lưỡng bội của loài người (Homo sapiens) bình thường có 46 nhiễm sắc thể (23 cặp)."
            return {
                "text": text,
                "api_name": "BiologyDataSource (Local Genome DB)",
                "api_url": "local:scp/data_sources/biology.py",
                "evidence": text,
            }
        if any(k in question_lower for k in ["bộ gen", "genome size", "kích thước gen"]) and any(k in question_lower for k in ["người", "human"]):
            text = "Kích thước bộ gen đơn bội của con người có khoảng 3.2 tỷ cặp base (3.2 × 10^9 base pairs)."
            return {
                "text": text,
                "api_name": "BiologyDataSource (Local Genome DB)",
                "api_url": "local:scp/data_sources/biology.py",
                "evidence": text,
            }

        # NCBI taxonomy lookup fallback
        if any(k in question_lower for k in ["loài", "chi", "species", "genus", "phân loại học"]):
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


_MOHS_SCALE = {
    "talc": (1, "Bột tan (Talc)", "Khoáng vật mềm nhất trong thang Mohs"),
    "thạch cao": (2, "Thạch cao (Gypsum)", "Độ cứng 2 Mohs, dễ cào bằng móng tay"),
    "gypsum": (2, "Gypsum", "Hardness 2 Mohs"),
    "canxit": (3, "Canxit (Calcite)", "Độ cứng 3 Mohs, thành phần chính của đá vôi"),
    "calcite": (3, "Calcite", "Hardness 3 Mohs"),
    "flurit": (4, "Flurit (Fluorite)", "Độ cứng 4 Mohs"),
    "fluorite": (4, "Fluorite", "Hardness 4 Mohs"),
    "apatit": (5, "Apatit (Apatite)", "Độ cứng 5 Mohs"),
    "apatite": (5, "Apatite", "Hardness 5 Mohs"),
    "fenspat": (6, "Fenspat (Orthoclase)", "Độ cứng 6 Mohs, chiếm phần lớn vỏ Trái Đất"),
    "feldspar": (6, "Feldspar", "Hardness 6 Mohs, 60% of Earth's crust"),
    "thạch anh": (7, "Thạch anh (Quartz)", "Độ cứng 7 Mohs, khoáng vật phổ biến thứ hai"),
    "quartz": (7, "Quartz", "Hardness 7 Mohs"),
    "topaz": (8, "Tôpaz (Topaz)", "Độ cứng 8 Mohs"),
    "corundum": (9, "Corindon (Corundum / Ruby, Sapphire)", "Độ cứng 9 Mohs, chỉ sau kim cương"),
    "kim cương": (10, "Kim cương (Diamond)", "Độ cứng 10 Mohs, khoáng vật tự nhiên cứng nhất"),
    "diamond": (10, "Diamond", "Hardness 10 Mohs, carbon, hardest natural mineral"),
}

_EARTH_LAYERS = {
    "vỏ trái đất": "Vỏ Trái Đất (Crust): Độ dày từ 5 km (dưới đại dương) đến 70 km (dưới lục địa), chiếm khoảng 1% thể tích Trái Đất.",
    "manti": "Lớp Manti (Mantle): Độ dày khoảng 2,900 km, chiếm khoảng 84% thể tích Trái Đất, cấu tạo chủ yếu từ đá silicat giàu sắt và magie.",
    "mantle": "Lớp Manti (Mantle): Độ dày khoảng 2,900 km, chiếm khoảng 84% thể tích Trái Đất, cấu tạo chủ yếu từ đá silicat giàu sắt và magie.",
    "lõi ngoài": "Lõi ngoài (Outer Core): Độ dày khoảng 2,200 km, kim loại nóng chảy (Fe-Ni), chuyển động tạo ra từ trường Trái Đất.",
    "outer core": "Lõi ngoài (Outer Core): Độ dày khoảng 2,200 km, kim loại nóng chảy (Fe-Ni), chuyển động tạo ra từ trường Trái Đất.",
    "lõi trong": "Lõi trong (Inner Core): Bán kính khoảng 1,220 km, kim loại rắn (Fe-Ni) với nhiệt độ khoảng 5,400°C.",
    "inner core": "Lõi trong (Inner Core): Bán kính khoảng 1,220 km, kim loại rắn (Fe-Ni) với nhiệt độ khoảng 5,400°C.",
}


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

        # Loại đá
        if any(k in question_lower for k in ["đá magma", "hỏa sinh", "igneous"]):
            text = "Đá magma (nham thạch) hình thành từ sự nguội lạnh của magma nóng chảy; các loại tiêu biểu gồm đá bazan (basalt) và đá hoa cương (granite)."
            return {
                "text": text,
                "api_name": "GeologyDataSource (Petrology DB)",
                "api_url": "local:scp/data_sources/geology.py",
                "evidence": text,
            }
        if any(k in question_lower for k in ["đá trầm tích", "sedimentary"]):
            text = "Đá trầm tích hình thành do sự tích tụ và nén chặt của trầm tích theo thời gian; các loại tiêu biểu gồm đá vôi (limestone) và đá sa thạch (sandstone)."
            return {
                "text": text,
                "api_name": "GeologyDataSource (Petrology DB)",
                "api_url": "local:scp/data_sources/geology.py",
                "evidence": text,
            }
        if any(k in question_lower for k in ["đá biến chất", "metamorphic"]):
            text = "Đá biến chất hình thành khi đá có sẵn bị biến đổi bởi nhiệt độ và áp suất cao; các loại tiêu biểu gồm đá cẩm thạch (marble, biến chất từ đá vôi) và đá phiến (slate)."
            return {
                "text": text,
                "api_name": "GeologyDataSource (Petrology DB)",
                "api_url": "local:scp/data_sources/geology.py",
                "evidence": text,
            }
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

    return None

