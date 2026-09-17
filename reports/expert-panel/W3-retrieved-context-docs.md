# W3 — Tài liệu `retrieved_context` cho /ask (docs-only, không sửa code)

- Worker: **W3** · Repo: `C:\Users\check\Downloads\scp` · HEAD đối chiếu: `018ed21` · Ngày: 2026-09-15
- Scope: CHỈ file `*.md`. Không commit/push. Không đổi `.py` (ghi chú: `scp/security/attack_crawler.py`
  đang cóthay đổi trong working tree thuộc worker W2 chạy song song — W3 không đụng, giữ nguyên).
- Skills nạp: `scp-dna` (vòng evidence-first).

## 1. Problem statement

`AskRequest.retrieved_context` (schema `scp/api_server_parts/helpers.py:193`, `str`, default
`""`, `max_length=96000`) được code tham chiếu ở 6+ chỗ nhưng TRƯỚC W3 không một file
`docs/**` hay `README*.md` nào nhắc tới (kiểm chứng: `rg -n retrieved_context docs README*.md *.md`
→ 0 kết quả). F-2 (đã merge vào HEAD này, xem `reports/expert-panel/F2-ask-retrieval-wiring.md`
và code thật) nối canonical BM25 retriever vào /ask, nên người dùng/dashboard không biết field
này là gì, khi nào server tự nạp bằng chứng, kill switch nào, và provenance chảy ra sao.

## 2. Why chain (tóm tắt)

1. Tại sao doc gap tồn tại? → F-2 sửa hành vi ở seam `run_rag` nhưng tài liệu API cho field
   input chưa từng tồn tại (repo không có `docs/api/`).
