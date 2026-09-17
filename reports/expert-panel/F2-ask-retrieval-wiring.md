# F-2 — Wire CanonicalRetriever vào /ask (LOOKUP/thiếu-context → auto-evidence)

- Worker: **F-2** · Branch: `audit/runtime-guard-AUDIT-20260909` · Baseline commit: `1b93920` · Ngày đo: 2026-09-15 · Trạng thái: **chưa commit** (orchestrator làm)
- Skills đã nạp: `scp-dna` (vòng evidence-first), `scp-reality-verifier` (cấp bằng chứng), `scp-safe-latency-optimizer` (đo latency trước khi chấp nhận), `scp-web-orchestration-safety` (corpus = dữ liệu untrusted, đi qua semantic firewall sẵn có của `_ask_impl`)
- File thay đổi (đúng scope được phép):
  - `scp/runtime/question_router.py` — + seam `attempt_canonical_retrieval` + env/gates + KPI `ask_retrieval_*`
  - `scp/ask_kernel_adapter.py` — `_attach_canonical_evidence` + call site trong `run_rag` (duy nhất 1 chỗ)
  - `scp/api_server_parts/_ask_impl.py` — surface evidence đã-chấm vào `slm_trace` (provenance output, không phải input quyết định)
  - `tests/T07_learning/test_f2_ask_retrieval_wiring.py` — mới, 32 test
  - `tests/T02_contract/test_flow_02b_f2_ask_retrieval_http.py` — mới, 1 test Level-C in-process
- KHÔNG sửa: `stream_routes.py`, `v105_routes.py`, `canonical_retriever.py`, `api_server.py`, `lifespan.py`, config/logging — đã verify bằng `git status` (diff của tôi chỉ 3+2 file trên).

## 1. Problem statement

Q08 đã có BM25 `get_canonical_retriever()` (recall@5 = 0.9812 fixture) NHƯNG /ask không gọi nó ở bất kỳ đâu: `StreamAskRequest` không có field context (F-2 của Q08), và `get_canonical_retriever` chỉ sống sau `/v105/rag/query` (admin). Hệ quả: `D_evidence_recall = 0.3846` (bench 2026-09-12) bất động với mọi cải thiện retriever.

## 2. Why chain (DNA #1)

1. **Tại sao retriever tốt mà e2e không đổi?** → vì /ask không có đường nào tới retriever. Audit còn tìm ra tầng sâu hơn (NF-1): benchmark đọc `response.slm_trace` (`benchmark/run_benchmark_v2.py:607`), còn `req.contexts` chỉ đi vào judge/verify — **dù có nạp contexts đúng pipeline mà không surface vào slm_trace thì số e2e vẫn không đổi**. Wiring phải làm cả hai việc.
2. **Tại sao 0.3846 tồn tại được?** → các câu LOOKUP chạy dạng "general chat ask": `verify_response.is_rag_ask=False`, tier1 `check_grounding` bỏ qua vì context rỗng (`scp/security/tier1_guard.py:88` — `if ... and (context or "").strip()`), answer LLM không bị đòi bằng chứng.
3. **Tại sao không nối thẳng vào `StreamAskRequest`?** → file bị cấm sửa (Q19 committed); seam đúng hơn nằm ở `run_rag` — nơi CẢ 3 transport (`/ask` api_server.py:587, stream_routes.py:132, openai_compat.py:112) đi qua → 1 chỗ, tối thiểu.
4. **Tại sao gate bằng L0, không L2?** → L2 là 1 LLM call 30–260s (bằng chứng S20 trong lease comment) cho một read-path phụ — vi phạm scp-safe-latency-optimizer. L0 regex LOOKUP (conf ≥ SCP_T2_MIN_CONFIDENCE) đủ và deterministic.

## 3. Change đã làm

`run_rag`, nhánh generation (SAU S24 fork — fork thắng đã có `data_api_evidence`, không double-retrieve; SAU `begin()` — durable identity/input_hash giữ đúng body client):

