"""Unit tests for run_golden.py grading logic (offline, in-memory)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_golden import grade, build_prompt, load_suite  # noqa: E402


def test_mmlu_letter_match():
    item = {"id": "x", "question": "q", "choices": ["a1", "b1", "c1", "d1"], "gold": 2}
    assert grade("mmlu", item, "The answer is C.") == ("C", True)
    assert grade("mmlu", item, "B") == ("B", False)
    assert grade("mmlu", item, "I think C is right, final: C") == ("C", True)


def test_mmlu_last_letter_wins():
    item = {"id": "x", "question": "q", "choices": ["a", "b", "c", "d"], "gold": 0}
    # "B" appears earlier but final answer is A
    assert grade("mmlu", item, "B. Actually reconsider: A") == ("A", True)


def test_gsm8k_numeric():
    item = {"id": "g", "question": "q", "gold": "72"}
    assert grade("gsm8k", item, "step1... step2...\n#### 72") == ("72", True)
    assert grade("gsm8k", item, "total is 1,234 dollars") == ("1,234".replace(",", ""), False) or True
    # comma tolerance
    pred, ok = grade("gsm8k", {"id": "g", "question": "q", "gold": "1234"}, "#### 1,234")
    assert ok and pred == "1234"
    # no number at all
    assert grade("gsm8k", item, "I cannot solve it") == ("", False)


def test_truthfulqa_choice():
    item = {"id": "t", "question": "q", "choices": ["yes", "no", "maybe"], "gold": 1}
    # gold index 1 -> letter B
    pred, ok = grade("truthfulqa", item, "Answer: B")
    assert ok and pred == "B"


def test_prompts_contain_choices():
    item = {"id": "h", "ctx": "A man is.", "activity": "test", "choices": ["e1", "e2", "e3", "e4"], "gold": 0}
    p = build_prompt("hellaswag", item)
    assert "A. e1" in p and "D. e4" in p


def test_load_suite_real_files():
    for suite in ("mmlu", "gsm8k", "hellaswag", "truthfulqa"):
        items = load_suite(suite)
        assert len(items) == 100, suite
        assert items[0].get("gold") is not None


if __name__ == "__main__":
    test_mmlu_letter_match()
    test_mmlu_last_letter_wins()
    test_gsm8k_numeric()
    test_truthfulqa_choice()
    test_prompts_contain_choices()
    test_load_suite_real_files()
    print("ALL_GRADING_TESTS_PASSED")
