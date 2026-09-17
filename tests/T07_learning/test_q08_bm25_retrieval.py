"""Q08 — BM25 CanonicalRetriever regression contract (hermetic, fixture corpus).

Real logic, no mocks: the retriever runs unmodified over a fixture corpus
built from this repository's own gold data
(``scp/rag/retrieval_eval.py``: ``_GEO_FACTS`` from
``scp/benchmark/question_generator.py`` + static categories of
``scp/benchmark/iso_comprehensive.jsonl``). Corpus is synthetic; goldsets and
the retriever are real repo code. Production corpus numbers are NOT claimed
(data/rag_corpus is absent in this checkout) — see
reports/expert-panel/Q08-hybrid-retrieval.md.

Measured floors (branch audit/runtime-guard-AUDIT-20260909, legacy=bf5af3b):
    overall recall@5: legacy 0.5016 -> BM25 0.9812
    geo-paraphrase:   legacy 0.0    -> BM25 0.98
    iso-diluted:      legacy 0.0    -> BM25 0.951
Gates below keep a margin and fail closed if recall regresses.
No skip/xfail (SCP test discipline).
"""
from __future__ import annotations

import json
import math
import tempfile
from pathlib import Path

import pytest

from scp.rag import retrieval_eval as ev
from scp.rag.canonical_retriever import (
    BM25_B,
    BM25_K1,
    CanonicalRetriever,
    get_canonical_retriever,
)

REQUIRED_FIELDS = {
    "chunk_id", "document_id", "source_url", "source_title", "text",
    "retrieval_score", "term_coverage", "matched_bigrams",
}
INTERNAL_FIELDS = {"terms", "norm", "title_terms", "tf", "ttf", "tlen", "ttlen"}