```
no client contexts/retrieved_context
  → question_router.attempt_canonical_retrieval(question)   # L0==LOOKUP ∧ conf≥ngưỡng
      → CanonicalRetriever.retrieve(q, k=5)  (BM25 Q08)     # qua asyncio.to_thread + wait_for ≤10s
      → filter retrieval_score ≥ floor(1.0), trunc 2400/chunk, tổng ≤ 8000
  → req.contexts = "[chunk_id=...] source_url=...\n<text>"  # format đúng CanonicalRetriever.contexts()
  → request.state.scp_ask_auto_evidence = [hits có cấu trúc]
_ask_impl: evidence đi vào judge grounding (qua semantic firewall SẴN CÓ tại _ask_impl:362-367)
         + được surface vào slm_trace (slm_name="canonical_bm25") — CHỈ khi answer không bị withheld
verify_response: đọc đúng req.contexts đó → grounded_ratio/evidence_context_count + judge context
```

Mọi fail-closed path (kill-switch off, REASONING, ambiguous, empty corpus, mọi hit dưới floor, lỗi retriever, timeout) → **không đổi gì** so với hành vi cũ — không bịa evidence.

Bất biến chống vòng lặp tự-duyệt (đã có test): hit chọn theo **question** (BM25), không theo candidate answer; answer mới của reflection đi qua đúng `verify_response` cũ; `slm_trace` là output của verdict đã chốt, không phải input check nào; withheld/KILL/FAIL boundary vẫn xóa toàn bộ trace.

## 4. Bằng chứng rằng verification KHÔNG bị nới (nghịch lại còn SIẾT)

- 5 check `verify_response` giữ nguyên; `rag_evidence_bound` vẫn là marker no-op (xem NF-3).
- Khi có evidence: `is_rag_ask` flip → tier1 `REJECT_GROUNDING` trở nên **áp dụng** với LOOKUP ask (trước đây vô hiệu vì context rỗng). Test `test_run_rag_anti_loop_ungrounded_answer_still_rejected`: LLM-judge gate always-PASS mà answer lệch corpus vẫn bị CONTRADICTED → withheld. Đây là bằng chứng trực tiếp retrieval ≠ đường tự-duyệt.
- Test `test_l0_gate_does_not_call_llm`: chốt bằng monkeypatch-raise rằng seam không gọi L2.

## 5. Đo TRƯỚC/SAU

| Metric | TRƯỚC (baseline 1b93920) | SAU (branch này) | Lineage / giới hạn |
|---|---:|---:|---|
| Retriever recall@5 fixture | 0.9812 | 0.9812 (không đổi — file cấm sửa) | `python -m scp.rag.retrieval_eval --k 5 --impl current` |
|BM25 top-1 score, 319 probes fixture | — | min 5.51 · median 10.6–23.3 → floor 1.0 giữ 319/319 | script đo tạm, số trong report |
| Đóng góp của retriever vào /ask | **0** (không có đường gọi) | LOOKUP-ask có evidence; sim 50/50 gold `_GEO_FACTS` khớp >50% content-word trong slm_trace surface ⇒ **proxy 1.00** | FIXTURE corpus + đúng luật `compute_evidence_metrics`; KHÔNG phải số prod |
| `D_evidence_recall` e2e trên corpus prod | 0.3846 (bench live 2026-09-12) | **INSUFFICIENT** — corpus prod absent trong checkout (Q08 F-3) ⇒ seam trả `[]` trên PC này; chạy lại bench khi orchestrator cấp verified-v3 | benchmark harness cần live LLM + corpus |
| p50/p95 auto-retrieval trên request (warm) | — | **0.59ms / 0.90ms** (fixture 266 chunks); cold load 174ms một lần/process | prod corpus chưa đo — đã đặt trần: `to_thread` + `wait_for ≤10s` + heartbeat S20 giữ lease; timeout → hành vi cũ |

Cấm-nới compliance: không bỏ/skip/xfail test nào; test mới chạy subsystem thật (kernel SQLite tmp thật, TaskKernel lifecycle thật, verify_response thật, retriever thật, TestClient + route thật, fixture provider OpenAI-compat local theo pattern T02 — credential isolation, không mock logic).

