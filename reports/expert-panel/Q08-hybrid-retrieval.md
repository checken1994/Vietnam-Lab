# Q08 — CanonicalRetriever: heuristic → Okapi BM25 (k1=1.2, b=0.75)

- Worker: Q08 · Branch: `audit/runtime-guard-AUDIT-20260909` · Baseline commit: `bf5af3b` (đã reconcile vs main)
- Ngày đo: 2026-09-15 · Trạng thái: **chưa commit** (branch có writer thứ hai — Gemini; orchestrator merge)
- File thay đổi (scope được phép):
  - `scp/rag/canonical_retriever.py` — scorer mới BM25 + tokenizer VN-aware, giữ nguyên public API
  - `scp/rag/retrieval_eval.py` — **mới**: harness fixture-corpus + goldset tái dùng data thật của repo
  - `tests/T07_learning/test_q08_bm25_retrieval.py` — **mới**: 12 test (no-mock, no-skip, no-xfail)

## 1. Problem statement

`evidence_recall = 0.3846` (bench 2026-09-12, `reports/benchmark-2026-09-12/bench_after_fix_seed42.json`, metric `D_evidence_recall`, 13 câu có gold). Retrieval lexical tự chế (`idf-sum × coverage` + 3 quy tắc cắt cứng) không phải BM25 chuẩn, không vector.

## 2. Why chain (DNA #1)

1. **Tại sao recall thấp?** → vì 3 rule `continue` trong scorer cũ (canonical_retriever.py cũ, dòng 58–63): (a) query 1-token phải khớp title/norm-start; (b) không có bigram giáp riêng thì loại trừ; (c) có bigram nhưng coverage < 0.75 thì cắt.
2. **Tại sao rule đó cắt nhiều?** → vì query thật đổi trật tự từ / thêm bối cảnh dài làm **coverage tụt** và **bigram giáp vỡ**, dù câu trả lời chứa đầy đủ trong corpus. Đo fixture: `geo-paraphrase` và `iso-diluted` recall@5 = **0.0** (mất trắng, không phải giảm dần).
3. **Tại sao geo-verbatim cũng chỉ 0.88?** → tokenizer cũ bỏ token ≤2 ký tự: `mỹ`, `nga`, `đô`, `ấn`, `độ`, `bỉ`, `áo` biến mất → query "thủ đô Mỹ" còn đúng token `thủ` (có mặt ở ~mọi doc) → tie-break rơi về **thứ tự chèn** (insertion order), chọn sai.
4. **Tại sao 0.3846 (e2e) khác số fixture?** → metric 0.3846 đo ở tầng `/ask` qua HTTP live (seed 42); retriever-level chưa từng được đo tách bạch. Harness này lấp khoảng đó (mục 5).

## 3. Evidence map (độc lập ≥2 lineage)

| Lineage | Nguồn | Ghi chú |
|---|---|---|
| Repo gold data | `scp/benchmark/question_generator.py::_GEO_FACTS` (50 cặp question→gold_evidence — chính là chuỗi sinh ra bench 0.3846) + `scp/benchmark/iso_comprehensive.jsonl` (110 câu static) | nhãn KHÔNG do tôi đặt; trích bằng `ast`, không import side-effect |
| Harness độc lập scorer | `scp/rag/retrieval_eval.py` — 2 chế độ relevance: `overlap` (đúng luật `>50% content-word overlap` của `scp/benchmark/run_benchmark_v2_parts/compute_evidence_metrics.py`) và `doc` (document-level recall chuẩn IR) | chạy cả legacy lẫn mới trên **cùng** corpus |
| Chép impl cũ từ git | `git show bf5af3b:scp/rag/canonical_retriever.py` (worktree == HEAD tại thời điểm đo — đã `diff` xác nhận) | legacy không bị drift |

