# Báo Cáo Trực Quan Hằng Tháng (Tháng 10, Năm 2026)

> **Thời điểm xuất bản:** `2026-10-24 04:40:53 (UTC+7)`  
> **Khoảng thời gian giám sát:** `2026-09-24 04:40 -> 2026-10-24 04:40`  
> **Cơ chế vận hành:** `SCP_AUTOFIX_MODE=apply` | `SCP_EVOLUTION_ENABLED=1` | `SCP_CAPABILITY_LEVEL=FULL_PRODUCTION`  

---

## 1. BẢNG TRỰC QUAN HÓA 6 CHIỀU TIẾN HÓA (KPI DASHBOARD)

![KPI Dashboard](../assets/kpi_monthly_2026-10.svg)

---

## 2. PHÂN TÍCH PHẢN BIỆN TỰ THÂN (WHY-GATE FALSIFICATION)

![WHY-Gate Donut Chart](../assets/why_donut_monthly_2026-10.svg)

### Chi Tiết Phán Quyết Phản Biện Của WHY-Gate:
- **Tổng số quyết định ghi nhận:** `47,489` lượt.
- **REJECT (Bác bỏ bản vá xấu):** `1,704` lượt — Ngăn chặn code nuốt lỗi, cắt xén bảo mật.
- **ALLOW (Chấp thuận an toàn):** `11,166` lượt — Bản vá hợp lệ về mặt cấu trúc.
- **UPHOLD (Cần thêm bằng chứng):** `34,619` lượt.

#### Danh sách các trường hợp gian lận điển hình bị WHY-Gate tóm gọn gần nhất:
**Ca #1: Fix BareExceptPass at C:\Users\check\AppData\Local\Temp\pytest-of-check\pytest-1656\test_autofix_engine_end_to_end0\buggy.py:3: Bare except**
- **Quyết định:** `REJECT` (Độ tin cậy: `0.0`)
- **Lý do bác bỏ nguyên văn:** `FALSIFICATION: Patch appears incomplete and likely unsafe. | SELF_FALSIFIED: yes`

**Ca #2: Fix HardcodedTimeout at scp/security/policy.py:10: Timeout is too short**
- **Quyết định:** `REJECT` (Độ tin cậy: `0.0`)
- **Lý do bác bỏ nguyên văn:** `FALSIFICATION: This patch only adjusts a timeout value and doesn't introduce new vulnerabilities. | SELF_FALSIFIED: yes`

**Ca #3: Fix BareExceptPass at C:\Users\check\AppData\Local\Temp\pytest-of-check\pytest-1661\test_autofix_engine_end_to_end0\buggy.py:3: Bare except**
- **Quyết định:** `REJECT` (Độ tin cậy: `0.0`)
- **Lý do bác bỏ nguyên văn:** `FALSIFICATION: The patch replaces the bare except with the generic placeholder "fix", which does not constrain the caught exceptions and therefore still permits unrestricted exception handling that ca`

**Ca #4: Fix HardcodedTimeout at scp/security/policy.py:10: Timeout is too short**
- **Quyết định:** `REJECT` (Độ tin cậy: `0.0`)
- **Lý do bác bỏ nguyên văn:** `FALSIFICATION: The patch increases the timeout, potentially allowing an attacker to hold a connection open longer, which could exhaust server resources or enable prolonged brute‑force attempts. | SELF`

**Ca #5: Fix BareExceptPass at C:\Users\check\AppData\Local\Temp\pytest-of-check\pytest-1661\test_clean_workspace_no_tier3b0\src\app_logic.py:4: swallows error**
- **Quyết định:** `REJECT` (Độ tin cậy: `0.0`)
- **Lý do bác bỏ nguyên văn:** `FALSIFICATION: Việc thêm `except Exception: pass` vẫn gây bỏ qua mọi lỗi, làm mất thông tin quan trọng và có thể ẩn lỗi bảo mật. | SELF_FALSIFIED: yes`

---

## 3. PHÂN BỐ LỖI AST VÀ CÁC FILE ĐƯỢC LLM VÁ MÃ

![Top Files Bar Chart](../assets/top_files_monthly_2026-10.svg)

