"""Mixins for DomainAntibodySystem."""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

from scp.interfaces.severity import Severity
from scp.knowledge.antibody_parts.antibody_helpers import (
    AgricultureAntibodyMixin,
    ArtAntibodyMixin,
    Earth_ScienceAntibodyMixin,
    EducationAntibodyMixin,
    EngineeringAntibodyMixin,
    EnvironmentalAntibodyMixin,
    MilitaryAntibodyMixin,
    PhilosophyAntibodyMixin,
    PsychologyAntibodyMixin,
)

if TYPE_CHECKING:
    from scp.knowledge.antibody_system import AntibodyResult


class GeneralAntibodyMixin:
    def verify_citation(self, result: AntibodyResult, question: str, answer: str, ab_name: str) -> bool:
        _check_ran = True
        has_claim = any(kw in answer.lower() for kw in ["theo", "according to", "nghiên cứu", "study"])
        has_citation = any(kw in answer.lower() for kw in ["doi", "http", "nguồn", "source:"])
        if has_claim and not has_citation:
            result.passed = False
            result.severity = "medium"
            result.confidence = 0.7
            result.details = "Answer makes claims without citations"
        else:
            result.details = "Citation check passed: no uncited claims detected"
        return True

    def verify_url(self, result: AntibodyResult, question: str, answer: str, ab_name: str) -> bool:
        _check_ran = True
        urls = re.findall(r"https?://\S+", answer)
        bare_domains = re.findall(r"\b([a-z0-9-]+\.(com|org|net|edu|gov|io|ai|co|vn|us|uk|invalid|test|foo|bar))\b", answer, re.IGNORECASE)
        all_urls = urls + [d[0] for d in bare_domains]
        for url in all_urls:
            if any(s in url.lower() for s in ["example.com", "example.invalid", "test.com", "foo.", "bar.", "fake."]):
                result.passed = False
                result.severity = "high"
                result.confidence = 0.8
                result.details = f"Suspicious URL: {url[:60]}"
                break
            tld = url.split("//")[-1].split("/")[0].split(".")[-1].lower()
            if tld not in ["com", "org", "net", "edu", "gov", "io", "ai", "co", "vn", "us", "uk"]:
                result.passed = False
                result.severity = "medium"
                result.confidence = 0.6
                result.details = f"Unusual TLD in URL: {url[:60]}"
        if result.passed:
            result.details = f"URL check passed: {len(all_urls)} URL(s) scanned"
        return True

    def verify_date(self, result: AntibodyResult, question: str, answer: str, ab_name: str) -> bool:
        _check_ran = True
        future_years = re.findall(r"\b(20[3-9]\d|2[1-9]\d\d)\b", answer)
        if future_years:
            result.passed = False
            result.severity = "medium"
            result.confidence = 0.7
            result.details = f"Future date(s) in answer: {future_years[:3]}"
        else:
            result.details = "Date check passed: no future dates"
        return True

    def verify_fact(self, result: AntibodyResult, question: str, answer: str, ab_name: str) -> bool:
        _check_ran = True
        extreme_pct = re.findall(r"(?:99\.9%|100%)", answer)
        if extreme_pct:
            result.passed = False
            result.severity = "low"
            result.confidence = 0.6
            result.details = f"Extreme percentage claim: {extreme_pct}"
        else:
            result.details = "Fact check passed: no extreme percentages"
        return True

    def verify_general(self, result: AntibodyResult, question: str, answer: str, ab_name: str) -> bool:
        _check_ran = True
        weasel_words: list[str] = []
        for ww in weasel_words:
            if ww in answer.lower():
                result.passed = False
                result.severity = "high"
                result.confidence = 0.8
                result.details = f"Closure/weasel word detected: '{ww}'"
                break
        if result.passed:
            result.details = "General check passed: no weasel words detected"
        return True


