"""Q08 evidence-recall harness for ``CanonicalRetriever`` (fixture corpus).

Why a fixture corpus: this checkout has NO production canonical corpus
(``data/rag_corpus/canonical-v2-20260817/...`` and ``canonical-v3-.../...``
are absent — the RAG-1000 input pool was recorded empty). All numbers
produced by this module are FIXTURE-CORPUS numbers, not a production
recall claim. See reports/expert-panel/Q08-hybrid-retrieval.md.

Goldsets come from data that already exists in this repository:
  * ``scp/benchmark/question_generator.py`` -> ``_GEO_FACTS`` (50 triples
    ``(question, expected_answer, gold_evidence)`` — the same gold_evidence
    strings behind the 2026-09-12 /ask benchmark that recorded
    evidence_recall=0.3846 end-to-end).
  * ``scp/benchmark/iso_comprehensive.jsonl`` -> static-fact lookup
    questions (math/logic/statistics/weather excluded: non-retrieval or
    dynamic-answer categories).

Relevance rules stay aligned with the repo's own benchmark semantics:
  * ``mode="overlap"`` mirrors
    ``scp/benchmark/run_benchmark_v2_parts/compute_evidence_metrics.py``:
    a gold evidence statement counts as retrieved when a hit shares >50%
    of its content words (length>3, lowercased). Golds without content
    words are not evaluable and are dropped from the denominator.
  * ``mode="doc"`` (fixture-only probes): a hit counts when its
    ``document_id`` is in the probe's gold doc set (document-level recall
    on a corpus with a unique gold doc per probe).

Query families: ``*-verbatim`` guards against regression; ``*-paraphrase``
and ``*-diluted`` expose the known failure mode of the pre-Q08 heuristic
(hard pruning rules that break when query wording/length changes).

CLI:
    python -m scp.rag.retrieval_eval --k 5 \
        --impl legacy=C:/path/legacy_canonical_retriever.py --impl current
"""
from __future__ import annotations

import argparse
import ast
import json
import random
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
GEO_SOURCE = REPO_ROOT / "scp" / "benchmark" / "question_generator.py"
ISO_GOLDSET = REPO_ROOT / "scp" / "benchmark" / "iso_comprehensive.jsonl"
CORPUS_SUBPATH = "data/rag_corpus/canonical-v2-20260817/corpus_all_fetched.jsonl"

# Categories with stable static facts expressible as a sentence containing
# the question's own words + the gold answer. Others are excluded on purpose.
ISO_STATIC_CATEGORIES = (
    "biology", "chemistry", "physics", "geography", "history", "astronomy",
    "conversion", "medical", "cybersecurity", "finance", "technology",
)

# Vietnamese -> English country names for the parallel-English probes.
EN_NAMES = {
    "Việt Nam": "Vietnam", "Pháp": "France", "Nhật Bản": "Japan",
    "Anh": "the United Kingdom", "Đức": "Germany", "Mỹ": "the United States",
    "Nga": "Russia", "Trung Quốc": "China", "Ấn Độ": "India",
    "Brazil": "Brazil", "Hàn Quốc": "South Korea", "Thái Lan": "Thailand",
    "Ý": "Italy", "Tây Ban Nha": "Spain", "Canada": "Canada",
}

_FILLERS = (
    "Hồ sơ này được cập nhật định kỳ bởi nhóm biên tập dữ liệu mở có kiểm duyệt.",
    "Tài liệu kèm theo mô tả lịch sử phát triển đô thị và quy hoạch vùng.",
    "Bảng thống kê bên dưới liệt kê dân số diện tích và các chỉ số phát triển.",
    "Ghi chú biên tập: nguồn dữ liệu tổng hợp từ nhiều văn bản công khai khác nhau.",
    "The appendix contains charts about transportation infrastructure and tourism.",
    "Lưu ý: thông tin liên hệ của đơn vị xuất bản nằm ở cuối tài liệu gốc.",
    "Chương mở đầu bàn về khí hậu khu vực và tác động của gió mùa đối với nông nghiệp.",
    "Phụ lục A trình bày phương pháp khảo sát mẫu và quy trình hiệu chuẩn thiết bị đo.",
    "Bản đồ phân bố dân cư cho thấy mật độ tập trung chủ yếu ở đồng bằng ven biển.",
    "Nhóm nghiên cứu đã phỏng vấn hai mươi chuyên gia độc lập trong ba tháng liền.",
    "Dòng thời gian sự kiện được đối chiếu với ba nguồn lưu trữ quốc gia khác nhau.",
    "Mục tham khảo liệt kê các báo cáo thường niên và niên giám thống kê liên quan.",
    "Biểu đồ tăng trưởng năng suất giai đoạn gần đây phản ánh chu kỳ đầu tư hạ tầng.",
    "The editorial board reviews every citation before publication to ensure consistency.",
)

