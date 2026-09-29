import json

from scp.rag.canonical_retriever import CanonicalRetriever, _tokens


def test_rag_tokenization_and_stemming():
    tokens = _tokens("The quick brown foxes were jumping quickly in the forest")
    assert "the" not in tokens
    assert "were" not in tokens
    assert "foxes" in tokens
    assert "quick" in tokens

def test_rag_retriever_in_memory_index(tmp_path):
    corpus_dir = tmp_path / "data" / "rag_corpus" / "canonical-v2-20260817"
    corpus_dir.mkdir(parents=True)
    corpus_file = corpus_dir / "corpus_all_fetched.jsonl"
    corpus_file.write_text(json.dumps({
        "source_url": "https://en.wikipedia.org/wiki/Helium",
        "source_title": "Helium Element",
        "chunks": [{"chunk_id": "c1", "document_id": "d1", "text": "Helium is a chemical element with atomic number 2."}]
    }) + "\n", encoding="utf-8")

    retriever = CanonicalRetriever(root=tmp_path)
    results = retriever.retrieve("chemical element helium", k=3)
    assert len(results) > 0
    assert results[0]["chunk_id"] == "c1"

def test_rag_retriever_missing_corpus_fallback(tmp_path):
    from scp.rag.canonical_retriever import CanonicalRetriever
    retriever = CanonicalRetriever(root=tmp_path)
    results = retriever.retrieve("NonExistentQueryForTesting12345")
    assert isinstance(results, list)
    assert retriever.available is False

def test_hybrid_retriever_search(tmp_path):
    from scp.rag.canonical_retriever import HybridRetriever
    retriever = HybridRetriever(root=tmp_path)
    results = retriever.search("NonExistentQueryForTesting12345", top_k=2)
    assert isinstance(results, list)

