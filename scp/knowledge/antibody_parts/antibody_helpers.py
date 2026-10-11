"""Domain antibody helper mixins for DomainAntibodySystem."""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

from scp.interfaces.severity import Severity

if TYPE_CHECKING:
    from scp.knowledge.antibody_system import AntibodyResult


class PhilosophyAntibodyMixin:
    def verify_fallacy(self, result: AntibodyResult, question: str, answer: str, ab_name: str) -> bool:
        _check_ran = True
        # [Task 29-B] Detect logical fallacies in answer.
        # Flag common fallacy patterns by name + Vietnamese equivalent.
        FALLACIES = {
            # English
            "ad hominem": "attacking the person instead of the argument",
            "straw man": "misrepresenting opponent's argument",
            "strawman": "misrepresenting opponent's argument",
            "false dichotomy": "presenting only 2 options when more exist",
            "false dilemma": "presenting only 2 options when more exist",
            "slippery slope": "claiming chain reaction without evidence",
            "circular reasoning": "using conclusion as premise",
            "begging the question": "using conclusion as premise",
            "appeal to authority": "using authority as evidence",
            "appeal to emotion": "using emotion instead of evidence",
            "post hoc": "correlation ≠ causation",
            "red herring": "distracting from the argument",
            "tu quoque": "whataboutism / appeal to hypocrisy",
            # Vietnamese
            "ngụy biện": "fallacy detected (Vietnamese)",
            "ngụy biện cá nhân": "ad hominem (Vietnamese)",
            "ngụy biện rơm rạ": "straw man (Vietnamese)",
            "lập luận vòng lặp": "circular reasoning (Vietnamese)",
        }
        a_lower = answer.lower()
        for fallacy, desc in FALLACIES.items():
            if fallacy in a_lower:
                result.passed = False
                result.severity = "medium"
                result.confidence = 0.7
                result.details = (
                    f"Logical fallacy detected: '{fallacy}' ({desc}) — "
                    f"answer should identify and refute, not commit fallacies"
                )
                break
        if result.passed:
            result.details = "Fallacy check passed: no logical fallacies detected"
        return True


class PsychologyAntibodyMixin:
    def verify_cognitive_bias(self, result: AntibodyResult, question: str, answer: str, ab_name: str) -> bool:
        _check_ran = True
        # [Task 29-B] Detect cognitive biases in reasoning.
        # Bias acknowledgment is fine; using biased reasoning is not.
        BIASES = {
            "anchoring bias": "over-relying on first piece of information",
            "confirmation bias": "favoring info that confirms existing beliefs",
            "availability heuristic": "overweighting easily-recalled examples",
            "dunning-kruger": "low-ability overestimating competence",
            "survivorship bias": "focusing on survivors, ignoring failures",
            "sunk cost": "continuing because of past investment",
            "hindsight bias": "'I knew it all along' after the fact",
            "framing effect": "drawing different conclusions from same info",
            "bandwagon effect": "believing because others do",
            "halo effect": "one positive trait coloring overall judgment",
            # Vietnamese
            "thiên kiến": "bias (Vietnamese)",
            "thiên kiến xác nhận": "confirmation bias (Vietnamese)",
            "thiên kiến mỏ neo": "anchoring bias (Vietnamese)",
            "thiên lệch": "bias (Vietnamese)",
        }
        a_lower = answer.lower()
        for bias, desc in BIASES.items():
            if bias in a_lower:
                # Check if answer is acknowledging bias (good) vs committing it (bad)
                # Heuristic: if 'avoid', 'recognize', 'aware of', 'bias toward' near → acknowledgment
                bias_pos = a_lower.find(bias)
                window = a_lower[max(0, bias_pos - 40):bias_pos + len(bias) + 40]
                ack_markers = ["avoid", "recognize", "aware of", "fall victim",
                               "overcome", "bias toward", "tránh", "nhận thức",
                               "ý thức được"]
                if not any(ack in window for ack in ack_markers):
                    result.passed = False
                    result.severity = "low"
                    result.confidence = 0.6
                    result.details = (
                        f"Possible cognitive bias in reasoning: '{bias}' ({desc}) — "
                        f"answer uses biased framing without acknowledging it"
                    )
                    break
        if result.passed:
            result.details = "Cognitive bias check passed: no biased reasoning detected"
        return True