## 6. VERIFY đã chạy (exit 0 tất cả)

| Lệnh | Kết quả |
|---|---|
| `python -m pytest -q tests/T02_contract/test_flow_02_ask_chat_scp_standard.py tests/T02_contract/test_flow_02b_f2_ask_retrieval_http.py tests/T07_learning/test_f2_ask_retrieval_wiring.py tests/T07_learning/test_q08_bm25_retrieval.py -p no:cacheprovider` | **79 passed** (gồm Level-C HTTP: slm_trace mang `canonical_bm25`; ambiguous & client-contexts → không mang) |
| `python -m pytest -q tests/T07_learning/test_ask_lookup_fork.py test_ask_reflection_selfcorrection.py` (trong combined run) | pass — fork/reflection không đổi hành vi |
| `python -m pytest -q tests/T04_kernel/ tests/T03_capability/test_flow_14_reintegrated_systems_scp_standard.py` | **325 passed, 23 skipped** — 23 skip toàn bộ là infra-skip khai báo sẵn (`SCP_PG_TEST_DSN`, file `test_pg_*`) của worker khác, không phải của F-2 |
| `python -m pytest -q tests/T07_learning/test_question_router_cascade.py tests/T07_learning/test_question_router_goldset.py` | **45 passed** |
| `python -m pytest -q tests/test_m1_empirical_challenger.py -k rag` | **8 passed** |
| `python tools/t00_meta_audit.py` | exit **0** — "All integrity checks passed (0 new regressions)"; DEBT/L4 liệt kê file của writer khác |
| `python tools/verify_scp_test_skill_contract.py` | exit **0** — PASS_WITHIN_SCOPE |

## 7. causal map

```
Q08: BM25 recall 0.50→0.98 (retriever-level)
   └─ F-2: /ask KHÔNG gọi retriever; và benchmark đọc slm_trace, không đọc contexts
          │   (NF-1 — 2 tầng đứt gãy, không phải 1)
          ▼
   [seam run_rag] LOOKUP∧no-context → BM25 → req.contexts (verify+tier1+judge)
                                      → request.state → _ask_impl → slm_trace
          ▼
   e2e-evidence đường-đo-được: 0 (trước) → có evidence (sau)
   tier1 grounding: bỏ-quạ (context rỗng) → ÁP DỤNG (siết, không nới)
   PC hiện tại: corpus prod ABSENT → retrieve()=[] → hành vi y hệt cũ (fail-closed)
          └─⇒ số e2e prod CHỈ đổi khi orchestrator cấp verified-v3 (Q08 F-3 vẫn chặn)
```

## PHÁT HIỆN MỚI (NEW FINDINGS)

