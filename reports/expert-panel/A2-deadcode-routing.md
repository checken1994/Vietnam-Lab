# A2 — Dead-code `rag_enabled` & route `/v100/routing/stats`

Worker: A2 (audit/runtime-guard-AUDIT-20260909)
Scope: HEAD `17feb35e69e6a53f91f81bfdd036919ee7099def` (working tree, KHÔNG commit/push — theo chỉ đạo)
Skills áp dụng: `scp-dna` (evidence-first, verify trước khi tin W3), `scp-reality-verifier` (phân cấp evidence A/B, không nâng cấp static thành runtime).
Phương pháp: mọi claim của W3 được TỰ verify bằng `grep -rn` trên cây live (loại `.debug-*`/`.tmp-*` archive copies) TRƯỚC khi hành động.

## Phán quyết tổng

| Claim W3 | Kết quả verify độc lập | Phán quyết |
|---|---|---|
| NF-W3-1: `rag_enabled` schema-only; `_ask_is_context_rag` 0 call-site | Đúng toàn bộ | **W3 ĐÚNG** → xóa theo nguyên tắc "không dùng thì xóa" |
| NF-W3-2: `/v100/routing/stats` không tồn tại; KPI thật ở `question_routing` trong `GET /health/detailed` | Đúng toàn bộ | **W3 ĐÚNG** → chọn phương án (a): THÊM route auth-gated |

Không có consumer hành vi nào mà W3 bỏ sót → không có nhánh "W3 sai" nào kích hoạt.

## Mục 1 — `rag_enabled`: xóa field chết + helper chết

### Bằng chứng verify (trước khi sửa)

1. `grep -rn "_ask_is_context_rag" scp/ tests/ scripts/` → chỉ dòng định nghĩa `scp/api_server.py:208`; chuỗi con `is_context_rag` cũng 0 match nào khác ⇒ không có dispatch theo tên (`getattr(..., "is_context_rag")`) ⇒ **0 call-site, xác nhận**.
2. `grep -rn "rag_enabled"` trên cây live: trong `scp/` chỉ có (a) schema `scp/api_server_parts/helpers.py:195`, (b) `getattr(req, "rag_enabled", False)` BÊN TRONG helper chết `api_server.py:210`, (c) hai dòng trang trí của batch route (`:134` payload, `:162` echo vào kết quả). Không có chỗ nào GATES hành vi.
3. Đường xử lý thật của `/ask` (`api_server.py:561-587` → `AskKernelAdapter.run_rag`): predicate RAG thật chỉ đọc `contexts`/`retrieved_context` (`ask_kernel_adapter.py:401-413`, `:426`, `is_rag_ask = bool(contexts)` `:476`); `_ask_impl.py:359` cũng chỉ ghép `contexts + [retrieved_context]`. `rag_enabled` không xuất hiện ở bất kỳ đâu trong adapter.
4. Semantic grounding/F-2 (không đổi, chỉ ghi nhận): kill switches `SCP_ASK_RETRIEVAL`/`SCP_T2_ROUTER`, floor, counters — mọi file đó không tham chiếu `rag_enabled`.

### Hành động (patch nhỏ, reversible)

| File | Thay đổi |
|---|---|
| `scp/api_server.py` | Xóa helper chết `_ask_is_context_rag` (8 dòng) |
| `scp/api_server_parts/helpers.py` | Xóa `rag_enabled: bool = False` khỏi `AskRequest` |
| `scp/api/routes/batch_benchmark_routes.py` | Xóa `"rag_enabled": bool(contexts)` khỏi payload `/ask` loopback + xóa echo `body.setdefault("rag_enabled", ...)` vào kết quả job (2 dòng) |
| `tests/T04_kernel/test_ask_kernel_lifecycle_and_identity.py` | Xóa attr chết `rag_enabled` trên `DummyReq`/`OTHER_REQ` (fake tự khai — attribute không còn field tương ứng, không phải test của dead path) |
| `tests/T04_kernel/test_lease_heartbeat.py` | Xóa attr chết `rag_enabled` trên `SlowReq` |
| `tests/T07_learning/test_ask_reflection_selfcorrection.py` | Xóa `self.rag_enabled = False` trên fake `Req` |
| `docs/api/ask-request-fields.md` | Cập nhật: bỏ dòng field khỏi bảng schema, shift line refs helpers 185-204 / api_server (-6 dòng) / batch refs, viết lại mục 7.4 (đã xóa), 7.6 (route stats mới), bảng env |

