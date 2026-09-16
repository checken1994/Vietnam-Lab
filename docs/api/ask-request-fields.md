# Trường `retrieved_context` trong request `/ask` — đặc tả theo code

- Phạm vi: schema của `POST /ask` (`AskRequest`), cách server dùng `retrieved_context`, cơ chế
  auto-retrieval (F-2) tự nạp bằng chứng corpus khi client **không** gửi context, env kill
  switches, và đường provenance vào response (`slm_trace`).
- Nguồn sự thật: code tại HEAD `018ed21` (2026-09-15). Mọi câu dưới đây dẫn `file:dòng` đã đọc
  trực tiếp; không có claim nào lấy từ memory/chat hay từ report F-2 mà không đối chiếu lại code.
- File liên quan: `reports/expert-panel/F2-ask-retrieval-wiring.md` (bối cảnh wiring),
  `reports/expert-panel/Q08-hybrid-retrieval.md` (retriever BM25 + F-3 corpus-prod-absent).

## 1. `/ask` và các transport đi qua cùng một adapter

| Entry point | Định nghĩa | Đường vào kernel |
|---|---|---|
| `POST /ask` (FastAPI, `response_model=AskResponse`) | `scp/api_server.py:561-564` | `adapter.run_rag(req, request, _ask_impl)` — `scp/api_server.py:587` |
| `POST /v1/chat/completions` (OpenAI-compatible) | `scp/api/routes/openai_compat.py:115`, `_run_canonical_ask` `:62-112` | `run_rag` — `scp/api/routes/openai_compat.py:112`; `AskRequest` được dựng **không có** `contexts`/`retrieved_context` (`:84-94`) |
| Stream (v105) | `StreamAskRequest` — `scp/api/routes/stream_routes.py:26-28` (chỉ có `question`, `ai_answer`) | dựng `canonical_req = AskRequest(question, ai_answer, source="v105_stream")` — `:114-118` rồi `run_rag` — `:132` |

Hệ quả: cả 3 transport đều đi qua `AskKernelAdapter.run_rag` (`scp/ask_kernel_adapter.py:1111-1184`).
Client **không thể** gửi `contexts`/`retrieved_context` qua stream hay OpenAI-compat — hai model
request đó không có field; chỉ `POST /ask` mới nhận được.

## 2. Schema `AskRequest` (nguyên văn theo code)

Định nghĩa tại `scp/api_server_parts/helpers.py:185-204`:

| Field | Kiểu / ràng buộc | Dòng | Ghi chú hành vi |
|---|---|---|---|
| `question` | `str`, `min_length=0`, `max_length=8000` | `:186` | bắt buộc |
| `ai_answer` | `str`, default `""`, max 10000 | `:187` | câu trả lời ứng viên để chấm |
| `domain` | `str`, default `"general"` | `:188` | |
| `confidence` | `float`, default 0.8, 0..1 | `:189` | |
| `session_id` | `str \| None` | `:190` | scope của idempotency |
| `source` | `str`, default `"api"` | `:191` | |
| `contexts` | `list[str]`, default `[]`, `max_length=8` | `:192` | bằng chứng dạng chunk do client gửi |
| **`retrieved_context`** | `str`, default `""`, **`max_length=96000`** | `:193` | **một chuỗi bằng chứng duy nhất** — chủ định dùng cho kết quả retrieval phía client (benchmark/batch/harness nhét cả bộ context vào đây — mục 6) |
| `ground_truth` | `str`, default `""`, max 4000 | `:194` | |
| `domain_override` | `str`, max 64 | `:195` | |
| `image_url` / `voice_url` | `str \| None` | `:197-198` | quét jailbreak đa phương thức |
| `image_data` | `str \| None`, max 8_000_000 | `:201` | data URL webcam |
| `conversation_history` | `list[dict[str,str]]`, max 8 | `:204` | history là context, không phải instruction |