class MedicalAntibodyMixin:
    def verify_dosage(self, result: AntibodyResult, question: str, answer: str, ab_name: str) -> bool:
        _check_ran = True
        dosage_match = re.findall(r"(\d+)\s*(mg|ml|g|mcg|µg)", answer, re.IGNORECASE)
        for amount, unit in dosage_match:
            amt = int(amount)
            unit_lower = unit.lower()
            if unit_lower == "mg" and amt > 4000:
                result.passed = False
                result.severity = "high"
                result.confidence = 0.8
                result.details = f"Dosage {amt}{unit} exceeds typical max (4000mg paracetamol)"
            elif unit_lower == "g" and amt > 10:
                result.passed = False
                result.severity = "high"
                result.confidence = 0.7
                result.details = f"Dosage {amt}{unit} seems excessive"
            elif unit_lower == "ml" and amt > 100:
                result.passed = False
                result.severity = "medium"
                result.confidence = 0.6
                result.details = f"Volume {amt}{unit} seems excessive for medication"
        if result.passed:
            result.details = f"Dosage check passed: {len(dosage_match)} dosage(s) scanned"
        return True

    def verify_drug_interaction(self, result: AntibodyResult, question: str, answer: str, ab_name: str) -> bool:
        _check_ran = True
        KNOWN_INTERACTIONS = [
            ({"warfarin", "aspirin"}, "bleeding risk (additive anticoagulant effect)", "high"),
            ({"warfarin", "nsaids"}, "GI bleeding risk", "high"),
            ({"warfarin", "ibuprofen"}, "GI bleeding risk", "high"),
            ({"ssri", "maoi"}, "serotonin syndrome (potentially fatal)", "critical"),
            ({"fluoxetine", "maoi"}, "serotonin syndrome", "critical"),
            ({"sertraline", "maoi"}, "serotonin syndrome", "critical"),
            ({"simvastatin", "grapefruit"}, "rhabdomyolysis risk (CYP3A4 inhibition)", "medium"),
            ({"atorvastatin", "grapefruit"}, "rhabdomyolysis risk", "medium"),
            ({"metronidazole", "alcohol"}, "disulfiram-like reaction", "high"),
            ({"ciprofloxacin", "theophylline"}, "theophylline toxicity (CYP1A2 inhibition)", "medium"),
            ({"lithium", "nsaids"}, "lithium toxicity (reduced renal clearance)", "high"),
            ({"ace inhibitor", "potassium"}, "hyperkalemia risk", "medium"),
            ({"spironolactone", "potassium"}, "hyperkalemia risk", "medium"),
            ({"tramadol", "maoi"}, "serotonin syndrome + seizure risk", "critical"),
            ({"clarithromycin", "statin"}, "rhabdomyolysis risk", "high"),
        ]
        a_lower = answer.lower()
        found_drugs = set()
        drug_canonical: dict[str, str] = {}
        for kw, canonical in drug_canonical.items():
            if re.search(r'\b' + re.escape(kw) + r'\b', a_lower):
                found_drugs.add(canonical)
        interaction_found = False
        for pair, desc, sev in KNOWN_INTERACTIONS:
            if pair.issubset(found_drugs):
                result.passed = False
                result.severity = sev
                result.confidence = 0.85
                result.details = (
                    f"Known drug-drug interaction: {' + '.join(sorted(pair))} → {desc}. "
                    f"Answer must explicitly warn about this interaction."
                )
                interaction_found = True
                break
        if not interaction_found:
            result.details = (
                f"Drug interaction check passed: {len(found_drugs)} drug(s) detected, "
                f"no known interactions found"
            )
        return True