| # | Vị trí | Severity | Nội dung | Next slot |
|---|---|---|---|---|
| **NF-1** | `benchmark/run_benchmark_v2.py:607` + `scp/runtime/question_router.py` (`attempt_lookup_fork` trả `"slm_trace": []`) | **high** | Benchmark `D_evidence_recall` CHỈ đọc `slm_trace`. Hệ quả kép: (a) F-2 phải surface evidence vào slm_trace mới đo được — đã làm; (b) **S24 LOOKUP-fork trả lời bằng data-API nhưng `slm_trace=[]`** → mọi answer fork (kể cả đúng, có provenance trong answer text) đóng góp 0 vào evidence_recall. Fork evidence chỉ sống ở input verify (`data_api_evidence`), bị drop khỏi response. Đây là lý do độc lập thứ hai khiến số e2e thấp, chưa ai ghi. | Slot S24/owner: surface `data_api_evidence` thành entry slm_trace khi fork PASS (không tự-duyệt — cùng pattern boundary withheld) |
| **NF-2** | `scp/api/routes/stream_routes.py:26-28` | low (đã giảm severity) | StreamAskRequest vẫn không có field contexts (file cấm sửa, Q19) — NHƯNG sau F-2 stream tự hưởng auto-retrieval qua `run_rag` dùng chung. Phần còn thiếu duy nhất: client không thể chủ động gửi contexts qua stream. | Q19 slot nếu owner muốn contexts-on-stream |
| **NF-3** | `scp/ask_kernel_adapter.py:487` | medium | `checks["rag_evidence_bound"] = True` là **hằng số không bao giờ fail**; `grounded_ratio` được tính/ghi-trace nhưng không gate verdict nào. "RAG contract" hiện chỉ enforce grounding qua tier1 trong judge. Nếu tier1 lỗi import/imperfect thì grounding không còn gate thứ hai. Không tự siết (đổi semantics gate cần owner). | Đề xuất owner: gate `grounded_ratio ≥ τ` trong verify — slot riêng |
| **NF-4** | `scp/runtime/judge.py` + `ask_kernel_adapter.verify_response` | informational | Double-judge (handler judge + verify judge) là kiến trúc có sẵn, TRƯỚC F-2. Sau F-2 hai judge dùng **cùng một** corpus context → cùng lineage (DNA #5): tăng cường độ tin grounding nhưng không còn "2 nguồn độc lập" cho phần context. Đã ghi nhận, không đổi design. | Reviewer |
| **NF-5** | runtime PC | low | Sau test run: `opentelemetry` "Exception while exporting Span ... I/O operation on closed file" (telemetry shutdown race, noise ngoài assert). Không phải lỗi seam F-2. | Cleanup slot |

## Limitations (Reality Verifier)

- Cấp bằng chứng: **C (end-to-end in-process workload)** cho *hành vi wiring* (route→adapter→kernel→retriever→judge fixture→response trace); **INSUFFICIENT** cho *số e2e trên corpus prod* — corpus verified-v3 absent (Q08 F-3), sim 50/50 là FIXTURE + đúng luật overlap của benchmark, không phải bench live lại.
- Provider chain trong test là fixture local (mô hình sau socket), judge semantics không phải model prod; verdict thật trên prod cần run bench lại (slot orchestrator, sau cấp corpus).
- Cold-load prod corpus chưa đo; đã bound 10s fail-closed — nếu prod corpus load >10s, LOOKUP ask sẽ silently giữ hành vi cũ (KPI `ask_retrieval_empty`/timeout log WARNING cho thấy).

## Open questions (DNA #23)

1. `SCP_ASK_RETRIEVAL_MIN_SCORE=1.0` an toàn trên fixture (min 5.51); phân bố score trên verified-v3 (N lớn hơn, title trùng hàng loạt) cần đo lại trước khi封印 gate số.
2. Với NF-1(b): benchmark slot có nên tính fork evidence khi so sánh trước/sau F-2 không? (Hai nguồn độc lập của cùng một metric.)
3. Nên bật grounding-gate (NF-3) ở τ bao nhiêu — decision của owner vì nó đổi semantics withhold.
4. Reflection round-2 kế thừa auto-contexts (clone giữ nguyên contexts) — có cần re-retrieve nếu round-2 critique đổi hướng câu hỏi? Hiện: không.

## Rollback

`git checkout 1b93920 -- scp/runtime/question_router.py scp/ask_kernel_adapter.py scp/api_server_parts/_ask_impl.py` + xóa 2 file test mới. Hoặc runtime: `SCP_ASK_RETRIEVAL=0` (giữ code, tắt hành vi) — đã test.

## lệnh-tái-lập chính

```bash
python -m pytest -q tests/T02_contract/test_flow_02_ask_chat_scp_standard.py \
  tests/T02_contract/test_flow_02b_f2_ask_retrieval_http.py \
  tests/T07_learning/test_f2_ask_retrieval_wiring.py \
  tests/T07_learning/test_q08_bm25_retrieval.py -p no:cacheprovider   # 79 passed
python -m scp.rag.retrieval_eval --k 5 --impl current                  # overall recall@5 0.9812
python tools/t00_meta_audit.py && python tools/verify_scp_test_skill_contract.py
curl -s localhost:PORT/v100/routing/stats | jq '{ask_retrieval_attempts,ask_retrieval_hits,ask_retrieval_empty,ask_retrieval_errors}'
```