Schema này được FastAPI tự expose qua `/docs`, `/redoc`, `/openapi.json` **trừ khi**
`SCP_PRODUCTION_MODE` bật (`scp/api_server.py:105`, `:369-370`, `:386-388`) — tức Swagger UI mặc
định (non-production) cho thấy field `retrieved_context`.

## 3. `retrieved_context` được dùng ở đâu trong pipeline

`retrieved_context` là **bằng chứng do client gửi lên** (input), khác với bằng chứng tự nạp
(F-2, chỉ ghi vào `contexts` — mục 4). Bốn chỗ tiêu dùng hiện có:

1. **Danh tính bền của ask (idempotency).** `run_rag` gọi `begin(req.question, req.contexts,
   req.retrieved_context, req.session_id)` — `scp/ask_kernel_adapter.py:1117-1124`. Cả ba thành
   phần vào `canonical_input_hash` (`:224-232`) và `task_id_for` (`:234-264`). Đổi
   `retrieved_context` ⇒ đổi `input_hash` ⇒ là ask khác, không bị dedupe. `begin` còn ghi
   metadata checkpoint `"retrieved_context_present": bool(...)` — `:327-341` (dòng 336).
   Request trùng tuyệt đối trong cửa sổ idempotency ⇒ `KernelError`
   ("stable logical ask already exists", `:361-382`) ⇒ response withheld
   (`_kernel_blocked_response`, `:930-946`).
2. **Grounding của judge phía generator (`_ask_impl`).** `_raw_evidence = contexts +
   [retrieved_context]` — `scp/api_server_parts/_ask_impl.py:359`; từng phần tử đi qua semantic
   firewall `inspect_untrusted` (import `:358`, loop `:362-368`, đếm
   `v98_context['semantic_firewall']` `:369-370`), rồi chuỗi ghép `_evidence_context` (`:371`)
   được truyền vào `judge.judge_with_react_fallback(..., context=_evidence_context, ...)` /
   `judge.judge(...)` (`:372-375`). Văn bản injection/firewall có thể bị loại **trước** khi judge
   thấy nó.
3. **Canonical verify (`verify_response`).** `scp/ask_kernel_adapter.py:399-413`: nếu
   `retrieved_context` strip khác rỗng thì được `contexts.append(retrieved_context)`
   (`:402-404`). Hệ quả:
   - `grounded_ratio` đo overlap content-word của answer với toàn bộ contexts (`:426-436`);
   - `is_rag_ask = bool(contexts)` (`:476`) → chỉ khi có bằng chứng mới áp cam kết grounding;
   - `checks["rag_evidence_bound"] = True` là **hằng số khi `is_rag_ask`** (`:486-487`, marker
     no-op — phát hiện NF-3 của report F-2, chưa gate theo `grounded_ratio`);
   - verifier còn gọi `RealityJudge.judge_async(..., context=" ".join(contexts))` (`:440-447`);
   - output lưu `evidence_context_count` + `evidence_context_hash` (`:493-497`).
   Verdict verify khác `VERIFIED` ⇒ `_safe_response` withhold answer (`:502-522`).
4. **Gate chặn fork S24 và chặn auto-retrieval.** Nếu `contexts` hoặc `retrieved_context`
   khác rỗng:
   - `attempt_lookup_fork` trả `None` ngay — `scp/runtime/question_router.py:917-921` (fork chỉ
     nhận "chat ask thuần");
   - guard của `_attach_canonical_evidence` trả về sớm — "client-supplied evidence wins — no
     auto-retrieval, no double" — `scp/ask_kernel_adapter.py:1038-1041`.

Tier-1 grounding (`scp/security/tier1_guard.py:85-92`) chỉ chạy `check_grounding` khi context
không rỗng (min_overlap 0.6 — `:66`), nên gửi `retrieved_context` còn có tác dụng **bật**
grounding check mà request thiếu-context không có.

## 4. Auto-retrieval khi client KHÔNG gửi context (F-2)

