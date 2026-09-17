"""CanonicalRetriever — Okapi BM25 lexical retrieval over the canonical corpus.

Q08 upgrade (branch audit/runtime-guard-AUDIT-20260909, 2026-09-15):

The pre-Q08 scorer was a hand-tuned heuristic (idf-sum x coverage plus hard
pruning rules). Measured on the Q08 fixture benchmark
(``scp/rag/retrieval_eval.py``, legacy impl extracted from bf5af3b):
paraphrased and context-diluted queries collapsed to recall@5 = 0.0 because
three hard ``continue`` rules pruned every candidate that changed word order
or added query length; and Vietnamese 2-character words (mỹ, nga, hà, đô,
ấn, độ, ...) were dropped by the >=3-char tokenizer, which turned whole
queries into a single generic token ranked by insertion order (geo-verbatim
recall@5 = 0.88).

Changes here:
  * Standard Okapi BM25 with k1=1.2, b=0.75 (Lucene-style non-negative idf
    ``ln(1 + (N - df + 0.5) / (df + 0.5))``) over chunk text, plus a
    second BM25 field over ``source_title`` (BM25F-style additive weight).
  * Phrase bonus: +0.35 per matched *distinctive* query bigram (adjacent
    query terms that also occur adjacent in the chunk), preserving the
    order sensitivity the old heuristic had but now as a soft signal.
  * Tokenizer now keeps short Vietnamese words (any token containing a
    Vietnamese diacritic character), so 'thủ đô Mỹ' is no longer one token.
  * Hard pruning rules removed; ranking is continuous.

Intentionally NOT included: vector/embedding hybrid. This repo declares no
embedding dependency (requirements.txt -> structlog only; scp/requirements
txt explicitly avoids pulling torch). BM25 stays stdlib-only. If an
embedding provider is introduced later, fuse at the ``score = text + title
+ phrase`` seam below (e.g. Reciprocal Rank Fusion of both candidate lists).

Public API contract preserved for callers (``scp/api/routes/v105_routes.py``
/v105/rag/query, ``tools/run_local_rag_answer_drafts_v1.py``,
tests/T03 test_flow_14, tests/test_m1_empirical_challenger.py):
  * ``CanonicalRetriever(root=None)`` reads the same two corpus paths.
  * ``retrieve(question, k=5) -> list[dict]`` with exactly the keys
    {chunk_id, document_id, source_url, source_title, text, retrieval_score,
    term_coverage, matched_bigrams}; ``matched_bigrams`` is an int; the k
    cap ``max(1, min(k, 8))`` and one-result-per-document dedup are kept.
  * ``contexts(question, k=5)`` and ``get_canonical_retriever()`` unchanged.
"""
from __future__ import annotations
import json, re, threading, math
from collections import Counter
from pathlib import Path
from typing import Any

import logging
logger = logging.getLogger(__name__)

BM25_K1 = 1.2
BM25_B = 0.75
TITLE_WEIGHT = 0.4
PHRASE_WEIGHT = 0.35

# Vietnamese words are frequently 1-2 characters (đô, mỹ, nga, hà, ý, ấn, độ,
# bỉ, áo, ...). ASCII 2-char tokens stay excluded (they are almost always
# English noise not covered by the stopword list).
_TOKEN_RE = re.compile(r'[\wÀ-ỹ]+', re.UNICODE)
_VN_CHARS = set('àáảãạăắằẳẵặâấầẩẫậèéẻẽẹêếềểễệìíỉĩịòóỏõọôốồổỗộơớờởỡợùúủũụưứừửữựỳýỷỹỵđ')
_STOP = set('the a an and or of to in on for from is are was were be been being what who when where why how which with that this these those do does did as by at it its their his her our your into about between general terms answer briefly source available explain give say if evidence missing main function difference signal'.split())
_GENERIC = {('united', 'states'), ('new', 'york'), ('world', 'war'), ('president', 'united')}
# Internal-only item keys; stripped from retrieve() output.
_INTERNAL_KEYS = ('terms', 'norm', 'title_terms', 'tf', 'ttf', 'tlen', 'ttlen')


def _keep_token(x: str) -> bool:
    if x in _STOP:
        return False
    if len(x) == 1:
        return x in _VN_CHARS  # 'ý', 'đ' — real Vietnamese words; ASCII letters are noise
    if len(x) == 2 and x.isascii():
        return False  # 'us', 'he', 'la' — ASCII 2-char tokens are almost always noise
    return True


def _tokens(s: str) -> list[str]:
    out = []
    for x in _TOKEN_RE.findall(str(s).lower()):
        if not _keep_token(x):
            continue
        if x.endswith('ly') and len(x) > 5:
            x = x[:-2]
        elif x.endswith('s') and len(x) > 5:
            x = x[:-1]
        out.append(x)
    return out


def _records(path: Path):
    s = path.read_text(encoding='utf-8'); d = json.JSONDecoder(); i = 0
    while i < len(s):
        while i < len(s) and s[i].isspace():
            i += 1
        if i >= len(s):
            break
        try:
            o, j = d.raw_decode(s, i); yield o; i = j
        except json.JSONDecodeError:
            logger.debug('_records: json.JSONDecodeError ignored', exc_info=True)
            k = s.find('{', i + 1)
            if k < 0:
                break
            i = k


def _bm25_tf(idf: float, tf: int, qlen_norm: float) -> float:
    # term component: idf * f*(k1+1) / (f + k1*(1-b + b*|d|/avgdl))
    return idf * tf * (BM25_K1 + 1.0) / (tf + BM25_K1 * qlen_norm)


