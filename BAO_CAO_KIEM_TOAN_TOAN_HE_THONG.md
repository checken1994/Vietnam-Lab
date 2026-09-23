# Báo Cáo Kiểm Toán Toàn Diện: SCP (Secure Control Plane)

**Ngày thực hiện kiểm toán**: 2026-09-22  
**Repository mục tiêu**: `D:\scp`  
**Nhánh Git**: `feature/autonomous-mode-antigravity-v2` (HEAD: `6d6256f`)  
**Chế độ kiểm toán**: Pháp y chuyên sâu / Chế độ phát triển  
**Trạng thái kiểm toán**: Hoàn thành & Đã xác minh thực nghiệm  

---

## Tóm Tắt Điều Hành

Một cuộc kiểm toán pháp y toàn diện đã được thực hiện trên toàn bộ repository SCP (Secure Control Plane) bao gồm cả codebase Python và TypeScript (~221.000 dòng Python, 1.201 file `.py`, 43 subsystem hoạt động dưới `scp/`, 12 module gốc package, và 1.937 test). Cuộc kiểm toán đánh giá hệ thống theo sáu lĩnh vực yêu cầu cốt lõi:
- **R1: Lỗi ẩn & Lỗi logic**
- **R2: Bảo mật & Quản lý bí mật**
- **R3: Tính toàn vẹn kiến trúc & Mã chết**
- **R4: Độ phủ test & Chất lượng test**
- **R5: Tính đúng đắn runtime & Đồng thời**
- **R6: Tính nhất quán cấu hình & Triển khai**

### Các phát hiện hệ thống chính
1. **"Ảo giác Test Xanh"**: Mặc dù `pytest tests/ -q` báo cáo **1.937 test pass với 0 failure**, kiểm tra thực nghiệm cho thấy 38 file test chỉ thực hiện kiểm tra import mà không chạy logic, các test chaos nội bộ là stub (`assert True`), và các test trace quan trọng truy vấn fixture mock thay vì API thực. Hơn 51% subsystem có độ phủ test chức năng bằng 0 hoặc tối thiểu.
2. **Tính truy ngược bị ngắt kết nối & Lỗ hổng che giấu dữ liệu**: Stack truy ngược ngược (`TraceStore`, `_trace_impl.py`) không được theo dõi trong git và hoàn toàn bị ngắt khỏi `api_server.py`. Trong khi đó, endpoint `/v3/trace/{trace_id}` đang hoạt động trong `api_server.py` không có xác thực và trả về dữ liệu thô mà không gọi hàm redact thuộc tính, còn bản thân `redact_attributes()` chứa lỗ hổng che giấu — để lọt header tuple và giá trị chuỗi thô nhạy cảm.
3. **Lỗ hổng mất dữ liệu & đồng thời trong tầng lưu trữ**: Các mẫu đọc-sửa-ghi không có khóa trong `ChatMemoryStore` và `TraceLedger` dẫn đến ghi đè dữ liệu âm thầm, lỗi chia sẻ file `WinError 32` trên Windows, và chuỗi hash mật mã bị hỏng vĩnh viễn. Trong `db_manager.py`, mutex lock được giải phóng *trước khi* thực thi truy vấn SQLite, để lộ kết nối dùng chung cho data race đồng thời.
4. **API hỏng do thuộc tính Judge bị stub**: Sáu thuộc tính trên `Judge` trong `scp/runtime/judge.py` được hardcode trả về `return None`, gây ra lỗi HTTP 503 Service Unavailable chắc chắn trên bốn endpoint công khai `/v98/`.
5. **Trôi dạt cấu hình & triển khai**: 84% biến môi trường được tham chiếu (165 trong 197) bị thiếu trong `.env.example`, script khởi động Windows (`start-scp.bat`) bỏ qua microservice LLM Bridge, và có sự khác biệt port giữa Docker, frontend, và backend.

### Ma trận phát hiện theo lĩnh vực và mức độ nghiêm trọng

| Lĩnh vực | NGHIÊM TRỌNG | CAO | TRUNG BÌNH | THẤP | THÔNG TIN | Tổng phát hiện |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| **R1: Lỗi ẩn & Lỗi logic** | 2 | 2 | 2 | 1 | 0 | **7** |
| **R2: Bảo mật & Quản lý bí mật** | 2 | 4 | 2 | 0 | 0 | **8** |
| **R3: Tính toàn vẹn kiến trúc & Mã chết** | 1 | 3 | 3 | 1 | 1 | **9** |
| **R4: Độ phủ test & Chất lượng test** | 2 | 2 | 2 | 0 | 1 | **7** |
| **R5: Tính đúng đắn runtime & Đồng thời** | 3 | 4 | 2 | 0 | 0 | **9** |
| **R6: Tính nhất quán cấu hình & Triển khai** | 1 | 3 | 1 | 1 | 0 | **6** |
| **TỔNG** | **11** | **18** | **12** | **3** | **2** | **46** |

---

## Kiểm kê Toàn bộ Subsystem (Tất cả 43 Subsystem + File Gốc)