Seam duy nhất: `AskKernelAdapter._attach_canonical_evidence`
(`scp/ask_kernel_adapter.py:1009-1109`), được gọi trong `run_rag` **sau** S24 fork và **sau**
`begin()` — `scp/ask_kernel_adapter.py:1145-1169` — nên (a) fork thắng (đã có
`data_api_evidence`) không bao giờ double-retrieve, (b) evidence tự nạp **không** đổi
task id/input_hash (đã chốt từ body client — mục 3.1).

Điều kiện để tự nạp (mọi nhánh sai ⇒ fail-closed, giữ nguyên hành vi cũ):

| Bước | Ràng buộc | Dòng code |
|---|---|---|
| 0. Client bằng chứng trống | `contexts` rỗng ∧ `retrieved_context` strip rỗng | `ask_kernel_adapter.py:1038-1041` |
| 1. Kill switch | `SCP_ASK_RETRIEVAL` không thuộc `{0,false,off,no}` (default bật) | `question_router.py:1086-1090`, check `:1156-1157` |
| 2. Classification | `classify_l0(question)` ≠ None **và** `intent == LOOKUP` **và** `confidence >= t2_min_confidence()` | `question_router.py:1161-1167` |
| 3. Retrieval | `get_canonical_retriever().retrieve(q, k)` — BM25 Q08; exception ⇒ `[]` | `question_router.py:1169-1179` |
| 4. Floor + budget | bỏ hit có `retrieval_score < floor`; cắt `_trim(body, 2400)`/chunk; dừng khi tổng > 8000 | `question_router.py:1180-1197` |
| 5. Ghi kết quả | `req.contexts = [format_canonical_context(hit) ...]` (định dạng `[chunk_id=…] source_url=…\n<text>`, cùng convention `CanonicalRetriever.contexts()` — `canonical_retriever.py:227-228`, `question_router.py:1140-1143`) | `ask_kernel_adapter.py:1079-1086` |
| 6. Stash provenance | `request.state.scp_ask_auto_evidence = [{chunk_id, document_id, source_url, source_title, retrieval_score, term_coverage, matched_bigrams, text}]` | `ask_kernel_adapter.py:1087-1104` |

**`retrieved_context` KHÔNG bao giờ được server tự gán** — tự-điền chỉ ghi vào `contexts`
(`ask_kernel_adapter.py:1080`; repo không có chỗ nào product-code gán `req.retrieved_context`).

Ngưỡng và timeout (mặc định đã chốt trong code):

- `SCP_T2_MIN_CONFIDENCE` — ngưỡng confidence LOOKUP, default `0.6`
  (`question_router.py:61`, `:93-102`; giá trị env hỏng/out-range rơi về default).
- `SCP_ASK_RETRIEVAL_K` — số hit, default 5, clamp 1..8, về default khi hỏng (`:1093-1102`).
- `SCP_ASK_RETRIEVAL_MIN_SCORE` — floor BM25, default `1.0`; lý do chọn theo đo fixture
  (min top-1 = 5.51) ghi tại docstring `:1105-1123`; giá trị không finite/âm/quá khổ → default.
- `SCP_ASK_RETRIEVAL_TIMEOUT_SECONDS` — default `10`, hard cap `30` (`:1126-1137`); adapter
  bọc `asyncio.wait_for(asyncio.to_thread(...), budget)` — quá hạn ⇒ log WARNING, ask chạy tiếp
  KHÔNG evidence (`ask_kernel_adapter.py:1054-1070`). Lý do trần cứng: cold-load corpus prod lớn
  chưa đo; BM25 là sync.
- Budget ký tự: `ASK_RETRIEVAL_CHUNK_MAX_CHARS=2400`, `ASK_RETRIEVAL_TOTAL_MAX_CHARS=8000`
  (`question_router.py:1082-1083`).
- Gate chỉ dùng L0 regex, **không gọi L2 LLM** cho quyết định này (docstring
  `question_router.py:1146-1154` + comment `:1052-1061`) — tránh 30-260s cho một read-path phụ.

