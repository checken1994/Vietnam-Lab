"""[DEHARDCODE] Gate bắt buộc cho logic leak-check (dữ liệu tổng hợp, không cần bộ realq).

Anti-placebo: câu "eval" cài vào corpus PHẢI bị bắt (nguyên câu và đoạn 10 từ); câu
không có trong corpus không bị báo nhầm; corpus thật của repo được quét được.
"""
from __future__ import annotations

from tools.realq_leak_check import corpus_docs, find_leaks, norm_words

PLANTED = "Synthetic probe sentence number seven about orbital tea kettles and polite walruses"


def test_planted_question_is_detected_full_and_ngram() -> None:
    docs = [norm_words("x = 1  # " + PLANTED.upper() + " trailing words here")]
    leaks = find_leaks([("q-planted", PLANTED), ("q-clean", "an entirely different unrelated sentence of words")], docs)
    assert leaks["full_question_in_repo"] == ["q-planted"]
    assert leaks["ten_gram_overlap"] == ["q-planted"]


def test_partial_ten_word_overlap_is_detected() -> None:
    docs = [norm_words("prefix " + " ".join(PLANTED.split()[:10]) + " suffix")]
    leaks = find_leaks([("q", PLANTED + " with an extra tail")], docs)
    assert leaks["ten_gram_overlap"] == ["q"]


def test_repo_corpus_is_scanned() -> None:
    docs = corpus_docs()
    assert len(docs) > 100 and sum(len(d) for d in docs) > 100_000