class AgricultureAntibodyMixin:
    def verify_crop_yield(self, result: AntibodyResult, question: str, answer: str, ab_name: str) -> bool:
        _check_ran = True
        # [Task 29-B] Verify crop yield claims — rice 5-10 t/ha normal.
        # >15 t/ha implausible (record is ~12 t/ha for hybrid rice).
        # <1 t/ha suspicious for major crops (would indicate failure).
        # Pattern: extract number near "tấn/ha", "t/ha", "tons/ha".
        a_lower = answer.lower()
        # Match patterns: "X tấn/ha", "X t/ha", "X tons per hectare", "X t/ha"
        yield_patterns = [
            r'(\d+(?:\.\d+)?)\s*(?:tấn|t|tons?)\s*(?:/|per)?\s*(?:ha|hectare)',
            r'(\d+(?:\.\d+)?)\s*(?:tạ)\s*(?:/|per)?\s*(?:ha|hectare)',  # 1 tạ = 0.1 t
        ]
        checked = 0
        for pattern in yield_patterns:
            for m in re.finditer(pattern, a_lower):
                val = float(m.group(1))
                # Convert tạ → tấn (1 tấn = 10 tạ)
                if 'tạ' in pattern:
                    val = val / 10.0
                checked += 1
                if val > 15:
                    result.passed = False
                    result.severity = "medium"
                    result.confidence = 0.75
                    result.details = (
                        f"Crop yield {val} t/ha is implausibly high "
                        f"(rice 5-10 t/ha normal; record ~12 t/ha for hybrid rice)"
                    )
                    break
                elif val < 1:
                    result.passed = False
                    result.severity = "low"
                    result.confidence = 0.6
                    result.details = (
                        f"Crop yield {val} t/ha is very low — "
                        f"verify if answer mentions crop failure or specific conditions"
                    )
                    break
            if not result.passed:
                break
        if result.passed:
            result.details = f"Crop yield check passed: {checked} yield(s) scanned"
        return True


class Earth_ScienceAntibodyMixin:
    def verify_earthquake_magnitude(self, result: AntibodyResult, question: str, answer: str, ab_name: str) -> bool:
        _check_ran = True
        # [Task 29-B] Verify earthquake magnitudes — 0-9 range, >9.5 implausible.
        # Largest recorded: 9.5 (Valdivia, Chile 1960).
        # Negative magnitude is impossible (Richter scale starts at 0).
        a_lower = answer.lower()
        mag_patterns = [
            r'magnitude\s*(?:of|was|is|:|=|scale)?\s*(\d+(?:\.\d+)?)',
            r'(\d+(?:\.\d+)?)\s*magnitude',
            r'mw\s*(?:of|was|is|:|=)?\s*(\d+(?:\.\d+)?)',
            r'ml\s*(?:of|was|is|:|=)?\s*(\d+(?:\.\d+)?)',
            r'richter\s*(?:scale)?\s*(?:of|was|is|:|=)?\s*(\d+(?:\.\d+)?)',
            r'độ lớn\s*(?:là|:|=)?\s*(\d+(?:\.\d+)?)',
        ]
        checked = 0
        for pattern in mag_patterns:
            for m in re.finditer(pattern, a_lower):
                val = float(m.group(1))
                checked += 1
                if val > 9.5:
                    result.passed = False
                    result.severity = "high"
                    result.confidence = 0.85
                    result.details = (
                        f"Earthquake magnitude {val} is implausible — "
                        f"largest recorded: 9.5 (Valdivia, Chile 1960)"
                    )
                    break
                elif val < 0:
                    result.passed = False
                    result.severity = "medium"
                    result.confidence = 0.8
                    result.details = (
                        f"Earthquake magnitude {val} is invalid — "
                        f"Richter scale starts at 0 (negative magnitudes impossible)"
                    )
                    break
                elif val > 8:
                    # Great earthquake — flag as low severity for verification
                    result.passed = False
                    result.severity = "low"
                    result.confidence = 0.55
                    result.details = (
                        f"Earthquake magnitude {val} is in 'great earthquake' range (>8.0) — "
                        f"verify against historical records (rare, ~1/year globally)"
                    )
                    break
            if not result.passed:
                break
        if result.passed:
            result.details = f"Earthquake magnitude check passed: {checked} magnitude(s) scanned"
        return True