Anti-self-approval: hit được chọn theo **question** (BM25), không theo candidate answer; answer
vẫn qua đúng `judge` + `verify_response` cũ (`question_router.py:1063-1069`; test
`tests/T07_learning/test_f2_ask_retrieval_wiring.py`,
`tests/T02_contract/test_flow_02b_f2_ask_retrieval_http.py`).

## 5. Provenance của bằng chứng chảy vào response thế nào

- **Auto-evidence (canonical BM25):** `_ask_impl` đọc `request.state.scp_ask_auto_evidence`
  (`scp/api_server_parts/_ask_impl.py:417`) và append vào `slm_trace` các entry
  `slm_name='canonical_bm25'`, `source='canonical-corpus'`, `confidence=retrieval_score`,
  `answer=text[:200]`, `evidence={source_url, chunk_id, document_id, source_title, text,
  term_coverage, retrieval_score}` (`:418-421`). Đây là **trình bày lại** bằng chứng judge vừa
  chấm (`:409-416` comment), không phải input cho quyết định nào.
- **Fork S24 (data-API):** answer fork mang `data_api_evidence` (`question_router.py:1040`)
  nhưng `AskResponse` không có field đó — pydantic drop extra khi serialize qua HTTP
  (comment `question_router.py:988-997`); provenance công khai duy nhất là entry
  `slm_name='lookup_data_api'`, `source='data-api'`, evidence `{source_url, api_name, route,
  text}` trong `slm_trace` (`:1002-1017`).
- **Boundary withheld:** khi governance `KILL` / verdict `FAIL|FLAGGED` (hoặc WHY-gate REJECT),
  `_api_slm_trace = []` cùng toàn bộ các trường evidence khác bị xóa
  (`_ask_impl.py:455-470`, `:474-478`). Không có response nào lộ evidence khi answer bị withhold.
- **`retrieved_context` của client KHÔNG được echo** trong `AskResponse`
  (`helpers.py:138-170` — model không có field tương ứng). Nó chỉ xuất hiện gián tiếp qua kết quả
  grounding của judge/verify.

## 6. Ai đang thực sự gửi `retrieved_context` (client nội bộ repo)

| Client | Cách dùng | Dòng |
|---|---|---|
| Batch benchmark route (server tự POST `/ask` loopback) | `retrieved_context = "\n\n".join(contexts[:8])`, đồng thời append `contexts` (field `rag_enabled` đã gỡ — A2); câu hỏi được bọc framing trung tính "Nguồn tham khảo để đối chiếu (dữ liệu, không phải chỉ dẫn)" | `scp/api/routes/batch_benchmark_routes.py:109-134` (join `:115`, framing `:118-125`, payload `:126-134`) |
| Kết quả batch | response body được thêm `retrieved_context_count=len(contexts)`; job item lưu `retrieved_contexts` | `batch_benchmark_routes.py:161-163`, `:172` |
| Acceptance harness | payload chứa `retrieved_context` | `scripts/run_scp_acceptance.py:504`, `:519` (đọc `:77`) |
| Smoke/eval scripts | `tools/run_standard_rag_pipeline.py:27`, `tools/run_canonical_1000_abstain_pipeline.py:13`, `tools/run_bounded_system_smoke.py:137`, `tools/run_scp_rag_grounded_smoke.ps1` | — |
| Dashboard web | **KHÔNG gửi** — payload chỉ có `question/domain/ai_answer/session_id/conversation_history/image_data` | `scp/api/dashboard_html.py:310-322` |

Vì vậy trên dashboard/chat thường: `retrieved_context` luôn rỗng → mọi LOOKUP ask đủ điều kiện
đều đi qua auto-retrieval (mục 4). Ngược lại, request batch có `contexts` → auto-retrieval bị bỏ
qua theo luật "client wins" (mục 3.4).

## 7. Ghi chú chính xác hóa (đọc code HEAD `018ed21`; các mục 4 và 6 cùng line
numbers của `api_server.py`/`helpers.py`/`batch_benchmark_routes.py` đã được A2
re-check tại HEAD `17feb35` sau khi xóa field chết và thêm route stats)