# Dilution sentences: deliberately DISJOINT from _FILLERS (never written into
# the corpus). Otherwise the diluted probe degenerates into "find the noise
# doc that literally contains the query prefix", which is a defensible
# ranking outcome, not the dilution robustness we intend to measure.
_DILUTION = (
    "Đây là đoạn dẫn nhập dài không chứa thông tin cần thiết cho câu trả lời.",
    "Vui lòng cân nhắc các yếu tố ngoại cảnh khi đánh giá chất lượng nguồn tin.",
    "Bối cảnh bổ sung chỉ nhằm kiểm tra độ bền của pipeline truy xuất dữ liệu.",
    "Bạn có thể bỏ qua đoạn này nếu đã quen với định dạng đầu vào tiêu chuẩn.",
)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "x"


def load_geo_facts() -> list[tuple[str, str, str]]:
    """Extract ``_GEO_FACTS`` from question_generator.py via ast (no import)."""
    tree = ast.parse(GEO_SOURCE.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "_GEO_FACTS" for t in node.targets
        ):
            return [tuple(x) for x in ast.literal_eval(node.value)]  # type: ignore[misc]
    raise RuntimeError(f"_GEO_FACTS not found in {GEO_SOURCE}")


def load_iso_questions() -> list[dict[str, Any]]:
    rows = []
    for line in ISO_GOLDSET.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        x = json.loads(line)
        if x.get("category") in ISO_STATIC_CATEGORIES and str(x.get("expected_answer") or "").strip():
            rows.append(x)
    return rows


def build_fixture(root: Path) -> dict[str, Any]:
    """Write the deterministic Q08 fixture corpus under ``root``.

    Returns ``{"documents": int, "chunks": int, "probes": list[dict]}``.
    Corpus layout matches CanonicalRetriever's expectation: documents of
    ``{final_url, source_title, chunks:[{chunk_id, document_id, text}]}``
    at ``<root>/data/rag_corpus/canonical-v2-20260817/corpus_all_fetched.jsonl``.
    """
    rng = random.Random(20260915)
    geo = load_geo_facts()
    iso = load_iso_questions()
    docs: list[dict[str, Any]] = []
    probes: list[dict[str, Any]] = []

    def add_doc(doc_id: str, title: str, url: str, texts: list[str]) -> None:
        docs.append({
            "document_id": doc_id, "final_url": url, "source_title": title,
            "chunks": [{"chunk_id": f"{doc_id}#c{i}", "document_id": doc_id, "text": t}
                       for i, t in enumerate(texts)],
        })

    # Vietnamese geo facts: gold chunk (half length-stressed) + twins.
    for i, (country, capital, evidence) in enumerate(geo):
        country_clean = country.replace("thủ đô ", "").strip()
        if i % 2 == 0:
            body = f"{evidence} {rng.choice(_FILLERS)} {rng.choice(_FILLERS)}"
        else:
            body = evidence
        add_doc(f"geo-vn-{i:03d}", country, f"https://example.org/geo-vn/{i:03d}", [body])
        add_doc(f"geo-dx-{i:03d}", capital, f"https://example.org/geo-dx/{i:03d}",
                [f"{capital} là thành phố lớn nhất của {country_clean}, nổi tiếng về ẩm thực và giao thông."])
        add_doc(f"geo-en-{i:03d}", country, f"https://example.org/geo-en/{i:03d}",
                [f"The capital of {EN_NAMES.get(country_clean, country_clean)} is {capital}."])
        probes.append({"probe_id": f"geo-v-{i:03d}", "group": "geo-verbatim",
                       "question": country, "gold_evidence": evidence,
                       "gold_docs": [f"geo-vn-{i:03d}"], "mode": "overlap"})
        probes.append({"probe_id": f"geo-p-{i:03d}", "group": "geo-paraphrase",
                       "question": f"Thủ đô của {country_clean} là thành phố nào, trả lời ngắn gọn.",
                       "gold_evidence": evidence,
                       "gold_docs": [f"geo-vn-{i:03d}"], "mode": "overlap"})
        if country_clean in EN_NAMES:
            probes.append({"probe_id": f"geo-e-{i:03d}", "group": "en-capital",
                           "question": f"What is the capital of {EN_NAMES[country_clean]}?",
                           "gold_evidence": f"The capital of {EN_NAMES[country_clean]} is {capital}.",
                           "gold_docs": [f"geo-en-{i:03d}"], "mode": "doc"})

    # ISO static facts.
    for x in iso:
        qid = str(x["id"])
        q = str(x["question"]).strip()
        ans = str(x["expected_answer"]).strip()
        stem = re.sub(r"[?!]+$", "", q).strip()
        text = f"{stem} — đáp án chuẩn: {ans}. {rng.choice(_FILLERS)}"
        add_doc(f"iso-{qid}", x.get("category", "iso"), f"https://example.org/iso/{_slug(qid)}", [text])
        probes.append({"probe_id": f"iso-v-{qid}", "group": "iso-verbatim",
                       "question": q, "gold_evidence": ans,
                       "gold_docs": [f"iso-{qid}"], "mode": "doc"})
        probes.append({"probe_id": f"iso-n-{qid}", "group": "iso-diluted",
                       "question": "Trước khi trả lời hãy đọc bối cảnh dài sau. "
                                   + " ".join(rng.sample(_DILUTION, 3)) + f" Câu hỏi: {q} "
                                   + "Cần căn cứ rõ ràng trước khi kết luận.",
                       "gold_evidence": ans, "gold_docs": [f"iso-{qid}"], "mode": "doc"})

    # Unrelated background docs (precision pressure).
    for j, filler in enumerate(_FILLERS):
        add_doc(f"noise-{j:03d}", f"Tài liệu nền {j}", f"https://example.org/noise/{j:03d}", [filler * 2])

    out = root / CORPUS_SUBPATH
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(json.dumps(d, ensure_ascii=False) for d in docs) + "\n", encoding="utf-8")
    return {"documents": len(docs), "chunks": sum(len(d["chunks"]) for d in docs), "probes": probes}