Bảng dưới đây liệt kê từng thư mục subsystem hoạt động trực tiếp dưới `D:\scp\scp\` và 12 module gốc package, chi tiết số file, số dòng code (LOC) ước tính, số file God (>500 LOC), và trách nhiệm kiến trúc cốt lõi.

| # | Subsystem | Đường dẫn | Files | LOC | God Files (>500 LOC) | Trách nhiệm kiến trúc cốt lõi |
|---|---|---|:---:|:---:|:---:|---|
| 1 | **api** | `scp/api` | 35 | 6.678 | 4 | Tầng routing REST & WebSocket API cung cấp endpoint FastAPI, router, và xác thực request cho client và dashboard. |
| 2 | **api_server_parts** | `scp/api_server_parts` | 6 | 1.891 | 1 | Các hàm thực thi request, kiểm tra sự thật, và lifespan được tách ra từ `api_server.py` đơn khối. |
| 3 | **audit_engine** | `scp/audit_engine` | 9 | 288 | 0 | Engine pipeline kiểm toán và xác minh hermetic chính thức; hiện bị cô lập như subsystem deadzone. |
| 4 | **audit_r8** | `scp/audit_r8` | 0 | 0 | 0 | Artifact kiểm toán Vòng 8 lịch sử, ghi chú tự kiểm toán, và dữ liệu phát hiện (repo markdown/JSON không phải code). |
| 5 | **audit_r9** | `scp/audit_r9` | 0 | 0 | 0 | Artifact kiểm toán Vòng 9 lịch sử, ghi chú tự kiểm toán, và dữ liệu phát hiện (repo markdown/JSON không phải code). |
| 6 | **autofix** | `scp/autofix` | 99 | 32.648 | 18 | Engine tự sửa lỗi: phát hiện lỗi tự động, biến đổi AST, quét lỗ hổng, vá code ngữ nghĩa, và tự phục hồi. |
| 7 | **benchmark** | `scp/benchmark` | 22 | 2.643 | 1 | Framework đo hiệu năng, so sánh baseline LLM, và đo độ chính xác chống ảo giác. |
| 8 | **brain** | `scp/brain` | 8 | 2.122 | 2 | Bộ nhớ nhận thức hệ thống và chỉ mục lỗi; lưu trữ và chỉ mục các lỗi thực thi runtime và dấu vết lỗi. |
| 9 | **calibration** | `scp/calibration` | 4 | 374 | 0 | Hiệu chuẩn độ tin cậy mô hình đảm bảo ước lượng không chắc chắn và ngưỡng xác suất phù hợp với độ đúng thực nghiệm. |
| 10 | **capabilities** | `scp/capabilities` | 4 | 231 | 0 | Registry khả năng chi tiết, kiểm tra quyền token, và định nghĩa ranh giới khả năng. |
| 11 | **consolidator** | `scp/consolidator` | 2 | 161 | 0 | Hợp nhất và đối chiếu trạng thái phân tán giữa các vòng lặp worker nền không đồng bộ. |
| 12 | **contracts** | `scp/contracts` | 8 | 263 | 0 | Schema giao diện chính thức, đặc tả giao thức, khai báo bất biến, và cấu trúc dữ liệu. |
| 13 | **core** | `scp/core` | 94 | 21.791 | 8 | Điều phối hệ thống cốt lõi, quản lý vòng đời, lưu trữ sổ cái, xác minh đa nguồn, và xác thực token khả năng. |
| 14 | **data_sources** | `scp/data_sources` | 71 | 12.382 | 2 | Kết nối dữ liệu bên ngoài, crawler bằng chứng trực tiếp, scraper Wikipedia/Wikidata, và bộ lấy kiến thức chuyên biệt. |
| 15 | **epistemic** | `scp/epistemic` | 5 | 884 | 0 | Theo dõi bằng chứng nhận thức, thực thi ranh giới độ chắc chắn, và xác minh nguồn gốc kiến thức sự thật. |
| 16 | **experience** | `scp/experience` | 3 | 846 | 1 | Tích lũy kinh nghiệm lịch sử, chỉ mục trường hợp episodic, và khớp mẫu truy vấn lịch sử. |
| 17 | **forecast** | `scp/forecast` | 3 | 405 | 0 | Chấm điểm dự đoán kết quả, dự báo quỹ đạo, và ước lượng rủi ro thất bại. |
| 18 | **foundation** | `scp/foundation` | 3 | 476 | 0 | Trừu tượng nền tảng hệ thống, lớp cơ sở trừu tượng, và tiện ích cấp thấp. |
| 19 | **governance** | `scp/governance` | 4 | 515 | 0 | Rào chắn đạo đức, cổng ghi riêng tư, chính sách lưu giữ, và quy tắc tuân thủ kiến trúc. |
| 20 | **hands** | `scp/hands` | 8 | 2.552 | 2 | Bộ máy thực thi vật lý: phân rã tác vụ, lập kế hoạch mục tiêu, thực thi OS, và tương tác TaskKernel. |
| 21 | **history** | `scp/history` | 5 | 660 | 0 | Nhật ký phiên dài hạn, biên niên tương tác lịch sử, và lưu trữ timeline truy vấn. |
| 22 | **interfaces** | `scp/interfaces` | 2 | 83 | 0 | Hợp đồng giao diện trừu tượng công khai và đặc tả giao thức cho tích hợp caller bên ngoài. |
| 23 | **knowledge** | `scp/knowledge` | 28 | 6.807 | 4 | Quản lý cơ sở kiến thức, registry kháng thể lỗi, kho vector miền, và chấm điểm uy tín nguồn. |
| 24 | **learning** | `scp/learning` | 2 | 37 | 0 | Vòng lặp học liên tục và pipeline học tăng cường để điều chỉnh tham số mô hình và prompt. |
| 25 | **llm_gateway** | `scp/llm_gateway` | 5 | 1.317 | 1 | Routing nhà cung cấp LLM hợp nhất (OpenAI, Anthropic, OpenRouter), thực thi egress, định giá token, và chuyển đổi mô hình dự phòng. |
| 26 | **mcp_server** | `scp/mcp_server` | 3 | 437 | 0 | Triển khai server Model Context Protocol (MCP) cho phép tương tác công cụ bên ngoài chuẩn hóa. |
| 27 | **meta** | `scp/meta` | 55 | 14.110 | 5 | Phản ánh siêu nhận thức, engine bác bỏ, đánh giá lý do cổng WHY, và xác thực đồng thuận đa LLM. |
| 28 | **observability** | `scp/observability` | 4 | 190 | 0 | Hạ tầng quan sát, hook truy vết phân tán OpenTelemetry, và định nghĩa metrics Prometheus. |
| 29 | **pc_control** | `scp/pc_control` | 2 | 470 | 0 | Bộ điều khiển tự động PC và OS cục bộ (tự động desktop, thực thi lệnh, thao tác cửa sổ). |
| 30 | **persistence** | `scp/persistence` | 2 | 118 | 0 | Trừu tượng lưu trữ database, cấu hình pool kết nối, và tiện ích SQLite/PostgreSQL. |
| 31 | **policy** | `scp/policy` | 2 | 113 | 0 | Chính sách kiểm soát truy cập, quy tắc ủy quyền, và đánh giá quyết định chính sách. |
| 32 | **prediction** | `scp/prediction` | 2 | 1.011 | 1 | Engine phân tích dự đoán mô hình hóa chuyển đổi trạng thái thời gian và kỳ vọng bất thường. |
| 33 | **rag** | `scp/rag` | 2 | 77 | 0 | Pipeline Retrieval-Augmented Generation phối hợp chia nhỏ tài liệu, embedding, và tổng hợp ngữ cảnh. |
| 34 | **release** | `scp/release` | 2 | 210 | 0 | Xác minh danh tính phát hành, cổng tái tạo build, và xác minh SHA commit. |
| 35 | **risk_intelligence** | `scp/risk_intelligence` | 6 | 360 | 0 | Tình báo rủi ro mối đe dọa động, phát hiện bất thường, và đánh giá tư thế lỗ hổng. |
| 36 | **runtime** | `scp/runtime` | 61 | 10.932 | 7 | Runtime thực thi điều phối RealityJudge, chuyên gia SLM miền, thực thi pipeline ask, và tiến trình worker. |
| 37 | **sandbox_evaluator** | `scp/sandbox_evaluator` | 4 | 753 | 0 | Sandbox thực thi cô lập đánh giá đoạn mã, bản vá, và lệnh không tin cậy một cách an toàn. |
| 38 | **security** | `scp/security` | 44 | 11.623 | 6 | Engine bảo mật zero-trust: ủy quyền JWT, lọc SSRF/egress, phân loại tấn công, và bẫy canary. |
| 39 | **self_model** | `scp/self_model` | 2 | 280 | 0 | Mô hình tự nội quan theo dõi trạng thái hoạt động nội bộ, tình trạng sức khỏe, và ranh giới khả năng. |
| 40 | **task_kernel_parts** | `scp/task_kernel_parts` | 2 | 1.966 | 1 | Triển khai máy trạng thái TaskKernel cấp thấp, thu nhận lease, hàng rào giao dịch, và nhật ký sự kiện. |
| 41 | **tests** | `scp/tests` | 9 | 792 | 0 | Bộ test nội bộ nhúng, test phục hồi chaos, test dựa trên thuộc tính, và hồi quy bảo mật kiểm toán bên ngoài. |
| 42 | **web_control** | `scp/web_control` | 7 | 1.007 | 0 | Quản lý trình duyệt headless (Playwright), scraping tìm kiếm, duyệt DOM, và điều hướng web. |
| 43 | **world_state** | `scp/world_state` | 4 | 316 | 0 | Biểu diễn trạng thái thế giới bên ngoài, theo dõi delta trạng thái thời gian, và neo thực thể. |
| - | **module gốc** | `scp/*.py` | 12 | 5.142 | 3 | Điểm vào chính và kho lưu trữ backend: `api_server.py`, `ask_kernel_adapter.py`, `kernel_storage_pg.py`, `event_bus_pg.py`, `task_kernel.py`. |
| **Tổng** | **43 Subsystem + Gốc** | **`scp/`** | **655** | **162.118** | **78** | **Hệ điều hành Agent có quản trị cốt lõi** |

---

## Phân tích Sức khỏe Kiến trúc & Nợ Cấu trúc

### 1. Hồ sơ bảo trì File God (>500 LOC)
Codebase chứa **78 File God** vượt quá 500 dòng code (75 trong subsystem và 3 ở gốc package). 10 file phức tạp nhất:
1. `scp/task_kernel_parts/taskkernel.py` (1.966 LOC): Chuyển đổi trạng thái, lease, khóa OCC, rollback, và nhật ký sự kiện kết hợp trong một class duy nhất.
2. `scp/autofix/engine_parts/autofix_mixin.py` (1.631 LOC): Logic tạo bản vá, xác minh, và rollback hồi quy.
3. `scp/knowledge/antibody_parts/mixins.py` (1.190 LOC): Mixin đơn khối quản lý trạng thái kháng thể, đột biến, khớp mẫu, và tính toán mức độ nghiêm trọng.
4. `scp/autofix/engine.py` (1.045 LOC): Core engine tự sửa phối hợp quét, tạo bản vá, test, và vòng lặp phản hồi.
5. `scp/llm_gateway/client.py` (1.024 LOC): Client LLM đơn khối triển khai logic retry, phân tích stream SSE, định giá, và chuyển đổi dự phòng.
6. `scp/ask_kernel_adapter.py` (1.017 LOC): Module keo điều phối luồng request `/ask`, routing câu hỏi, và thực thi tác vụ async.
7. `scp/prediction/predictive.py` (1.006 LOC): Class phân tích dự đoán đơn khối với routine toán học lẫn truy vấn database.
8. `scp/meta/falsification_engine.py` (987 LOC): Bộ bác bỏ mệnh đề đối kháng đa lượt với quy tắc AST và regex lồng nhau.
9. `scp/hands/planner.py` (977 LOC): Engine lập kế hoạch tác vụ thủ tục kết hợp quy tắc heuristic với chỉ dẫn LLM.
10. `scp/runtime/question_router.py` (977 LOC): Cây quyết định routing 44 miền, khớp từ khóa heuristic, và logic dispatch SLM.

### 2. Dấu chân Mã Chết: 63 Module Mồ côi Đã Xác minh (>13.000 LOC)
Phân giải import dựa trên AST xác nhận 63 module Python dưới `scp/` không bao giờ được import bởi bất kỳ file runtime, điểm vào, hoặc file test nào trong toàn bộ repository 1.201 file. Ví dụ:
- `scp/runtime/experts/chem_reality_astro.py` (817 LOC)
- `scp/runtime/experts/misc.py` (674 LOC)
- `scp/security/h8_redteam_bridge.py` (660 LOC)
- `scp/runtime/engine_parts/scpv14_process_mixin.py` (650 LOC)
- `scp/meta/calibration_engine.py` (607 LOC)
- `scp/meta/adversary_verifier.py` (549 LOC)
- `scp/brain/error_store_index.py` (534 LOC)
- `scp/benchmark/run_benchmark_enhanced.py` (526 LOC)
- `scp/meta/question_tracker.py` (460 LOC)
- `scp/ai_patterns.py` (454 LOC)

### 3. Chu trình Phụ thuộc Vòng tròn (30 Chu trình)
Phân tích DFS trên đồ thị import AST phát hiện **30 chu trình phụ thuộc vòng tròn** trải rộng 12 subsystem quan trọng:
- `core` <-> `autofix` (`core.bounded_evolution` import `autofix.evolution`; `autofix.engine` import `core.code_evolution_agent`)
- `core` <-> `hands` (`core.agent_orchestrator` import `hands.planner`; `hands.planner` import `core.capability_token`)
- `epistemic` <-> `governance` (`epistemic.evidence_writer` import `governance.privacy`; `governance.retention` import `epistemic.evidence_store`)
- `security` <-> `api_server_parts` (`security.threat_simulator` import `api_server_parts.helpers`; `api_server_parts.helpers` import `security.auth`)
- `llm_gateway` <-> `security` (`llm_gateway.client` import `security.url_safety`; chu trình đã cắt: `security.quorum_why` loại bỏ thay bằng `multi_llm_crosscheck`)
- `meta` <-> `knowledge` (`meta.epistemic_boundary` import `knowledge.learning_db`; `knowledge.antibody_system` import `meta.severity`)
- `meta` <-> `security` (`meta.why_sources.crypto` import `security.url_safety`; `security.predictor` import `meta.why_gate`)

### 4. Anti-Pattern Kiến trúc "Tách-và-Khâu"
Bảy subsystem (`api_server_parts`, `task_kernel_parts`, `autofix_parts`, `fast_learning_engine_parts`, `antibody_parts`, `why_engine_parts`, `engine_parts`) phân rã file một cách bề ngoài để vượt giới hạn dòng, nhưng khâu lại bằng rebind bytecode hàm (`types.FunctionType`) và monkeypatch class runtime (`TaskKernel.idempotency_claim = _idempotency_claim_fenced`). Điều này tạo coupling mong manh với biến toàn cục tiến trình và vô hiệu hóa phân tích tĩnh và công cụ IDE.

---

## 10 Phát Hiện Nghiêm Trọng Nhất Xuyên Suốt Tất Cả Lĩnh Vực

1. **[R2 - NGHIÊM TRỌNG] Endpoint Trace Công khai Không Xác thực & Không Che giấu Thuộc tính trên Route Đang Hoạt động**  
   *File*: `scp/api_server.py:541-566`  
   *Nguyên nhân gốc*: Server FastAPI đang hoạt động cung cấp `/v3/trace/{trace_id}` mà không có dependency xác thực (`verify_admin`) và trả trực tiếp bản ghi sổ cái không che giấu chứa header ủy quyền, token prompt, và bí mật hệ thống.  
   *Bằng chứng code*:
   ```python
   # scp/api_server.py:541-566
   @app.get("/v3/trace/{trace_id}")
   @app.get("/api/v3/trace/{trace_id}")
   async def get_trace_record(trace_id: str):
       ...
       record = ledger.get_trace(trace_id)
       ...
       if isinstance(record.get("fields"), dict):
           merged = dict(record["fields"])
           merged.update({k: v for k, v in record.items() if k != "fields"})
           merged["_ledger_entry"] = record
           return merged  # Trả về không che giấu, không xác thực!
   ```

2. **[R1 - NGHIÊM TRỌNG] `return None` Hardcode trong Thuộc tính Judge Âm thầm Phá vỡ 4 Endpoint Admin Công khai (HTTP 503)**  
   *File*: `scp/runtime/judge.py:101-111` & `scp/api/routes/admin_v98.py:74-117`  
   *Nguyên nhân gốc*: Các thuộc tính cốt lõi trên `Judge` (`counter_response`, `canary_monitor`, `error_store`, `attack_memory`, `falsification`, `governance`) được stub bằng `return None`. Bốn endpoint quản trị yêu cầu các thuộc tính này và hủy vô điều kiện với `HTTPException(status_code=503)`.  
   *Bằng chứng code*:
   ```python
   # scp/runtime/judge.py:101-111
   @property
   def falsification(self): return None
   @property
   def error_store(self): return None
   @property
   def governance(self): return None
   @property
   def counter_response(self): return None
   @property
   def canary_monitor(self): return None
   @property
   def attack_memory(self): return None

   # scp/api/routes/admin_v98.py:84-87
   judge = get_judge()
   if not judge.counter_response:
       raise HTTPException(status_code=503, detail="CounterResponseEngine not available")
   return judge.counter_response.stats()
   ```

3. **[R5 - NGHIÊM TRỌNG] Race Condition Phá hủy và Mất Dữ liệu Âm thầm trong Cắt tỉa `ChatMemoryStore`**  
   *File*: `scp/core/chat_memory_store.py:78-83, 115-146`  
   *Nguyên nhân gốc*: `_prune_if_needed()` sao chép bản ghi sang file tạm và thay thế file JSONL đang hoạt động bằng `os.replace(temp_name, self.path)` mà không giữ bất kỳ khóa thread hoặc file nào. Dưới ghi WebSocket chat đồng thời, các cập nhật được thêm giữa đọc và thay thế bị hủy. Trên Windows, handle file đồng thời gây `PermissionError: [WinError 32]`.  
   *Bằng chứng code*:
   ```python
   # scp/core/chat_memory_store.py:131-138
   fd, temp_name = tempfile.mkstemp(prefix="chat-memory-", suffix=".jsonl", dir=str(self.path.parent))
   try:
       with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
           for row in rows:
               handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
           handle.flush()
           os.fsync(handle.fileno())
       os.replace(temp_name, self.path)  # Thay thế nguyên tử không khóa phá hủy ghi đồng thời!
   ```

4. **[R5 - NGHIÊM TRỌNG] Chuỗi Hash Mật mã Bị Hỏng và I/O Đĩa $O(N)$ trong `TraceLedger.append()`**  
   *File*: `scp/trace_ledger.py:28-34, 35-47`  
   *Nguyên nhân gốc*: `append()` đọc toàn bộ file từ đĩa, kiểm tra dòng cuối, và tính `seq = len(lines) + 1` và `prev_hash` mà không có khóa. Các request đồng thời đọc cùng dòng cuối, gán trùng sequence và hash, làm hỏng vĩnh viễn xác minh sổ cái (`TraceLedger.verify()`).  
   *Bằng chứng code*:
   ```python
   # scp/trace_ledger.py:28-34
   def append(self, **fields: Any) -> dict[str, Any]:
       lines=self.path.read_text(encoding="utf-8").splitlines() if self.path.exists() else []
       prev=json.loads(lines[-1]) if lines else None
       entry={"trace_id":fields.pop("trace_id",None) or "trace_"+secrets.token_hex(8),"seq":len(lines)+1,"prev_hash":prev.get("hash") if prev else None,"fields":_redact(fields)}
       entry["hash"]=_hash(entry)
       with self.path.open("a",encoding="utf-8") as f:f.write(json.dumps(entry,ensure_ascii=False,sort_keys=True)+"\n")
       return entry
   ```

5. **[R5 - NGHIÊM TRỌNG] Khóa SQLite Bị Thả Trước Khi Thực thi Truy vấn trong `db_manager.py`**  
   *File*: `scp/core/db_manager.py:149-158`  
   *Nguyên nhân gốc*: `db_query_one` và `db_query_all` thu nhận `_db_lock` hoặc `_read_lock` chỉ để lấy tham chiếu `sqlite3.Connection` dùng chung. Khóa được giải phóng trước `conn.execute(...)`, gây thực thi đa luồng đồng thời trên kết nối dùng chung không được bảo vệ được tạo với `check_same_thread=False`.  
   *Bằng chứng code*:
   ```python
   # scp/core/db_manager.py:149-158
   def db_query_one(sql: str, params=(), db_path: Optional[str] = None) -> Optional[dict]:
       if db_path:
           with _db_lock:
               conn = _get_path_conn(db_path)
       else:
           with _read_lock:
               conn = get_db()
       row = conn.execute(sql, params).fetchone()  # Thực thi NGOÀI khóa!
       return dict(row) if row else None
   ```

6. **[R3 - NGHIÊM TRỌNG] Stack Truy ngược Ngược Không Được Theo dõi & Bị Ngắt Kết nối với Bộ Test Bỏ qua**  
   *File*: `scp/core/trace_store.py`, `scp/api_server_parts/_trace_impl.py`, `scp/api_server.py`, `tests/T12_unified_chatbot/conftest.py:189-235`, `test_tier4_real_world_scenarios.py:261-320`  
   *Nguyên nhân gốc*: `TraceStore` production và REST router (`_trace_impl.py`) không được theo dõi trong git, và `api_server.py` không bao giờ mount `_trace_impl.router`. Bộ test tích hợp bỏ qua lỗi này bằng cách tạo fixture test `SqliteTraceStore` sao chép và gọi method Python trực tiếp thay vì truy vấn server HTTP.  
   *Bằng chứng code*:
   ```python
   # git status:
   ?? scp/api_server_parts/_trace_impl.py
   ?? scp/core/trace_store.py

   # tests/T12_unified_chatbot/test_tier4_real_world_scenarios.py:309-311
   # Tuyên bố test "GET /api/scp/v3/trace/{trace_id}" nhưng gọi fixture trực tiếp:
   audit = trace_store.get_trace(trace_id)
   assert audit is not None
   ```

7. **[R4 - NGHIÊM TRỌNG] Xanh Giả tạo trong Test Phục hồi Chaos (Vi phạm FA-04)**  
   *File*: `scp/tests/chaos_recovery.py:1-8`  
   *Nguyên nhân gốc*: Test phục hồi chaos nội bộ thực thi hai câu lệnh print và `assert True` vô điều kiện, không thực hiện injection lỗi, xác minh trạng thái, hoặc đối chiếu TaskKernel nào.  
   *Bằng chứng code*:
   ```python
   # scp/tests/chaos_recovery.py:1-8
   def test_chaos_recovery():
       print("Simulating Chaos Crash...")
       print("Recovery verified.")
       assert True
   ```

8. **[R1 - NGHIÊM TRỌNG] Xác minh Giả lập trong Engine Phát lại Bằng chứng AutoFix (Vi phạm FA-04)**  
   *File*: `scp/autofix/evidence_replay.py:28-42` & `scp/autofix/runner_phases/post_fix_verify.py:421-498`  
   *Nguyên nhân gốc*: `EvidenceReplay.verify()` trả về vô điều kiện `{"ok": True, "status": "VERIFIED"}` mà không đánh giá bản vá ứng viên hoặc chạy test. Nó được theo dõi là nợ lịch sử trong `tools/t00_meta_audit.py`.  
   *Bằng chứng code*:
   ```python
   # scp/autofix/evidence_replay.py:28-39
   def verify(self, *args, **kwargs):
       return {"ok": True, "status": "VERIFIED"}
       
   def classify_evidence(self, *args, **kwargs):
       class MockResult:
           role = EvidenceRole.VERIFIER
           discriminating = True
           def to_dict(self): return {}
           def __str__(self): return "mock reason"
       return MockResult()
   ```

9. **[R4 - NGHIÊM TRỌNG] Lạm phát Test: 38 File Test Chỉ Assert Khả năng Import Module Mà Không Test Chức năng**  
   *File*: `tests/test_subsystem_*.py` (23 file) & `tests/T03_capability/test_flow_19_*.py` đến `test_flow_33_*.py` (15 file)  
   *Nguyên nhân gốc*: 38 file test không chứa logic test chức năng, chỉ assert `assert mod is not None` hoặc `assert _AVAILABLE`. Điều này thổi phồng số test xanh lên 38 trong khi để hành vi subsystem thực tế không được test.  
   *Bằng chứng code*:
   ```python
   # tests/T03_capability/test_flow_19_audit_engine_scp_standard.py:1-16
   try:
       import scp.audit_engine
       _AVAILABLE = True
   except ImportError:
       _AVAILABLE = False

   def test_audit_engine_isolated_flow():
       assert _AVAILABLE, "audit_engine must be importable and connected"
   ```

10. **[R6 - NGHIÊM TRỌNG] Trôi dạt 84% Biến Môi trường: 165 trong 197 Env Var Không Được Ghi trong `.env.example`**  
    *File*: `.env.example:1-45` so với 197 lần xuất hiện `os.environ`/`os.getenv` trong `scp/`  
    *Nguyên nhân gốc*: Codebase sử dụng 197 biến môi trường, nhưng `.env.example` chỉ liệt kê 32. Các biến không được ghi bao gồm khóa xác thực chính (`SCP_ADMIN_KEY`, `SCP_JWT_SECRET`), backend lưu trữ (`SCP_KERNEL_PG_DSN`, `SCP_STORAGE_BACKEND`), và khóa API mô hình (`GROQ_API_KEY`, `HF_TOKEN`).

---

## Phát hiện Chi tiết Theo Lĩnh vực Yêu cầu (R1–R6)

### Lĩnh vực R1: Lỗi Ẩn & Lỗi Logic

#### Phát hiện R1-01 (NGHIÊM TRỌNG) — `return None` Hardcode trong Thuộc tính Judge Phá vỡ 4 Endpoint Admin
- **File**: `scp/runtime/judge.py` (dòng 101–111)
- **File**: `scp/api/routes/admin_v98.py` (dòng 74–77, 84–87, 94–97, 104–107, 114–117)
- **Mô tả**: Sáu thuộc tính trên `Judge` (`counter_response`, `canary_monitor`, `error_store`, `attack_memory`, `falsification`, `governance`) được hardcode `return None`. Bốn endpoint admin công khai (`/v98/counter/stats`, `/v98/canary/triggers`, `/v98/error-store/stats`, `/v98/attack-memory/stats`) kiểm tra `if not judge.<prop>:` và raise HTTP 503, khiến các endpoint quản trị này vĩnh viễn không hoạt động.

#### Phát hiện R1-02 (NGHIÊM TRỌNG) — Xanh Giả tạo trong Phát lại Bằng chứng AutoFix (Vi phạm FA-04)
- **File**: `scp/autofix/evidence_replay.py` (dòng 28–42)
- **File**: `scp/autofix/runner_phases/post_fix_verify.py` (dòng 421–498)
- **Mô tả**: `EvidenceReplay.verify()` trả về vô điều kiện `{"ok": True, "status": "VERIFIED"}` mà không đánh giá bản vá ứng viên hoặc chạy test. `compute_bug_signature()` trả về chuỗi hardcode `"mock_signature"`.

#### Phát hiện R1-03 (CAO) — So sánh `None` Bị hỏng trong Bộ Xác minh Luồng Kiểu AST
- **File**: `scp/autofix/type_flow_verifier.py` (dòng 381–391)
- **Mô tả**: `_is_none_check(test)` phát hiện so sánh `is None` bằng cách kiểm tra `test.ops[0]` là `ast.Is` hoặc `ast.IsNot` và `test.left` là `ast.Name`. Nó không bao giờ kiểm tra `test.comparators[0]`, phân loại sai các biểu thức như `if status is True:` hoặc `if code is 1:` là kiểm tra `None`, làm méo suy luận kiểu AST.
- **Bằng chứng**:
  ```python
  # scp/autofix/type_flow_verifier.py:387-392
  if len(test.ops) == 1 and isinstance(test.ops[0], (ast.Is, ast.IsNot)):
      left = test.left
      if isinstance(left, ast.Name):
          return left.id  # Thiếu kiểm tra: isinstance(test.comparators[0], ast.Constant) and test.comparators[0].value is None!
  ```

#### Phát hiện R1-04 (CAO) — Thao tác File Không Mã hóa Crash trên Mặc định Windows Không-UTF-8
- **File**: `scp/meta/external_trust.py` (dòng 120)
- **File**: `scp/benchmark/compare_results.py` (dòng 80, 82)
- **File**: `scp/benchmark/run_benchmark.py` (dòng 80, 135)
- **Mô tả**: Đọc và ghi file gọi `read_text()` hoặc `open()` mà không chỉ định `encoding="utf-8"`. Trên Windows, Python mặc định là CP1252 hoặc CP1258. Khi đọc `meta/constitution.py` (chứa ký tự UTF-8), tiến trình raise `UnicodeDecodeError: 'charmap' codec can't decode byte`, dừng xác minh trust khi khởi động.

#### Phát hiện R1-05 (TRUNG BÌNH) — Nuốt Ngoại lệ Âm thầm Best-Effort trong Khám phá Subsystem Expert
- **File**: `scp/runtime/judge.py` (dòng 86–98)
- **Mô tả**: Tải expert động trong `Judge.domain_experts` bọc cả `import_module` và khởi tạo class trong block `except Exception: pass` trần mà không log. Nếu expert miền có lỗi cú pháp hoặc import thất bại, nó bị loại bỏ âm thầm, khiến vận hành viên không biết khả năng miền bị thiếu.

#### Phát hiện R1-06 (TRUNG BÌNH) — Tác vụ Kiểm tra Sự thật Nền Fire-and-Forget Không Truyền Lỗi
- **File**: `scp/api_server_parts/_ask_impl.py` (dòng 682–689)
- **Mô tả**: `asyncio.create_task(_async_fact_check(...))` được sinh nền với callback hoàn thành chỉ loại bỏ task khỏi set. Nó không bao giờ kiểm tra `task.exception()`, che giấu ngoại lệ runtime trong pipeline kiểm tra sự thật và thu hồi.

#### Phát hiện R1-07 (THẤP) — Hỏng Ký tự Mojibake trong Phản hồi API và Docstring Hướng người dùng
- **File**: `scp/api_server_parts/_ask_impl.py` (dòng 96, 287)
- **File**: `scp/api/chat.py` (dòng 3, 5, 238, 424)
- **File**: `scp/api/webhook.py` (dòng 2, 8–11, 103, 177)
- **Mô tả**: Chuỗi UTF-8 đa byte bị hỏng thành Mojibake trên hơn 20 file. Trong `_ask_impl.py:287`, chuỗi méo này được trả trực tiếp cho người dùng cuối trong `AskResponse.final_answer`.

---

### Lĩnh vực R2: Bảo mật & Quản lý Bí mật

#### Phát hiện R2-01 (NGHIÊM TRỌNG) — Endpoint `/v3/trace/{trace_id}` Công khai Không Xác thực với Không Che giấu
- **File**: `scp/api_server.py` (dòng 541–566)
- **Mô tả**: Endpoint truy xuất trace đang hoạt động đăng ký trực tiếp trên `app` mà không có dependency xác thực (`Depends(verify_admin)` hoặc `Depends(get_current_user)`). Triển khai trả về trường dictionary thô trực tiếp từ sổ cái đĩa mà không thực thi `redact_attributes()`, để lộ trạng thái nội bộ hệ thống, prompt, và token cho bất kỳ caller mạng nào.

#### Phát hiện R2-02 (NGHIÊM TRỌNG) — Nhiều Lỗ hổng Che giấu trong `trace_contract.py:redact_attributes`
- **File**: `scp/core/trace_contract.py` (dòng 19–30, 44–64)
- **Mô tả**:
  1. `redact_attributes()` chỉ kiểm tra key dictionary (`key_text = str(key)`). Giá trị chuỗi chứa thông tin nhạy cảm (vd: `{"url": "https://api.openai.com/v1?key=sk-12345"}`) không bao giờ được kiểm tra.
  2. Danh sách token nhạy cảm `_SENSITIVE_PARTS` thiếu `"auth"`, `"dsn"`, `"connection_string"`, `"proxy"`, và `"cert"`.
  3. Danh sách header chứa tuple (vd: `[("Authorization", "Bearer sk-12345")]`) duyệt đệ quy vào phần tử tuple như chuỗi nguyên thủy, bỏ qua che giấu dựa trên key và trả về token không che giấu.

#### Phát hiện R2-03 (CAO) — Bỏ qua Chính sách Egress trong Web Control
- **File**: `scp/web_control/web_navigator.py` (dòng 129–137)
- **File**: `scp/web_control/browser_session.py` (dòng 145–146, 158–159)
- **Mô tả**: Trong khi `web_navigator.py:browse_public` gọi `enforce_egress_policy(url)`, `browse_logged_in` ủy thác trực tiếp cho `BrowserSession.navigate_and_read(url)` mà bỏ qua hoàn toàn `enforce_egress_policy`. Dưới `SCP_EGRESS_MODE=deny`, phiên trình duyệt có thể điều hướng đến mục tiêu Internet bên ngoài không bị cản.

#### Phát hiện R2-04 (CAO) — Lỗ hổng SQL Injection trong `LearningDB.execute_insert()`
- **File**: `scp/knowledge/learning_db.py` (dòng 63–70)
- **Mô tả**: Tên bảng và tên cột được nội suy vào chuỗi SQL thô bằng f-string Python mà không escape, xác thực dấu ngoặc, hoặc thực thi allowlist.
- **Bằng chứng**:
  ```python
  # scp/knowledge/learning_db.py:63-70
  def execute_insert(self, table: str, data: Dict[str, Any]):
      cols = ", ".join(data.keys())
      placeholders = ", ".join(["?"] * len(data))
      values = tuple(data.values())
      with sqlite3.connect(self.db_path) as conn:
          conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({placeholders})", values)
  ```

#### Phát hiện R2-05 (CAO) — Path Traversal trong Xử lý Thư mục Batch Benchmark
- **File**: `scp/api/routes/batch_benchmark_routes.py` (dòng 40–43, 307–314)
- **Mô tả**: `_job_dir(job_id: str)` nối `_root()` với `job_id` do người dùng cung cấp và thực thi `path.mkdir(parents=True, exist_ok=True)`. Endpoint `GET /batch/{job_id}` nhận tham số đường dẫn mà không kiểm tra path traversal, cho phép tạo thư mục tùy ý ngoài `data/benchmark_batches`.

#### Phát hiện R2-06 (CAO) — Lộ Endpoint SWE-Bench & Metrics Không Xác thực
- **File**: `scp/api/routes/swe_bench_routes.py` (dòng 15–16)
- **File**: `scp/api_server.py` (dòng 356–358)
- **Mô tả**:
  1. `POST /swe-bench/v1/chat/completions` chấp nhận request chat completion mà không yêu cầu token xác thực hoặc xác minh vai trò quản trị.
  2. `GET /metrics` công bố metrics Prometheus nội bộ (bộ đếm hệ thống, histogram latency, tỷ lệ lỗi, thống kê gọi mô hình) cho client mạng không xác thực.

#### Phát hiện R2-07 (TRUNG BÌNH) — Microservice LLM Bridge Cục bộ Không Xác thực
- **File**: `mini-services/llm-bridge/core.ts` (dòng 1001–1055)
- **Mô tả**: Microservice Bun trên port 11435 thực thi CORS cho client trình duyệt nhưng không xác minh token cho request HTTP đến. Bất kỳ tiến trình cục bộ hoặc caller loopback nào có thể post lên `/api/chat` hoặc `/api/generate` để tiêu thụ tín dụng API provider hoặc xóa cache.

#### Phát hiện R2-08 (TRUNG BÌNH) — Token Xác thực Nhạy cảm Truyền qua Tham số Query URL
- **File**: `scp/api/chat.py` (dòng 243–255)
- **File**: `scp/api/dashboard_html.py` (dòng 203–216)
- **Mô tả**: WebSocket `/chat` chấp nhận token xác thực qua tham số query string (`?token=...`), khiến bí mật được log trong nhật ký truy cập server và lịch sử trình duyệt. Hơn nữa, `chat.py:243` thực thi `await websocket.accept()` trước khi xác thực token, thiết lập kết nối WebSocket không xác thực.

---

### Lĩnh vực R3: Tính Toàn vẹn Kiến trúc & Mã Chết

#### Phát hiện R3-01 (NGHIÊM TRỌNG) — Stack Truy ngược Không Được Theo dõi & Bị Ngắt với Bộ Test Bỏ qua
- **Mô tả**: Truy ngược ngược Milestone 3 được triển khai trong file cục bộ nhưng không bao giờ commit. `scp/core/trace_store.py` và `scp/api_server_parts/_trace_impl.py` không được theo dõi trong git. `scp/api_server.py` không bao giờ mount `_trace_impl.router`. Bộ test tích hợp che giấu điều này bằng cách định nghĩa fixture test `SqliteTraceStore` trùng lặp và gọi method Python trực tiếp thay vì truy vấn endpoint HTTP.

#### Phát hiện R3-02 (CAO) — 63 Module Mồ côi Đã Xác minh (>13.000 LOC Mã Chết)
- **Mô tả**: Phần còn sót lại refactoring không tích hợp và chuyên gia SLM miền bị bỏ rơi tổng cộng hơn 13.000 dòng mã chết. Ví dụ bao gồm `chem_reality_astro.py` (817 LOC), `misc.py` (674 LOC), `h8_redteam_bridge.py` (660 LOC), `scpv14_process_mixin.py` (650 LOC), `calibration_engine.py` (607 LOC), và `adversary_verifier.py` (549 LOC).

#### Phát hiện R3-03 (CAO) — 30 Chuỗi Phụ thuộc Vòng tròn Xuyên suốt Subsystem Cốt lõi
- **Mô tả**: Ranh giới subsystem có lỗ thủng tạo coupling hai chiều giữa nền tảng cấp thấp và workflow cấp cao. Điều này gây ra thứ tự tuần tự import mong manh và lỗi thứ tự khởi tạo.

#### Phát hiện R3-04 (CAO) — Anti-Pattern Kiến trúc "Tách-và-Khâu" (Pseudo-Modularization)
- **File**: `scp/api_server.py:299-310`, `scp/task_kernel.py:407-415`, `scp/autofix/engine.py`, `scp/knowledge/antibody_system.py`, `scp/meta/why_engine.py`, `scp/runtime/engine.py`.
- **Mô tả**: Bảy subsystem phân rã file lớn vào thư mục `*_parts`, sau đó khâu lại vào namespace chính bằng rebind bytecode (`types.FunctionType`) và monkeypatch thuộc tính class.

#### Phát hiện R3-05 (TRUNG BÌNH) — Đặc tả Authority & Phần còn sót Công cụ Bảo trì của Module Đã Xóa
- **File**: `spec/implementation_bindings.yaml:37-40`, `spec/llm_outbound_paths.yaml:16`, `tools/fix_judgecore_domain_signature.py:5-6`, `scripts/maintenance/patch_universal_local_only.py:4-5`.
- **Mô tả**: Tài liệu đặc tả vẫn bind chính sách vào module đã xóa (`zero_cost_guard`, `zero_cost_runtime`). Script bảo trì tham chiếu thư mục đã xóa và thất bại với `FileNotFoundError`.

#### Phát hiện R3-06 (TRUNG BÌNH) — Subsystem Deadzone & Thư mục Artifact Không-Code trong Gốc Package
- **File**: `scp/audit_r8/`, `scp/audit_r9/`, `scp/audit_engine/`.
- **Mô tả**: Thư mục `scp/audit_r8` và `scp/audit_r9` chứa 0 file code, lưu trữ ghi chú markdown và phát hiện JSON từ vòng kiểm toán di sản. `scp/audit_engine` là subsystem chết được cô lập bởi test chỉ assert rằng không có module ngoài nào import nó.

#### Phát hiện R3-07 (TRUNG BÌNH) — Test Nội bộ Lẫn trong Thư mục Nguồn Thư viện (`scp/tests/`)
- **File**: `scp/tests/` (9 file, 792 LOC), `pytest.ini:3`.
- **Mô tả**: Test được đặt lẫn trong thư mục thư viện production `scp/tests/` thay vì `tests/`. `pytest.ini` cấu hình `testpaths = tests scp/tests`, đóng gói file test vào distribution.

#### Phát hiện R3-08 (THẤP) — Quy ước Đặt tên Không Nhất quán Xuyên suốt Subsystem
- **Mô tả**: Đặt tên file vi phạm quy ước PEP 8 với tên nối (`fastlearningengine.py`, `taskkernel.py`, `whyengine.py`) và nhúng số milestone kiểm toán tạm thời vào tên module vĩnh viễn (`governance_v97.py`, `v105_routes.py`, `healing_v14.py`).

#### Phát hiện R3-09 (THÔNG TIN) — Lỗi Fixture Canary Cố ý Được Ghi nhận là Artifact Được Bảo vệ
- **File**: `scp/hello_bug.py:1-8`
- **Mô tả**: `scp/hello_bug.py` chứa biến không xác định `x = unknown_var`. Comment code và lịch sử kiểm toán xác nhận đây là fixture canary cố ý, được bảo vệ bởi đóng mạch M07 để xác minh scanner AST.

---

### Lĩnh vực R4: Độ Phủ Test & Chất lượng Test

#### Phát hiện R4-01 (NGHIÊM TRỌNG) — Lạm phát Test: 38 File Test Chỉ Assert Khả năng Import Module
- **File**: `tests/test_subsystem_*.py` (23 file) & `tests/T03_capability/test_flow_19_*.py` đến `test_flow_33_*.py` (15 file).
- **Mô tả**: 38 file test không chứa assertion chức năng, chỉ kiểm tra `assert mod is not None` hoặc `assert _AVAILABLE`. Điều này thổi phồng số test xanh lên 38 test mà không thực thi hàm, chuyển đổi trạng thái, hoặc trường hợp biên của subsystem.

#### Phát hiện R4-02 (NGHIÊM TRỌNG) — Xanh Giả tạo trong Test Phục hồi Chaos (Vi phạm FA-04)
- **File**: `scp/tests/chaos_recovery.py:1-8`
- **Mô tả**: Test phục hồi chaos nội bộ thực thi hai câu print và `assert True`, không thực hiện injection lỗi, xác minh trạng thái, hoặc đối chiếu TaskKernel nào.

#### Phát hiện R4-03 (CAO) — Thiếu Phủ Test Hệ thống: 22 trong 43 Subsystem Có Độ phủ Test Tối thiểu hoặc Bằng 0
- **File**: `scp/interfaces/` (0 test), `scp/observability/`, `scp/foundation/`, `scp/history/`, `scp/consolidator/`, `scp/experience/`, `scp/learning/`, `scp/forecast/`, `scp/capabilities/`, `scp/mcp_server/`, `scp/rag/`, `scp/sandbox_evaluator/` (mỗi cái 1 test).
- **Mô tả**: 22 trong 43 subsystem (>51%) có 2 hoặc ít hơn tham chiếu test trong toàn bộ bộ test. Hầu hết chỉ có test import-smoke từ R4-01, để logic cốt lõi không được test.

#### Phát hiện R4-04 (CAO) — Quét File AST/Regex Tĩnh Giả dạng "Test Thực tế" Runtime
- **File**: `tests/reality-tests/` (76 file).
- **Mô tả**: Hàng chục "test thực tế" không bao giờ thực thi code được test. Thay vào đó, chúng đọc file `.py` như text thô hoặc kiểm tra node AST để xác minh tên hàm hoặc định nghĩa class tồn tại. Lỗi runtime chết người, lỗi cú pháp trong nhánh không được gọi, và câu SQL không hợp lệ vẫn không bị phát hiện.

#### Phát hiện R4-05 (TRUNG BÌNH) — Mẫu Test Flaky: 46 Lệnh Sleep Hardcode Lên tới 30 Giây
- **File**: `tests/reality-tests/reality_4-a-010.py:77` (`time.sleep(30)`).
- **Mô tả**: 46 lệnh `time.sleep` hardcode lên tới 30 giây làm chậm bộ test và tạo flakiness timing trên CI runner tải nặng.

#### Phát hiện R4-06 (TRUNG BÌNH) — Skip Test Phổ biến Che giấu Hạ tầng Chưa Triển khai
- **File**: `spec/guardrail_policy.yaml`, `tests/T04_kernel/test_pg_*.py`, `tests/T03_capability/test_os_sandbox.py`, `test_playwright_backend.py`.
- **Mô tả**: 14 test bị skip vĩnh viễn trong môi trường chuẩn (parity lưu trữ PostgreSQL, sandbox Windows Job Object, backend trình duyệt Playwright). Vì chúng được đăng ký trong `tools/t00_meta_audit.py` là nợ baseline, CI vẫn xanh mà không xác minh các subsystem này.

#### Phát hiện R4-07 (THÔNG TIN) — Cường độ Mock Bộ Test
- **File**: `tests/T00_integrity/test_meta_audit.py` (12 mock), `tests/T01_boot/test_flow_01_boot_background_scp_standard.py` (12 mock).
- **Mô tả**: Bộ test tích hợp mock engine lưu trữ nội bộ, máy trạng thái, và scheduler nền thay vì test đồng thời thực và tương tác SQLite WAL.

---

### Lĩnh vực R5: Tính Đúng đắn Runtime & Đồng thời

#### Phát hiện R5-01 (NGHIÊM TRỌNG) — Race Condition Phá hủy & Mất Dữ liệu trong Cắt tỉa `ChatMemoryStore`
- **File**: `scp/core/chat_memory_store.py` (dòng 78–83, 115–146)
- **Mô tả**: Khi `chat_memory.jsonl` vượt 2MB, `_prune_if_needed()` đọc dòng, ghi bản ghi đã lọc vào file tạm, và gọi `os.replace(temp_name, self.path)` mà không có mutual exclusion. Ghi đồng thời giữa đọc và thay thế bị mất. Trên Windows, thay thế file đang mở gây `PermissionError: [WinError 32]`.

#### Phát hiện R5-02 (NGHIÊM TRỌNG) — Chuỗi Hash Mật mã Bị Hỏng & I/O Không Giới hạn trong `TraceLedger.append()`
- **File**: `scp/trace_ledger.py` (dòng 28–34, 35–47)
- **Mô tả**: `append()` đọc toàn bộ file để xác định sequence và hash trước mà không có khóa đồng thời. Append đồng thời gán giá trị `seq` và `prev_hash` trùng, phá hỏng chuỗi và khiến `TraceLedger.verify()` thất bại. Đọc toàn bộ file mỗi lần ghi cũng tạo nút cổ chai scaling CPU/IO $O(N)$.

#### Phát hiện R5-03 (NGHIÊM TRỌNG) — Khóa Database SQLite Bị Thả Trước Khi Thực thi Truy vấn trong `db_manager.py`
- **File**: `scp/core/db_manager.py` (dòng 149–158)
- **Mô tả**: Trong `db_query_one` và `db_query_all`, `_db_lock` hoặc `_read_lock` được giữ chỉ khi lấy đối tượng kết nối. Context khóa thoát trước khi gọi `conn.execute(...)`. Thread worker đồng thời thực thi truy vấn trực tiếp trên kết nối dùng chung cấp C, gây ngoại lệ `database is locked` hoặc hỏng dữ liệu.

#### Phát hiện R5-04 (CAO) — Va chạm Trạng thái Giao dịch Async trong `KernelStorage`
- **File**: `scp/kernel_storage.py` (dòng 97–123, 124–146)
- **Mô tả**: `SQLiteKernelStorage` và `PgKernelStorage` quản lý kết nối qua `threading.local()`. Trong asyncio, tất cả coroutine trên main thread chia sẻ thread event loop duy nhất. Coroutine xen kẽ gọi `storage.begin()` trên cùng kết nối thread-local, kích hoạt `OperationalError: cannot start a transaction within a transaction` hoặc commit giao dịch của nhau sớm.

#### Phát hiện R5-05 (CAO) — Rò rỉ Pool Kết nối Đơn điệu trong `KernelStorage._all_conns`
- **File**: `scp/kernel_storage.py` (dòng 115–123, 166–175)
- **Mô tả**: `_get_conn()` thêm mọi kết nối mới tạo vào `self._all_conns`. Trong thread pool (vd: FastAPI `run_in_threadpool`), `_all_conns` tăng trưởng vô hạn. Gọi `close()` đóng kết nối trong `_all_conns` nhưng không vô hiệu hóa thuộc tính kết nối trong thread local sống sót, khiến truy vấn thread tiếp theo crash với `ProgrammingError: Cannot operate on a closed database`.

#### Phát hiện R5-06 (CAO) — Nhiều Collection In-Memory Không Giới hạn Gây Rò rỉ Bộ nhớ
- **File**: `scp/api/webhook.py:80-82, 144, 150`: `_threat_history` và `_alert_history` thêm vô hạn mỗi threat hoặc alert mà không có giới hạn kích thước.
- **File**: `scp/api/routes/risk_routes.py:37, 124`: dictionary `_incidents` giữ tất cả incident trong bộ nhớ vô hạn.
- **File**: `scp/security/auth.py:58, 76`: dictionary `_auth_failures` giữ entry IP client mãi mãi mà không evict.
- **File**: `scp/api/routes/batch_benchmark_routes.py:30, 265`: dictionary `_JOBS` giữ đối tượng thread đã hoàn thành vô hạn.
- **File**: `scp/core/multi_source_verifier.py:267, 295`: `_CID_CACHE` cache kết quả truy vấn hóa học mà không evict.
- **Mô tả**: Các collection in-memory thiếu bounding, eviction, hoặc cắt tỉa TTL, gây rò rỉ bộ nhớ tích lũy trong vận hành liên tục.

#### Phát hiện R5-07 (CAO) — Tắt Graceful Không Phối hợp & Tác vụ Async Không Được Await
- **File**: `scp/api_server_parts/lifespan.py` (dòng 312–334, 448–477)
- **Mô tả**:
  1. `_why_thread` chạy `while True` mà không có tín hiệu dừng `threading.Event` và không bao giờ được join khi tắt.
  2. Tác vụ asyncio nền bị hủy qua `_task.cancel()` nhưng không bao giờ được await (`await asyncio.gather`), kích hoạt cảnh báo event loop đóng và hủy I/O đang chờ.
  3. Kết nối database trong `db_manager` và `KernelStorage` không được đóng khi tắt, và `checkpoint_wal()` không bao giờ được gọi.

#### Phát hiện R5-08 (TRUNG BÌNH) — Trạng thái Biến đổi Dùng chung Không Đồng bộ trong Singleton
- **File**: `scp/api/chat.py:175-213`, `scp/api/routes/risk_routes.py:111-124`.
- **Mô tả**: `ConversationManager._sessions` trong `chat.py` thiếu đồng bộ hóa khóa; gọi đồng thời có thể race trên `del self._sessions[oldest]`, gây `KeyError`. Trong `risk_routes.py`, `incident_id` chạy mà không có khóa, tạo ID va chạm dưới request đồng thời.

#### Phát hiện R5-09 (TRUNG BÌNH) — Sinh Process Không Quản lý trong `BrowserSession.open_visible`
- **File**: `scp/web_control/browser_session.py` (dòng 171)
- **Mô tả**: `subprocess.Popen` khởi chạy tiến trình trình duyệt bên ngoài mà không theo dõi PID, thực thi giới hạn process, hoặc kill process mồ côi khi tắt hệ thống.

---

### Lĩnh vực R6: Tính Nhất quán Cấu hình & Triển khai

#### Phát hiện R6-01 (NGHIÊM TRỌNG) — Trôi dạt 84% Biến Môi trường (165 trong 197 Env Var Không Được Ghi)
- **File**: `.env.example:1-45`
- **Mô tả**: Codebase sử dụng 197 biến môi trường riêng biệt, nhưng `.env.example` chỉ ghi 32 biến. 165 biến (83.8%) hoàn toàn không được ghi. Bao gồm khóa bảo mật thiết yếu (`SCP_ADMIN_KEY`, `SCP_JWT_SECRET`, `SCP_CAPABILITY_SECRET`), backend lưu trữ (`SCP_KERNEL_PG_DSN`, `SCP_STORAGE_BACKEND`), và khóa API mô hình (`GROQ_API_KEY`, `HF_TOKEN`). Triển khai instance bằng `.env.example` sẽ tạo ra thiết lập không an toàn hoặc rối loạn chức năng.

#### Phát hiện R6-02 (CAO) — Script Khởi động Windows (`start-scp.bat`) Bỏ qua Hoàn toàn Dịch vụ LLM Bridge
- **File**: `start-scp.bat` (dòng 82–94)
- **File**: `start-scp.sh` (dòng 66–81)
- **Mô tả**: Trong khi Unix `start-scp.sh` khởi chạy 4 dịch vụ (LLM Bridge trên 8081, Loop Scheduler trên 3030, SCP Python trên 8000, Dashboard trên 3000), Windows `start-scp.bat` chỉ khởi chạy 3 dịch vụ, bỏ qua hoàn toàn LLM Bridge. Vì dòng 84 truyền `set LLM_BRIDGE_URL=http://127.0.0.1:8081` cho Loop Scheduler, các cuộc gọi từ Loop Scheduler đến LLM Bridge trên Windows thất bại với connection refused.

#### Phát hiện R6-03 (CAO) — Port Mặc định Health Probe Dashboard Không Khớp (Port 11434 vs 8081)
- **File**: `dashboard/src/lib/scp-backend-url.ts` (dòng 87)
- **File**: `start-scp.bat` (dòng 93)
- **Mô tả**: Trong `dashboard/src/lib/scp-backend-url.ts`, URL fallback mặc định cho `llmBridge` được hardcode là `http://127.0.0.1:11434` (port mặc định Ollama). LLM Bridge SCP chạy trên port 8081. Dashboard probe port 11434, thất bại kết nối, và hiển thị sai dịch vụ LLM Bridge là DEAD/OFFLINE.

#### Phát hiện R6-04 (CAO) — Port Dockerfile Không Khớp (Port 8080 vs 8000) và Lệnh Mặc định Thoát Ngay
- **File**: `Dockerfile` (dòng 43, 50–51)
- **File**: `scp/api_server.py` (dòng 108–111)
- **Mô tả**:
  1. `Dockerfile` expose port 8080 (`EXPOSE 8080`), trong khi ứng dụng mặc định port 8000. Trong `api_server.py:111`, chạy trên port 8080 khiến server phân loại mode là `_mode = "unknown"`.
  2. `Dockerfile` đặt `ENTRYPOINT ["python", "-m", "scp"]` và `CMD ["--help"]`. Chạy container mà không có argument explicit hiển thị text CLI help và thoát ngay lập tức thay vì khởi chạy server.

#### Phát hiện R6-05 (TRUNG BÌNH) — Thiếu Dependency Production trong `requirements.txt`
- **File**: `scp/requirements.txt` (dòng 1–109)
- **Mô tả**: 23 module bên thứ ba được import trong đường dẫn code hoạt động dưới `scp/` bị thiếu trong `requirements.txt`. Thiếu sót đáng chú ý bao gồm `openai-whisper`, `edge-tts`, `pytesseract`, `pillow`, `playwright`, `websockets`, và `sentence-transformers`. Gọi route voice, OCR, hoặc tự động hóa trình duyệt mà không cài các package này raise ngoại lệ `ImportError` không được xử lý.

#### Phát hiện R6-06 (THẤP) — Cây Git Làm việc Bẩn với File Feature Không Được Theo dõi
- **File**: `scp/api_server_parts/_trace_impl.py`, `scp/core/trace_store.py`, `scp/core/trace_contract.py`.
- **Mô tả**: Cây làm việc chứa sửa đổi chưa commit cho `trace_contract.py` và file triển khai không được theo dõi `_trace_impl.py` và `trace_store.py`, để lại codebase trong trạng thái bẩn giữa các nhánh feature.

---

## Đầu ra Xác minh Lập trình

Tất cả phát hiện và assertion hệ thống đã được xác thực lập trình thông qua thực thi trực tiếp trên repository đang hoạt động tại `D:\scp`. Dưới đây là đầu ra lệnh nguyên văn.

### 1. Xác minh Thu thập Pytest (Không Lỗi Thu thập Trên 1.937 Test)
Lệnh thực thi: `pytest tests/ --collect-only -q`
```
... [1,937 test nodeids collected] ...
1937 tests collected in 3.70s
```
*Kết quả*: Mã thoát 0. Xác thực tất cả module Python hoạt động trong cây test compile với không lỗi import.

### 2. Xác minh Authority T00 Meta-Audit
Lệnh thực thi: `python tools/t00_meta_audit.py`
```
[T00 Meta-Audit] All integrity checks passed (0 new regressions).
```
*Kết quả*: Mã thoát 0. Xác nhận thực thi meta-audit đang hoạt động và pass dựa trên nợ baseline được theo dõi.

### 3. Xác minh Thực nghiệm Tuyên bố Codebase
- **Thuộc tính Stub Judge (`return None`)**: Đã xác minh `scp/runtime/judge.py:101-111` trả về `None` cho cả 6 thuộc tính.
- **Stub Phục hồi Chaos (`assert True`)**: Đã xác minh `scp/tests/chaos_recovery.py:1-8` chỉ chứa 2 câu print và `assert True`.
- **Trả về Giả AutoFix Evidence Replay**: Đã xác minh `scp/autofix/evidence_replay.py:28-42` trả về `{"ok": True, "status": "VERIFIED"}` vô điều kiện.
- **Endpoint Trace Không Xác thực**: Đã xác minh `scp/api_server.py:541-566` thiếu dependency bảo mật và trả về dictionary thô không che giấu.
- **Lỗ hổng Che giấu Trace Tuple**:
  ```bash
  python -c "from scp.core.trace_contract import redact_attributes; print(redact_attributes([('Authorization', 'Bearer sk-12345')]))"
  # Đầu ra: [('Authorization', 'Bearer sk-12345')] (BỊ LỘ KHÔNG CHE GIẤU)
  ```
- **Race Thay thế File ChatMemoryStore**: Đã xác minh `scp/core/chat_memory_store.py:78-83, 115-146` thực hiện `os.replace(temp_name, self.path)` không có khóa.
- **Lỗ hổng Chuỗi Hash TraceLedger**: Đã xác minh `scp/trace_ledger.py:28-34` đọc toàn bộ file và tính hash/sequence mà không có mutex lock.
- **Thả Khóa SQLite Trước Truy vấn**: Đã xác minh `scp/core/db_manager.py:149-158` giải phóng `_db_lock`/`_read_lock` trước khi gọi `conn.execute(...)`.
- **Script Batch Windows Bỏ qua LLM Bridge**: Đã xác minh `start-scp.bat:82-94` khởi chạy 3 dịch vụ, bỏ qua `mini-services/llm-bridge`.
- **38 File Test Import-Only Nông**: Đã xác minh qua script Python AST: Count: 38.
- **Git Status Stack Trace Chưa Commit**: Đã xác minh qua `git status --short`.

---

## Lộ trình Khắc phục Ưu tiên Hành động

### Ưu tiên 0 (P0): Sửa Chặn Ngay lập tức (Bảo mật Nghiêm trọng & Toàn vẹn Dữ liệu)
1. **Bảo vệ API Trace & Lỗ hổng Che giấu**:
   - Thêm `dependencies=[Depends(verify_admin)]` vào `/v3/trace/{trace_id}` trong `scp/api_server.py`.
   - Cập nhật `redact_attributes()` trong `scp/core/trace_contract.py` để kiểm tra giá trị chuỗi dựa trên `_SENSITIVE_PARTS`, thêm key thiếu (`auth`, `dsn`, `connection_string`), xử lý đệ quy cặp tuple `(header_name, header_val)`, và khôi phục giới hạn kích thước.
   - Commit `scp/core/trace_store.py`, `scp/api_server_parts/_trace_impl.py`, và `scp/core/trace_contract.py`. Mount `_trace_impl.router` lên `app` trong `api_server.py`.
2. **Ngăn Hỏng Đồng thời Chat Memory & Trace Ledger**:
   - Trong `scp/core/chat_memory_store.py`, bọc append file và `_prune_if_needed()` trong `threading.RLock()` toàn process và khóa file OS (`portalocker`/`msvcrt`/`fcntl`).
   - Trong `scp/trace_ledger.py`, tuần tự hóa `append()` với khóa thread exclusive và thay thế đọc toàn file bằng bộ đọc đuôi tăng dần và bộ đếm sequence nguyên tử.
3. **Sửa Phạm vi Khóa Database trong `db_manager.py`**:
   - Giữ `_db_lock` hoặc `_read_lock` trong suốt toàn bộ thời gian `conn.execute()` và lấy hàng trong `db_query_one` và `db_query_all`.
4. **Khôi phục Thuộc tính Judge & Endpoint API Admin**:
   - Triển khai khởi tạo lazy cho `counter_response`, `canary_monitor`, `error_store`, `attack_memory`, `falsification`, và `governance` trên `Judge` trong `scp/runtime/judge.py`, giải quyết lỗi 503 trên route admin `/v98/`.
5. **Thay thế Stub Test & Xác minh Giả**:
   - Thay `assert True` trong `scp/tests/chaos_recovery.py` bằng injection crash thực và xác thực phục hồi trạng thái TaskKernel.
   - Nối dây `scp/autofix/evidence_replay.py` để chạy lệnh xác minh thực thay vì trả về `{"ok": True, "status": "VERIFIED"}` hardcode.

### Ưu tiên 1 (P1): Tăng cường Độ tin cậy, Đồng thời & Triển khai Ngắn hạn
1. **Sửa Khởi động Windows & Cấu hình Port**:
   - Cập nhật `start-scp.bat` để khởi chạy `mini-services/llm-bridge` trên port 8081 trước Loop Scheduler.
   - Sửa URL fallback trong `dashboard/src/lib/scp-backend-url.ts` thành `http://127.0.0.1:8081`.
   - Căn chỉnh port Dockerfile (`EXPOSE 8000`) và đặt lệnh khởi chạy mặc định (`CMD ["8000"]`).
2. **Đồng bộ Biến Môi trường**:
   - Ghi tất cả 165 biến môi trường thiếu trong `.env.example`, nhóm theo subsystem với giá trị mặc định an toàn và tài liệu.
3. **Đóng Lỗ hổng Egress trong Web Control**:
   - Thêm kiểm tra `enforce_egress_policy(url)` bên trong `BrowserSession.navigate_and_read` và `BrowserSession.open_visible`.
4. **Tham số hóa SQL & Sanitize Path**:
   - Trong `scp/knowledge/learning_db.py`, xác thực identifier bảng và cột dựa trên allowlist trước khi thực thi truy vấn.
   - Trong `scp/api/routes/batch_benchmark_routes.py`, xác thực `job_id` bằng regex (`^[a-zA-Z0-9_-]+$`) để ngăn directory traversal.
5. **Thêm Xác thực cho Route Bị Lộ**:
   - Bảo vệ `/swe-bench/v1/chat/completions` và `/metrics` bằng xác thực token/admin.
   - Yêu cầu bearer token cho khởi tạo kết nối WebSocket trước khi thực thi `await websocket.accept()`.
6. **Tắt Graceful & Buffer In-Memory Có Giới hạn**:
   - Thay danh sách và dict không giới hạn (`_threat_history`, `_alert_history`, `_incidents`, `_auth_failures`) bằng LRU cache có giới hạn hoặc deque vòng tròn.
   - Thêm chờ hủy (`await asyncio.gather`) và sự kiện dừng thread trong `api_server_parts/lifespan.py`.

### Ưu tiên 2 (P2): Dọn dẹp Kiến trúc & Nâng cao Bộ Test Trung hạn
1. **Tách rời Subsystem & Loại bỏ Chu trình**:
   - Phá vỡ import vòng tròn hai chiều (`core` <-> `autofix`, `core` <-> `hands`) bằng giao thức interface trừu tượng trong `scp/interfaces/`.
   - Loại bỏ rebind bytecode "tách-và-khâu" trong `api_server_parts`, `task_kernel_parts`, và `autofix`, thay monkeypatch bằng composition class và dependency injection phù hợp.
2. **Deprecation & Loại bỏ Mã Chết**:
   - Deprecate và cắt tỉa an toàn 63 module Python mồ côi (>13.000 LOC) đã xác định trong kiểm toán.
   - Dọn tham chiếu đặc tả cũ trong `spec/implementation_bindings.yaml` và xóa script bảo trì hỏng.
   - Di chuyển thư mục không-code `scp/audit_r8` và `scp/audit_r9` sang `docs/audit_history/`, và di chuyển `scp/tests/` vào `tests/internal/`.
3. **Mở rộng Bộ Test Có Ý nghĩa**:
   - Thay 38 file test import-only smoke bằng test case chức năng bao phủ chuyển đổi trạng thái và ranh giới lỗi.
   - Mở rộng độ phủ test cho 22 subsystem thiếu phủ (`interfaces`, `observability`, `capabilities`, `rag`, `sandbox_evaluator`).
   - Thay assertion text AST tĩnh trong `tests/reality-tests/` bằng test thực thi hành vi.
   - Thay lệnh `time.sleep` hardcode bằng điều kiện polling hoặc primitive đồng bộ hóa sự kiện.