class EngineeringAntibodyMixin:
    def verify_safety_factor(self, result: AntibodyResult, question: str, answer: str, ab_name: str) -> bool:
        _check_ran = True
        # [Task 30-C] Safety factor must be >1.0 for structural integrity.
        # <1.0 = failure risk (critical). >10 = over-engineered (suspicious).
        a_lower = answer.lower()
        sf_patterns = [
            r'safety\s+factor\s*(?:of|is|=|:|was)?\s*(\d+\.?\d*)',
            r'factor\s+of\s+safety\s*(?:of|is|=|:|was)?\s*(\d+\.?\d*)',
            r'fos\s*(?:of|is|=|:|was)?\s*(\d+\.?\d*)',
            r'hệ\s+số\s+an\s+toàn\s*(?:=|:|là)?\s*(\d+\.?\d*)',
            r'design\s+factor\s*(?:of|is|=|:|was)?\s*(\d+\.?\d*)',
        ]
        checked = 0
        for pattern in sf_patterns:
            for m in re.finditer(pattern, a_lower):
                val = float(m.group(1))
                checked += 1
                if val < 1.0:
                    result.passed = False
                    result.severity = Severity.CRITICAL  # [ROOT-FIX Task 38-A] was: "critical"
                    result.confidence = 0.95
                    result.details = (
                        f"Safety factor {val} < 1.0 — structural failure risk "
                        f"(load exceeds capacity)"
                    )
                    break
                elif val > 10:
                    result.passed = False
                    result.severity = Severity.MEDIUM  # [ROOT-FIX Task 38-A] was: "medium"
                    result.confidence = 0.7
                    result.details = (
                        f"Safety factor {val} > 10 — over-engineered (suspicious); "
                        f"typical FoS = 1.5-3 for most structures"
                    )
                    break
            if not result.passed:
                break
        if result.passed:
            result.details = f"Safety factor check passed: {checked} value(s) scanned"
        return True

    def verify_material_strength(self, result: AntibodyResult, question: str, answer: str, ab_name: str) -> bool:
        _check_ran = True
        # [Task 30-C] Material strength plausibility.
        a_lower = answer.lower()
        strength_patterns = [
            (r'(\d+(?:\.\d+)?)\s*gpa', 1000.0),       # GPa → MPa
            (r'(\d+(?:\.\d+)?)\s*ksi', 6.895),        # ksi → MPa
            (r'(\d+(?:\.\d+)?)\s*psi', 0.006895),     # psi → MPa
            (r'(\d+(?:\.\d+)?)\s*mpa', 1.0),          # MPa
        ]
        checked = 0
        for pattern, mult in strength_patterns:
            for m in re.finditer(pattern, a_lower):
                val_mpa = float(m.group(1)) * mult
                checked += 1
                if val_mpa > 5000:
                    result.passed = False
                    result.severity = "medium"
                    result.confidence = 0.75
                    result.details = (
                        f"Material strength {val_mpa:.1f} MPa is implausibly high "
                        f"(strongest steels ~2000 MPa; carbon nanotube ~63000 MPa is lab-only)"
                    )
                    break
                elif val_mpa < 10:
                    result.passed = False
                    result.severity = "low"
                    result.confidence = 0.6
                    result.details = (
                        f"Material strength {val_mpa:.1f} MPa is very low for structural use "
                        f"(<10 MPa; concrete ~20-40 MPa minimum)"
                    )
                    break
            if not result.passed:
                break
        if result.passed:
            result.details = f"Material strength check passed: {checked} value(s) scanned"
        return True