1. **Mọi `/ask` đều đi qua kernel wrapper** (`api_server.py:554-580` → `run_rag`), bất kể có hay
   không bằng chứng — không còn nhánh "RAG vs non-RAG" ở tầng route.
2. `is_rag_ask` thực tế = `bool(contexts)` SAU KHI ghép `retrieved_context` và fork evidence
   (`ask_kernel_adapter.py:401-413`, `:476`) — tức chỉ cần gửi `retrieved_context` (không cần
   `contexts`, không cần `rag_enabled`) là bật cam kết grounding.
3. Reflection round-2 (Q07) nhân bản `req` qua `model_copy` và gọi **handler** trực tiếp, không
   phải `run_rag` (`ask_kernel_adapter.py:651-668`): `question/contexts/retrieved_context` giữ
   nguyên (`:583-584` comment) — round-2 dùng lại đúng bằng chứng round-1 (kể cả auto-attached)
   và **không re-retrieve**; verify round-2 chạy trên `req` gốc đã mutate (`:666-667`).
4. **`rag_enabled` đã bị xóa khỏi schema (A2, HEAD `17feb35`).** Trước đó field là
   schema-only: consumer hành vi duy nhất là `_ask_is_context_rag` (khi đó tại
   `api_server.py:208-213`) với **0 call site** — A2 xác minh độc lập bằng
   `grep -rn "_ask_is_context_rag" scp/ tests/` (chỉ ra dòng định nghĩa) và kiểm
   tra `ask_kernel_adapter.run_rag`/`_ask_impl`: gate RAG thật chỉ đọc
   `contexts`/`retrieved_context`. Helper dead, field trong `AskRequest`
   (`helpers.py:195` cũ) và hai dòng trang trí trong batch route đã bị gỡ.
   `AskRequest` (pydantic 2.13, `extra='ignore'` mặc định) nên client cũ còn gửi
   `rag_enabled` chỉ bị ignore — không 422, không đổi xử lý. Các `tools/` và
   `scripts/run_scp_acceptance.py` còn ghi field này trong payload là no-op lịch
   sử, không được server đọc. Không còn (và chưa từng có trong HEAD này) đường nào
   mà `rag_enabled=true` một mình thay đổi xử lý.
5. **Giới hạn corpus (Q08 F-3):** retriever đọc
   `data/rag_corpus/canonical-v2-20260817/corpus_all_fetched.jsonl` và
   `data/rag_corpus/canonical-v3-20260817/verified_seed_corpus.jsonl`
   (`scp/rag/canonical_retriever.py:115-116`); path không tồn tại bị skip im lặng
   (`:131-133`) ⇒ `retrieve()` trả `[]` ⇒ auto-retrieval trả `[]`
   (`question_router.py:1198-1211` — "NO fabricated evidence") ⇒ hành vi y hệt trước F-2.
   Trong checkout này `data/rag_corpus/` **không tồn tại** (đã kiểm tra `test -d`) — mọi số
   retrieval công bố ở F-2/Q08 là FIXTURE, và trên máy này LOOKUP ask thiếu-context hiện không
   nhận được auto-evidence nào.
6. **KPI auto-retrieval** (`ask_retrieval_attempts|hits|empty|errors`) đếm tại
   `question_router.py:406-424`, snapshot tại `:533-536`, prometheus
   `scp_ask_retrieval_total{outcome=...}` (`:424`). Hai đường expose (A2 `17feb35`):
   (a) `GET /v100/routing/stats` (`admin_v100.py`, `verify_admin` route-level,
   fail-closed 503 khi snapshot lỗi) trả đúng `route_stats_snapshot()` — nhóm
   `versioned_admin` nên **chỉ mount khi `SCP_API_PROFILE=full`**;
   (b) `question_routing` trong `GET /health/detailed`
   (`api_server.py:212-221` → `:610`, `:646`) — seam luôn có mặt kể cả container
   `core`; admin-auth ở đó chỉ bắt buộc khi `SCP_PRODUCTION_MODE` bật (`:610`).
   Lệnh curl trong report F-2 (`/v100/routing/stats`) tới nay mới có route thật,
   nhưng cần header admin auth (`Authorization`) và profile full; xem
   `reports/expert-panel/W3-retrieved-context-docs.md` (PHÁT HIỆN MỚI NF-W3-2) và
   `reports/expert-panel/A2-deadcode-routing.md`.