| Vị Trí File Mã Nguồn | Số Lỗi Phát Hiện | Hành Động Sửa Lỗi Của SCP |
|---|:---:|---|
| [`scp\autofix\llm_fix_parts\_call_smart_llm.py`](D:\scp\scp\autofix\llm_fix_parts\_call_smart_llm.py) | **990** | LLM sinh bản vá -> WHY-Gate kiểm chứng -> Fail-Closed |
| [`scp\autofix\llm_fix_parts\generate_fix_for_bug.py`](D:\scp\scp\autofix\llm_fix_parts\generate_fix_for_bug.py) | **771** | LLM sinh bản vá -> WHY-Gate kiểm chứng -> Fail-Closed |
| [`scp\autofix\llm_fix_parts\process_bug_with_llm.py`](D:\scp\scp\autofix\llm_fix_parts\process_bug_with_llm.py) | **457** | LLM sinh bản vá -> WHY-Gate kiểm chứng -> Fail-Closed |
| [`scp\autofix\llm_fix_parts\_call_openrouter.py`](D:\scp\scp\autofix\llm_fix_parts\_call_openrouter.py) | **244** | LLM sinh bản vá -> WHY-Gate kiểm chứng -> Fail-Closed |
| [`scp\autofix\engine.py`](D:\scp\scp\autofix\engine.py) | **58** | LLM sinh bản vá -> WHY-Gate kiểm chứng -> Fail-Closed |
| [`scp\autofix\scanners\cross_func_taint_scanner_parts\_functiondetector.py`](D:\scp\scp\autofix\scanners\cross_func_taint_scanner_parts\_functiondetector.py) | **32** | LLM sinh bản vá -> WHY-Gate kiểm chứng -> Fail-Closed |
| [`scp\autofix\scanners\cross_func_taint_scanner_parts\_get_scp_call_graph.py`](D:\scp\scp\autofix\scanners\cross_func_taint_scanner_parts\_get_scp_call_graph.py) | **25** | LLM sinh bản vá -> WHY-Gate kiểm chứng -> Fail-Closed |
| [`scp\core\fast_learning_engine_parts\fastlearningengine.py`](D:\scp\scp\core\fast_learning_engine_parts\fastlearningengine.py) | **25** | LLM sinh bản vá -> WHY-Gate kiểm chứng -> Fail-Closed |

---

## 4. TÌNH TRẠNG BỘ NHỚ BỀN VỮNG & TABULA RASA (ĐO ĐẠC VẬT LÝ)

- **Kho tri thức ([`data/knowledge.sqlite3`](file:///d:/scp/data/knowledge.sqlite3)):** `2094 rows` (Bảo toàn 100% Tabula Rasa, 0 dữ liệu tĩnh mớm sẵn).
- **Task Kernel ([`data/ask_task_kernel.sqlite3`](file:///d:/scp/data/ask_task_kernel.sqlite3)):** `1,534` tasks, `13,313` events nguyên tử.
- **Bàn tay thao tác ([`data/hands/task_kernel.sqlite3`](file:///d:/scp/data/hands/task_kernel.sqlite3)):** `275` tasks, `2200` events.
- **Kiểm tra tính toàn vẹn SQLite WAL thực tế (`PRAGMA integrity_check`):** `knowledge:ok(wal); ask_kernel:ok(wal); hands_kernel:ok(wal)`.

---

## 5. HẠ TẦNG VÀ TIẾN TRÌNH VẬN HÀNH 24/7 (ĐO ĐẠC THỜI GIAN THỰC)

- **Tổng số chu kỳ hoàn thành của Loop Scheduler:** `5,593` chu kỳ (Thành công: `3103`, Lỗi: `2490`, Tỷ lệ: `55.48%`).
- **Trạng thái thực tế 4 dịch vụ cốt lõi (Live HTTP Socket/Endpoint Probe):**
  + Backend API (`Port 8000`): **HTTP 200 OK (status=ok)**
  + Loop Scheduler (`Port 3030`): **HTTP 200 OK**
  + Agent Desktop (`Port 6080`): **HTTP 200 OK**
  + Web Dashboard (`Port 3000`): **HTTP 200 OK**
- **Trạng thái bộ lập lịch hệ điều hành (OS Scheduler Probe):**
  + `SCP-247-Supervisor`: **Disabled (Next run: N/A)**
  + `SCP-247-Recovery-Watchdog`: **Disabled (Next run: N/A)**
  + `SCP-247-Visual-Reports`: **Running (Next run: 24/10/2026 05:40:47)**

---
_Báo cáo được khởi tạo tự động bởi `tools/generate_visual_reports.py` dựa trên đo đạc vật lý 100% (Zero hardcoding)._