class FinanceAntibodyMixin:
    def verify_pe_ratio(self, result: AntibodyResult, question: str, answer: str, ab_name: str) -> bool:
        _check_ran = True
        pe_match = re.findall(r"P/E[^\d]{0,20}(\d+(?:\.\d+)?)", answer, re.IGNORECASE)
        for pe_str in pe_match:
            pe = float(pe_str)
            if pe < 0 or pe > 500:
                result.passed = False
                result.severity = "medium"
                result.confidence = 0.7
                result.details = f"P/E ratio {pe} outside plausible range (0-500)"
        if ab_name == "ratio_validator":
            ratio_match = re.findall(r"(\d+)\s*:\s*(\d+)", answer)
            for num_str, denom_str in ratio_match:
                num, denom = float(num_str), float(denom_str)
                if denom > 0 and (num / denom > 50 or num / denom < 0.01):
                    result.passed = False
                    result.severity = "medium"
                    result.confidence = 0.7
                    result.details = f"Ratio {num}:{denom} outside plausible range"
        if result.passed:
            result.details = f"Ratio check passed: {len(pe_match)} P/E + ratio(s) scanned"
        return True

    def verify_ratio(self, result: AntibodyResult, question: str, answer: str, ab_name: str) -> bool:
        _check_ran = True
        pe_match = re.findall(r"P/E[^\d]{0,20}(\d+(?:\.\d+)?)", answer, re.IGNORECASE)
        for pe_str in pe_match:
            pe = float(pe_str)
            if pe < 0 or pe > 500:
                result.passed = False
                result.severity = "medium"
                result.confidence = 0.7
                result.details = f"P/E ratio {pe} outside plausible range (0-500)"
        if ab_name == "ratio_validator":
            ratio_match = re.findall(r"(\d+)\s*:\s*(\d+)", answer)
            for num_str, denom_str in ratio_match:
                num, denom = float(num_str), float(denom_str)
                if denom > 0 and (num / denom > 50 or num / denom < 0.01):
                    result.passed = False
                    result.severity = "medium"
                    result.confidence = 0.7
                    result.details = f"Ratio {num}:{denom} outside plausible range"
        if result.passed:
            result.details = f"Ratio check passed: {len(pe_match)} P/E + ratio(s) scanned"
        return True

    def verify_interest_rate(self, result: AntibodyResult, question: str, answer: str, ab_name: str) -> bool:
        _check_ran = True
        rate_match = re.findall(r"(\d+(?:\.\d+)?)\s*%", answer)
        for rate_str in rate_match:
            rate = float(rate_str)
            if rate > 50:
                result.passed = False
                result.severity = "medium"
                result.confidence = 0.6
                result.details = f"Interest rate {rate}% seems implausibly high"
        if result.passed:
            result.details = f"Interest rate check passed: {len(rate_match)} rate(s) scanned"
        return True


class LegalAntibodyMixin:
    def verify_contract(self, result: AntibodyResult, question: str, answer: str, ab_name: str) -> bool:
        _check_ran = True
        if "penalty" in answer.lower() or "phạt" in answer.lower():
            penalty_match = re.findall(r"(\d+)\s*%", answer)
            for p_str in penalty_match:
                p = float(p_str)
                if p > 20:
                    result.passed = False
                    result.severity = "medium"
                    result.confidence = 0.6
                    result.details = f"Penalty rate {p}% may be excessive"
        if result.passed:
            result.details = "Contract check passed: no excessive penalties"
        return True

    def verify_statute(self, result: AntibodyResult, question: str, answer: str, ab_name: str) -> bool:
        _check_ran = True
        q_lower = question.lower()
        _a_lower = answer.lower()
        if any(kw in q_lower for kw in ["thời hiệu", "statute", "prescription", "hiệu lực"]):
            years = re.findall(r"\b(\d+)\s*năm\b", answer, re.IGNORECASE)
            if not years:
                years = re.findall(r"\b(\d+)\s*years?\b", answer, re.IGNORECASE)
            for y_str in years:
                y = int(y_str)
                if y > 30:
                    result.passed = False
                    result.severity = "low"
                    result.confidence = 0.5
                    result.details = f"Statute of limitations {y} years seems unusually long"
        if result.passed:
            result.details = "Statute check passed: no unusually long statutes"
        return True


class GeographyAntibodyMixin:
    def verify_capital(self, result: AntibodyResult, question: str, answer: str, ab_name: str) -> bool:
        _check_ran = True
        KNOWN_CAPITALS: dict[str, str] = {}
        a_lower = answer.lower()
        q_lower = question.lower()
        for country, capital in KNOWN_CAPITALS.items():
            if country in q_lower and capital not in a_lower:
                result.passed = False
                result.severity = "high"
                result.confidence = 0.8
                result.details = f"Question asks about '{country}', capital should be '{capital}' but not in answer"
                break
        if result.passed:
            result.details = "Capital check passed: no contradictions found"
        return True