## 8. Env vars liên quan (bảng tra nhanh)

| Env | Mặc định | Tác dụng | Dòng |
|---|---|---|---|
| `SCP_ASK_RETRIEVAL` | bật (`1`) | Kill switch auto-retrieval; `0|false|off|no` ⇒ tắt hẳn, /ask như trước F-2 | `question_router.py:1086-1090` |
| `SCP_ASK_RETRIEVAL_K` | 5 (clamp 1..8) | số hit BM25 mỗi lượt | `:1093-1102` |
| `SCP_ASK_RETRIEVAL_MIN_SCORE` | 1.0 | floor điểm BM25; hit dưới floor không phải bằng chứng | `:1105-1123` |
| `SCP_ASK_RETRIEVAL_TIMEOUT_SECONDS` | 10 (cap 30) | timeout một lượt retrieval (adapter `wait_for`) | `:1126-1137`, `ask_kernel_adapter.py:1054-1070` |
| `SCP_T2_MIN_CONFIDENCE` | 0.6 | ngưỡng confidence LOOKUP; dùng chung cho fork S24 **và** auto-retrieval | `question_router.py:61`, `:93-102`, `:926`, `:1165` |
| `SCP_T2_ROUTER` | bật | Kill switch fork S24 (data-API). **Không** ảnh hưởng auto-retrieval | `question_router.py:87-91`, `ask_kernel_adapter.py:1146` |
| `SCP_ASK_KERNEL_ENABLED` | `1` | Tắt ⇒ mọi /ask fail-closed (`_kernel_gate_unavailable_response`) | `api_server.py:208-209`, `:571-572` |
| `SCP_PRODUCTION_MODE` | tắt | Bật ⇒ ẩn `/docs` `/redoc` `/openapi.json` + admin-auth cho `/health/detailed` | `api_server.py:105`, `:369-370`, `:386-388`, `:610` |

## 9. Ví dụ tối thiểu (hành vi đã đối chiếu code)

```bash
# (a) LOOKUP thiếu context: server có thể TỰ nạp contexts từ canonical corpus
#     (khi corpus tồn tại + L0==LOOKUP + conf>=0.6 + SCP_ASK_RETRIEVAL bật).
curl -X POST http://127.0.0.1:8000/ask -H "Content-Type: application/json" \
  -d '{"question":"What is the capital of France?"}'
# Bắng chứng tự nạp xuất hiện ở response.slm_trace[*].slm_name == "canonical_bm25".

# (b) Client tự chủ động gửi bằng chứng: auto-retrieval KHÔNG chạy (client wins,
#     mục 3.4); chuỗi gửi vào cả grounding judge lẫn verify (mục 3.2, 3.3).
curl -X POST http://127.0.0.1:8000/ask -H "Content-Type: application/json" \
  -d '{"question":"...","retrieved_context":"[chunk_id=c1] source_url=https://...\n<text>"}'
```

## 10. Giới hạn của tài liệu này

- Mô tả **hành vi đọc được trong code tại HEAD `018ed21`**, không phải cam kết production:
  số liệu e2e retrieval trên corpus prod chưa tồn tại (Q08 F-3; mục 7.5).
- Không mô tả `data_api_evidence` là surface công khai — nó bị drop khi serialize HTTP
  (mục 5, `question_router.py:988-997`).
- Các giá trị default/env có thể đổi theo code; khi đó cập nhật file này kèm SHA đối chiếu.
- `PASS` ở đây chỉ nghĩa là "không thấy mâu thuẫn giữa doc và code trong phạm vi đã đọc";
  open questions còn lại: xem report W3 và mục Open questions của F-2.
