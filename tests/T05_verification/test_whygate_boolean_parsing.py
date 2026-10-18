"""
Unit and anti-placebo tests for WhyGate structured boolean parsing.
Tests prove that structured boolean responses from LLM (JSON and regex key-value)
correctly parse false values as False without false-positive positive substring matching.
"""
from __future__ import annotations

import pytest
from scp.meta.why_gate import WhyGate


class TestWhyGateStructuredBooleanParsing:
    """Test suite verifying structured boolean parsing in WhyGate._parse_llm_necessity."""

    def test_structured_json_false_returns_false(self):
        """Prove JSON payloads with is_necessary=false evaluate to False."""
        assert WhyGate._parse_llm_necessity('{"is_necessary": false}') is False
        assert WhyGate._parse_llm_necessity('{"is_necessary": false, "reason": "redundant step"}') is False
        assert WhyGate._parse_llm_necessity('{"necessary": false}') is False
        assert WhyGate._parse_llm_necessity('{"is_necessary": "false"}') is False
        assert WhyGate._parse_llm_necessity('{"is_necessary": 0}') is False

    def test_structured_key_value_false_returns_false(self):
        """Prove key-value / YAML formats with is_necessary: false evaluate to False."""
        assert WhyGate._parse_llm_necessity("is_necessary: false") is False
        assert WhyGate._parse_llm_necessity("is_necessary: False") is False
        assert WhyGate._parse_llm_necessity("'is_necessary': false") is False
        assert WhyGate._parse_llm_necessity('"is_necessary": false') is False
        assert WhyGate._parse_llm_necessity("necessary: false") is False
        assert WhyGate._parse_llm_necessity("is_necessary: 0") is False
        assert WhyGate._parse_llm_necessity("is_necessary: no") is False

    def test_structured_json_true_returns_true(self):
        """Prove JSON payloads with is_necessary=true evaluate to True."""
        assert WhyGate._parse_llm_necessity('{"is_necessary": true}') is True
        assert WhyGate._parse_llm_necessity('{"is_necessary": true, "reason": "required action"}') is True
        assert WhyGate._parse_llm_necessity('{"necessary": true}') is True
        assert WhyGate._parse_llm_necessity('{"is_necessary": "true"}') is True
        assert WhyGate._parse_llm_necessity('{"is_necessary": 1}') is True

    def test_structured_key_value_true_returns_true(self):
        """Prove key-value / YAML formats with is_necessary: true evaluate to True."""
        assert WhyGate._parse_llm_necessity("is_necessary: true") is True
        assert WhyGate._parse_llm_necessity("is_necessary: True") is True
        assert WhyGate._parse_llm_necessity("'is_necessary': true") is True
        assert WhyGate._parse_llm_necessity('"is_necessary": true') is True
        assert WhyGate._parse_llm_necessity("necessary: true") is True
        assert WhyGate._parse_llm_necessity("is_necessary: 1") is True
        assert WhyGate._parse_llm_necessity("is_necessary: yes") is True

    def test_word_boundary_necessary_does_not_match_is_necessary(self):
        """Prove word boundaries prevent 'is_necessary' from triggering positive 'necessary' match."""
        # When structured is_necessary: false is provided, it must NOT evaluate to True
        result = WhyGate._parse_llm_necessity("is_necessary: false")
        assert result is False
        assert result is not True

        # Even with embedded markdown or json codeblock
        codeblock = "```json\n{\n  \"is_necessary\": false\n}\n```"
        assert WhyGate._parse_llm_necessity(codeblock) is False

    def test_natural_language_phrases_preserved(self):
        """Prove natural language positive and negative phrases continue working."""
        assert WhyGate._parse_llm_necessity("Action is necessary and justified.") is True
        assert WhyGate._parse_llm_necessity("There is no valid reason.") is False
        assert WhyGate._parse_llm_necessity("There is not a valid reason to proceed with this fix.") is False
        assert WhyGate._parse_llm_necessity("Khong co ly do hop le de thuc hien.") is False
        assert WhyGate._parse_llm_necessity("Can thiet de bao ve he thong.") is True

    def test_ambiguous_and_empty_responses_return_none(self):
        """Prove empty or ambiguous responses fail-closed to None (UPHOLD)."""
        assert WhyGate._parse_llm_necessity("") is None
        assert WhyGate._parse_llm_necessity("   ") is None
        assert WhyGate._parse_llm_necessity("not unnecessary") is None
        assert WhyGate._parse_llm_necessity("not unneeded") is None
        assert WhyGate._parse_llm_necessity("I am unsure whether this is valid.") is None

    def test_anti_placebo_negative_verification(self):
        """Anti-placebo negative test: explicitly assert that false outputs are strictly NOT True.

        Proves that false responses cannot accidentally pass a loose assertion.
        """
        negative_inputs = [
            '{"is_necessary": false}',
            'is_necessary: false',
            '{"is_necessary": false, "reason": "unneeded"}',
            "{'is_necessary': false}",
            'is_necessary: False',
            '{"necessary": false}',
        ]
        for inp in negative_inputs:
            verdict = WhyGate._parse_llm_necessity(inp)
            assert verdict is False, f"Input {inp!r} must return False"
            assert verdict is not True, f"Input {inp!r} must NEVER return True"