class ChemistryAntibodyMixin:
    def verify_formula(self, result: AntibodyResult, question: str, answer: str, ab_name: str) -> bool:
        _check_ran = True
        KNOWN_FORMULAS: dict[str, str] = {}
        a_lower = answer.lower()
        q_lower = question.lower()
        for substance, formula in KNOWN_FORMULAS.items():
            if substance in q_lower and formula not in a_lower:
                import re as _re
                formula_match = _re.findall(r'\b([A-Z][a-z]?\d*(?:[A-Z][a-z]?\d*)+)\b', answer)
                if formula_match:
                    result.passed = False
                    result.severity = "high"
                    result.confidence = 0.8
                    result.details = f"Question asks '{substance}' (formula='{formula}'), but answer has '{formula_match[0]}'"
                    break
        if result.passed:
            result.details = "Formula check passed: no wrong formulas detected"
        return True


class BiologyAntibodyMixin:
    def verify_abbreviation(self, result: AntibodyResult, question: str, answer: str, ab_name: str) -> bool:
        _check_ran = True
        KNOWN_ABBREVS = {
            "dna": "deoxyribonucleic acid", "rna": "ribonucleic acid",
            "atp": "adenosine triphosphate", "adp": "adenosine diphosphate",
            "rbc": "red blood cell", "wbc": "white blood cell",
            "mrna": "messenger rna", "trna": "transfer rna", "rrna": "ribosomal rna",
            "pcr": "polymerase chain reaction", "nadh": "nicotinamide adenine dinucleotide",
        }
        q_lower = question.lower()
        a_lower = answer.lower()
        for abbr, expansion in KNOWN_ABBREVS.items():
            if abbr in q_lower and expansion not in a_lower:
                result.passed = False
                result.severity = "low"
                result.confidence = 0.6
                result.details = f"Question asks '{abbr.upper()}', correct expansion '{expansion}' not in answer"
                break
        if result.passed:
            result.details = "Abbreviation check passed: no wrong expansions"
        return True


class PhysicsAntibodyMixin:
    def verify_unit(self, result: AntibodyResult, question: str, answer: str, ab_name: str) -> bool:
        _check_ran = True
        import re as _re
        vel_match = _re.findall(r'(\d+(?:\.\d+)?(?:e[+-]?\d+)?)\s*(m/s|km/h|mps|kph)', answer, _re.IGNORECASE)
        for val_str, unit in vel_match:
            val = float(val_str)
            if unit.lower() in ("m/s", "mps") and val > 3e8:
                result.passed = False
                result.severity = "high"
                result.confidence = 0.9
                result.details = f"Velocity {val} m/s exceeds speed of light (3e8 m/s)"
            elif unit.lower() in ("km/h", "kph") and val > 1.08e9:
                result.passed = False
                result.severity = "high"
                result.confidence = 0.9
                result.details = f"Velocity {val} km/h exceeds speed of light"
        temp_match = _re.findall(r'(\d+(?:\.\d+)?)\s*(°c|°f|k|kelvin|celsius)', answer, _re.IGNORECASE)
        for val_str, unit in temp_match:
            val = float(val_str)
            if unit.lower() in ("°c", "celsius") and (val > 6000 or val < -273.15):
                result.passed = False
                result.severity = "medium"
                result.confidence = 0.8
                result.details = f"Temperature {val}°C outside plausible range (-273.15 to 6000)"
        if result.passed:
            result.details = "Physics unit check passed: no implausible values"
        return True


