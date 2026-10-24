# Báo Cáo Trực Quan Hằng Tuần (Tuần 43, Năm 2026)

> **Thời điểm xuất bản:** `2026-10-24 23:40:56 (UTC+7)`  
> **Khoảng thời gian giám sát:** `2026-10-17 23:40 -> 2026-10-24 23:40`  
> **Cơ chế vận hành:** `SCP_AUTOFIX_MODE=apply` | `SCP_EVOLUTION_ENABLED=1` | `SCP_CAPABILITY_LEVEL=FULL_PRODUCTION`  

---

## 1. BẢNG TRỰC QUAN HÓA 6 CHIỀU TIẾN HÓA (KPI DASHBOARD)

![KPI Dashboard](../assets/kpi_weekly_2026-W43.svg)

---

## 2. PHÂN TÍCH PHẢN BIỆN TỰ THÂN (WHY-GATE FALSIFICATION)

![WHY-Gate Donut Chart](../assets/why_donut_weekly_2026-W43.svg)

### Chi Tiết Phán Quyết Phản Biện Của WHY-Gate:
- **Tổng số quyết định ghi nhận:** `4,418` lượt.
- **REJECT (Bác bỏ bản vá xấu):** `7` lượt — Ngăn chặn code nuốt lỗi, cắt xén bảo mật.
- **ALLOW (Chấp thuận an toàn):** `389` lượt — Bản vá hợp lệ về mặt cấu trúc.
- **UPHOLD (Cần thêm bằng chứng):** `4,022` lượt.

#### Danh sách các trường hợp gian lận điển hình bị WHY-Gate tóm gọn gần nhất:
**Ca #1: Fix HardcodedTimeout at scp/security/policy.py:10: Timeout is too short**
- **Quyết định:** `REJECT` (Độ tin cậy: `0.0`)
- **Lý do bác bỏ nguyên văn:** `FALSIFICATION: Việc tăng thời gian timeout có thể mở rộng thời gian mà một kết nối hoặc phiên làm việc có thể tiếp tục tồn tại, qua đó tăng khả năng khai thác các lỗ hổng như session hijacking hoặc br`

**Ca #2: Fix HardcodedTimeout at scp/security/policy.py:10: Timeout is too short**
- **Quyết định:** `REJECT` (Độ tin cậy: `0.0`)
- **Lý do bác bỏ nguyên văn:** `FALSIFICATION: Việc tăng thời gian timeout làm kéo dài thời gian duy trì tài nguyên và mở rộng cửa sổ tấn công (attack window), tạo điều kiện cho các cuộc tấn công Từ chối dịch vụ (DoS) hoặc lạm dụng `

**Ca #3: FastLearning store: entity=ADN được phát hiện bởi ai?, source=llm-gateway+wiki, conf=0.75**
- **Quyết định:** `REJECT` (Độ tin cậy: `0.0`)
- **Lý do bác bỏ nguyên văn:** `FALSIFICATION: Action bị bác bỏ do lưu trữ khóa entity dưới dạng một câu hỏi tự nhiên thay vì chuẩn hóa thành chuẩn dữ liệu cấu trúc (canonical entity/relation), gây ô nhiễm Knowledge Graph dù giá trị`

**Ca #4: FastLearning store: entity=Động vật đặc trưng (đặc hữu) của Thái Lan là con g, source=llm-gateway+wiki, conf=0.7285714285714285**
- **Quyết định:** `REJECT` (Độ tin cậy: `0.0`)
- **Lý do bác bỏ nguyên văn:** `FALSIFICATION: Action này bị bác bỏ vì thực thể dữ liệu bị trích xuất lỗi/cắt từ nghiêm trọng ("con g" thay vì "voi Thái"), dẫn đến việc lưu trữ tri thức rác và không chính xác so với Context. | SELF_`

**Ca #5: Fix BareExceptPass at C:\Users\check\AppData\Local\Temp\pytest-of-check\pytest-1024\test_autofix_engine_end_to_end0\buggy.py:3: Bare except**
- **Quyết định:** `REJECT` (Độ tin cậy: `0.0`)
- **Lý do bác bỏ nguyên văn:** `FALSIFICATION: Applying the patch as‑is inserts the bare word “fix” into the code, which is not a valid Python statement and will raise a SyntaxError (potentially exploitable as a denial‑of‑service), `

---

## 3. PHÂN BỐ LỖI AST VÀ CÁC FILE ĐƯỢC LLM VÁ MÃ