KHÔNG xóa: các producer trong `tools/*.py`, `scripts/run_scp_acceptance.py` (harness/pipeline lịch sử gửi field vào `/ask`). Lý do: `AskRequest` là pydantic 2.13 mặc định `extra='ignore'` — đã verify runtime `AskRequest(question='x', rag_enabled=True)` OK, không 422; server bỏ qua field ⇒ hành vi bit-for-bit như trước (trước: field có trong schema nhưng không ai đọc; sau: không trong schema, không ai đọc). Xóa tiếp 10+ file tools/scripts chỉ phình diff mà không đổi semantics (DNA #17 batch nhỏ). Chúng được ghi nhận là residual no-op refs.

### Compatibility

API là additive-ignored theo pydantic; field chưa từng được dùng trong bất kỳ response schema (`AskResponse` không có field này). Không có test nào assert sự có mặt của `rag_enabled` (grep `tests/` = 0 sau patch). `test_egress_enforcement` pin `(file, kind)` pairs, không pin line-number — batch vẫn còn đúng 1 `requests.post` ⇒ gate không đổi verdict (đã chạy lại: pass).

## Mục 2 — `/v100/routing/stats`: chọn (a) THÊM route auth-gated

### Bằng chứng verify

1. `grep -rn "v100/routing/stats"` cây live: chỉ comment/reports; `scp/api/routes/admin_v100.py` có đúng 9 route (`/v100/{status,crawl,antibodies/stats,antibodies/check,knowledge/stats,knowledge/search,h8/stats,h8/bypasses,h8/analyses}` + `release/evidence`) — **không có** `routing/stats`. W3 đúng.
2. KPI thật hiện tại: `question_routing` trong `GET /health/detailed` (`api_server.py:220-229 → :618, :654` tại HEAD gốc); comment S24 ở `:650-653` giải thích seam chọn `/health/detailed` vì container production chạy `SCP_API_PROFILE=core` ⇒ nhóm `versioned_admin` KHÔNG mount.
3. `route_stats_snapshot()` tồn tại + được 5 suite test trực tiếp (`test_f2_ask_retrieval_wiring.py`, `test_ask_lookup_fork.py`, `test_question_router_cascade.py`, `test_ask_reflection_selfcorrection.py`).

### Vì sao chọn (a) thay vì chỉ sửa comment

- Counter F-2/Q07/S24 đã được thiết kế quanh path này; comment `question_router.py:36/:365/:405` và report F-2 §curl coi `/v100/routing/stats` là đường expose chủ định — route là tiện quan-sát hợp lệ, thống nhất pattern `*/stats` admin có sẵn (`antibodies/stats`, `knowledge/stats`, `h8/stats`...).
- **Không phá profile core/full**: route nằm trong router `admin_v100`, router chỉ được include sau `_route_enabled("versioned_admin")` (`api_server.py:438-440`, minimum `full` trong `route_profile.py:16`). Container core không mount ⇒ seam `/health/detailed` giữ nguyên 100%; đó là route mới ADDITIVE cho profile full. `test_api_route_profile.py` (core<full, path-specific asserts) không đổi verdict — đã chạy lại: pass.
- KPI của route `/health/detailed` không bị di chuyển hay đổi key — semantics retrieval/F-2 đã commit không đổi.

### Hành động

| File | Thay đổi |
|---|---|
| `scp/api/routes/admin_v100.py` | Thêm `GET /v100/routing/stats`: `dependencies=[Depends(verify_admin)]` (route-level, không có điều kiện nới lỏng kiểu `_PRODUCTION_MODE`), `@traced_request(require_write=False, action="routing_stats")` giống 9 route kia, lazy-import `route_stats_snapshot` (đúng pattern seam `[S24]`), lỗi snapshot ⇒ **503** (fail-closed, không đổi failure thành 200 rỗng); cập nhật header docstring "Routes:" |
| `scp/runtime/question_router.py:36` | Comment trỏ endpoint giờ ĐÃ thật; sửa thành 1 dòng duy nhất bổ sung: verify_admin + chỉ mount khi profile full + seam `/health/detailed` cho core (giữ số dòng ⇒ mọi line-ref trong docs/reports về `:365/:405/:406-424/:533-536...` không lệch) |
| `scp/api/routes/__init__.py` | "(9 routes)" → "(11 routes)" — phát hiện count cũ vốn ĐÃ lệch 1 (thiếu `release/evidence` được thêm ở Wave 1 mà không cập nhật header); cả hai được sửa một lượt, và header docstring `admin_v100.py` giờ liệt kê đủ 11 route |
| `tests/T03_capability/test_flow_11_admin_import_scp_standard.py` | Thêm `[ADMIN-19]` requires-admin cho route mới (pattern hệt ADMIN-1..18) |
| `tests/T03_capability/test_v100_routing_stats_endpoint.py` (MỚI) | 4 test cấp B: (1) route đăng ký đúng 1 lần + dependency bậc-route **is** `verify_admin` canonical từ `scp.security.auth` (không placeholder); (2) deny → 401 (override deny, không gọi verify_admin thật để tránh side-effect IP rate-limit); (3) body khi auth đạt = `route_stats_snapshot()` **chính xác từng key** sau khi tách 4 field ledger chuẩn (`run_id/trace_id/run_status/ledger_status` do `traced_request` attach), kèm assert đủ key F-2 `ask_retrieval_*`; (4) snapshot raise → 503 fail-closed |

KHÔNG sửa các report lịch sử F-2/Q07/W3 (evidence đã封印 theo SHA; lệnh curl F-2 được ghi chú chính xác ở docs 7.6 + mục NEW FINDINGS dưới đây).

## VERIFY đã chạy (toàn bộ sau patch, cùng HEAD base 17feb35)

| Lệnh | Kết quả |
|---|---|
| `grep -rn "rag_enabled" scp/ tests/` | 0 match |
| `grep -rn "_ask_is_context_rag" scp/ tests/` | 0 match |
| `python -m py_compile <11 file sửa>` | OK |
| `python -c "import scp.api_server..."` | field gone, extra-ignored OK, helper gone, import sạch |
| `pytest -q tests/T03_capability/test_v100_routing_stats_endpoint.py` | **4 passed** |
| `pytest -q .../test_flow_11... test_api_route_profile.py test_v100_routing_stats_endpoint.py` | **44 passed** |
| `pytest -q tests/T04_kernel/test_ask_kernel_lifecycle_and_identity.py tests/T04_kernel/test_lease_heartbeat.py tests/T07_learning/test_ask_reflection_selfcorrection.py tests/T07_learning/test_f2_ask_retrieval_wiring.py tests/T07_learning/test_ask_lookup_fork.py tests/T07_learning/test_question_router_cascade.py tests/T03_capability/test_egress_enforcement.py` | **163 passed, 2 skipped** — 2 skip là skip-declared có sẵn (`SCP_EE_CONTAINER_TESTS=1` opt-in, không liên quan patch) |
| `python tools/verify_scp_test_skill_contract.py` | exit 0, `PASS_WITHIN_SCOPE` |
| `python tools/t00_meta_audit.py` | exit 1 — NGUYÊN NHÂN LÀ BASELINE, không phải candidate (xem NF-A2-2) |

## PHÁT HIỆN MỚI (NEW FINDINGS)

**NF-A2-1 (low, observability-contract).** `traced_request` qua `RequestRunLedger.attach` (`scp/core/request_run_ledger.py:~320`) inject đúng 4 field `run_id/trace_id/run_status/ledger_status` vào MỌI response dict của route traced — gồm cả route mới. Hệ quả thực tế: (a) so sánh naive `body == route_stats_snapshot()` SAI (test của tôi phát hiện thật trên run đầu tiên, exit assertion); (b) `question_routing` trong `/health/detailed` KHÔNG có enrichment đó (health không traced qua ledger này), nên hai đường expose không byte-identical — cùng một snapshot + 4 field. Lệnh curl F-2 `| jq '{ask_retrieval_attempts,...}'` vẫn đúng vì jq chọn key con. Người dùng dashboard nên biết route trả thêm metadata ledger.

**NF-A2-2 (pre-existing, harness, không do A2).** `t00_meta_audit.py` exit 1 TRÊN MÁY NÀY vì bước `check_real_test_deletion` collect baseline worktree `origin/main`, mà `tests/test_api.py` tại `origin/main` vẫn chạy `urllib.urlopen` lúc import (B1 mới fix ở HEAD branch này, docstring file ghi rõ chính xác failure mode này). Chứng minh tách bạch: `git show origin/main:tests/test_api.py` = probe unguarded; `pytest tests/test_api.py --collect-only` trong worktree origin/main ⇒ URLError; trong tree hiện tại (candidate) ⇒ collect sạch; `pytest tests/ --collect-only -q` toàn candidate ⇒ **exit 0, 1961 tests**. Không phải regression của A2 và nằm ngoài file được phép sửa của A2. Khuyến nghị cho orchestrator: bảo đảm fix B1 tới `main` (hoặc cho phép t00 bỏ qua collection-time error ở baseline worktree theo cách không nới lỏng FA-02) — đây là fail-closed ĐÚNG chiều của harness, chỉ cần baseline hết nhiễm.

**NF-A2-3 (low, schema-doc drift, đã tự sửa trong phạm vi).** `docs/api/ask-request-fields.md` mục 7.6 cũ nói "route đó KHÔNG tồn tại" và comment `question_router.py:36` nói "auth sẵn" — hai phát ngôn lệch nhau một chiều (comment giả định route tồn tại, docs nói không); route giờ tồn tại thật nên docs + comment đã được chỉnh khớp, kèm ghi chú profile-full gating. Không có semantic nào thay đổi.

**NF-A2-4 (info).** `scp/api_server.py` còn hai pattern liên quan nhưng ĐÚNG, không phải dead-code: (a) `AskRequest` không còn field `rag_enabled` nhưng `getattr(req, ...)` trong các fakes/tests T04 giờ chỉ còn đọc `contexts`/`retrieved_context` — khớp thật của kernel; (b) seam `_question_routing_stats()` fail-open (`{"error": TypeName}` 200) trong `/health/detailed` là cố ý (health không chết vì counters) — route admin mới chọn fail-CLOSED (503) vì surface admin phải báo đúng trạng thái; hai semantics khác nhau theo vai trò surface, có test cho cả hai đường (cascade/f2 cho seam; test_mới (4) cho route).

**NF-A2-5 (low, doc-drift đã sửa).** Đếm route khai trong `scp/api/routes/__init__.py` và header `admin_v100.py` là "9 routes" từ trước, nhưng router tại HEAD gốc đã có **10** route — `GET /v100/release/evidence` (Wave 1) không được thêm vào "Routes:" list. Kiểm chứng bằng chính đối tượng router (`len(router.routes)` trước khi A2 thêm = 10). Route mới của A2 đưa tổng về 11; header + count giờ khớp thật. Bài học loại hình: các comment đếm route không có test giữ chúng đồng bộ — nếu muốn enforce, một test so `count` khai báo vs `len(router.routes)` là ứng viên nhỏ cho follow-up (A2 không tự thêm vì ngoài chỉ đạo).

**GHI CHÚ TÌNH TRẠNG TREE (không phải finding code).** `git diff` cho thấy `.agents/EXECUTION_PROTOCOL.md` có 1 dòng thêm mới ("Orchestrator không được copy claim của worker khi chưa verify từng claim" — lesson từ A1 `e33622d`). Đây là sửa đổi KHÔNG phải của A2 (A2 không đụng file đó — nó nằm trong danh sách cấm); A2 giữ nguyên, không revert, không commit. Đồng thời tree tồn tại nhiều untracked artifacts của worker khác (`s2probe.py`, `EVIDENCE-MANIFEST-2026-09-16.txt`, `tests/test_m1_empirical_challenger.py`, `tests/test_m2_auth_settings_challenger.py`, `.v2c/`...) — A2 không đụng.

## Vi phạm / cảnh giác cần người biết

- `spec/guardrail_policy.yaml protected_paths` gồm `tests/` — patch này THÊM 5 test functions + xóa 4 dòng fake attr trong `tests/` theo lệnh trực tiếp của coordinator ("dọn test tương ứng"); đây là vùng L4 theo policy. commit authority (orchestrator/owner) cần biết khi qua pre-commit `t00` code-owner check.
- Không skip/xfail/deselect mới; không hạ ngưỡng; không secret trong diff; route auth fail-closed (route-level `Depends(verify_admin)`, canonical impl, test chứng minh dependency identity).

## Phạm vi còn bỏ ngỏ (open questions)

1. **Chưa runtime-verify qua HTTP server thật** (cấp C): các bằng chứng route mới là TestClient in-process (cấp B). Mount-core-absence được suy từ code path include + test profile có sẵn, chưa curl trên `SCP_API_PROFILE=core` container sống.
2. Chưa chạy full suite `tests/` của toàn repo trong session này (chỉ các suite liên quan trực tiếp + collect-only toàn bộ = 1961 tests sạch).
3. `tools/*.py` và `scripts/run_scp_acceptance.py` vẫn gửi `rag_enabled` (no-op); nếu muốn dọn nốt thì là một follow-up 10-file nhỏ, tách biệt.
4. t00 xanh hoàn chỉnh trên máy này cần baseline `origin/main` chứa fix B1 — ngoài quyền A2.

Verdict trong phạm vi đã nêu: **PASS_WITHIN_SCOPE** — 2 mục tiêu W3 verify đúng, xóa dead code an toàn + thêm route auth-gated khớp pattern, toàn bộ test liên quan xanh, contract xanh; các giới hạn còn lại đã liệt kê, không có claim production-release nào.