class ArtAntibodyMixin:
    def verify_art_period(self, result: AntibodyResult, question: str, answer: str, ab_name: str) -> bool:
        _check_ran = True
        # [Task 30-C] Verify artwork period matches artist's lifetime + style era.
        ARTIST_LIFESPAN = {
            "leonardo da vinci": (1452, 1519),
            "da vinci": (1452, 1519),
            "michelangelo": (1475, 1564),
            "raphael": (1483, 1520),
            "rembrandt": (1606, 1669),
            "vermeer": (1632, 1675),
            "vangogh": (1853, 1890), "van gogh": (1853, 1890),
            "monet": (1840, 1926),
            "renoir": (1841, 1919),
            "degas": (1834, 1917),
            "cezanne": (1839, 1906), "cézanne": (1839, 1906),
            "picasso": (1881, 1973),
            "matisse": (1869, 1954),
            "dali": (1904, 1989), "dalí": (1904, 1989),
            "klimt": (1862, 1918),
            "warhol": (1928, 1987),
        }
        STYLE_ERA = {
            "renaissance": (1400, 1600),
            "baroque": (1600, 1750),
            "rococo": (1700, 1780),
            "neoclassicism": (1760, 1840),
            "romanticism": (1780, 1850),
            "realism": (1840, 1880),
            "impressionism": (1860, 1890),
            "post-impressionism": (1880, 1910),
            "cubism": (1907, 1925),
            "surrealism": (1924, 1966),
            "modernism": (1860, 1970),
            "gothic": (1150, 1500),
        }
        a_lower = answer.lower()
        years = re.findall(r'\b(1[2-9]\d{2}|20\d{2})\b', answer)
        found_artist = None
        for artist, (birth, death) in ARTIST_LIFESPAN.items():
            if artist in a_lower:
                found_artist = (artist, birth, death)
                break
        found_style = None
        for style, (start, end) in STYLE_ERA.items():
            if style in a_lower:
                found_style = (style, start, end)
                break
        flagged = False
        for year_str in years:
            year = int(year_str)
            if found_artist:
                _, birth, death = found_artist
                if year < birth:
                    result.passed = False
                    result.severity = "high"
                    result.confidence = 0.85
                    result.details = (
                        f"Year {year} is before artist's birth ({birth}) — "
                        f"implausible artwork date"
                    )
                    flagged = True
                    break
                if year > death + 10:
                    result.passed = False
                    result.severity = "high"
                    result.confidence = 0.85
                    result.details = (
                        f"Year {year} is more than 10 years after artist's death ({death}) — "
                        f"implausible artwork date"
                    )
                    flagged = True
                    break
            if found_style:
                style_name, start, end = found_style
                start_i, end_i = int(start), int(end)
                if year < start_i - 5 or year > end_i + 5:
                    result.passed = False
                    result.severity = "medium"
                    result.confidence = 0.7
                    result.details = (
                        f"Year {year} doesn't match {style_name} era "
                        f"(approx. {start}-{end}) — verify attribution"
                    )
                    flagged = True
                    break
        if not flagged:
            result.details = (
                f"Art period check passed: {len(years)} year(s) scanned, "
                f"artist={found_artist[0] if found_artist else 'unknown'}, "
                f"style={found_style[0] if found_style else 'unknown'}"
            )
        return True


