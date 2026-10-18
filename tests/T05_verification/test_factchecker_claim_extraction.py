"""
Unit and anti-placebo tests for StreamingFactChecker entity/geography claim extraction.
Tests prove that non-arithmetic statements (geography, entity location, relations)
in English and Vietnamese are correctly extracted for fact-checking.
"""
from __future__ import annotations

import pytest
from scp.core.streaming_factcheck import StreamingFactChecker


class TestStreamingFactCheckerClaimExtraction:
    """Test suite verifying claim extraction in StreamingFactChecker."""

    @pytest.fixture
    def checker(self) -> StreamingFactChecker:
        return StreamingFactChecker()

    def test_extract_english_entity_location_claims(self, checker: StreamingFactChecker):
        """Prove English geography and entity-location claims are extracted."""
        text_1 = "The Eiffel Tower is in Berlin."
        claims_1 = checker._extract_claims(text_1)
        assert len(claims_1) == 1
        assert claims_1[0] == "The Eiffel Tower is in Berlin"

        text_2 = "Mount Everest is located in Nepal."
        claims_2 = checker._extract_claims(text_2)
        assert len(claims_2) == 1
        assert claims_2[0] == "Mount Everest is located in Nepal"

        text_3 = "Paris is the capital of France."
        claims_3 = checker._extract_claims(text_3)
        assert len(claims_3) == 1
        assert claims_3[0] == "Paris is the capital of France"

    def test_extract_vietnamese_entity_location_claims(self, checker: StreamingFactChecker):
        """Prove Vietnamese geography and entity-location claims are extracted."""
        text_1 = "Tháp Eiffel nằm ở Paris."
        claims_1 = checker._extract_claims(text_1)
        assert len(claims_1) == 1
        assert claims_1[0] == "Tháp Eiffel nằm ở Paris"

        text_2 = "Fansipan tọa lạc tại Lào Cai."
        claims_2 = checker._extract_claims(text_2)
        assert len(claims_2) == 1
        assert claims_2[0] == "Fansipan tọa lạc tại Lào Cai"

        text_3 = "Hà Nội là thủ đô của Việt Nam."
        claims_3 = checker._extract_claims(text_3)
        assert len(claims_3) == 1
        assert claims_3[0] == "Hà Nội là thủ đô của Việt Nam"

        # Non-diacritic Vietnamese text
        text_4 = "Thap Eiffel nam o Paris."
        claims_4 = checker._extract_claims(text_4)
        assert len(claims_4) == 1
        assert claims_4[0] == "Thap Eiffel nam o Paris"

    def test_extract_entity_relation_claims(self, checker: StreamingFactChecker):
        """Prove entity-relation and categorization claims are extracted."""
        text_1 = "Python is a programming language."
        claims_1 = checker._extract_claims(text_1)
        assert len(claims_1) == 1
        assert claims_1[0] == "Python is a programming language"

        text_2 = "Mặt trời là một ngôi sao lớn."
        claims_2 = checker._extract_claims(text_2)
        assert len(claims_2) == 1
        assert claims_2[0] == "Mặt trời là một ngôi sao lớn"

    def test_preserves_numeric_and_date_and_comparative_claims(self, checker: StreamingFactChecker):
        """Prove existing numeric, date, and comparative patterns remain functional."""
        text = "Dân số Việt Nam năm 2023 là 100 triệu người. Toà nhà Burj Khalifa là toà nhà lớn nhất thế giới."
        claims = checker._extract_claims(text)
        assert len(claims) == 2
        assert any("100 triệu" in c for c in claims)
        assert any("lớn nhất" in c for c in claims)

    def test_conversational_text_not_extracted(self, checker: StreamingFactChecker):
        """Prove conversational sentences without factual assertions are ignored."""
        text = "Hello there. How are you today? Please proceed."
        claims = checker._extract_claims(text)
        assert claims == []

    def test_short_sentences_ignored(self, checker: StreamingFactChecker):
        """Prove sentences under 10 chars are ignored per boundary rule."""
        text = "Yes is in. No is at."
        claims = checker._extract_claims(text)
        assert claims == []

    def test_anti_placebo_negative_verification(self, checker: StreamingFactChecker):
        """Anti-placebo test:
        1. Prove that non-arithmetic statements strictly do NOT return empty list (old bug behavior).
        2. Prove that non-claims do NOT produce false-positive claim extractions.
        """
        factual_sentences = [
            "The Eiffel Tower is in Berlin.",
            "Tháp Eiffel nằm ở Paris.",
            "Mount Everest is located in Nepal.",
            "Paris is the capital of France.",
            "Python is a programming language.",
        ]
        for sentence in factual_sentences:
            extracted = checker._extract_claims(sentence)
            assert len(extracted) > 0, f"Expected claim from {sentence!r}, got empty list (GAP DETECTED)"
            assert extracted[0] != "", "Extracted claim must not be empty"

        non_claims = [
            "Hello world how are you doing today.",
            "Good morning to all participants.",
            "Thank you very much for your help.",
        ]
        for sentence in non_claims:
            extracted = checker._extract_claims(sentence)
            assert extracted == [], f"Non-claim {sentence!r} must return empty list, got {extracted}"