**Missing piece (khai báo thật):** corpus prod **không tồn tại trong checkout này** (`data/rag_corpus/canonical-v2-20260817/…` và `canonical-v3-…` trống — đúng ghi nhận RAG-1000 input pool rỗng trước đó). Mọi số dưới đây là **FIXTURE-CORPUS**, chưa đo trên corpus prod. Relevance `overlap` có tính lỏng cố hữu (vd "Berlin là thủ đô của Đức" chỉ còn 1 content-word `>3 ký tự` là `berlin`) — giữ nguyên để khớp lineage benchmark của repo.

## 4. Change

- **BM25 Okapi chuẩn**: `k1=1.2`, `b=0.75`, idf Lucene không-âm `ln(1+(N−df+0.5)/(df+0.5))` (biến thể RWJ cổ điển cho idf âm khi `df>N/2` — đã cố ý tránh vì corpus nhỏ, term phổ biến dễ bị âm).
- **2 trường (BM25F-style additive)**: chunk text + `source_title` (weight 0.4, df/avgdl riêng cho title).
- **Phrase bonus mềm** `+0.35`/bigram giáp *distinctive` (giữ tín hiệu trật tự-từ của heuristic cũ nhưng không còn cắt cứng).
- **Tokenizer VN-aware**: giữ token 1–2 ký tự có dấu tiếng Việt (`đô`, `mỹ`, `nga`, `ý`…); vẫn bỏ ASCII ≤2 ký tự (noise Anh ngữ). Stoplist + stemming `ly/s` cũ giữ nguyên.
- **Bỏ cả 3 rule cắt**; ranking liên tục, tie-break deterministic `(-score, -coverage, -mb, idx)`.
- **Vector: KHÔNG bật.** Repo không khai báo embedding dependency nào (`requirements.txt` → chỉ `structlog`; `scp/requirements.txt` tự tay loại torch). `sentence-transformers` có trong venv máy tôi nhưng **không phải dep của repo** → thêm vào sẽ là heavy-dependency ngầm, cấm theo đề bài. Điểm fusion sau này: seam `score = s_text + TITLE_WEIGHT*s_title + PHRASE_WEIGHT*mb` (RRF với candidate list thứ hai nếu có embedding).
- **API public giữ 100%**: `CanonicalRetriever(root)`, `retrieve(question,k=5)` → đúng 8 key (`chunk_id, document_id, source_url, source_title, text, retrieval_score, term_coverage, matched_bigrams`; `matched_bigrams` là int), cap `max(1,min(k,8))`, dedup 1-result/document, `contexts()`, `get_canonical_retriever()`. Key nội bộ mới (`tf`,`ttf`,`tlen`,`ttlen`) bị strip khỏi output.

## 5. Đo TRƯỚC/SAU (số THẬT, fixture corpus 266 chunks / 319 probes, k=5)

Lệnh tái tạo:
```bash
python -m scp.rag.retrieval_eval --k 5 --impl "legacy=<git show bf5af3b:scp/rag/canonical_retriever.py>" --impl current
```

| Nhóm probe | n | legacy recall@5 | BM25 recall@5 | legacy top1 | BM25 top1 |
|---|---:|---:|---:|---:|---:|
| geo-verbatim (nguyên văn question repo) | 50 | 0.880 | **1.000** | 0.800 | 0.960 |
| geo-paraphrase (đổi trật tự/diễn đạt) | 50 | **0.000** | **0.980** | 0.000 | 0.960 |
| iso-verbatim | 102 | 0.990 | **1.000** | 0.980 | 0.971 |
| iso-diluted (question + bối cảnh dài) | 102 | **0.000** | **0.951** | 0.000 | 0.814 |
| en-capital (probe tiếng Anh) | 15 | 1.000 | 1.000 | 1.000 | 1.000 |
| **TỔNG HỢP (trung bình theo n)** | 319 | **0.5016** | **0.9812** | 0.4859 | **0.9185** |

Kết luận trung thực: recall **tăng thật và có cơ chế nhân quả** (bỏ cắt cứng + tokenizer), không tâng bốc: không đạt 1.0; 6 miss còn lại được giải thích ở mục 6 và đều **không** phải lỗi thuật toán cốt lõi (1 miss do gold data repo hỏng, 3 miss do fixture trùng fact ở 2 doc, 2 miss do va chạm lexical chung của VN text). Legacy trên cùng fixture: 0.5016 — mức 0.3846 của bench e2e là dòng đo khác (`/ask`), không phải retriever-level.

**Sensitivity (plateau, không phải đỉnh may mắn)** — overall recall@5 theo `PHRASE_WEIGHT × TITLE_WEIGHT`:

| P\T | 0 | 0.2 | 0.4 | 0.7 | 1.0 |
|---:|---:|---:|---:|---:|---:|
| 0.0 | .9812 | .9812 | .9781 | .9718 | .9655 |
| 0.2 | .9812 | .9812 | .9812 | .9812 | .9781 |
| 0.35★ | .9812 | .9843 | **.9812** | .9812 | .9781 |
| 0.6 | .9812 | .9843 | .9875 | .9843 | .9781 |
| 1.0 | .9843 | .9875 | .9875 | .9843 | .9843 |

★ = cấu hình chốt (0.35/0.4). Chênh với "đỉnh bảng" chỉ +0.006 (4/319 probe miss) — từ chối tune thêm để không overfit fixture.

**Latency (an toàn theo scp-safe-latency-optimizer — đo, không đoán):** avg query trên 319 probe: legacy 0.49 ms → BM25 **1.75 ms** (cold load 8→12 ms, corpus 266 chunks). Vẫn dưới ngưỡng 2 ms/query, không đổi risk tier, không bỏ verifier nào; rollback = `git checkout bf5af3b -- scp/rag/canonical_retriever.py`.

## 6. Miss còn lại (rã từng case)

| Probe | Thủ phạm | Phân loại |
|---|---|---|
| `geo-p-046` ("Thủ đô của Việt Nam cũ…") | gold là "Huế**曾是** thủ đô cổ của Việt Nam" — `曾是` 2 chữ Hán dính chuỗi `_GEO_FACTS` (question_generator.py:180) → overlap-rule chỉ còn 1 content-word; doc top lại chứa `Việt Nam` không chứa token ghép | **Data bug của repo** (NEW FINDING F-1), không phải lỗi scorer |
| `iso-n-iso_geo_001/002/010` | cùng một fact tồn tại 2 doc trong fixture (doc `iso-geo_…` và doc `geo-vn-…`); scorer trả doc geo (đúng nội dung) nhưng `doc`-mode chỉ nhận gold doc id | **Fixture artifact** (đánh trùng dữ liệu) |
| `iso-n-iso_phys_005`, `iso-n-iso_cyber_008` | token chung loãng (`định`, `dạng`, `đầu vào`…) giữa câu dilution và doc khác đẩy doc nhiễu lên top-5 | Va chạm lexical tự nhiên — chính xác là vùng mà vector/RRF sau này mới giải quyết được |

## 7. Verify đã chạy (evidence cấp B — integration thật, hermetic)

| Lệnh | Kết quả |
|---|---|
| `python -m pytest tests/T07_learning/test_q08_bm25_retrieval.py -q` | **12 passed** (gồm test BM25 **đối chiếu công thức tính tay** k1/b/idf; schema+cap+dedup; 5 gate recall theo nhóm; gate tổng thể ≥0.95) |
| `python -m pytest tests/test_m1_empirical_challenger.py -q -k rag` | **8 passed** (schema + k-cap + body corner cases qua `/v105/rag/query`) |
| `python -m pytest tests/T03_capability/test_flow_14_reintegrated_systems_scp_standard.py -q` | **80 passed** (wiring `v105 → rag.canonical_retriever` nguyên vẹn) |
| `python tools/t00_meta_audit.py` | **exit 0** — "All integrity checks passed (0 new regressions)" (DEBT/L4 cảnh báo liệt kê là file của writer khác, không phải scope Q08) |
| `python tools/verify_scp_test_skill_contract.py` | **PASS_WITHIN_SCOPE** (exit 0) |
| API contract script (mục 4) | keys đúng 8 field, `matched_bigrams:int`, không leak field nội bộ, empty-corpus → `[]`, singleton, deterministic |

Không mock scorer; không skip/xfail; không secret literal. **Giới hạn bằng chứng (Reality Verifier):** chưa chạy Docker/prod runtime vì corpus prod absent; số recall là fixture-level; claim "cải thiện retrieval tầng `/ask`" **CHƯA được chứng minh** và cũng không thể chứng minh khi ask chưa nối retriever (F-2).

## 8. causal map (hiển thị)

```
rule cắt cứng (coverage<0.75 | no-bigram | 1-token-title-rule)
        │  vỡ khi paraphrase/dilution ──────────────► geo-p 0.0 / iso-d 0.0 (fixture)
