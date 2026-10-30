"""[DEHARDCODE 2026-10-30] Leak check: câu hỏi của bộ eval realq KHÔNG được xuất hiện trong
``scp/**/*.py`` hoặc ``data/**`` (json/jsonl/md/txt).

Luật owner: bộ test câu hỏi thật nằm NGOÀI ``scp/``; không tune SCP theo bộ test.
Phát hiện: nguyên câu (>= MIN_FULL_WORDS từ, sau chuẩn hoá NFKC+lower+token) hoặc bất kỳ
đoạn SHINGLE từ liên tiếp nào trùng với corpus repo. Chỉ báo qid, KHÔNG in nội dung câu.

CLI: python tools/realq_leak_check.py [REALQ_DIR]   (exit 0 sạch, 1 rò, 2 thiếu dữ liệu)
"""
from __future__ import annotations

import json
import re
import sys
import unicodedata
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SETS = ("realq_v1_dev.jsonl", "realq_v1_holdout.jsonl")
SHINGLE = 10
MIN_FULL_WORDS = 5
DATA_SUFFIXES = {".json", ".jsonl", ".md", ".txt"}


def norm_words(text: str) -> list[str]:
    return re.findall(r"\w+", unicodedata.normalize("NFKC", text).lower())


def corpus_docs(root: Path = REPO) -> list[list[str]]:
    paths = list((root / "scp").rglob("*.py")) + [
        p for p in (root / "data").rglob("*") if p.is_file() and p.suffix in DATA_SUFFIXES
    ]
    docs = []
    for path in paths:
        try:
            docs.append(norm_words(path.read_text(encoding="utf-8", errors="ignore")))
        except OSError:
            continue
    return docs


def find_leaks(questions: list[tuple[str, str]], docs: list[list[str]]) -> dict[str, list[str]]:
    joined = " " + " ".join(" ".join(d) for d in docs) + " "
    shingles = {" ".join(d[i:i + SHINGLE]) for d in docs for i in range(len(d) - SHINGLE + 1)}
    full, ngram = [], []
    for qid, text in questions:
        words = norm_words(text)
        if len(words) >= MIN_FULL_WORDS and (" " + " ".join(words) + " ") in joined:
            full.append(qid)
        if any(" ".join(words[i:i + SHINGLE]) in shingles for i in range(len(words) - SHINGLE + 1)):
            ngram.append(qid)
    return {"full_question_in_repo": full, "ten_gram_overlap": ngram}


def load_questions(realq_dir: Path) -> list[tuple[str, str]] | None:
    files = [realq_dir / s for s in SETS]
    if not all(f.is_file() for f in files):
        return None
    out = []
    for f in files:
        for line in f.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            out.append((row["qid"], row["text"]))
    return out


def main(argv: list[str]) -> int:
    realq_dir = Path(argv[1]) if len(argv) > 1 else Path("D:/scp-eval-data/samples")
    qs = load_questions(realq_dir)
    if qs is None:
        print(f"realq dataset not present at {realq_dir} — cannot verify (exit 2)")
        return 2
    leaks = find_leaks(qs, corpus_docs())
    n = len(leaks["full_question_in_repo"]) + len(leaks["ten_gram_overlap"])
    print(json.dumps({"questions": len(qs), "full_hits": len(leaks["full_question_in_repo"]),
                      "ngram_hits": len(leaks["ten_gram_overlap"]), "qids": sorted(set(sum(leaks.values(), [])))[:20]}))
    return 1 if n else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