class MilitaryAntibodyMixin:
    def verify_weapon_range(self, result: AntibodyResult, question: str, answer: str, ab_name: str) -> bool:
        _check_ran = True
        # [Task 30-C] Weapon range plausibility by category.
        a_lower = answer.lower()
        category = None
        if any(w in a_lower for w in ["handgun", "pistol", "súng ngắn"]):
            category = ("handgun", 1.0, "km")
        elif any(w in a_lower for w in ["rifle", "súng trường", "sniper"]):
            category = ("rifle", 3.0, "km")
        elif any(w in a_lower for w in ["howitzer", "pháo", "artillery", "cannon"]):
            category = ("artillery", 100.0, "km")
        elif any(w in a_lower for w in ["sam", "surface-to-air", "air defense"]):
            category = ("SAM", 500.0, "km")
        elif any(w in a_lower for w in ["icbm", "intercontinental", "tên lửa liên lục địa"]):
            category = ("ICBM", 20000.0, "km")
        elif any(w in a_lower for w in ["missile", "tên lửa"]):
            category = ("missile", 10000.0, "km")
        range_patterns = [
            (r'(\d+(?:\.\d+)?)\s*km\b', 1.0, "km"),
            (r'(\d+(?:\.\d+)?)\s*kilometers?\b', 1.0, "km"),
            (r'(\d+(?:\.\d+)?)\s*(?:m|meters?)\b', 0.001, "km"),
            (r'(\d+(?:\.\d+)?)\s*(?:nm|nautical\s+miles?)\b', 1.852, "km"),
        ]
        checked = 0
        flagged = False
        for pattern, mult, _unit in range_patterns:
            for m in re.finditer(pattern, a_lower):
                val_km = float(m.group(1)) * mult
                start = max(0, m.start() - 40)
                end = min(len(a_lower), m.end() + 40)
                context = a_lower[start:end]
                if not any(kw in context for kw in
                           ["range", "tầm", "reach", "distance", "reach", "fired"]):
                    continue
                checked += 1
                if category:
                    cat_name, max_km, _ = category
                    if val_km > max_km:
                        result.passed = False
                        result.severity = "high"
                        result.confidence = 0.8
                        result.details = (
                            f"Weapon range {val_km} km exceeds {cat_name} typical max "
                            f"(~{max_km} km) — verify specification"
                        )
                        flagged = True
                        break
            if flagged:
                break
        if not flagged:
            result.details = (
                f"Weapon range check passed: {checked} range(s) scanned"
                + (f", category={category[0]}" if category else "")
            )
        return True


class EnvironmentalAntibodyMixin:
    def verify_carbon_emission(self, result: AntibodyResult, question: str, answer: str, ab_name: str) -> bool:
        _check_ran = True
        # [Task 30-C] Carbon emission plausibility.
        a_lower = answer.lower()
        is_per_capita = any(kw in a_lower for kw in
                            ["per capita", "per person", "người", "mỗi người"])
        unit_patterns = [
            (r'(\d+(?:,\d{3})*(?:\.\d+)?)\s*(?:mt|megaton)', "Mt"),
            (r'(\d+(?:,\d{3})*(?:\.\d+)?)\s*(?:gt|gigaton)', "Gt"),
            (r'(\d+(?:,\d{3})*(?:\.\d+)?)\s*(?:kt|kiloton)', "kt"),
            (r'(\d+(?:,\d{3})*(?:\.\d+)?)\s*tons?\s*(?:co2|carbon)', "tons"),
            (r'(\d+(?:,\d{3})*(?:\.\d+)?)\s*(?:tấn)\s*(?:co2|carbon)', "tons"),
            (r'(\d+(?:,\d{3})*(?:\.\d+)?)\s*kg\s*(?:co2|carbon)', "kg"),
        ]
        checked = 0
        flagged = False
        for pattern, unit in unit_patterns:
            for m in re.finditer(pattern, a_lower, re.IGNORECASE):
                raw = m.group(1).replace(",", "")
                val = float(raw)
                checked += 1
                if unit == "Gt":
                    val_mt = val * 1000
                elif unit == "kt":
                    val_mt = val / 1000
                elif unit == "tons":
                    if is_per_capita:
                        if val > 100:
                            result.passed = False
                            result.severity = "high"
                            result.confidence = 0.85
                            result.details = (
                                f"Per capita CO2 emission {val} tons/yr is implausible "
                                f"(global highest: Qatar ~37 tons/yr)"
                            )
                            flagged = True
                            break
                        continue
                    else:
                        val_mt = val / 1_000_000
                elif unit == "kg":
                    if is_per_capita:
                        val_tons = val / 1000
                        if val_tons > 100:
                            result.passed = False
                            result.severity = "high"
                            result.confidence = 0.85
                            result.details = (
                                f"Per capita CO2 emission {val_tons:.1f} tons/yr is implausible "
                                f"(global highest: Qatar ~37 tons/yr)"
                            )
                            flagged = True
                            break
                    continue
                else:
                    val_mt = val
                if not is_per_capita and val_mt > 50000:
                    result.passed = False
                    result.severity = "high"
                    result.confidence = 0.85
                    result.details = (
                        f"CO2 emission {val_mt:.0f} Mt is implausible — "
                        f"global total ~37000 Mt/yr; verify unit/context"
                    )
                    flagged = True
                    break
            if flagged:
                break
        if not flagged:
            result.details = (
                f"Carbon emission check passed: {checked} value(s) scanned"
                + (", per capita context" if is_per_capita else "")
            )
        return True