@pytest.fixture(scope="module")
def fixture_root(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("q08-corpus")
    meta = ev.build_fixture(root)
    assert meta["chunks"] > 200
    return root


@pytest.fixture(scope="module")
def probes(fixture_root: Path) -> list[dict]:
    return ev.build_fixture(fixture_root)["probes"]


@pytest.fixture(scope="module")
def retriever(fixture_root: Path) -> CanonicalRetriever:
    return CanonicalRetriever(root=fixture_root)


def _tiny_corpus(root: Path) -> None:
    out = root / ev.CORPUS_SUBPATH
    out.parent.mkdir(parents=True, exist_ok=True)
    docs = [
        {"document_id": "d1", "final_url": "https://example.org/d1", "source_title": "",
         "chunks": [{"chunk_id": "d1#c0", "document_id": "d1", "text": "alpha beta"}]},
        {"document_id": "d2", "final_url": "https://example.org/d2", "source_title": "",
         "chunks": [{"chunk_id": "d2#c0", "document_id": "d2", "text": "alpha alpha gamma delta"}]},
        {"document_id": "d3", "final_url": "https://example.org/d3", "source_title": "",
         "chunks": [{"chunk_id": "d3#c0", "document_id": "d3", "text": "epsilon zeta eta"}]},
    ]
    out.write_text("\n".join(json.dumps(d) for d in docs) + "\n", encoding="utf-8")


def test_bm25_scores_match_reference_formula(tmp_path: Path):
    """Independent recomputation of Okapi BM25 (k1=1.2, b=0.75, Lucene idf)."""
    assert (BM25_K1, BM25_B) == (1.2, 0.75)
    _tiny_corpus(tmp_path)
    r = CanonicalRetriever(root=tmp_path)
    hits = r.retrieve("alpha", k=5)
    assert [h["document_id"] for h in hits] == ["d2", "d1"]  # tf saturation ordering
    n, df, avgdl = 3, 2, (2 + 4 + 3) / 3.0
    idf = math.log(1.0 + (n - df + 0.5) / (df + 0.5))
    for h, tf, dl in (("d2", 2, 4), ("d1", 1, 2)):
        expected = idf * tf * (BM25_K1 + 1.0) / (tf + BM25_K1 * (1.0 - BM25_B + BM25_B * dl / avgdl))
        got = next(x["retrieval_score"] for x in hits if x["document_id"] == h)
        assert abs(got - round(expected, 4)) < 1e-3, f"{h}: {got} != {expected}"


def test_phrase_bonus_breaks_unigram_tie(tmp_path: Path):
    """Same unigrams, different adjacency: ordered chunk must rank first."""
    out = tmp_path / ev.CORPUS_SUBPATH
    out.parent.mkdir(parents=True, exist_ok=True)
    docs = [
        {"document_id": "b", "final_url": "https://example.org/b", "source_title": "",
         "chunks": [{"chunk_id": "b#c0", "document_id": "b", "text": "hội xã sinh"}]},
        {"document_id": "a", "final_url": "https://example.org/a", "source_title": "",
         "chunks": [{"chunk_id": "a#c0", "document_id": "a", "text": "sinh xã hội"}]},
    ]
    out.write_text("\n".join(json.dumps(d) for d in docs) + "\n", encoding="utf-8")
    r = CanonicalRetriever(root=tmp_path)
    hits = r.retrieve("sinh xã hội", k=5)
    assert hits[0]["document_id"] == "a"
    assert hits[0]["matched_bigrams"] >= 1
    assert hits[0]["retrieval_score"] > hits[1]["retrieval_score"]


def test_item_schema_and_caps_and_dedup(retriever, probes):
    sample_q = probes[0]["question"]
    for k, cap in ((0, 1), (-5, 1), (3, 3), (100, 8)):
        hits = retriever.retrieve(sample_q, k=k)
        assert 0 < len(hits) <= max(1, min(k, 8) if k > 0 else 1), k
        if cap != 3:
            assert len(hits) <= (1 if k <= 0 else 8)
    hits = retriever.retrieve("thủ", k=8)
    assert len(hits) <= 8
    assert len({h["document_id"] for h in hits}) == len(hits), "one chunk per document"
    for h in hits:
        assert REQUIRED_FIELDS <= set(h), h.keys()
        assert not INTERNAL_FIELDS & set(h), "internal index fields leaked into output"
        assert isinstance(h["matched_bigrams"], int)
        assert isinstance(h["retrieval_score"], (int, float))
        assert 0.0 <= h["term_coverage"] <= 1.0


@pytest.mark.parametrize("group,min_recall", (
    ("geo-verbatim", 0.95),
    ("geo-paraphrase", 0.90),
    ("en-capital", 0.90),
    ("iso-verbatim", 0.95),
    ("iso-diluted", 0.90),
))
def test_recall_gates_per_group(retriever, probes, group, min_recall):
    report = ev.evaluate(retriever, [p for p in probes if p["group"] == group], k=5)
    row = report[group]
    assert row["n"] >= 10
    assert row["recall@5"] >= min_recall, f"{group}: {row['recall@5']} < {min_recall}"


def test_overall_recall_gate(retriever, probes):
    report = ev.evaluate(retriever, probes, k=5)
    overall = report["__overall__"]
    assert overall["n"] == len(probes) - 0  # all probes evaluable for the new scorer
    assert overall["recall@5"] >= 0.95, overall
    assert overall["top1@5"] >= 0.85, overall


def test_vietnamese_short_word_queries(retriever):
    # 'mỹ'/'nga' (2-char, diacritic) used to be dropped -> single generic token ->
    # insertion-order ties. Now BM25 must surface the right country chunk.
    for question, needle in (("thủ đô Mỹ", "Washington"), ("thủ đô Nga", "Moscow")):
        hits = retriever.retrieve(question, k=5)
        assert any(needle in h["text"] for h in hits), (question, hits[:1])


def test_empty_corpus_and_no_query(retriever):
    with tempfile.TemporaryDirectory() as td:
        empty = CanonicalRetriever(root=Path(td))
        assert empty.retrieve("anything at all", k=5) == []
        assert empty.contexts("anything at all") == []
    assert retriever.retrieve("   ", k=5) == []
    assert retriever.retrieve("the of to", k=5) == []  # stopword-only


def test_contexts_format_and_singleton(fixture_root: Path):
    cls_ctx = CanonicalRetriever(root=fixture_root).contexts("thủ đô Nhật Bản", k=1)
    assert len(cls_ctx) == 1
    assert cls_ctx[0].startswith("[chunk_id=") and "source_url=https://example.org" in cls_ctx[0]
    assert isinstance(get_canonical_retriever(), CanonicalRetriever)
    assert get_canonical_retriever() is get_canonical_retriever()