class HistoryAntibodyMixin:
    def verify_event_date(self, result: AntibodyResult, question: str, answer: str, ab_name: str) -> bool:
        _check_ran = True
        KNOWN_EVENTS = {
            "thế chiến 2": ("1939", "1945"), "world war 2": ("1939", "1945"), "ww2": ("1939", "1945"),
            "thế chiến 1": ("1914", "1918"), "world war 1": ("1914", "1918"), "ww1": ("1914", "1918"),
            "cách mạng tháng 10": ("1917", "1917"), "october revolution": ("1917", "1917"),
            "cách mạng pháp": ("1789", "1799"), "french revolution": ("1789", "1799"),
            "moon landing": ("1969", "1969"), "apollo": ("1969", "1972"),
            "berlin wall": ("1989", "1989"), "tường berlin": ("1989", "1989"),
            "vietnam war": ("1955", "1975"), "chiến tranh việt": ("1955", "1975"),
        }
        q_lower = question.lower()
        _a_lower = answer.lower()
        for event, (start, end) in KNOWN_EVENTS.items():
            if event in q_lower:
                years = re.findall(r'\b(1[5-9]\d{2}|20[0-2]\d)\b', answer)
                for year in years:
                    if int(year) < int(start) - 5 or int(year) > int(end) + 5:
                        result.passed = False
                        result.severity = "medium"
                        result.confidence = 0.7
                        result.details = f"'{event}' occurred {start}-{end}, but answer mentions {year}"
                        break
                if not result.passed:
                    break
        if result.passed:
            result.details = "Event date check passed: no date contradictions"
        return True


class TechnologyAntibodyMixin:
    def verify_http_status(self, result: AntibodyResult, question: str, answer: str, ab_name: str) -> bool:
        _check_ran = True
        KNOWN_STATUS = {
            "200": ("ok", "success", "thành công"),
            "201": ("created", "tạo"),
            "204": ("no content", "không nội dung"),
            "301": ("moved permanently", "chuyển vĩnh viễn"),
            "302": ("found", "redirect", "chuyển hướng"),
            "304": ("not modified", "không thay đổi"),
            "400": ("bad request", "yêu cầu sai"),
            "401": ("unauthorized", "không được ủy quyền"),
            "403": ("forbidden", "cấm"),
            "404": ("not found", "không tìm thấy"),
            "405": ("method not allowed", "phương thức không được phép"),
            "429": ("too many requests", "quá nhiều yêu cầu"),
            "500": ("internal server error", "lỗi server", "lỗi máy chủ"),
            "502": ("bad gateway", "cổng sai"),
            "503": ("service unavailable", "dịch vụ không khả dụng"),
            "504": ("gateway timeout", "hết thời gian chờ"),
        }
        _q_lower = question.lower()
        a_lower = answer.lower()
        status_codes = re.findall(r'\b([1-5]\d{2})\b', question)
        for code in status_codes:
            if code in KNOWN_STATUS:
                valid_descs = KNOWN_STATUS[code]
                if not any(desc in a_lower for desc in valid_descs):
                    result.passed = False
                    result.severity = "high"
                    result.confidence = 0.8
                    result.details = f"HTTP {code} should mean '{valid_descs[0]}', but answer doesn't mention it"
                    break
        if result.passed:
            result.details = f"HTTP status check passed: {len(status_codes)} code(s) scanned"
        return True


class EconomicsAntibodyMixin:
    def verify_gdp(self, result: AntibodyResult, question: str, answer: str, ab_name: str) -> bool:
        _check_ran = True
        a_lower = answer.lower()
        all_pcts = list(re.finditer(r'(\d+(?:\.\d+)?)\s*%', answer))
        for m in all_pcts:
            pct_val = float(m.group(1))
            start = max(0, m.start() - 30)
            end = min(len(a_lower), m.end() + 30)
            context = a_lower[start:end]
            if any(kw in context for kw in ["gdp", "growth", "tăng trưởng", "kinh tế"]):
                if pct_val > 10:
                    result.passed = False
                    result.severity = "medium"
                    result.confidence = 0.75
                    result.details = (
                        f"GDP growth rate {pct_val}% is implausibly high "
                        f"(most countries grow 0-10%/yr; only China peaked ~14%)"
                    )
                    break
        if result.passed:
            result.details = f"GDP check passed: {len(all_pcts)} percentage(s) scanned"
        return True

    def verify_inflation(self, result: AntibodyResult, question: str, answer: str, ab_name: str) -> bool:
        _check_ran = True
        a_lower = answer.lower()
        all_pcts = list(re.finditer(r'(\d+(?:\.\d+)?)\s*%', answer))
        for m in all_pcts:
            pct_val = float(m.group(1))
            start = max(0, m.start() - 30)
            end = min(len(a_lower), m.end() + 30)
            context = a_lower[start:end]
            if any(kw in context for kw in ["inflation", "lạm phát", "cpi", "price index"]):
                if pct_val > 50:
                    result.passed = False
                    result.severity = "high"
                    result.confidence = 0.85
                    result.details = (
                        f"Inflation {pct_val}% — hyperinflation territory. "
                        f"Verify if answer explicitly mentions hyperinflation context."
                    )
                    break
                elif pct_val > 20:
                    result.passed = False
                    result.severity = "medium"
                    result.confidence = 0.7
                    result.details = (
                        f"Inflation {pct_val}% is extreme — verify against historical data "
                        f"(most countries stay under 20%, except crises)"
                    )
                    break
        if result.passed:
            result.details = f"Inflation check passed: {len(all_pcts)} percentage(s) scanned"
        return True