tokenizer ≥3 ký tự ──► mất 'mỹ nga đô ấn độ' ──► tie theo insertion order ──► geo-v 0.88
        ▼ (Q08)
bỏ cắt cứng + BM25 idf-bão-hòa tf + title field + phrase bonus + VN tokens
        ▼
geo-p 0.98 · iso-d 0.951 · geo-v/iso-v/en 1.0 · overall 0.50→0.98
        │
        └─ còn chặn ở tầng /ask: retrieval CHƯA được wire vào ask path (F-2) ⇒ 0.3846 e2e chưa tự đổi
```

## PHÁT HIỆN MỚI (NEW FINDINGS)

| # | Vị trí | Severity | Nội dung | Next slot |
|---|---|---|---|---|
| F-1 | `scp/benchmark/question_generator.py:180` | medium | Gold data hỏng: `"Huế曾是 thủ đô cổ của Việt Nam"` — 2 chữ Hán lẫn trong corpus gold của benchmark; làm 1 probe không-thể-đo đúng và là dấu vết scraper ô-nhiễm chưa bị lint | Slot data-clean benchmark (không thuộc scope Q08) |
| F-2 | `scp/api/routes/stream_routes.py:26-28` | **high** (với mục tiêu recall e2e) | `StreamAskRequest` không có field context/retrieval; `get_canonical_retriever` **không được import ở bất kỳ route nào** — retriever chỉ sống sau `/v105/rag/query` (admin). Improving retriever **không đổi** số `/ask` cho tới khi wire | Q07/orchestrator (wire ask → retriever + mở cap `contexts(k=5)` đã có sẵn API) |
| F-3 | `data/rag_corpus/…` (absent) | informational | Corpus prod trống trong checkout ⇒ mọi số RAG hiện tại là fixture; gate RAG chưa thể封印 bằng số thật | Orchestrator cấp corpus verified-v3 |
| F-4 | `tools/probe_retriever_v3.py:6` | low | Tool stale: dùng `r.path` (không tồn tại ở cả impl cũ lẫn mới) → `AttributeError` khi chạy | Cleanup tools (ngoài scope) |
| F-5 | scorer cũ | closed | Hành vi cap `min(k,8)` + `matched_bigrams:int` là **hợp đồng ngầm** test_m1 đang khóa; đã giữ nguyên và ghi thành test hồi quy | — |

## Open questions (DNA #23)

1. Cấu hình tối ưu trên corpus prod thật (df dài hơn nhiều, title trùng hàng loạt) có giữ plateau không?
2. `term_coverage` trong output giờ là coverage-set đơn thuần (không còn là thành phần điểm số) — caller dashboard nào đọc field này để hiển thị cần xác nhận.
3. Bật vector hybrid chỉ khi repo *khai báo* dep embedding — ai owns quyết định dependency policy?
4. Recall@5 fixture 0.98 không phải claim production; cần một run lại trên verified-v3 corpus thật trước khi đưa số này vào gate.