class EducationAntibodyMixin:
    def verify_pedagogy(self, result: AntibodyResult, question: str, answer: str, ab_name: str) -> bool:
        _check_ran = True
        # [Task 30-C] Verify pedagogical theory claims.
        a_lower = answer.lower()
        flagged = False
        PIAGET_STAGES = {
            "sensorimotor": (0, 2),
            "preoperational": (2, 7),
            "concrete operational": (7, 11),
            "formal operational": (11, 100),
        }
        if "piaget" in a_lower:
            for stage, (lo, hi) in PIAGET_STAGES.items():
                if stage in a_lower:
                    stage_pos = a_lower.find(stage)
                    window = a_lower[max(0, stage_pos - 60):stage_pos + len(stage) + 60]
                    ages = re.findall(r'(\d+)\s*(?:-|\s+to\s+)\s*(\d+)', window)
                    for age_lo, age_hi in ages:
                        age_lo, age_hi = int(age_lo), int(age_hi)
                        if age_lo != lo or age_hi != hi:
                            result.passed = False
                            result.severity = "medium"
                            result.confidence = 0.8
                            result.details = (
                                f"Piaget '{stage}' stage is ages {lo}-{hi}, "
                                f"but answer claims {age_lo}-{age_hi}"
                            )
                            flagged = True
                            break
                    if flagged:
                        break
            if not flagged:
                stage_count_claim = re.search(
                    r'(\d+)\s*(?:stages?|giai đoạn)', a_lower)
                if stage_count_claim:
                    claimed = int(stage_count_claim.group(1))
                    if "piaget" in a_lower and claimed != 4:
                        result.passed = False
                        result.severity = "medium"
                        result.confidence = 0.8
                        result.details = (
                            f"Piaget has 4 stages, but answer claims {claimed}"
                        )
                        flagged = True
        if not flagged and "bloom" in a_lower:
            BLOOM_LEVELS = {"remember", "understand", "apply", "analyze",
                            "evaluate", "create"}
            [lvl for lvl in BLOOM_LEVELS if lvl in a_lower]
            count_claim = re.search(r'(\d+)\s*(?:levels?|cấp độ|tầng)', a_lower)
            if count_claim:
                claimed = int(count_claim.group(1))
                if claimed != 6:
                    result.passed = False
                    result.severity = "medium"
                    result.confidence = 0.8
                    result.details = (
                        f"Bloom's revised taxonomy has 6 levels, "
                        f"but answer claims {claimed}"
                    )
                    flagged = True
        if not flagged and "vygotsky" in a_lower:
            if "zpd" in a_lower or "zone of proximal development" in a_lower:
                zpd_pos = max(a_lower.find("zpd"),
                              a_lower.find("zone of proximal development"))
                if zpd_pos >= 0:
                    window = a_lower[max(0, zpd_pos - 60):zpd_pos + 100]
                    if any(wrong in window for wrong in
                           ["fixed ability", "innate ability",
                            " IQ ", "intelligence quotient"]):
                        result.passed = False
                        result.severity = "medium"
                        result.confidence = 0.75
                        result.details = (
                            "Vygotsky ZPD is the gap between what a learner can do "
                            "alone vs with guidance — not a fixed ability or IQ measure"
                        )
                        flagged = True
        if not flagged:
            result.details = "Pedagogy check passed: no misattributed theory claims"
        return True