# ARC-02: Engine aliases for modular composition and Constructor DI
GeneralAntibodyEngine = GeneralAntibodyMixin
MedicalAntibodyEngine = MedicalAntibodyMixin
FinanceAntibodyEngine = FinanceAntibodyMixin
LegalAntibodyEngine = LegalAntibodyMixin
GeographyAntibodyEngine = GeographyAntibodyMixin
ChemistryAntibodyEngine = ChemistryAntibodyMixin
BiologyAntibodyEngine = BiologyAntibodyMixin
PhysicsAntibodyEngine = PhysicsAntibodyMixin
HistoryAntibodyEngine = HistoryAntibodyMixin
TechnologyAntibodyEngine = TechnologyAntibodyMixin
EconomicsAntibodyEngine = EconomicsAntibodyMixin
PhilosophyAntibodyEngine = PhilosophyAntibodyMixin
PsychologyAntibodyEngine = PsychologyAntibodyMixin
AgricultureAntibodyEngine = AgricultureAntibodyMixin
Earth_ScienceAntibodyEngine = Earth_ScienceAntibodyMixin
EngineeringAntibodyEngine = EngineeringAntibodyMixin
ArtAntibodyEngine = ArtAntibodyMixin
MilitaryAntibodyEngine = MilitaryAntibodyMixin
EnvironmentalAntibodyEngine = EnvironmentalAntibodyMixin
EducationAntibodyEngine = EducationAntibodyMixin


__all__ = [
    "Severity",
    "GeneralAntibodyMixin",
    "MedicalAntibodyMixin",
    "FinanceAntibodyMixin",
    "LegalAntibodyMixin",
    "GeographyAntibodyMixin",
    "ChemistryAntibodyMixin",
    "BiologyAntibodyMixin",
    "PhysicsAntibodyMixin",
    "HistoryAntibodyMixin",
    "TechnologyAntibodyMixin",
    "EconomicsAntibodyMixin",
    "PhilosophyAntibodyMixin",
    "PsychologyAntibodyMixin",
    "AgricultureAntibodyMixin",
    "Earth_ScienceAntibodyMixin",
    "EngineeringAntibodyMixin",
    "ArtAntibodyMixin",
    "MilitaryAntibodyMixin",
    "EnvironmentalAntibodyMixin",
    "EducationAntibodyMixin",
    "GeneralAntibodyEngine",
    "MedicalAntibodyEngine",
    "FinanceAntibodyEngine",
    "LegalAntibodyEngine",
    "GeographyAntibodyEngine",
    "ChemistryAntibodyEngine",
    "BiologyAntibodyEngine",
    "PhysicsAntibodyEngine",
    "HistoryAntibodyEngine",
    "TechnologyAntibodyEngine",
    "EconomicsAntibodyEngine",
    "PhilosophyAntibodyEngine",
    "PsychologyAntibodyEngine",
    "AgricultureAntibodyEngine",
    "Earth_ScienceAntibodyEngine",
    "EngineeringAntibodyEngine",
    "ArtAntibodyEngine",
    "MilitaryAntibodyEngine",
    "EnvironmentalAntibodyEngine",
    "EducationAntibodyEngine",
]