class CanonicalRetriever:
    def __init__(self, root: Path | None = None):
        self.root = root or Path(__file__).resolve().parents[2]
        self.paths = [self.root / 'data' / 'rag_corpus' / 'canonical-v2-20260817' / 'corpus_all_fetched.jsonl',
                      self.root / 'data' / 'rag_corpus' / 'canonical-v3-20260817' / 'verified_seed_corpus.jsonl']
        self._lock = threading.Lock(); self._loaded = False
        self.items = []
        self.df = {}; self.postings = {}
        self.tdf = {}; self.title_postings = {}
        self.avg_len = 0.0; self.avg_title_len = 0.0
        self._idf_cache = {}; self._tidf_cache = {}

    # ------------------------------------------------------------------ load
    def _load(self):
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            for path in self.paths:
                if not path.exists():
                    continue
                for doc in _records(path):
                    url = doc.get('final_url') or doc.get('source_url') or ''
                    title = str(doc.get('source_title') or '')
                    if (not str(url).startswith(('http://', 'https://')) or 'bing.com/' in str(url)
                            or any(bad in str(url).lower() for bad in ('mangatown.com', 'free-work.com', 'xhamster.com', 'pornhub.com'))):
                        continue
                    for c in doc.get('chunks') or []:
                        text = str(c.get('text') or '').strip()
                        toks = _tokens(text)
                        ttoks = _tokens(title)
                        if text and toks:
                            idx = len(self.items)
                            tf = Counter(toks)
                            self.items.append({
                                'chunk_id': c.get('chunk_id'), 'document_id': c.get('document_id'),
                                'source_url': url, 'source_title': title, 'text': text,
                                'terms': set(toks), 'norm': ' '.join(toks), 'title_terms': set(ttoks),
                                'tf': tf, 'tlen': len(toks),
                                'ttf': Counter(ttoks), 'ttlen': len(ttoks),
                            })
                            for t, f in tf.items():
                                self.postings.setdefault(t, []).append((idx, f))
                            for t in set(ttoks):
                                self.title_postings.setdefault(t, []).append(idx)
            self.df = {t: len(ids) for t, ids in self.postings.items()}
            self.tdf = {t: len(ids) for t, ids in self.title_postings.items()}
            n = len(self.items) or 1
            self.avg_len = max(1.0, sum(i['tlen'] for i in self.items) / n)
            self.avg_title_len = max(1.0, sum(i['ttlen'] for i in self.items if i['ttlen']) / max(1, sum(1 for i in self.items if i['ttlen'])))
            self._loaded = True

    def _idf(self, t: str) -> float:
        v = self._idf_cache.get(t)
        if v is None:
            df = self.df.get(t, 0)
            v = math.log(1.0 + (len(self.items) - df + 0.5) / (df + 0.5))
            self._idf_cache[t] = v
        return v

    def _tidf(self, t: str) -> float:
        v = self._tidf_cache.get(t)
        if v is None:
            df = self.tdf.get(t, 0)
            v = math.log(1.0 + (len(self.items) - df + 0.5) / (df + 0.5))
            self._tidf_cache[t] = v
        return v

    # --------------------------------------------------------------- retrieve
    def retrieve(self, question: str, k: int = 5) -> list[dict[str, Any]]:
        self._load()
        qt = _tokens(question); qset = set(qt); q_bigrams = set(zip(qt, qt[1:]))
        if not qset:
            return []
        candidates: set[int] = set()
        for t in qset:
            for idx, _f in self.postings.get(t, ()):
                candidates.add(idx)
            candidates.update(self.title_postings.get(t, ()))
        scored = []
        for idx in candidates:
            item = self.items[idx]
            norm_len = 1.0 - BM25_B + BM25_B * (item['tlen'] / self.avg_len)
            s_text = 0.0
            for t in qset:
                f = item['tf'].get(t)
                if f:
                    s_text += _bm25_tf(self._idf(t), f, norm_len)
            s_title = 0.0
            if item['ttlen']:
                tnorm_len = 1.0 - BM25_B + BM25_B * (item['ttlen'] / self.avg_title_len)
                for t in qset:
                    f = item['ttf'].get(t)
                    if f:
                        s_title += _bm25_tf(self._tidf(t), f, tnorm_len)
            item_tokens = item['norm'].split()
            matched = q_bigrams & set(zip(item_tokens, item_tokens[1:]))
            distinctive = {p for p in matched if p not in _GENERIC}
            mb = len(distinctive)
            coverage = len(qset & item['terms']) / max(1, len(qset))
            score = s_text + TITLE_WEIGHT * s_title + PHRASE_WEIGHT * mb
            scored.append((score, coverage, mb, idx, item))
        scored.sort(key=lambda x: (-x[0], -x[1], -x[2], x[3]))
        out = []; seen = set()
        for score, cov, mb, idx, item in scored:
            if item['document_id'] in seen:
                continue
            seen.add(item['document_id'])
            out.append({key: value for key, value in item.items() if key not in _INTERNAL_KEYS}
                       | {'retrieval_score': round(score, 4), 'term_coverage': round(cov, 4), 'matched_bigrams': mb})
            if len(out) >= max(1, min(k, 8)):
                break
        return out

    def contexts(self, question: str, k: int = 5):
        return [f"[chunk_id={x['chunk_id']}] source_url={x['source_url']}\n{x['text']}" for x in self.retrieve(question, k)]


_default = None


def get_canonical_retriever():
    global _default
    if _default is None:
        _default = CanonicalRetriever()
    return _default