2. Tại sao phải đọc code mà không chép F-2 report? → report CÙNG lineage tác giả với code/test
   (DNA #5); mọi claim trong doc mới phải dẫn `file:line` đọc trực tiếp tại HEAD `018ed21`.
3. Tại sao nêu `rag_enabled`/routing-stats? → đối chiếu code lộ ra field và endpoint mà
   comment/report coi là sống nhưng thực tế đã chết/lệch (mục PHÁT HIỆN MỚI).

## 3. Bằng chứng đã dùng (lineage)

| Nguồn | Loại | Ghi chú |
|---|---|---|
| `scp/api_server_parts/helpers.py`, `scp/api_server.py`, `scp/api_server_parts/_ask_impl.py`, `scp/ask_kernel_adapter.py`, `scp/runtime/question_router.py`, `scp/rag/canonical_retriever.py`, `scp/security/tier1_guard.py`, `scp/api/routes/{batch_benchmark_routes,stream_routes,openai_compat}.py`, `scp/api/dashboard_html.py`, `scripts/run_scp_acceptance.py`, `tools/*` | Code thật tại HEAD (product) | Đọc trực tiếp, mọi citation `file:line` |
| `reports/expert-panel/F2-ask-retrieval-wiring.md`, `reports/expert-panel/Q08-hybrid-retrieval.md` | Report (cùng lineage với code F-2/Q08) | Chỉ dùng để định hướng; từng claim được đối chiếu lại code |
| `tests/T07_learning/test_f2_ask_retrieval_wiring.py` + `tests/T02_contract/test_flow_02b_f2_ask_retrieval_http.py` chạy thật | Hành vi thực thi | `33 passed in 18.89s` (exit 0) — khớp semantics doc (kill switch, gate, client-wins, slm_trace `canonical_bm25` qua HTTP Level-C). Lưu ý lineage: test do chính worker F-2 viết → không hoàn toàn độc lập với code |
| `test -d data/rag_corpus` | Reality PC | **ABSENT** — điều kiện corpus-prod-absent (Q08 F-3) đang đúng trên máy này, doc nêu rõ |
| Shared-origin risk | Khai báo | code + test + report F-2 cùng một tác giả slot; W3 chỉ thêm lineage đọc-code-độc-lập-at-HEAD và chạy-test, không có lineage thứ hai ngoài repo |

## 4. Thay đổi (chỉ `*.md`)

1. **Mới** `docs/api/ask-request-fields.md` — đặc tả theo code:
   - transports cùng qua `run_rag` (`/ask` `api_server.py:561-587`; `/v1/chat/completions`
     `openai_compat.py:112`; stream `stream_routes.py:132`; stream/compat KHÔNG có field context);
   - bảng schema `AskRequest` (`helpers.py:185-205`) + OpenAPI exposure
     (`api_server.py:394-396`, tắt khi `SCP_PRODUCTION_MODE`);
   - 4 chỗ tiêu dùng `retrieved_context`: input_hash/idempotency
     (`ask_kernel_adapter.py:224-264`, `:336`), grounding judge generator
     (`_ask_impl.py:359-375`, semantic firewall `:362-368`), canonical verify
     (`ask_kernel_adapter.py:402-404`, `:426-436`, `:476`), chặn fork S24 + chặn auto-retrieval
     (`question_router.py:920`, `ask_kernel_adapter.py:1038-1041`);
   - pipeline auto-retrieval F-2 đủ 6 bước + bảng env (`SCP_ASK_RETRIEVAL`,
     `SCP_ASK_RETRIEVAL_K`=5 clamp 1..8, `SCP_ASK_RETRIEVAL_MIN_SCORE`=1.0,
     `SCP_ASK_RETRIEVAL_TIMEOUT_SECONDS`=10 cap 30, `SCP_T2_MIN_CONFIDENCE`=0.6,
     budget 2400/8000) — tất cả cite `question_router.py:1075-1211` /
     `ask_kernel_adapter.py:1009-1104`; bất biến "server không bao giờ gán `retrieved_context`,
     tự-điền chỉ ghi `contexts`" (`:1080`);
   - provenance: `slm_trace` entries `canonical_bm25` (`_ask_impl.py:417-421`),
     `lookup_data_api` (`question_router.py:1002-1017`), `data_api_evidence` bị pydantic drop
     (`:988-997`), withheld boundary xóa trace (`_ask_impl.py:455-470`);
   - clients nội bộ gửi field này: batch route (`batch_benchmark_routes.py:109-135`,
     echo `:162-164`, `:173`), acceptance harness, smoke scripts; **dashboard KHÔNG gửi**
     (`dashboard_html.py:310-322`);
   - giới hạn corpus-prod-absent (Q08 F-3) với path thật `canonical_retriever.py:115-116`,
     `:131-133` và xác nhận `data/rag_corpus` ABSENT trên checkout.
2. **Trỏ** `README.md` (mục "Chạy API thủ công" → link doc mới).
3. **Trỏ** `docs/guides/WINDOWS-README.md` (ngay sau ví dụ curl `/ask`).
4. **Layout** `docs/README.md` (thêm dòng `docs/api/`).

## 5. VERIFY đã chạy

| Lệnh | Kết quả |
|---|---|
| `rg -rn 'retrieved_context' docs` | exit 0, nhiều kết quả (docs/api + 2 con trỏ). Lưu ý: `-r n` là replace-macro của ripgrep — chạy thêm bản plain `rg -n` để thấy nguyên văn |
| `rg -n 'retrieved_context' docs \| wc -l` | 25 dòng trong `docs/api/ask-request-fields.md` + 2 file con trỏ |
| `rg -n 'retrieved_context' docs README*.md *.md` (TRƯỚC khi viết, tại HEAD) | 0 kết quả — xác nhận gap đề bài nêu |
| `python -m pytest -q tests/T07_learning/test_f2_ask_retrieval_wiring.py tests/T02_contract/test_flow_02b_f2_ask_retrieval_http.py` | **33 passed** |
| `test -d data/rag_corpus` | ABSENT (đúng claim mục 7.5 của doc) |
| Spot-check từng citation `file:line` | bằng `Read`/`grep -n` trực tiếp; sửa 1 lệnh sai (canonical_retriever paths 117-120 → **115-116**) |
| `git status --porcelain` | W3 chỉ đụng: `README.md`, `docs/README.md`, `docs/guides/WINDOWS-README.md`, `docs/api/` (mới). `scp/security/attack_crawler.py` M thuộc worker W2 — không đụng |

Giới hạn bằng chứng (Reality Verifier): cấp A/B (đọc code + test run) cho *semantics*; doc KHÔNG
claim số e2e prod — corpus prod absent, đúng như doc ghi. Chưa chạy lại toàn bộ suite T02/T07
của F-2 (79 test) vì scope W3 là docs-only và 33 test trực tiếp đã pass.

## 6. PHÁT HIỆN MỚI (NEW FINDINGS)

| # | Vị trí | Severity | Nội dung | Đề xuất slot |
|---|---|---|---|---|
| **NF-W3-1** | `scp/api_server.py:208-213` | medium | `rag_enabled` là **schema-only field**: consumer hành vi duy nhất là `_ask_is_context_rag` — và hàm này có **0 call site** trong repo (`rg _ask_is_context_rag` chỉ ra dòng định nghĩa). Nhánh cũ `if req.rag_enabled and (req.contexts or req.retrieved_context): _ask_rag_verified` chỉ còn trong `tools/patch_auto_canonical_retrieval_v1.py` (patch script lịch sử). Khách ngoài đặt `rag_enabled=true` mà không gửi evidence thì hành VIỄN y hệt chat thường — doc mới nêu rõ để tránh hiểu nhầm | Chủ repo: hoặc tái nối (gate thật) hoặc deprecate khỏi schema; W3 không sửa .py |
| **NF-W3-2** | `scp/runtime/question_router.py:36,365,405` + `reports/expert-panel/F2-ask-retrieval-wiring.md` §lệnh-tái-lập | medium | KPI `ask_retrieval_*` được comment/report trỏ tới **`/v100/routing/stats` — endpoint KHÔNG tồn tại** trong code hiện tại (không có route `/v100/*` nào). Path expose thực tế: `question_routing` trong `GET /health/detailed` (`api_server.py:220-229`, `:618`, `:654`) | Chủ slot: hoặc thêm route, hoặc sửa comment + report; lệnh curl tái-lập của F-2 tới nay là hỏng |
| **NF-W3-3** | `scp/api_server.py:618` | low | `dependencies=[Depends(verify_admin)] if _PRODUCTION_MODE else None`: ở non-production, `/health/detailed` (chồm count retrieval, routing stats) là **unauthenticated** trong khi comment `question_router.py:36` ghi "admin, auth sẵn" | Cân nhắc auth-on-mọi-mode hoặc chấp nhận công khai counter (không secret) — decision owner |
| **NF-W3-4** | `scp/api/dashboard_html.py:310-322` + schema | informational | Trả lời đúng câu hỏi "field có đang bị dashboard/schema expose không": **schema CÓ expose** `retrieved_context` qua `/docs`+`/openapi.json` khi không bật `SCP_PRODUCTION_MODE` (`api_server.py:394-396`); **dashboard KHÔNG gửi và KHÔNG render** field này; response không echo giá trị (`AskResponse` không có field — `helpers.py:138-170`), chỉ có derived `retrieved_context_count` trong batch result (`batch_benchmark_routes.py:163`) | Không cần sửa — ghi nhận |
| **NF-W3-5** | `scp/ask_kernel_adapter.py:224-264`, `:361-382` | low | `retrieved_context` participates trong durable identity: client gửi lại CÙNG câu hỏi + CÙNG evidence trong cửa sổ idempotency sẽ nhận withheld "stable logical ask already exists" (`:382`) thay vì kết quả mới; đổi từng ký tự trong field 96KB này ⇒ ask khác. API-doc chưa từng nêu bẫy này → doc mới đã nêu | Nếu owner muốn: tách dedupe-key khỏi body-hash semantics (quyết định lớn, không tự làm) |
| **NF-W3-6** | `docs/guides/WINDOWS-README.md:90-93` | low | Ví dụ "Kết quả mong đợi" (`final_answer":"thủ đô France = Paris"`) là minh họa lịch sử, không phải output xác-minh-được tại HEAD này (phụ thuộc provider + corpus; corpus prod absent). W3 chỉ thêm con trỏ, không đổi nội dung cũ | Slot guide-owner cập nhật ví dụ |

## 7. Open questions (DNA #23)

1. Khi `verified-v3` corpus được cấp (Q08 F-3), các doc citation về default env/budget vẫn đúng,
   nhưng số recall/latency cần đo lại — doc hiện chỉ claim hành vi, không claim số.
2. `rag_enabled`: deprecate hay reconnect (NF-W3-1)?
3. Có nên thêm route `/v100/routing/stats` thật hay chỉ sửa comment/report (NF-W3-2)?
4. Doc mới bằng tiếng Việt (theo working rules) — cần bản tiếng Anh nếu API doc hướng khách
   ngoài không?

## 8. Rollback

`rm docs/api/ask-request-fields.md` + `git checkout -- README.md docs/README.md
docs/guides/WINDOWS-README.md` (chỉ 3 hunk pointer của W3; giữ nguyênthay đổi W2/worker khác).
Hành vi runtime không đổi vì không đụng `.py`; kill-switch runtime vẫn là `SCP_ASK_RETRIEVAL=0`.