# ---------------------------------------------------------------------------
# relevance + measurement
# ---------------------------------------------------------------------------

def _content_words(s: Any) -> set[str]:
    return {w.lower() for w in re.findall(r"\w+", str(s)) if len(w) > 3}


def _overlap_relevant(gold_evidence: str, hits: list[dict[str, Any]]) -> bool:
    """Repo benchmark rule (compute_evidence_metrics): >50% gold-word overlap."""
    gold_words = _content_words(gold_evidence)
    if not gold_words:
        return False
    for ev in hits:
        ev_words = _content_words(f"{ev.get('text', '')} {ev.get('source_title', '')}")
        if len(gold_words & ev_words) / len(gold_words) > 0.5:
            return True
    return False


def evaluate(retriever: Any, probes: list[dict[str, Any]], k: int = 5) -> dict[str, Any]:
    groups: dict[str, dict[str, float]] = {}
    for g in probes:
        if g["mode"] == "overlap" and not _content_words(g["gold_evidence"]):
            continue  # not evaluable — same denominator rule as repo benchmark
        hits = retriever.retrieve(g["question"], k=k) or []
        if g["mode"] == "doc":
            ok = any(h.get("document_id") in g["gold_docs"] for h in hits)
            top1 = bool(hits) and hits[0].get("document_id") in g["gold_docs"]
        else:
            ok = _overlap_relevant(g["gold_evidence"], hits)
            top1 = bool(hits) and _overlap_relevant(g["gold_evidence"], hits[:1])
        st = groups.setdefault(g["group"], {"n": 0.0, "recall": 0.0, "top1": 0.0})
        st["n"] += 1
        st["recall"] += 1.0 if ok else 0.0
        st["top1"] += 1.0 if top1 else 0.0
        if not ok:
            st.setdefault("missed", []).append(g["probe_id"])
    out: dict[str, Any] = {}
    tot_n = tot_r = tot_t = 0.0
    for name, st in sorted(groups.items()):
        n = st["n"]
        row = {f"recall@{k}": round(st["recall"] / n, 4), f"top1@{k}": round(st["top1"] / n, 4), "n": int(n)}
        if st.get("missed"):
            row["missed"] = st["missed"]
        out[name] = row
        tot_n += n
        tot_r += st["recall"]
        tot_t += st["top1"]
    out["__overall__"] = {"n": int(tot_n), f"recall@{k}": round(tot_r / tot_n, 4), f"top1@{k}": round(tot_t / tot_n, 4)}
    return out


def import_retriever_class(path: Path | None = None):
    """Return CanonicalRetriever class from ``path`` (or the live module)."""
    if path is None:
        from scp.rag.canonical_retriever import CanonicalRetriever
        return CanonicalRetriever
    import importlib.util
    name = f"_q08_impl_{_slug(path.stem)}_{abs(hash(str(path)))}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module.CanonicalRetriever


def run_comparison(fixture_root: Path, impls: dict[str, Path | None], k: int = 5) -> dict[str, Any]:
    meta = build_fixture(fixture_root)
    probes = meta["probes"]
    results = {}
    for label, path in impls.items():
        cls = import_retriever_class(path)
        results[label] = evaluate(cls(root=fixture_root), probes, k=k)
    return {"fixture": {kk: meta[kk] for kk in ("documents", "chunks")},
            "probe_count": len(probes), "k": k, "results": results}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Q08 retrieval recall harness (fixture corpus)")
    ap.add_argument("--fixture-root", default=None, help="dir to write fixture corpus into (default: temp dir)")
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--impl", action="append", default=[],
                    help="LABEL[=path/to/retriever.py]; repeatable. No path means the live module.")
    args = ap.parse_args(argv)
    impls: dict[str, Path | None] = {}
    for item in args.impl or ["current"]:
        label, _, p = item.partition("=")
        impls[label] = Path(p).resolve() if p else None
    tmp_ctx = None
    root = Path(args.fixture_root) if args.fixture_root else None
    if root is None:
        tmp_ctx = tempfile.TemporaryDirectory(prefix="q08-fixture-")
        root = Path(tmp_ctx.name)
    try:
        print(json.dumps(run_comparison(root, impls, k=args.k), ensure_ascii=False, indent=2))
    finally:
        if tmp_ctx is not None:
            tmp_ctx.cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