![Top Files Bar Chart](../assets/top_files_weekly_2026-W43.svg)

| Vị Trí File Mã Nguồn | Số Lỗi Phát Hiện | Hành Động Sửa Lỗi Của SCP |
|---|:---:|---|
| [`app/scp/web_control/web_navigator.py`](/app/scp/web_control/web_navigator.py) | **4** | LLM sinh bản vá -> WHY-Gate kiểm chứng -> Fail-Closed |
| [`scp/core/autonomous_cycle.py`](scp/core/autonomous_cycle.py) | **3** | LLM sinh bản vá -> WHY-Gate kiểm chứng -> Fail-Closed |
| [`app/scp/autofix/scanners/cross_func_taint_scanner_parts/_get_scp_call_graph.py`](/app/scp/autofix/scanners/cross_func_taint_scanner_parts/_get_scp_call_graph.py) | **2** | LLM sinh bản vá -> WHY-Gate kiểm chứng -> Fail-Closed |
| [`app/scp/autofix/subsystem_reviewer.py`](/app/scp/autofix/subsystem_reviewer.py) | **2** | LLM sinh bản vá -> WHY-Gate kiểm chứng -> Fail-Closed |
| [`app/scp/web_control/internet_search.py`](/app/scp/web_control/internet_search.py) | **2** | LLM sinh bản vá -> WHY-Gate kiểm chứng -> Fail-Closed |
| [`app/scp/core/db_manager_parts/get_db.py`](/app/scp/core/db_manager_parts/get_db.py) | **1** | LLM sinh bản vá -> WHY-Gate kiểm chứng -> Fail-Closed |
| [`app/scp/core/fast_learning_engine_parts/start_fast_learning_thread.py`](/app/scp/core/fast_learning_engine_parts/start_fast_learning_thread.py) | **1** | LLM sinh bản vá -> WHY-Gate kiểm chứng -> Fail-Closed |
| [`app/scp/security/canary_monitor.py`](/app/scp/security/canary_monitor.py) | **1** | LLM sinh bản vá -> WHY-Gate kiểm chứng -> Fail-Closed |

---

## 4. TÌNH TRẠNG BỘ NHỚ BỀN VỮNG & TABULA RASA (ĐO ĐẠC VẬT LÝ)

- **Kho tri thức ([`data/knowledge.sqlite3`](file:///d:/scp/data/knowledge.sqlite3)):** `2092 rows` (Bảo toàn 100% Tabula Rasa, 0 dữ liệu tĩnh mớm sẵn).
- **Task Kernel ([`data/ask_task_kernel.sqlite3`](file:///d:/scp/data/ask_task_kernel.sqlite3)):** `1,534` tasks, `13,313` events nguyên tử.
- **Bàn tay thao tác ([`data/hands/task_kernel.sqlite3`](file:///d:/scp/data/hands/task_kernel.sqlite3)):** `275` tasks, `2200` events.
- **Kiểm tra tính toàn vẹn SQLite WAL thực tế (`PRAGMA integrity_check`):** `knowledge:ok(wal); ask_kernel:ok(wal); hands_kernel:ok(wal)`.

---

## 5. HẠ TẦNG VÀ TIẾN TRÌNH VẬN HÀNH 24/7 (ĐO ĐẠC THỜI GIAN THỰC)

- **Tổng số chu kỳ hoàn thành của Loop Scheduler:** `5,872` chu kỳ (Thành công: `3103`, Lỗi: `2769`, Tỷ lệ: `52.84%`).
- **Trạng thái thực tế 4 dịch vụ cốt lõi (Live HTTP Socket/Endpoint Probe):**
  + Backend API (`Port 8000`): **HTTP 200 OK (status=ok)**
  + Loop Scheduler (`Port 3030`): **HTTP 200 OK**
  + Agent Desktop (`Port 6080`): **HTTP 200 OK**
  + Web Dashboard (`Port 3000`): **HTTP 200 OK**
- **Trạng thái bộ lập lịch hệ điều hành (OS Scheduler Probe):**
  + `SCP-247-Supervisor`: **Disabled (Next run: N/A)**
  + `SCP-247-Recovery-Watchdog`: **Disabled (Next run: N/A)**
  + `SCP-247-Visual-Reports`: **Running (Next run: 25/10/2026 00:40:47)**

---
_Báo cáo được khởi tạo tự động bởi `tools/generate_visual_reports.py` dựa trên đo đạc vật lý 100% (Zero hardcoding)._